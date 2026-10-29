"""Decode (design/internal/g1-slice.md, S2), with no model."""

import wave
from pathlib import Path

import numpy
import pytest

from lmt import audio
from lmt.errors import InputError

ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "tests" / "fixtures" / "clip.wav"


def write_wav(path, samples, rate, channels=1):
    """`samples` as int16, interleaved when `channels` > 1."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(numpy.asarray(samples, dtype="<i2").tobytes())
    return path


def tone(rate, seconds=1.0, hz=440.0):
    t = numpy.arange(round(rate * seconds)) / rate
    return numpy.round(0.5 * 32767 * numpy.sin(2 * numpy.pi * hz * t)).astype(numpy.int16)


def test_a_16_khz_mono_wav_goes_through_unchanged(tmp_path):
    samples = tone(16_000)
    got = audio.decode(write_wav(tmp_path / "tone.wav", samples, 16_000))
    assert got.dtype == numpy.float32
    assert got.shape == (16_000,)
    assert numpy.max(numpy.abs(got - samples / 32768)) <= 1e-7


def test_a_44_1_khz_stereo_wav_becomes_16_khz_mono(tmp_path):
    left = tone(44_100)
    stereo = numpy.stack([left, left], axis=1).reshape(-1)
    got = audio.decode(write_wav(tmp_path / "stereo.wav", stereo, 44_100, channels=2))
    assert got.dtype == numpy.float32
    assert got.ndim == 1
    assert abs(len(got) - 16_000) <= 16


def test_the_clip_decodes_to_its_frames():
    with wave.open(str(CLIP), "rb") as w:
        frames = w.getnframes()
    assert len(audio.decode(CLIP)) == frames


def empty_wav(tmp_path):
    return write_wav(tmp_path / "header-only.wav", [], 16_000)


def random_bytes(tmp_path):
    path = tmp_path / "random.wav"
    path.write_bytes(numpy.random.default_rng(1234).integers(0, 256, 1000, dtype=numpy.uint8).tobytes())
    return path


def text_file(tmp_path):
    path = tmp_path / "notes.wav"
    path.write_text("These are meeting notes, not audio.\n" * 20, encoding="utf-8")
    return path


def empty_file(tmp_path):
    path = tmp_path / "empty.wav"
    path.touch()
    return path


@pytest.mark.parametrize(("make", "problem"), [
    (lambda tmp: tmp / "missing.wav", "no such file"),
    (lambda tmp: tmp, "is a directory"),
    (empty_file, "empty file"),
    (empty_wav, "no audio"),
    (random_bytes, "can't decode: "),
    (text_file, "can't decode: "),
], ids=["missing", "directory", "empty-file", "header-only", "random-bytes", "text-file"])
def test_each_failure_is_an_input_error(tmp_path, make, problem):
    path = make(tmp_path)
    with pytest.raises(InputError) as caught:
        audio.decode(path)
    assert caught.value.exit_code == 3
    assert caught.value.subject == str(path)
    assert caught.value.problem.startswith(problem)
    assert "\n" not in str(caught.value)
