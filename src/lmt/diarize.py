"""The diarization stage: pyannote/speaker-diarization-3.1 on pyannote.audio 3.x (design/internal/g1-slice.md,
S3). The pipeline and its two models load only from the verified directories that `lmt.models.ensure` gives,
never by name, and run on the CPU with fixed threads and seed.
"""

import contextlib
import logging
import warnings
from collections.abc import Mapping
from pathlib import Path

import numpy
import yaml

from lmt import asr
from lmt.transcript import Turn

THREADS = asr.THREADS
SEED = 0

# The manifest's names of the three pyannote models: the keys of load()'s `dirs`.
DIARIZATION = "speaker-diarization-3.1"
SEGMENTATION = "segmentation-3.0"
EMBEDDING = "wespeaker-voxceleb-resnet34-LM"

LOGGERS = ("lightning", "pytorch_lightning", "pyannote")


@contextlib.contextmanager
def _quiet():
    """No library warnings, and only errors from their loggers, so a successful run prints nothing."""
    for name in LOGGERS:
        logging.getLogger(name).setLevel(logging.ERROR)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def configure_torch() -> None:
    import torch

    torch.set_num_threads(THREADS)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(SEED)


def _allow_checkpoint_classes() -> None:
    """torch.load defaults to weights_only=True, which refuses the classes pyannote.audio pickles in its
    checkpoints: torch's version, and the task's specifications with their enums. Each file's SHA-256 is
    pinned and verified before it is loaded, so allowing these is safe."""
    import torch
    from pyannote.audio.core.task import Problem, Resolution, Specifications

    torch.serialization.add_safe_globals([torch.torch_version.TorchVersion, Specifications, Problem, Resolution])


def _load_model(model_dir: Path):
    from pyannote.audio import Model

    _allow_checkpoint_classes()
    # strict=False, as pyannote.audio itself loads a pipeline's models by name (pipelines.utils.get_model).
    return Model.from_pretrained(model_dir / "pytorch_model.bin", strict=False)


def load_segmentation(model_dir: Path):
    return _load_model(model_dir)


def load_embedding(model_dir: Path):
    return _load_model(model_dir)


def load(dirs: Mapping[str, Path]) -> "Diarizer":
    """The pipeline, from the three verified model directories. Call after `models.ensure`, which sets
    HF_HUB_OFFLINE."""
    config = yaml.safe_load((Path(dirs[DIARIZATION]) / "config.yaml").read_text(encoding="utf-8"))
    with _quiet():
        from pyannote.audio.pipelines import SpeakerDiarization

        params = dict(config["pipeline"]["params"])
        params["segmentation"] = load_segmentation(Path(dirs[SEGMENTATION]))
        params["embedding"] = load_embedding(Path(dirs[EMBEDDING]))
        pipeline = SpeakerDiarization(**params)
        pipeline.instantiate(config["params"])
    return Diarizer(pipeline)


class Diarizer:
    def __init__(self, pipeline):
        self._pipeline = pipeline
        self.raw: list[Turn] = []  # the last call's turns as the pipeline gave them, sorted, before clipping
        self.clipped = 0.0  # the most the last call clipped from a turn, below 0 or past the audio's end

    def diarize(self, audio: numpy.ndarray, *, num_speakers=None, min_speakers=None,
                max_speakers=None) -> list[Turn]:
        """The speaker turns of `audio` (float32, mono, 16 kHz), in seconds from its start, unrounded, sorted
        by (start, end, speaker). Turns may overlap. No speaker count unless one is given. pyannote's frame
        grid can run past the audio, so each turn is clipped to [0, duration], and a turn left empty is
        dropped."""
        import torch

        configure_torch()
        with _quiet():
            annotation = self._pipeline(
                {"waveform": torch.from_numpy(audio)[None, :], "sample_rate": 16000},
                num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers)
        turns = [Turn(float(segment.start), float(segment.end), str(speaker))
                 for segment, _track, speaker in annotation.itertracks(yield_label=True)]
        self.raw = sorted(turns, key=lambda t: (t.start, t.end, t.speaker))
        duration = len(audio) / 16000
        self.clipped = max([0.0] + [max(-t.start, t.end - duration) for t in self.raw])
        clipped = [Turn(max(t.start, 0.0), min(t.end, duration), t.speaker) for t in self.raw]
        return sorted((t for t in clipped if t.end > t.start), key=lambda t: (t.start, t.end, t.speaker))
