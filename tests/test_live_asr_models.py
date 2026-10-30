"""G2-2 (spec/acceptance.md): streaming ASR alone, with the live default model, in lockstep
(`FileSource(paced=False)` through `lockstep`; design/internal/g2-stage1.md §1, item 1e). 1e's two-speaker case,
ASR's half of G2-6c, went with 3a4: G2-6c's own test (`tests/test_live_session_models.py`) covers it, so CI
transcribes that clip in lockstep only once."""

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "tests" / "fixtures" / "clip.wav"
CLIP_TEXT = ROOT / "tests" / "fixtures" / "clip.txt"

WER_BOUND = 0.100  # spec/acceptance.md G2-2: WER <= 10.0 %, inclusive


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

