"""Kyutai on the models (#601 items 1 and 3): G4-1's `--engine kyutai` case, and the token buffer's roll."""

import json

import pytest
from conftest import CLIP, run_lmt


@pytest.mark.models
@pytest.mark.criterion("G4-1")
def test_engine_kyutai_gives_the_same_json_as_no_option(clip_run, tmp_path):
    """Spec G4-1: the product run on the reference clip with `--engine kyutai` gives the same JSON as without it."""
    assert clip_run.returncode == 0, clip_run.stderr
    r = run_lmt(["transcribe", str(CLIP), "--engine", "kyutai"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    with_option = json.loads((tmp_path / "clip.json").read_text(encoding="utf-8"))
    without = json.loads((clip_run.out_dir / "clip.json").read_text(encoding="utf-8"))
    assert with_option == without
    assert with_option["models"]["asr"]["engine"] == "kyutai"


@pytest.mark.models
def test_rolling_the_token_buffer_keeps_the_words(monkeypatch):
    """`Stream._roll` runs every MAX_STEPS steps (about 21.8 min); with a small buffer the clip rolls many times
    and must give the same words as without a roll."""
    from lmt import audio, engines
    from lmt.engines import kyutai

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HF_HUB_OFFLINE", "0")
        engine = engines.load("kyutai", offline=True)
    samples = audio.decode(CLIP, engine.rate)
    plain = engine.transcribe(samples, rate=engine.rate)
    monkeypatch.setattr(kyutai, "MAX_STEPS", 64)
    rolled = engine.transcribe(samples, rate=engine.rate)
    assert len(samples) / engine.rate / kyutai.STEP > 10 * 64  # the clip rolls the buffer many times
    assert rolled == plain
