"""Apple's engine: the Swift helper lmt-speech, built from source into the cache and called over pipes
(design/architecture.md, "Speech engines (G4)"; design/internal/g4-stage1.md §3).

The helper's only input is the samples decode produced, on its stdin, and its command line is fixed here:
no shell and no path from the user (G4-7). Only `download` sends `install` (G4-7).
"""

import contextlib
import hashlib
import json
import os
import platform
import queue
import shutil
import subprocess
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import numpy as np

from lmt import models
from lmt.errors import ModelError
from lmt.transcript import Word

try:  # item 3's interface (design §1)
    from lmt.engines import Piece, words
except ImportError:  # until it merges, the same shapes, for Apple's whole-word pieces
    @dataclass(frozen=True)
    class Piece:
        text: str
        start: float
        end: float
        starts_word: bool
        probability: float

    def words(pieces):
        return [Word(p.start, p.end, p.text.strip(), p.probability) for p in pieces]

SUBJECT = "--engine apple"
LOCALE = "en-US"
SOURCE = ("helper", "lmt-speech", "main.swift")  # under the lmt package, shipped as package data
XCRUN = "/usr/bin/xcrun"
XCODE_SELECT = "/usr/bin/xcode-select"
BUILD_TIMEOUT = 300  # seconds
WRITE_SIZE = 1 << 20  # stdin is written in 1 MB writes
STDERR_LINES = 20

# G4-2's reasons, each with what to do (design/public-api.md, exit code 5).
OLD_MACOS = "needs macOS 26 or later (this Mac has macOS {}); use --engine kyutai"
NO_COMPILER = "needs Xcode's Command Line Tools to build its helper; run xcode-select --install"
REASONS = {
    "unsupported": f"Apple's recognizer doesn't support {LOCALE} on this Mac; use --engine kyutai",
    "not-installed": f"Apple's {LOCALE} speech assets aren't installed; run lmt models download --engine apple",
    "denied": "speech recognition is denied in System Settings, Privacy & Security; allow it, or use "
              "--engine kyutai",
}


class HelperError(RuntimeError):
    """The helper failed, or broke its protocol: an internal error (exit 1), with its stderr's last lines."""


def source() -> bytes:
    return resources.files("lmt").joinpath(*SOURCE).read_bytes()


def binary_path(text=None) -> Path:
    """LMT_CACHE_DIR/helper/<main.swift's SHA-256>/lmt-speech, so a changed source is a new directory."""
    digest = hashlib.sha256(source() if text is None else text).hexdigest()
    return models.cache_dir() / "helper" / digest / "lmt-speech"


def macos_version() -> str:
    return platform.mac_ver()[0]


def _check_platform():
    version = macos_version()
    if not version or int(version.split(".")[0]) < 26:
        raise ModelError(SUBJECT, OLD_MACOS.format(version or "none: this isn't a Mac"))


def build() -> Path:
    """The helper's binary, built first if it isn't in the cache. Nothing is written outside LMT_CACHE_DIR
    (architecture, "Nothing outside the cache (Q8)"), and no half-built binary is ever in place."""
    text = source()
    binary = binary_path(text)
    if binary.is_file():
        return binary
    _check_platform()
    # Checked before swiftc, which without the Command Line Tools opens macOS's install dialog.
    if subprocess.run([XCODE_SELECT, "-p"], capture_output=True, check=False).returncode != 0:
        raise ModelError(SUBJECT, NO_COMPILER)
    work = binary.parent.with_name(f"{binary.parent.name}.build-{uuid.uuid4().hex[:12]}")
    try:
        (work / "tmp").mkdir(parents=True)
        (work / "module-cache").mkdir()
        (work / "main.swift").write_bytes(text)
        env = dict(os.environ, TMPDIR=str(work / "tmp"), CLANG_MODULE_CACHE_PATH=str(work / "module-cache"))
        command = [XCRUN, "swiftc", "-O", "-module-cache-path", str(work / "module-cache"),
                   "-o", str(work / "lmt-speech"), str(work / "main.swift")]
        try:
            r = subprocess.run(command, env=env, cwd=work, stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=BUILD_TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            raise ModelError(SUBJECT, f"its helper didn't build in {BUILD_TIMEOUT} s") from None
        if r.returncode != 0:
            lines = [line for line in r.stderr.splitlines() if line.strip()]
            raise ModelError(SUBJECT, f"its helper didn't build: {lines[-1] if lines else f'exit {r.returncode}'}")
        binary.parent.mkdir(parents=True, exist_ok=True)
        os.replace(work / "lmt-speech", binary)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return binary


class _Helper:
    """One running lmt-speech: a reader thread parses stdout's lines into a queue, a stderr thread keeps the
    last lines, and a writer thread feeds stdin."""

    END = object()  # stdout has closed

    def __init__(self, binary, verb, on_exit=None):
        self.verb = verb
        self.on_exit = on_exit
        self.proc = subprocess.Popen([str(binary), verb, "--locale", LOCALE], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines = queue.Queue()
        self.stderr = deque(maxlen=STDERR_LINES)
        self.blocks = queue.Queue()
        self.returncode = None
        self.threads = [threading.Thread(target=f, daemon=True) for f in (self._read, self._read_stderr,
                                                                           self._write)]
        for thread in self.threads:
            thread.start()

    def _read(self):
        for raw in self.proc.stdout:
            try:
                self.lines.put(json.loads(raw))
            except ValueError:
                self.lines.put(HelperError(f"lmt-speech {self.verb}: a line that isn't JSON: {raw[:200]!r}"))
        self.lines.put(self.END)

    def _read_stderr(self):
        for raw in self.proc.stderr:
            self.stderr.append(raw.decode("utf-8", "replace").rstrip())

    def _write(self):
        with contextlib.suppress(OSError):  # a helper that has gone; its stdout says why
            while (data := self.blocks.get()) is not None:
                for i in range(0, len(data), WRITE_SIZE):
                    self.proc.stdin.write(data[i:i + WRITE_SIZE])
                self.proc.stdin.flush()
        with contextlib.suppress(OSError):
            self.proc.stdin.close()

    def write(self, data: bytes):
        self.blocks.put(data)

    def close_stdin(self):
        self.blocks.put(None)

    def next(self, wait=True):
        """The next parsed line, END once stdout has closed, or None if nothing is there and not `wait`."""
        try:
            line = self.lines.get(block=wait)
        except queue.Empty:
            return None
        if isinstance(line, HelperError):
            raise self.failure(str(line))
        return line

    def wait(self) -> int:
        """Reaps the helper, with os.wait4 for its peak RSS (bytes on macOS), and gives its exit code."""
        if self.returncode is None:
            _, status, usage = os.wait4(self.proc.pid, 0)
            self.returncode = self.proc.returncode = os.waitstatus_to_exitcode(status)
            if self.on_exit:
                self.on_exit(usage.ru_maxrss)
            self.threads[1].join(timeout=5)
        return self.returncode

    def failure(self, problem) -> HelperError:
        """An internal error ends the helper, so its stderr is complete for the report."""
        self.close()
        tail = " | ".join(self.stderr)
        return HelperError(f"{problem}; its stderr: {tail}" if tail else problem)

    def close(self):
        if self.returncode is None:
            with contextlib.suppress(OSError):
                self.proc.kill()
            self.blocks.put(None)
            self.wait()


def _error(line, helper):
    """An error line: G4-2's reasons are exit 5, `failed` or anything else an internal error."""
    helper.wait()
    if line.get("code") in REASONS:
        raise ModelError(SUBJECT, REASONS[line["code"]])
    return helper.failure(f"lmt-speech {helper.verb} failed: {line.get('code')}: {line.get('message')}")


def _check(status):
    if status.get("supported") is not True:
        raise ModelError(SUBJECT, REASONS["unsupported"])
    if status.get("installed") is not True:
        raise ModelError(SUBJECT, REASONS["not-installed"])


def _run(binary, verb) -> dict:
    """`status` or `install`: their one status line, after a clean exit."""
    helper = _Helper(binary, verb)
    try:
        helper.close_stdin()
        status = None
        while (line := helper.next()) is not _Helper.END:
            if line.get("type") == "error":
                raise _error(line, helper)
            if line.get("type") != "status" or status is not None:
                raise helper.failure(f"lmt-speech {verb}: an unexpected line: {line}")
            status = line
        if helper.wait() != 0 or status is None:
            raise helper.failure(f"lmt-speech {verb} exited {helper.returncode} without a status line")
        return status
    finally:
        helper.close()


def version_record(status) -> dict:
    """models.asr for Apple's engine (design/public-api.md, transcript JSON 1.2)."""
    return {"name": "SpeechTranscriber", "engine": "apple", "repository": "com.apple.Speech",
            "revision": f"macOS {status['os']} ({status['build']}) {status['locale']}"}


class AppleStream:
    """One `transcribe` helper. `feed` never waits; `volatile` is the latest provisional text, or None once a
    final result covers it (item 5's terminal line)."""

    def __init__(self, engine):
        self._helper = _Helper(engine.binary, "transcribe", on_exit=engine._saw_rss)
        self.done_until = 0.0
        self.volatile = None
        self._done = False

    def feed(self, block) -> list[Piece]:
        self._helper.write(np.asarray(block).astype("<f4").tobytes())
        return self._drain(wait=False)

    def finish(self) -> list[Piece]:
        self._helper.close_stdin()
        return self._drain(wait=True)

    def _drain(self, wait) -> list[Piece]:
        pieces = []
        while not self._done and (line := self._helper.next(wait=wait)) is not None:
            if line is _Helper.END:
                self._helper.wait()
                raise self._helper.failure(f"lmt-speech transcribe exited {self._helper.returncode} "
                                           "without done")
            kind = line.get("type")
            if kind == "status":
                _check(line)
            elif kind == "volatile":
                self.volatile = line["text"]
            elif kind == "final":
                pieces += [Piece(" " + w["text"], round(w["start"], 3), round(w["end"], 3), True,
                                 float(w["confidence"])) for w in line["words"]]
                self.done_until = max(self.done_until, round(line["end"], 3))
                self.volatile = None  # the next volatile line starts after this result
            elif kind == "done":
                self._done = True
                if self._helper.wait() != 0:
                    raise self._helper.failure(f"lmt-speech transcribe exited {self._helper.returncode}")
            elif kind == "error":
                raise _error(line, self._helper)
            else:
                raise self._helper.failure(f"lmt-speech transcribe: an unexpected line: {line}")
        return pieces

    def close(self):
        self._helper.close()


class AppleEngine:
    name = "apple"
    word_gap = 0.0  # each final word is complete once it is out

    def __init__(self, binary, status):
        self.binary = binary
        self.info = version_record(status)
        self.peak_rss_bytes = 0

    def _saw_rss(self, rss):
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss)

    def stream(self) -> AppleStream:
        return AppleStream(self)

    def transcribe(self, audio) -> list[Word]:
        stream = self.stream()
        try:
            pieces = stream.feed(audio)
            pieces += stream.finish()
        finally:
            stream.close()
        return words(pieces)


def load(offline=False) -> AppleEngine:
    """Ready to transcribe, or ModelError (exit 5) with G4-2's reason. `offline` changes nothing: Apple's
    engine never downloads, and only `download` asks macOS to install anything."""
    _check_platform()
    binary = build()
    status = _run(binary, "status")
    _check(status)
    return AppleEngine(binary, status)


def download() -> None:
    """`lmt models download --engine apple`: builds the helper and asks macOS for the assets (G4-7)."""
    _check_platform()
    status = _run(build(), "install")
    _check(status)


def status() -> dict:
    """`lmt models list`'s row: the same status as load's (G4-2), and the macOS version. It never raises, so
    `models list` exits 0 (G4-2) even when the helper fails: the row then names the internal error."""
    version = macos_version()
    try:
        engine = load()
    except ModelError as e:
        return {"name": "apple", "status": e.problem, "ok": False, "os": version}
    except (HelperError, OSError) as e:  # a helper that crashed, broke its protocol, or wouldn't start
        return {"name": "apple", "status": f"internal error: {type(e).__name__}: {e}", "ok": False,
                "os": version}
    return {"name": "apple", "status": "supported, installed, helper built", "ok": True,
            "os": engine.info["revision"]}
