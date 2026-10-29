"""The ASR stage (design/internal/g1-slice.md, S2): its fixed settings with a fake model, and the first WER
reading on the reference clip with the real one."""

import os
import subprocess
import sys
import types
from pathlib import Path
from typing import ClassVar

import numpy
import pytest

from lmt import asr, audio, models
from lmt.transcript import Word

ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "tests" / "fixtures" / "clip.wav"
CLIP_TEXT = ROOT / "tests" / "fixtures" / "clip.txt"


def fake_word(start, end, word, probability):
    return types.SimpleNamespace(start=start, end=end, word=word, probability=probability)


class FakeWhisperModel:
    """Records its arguments, and serves `segments` from transcribe as a generator, as faster-whisper does."""

    instances: ClassVar[list] = []
    segments: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.calls = []
        FakeWhisperModel.instances.append(self)

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return (s for s in FakeWhisperModel.segments), types.SimpleNamespace(language="en")


@pytest.fixture
def fake(monkeypatch):
    """faster_whisper replaced by a module whose WhisperModel is the fake, and onnxruntime by one whose
    `severities` records each severity set, with how many WhisperModels existed then. So no test imports the
    real onnxruntime or sets its process-wide severity."""
    monkeypatch.setattr(FakeWhisperModel, "instances", [])
    monkeypatch.setattr(FakeWhisperModel, "segments", [])
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisperModel))
    severities = []
    monkeypatch.setitem(sys.modules, "onnxruntime", types.SimpleNamespace(
        severities=severities,
        set_default_logger_severity=lambda level: severities.append((level, len(FakeWhisperModel.instances)))))
    return FakeWhisperModel


@pytest.mark.criterion("G1-7", "G1-12")
class TestFixedSettings:
    """The settings fixed before any WER is measured: a change to any of them fails here."""

    def test_threads(self):
        assert asr.THREADS == min(8, os.cpu_count() or 1)

    def test_load(self, fake, tmp_path):
        asr.load(tmp_path)
        (model,) = fake.instances
        assert model.args == (str(tmp_path),)
        assert model.kwargs == {"device": "cpu", "compute_type": "int8", "cpu_threads": asr.THREADS,
                                "num_workers": 1, "local_files_only": True}

    def test_transcribe(self, fake, tmp_path):
        samples = numpy.zeros(16_000, numpy.float32)
        asr.load(tmp_path).transcribe(samples)
        ((got, kwargs),) = fake.instances[0].calls
        assert got is samples
        assert kwargs == {"language": "en", "task": "transcribe", "beam_size": 5, "temperature": 0.0,
                          "condition_on_previous_text": False, "word_timestamps": True,
                          "vad_filter": True, "initial_prompt": None}
        assert type(kwargs["temperature"]) is float  # a single temperature: no fallback
        assert asr.BEAM_SIZE == 5

    def test_initial_prompt_is_passed(self, fake, tmp_path):
        asr.load(tmp_path).transcribe(numpy.zeros(10, numpy.float32), initial_prompt="Kubernetes")
        assert fake.instances[0].calls[0][1]["initial_prompt"] == "Kubernetes"


def test_words_are_stripped_and_empty_ones_dropped(fake, tmp_path):
    fake.segments = [
        types.SimpleNamespace(words=[fake_word(0.0, 0.42, " Hello", 0.9), fake_word(0.42, 0.5, "  ", 0.1)]),
        types.SimpleNamespace(words=None),
        types.SimpleNamespace(words=[fake_word(1.234567, 1.9, " world. ", 0.75)]),
    ]
    words = asr.load(tmp_path).transcribe(numpy.zeros(10, numpy.float32))
    assert words == [Word(0.0, 0.42, "Hello", 0.9), Word(1.234567, 1.9, "world.", 0.75)]


def test_onnxruntime_is_quieted_before_the_model_loads(fake, tmp_path):
    asr.load(tmp_path)
    assert sys.modules["onnxruntime"].severities == [(3, 0)]  # errors and fatal only, before any WhisperModel


def test_the_vad_leaves_stderr_empty():
    """onnxruntime's warnings (on Linux, GetPciBusId's) never reach stderr, so G1-11's one line holds. A fresh
    process, so its environment is created here; the VAD model ships in faster-whisper's wheel."""
    code = ("import numpy\n"
            "from lmt import asr\n"
            "asr.quiet_onnxruntime()\n"
            "from faster_whisper.vad import get_speech_timestamps\n"
            "get_speech_timestamps(numpy.zeros(16_000, numpy.float32))\n")
    result = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.models
def test_the_clip_transcribes_within_its_duration(monkeypatch):
    """The slice's first WER reading on the clip. It reports the WER and doesn't assert the bound: that is
    G1-1's, on the product run."""
    from lmt.eval.score import wer

    monkeypatch.setenv("HF_HUB_OFFLINE", "0")  # so teardown undoes ensure()'s
    manifest = models.load_manifest()
    model = manifest.get(manifest.default_asr)
    model_dir = models.ensure([model])[model.name]
    samples = audio.decode(CLIP)
    duration = len(samples) / audio.SAMPLE_RATE

    words = asr.load(model_dir).transcribe(samples)

    assert words
    assert all(0 <= w.start <= w.end <= duration for w in words)
    reading = wer([(CLIP_TEXT.read_text(encoding="utf-8"), " ".join(w.text for w in words))])
    line = f"ASR alone ({model.name}) on tests/fixtures/clip.wav: WER {reading:.2%}, {len(words)} words"
    print(line)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(line + "\n")
