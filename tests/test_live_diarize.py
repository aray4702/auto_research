"""Online diarization's clustering, on scripted steps and no model (design/internal/g2-stage1.md §2, item 2a)."""

import dataclasses
import random
import unittest

import numpy as np

from lmt.live import diarize
from lmt.live.diarize import Clusterer, DiarConfig, NullDiarizer, Piece, Step

CONFIG = diarize.DEFAULT_DIAR_CONFIG
FS = 1600  # a frame step of 0.1 s, so frame f starts at f * FS
A, B, C, E = np.eye(4)  # E is the direction `near` moves toward, which no speaker uses


def near(distance, base=A):
    """A unit vector at `distance` (cosine) from `base`."""
    c = 1.0 - distance
    return c * base + np.sqrt(1.0 - c * c) * E


def unit(*v):
    v = np.array(v, dtype=float)
    return v / np.linalg.norm(v)


def scripted(position, embeddings, *, weight=None, frames=None, activity=None):
    """A Step with up to three local speakers (None: inactive). By default local speaker k is active alone on
    frame `position // FS + k`, so the speaker it was given shows on that frame."""
    rows = list(embeddings) + [None] * (3 - len(embeddings))
    if weight is None:
        weight = [0.0 if e is None else 2.0 for e in rows]
    if frames is None:
        frames, activity = position // FS + np.arange(3), np.eye(3)
    return Step(position, np.asarray(frames), np.asarray(activity, dtype=float),
                np.array([np.full(4, np.nan) if e is None else e for e in rows]), np.asarray(weight, dtype=float))


def window(position, rows, config=CONFIG, frame_step=FS):
    """A Step as a window [p - W, p] gives it: every grid frame starting in it, local speaker k active on all of
    them with embedding rows[k]."""
    frames = np.arange(max(0, position - config.window) // frame_step, -(-position // frame_step))
    activity = np.zeros((len(frames), 3))
    activity[:, :len(rows)] = 1.0
    return scripted(position, rows, frames=frames, activity=activity)


def run(steps, config=CONFIG, end=None):
    """The clusterer after the steps and `finish`, and each frame's speakers."""
    clusterer = Clusterer(config, FS)
    pieces = [p for s in steps for p in clusterer.step(s).pieces]
    pieces += clusterer.finish(end or max(s.position for s in steps) + 8 * FS).pieces
    labels = {}
    for p in pieces:
        for frame in range(p.start // FS, -(-p.end // FS)):
            labels.setdefault(frame, set()).add(p.speaker)
    return clusterer, labels


class ConfigTest(unittest.TestCase):
    def test_the_provisional_settings(self):
        self.assertEqual(CONFIG, DiarConfig(step=8000, window=80000, latency=16000, min_active=0.5,
                                            min_update=1.0, new_speaker=0.70, gamma=3.0, beta=10.0,
                                            max_speakers=20))


class MatchTest(unittest.TestCase):
    def test_a_local_speaker_near_a_centroid_is_matched(self):
        clusterer, labels = run([scripted(0, [A]), scripted(8000, [near(0.6)])])
        self.assertEqual((labels[0], labels[5]), ({0}, {0}))
        self.assertEqual(len(clusterer.centroids), 1)

    def test_one_beyond_new_speaker_becomes_new(self):
        clusterer, labels = run([scripted(0, [A]), scripted(8000, [near(0.75)])])
        self.assertEqual((labels[0], labels[5]), ({0}, {1}))
        np.testing.assert_array_equal(clusterer.centroids[1], near(0.75))

    def test_two_local_speakers_never_take_one_speaker(self):
        clusterer, labels = run([scripted(0, [A]), scripted(8000, [near(0.1), near(0.2)])])
        self.assertEqual((labels[5], labels[6]), ({0}, {1}))  # the nearer keeps it, the other is new
        self.assertEqual(len(clusterer.centroids), 2)

    def test_at_max_speakers_a_new_one_takes_the_nearest_free_or_is_dropped(self):
        config = dataclasses.replace(CONFIG, max_speakers=2)
        x = unit(0.2, 0.1, 1, 0)  # beyond new_speaker from both, nearer A (0.80) than B (0.90)
        clusterer, labels = run([
            scripted(0, [A, B]),
            scripted(8000, [x]),                                  # the nearest, A
            scripted(16000, [near(0.1, A), x]),                   # A is taken: B
            scripted(24000, [near(0.1, A), near(0.1, B), x]),     # none free: dropped
        ], config)
        self.assertEqual((labels[0], labels[1]), ({0}, {1}))
        self.assertEqual(labels[5], {0})
        self.assertEqual((labels[10], labels[11]), ({0}, {1}))
        self.assertEqual((labels[15], labels[16]), ({0}, {1}))
        self.assertNotIn(17, labels)
        self.assertEqual(len(clusterer.centroids), 2)

    def test_ties_go_to_the_lower_index(self):
        _, labels = run([scripted(0, [A, B]), scripted(8000, [unit(1, 1, 0, 0)])])  # 0.29 from both
        self.assertEqual(labels[5], {0})
        config = dataclasses.replace(CONFIG, max_speakers=2)
        _, labels = run([scripted(0, [A, B]), scripted(8000, [C])], config)  # 1.0 from both, at max
        self.assertEqual(labels[5], {0})

    def test_an_inactive_local_speaker_adds_nothing(self):
        clusterer, labels = run([scripted(0, [A, None, B], weight=[2.0, 0.0, 0.4])])
        self.assertEqual(labels, {0: {0}})  # NaN, and below min_active
        self.assertEqual(len(clusterer.centroids), 1)

    def test_below_min_update_the_centroid_is_unchanged(self):
        clusterer = Clusterer(CONFIG, FS)
        clusterer.step(scripted(0, [A]))
        clusterer.step(scripted(8000, [near(0.3)], weight=[0.9, 0, 0]))
        np.testing.assert_array_equal(clusterer.centroids[0], A)
        clusterer.step(scripted(16000, [near(0.3)], weight=[1.0, 0, 0]))
        np.testing.assert_allclose(clusterer.centroids[0], A + near(0.3))


class FinalizeTest(unittest.TestCase):
    def test_each_step_makes_exactly_the_frames_before_p_minus_l_final(self):
        frame_step = 270  # segmentation-3.0's, so p - L doesn't fall on a frame
        clusterer = Clusterer(CONFIG, frame_step)
        previous = 0
        for p in range(8000, 80001, 8000):
            update = clusterer.step(window(p, [A], frame_step=frame_step))
            if p <= CONFIG.latency:
                self.assertEqual(update, diarize.DiarUpdate([], 0))
                continue
            # The last final frame starts before p - L, the next doesn't.
            self.assertLess(update.final_until - frame_step, p - CONFIG.latency)
            self.assertGreaterEqual(update.final_until, p - CONFIG.latency)
            self.assertEqual(update.final_until % frame_step, 0)
            self.assertEqual(update.pieces, [Piece(previous, update.final_until, 0)])
            # The accumulators before final_until are gone.
            self.assertTrue(all(f * frame_step >= update.final_until for f in clusterer._count))
            self.assertTrue(all(f * frame_step >= update.final_until for f in clusterer._sums))
            previous = update.final_until

    def test_active_when_the_mean_over_covering_windows_is_at_least_half(self):
        config = dataclasses.replace(CONFIG, latency=10**6)  # nothing final until finish
        on = [[1, 1, 1, 1], [1, 0, 1, 1], [0, 0, 1, 1], [0, 0, 0, 1]]  # frames 0-3, four windows
        steps = [scripted(8000 * (i + 1), [A], frames=np.arange(4), activity=np.array([row, [0] * 4, [0] * 4]).T)
                 for i, row in enumerate(on)]
        clusterer = Clusterer(config, FS)
        self.assertEqual([clusterer.step(s) for s in steps], [diarize.DiarUpdate([], 0)] * 4)
        # Frame 0 is on in 2 of 4 windows, frame 1 in 1, frames 2 and 3 in 3 and 4.
        self.assertEqual(clusterer.finish(4 * FS), diarize.DiarUpdate([Piece(0, FS, 0), Piece(2 * FS, 4 * FS, 0)],
                                                                      4 * FS))

    def test_a_final_frame_never_changes(self):
        clusterer = Clusterer(CONFIG, FS)
        first = clusterer.step(window(32000, [A]))
        self.assertEqual(first, diarize.DiarUpdate([Piece(0, 16000, 0)], 16000))
        # Later windows cover frames 0-9 with a different speaker, and A silent.
        later = [clusterer.step(window(p, [B])) for p in (40000, 48000)]
        self.assertEqual(min(clusterer._count), 20)  # frames 0-9 were never accumulated again
        later.append(clusterer.finish(48000))
        pieces = [p for u in later for p in u.pieces]
        self.assertTrue(all(p.start >= 16000 for p in pieces))
        self.assertEqual(later[-1].final_until, 48000)
        # Frames 10-14, final at 40000, are A's in 1 of their 2 windows and B's in the other: both are on.
        self.assertEqual(later[0], diarize.DiarUpdate([Piece(16000, 24000, 0), Piece(16000, 24000, 1)], 24000))

    def test_finish_makes_everything_final(self):
        frame_step = 270
        clusterer = Clusterer(CONFIG, frame_step)
        for p in range(8000, 40001, 8000):
            clusterer.step(window(p, [A], frame_step=frame_step))
        before = clusterer.final_until
        update = clusterer.finish(40000)
        self.assertEqual(update, diarize.DiarUpdate([Piece(before, 40000, 0)], 40000))  # the last frame clipped
        self.assertEqual((clusterer._count, clusterer._sums), ({}, {}))


def random_steps(seed, n=30):
    rng = random.Random(seed)
    speakers = [unit(*[rng.gauss(0, 1) for _ in range(4)]) for _ in range(4)]
    steps = []
    for i in range(1, n + 1):
        p = 8000 * i
        frames = np.arange(max(0, p - 80000) // FS, p // FS)
        rows = [None if rng.random() < 0.3 else unit(*(rng.choice(speakers) + [rng.gauss(0, 0.2) for _ in range(4)]))
                for _ in range(3)]
        activity = np.array([[float(rng.random() < 0.6) for _ in range(3)] for _ in frames])
        weight = [rng.uniform(0.0, 3.0) for _ in range(3)]
        steps.append(scripted(p, rows, weight=weight, frames=frames, activity=activity))
    return steps


class ReplayTest(unittest.TestCase):
    def test_replay_is_the_clusterer_over_the_steps(self):
        steps = random_steps(1)
        pieces = diarize.replay(steps, CONFIG, FS, 240000)
        self.assertEqual(pieces, diarize.replay(steps, CONFIG, FS, 240000))
        clusterer = Clusterer(CONFIG, FS)
        live = [p for s in steps for p in clusterer.step(s).pieces] + clusterer.finish(240000).pieces
        self.assertEqual(pieces, live)
        self.assertGreater(len({p.speaker for p in pieces}), 1)

    def test_replay_with_other_thresholds_differs(self):
        steps = random_steps(2)
        strict = dataclasses.replace(CONFIG, new_speaker=1e-6)
        self.assertGreater(len({p.speaker for p in diarize.replay(steps, strict, FS, 240000)}),
                           len({p.speaker for p in diarize.replay(steps, CONFIG, FS, 240000)}))


class NullDiarizerTest(unittest.TestCase):
    def test_every_sample_is_speaker_0_final_at_once(self):
        null = NullDiarizer()
        self.assertEqual(null.feed(np.zeros(1600, dtype=np.float32)), diarize.DiarUpdate([Piece(0, 1600, 0)], 1600))
        self.assertEqual(null.feed(np.zeros(4000, dtype=np.float32)),
                         diarize.DiarUpdate([Piece(1600, 5600, 0)], 5600))
        self.assertEqual(null.feed(np.zeros(0, dtype=np.float32)), diarize.DiarUpdate([], 5600))
        self.assertEqual(null.flush(), diarize.DiarUpdate([], 5600))


if __name__ == "__main__":
    unittest.main()
