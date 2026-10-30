"""The transcript and markup fixtures' own checks, which need no model (design/internal/g3-stage1.md, 0c), and
the transcript fixture kept equal to Linux's product run, which does (0d)."""

import json
import os
import re
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

# A word's probability isn't reproduced exactly from one CPU run to the next, so the comparison masks it (0d).
PROBABILITY = re.compile(r'("probability": )[^,\n}]+')


def mask_probabilities(text: str) -> str:
    return PROBABILITY.sub(r"\1_", text)


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
@pytest.mark.skipif(sys.platform != "linux", reason="the fixture is the product run's on Linux (spec G3, Terms)")
def test_the_fixture_is_the_product_runs_json(two_speakers_run):
    assert two_speakers_run.returncode == 0, two_speakers_run.stderr
    produced = two_speakers_run.out_dir / "two_speakers.json"
    text = produced.read_text(encoding="utf-8")
    expected = FIXTURE.read_text(encoding="utf-8")
    if mask_probabilities(text) != mask_probabilities(expected):
        # The run's file goes to the job's artifact, for the builder to commit unchanged (0d).
        if out := os.environ.get("LMT_FIXTURE_OUT"):
            Path(out).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(produced, Path(out) / FIXTURE.name)
        pytest.fail(f"{FIXTURE.name} differs from this run's two_speakers.json in more than word probabilities")
    words = [w for s in json.loads(text)["segments"] for w in s["words"]]
    values = [m.group(0)[len(m.group(1)) :] for m in PROBABILITY.finditer(text)]
    assert len(values) == len(PROBABILITY.findall(expected)) == len(words)
    for value in values:
        assert 0 <= float(value) <= 1, value


class MaskProbabilitiesTest(unittest.TestCase):
    TEXT = '{\n  "start": 0.5,\n  "text": " also",\n  "probability": 0.943\n},\n{"probability": 0.5, "end": 1.0}\n'

    def test_texts_differing_only_in_a_probability_mask_the_same(self):
        other = self.TEXT.replace("0.943", "0.959").replace('"probability": 0.5', '"probability": 1e-05')
        self.assertNotEqual(other, self.TEXT)
        self.assertEqual(mask_probabilities(other), mask_probabilities(self.TEXT))
        self.assertEqual(mask_probabilities(self.TEXT).count('"probability": _'), 2)

    def test_texts_differing_in_a_start_or_a_text_do_not(self):
        for old, new in [('"start": 0.5', '"start": 0.51'), ('" also"', '" else"')]:
            with self.subTest(changed=new):
                self.assertNotEqual(mask_probabilities(self.TEXT.replace(old, new)), mask_probabilities(self.TEXT))


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
