"""The ASR stage: faster-whisper with the settings fixed before any measurement (design/internal/g1-slice.md,
S2). The model loads only from the verified directory that `lmt.models.ensure` gives, never by name.
"""

import os
from pathlib import Path

import numpy

from lmt.transcript import Word

BEAM_SIZE = 5
THREADS = min(8, os.cpu_count() or 1)  # fixed per machine (Q7)


def load(model_dir: Path) -> "Engine":
    """The model in `model_dir`. Call after `models.ensure`, which sets HF_HUB_OFFLINE."""
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
