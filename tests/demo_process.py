"""`lmt demo` in a subprocess (design/internal/g3-stage1.md, 2a4's tests): `DemoProcess`, which 2a4's, 2b2's and
3a's tests use, and `run_demo` for a run that ends by itself."""

import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TRANSCRIPT = "two_speakers.transcript.json"
AUDIO = "two_speakers.wav"
URL = re.compile(r"^http://127\.0\.0\.1:(\d+)/([A-Za-z0-9_-]{22})/$")
LIMIT = 10  # seconds: for the URL line, and for the process to exit once stopped


def fixture_dir(tmp_path) -> Path:
    """`<tmp_path>/demo`, holding copies of the transcript fixture and its clip; made on the first call only,
    so a test can change the copies before it starts the demo."""
    directory = Path(tmp_path) / "demo"
    if not directory.is_dir():
        directory.mkdir()
        shutil.copy(FIXTURES / TRANSCRIPT, directory)
        shutil.copy(FIXTURES / AUDIO, directory)
    return directory


class Fresh:
    """A run's fresh HOME, TMPDIR, XDG_CACHE_HOME and working directory, under a new directory in `tmp_path`,
    and its environment: those four, PATH, LANG, PYTHONDONTWRITEBYTECODE=1 and `env`."""

    def __init__(self, tmp_path, env=None):
        self.root = Path(tempfile.mkdtemp(prefix="run", dir=tmp_path))
        self.home, self.tmp, self.xdg, self.work = (self.root / name for name in ("home", "tmp", "xdg", "work"))
        for directory in self.dirs:
            directory.mkdir()
        self.env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "LANG": "C.UTF-8",
            "HOME": str(self.home),
            "TMPDIR": str(self.tmp),
            "XDG_CACHE_HOME": str(self.xdg),
            "PYTHONDONTWRITEBYTECODE": "1",
            **(env or {}),
        }

    @property
    def dirs(self):
        return (self.home, self.tmp, self.xdg, self.work)


def run_demo(tmp_path, *args, env=None, timeout=60) -> subprocess.CompletedProcess:
    """`lmt demo <args> --no-browser`, expected to end by itself (an error), in fresh directories."""
    fresh = Fresh(tmp_path, env)
    return subprocess.run([sys.executable, "-m", "lmt", "demo", *args, "--no-browser"], env=fresh.env,
                          cwd=fresh.work, capture_output=True, text=True, timeout=timeout, check=False)


class DemoProcess:
    """`lmt demo <args> --no-browser` (no `--no-browser` with `browser=True`) in fresh directories, with no args
    meaning the copied fixture (`fixture_dir`). Entering waits for the URL line; exiting sends `stop` (SIGINT, or
    SIGTERM if asked), waits, and asserts exit code 0. Afterwards `stdout` holds what followed the URL line, and
    `stderr` all of stderr."""

    def __init__(self, tmp_path, *args, env=None, stop=signal.SIGINT, browser=False):
        self.dir = fixture_dir(tmp_path)
        self.fresh = Fresh(tmp_path, env)
        self.args = list(args) or [str(self.dir / TRANSCRIPT)]
        self.stop = stop
        self.browser = browser
        self.proc = None
        self.url = self.port = self.token = None
        self.stdout = self.stderr = None

    def __enter__(self):
        argv = [sys.executable, "-m", "lmt", "demo", *self.args, *([] if self.browser else ["--no-browser"])]
        self._err = open(self.fresh.root / "stderr.txt", "w+", encoding="utf-8")
        self.proc = subprocess.Popen(argv, env=self.fresh.env, cwd=self.fresh.work, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=self._err, text=True)
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(self.proc.stdout.readline()), daemon=True).start()
        try:
            line = lines.get(timeout=LIMIT)
        except queue.Empty:
            self._kill()
            raise AssertionError(f"no URL line within {LIMIT} s; stderr: {self.stderr!r}") from None
        m = URL.match(line.rstrip("\n"))
        if not m:
            self._kill()
            raise AssertionError(f"the first stdout line isn't the URL: {line!r}; stderr: {self.stderr!r}")
        self.url, self.port, self.token = m.group(0), int(m.group(1)), m.group(2)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._kill()
            return False
        self.proc.send_signal(self.stop)
        try:
            code = self.proc.wait(LIMIT)
        except subprocess.TimeoutExpired:
            self._kill()
            raise AssertionError(f"still running {LIMIT} s after {self.stop.name}; stderr: {self.stderr!r}") from None
        self._collect()
        assert code == 0, f"exit {code} after {self.stop.name}; stderr: {self.stderr!r}"
        return False

    def _kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self._collect(stdout=False)  # the URL reader may still hold stdout

    def _collect(self, stdout=True):
        if stdout:
            self.stdout = self.proc.stdout.read()
            self.proc.stdout.close()
        self._err.seek(0)
        self.stderr = self._err.read()
        self._err.close()
