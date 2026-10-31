"""Streaming ASR's pause rule on Silero VAD's gaps (design/internal/g2-stage1.md §1, step 4, item 1c): with
1b's fake engine and a scripted `speech`, and one `models` case for `vad_speech` itself."""

import inspect
import unittest
from pathlib import Path

import numpy
import pytest
from test_live_asr import CONFIG, FakeEngine, Feeder, U, texts

from lmt import audio
from lmt.live import asr
from lmt.live.asr import AsrConfig, StreamingASR

CLIP = Path(__file__).resolve().parent / "fixtures" / "clip.wav"


class Speech:
    """Scripted speech chunks, in absolute samples. Like the fake engine's audio, the buffer's first sample is
    its start, so it returns the chunks that overlap the buffer, clipped to it, relative to its start."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []  # (start, length)

    def __call__(self, buffer):
        start, length = int(buffer[0]), len(buffer)
        self.calls.append((start, length))
        return [(max(s, start) - start, min(e, start + length) - start)
                for s, e in self.chunks if s < start + length and e > start]


def make(engine, speech, config=CONFIG):
    return StreamingASR(engine, config, speech=speech)


class GapTest(unittest.TestCase):
    def test_a_gap_commits_the_words_before_it_and_cuts_at_its_end_less_200ms(self):
        engine = FakeEngine({
            # The gap is 6,400 to 16,000 (0.4 to 1.0 s). "so" starts in its last 0.2 s, so it doesn't
            # straddle it and isn't committed.
            (0, U): [(0.1, 0.25, "hello"), (0.26, 0.42, "there"), (0.82, 0.98, "so")],
            (12800, 2 * U - 12800): [(0.82, 0.98, "so"), (1.1, 1.3, "then")],
        })
        speech = Speech([(1600, 6400), (U, 2 * U)])
        feeder = Feeder(make(engine, speech))
        update = feeder.blocks(10)[-1]
        # On the first update, where agreement commits nothing.
        self.assertEqual(texts(update.words), ["hello", "there"])
        self.assertEqual(update.done_until, U - 3200)
        self.assertEqual(speech.calls, [(0, U)])
        # The cut kept "so"'s lead-in; it agrees now.
        self.assertEqual(texts(feeder.seconds(1)), ["so"])
        self.assertEqual(engine.calls[-1][:2], (12800, 2 * U - 12800))

    def test_a_word_ending_200ms_into_the_gap_is_committed(self):
        engine = FakeEngine({(0, U): [(0.1, 0.25, "hello"), (0.26, 0.6, "there")]})
        feeder = Feeder(make(engine, Speech([(1600, 6400)])))
        self.assertEqual(texts(feeder.seconds(1)), ["hello", "there"])

    def test_a_gap_a_word_straddles_isnt_used(self):
        # "there" starts before 0.8 s and ends after 0.6 s.
        engine = FakeEngine({(0, U): [(0.1, 0.25, "hello"), (0.26, 0.61, "there")]})
        feeder = Feeder(make(engine, Speech([(1600, 6400)])))
        update = feeder.blocks(10)[-1]
        self.assertEqual((update.words, update.done_until), ([], 0))

    def test_a_gap_shorter_than_pause_isnt_one(self):
        hyp = [(0.1, 0.25, "hello"), (0.26, 0.42, "there")]
        for end, committed, done_until in [(14399, [], 0), (14400, ["hello", "there"], 14400 - 3200)]:
            with self.subTest(gap=end - 6400):
                engine = FakeEngine({(0, U): hyp})
                feeder = Feeder(make(engine, Speech([(1600, 6400), (end, U)])))
                update = feeder.blocks(10)[-1]
                self.assertEqual((texts(update.words), update.done_until), (committed, done_until))

    def test_pause_comes_from_the_config(self):
        engine = FakeEngine({(0, U): [(0.1, 0.25, "hello"), (0.26, 0.42, "there")]})
        feeder = Feeder(make(engine, Speech([(1600, 6400)]), AsrConfig(update=U, pause=9601)))
        self.assertEqual(feeder.seconds(1), [])

    def test_the_last_gap_is_taken(self):
        # Two gaps: 1,600 to 9,600 and 11,200 to 32,000. The first would commit "a" alone and cut at 6,400.
        engine = FakeEngine({(0, 2 * U): [(0.02, 0.08, "a"), (0.62, 0.68, "b")]})
        feeder = Feeder(make(engine, Speech([(0, 1600), (9600, 11200)]), AsrConfig(update=2 * U)))
        update = feeder.blocks(20)[-1]
        self.assertEqual(texts(update.words), ["a", "b"])
        self.assertEqual(update.done_until, 2 * U - 3200)

    def test_a_buffer_with_no_speech_at_all_is_one_gap(self):
        engine = FakeEngine({(0, U): []})
        feeder = Feeder(make(engine, Speech([])))
        update = feeder.blocks(10)[-1]
        self.assertEqual((update.words, update.done_until), ([], U - 3200))

    def test_a_short_buffer_with_no_speech_is_no_gap(self):
        engine = FakeEngine({(0, U // 2): [(0.1, 0.2, "a")]})
        feeder = Feeder(make(engine, Speech([]), AsrConfig(update=U // 2, pause=U // 2 + 1)))
        update = feeder.blocks(5)[-1]
        self.assertEqual((update.words, update.done_until), ([], 0))

    def test_flush_commits_every_word_without_asking_for_speech(self):
        engine = FakeEngine({(0, 3200): [(0.01, 0.05, "a"), (0.1, 0.2, "b")]})
        speech = Speech([])
        stage = make(engine, speech)
        Feeder(stage).blocks(2)
        self.assertEqual(texts(stage.flush().words), ["a", "b"])
        self.assertEqual(speech.calls, [])


class CommitTest(unittest.TestCase):
    """n is max(len(A), len(P)), and the cut is at the gap only when len(P) >= len(A)."""

    def run_two(self, first, second, chunks):
        """The first update with all speech, the second with `chunks`; the second's update."""
        engine = FakeEngine({(0, U): first, (0, 2 * U): second})
        speech = Speech([(0, 2 * U)])
        feeder = Feeder(make(engine, speech))
        self.assertEqual(feeder.seconds(1), [])
        speech.chunks = chunks
        return feeder.blocks(10)[-1]

    def test_agreement_longer_than_p_commits_a_and_cuts_after_it(self):
        first = [(0.1, 0.3, "a"), (0.4, 0.6, "b"), (1.2, 1.4, "c")]
        # The gap is 8,000 to 16,800: P is "a b", A is "a b c".
        update = self.run_two(first, [*first, (1.5, 1.7, "d")], [(0, 8000), (16800, 2 * U)])
        self.assertEqual(texts(update.words), ["a", "b", "c"])
        self.assertEqual(update.done_until, 1.4 * U)

    def test_p_longer_than_agreement_commits_p_and_cuts_at_the_gap(self):
        first = [(0.1, 0.3, "a"), (0.4, 0.6, "b"), (0.7, 0.9, "c")]
        second = [(0.1, 0.3, "a"), (0.4, 0.6, "x"), (0.7, 0.9, "c")]
        update = self.run_two(first, second, [(0, U)])
        self.assertEqual(texts(update.words), ["a", "x", "c"])
        self.assertEqual(update.done_until, 2 * U - 3200)

    def test_p_as_long_as_agreement_cuts_at_the_gap(self):
        first = [(0.1, 0.3, "a"), (0.4, 0.6, "b")]
        update = self.run_two(first, first, [(0, U)])
        self.assertEqual(texts(update.words), ["a", "b"])
        self.assertEqual(update.done_until, 2 * U - 3200)

    def test_without_a_gap_agreement_alone_commits(self):
        first = [(0.1, 0.3, "a"), (0.4, 0.6, "b"), (1.2, 1.9, "c")]
        update = self.run_two(first, [(0.1, 0.3, "a"), (0.4, 0.6, "b"), (1.2, 1.9, "d")], [(0, 2 * U)])
        self.assertEqual(texts(update.words), ["a", "b"])
        self.assertEqual(update.done_until, 0.6 * U)


class DefaultTest(unittest.TestCase):
    def test_the_default_speech_is_vad_speech(self):
        self.assertIs(inspect.signature(StreamingASR).parameters["speech"].default, asr.vad_speech)


@pytest.mark.models
def test_vad_speech_on_silence_and_on_the_clip():
    """Loads Silero's ONNX model and onnxruntime."""
    assert asr.vad_speech(numpy.zeros(2 * 16000, dtype=numpy.float32)) == []
    buffer = audio.decode(CLIP)[:5 * 16000]
    chunks = asr.vad_speech(buffer)
    print(f"vad_speech on clip.wav's first 5 s: {chunks}")
    assert chunks
    assert all(isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(buffer) for s, e in chunks)
