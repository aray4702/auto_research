"""G1-4's checks on the exports (design/internal/g1-stage2.md, P6a): the strict parsers, the ffmpeg
re-encode the spec gives, and each format's words in order. G1-4's test and G3's items call these."""

import html
import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import srt
import webvtt

TXT_STAMP = re.compile(r"\[\d{2,}:\d{2}:\d{2}\] ")
VOICE_TAG = re.compile(r"<v[^>]*>")


def check_srt(path):
    """`srt.parse` with its default strictness, which raises on anything it can't parse, and at least one
    cue. Then the ffmpeg re-encode to SRT."""
    cues = list(srt.parse(Path(path).read_text(encoding="utf-8")))
    assert cues, f"{path}: no cue"
    ffmpeg_reencode(path, "srt")


def check_vtt(path):
    """`webvtt.read`, with at least one caption. Then the ffmpeg re-encode to WebVTT."""
    captions = webvtt.read(str(path))
    assert len(captions) > 0, f"{path}: no cue"
    ffmpeg_reencode(path, "webvtt")


def ffmpeg_reencode(path, fmt) -> subprocess.CompletedProcess:
    """The spec's command, exactly. Fails, naming the file and the command, when no ffmpeg is on PATH (it
    never skips), or when the command exits other than 0 or prints anything."""
    argv = ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:s:0", "-c:s", fmt, "-f", fmt, "-y", "/dev/null"]
    command = shlex.join(argv)
    if shutil.which("ffmpeg") is None:
        pytest.fail(f"{path}: no ffmpeg on PATH to run `{command}`", pytrace=False)
    r = subprocess.run(argv, capture_output=True, check=False)
    stdout, stderr = (out.decode("utf-8", "replace") for out in (r.stdout, r.stderr))
    if r.returncode != 0 or stdout or stderr:
        pytest.fail(f"{path}: `{command}` exited {r.returncode}, with stdout {stdout!r} and stderr {stderr!r}",
                    pytrace=False)
    return r


def after_label(text, labels, path):
    """`text` after its `<label>: `, which must be one of `labels`."""
    for label in labels:
        if text.startswith(f"{label}: "):
            return text[len(label) + 2:]
    pytest.fail(f"{path}: {text!r} doesn't start with one of the labels {list(labels)!r}", pytrace=False)


def words(path, fmt, labels) -> list[str]:
    """The format's words in order. For `json`, each segment's `text` split on white space. For `txt`, each
    line after its `] <label>: `. For `srt`, each cue's lines joined, after its `<label>: `. For `vtt`, each
    cue's text after its voice tag and its `<label>: `, with character references decoded."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if fmt == "json":
        return [w for segment in json.loads(text)["segments"] for w in segment["text"].split()]
    if fmt == "txt":
        lines = []
        for line in text.splitlines():
            stamp = TXT_STAMP.match(line)
            assert stamp, f"{path}: {line!r} doesn't start with [HH:MM:SS]"
            lines.append(line[stamp.end():])
    elif fmt == "srt":
        lines = [" ".join(cue.content.splitlines()) for cue in srt.parse(text)]
    elif fmt == "vtt":
        lines = []
        for caption in webvtt.read(str(path)):
            raw = " ".join(caption.lines)
            voice = VOICE_TAG.match(raw)
            assert voice, f"{path}: the cue {raw!r} has no voice tag"
            lines.append(html.unescape(raw[voice.end():]))
    else:
        raise ValueError(f"unknown format {fmt!r}")
    return [w for line in lines for w in after_label(line, labels, path).split()]
