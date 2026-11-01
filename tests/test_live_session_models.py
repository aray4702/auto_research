"""G2-1, G2-3 and G2-6c (spec/acceptance.md), on the models (design/internal/g2-stage1.md §3a, item 3a4, moved onto
the shared run by 3b5): the events of `live_run`, the one lockstep run of `lmt live --from-file` on the two-speaker
clip, with the live default ASR model and online diarization, shared by the three cases. Each event's `t` is the
stream position when it was written. Offline word times come from `two_speakers_run`, and committed words are
matched to them with `lmt.eval.live.match_words`."""

import json
import wave
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple

import jsonschema
import pytest
from test_g1_1 import report
from test_g1_3 import read_rttm, reference_speaker

from lmt.transcript import Word

ROOT = Path(__file__).resolve().parents[1]
TWO_SPEAKERS = ROOT / "tests" / "fixtures" / "two_speakers.wav"
RTTM = ROOT / "tests" / "fixtures" / "two_speakers.rttm"
EVENTS_SCHEMA = ROOT / "schema" / "live-events.schema.json"
RATE = 16000

EARLY = 5.0  # s, spec/acceptance.md G2-1: words ending at least 5 s before the clip's end are committed before its end
SHARE_BOUND = 0.90  # spec/acceptance.md G2-3: at least 90 % of a speaker's words carry their majority label
WORD_BOUND = 0.7  # s, spec/acceptance.md G2-6c: p95 word stream delay <= 0.7 s, inclusive (G4, #577)
LABEL_BOUND = 2.0  # s, and p95 label stream delay <= 2.0 s


class Session(NamedTuple):
    events: list[dict]
    result: object  # the saved transcript, lmt.transcript.Transcript: its speakers and segments
    duration: float  # s, the clip's length: the position of the source's last block
    asr_name: str


@pytest.fixture(scope="module")
def session(live_run):
    """The shared run's events and transcript."""
    from lmt import transcript

    assert live_run.returncode == 0, live_run.stderr
    with wave.open(str(TWO_SPEAKERS), "rb") as w:
        duration = w.getnframes() / RATE
    result = transcript.from_dict(json.loads((live_run.out_dir / f"{live_run.stem}.json").read_text(encoding="utf-8")))
    return Session(live_run.events, result, duration, live_run.events[0]["models"]["asr"]["name"])


def committed(events) -> list[tuple[Word, float, str]]:
    """Each committed word, in stream order, with its event's `t` and speaker."""
    return [(Word(w["start"], w["end"], w["text"], w["probability"]), e["t"], e["speaker"])
            for e in events if e["type"] == "words" for w in e["words"]]


def offline_words(run):
    from lmt.eval.live import RefWord

    assert run.returncode == 0, run.stderr
    doc = json.loads((run.out_dir / "two_speakers.json").read_text(encoding="utf-8"))
    # Turns-first segments can hold words out of time order where turns overlap (P12), so sort them.
    words = sorted((RefWord(w["start"], w["end"], w["text"]) for s in doc["segments"] for w in s["words"]),
                   key=lambda w: (w.start, w.end))
    assert words, "the offline run has no words"
    return words


@pytest.mark.models
@pytest.mark.criterion("G2-1")
def test_g2_1_words_are_committed_while_the_audio_arrives(session, two_speakers_run):
    from lmt.eval.live import match_words

    events = session.events
    validator = jsonschema.Draft202012Validator(json.loads(EVENTS_SCHEMA.read_text(encoding="utf-8")))
    for n, event in enumerate(events, start=1):
        errors = [e.message for e in validator.iter_errors(event)]
        assert not errors, f"event {n} ({event.get('type')}): {errors}"
    types = [e["type"] for e in events]
    assert types[0] == "start" and types[-1] == "stop", f"first {types[0]}, last {types[-1]}"
    assert types.count("start") == 1 and types.count("stop") == 1, Counter(types)

    words = committed(events)
    assert words, "nothing was committed"
    ids = {s.id for s in session.result.speakers}
    assert all(speaker in ids for _, _, speaker in words), f"speakers {ids}"
    for (a, _, _), (b, _, _) in pairwise(words):
        assert a.start <= b.start, f"out of order: {a} then {b}"
        assert a.end <= b.start, f"overlapping: {a} then {b}"
    keys = Counter((w.start, w.end, w.text) for w, _, _ in words)
    assert not [k for k, n in keys.items() if n > 1], "repeated words"
    # Nothing changed after its event: the final segments hold exactly the committed words, with their speakers.
    final = [(w, s.speaker) for s in session.result.segments for w in s.words]
    assert final == [(w, speaker) for w, _, speaker in words], "the final segments differ from the event stream"

    offline = offline_words(two_speakers_run)
    matches = match_words(offline, [(w, t) for w, t, _ in words])
    early = [(ref, t) for ref, t in matches if ref.end <= session.duration - EARLY]
    late = [(ref, t) for ref, t in early if t >= session.duration]
    report(f"G2-1: the live pipeline ({session.asr_name}) in lockstep on tests/fixtures/two_speakers.wav "
           f"({session.duration:.3f} s): {len(events)} events, {len(words)} committed words, {len(matches)} of "
           f"{len(offline)} offline words matched ({len(matches) / len(offline):.1%}); {len(early)} matched words "
           f"end at least {EARLY:.0f} s before the clip's end, {len(late)} of them committed at its end")
    assert matches, "no committed word matched an offline word"
    assert not late, f"committed only at the clip's end: {late[:5]}"


@pytest.mark.models
@pytest.mark.criterion("G2-3")
def test_g2_3_each_speakers_words_carry_one_label(session):
    words = committed(session.events)
    speakers = session.result.speakers
    turns = read_rttm(RTTM)
    labels = {speaker: Counter() for speaker in dict.fromkeys(turn.speaker for turn in turns)}
    for w, _, speaker in words:
        labels[reference_speaker(w.start, w.end, turns)][speaker] += 1

    majority, shares = {}, {}
    for speaker, counts in labels.items():
        total = sum(counts.values())
        majority[speaker], top = counts.most_common(1)[0] if counts else (None, 0)
        shares[speaker] = top / total if total else 0.0
        report(f"G2-3: reference speaker {speaker}: {total} committed words, majority label {majority[speaker]} "
               f"on {shares[speaker]:.1%} (bound {SHARE_BOUND:.0%}), labels {dict(counts)}")
    report(f"G2-3: the live pipeline in lockstep on tests/fixtures/two_speakers.wav: speakers "
           f"{[s.id for s in speakers]}, {len(session.result.segments)} segments")

    assert len(speakers) == 2, f"speakers: {speakers}"
    assert {speaker for _, _, speaker in words} == {s.id for s in speakers}, "the words don't use both speakers"
    for speaker, share in shares.items():
        assert share >= SHARE_BOUND, f"reference speaker {speaker}: {share:.1%} on their majority label"
    assert len(set(majority.values())) == len(majority), f"the speakers' majority labels are the same: {majority}"


@pytest.mark.models
@pytest.mark.criterion("G2-6c")
def test_g2_6c_the_p95_word_and_label_stream_delays_are_within_their_bounds(session, two_speakers_run):
    """The word delay from the `text` events (a word's last piece), the label delay from the labelled `words`
    events, each against the offline word times, in stream positions (spec G2-6c, G4 "Terms")."""
    from lmt.eval.live import delays, match_words, percentile, read_text

    offline = offline_words(two_speakers_run)
    lines = []
    for name, hyp, bound in (("word", read_text([(e["t"], e) for e in session.events]), WORD_BOUND),
                             ("label", [(w, t) for w, t, _ in committed(session.events)], LABEL_BOUND)):
        matches = match_words(offline, hyp)
        assert matches, f"no {name} matched an offline word"
        values = delays(matches)
        p50, p95 = percentile(values, 0.50), percentile(values, 0.95)
        text = (f"{name} stream delay p50 {p50:.3f} s, p95 {p95:.3f} s (bound {bound:.1f} s), "
                f"maximum {max(values):.3f} s, {len(matches)} of {len(offline)} matched")
        lines.append((name, p95, bound, text))
    line = (f"G2-6c: the live pipeline ({session.asr_name}) in lockstep on tests/fixtures/two_speakers.wav: "
            + "; ".join(text for *_, text in lines))
    report(line)
    assert all(p95 <= bound for _, p95, bound, _ in lines), line
