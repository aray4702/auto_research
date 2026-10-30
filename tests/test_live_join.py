"""The join, on scripted ASR and diarization updates and no model (design/internal/g2-stage1.md §3a, item 3a1)."""

import unittest

from lmt import align
from lmt.live.asr import AsrUpdate
from lmt.live.diarize import DiarUpdate, Piece
from lmt.live.join import Join, SegmentOut, WordsOut
from lmt.transcript import Segment, Speaker, Turn, Word

RATE = 16000


def word(start, end, text="word"):
    return Word(start, end, text, 0.9)


def asr(*words, done=0.0):
    return AsrUpdate(list(words), round(done * RATE))


def activity(final_until, *pieces):
    """Pieces as (start, end, internal speaker), in seconds."""
    return DiarUpdate([Piece(round(a * RATE), round(b * RATE), g) for a, b, g in pieces], round(final_until * RATE))


def segment(speaker, *words):
    return Segment(words[0].start, words[-1].end, speaker, align.segment_text(words), tuple(words))


def kinds(out):
    return [type(o).__name__ for o in out]


class Labelling(unittest.TestCase):
    def test_a_word_waits_for_final_activity_to_reach_its_end_then_takes_the_overlapping_speaker(self):
        join = Join()
        w = word(0.2, 0.6)
        join.add_words(asr(w))
        join.add_activity(activity(0.5, (0.0, 0.3, 0), (0.3, 0.5, 1)))
        self.assertEqual(join.poll(), [])
        join.add_activity(activity(1.0, (0.5, 1.0, 1)))   # touches speaker 1's turn, which grows to 0.3-1.0
        self.assertEqual(join.poll(), [WordsOut(1, "S1", "Speaker 1", [w])])
        self.assertEqual(join.speakers, [Speaker("S1", "Speaker 1")])

    def test_the_nearest_turn_within_1_s_is_looked_for_only_back(self):
        join = Join()
        first, w = word(0.1, 0.4), word(2.0, 2.5)
        turns = [(0.0, 0.5, 1), (1.2, 1.8, 0), (2.6, 3.0, 1)]   # w is 0.2 s after speaker 0, 0.1 s before speaker 1
        join.add_words(asr(first, w))
        join.add_activity(activity(3.0, *turns))
        out = join.poll()
        # Offline, looking both ways, w would take speaker 1 (the previous word's), 0.1 s ahead.
        self.assertEqual(align.assign_speaker(w, [Turn(*t) for t in turns], 1), 1)
        self.assertEqual(out[-1], WordsOut(2, "S2", "Speaker 2", [w]))

    def test_a_turn_starting_at_the_words_end_is_ahead_of_it(self):
        join = Join()
        first, w = word(0.1, 0.4), word(1.0, 1.3)
        join.add_words(asr(first, w))
        join.add_activity(activity(2.0, (0.0, 0.5, 0), (1.3, 2.0, 1)))
        # Speaker 0's turn, 0.5 s back, not speaker 1's, which starts as w ends.
        self.assertEqual(join.poll()[-1], WordsOut(1, "S1", "Speaker 1", [first, w]))

    def test_the_first_words_wait_for_the_first_turn_and_take_its_speaker(self):
        join = Join()
        w1, w2 = word(0.1, 0.4), word(0.5, 0.9)
        join.add_words(asr(w1, w2))
        join.add_activity(activity(2.0))
        self.assertEqual(join.poll(), [])
        join.add_activity(activity(4.0, (3.0, 4.0, 5)))   # more than 1 s after both words
        self.assertEqual(join.poll(), [WordsOut(1, "S1", "Speaker 1", [w1, w2])])

    def test_ids_follow_the_first_labelled_word_not_the_diarizers_order(self):
        join = Join()
        w1, w2, w3 = word(1.2, 1.8), word(2.2, 2.8), word(3.2, 3.8)
        join.add_words(asr(w1, w2, w3))
        # Internal speaker 0 is the diarizer's first, but speaks no word before speaker 1 does.
        join.add_activity(activity(4.0, (0.0, 0.5, 0), (1.0, 2.0, 1), (2.0, 3.0, 0), (3.0, 4.0, 1)))
        join.finish()
        self.assertEqual(join.speakers, [Speaker("S1", "Speaker 1"), Speaker("S2", "Speaker 2")])
        self.assertEqual([s.speaker for s in join.segments], ["S1", "S2", "S1"])


class Segments(unittest.TestCase):
    def test_a_speaker_change_closes_the_segment_and_its_segment_event_comes_before_the_next_words(self):
        join = Join()
        w1, w2 = word(0.1, 0.5), word(0.6, 0.9)
        join.add_words(asr(w1, w2))
        join.add_activity(activity(1.0, (0.0, 0.55, 0), (0.55, 1.0, 1)))
        self.assertEqual(join.poll(), [WordsOut(1, "S1", "Speaker 1", [w1]),
                                       SegmentOut(segment("S1", w1), 1),
                                       WordsOut(2, "S2", "Speaker 2", [w2])])

    def test_a_pause_of_1_s_closes_the_segment_and_a_shorter_one_doesnt(self):
        join = Join()
        w1, w2, w3 = word(0.1, 0.5), word(1.499, 1.9), word(2.9, 3.2)   # 0.999 s, then 1.000 s
        join.add_words(asr(w1, w2, w3))
        join.add_activity(activity(4.0, (0.0, 4.0, 0)))
        self.assertEqual(join.poll(), [WordsOut(1, "S1", "Speaker 1", [w1, w2]),
                                       SegmentOut(segment("S1", w1, w2), 1),
                                       WordsOut(2, "S1", "Speaker 1", [w3])])

    def test_a_sentence_end_closes_the_segment_at_once(self):
        join = Join()
        w1, w2 = word(0.1, 0.5, "Hello"), word(0.6, 0.9, "there.\"")
        join.add_words(asr(w1, w2))
        join.add_activity(activity(1.0, (0.0, 1.0, 0)))
        self.assertEqual(join.poll(), [WordsOut(1, "S1", "Speaker 1", [w1, w2]),
                                       SegmentOut(segment("S1", w1, w2), 1)])

    def test_a_segment_never_passes_30_s(self):
        join = Join()
        words = [word(round(0.9 * k, 3), round(0.9 * k + 0.5, 3)) for k in range(40)]   # 0.4 s gaps
        join.add_words(asr(*words))
        join.add_activity(activity(40.0, (0.0, 40.0, 0)))
        out = join.poll()
        self.assertEqual(kinds(out), ["WordsOut", "SegmentOut", "WordsOut"])
        self.assertEqual(out[1], SegmentOut(segment("S1", *words[:33]), 1))   # 29.3 s; the next would end at 30.2
        self.assertEqual(out[2].words, words[33:])

    def test_done_until_1_s_past_the_last_word_closes_the_segment(self):
        join = Join()
        w = word(0.1, 0.5)
        join.add_words(asr(w, done=1.499))
        join.add_activity(activity(1.0, (0.0, 0.5, 0)))
        self.assertEqual(join.poll(), [WordsOut(1, "S1", "Speaker 1", [w])])
        join.add_words(asr(done=1.5))
        self.assertEqual(join.poll(), [SegmentOut(segment("S1", w), 1)])

    def test_done_until_doesnt_close_while_a_word_waits_for_its_label(self):
        join = Join()
        w1, w2 = word(0.1, 0.5), word(0.7, 1.2)
        join.add_words(asr(w1, w2, done=5.0))
        join.add_activity(activity(1.0, (0.0, 1.0, 0)))
        self.assertEqual(join.poll(), [WordsOut(1, "S1", "Speaker 1", [w1])])
        join.add_activity(activity(1.5, (1.0, 1.5, 0)))
        self.assertEqual(join.poll(), [WordsOut(1, "S1", "Speaker 1", [w2]),
                                       SegmentOut(segment("S1", w1, w2), 1)])

    def test_each_poll_sends_its_own_words_and_segments_are_numbered_from_1(self):
        join = Join()
        w1, w2, w3 = word(0.1, 0.5), word(0.6, 0.9, "end."), word(1.0, 1.4)
        join.add_words(asr(w1))
        join.add_activity(activity(0.5, (0.0, 0.5, 0)))
        self.assertEqual(join.poll(), [WordsOut(1, "S1", "Speaker 1", [w1])])
        join.add_words(asr(w2, w3))
        join.add_activity(activity(1.5, (0.5, 1.5, 0)))
        self.assertEqual(join.poll(), [WordsOut(1, "S1", "Speaker 1", [w2]),
                                       SegmentOut(segment("S1", w1, w2), 1),
                                       WordsOut(2, "S1", "Speaker 1", [w3])])
        self.assertEqual(join.segments, [segment("S1", w1, w2)])


class Finish(unittest.TestCase):
    def test_finish_labels_every_word_left_and_closes_the_last_segment(self):
        join = Join()
        w1, w2, w3 = word(0.1, 0.5), word(0.6, 0.9), word(1.0, 1.6)
        join.add_words(asr(w1, w2, w3))
        join.add_activity(activity(0.5, (0.0, 0.5, 0)))
        self.assertEqual(join.poll(), [WordsOut(1, "S1", "Speaker 1", [w1])])
        self.assertEqual(join.finish(), [WordsOut(1, "S1", "Speaker 1", [w2, w3]),
                                         SegmentOut(segment("S1", w1, w2, w3), 1)])
        self.assertEqual(join.segments, [segment("S1", w1, w2, w3)])
        self.assertEqual(join.speakers, [Speaker("S1", "Speaker 1")])

    def test_without_any_speech_found_the_words_take_a_speaker_of_their_own(self):
        join = Join()
        w1, w2 = word(0.1, 0.5), word(0.6, 0.9)
        join.add_words(asr(w1, w2, done=3.0))
        join.add_activity(activity(3.0))
        self.assertEqual(join.poll(), [])
        self.assertEqual(join.finish(), [WordsOut(1, "S1", "Speaker 1", [w1, w2]),
                                         SegmentOut(segment("S1", w1, w2), 1)])
        self.assertEqual(join.speakers, [Speaker("S1", "Speaker 1")])

    def test_finish_with_nothing_said(self):
        join = Join()
        join.add_activity(activity(2.0, (0.0, 2.0, 0)))
        self.assertEqual(join.finish(), [])
        self.assertEqual((join.speakers, join.segments), ([], []))


class Memory(unittest.TestCase):
    def test_old_turns_are_dropped(self):
        join = Join()
        for k in range(100):   # 100 s, the speaker alternating each second, one word a second
            join.add_words(asr(word(k + 0.2, k + 0.7), done=k + 1.0))
            join.add_activity(activity(k + 1.0, (k, k + 1.0, k % 2)))
            join.poll()
        self.assertLessEqual(len(join._turns), 4)
        self.assertEqual(len(join.finish()), 1)
        self.assertEqual([s.speaker for s in join.segments], ["S1", "S2"] * 50)


if __name__ == "__main__":
    unittest.main()
