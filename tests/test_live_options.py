"""`lmt live`'s options, a test per option, and G2-5 (ii) and (iii) through the command, with no model
(design/internal/g2-stage1.md §3b, item 3b4). The real `load_stages` runs, with the stand-in for `sounddevice`,
`models.ensure`, `engines.load`, the diarization loaders and the stages replaced by fakes that record what they
were given. `-v` is 3c2's.
"""

import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fake_engine import FakeEngine
from fake_sounddevice import FakeSoundDevice, device
from test_live_cli import assert_one_error, files, run, tone_wav

from lmt import diarize, engines, models
from lmt.live import asr as live_asr
from lmt.live import command
from lmt.live import diarize as live_diarize
from lmt.live.asr import AsrUpdate
from lmt.live.diarize import NullDiarizer
from lmt.transcript import Word

RATE = 16000
SECOND_ASR = "second-asr"
ensure_for_real = models.ensure  # before the fixture replaces it


class FakeASR:
    """`StreamingASR(stream, gap)`: commits one word for each whole second of audio that holds a non-zero
    sample, each ending a sentence, so every word is a segment of its own and silence gives none. Calls `probe`
    on each block, while the session runs."""

    def __init__(self, stream, gap, probe):
        self.stream = stream
        self.gap = gap
        self.position = 0
        self.committed = 0
        self._probe = probe
        self._loud = set()

    def feed(self, samples) -> AsrUpdate:
        self._probe()
        start, self.position = self.position, self.position + len(samples)
        self._loud.update(np.unique((start + np.flatnonzero(samples)) // RATE).tolist())
        words = []
        while (self.committed + 1) * RATE <= self.position:
            k = self.committed
            if k in self._loud:
                words.append(Word(k + 0.2, k + 0.6, f"Word{k}.", 0.9))
            self.committed += 1
        return AsrUpdate(words, self.committed * RATE)

    def flush(self) -> AsrUpdate:
        return AsrUpdate([], self.position)


class FakeDiarizer(NullDiarizer):
    """`OnlineDiarizer(segmentation, embedding, config)`, recording what it was built with."""

    def __init__(self, segmentation, embedding, config):
        super().__init__()
        self.segmentation, self.embedding, self.config = segmentation, embedding, config


@pytest.fixture
def stages(tmp_path, monkeypatch):
    """The fakes `load_stages` meets, and what each was given: `ensured` (the model names asked of
    `models.ensure`, per call), `engines` (engines.load's `(name, offline)`), `segmentation_dirs`, `embedding_dirs`,
    `asrs` and `diarizers` (the stages built). Set `probe` to run a check on each block the session feeds."""
    rec = SimpleNamespace(ensured=[], engines=[], segmentation_dirs=[], embedding_dirs=[], asrs=[], diarizers=[],
                          probe=lambda: None)
    cache = tmp_path / "cache"

    def ensure(needed, offline=False):
        rec.ensured.append([m.name for m in needed])
        return {m.name: cache / m.name for m in needed}

    def load(name, *, offline):
        rec.engines.append((name, offline))
        return FakeEngine(name=name, word_gap={"kyutai": 0.24, "apple": 0.0}[name])

    def streaming(stream, gap):
        rec.asrs.append(FakeASR(stream, gap, lambda: rec.probe()))
        return rec.asrs[-1]

    def online(segmentation, embedding, config):
        rec.diarizers.append(FakeDiarizer(segmentation, embedding, config))
        return rec.diarizers[-1]

    def loader(found):
        def load_model(model_dir):
            found.append(Path(model_dir))
            return f"model:{Path(model_dir).name}"
        return load_model

    monkeypatch.setattr(models, "ensure", ensure)
    monkeypatch.setattr(engines, "load", load)
    monkeypatch.setattr(diarize, "load_segmentation", loader(rec.segmentation_dirs))
    monkeypatch.setattr(diarize, "load_embedding", loader(rec.embedding_dirs))
    monkeypatch.setattr(diarize, "scratch_matplotlib", lambda: tmp_path)
    monkeypatch.setattr(live_asr, "StreamingASR", streaming)
    monkeypatch.setattr(live_diarize, "OnlineDiarizer", online)
    rec.cache = cache
    return rec


def stand_in(monkeypatch, *, seconds=2.0, silent=False, devices=None, channels=1):
    """The stand-in in `sys.modules`: by default a 1-channel microphone (the default input, index 0), a
    2-channel interface (index 1) and an output device, delivering `seconds` of a 48 kHz tone, or of exact zeros
    with `silent`, in `channels` channels, those of the device the test opens."""
    t = np.arange(int(seconds * 48000)) / 48000
    tone = np.zeros_like(t) if silent else 0.1 * np.sin(2 * np.pi * 440 * t)
    if devices is None:
        devices = [device("Built-in Microphone", 1), device("USB Audio Interface", 2), device("Speakers", 0, 2)]
    fake = FakeSoundDevice(devices, data=np.stack([tone] * channels, axis=1))
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


def transcript(out_dir, stem="m"):
    return json.loads((out_dir / f"{stem}.json").read_text(encoding="utf-8"))


def warnings_in(err):
    return [line for line in err.splitlines() if line.startswith("lmt: warning:")]


# What each option does (G2-7: each option has a test; G2-7 says G1-6, one test per option, covers lmt live too).

@pytest.mark.criterion("G1-6", "G2-7")
@pytest.mark.parametrize(("argv", "expected"), [(["--max-speakers", "3"], 3), ([], 20)])
def test_max_speakers(tmp_path, monkeypatch, stages, argv, expected):
    stand_in(monkeypatch)
    code, _, err = run("-o", str(tmp_path), "-n", "m", "--duration", "1.5", *argv)
    assert (code, err) == (0, "")
    [diarizer] = stages.diarizers
    assert diarizer.config.max_speakers == expected
    assert diarizer.config == live_diarize.DiarConfig(max_speakers=expected)  # nothing else changed
    assert stages.segmentation_dirs == [stages.cache / diarize.SEGMENTATION]
    assert stages.embedding_dirs == [stages.cache / diarize.EMBEDDING]
    assert transcript(tmp_path)["models"]["diarization"]["name"] == diarize.DIARIZATION


@pytest.mark.criterion("G1-6", "G2-7")
def test_no_diarize(tmp_path, monkeypatch, stages):
    stand_in(monkeypatch, seconds=3.5)
    code, _, err = run("-o", str(tmp_path), "-n", "m", "--duration", "3", "--no-diarize")
    assert (code, err) == (0, "")
    assert stages.diarizers == [] and stages.segmentation_dirs == [] and stages.embedding_dirs == []
    assert stages.ensured == [[]]  # the engine ensures its own model
    doc = transcript(tmp_path)
    assert len(doc["segments"]) == 3
    assert {s["speaker"] for s in doc["segments"]} == {"S1"}
    assert doc["models"]["diarization"] is None


@pytest.mark.criterion("G1-6", "G2-7")
@pytest.mark.parametrize(("argv", "expected", "gap"), [([], "kyutai", 0.24), (["--engine", "kyutai"], "kyutai", 0.24),
                                                       (["--engine", "apple"], "apple", 0.0)])
def test_engine(tmp_path, monkeypatch, stages, argv, expected, gap):
    stand_in(monkeypatch)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    code, _, err = run("-o", str(out_dir), "-n", "m", "--duration", "1.5", *argv)
    assert (code, err) == (0, "")
    assert stages.engines == [(expected, False)]
    assert stages.ensured == [[diarize.SEGMENTATION, diarize.EMBEDDING]]
    assert stages.asrs[0].gap == gap
    assert transcript(out_dir)["models"]["asr"]["engine"] == expected


@pytest.mark.criterion("G2-7")
def test_an_engine_that_cant_run_exits_5_before_capture(tmp_path, monkeypatch, stages):
    fake = stand_in(monkeypatch)

    def load(name, *, offline):
        raise engines.ModelError(f"--engine {name}", "not in this build")

    monkeypatch.setattr(engines, "load", load)
    code, out, err = run("-o", str(tmp_path), "-n", "m", "--duration", "1.5", "--engine", "apple")
    assert (code, out, err) == (5, "", "lmt: error: --engine apple: not in this build\n")
    assert list(tmp_path.glob("m.*")) == []
    assert not fake.opened and stages.asrs == [] and stages.diarizers == []


@pytest.mark.criterion("G1-6", "G2-7")
@pytest.mark.parametrize("spec", ["1", "usb audio"])
def test_device(tmp_path, monkeypatch, stages, spec):
    fake = stand_in(monkeypatch, channels=2)
    code, _, err = run("-o", str(tmp_path), "-n", "m", "--duration", "1.5", "--device", spec)
    assert (code, err) == (0, "")
    assert [s.device for s in fake.streams] == [1]
    assert fake.streams[0].channels == 2
    assert transcript(tmp_path)["source"]["device"] == "USB Audio Interface"


@pytest.mark.criterion("G1-6", "G2-7")
def test_formats(tmp_path, monkeypatch, stages):
    stand_in(monkeypatch, seconds=2.5)
    txt = tmp_path / "m.txt"
    seen = []
    stages.probe = lambda: seen.append(txt.exists())
    code, out, err = run("-o", str(tmp_path), "-n", "m", "--duration", "2", "-f", "srt,json")
    assert (code, err) == (0, "")
    assert seen and not any(seen)  # not at capture start, nor while the session ran
    assert files(tmp_path) == ["m.json", "m.srt"]
    assert out == "[00:00:00] Speaker 1: Word0.\n[00:00:01] Speaker 1: Word1.\n"  # the text form still prints
    assert len(transcript(tmp_path)["segments"]) == 2


@pytest.mark.criterion("G1-6", "G2-7")
def test_offline_with_an_empty_cache(tmp_path, monkeypatch, stages):
    monkeypatch.setattr(models, "ensure", ensure_for_real)
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("LMT_CACHE_DIR", str(cache))
    fake = stand_in(monkeypatch)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    code, out, err = run("-o", str(out_dir), "-n", "m", "--duration", "1", "--offline")
    assert code == 5, err
    assert out == ""
    assert len(err.splitlines()) == 1, err
    assert err.startswith("lmt: error: ") and "--offline never downloads" in err, err
    assert files(out_dir) == [] and files(cache) == []
    assert not fake.opened
    assert stages.engines == [("kyutai", True)] and stages.asrs == []


@pytest.mark.criterion("G1-6", "G2-7")
def test_output_dir_and_name(tmp_path, monkeypatch, stages):
    stand_in(monkeypatch)
    work, out_dir = tmp_path / "work", tmp_path / "out"
    work.mkdir()
    out_dir.mkdir()
    monkeypatch.chdir(work)
    code, _, err = run("-o", str(out_dir), "-n", "meeting", "--duration", "1.5", "--save-audio")
    assert (code, err) == (0, "")
    assert files(out_dir) == [f"meeting.{fmt}" for fmt in ("json", "srt", "txt", "vtt", "wav")]
    assert files(work) == []
    assert transcript(out_dir, "meeting")["source"]["file"] == "meeting"


@pytest.mark.criterion("G1-6", "G2-7")
def test_overwrite(tmp_path, monkeypatch, stages):
    stand_in(monkeypatch)
    names = [f"m.{fmt}" for fmt in ("json", "srt", "txt", "vtt", "wav")]
    for name in names:
        (tmp_path / name).write_text("old\n", encoding="utf-8")
    code, _, err = run("-o", str(tmp_path), "-n", "m", "--duration", "1.5", "--save-audio", "--overwrite")
    assert (code, err) == (0, "")
    assert files(tmp_path) == names
    assert all((tmp_path / name).read_bytes() != b"old\n" for name in names)
    assert transcript(tmp_path)["source"]["file"] == "m"
    assert (tmp_path / "m.txt").read_text(encoding="utf-8") == "[00:00:00] Speaker 1: Word0.\n"
    with wave.open(str(tmp_path / "m.wav"), "rb") as w:
        assert (w.getframerate(), w.getnframes()) == (RATE, round(1.5 * RATE))


# G2-5 (ii) through the command: no input device.

@pytest.mark.criterion("G2-5")
def test_no_input_device(tmp_path, monkeypatch, stages):
    fake = stand_in(monkeypatch, devices=[device("Speakers", 0, 2)])
    code, out, err = run("-o", str(tmp_path), "-n", "m", "--duration", "1")
    assert_one_error(code, out, err, 3, "--device")
    assert err == "lmt: error: --device: no input device found\n"
    assert files(tmp_path) == []
    assert not fake.opened
    assert stages.ensured == []
    assert run("--list-devices") == (0, "No input devices.\n", "")


@pytest.mark.criterion("G2-5")
def test_no_input_device_for_real(tmp_path, monkeypatch, stages):
    """The same through the real `sounddevice`, on a machine with no input device. macos-26's runner has virtual
    ones (#573), so there it skips, and the stand-in tests above carry G2-5 (ii)."""
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    from lmt.errors import InputError
    from lmt.live import mic
    try:
        if mic.input_devices():
            pytest.skip("this machine has an input device")
    except InputError as e:
        pytest.skip(str(e))
    code, out, err = run("-o", str(tmp_path), "-n", "m", "--duration", "1")
    assert (code, out, err) == (3, "", "lmt: error: --device: no input device found\n")
    assert files(tmp_path) == []
    assert run("--list-devices") == (0, "No input devices.\n", "")


# G2-5 (iii) through the command: silence.

@pytest.mark.criterion("G2-5")
def test_silent_device_warns_once_and_exits_0(tmp_path, monkeypatch, stages):
    fake = stand_in(monkeypatch, seconds=5, silent=True)
    code, out, err = run("-o", str(tmp_path), "-n", "m", "--duration", "4")
    assert code == 0, err
    assert out == ""
    assert len(err.splitlines()) == 1, err
    [warning] = warnings_in(err)
    assert warning.startswith("lmt: warning: Built-in Microphone: no sound for 3 s;"), warning
    assert "Privacy & Security, Microphone" in warning
    assert fake.opened
    doc = transcript(tmp_path)
    assert doc["segments"] == []
    assert doc["source"]["duration"] == 4.0
    assert files(tmp_path) == [f"m.{fmt}" for fmt in ("json", "srt", "txt", "vtt")]


def zeros_wav(path, seconds=4.0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.zeros(int(seconds * RATE), "<i2").tobytes())
    return path


@pytest.mark.criterion("G2-5")
def test_silent_file_gets_no_warning_and_exits_0(tmp_path, monkeypatch, stages):
    monkeypatch.setattr(command, "LOCKSTEP", True)
    wav = zeros_wav(tmp_path / "quiet.wav")
    code, out, err = run("--from-file", str(wav), "-o", str(tmp_path), "-n", "m")
    assert code == 0, err
    assert warnings_in(err) == [] and err == ""
    assert out == ""
    doc = transcript(tmp_path)
    assert doc["segments"] == []
    assert doc["source"] == {"file": "quiet.wav", "duration": 4.0, "live": True}


def test_the_fake_asr_hears_a_tone(tmp_path, monkeypatch, stages):
    """The silence cases mean something: the same fakes give segments for sound."""
    monkeypatch.setattr(command, "LOCKSTEP", True)
    wav = tone_wav(tmp_path / "talk.wav", seconds=4.0)
    code, _, err = run("--from-file", str(wav), "-o", str(tmp_path), "-n", "m", "-f", "json")
    assert (code, err) == (0, "")
    assert len(transcript(tmp_path)["segments"]) == 4
