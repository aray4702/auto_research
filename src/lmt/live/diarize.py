"""Online diarization (design/internal/g2-stage1.md §2): Coria et al. (2021), as `diart` implements it. This is
the part that needs no model, steps 3-6: a `Clusterer` that matches one step's local speakers to the session's
speakers, keeps their centroids, accumulates activity on the absolute frame grid and makes it final. `replay`
runs it over recorded steps, so the thresholds can be tuned without the models. `NullDiarizer` is
`--no-diarize`'s stage. Positions are in samples at 16 kHz.
"""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

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


class NullDiarizer:
    """--no-diarize: every sample is speaker 0, final at once."""

    def __init__(self):
        self.position = 0

    def feed(self, samples) -> DiarUpdate:
        start, self.position = self.position, self.position + len(samples)
        return DiarUpdate([Piece(start, self.position, 0)] if self.position > start else [], self.position)

    def flush(self) -> DiarUpdate:
        return DiarUpdate([], self.position)
