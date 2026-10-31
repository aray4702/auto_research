"""The microphone: its devices (item 4c) and `MicSource` (item 4d), design/internal/g2-stage1.md §4.
`sounddevice`, and so PortAudio, is imported only here, inside the functions, never at the top, so
`lmt transcribe` and the rest never load it. Tests pass `tests/fake_sounddevice.py` as `backend` instead.
"""

import queue
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from lmt import audio
from lmt.errors import InputError
from lmt.live.source import BLOCK

SILENT_SECONDS = 3


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    channels: int
    rate: int
    default: bool


def _backend(backend):
    if backend is not None:
        return backend
    try:
        import sounddevice
    except (ImportError, OSError) as e:  # Linux without libportaudio2 raises OSError
        raise InputError("--device", f"can't load PortAudio ({e}); on Linux, install libportaudio2") from None
    return sounddevice


def input_devices(backend=None) -> list[Device]:
    """The devices with an input channel. `channels` is at most 2, so a many-channel interface isn't mixed
    down from inputs that carry nothing; `rate` is the device's default sample rate."""
    backend = _backend(backend)
    default = backend.default.device[0]
    return [
        Device(i, d["name"], min(d["max_input_channels"], 2), int(d["default_samplerate"]), i == default)
        for i, d in enumerate(backend.query_devices())
        if d["max_input_channels"] > 0
    ]


def resolve(spec: str | None, backend=None) -> Device:
    """`None` is the default input device. A number is an index. Otherwise an exact name, else a unique
    case-insensitive substring of one. Every failure is an InputError (3)."""
    devices = input_devices(backend)
    if not devices:
        raise InputError("--device", "no input device found")
    if spec is None:
        found = [d for d in devices if d.default]
        if not found:  # PortAudio names no default input
            raise InputError("--device", "no input device found")
    elif spec.strip().isdecimal():
        found = [d for d in devices if d.index == int(spec)]
    else:
        found = [d for d in devices if d.name == spec]
        if not found:
            found = [d for d in devices if spec.casefold() in d.name.casefold()]
    if len(found) != 1:
        raise InputError("--device", f"no input device '{spec}' (lmt live --list-devices lists them)")
    return found[0]


def print_warning(message: str) -> None:
    print(f"lmt: warning: {message}", file=sys.stderr, flush=True)


class MicSource:
    """The same interface as `FileSource`: `name`, `position`, `reason`, `limit`, `blocks()` and `stop()`.
    The stream opens when `blocks()` is first iterated, which is when capture starts. Its callback only copies
    each block into an unbounded queue, so a slow consumer never makes it drop audio; `overflows` counts the
    blocks PortAudio flagged with an input overflow. `blocks()` resamples to 16 kHz mono and re-blocks into
    `BLOCK` samples. It ends at `limit` samples (reason "duration") or at `stop()` (reason "signal"), when it
    closes the stream, flushes the resampler and hands over what's left."""

    def __init__(self, device: Device, *, limit: int | None = None, backend=None, warn=print_warning):
        self.device = device
        self.name = device.name
        self.position = 0  # samples handed over so far; read from any thread
        self.reason = None  # "duration" or "signal", once it stops
        self.overflows = 0
        self._limit = limit
        self._backend = backend
        self._warn = warn
        self._queue = queue.SimpleQueue()
        self._stopped = threading.Event()

    def _callback(self, indata, frames, time, status):
        if status and status.input_overflow:
            self.overflows += 1
        self._queue.put(indata.copy())

    def blocks(self) -> Iterator[np.ndarray]:
        if self._stopped.is_set():
            self.reason = "signal"
            return
        backend = _backend(self._backend)
        resampler = audio.Resampler(self.device.rate, self.device.channels)
        silence = _SilenceCheck(self.device, self._warn)
        pending = np.zeros(0, np.float32)
        stream = self._open(backend)
        try:
            while not self._stopped.is_set():
                chunk = self._queue.get()
                if chunk is None:  # stop()'s wake-up
                    break
                silence.check(chunk)
                pending = np.concatenate([pending, resampler.push(chunk)])
                while len(pending) >= BLOCK:
                    block, pending = self._take(pending, BLOCK)
                    yield block
                    if self._at_limit():
                        self.reason = "duration"
                        return
        finally:
            stream.stop()
            stream.close()
        # Stopped, and the stream is closed: what the callback queued before then, and the resampler's tail.
        while True:
            try:
                chunk = self._queue.get_nowait()
            except queue.Empty:
                break
            if chunk is not None:
                silence.check(chunk)
                pending = np.concatenate([pending, resampler.push(chunk)])
        pending = np.concatenate([pending, resampler.flush()])
        while len(pending):
            block, pending = self._take(pending, BLOCK)
            yield block
            if self._at_limit():
                self.reason = "duration"
                return
        self.reason = "signal"

    def _open(self, backend):
        """Open and start the stream. A device PortAudio can't open or start (busy, unplugged since `resolve`,
        its rate unsupported) is an InputError (3) naming it, with PortAudio's message."""
        try:
            stream = backend.InputStream(device=self.device.index, samplerate=self.device.rate,
                                         channels=self.device.channels, dtype="float32", callback=self._callback)
        except backend.PortAudioError as e:
            raise self._cant_open(e) from None
        try:
            stream.start()
        except backend.PortAudioError as e:
            stream.close()
            raise self._cant_open(e) from None
        return stream

    def _cant_open(self, error):
        return InputError("--device", f"can't open input device '{self.device.name}' ({error})")

    def _take(self, pending, n):
        n = min(n, len(pending))
        if self._limit is not None:
            n = min(n, self._limit - self.position)
        self.position += n
        return pending[:n], pending[n:]

    def _at_limit(self):
        return self._limit is not None and self.position >= self._limit

    def stop(self) -> None:
        """Thread-safe: capture ends, and what was captured before it is handed over."""
        self._stopped.set()
        self._queue.put(None)


class _SilenceCheck:
    """G2-5 (iii): the device's own samples for their first `SILENT_SECONDS`. If every one is exactly 0.0,
    one warning; capture goes on either way, and nothing is checked after them."""

    def __init__(self, device: Device, warn):
        self._device = device
        self._warn = warn
        self._left = SILENT_SECONDS * device.rate
        self._done = False

    def check(self, chunk: np.ndarray) -> None:
        if self._done:
            return
        head = chunk[:self._left]
        self._left -= len(head)
        if np.any(head):
            self._done = True
        elif self._left <= 0:
            self._done = True
            self._warn(f"{self._device.name}: no sound for {SILENT_SECONDS} s; the microphone may be blocked "
                       "(System Settings, Privacy & Security, Microphone)")
