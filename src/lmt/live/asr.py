"""Streaming ASR: the engine's stream, its pieces joined into words as they complete (design/internal/g4-stage1.md
§2, "Live ASR"). Positions are samples at 16 kHz; word times are seconds, rounded to the millisecond by the engine.
"""

from dataclasses import dataclass

from lmt import engines
from lmt.audio import SAMPLE_RATE
from lmt.transcript import Word


@dataclass(frozen=True)
class AsrUpdate:
    words: list[Word]  # completed now, in order, times absolute, rounded to ms
    done_until: int  # no later word can start before this sample


class StreamingASR:
    """A stage: `feed` passes each block to the stream, and returns the words its pieces complete."""

    def __init__(self, stream, gap: float):
        self._stream = stream
        self._words = engines.Words(gap)

    def feed(self, samples) -> AsrUpdate:
        pieces = self._stream.feed(samples)
        return self._update(self._words.add(pieces, self._stream.done_until))

    def flush(self) -> AsrUpdate:
        """The rest, once the source has stopped; then the stream is closed."""
        try:
            words = self._words.add(self._stream.finish(), self._stream.done_until) + self._words.finish()
        finally:
            self._stream.close()
        return self._update(words)

    def _update(self, words) -> AsrUpdate:
        start = self._words.open_start
        until = self._stream.done_until if start is None else start
        return AsrUpdate(words, max(0, round(until * SAMPLE_RATE)))
