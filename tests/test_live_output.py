"""The live outputs (design/internal/g2-stage1.md §3b, item 3b2): the growing text, the WAV, `save` and the live
transcript. No model."""

import json
import os
import stat
import tempfile
import threading
import unittest
import wave
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import numpy as np

from lmt import export, schema
from lmt.live import output
from lmt.live.output import GrowingText, WavWriter, live_transcript, save
from lmt.transcript import SCHEMA_VERSION, Segment, Speaker, Transcript, Word, to_dict

MODELS = {"asr": {"name": "distil-large-v3", "repository": "Systran/faster-distil-whisper-large-v3",
                  "revision": "0123abc"},
          "diarization": {"name": "speaker-diarization-3.1", "repository": "pyannote/speaker-diarization-3.1",
                          "revision": "4567def"}}
SPEAKERS = [Speaker("S1", "Speaker 1"), Speaker("S2", "Speaker 2")]
SEGMENTS = [Segment(0.48, 1.2, "S1", "Shall we?", (Word(0.48, 0.71, "Shall", 0.97), Word(0.75, 1.2, "we?", 0.9))),
            Segment(3661.5, 3662.0, "S2", "Ja, gern.", (Word(3661.5, 3662.0, "Ja, gern.", 0.8),))]


@dataclass(frozen=True)
class Result:  # a SessionResult's fields (3a3)
    speakers: list
    segments: list
    samples: int
    reason: str


def transcript():
    return Transcript(SCHEMA_VERSION, "0.1.0", {"file": "meeting.wav", "duration": 3663.0, "live": True}, MODELS,
                      tuple(SPEAKERS), tuple(SEGMENTS))


class GrowingTextTest(unittest.TestCase):
    def test_a_line_per_segment_flushed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "m.txt"
            path.write_text("old\n")
            text = GrowingText(path)
            self.assertEqual(path.read_text(), "")  # truncated at capture start
            text.append(SEGMENTS[0], "Speaker 1")
            self.assertEqual(path.read_text(encoding="utf-8"), "[00:00:00] Speaker 1: Shall we?\n")
            text.append(SEGMENTS[1], "Speaker 2")
            self.assertEqual(path.read_text(encoding="utf-8"),
                             "[00:00:00] Speaker 1: Shall we?\n[01:01:01] Speaker 2: Ja, gern.\n")
            text.close()

    def test_equals_the_text_export(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "m.txt"
            text = GrowingText(path)
            for s in SEGMENTS:
                text.append(s, {"S1": "Speaker 1", "S2": "Speaker 2"}[s.speaker])
            text.close()
            self.assertEqual(path.read_text(encoding="utf-8"), export.render(transcript(), "txt"))


class WavWriterTest(unittest.TestCase):
    def test_reads_back_after_every_block(self):
        rng = np.random.default_rng(0)
        blocks = [rng.uniform(-1.2, 1.2, 1600).astype(np.float32) for _ in range(3)] + [np.zeros(700, np.float32)]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "m.wav"
            wav = WavWriter(path)
            with wave.open(str(path), "rb") as w:
                self.assertEqual(w.getnframes(), 0)
            written = []
            for block in blocks:
                wav.write(block)
                written.append(block)
                with wave.open(str(path), "rb") as w:
                    self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 16000))
                    self.assertEqual(w.getnframes(), sum(len(b) for b in written))
                    got = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
                want = np.round(np.clip(np.concatenate(written).astype(np.float64), -1, 1) * 32767).astype(np.int16)
                np.testing.assert_array_equal(got, want)
            self.assertEqual(path.stat().st_size, 44 + 2 * sum(len(b) for b in blocks))
            wav.close()

    def test_clips_and_scales(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "m.wav"
            wav = WavWriter(path)
            wav.write(np.array([-2.0, -1.0, 0.0, 0.5, 1.0, 3.0]))
            wav.close()
            with wave.open(str(path), "rb") as w:
                got = np.frombuffer(w.readframes(6), dtype="<i2")
            np.testing.assert_array_equal(got, [-32767, -32767, 0, 16384, 32767, 32767])


class SaveTest(unittest.TestCase):
    def test_writes_every_format_and_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.txt").write_text("growing\n")  # the growing text is replaced
            paths = save(transcript(), d, "m", ["txt", "srt", "vtt", "json"], abandon=threading.Event())
            self.assertEqual(paths, export.outputs(d, "m", ["txt", "srt", "vtt", "json"]))
            self.assertEqual(sorted(os.listdir(d)), ["m.json", "m.srt", "m.txt", "m.vtt"])
            for fmt in ("txt", "srt", "vtt", "json"):
                self.assertEqual((Path(d) / f"m.{fmt}").read_text(encoding="utf-8"),
                                 export.render(transcript(), fmt))

    def test_mode_is_what_export_write_gives(self):
        old = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as d:
                save(transcript(), d, "m", ["json"], abandon=threading.Event())
                self.assertEqual(stat.S_IMODE((Path(d) / "m.json").stat().st_mode), 0o644)
        finally:
            os.umask(old)

    def test_abandon_renames_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.txt").write_text("growing\n")
            abandon = threading.Event()
            abandon.set()
            self.assertEqual(save(transcript(), d, "m", ["txt", "json"], abandon=abandon), [])
            self.assertEqual(os.listdir(d), ["m.txt"])
            self.assertEqual((Path(d) / "m.txt").read_text(), "growing\n")

    def test_interrupt_between_writes_leaves_nothing(self):
        render = export.render
        calls = []

        def interrupted(t, fmt):
            calls.append(fmt)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return render(t, fmt)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(output.export, "render", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                save(transcript(), d, "m", ["srt", "json", "vtt"], abandon=threading.Event())
            self.assertEqual(calls, ["srt", "json"])
            self.assertEqual(os.listdir(d), [])

    def test_failed_rename_leaves_no_temporary_file(self):
        replace = os.replace
        calls = []

        def failing(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError("disk gone")
            replace(src, dst)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(output.os, "replace", failing):
            with self.assertRaises(OSError):
                save(transcript(), d, "m", ["srt", "json", "vtt"], abandon=threading.Event())
            self.assertEqual(os.listdir(d), ["m.srt"])  # renamed before the failure; no temporary file is left


class LiveTranscriptTest(unittest.TestCase):
    def result(self, samples=16000 * 3663 + 7):
        return Result(SPEAKERS, SEGMENTS, samples, "end-of-input")

    def test_a_file(self):
        t = live_transcript(self.result(), {"file": "meeting.wav", "live": True}, MODELS)
        doc = to_dict(t)
        schema.validate(doc)
        self.assertEqual(doc["schema_version"], SCHEMA_VERSION)
        self.assertEqual(doc["source"], {"file": "meeting.wav", "duration": 3663.0, "live": True})
        self.assertEqual(doc["models"], MODELS)
        self.assertEqual(t.speakers, tuple(SPEAKERS))
        self.assertEqual(t.segments, tuple(SEGMENTS))
        self.assertIsInstance(t.segments, tuple)

    def test_a_device(self):
        t = live_transcript(self.result(), {"file": "live-20261030-091500", "live": True, "device": "USB Mic"},
                            MODELS)
        doc = json.loads(export.render(t, "json"))
        schema.validate(doc)
        self.assertEqual(doc["source"], {"file": "live-20261030-091500", "duration": 3663.0, "live": True,
                                         "device": "USB Mic"})

    def test_duration_to_the_millisecond(self):
        t = live_transcript(self.result(samples=16000 + 1599), {"file": "m"}, MODELS)
        self.assertEqual(t.source, {"file": "m", "duration": 1.099, "live": True})

    def test_no_speech(self):
        t = live_transcript(Result([], [], 64000, "duration"), {"file": "m", "live": True},
                            {"asr": MODELS["asr"], "diarization": None})
        schema.validate(to_dict(t))
        self.assertEqual((t.speakers, t.segments), ((), ()))


if __name__ == "__main__":
    unittest.main()
