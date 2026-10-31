"""A stand-in for the `sounddevice` module (design/internal/g2-stage1.md §4, item 4c), passed as `backend` to
`lmt.live.mic`. It has `query_devices()`, `default.device`, and an `InputStream` with sounddevice's constructor
and `start`, `stop` and `close`, used as a context manager. The stream calls its callback from its own thread
with the array it was given, in blocks of the size it was given, as `(indata, frames, time, status)`. As
PortAudio does, every callback gets a view of one buffer, overwritten for the next, so a callback that keeps
`indata` without copying it loses its samples. It records whether a stream was opened, so a test can check that
no audio was captured, and it can fail to open, raising `PortAudioError` as sounddevice does.
"""

import threading
from types import SimpleNamespace

import numpy as np


def device(name, inputs, outputs=0, rate=48000.0):
    """One entry of `query_devices()`, with the keys sounddevice gives."""
    return {
        "name": name,
        "max_input_channels": inputs,
        "max_output_channels": outputs,
        "default_samplerate": float(rate),
    }


class PortAudioError(Exception):
    """sounddevice's `PortAudioError`."""


class Status:
    """sounddevice's CallbackFlags, as far as the callback reads them: false unless input overflowed."""

    def __init__(self, input_overflow=False):
        self.input_overflow = input_overflow

    def __bool__(self):
        return self.input_overflow


class FakeSoundDevice:
    """`devices` are `device(...)` dicts, possibly none. `default` is the default input's index; by default
    the first device with an input, else -1, as PortAudio gives with none. A stream delivers `data`, float32
    (frames, channels), in blocks of `blocksize` frames, the last one shorter, then delivers no more; the blocks
    whose numbers are in `overflow` come with the input-overflow flag. With `open_error`, constructing a stream
raises `PortAudioError(open_error)` and opens nothing; with `start_error`, starting it does."""

    PortAudioError = PortAudioError

    def __init__(self, devices=(), *, default=None, data=None, blocksize=512, overflow=(), open_error=None,
                 start_error=None):
        self.devices = [dict(d) for d in devices]
        if default is None:
            default = next((i for i, d in enumerate(self.devices) if d["max_input_channels"] > 0), -1)
        self.default = SimpleNamespace(device=[default, -1])
        self.data = np.zeros((0, 1), np.float32) if data is None else np.asarray(data, np.float32)
        self.blocksize = blocksize
        self.overflow = set(overflow)
        self.open_error = open_error
        self.start_error = start_error
        self.streams = []  # every InputStream constructed, in order

    @property
    def opened(self):
        """Whether any stream was constructed, which is when PortAudio would open the device."""
        return bool(self.streams)

    def query_devices(self):
        return [dict(d, index=i) for i, d in enumerate(self.devices)]

    def InputStream(self, *, device=None, samplerate=None, channels=None, dtype=None, callback=None, **kwargs):
        if self.open_error is not None:
            raise PortAudioError(self.open_error)
        stream = InputStream(self, device, samplerate, channels, dtype, callback, kwargs)
        self.streams.append(stream)
        return stream


class InputStream:
    def __init__(self, fake, device, samplerate, channels, dtype, callback, kwargs):
        self.device = device
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.kwargs = kwargs
        self._fake = fake
        self._callback = callback
        self._thread = None
        self._stopping = threading.Event()
        self.started = False
        self.closed = False
        self.done = threading.Event()  # set once every block has been delivered, or the stream stopped

    def _run(self):
        data, size = self._fake.data, self._fake.blocksize
        buffer = np.empty((size, data.shape[1]), np.float32)  # PortAudio's one buffer, reused for every callback
        for n, start in enumerate(range(0, len(data), size)):
            if self._stopping.is_set():
                break
            block = data[start:start + size]
            buffer[:len(block)] = block
            self._callback(buffer[:len(block)], len(block), None, Status(n in self._fake.overflow))
        self.done.set()

    def start(self):
        if self._fake.start_error is not None:
            raise PortAudioError(self._fake.start_error)
        self.started = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stopping.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()

    def close(self):
        self.stop()
        self.closed = True

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()
        return False
