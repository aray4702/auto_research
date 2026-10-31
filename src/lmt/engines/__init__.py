"""The speech engines behind one interface (design/internal/g4-stage1.md §1; design/architecture.md, "Speech
engines (G4)"). Both take decode's 16 kHz mono float32 and give pieces of text with times; `Words` joins pieces
into `transcript.Word`s by the spec's rules (G4 "Terms"), the same way in batch and live mode.

`load` imports `lmt.engines.<name>` only when that engine is chosen, so this module stays light: `lmt --help`
imports it for `--engine`'s choices.
"""

import importlib
from dataclasses import dataclass

from lmt.errors import ModelError
from lmt.transcript import Word

ENGINES = ("kyutai", "apple")  # the order `--engine`'s choices and `models list` use


@dataclass(frozen=True)
class Piece:
    """One committed unit of text."""

    text: str  # starts with " " when it starts a word (the `text` event's `text`)
    start: float  # seconds from the start of the audio, rounded to the ms
    end: float
    starts_word: bool
    probability: float


class Stream:
    """One live session. `feed` takes 16 kHz mono float32 blocks and returns the pieces committed, in order,
    once each; `finish` returns the rest once the source has stopped. Every piece of the audio before
    `done_until` (seconds) is out. `close` frees the model state or stops the helper; it is safe to call twice."""

    done_until: float = 0.0

    def feed(self, block) -> list[Piece]:
        raise NotImplementedError

    def finish(self) -> list[Piece]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class Engine:
    name: str  # "kyutai" | "apple"
    info: dict  # the transcript's models.asr: name, engine, repository, revision
    word_gap: float  # seconds after a word's end with no new piece that complete it (G4 "Terms")
    peak_rss_bytes: int = 0  # a helper process's peak RSS (Apple), else 0

    def transcribe(self, audio) -> list[Word]:
        raise NotImplementedError

    def stream(self) -> Stream:
        raise NotImplementedError


def module(name: str):
    """lmt.engines.kyutai or lmt.engines.apple, imported now."""
    if name not in ENGINES:
        raise ValueError(f"no engine {name!r}")
    return importlib.import_module(f"lmt.engines.{name}")


def _module_or_error(name: str):
    try:
        return module(name)
    except ModuleNotFoundError as e:
        if e.name != f"lmt.engines.{name}":
            raise
        raise ModelError(f"--engine {name}", "not in this build") from None


def load(name: str, *, offline: bool) -> Engine:
    """The engine, ready to run. Raises ModelError (exit 5) if it can't run here."""
    return _module_or_error(name).load(offline=offline)


def download(name: str) -> None:
    """What `lmt models download --engine NAME` does for the engine itself."""
    _module_or_error(name).download()


def status(name: str) -> dict:
    """`name`, `status` (a short phrase) and `ok`, for `lmt models list`."""
    try:
        return _module_or_error(name).status()
    except ModelError as e:
        return {"name": name, "status": e.problem, "ok": False}


class Words:
    """Pieces to words, as they complete. A word is a piece that starts a word and the pieces after it up to the
    next such piece (a first piece that doesn't start a word starts one too). The open word is complete when a
    piece starting a word arrives, when `done_until` reaches its end plus `gap`, or at `finish`."""

    def __init__(self, gap: float):
        self.gap = gap
        self._open: list[Piece] = []

    @property
    def open_start(self) -> float | None:
        """The open word's start, or None if no word is open."""
        return self._open[0].start if self._open else None

    def add(self, pieces, done_until: float) -> list[Word]:
        words = []
        for piece in pieces:
            if piece.starts_word and self._open:
                words.append(self._close())
            self._open.append(piece)
        if self._open and round(done_until, 3) >= round(self._open[-1].end + self.gap, 3):
            words.append(self._close())
        return [w for w in words if w is not None]

    def finish(self) -> list[Word]:
        word = self._close() if self._open else None
        return [word] if word is not None else []

    def _close(self) -> Word | None:
        pieces, self._open = self._open, []
        text = "".join(p.text for p in pieces).strip()
        if not text:
            return None
        return Word(pieces[0].start, pieces[-1].end, text, min(p.probability for p in pieces))


def words(pieces) -> list[Word]:
    """Batch mode: every word in `pieces`."""
    joined = Words(0.0)
    return joined.add(pieces, float("-inf")) + joined.finish()
