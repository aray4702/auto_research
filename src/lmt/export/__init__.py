"""The exporters, and writing their files so that a failed run leaves none (design/public-api.md,
"Output formats" and "Exit codes")."""

import contextlib
import os
import tempfile
from pathlib import Path

from lmt.errors import InputError, NotBuilt, UsageError
from lmt.export import json, srt, txt, vtt

FORMATS = ("txt", "srt", "vtt", "json")
RENDERERS = {"txt": txt.render, "srt": srt.render, "vtt": vtt.render, "json": json.render}


def render(transcript, fmt: str) -> str:
    if fmt not in RENDERERS:
        raise NotBuilt("--formats")
    return RENDERERS[fmt](transcript)


def outputs(out_dir: Path, stem: str, formats) -> list[Path]:
    return [Path(out_dir) / f"{stem}.{fmt}" for fmt in formats]


def check(out_dir: Path, stem: str, formats, *, overwrite: bool) -> list[Path]:
    """The output paths, in `formats` order, once the directory is writable and, without `overwrite`,
    none of them exists. Runs before any work. The directory is never created."""
    out_dir = Path(out_dir)
    if not out_dir.exists():
        raise InputError(str(out_dir), "no such directory")
    if not out_dir.is_dir():
        raise InputError(str(out_dir), "not a directory")
    if not os.access(out_dir, os.W_OK | os.X_OK):
        raise InputError(str(out_dir), "directory not writable")
    paths = outputs(out_dir, stem, formats)
    if not overwrite:
        for path in paths:
            if os.path.lexists(path):
                raise UsageError(str(path), "exists; use --overwrite to replace it")
    return paths


def write(transcript, out_dir: Path, stem: str, formats, *, overwrite: bool) -> list[Path]:
    """Every format to a temporary file, then all renamed into place; on any exception, none."""
    paths = check(out_dir, stem, formats, overwrite=overwrite)
    temps = []
    try:
        for fmt in formats:
            text = render(transcript, fmt)
            fd, temp = tempfile.mkstemp(prefix=f".{stem}.", suffix=f".{fmt}.tmp", dir=out_dir)
            temps.append(temp)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        for temp, path in zip(temps, paths):
            os.replace(temp, path)
    except BaseException:  # KeyboardInterrupt too
        for temp in temps:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp)
        raise
    return paths
