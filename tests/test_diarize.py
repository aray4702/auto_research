"""The diarization stage (design/internal/g1-slice.md, S3): loading from local directories with fake models,
the turns from a fake pipeline, and the real pipeline on the reference clip with no network."""

import logging
import os
import socket
import warnings
import wave
from pathlib import Path
from typing import ClassVar

import numpy
import pytest
import torch
import yaml
from pyannote.core import Annotation, Segment

from lmt import diarize, models
from lmt.transcript import Turn

ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "tests" / "fixtures" / "clip.wav"

CONFIG = {  # the shape of speaker-diarization-3.1's config.yaml
    "version": "3.1.0",
    "pipeline": {
        "name": "pyannote.audio.pipelines.SpeakerDiarization",
        "params": {"clustering": "AgglomerativeClustering", "embedding": "pyannote/wespeaker-voxceleb-resnet34-LM",
                   "embedding_batch_size": 32, "embedding_exclude_overlap": True,
                   "segmentation": "pyannote/segmentation-3.0", "segmentation_batch_size": 32},
    },
    "params": {"clustering": {"method": "centroid", "min_cluster_size": 12, "threshold": 0.7045654963945799},
               "segmentation": {"min_duration_off": 0.0}},
}


@pytest.fixture(autouse=True)
def torch_state():
    """configure_torch changes process-wide settings: put them back after each test."""
    threads, deterministic = torch.get_num_threads(), torch.are_deterministic_algorithms_enabled()
    yield
    torch.set_num_threads(threads)
    torch.use_deterministic_algorithms(deterministic)


class FakePipeline:
    """Records how it was built, instantiated and called, and returns `annotation`."""

    instances: ClassVar[list] = []
    annotation: ClassVar[Annotation] = Annotation()

    def __init__(self, **kwargs):
        warnings.warn("a library warning", UserWarning, stacklevel=1)
        logging.getLogger("pyannote.audio").warning("a library log line")
        self.kwargs, self.instantiated, self.calls = kwargs, None, []
        FakePipeline.instances.append(self)

    def instantiate(self, params):
        self.instantiated = params

    def __call__(self, file, **kwargs):
        self.calls.append((file, kwargs))
        return FakePipeline.annotation


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Three model directories, with the pipeline and Model.from_pretrained replaced by fakes. load's
    MPLCONFIGDIR and scratch directory are this test's: the variable is undone after it, and the directory
    goes at pytest's exit."""
    import pyannote.audio
    import pyannote.audio.pipelines

    monkeypatch.setenv("MPLCONFIGDIR", "")  # so teardown restores the variable, even where it was unset
    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    monkeypatch.setattr(diarize, "_scratch", None)

    dirs = {name: tmp_path / name for name in (diarize.DIARIZATION, diarize.SEGMENTATION, diarize.EMBEDDING)}
    for d in dirs.values():
        d.mkdir()
    (dirs[diarize.DIARIZATION] / "config.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    loaded = {}

    def from_pretrained(checkpoint, **kwargs):
        model = object()
        loaded[str(checkpoint)] = (model, kwargs)
        return model

    monkeypatch.setattr(FakePipeline, "instances", [])
    monkeypatch.setattr(pyannote.audio.pipelines, "SpeakerDiarization", FakePipeline)
    monkeypatch.setattr(pyannote.audio.Model, "from_pretrained", from_pretrained)
    return dirs, loaded


def test_settings():
    assert diarize.THREADS == min(8, os.cpu_count() or 1)
    assert diarize.SEED == 0


def test_configure_torch():
    torch.use_deterministic_algorithms(False)
    torch.manual_seed(123)
    diarize.configure_torch()
    assert torch.get_num_threads() == diarize.THREADS
    assert torch.are_deterministic_algorithms_enabled()
    assert torch.initial_seed() == diarize.SEED


def test_load_moves_matplotlibs_cache_first(monkeypatch):
    class Sentinel(Exception):
        pass

    def scratch_matplotlib():
        raise Sentinel

    monkeypatch.setattr(diarize, "scratch_matplotlib", scratch_matplotlib)
    with pytest.raises(Sentinel):
        diarize.load({})  # before the config is read, which would raise KeyError


def test_load_passes_the_loaded_models_not_names(fake):
    dirs, loaded = fake
    diarize.load(dirs)
    (pipeline,) = FakePipeline.instances
    segmentation = dirs[diarize.SEGMENTATION] / "pytorch_model.bin"
    embedding = dirs[diarize.EMBEDDING] / "pytorch_model.bin"
    assert set(loaded) == {str(segmentation), str(embedding)}  # local files, never a repository name
    assert pipeline.kwargs["segmentation"] is loaded[str(segmentation)][0]
    assert pipeline.kwargs["embedding"] is loaded[str(embedding)][0]
    others = {k: v for k, v in CONFIG["pipeline"]["params"].items() if k not in ("segmentation", "embedding")}
    assert {k: v for k, v in pipeline.kwargs.items() if k not in ("segmentation", "embedding")} == others
    assert "use_auth_token" not in pipeline.kwargs
    assert pipeline.instantiated == CONFIG["params"]


def test_load_is_quiet(fake, recwarn):
    diarize.load(fake[0])
    assert not recwarn.list
    assert all(logging.getLogger(name).level == logging.ERROR for name in diarize.LOGGERS)


def test_the_checkpoints_classes_load_with_weights_only(tmp_path):
    """pyannote.audio's checkpoints pickle torch's version and the task's specifications. torch.load refuses
    them by default; the stage allows exactly those, as the files are verified first."""
    from pyannote.audio.core.task import Problem, Resolution, Specifications

    checkpoint = {"pyannote.audio": {
        "versions": {"torch": torch.__version__},
        "specifications": Specifications(problem=Problem.MONO_LABEL_CLASSIFICATION, resolution=Resolution.FRAME,
                                         duration=10.0, classes=["speech"])}}
    path = tmp_path / "pytorch_model.bin"
    torch.save(checkpoint, path)
    diarize._allow_checkpoint_classes()
    got = torch.load(path, weights_only=True)
    assert got["pyannote.audio"]["specifications"] == checkpoint["pyannote.audio"]["specifications"]


def test_diarize_gives_sorted_turns(fake):
    annotation = Annotation()
    annotation[Segment(3.5, 4.25)] = "SPEAKER_00"
    annotation[Segment(0.125, 2.0)] = "SPEAKER_01"
    annotation[Segment(0.125, 1.5), "b"] = "SPEAKER_00"  # overlapping turns stay
    annotation[Segment(0.125, 1.5), "c"] = "SPEAKER_02"
    FakePipeline.annotation = annotation
    samples = numpy.linspace(-1, 1, 5 * 16_000, dtype=numpy.float32)

    turns = diarize.load(fake[0]).diarize(samples)

    assert turns == [Turn(0.125, 1.5, "SPEAKER_00"), Turn(0.125, 1.5, "SPEAKER_02"),
                     Turn(0.125, 2.0, "SPEAKER_01"), Turn(3.5, 4.25, "SPEAKER_00")]
    ((file, kwargs),) = FakePipeline.instances[0].calls
    assert file["sample_rate"] == 16000
    assert file["waveform"].shape == (1, 5 * 16_000)
    assert numpy.array_equal(file["waveform"][0].numpy(), samples)
    assert kwargs == {"num_speakers": None, "min_speakers": None, "max_speakers": None}  # no speaker count
    assert torch.get_num_threads() == diarize.THREADS
    assert torch.are_deterministic_algorithms_enabled()


def test_diarize_clips_the_turns_to_the_audio(fake):
    annotation = Annotation()
    annotation[Segment(-0.01, 0.5)] = "SPEAKER_00"
    annotation[Segment(0.75, 1.02)] = "SPEAKER_01"
    annotation[Segment(1.0, 1.015)] = "SPEAKER_00"  # wholly past the end: dropped
    FakePipeline.annotation = annotation
    diarizer = diarize.load(fake[0])

    turns = diarizer.diarize(numpy.zeros(16_000, numpy.float32))

    assert turns == [Turn(0.0, 0.5, "SPEAKER_00"), Turn(0.75, 1.0, "SPEAKER_01")]
    assert diarizer.raw == [Turn(-0.01, 0.5, "SPEAKER_00"), Turn(0.75, 1.02, "SPEAKER_01"),
                            Turn(1.0, 1.015, "SPEAKER_00")]
    assert diarizer.clipped == pytest.approx(0.02)


def test_diarize_passes_the_speaker_options(fake):
    FakePipeline.annotation = Annotation()
    diarizer = diarize.load(fake[0])
    diarizer.diarize(numpy.zeros(10, numpy.float32), num_speakers=2)
    diarizer.diarize(numpy.zeros(10, numpy.float32), min_speakers=1, max_speakers=3)
    calls = [kwargs for _, kwargs in FakePipeline.instances[0].calls]
    assert calls == [{"num_speakers": 2, "min_speakers": None, "max_speakers": None},
                     {"num_speakers": None, "min_speakers": 1, "max_speakers": 3}]


def read_clip():
    with wave.open(str(CLIP), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16_000, 1, 2)
        frames = w.readframes(w.getnframes())
    return numpy.frombuffer(frames, dtype="<i2").astype(numpy.float32) / 32768


@pytest.mark.models
def test_the_clip_diarizes_within_its_duration_without_network(monkeypatch, capsys):
    """The speaker count isn't asserted: that is G1-3's, on the two-speaker clip."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")  # so teardown undoes ensure()'s
    manifest = models.load_manifest()
    dirs = models.ensure([manifest.get(name) for name in
                          (diarize.DIARIZATION, diarize.SEGMENTATION, diarize.EMBEDDING)])
    samples = read_clip()
    duration = len(samples) / 16_000

    def no_network(*args, **kwargs):
        raise OSError("the diarization stage tried the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    diarizer = diarize.load(dirs)
    turns = diarizer.diarize(samples)

    # The guard on clipping (builder, #285): pyannote's last segmentation window is zero-padded past the audio,
    # so a turn may overshoot by less than the window's step, never more. A larger overshoot is a different fault.
    step = diarizer._pipeline._segmentation.step
    with capsys.disabled():  # into CI's log, whether the test passes or fails
        print(f"\nRaw turns: first {diarizer.raw[0]}, last {diarizer.raw[-1]}; duration {duration:.6f} s; "
              f"clipped at most {diarizer.clipped:.6f} s; window step {step:.6f} s")
    assert diarizer.clipped < step
    assert turns
    assert all(0 <= t.start < t.end <= duration for t in turns)
    assert turns == sorted(turns, key=lambda t: (t.start, t.end, t.speaker))
    speakers = len({t.speaker for t in turns})
    head = f"Diarization ({diarize.DIARIZATION}) on tests/fixtures/clip.wav: {len(turns)} turns, {speakers} speakers"
    lines = [head] + [f"  {t.start:.3f}-{t.end:.3f} {t.speaker}" for t in turns]
    print("\n".join(lines))
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
