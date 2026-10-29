"""G1-1 (spec/acceptance.md): the product run on the reference clip, scored and its times checked
(design/internal/g1-slice.md, S6). What it checks is computed from the run's JSON and the clip itself,
independently of the product's code."""

import json
import os
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "tests" / "fixtures" / "clip.wav"
CLIP_TEXT = ROOT / "tests" / "fixtures" / "clip.txt"
OUTPUTS = ("clip.txt", "clip.srt", "clip.vtt", "clip.json")  # the default --formats, in order

WER_BOUND = 0.050  # spec/acceptance.md G1-1: WER <= 5.0 %, inclusive


def clip_duration() -> float:
    """From the WAV's header, frames over rate, unrounded: never from the JSON."""
    with wave.open(str(CLIP), "rb") as w:
        return w.getnframes() / w.getframerate()


def report(line):
    print(line)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(line + "\n")


@pytest.mark.models
@pytest.mark.criterion("G1-1")
def test_g1_1_the_clip_transcribes_within_the_wer_bound_with_ordered_times(clip_run):
    from lmt.eval.score import hypothesis_from_transcript, wer

    assert clip_run.returncode == 0, clip_run.stderr
    path = clip_run.out_dir / "clip.json"
    assert path.is_file(), clip_run.stdout
    doc = json.loads(path.read_text(encoding="utf-8"))

    reading = wer([(CLIP_TEXT.read_text(encoding="utf-8"), hypothesis_from_transcript(doc).text)])
    line = (f"G1-1: the product run on tests/fixtures/clip.wav: WER {reading:.2%} "
            f"(bound {WER_BOUND:.1%}), {len(doc['segments'])} segments")
    report(line)
    assert reading <= WER_BOUND, line

    duration = clip_duration()
    previous = None
    for n, segment in enumerate(doc["segments"]):
        start, end = segment["start"], segment["end"]
        assert 0 <= start < end <= duration, f"segment {n}: start {start}, end {end}, clip {duration} s"
        if previous is not None:
            assert start >= previous, f"segment {n} starts at {start}, before segment {n - 1}'s {previous}"
        previous = start


@pytest.mark.models
def test_the_product_run_writes_the_four_outputs(clip_run):
    from lmt.schema import validate

    assert clip_run.returncode == 0, clip_run.stderr
    assert clip_run.stdout.splitlines() == list(OUTPUTS)
    for name in clip_run.stdout.splitlines():
        assert (clip_run.out_dir / name).is_file(), name  # the run's paths are relative to its directory
    validate(json.loads((clip_run.out_dir / "clip.json").read_text(encoding="utf-8")))
    assert not [line for line in clip_run.stderr.splitlines() if line.startswith("lmt: error:")]
