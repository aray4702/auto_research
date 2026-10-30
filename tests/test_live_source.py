"""The file source and lockstep (design/internal/g2-stage1.md §1, item 1a). No model."""

import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from lmt import audio
from lmt.errors import InputError
from lmt.live import source
from lmt.live.source import BLOCK, FileSource, lockstep


def write_wav(path, n):
    """`n` samples of 16 kHz mono, sample t holding t % 30000 - 15000, so each block's samples differ."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.arange(n) % 30000 - 15000).astype("<i2").tobytes())
    return path


class FakeTime:
    """A clock that moves only when slept on, or when a test moves it. Records each sleep."""

    def __init__(self, now=100.0):
        self.now = now
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Recorder:
    """A stage that logs each feed and flush in a log it shares with `Taken`."""

    def __init__(self, name, log):
        self.name, self.log = name, log

    def feed(self, samples):
        self.log.append(("feed", self.name, len(samples)))
        return (self.name, len(samples))

    def flush(self):
        self.log.append(("flush", self.name))
        return (self.name, "flush")


class Taken:
    """Wraps a source, logging each block as it is taken."""

    def __init__(self, inner, log):
        self.inner, self.log = inner, log

    @property
    def position(self):
        return self.inner.position

    def blocks(self):
        for block in self.inner.blocks():
            self.log.append(("take", len(block)))
            yield block


class FileSourceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.wav = write_wav(self.dir / "talk.wav", 16800)  # 1.05 s

    def test_blocks_of_1600_the_last_shorter_position_rising_before_each(self):
        src = FileSource(self.wav, paced=False)
        self.assertEqual(src.name, "talk.wav")
        self.assertEqual(src.position, 0)
        self.assertIsNone(src.reason)
        sizes, positions, got = [], [], []
        for block in src.blocks():
            sizes.append(len(block))
            positions.append(src.position)
            got.append(block)
        self.assertEqual(sizes, [BLOCK] * 10 + [800])
        self.assertEqual(positions, [1600 * i for i in range(1, 11)] + [16800])
        np.testing.assert_array_equal(np.concatenate(got), audio.decode(self.wav))
        self.assertEqual(src.reason, "end-of-input")

    def test_limit_trims_at_exactly_limit_samples(self):
        src = FileSource(self.wav, paced=False, limit=5000)
        sizes = [len(block) for block in src.blocks()]
        self.assertEqual(sizes, [1600, 1600, 1600, 200])
        self.assertEqual(src.position, 5000)
        self.assertEqual(src.reason, "duration")

    def test_a_limit_past_the_end_runs_out(self):
        src = FileSource(self.wav, paced=False, limit=20000)
        self.assertEqual(sum(len(block) for block in src.blocks()), 16800)
        self.assertEqual(src.reason, "end-of-input")

    def test_stop_hands_over_no_further_block(self):
        src = FileSource(self.wav, paced=False)
        blocks = src.blocks()
        for _ in range(3):
            next(blocks)
        src.stop()
        self.assertEqual(list(blocks), [])
        self.assertEqual(src.position, 4800)
        self.assertEqual(src.reason, "signal")

    def test_a_missing_file_and_an_empty_one_raise_input_error(self):
        empty = self.dir / "empty.wav"
        empty.write_bytes(b"")
        for path in (self.dir / "missing.wav", empty):
            with self.subTest(path=path.name), self.assertRaises(InputError):
                FileSource(path, paced=False)


class PacingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wav = write_wav(Path(tmp.name) / "talk.wav", 8000)  # 5 blocks

    def test_block_i_is_released_at_t0_plus_i_plus_1_tenths(self):
        t = FakeTime()
        src = FileSource(self.wav, paced=True, clock=t.clock, sleep=t.sleep)
        released = [t.now for _ in src.blocks()]
        np.testing.assert_allclose(released, [100.1, 100.2, 100.3, 100.4, 100.5])

    def test_t0_is_the_clock_when_blocks_is_first_iterated(self):
        t = FakeTime()
        src = FileSource(self.wav, paced=True, clock=t.clock, sleep=t.sleep)
        blocks = src.blocks()
        t.now = 200.0
        next(blocks)
        self.assertAlmostEqual(t.now, 200.1)

    def test_a_late_block_goes_at_once_and_the_source_does_not_drift(self):
        t = FakeTime()
        src = FileSource(self.wav, paced=True, clock=t.clock, sleep=t.sleep)
        released = []
        for i, _ in enumerate(src.blocks()):
            released.append(t.now)
            if i == 0:
                t.now += 0.35  # a slow consumer: blocks 1 to 3 are already late
        np.testing.assert_allclose(released, [100.1, 100.45, 100.45, 100.45, 100.5])
        self.assertEqual(len(t.sleeps), 2)  # before block 0 and block 4 only

    def test_stop_during_the_wait_hands_over_nothing_more(self):
        t = FakeTime()

        def sleep(seconds):
            t.sleep(seconds)
            src.stop()

        src = FileSource(self.wav, paced=True, clock=t.clock, sleep=sleep)
        self.assertEqual(list(src.blocks()), [])
        self.assertEqual(src.position, 0)
        self.assertEqual(src.reason, "signal")


class LockstepTest(unittest.TestCase):
    def test_each_stage_sees_each_block_before_the_next_is_taken_and_flushes_come_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "talk.wav", 4000)  # 1600, 1600, 800
            log = []
            src = FileSource(wav, paced=False)
            out = list(lockstep(Taken(src, log), Recorder("a", log), Recorder("b", log)))
        expected = []
        for n in (1600, 1600, 800):
            expected += [("take", n), ("feed", "a", n), ("feed", "b", n)]
        expected += [("flush", "a"), ("flush", "b")]
        self.assertEqual(log, expected)
        self.assertEqual(out, [
            (1600, [("a", 1600), ("b", 1600)]),
            (3200, [("a", 1600), ("b", 1600)]),
            (4000, [("a", 800), ("b", 800)]),
            (4000, [("a", "flush"), ("b", "flush")]),
        ])

    def test_lockstep_works_on_the_source_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = FileSource(write_wav(Path(tmp) / "talk.wav", 4000), paced=False)
            log = []
            positions = [position for position, _ in source.lockstep(src, Recorder("a", log))]
        self.assertEqual(positions, [1600, 3200, 4000, 4000])
        self.assertEqual(src.reason, "end-of-input")


if __name__ == "__main__":
    unittest.main()
