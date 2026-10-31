"""Decode any input PyAV reads to 16 kHz mono float32 (design/internal/g1-slice.md, S2).

Every failure is an InputError naming the file (exit 3). FFmpeg's own log stays off (`av.logging` at its
default), so a bad input gives stderr only the one `lmt: error:` line. FFmpeg decodes a WAV whose data is
cut short without an error, so decode checks a WAV's `data` chunk first (design/internal/g1-stage2.md, P1).
`Resampler` takes pushed float32 blocks through the same settings, `RESAMPLE` (design/internal/g2-stage1.md, 4b),
to 16 kHz or another `out_rate`.
"""

import struct
from fractions import Fraction
from pathlib import Path

import av
import numpy

from lmt.errors import InputError

SAMPLE_RATE = 16_000
RESAMPLE = {"format": "flt", "layout": "mono", "rate": SAMPLE_RATE}


class Resampler:
    """Float32 `(frames, channels)` blocks at `in_rate`, pushed one at a time, to mono float32 at `out_rate`
    (16 kHz, or Kyutai's 24 kHz: design/internal/g4-stage1.md §2)."""

    def __init__(self, in_rate: int, channels: int, out_rate: int = SAMPLE_RATE):
        self.in_rate = in_rate
        self.channels = channels
        self.out_rate = out_rate
        self._layout = {1: "mono", 2: "stereo"}.get(channels, f"{channels}c")
        self._resampler = av.AudioResampler(**{**RESAMPLE, "rate": out_rate})
        self._pts = 0

    def push(self, samples: numpy.ndarray) -> numpy.ndarray:
        samples = numpy.asarray(samples, dtype=numpy.float32).reshape(-1, self.channels)
        if len(samples) == 0:
            return numpy.zeros(0, numpy.float32)
        frame = av.AudioFrame.from_ndarray(numpy.ascontiguousarray(samples.T), format="fltp", layout=self._layout)
        frame.sample_rate = self.in_rate
        frame.time_base = Fraction(1, self.in_rate)
        frame.pts = self._pts
        self._pts += len(samples)
        return self._join(self._resampler.resample(frame))

    def flush(self) -> numpy.ndarray:
        return self._join(self._resampler.resample(None))

    @staticmethod
    def _join(frames):
        pieces = [f.to_ndarray().reshape(-1) for f in frames]
        return numpy.concatenate(pieces).astype(numpy.float32, copy=False) if pieces else numpy.zeros(0, numpy.float32)


def decode(path: str | Path) -> numpy.ndarray:
    """The first audio stream's samples: float32, mono, 16 kHz, one dimension."""
    path = Path(path)
    if not path.exists():
        raise InputError(str(path), "no such file")
    if path.is_dir():
        raise InputError(str(path), "is a directory")
    if path.stat().st_size == 0:
        raise InputError(str(path), "empty file")
    try:
        _check_wav_data(path)
    except OSError as e:
        raise _cant_decode(path, e) from None
    try:
        container = av.open(str(path))
    except Exception as e:  # noqa: BLE001 -- nothing PyAV raises may escape decode
        raise _cant_decode(path, e) from None
    try:
        if not container.streams.audio:
            raise InputError(str(path), "no audio track")
        resampler = av.AudioResampler(**RESAMPLE)
        pieces = []
        for frame in container.decode(container.streams.audio[0]):
            pieces.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
        pieces.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    except InputError:
        raise
    except Exception as e:  # noqa: BLE001 -- nothing PyAV raises may escape decode
        raise _cant_decode(path, e) from None
    finally:
        container.close()
    samples = numpy.concatenate(pieces) if pieces else numpy.zeros(0, numpy.float32)
    if samples.size == 0:
        raise InputError(str(path), "no audio")
    return samples.astype(numpy.float32, copy=False)


def _check_wav_data(path):
    """For a RIFF/WAVE file, an InputError if its first `data` chunk runs past the end of the file. 0 and
    0xFFFFFFFF are the sizes streaming writers leave, so they pass; a file with no `data` chunk is PyAV's."""
    length = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:] != b"WAVE":
            return
        offset = 12
        while offset + 8 <= length:
            f.seek(offset)
            chunk, size = struct.unpack("<4sI", f.read(8))
            if chunk == b"data":
                held = length - offset - 8
                if size not in (0, 0xFFFFFFFF) and size > held:
                    raise InputError(str(path), f"truncated: its data chunk declares {size} bytes, "
                                                f"the file holds {held}")
                return
            offset += 8 + size + (size & 1)


def _cant_decode(path, e):
    lines = str(e).strip().splitlines()
    return InputError(str(path), f"can't decode: {lines[0] if lines else type(e).__name__}")
