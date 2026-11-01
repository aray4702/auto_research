"""`lmt app` (G5, F12): a page on this computer that runs live mode and shows its text as it's spoken, with each
line's speaker once it's final, and then opens the saved transcript in F8's page.

The server is F8's (`lmt.demo.server`): 127.0.0.1 only, a random token in every path, the Host check and the same
security headers. A session is `lmt live --jsonl --save-audio` in a process of its own, so it behaves exactly as on
the command line; Stop sends it SIGINT, as Ctrl-C does, and it saves its outputs. Its events reach the page through
`api/events`, a server-sent event stream. When it ends, `view/` serves F8's page for the saved transcript.
"""

import hmac
import importlib.resources
import json
import secrets
import signal
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lmt import engines
from lmt.demo import server as demo_server
from lmt.errors import InputError

LIVE = [sys.executable, "-m", "lmt", "live"]  # the session's command; tests replace it
PAGE = {
    "": ("live.html", "text/html; charset=utf-8"),
    "live.js": ("live.js", "text/javascript; charset=utf-8"),
    "app.css": ("app.css", "text/css; charset=utf-8"),
    "favicon.svg": ("favicon.svg", "image/svg+xml; charset=utf-8"),
}
STDERR_TAIL = 20  # lines of a failed session's stderr kept for the page


@dataclass
class Session:
    engine: str
    stem: str
    proc: subprocess.Popen
    events: list[str] = field(default_factory=list)  # the JSONL lines, in order
    stderr: list[str] = field(default_factory=list)
    code: int | None = None  # set once the process has exited and its outputs are read
    view: bool = False  # a saved transcript is on view/


class App:
    """The state the handler serves: one session at a time, and F8's page for the last one that saved."""

    def __init__(self, out_dir: Path, *, source: list[str], offline: bool, token: str | None = None):
        self.out_dir = out_dir
        self.source = source  # e.g. ["--device", "2"] or ["--from-file", "x.wav"]
        self.offline = offline
        self.token = token or secrets.token_urlsafe(16)
        self.port = 0
        self.session: Session | None = None
        self.view: demo_server.Demo | None = None
        self.changed = threading.Condition()

    def start(self, engine: str) -> Session:
        if engine not in engines.ENGINES:
            raise ValueError(f"unknown engine {engine!r}")
        with self.changed:
            if self.session is not None and self.session.code is None:
                raise RuntimeError("a session is running")
            stem = "live-" + datetime.now().strftime("%Y%m%d-%H%M%S")
            argv = [*LIVE, "--engine", engine, "--jsonl", "--save-audio", "-o", str(self.out_dir), "-n", stem,
                    *self.source, *(["--offline"] if self.offline else [])]
            # Its own process group, so the terminal's Ctrl-C reaches only the app, which then stops the session
            # with one SIGINT: a second would abandon its save (exit 130).
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", bufsize=1, start_new_session=True)
            self.session = session = Session(engine, stem, proc)
            self.changed.notify_all()
        threading.Thread(target=self._read_stderr, args=(session,), daemon=True).start()
        threading.Thread(target=self._read_events, args=(session,), daemon=True).start()
        return session

    def stop(self) -> bool:
        with self.changed:
            session = self.session
            if session is None or session.code is not None:
                return False
        session.proc.send_signal(signal.SIGINT)  # as Ctrl-C: the session stops and saves
        return True

    def _read_stderr(self, session: Session) -> None:
        for line in session.proc.stderr:
            with self.changed:
                session.stderr = (session.stderr + [line.rstrip("\n")])[-STDERR_TAIL:]

    def _read_events(self, session: Session) -> None:
        for line in session.proc.stdout:
            line = line.strip()
            if line:
                with self.changed:
                    session.events.append(line)
                    self.changed.notify_all()
        code = session.proc.wait()
        view = None
        transcript = self.out_dir / f"{session.stem}.json"
        if transcript.is_file():
            try:
                view = demo_server.load(transcript, self.out_dir / f"{session.stem}.wav", True)
                view.token, view.port = self.token, self.port
            except InputError as e:
                session.stderr = (session.stderr + [f"lmt: error: {e}"])[-STDERR_TAIL:]
        with self.changed:
            if view is not None:
                self.view = view
            session.view = view is not None
            session.code = code
            self.changed.notify_all()

    def state(self) -> dict:
        with self.changed:
            s = self.session
            return {"engines": list(engines.ENGINES), "running": s is not None and s.code is None,
                    "session": None if s is None else {"engine": s.engine, "stem": s.stem, "code": s.code,
                                                       "events": len(s.events), "view": s.view,
                                                       "error": "\n".join(s.stderr) if s.code else None}}


class AppServer(demo_server.Server):
    @property
    def demo(self):
        """F8's handler reads `server.demo`: the last saved session's, on `view/` (its token and port are ours)."""
        return self.app.view


class AppHandler(demo_server.Handler):
    """F8's handler, with the app's page and `api/` in front of it, and F8's routes under `view/`."""

    def __getattr__(self, name):
        if name.startswith("do_"):
            return lambda: self.respond_app(head=False)
        raise AttributeError(name)

    def do_GET(self):
        self.respond_app(head=False)

    def do_HEAD(self):
        self.respond_app(head=True)

    def do_POST(self):
        self.respond_app(head=False)

    def _origins(self):
        port = self.server.app.port
        return (f"127.0.0.1:{port}", f"localhost:{port}")

    def respond_app(self, *, head):
        app = self.server.app
        host = self.headers.get("Host")
        if host is None or host.lower() not in self._origins():
            return self.refuse(403, head=head)
        path = self.path.split("?", 1)[0]
        first, under, rest = path[1:].partition("/")
        if not path.startswith("/") or not under or not hmac.compare_digest(first.encode("latin-1", "replace"),
                                                                            app.token.encode()):
            return self.refuse(404, head=head)
        if rest in ("api/start", "api/stop"):
            return self.command_route(rest, head=head)
        if self.command not in ("GET", "HEAD"):
            return self.refuse(405, head=head, headers=[("Allow", "GET, HEAD")])
        if rest in PAGE:
            name, content_type = PAGE[rest]
            body = (importlib.resources.files("lmt.demo") / "static" / name).read_bytes()
            return self.send(200, content_type, body, head=head)
        if rest == "api/state":
            return self.json(200, app.state(), head=head)
        if rest == "api/events":
            return self.events(head=head)
        if rest.startswith("view/"):
            if app.view is None:
                return self.refuse(404, head=head)
            self.path = f"/{app.token}/{rest[len('view/'):]}"
            return demo_server.Handler.respond(self, head=head)
        return self.refuse(404, head=head)

    def command_route(self, route, *, head):
        """POST only, and only from the app's own page: its Origin must be this server (a cross-site form or
        script on another page can't start the microphone)."""
        if self.command != "POST":
            return self.refuse(405, head=head, headers=[("Allow", "POST")])
        origin = self.headers.get("Origin", "")
        if origin.lower() not in tuple(f"http://{o}" for o in self._origins()):
            return self.refuse(403, head=head)
        app = self.server.app
        length = min(int(self.headers.get("Content-Length") or 0), 4096)
        body = self.rfile.read(length) if length else b""
        if route == "api/stop":
            return self.json(200 if app.stop() else 409, app.state(), head=head)
        try:
            engine = json.loads(body or b"{}").get("engine", "kyutai")
            app.start(engine)
        except (ValueError, AttributeError):
            return self.json(400, {"error": "engine must be one of " + ", ".join(engines.ENGINES)}, head=head)
        except RuntimeError as e:
            return self.json(409, {"error": str(e)}, head=head)
        return self.json(200, app.state(), head=head)

    def json(self, status, doc, *, head):
        return self.send(status, "application/json", json.dumps(doc).encode("utf-8"), head=head)

    def events(self, *, head):
        """The current session's events as server-sent events, from `Last-Event-ID` (or the first) on, then an
        `end` event once the session has exited and its outputs are read."""
        app = self.server.app
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        if head:
            return
        session = app.session
        if session is None:
            return
        try:
            sent = int(self.headers.get("Last-Event-ID", "-1")) + 1
        except ValueError:
            sent = 0
        try:
            while True:
                with app.changed:
                    while len(session.events) <= sent and session.code is None:
                        app.changed.wait(timeout=15)
                        if len(session.events) <= sent and session.code is None:
                            break  # a keep-alive comment below
                    new = session.events[sent:]
                    done = session.code is not None and sent + len(new) >= len(session.events)
                for i, line in enumerate(new, start=sent):
                    self.wfile.write(f"id: {i}\ndata: {line}\n\n".encode())
                sent += len(new)
                if done:
                    end = {"code": session.code, "view": session.view,
                           "error": "\n".join(session.stderr) if session.code else None}
                    self.wfile.write(f"event: end\ndata: {json.dumps(end)}\n\n".encode())
                    self.wfile.flush()
                    return
                if not new:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def make_server(app: App, port: int) -> AppServer:
    """F8's server, on 127.0.0.1:`port`, with the app on it. A port in use is InputError (exit 3)."""
    import errno

    try:
        server = AppServer(("127.0.0.1", port), AppHandler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            raise InputError(f"--port {port}", "already in use") from None
        raise
    server.daemon_threads = True
    server.app = app
    app.port = server.server_address[1]
    return server


def cli_app(args) -> int:
    """Serve the app until Ctrl-C or SIGTERM; a running session is stopped and saved first. Exits 0."""
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source = (["--from-file", args.from_file] if args.from_file else []) + \
             (["--device", args.device] if args.device is not None else [])
    app = App(out_dir, source=source, offline=args.offline)
    server = make_server(app, args.port)
    previous = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, demo_server._stop)
        url = f"http://127.0.0.1:{app.port}/{app.token}/"
        print(url, flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        server.serve_forever()
    except (KeyboardInterrupt, demo_server._Stop):
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        if app.stop():
            app.session.proc.wait(timeout=120)
        server.server_close()
    return 0
