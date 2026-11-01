"""Streaming ASR: the engine's stream, its pieces joined into words as they complete (design/internal/g4-stage1.md
§2, "Live ASR"). Positions are samples at 16 kHz; word times are seconds, rounded to the millisecond by the engine.
"""

from dataclasses import dataclass

import numpy

from lmt import audio, engines
from lmt.audio import SAMPLE_RATE
from lmt.transcript import Word


@dataclass(frozen=True)
class AsrUpdate:
    words: list[Word]  # completed now, in order, times absolute, rounded to ms
    done_until: int  # no later word can start before this sample
    # The pieces committed now, as the engine gave them: the join writes each as a `text` event at once, so a word
    # is shown when its last piece is out, not when the next word starts (G4's word delay, #577).
    pieces: list[engines.Piece] | None = None  # None: not given, and the join takes one piece per word


class StreamingASR:
    """A stage: `feed` passes each block to the stream, and returns the words its pieces complete."""

    def __init__(self, stream, gap: float, rate: int = SAMPLE_RATE):
        self._stream = stream
        self._words = engines.Words(gap)
        self.rate = rate  # the stream's input rate: the engine's own (Engine.rate)
        self._tap = None  # the source's audio at `rate`, once attached (#603)
        self._fed = 0  # 16 kHz samples handed to feed so far
        self._resampler = None if rate == SAMPLE_RATE else audio.Resampler(SAMPLE_RATE, 1, out_rate=rate)

    def attach(self, source) -> None:
        """Take the audio at the stream's rate from the source, straight from its input, instead of resampling the
        16 kHz blocks (#603). Call before capture starts. A source without `asr_rate` keeps the resampler."""
        if self.rate != SAMPLE_RATE and hasattr(source, "asr_rate"):
            source.asr_rate(self.rate)
            self._tap, self._resampler = source.tap, None

    def _audio(self, samples, *, last=False):
        """The audio for the next `samples` (16 kHz), at the stream's rate."""
        self._fed += len(samples)
        if self._tap is not None:
            return self._tap.take(None if last else round(self._fed * self.rate / SAMPLE_RATE))
        if self._resampler is not None:
            block = self._resampler.push(samples) if len(samples) else numpy.zeros(0, numpy.float32)
            return numpy.concatenate([block, self._resampler.flush()]) if last else block
        return samples

    def feed(self, samples) -> AsrUpdate:
        samples = numpy.asarray(samples, dtype=numpy.float32).reshape(-1)
        pieces = self._stream.feed(self._audio(samples))
        return self._update(self._words.add(pieces, self._stream.done_until), pieces)

    def flush(self) -> AsrUpdate:
        """The rest, once the source has stopped; then the stream is closed."""
        try:
            rest = self._audio(numpy.zeros(0, numpy.float32), last=True)
            pieces = (self._stream.feed(rest) if len(rest) else []) + self._stream.finish()
            words = self._words.add(pieces, self._stream.done_until) + self._words.finish()
        finally:
            self._stream.close()
        return self._update(words, pieces)

    def _update(self, words, pieces) -> AsrUpdate:
        start = self._words.open_start
        until = self._stream.done_until if start is None else start
        return AsrUpdate(words, max(0, round(until * SAMPLE_RATE)), list(pieces))
