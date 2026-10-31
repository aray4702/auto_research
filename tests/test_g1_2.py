"""G1-2 (spec/acceptance.md): the product run on each of the reference clip's five encodings, its WER compared with
the WAV's from the same session (design/internal/g1-formats.md, "Tests"). The same test, run once per format."""

import json
from pathlib import Path

import pytest
from conftest import run_lmt
from test_g1_1 import report

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
CLIP_TEXT = FIXTURES / "clip.txt"
FORMATS = ("flac", "mp3", "m4a", "mp4", "mov")

# spec/acceptance.md G1-2: each format's WER within 1.5 percentage points of the WAV's, inclusive (was 1.0; #575, a). The 1e-9 only
# absorbs float rounding at an exact tie: both WERs share the reference's word count N, so a real miss exceeds
# the bound by at least 1 / (100 N).
DELTA_BOUND = 0.015


def clip_wer(json_path):
    from lmt.eval.score import hypothesis_from_transcript, wer

    doc = json.loads(json_path.read_text(encoding="utf-8"))
    return wer([(CLIP_TEXT.read_text(encoding="utf-8"), hypothesis_from_transcript(doc).text)])


@pytest.mark.models
@pytest.mark.criterion("G1-2")
@pytest.mark.parametrize("fmt", FORMATS)
def test_g1_2_each_encoding_transcribes_within_a_point_of_the_wavs_wer(fmt, clip_run, tmp_path):
    assert clip_run.returncode == 0, clip_run.stderr
    wer_wav = clip_wer(clip_run.out_dir / "clip.json")

    r = run_lmt(["transcribe", str(FIXTURES / f"clip.{fmt}")], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "clip.json").is_file(), r.stdout
    wer_format = clip_wer(tmp_path / "clip.json")

    difference = wer_format - wer_wav
    line = (f"G1-2: the product run on tests/fixtures/clip.{fmt}: WER {wer_format:.2%}, the WAV's {wer_wav:.2%}, "
            f"difference {difference * 100:+.2f} points (bound {DELTA_BOUND * 100:.1f})")
    report(line)
    assert abs(difference) <= DELTA_BOUND + 1e-9, line
