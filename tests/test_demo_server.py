"""The demo's handler, in process: `make_server` and `Handler` (design/internal/g3-stage1.md, 2a3's tests)."""

import contextlib
import http.client
import io
import shutil
import socket
import threading
from pathlib import Path

import pytest

from lmt import cli, transcript
from lmt.demo.server import load, make_server
from lmt.errors import InputError
from lmt.export import vtt

pytestmark = pytest.mark.criterion("G3-4")

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def relabel(path, mapping):
    """`lmt rename -f json` on the transcript at `path` with `mapping`'s labels: the JSON alone, written again."""
    assert cli.main(["rename", "-f", "json", str(path), *(f"{k}={v}" for k, v in mapping.items())]) == 0


@pytest.fixture
def served(tmp_path):
    shutil.copy(FIXTURES / "two_speakers.transcript.json", tmp_path)
    shutil.copy(FIXTURES / "two_speakers.wav", tmp_path)
    with contextlib.redirect_stderr(io.StringIO()):
        demo = load(tmp_path / "two_speakers.transcript.json", None, False)
    server = make_server(demo, 0)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    yield server, demo
    server.shutdown()
    server.server_close()
    thread.join()


def request(demo, method, path, *, host=True, headers=None):
    """(status, headers, body); `host` True sends `127.0.0.1:<port>`, a string sends that, None sends none."""
    conn = http.client.HTTPConnection("127.0.0.1", demo.port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host is True:
            host = f"127.0.0.1:{demo.port}"
        if host is not None:
            conn.putheader("Host", host)
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        conn.endheaders()
        resp = conn.getresponse()
        return resp.status, resp.headers, resp.read()
    finally:
        conn.close()


def get(demo, route, **kw):
    return request(demo, "GET", f"/{demo.token}/{route}", **kw)


def assert_secure(headers):
    for name, value in SECURITY_HEADERS.items():
        assert headers[name] == value, name


def test_listens_on_loopback_only(served):
    server, _ = served
    assert server.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("route, content_type, static", [
    ("", "text/html; charset=utf-8", "index.html"),
    ("app.js", "text/javascript; charset=utf-8", "app.js"),
    ("app.css", "text/css; charset=utf-8", "app.css"),
    ("favicon.svg", "image/svg+xml; charset=utf-8", "favicon.svg"),
])
def test_static_files(served, route, content_type, static):
    _, demo = served
    status, headers, body = get(demo, route)
    assert (status, headers["Content-Type"]) == (200, content_type)
    assert body == (Path(__file__).resolve().parents[1] / "src" / "lmt" / "demo" / "static" / static).read_bytes()
    assert_secure(headers)


def test_query_string_is_ignored(served):
    _, demo = served
    assert get(demo, "app.js?v=1")[0] == 200


def test_transcript_audio_and_track(served):
    _, demo = served
    status, headers, body = get(demo, "transcript.json")
    assert (status, headers["Content-Type"]) == (200, "application/json")
    assert body == (FIXTURES / "two_speakers.transcript.json").read_bytes()
    assert_secure(headers)
    status, headers, body = get(demo, "transcript.vtt")
    assert (status, headers["Content-Type"]) == (200, "text/vtt; charset=utf-8")
    assert body == vtt.render(transcript.load(FIXTURES / "two_speakers.transcript.json")).encode("utf-8")
    assert_secure(headers)
    status, headers, body = get(demo, "audio.wav")
    assert (status, headers["Content-Type"], headers["Accept-Ranges"]) == (200, "audio/wav", "bytes")
    assert body == demo.audio
    assert_secure(headers)


@pytest.mark.parametrize("spec, piece", [
    ("bytes=0-99", slice(0, 100)),
    ("bytes=1000-", slice(1000, None)),
    ("bytes=-100", slice(-100, None)),
    ("bytes=0-{past}", slice(0, None)),
])
def test_ranges(served, spec, piece):
    _, demo = served
    total = len(demo.audio)
    status, headers, body = get(demo, "audio.wav", headers={"Range": spec.format(past=total + 1000)})
    start, stop, _ = piece.indices(total)
    assert status == 206
    assert body == demo.audio[piece]
    assert headers["Content-Range"] == f"bytes {start}-{stop - 1}/{total}"
    assert headers["Accept-Ranges"] == "bytes"
    assert_secure(headers)


def test_range_at_the_end_is_416(served):
    _, demo = served
    total = len(demo.audio)
    status, headers, _ = get(demo, "audio.wav", headers={"Range": f"bytes={total}-"})
    assert status == 416
    assert headers["Content-Range"] == f"bytes */{total}"
    assert_secure(headers)


def test_several_ranges_get_the_whole_file(served):
    _, demo = served
    status, _, body = get(demo, "audio.wav", headers={"Range": "bytes=0-1,5-6"})
    assert status == 200
    assert body == demo.audio


@pytest.mark.parametrize("route, headers", [
    ("", {}), ("transcript.json", {}), ("audio.wav", {}), ("audio.wav", {"Range": "bytes=0-99"}),
])
def test_head_has_gets_headers_and_no_body(served, route, headers):
    _, demo = served
    g_status, g_headers, _ = get(demo, route, headers=headers)
    status, h_headers, body = request(demo, "HEAD", f"/{demo.token}/{route}", headers=headers)
    assert status == g_status
    assert body == b""
    drop = {"Date"}
    assert {k: v for k, v in h_headers.items() if k not in drop} == {k: v for k, v in g_headers.items()
                                                                      if k not in drop}


@pytest.mark.parametrize("path", ["/", "/app.js", "/{wrong}/", "/{token}", "/{token}/nothing"])
def test_paths_without_the_token_or_route_are_404(served, path):
    _, demo = served
    wrong = ("A" if demo.token[0] != "A" else "B") + demo.token[1:]
    status, headers, _ = request(demo, "GET", path.format(token=demo.token, wrong=wrong))
    assert status == 404
    assert_secure(headers)


@pytest.mark.parametrize("host", ["evil.example:{port}", "127.0.0.1:1", None, "127.0.0.1", "localhost"])
def test_other_hosts_are_403(served, host):
    _, demo = served
    status, headers, _ = get(demo, "", host=host and host.format(port=demo.port))
    assert status == 403
    assert_secure(headers)


def test_localhost_in_any_case_is_allowed(served):
    _, demo = served
    assert get(demo, "", host=f"LocalHost:{demo.port}")[0] == 200


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "OPTIONS", "BREW"])
def test_other_methods_are_405(served, method):
    _, demo = served
    status, headers, _ = request(demo, method, f"/{demo.token}/")
    assert status == 405
    assert headers["Allow"] == "GET, HEAD"
    assert_secure(headers)


def test_host_is_checked_before_the_method(served):
    _, demo = served
    assert request(demo, "POST", f"/{demo.token}/", host="evil.example")[0] == 403


def test_transcript_is_read_again_on_each_request(served, capfd):
    _, demo = served
    path = demo.transcript
    labels = {s.id: f"New {s.id}" for s in transcript.load(path).speakers}
    relabel(path, labels)
    status, _, body = get(demo, "transcript.json")
    assert status == 200
    assert body == path.read_bytes()
    assert [s.label for s in transcript.load(path).speakers] == list(labels.values())
    status, _, body = get(demo, "transcript.vtt")
    assert status == 200
    assert body == vtt.render(transcript.load(path)).encode("utf-8")
    for label in labels.values():
        assert label.encode() in body
    assert not list(path.parent.rglob("*.vtt"))

    path.write_text("{", encoding="utf-8")
    for _ in range(2):
        for route in ("transcript.json", "transcript.vtt"):
            status, headers, body = get(demo, route)
            assert (status, headers["Content-Type"]) == (500, "text/plain; charset=utf-8")
            assert body.decode().startswith(f"{path}: ")
            assert_secure(headers)
    assert get(demo, "")[0] == 200
    warnings = [line for line in capfd.readouterr().err.splitlines() if line.startswith("lmt: warning:")]
    assert len(warnings) == 1, warnings
    assert warnings[0].startswith(f"lmt: warning: {path}: ")

    shutil.copy(FIXTURES / "two_speakers.transcript.json", path)
    assert get(demo, "transcript.json")[0] == 200
    assert get(demo, "transcript.vtt")[0] == 200


def test_a_port_in_use(tmp_path):
    shutil.copy(FIXTURES / "two_speakers.transcript.json", tmp_path)
    shutil.copy(FIXTURES / "two_speakers.wav", tmp_path)
    with contextlib.redirect_stderr(io.StringIO()):
        demo = load(tmp_path / "two_speakers.transcript.json", None, False)
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        with pytest.raises(InputError) as e:
            make_server(demo, port)
    assert e.value.subject == f"--port {port}"
    assert e.value.exit_code == 3
