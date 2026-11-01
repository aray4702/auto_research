"""The file source and lockstep (design/internal/g2-stage1.md §1). `FileSource` decodes a file up front and
hands it over in blocks of 0.1 s, paced to the clock or as fast as they're taken. `lockstep` feeds each block to
every stage before taking the next, single-threaded, so its output doesn't depend on the machine's speed.
Positions are samples at 16 kHz.
"""

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from lmt import audio
from lmt.audio import SAMPLE_RATE

BLOCK = 1600


class AsrTap:
    """The source's audio at the ASR engine's own rate, taken straight from the input, not resampled from the 16 kHz
    blocks (#603): `put` appends what the source produces, `take(until)` hands it out in order up to `until`
    samples at that rate (None: all of it, at the end), and drops what's been taken, so memory stays bounded."""

    def __init__(self, rate: int):
        self.rate = rate
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._have = 0  # samples put so far
        self._taken = 0  # samples handed out so far

    def put(self, samples) -> None:
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if samples.size:
            with self._lock:
                self._chunks.append(samples)
                self._have += samples.size

    def take(self, until: int | None) -> np.ndarray:
        with self._lock:
            end = self._have if until is None else min(until, self._have)
            n = max(0, end - self._taken)
            if n == 0:
                return np.zeros(0, np.float32)
            joined = np.concatenate(self._chunks)
            out, rest = joined[:n], joined[n:]
            self._chunks = [rest] if rest.size else []
            self._taken += n
            return out


class FileSource:
    def __init__(self, path, *, paced: bool, limit: int | None = None, clock=time.monotonic, sleep=time.sleep):
        self._audio = audio.decode(path)  # an InputError (3) before capture starts
        self._path = path
        self.tap: AsrTap | None = None
        self.name = Path(path).name
        self.position = 0  # samples handed over so far; read from any thread
        self.reason = None  # "end-of-input", "duration" or "signal", once it stops
        self._paced = paced
        self._limit = limit
        self._clock = clock
        self._sleep = sleep
        self._stopped = threading.Event()

    def asr_rate(self, rate: int) -> None:
        """Also keep the file at `rate`, decoded straight from it, for the ASR engine (#603)."""
        self._hi = audio.decode(self._path, rate)
        self.tap = AsrTap(rate)

    def blocks(self) -> Iterator[np.ndarray]:
        """Blocks of `BLOCK` samples, the last one shorter if the file or `limit` ends mid-block. Paced, block i
        is handed over at t0 + (i + 1) × 0.1 s, t0 being the clock when this is first iterated; a late block goes
        at once."""
        n = len(self._audio)
        end = n if self._limit is None else min(self._limit, n)
        t0 = self._clock() if self._paced else None
        i = 0
        while True:
            if self._stopped.is_set():
                self.reason = "signal"
                return
            if self.position >= end:
                self.reason = "duration" if self._limit is not None and self._limit <= n else "end-of-input"
                return
            if self._paced:
                wait = t0 + (i + 1) * BLOCK / SAMPLE_RATE - self._clock()
                if wait > 0:
                    self._sleep(wait)
                if self._stopped.is_set():
                    self.reason = "signal"
                    return
            start = self.position
            self.position = min(start + BLOCK, end)
            if self.tap is not None:  # the same span at the engine's rate; the last block takes the rest
                hi_start = round(start * self.tap.rate / SAMPLE_RATE)
                hi_end = len(self._hi) if self.position >= n else round(self.position * self.tap.rate / SAMPLE_RATE)
                self.tap.put(self._hi[hi_start:hi_end])
            yield self._audio[start:self.position]
            i += 1

    def stop(self) -> None:
        """Thread-safe: the next block isn't handed over."""
        self._stopped.set()


def lockstep(source, *stages) -> Iterator[tuple[int, list]]:
    """For each block, every stage's `feed` returns before the next block is taken: yields `(source.position,
    [each stage's update])`. After the last block, `(position, [each stage's flush()])`."""
    for block in source.blocks():
        yield source.position, [stage.feed(block) for stage in stages]
    yield source.position, [stage.flush() for stage in stages]
