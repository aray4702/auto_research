"""Loading a demo: `Demo`, `wav_bytes` and `load` (design/internal/g3-stage1.md, 2a2's tests)."""

import contextlib
import io
import re
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

import numpy

from lmt.audio import decode
from lmt.demo.server import load, wav_bytes
from lmt.errors import InputError

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def read_wav(data: bytes):
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes(), w.readframes(w.getnframes())


class DemoLoadTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.transcript = self.dir / "two_speakers.transcript.json"
        self.wav = self.dir / "two_speakers.wav"
        shutil.copy(FIXTURES / "two_speakers.transcript.json", self.transcript)
        shutil.copy(FIXTURES / "two_speakers.wav", self.wav)

    def load_quietly(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            demo = load(*args)
        return demo, err.getvalue()

    def test_wav_is_16_khz_mono_16_bit_with_decodes_frames(self):
        demo, err = self.load_quietly(self.transcript, None, False)
        rate, channels, width, frames, _ = read_wav(demo.audio)
        self.assertEqual((rate, channels, width), (16000, 1, 2))
        self.assertEqual(frames, len(decode(self.wav)))
        self.assertEqual(err, "")

    def test_wav_bytes_clips_beyond_one(self):
        _, _, _, frames, data = read_wav(wav_bytes(numpy.array([2.0, -3.0, 1.0, -1.0, 0.0], numpy.float32)))
        self.assertEqual(frames, 5)
        self.assertEqual(numpy.frombuffer(data, "<i2").tolist(), [32767, -32767, 32767, -32767, 0])

    def test_default_audio_is_source_file_beside_the_transcript(self):
        demo, _ = self.load_quietly(self.transcript, None, False)
        self.assertEqual(demo.audio, wav_bytes(decode(self.wav)))
        self.assertEqual(demo.transcript, self.transcript)

    def test_audio_option_names_the_file(self):
        other = self.dir / "other.flac"
        shutil.copy(FIXTURES / "clip.flac", other)
        self.wav.unlink()
        demo, _ = self.load_quietly(self.transcript, other, True)
        self.assertEqual(demo.audio, wav_bytes(decode(other)))

    def test_audio_option_naming_a_missing_file(self):
        missing = self.dir / "missing.wav"
        with self.assertRaises(InputError) as cm:
            load(self.transcript, missing, True)
        self.assertEqual(cm.exception.subject, f"--audio {missing}")

    def test_default_audio_missing(self):
        self.wav.unlink()
        with self.assertRaises(InputError) as cm:
            load(self.transcript, None, False)
        self.assertEqual(cm.exception.subject, str(self.wav))
        self.assertTrue(cm.exception.problem.endswith("; give the recording with --audio"), cm.exception.problem)

    def test_invalid_transcript(self):
        self.transcript.write_text("{", encoding="utf-8")
        with self.assertRaises(InputError) as cm:
            load(self.transcript, None, False)
        self.assertEqual(cm.exception.subject, str(self.transcript))

    def test_duration_differing_by_over_a_second_warns_once_and_still_loads(self):
        short = self.dir / "short.wav"
        short.write_bytes(wav_bytes(numpy.zeros(16000, numpy.float32)))
        demo, err = self.load_quietly(self.transcript, short, True)
        lines = err.splitlines()
        self.assertEqual(len(lines), 1, err)
        self.assertTrue(lines[0].startswith(f"lmt: warning: {short}: lasts 1 s, but the transcript says"), err)
        self.assertEqual(read_wav(demo.audio)[3], 16000)

    def test_token_is_22_url_safe_characters_and_differs_between_loads(self):
        a, _ = self.load_quietly(self.transcript, None, False)
        b, _ = self.load_quietly(self.transcript, None, False)
        for token in (a.token, b.token):
            self.assertRegex(token, re.compile(r"^[A-Za-z0-9_-]{22}$"))
        self.assertNotEqual(a.token, b.token)
