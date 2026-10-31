"""The lockstep session, on fake stages and no model (design/internal/g2-stage1.md §3a, item 3a3)."""

import io
import json
import unittest

import numpy as np

from lmt import align
from lmt.live.asr import AsrUpdate
from lmt.live.diarize import DiarUpdate, Piece
from lmt.live.events import EventWriter
from lmt.live.session import SessionResult, Sinks, run_lockstep
from lmt.transcript import Segment, Speaker, Word

RATE = 16000
BLOCK = 1600
SOURCE = {"file": "clip.wav", "live": True}
MODELS = {"asr": {"name": "fake"}, "diarization": {"name": "fake"}}


def word(start, end, text):
    return Word(start, end, text, 0.9)


class FakeSource:
    """`blocks` blocks of 0.1 s, each block's samples its index; logs each block as it is taken."""

    def __init__(self, log, blocks=4):
        self.log = log
        self.position = 0
        self.reason = None
        self._blocks = blocks

    def blocks(self):
        for i in range(self._blocks):
            self.log.append(("take", i))
            self.position += BLOCK
            yield np.full(BLOCK, float(i))
        self.reason = "end-of-input"


class FakeStage:
    """Returns its scripted updates, one per `feed`, then `flushed`; logs each call with the block's index."""

    def __init__(self, name, log, updates, flushed):
        self.name = name
        self.log = log
        self._updates = list(updates)
        self._flushed = flushed

    def feed(self, samples):
        self.log.append((self.name, int(samples[0])))
        return self._updates.pop(0)

    def flush(self):
        self.log.append((self.name, "flush"))
        return self._flushed


class LoggingWriter(EventWriter):
    """An `EventWriter` into a `StringIO` that also logs each event's type as it is written."""

    def __init__(self, log, source):
        self.out = io.StringIO()
        super().__init__(self.out, jsonl=True, clock_t=lambda: source.position)
        self.log = log

    def _event(self, event):
        self.log.append(("event", event["type"]))
        super()._event(event)

    def events(self):
        return [json.loads(line) for line in self.out.getvalue().splitlines()]


def none(n):
    return DiarUpdate([], n)


HELLO = word(0.0, 0.05, "Hello.")       # a sentence end: its segment closes at once
THERE = word(0.15, 0.3, "there")        # labelled once the flushed activity reaches it; `finish` closes its segment


def session(sinks=lambda log: Sinks()):
    """A run with a sentence-ended "Hello." labelled after block 0 and "there" labelled after the flushes; `sinks` is
    made from the run's log, so the sinks' calls can log among the stages' and the writer's."""
    log = []
    source = FakeSource(log)
    asr = FakeStage("asr", log, [AsrUpdate([HELLO], 800), AsrUpdate([], 1600), AsrUpdate([THERE], 3200),
                                 AsrUpdate([], 4800)], AsrUpdate([], 6400))
    diarizer = FakeStage("diar", log, [DiarUpdate([Piece(0, 1600, 0)], 1600), none(1600), none(1600),
                                       none(1600)], DiarUpdate([Piece(1600, 6400, 1)], 6400))
    writer = LoggingWriter(log, source)
    result = run_lockstep(source, asr, diarizer, writer, sinks(log), source_info=SOURCE, models=MODELS)
    return log, writer, result


class Order(unittest.TestCase):
    def test_start_comes_first_with_the_source_info_and_models_given_and_stop_last(self):
        log, writer, _ = session()
        events = writer.events()
        self.assertEqual(events[0], {"type": "start", "schema_version": "1.1", "t": 0, "source": SOURCE,
                                     "models": MODELS})
        self.assertEqual(events[-1], {"type": "stop", "t": 0.4, "reason": "end-of-input"})
        self.assertEqual([e["type"] for e in events],
                         ["start", "text", "words", "segment", "text", "words", "segment", "stop"])
        self.assertEqual(log[0], ("event", "start"))
        self.assertEqual(log[-1], ("event", "stop"))

    def test_each_block_reaches_both_stages_before_the_next_and_the_flushes_come_after_the_last(self):
        log, _, _ = session()
        calls = [entry for entry in log if entry[0] != "event"]
        self.assertEqual(calls, [entry for i in range(4) for entry in (("take", i), ("asr", i), ("diar", i))]
                         + [("asr", "flush"), ("diar", "flush")])

    def test_finish_comes_after_the_last_block_and_the_flushes(self):
        log, writer, _ = session()
        # "there" is labelled once the flushed activity reaches it, and `finish` then closes its segment.
        flushed = log.index(("diar", "flush"))
        self.assertEqual(log[flushed + 1:], [("event", "words"), ("event", "segment"), ("event", "stop")])
        self.assertEqual(writer.events()[5]["words"][0]["text"], "there")

    def test_text_goes_out_when_its_words_are_committed_before_their_label(self):
        log, writer, _ = session()
        # "there" is committed at block 2, its text event written then, and labelled only after the flushes.
        written = log.index(("event", "text"), log.index(("asr", 2)))
        self.assertLess(written, log.index(("asr", 3)))
        self.assertEqual(writer.events()[4], {"type": "text", "t": 0.3, "text": " there", "start": THERE.start,
                                              "end": THERE.end, "starts_word": True})

    def test_a_closed_segment_reaches_on_segment_before_its_event_is_written(self):
        log, _, _ = session(lambda log: Sinks(on_segment=lambda s, label: log.append(("on_segment", s.text, label))))
        self.assertEqual([entry for entry in log if entry[0] in ("event", "on_segment")],
                         [("event", "start"), ("event", "text"), ("event", "words"),
                          ("on_segment", "Hello.", "Speaker 1"), ("event", "segment"), ("event", "text"),
                          ("event", "words"), ("on_segment", "there", "Speaker 2"),
                          ("event", "segment"), ("event", "stop")])

    def test_on_block_sees_every_block_before_the_stages(self):
        log, _, _ = session(lambda log: Sinks(on_block=lambda b: log.append(("on_block", int(b[0])))))
        blocks = [entry for entry in log if entry[0] in ("take", "on_block", "asr", "diar")]
        self.assertEqual(blocks, [e for i in range(4) for e in (("take", i), ("on_block", i), ("asr", i), ("diar", i))]
                         + [("asr", "flush"), ("diar", "flush")])


class Result(unittest.TestCase):
    def test_it_holds_the_joins_speakers_and_segments_the_samples_and_the_sources_reason(self):
        _, _, result = session()
        hello = Segment(0.0, 0.05, "S1", align.segment_text((HELLO,)), (HELLO,))
        there = Segment(0.15, 0.3, "S2", align.segment_text((THERE,)), (THERE,))
        self.assertEqual(result, SessionResult([Speaker("S1", "Speaker 1"), Speaker("S2", "Speaker 2")],
                                               [hello, there], 4 * BLOCK, "end-of-input"))

    def test_the_reason_is_the_sources_own_and_stop_carries_it(self):
        class Stopped(FakeSource):
            def blocks(self):
                yield from super().blocks()
                self.reason = "signal"

        log = []
        source = Stopped(log, blocks=2)
        asr = FakeStage("asr", log, [AsrUpdate([], 1600), AsrUpdate([], 3200)], AsrUpdate([], 3200))
        diar = FakeStage("diar", log, [none(1600), none(3200)], none(3200))
        writer = LoggingWriter(log, source)
        result = run_lockstep(source, asr, diar, writer, source_info=SOURCE, models=MODELS)
        self.assertEqual(result, SessionResult([], [], 2 * BLOCK, "signal"))
        self.assertEqual(writer.events(), [writer.events()[0], {"type": "stop", "t": 0.2, "reason": "signal"}])

if __name__ == "__main__":
    unittest.main()
