"""G3-2 on the fixture renamed through `lmt rename` with the rename names, and the names the spec doesn't test
(design/internal/g3-stage1.md, 1c).

The parse checks need no ffmpeg and run in the `test` job. The ffmpeg re-encodes are marked `models` only because
`test-models` is the job that installs ffmpeg; they need no model.
"""

import html
import re
import shutil

import pytest
import srt
import webvtt
from subtitle_checks import check_srt, check_vtt
from test_rename import FIXTURE, NAME1, NAME2, outputs, rename

from lmt.export.cues import cues
from lmt.transcript import load

LABELS = {"S1": NAME1, "S2": NAME2}
BARE_AMPERSAND = re.compile(r"&(?![A-Za-z]+;|#[0-9]+;|#x[0-9A-Fa-f]+;)")
VOICE_TAG = re.compile(r"<v ([^>]*)>")
# G3-2's own numbers, not the exporter's: every cue has at most two lines of at most 42 characters (code points).
LINE = 42
LINES = 2


@pytest.fixture
def renamed(tmp_path, capsys):
    """The fixture, copied as two_speakers.json and renamed S1=NAME1 S2=NAME2. Returns the SRT and WebVTT paths."""
    work = tmp_path / "two_speakers.json"
    shutil.copyfile(FIXTURE, work)
    code, _, err = rename(capsys, work, f"S1={NAME1}", f"S2={NAME2}")
    assert (code, err) == (0, ""), err
    return outputs(tmp_path, ("srt", "vtt"))


def cue_labels():
    """Each cue's speaker label, in order, as the exporters split the renamed fixture's segments into cues."""
    return [LABELS[s.speaker] for s in load(FIXTURE).segments for _ in cues(s, LABELS[s.speaker])]


def assert_fits(lines, where):
    assert 1 <= len(lines) <= LINES, f"{where}: {len(lines)} lines {lines!r}"
    for line in lines:
        assert len(line) <= LINE or len(line.split()) == 1, f"{where}: {len(line)} characters {line!r}"


@pytest.mark.criterion("G3-2")
def test_the_srt_parses_strictly_and_its_cues_fit(renamed):
    path = renamed[0]
    subtitles = list(srt.parse(path.read_text(encoding="utf-8"), ignore_errors=False))
    assert [s.content.split(": ", 1)[0] for s in subtitles] == cue_labels()
    for s in subtitles:
        assert_fits(s.content.splitlines(), f"{path.name} cue {s.index}")


@pytest.mark.criterion("G3-2")
def test_the_webvtt_parses_with_the_names_escaped_and_its_cues_fit(renamed):
    path = renamed[1]
    captions = list(webvtt.read(str(path)))
    assert [html.unescape(c.voice) for c in captions] == cue_labels()
    escaped = NAME1.replace("&", "&amp;")
    assert any(c.voice == escaped for c in captions)
    for n, c in enumerate(captions, 1):
        raw = "\n".join(c.lines)
        voice = VOICE_TAG.match(raw)
        assert voice, f"{path.name} cue {n}: no voice tag in {raw!r}"
        annotation, text = voice.group(1), raw[voice.end():]
        assert not BARE_AMPERSAND.search(annotation) and not BARE_AMPERSAND.search(text), f"cue {n}: {raw!r}"
        assert html.unescape(annotation) == html.unescape(c.voice)
        assert html.unescape(text).startswith(f"{html.unescape(c.voice)}: ")
        assert_fits(html.unescape(text).splitlines(), f"{path.name} cue {n}")
    assert f"<v {escaped}>{escaped}: " in path.read_text(encoding="utf-8")


@pytest.mark.models  # needs only ffmpeg, which only test-models installs
@pytest.mark.criterion("G3-2")
def test_ffmpeg_re_encodes_the_renamed_subtitles(renamed):
    srt_path, vtt_path = renamed
    check_srt(srt_path)
    check_vtt(vtt_path)


# Not a criterion: U+2028 and U+2029 pass the label rule, which excludes only control characters, but some
# parsers treat them as line breaks. If this fails, the item stops for the Architect: never weaken it.
@pytest.mark.models  # needs only ffmpeg, which only test-models installs
@pytest.mark.parametrize("separator", [" ", " "], ids=["U+2028", "U+2029"])
def test_a_line_or_paragraph_separator_in_a_name_keeps_the_subtitles_valid(tmp_path, capsys, separator):
    work = tmp_path / "two_speakers.json"
    shutil.copyfile(FIXTURE, work)
    name = f"A{separator}B"
    code, _, err = rename(capsys, work, f"S1={name}")
    assert (code, err) == (0, ""), err
    srt_path, vtt_path = outputs(tmp_path, ("srt", "vtt"))
    check_srt(srt_path)
    check_vtt(vtt_path)
