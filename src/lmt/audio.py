"""Decode any input PyAV reads to 16 kHz mono float32 (design/internal/g1-slice.md, S2).

Every failure is an InputError naming the file (exit 3). FFmpeg's own log stays off (`av.logging` at its
default), so a bad input gives stderr only the one `lmt: error:` line. FFmpeg decodes a WAV whose data is
cut short without an error, so decode checks a WAV's `data` chunk first (design/internal/g1-stage2.md, P1).
"""

import struct
from pathlib import Path

import av
import numpy

from lmt.errors import InputError

SAMPLE_RATE = 16_000


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
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
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
