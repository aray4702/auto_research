"""The live outputs (design/internal/g2-stage1.md §3b, item 3b2): the growing `<stem>.txt`, the `<stem>.wav` written
as it is captured, the final formats saved through temporary files, and the live transcript. Positions are samples
at 16 kHz.
"""

import contextlib
import os
import secrets
import struct
import threading
from pathlib import Path

import numpy as np

from lmt import __version__, export
from lmt.export import txt
from lmt.transcript import SCHEMA_VERSION, Segment, Transcript

RATE = 16000


class GrowingText:
    """`<stem>.txt` as the session goes: one line per closed segment, flushed at once, so a reader who has seen a
    segment's event finds its line (G2-4)."""

    def __init__(self, path):
        # Open for the session, and truncated, since the command checked the path before capture.
        self._file = open(path, "w", encoding="utf-8", newline="")  # noqa: SIM115

    def append(self, segment: Segment, label: str) -> None:
        self._file.write(txt.render_segment(segment, label))  # the line ends in its own newline
        self._file.flush()

    def close(self) -> None:
        self._file.close()


class WavWriter:
    """`<stem>.wav`, 16 kHz mono 16-bit PCM. The header's sizes are rewritten after each block, so the file is a valid
    WAV of everything so far if the process dies (public-api.md, "What a failed session keeps")."""

    def __init__(self, path):
        self._file = open(path, "wb")  # noqa: SIM115 (open for the session)
        self._frames = 0
        self._file.write(_header(0))
        self._file.flush()

    def write(self, block: np.ndarray) -> None:
        pcm = np.round(np.clip(np.asarray(block, dtype=np.float64), -1.0, 1.0) * 32767).astype("<i2")
        self._file.seek(0, os.SEEK_END)
        self._file.write(pcm.tobytes())
        self._frames += len(pcm)
        self._file.seek(0)
        self._file.write(_header(self._frames))
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def _header(frames: int) -> bytes:
    data = frames * 2
    return (b"RIFF" + struct.pack("<I", 36 + data) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
            + b"data" + struct.pack("<I", data))


def save(transcript: Transcript, out_dir, stem: str, formats, *, abandon: threading.Event) -> list[Path]:
    """Every format to a temporary file in `out_dir`, then all renamed into place, unless `abandon` is set before
    the renames. Temporary files not renamed are removed on any exception, `KeyboardInterrupt` too, so a failed save
    leaves neither them nor a final format. A file's mode is what `export.write` gives (#286). The paths renamed into
    place, none when abandoned."""
    paths = export.outputs(out_dir, stem, formats)
    temps = []
    try:
        for fmt in formats:
            text = export.render(transcript, fmt)
            temp = Path(out_dir) / f".{stem}.{secrets.token_hex(8)}.{fmt}.tmp"
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            temps.append(temp)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        if abandon.is_set():
            return []
        for path in paths:
            os.replace(temps[0], path)
            temps.pop(0)
    finally:
        for temp in temps:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp)
    return paths


def live_transcript(result, source: dict, models: dict) -> Transcript:
    """The transcript of a session from its `SessionResult` (3a3). `source` is the `start` event's: `file` (the
    file's base name, or the stem), and `device` for a device. The duration is the samples to the millisecond,
    rounded down as `align.build` does, so no time passes the audio's end."""
    full = {"file": source["file"], "duration": result.samples // 16 / 1000, "live": True}
    if "device" in source:
        full["device"] = source["device"]
    return Transcript(SCHEMA_VERSION, __version__, full, models, tuple(result.speakers), tuple(result.segments))
