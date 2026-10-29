"""Decode (design/internal/g1-slice.md, S2; design/internal/g1-stage2.md, P1), with no model."""

import struct
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


def test_a_wav_with_truncated_data_is_an_input_error(tmp_path):
    clip = CLIP.read_bytes()
    assert clip[12:16] == b"fmt " and clip[36:40] == b"data"  # the clip's header is 44 bytes
    declared = struct.unpack("<I", clip[40:44])[0]
    assert declared > 16_000
    path = tmp_path / "truncated.wav"
    path.write_bytes(clip[:44 + 16_000])
    with pytest.raises(InputError) as caught:
        audio.decode(path)
    assert caught.value.exit_code == 3
    assert caught.value.subject == str(path)
    assert caught.value.problem == f"truncated: its data chunk declares {declared} bytes, the file holds 16000"


def test_a_list_chunk_before_data_decodes(tmp_path):
    samples = tone(16_000)
    plain = write_wav(tmp_path / "plain.wav", samples, 16_000).read_bytes()
    assert plain[36:40] == b"data"
    info = b"INFOx"  # an odd size, so the chunk's pad byte is skipped too
    listed = b"LIST" + struct.pack("<I", len(info)) + info + b"\0"
    body = plain[12:36] + listed + plain[36:]
    path = tmp_path / "listed.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)
    got = audio.decode(path)
    assert got.shape == (16_000,)
    assert numpy.max(numpy.abs(got - samples / 32768)) <= 1e-7


def test_a_data_size_of_0xffffffff_is_no_truncation(tmp_path):
    data = bytearray(write_wav(tmp_path / "tone.wav", tone(16_000), 16_000).read_bytes())
    assert data[36:40] == b"data"
    data[40:44] = struct.pack("<I", 0xFFFFFFFF)
    path = tmp_path / "streamed.wav"
    path.write_bytes(bytes(data))
    try:
        audio.decode(path)
    except InputError as e:
        assert not e.problem.startswith("truncated"), e.problem
