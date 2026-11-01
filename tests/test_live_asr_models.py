"""G2-2 and G4-3's Kyutai half (spec/acceptance.md): streaming ASR alone, with Kyutai's engine, in lockstep
(`FileSource(paced=False)` through `lockstep`; design/internal/g4-stage1.md §2, "Tests in test-models"). The
reference clip's live WER is bounded by G2-2 at 10.0 % and by G4-3 at 5.0 %, the batch bound."""

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "tests" / "fixtures" / "clip.wav"
CLIP_TEXT = ROOT / "tests" / "fixtures" / "clip.txt"

WER_BOUND = 0.050  # spec/acceptance.md G4-3: live mode on the reference clip, WER <= 5.0 % (G2-2's is 10.0 %)


def report(line):
    print(line)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(line + "\n")


@pytest.fixture(scope="module")
def live_engine():
    """Kyutai's engine, loaded once from the verified cache."""
    from lmt import engines

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HF_HUB_OFFLINE", "0")  # undone afterwards, as ensure() sets it
        yield engines.load("kyutai", offline=True)


def committed(engine, path):
    """Each committed word, with the stream position at its commit in seconds, in lockstep."""
    from lmt.audio import SAMPLE_RATE
    from lmt.live.asr import StreamingASR
    from lmt.live.source import FileSource, lockstep

    words = []
    # As `lmt live` builds it: the stream at the engine's rate, fed from the source's own copy at it (#603).
    stage = StreamingASR(engine.stream(engine.rate), engine.word_gap, engine.rate)
    source = FileSource(path, paced=False)
    stage.attach(source)
    for position, (update,) in lockstep(source, stage):
        words.extend((w, position / SAMPLE_RATE) for w in update.words)
    return words


@pytest.mark.models
@pytest.mark.criterion("G2-2", "G4-3")
def test_the_clip_in_lockstep_is_within_the_wer_bound(live_engine):
    from lmt.eval.score import wer

    words = committed(live_engine, CLIP)
    assert words, "nothing was committed"
    starts = [w.start for w, _ in words]
    assert starts == sorted(starts), "committed words come in order"

    reading = wer([(CLIP_TEXT.read_text(encoding="utf-8"), " ".join(w.text for w, _ in words))])
    line = (f"G2-2, G4-3: streaming ASR ({live_engine.info['name']}) in lockstep on tests/fixtures/clip.wav: "
            f"WER {reading:.2%} (bound {WER_BOUND:.1%}), {len(words)} committed words")
    report(line)
    assert reading <= WER_BOUND, line


@pytest.mark.models
def test_batch_and_live_give_the_same_words(live_engine):
    """design/internal/g4-stage1.md §2: `transcribe` is one pass of the stream, so both modes agree."""
    from lmt import audio

    live = [w for w, _ in committed(live_engine, CLIP)]
    # As `lmt transcribe` runs it: the file decoded straight to the engine's rate (#575, #603).
    assert live_engine.transcribe(audio.decode(CLIP, live_engine.rate), rate=live_engine.rate) == live
