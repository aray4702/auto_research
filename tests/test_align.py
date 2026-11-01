"""Alignment's rules, one test each, and its times (design/internal/g1-slice.md, S4)."""

import random
import unittest

from lmt import align
from lmt.transcript import Speaker, Turn, Word

MODELS = {"asr": {"name": "m", "repository": "o/m", "revision": "abc"}, "diarization": None}


def build(words, turns=None, samples=16_000 * 60):
    return align.build(words, turns, duration_samples=samples, source_file="a.wav", models=MODELS,
                       lmt_version="0.1.0")


def w(start, end, text="w", probability=0.9):
    return Word(start, end, text, probability)


def times(transcript):
    return [(x.start, x.end) for s in transcript.segments for x in s.words]


class AssignSpeakerTest(unittest.TestCase):
    def test_most_overlap_wins(self):
        turns = [Turn(0.0, 1.3, "A"), Turn(1.3, 3.0, "B")]
        self.assertEqual(align.assign_speaker(w(1.0, 2.0), turns, "X"), "B")

    def test_overlap_tie_goes_to_the_earlier_turn(self):
        a, b = Turn(0.0, 1.5, "A"), Turn(1.5, 3.0, "B")
        self.assertEqual(align.assign_speaker(w(1.0, 2.0), [a, b], None), "A")
        self.assertEqual(align.assign_speaker(w(1.0, 2.0), [b, a], None), "B")

    def test_word_in_no_turn_takes_the_nearest_within_1_s(self):
        self.assertEqual(align.assign_speaker(w(5.0, 5.5), [Turn(0.0, 4.0, "A"), Turn(6.0, 7.0, "B")], "X"), "B")
        self.assertEqual(align.assign_speaker(w(5.0, 5.5), [Turn(0.0, 4.0, "A")], "X"), "A")  # 1.0 s away

    def test_nearest_tie_goes_to_the_earlier_turn(self):
        a, b = Turn(0.0, 4.5, "A"), Turn(6.0, 7.0, "B")
        self.assertEqual(align.assign_speaker(w(5.0, 5.5), [a, b], None), "A")
        self.assertEqual(align.assign_speaker(w(5.0, 5.5), [b, a], None), "B")

    def test_word_1_2_s_away_takes_the_previous_speaker(self):
        self.assertEqual(align.assign_speaker(w(5.0, 5.5), [Turn(0.0, 3.8, "A"), Turn(6.7, 7.0, "B")], "X"), "X")
        self.assertIsNone(align.assign_speaker(w(5.0, 5.5), [Turn(0.0, 3.8, "A")], None))

    def test_zero_length_word_inside_a_turn_is_at_gap_0(self):
        turns = [Turn(2.5, 3.0, "B"), Turn(0.0, 2.0, "A")]
        self.assertEqual(align.assign_speaker(w(1.0, 1.0), turns, "X"), "A")

    def test_no_turns_gives_one_speaker(self):
        words = [w(0.0, 0.5, "Hi."), w(3.0, 3.5, "there")]
        for turns in (None, []):
            with self.subTest(turns=turns):
                t = build(words, turns)
                self.assertEqual(t.speakers, (Speaker("S1", "Speaker 1"),))
                self.assertEqual([s.speaker for s in t.segments], ["S1", "S1"])


class BreaksTest(unittest.TestCase):
    def test_pause_of_exactly_1_s_breaks(self):
        # 4.3 - 3.3 is 0.9999999999999996 in floats: the pause is measured in whole milliseconds.
        self.assertTrue(align.breaks(w(3.0, 3.3), w(4.3, 4.5), 3.0))

    def test_pause_of_0_999_s_doesnt(self):
        self.assertFalse(align.breaks(w(3.0, 3.3), w(4.299, 4.5), 3.0))

    def test_sentence_ends(self):
        for text in ("end.", 'end?"', "end!)", "end.']"):
            with self.subTest(text=text):
                self.assertTrue(align.ends_sentence(w(0, 1, text)))
                self.assertTrue(align.breaks(w(0.0, 1.0, text), w(1.0, 1.5), 0.0))

    def test_no_sentence_end(self):
        for text in ("Mr", "end,", "end)", "e.g"):
            with self.subTest(text=text):
                self.assertFalse(align.ends_sentence(w(0, 1, text)))
                self.assertFalse(align.breaks(w(0.0, 1.0, text), w(1.0, 1.5), 0.0))

    def test_30_s(self):
        self.assertFalse(align.breaks(w(29.0, 29.5), w(29.5, 30.0), 0.0))
        self.assertTrue(align.breaks(w(29.0, 29.5), w(29.5, 30.001), 0.0))

    def test_build_breaks_at_30_s(self):
        t = build([w(i * 0.5, i * 0.5 + 0.5) for i in range(70)])
        self.assertEqual([(s.start, s.end) for s in t.segments], [(0.0, 30.0), (30.0, 35.0)])

    def test_build_breaks_at_a_pause_a_sentence_and_a_speaker(self):
        words = [w(0.0, 0.5, "a"), w(1.5, 2.0, "b."), w(2.0, 2.5, "c"), w(2.5, 3.0, "d")]
        self.assertEqual([s.text for s in build(words).segments], ["a", "b.", "c d"])
        # With turns, nothing but a turn breaks a segment (P12).
        turns = [Turn(0.0, 2.6, "A"), Turn(2.6, 3.5, "B")]
        self.assertEqual([s.text for s in build(words, turns).segments], ["a b. c", "d"])

    def test_turns_all_dropped_give_todays_rule(self):
        words = [w(0.0, 0.5, "a"), w(1.5, 2.0, "b."), w(2.0, 2.5, "c")]
        self.assertEqual(build(words, [Turn(0.5, 0.5, "A")]), build(words, None))


class TurnsFirstTest(unittest.TestCase):
    """design/internal/g1-stage2.md, P12: one test per rule."""

    def assertSegments(self, words, turns, expected, samples=16_000 * 60):
        t = build(words, turns, samples)
        self.assertEqual([(s.start, s.end, s.speaker, s.text) for s in t.segments], expected)
        self.assertEqual([s.id for s in t.speakers], sorted({s[2] for s in expected}))
        return t

    def test_a_turns_span_and_speaker(self):
        self.assertSegments([w(1.5, 2.0, "a"), w(2.5, 3.0, "b")], [Turn(1.0, 4.0, "A")], [(1.0, 4.0, "S1", "a b")])

    def test_the_midpoint_start_inclusive_end_exclusive(self):
        self.assertSegments([w(0.9, 1.1, "x")], [Turn(0.0, 1.0, "A"), Turn(1.0, 2.0, "B")], [(1.0, 2.0, "S1", "x")])

    def test_the_midpoint_not_the_most_overlap(self):
        self.assertSegments([w(0.0, 1.2, "x")], [Turn(0.55, 0.65, "A"), Turn(0.7, 3.0, "B")],
                            [(0.55, 0.65, "S1", "x")])

    def test_two_turns_hold_it_most_overlap(self):
        self.assertSegments([w(0.5, 1.0, "x"), w(2.0, 2.5, "y")], [Turn(0.0, 2.3, "A"), Turn(2.0, 4.0, "B")],
                            [(0.0, 2.3, "S1", "x"), (2.0, 4.0, "S2", "y")])

    def test_two_turns_hold_it_a_tie_goes_to_the_earlier(self):
        self.assertSegments([w(0.5, 1.0, "x"), w(2.2, 2.6, "y")], [Turn(0.0, 5.0, "A"), Turn(2.0, 3.0, "B")],
                            [(0.0, 5.0, "S1", "x y")])

    def test_a_wordless_turn_goes_with_its_speaker(self):
        self.assertSegments([w(0.2, 0.5, "a"), w(4.2, 4.5, "b")],
                            [Turn(0.0, 1.0, "A"), Turn(2.0, 3.0, "B"), Turn(4.0, 5.0, "A")],
                            [(0.0, 1.0, "S1", "a"), (4.0, 5.0, "S1", "b")])

    def test_a_stray_word_joins_the_nearest_turn_which_stretches(self):
        self.assertSegments([w(0.2, 0.5, "a"), w(1.6, 1.9, "s"), w(3.2, 3.5, "b")],
                            [Turn(0.0, 1.0, "A"), Turn(3.0, 4.0, "B")],
                            [(0.0, 1.9, "S1", "a s"), (3.0, 4.0, "S2", "b")])

    def test_a_stray_words_tie_goes_to_the_earlier(self):
        self.assertSegments([w(0.2, 0.5, "a"), w(1.25, 1.75, "s"), w(2.2, 2.5, "b")],
                            [Turn(0.0, 1.0, "A"), Turn(2.0, 3.0, "B")],
                            [(0.0, 1.75, "S1", "a s"), (2.0, 3.0, "S2", "b")])

    def test_a_stray_word_however_far(self):
        self.assertSegments([w(0.2, 0.5, "a"), w(6.0, 6.5, "s")], [Turn(0.0, 1.0, "A")], [(0.0, 6.5, "S1", "a s")])

    def test_turns_rounded_and_clipped(self):
        self.assertSegments([w(0.1, 0.3, "a"), w(0.7, 0.9, "b")],
                            [Turn(-0.5, 0.4, "A"), Turn(0.45, 0.4504, "C"), Turn(0.6, 3.0, "B")],
                            [(0.0, 0.4, "S1", "a"), (0.6, 1.0, "S2", "b")], samples=16_000)

    def test_order_and_numbering_by_first_segment(self):
        t = self.assertSegments([w(0.2, 0.5, "a"), w(2.2, 2.5, "b")], [Turn(2.0, 3.0, "B"), Turn(0.0, 1.0, "A")],
                                [(0.0, 1.0, "S1", "a"), (2.0, 3.0, "S2", "b")])
        self.assertEqual(t.speakers, (Speaker("S1", "Speaker 1"), Speaker("S2", "Speaker 2")))

    def pieces(self, words, turns):
        return [(s.start, s.end, s.speaker, len(s.words)) for s in build(words, turns).segments]

    def test_over_30_s(self):
        words = [w(i * 0.5, i * 0.5 + 0.5) for i in range(80)]
        self.assertEqual(self.pieces(words, [Turn(0.0, 40.0, "A")]), [(0.0, 30.0, "S1", 60), (30.0, 40.0, "S1", 20)])

    def test_an_overlapping_word_moves_the_cut(self):
        words = [w(i * 0.5, i * 0.5 + 0.5) for i in range(59)] + [w(29.4, 30.2), w(30.1, 30.6), w(30.6, 31.0)]
        self.assertEqual(self.pieces(words, [Turn(0.0, 32.0, "A")]), [(0.0, 30.6, "S1", 61), (30.6, 32.0, "S1", 1)])

    def test_no_piece_without_a_word(self):
        self.assertSegments([w(0.2, 0.5, "a"), w(40.0, 40.5, "s")], [Turn(0.0, 1.0, "A")],
                            [(0.0, 40.0, "S1", "a"), (40.0, 40.5, "S1", "s")])
        self.assertSegments([w(40.0, 40.5, "s")], [Turn(0.0, 1.0, "A")], [(0.0, 40.5, "S1", "s")])


class SegmentsTest(unittest.TestCase):
    def test_segment_text_and_times(self):
        t = build([w(0.0, 2.0, "a"), w(0.5, 1.0, "b")])
        (s,) = t.segments
        self.assertEqual((s.start, s.end, s.text), (0.0, 2.0, "a b"))
        self.assertEqual(align.segment_text([w(0, 1, "Shall"), w(1, 2, "we?")]), "Shall we?")

    def test_speakers_numbered_by_first_word_and_a_speaker_without_words_left_out(self):
        turns = [Turn(1.0, 2.0, "SPEAKER_00"), Turn(0.0, 1.0, "SPEAKER_01"), Turn(8.0, 9.0, "SPEAKER_02")]
        t = build([w(0.2, 0.5, "one"), w(1.2, 1.5, "two")], turns)
        self.assertEqual(t.speakers, (Speaker("S1", "Speaker 1"), Speaker("S2", "Speaker 2")))
        self.assertEqual([s.speaker for s in t.segments], ["S1", "S2"])

    def test_transcript_fields(self):
        t = build([w(0.0, 0.5)], samples=16_000)
        self.assertEqual((t.schema_version, t.lmt_version, t.source, t.models),
                         ("1.2", "0.1.0", {"file": "a.wav", "duration": 1.0}, MODELS))


class TimesTest(unittest.TestCase):
    def assertValid(self, t, across=True):
        """`across`: word starts in order across segments too, not only within each (without turns)."""
        d = t.source["duration"]
        for s in t.segments:
            self.assertTrue(0 <= s.start < s.end <= d, (s.start, s.end, d))
            for x in s.words:
                self.assertTrue(0 <= x.start < x.end <= d, (x.start, x.end, d))
            self.assertEqual([x.start for x in s.words], sorted(x.start for x in s.words))
        starts = [start for start, _ in times(t)]
        if across:
            self.assertEqual(starts, sorted(starts))
        self.assertEqual([s.start for s in t.segments], sorted(s.start for s in t.segments))

    def test_rounded_to_the_millisecond(self):
        t = build([w(0.12345, 0.6789, "a", 0.98765)])
        self.assertEqual(times(t), [(0.123, 0.679)])
        self.assertEqual(t.segments[0].words[0].probability, 0.988)

    def test_bad_words_come_out_valid(self):
        cases = {"end before start": (w(0.5, 0.2), (0.5, 0.501)), "zero length": (w(0.5, 0.5), (0.5, 0.501)),
                 "past the end": (w(1.2, 1.5), (0.999, 1.0)), "negative start": (w(-0.05, 0.2), (0.0, 0.2))}
        for name, (word, expected) in cases.items():
            with self.subTest(name):
                t = build([word], samples=16_000)
                self.assertEqual(times(t), [expected])
                self.assertValid(t)

    def test_word_before_its_predecessor_moves_up_and_keeps_its_place(self):
        t = build([w(0.5, 0.8, "b"), w(0.3, 0.6, "c")])
        self.assertEqual(times(t), [(0.5, 0.8), (0.5, 0.6)])
        self.assertEqual(t.segments[0].text, "b c")
        self.assertValid(t)

    def test_duration_rounded_down(self):
        t = build([w(0.5, 1.0009)], samples=16_015)
        self.assertEqual(t.source["duration"], 1.0)
        self.assertEqual(times(t), [(0.5, 1.0)])

    def test_random_words_always_valid(self):
        rng = random.Random(0)
        for _ in range(200):
            samples = rng.randint(16, 16_000 * 20)
            words = [w(rng.uniform(-2, 25), rng.uniform(-2, 25), rng.choice(["a", "b.", "c?"])) for _ in range(30)]
            starts = sorted(rng.uniform(0, 20) for _ in range(5))
            turns = [Turn(s, s + rng.uniform(0, 3), rng.choice("AB")) for s in starts]
            chosen = rng.choice([None, turns])
            t = build(words, chosen, samples=samples)
            self.assertValid(t, across=chosen is None)
            first = list(dict.fromkeys(s.speaker for s in t.segments))
            self.assertEqual([s.id for s in t.speakers], first)
            self.assertEqual(first, [f"S{n}" for n in range(1, len(first) + 1)])
            if chosen is None:
                self.assertEqual(" ".join(s.text for s in t.segments), " ".join(x.text for x in words))
                continue
            rounded = [(x.start, x.end, x.text) for s in build(words, None, samples=samples).segments for x in s.words]
            placed = [(x.start, x.end, x.text) for s in t.segments for x in s.words]
            self.assertEqual(sorted(placed), sorted(rounded))
            for s in t.segments:
                for x in s.words:
                    ms = [round(v * 1000) for v in (s.start, s.end, x.start, x.end)]
                    self.assertTrue(2 * ms[0] <= ms[2] + ms[3] < 2 * ms[1], (s.start, s.end, x.start, x.end))


if __name__ == "__main__":
    unittest.main()
