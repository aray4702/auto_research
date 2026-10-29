"""G1-4's helpers (tests/subtitle_checks.py) on the committed export goldens, with a fake ffmpeg, so no model
and no ffmpeg is needed (design/internal/g1-stage2.md, P6a)."""

import json
import os
import shlex
from pathlib import Path

import pytest
import srt
import test_export_formats
import webvtt
from subtitle_checks import check_srt, check_vtt, ffmpeg_reencode, words

from lmt import export

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "export"
FORMATS = ("json", "txt", "srt", "vtt")

pytestmark = pytest.mark.criterion("G1-4")


def golden(fmt):
    return GOLDEN / f"transcript.{fmt}"


def command(path, fmt):
    return ["-v", "error", "-i", str(path), "-map", "0:s:0", "-c:s", fmt, "-f", fmt, "-y", "/dev/null"]


@pytest.fixture
def fake_ffmpeg(tmp_path, monkeypatch):
    """Puts first on PATH an `ffmpeg` that writes its arguments, one per line, to `args` beside it, prints
    `stdout` and `stderr` and exits with `status`. Returns `args`'s path."""
    def make(stdout="", stderr="", status=0):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args = bin_dir / "args"
        script = ['#!/bin/sh', f'printf "%s\\n" "$@" > {shlex.quote(str(args))}']
        if stdout:
            script.append(f"echo {shlex.quote(stdout)}")
        if stderr:
            script.append(f"echo {shlex.quote(stderr)} >&2")
        script.append(f"exit {status}")
        ffmpeg = bin_dir / "ffmpeg"
        ffmpeg.write_text("\n".join(script) + "\n", encoding="utf-8")
        ffmpeg.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        return args
    return make


def recorded(args):
    return args.read_text(encoding="utf-8").splitlines()


def test_goldens_pass(fake_ffmpeg):
    args = fake_ffmpeg()
    check_srt(golden("srt"))
    assert recorded(args) == command(golden("srt"), "srt")
    check_vtt(golden("vtt"))
    assert recorded(args) == command(golden("vtt"), "webvtt")


@pytest.mark.parametrize(("stdout", "stderr", "status"), [("", "", 1), ("a line", "", 0), ("", "a line", 0)],
                         ids=["exits-1", "stdout", "stderr"])
def test_reencode_fails(fake_ffmpeg, stdout, stderr, status):
    fake_ffmpeg(stdout, stderr, status)
    with pytest.raises(pytest.fail.Exception) as failure:
        ffmpeg_reencode(golden("srt"), "srt")
    assert str(golden("srt")) in str(failure.value)
    assert shlex.join(["ffmpeg", *command(golden("srt"), "srt")]) in str(failure.value)


def test_reencode_fails_without_ffmpeg(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(pytest.fail.Exception) as failure:
        ffmpeg_reencode(golden("vtt"), "webvtt")
    assert str(golden("vtt")) in str(failure.value)
    assert shlex.join(["ffmpeg", *command(golden("vtt"), "webvtt")]) in str(failure.value)


def test_srt_parser_is_strict(fake_ffmpeg, tmp_path):
    fake_ffmpeg()
    bad = tmp_path / "bad.srt"
    bad.write_text(golden("srt").read_text(encoding="utf-8").replace("00:00:00,480", "00:00:0x,480", 1),
                   encoding="utf-8")
    with pytest.raises(srt.SRTParseError):
        check_srt(bad)
    empty = tmp_path / "empty.srt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(AssertionError, match="no cue"):
        check_srt(empty)


def test_vtt_parser_is_strict(fake_ffmpeg, tmp_path):
    fake_ffmpeg()
    headless = tmp_path / "headless.vtt"
    headless.write_text(golden("vtt").read_text(encoding="utf-8").removeprefix("WEBVTT\n\n"), encoding="utf-8")
    with pytest.raises(webvtt.errors.MalformedFileError):
        check_vtt(headless)
    empty = tmp_path / "empty.vtt"
    empty.write_text("WEBVTT\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="no cue"):
        check_vtt(empty)


def test_words_agree(tmp_path):
    # The committed transcript.json is test_export.py's two-segment small(); the other three goldens are
    # test_export_formats.py's, so the JSON is rendered from that one.
    rendered = tmp_path / "transcript.json"
    rendered.write_text(export.render(test_export_formats.small(), "json"), encoding="utf-8")
    doc = json.loads(rendered.read_text(encoding="utf-8"))
    labels = [s["label"] for s in doc["speakers"]]
    assert "José & Ana" in labels
    expected = [w for s in doc["segments"] for w in s["text"].split()]
    paths = {"json": rendered, **{fmt: golden(fmt) for fmt in ("txt", "srt", "vtt")}}
    for fmt in FORMATS:
        assert words(paths[fmt], fmt, labels) == expected, fmt
    vtt = words(golden("vtt"), "vtt", labels)
    i = vtt.index("Yes,")
    assert vtt[i:i + 4] == ["Yes,", "the", "Q&A", "<draft>"]
