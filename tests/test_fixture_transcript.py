"""The transcript and markup fixtures' own checks, which need no model (design/internal/g3-stage1.md, 0c)."""

import json
import unittest
from pathlib import Path

from fixtures.markup import IMG, SCRIPT, markup_transcript

from lmt import schema
from lmt.transcript import load

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "two_speakers.transcript.json"

# G3-5 checks the marked segment 1 s after the click, from up to 0.05 s before its start (g3-stage1.md, 3b).
MIN_SEGMENT = 1.6


class TranscriptFixtureTest(unittest.TestCase):
    def setUp(self):
        self.t = load(FIXTURE)

    def test_names_its_audio(self):
        self.assertEqual(self.t.source["file"], "two_speakers.wav")
        self.assertTrue((FIXTURE.parent / self.t.source["file"]).is_file())

    def test_two_speakers(self):
        self.assertEqual([s.id for s in self.t.speakers], ["S1", "S2"])

    def test_first_middle_and_last_segments_last_long_enough(self):
        n = len(self.t.segments)
        for i in (0, n // 2, n - 1):
            s = self.t.segments[i]
            with self.subTest(segment=i):
                self.assertGreaterEqual(round(s.end - s.start, 3), MIN_SEGMENT)


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
