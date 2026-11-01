"""`lmt live`, the command: the checks before capture, the session and the outputs, on fake stages and the stand-in
for `sounddevice`, with no model (design/internal/g2-stage1.md §3b, item 3b3). G2-7: every error that can be found
before capture is found before it, with its exit code and one `lmt: error:` line, nothing written and no audio
captured. G2-8: G1-11's bad inputs through `lmt live --from-file`, in a subprocess, as `lmt transcribe` meets them.
"""

import contextlib
import io
import json
import math
import os
import re
import subprocess
import sys
import wave
from datetime import datetime
from pathlib import Path

import jsonschema
import numpy as np
import pytest
from bad_inputs import build, run_bounded
from fake_sounddevice import FakeSoundDevice, device

from lmt import cli, export, schema, transcript
from lmt.live import command
from lmt.live.asr import AsrUpdate
from lmt.live.diarize import NullDiarizer
from lmt.transcript import Word

ROOT = Path(__file__).resolve().parents[1]
EVENTS_SCHEMA = ROOT / "schema" / "live-events.schema.json"
RATE = 16000
FORMATS = ("txt", "srt", "vtt", "json")
MODELS = {
    "asr": {"name": "fake-asr", "repository": "test/fake-asr", "revision": "0" * 40},
    "diarization": {"name": "speaker-diarization-3.1", "repository": "pyannote/speaker-diarization-3.1",
                    "revision": "1" * 40},
}


class FakeASR:
    """Commits one word per second of audio, each ending a sentence, so every word is a segment of its own."""

    def __init__(self):
        self.position = 0
        self.committed = 0

    def feed(self, samples) -> AsrUpdate:
        self.position += len(samples)
        words = []
        while (self.committed + 1) * RATE <= self.position:
            k = self.committed
            words.append(Word(k + 0.2, k + 0.6, f"Word{k}.", 0.9))
            self.committed += 1
        return AsrUpdate(words, self.committed * RATE)

    def flush(self) -> AsrUpdate:
        return AsrUpdate([], self.position)


@pytest.fixture
def stages(monkeypatch):
    """`load_stages` replaced by fake stages; the list of the argument lists it was called with."""
    calls = []

    def fake(args, manifest):
        calls.append(args)
        return FakeASR(), NullDiarizer(), MODELS

    monkeypatch.setattr(command, "load_stages", fake)
    return calls


def fake_device(monkeypatch, *, seconds=2.0, **kwargs):
    """The stand-in in `sys.modules`, with two input devices (the second the default) and an output device,
    delivering `seconds` of a 48 kHz stereo tone."""
    t = np.arange(int(seconds * 48000)) / 48000
    tone = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    fake = FakeSoundDevice([device("Built-in Microphone", 1, rate=48000), device("Speakers", 0, outputs=2),
                            device("USB Audio Interface", 2, rate=48000)], default=2,
                           data=np.stack([tone, tone], axis=1), **kwargs)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


def tone_wav(path, seconds=3.0):
    t = np.arange(int(seconds * RATE)) / RATE
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.round(0.1 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2").tobytes())
    return path


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["live", *argv])
    return code, out.getvalue(), err.getvalue()


def files(directory):
    return sorted(p.name for p in Path(directory).iterdir())


def assert_one_error(code, out, err, expected_code, subject):
    assert code == expected_code, err
    assert out == "", out  # no `start` event, or anything else
    assert len(err.splitlines()) == 1, err
    assert err.startswith(f"lmt: error: {subject}: "), err


# G2-7: the errors found before capture.

@pytest.mark.criterion("G2-7")
def test_unknown_device(tmp_path, monkeypatch, stages):
    fake = fake_device(monkeypatch)
    code, out, err = run("--device", "Nonexistent", "-o", str(tmp_path), "-n", "m")
    assert_one_error(code, out, err, 3, "--device")
    assert "'Nonexistent'" in err
    assert files(tmp_path) == []
    assert not fake.opened
    assert stages == []  # no model loaded either


@pytest.mark.criterion("G2-7")
def test_missing_from_file_input(tmp_path, stages):
    missing = tmp_path / "missing.wav"
    code, out, err = run("--from-file", str(missing), "-o", str(tmp_path), "-n", "m")
    assert_one_error(code, out, err, 3, str(missing))
    assert files(tmp_path) == []
    assert stages == []


@pytest.mark.criterion("G2-7")
@pytest.mark.parametrize("fmt", FORMATS)
def test_existing_output_without_overwrite(tmp_path, monkeypatch, stages, fmt):
    fake = fake_device(monkeypatch)
    existing = tmp_path / f"m.{fmt}"
    existing.write_text("keep\n", encoding="utf-8")
    code, out, err = run("-o", str(tmp_path), "-n", "m", "--duration", "1")
    assert_one_error(code, out, err, 2, str(existing))
    assert files(tmp_path) == [existing.name]
    assert existing.read_text(encoding="utf-8") == "keep\n"
    assert not fake.opened
    assert stages == []


@pytest.mark.criterion("G2-7")
def test_existing_wav_with_save_audio(tmp_path, monkeypatch, stages):
    fake = fake_device(monkeypatch)
    existing = tmp_path / "m.wav"
    existing.write_bytes(b"keep")
    code, out, err = run("-o", str(tmp_path), "-n", "m", "--save-audio", "--duration", "1")
    assert_one_error(code, out, err, 2, str(existing))
    assert files(tmp_path) == ["m.wav"]
    assert existing.read_bytes() == b"keep"
    assert not fake.opened
    assert stages == []


@pytest.mark.criterion("G2-7")
def test_missing_output_directory(tmp_path, monkeypatch, stages):
    fake = fake_device(monkeypatch)
    out_dir = tmp_path / "missing"
    code, out, err = run("-o", str(out_dir), "-n", "m", "--duration", "1")
    assert_one_error(code, out, err, 3, str(out_dir))
    assert files(tmp_path) == []
    assert not fake.opened
    assert stages == []


@pytest.mark.criterion("G2-7")
@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to any directory")
def test_unwritable_output_directory(tmp_path, monkeypatch, stages):
    fake = fake_device(monkeypatch)
    out_dir = tmp_path / "locked"
    out_dir.mkdir()
    out_dir.chmod(0o500)
    try:
        code, out, err = run("-o", str(out_dir), "-n", "m", "--duration", "1")
        assert_one_error(code, out, err, 3, str(out_dir))
        assert files(out_dir) == []
    finally:
        out_dir.chmod(0o700)
    assert not fake.opened
    assert stages == []


@pytest.mark.criterion("G2-7")
def test_device_that_fails_to_open_writes_nothing(tmp_path, monkeypatch, stages):
    fake_device(monkeypatch, open_error="Device unavailable")
    code, out, err = run("-o", str(tmp_path), "-n", "m", "--save-audio", "--duration", "1")
    assert_one_error(code, out, err, 3, "--device")
    assert "USB Audio Interface" in err
    assert files(tmp_path) == []


# --list-devices and the default name.

@pytest.mark.criterion("G2-7")
def test_list_devices(monkeypatch, stages):
    fake = fake_device(monkeypatch)
    # Whatever else is given, even options that conflict, it lists and exits 0.
    code, out, err = run("--list-devices", "--device", "x", "--from-file", "y", "--no-diarize", "--max-speakers", "2")
    assert (code, err) == (0, "")
    assert out == "0\tBuilt-in Microphone\t1 ch\t48000 Hz\n2*\tUSB Audio Interface\t2 ch\t48000 Hz\n"
    assert not fake.opened
    assert stages == []


@pytest.mark.criterion("G2-7")
def test_default_name_is_the_local_start_time(tmp_path, monkeypatch, stages):
    fake_device(monkeypatch)
    before = datetime.now().replace(microsecond=0)
    code, _, err = run("-o", str(tmp_path), "--save-audio", "--duration", "1.5")
    after = datetime.now()
    assert (code, err) == (0, "")
    names = files(tmp_path)
    assert len(names) == 5, names
    stems = {Path(n).stem for n in names}
    assert len(stems) == 1, names
    stem = stems.pop()
    assert re.fullmatch(r"live-\d{8}-\d{6}", stem), stem
    assert before <= datetime.strptime(stem, "live-%Y%m%d-%H%M%S") <= after
    assert sorted(Path(n).suffix for n in names) == [".json", ".srt", ".txt", ".vtt", ".wav"]


# A session through to its outputs.

def test_device_session_writes_the_outputs(tmp_path, monkeypatch, stages):
    fake = fake_device(monkeypatch)
    code, out, err = run("-o", str(tmp_path), "-n", "m", "--save-audio", "--duration", "1.5")
    assert (code, err) == (0, "")
    assert fake.streams[0].device == 2  # the default input
    assert out == "[00:00:00] Speaker 1: Word0.\n"  # the text form, without --jsonl
    doc = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    schema.validate(doc)
    assert doc["source"] == {"file": "m", "duration": 1.5, "live": True, "device": "USB Audio Interface"}
    with wave.open(str(tmp_path / "m.wav")) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (RATE, 1, 24000)


@pytest.fixture
def file_run(tmp_path, monkeypatch, stages):
    """`lmt live --from-file <a 3 s WAV> --jsonl` in lockstep on fake stages: (exit, stdout, stderr, out dir)."""
    monkeypatch.setattr(command, "LOCKSTEP", True)
    wav = tone_wav(tmp_path / "talk.wav")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    code, out, err = run("--from-file", str(wav), "--jsonl", "-o", str(out_dir), "-n", "talk")
    return code, out, err, out_dir


def test_from_file_writes_the_four_outputs(file_run):
    code, _, err, out_dir = file_run
    assert (code, err) == (0, "")
    assert files(out_dir) == [f"talk.{fmt}" for fmt in sorted(FORMATS)]
    doc = json.loads((out_dir / "talk.json").read_text(encoding="utf-8"))
    schema.validate(doc)
    assert doc["schema_version"] == transcript.SCHEMA_VERSION
    assert doc["source"] == {"file": "talk.wav", "duration": 3.0, "live": True}
    assert doc["models"] == MODELS
    assert [s["text"] for s in doc["segments"]] == ["Word0.", "Word1.", "Word2."]
    loaded = transcript.load(out_dir / "talk.json")
    for fmt in FORMATS:
        assert (out_dir / f"talk.{fmt}").read_text(encoding="utf-8") == export.render(loaded, fmt), fmt


def test_from_file_jsonl_is_start_the_events_stop(file_run):
    code, out, err, _ = file_run
    assert (code, err) == (0, "")
    events = [json.loads(line) for line in out.splitlines()]
    validator = jsonschema.Draft202012Validator(json.loads(EVENTS_SCHEMA.read_text(encoding="utf-8")))
    for event in events:
        validator.validate(event)
    assert [e["type"] for e in events] == ["start"] + ["text", "words", "segment"] * 3 + ["stop"]
    assert events[0]["source"] == {"file": "talk.wav", "live": True}
    assert events[0]["models"] == MODELS
    assert events[-1]["reason"] == "end-of-input"
    assert math.isclose(events[-1]["t"], 3.0)


def test_from_file_duration_stops_the_session(tmp_path, monkeypatch, stages):
    monkeypatch.setattr(command, "LOCKSTEP", True)
    wav = tone_wav(tmp_path / "talk.wav")
    code, out, err = run("--from-file", str(wav), "--jsonl", "--duration", "2", "-o", str(tmp_path), "-n", "m",
                         "-f", "json")
    assert (code, err) == (0, "")
    assert json.loads(out.splitlines()[-1]) == {"type": "stop", "t": 2.0, "reason": "duration"}
    assert json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))["source"]["duration"] == 2.0


# G2-8: bad input, through the product, as `lmt transcribe` meets it.

@pytest.mark.criterion("G2-8")
@pytest.mark.parametrize("name", ["empty.wav", "random.wav", "notes.txt"])
def test_bad_input_exits_3(tmp_path, name):
    bad = build(name, tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = run_bounded(["live", "--from-file", str(bad), "-o", str(out_dir), "-n", "m"], cwd=tmp_path)
    assert result.returncode == 3, result.stderr
    assert result.stdout == ""
    assert len(result.stderr.splitlines()) == 1, result.stderr
    assert result.stderr.startswith(f"lmt: error: {bad}: "), result.stderr
    assert files(out_dir) == []


def test_live_lockstep_runs_the_cli(tmp_path):
    """tests/live_lockstep.py is `lmt` with LOCKSTEP set: its exit code and error line are the CLI's."""
    bad = build("random.wav", tmp_path)
    result = subprocess.run([sys.executable, str(ROOT / "tests" / "live_lockstep.py"), "live", "--from-file",
                             str(bad), "-o", str(tmp_path), "-n", "m"], capture_output=True, text=True,
                            check=False, timeout=120, cwd=tmp_path)
    assert result.returncode == 3, result.stderr
    assert result.stderr.startswith(f"lmt: error: {bad}: "), result.stderr
