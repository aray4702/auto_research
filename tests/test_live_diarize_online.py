"""Online diarization's steps on fake models: segmentation, overlap-aware embeddings, when it steps and catching
up, `flush` and `record` (design/internal/g2-stage1.md §2, item 2b)."""

import types
import unittest
from unittest import mock

import numpy as np
import torch
from pyannote.audio.utils.powerset import Powerset

import lmt.diarize
from lmt.live import diarize
from lmt.live.diarize import OnlineDiarizer, Piece

CONFIG = diarize.DEFAULT_DIAR_CONFIG
W = CONFIG.window
FS = 1600                        # the fakes' frame step, 0.1 s: frame i of a window starting at s is centred at
CENTRE = 800                     # s + 800 + i * 1600, so it is grid frame s // 1600 + i
SPEAKERS = np.eye(4)             # speaker j's embedding
CLASSES = {frozenset(np.flatnonzero(row).tolist()): c for c, row in enumerate(Powerset(3, 2).mapping.numpy())}


def audio(n, start=0):
    """Sample t holds t + 1, so a fake reads a window's position from its last sample, and padding is 0."""
    return np.arange(start + 1, start + n + 1, dtype=np.float32)


def centres(waveform):
    """The absolute centre of each frame of a window, read from its audio."""
    p = int(waveform[0, -1])
    return p - W + CENTRE + FS * np.arange(W // FS)


class FakeSegmentation:
    """Speakers `script(t)` are active at sample t (padding included), local speaker k being speaker k."""

    def __init__(self, script, log, frames="receptive_field"):
        self.script, self.log = script, log
        if frames == "receptive_field":
            self.receptive_field = types.SimpleNamespace(start=-0.05, duration=0.2, step=0.1)
        else:
            self.example_output = types.SimpleNamespace(frames=types.SimpleNamespace(start=0.0, duration=0.1,
                                                                                      step=0.1))

    def eval(self):
        self.log.append("eval")

    def __call__(self, waveform):
        self.log.append("segment")
        scores = torch.full((1, W // FS, 7), -10.0)
        for i, t in enumerate(centres(waveform[0])):
            scores[0, i, CLASSES[frozenset(self.script(t))]] = 0.0
        return scores


class FakeEmbedding:
    """The embedding of the speaker the mask weighs most, by `script`; NaN with `nan`."""

    dimension = 4

    def __init__(self, script, nan=False):
        self.script, self.nan = script, nan
        self.masks = []

    def __call__(self, waveforms, masks):
        rows = []
        for waveform, mask in zip(waveforms, masks.numpy()):
            self.masks.append(mask)
            totals = np.zeros(len(SPEAKERS))
            for t, w in zip(centres(waveform), mask):
                for j in self.script(t):
                    totals[j] += w
            rows.append(np.full(4, np.nan) if self.nan else 2.0 * SPEAKERS[int(np.argmax(totals))])
        return np.array(rows)


def make(script, *, nan=False, frames="receptive_field", record=None, log=None):
    log = [] if log is None else log
    segmentation, embedding = FakeSegmentation(script, log, frames), FakeEmbedding(script, nan)
    with mock.patch.object(lmt.diarize, "configure_torch", lambda: log.append("configure")), \
            mock.patch("pyannote.audio.pipelines.speaker_verification.PretrainedSpeakerEmbedding", lambda m: m):
        return OnlineDiarizer(segmentation, embedding, CONFIG, record=record), embedding


def in_blocks(diarizer, n, block=1600):
    """Feed n samples a block at a time, as lockstep does: each block's update."""
    return [diarizer.feed(audio(min(block, n - s), s)) for s in range(0, n, block)]


def speech(t):
    return (0,)


def frames_of(pieces, speaker=0):
    return {f for p in pieces if p.speaker == speaker for f in range(p.start // FS, -(-p.end // FS))}


class SetupTest(unittest.TestCase):
    def test_configure_torch_runs_first(self):
        log = []
        diarizer, _ = make(speech, log=log)
        in_blocks(diarizer, 16000)
        self.assertEqual(log[0], "configure")
        self.assertIn("segment", log)

    def test_frames_from_example_output_when_there_is_no_receptive_field(self):
        records = []
        for frames in ("receptive_field", "example_output"):
            records.append([])
            diarizer, _ = make(speech, frames=frames, record=records[-1])
            self.assertEqual(diarizer.frame_step, FS)
            in_blocks(diarizer, 24000)
        self.assertEqual(len(records[0]), 3)
        for a, b in zip(*records):
            np.testing.assert_array_equal(a.frames, b.frames)
            np.testing.assert_array_equal(a.activity, b.activity)


class EmbedTest(unittest.TestCase):
    def test_a_frame_shared_by_two_speakers_weighs_less_than_one_held_alone(self):
        def script(t):
            return (0, 1) if 32000 <= t < 48000 else (0,)   # speaker 1 joins from 2 to 3 s

        record = []
        diarizer, embedding = make(script, record=record)
        in_blocks(diarizer, W)
        step = record[-1]
        mask = embedding.masks[-1]                 # local speaker 0's, in the window [0, 5 s]
        alone, shared = mask[10], mask[25]         # frames at 1 s and 2.5 s
        self.assertGreater(alone, 0.99)
        self.assertGreater(shared, 0.0)
        self.assertLess(shared, 0.5 * alone)
        np.testing.assert_allclose(step.weight[0], mask.sum() * 0.1, rtol=1e-6)
        # Speaker 1's 1 s, all of it shared, weighs less than min_active: not embedded.
        self.assertLess(step.weight[1], CONFIG.min_active)
        self.assertTrue(np.all(np.isnan(step.embeddings[1])))
        np.testing.assert_allclose(step.embeddings[0], SPEAKERS[0])   # L2-normalised

    def test_a_nan_embedding_makes_the_speaker_inactive(self):
        record = []
        diarizer, _ = make(speech, nan=True, record=record)
        updates = in_blocks(diarizer, W)
        self.assertTrue(np.all(np.isnan(record[-1].embeddings)))
        self.assertGreaterEqual(record[-1].weight[0], CONFIG.min_active)
        self.assertEqual([p for u in updates for p in u.pieces], [])


class PaddingTest(unittest.TestCase):
    def test_frames_in_the_padding_reach_no_step(self):
        record = []
        diarizer, embedding = make(speech, record=record)   # active in the padding too
        in_blocks(diarizer, 16000)
        self.assertEqual([s.position for s in record], [8000, 16000])
        np.testing.assert_array_equal(record[0].frames, np.arange(5))
        np.testing.assert_array_equal(record[1].frames, np.arange(10))
        self.assertEqual(record[1].activity.shape, (10, 3))
        np.testing.assert_allclose(record[1].weight[0], 10 * embedding.masks[-1][-1] * 0.1, rtol=1e-6)
        # The embedding's mask is 0 on the 40 frames of padding.
        self.assertEqual(embedding.masks[-1][:40].tolist(), [0.0] * 40)
        self.assertTrue(np.all(embedding.masks[-1][40:] > 0.99))


class FinalizeTest(unittest.TestCase):
    def test_nothing_is_final_before_l_plus_s(self):
        diarizer, _ = make(speech)
        updates = in_blocks(diarizer, 32000)
        for position, update in zip(range(1600, 32001, 1600), updates):
            if position < CONFIG.latency + CONFIG.step:
                self.assertEqual(update, diarize.DiarUpdate([], 0))
        self.assertEqual(updates[14], diarize.DiarUpdate([Piece(0, 8000, 0)], 8000))   # at 24000

    def test_in_lockstep_it_steps_at_every_multiple_of_s(self):
        record = []
        diarizer, _ = make(speech, record=record)
        in_blocks(diarizer, 40000 + 800)
        self.assertEqual([s.position for s in record], [8000, 16000, 24000, 32000, 40000])


class CatchUpTest(unittest.TestCase):
    def test_12_s_at_once_steps_at_4_8_and_12_s(self):
        record = []
        diarizer, _ = make(speech, record=record)
        update = diarizer.feed(audio(192000))
        self.assertEqual([s.position for s in record], [64000, 128000, 192000])
        self.assertEqual(update.final_until, 176000)   # every frame before 11 s
        self.assertEqual(frames_of(update.pieces), set(range(110)))
        covered = {int(f) for s in record for f in s.frames}
        self.assertTrue(set(range(110)) <= covered)
        # Each frame is inside the window of the step that makes it final: [0, 3 s), [3, 7 s) and [7, 11 s).
        for s, made_final in zip(record, (0, 48000, 112000)):
            self.assertLessEqual(max(0, s.position - W), made_final)
        # Then it goes on stepping at each multiple of S.
        diarizer.feed(audio(8000, 192000))
        self.assertEqual(record[-1].position, 200000)


class FlushTest(unittest.TestCase):
    def test_flush_makes_everything_final(self):
        record = []
        diarizer, _ = make(speech, record=record)
        updates = in_blocks(diarizer, 52800)
        update = diarizer.flush()
        self.assertEqual(record[-1].position, 52800)   # once, at the end
        self.assertEqual(len(record), 7)
        self.assertEqual(update.final_until, 52800)
        pieces = [p for u in updates + [update] for p in u.pieces]
        self.assertEqual(frames_of(pieces), set(range(33)))
        self.assertEqual(max(p.end for p in pieces), 52800)

    def test_flush_on_a_step_doesnt_step_again(self):
        record = []
        diarizer, _ = make(speech, record=record)
        in_blocks(diarizer, 48000)
        update = diarizer.flush()
        self.assertEqual(len(record), 6)
        self.assertEqual(update.final_until, 48000)


class RecordTest(unittest.TestCase):
    def test_replay_of_the_record_gives_the_live_pieces(self):
        def script(t):
            return (1,) if 48000 <= t < 96000 else (0,)   # speaker 1 from 3 to 6 s

        record = []
        diarizer, _ = make(script, record=record)
        updates = in_blocks(diarizer, 148800)
        updates.append(diarizer.flush())
        live = [p for u in updates for p in u.pieces]
        self.assertEqual({p.speaker for p in live}, {0, 1})
        self.assertEqual(frames_of(live, 1), set(range(30, 60)))
        self.assertEqual(diarize.replay(record, CONFIG, diarizer.frame_step, diarizer.position), live)


if __name__ == "__main__":
    unittest.main()
