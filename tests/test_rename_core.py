"""`lmt rename`'s parts: parse_pair, check_name, resolve, relabel, speaker_table (design/internal/g3-stage1.md, 1a).

On small transcripts built here, so no fixture or model. 1b checks the parts through the command.
"""

import json
import re
import unittest

from lmt.errors import UsageError
from lmt.rename import check_name, parse_pair, relabel, resolve, speaker_table
from lmt.schema import SCHEMA_PATH
from lmt.transcript import SCHEMA_VERSION, Segment, Speaker, Transcript

LABEL = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]["speaker"]["properties"]["label"]

# (name, the refusal's problem, or None if check_name accepts it)
NAMES = [
    ("Ana", None),
    ("  José & Ana  ", None),
    ("a" * 32, None),
    (" " + "a" * 32 + "\t", None),
    ("a" * 33, "the name is 33 characters; at most 32"),
    ("﻿Ana", None),
    ("Ana﻿", None),
    ("A B", None),
    ("A B", None),
    ("A B", None),
    ("A B", None),
    ("x", None),
    ("", "the name is empty"),
    ("   ", "the name is empty"),
    ("\t\n ", "the name is empty"),
    ("a\x00b", "the name has a control character"),
    ("a\x1fb", "the name has a control character"),
    ("a\nb", "the name has a control character"),
    ("a\x7fb", "the name has a control character"),
    ("a\x9fb", "the name has a control character"),
    ("\x00a", "the name has a control character"),
    ("a\x7f", "the name has a control character"),
    ("a\x85b", "the name has a control character"),
    ("a\x7e\xa0b", None),
    ("a<b", "the name has '<'"),
    ("a>b", "the name has '>'"),
    ("a{b", "the name has '{'"),
    ("a}b", "the name has '}'"),
    ("a=b", "the name has '='"),
    ("<", "the name has '<'"),
    # The order: length before control characters, control characters before the five.
    ("a\x00" + "b" * 32, "the name is 34 characters; at most 32"),
    ("<\x00", "the name has a control character"),
]


def schema_accepts(label: str) -> bool:
    return (LABEL["minLength"] <= len(label) <= LABEL["maxLength"]
            and re.search(LABEL["pattern"], label) is not None)


def transcript(*speakers, segments=()):
    return Transcript(SCHEMA_VERSION, "0.1.0", {"file": "m.wav", "duration": 9.0}, {},
                      tuple(Speaker(i, label) for i, label in speakers), tuple(segments))


def segment(start, end, speaker, text):
    return Segment(start, end, speaker, text, ())


class CheckNameTest(unittest.TestCase):
    def test_agrees_with_the_schema_both_ways(self):
        for name, problem in NAMES:
            with self.subTest(name=name):
                trimmed = name.strip()
                if problem is None:
                    self.assertEqual(check_name("S1", name), trimmed)
                    self.assertTrue(schema_accepts(trimmed))
                else:
                    with self.assertRaises(UsageError):
                        check_name("S1", name)
                    self.assertFalse(schema_accepts(trimmed))

    def test_each_refusal_says_why(self):
        for name, problem in NAMES:
            if problem is None:
                continue
            with self.subTest(name=name):
                with self.assertRaises(UsageError) as cm:
                    check_name("S1", name)
                self.assertEqual((cm.exception.subject, cm.exception.problem), ("S1", problem))

    def test_the_subject_is_the_key_with_control_characters_echoed(self):
        with self.assertRaises(UsageError) as cm:
            check_name("S\n1\x9f", "a\nb")
        self.assertEqual(cm.exception.subject, "S\\u000A1\\u009F")
        self.assertNotIn("\n", str(cm.exception))


class ParsePairTest(unittest.TestCase):
    def test_splits_at_the_first_equals(self):
        self.assertEqual(parse_pair("S1=José & Ana"), ("S1", "José & Ana"))
        self.assertEqual(parse_pair("S1="), ("S1", ""))
        self.assertEqual(parse_pair("S1=a=b"), ("S1", "a=b"))

    def test_the_name_after_the_first_equals_is_then_refused(self):
        key, name = parse_pair("S1=a=b")
        with self.assertRaises(UsageError) as cm:
            check_name(key, name)
        self.assertEqual(cm.exception.problem, "the name has '='")

    def test_no_equals(self):
        with self.assertRaises(UsageError) as cm:
            parse_pair("Bob")
        self.assertEqual((cm.exception.subject, cm.exception.problem), ("Bob", "expected KEY=NAME"))

    def test_no_equals_echoes_control_characters(self):
        with self.assertRaises(UsageError) as cm:
            parse_pair("Bo\nb\x00")
        self.assertEqual(cm.exception.subject, "Bo\\u000Ab\\u0000")
        self.assertNotIn("\n", str(cm.exception))


class ResolveTest(unittest.TestCase):
    T = transcript(("S1", "Speaker 1"), ("S2", "Speaker 2"))

    def assert_refused(self, t, pairs, subject, problem):
        with self.assertRaises(UsageError) as cm:
            resolve(t, pairs)
        self.assertEqual((cm.exception.subject, cm.exception.problem), (subject, problem))

    def test_by_id(self):
        self.assertEqual(resolve(self.T, [("S2", " Bob ")]), {"S2": "Bob"})

    def test_by_a_unique_label(self):
        self.assertEqual(resolve(self.T, [("Speaker 1", "Ana"), ("S2", "Bob")]), {"S1": "Ana", "S2": "Bob"})

    def test_a_label_two_speakers_hold(self):
        t = transcript(("S1", "Alex"), ("S2", "Alex"))
        self.assert_refused(t, [("Alex", "Bob")], "Alex", "is the label of S1 and S2; use an id")

    def test_no_match(self):
        self.assert_refused(self.T, [("S9", "Bob")], "S9", "no speaker has this id or label")

    def test_two_pairs_reach_one_speaker(self):
        self.assert_refused(self.T, [("S1", "Ana"), ("Speaker 1", "Bob")], "Speaker 1", "names S1 a second time")
        self.assert_refused(self.T, [("S1", "Ana"), ("S1", "Bob")], "S1", "names S1 a second time")

    def test_an_id_wins_over_another_speakers_label(self):
        t = transcript(("S1", "S2"), ("S2", "Speaker 2"))
        self.assertEqual(resolve(t, [("S2", "Bob")]), {"S2": "Bob"})

    def test_against_the_labels_as_read(self):
        # "Ana" isn't a label until this command's renames, so it matches nothing.
        self.assert_refused(self.T, [("S1", "Ana"), ("Ana", "Bob")], "Ana", "no speaker has this id or label")

    def test_a_bad_name(self):
        self.assert_refused(self.T, [("S1", "a<b")], "S1", "the name has '<'")

    def test_names_may_repeat(self):
        self.assertEqual(resolve(self.T, [("S1", "Alex"), ("S2", "Alex")]), {"S1": "Alex", "S2": "Alex"})


class RelabelTest(unittest.TestCase):
    def test_only_speakers_change(self):
        t = transcript(("S1", "Speaker 1"), ("S2", "Speaker 2"), segments=[segment(0.0, 1.0, "S1", "Hi.")])
        r = relabel(t, {"S2": "Bob"})
        self.assertEqual(r.speakers, (Speaker("S1", "Speaker 1"), Speaker("S2", "Bob")))
        self.assertIs(r.segments, t.segments)
        self.assertEqual((r.schema_version, r.lmt_version, r.source, r.models),
                         (t.schema_version, t.lmt_version, t.source, t.models))
        self.assertEqual(t.speakers[1].label, "Speaker 2")


class SpeakerTableTest(unittest.TestCase):
    def test_table(self):
        t = transcript(
            ("S1", "Speaker 1"), ("S2", "Ana\tB"), ("S3", "Speaker 3"),
            segments=[
                segment(0.0, 1.24, "S1", "one two three four five six seven"),
                segment(1.5, 2.0, "S2", "Yes."),
                segment(2.0, 3.27, "S1", "later"),
            ],
        )
        self.assertEqual(speaker_table(t).split("\n"), [
            "ID\tLABEL\tSEGMENTS\tSECONDS\tFIRST WORDS",
            "S1\tSpeaker 1\t2\t2.5\tone two three four five six…",
            "S2\tAna B\t1\t0.5\tYes.",
            "S3\tSpeaker 3\t0\t0.0\t",
        ])

    def test_six_words_get_no_ellipsis(self):
        t = transcript(("S1", "A"), segments=[segment(0.0, 1.0, "S1", "one two three four five six")])
        self.assertEqual(speaker_table(t).split("\n")[1], "S1\tA\t1\t1.0\tone two three four five six")


if __name__ == "__main__":
    unittest.main()
