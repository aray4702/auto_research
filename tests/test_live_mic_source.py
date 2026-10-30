"""`MicSource` through the stand-in: G2-5 (i) resampling and (iii) the silent device, the slow consumer, and
`limit`, `stop()`, `position` and `reason` as for `FileSource` (design/internal/g2-stage1.md §4, item 4d).
No model.
"""

import threading
import wave
from pathlib import Path

import av
import numpy as np
import pytest
from fake_sounddevice import FakeSoundDevice, device

from lmt import audio
from lmt.live.mic import Device, MicSource
from lmt.live.source import BLOCK

pytestmark = pytest.mark.criterion("G2-5")

CLIP = Path(__file__).parent / "fixtures" / "clip.wav"
RATE = 48000
WARNING = "Mic: no sound for 3 s; the microphone may be blocked (System Settings, Privacy & Security, Microphone)"
WAIT = 60  # seconds; generous, for a loaded CI runner


def mic(channels=2):
    return Device(0, "Mic", channels, RATE, True)


def fake(data, blocksize=512, **kwargs):
    return FakeSoundDevice([device("Mic", 2, rate=RATE)], data=data, blocksize=blocksize, **kwargs)


def stop_when_delivered(src, backend):
    """From another thread, as a signal would: `stop()` once the stand-in has delivered every block."""

    def run():
        while not backend.streams:
            threading.Event().wait(0.001)
        assert backend.streams[0].done.wait(WAIT)
        src.stop()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def take_all(src, backend):
    thread = stop_when_delivered(src, backend)
    blocks = list(src.blocks())
    thread.join(WAIT)
    return blocks


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    """`X`: the clip resampled to 48 kHz with PyAV, duplicated to two channels and quantized to 16 bits, as
    int16 (frames, 2); and what `audio.decode` makes of `X` written as a 48 kHz stereo 16-bit WAV."""
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    with av.open(str(CLIP)) as container:
        frames = [f for frame in container.decode(audio=0) for f in resampler.resample(frame)]
    frames += resampler.resample(None)
    mono = np.concatenate([f.to_ndarray().reshape(-1) for f in frames])
    x = np.stack([mono, mono], axis=1).astype(np.int16)
    wav = tmp_path_factory.mktemp("mic") / "x.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(x.astype("<i2").tobytes())
    return x, audio.decode(wav)


@pytest.mark.parametrize("blocksize", [512, 1000])
def test_resampling_matches_decoding_the_same_samples(recording, blocksize):
    """G2-5 (i)."""
    x, expected = recording
    backend = fake(x.astype(np.float32) / 32768, blocksize=blocksize)
    src = MicSource(mic(), backend=backend, warn=lambda m: None)
    blocks = take_all(src, backend)
    stream = backend.streams[0]
    assert (stream.device, stream.samplerate, stream.channels, stream.dtype) == (0, RATE, 2, "float32")
    assert stream.closed
    assert all(len(b) == BLOCK for b in blocks[:-1]) and 0 < len(blocks[-1]) <= BLOCK
    got = np.concatenate(blocks)
    assert got.dtype == np.float32
    assert len(got) == len(expected)
    assert np.max(np.abs(got - expected)) <= 1e-4
    assert src.position == len(expected)
    assert src.reason == "signal"


def speech(seconds, start=0.0):
    t = np.arange(int(seconds * RATE)) / RATE + start
    return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def two_channels(mono):
    return np.stack([mono, mono], axis=1)


def test_silence_then_speech_warns_once_and_capture_goes_on():
    """G2-5 (iii): exact zeros for 3 s, then speech."""
    data = two_channels(np.concatenate([np.zeros(3 * RATE, np.float32), speech(2)]))
    warnings = []
    backend = fake(data)
    src = MicSource(mic(), backend=backend, warn=warnings.append)
    got = np.concatenate(take_all(src, backend))
    assert warnings == [WARNING]
    assert len(got) == 5 * audio.SAMPLE_RATE
    assert np.any(got[-audio.SAMPLE_RATE:])  # the speech after the warning was captured


def test_one_sample_at_2_9_s_means_no_warning():
    mono = np.zeros(5 * RATE, np.float32)
    mono[int(2.9 * RATE)] = 0.001
    warnings = []
    backend = fake(two_channels(mono))
    src = MicSource(mic(), backend=backend, warn=warnings.append)
    take_all(src, backend)
    assert warnings == []


def test_a_device_silent_throughout_warns_once_and_ends_normally():
    warnings = []
    backend = fake(np.zeros((10 * RATE, 2), np.float32))
    src = MicSource(mic(), limit=8 * audio.SAMPLE_RATE, backend=backend, warn=warnings.append)
    got = np.concatenate(list(src.blocks()))
    assert warnings == [WARNING]
    assert len(got) == 8 * audio.SAMPLE_RATE and not np.any(got)
    assert src.reason == "duration"
    assert backend.streams[0].closed


def test_the_warning_goes_to_stderr_by_default(capsys):
    backend = fake(np.zeros((4 * RATE, 2), np.float32))
    src = MicSource(mic(), limit=4 * audio.SAMPLE_RATE - 2000, backend=backend)
    list(src.blocks())
    assert capsys.readouterr().err == f"lmt: warning: {WARNING}\n"


def test_limit_trims_at_exactly_limit_samples():
    backend = fake(two_channels(speech(2)))
    src = MicSource(mic(), limit=5000, backend=backend, warn=lambda m: None)
    assert src.position == 0 and src.reason is None
    sizes, positions = [], []
    for block in src.blocks():
        sizes.append(len(block))
        positions.append(src.position)
    assert sizes == [1600, 1600, 1600, 200]
    assert positions == [1600, 3200, 4800, 5000]
    assert src.reason == "duration"
    assert backend.streams[0].closed


def test_capture_starts_when_blocks_is_first_iterated():
    backend = fake(two_channels(speech(1)))
    src = MicSource(mic(), limit=1600, backend=backend, warn=lambda m: None)
    blocks = src.blocks()
    assert not backend.opened
    next(blocks)
    assert backend.opened and backend.streams[0].started


def test_stop_ends_capture_and_hands_over_what_was_captured():
    backend = fake(two_channels(speech(10)), blocksize=480)
    src = MicSource(mic(), backend=backend, warn=lambda m: None)
    blocks = src.blocks()
    for _ in range(3):
        next(blocks)
    src.stop()
    rest = list(blocks)
    assert backend.streams[0].closed
    assert src.reason == "signal"
    assert all(len(b) == BLOCK for b in rest[:-1])
    assert src.position == 3 * BLOCK + sum(len(b) for b in rest)


def test_stop_before_capture_opens_nothing():
    backend = fake(two_channels(speech(1)))
    src = MicSource(mic(), backend=backend, warn=lambda m: None)
    src.stop()
    assert list(src.blocks()) == []
    assert not backend.opened
    assert src.position == 0 and src.reason == "signal"


def test_a_slow_consumer_never_blocks_the_callback_and_every_frame_arrives():
    """The stand-in delivers all 5 s while the consumer holds its first block: the callback only queues."""
    data = two_channels(speech(5))
    backend = fake(data, blocksize=256)
    src = MicSource(mic(), backend=backend, warn=lambda m: None)
    blocks = src.blocks()
    first = next(blocks)
    assert backend.streams[0].done.wait(WAIT)  # every callback returned while the consumer held its block
    src.stop()
    assert len(first) + sum(len(b) for b in blocks) == 5 * audio.SAMPLE_RATE


def test_input_overflows_are_counted():
    backend = fake(two_channels(speech(1)), overflow={1, 4})
    src = MicSource(mic(), backend=backend, warn=lambda m: None)
    take_all(src, backend)
    assert src.overflows == 2


def test_a_mono_device():
    mono = speech(1)
    backend = FakeSoundDevice([device("Mic", 1, rate=RATE)], data=mono.reshape(-1, 1))
    src = MicSource(mic(channels=1), backend=backend, warn=lambda m: None)
    got = np.concatenate(take_all(src, backend))
    assert backend.streams[0].channels == 1
    assert len(got) == audio.SAMPLE_RATE
