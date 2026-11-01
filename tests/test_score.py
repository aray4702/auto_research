"""The harness's scoring: the normalizer, pooled WER and DER, and the figures (design/internal/g1-stage1.md §4).

The fixtures in tests/fixtures/eval/ are fixed references and hypotheses, so no model is needed. Every
expected figure is computed by hand below, never taken from what the code prints.
"""

import dataclasses
import json
from pathlib import Path

import pytest

from lmt.eval.score import (
    SYSTEMS,
    Hypothesis,
    Item,
    Turn,
    der,
    hypothesis_from_transcript,
    normalize,
    score,
    turns_from_rttm,
    wer,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eval"

# Made-up timings for the product's hypotheses: seconds, and peak RSS in bytes.
WALL = {"u1": 1.2, "u2": 1.0, "u3": 0.6, "u4": 0.4, "f1": 4.0, "f2": 2.0}
PEAK = {"u1": 1000, "u2": 2000, "u3": 1500, "u4": 1200, "f1": 3000, "f2": 2500}

KEYS = ["dataset", "system", "wer", "der", "audio_seconds", "wall_seconds", "rtf", "peak_rss_bytes"]


def transcript(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ls_items() -> list[Item]:
    """LS-ci's four utterances: references from a LibriSpeech-style .trans.txt, durations from the transcripts."""
    items = []
    for line in (FIXTURES / "ls" / "refs.trans.txt").read_text(encoding="utf-8").splitlines():
        uid, text = line.split(" ", 1)
        duration = transcript(FIXTURES / "ls" / f"{uid}.json")["source"]["duration"]
        items.append(Item("LS-ci", uid, Path(f"{uid}.flac"), duration, ref_text=text))
    return items


def ami_items() -> list[Item]:
    """AMI-ci's two files: references from RTTM, no words."""
    items = []
    for fid in ("f1", "f2"):
        duration = transcript(FIXTURES / "ami" / f"{fid}.json")["source"]["duration"]
        turns = turns_from_rttm((FIXTURES / "ami" / f"{fid}.rttm").read_text(encoding="utf-8"))
        items.append(Item("AMI-ci", fid, Path(f"{fid}.wav"), duration, ref_turns=turns))
    return items


def product(dataset_dir: str, item_id: str) -> Hypothesis:
    h = hypothesis_from_transcript(transcript(FIXTURES / dataset_dir / f"{item_id}.json"))
    return dataclasses.replace(h, wall_seconds=WALL[item_id], peak_rss_bytes=PEAK[item_id])


def all_hypotheses() -> dict:
    """The product on both sets; Whisper alone (text, no turns, no timing) on LS-ci; pyannote alone (turns,
    no text, no timing) on AMI-ci. Inserted out of order, so the figures' order comes from score."""
    hyps = {}
    for fid in ("f1", "f2"):
        h = product("ami", fid)
        hyps["pyannote-alone", "AMI-ci", fid] = Hypothesis(text=None, turns=h.turns)
        hyps["product", "AMI-ci", fid] = h
    for uid in ("u1", "u2", "u3", "u4"):
        h = product("ls", uid)
        hyps["asr-alone", "LS-ci", uid] = Hypothesis(text=h.text, turns=None)
        hyps["product", "LS-ci", uid] = h
    return hyps


# The design's table: each side's normalized words.
NORMALIZED = [
    ("THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG", "the quick brown fox jumps over the lazy dog"),
    ("The quick brown fox jumped over the lazy dog.", "the quick brown fox jumped over the lazy dog"),
    ("IT WAS TWENTY FIVE PAST TEN", "it was 25 past 10"),
    ("It was 25 past ten.", "it was 25 past 10"),
    ("I SAW HER", "i saw her"),
    ("", ""),
    ("GOOD MORNING", "good morning"),
    ("Good good morning.", "good good morning"),
]


@pytest.mark.parametrize(("text", "words"), NORMALIZED)
def test_the_pinned_normalizer_agrees_with_the_design_table(text, words):
    assert normalize(text).split() == words.split()


def test_wer_is_pooled_over_the_set():
    pairs = [(i.ref_text, product("ls", i.id).text) for i in ls_items()]
    # By hand, after normalizing: u1 1 substitution of 9 words; u2 0 of 5; u3 3 deletions of 3; u4 1 insertion
    # of 2. Pooled: (1 + 0 + 3 + 1) / (9 + 5 + 3 + 2) = 5 / 19 = 0.263158. The mean of per-file rates,
    # (1/9 + 0 + 1 + 1/2) / 4 = 0.402778, would fail this.
    assert wer(pairs) == pytest.approx(5 / 19)
    assert round(wer(pairs), 6) == 0.263158


def test_an_empty_hypothesis_counts_every_reference_word_as_deleted():
    assert wer([("I SAW HER", "")]) == 1.0  # 3 deletions of 3 words


def test_an_empty_normalized_reference_is_an_error():
    with pytest.raises(ValueError, match="reference 1 is empty once normalized"):
        wer([("GOOD MORNING", "good morning"), ("Um, hmm.", "")])  # "um" and "hmm" normalize away


def test_der_is_pooled_with_no_collar_and_overlap_scored():
    files = [(i.ref_turns, product("ami", i.id).turns, i.duration) for i in ami_items()]
    # By hand, with A as S1 and B as S2: f1 misses B in the overlap 7-8 s, 1 s of 11 s of reference speech
    # (A 4 + B 4 + A 3); f2 confuses B as S1 for 3-4 s, 1 s of 6 s. Pooled: 2 / 17 = 0.117647. With
    # skip_overlap=True f1 would score 0, and the mean of per-file rates would be (1/11 + 1/6) / 2 = 0.128788.
    assert der(files[:1]) == pytest.approx(1 / 11)
    assert der(files[1:]) == pytest.approx(1 / 6)
    assert der(files) == pytest.approx(2 / 17)
    assert round(der(files), 6) == 0.117647


def test_turns_from_rttm():
    text = (FIXTURES / "ami" / "f1.rttm").read_text(encoding="utf-8")
    assert turns_from_rttm(text) == (Turn(0.0, 4.0, "A"), Turn(4.0, 8.0, "B"), Turn(7.0, 10.0, "A"))


@pytest.mark.parametrize("bad", [
    "SPKR-INFO f1 1 <NA> <NA> <NA> unknown A <NA> <NA>",  # not a SPEAKER line
    "SPEAKER f1 1 4.00 4.00 <NA> <NA> B <NA>",  # 9 fields
    "SPEAKER f1 1 four 4.00 <NA> <NA> B <NA> <NA>",  # a start that isn't a number
    "",  # a blank line
])
def test_turns_from_rttm_names_a_bad_line(bad):
    text = f"SPEAKER f1 1 0.00 4.00 <NA> <NA> A <NA> <NA>\n{bad}\n"
    with pytest.raises(ValueError, match=r"RTTM line 2 ") as e:
        turns_from_rttm(text)
    assert repr(bad) in str(e.value)


def test_hypothesis_from_transcript():
    h = hypothesis_from_transcript(transcript(FIXTURES / "ami" / "f1.json"))
    assert h.text == "Shall we get started? Yes, the agenda first. Right, item one."
    assert h.turns == (Turn(0.0, 4.0, "S1"), Turn(4.0, 7.0, "S2"), Turn(7.0, 10.0, "S1"))
    assert (h.wall_seconds, h.peak_rss_bytes) == (None, None)
    empty = hypothesis_from_transcript(transcript(FIXTURES / "ls" / "u3.json"))
    assert (empty.text, empty.turns) == ("", ())


def test_score_gives_one_figure_per_dataset_and_system():
    figures = score(ls_items() + ami_items(), all_hypotheses())
    # By hand. LS-ci: WER 5/19 as above; no reference turns, so no DER; 3.0 + 2.5 + 1.5 + 1.0 = 8.0 s of
    # audio; the product's wall time 1.2 + 1.0 + 0.6 + 0.4 = 3.2 s, RTF 3.2 / 8.0 = 0.4, peak RSS 2000.
    # AMI-ci: DER 2/17 as above; no reference words, so no WER; 10.0 + 6.0 = 16.0 s of audio; the product's
    # wall time 4.0 + 2.0 = 6.0 s, RTF 6.0 / 16.0 = 0.375, peak RSS 3000. Whisper alone and pyannote alone
    # have no timing, so theirs are null.
    assert figures == [
        {"dataset": "LS-ci", "system": "product", "wer": 0.263158, "der": None,
         "audio_seconds": 8.0, "wall_seconds": 3.2, "rtf": 0.4, "peak_rss_bytes": 2000},
        {"dataset": "LS-ci", "system": "asr-alone", "wer": 0.263158, "der": None,
         "audio_seconds": 8.0, "wall_seconds": None, "rtf": None, "peak_rss_bytes": None},
        {"dataset": "AMI-ci", "system": "product", "wer": None, "der": 0.117647,
         "audio_seconds": 16.0, "wall_seconds": 6.0, "rtf": 0.375, "peak_rss_bytes": 3000},
        {"dataset": "AMI-ci", "system": "pyannote-alone", "wer": None, "der": 0.117647,
         "audio_seconds": 16.0, "wall_seconds": None, "rtf": None, "peak_rss_bytes": None},
    ]
    assert all(list(f) == KEYS for f in figures)
    assert SYSTEMS == ("product", "asr-alone", "pyannote-alone")


def test_one_missing_timing_makes_the_set_s_timing_null():
    hyps = all_hypotheses()
    hyps["product", "LS-ci", "u2"] = dataclasses.replace(hyps["product", "LS-ci", "u2"], wall_seconds=None)
    (figure,) = score(ls_items()[:2], {k: v for k, v in hyps.items() if k[0] == "product"})
    assert (figure["wall_seconds"], figure["rtf"], figure["peak_rss_bytes"]) == (None, None, 2000)


def test_hypotheses_for_some_items_but_not_all_is_an_error():
    hyps = all_hypotheses()
    del hyps["asr-alone", "LS-ci", "u3"]
    with pytest.raises(ValueError, match="LS-ci, asr-alone: hypotheses for 3 of 4 items"):
        score(ls_items(), hyps)
