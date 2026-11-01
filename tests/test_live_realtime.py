"""The threaded run, its stage timings and the "behind" warning, on fake stages and no model
(design/internal/g2-stage1.md §3c, item 3c1)."""

import contextlib
import io
import json
import threading
import time
import unittest

import numpy as np

from lmt.live.asr import AsrUpdate
from lmt.live.diarize import DiarUpdate, Piece
from lmt.live.events import EventWriter
from lmt.live.session import NO_SINKS, Sinks, run_lockstep, run_threaded
from lmt.transcript import Word

RATE = 16000
BLOCK = 1600
SOURCE = {"file": "clip.wav", "live": True}
MODELS = {"asr": {"name": "fake"}, "diarization": {"name": "fake"}}
WAIT = 10  # seconds: a bound on every wait, so a broken run fails rather than hangs


class GatedSource:
    """Blocks of 0.1 s, each block's samples its index, in bursts: before each burst after the first, it calls
    `between(burst)` from the capture thread, so a test can hold it there."""

    def __init__(self, bursts, between=lambda burst: None):
        self.position = 0
        self.reason = None
        self._bursts = bursts
        self._between = between

    def blocks(self):
        i = 0
        for burst, n in enumerate(self._bursts):
            if burst:
                self._between(burst)
            for _ in range(n):
                self.position += BLOCK
                yield np.full(BLOCK, float(i))
                i += 1
        self.reason = "end-of-input"


class FakeStage:
    """Logs each `feed`'s samples, calls `gate(n)` before returning from its n-th `feed` (from 0), and returns an
    empty update reaching what it has been fed."""

    def __init__(self, update, gate=lambda n: None):
        self.fed = []
        self.flushes = 0
        self._update = update
        self._gate = gate
        self._samples = 0

    def feed(self, samples):
        self.fed.append(samples.copy())
        self._samples += len(samples)
        self._gate(len(self.fed) - 1)
        return self._update(self._samples)

    def flush(self):
        self.flushes += 1
        return self._update(self._samples)


def asr_stage(gate=lambda n: None):
    return FakeStage(lambda n: AsrUpdate([], n), gate)


def diar_stage(gate=lambda n: None):
    return FakeStage(lambda n: DiarUpdate([], n), gate)


class Warnings(io.StringIO):
    """stderr, with an event set at each warning."""

    def __init__(self):
        super().__init__()
        self.warned = threading.Event()

    def write(self, text):
        if "behind real time" in text:
            self.warned.set()
        return super().write(text)

    def lines(self):
        return [line for line in self.getvalue().splitlines() if line]


def writer_for(source):
    out = io.StringIO()
    return out, EventWriter(out, jsonl=True, clock_t=lambda: source.position)


def events(out):
    return [json.loads(line) for line in out.getvalue().splitlines()]


def run(source, asr, diar, sinks=NO_SINKS, stderr=None):
    out, writer = writer_for(source)
    with contextlib.redirect_stderr(stderr or io.StringIO()):
        result = run_threaded(source, asr, diar, writer, sinks, source_info=SOURCE, models=MODELS)
    return out, result


def blocks_of(stage):
    """The block indices in the order the stage was fed them."""
    return [int(b[0]) for b in np.concatenate(stage.fed).reshape(-1, BLOCK)]


class Behind(unittest.TestCase):
    """The ASR worker is held on its first feed of each 6 s burst until the main thread has warned, and the
    capture holds the second burst until the ASR has caught up with the first."""

    def setUp(self):
        self.stderr = Warnings()
        self.caught_up = threading.Event()
        self.feeds_at_burst = []

        def gate(n):
            if n in [0, *self.feeds_at_burst]:  # the first feed of each burst
                self.assertTrue(self.stderr.warned.wait(WAIT))
                time.sleep(0.3)  # several more of the main thread's turns while still behind
            if sum(len(b) for b in self.asr.fed) == 60 * BLOCK:
                self.caught_up.set()

        def between(burst):
            self.assertTrue(self.caught_up.wait(WAIT))
            time.sleep(0.3)  # the main thread sees the caught-up update
            self.stderr.warned.clear()
            self.feeds_at_burst.append(len(self.asr.fed))

        self.asr = asr_stage(gate)
        self.diar = diar_stage()
        self.source = GatedSource([60, 60], between)
        self.out, self.result = run(self.source, self.asr, self.diar, stderr=self.stderr)

    def test_a_worker_that_falls_6_s_behind_gets_one_feed_covering_all_the_queued_blocks(self):
        first, second = self.feeds_at_burst[0], len(self.asr.fed)
        # Each burst's first feed took what had queued when it began, and was held; the next took all the rest of
        # the burst at once. (A worker that starts late may take the whole burst in its first feed.)
        self.assertIn(first, (1, 2))
        self.assertEqual(sum(len(b) for b in self.asr.fed[:first]), 60 * BLOCK)
        self.assertIn(second - first, (1, 2))
        self.assertEqual(blocks_of(self.asr), list(range(120)))

    def test_the_warning_appears_once_then_again_only_after_catching_up_and_falling_behind_again(self):
        lines = self.stderr.lines()
        self.assertEqual(len(lines), 2, lines)
        for line in lines:
            self.assertRegex(line, r"^lmt: warning: live: \d+\.\d s behind real time$")

    def test_stats_count_every_feed_and_flush_and_hold_the_largest_behind(self):
        stats = self.result.stats
        self.assertEqual(stats.asr.count, len(self.asr.fed) + 1)
        self.assertEqual(stats.diarization.count, len(self.diar.fed) + 1)
        for stage in (stats.asr, stats.diarization):
            self.assertLessEqual(0, stage.mean)
            self.assertLessEqual(stage.mean, stage.max)
        self.assertGreaterEqual(stats.asr.max, 0.3)  # the held feeds
        self.assertGreater(stats.behind, 5.0)
        self.assertLessEqual(stats.behind, 6.0)


class Flow(unittest.TestCase):
    def test_every_block_reaches_both_stages_in_order_and_each_flushes_once(self):
        asr, diar = asr_stage(lambda n: time.sleep(0.01)), diar_stage()
        source = GatedSource([50])
        _, result = run(source, asr, diar)
        self.assertEqual(blocks_of(asr), list(range(50)))
        self.assertEqual(blocks_of(diar), list(range(50)))
        self.assertEqual((asr.flushes, diar.flushes), (1, 1))
        self.assertEqual((result.samples, result.reason), (50 * BLOCK, "end-of-input"))

    def test_start_comes_first_with_the_source_info_and_models_and_stop_last(self):
        source = GatedSource([5])
        out, _ = run(source, asr_stage(), diar_stage())
        got = events(out)
        self.assertEqual(got[0], {"type": "start", "schema_version": "1.1", "t": 0, "source": SOURCE,
                                  "models": MODELS})
        self.assertEqual(got[-1], {"type": "stop", "t": 0.5, "reason": "end-of-input"})

    def test_on_block_sees_every_block(self):
        seen = []
        source = GatedSource([20])
        run(source, asr_stage(), diar_stage(), Sinks(on_block=lambda b: seen.append(int(b[0]))))
        self.assertEqual(seen, list(range(20)))

    def test_the_main_threads_t_follows_the_source_position(self):
        # "Hello." is committed with the first feed, and labelled once diarization reaches 1 s; the capture holds
        # its second burst until then, so the events are written while the position is 1 s.
        hello = Word(0.0, 0.05, "Hello.", 0.9)
        labelled = threading.Event()
        asr = FakeStage(lambda n: AsrUpdate([hello] if not asr.fed[1:] else [], n))
        diar = FakeStage(lambda n: DiarUpdate([Piece(0, n, 0)] if n == 10 * BLOCK else [], n))
        source = GatedSource([10, 10], lambda burst: labelled.wait(WAIT))
        out, _ = run(source, asr, diar, Sinks(on_segment=lambda s, label: labelled.set()))
        got = events(out)
        # Its text is written once it is committed, in the first burst, before its label.
        self.assertEqual([e["type"] for e in got], ["start", "text", "words", "segment", "stop"])
        self.assertLessEqual(got[1]["t"], 1.0)
        self.assertEqual([e["t"] for e in got[2:4]], [1.0, 1.0])
        self.assertEqual(got[-1]["t"], 2.0)


class Failure(unittest.TestCase):
    def test_a_workers_exception_is_raised_after_the_on_segment_calls_already_made(self):
        hello = Word(0.0, 0.05, "Hello.", 0.9)
        segments = []
        closed = threading.Event()

        def fail(n):
            if n == 2:
                self.assertTrue(closed.wait(WAIT))
                raise RuntimeError("the ASR broke")

        def on_segment(segment, label):
            segments.append((segment.text, label))
            closed.set()

        asr = FakeStage(lambda n: AsrUpdate([hello] if not asr.fed[1:] else [], n), fail)
        diar = FakeStage(lambda n: DiarUpdate([Piece(0, BLOCK, 0)] if n <= BLOCK else [], n))
        source = GatedSource([1, 1, 1], lambda burst: time.sleep(0.2))  # one block per feed
        with self.assertRaisesRegex(RuntimeError, "the ASR broke"):
            run(source, asr, diar, Sinks(on_segment=on_segment))
        self.assertEqual(segments, [("Hello.", "Speaker 1")])


class EndlessSource:
    """Blocks of 0.1 s for ever, a little apart, or until `fail_at` blocks, where `blocks()` raises."""

    def __init__(self, fail_at=None):
        self.position = 0
        self.reason = None
        self._fail_at = fail_at

    def blocks(self):
        while True:
            if self.position == (self._fail_at or -1) * BLOCK:
                raise OSError("the device went away")
            time.sleep(0.005)
            self.position += BLOCK
            yield np.zeros(BLOCK)


class Shutdown(unittest.TestCase):
    """After `run_threaded` raises, none of its threads is still running and `on_block` is not called again."""

    def check_shut_down(self, source, asr, diar, error):
        before = set(threading.enumerate())
        seen = []
        with self.assertRaisesRegex(type(error), str(error)):
            run(source, asr, diar, Sinks(on_block=seen.append))
        self.assertEqual([t for t in threading.enumerate() if t not in before], [])
        calls = len(seen)
        time.sleep(0.1)  # 20 of the source's blocks
        self.assertEqual(len(seen), calls)

    def test_when_a_worker_fails(self):
        def fail(n):
            if n == 2:
                raise RuntimeError("the ASR broke")

        self.check_shut_down(EndlessSource(), asr_stage(fail), diar_stage(), RuntimeError("the ASR broke"))

    def test_when_a_worker_dies_without_an_exception(self):
        def die(n):
            raise SystemExit("the ASR exited")

        self.check_shut_down(EndlessSource(), asr_stage(die), diar_stage(), SystemExit("the ASR exited"))

    def test_when_the_capture_fails(self):
        self.check_shut_down(EndlessSource(fail_at=5), asr_stage(), diar_stage(), OSError("the device went away"))


class Lockstep(unittest.TestCase):
    def test_run_lockstep_fills_stats_with_behind_0(self):
        source = GatedSource([4])
        asr, diar = asr_stage(), diar_stage()
        _, writer = writer_for(source)
        result = run_lockstep(source, asr, diar, writer, source_info=SOURCE, models=MODELS)
        self.assertEqual((result.stats.asr.count, result.stats.diarization.count), (5, 5))
        self.assertEqual(result.stats.behind, 0.0)


if __name__ == "__main__":
    unittest.main()
