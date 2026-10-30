"""Online diarization (design/internal/g2-stage1.md §2): Coria et al. (2021), as `diart` implements it.
`OnlineDiarizer` is the stage: when it steps, steps 1-2 on the models (segmentation, overlap-aware embeddings),
and steps 3-6 in a `Clusterer`, which matches one step's local speakers to the session's speakers, keeps their
centroids, accumulates activity on the absolute frame grid and makes it final. `replay` runs the `Clusterer`
over recorded steps, so the thresholds can be tuned without the models. `NullDiarizer` is `--no-diarize`'s
stage. Positions are in samples at 16 kHz.
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from lmt import diarize

RATE = 16000
LOCAL_SPEAKERS = 3

# Added to a pair's cost in proportion to the speaker's index, so that a local speaker at equal distances takes
# the lower one. Far below any difference between two real cosine distances.
TIE = 1e-12


@dataclass(frozen=True)
class DiarConfig:
    step: int = 8000              # S, 0.5 s
    window: int = 80000           # W, 5 s
    latency: int = 16000          # L, 1.0 s (provisional; stage 2 sweeps 0.5, 1.0 and 1.5 s)
    min_active: float = 0.5       # s of weighted activity for a local speaker to be embedded
    min_update: float = 1.0       # s of weighted activity for its centroid to be updated
    new_speaker: float = 0.70     # cosine distance beyond which a local speaker is new
    gamma: float = 3.0
    beta: float = 10.0
    max_speakers: int = 20


DEFAULT_DIAR_CONFIG = DiarConfig()


@dataclass(frozen=True)
class Piece:                      # final activity of one speaker
    start: int                    # samples
    end: int
    speaker: int                  # internal id, 0, 1, … in order of creation


@dataclass(frozen=True)
class DiarUpdate:
    pieces: list[Piece]           # newly final, within [previous final_until, final_until)
    final_until: int


@dataclass(frozen=True)
class Step:                       # what one step hands the clusterer, and what `record` keeps
    position: int                 # p, samples
    frames: np.ndarray            # (F,) int: each frame's index on the absolute grid, padding dropped
    activity: np.ndarray          # (F, 3) 0/1: the local speakers' hard activity
    embeddings: np.ndarray        # (3, D): L2-normalised, a row of NaN for an inactive local speaker
    weight: np.ndarray            # (3,): each local speaker's weights' sum, in seconds


class Clusterer:
    """Steps 3-6 of §2, one `Step` at a time, in order of position."""

    def __init__(self, config, frame_step: int):
        self.config = config
        self.frame_step = frame_step       # the model's frame step, in samples
        self.centroids: list[np.ndarray] = []   # per speaker, a sum of unit vectors
        self.final_until = 0
        self._next = 0                     # the first grid frame not yet final
        self._count: dict[int, int] = {}   # grid frame -> how many windows covered it
        self._sums: dict[int, dict[int, float]] = {}   # grid frame -> speaker -> the sum of its activity

    def step(self, step: Step) -> DiarUpdate:
        assigned = self._match(step)
        self._accumulate(step, assigned)
        return self._finalize(step.position - self.config.latency)

    def finish(self, end: int) -> DiarUpdate:
        """Every frame final up to `end`, the end of the audio."""
        before = self.final_until
        pieces = [Piece(p.start, min(p.end, end), p.speaker) for p in self._finalize(end).pieces]
        self.final_until = max(before, end)   # the last frame can run past the audio, its piece clipped
        return DiarUpdate(pieces, self.final_until)

    def _match(self, step: Step) -> dict[int, int]:
        """Local speaker -> speaker, for the active local speakers that aren't dropped (steps 3 and 4)."""
        config = self.config
        active = [k for k in range(len(step.embeddings))
                  if np.all(np.isfinite(step.embeddings[k])) and step.weight[k] >= config.min_active]
        cost = np.zeros((len(active), len(self.centroids)))
        for g, centroid in enumerate(self.centroids):
            unit = centroid / np.linalg.norm(centroid)
            for i, k in enumerate(active):
                cost[i, g] = 1.0 - float(np.dot(step.embeddings[k], unit))
        matched: dict[int, int] = {}
        if cost.size:
            for i, g in zip(*linear_sum_assignment(cost + TIE * np.arange(len(self.centroids)))):
                if cost[i, g] <= config.new_speaker:
                    matched[active[i]] = int(g)
        assigned = dict(matched)
        for i, k in enumerate(active):
            if k in assigned:
                continue
            if len(self.centroids) < config.max_speakers:
                assigned[k] = len(self.centroids)
                self.centroids.append(np.array(step.embeddings[k], dtype=float))
                continue
            free = [g for g in range(len(self.centroids)) if g not in assigned.values()]
            if free:
                assigned[k] = min(free, key=lambda g: (cost[i, g], g))
        for k, g in matched.items():
            if step.weight[k] >= config.min_update:
                self.centroids[g] = self.centroids[g] + step.embeddings[k]
        return assigned

    def _accumulate(self, step: Step, assigned: dict[int, int]) -> None:
        """Step 5: frames already final are left alone."""
        for row, frame in enumerate(step.frames):
            frame = int(frame)
            if frame < self._next:
                continue
            self._count[frame] = self._count.get(frame, 0) + 1
            sums = self._sums.setdefault(frame, {})
            for k, g in assigned.items():
                sums[g] = sums.get(g, 0.0) + float(step.activity[row, k])

    def _finalize(self, until: int) -> DiarUpdate:
        """Step 6: every grid frame that starts before `until` becomes final, and its accumulators go."""
        last = -(-until // self.frame_step) if until > 0 else 0   # the first frame starting at or after `until`
        runs: dict[int, list[list[int]]] = {}    # speaker -> runs of active frames, [first, last + 1]
        for frame in range(self._next, last):
            count = self._count.pop(frame, 0)
            sums = self._sums.pop(frame, {})
            for g, total in sums.items():
                if count and total / count >= 0.5:
                    speaker_runs = runs.setdefault(g, [])
                    if speaker_runs and speaker_runs[-1][1] == frame:
                        speaker_runs[-1][1] = frame + 1
                    else:
                        speaker_runs.append([frame, frame + 1])
        if last > self._next:
            self._next = last
            self.final_until = last * self.frame_step
        pieces = [Piece(first * self.frame_step, end * self.frame_step, g)
                  for g, speaker_runs in runs.items() for first, end in speaker_runs]
        return DiarUpdate(sorted(pieces, key=lambda p: (p.start, p.end, p.speaker)), self.final_until)


def replay(records: list[Step], config, frame_step: int, end: int) -> list[Piece]:
    """A fresh `Clusterer` over recorded steps, then `finish(end)`: the pieces a live run with `config` gives."""
    clusterer = Clusterer(config, frame_step)
    pieces: list[Piece] = []
    for step in records:
        pieces.extend(clusterer.step(step).pieces)
    pieces.extend(clusterer.finish(end).pieces)
    return pieces


def _frames(model):
    """The model's output frames, as a sliding window in seconds: its receptive field in pyannote.audio 3.1 and
    later, else its example output's frames."""
    if hasattr(model, "receptive_field"):
        return model.receptive_field
    return model.example_output.frames


class OnlineDiarizer:
    """The diarization stage: `feed` steps at the multiples of S it has passed, catching up when several are
    pending, and `flush` steps once at the end and makes everything final. With `record`, a list, each step's
    `Step` is appended to it, for `replay`."""

    def __init__(self, segmentation, embedding, config=DEFAULT_DIAR_CONFIG, *, record=None):
        diarize.configure_torch()
        with diarize._quiet():   # the imports' library warnings, before any worker thread starts
            from pyannote.audio.pipelines.speaker_verification import PretrainedSpeakerEmbedding
            from pyannote.audio.utils.powerset import Powerset

            self._embedding = PretrainedSpeakerEmbedding(embedding)
        self.config = config
        self.record = record
        self._segmentation = segmentation
        self._segmentation.eval()
        self._powerset = Powerset(LOCAL_SPEAKERS, 2)
        frames = _frames(segmentation)
        self._frame_step = frames.step * RATE
        self._center = (frames.start + frames.duration / 2) * RATE   # frame 0's centre, from the window's start
        self.frame_step = round(self._frame_step)                     # the absolute grid's, in samples
        self._clusterer = Clusterer(config, self.frame_step)
        self.position = 0
        self._stepped = 0                  # the last step's position (0 before the first)
        self._start = 0                    # the position of self._audio's first sample
        self._audio = np.zeros(0, dtype=np.float32)

    def feed(self, samples) -> DiarUpdate:
        samples = np.asarray(samples, dtype=np.float32)
        self._audio = np.concatenate([self._audio, samples])
        self.position += len(samples)
        config = self.config
        last = self.position // config.step * config.step
        if last <= self._stepped:
            return DiarUpdate([], self._clusterer.final_until)
        # Catching up: the last pending step, and earlier ones W - L apart, so that every frame is inside the
        # window of the step that makes it final.
        gap = max(config.step, (config.window - config.latency) // config.step * config.step)
        positions = list(range(last, self._stepped, -gap))[::-1]
        pieces: list[Piece] = []
        for p in positions:
            pieces.extend(self._step(p).pieces)
        self._stepped = last
        self._trim(self._stepped - config.window)   # flush's window, [end - W, end], starts after this
        return DiarUpdate(pieces, self._clusterer.final_until)

    def flush(self) -> DiarUpdate:
        end = self.position
        pieces = self._step(end).pieces if end > self._stepped else []
        self._stepped = end
        pieces = pieces + self._clusterer.finish(end).pieces
        self._trim(end)
        return DiarUpdate(pieces, self._clusterer.final_until)

    def _trim(self, start: int) -> None:
        """Drop the audio before `start`, which no later window reaches."""
        if start > self._start:
            self._audio = self._audio[start - self._start:]
            self._start = start

    def _step(self, p: int) -> DiarUpdate:
        step = self._local(p)
        if self.record is not None:
            self.record.append(step)
        return self._clusterer.step(step)

    def _local(self, p: int) -> Step:
        """Steps 1 and 2 at position p: the window's local activity and embeddings."""
        import torch

        config = self.config
        start = p - config.window
        window = np.zeros(config.window, dtype=np.float32)
        lo = max(start, self._start)
        window[lo - start:] = self._audio[lo - self._start:p - self._start]
        waveform = torch.from_numpy(window)[None, None]
        with torch.inference_mode():
            scores = self._segmentation(waveform)
            activity = self._powerset.to_multilabel(scores)[0].numpy().astype(float)   # (F, 3) 0/1
        # Each frame's grid index, by its centre; a frame centred before sample 0 is in the padding.
        first = math.floor((start + self._center) / self._frame_step)
        grid = first + np.arange(len(activity))
        real = grid >= 0
        activity[~real] = 0.0
        # Overlap-aware weights: a frame shared with another speaker weighs less than one held alone.
        exp = np.exp(config.beta * (activity - activity.max(axis=1, keepdims=True)))
        weights = activity ** config.gamma * (exp / exp.sum(axis=1, keepdims=True)) ** config.gamma
        weight = weights.sum(axis=0) * self._frame_step / RATE
        embeddings = np.full((LOCAL_SPEAKERS, self._embedding.dimension), np.nan)
        active = [k for k in range(LOCAL_SPEAKERS) if weight[k] >= config.min_active]
        if active:
            masks = torch.from_numpy(weights[:, active].T.astype(np.float32))
            result = np.asarray(self._embedding(waveform.repeat(len(active), 1, 1), masks=masks), dtype=float)
            for row, k in zip(result, active):
                norm = np.linalg.norm(row)
                if np.all(np.isfinite(row)) and norm > 0:
                    embeddings[k] = row / norm
        return Step(p, grid[real], activity[real], embeddings, weight)


class NullDiarizer:
    """--no-diarize: every sample is speaker 0, final at once."""

    def __init__(self):
        self.position = 0

    def feed(self, samples) -> DiarUpdate:
        start, self.position = self.position, self.position + len(samples)
        return DiarUpdate([Piece(start, self.position, 0)] if self.position > start else [], self.position)

    def flush(self) -> DiarUpdate:
        return DiarUpdate([], self.position)
