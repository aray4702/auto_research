"""The join (design/internal/g2-stage1.md §3a, item 3a1): committed words and final speaker activity into labelled
words and segments, by alignment's words-first rules (P12 leaves them to the live join). A word is labelled once
final activity reaches its end, looking only back from it; a segment closes on a speaker change, on `align.breaks`,
at once after a sentence end, once ASR is done 1 s past it, and at `finish`. Positions are samples at 16 kHz; word
times are seconds.

Words before labels (design/internal/g4-stage1.md §4): each committed piece goes out as a `TextOut` in the `poll`
it arrives in, before any label. An update without `pieces` (ASR that commits whole words) gives one piece per word.
"""

from dataclasses import dataclass

from lmt import align
from lmt.live.asr import AsrUpdate
from lmt.live.diarize import DiarUpdate
from lmt.transcript import Segment, Speaker, Turn, Word

RATE = 16000
KEEP = 2.0  # s: turns that ended this long before the oldest unlabelled word are dropped
NO_SPEECH = -1  # the internal speaker of words labelled at `finish` when diarization found no speech at all

try:  # the engine interface (design/internal/g4-stage1.md §1, item 3)
    from lmt.engines import Piece
except ImportError:  # until it merges, the same shape
    @dataclass(frozen=True)
    class Piece:
        text: str  # starts with " " when it starts a word
        start: float
        end: float
        starts_word: bool
        probability: float


@dataclass(frozen=True)
class TextOut:       # → a "text" event
    piece: Piece


@dataclass(frozen=True)
class WordsOut:      # → a "words" event
    segment: int
    speaker: str
    label: str
    words: list[Word]


@dataclass(frozen=True)
class SegmentOut:    # → a "segment" event
    segment: Segment
    number: int


def _ms(t: float) -> int:
    return round(t * 1000)


class Join:
    """`add_words` and `add_activity` take the stages' updates; `poll` labels what it can and returns the outputs
    since the last call. `speakers` and `segments` (the closed ones) are the transcript so far."""

    def __init__(self):
        self.speakers: list[Speaker] = []
        self.segments: list[Segment] = []
        self._pending: list[Word] = []         # committed, not yet labelled, in order
        self._done_until = 0                   # samples: no later word starts before this
        self._turns: list[list[int]] = []      # final activity, [start, end, speaker] in samples, by start
        self._last: dict[int, list[int]] = {}  # speaker -> its latest turn, which a touching piece extends
        self._first: int | None = None         # the first final turn's speaker
        self._final_until = 0                  # samples
        self._finished = False                 # at `finish`, final activity reaches the end
        self._previous: int | None = None      # the last labelled word's internal speaker
        self._ids: dict[int, str] = {}         # internal speaker -> id, by first labelled word
        self._open: list[Word] = []            # the open segment's words
        self._speaker = ""                     # its id
        self._out: list[WordsOut | SegmentOut] = []
        self._text: list[TextOut] = []         # committed pieces since the last poll

    def add_words(self, update: AsrUpdate) -> None:
        pieces = getattr(update, "pieces", None)
        if pieces is None:
            pieces = [Piece(" " + w.text, w.start, w.end, True, w.probability) for w in update.words]
        self._text.extend(TextOut(p) for p in pieces)
        self._pending.extend(update.words)
        self._done_until = max(self._done_until, update.done_until)

    def add_activity(self, update: DiarUpdate) -> None:
        for p in update.pieces:
            if self._first is None:
                self._first = p.speaker
            last = self._last.get(p.speaker)
            if last is not None and last[1] == p.start:
                last[1] = max(last[1], p.end)
            else:
                self._last[p.speaker] = [p.start, p.end, p.speaker]
                self._turns.append(self._last[p.speaker])
        self._final_until = max(self._final_until, update.final_until)

    def poll(self) -> list[TextOut | WordsOut | SegmentOut]:
        text, self._text = self._text, []
        while self._pending and self._label(self._pending[0]):
            self._pending.pop(0)
        if (self._open and not self._pending
                and _ms(self._done_until / RATE) - _ms(self._open[-1].end) >= _ms(align.PAUSE)):
            self._close()
        self._drop()
        out, self._out = self._out, []
        return text + out

    def finish(self) -> list[TextOut | WordsOut | SegmentOut]:
        self._finished = True
        out = self.poll()
        if self._open:
            self._close()
        out, self._out = out + self._out, []
        return out

    def _label(self, word: Word) -> bool:
        """Labels `word` if it can be now: final activity reaches its end, and a speaker is found."""
        end = round(word.end * RATE)
        if not self._finished and end > self._final_until:
            return False
        turns = [Turn(s / RATE, min(e, end) / RATE, g) for s, e, g in self._turns if s < end]
        raw = align.assign_speaker(word, turns, self._previous)
        if raw is None:
            raw = self._first
        if raw is None:
            if not self._finished:
                return False
            raw = NO_SPEECH
        self._previous = raw
        if raw not in self._ids:
            self._ids[raw] = f"S{len(self._ids) + 1}"
            self.speakers.append(Speaker(self._ids[raw], f"Speaker {len(self._ids)}"))
        speaker = self._ids[raw]
        if self._open and (speaker != self._speaker or align.breaks(self._open[-1], word, self._open[0].start)):
            self._close()
        if not self._open:
            self._speaker = speaker
        self._open.append(word)
        number = len(self.segments) + 1
        if self._out and isinstance(self._out[-1], WordsOut) and self._out[-1].segment == number:
            run = self._out.pop()
            self._out.append(WordsOut(number, speaker, run.label, run.words + [word]))
        else:
            self._out.append(WordsOut(number, speaker, f"Speaker {speaker[1:]}", [word]))
        if align.ends_sentence(word):
            self._close()
        return True

    def _close(self) -> None:
        words = tuple(self._open)
        segment = Segment(words[0].start, max(w.end for w in words), self._speaker, align.segment_text(words), words)
        self.segments.append(segment)
        self._out.append(SegmentOut(segment, len(self.segments)))
        self._open = []

    def _drop(self) -> None:
        """Turns that ended more than KEEP before the oldest unlabelled word (or, with none, before `done_until`,
        where the next word starts at the earliest) and that no later piece can touch."""
        oldest = round(self._pending[0].start * RATE) if self._pending else self._done_until
        cut = oldest - round(KEEP * RATE)
        self._turns = [t for t in self._turns if not (t[1] < cut and t[1] < self._final_until)]
