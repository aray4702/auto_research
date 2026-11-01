"""G2-8's network and files, and G2-4's outputs in lockstep (spec/acceptance.md), on the shared run, `live_run`
(design/internal/g2-stage1.md §3b, item 3b5): `lmt live --from-file` on the two-speaker clip, through
tests/live_lockstep.py, with `--jsonl --save-audio`, isolated as G1-13's run is."""

import json
import os
import wave
from pathlib import Path

import jsonschema
import numpy as np
import pytest
from conftest import ROOT, TWO_SPEAKERS
from test_g1_1 import report

TRANSCRIPT_SCHEMA = ROOT / "schema" / "transcript.schema.json"
FORMATS = ("txt", "srt", "vtt", "json")  # the default --formats
STEP = 1 / 32767  # one 16-bit step, as output.WavWriter scales a sample


def outputs(run) -> list[Path]:
    return [run.out_dir / f"{run.stem}.{fmt}" for fmt in (*FORMATS, "wav")]


@pytest.mark.models
@pytest.mark.criterion("G2-8")
def test_g2_8_the_live_run_opens_no_connection_and_writes_only_its_outputs(live_run):
    from lmt import models

    run = live_run
    report(f"G2-8: lmt live --from-file tests/fixtures/two_speakers.wav in lockstep: exit {run.returncode}, "
           f"isolated {run.isolated}, stem {run.stem!r}")
    report(f"G2-8: the guard's log: {len(run.connections)} connections {run.connections}")
    report(f"G2-8: {run.watched} entries watched (cache {models.cache_dir()}); new {run.new}, "
           f"changed {run.changed}, gone {run.gone}")

    assert run.returncode == 0, run.stderr
    if os.environ.get("CI"):
        assert run.isolated, "under CI, the run must be isolated (sandbox-exec, network denied)"
    assert not run.connections, f"the run opened connections: {run.connections}"
    assert run.stem, f"not one <stem>.wav: new entries {run.new}"
    assert not run.changed and not run.gone, f"changed {run.changed}, gone {run.gone}"
    assert run.new == sorted(str(path) for path in outputs(run)), f"new entries: {run.new}"


@pytest.mark.models
@pytest.mark.criterion("G2-4")
def test_g2_4_the_outputs_hold_the_event_streams_words(live_run):
    from lmt import audio, export, transcript

    run = live_run
    assert run.returncode == 0, run.stderr
    doc = json.loads((run.out_dir / f"{run.stem}.json").read_text(encoding="utf-8"))
    errors = [e.message for e in jsonschema.Draft202012Validator(
        json.loads(TRANSCRIPT_SCHEMA.read_text(encoding="utf-8"))).iter_errors(doc)]
    assert not errors, errors
    assert doc["schema_version"] == "1.2"
    assert doc["source"]["live"] is True

    streamed = [(w, e["speaker"]) for e in run.events if e["type"] == "words" for w in e["words"]]
    saved = [(w, s["speaker"]) for s in doc["segments"] for w in s["words"]]
    report(f"G2-4: in lockstep, {len(run.events)} events, {len(streamed)} committed words; the JSON has "
           f"{len(doc['segments'])} segments, {len(saved)} words")
    assert streamed, "nothing was committed"
    assert saved == streamed, "the JSON's words differ from the event stream's"

    text = (run.out_dir / f"{run.stem}.txt").read_text(encoding="utf-8")
    assert text == export.render(transcript.from_dict(doc), "txt")

    with wave.open(str(run.out_dir / f"{run.stem}.wav"), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        saved_audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32767
    clip = audio.decode(TWO_SPEAKERS).astype(np.float64)
    worst = float(np.max(np.abs(saved_audio - clip))) if len(saved_audio) == len(clip) else float("inf")
    report(f"G2-4: <stem>.wav has {len(saved_audio)} samples, the clip {len(clip)}; largest difference "
           f"{worst * 32767:.3f} of one 16-bit step")
    assert len(saved_audio) == len(clip)
    assert worst <= STEP * (1 + 1e-6), f"largest difference {worst * 32767:.3f} steps"
