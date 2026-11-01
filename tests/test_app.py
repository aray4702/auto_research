"""G5 (F12): `lmt app`'s server, in process, with `tests/fake_live.py` standing in for `lmt live` (no model)."""

import http.client
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from lmt.demo import app as lmt_app

pytestmark = pytest.mark.criterion("G5-1", "G5-3")
FAKE = Path(__file__).with_name("fake_live.py")
# A session that runs until SIGINT, then saves, as `lmt live` does.
UNTIL_SIGINT = r'''
import json, signal, sys, time, wave
from pathlib import Path
args = sys.argv[1:]
stem, out = args[args.index("-n") + 1], Path(args[args.index("-o") + 1])
stopped = []
signal.signal(signal.SIGINT, lambda *_: stopped.append(1))
print(json.dumps({"type": "start", "t": 0, "wall": time.time()}), flush=True)
while not stopped:
    time.sleep(0.02)
print(json.dumps({"type": "stop", "t": 0.5, "reason": "signal"}), flush=True)
sys.exit(0)
'''


@pytest.fixture
def served(tmp_path, monkeypatch):
    def serve(live):
        monkeypatch.setattr(lmt_app, "LIVE", live)
        app = lmt_app.App(tmp_path / "out", source=["--from-file", "x.wav"], offline=True)
        (tmp_path / "out").mkdir()
        server = lmt_app.make_server(app, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return app

    servers = []
    yield serve
    for s in servers:
        s.shutdown()
        s.server_close()


def request(app, method, route, *, body=None, origin=True, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
    headers = {"Host": host or f"127.0.0.1:{app.port}"}
    if origin:
        headers["Origin"] = f"http://127.0.0.1:{app.port}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, f"/{app.token}/{route}", body=json.dumps(body) if body is not None else None, headers=headers)
    r = conn.getresponse()
    return r.status, r.read()


def events(app):
    """The event stream until `end`: the JSON events, and the end event's data."""
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
    conn.request("GET", f"/{app.token}/api/events", headers={"Host": f"127.0.0.1:{app.port}"})
    r = conn.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
    out, kind = [], None
    for raw in r:
        line = raw.decode().rstrip("\n")
        if line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: "):
            if kind == "end":
                return out, json.loads(line[6:])
            out.append(json.loads(line[6:]))
    raise AssertionError("the stream ended without an end event")


def test_the_page_and_its_assets_are_served_under_the_token_only(served):
    app = served([sys.executable, str(FAKE)])
    assert request(app, "GET", "")[0] == 200
    assert request(app, "GET", "live.js")[0] == 200
    assert request(app, "GET", "api/state")[0] == 200
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    conn.request("GET", "/wrong-token/", headers={"Host": f"127.0.0.1:{app.port}"})
    assert conn.getresponse().status == 404
    assert request(app, "GET", "", host="evil.example:80")[0] == 403


def test_start_needs_post_from_the_page_s_own_origin(served):
    app = served([sys.executable, str(FAKE)])
    assert request(app, "GET", "api/start")[0] == 405
    assert request(app, "POST", "api/start", body={"engine": "kyutai"}, origin=False)[0] == 403
    assert request(app, "POST", "api/start", body={"engine": "whisper"})[0] == 400
    assert app.session is None


def test_a_session_streams_its_events_saves_and_opens_in_f8_s_page(served, tmp_path):
    app = served([sys.executable, str(FAKE)])
    status, body = request(app, "POST", "api/start", body={"engine": "kyutai"})
    assert status == 200, body
    got, end = events(app)
    assert [e["type"] for e in got] == ["start", "words", "stop"]
    assert end == {"code": 0, "view": True, "error": None}
    argv = app.session.proc.args
    assert argv[argv.index("--engine") + 1] == "kyutai" and "--jsonl" in argv and "--save-audio" in argv
    assert argv[argv.index("--from-file") + 1] == "x.wav" and "--offline" in argv
    assert request(app, "GET", "view/")[0] == 200
    status, doc = request(app, "GET", "view/transcript.json")
    assert status == 200 and json.loads(doc)["segments"][0]["text"] == "okay so"
    assert request(app, "GET", "view/audio.wav")[0] == 200


def test_stop_sends_one_sigint_and_a_second_start_waits_for_the_first(served):
    app = served([sys.executable, "-c", UNTIL_SIGINT])
    assert request(app, "POST", "api/start", body={"engine": "kyutai"})[0] == 200
    deadline = time.monotonic() + 10
    while not app.session.events and time.monotonic() < deadline:
        time.sleep(0.02)
    assert request(app, "POST", "api/start", body={"engine": "kyutai"})[0] == 409
    assert request(app, "POST", "api/stop", body={})[0] == 200
    got, end = events(app)
    assert [e["type"] for e in got] == ["start", "stop"] and end["code"] == 0
    assert request(app, "POST", "api/stop", body={})[0] == 409  # nothing running


def test_it_listens_on_127_0_0_1_only(served):
    app = served([sys.executable, str(FAKE)])
    assert app.port and lmt_app.make_server.__doc__.startswith("F8's server, on 127.0.0.1")


@pytest.mark.criterion("G5-2")
def test_events_reach_the_stream_within_half_a_second_of_being_written(served):
    """F12: live text reaches the page within Q10's delays plus 0.5 s; the app's own share is the stream."""
    app = served([sys.executable, "-c", UNTIL_SIGINT])
    assert request(app, "POST", "api/start", body={"engine": "kyutai"})[0] == 200
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
    conn.request("GET", f"/{app.token}/api/events", headers={"Host": f"127.0.0.1:{app.port}"})
    r = conn.getresponse()
    for raw in r:
        if raw.startswith(b"data: "):
            arrived = time.time()
            written = json.loads(raw[6:])["wall"]
            break
    request(app, "POST", "api/stop", body={})
    assert arrived - written < 0.5, arrived - written
