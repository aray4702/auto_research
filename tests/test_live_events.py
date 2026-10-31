"""The live events and their schema, with no model (design/internal/g2-stage1.md §3a, item 3a2)."""

import io
import json
import os
import queue
import threading
import unittest
from pathlib import Path

import jsonschema

from lmt import align, schema
from lmt.live.events import EventWriter
from lmt.live.join import SegmentOut, WordsOut
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
        self.assertEqual(start, {"type": "start", "schema_version": "1.0", "t": 0, "source": SOURCE, "models": MODELS})
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
