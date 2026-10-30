"""`lmt demo` in a subprocess: `cli_demo` (design/internal/g3-stage1.md, 2a4's tests)."""

import http.client
import shlex
import signal
import socket
import sys
import time
import wave
from pathlib import Path

import pytest
from demo_process import AUDIO, TRANSCRIPT, DemoProcess, fixture_dir, run_demo
from isolation import snapshot

pytestmark = pytest.mark.criterion("G3-4")

NETGUARD = Path(__file__).resolve().parent / "netguard"
ROUTES = ("", "app.js", "app.css", "favicon.svg", "transcript.json", "transcript.vtt", "audio.wav")
RECORDER = """import os, sys
with open(os.environ["LMT_TEST_BROWSER_LOG"], "a", encoding="utf-8") as f:
    f.write(sys.argv[1] + "\\n")
"""


def get(demo, route):
    """(status, body) of a GET for `route` under the demo's token."""
    conn = http.client.HTTPConnection("127.0.0.1", demo.port, timeout=10)
    try:
        conn.request("GET", f"/{demo.token}/{route}")
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def errors(stderr):
    return [line for line in stderr.splitlines() if line.startswith("lmt: error:")]


def warnings(stderr):
    return [line for line in stderr.splitlines() if line.startswith("lmt: warning:")]


def one_second_wav(path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000)


def first_ipv4():
    """The machine's first non-loopback IPv4 address, or None. A UDP connect sends nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
            address = s.getsockname()[0]
        except OSError:
            return None
    return None if address.startswith("127.") or address == "0.0.0.0" else address


@pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
def test_it_prints_the_url_serves_and_exits_0_when_stopped(tmp_path, stop):
    with DemoProcess(tmp_path, stop=stop) as demo:
        status, body = get(demo, "")
        assert status == 200 and b"<main>" in body
    assert demo.stdout == ""
    assert not errors(demo.stderr) and not warnings(demo.stderr), demo.stderr


def test_two_runs_get_different_tokens(tmp_path):
    with DemoProcess(tmp_path) as first:
        pass
    with DemoProcess(tmp_path) as second:
        pass
    assert first.token != second.token


@pytest.mark.criterion("G1-6")
def test_the_browser_opens_the_url_once_and_never_with_no_browser(tmp_path):
    recorder = tmp_path / "recorder.py"
    recorder.write_text(RECORDER, encoding="utf-8")
    opened, not_opened = tmp_path / "opened.txt", tmp_path / "not_opened.txt"
    browser = f"{shlex.quote(sys.executable)} {shlex.quote(str(recorder))} %s"

    with DemoProcess(tmp_path, env={"BROWSER": browser, "LMT_TEST_BROWSER_LOG": str(not_opened)}) as quiet:
        pass
    with DemoProcess(tmp_path, env={"BROWSER": browser, "LMT_TEST_BROWSER_LOG": str(opened)}, browser=True) as demo:
        deadline = time.monotonic() + 10  # the browser command runs beside the server
        while not opened.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)
    assert opened.read_text(encoding="utf-8").splitlines() == [demo.url]
    assert quiet.url and not not_opened.exists()


@pytest.mark.criterion("G1-6")
def test_port_serves_on_the_port_given(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with DemoProcess(tmp_path, str(fixture_dir(tmp_path) / TRANSCRIPT), "--port", str(port)) as demo:
        assert demo.port == port
        assert get(demo, "")[0] == 200


def test_it_listens_on_loopback_only(tmp_path):
    with DemoProcess(tmp_path) as demo:
        targets = [(socket.AF_INET6, "::1")]
        if (address := first_ipv4()) is not None:
            targets.append((socket.AF_INET, address))
        for family, host in targets:
            try:
                s = socket.socket(family, socket.SOCK_STREAM)
            except OSError:  # no IPv6 on this machine: nothing can connect over it
                continue
            with s:
                s.settimeout(5)
                with pytest.raises(OSError):
                    s.connect((host, demo.port))
        assert get(demo, "")[0] == 200


@pytest.mark.criterion("G1-6")
def test_audio_naming_a_missing_file_exits_3(tmp_path):
    missing = tmp_path / "missing.wav"
    r = run_demo(tmp_path, str(fixture_dir(tmp_path) / TRANSCRIPT), "--audio", str(missing))
    assert r.returncode == 3, r.stderr
    assert errors(r.stderr) == r.stderr.splitlines() and len(errors(r.stderr)) == 1, r.stderr
    assert r.stderr.startswith(f"lmt: error: --audio {missing}: "), r.stderr


def test_the_default_audio_missing_exits_3(tmp_path):
    directory = fixture_dir(tmp_path)
    (directory / AUDIO).unlink()
    r = run_demo(tmp_path, str(directory / TRANSCRIPT))
    assert r.returncode == 3, r.stderr
    assert len(r.stderr.splitlines()) == 1 and r.stderr.startswith(f"lmt: error: {directory / AUDIO}: "), r.stderr
    assert r.stderr.rstrip("\n").endswith("; give the recording with --audio"), r.stderr


def test_an_invalid_transcript_exits_3(tmp_path):
    path = fixture_dir(tmp_path) / TRANSCRIPT
    path.write_text("{", encoding="utf-8")
    r = run_demo(tmp_path, str(path))
    assert r.returncode == 3, r.stderr
    assert len(r.stderr.splitlines()) == 1 and r.stderr.startswith(f"lmt: error: {path}: "), r.stderr


@pytest.mark.criterion("G1-6")
def test_a_port_in_use_exits_3(tmp_path):
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        r = run_demo(tmp_path, str(fixture_dir(tmp_path) / TRANSCRIPT), "--port", str(port))
    assert r.returncode == 3, r.stderr
    assert r.stderr == f"lmt: error: --port {port}: already in use\n"


def test_transcript_with_sample_exits_2(tmp_path):
    r = run_demo(tmp_path, str(fixture_dir(tmp_path) / TRANSCRIPT), "--sample")
    assert r.returncode == 2, r.stderr
    assert len(r.stderr.splitlines()) == 1 and r.stderr.startswith("lmt: error: --sample: "), r.stderr


@pytest.mark.criterion("G1-6")
def test_a_short_audio_warns_once_and_still_serves(tmp_path):
    short = tmp_path / "short.wav"
    one_second_wav(short)
    with DemoProcess(tmp_path, str(fixture_dir(tmp_path) / TRANSCRIPT), "--audio", str(short)) as demo:
        assert get(demo, "audio.wav")[0] == 200
    assert len(warnings(demo.stderr)) == 1 and warnings(demo.stderr) == demo.stderr.splitlines(), demo.stderr
    assert str(short) in demo.stderr


def test_it_writes_no_file_and_opens_no_connection(tmp_path):
    """G1-13's checks, once more: under the network guard, a run that serves every route once."""
    log = tmp_path / "net.log"
    env = {"PYTHONPATH": str(NETGUARD), "LMT_NETGUARD_ALLOW": "127.0.0.1", "LMT_NETGUARD_LOG": str(log)}
    directory = fixture_dir(tmp_path)
    demo = DemoProcess(tmp_path, env=env)
    watched = (*demo.fresh.dirs, directory)
    before = snapshot(*watched)
    with demo:
        for route in ROUTES:
            assert get(demo, route)[0] == 200, route
    after = snapshot(*watched)
    assert after == before
    assert not log.exists() or log.read_text(encoding="utf-8") == ""
