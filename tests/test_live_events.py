"""The live events and their schema, with no model (design/internal/g2-stage1.md §3a, item 3a2), and live events
1.1's `text` event, with a stand-in engine's pieces through the join (g4-stage1.md §4, item 5; G2-1)."""

import io
import json
import os
import queue
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import pytest

from lmt import align, schema
from lmt.eval import live
from lmt.live.diarize import DiarUpdate
from lmt.live.diarize import Piece as Activity
from lmt.live.events import EventWriter
from lmt.live.join import Join, Piece, SegmentOut, TextOut, WordsOut
from lmt.transcript import Segment, Word

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "schema" / "live-events.schema.json"
PACKAGED = Path(schema.__file__).with_name("live-events.schema.json")
FIXTURES = sorted((ROOT / "tests" / "fixtures" / "eval" / "live").glob("run*.jsonl"))
RATE = 16000

SOURCE = {"file": "meeting.wav", "live": True}
MODELS = {
    "asr": {"name": "distil-large-v3", "repository": "Systran/faster-distil-whisper-large-v3", "revision": "abc"},
    "diarization": {"name": "speaker-diarization-3.1", "repository": "pyannote/speaker-diarization-3.1",
                    "revision": "def"},
}
FIELDS = {
    "start": ["type", "schema_version", "t", "source", "models"],
    "words": ["type", "t", "segment", "speaker", "label", "words"],
    "segment": ["type", "t", "segment", "start", "end", "speaker", "text"],
    "stop": ["type", "t", "reason"],
}

W1, W2 = Word(0.5, 0.8, "Okay,", 0.9), Word(0.9, 1.0, "so", 0.8)
W3 = Word(3661.2, 3661.5, "Right.", 0.7)
S1 = Segment(0.5, 1.0, "S1", align.segment_text((W1, W2)), (W1, W2))
S2 = Segment(3661.2, 3661.5, "S2", "Right.", (W3,))
# A fixed sequence: a segment whose words come in two events, then one in a second speaker's.
SEQUENCE = [
    WordsOut(1, "S1", "Speaker 1", [W1]), WordsOut(1, "S1", "Speaker 1", [W2]), SegmentOut(S1, 1),
    WordsOut(2, "S2", "Speaker 2", [W3]), SegmentOut(S2, 2),
]


def validator():
    return jsonschema.Draft202012Validator(json.loads(PUBLISHED.read_text(encoding="utf-8")))


def clock(*samples):
    """A `clock_t` that gives these positions, one per call."""
    return iter(samples).__next__


def session(stream, *, jsonl=True):
    """The fixed sequence through a writer, with `t` at 1 s, 2 s, … per event after `start`."""
    writer = EventWriter(stream, jsonl=jsonl, clock_t=clock(*(k * RATE for k in range(1, 7))))
    writer.start(SOURCE, MODELS)
    for out in SEQUENCE:
        writer.emit(out)
    writer.stop("end-of-input")


def events():
    stream = io.StringIO()
    session(stream)
    return [json.loads(line) for line in stream.getvalue().splitlines()]


class Events(unittest.TestCase):
    def test_each_event_validates_and_has_its_fields_in_order(self):
        found = events()
        self.assertEqual([e["type"] for e in found], ["start", "words", "words", "segment", "words", "segment", "stop"])
        for event in found:
            with self.subTest(event=event):
                validator().validate(event)
                self.assertEqual(list(event), FIELDS[event["type"]])

    def test_the_values(self):
        start, words, _, segment, _, _, stop = events()
        self.assertEqual(start, {"type": "start", "schema_version": "1.1", "t": 0, "source": SOURCE, "models": MODELS})
        self.assertEqual(words, {"type": "words", "t": 1.0, "segment": 1, "speaker": "S1", "label": "Speaker 1",
                                 "words": [{"start": 0.5, "end": 0.8, "text": "Okay,", "probability": 0.9}]})
        self.assertEqual(segment, {"type": "segment", "t": 3.0, "segment": 1, "start": 0.5, "end": 1.0,
                                   "speaker": "S1", "text": "Okay, so"})
        self.assertEqual(stop, {"type": "stop", "t": 6.0, "reason": "end-of-input"})

    def test_t_is_in_seconds_rounded_to_the_millisecond(self):
        stream = io.StringIO()
        EventWriter(stream, jsonl=True, clock_t=clock(12345)).stop("signal")
        self.assertEqual(json.loads(stream.getvalue())["t"], 0.772)

    def test_one_compact_line_per_event_without_escaping(self):
        stream = io.StringIO()
        writer = EventWriter(stream, jsonl=True, clock_t=clock(RATE))
        writer.start({"file": "réunion.wav", "live": True}, MODELS)
        line = stream.getvalue()
        self.assertTrue(line.endswith("}\n"))
        self.assertEqual(line.count("\n"), 1)
        self.assertIn('"file":"réunion.wav"', line)
        self.assertNotIn(", ", line.replace('"Okay, so"', ""))


class Schema(unittest.TestCase):
    def setUp(self):
        self.found = events()

    def rejects(self, event):
        self.assertFalse(validator().is_valid(event), event)

    def test_rejects_a_missing_field(self):
        for event in self.found:
            for field in FIELDS[event["type"]]:
                with self.subTest(type=event["type"], field=field):
                    self.rejects({k: v for k, v in event.items() if k != field})

    def test_rejects_a_lower_case_speaker(self):
        for event in (self.found[1], self.found[3]):
            with self.subTest(type=event["type"]):
                self.rejects({**event, "speaker": "s1"})

    def test_rejects_empty_words(self):
        self.rejects({**self.found[1], "words": []})

    def test_rejects_an_unknown_reason(self):
        self.rejects({**self.found[-1], "reason": "crash"})
        for reason in ("end-of-input", "duration", "signal"):
            validator().validate({**self.found[-1], "reason": reason})

    def test_allows_unknown_fields(self):
        for event in self.found:
            with self.subTest(type=event["type"]):
                validator().validate({**event, "added_in_1_1": True})

    def test_copies_identical(self):
        self.assertEqual(PUBLISHED.read_bytes(), PACKAGED.read_bytes())
        self.assertEqual(json.loads(PUBLISHED.read_text(encoding="utf-8"))["$schema"],
                         "https://json-schema.org/draft/2020-12/schema")

    def test_the_fixture_logs_are_real_events(self):
        self.assertEqual(len(FIXTURES), 3)
        for path in FIXTURES:
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                with self.subTest(file=path.name, line=n):
                    validator().validate(json.loads(line)["event"])


class Flushing(unittest.TestCase):
    def test_each_line_arrives_through_a_pipe_as_it_is_written(self):
        read, write = os.pipe()
        lines = queue.Queue()

        def reader():
            with os.fdopen(read, encoding="utf-8") as f:
                for line in f:
                    lines.put(line)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        with os.fdopen(write, "w", encoding="utf-8") as stream:  # buffered: only a flush sends a line
            writer = EventWriter(stream, jsonl=True, clock_t=clock(*(k * RATE for k in range(1, 7))))
            writer.start(SOURCE, MODELS)
            self.assertEqual(json.loads(lines.get(timeout=5))["type"], "start")
            for out in SEQUENCE:
                writer.emit(out)
                self.assertEqual(json.loads(lines.get(timeout=5))["type"], "words" if isinstance(out, WordsOut)
                                 else "segment")
            writer.stop("end-of-input")
            self.assertEqual(json.loads(lines.get(timeout=5))["type"], "stop")
        thread.join(timeout=5)
        self.assertTrue(lines.empty())


TEXT_FIELDS = ["type", "t", "text", "start", "end", "starts_word"]


def text_event(**changes):
    stream = io.StringIO()
    EventWriter(stream, jsonl=True, clock_t=clock(RATE)).emit(TextOut(Piece(" meet", 0.5004, 0.8, True, 0.6)))
    return {**json.loads(stream.getvalue()), **changes}


class TextEvent(unittest.TestCase):
    def test_a_piece_is_a_text_event_with_its_fields_in_order(self):
        event = text_event()
        self.assertEqual(event, {"type": "text", "t": 1.0, "text": " meet", "start": 0.5, "end": 0.8,
                                 "starts_word": True})
        self.assertEqual(list(event), TEXT_FIELDS)
        validator().validate(event)
        validator().validate(text_event(text="ing", starts_word=False))

    def test_the_schema_rejects_a_text_event_without_a_field_or_with_a_wrong_one(self):
        for field in TEXT_FIELDS:
            with self.subTest(field=field):
                self.assertFalse(validator().is_valid({k: v for k, v in text_event().items() if k != field}))
        for change in ({"starts_word": "yes"}, {"text": ""}, {"start": -0.1}, {"t": -1}):
            with self.subTest(change=change):
                self.assertFalse(validator().is_valid(text_event(**change)))

    def test_the_start_events_asr_model_may_name_its_engine(self):
        start = events()[0]
        for engine in ("kyutai", "apple"):
            models = {**MODELS, "asr": {**MODELS["asr"], "engine": engine}}
            validator().validate({**start, "models": models})
        self.assertFalse(validator().is_valid({**start, "models": {**MODELS, "asr": {**MODELS["asr"],
                                                                                    "engine": "whisper"}}}))

    def test_text_lines_leave_pieces_out_and_dont_read_the_clock(self):
        stream = io.StringIO()
        writer = EventWriter(stream, jsonl=False, clock_t=clock())   # no position to give
        writer.emit(TextOut(Piece(" Okay,", 0.5, 0.8, True, 0.9)))
        self.assertEqual(stream.getvalue(), "")


@dataclass(frozen=True)
class PieceUpdate:  # `AsrUpdate` with the pieces a stream gave (design §4)
    words: list
    done_until: int
    pieces: list


class StandInEngine:
    """A stand-in for item 3's live ASR: scripted pieces, out once the audio reaches their end, grouped into words
    as `engines.Words(0)` does (design §1), each word complete once the next word's first piece is out."""

    def __init__(self, pieces):
        self._script = list(pieces)
        self._open: list[Piece] = []

    def feed(self, position: float) -> PieceUpdate:
        out = []
        while self._script and self._script[0].end <= position:
            out.append(self._script.pop(0))
        return self._update(out, done=False)

    def flush(self) -> PieceUpdate:
        out, self._script = self._script, []
        return self._update(out, done=True)

    def _update(self, pieces, *, done) -> PieceUpdate:
        words = []
        for p in pieces:
            if self._open and p.starts_word:
                words.append(self._word())
            self._open.append(p)
        if done and self._open:
            words.append(self._word())
        until = self._open[0].start if self._open else (pieces[-1].end if pieces else 0.0)
        return PieceUpdate(words, round(until * RATE), pieces)

    def _word(self) -> Word:
        ps, self._open = self._open, []
        return Word(ps[0].start, ps[-1].end, "".join(p.text for p in ps).strip(), min(p.probability for p in ps))


# Sub-word pieces as Kyutai gives them (80 ms steps), and a word ending a sentence that closes its segment.
PIECES = [Piece(" O", 0.5, 0.58, True, 0.9), Piece("kay,", 0.58, 0.66, False, 0.8),
          Piece(" so", 0.9, 0.98, True, 0.95), Piece(" let", 1.1, 1.18, True, 0.9), Piece("'s", 1.18, 1.26, False, 0.7),
          Piece(" start.", 1.4, 1.48, True, 0.9), Piece(" Right", 2.6, 2.68, True, 0.9)]


def stand_in_session() -> list[tuple[float, dict]]:
    """The stand-in engine and scripted final activity through the join and the writer, one 0.1 s block at a time,
    as `run_lockstep` does; each event stamped with its `t` as its arrival."""
    engine, join, stream = StandInEngine(PIECES), Join(), io.StringIO()
    position = [0]
    writer = EventWriter(stream, jsonl=True, clock_t=lambda: position[0])
    writer.start(SOURCE, MODELS)
    for k in range(1, 31):
        position[0] = k * 1600
        join.add_words(engine.feed(k * 0.1))
        join.add_activity(DiarUpdate([Activity((k - 1) * 1600, k * 1600, 0 if k <= 20 else 1)], max(0, k - 5) * 1600))
        for out in join.poll():
            writer.emit(out)
    join.add_words(engine.flush())
    join.add_activity(DiarUpdate([], position[0]))
    for out in join.finish():
        writer.emit(out)
    writer.stop("end-of-input")
    found = [json.loads(line) for line in stream.getvalue().splitlines()]
    return [(e["t"], e) for e in found]


@pytest.mark.criterion("G2-1")
class TextAndWordsCarryTheSameWords(unittest.TestCase):
    def test_the_text_events_pieces_join_to_the_words_events_words_in_order(self):
        lines = stand_in_session()
        for _, event in lines:
            validator().validate(event)
        types = [e["type"] for _, e in lines]
        self.assertEqual(types.count("text"), len(PIECES))
        labelled, _ = live.read_events(lines)   # a 1.0 reader: words events alone
        text = live.read_text(lines)
        self.assertEqual([(w.text, w.start, w.end) for w, _ in text],
                         [(w.text, w.start, w.end) for w, _ in labelled])
        self.assertEqual([w.text for w, _ in text], ["Okay,", "so", "let's", "start.", "Right"])

    def test_each_words_event_comes_after_the_text_event_carrying_its_words_last_piece(self):
        lines = stand_in_session()
        last_piece_at = {}   # a word's end -> the index of the text event carrying its last piece
        for n, (_, event) in enumerate(lines):
            if event["type"] == "text":
                last_piece_at[event["end"]] = n
            elif event["type"] == "words":
                for w in event["words"]:
                    self.assertLess(last_piece_at[w["end"]], n, w)
        # And some text goes out well before its label: `so`'s piece is out at 1.0 s, labelled only at 1.5 s.
        so_text = next(t for t, e in lines if e["type"] == "text" and e["text"] == " so")
        so_label = next(t for t, e in lines if e["type"] == "words" and any(w["text"] == "so" for w in e["words"]))
        self.assertLess(so_text, so_label)


class Text(unittest.TestCase):
    def test_the_text_form_of_a_fixed_sequence(self):
        stream = io.StringIO()
        session(stream, jsonl=False)
        self.assertEqual(stream.getvalue(), "[00:00:00] Speaker 1: Okay, so\n[01:01:01] Speaker 2: Right.\n")

    def test_every_text_write_is_flushed(self):
        flushed = []

        class Recorder(io.StringIO):
            def flush(self):
                flushed.append(self.getvalue())

        session(Recorder(), jsonl=False)
        self.assertEqual(flushed, [
            "[00:00:00] Speaker 1: Okay,",
            "[00:00:00] Speaker 1: Okay, so",
            "[00:00:00] Speaker 1: Okay, so\n",
            "[00:00:00] Speaker 1: Okay, so\n[01:01:01] Speaker 2: Right.",
            "[00:00:00] Speaker 1: Okay, so\n[01:01:01] Speaker 2: Right.\n",
        ])
