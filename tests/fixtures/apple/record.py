"""Records the helper's replies on the Mac into this directory (design/internal/g4-stage1.md §3, "Tests"):

    uv run --frozen pytest tests/fixtures/apple/record.py

It isn't collected by a plain `pytest` (not a `test_*.py`). It builds the helper into LMT_CACHE_DIR and writes
each reply's stdout as it came, one JSON line each:

- `status-ok.jsonl`: `lmt-speech status --locale en-US`, on a Mac with the en-US assets installed.
- `status-not-installed.jsonl`: `status` for the first of NOT_INSTALLED that the recognizer supports and this
  Mac hasn't installed, so nothing is uninstalled to record it.
- `status-unsupported.jsonl`: `status` for UNSUPPORTED, a locale the recognizer doesn't support anywhere.
- `transcribe-clip.jsonl`: `lmt-speech transcribe --locale en-US` with clip.wav's decoded samples on stdin.

The two `status` replies for other locales carry that locale in `locale`; the Python side reads only
`supported` and `installed` from them.
"""

import json
import subprocess
from pathlib import Path

import pytest

from lmt import audio
from lmt.engines import apple

HERE = Path(__file__).resolve().parent
CLIP = HERE.parent / "clip.wav"
NOT_INSTALLED = ("de-DE", "fr-FR", "es-ES", "it-IT", "ja-JP", "ko-KR", "pt-BR", "zh-CN")
UNSUPPORTED = "tlh-001"


def reply(binary, verb, locale, stdin=b""):
    r = subprocess.run([str(binary), verb, "--locale", locale], input=stdin, capture_output=True, timeout=600,
                       check=False)
    print(r.stderr.decode())  # the helper's diagnostics, shown when a recording fails
    lines = [json.loads(line) for line in r.stdout.splitlines()]
    return r.returncode, lines, r.stdout


def save(name, stdout):
    (HERE / name).write_bytes(stdout)


@pytest.fixture(scope="module")
def binary():
    return apple.build()


def test_status_ok(binary):
    code, lines, stdout = reply(binary, "status", "en-US")
    assert code == 0 and lines[0]["supported"] and lines[0]["installed"], stdout
    save("status-ok.jsonl", stdout)


def test_status_not_installed(binary):
    for locale in NOT_INSTALLED:
        code, lines, stdout = reply(binary, "status", locale)
        if code == 0 and lines[0]["supported"] and not lines[0]["installed"]:
            save("status-not-installed.jsonl", stdout)
            return
    pytest.fail(f"every one of {NOT_INSTALLED} is installed or unsupported here")


def test_status_unsupported(binary):
    code, lines, stdout = reply(binary, "status", UNSUPPORTED)
    assert code == 0 and not lines[0]["supported"], stdout
    save("status-unsupported.jsonl", stdout)


def test_transcribe_clip(binary):
    samples = audio.decode(CLIP).astype("<f4").tobytes()
    code, lines, stdout = reply(binary, "transcribe", "en-US", stdin=samples)
    assert code == 0 and lines[-1] == {"type": "done"}, stdout
    save("transcribe-clip.jsonl", stdout)
