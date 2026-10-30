"""Streaming ASR: LocalAgreement-2 over a rolling buffer (design/internal/g2-stage1.md §1). Positions are
samples at 16 kHz; word times are seconds, rounded to the millisecond once, when the word is committed.
"""

import itertools
import unicodedata
from dataclasses import dataclass

import numpy

from lmt.audio import SAMPLE_RATE
from lmt.transcript import Word

FLUSH_MIN = 1600  # one block (source.BLOCK): flush transcribes what's left only if it is at least this long
NGRAM_MAX = 5  # the n-gram check's longest run of words
NGRAM_WINDOW = 1.0  # s: the run's first word starts within this of the buffer's start
OVERFLOW_MARGIN = 2.0  # s: overflow commits words that end at least this long before the buffer's end
MIN_DURATION = 0.010  # s: a committed word's shortest length
PAUSE_MARGIN = 3200  # samples (0.2 s): a word may reach this far into a gap, and the cut keeps this much lead-in


@dataclass(frozen=True)
class AsrConfig:
    update: int = 16000  # U, samples (1.0 s, provisional; stage 2 sweeps 0.5 and 1.0 s)
    max_buffer: int = 240000  # 15 s
    pause: int = 8000  # 0.5 s of non-speech commits the buffer (1c)
    prompt_chars: int = 200


DEFAULT_ASR_CONFIG = AsrConfig()


@dataclass(frozen=True)
class AsrUpdate:
    words: list[Word]  # committed now, in order, times absolute, rounded to ms
    done_until: int  # no later word can start before this sample


def key(text: str) -> str:
    """The text compared for agreement: casefolded, with leading and trailing punctuation (Unicode category
    P) and white space removed, so a flicker in punctuation or case doesn't hold back a commit."""
    def strip(c):
        return c.isspace() or unicodedata.category(c).startswith("P")

    start, end = 0, len(text)
    while start < end and strip(text[start]):
        start += 1
    while end > start and strip(text[end - 1]):
        end -= 1
    return text[start:end].casefold()


_quiet = False  # whether vad_speech has quieted onnxruntime's log yet


def vad_speech(buffer) -> list[tuple[int, int]]:
    """Silero VAD's speech chunks in `buffer`, as (start, end) in samples from its start. It runs through
    `lmt.asr`, imported first, so P11's quiet log covers it."""
    global _quiet
    from lmt import asr

    if not _quiet:
        asr.quiet_onnxruntime()
        _quiet = True
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    chunks = get_speech_timestamps(numpy.asarray(buffer, dtype=numpy.float32),
                                   VadOptions(min_silence_duration_ms=500, speech_pad_ms=0))
    return [(int(c["start"]), int(c["end"])) for c in chunks]


class StreamingASR:
    """A stage: `feed` runs at most one update per call, once the position reaches the next multiple of U."""

    def __init__(self, engine, config=DEFAULT_ASR_CONFIG, *, speech=vad_speech):
        self._engine = engine
        self._config = config
        self._speech = speech
        self._buffer = numpy.zeros(0, dtype=numpy.float32)
        self._buffer_start = 0
        self._position = 0
        self._next = config.update
        self._committed: list[Word] = []  # the latest committed words: enough for the prompt and the n-gram check
        self._previous: list[Word] = []  # the uncommitted tail of the last update's hypothesis

    def feed(self, samples) -> AsrUpdate:
        samples = numpy.asarray(samples, dtype=numpy.float32)
        self._buffer = numpy.concatenate([self._buffer, samples])
        self._position += len(samples)
        if self._position < self._next:
            return AsrUpdate([], self._buffer_start)
        words = self._update()
        self._next = (self._position // self._config.update + 1) * self._config.update
        return AsrUpdate(words, self._buffer_start)

    def flush(self) -> AsrUpdate:
        words = []
        if len(self._buffer) >= FLUSH_MIN:
            words = self._commit(self._hypothesis())
        self._cut(self._position)
        self._previous = []
        return AsrUpdate(words, self._position)

    def _update(self) -> list[Word]:
        hyp = self._hypothesis()
        # 3. Agreement: the longest common prefix of `previous` and `hyp`, by key.
        n = 0
        while n < min(len(self._previous), len(hyp)) and key(self._previous[n].text) == key(hyp[n].text):
            n += 1
        # 4. Pause: P, the words that end by the last gap's start plus 0.2 s, if no word straddles the gap.
        gap = self._gap(hyp)
        p = 0
        if gap is not None:
            while p < len(hyp) and hyp[p].end <= (gap[0] + PAUSE_MARGIN) / SAMPLE_RATE:
                p += 1
        # 5. Commit.
        words = self._commit(hyp[:max(n, p)])
        if gap is not None and p >= n:
            self._cut(gap[1] - PAUSE_MARGIN)
        elif words:
            self._cut_after(words[-1])
        self._previous = hyp[max(n, p):]
        # 6. Overflow.
        if len(self._buffer) > self._config.max_buffer:
            end = self._position / SAMPLE_RATE - OVERFLOW_MARGIN
            m = 0
            while m < len(self._previous) and self._previous[m].end <= end:
                m += 1
            if m:
                more = self._commit(self._previous[:m])
                self._cut_after(more[-1])
                self._previous = self._previous[m:]
                words += more
            else:
                self._cut(self._position - self._config.max_buffer)
        # 7. done_until is buffer_start after the cut, which feed returns.
        return words

    def _gap(self, hyp: list[Word]) -> tuple[int, int] | None:
        """The buffer's last stretch of at least `pause` samples with no speech, as absolute (start, end)
        samples: between two speech chunks, or from the last one to the buffer's end, or the whole buffer if
        it has none. None if there is no such stretch, or a word of `hyp` straddles it: one that starts
        before its end less 0.2 s and ends after its start plus 0.2 s."""
        chunks = self._speech(self._buffer)
        if chunks:
            stretches = [(a[1], b[0]) for a, b in itertools.pairwise(chunks)]
            stretches.append((chunks[-1][1], len(self._buffer)))
        else:
            stretches = [(0, len(self._buffer))]
        gaps = [(start, end) for start, end in stretches if end - start >= self._config.pause]
        if not gaps:
            return None
        start, end = (self._buffer_start + s for s in gaps[-1])
        for w in hyp:
            if w.start < (end - PAUSE_MARGIN) / SAMPLE_RATE and w.end > (start + PAUSE_MARGIN) / SAMPLE_RATE:
                return None
        return start, end

    def _hypothesis(self) -> list[Word]:
        """Steps 1 and 2: the buffer transcribed, in absolute times, without what's already committed."""
        offset = self._buffer_start / SAMPLE_RATE
        buffer_end = self._position / SAMPLE_RATE
        hyp = [Word(w.start + offset, min(w.end + offset, buffer_end), w.text, w.probability)
               for w in self._engine.transcribe(self._buffer, initial_prompt=self._prompt())]
        if not self._committed:
            return hyp
        last_end = self._committed[-1].end
        hyp = [w for w in hyp if w.end > last_end]
        # whisper_streaming's n-gram check, for a word cut in two at the buffer's start.
        if hyp and hyp[0].start - offset <= NGRAM_WINDOW:
            for k in range(min(NGRAM_MAX, len(hyp), len(self._committed)), 0, -1):
                if [key(w.text) for w in hyp[:k]] == [key(w.text) for w in self._committed[-k:]]:
                    return hyp[k:]
        return hyp

    def _prompt(self) -> str | None:
        """The committed words' text, joined by spaces, cut to its last `prompt_chars` characters at a word
        boundary; None before the first commit."""
        texts, length = [], -1
        for w in reversed(self._committed):
            length += len(w.text) + 1
            if length > self._config.prompt_chars:
                break
            texts.append(w.text)
        return " ".join(reversed(texts)) or None

    def _commit(self, hyp: list[Word]) -> list[Word]:
        """Commits `hyp`, its times clamped so committed words never overlap, then rounded to the
        millisecond."""
        words = []
        previous_end = self._committed[-1].end if self._committed else 0.0
        for w in hyp:
            start = round(max(w.start, previous_end), 3)
            end = round(max(w.end, start + MIN_DURATION), 3)
            words.append(Word(start, end, w.text, w.probability))
            previous_end = end
        keep = max(NGRAM_MAX, self._config.prompt_chars)  # every word has at least one character
        self._committed = (self._committed + words)[-keep:]
        return words

    def _cut_after(self, word: Word) -> None:
        self._cut(round(word.end * SAMPLE_RATE))

    def _cut(self, sample: int) -> None:
        """Drops the buffer's audio before `sample`, kept within the buffer."""
        sample = min(max(sample, self._buffer_start), self._position)
        self._buffer = self._buffer[sample - self._buffer_start:]
        self._buffer_start = sample
