"""`lmt demo`'s server (g3-stage1.md §2a).

`Demo`, `wav_bytes` and `load` are 2a2's, `make_server` and `Handler` 2a3's, and `cli_demo` 2a4's.
"""

import errno
import hmac
import importlib.resources
import io
import re
import secrets
import signal
import sys
import threading
import wave
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy

from lmt import audio as lmt_audio
from lmt import transcript as lmt_transcript
from lmt.demo import sample
from lmt.errors import InputError
from lmt.export import vtt


@dataclass
class Demo:
    transcript: Path
    audio: bytes                 # the whole WAV file
    token: str
    port: int = 0                # set once bound
    last_warning: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def wav_bytes(samples: numpy.ndarray) -> bytes:
    """16-bit PCM, 16 kHz, mono: the samples clipped to [-1, 1], times 32767, rounded, little-endian."""
    pcm = numpy.round(numpy.clip(numpy.asarray(samples, dtype=numpy.float64), -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(lmt_audio.SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def load(transcript: Path, audio: Path | None, audio_option: bool) -> Demo:
    """The demo for a transcript: its audio is `audio` (given by --audio when `audio_option`), or by
    default `source.file` in the transcript's directory. Every failure is InputError (exit 3)."""
    transcript = Path(transcript)
    t = lmt_transcript.load(transcript)
    if audio is None:
        audio = transcript.parent / t.source["file"]
    audio = Path(audio)
    try:
        samples = lmt_audio.decode(audio)
    except InputError as e:
        if audio_option:
            raise InputError(f"--audio {audio}", e.problem) from None
        raise InputError(str(audio), f"{e.problem}; give the recording with --audio") from None
    lasts = len(samples) / lmt_audio.SAMPLE_RATE
    says = t.source["duration"]
    if abs(lasts - says) > 1:
        print(f"lmt: warning: {audio}: lasts {round(lasts, 2):g} s, but the transcript says {says:g} s",
              file=sys.stderr, flush=True)
    return Demo(transcript=transcript, audio=wav_bytes(samples), token=secrets.token_urlsafe(16))


def make_server(demo: Demo, port: int) -> ThreadingHTTPServer:
    """The server on 127.0.0.1:`port` (0 for any free one), with `demo` on it and its bound port in
    `demo.port`. A port in use is InputError (exit 3)."""
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            raise InputError(f"--port {port}", "already in use") from None
        raise
    server.daemon_threads = True
    server.demo = demo
    demo.port = server.server_address[1]
    return server


SECURITY_HEADERS = (
    ("Content-Security-Policy", "default-src 'self'"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
)
STATIC = {
    "": ("index.html", "text/html; charset=utf-8"),
    "app.js": ("app.js", "text/javascript; charset=utf-8"),
    "app.css": ("app.css", "text/css; charset=utf-8"),
    "favicon.svg": ("favicon.svg", "image/svg+xml; charset=utf-8"),
}
RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class Handler(BaseHTTPRequestHandler):
    """GET and HEAD under `/<token>/`, from a page on 127.0.0.1 or localhost only (§2a, "The handler")."""

    def __getattr__(self, name):
        # BaseHTTPRequestHandler answers a method with no do_<METHOD> with 501; every one but GET and HEAD
        # is 405 here, after the Host check.
        if name.startswith("do_"):
            return lambda: self.respond(head=False)
        raise AttributeError(name)

    def do_GET(self):
        self.respond(head=False)

    def do_HEAD(self):
        self.respond(head=True)

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        super().end_headers()

    def send(self, status, content_type, body, *, head, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def refuse(self, status, *, head, headers=()):
        self.send(status, "text/plain; charset=utf-8", f"{status} {self.responses[status][0]}\n".encode(),
                  head=head, headers=headers)

    def respond(self, *, head):
        demo = self.server.demo
        host = self.headers.get("Host")
        if host is None or host.lower() not in (f"127.0.0.1:{demo.port}", f"localhost:{demo.port}"):
            return self.refuse(403, head=head)
        if self.command not in ("GET", "HEAD"):
            return self.refuse(405, head=head, headers=[("Allow", "GET, HEAD")])
        path = self.path.split("?", 1)[0]
        first, under, rest = path[1:].partition("/")
        if not path.startswith("/") or not under or not hmac.compare_digest(first.encode("latin-1", "replace"),
                                                                            demo.token.encode()):
            return self.refuse(404, head=head)
        if rest in STATIC:
            name, content_type = STATIC[rest]
            body = (importlib.resources.files("lmt.demo") / "static" / name).read_bytes()
            return self.send(200, content_type, body, head=head)
        if rest in ("transcript.json", "transcript.vtt"):
            return self.transcript(rest, head=head)
        if rest == "audio.wav":
            return self.audio(head=head)
        return self.refuse(404, head=head)

    def transcript(self, route, *, head):
        """The transcript, read again on every request (G3-4). An invalid file is 500, with one warning
        on stderr until a good read."""
        demo = self.server.demo
        try:
            t = lmt_transcript.load(demo.transcript)
            if route == "transcript.json":
                try:
                    body, content_type = demo.transcript.read_bytes(), "application/json"
                except OSError as e:
                    raise InputError(str(demo.transcript), f"can't read: {e.strerror}") from None
            else:
                body, content_type = vtt.render(t).encode("utf-8"), "text/vtt; charset=utf-8"
        except InputError as e:
            warning = f"lmt: warning: {e}"
            with demo.lock:
                if warning != demo.last_warning:
                    demo.last_warning = warning
                    print(warning, file=sys.stderr, flush=True)
            return self.send(500, "text/plain; charset=utf-8", str(e).encode("utf-8"), head=head)
        with demo.lock:
            demo.last_warning = None
        return self.send(200, content_type, body, head=head)

    def audio(self, *, head):
        """The WAV, with one byte range if asked for; a header that doesn't parse, or several ranges,
        gets the whole file (RFC 9110 allows it)."""
        data = self.server.demo.audio
        total = len(data)
        ranges = [("Accept-Ranges", "bytes")]
        m = RANGE.fullmatch(self.headers.get("Range", "").strip())
        if not m or m.groups() == ("", ""):
            return self.send(200, "audio/wav", data, head=head, headers=ranges)
        a, b = m.groups()
        if a == "":
            start, end = max(total - int(b), 0), total - 1
            if int(b) == 0:
                start = total
        else:
            start, end = int(a), min(int(b), total - 1) if b else total - 1
            if b and int(b) < start:
                return self.send(200, "audio/wav", data, head=head, headers=ranges)
        if start >= total:
            return self.refuse(416, head=head, headers=ranges + [("Content-Range", f"bytes */{total}")])
        return self.send(206, "audio/wav", data[start:end + 1], head=head,
                         headers=ranges + [("Content-Range", f"bytes {start}-{end}/{total}")])


class _Stop(Exception):
    """Raised by the SIGTERM handler, so SIGTERM stops the server as Ctrl-C does."""


def _stop(signum, frame):
    raise _Stop


def cli_demo(args) -> int:
    """Serve the demo until Ctrl-C or SIGTERM, then exit 0 (§2a, "cli_demo")."""
    if args.sample:
        transcript, audio = sample.prepare(offline=args.offline)
        demo = load(transcript, audio, True)
    else:
        demo = load(Path(args.transcript), args.audio and Path(args.audio), args.audio is not None)
    server = make_server(demo, args.port)
    try:
        url = f"http://127.0.0.1:{demo.port}/{demo.token}/"
        print(url, flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        previous = signal.signal(signal.SIGTERM, _stop)
        try:
            server.serve_forever()
        except (KeyboardInterrupt, _Stop):
            pass
        finally:
            signal.signal(signal.SIGTERM, previous)
    finally:
        server.server_close()
    return 0
