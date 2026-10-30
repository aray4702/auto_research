"""G2-2 and ASR's half of G2-6c (spec/acceptance.md): streaming ASR alone, with the live default model, in
lockstep (`FileSource(paced=False)` through `lockstep`; design/internal/g2-stage1.md §1, item 1e). 3a4 drops the
two-speaker case once G2-6c's own test runs, so that CI transcribes the clip in lockstep only once."""

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "tests" / "fixtures" / "clip.wav"
CLIP_TEXT = ROOT / "tests" / "fixtures" / "clip.txt"
TWO_SPEAKERS = ROOT / "tests" / "fixtures" / "two_speakers.wav"

WER_BOUND = 0.100  # spec/acceptance.md G2-2: WER <= 10.0 %, inclusive
P95_BOUND = 2.0  # s, spec/acceptance.md G2-6c: p95 stream delay <= 2.0 s, inclusive


def report(line):
    print(line)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(line + "\n")


@pytest.fixture(scope="module")
def live_engine():
    """The live default model's engine, loaded once. HF_HUB_OFFLINE is put back afterwards, undoing ensure()'s."""
    from lmt import asr, models

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HF_HUB_OFFLINE", "0")
        model = models.default_live_asr(models.load_manifest())
        yield model, asr.load(models.ensure([model])[model.name])


def committed(engine, path):
    """Each committed word, with the stream position at its commit in seconds, in lockstep."""
    from lmt.audio import SAMPLE_RATE
    from lmt.live.asr import StreamingASR
    from lmt.live.source import FileSource, lockstep

    words = []
    for position, (update,) in lockstep(FileSource(path, paced=False), StreamingASR(engine)):
        words.extend((w, position / SAMPLE_RATE) for w in update.words)
    return words


@pytest.mark.models
@pytest.mark.criterion("G2-2")
def test_g2_2_the_clip_in_lockstep_is_within_the_wer_bound(live_engine):
    from lmt.eval.score import wer

    model, engine = live_engine
    words = committed(engine, CLIP)
    assert words, "nothing was committed"

    reading = wer([(CLIP_TEXT.read_text(encoding="utf-8"), " ".join(w.text for w, _ in words))])
    line = (f"G2-2: streaming ASR ({model.name}) in lockstep on tests/fixtures/clip.wav: WER {reading:.2%} "
            f"(bound {WER_BOUND:.1%}), {len(words)} committed words")
    report(line)
    assert reading <= WER_BOUND, line


@pytest.mark.models
@pytest.mark.criterion("G2-6c")
def test_g2_6c_asr_stream_delay_on_the_two_speaker_clip(live_engine, two_speakers_run):
    from lmt.eval.live import RefWord, delays, match_words, percentile

    assert two_speakers_run.returncode == 0, two_speakers_run.stderr
    doc = json.loads((two_speakers_run.out_dir / "two_speakers.json").read_text(encoding="utf-8"))
    # Turns-first segments can hold words out of time order where turns overlap (P12), so sort them.
    offline = sorted((RefWord(w["start"], w["end"], w["text"]) for s in doc["segments"] for w in s["words"]),
                     key=lambda w: (w.start, w.end))
    assert offline, "the offline run has no words"

    model, engine = live_engine
    words = committed(engine, TWO_SPEAKERS)
    matches = match_words(offline, words)
    assert matches, "no committed word matched an offline word"

    p95 = percentile(delays(matches), 0.95)
    line = (f"G2-6c (ASR's half): streaming ASR ({model.name}) in lockstep on tests/fixtures/two_speakers.wav: "
            f"p95 stream delay {p95:.3f} s (bound {P95_BOUND:.1f} s), {len(matches)} of {len(offline)} offline "
            f"words matched, {len(words)} committed")
    report(line)
    assert p95 <= P95_BOUND, line
