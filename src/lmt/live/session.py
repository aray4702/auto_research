"""The lockstep session (design/internal/g2-stage1.md §3a, item 3a3): the source's blocks through the stages in
lockstep, their updates through the join, and each output to the sinks and the event writer. `start` is written
first and `stop` last. Positions are samples at 16 kHz.
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from lmt.live import source as live_source
from lmt.live.join import Join, SegmentOut
from lmt.transcript import Segment, Speaker


@dataclass
class Sinks:
    on_block: Callable[[np.ndarray], None] = lambda b: None      # --save-audio (3b3)
    on_segment: Callable[[Segment, str], None] = lambda s, l: None  # the growing <stem>.txt (3b3)


NO_SINKS = Sinks()


@dataclass(frozen=True)
class SessionResult:
    speakers: list[Speaker]
    segments: list[Segment]
    samples: int
    reason: str


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


def run_lockstep(source, asr, diarizer, writer, sinks=NO_SINKS, *, source_info: dict, models: dict) -> SessionResult:
    """`source_info` and `models` are `start`'s `source` and models' field, which the caller has. After each block:
    `sinks.on_block`, the two updates into the join, `poll`, and its outputs; at the end, the flushes, `finish`, the
    same again, and `stop`."""
    join = Join()

    def send(outs):
        # A closed segment goes to `on_segment` before its event, so a reader who has seen the event finds its
        # line in the growing text (G2-4).
        for out in outs:
            if isinstance(out, SegmentOut):
                labels = {s.id: s.label for s in join.speakers}
                sinks.on_segment(out.segment, labels[out.segment.speaker])
            writer.emit(out)

    writer.start(source_info, models)
    for _, (words, activity) in live_source.lockstep(_Tapped(source, sinks.on_block), asr, diarizer):
        join.add_words(words)
        join.add_activity(activity)
        send(join.poll())
    send(join.finish())
    writer.stop(source.reason)
    return SessionResult(list(join.speakers), list(join.segments), source.position, source.reason)
