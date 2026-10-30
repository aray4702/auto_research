"""G2-3's speaker rule on time, on the models (design/internal/g2-stage1.md §2, item 2c): the two-speaker clip in
lockstep (`FileSource(paced=False)`), online diarization alone. The final pieces hold exactly two speakers, and
each reference speaker in the clip's RTTM has at least 90 % of their speech time covered by one speaker, a
different one for each. G2-3 itself, on words, is 3a4's. The clip's DER is printed too."""

from pathlib import Path

import pytest
from test_g1_1 import report

from lmt import diarize, models
from lmt.eval.score import Turn, der, turns_from_rttm
from lmt.live.diarize import RATE, OnlineDiarizer
from lmt.live.source import FileSource, lockstep

ROOT = Path(__file__).resolve().parents[1]
TWO_SPEAKERS = ROOT / "tests" / "fixtures" / "two_speakers.wav"
RTTM = ROOT / "tests" / "fixtures" / "two_speakers.rttm"

SHARE_BOUND = 0.90  # at least 90 % of a reference speaker's speech time is covered by one speaker


def merged(intervals) -> list[tuple[float, float]]:
    """The intervals' union, as sorted, disjoint (start, end) pairs."""
    out: list[list[float]] = []
    for start, end in sorted(intervals):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(start, end) for start, end in out]


def overlap(a, b) -> float:
    """The time two unions of intervals share."""
    return sum(max(0.0, min(e1, e2) - max(s1, s2)) for s1, e1 in a for s2, e2 in b)


def test_overlap_counts_shared_time_once_per_union():
    a = merged([(0.0, 2.0), (1.0, 3.0), (5.0, 6.0)])
    assert a == [(0.0, 3.0), (5.0, 6.0)]
    assert overlap(a, merged([(2.5, 5.5)])) == pytest.approx(1.0)
    assert overlap(a, []) == 0.0


@pytest.mark.models
@pytest.mark.criterion("G2-3")
def test_g2_3_online_diarization_covers_each_speakers_time_with_one_speaker(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")  # so teardown undoes ensure()'s
    manifest = models.load_manifest()
    dirs = models.ensure([manifest.get(name) for name in (diarize.SEGMENTATION, diarize.EMBEDDING)])
    diarize.scratch_matplotlib()
    diarizer = OnlineDiarizer(diarize.load_segmentation(Path(dirs[diarize.SEGMENTATION])),
                              diarize.load_embedding(Path(dirs[diarize.EMBEDDING])))
    source = FileSource(TWO_SPEAKERS, paced=False)

    pieces, final_until = [], 0
    for _, (update,) in lockstep(source, diarizer):
        pieces.extend(update.pieces)
        final_until = update.final_until
    duration = source.position / RATE
    assert final_until == source.position, "the flush didn't make every frame final"
    assert all(0 <= p.start < p.end <= source.position for p in pieces)

    hyp = {}
    for p in pieces:
        hyp.setdefault(p.speaker, []).append((p.start / RATE, p.end / RATE))
    hyp = {speaker: merged(times) for speaker, times in hyp.items()}
    turns = turns_from_rttm(RTTM.read_text(encoding="utf-8"))
    ref = {}
    for turn in turns:
        ref.setdefault(turn.speaker, []).append((turn.start, turn.end))
    ref = {speaker: merged(times) for speaker, times in ref.items()}

    majority, shares = {}, {}
    for speaker, times in ref.items():
        total = sum(end - start for start, end in times)
        covered = {g: overlap(times, hyp_times) for g, hyp_times in hyp.items()}
        majority[speaker], top = max(covered.items(), key=lambda kv: (kv[1], -kv[0])) if covered else (None, 0.0)
        shares[speaker] = top / total
        report(f"G2-3 (on time): reference speaker {speaker}: {total:.3f} s of speech, majority speaker "
               f"{majority[speaker]} on {shares[speaker]:.1%} (bound {SHARE_BOUND:.0%}), "
               f"covered { {g: round(s, 3) for g, s in covered.items()} }")
    hyp_turns = [Turn(p.start / RATE, p.end / RATE, str(p.speaker)) for p in pieces]
    report(f"G2-3 (on time): online diarization of tests/fixtures/two_speakers.wav in lockstep: "
           f"{len(pieces)} pieces, speakers {sorted(hyp)}, DER {der([(turns, hyp_turns, duration)]):.1%}")

    assert len(hyp) == 2, f"speakers: {sorted(hyp)}"
    for speaker in ref:
        assert shares[speaker] >= SHARE_BOUND, f"reference speaker {speaker}: {shares[speaker]:.1%}"
    assert len(set(majority.values())) == len(majority), f"the speakers' majority speakers are the same: {majority}"
