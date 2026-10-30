"""`lmt eval --set live`'s measures: matching committed words to the reference, their delays, nearest-rank
percentiles and reading a run's event log (design/internal/g2-stage1.md §5, item 5a), as spec/acceptance.md
G2 "Terms" defines them.

jiwer is imported inside `match_words`, as `score` imports it, so this module imports without the `eval` extra.
"""

import math
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

from lmt.errors import InputError
from lmt.transcript import Word


class RefWord(NamedTuple):  # a reference word with its times; datasets' triples are RefWord(*w)
    start: float
    end: float
    text: str


def key(text: str) -> str:
    """The form both sides are matched in: casefolded, Unicode punctuation (category P) dropped, stripped."""
    folded = text.casefold()
    return "".join(c for c in folded if not unicodedata.category(c).startswith("P")).strip()


def match_words(ref: Sequence[RefWord], hyp: Sequence[tuple[Word, float]]) -> list[tuple[RefWord, float]]:
    """Each matched word, as its reference word with its committed word's time, in reference order: both sides
    keyed, empty keys dropped, and every `equal` chunk of jiwer's alignment pairs a reference word with a
    committed word."""
    import jiwer

    ref_kept = [(w, k) for w in ref if (k := key(w.text))]
    hyp_kept = [(t, k) for w, t in hyp if (k := key(w.text))]
    if not ref_kept or not hyp_kept:
        return []
    out = jiwer.process_words(" ".join(k for _, k in ref_kept), " ".join(k for _, k in hyp_kept))
    matches = []
    for chunk in out.alignments[0]:
        if chunk.type != "equal":
            continue
        refs = range(chunk.ref_start_idx, chunk.ref_end_idx)
        hyps = range(chunk.hyp_start_idx, chunk.hyp_end_idx)
        matches.extend((ref_kept[r][0], hyp_kept[h][0]) for r, h in zip(refs, hyps))
    return matches


def delays(matches: Sequence[tuple[RefWord, float]]) -> list[float]:
    """Each matched word's delay: its committed word's time minus its reference word's end."""
    return [t - ref.end for ref, t in matches]


def percentile(values: Sequence[float], q: float) -> float:
    """The nearest-rank percentile: the ⌈q·n⌉-th smallest of the n values, for 0 < q ≤ 1."""
    if not values:
        raise ValueError("no values")
    if not 0 < q <= 1:
        raise ValueError(f"q must be in (0, 1], not {q}")
    rank = math.ceil(round(q * len(values), 9))  # rounded first, so 0.07 · 100 is rank 7, not 8
    return sorted(values)[rank - 1]


@dataclass(frozen=True)
class RunLog:  # one run of `lmt live --jsonl`
    lines: list[tuple[float, dict]]  # (arrival in seconds from the start event's arrival, event)
    wall_seconds: float
    transcript: dict


def read_events(lines: Sequence[tuple[float, dict]]) -> tuple[list[tuple[Word, float]], float]:
    """Each committed word with its event's arrival, and `behind_seconds`: the stop event's arrival minus its `t`.
    A stream that doesn't start with `start`, doesn't end with `stop`, or whose `t` falls is an InputError."""
    if not lines or lines[0][1].get("type") != "start":
        raise InputError("lmt live", "the event stream doesn't start with a start event")
    if lines[-1][1].get("type") != "stop":
        raise InputError("lmt live", "the event stream doesn't end with a stop event")
    words = []
    previous = None
    for n, (arrival, event) in enumerate(lines, start=1):
        t = event["t"]
        if previous is not None and t < previous:
            raise InputError("lmt live", f"event {n}'s t {t} falls below the previous event's {previous}")
        previous = t
        if event["type"] == "words":
            for w in event["words"]:
                words.append((Word(w["start"], w["end"], w["text"], w["probability"]), arrival))
    stop_arrival, stop = lines[-1]
    return words, stop_arrival - stop["t"]
