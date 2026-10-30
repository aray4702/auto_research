"""The transcript types, their document, and `load` (design/internal/g1-slice.md, S4; g1-stage2.md, P9)."""

import json
import tempfile
import unittest
from pathlib import Path

import test_export
import test_export_formats
from test_export import with_value, without

from lmt import export
from lmt.errors import InputError
from lmt.transcript import SCHEMA_VERSION, Segment, Speaker, Transcript, Word, from_dict, load, to_dict

MODEL = {"name": "m", "repository": "o/m", "revision": "abc"}
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "export"


def two_speakers():
    w1, w2 = Word(0.48, 0.71, "Shall", 0.97), Word(0.75, 1.2, "we?", 0.9)
    w3 = Word(2.5, 3.0, "Yes.", 0.812)
    return Transcript(
        SCHEMA_VERSION, "0.1.0", {"file": "meeting.wav", "duration": 3.5}, {"asr": MODEL, "diarization": MODEL},
        (Speaker("S1", "Speaker 1"), Speaker("S2", "Speaker 2")),
        (Segment(0.48, 1.2, "S1", "Shall we?", (w1, w2)), Segment(2.5, 3.0, "S2", "Yes.", (w3,))),
    )


class TranscriptTest(unittest.TestCase):
    def test_round_trip(self):
        t = two_speakers()
        self.assertEqual(from_dict(to_dict(t)), t)

    def test_key_order(self):
        doc = to_dict(two_speakers())
        self.assertEqual(list(doc), ["schema_version", "lmt_version", "source", "models", "speakers", "segments"])
        self.assertEqual(list(doc["segments"][0]), ["start", "end", "speaker", "text", "words"])
        self.assertEqual(list(doc["segments"][0]["words"][0]), ["start", "end", "text", "probability"])
        self.assertEqual(list(doc["speakers"][0]), ["id", "label"])

    def test_version(self):
        self.assertEqual(SCHEMA_VERSION, "1.1")
        self.assertEqual(to_dict(two_speakers())["schema_version"], "1.1")

    def test_document_is_a_copy(self):
        t = two_speakers()
        to_dict(t)["models"]["asr"]["name"] = "changed"
        self.assertEqual(t.models["asr"]["name"], "m")

    def test_words_are_optional(self):
        doc = to_dict(two_speakers())
        del doc["segments"][0]["words"]
        self.assertEqual(from_dict(doc).segments[0].words, ())


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_golden(self):
        """The committed JSON golden reads back to its transcript and renders byte-identical."""
        t = load(GOLDEN / "transcript.json")
        self.assertEqual(t, test_export.small())
        self.assertEqual(export.render(t, "json").encode("utf-8"), (GOLDEN / "transcript.json").read_bytes())

    def test_every_format_from_a_loaded_transcript(self):
        """The text, SRT and WebVTT goldens are S5's transcript, so its JSON is rendered, loaded, and rendered again."""
        path = self.dir / "transcript.json"
        path.write_bytes(export.render(test_export_formats.small(), "json").encode("utf-8"))
        t = load(path)
        self.assertEqual(export.render(t, "json").encode("utf-8"), path.read_bytes())
        for fmt in ("txt", "srt", "vtt"):
            with self.subTest(fmt=fmt):
                self.assertEqual(export.render(t, fmt).encode("utf-8"), (GOLDEN / f"transcript.{fmt}").read_bytes())

    def assertLoadFails(self, path):
        with self.assertRaises(InputError) as caught:
            load(path)
        self.assertEqual((caught.exception.subject, caught.exception.exit_code), (str(path), 3))
        return caught.exception.problem

    def write(self, text):
        path = self.dir / "t.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file(self):
        self.assertEqual(self.assertLoadFails(self.dir / "missing.json"), "can't read: No such file or directory")

    def test_directory(self):
        self.assertEqual(self.assertLoadFails(self.dir), "can't read: Is a directory")

    def test_not_json(self):
        self.assertTrue(self.assertLoadFails(self.write("{")).startswith("not JSON: "))

    def test_not_utf8(self):
        path = self.dir / "t.json"
        path.write_bytes(b'{"schema_version": "1.1\xff"}')
        self.assertTrue(self.assertLoadFails(path).startswith("not JSON: "))

    def test_not_an_object(self):
        self.assertEqual(self.assertLoadFails(self.write("[]")), "not a transcript")

    def test_fails_the_schema(self):
        doc = without(to_dict(two_speakers()), ("segments",))
        problem = self.assertLoadFails(self.write(json.dumps(doc)))
        self.assertEqual(problem, "fails the schema at $: 'segments' is a required property")

    def test_unsupported_version(self):
        """Checked before the schema, whose pattern would also reject it, so the message is clear."""
        doc = with_value(to_dict(two_speakers()), ("schema_version",), "2.0")
        problem = self.assertLoadFails(self.write(json.dumps(doc)))
        self.assertEqual(problem, "schema version 2.0 isn't supported; this lmt reads 1.x")

    def test_other_1x_version(self):
        doc = with_value(to_dict(two_speakers()), ("schema_version",), "1.0")
        self.assertEqual(load(self.write(json.dumps(doc))).schema_version, "1.0")


if __name__ == "__main__":
    unittest.main()
