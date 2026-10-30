"""G1-3 (spec/acceptance.md): the product run on the two-speaker clip gives exactly two speaker labels, and
each reference speaker's words carry one label, a different one for each (design/internal/g1-stage2.md, P5).
Words go to reference speakers by the clip's committed RTTM, by word time, independently of the product's code."""

import json
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest
from test_g1_1 import report

ROOT = Path(__file__).resolve().parents[1]
RTTM = ROOT / "tests" / "fixtures" / "two_speakers.rttm"

SHARE_BOUND = 0.90  # spec/acceptance.md G1-3: at least 90 % of a speaker's words carry their majority label


class Turn(NamedTuple):
    start: float
    end: float
    speaker: str  # the LibriSpeech speaker id


def read_rttm(path) -> list[Turn]:
    turns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if fields and fields[0] == "SPEAKER":
            start, duration = float(fields[3]), float(fields[4])
            turns.append(Turn(start, start + duration, fields[7]))
    return sorted(turns)


def reference_speaker(start, end, turns) -> str:
    """The turn holding the word's midpoint, or else the nearest turn by gap, the earlier on a tie."""
    mid = (start + end) / 2
    for turn in turns:
        if turn.start <= mid <= turn.end:
            return turn.speaker
    gaps = [(turn.start - mid if mid < turn.start else mid - turn.end, n) for n, turn in enumerate(turns)]
    return turns[min(gaps)[1]].speaker  # min on (gap, position): the earlier turn on a tie


def test_a_word_goes_to_the_turn_holding_its_midpoint_or_else_the_nearest():
    turns = [Turn(0.0, 2.0, "A"), Turn(3.0, 5.0, "B")]
    assert reference_speaker(1.5, 2.4, turns) == "A"  # midpoint 1.95, inside A
    assert reference_speaker(1.8, 3.4, turns) == "B"  # midpoint 2.6, between: B's 0.4 s is nearer than A's 0.6 s
    assert reference_speaker(2.0, 3.0, turns) == "A"  # midpoint 2.5, a tie: the earlier
    assert reference_speaker(5.5, 6.0, turns) == "B"  # after the last turn


@pytest.mark.models
@pytest.mark.criterion("G1-3")
def test_g1_3_the_two_speaker_clip_gets_two_labels_one_per_speaker(two_speakers_run):
    assert two_speakers_run.returncode == 0, two_speakers_run.stderr
    path = two_speakers_run.out_dir / "two_speakers.json"
    assert path.is_file(), two_speakers_run.stdout
    doc = json.loads(path.read_text(encoding="utf-8"))

    turns = read_rttm(RTTM)
    labels = {speaker: Counter() for speaker in dict.fromkeys(turn.speaker for turn in turns)}
    for segment in doc["segments"]:
        for word in segment.get("words", []):
            labels[reference_speaker(word["start"], word["end"], turns)][segment["speaker"]] += 1

    majority, shares = {}, {}
    for speaker, counts in labels.items():
        total = sum(counts.values())
        majority[speaker], top = counts.most_common(1)[0] if counts else (None, 0)
        shares[speaker] = top / total if total else 0.0
        report(f"G1-3: reference speaker {speaker}: {total} words, majority label {majority[speaker]} "
               f"on {shares[speaker]:.1%} (bound {SHARE_BOUND:.0%}), labels {dict(counts)}")
    ids = [speaker["id"] for speaker in doc["speakers"]]
    report(f"G1-3: the product run on tests/fixtures/two_speakers.wav: speakers {ids}, "
           f"{len(doc['segments'])} segments")

    assert len(ids) == 2, f"speakers: {doc['speakers']}"
    assert len({speaker["label"] for speaker in doc["speakers"]}) == 2, f"speakers: {doc['speakers']}"
    assert {segment["speaker"] for segment in doc["segments"]} == set(ids), "the segments don't use both speakers"
    for speaker, counts in labels.items():
        assert counts, f"reference speaker {speaker} has no words"
        assert shares[speaker] >= SHARE_BOUND, f"reference speaker {speaker}: {shares[speaker]:.1%} {dict(counts)}"
    assert len(set(majority.values())) == len(majority), f"the speakers' majority labels are the same: {majority}"
