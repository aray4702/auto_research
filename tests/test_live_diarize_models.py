"""G2-3's speaker rule on time, on the models (design/internal/g2-stage1.md §2, item 2c): the two-speaker clip in
lockstep (`FileSource(paced=False)`), online diarization alone. The final pieces hold exactly two speakers. For each
reference speaker in the clip's RTTM, only the part of their turns' time that some final piece covers counts: at
least 90 % of it is on one speaker, a different one for each (purity), and it is at least 60 % of the speaker's RTTM
time (coverage). G2-3 itself, on words, is 3a4's. The clip's DER and each speaker's covered and majority seconds
are printed too."""

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

PURITY_BOUND = 0.90  # at least 90 % of a reference speaker's covered time is on one speaker
COVERAGE_BOUND = 0.60  # a reference speaker's covered time is at least 60 % of their RTTM time


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
def test_g2_3_online_diarization_labels_each_speakers_covered_time_with_one_speaker(monkeypatch):
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

    everyone = merged(t for times in hyp.values() for t in times)
    majority, purity, coverage = {}, {}, {}
    for speaker, times in ref.items():
        total = sum(end - start for start, end in times)
        covered = overlap(times, everyone)  # the speaker's turn time some final piece covers
        on = {g: overlap(times, hyp_times) for g, hyp_times in hyp.items()}
        majority[speaker], top = max(on.items(), key=lambda kv: (kv[1], -kv[0])) if on else (None, 0.0)
        purity[speaker] = top / covered if covered else 0.0
        coverage[speaker] = covered / total
        report(f"G2-3 (on time): reference speaker {speaker}: {total:.3f} s in the RTTM, {covered:.3f} s covered "
               f"({coverage[speaker]:.1%}, bound {COVERAGE_BOUND:.0%}), majority speaker {majority[speaker]} on "
               f"{top:.3f} s ({purity[speaker]:.1%} of covered, bound {PURITY_BOUND:.0%}), "
               f"per speaker { {g: round(s, 3) for g, s in on.items()} }")
    hyp_turns = [Turn(p.start / RATE, p.end / RATE, str(p.speaker)) for p in pieces]
    report(f"G2-3 (on time): online diarization of tests/fixtures/two_speakers.wav in lockstep: "
           f"{len(pieces)} pieces, speakers {sorted(hyp)}, DER {der([(turns, hyp_turns, duration)]):.1%}")

    assert len(hyp) == 2, f"speakers: {sorted(hyp)}"
    for speaker in ref:
        assert purity[speaker] >= PURITY_BOUND, f"reference speaker {speaker}: purity {purity[speaker]:.1%}"
        assert coverage[speaker] >= COVERAGE_BOUND, f"reference speaker {speaker}: coverage {coverage[speaker]:.1%}"
    assert len(set(majority.values())) == len(majority), f"the speakers' majority speakers are the same: {majority}"
