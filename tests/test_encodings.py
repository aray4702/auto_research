"""The reference clip's committed encodings (design/internal/g1-formats.md, "Tests"): each file matches its hash
in sets.toml, so it can't change without its manifest, and has the streams the spec's G1 "Terms" name. No model
and no ffmpeg: PyAV reads them, and `lmt.audio.decode` decodes them."""

import hashlib
import tomllib
import wave
from pathlib import Path

import av
import pytest

from lmt import audio
from lmt.eval import datasets

ROOT = Path(__file__).resolve().parents[1]
SETS = tomllib.loads((datasets.MANIFEST_DIR / "sets.toml").read_text(encoding="utf-8"))
CLIP = next(f for f in SETS["fixture"] if f["name"] == "clip")
FORMATS = ("flac", "mp3", "m4a", "mp4", "mov")
BIT_RATE = 128_000
AAC_TOLERANCE = 0.10  # the encoder's own variation about its 128 kbit/s target
PADDING = 1_600  # samples at 16 kHz, 0.1 s: room for the encoders' padding


def path(fmt):
    return ROOT / Path(CLIP["path"]).with_suffix(f".{fmt}")


def wav_frames():
    with wave.open(str(ROOT / CLIP["path"]), "rb") as w:
        return w.getnframes()


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_file_matches_its_hash_in_sets_toml(fmt):
    expected = {e["format"]: e["sha256"] for e in CLIP["encodings"]}
    assert hashlib.sha256(path(fmt).read_bytes()).hexdigest() == expected[fmt]


@pytest.mark.parametrize("fmt", FORMATS)
def test_one_mono_audio_stream_in_the_terms_codec_and_bit_rate(fmt):
    with av.open(str(path(fmt))) as container:
        assert len(container.streams.audio) == 1
        stream = container.streams.audio[0]
        assert stream.codec_context.channels == 1
        codec, bit_rate, profile = stream.codec_context.codec, stream.bit_rate, stream.codec_context.profile
    if fmt == "flac":
        assert codec.name == "flac"
    elif fmt == "mp3":
        assert codec.id == av.Codec("mp3", "r").id, codec.name  # FFmpeg decodes MP3 by "mp3float" or "mp3"
        assert bit_rate == BIT_RATE
    else:
        assert codec.name == "aac"
        assert profile == "LC"
        assert abs(bit_rate - BIT_RATE) <= AAC_TOLERANCE * BIT_RATE, bit_rate


@pytest.mark.parametrize("fmt", FORMATS)
def test_mp4_and_mov_have_one_video_stream_and_the_audio_files_none(fmt):
    with av.open(str(path(fmt))) as container:
        assert len(container.streams.video) == (1 if fmt in ("mp4", "mov") else 0)


@pytest.mark.parametrize("fmt", FORMATS)
def test_decode_gives_the_wavs_length_within_the_padding(fmt):
    samples = audio.decode(path(fmt))
    assert abs(len(samples) - wav_frames()) <= PADDING, (len(samples), wav_frames())
