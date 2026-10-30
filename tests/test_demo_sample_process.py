"""`lmt demo --sample` end to end, in a subprocess on fakes: no network or model (design/internal/g3-stage1.md
§2b, 2b2). `fixtures/sample_fakes/sitecustomize.py` gives the clip as the sample and the fixture as its
transcription."""

import http.client
from pathlib import Path

import pytest
from demo_process import TRANSCRIPT, DemoProcess, fixture_dir, run_demo

pytestmark = pytest.mark.criterion("G1-6")

FAKES = Path(__file__).resolve().parent / "fixtures" / "sample_fakes"


def get(demo, route):
    """(status, body) of a GET for `route` under the demo's token."""
    conn = http.client.HTTPConnection("127.0.0.1", demo.port, timeout=10)
    try:
        conn.request("GET", f"/{demo.token}/{route}")
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


@pytest.fixture
def sample_env(tmp_path):
    """The fakes' environment, with an LMT_DATA_DIR and a transcribe log shared by every run in the test."""
    data = tmp_path / "data"
    data.mkdir()
    return {"PYTHONPATH": str(FAKES), "LMT_DATA_DIR": str(data),
            "LMT_TEST_TRANSCRIBE_LOG": str(tmp_path / "transcribed.txt")}


def transcribed(env):
    log = Path(env["LMT_TEST_TRANSCRIBE_LOG"])
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_sample_serves_the_cached_transcript_and_a_second_run_uses_the_cache(tmp_path, sample_env):
    with DemoProcess(tmp_path, "--sample", env=sample_env) as demo:
        cached = list(Path(sample_env["LMT_DATA_DIR"]).glob("sample/*/ES2004a.json"))
        assert len(cached) == 1, cached
        status, body = get(demo, "")
        assert status == 200 and b"<main>" in body
        assert get(demo, "transcript.json") == (200, cached[0].read_bytes())
        status, body = get(demo, "audio.wav")
        assert status == 200 and body.startswith(b"RIFF")
    assert len(transcribed(sample_env)) == 1
    assert "transcribing the sample meeting once" in demo.stderr
    assert not [line for line in demo.stderr.splitlines() if line.startswith("lmt: error:")], demo.stderr

    with DemoProcess(tmp_path, "--sample", env=sample_env) as again:
        assert get(again, "transcript.json") == (200, cached[0].read_bytes())
    assert len(transcribed(sample_env)) == 1
    assert "transcribing" not in again.stderr


@pytest.mark.criterion("G3-4")
def test_transcript_with_sample_still_exits_2(tmp_path, sample_env):
    r = run_demo(tmp_path, str(fixture_dir(tmp_path) / TRANSCRIPT), "--sample", env=sample_env)
    assert r.returncode == 2, r.stderr
    assert len(r.stderr.splitlines()) == 1 and r.stderr.startswith("lmt: error: --sample: "), r.stderr
    assert transcribed(sample_env) == []
