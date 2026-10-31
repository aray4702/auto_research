"""Streaming ASR's LocalAgreement-2, without the pause rule, on a fake engine (design/internal/g2-stage1.md §1,
item 1b). No model."""

import unittest

import numpy

from lmt.live import asr
from lmt.live.asr import AsrConfig, StreamingASR
from lmt.transcript import Word

BLOCK = 1600
U = 16000
CONFIG = AsrConfig(update=U)  # the scripted hypotheses assume U = 1.0 s, not the default


class FakeEngine:
    """Returns scripted hypotheses, keyed by the buffer's start and length in samples. The audio fed is each
    sample's own index, so the buffer's first sample is its start. A script entry is (start, end, text) in
    absolute seconds; the engine returns them relative to the buffer, as the real one does."""

    def __init__(self, script):
        self.script = script
        self.calls = []  # (start, length, initial_prompt)

    def transcribe(self, audio, *, initial_prompt=None):
        start, length = int(audio[0]), len(audio)
        self.calls.append((start, length, initial_prompt))
        hyp = self.script(start, length) if callable(self.script) else self.script[(start, length)]
        return [Word(s - start / 16000, e - start / 16000, text, 0.5) for s, e, text in hyp]


def make(engine, config=CONFIG):
    """All speech, so no gap: the pause rule (1c) never applies here."""
    return StreamingASR(engine, config, speech=lambda b: [(0, len(b))])


class Feeder:
    """Feeds `asr` the audio after what it has had, in blocks, and keeps every update."""

    def __init__(self, stage):
        self.stage = stage
        self.position = 0
        self.updates = []

    def feed(self, samples):
        update = self.stage.feed(numpy.arange(self.position, self.position + samples, dtype=numpy.float32))
        self.position += samples
        self.updates.append(update)
        return update

    def blocks(self, n):
        return [self.feed(BLOCK) for _ in range(n)]

    def seconds(self, s):
        """Feeds `s` seconds in blocks; the words committed on the way."""
        return [w for u in self.blocks(int(s * 10)) for w in u.words]


def texts(words):
    return [w.text for w in words]


class AgreementTest(unittest.TestCase):
    def test_nothing_is_committed_on_the_first_update(self):
        engine = FakeEngine({(0, U): [(0.1, 0.4, "the"), (0.5, 0.9, "cat")]})
        feeder = Feeder(make(engine))
        updates = feeder.blocks(10)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual([u.words for u in updates], [[]] * 10)
        self.assertEqual(updates[-1].done_until, 0)

    def test_two_agreeing_updates_commit_their_common_prefix_by_key(self):
        engine = FakeEngine({
            (0, U): [(0.1, 0.3, "the"), (0.4, 0.6, "cat"), (0.7, 0.95, "started?"), (1.0, 1.0, "to")],
            (0, 2 * U): [(0.11, 0.31, "The"), (0.41, 0.61, "cat"), (0.71, 0.96, "started"), (1.1, 1.4, "now")],
        })
        feeder = Feeder(make(engine))
        self.assertEqual(feeder.seconds(1), [])
        update = feeder.blocks(10)[-1]
        # The second update's text, times and probability.
        self.assertEqual(update.words, [Word(0.11, 0.31, "The", 0.5), Word(0.41, 0.61, "cat", 0.5),
                                        Word(0.71, 0.96, "started", 0.5)])
        # The buffer is cut at the last committed word's end, and done_until is buffer_start after the cut.
        self.assertEqual(update.done_until, 15360)
        engine.script[(15360, 3 * U - 15360)] = [(1.1, 1.4, "now"), (1.5, 1.8, "then")]
        self.assertEqual(texts(feeder.seconds(1)), ["now"])

    def test_a_disagreement_holds_a_word_back_one_update(self):
        engine = FakeEngine({
            (0, U): [(0.1, 0.3, "a"), (0.4, 0.6, "b"), (0.7, 0.9, "c")],
            (0, 2 * U): [(0.1, 0.3, "a"), (0.4, 0.6, "x"), (0.7, 0.9, "c")],
            (4800, 3 * U - 4800): [(0.4, 0.6, "x"), (0.7, 0.9, "c"), (1.0, 1.2, "d")],
        })
        feeder = Feeder(make(engine))
        self.assertEqual(texts(feeder.seconds(2)), ["a"])
        self.assertEqual(texts(feeder.seconds(1)), ["x", "c"])

    def test_words_ending_by_the_last_committed_end_are_dropped(self):
        engine = FakeEngine({
            (0, U): [(0.1, 0.5, "one"), (0.6, 0.9, "two")],
            (0, 2 * U): [(0.1, 0.5, "one"), (0.6, 0.9, "three")],
            # A word ending exactly at the cut: dropped, so "three" agrees with `previous`.
            (8000, 3 * U - 8000): [(0.45, 0.5, "won"), (0.6, 0.9, "three")],
        })
        feeder = Feeder(make(engine))
        self.assertEqual(texts(feeder.seconds(2)), ["one"])
        self.assertEqual(texts(feeder.seconds(1)), ["three"])

    def test_the_ngram_check_drops_a_repeated_word_at_the_buffers_start(self):
        engine = FakeEngine({
            (0, U): [(0.1, 0.5, "hello"), (0.55, 0.9, "world"), (0.95, 1.0, "foo")],
            (0, 2 * U): [(0.1, 0.5, "hello"), (0.55, 0.9, "world"), (1.2, 1.5, "bar")],
            # "World," again, cut in two at the buffer's start: it ends after the last commit, so only the
            # n-gram check drops it, and "bar" then agrees.
            (14400, 3 * U - 14400): [(0.92, 1.1, "World,"), (1.2, 1.5, "bar"), (1.6, 1.9, "baz")],
        })
        feeder = Feeder(make(engine))
        self.assertEqual(texts(feeder.seconds(2)), ["hello", "world"])
        self.assertEqual(texts(feeder.seconds(1)), ["bar"])

    def test_the_ngram_check_ignores_a_word_more_than_1s_after_the_buffers_start(self):
        engine = FakeEngine({
            (0, U): [(0.1, 0.5, "no"), (0.55, 0.9, "no")],
            (0, 2 * U): [(0.1, 0.5, "no"), (0.55, 0.9, "yes")],
            (8000, 3 * U - 8000): [(1.6, 1.9, "no"), (2.0, 2.2, "end")],
        })
        feeder = Feeder(make(engine))
        self.assertEqual(texts(feeder.seconds(2)), ["no"])
        # "no" at 1.6 s starts 1.1 s after buffer_start (0.5 s), so it stays and disagrees with "yes".
        self.assertEqual(feeder.seconds(1), [])
        self.assertEqual(engine.calls[-1][:2], (8000, 3 * U - 8000))

    def test_keys_ignore_case_and_outer_punctuation_only(self):
        self.assertEqual(asr.key(" ¿Started?! "), "started")
        self.assertEqual(asr.key("«Don't»"), "don't")
        self.assertEqual(asr.key("STRASSE"), asr.key("straße"))
        self.assertNotEqual(asr.key("it's"), asr.key("its"))


class TimesTest(unittest.TestCase):
    def test_times_are_clamped_rounded_and_never_overlap(self):
        hyp = [(0.100049, 0.5, "a"), (0.4, 0.45, "b"), (0.9, 2.5, "c")]
        engine = FakeEngine({(0, U): hyp, (0, 2 * U): hyp})
        feeder = Feeder(make(engine))
        feeder.seconds(1)
        words = feeder.seconds(1)
        # b starts at a's end and lasts 10 ms; c's end is clipped to the buffer's end (2.0 s).
        self.assertEqual([(w.start, w.end) for w in words], [(0.1, 0.5), (0.5, 0.51), (0.9, 2.0)])

    def test_a_later_commit_never_starts_before_the_last_committed_end(self):
        engine = FakeEngine({
            (0, U): [(0.1, 0.8, "a"), (0.9, 1.0, "b")],
            (0, 2 * U): [(0.1, 0.8, "a"), (0.9, 1.2, "c")],
            (12800, 3 * U - 12800): [(0.75, 1.0, "c"), (1.1, 1.3, "d")],
        })
        feeder = Feeder(make(engine))
        self.assertEqual(feeder.seconds(2), [Word(0.1, 0.8, "a", 0.5)])
        self.assertEqual(feeder.seconds(1), [Word(0.8, 1.0, "c", 0.5)])


class BufferTest(unittest.TestCase):
    @staticmethod
    def flicker(start, length):
        """A hypothesis whose first word changes every update, so agreement never commits."""
        return [(1.0, 1.5, f"x{length}"), (3.0, 3.5, "y"), (14.5, 15.5, "z")]

    def test_overflow_at_15s_commits_the_words_2s_before_the_end_and_cuts_after_them(self):
        engine = FakeEngine(self.flicker)
        feeder = Feeder(make(engine))
        self.assertEqual(feeder.seconds(15), [])  # 15 s is the most the buffer holds
        self.assertEqual(engine.calls[-1][:2], (0, 15 * U))
        update = feeder.blocks(10)[-1]
        # At 16 s: "x" and "y" end at least 2 s before the end; "z" doesn't.
        self.assertEqual(texts(update.words), ["x256000", "y"])
        self.assertEqual(update.done_until, 3.5 * U)
        engine.script = lambda start, length: [(14.5, 15.5, "z"), (16.0, 16.5, "w")]
        self.assertEqual(texts(feeder.seconds(1)), ["z"])
        self.assertEqual(engine.calls[-1][:2], (56000, 17 * U - 56000))

    def test_overflow_without_such_words_keeps_the_last_15s(self):
        def hyp(start, length):  # one word, ending at the buffer's end and new each update
            end = (start + length) / 16000
            return [(end - 1.5, end, f"w{start + length}")]

        engine = FakeEngine(hyp)
        feeder = Feeder(make(engine))
        update = feeder.blocks(160)[-1]
        self.assertEqual(update.words, [])
        self.assertEqual(update.done_until, U)
        feeder.seconds(1)
        self.assertEqual(engine.calls[-1][:2], (U, 16 * U))

    def test_a_feed_of_25_blocks_runs_one_update(self):
        engine = FakeEngine(lambda start, length: [])
        feeder = Feeder(make(engine))
        feeder.feed(25 * BLOCK)
        self.assertEqual([c[:2] for c in engine.calls], [(0, 25 * BLOCK)])
        # The next update is at the first multiple of U after the position: 3 s.
        feeder.blocks(4)
        self.assertEqual(len(engine.calls), 1)
        feeder.blocks(1)
        self.assertEqual([c[:2] for c in engine.calls], [(0, 25 * BLOCK), (0, 3 * U)])

    def test_updates_fall_on_the_multiples_of_u(self):
        engine = FakeEngine(lambda start, length: [])
        feeder = Feeder(make(engine, AsrConfig(update=8000)))
        feeder.seconds(3)
        self.assertEqual([c[1] for c in engine.calls], [8000 * k for k in range(1, 7)])


class FlushTest(unittest.TestCase):
    def test_flush_commits_the_rest(self):
        engine = FakeEngine({
            (0, U): [(0.1, 0.3, "a"), (0.4, 0.6, "b")],
            (0, U + 800): [(0.1, 0.3, "a"), (0.4, 0.6, "c"), (0.7, 1.05, "d")],
        })
        stage = make(engine)
        feeder = Feeder(stage)
        feeder.seconds(1)
        feeder.feed(800)
        update = stage.flush()
        self.assertEqual(texts(update.words), ["a", "c", "d"])
        self.assertEqual(update.done_until, U + 800)

    def test_flush_with_less_than_a_block_left_transcribes_nothing(self):
        engine = FakeEngine({})
        stage = make(engine)
        Feeder(stage).feed(BLOCK - 1)
        update = stage.flush()
        self.assertEqual((update.words, update.done_until), ([], BLOCK - 1))
        self.assertEqual(engine.calls, [])

    def test_flush_drops_what_is_already_committed(self):
        engine = FakeEngine({
            (0, U): [(0.1, 0.3, "a"), (0.4, 0.6, "b")],
            (0, 2 * U): [(0.1, 0.3, "a"), (0.4, 0.6, "c")],
            (4800, 2 * U + BLOCK - 4800): [(0.2, 0.3, "a"), (0.4, 0.6, "c"), (2.0, 2.05, "e")],
        })
        stage = make(engine)
        feeder = Feeder(stage)
        self.assertEqual(texts(feeder.seconds(2)), ["a"])
        feeder.feed(BLOCK)
        self.assertEqual(texts(stage.flush().words), ["c", "e"])


class PromptTest(unittest.TestCase):
    def test_the_prompt_is_the_committed_texts_last_200_characters_at_a_word_boundary(self):
        words = [f"ab{i:02d}" for i in range(60)]  # 5 characters with a space: 299 in all
        hyp = [(0.01 * i, 0.01 * i + 0.005, w) for i, w in enumerate(words)]
        engine = FakeEngine(lambda start, length: hyp if start == 0 else [])
        feeder = Feeder(make(engine))
        self.assertEqual(texts(feeder.seconds(2)), words)
        feeder.seconds(1)
        prompts = [c[2] for c in engine.calls]
        self.assertEqual(prompts[:2], [None, None])
        self.assertEqual(prompts[2], " ".join(words[20:]))
        self.assertEqual(prompts[2], " ".join(words)[-200:].lstrip())

    def test_the_prompt_never_cuts_a_word(self):
        words = ["a" * 150, "b" * 100, "c" * 60]
        hyp = [(0.1 * i, 0.1 * i + 0.05, w) for i, w in enumerate(words)]
        engine = FakeEngine(lambda start, length: hyp if start == 0 else [])
        feeder = Feeder(make(engine))
        feeder.seconds(3)
        self.assertEqual(engine.calls[-1][2], "b" * 100 + " " + "c" * 60)


class ConfigTest(unittest.TestCase):
    def test_the_provisional_defaults(self):
        self.assertEqual(asr.DEFAULT_ASR_CONFIG, AsrConfig(update=12800, max_buffer=240000, pause=8000,
                                                           prompt_chars=200))


if __name__ == "__main__":
    unittest.main()
