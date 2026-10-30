"""G1-4 (spec/acceptance.md): both clips' product runs write the four formats, each with every segment's speaker
label and times, and the subtitles pass the strict parsers and the real ffmpeg's re-encode
(design/internal/g1-stage2.md, P6b). The runs pass no option, so they write the default formats, which
tests/test_cli.py's test_default_formats checks are exactly the spec's `--formats txt,srt,vtt,json`."""

import html
import json
import re
import shlex
from pathlib import Path

import jsonschema
import pytest
import srt
import subtitle_checks
import webvtt
from subtitle_checks import check_srt, check_vtt, words
from test_g1_1 import report

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema" / "transcript.schema.json"
FORMATS = ("txt", "srt", "vtt", "json")  # the default --formats, in order
RUNS = {"clip_run": "clip", "two_speakers_run": "two_speakers"}  # each product run's fixture and stem


@pytest.fixture
def ffmpeg_runs(monkeypatch):
    """Reports each ffmpeg command check_srt and check_vtt run, as run, with its exit code. A command that fails
    stops the test with a message naming it and its exit code."""
    reencode, runs = subtitle_checks.ffmpeg_reencode, []

    def recording(path, fmt):
        r = reencode(path, fmt)
        runs.append(r)
        report(f"G1-4: `{shlex.join(r.args)}` exited {r.returncode}")
        return r

    monkeypatch.setattr(subtitle_checks, "ffmpeg_reencode", recording)
    return runs


@pytest.mark.models
@pytest.mark.criterion("G1-4")
@pytest.mark.parametrize("run_name", list(RUNS))
def test_g1_4_the_product_run_writes_four_valid_formats_with_labels_and_times(run_name, request, ffmpeg_runs):
    run, stem = request.getfixturevalue(run_name), RUNS[run_name]
    assert run.returncode == 0, run.stderr

    # 1. stdout lists the four paths, and the files exist.
    names = [f"{stem}.{fmt}" for fmt in FORMATS]
    assert run.stdout.splitlines() == names, run.stdout
    paths = {fmt: run.out_dir / name for fmt, name in zip(FORMATS, names)}
    for path in paths.values():
        assert path.is_file(), path

    # 2. The JSON validates against the schema.
    doc = json.loads(paths["json"].read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(doc)

    # 3. Every segment has a speaker label and timestamps in every format.
    ids = [speaker["id"] for speaker in doc["speakers"]]
    labels = [speaker["label"] for speaker in doc["speakers"]]
    assert doc["segments"], f"{paths['json']}: no segment"
    for n, segment in enumerate(doc["segments"]):
        assert "start" in segment and "end" in segment, f"{paths['json']}: segment {n}: {segment}"
        assert segment["speaker"] in ids, f"{paths['json']}: segment {n}'s speaker isn't among {ids}"

    any_label = "|".join(re.escape(label) for label in labels)
    lines = paths["txt"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(doc["segments"]), f"{paths['txt']}: {len(lines)} lines, {len(doc['segments'])} segments"
    for line in lines:
        assert re.match(rf"\[\d{{2}}:\d{{2}}:\d{{2}}\] (?:{any_label}): ", line), f"{paths['txt']}: {line!r}"

    cues = list(srt.parse(paths["srt"].read_text(encoding="utf-8")))
    for cue in cues:
        assert cue.start < cue.end, f"{paths['srt']}: cue {cue.index}: {cue.start} to {cue.end}"
        first = cue.content.splitlines()[0]
        assert any(first.startswith(f"{label}: ") for label in labels), f"{paths['srt']}: cue {cue.index}: {first!r}"

    # public-api.md: `&`, `<` and `>` escaped in the text, and `&` in the voice tag (a label holds no `<` or `>`).
    prefixes = [f"<v {html.escape(label, quote=False)}>{html.escape(label, quote=False)}: " for label in labels]
    for caption in webvtt.read(str(paths["vtt"])):
        first = caption.lines[0]
        assert any(first.startswith(prefix) for prefix in prefixes), f"{paths['vtt']}: {first!r}"

    # 4. The strict parsers and the real ffmpeg's re-encode, to SRT and to WebVTT.
    check_srt(paths["srt"])
    check_vtt(paths["vtt"])
    assert [r.returncode for r in ffmpeg_runs] == [0, 0]

    # 5. The same words, in the same order, in all four formats.
    expected = words(paths["json"], "json", labels)
    assert expected, f"{paths['json']}: no words"
    for fmt in ("txt", "srt", "vtt"):
        assert words(paths[fmt], fmt, labels) == expected, paths[fmt]
    report(f"G1-4: the product run on tests/fixtures/{stem}.wav: {len(doc['segments'])} segments, {len(cues)} SRT "
           f"cues, {len(expected)} words, speakers {labels}")
