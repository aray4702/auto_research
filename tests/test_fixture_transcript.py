"""The transcript and markup fixtures' own checks, which need no model (design/internal/g3-stage1.md, 0c), and
the transcript fixture kept equal to Linux's product run in its machine-independent fields, which does (0d)."""

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from typing import NamedTuple

import pytest
from fixtures.markup import IMG, SCRIPT, markup_transcript
from fixtures.segments import MIN_CURRENT, current_span, play_targets

from lmt import schema
from lmt.transcript import load

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "two_speakers.transcript.json"

# The fields that don't vary from one machine to the next (#409, option 1). Word texts, times and probabilities
# do (PR #494), so they aren't compared.
TOP_LEVEL = ("schema_version", "lmt_version", "source", "models", "speakers")


def first_difference(expected: dict, actual: dict) -> str | None:
    """The first machine-independent field in which two transcripts differ, or None if they agree in all."""
    for key in TOP_LEVEL:
        if expected.get(key) != actual.get(key):
            return key
    if len(expected["segments"]) != len(actual["segments"]):
        return "len(segments)"
    for i, (e, a) in enumerate(zip(expected["segments"], actual["segments"], strict=True)):
        if e["speaker"] != a["speaker"]:
            return f"segments[{i}].speaker"
    return None


class Seg(NamedTuple):
    start: float
    end: float


class TranscriptFixtureTest(unittest.TestCase):
    def setUp(self):
        self.t = load(FIXTURE)

    def test_names_its_audio(self):
        self.assertEqual(self.t.source["file"], "two_speakers.wav")
        self.assertTrue((FIXTURE.parent / self.t.source["file"]).is_file())

    def test_two_speakers(self):
        self.assertEqual([s.id for s in self.t.speakers], ["S1", "S2"])

    def test_first_middle_and_last_segments_stay_current_long_enough(self):
        targets = play_targets(self.t.segments)
        self.assertEqual(len(set(targets)), 3)
        for i in targets:
            with self.subTest(segment=i):
                self.assertGreaterEqual(round(current_span(self.t.segments, i), 3), MIN_CURRENT)


@pytest.mark.models
@pytest.mark.skipif(
    sys.platform != "linux", reason="the fixture is the product run's on Linux (spec G3, Terms)"
)
def test_the_fixture_matches_the_product_run(two_speakers_run):
    assert two_speakers_run.returncode == 0, two_speakers_run.stderr
    produced = two_speakers_run.out_dir / "two_speakers.json"
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if (field := first_difference(expected, json.loads(produced.read_text(encoding="utf-8")))) is not None:
        # The run's file goes to the job's artifact, for the builder to commit unchanged (0d).
        if out := os.environ.get("LMT_FIXTURE_OUT"):
            Path(out).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(produced, Path(out) / FIXTURE.name)
        pytest.fail(f"{FIXTURE.name} differs from this run's two_speakers.json in {field}")


class FirstDifferenceTest(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.other = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_agrees_when_only_texts_times_and_probabilities_differ(self):
        for seg in self.other["segments"]:
            seg["start"] += 0.5
            seg["end"] += 0.5
            seg["text"] = "else"
            for word in seg["words"]:
                word["start"] += 0.58
                word["end"] += 0.58
                word["text"] = "else"
                word["probability"] = 0.5
        self.assertNotEqual(self.other, self.doc)
        self.assertIsNone(first_difference(self.doc, self.other))

    def test_names_a_differing_speaker(self):
        self.other["segments"][3]["speaker"] = "S2" if self.doc["segments"][3]["speaker"] == "S1" else "S1"
        self.assertEqual(first_difference(self.doc, self.other), "segments[3].speaker")

    def test_names_a_differing_segment_count(self):
        del self.other["segments"][-1]
        self.assertEqual(first_difference(self.doc, self.other), "len(segments)")

    def test_names_a_differing_lmt_version(self):
        self.other["lmt_version"] = "9.9.9"
        self.other["segments"][0]["speaker"] = "S2"
        self.assertEqual(first_difference(self.doc, self.other), "lmt_version")


class PlayTargetsTest(unittest.TestCase):
    def test_picks_by_how_long_a_segment_stays_current(self):
        segments = [
            Seg(0.0, 2.0),
            Seg(1.8, 3.0),  # segment 0 overlaps it, so 0 stays current for 1.8 s
            Seg(3.0, 5.0),  # n // 2: lasts 2 s but stays current for only 0.5 s, so the rule skips it
            Seg(3.5, 4.0),  # lasts 0.5 s, but the gap after it keeps it current for 2.5 s
            Seg(6.0, 8.0),
        ]
        self.assertAlmostEqual(current_span(segments, 0), 1.8)
        self.assertAlmostEqual(current_span(segments, 2), 0.5)
        self.assertAlmostEqual(current_span(segments, 3), 2.5)
        self.assertAlmostEqual(current_span(segments, 4), 2.0)
        self.assertEqual(play_targets(segments), (0, 3, 4))

    def test_raises_when_no_middle_segment_stays_current(self):
        segments = [Seg(0.0, 2.0), Seg(2.0, 3.0), Seg(3.0, 4.0), Seg(4.0, 6.0)]
        with self.assertRaisesRegex(LookupError, "index 2"):
            play_targets(segments)


class MarkupFixtureTest(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_passes_the_schema_with_the_two_strings(self):
        doc = markup_transcript(self.doc)
        schema.validate(doc)
        self.assertEqual([s["text"] for s in doc["segments"][:2]], [IMG, SCRIPT])

    def test_leaves_the_fixture_unchanged(self):
        before = json.dumps(self.doc)
        markup_transcript(self.doc)
        self.assertEqual(json.dumps(self.doc), before)
