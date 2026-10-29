"""The harness's scoring: WER, DER and RTF over a set (design/internal/g1-stage1.md §4), as
spec/acceptance.md G1 "Terms" defines them.

jiwer, pyannote and the vendored normalizer (which needs regex and more-itertools) are imported only
inside the functions that use them, so this module imports without the `eval` extra: `lmt demo --sample`
uses `lmt.eval.datasets`, which imports these types (design/internal/g3-stage1.md).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path


@dataclass(frozen=True)
class Turn:
    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class Item:  # one scored file; item 5a builds these
    dataset: str  # "LS-ci", "AMI-ci", "LS full", "AMI full", "one-hour"
    id: str
    audio: Path
    duration: float
    ref_text: str | None = None
    ref_turns: tuple[Turn, ...] | None = None


@dataclass(frozen=True)
class Hypothesis:  # one system's output on one item
    text: str | None  # the words in order (WER)
    turns: tuple[Turn, ...] | None  # speaker turns (DER)
    wall_seconds: float | None = None
    peak_rss_bytes: int | None = None


SYSTEMS = ("product", "whisper-alone", "pyannote-alone")


@cache
def _normalizer():
    from lmt.eval.normalizer import EnglishTextNormalizer

    return EnglishTextNormalizer()


def normalize(text: str) -> str:
    """Whisper's English text normalizer, the vendored copy, built once."""
    return _normalizer()(text)


def wer(pairs: Sequence[tuple[str, str]]) -> float:
    """Pooled WER over (reference, hypothesis) pairs: (S + D + I) / (S + D + H) over the whole set, both
    sides normalized. An empty hypothesis counts every reference word as a deletion."""
    import jiwer

    refs, hyps = [], []
    for n, (ref, hyp) in enumerate(pairs):
        ref_normalized = normalize(ref)
        if not ref_normalized.split():
            raise ValueError(f"reference {n} is empty once normalized: {ref!r}")
        refs.append(ref_normalized)
        hyps.append(normalize(hyp))
    if not refs:
        raise ValueError("no (reference, hypothesis) pairs to score")
    out = jiwer.process_words(refs, hyps)
    s, d, i, h = out.substitutions, out.deletions, out.insertions, out.hits
    return (s + d + i) / (s + d + h)


def der(files: Sequence[tuple[Sequence[Turn], Sequence[Turn], float]]) -> float:
    """Pooled DER over (reference, hypothesis, duration) files: collar 0, overlap scored, the whole file."""
    from pyannote.core import Segment, Timeline
    from pyannote.metrics.diarization import DiarizationErrorRate

    if not files:
        raise ValueError("no files to score")
    metric = DiarizationErrorRate(collar=0.0, skip_overlap=False)
    for ref, hyp, duration in files:
        metric(_annotation(ref), _annotation(hyp), uem=Timeline([Segment(0, duration)]))
    return abs(metric)


def _annotation(turns: Sequence[Turn]):
    from pyannote.core import Annotation, Segment

    annotation = Annotation()
    for track, turn in enumerate(turns):  # a track per turn, so two turns with the same times both count
        annotation[Segment(turn.start, turn.end), track] = turn.speaker
    return annotation


def hypothesis_from_transcript(doc: dict) -> Hypothesis:
    """A transcript JSON's hypothesis: its segments' text joined by single spaces, and their turns."""
    segments = doc["segments"]
    return Hypothesis(
        text=" ".join(s["text"] for s in segments),
        turns=tuple(Turn(float(s["start"]), float(s["end"]), s["speaker"]) for s in segments),
    )


def turns_from_rttm(text: str) -> tuple[Turn, ...]:
    """`SPEAKER <file> <channel> <start> <duration> <NA> <NA> <speaker> <NA> <NA>` lines, as turns."""
    turns = []
    for n, line in enumerate(text.splitlines(), start=1):
        fields = line.split()
        try:
            if len(fields) != 10 or fields[0] != "SPEAKER":
                raise ValueError
            start, duration = float(fields[3]), float(fields[4])
        except ValueError:
            raise ValueError(f"RTTM line {n} isn't a SPEAKER line of 10 fields: {line!r}") from None
        turns.append(Turn(start, start + duration, fields[7]))
    return tuple(turns)


def score(items: Sequence[Item], hyps: Mapping[tuple[str, str, str], Hypothesis]) -> list[dict]:
    """The results file's "figures": one per dataset (in the items' order) and system (in SYSTEMS order)
    with hypotheses. `hyps` is keyed by (system, dataset, item id)."""
    figures = []
    for dataset in dict.fromkeys(item.dataset for item in items):
        members = [item for item in items if item.dataset == dataset]
        for system in SYSTEMS:
            got = [hyps.get((system, dataset, item.id)) for item in members]
            have = sum(h is not None for h in got)
            if have == 0:
                continue
            if have < len(members):
                raise ValueError(f"{dataset}, {system}: hypotheses for {have} of {len(members)} items")
            figures.append(_figure(dataset, system, members, got))
    return figures


def _figure(dataset: str, system: str, items: list[Item], hyps: list[Hypothesis]) -> dict:
    wer_value = der_value = None
    if all(i.ref_text is not None for i in items) and all(h.text is not None for h in hyps):
        wer_value = round(wer([(i.ref_text, h.text) for i, h in zip(items, hyps)]), 6)
    if all(i.ref_turns is not None for i in items) and all(h.turns is not None for h in hyps):
        der_value = round(der([(i.ref_turns, h.turns, i.duration) for i, h in zip(items, hyps)]), 6)
    audio = sum(i.duration for i in items)
    wall = None
    if all(h.wall_seconds is not None for h in hyps):
        wall = sum(h.wall_seconds for h in hyps)
    peak = None
    if all(h.peak_rss_bytes is not None for h in hyps):
        peak = max(h.peak_rss_bytes for h in hyps)
    return {
        "dataset": dataset,
        "system": system,
        "wer": wer_value,
        "der": der_value,
        "audio_seconds": round(audio, 3),
        "wall_seconds": None if wall is None else round(wall, 3),
        "rtf": None if wall is None else round(wall / audio, 3),
        "peak_rss_bytes": peak,
    }
