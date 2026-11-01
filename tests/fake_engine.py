"""A stand-in speech engine (design/internal/g4-stage1.md §1, "Stand-ins"): scripted pieces, given out by audio time.

`FakeEngine(pieces)`'s stream returns, from each `feed`, the pieces that end by the audio fed so far, and the rest
from `finish`; its `done_until` is the audio fed so far. Its `transcribe` is `engines.words` of every piece. Tests
put it in place of `engines.load` with `install`.
"""

from lmt import engines
from lmt.audio import SAMPLE_RATE
from lmt.engines import Piece

INFO = {"name": "stt-1b-en_fr", "engine": "kyutai", "repository": "kyutai/stt-1b-en_fr-mlx",
        "revision": "2b995724eef1e964b7ccb6a762b35a665c4abe0d"}


def pieces_of(words, probability=0.9) -> list[Piece]:
    """One piece per `(start, end, text)`, each starting a word."""
    return [Piece(" " + text, start, end, True, probability) for start, end, text in words]


class FakeStream(engines.Stream):
    def __init__(self, engine):
        self._engine = engine
        self._pending = list(engine.pieces)
        self._fed = 0
        self.done_until = 0.0
        self.closed = 0

    def feed(self, block) -> list[Piece]:
        self._fed += len(block)
        self.done_until = self._fed / SAMPLE_RATE
        self._engine.fed.append(len(block))
        out = [p for p in self._pending if p.end <= self.done_until]
        self._pending = self._pending[len(out):]
        return out

    def finish(self) -> list[Piece]:
        out, self._pending = self._pending, []
        self._engine.finished += 1
        return out

    def close(self) -> None:
        self.closed += 1


class FakeEngine(engines.Engine):
    def __init__(self, pieces=(), name="kyutai", word_gap=0.24, info=None):
        self.pieces = list(pieces)
        self.name = name
        self.word_gap = word_gap
        self.info = dict(info or {**INFO, "engine": name})
        self.transcribed = []  # each `transcribe`'s audio
        self.fed = []  # each `feed`'s length, in samples
        self.finished = 0
        self.streams = []

    def transcribe(self, audio):
        self.transcribed.append(audio)
        return engines.words(self.pieces)

    def stream(self) -> FakeStream:
        stream = FakeStream(self)
        self.streams.append(stream)
        return stream


def install(monkeypatch, engine: FakeEngine, loads=None):
    """`engines.load` gives `engine`; each call's `(name, offline)` is appended to `loads` if it is given."""

    def load(name, *, offline):
        if loads is not None:
            loads.append((name, offline))
        return engine

    monkeypatch.setattr(engines, "load", load)
    return engine
