"""`lmt demo`'s server (g3-stage1.md §2a).

`Demo`, `wav_bytes` and `load` are 2a2's. The handler (2a3) and `cli_demo` (2a4) are still to come, so
`cli_demo` raises `NotBuilt`.
"""

import io
import secrets
import sys
import threading
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy

from lmt import audio as lmt_audio
from lmt import transcript as lmt_transcript
from lmt.demo import sample
from lmt.errors import InputError, NotBuilt


@dataclass
class Demo:
    transcript: Path
    audio: bytes                 # the whole WAV file
    token: str
    port: int = 0                # set once bound
    last_warning: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def wav_bytes(samples: numpy.ndarray) -> bytes:
    """16-bit PCM, 16 kHz, mono: the samples clipped to [-1, 1], times 32767, rounded, little-endian."""
    pcm = numpy.round(numpy.clip(numpy.asarray(samples, dtype=numpy.float64), -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(lmt_audio.SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def load(transcript: Path, audio: Path | None, audio_option: bool) -> Demo:
    """The demo for a transcript: its audio is `audio` (given by --audio when `audio_option`), or by
    default `source.file` in the transcript's directory. Every failure is InputError (exit 3)."""
    transcript = Path(transcript)
    t = lmt_transcript.load(transcript)
    if audio is None:
        audio = transcript.parent / t.source["file"]
    audio = Path(audio)
    try:
        samples = lmt_audio.decode(audio)
    except InputError as e:
        if audio_option:
            raise InputError(f"--audio {audio}", e.problem) from None
        raise InputError(str(audio), f"{e.problem}; give the recording with --audio") from None
    lasts = len(samples) / lmt_audio.SAMPLE_RATE
    says = t.source["duration"]
    if abs(lasts - says) > 1:
        print(f"lmt: warning: {audio}: lasts {round(lasts, 2):g} s, but the transcript says {says:g} s",
              file=sys.stderr, flush=True)
    return Demo(transcript=transcript, audio=wav_bytes(samples), token=secrets.token_urlsafe(16))


def cli_demo(args):
    if args.sample:
        sample.prepare(offline=args.offline)
    raise NotBuilt("demo", "G3 stage 1")
