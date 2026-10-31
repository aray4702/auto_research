"""The session (design/internal/g2-stage1.md §3a, item 3a3, and §3c, item 3c1): the source's blocks through the
stages, their updates through the join, and each output to the sinks and the event writer. `start` is written
first and `stop` last. `run_lockstep` takes each block through both stages before the next; `run_threaded` gives
the capture and each stage a thread of its own, so a slow stage catches up rather than drifting. Positions are
samples at 16 kHz.
"""

import queue
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from lmt.live import source as live_source
from lmt.live.join import Join, SegmentOut
from lmt.transcript import Segment, Speaker

RATE = 16000
BEHIND = 5 * RATE  # past this, stderr is warned once (3c1)
TICK = 0.1  # the main thread's wait on its queue, in seconds


@dataclass
class Sinks:
    on_block: Callable[[np.ndarray], None] = lambda b: None      # --save-audio (3b3)
    on_segment: Callable[[Segment, str], None] = lambda s, l: None  # the growing <stem>.txt (3b3)


NO_SINKS = Sinks()


@dataclass(frozen=True)
class StageStats:
    count: int  # every `feed` and `flush`
    mean: float  # seconds
    max: float


@dataclass(frozen=True)
class SessionStats:
    asr: StageStats
    diarization: StageStats
    behind: float  # the largest, in seconds


@dataclass(frozen=True)
class SessionResult:
    speakers: list[Speaker]
    segments: list[Segment]
    samples: int
    reason: str
    stats: SessionStats | None = field(default=None, compare=False)  # timings, which no two runs share


class _Tapped:
    """The source, with each block handed to `on_block` as it is taken, before the stages see it."""

    def __init__(self, source, on_block):
        self._source = source
        self._on_block = on_block

    @property
    def position(self) -> int:
        return self._source.position

    def blocks(self):
        for block in self._source.blocks():
            self._on_block(block)
            yield block


class _Timed:
    """A stage, with each `feed` and `flush` timed by `time.perf_counter`. Used by one thread at a time."""

    def __init__(self, stage):
        self._stage = stage
        self.seconds: list[float] = []

    def feed(self, samples):
        return self._timed(self._stage.feed, samples)

    def flush(self):
        return self._timed(self._stage.flush)

    def _timed(self, call, *args):
        t0 = time.perf_counter()
        try:
            return call(*args)
        finally:
            self.seconds.append(time.perf_counter() - t0)

    def stats(self) -> StageStats:
        s = self.seconds
        return StageStats(len(s), sum(s) / len(s) if s else 0.0, max(s, default=0.0))


class _Output:
    """The join, and its outputs to the sinks and the writer."""

    def __init__(self, writer, sinks):
        self.join = Join()
        self._writer = writer
        self._sinks = sinks

    def send(self, outs) -> None:
        # A closed segment goes to `on_segment` before its event, so a reader who has seen the event finds its
        # line in the growing text (G2-4).
        for out in outs:
            if isinstance(out, SegmentOut):
                labels = {s.id: s.label for s in self.join.speakers}
                self._sinks.on_segment(out.segment, labels[out.segment.speaker])
            self._writer.emit(out)

    def result(self, source, stats: SessionStats) -> SessionResult:
        return SessionResult(list(self.join.speakers), list(self.join.segments), source.position, source.reason,
                             stats)


def run_lockstep(source, asr, diarizer, writer, sinks=NO_SINKS, *, source_info: dict, models: dict) -> SessionResult:
    """`source_info` and `models` are `start`'s `source` and models' field, which the caller has. After each block:
    `sinks.on_block`, the two updates into the join, `poll`, and its outputs; at the end, the flushes, `finish`, the
    same again, and `stop`. `stats.behind` is 0."""
    output = _Output(writer, sinks)
    asr, diarizer = _Timed(asr), _Timed(diarizer)
    writer.start(source_info, models)
    for _, (words, activity) in live_source.lockstep(_Tapped(source, sinks.on_block), asr, diarizer):
        output.join.add_words(words)
        output.join.add_activity(activity)
        output.send(output.join.poll())
    output.send(output.join.finish())
    writer.stop(source.reason)
    return output.result(source, SessionStats(asr.stats(), diarizer.stats(), 0.0))


_STOP = object()  # the capture's last item on each stage's queue
JOIN = 5.0  # seconds: how long `run_threaded` waits for each of its threads at the end


def _capture(source, sinks, inboxes, main, stop) -> None:
    """Takes the source's blocks to `on_block` and both queues until the source ends or `stop` is set. The stop
    marker goes on both queues however it ends, so neither worker waits for ever."""
    try:
        for block in source.blocks():
            if stop.is_set():
                break
            sinks.on_block(block)
            for inbox in inboxes:
                inbox.put(block)
    except BaseException as e:  # noqa: BLE001 -- the main thread raises it (design §3c)
        main.put(e)
    finally:
        for inbox in inboxes:
            inbox.put(_STOP)


def _work(name, stage, inbox, main, stop) -> None:
    """Feeds every block that has queued up in one `feed`; at the stop marker, flushes. Puts `(name, update,
    samples processed, last)` on the main queue, or the exception that ended it. Once `stop` is set it feeds
    nothing more."""
    try:
        processed = 0
        done = False
        while not done:
            items = [inbox.get()]
            while True:
                try:
                    items.append(inbox.get_nowait())
                except queue.Empty:
                    break
            if stop.is_set():
                return
            done = items[-1] is _STOP
            blocks = items[:-1] if done else items
            if blocks:
                samples = np.concatenate(blocks)
                update = stage.feed(samples)
                processed += len(samples)
                main.put((name, update, processed, False))
        main.put((name, stage.flush(), processed, True))
    except BaseException as e:  # noqa: BLE001 -- the main thread raises it (design §3c)
        main.put(e)


class _Behind:
    """How far the slower stage is behind the source: warns once each time it rises past `BEHIND`."""

    def __init__(self):
        self.largest = 0
        self._warned = False

    def see(self, behind: int) -> None:
        self.largest = max(self.largest, behind)
        if behind > BEHIND and not self._warned:
            print(f"lmt: warning: live: {behind / RATE:.1f} s behind real time", file=sys.stderr, flush=True)
            self._warned = True
        elif behind < BEHIND:
            self._warned = False


def run_threaded(source, asr, diarizer, writer, sinks=NO_SINKS, *, source_info: dict, models: dict) -> SessionResult:
    """As `run_lockstep`, with a capture thread, a worker thread per stage, and this thread taking their updates
    through the join to the outputs. A worker's or the capture's exception is raised here."""
    output = _Output(writer, sinks)
    stages = {"asr": _Timed(asr), "diarization": _Timed(diarizer)}
    inboxes = {name: queue.Queue() for name in stages}
    main = queue.Queue()
    stop = threading.Event()
    writer.start(source_info, models)
    threads = [threading.Thread(target=_capture, args=(source, sinks, list(inboxes.values()), main, stop),
                                daemon=True)]
    threads += [threading.Thread(target=_work, args=(name, stage, inboxes[name], main, stop), daemon=True)
                for name, stage in stages.items()]
    try:
        for thread in threads:
            thread.start()
        processed = dict.fromkeys(stages, 0)
        running = set(stages)
        behind = _Behind()
        while running:
            try:
                item = main.get(timeout=TICK)
            except queue.Empty:
                item = None
            if isinstance(item, BaseException):
                raise item
            if item is not None:
                name, update, processed[name], last = item
                if name == "asr":
                    output.join.add_words(update)
                else:
                    output.join.add_activity(update)
                output.send(output.join.poll())
                if last:
                    running.discard(name)
            behind.see(source.position - min(processed.values()))
    finally:
        # On any way out, the capture stops taking blocks (and calling `on_block`) and every thread is joined, so
        # none outlives this call to race the caller's clean-up.
        stop.set()
        for thread in threads:
            if thread.is_alive():
                thread.join(JOIN)
    output.send(output.join.finish())
    writer.stop(source.reason)
    return output.result(source, SessionStats(stages["asr"].stats(), stages["diarization"].stats(),
                                              behind.largest / RATE))
