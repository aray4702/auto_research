"""The transcript types and their document (design/internal/g1-slice.md, S4)."""

import unittest

from lmt.transcript import SCHEMA_VERSION, Segment, Speaker, Transcript, Word, from_dict, to_dict

MODEL = {"name": "m", "repository": "o/m", "revision": "abc"}


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


if __name__ == "__main__":
    unittest.main()
