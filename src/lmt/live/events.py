"""The live events (design/internal/g2-stage1.md §3a, item 3a2): the join's outputs to stdout, as live events 1.1
in JSONL (design/public-api.md, "Live events, version 1.1" and "G4's changes to the commands"; g4-stage1.md §4)
or as text lines that grow word by word, as they're labelled. Every write is flushed, so a reader through a pipe
gets each one at once. `clock_t` gives the session's time in samples at 16 kHz.
"""

import json
from collections.abc import Callable

from lmt.export.cues import ms
from lmt.live.join import SegmentOut, TextOut, WordsOut

RATE = 16000
SCHEMA_VERSION = "1.1"


def _stamp(start: float) -> str:
    """`[HH:MM:SS]`, the segment's start rounded down to the second, as the text export writes it."""
    minutes, seconds = divmod(ms(start) // 1000, 60)
    hours, minutes = divmod(minutes, 60)
    return f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"


class EventWriter:
    def __init__(self, stream, *, jsonl: bool, clock_t: Callable[[], int]):
        self._stream = stream
        self._jsonl = jsonl
        self._clock_t = clock_t
        self._line = 0  # the segment whose text line is open, or 0

    def start(self, source: dict, models: dict) -> None:
        """`source` is the transcript's, without `duration`."""
        self._event({"type": "start", "schema_version": SCHEMA_VERSION, "t": 0, "source": source, "models": models})

    def emit(self, out: TextOut | WordsOut | SegmentOut) -> None:
        if isinstance(out, TextOut):
            if not self._jsonl:
                return  # text lines come as words are labelled, as in 1.0
            p = out.piece
            self._event({"type": "text", "t": self._t(), "text": p.text, "start": round(p.start, 3),
                         "end": round(p.end, 3), "starts_word": p.starts_word})
        elif isinstance(out, WordsOut):
            if self._jsonl:
                self._event({
                    "type": "words", "t": self._t(), "segment": out.segment, "speaker": out.speaker,
                    "label": out.label,
                    "words": [{"start": w.start, "end": w.end, "text": w.text, "probability": w.probability}
                              for w in out.words],
                })
            else:
                text = " ".join(w.text for w in out.words)
                if self._line == out.segment:
                    self._write(" " + text)
                else:
                    self._line = out.segment
                    self._write(f"{_stamp(out.words[0].start)} {out.label}: {text}")
        elif self._jsonl:
            s = out.segment
            self._event({"type": "segment", "t": self._t(), "segment": out.number, "start": s.start, "end": s.end,
                         "speaker": s.speaker, "text": s.text})
        else:
            self._line = 0
            self._write("\n")

    def stop(self, reason: str) -> None:
        """Written once the captured audio is finished and every word emitted, so it is the last event."""
        self._event({"type": "stop", "t": self._t(), "reason": reason})

    def _t(self) -> float:
        return round(self._clock_t() / RATE, 3)

    def _event(self, event: dict) -> None:
        if self._jsonl:
            self._write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _write(self, text: str) -> None:
        self._stream.write(text)
        self._stream.flush()
