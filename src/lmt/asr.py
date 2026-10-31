"""The ASR stage: faster-whisper with the settings fixed before any measurement (design/internal/g1-slice.md,
S2). The model loads only from the verified directory that `lmt.models.ensure` gives, never by name.
"""

import os
import re
import sys
import tempfile
from pathlib import Path

import numpy

from lmt.transcript import Word

os.environ["ORT_DISABLE_TELEMETRY"] = "1"  # before onnxruntime is imported (Q8)

BEAM_SIZE = 5
THREADS = min(8, os.cpu_count() or 1)  # fixed per machine (Q7)
# The lines quiet_onnxruntime drops: onnxruntime's verbose, info and warning ones, which on Linux start with
# a colour code and a timestamp, as in "\x1b[0;93m2026-10-29 22:31:07.5 [W:onnxruntime:Default, ...] ..."
_ONNXRUNTIME_QUIET = re.compile(r"(\x1b\[[0-9;]*m)?([0-9-]+ [0-9:.]+ )?\[[VIW]:onnxruntime")


def quiet_onnxruntime() -> None:
    """onnxruntime's default logger down to errors and fatal, so its warnings (on Linux, GetPciBusId's) stay
    off stderr and its errors still print (design/internal/g1-stage2.md, P11). It is faster-whisper's, for the
    Silero VAD. On Linux a warning prints with the severity set too, so descriptor 2 is captured around the
    import, the severity and the VAD model's load (cached, so `transcribe` reuses that session), and every
    line but onnxruntime's verbose, info and warning ones is written back. A second call is harmless."""
    sys.stderr.flush()
    saved = os.dup(2)
    with tempfile.TemporaryFile() as captured:
        os.dup2(captured.fileno(), 2)
        try:
            import onnxruntime

            onnxruntime.set_default_logger_severity(3)
            from faster_whisper.vad import get_vad_model

            get_vad_model()
        finally:
            sys.stderr.flush()
            os.dup2(saved, 2)
            os.close(saved)
            captured.seek(0)
            for line in captured.read().decode(errors="replace").splitlines(keepends=True):
                if not _ONNXRUNTIME_QUIET.match(line):
                    sys.stderr.write(line)
            sys.stderr.flush()


def load(model_dir: Path) -> "Engine":
    """The model in `model_dir`. Call after `models.ensure`, which sets HF_HUB_OFFLINE."""
    quiet_onnxruntime()
    from faster_whisper import WhisperModel

    model = WhisperModel(str(model_dir), device="cpu", compute_type="int8", cpu_threads=THREADS,
                         num_workers=1, local_files_only=True)
    return Engine(model)


class Engine:
    def __init__(self, model):
        self._model = model

    def transcribe(self, audio: numpy.ndarray, *, initial_prompt: str | None = None) -> list[Word]:
        """The words, with times in seconds from the start of `audio`, unrounded. A single temperature
        means no fallback (G1-12)."""
        segments, _info = self._model.transcribe(
            audio, language="en", task="transcribe", beam_size=BEAM_SIZE, temperature=0.0,
            condition_on_previous_text=False, word_timestamps=True, vad_filter=True,
            initial_prompt=initial_prompt)
        words = []
        for segment in segments:  # a generator: decoding happens as it is consumed
            for w in segment.words or ():
                text = w.word.strip()
                if text:
                    words.append(Word(float(w.start), float(w.end), text, float(w.probability)))
        return words
