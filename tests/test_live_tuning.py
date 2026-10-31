"""The tuning entry point, `python -m lmt.live.tuning` (design/internal/g2-stage1.md §3c, item 3c3), with no model:
`asr.load`, the diarization loaders and `models.ensure` are fakes, and the real `load_stages` builds the stages, so
the configs they carry are the ones the command would run with. An unknown key, or a value that doesn't parse,
exits 2.
"""

import contextlib
import io
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from lmt import asr as offline_asr
from lmt import diarize as offline_diarize
from lmt import models
from lmt.live import asr, command, diarize, tuning
from lmt.live.asr import AsrConfig, AsrUpdate
from lmt.live.diarize import DiarConfig, NullDiarizer

ROOT = Path(__file__).resolve().parents[1]
RATE = 16000
MODELS = {"asr": {"name": "fake-asr", "repository": "test/fake-asr", "revision": "0" * 40}, "diarization": None}


class FakeASR:
    def feed(self, samples) -> AsrUpdate:
        return AsrUpdate([], 0)

    def flush(self) -> AsrUpdate:
        return AsrUpdate([], 0)


class FakeOnlineDiarizer:
    def __init__(self, segmentation, embedding, config=diarize.DEFAULT_DIAR_CONFIG, *, record=None):
        self.config = config


@pytest.fixture
def built(monkeypatch):
    """The stages the real `load_stages` builds, on fake loaders, appended each time the command calls it. The
    session then runs on stages that do nothing. The default settings are put back after the test."""
    for module, attribute in tuning.CONFIGS.values():
        monkeypatch.setattr(module, attribute, getattr(module, attribute))
    monkeypatch.setattr(command, "LOCKSTEP", True)
    monkeypatch.setattr(models, "ensure", lambda needed, offline=False: {m.name: Path("/nonexistent") for m in needed})
    monkeypatch.setattr(offline_asr, "load", lambda directory: object())
    monkeypatch.setattr(offline_diarize, "scratch_matplotlib", lambda: None)
    monkeypatch.setattr(offline_diarize, "load_segmentation", lambda directory: object())
    monkeypatch.setattr(offline_diarize, "load_embedding", lambda directory: object())
    monkeypatch.setattr(diarize, "OnlineDiarizer", FakeOnlineDiarizer)
    real = command.load_stages
    stages = []

    def recording(args, manifest):
        stage, diarizer, _ = real(args, manifest)
        stages.append((stage, diarizer))
        return FakeASR(), NullDiarizer(), MODELS

    monkeypatch.setattr(command, "load_stages", recording)
    return stages


def silence_wav(path, seconds=1.0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.zeros(int(seconds * RATE), dtype="<i2").tobytes())
    return path


def run(tmp_path, *settings):
    wav = silence_wav(tmp_path / "talk.wav")
    argv = [*settings, "--", "live", "--from-file", str(wav), "-o", str(tmp_path), "-n", "talk", "-f", "json"]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = tuning.main(argv)
        except SystemExit as e:
            code = e.code
    return code, err.getvalue()


def test_overrides_reach_the_stages_the_command_builds(tmp_path, built):
    code, err = run(tmp_path, "--set", "asr.update=8000", "--set", "diar.latency=24000",
                    "--set", "diar.new_speaker=0.6")
    assert code == 0, err
    [(stage, diarizer)] = built
    assert stage._config == AsrConfig(update=8000)
    assert diarizer.config == DiarConfig(latency=24000, new_speaker=0.6)
    assert type(diarizer.config.new_speaker) is float and type(stage._config.update) is int
    assert f"lmt: tuning: {AsrConfig(update=8000)}" in err.splitlines()
    assert f"lmt: tuning: {DiarConfig(latency=24000, new_speaker=0.6)}" in err.splitlines()
    assert (tmp_path / "talk.json").exists()


def test_max_speakers_applies_over_an_override(tmp_path, built):
    code, err = run(tmp_path, "--set", "diar.latency=24000", "--set", "asr.max_buffer=160000")
    assert code == 0, err
    wav = tmp_path / "talk.wav"
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tuning.main(["--set", "diar.latency=24000", "--", "live", "--from-file", str(wav), "-o",
                            str(tmp_path), "-n", "again", "-f", "json", "--max-speakers", "3"])
    assert code == 0, err.getvalue()
    assert built[0][0]._config == AsrConfig(max_buffer=160000)
    assert built[1][1].config == DiarConfig(latency=24000, max_speakers=3)


def test_no_set_builds_the_defaults(tmp_path, built):
    code, err = run(tmp_path)
    assert code == 0, err
    [(stage, diarizer)] = built
    assert stage._config == AsrConfig()
    assert diarizer.config == DiarConfig()


@pytest.mark.parametrize("setting", ["asr.nonsense=1", "diar.speed=2", "vad.update=1", "update=8000", "asr.update"])
def test_unknown_key_exits_2(tmp_path, built, setting):
    code, err = run(tmp_path, "--set", setting)
    assert code == 2
    assert "error:" in err
    assert built == []
    assert (asr.DEFAULT_ASR_CONFIG, diarize.DEFAULT_DIAR_CONFIG) == (AsrConfig(), DiarConfig())


@pytest.mark.parametrize("setting", ["asr.update=fast", "asr.update=0.5", "diar.new_speaker=near", "asr.pause="])
def test_value_that_does_not_parse_exits_2(tmp_path, built, setting):
    code, err = run(tmp_path, "--set", setting)
    assert code == 2
    assert "error:" in err
    assert built == []


def test_missing_separator_exits_2(tmp_path, built):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), pytest.raises(SystemExit) as e:
        tuning.main(["--set", "asr.update=8000", "live"])
    assert e.value.code == 2
    assert built == []


def test_runs_as_a_module():
    r = subprocess.run([sys.executable, "-m", "lmt.live.tuning", "--set", "asr.update=x", "--", "live"],
                       check=False, capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 2
    assert "python -m lmt.live.tuning" in r.stderr
