"""The microphone's devices: `input_devices`, `resolve` and the PortAudio error, through the stand-in, and
G2-5 (ii) for real (design/internal/g2-stage1.md §4, item 4c). No model.

The real case asks the real `sounddevice` for its input devices. In CI, whose runner has no input device, it
must find none. Elsewhere, such as a Mac with a microphone, it is skipped, saying so.
"""

import importlib.abc
import os
import subprocess
import sys
import threading
import time

import numpy as np
import pytest
from fake_sounddevice import FakeSoundDevice, device

from lmt.errors import InputError
from lmt.live import mic
from lmt.live.mic import Device

pytestmark = pytest.mark.criterion("G2-5")

NOT_LISTED = "--device: no input device '{}' (lmt live --list-devices lists them)"


def devices():
    return FakeSoundDevice(
        [
            device("MacBook Pro Speakers", 0, outputs=2),
            device("MacBook Pro Microphone", 1, rate=48000),
            device("USB Audio Interface", 8, outputs=8, rate=44100),
            device("USB Audio Interface (loopback)", 2, rate=44100),
        ],
        default=1,
    )


def test_input_devices_keeps_inputs_marks_the_default_and_caps_channels():
    assert mic.input_devices(devices()) == [
        Device(1, "MacBook Pro Microphone", 1, 48000, True),
        Device(2, "USB Audio Interface", 2, 44100, False),
        Device(3, "USB Audio Interface (loopback)", 2, 44100, False),
    ]


def test_no_input_device():
    """G2-5 (ii) through the stand-in: only an output device."""
    fake = FakeSoundDevice([device("Speakers", 0, outputs=2)])
    assert mic.input_devices(fake) == []
    with pytest.raises(InputError) as e:
        mic.resolve(None, fake)
    assert e.value.exit_code == 3
    assert str(e.value) == "--device: no input device found"
    with pytest.raises(InputError) as e:
        mic.resolve("Speakers", fake)
    assert str(e.value) == "--device: no input device found"
    assert not fake.opened


def test_no_default_input_device():
    """An input device exists, but PortAudio names no default input (-1)."""
    fake = FakeSoundDevice([device("MacBook Pro Microphone", 1)], default=-1)
    assert mic.input_devices(fake) == [Device(0, "MacBook Pro Microphone", 1, 48000, False)]
    with pytest.raises(InputError) as e:
        mic.resolve(None, fake)
    assert e.value.exit_code == 3
    assert str(e.value) == "--device: no input device found"


def test_resolve_the_default():
    assert mic.resolve(None, devices()).name == "MacBook Pro Microphone"


def test_resolve_by_index():
    assert mic.resolve("2", devices()).name == "USB Audio Interface"


def test_resolve_an_output_device_by_index_is_unknown():
    with pytest.raises(InputError) as e:
        mic.resolve("0", devices())
    assert e.value.exit_code == 3
    assert str(e.value) == NOT_LISTED.format("0")


def test_resolve_a_digit_int_cannot_parse_is_unknown():
    """'²' is a digit to str.isdigit() but not to int(): it is an unknown device, not a ValueError."""
    with pytest.raises(InputError) as e:
        mic.resolve("²", devices())
    assert e.value.exit_code == 3
    assert str(e.value) == NOT_LISTED.format("²")


def test_resolve_by_exact_name_before_substring():
    """"USB Audio Interface" is also a substring of the loopback's name; the exact name wins."""
    assert mic.resolve("USB Audio Interface", devices()).index == 2


def test_resolve_by_unique_case_insensitive_substring():
    assert mic.resolve("loopBACK", devices()).index == 3
    assert mic.resolve("microphone", devices()).index == 1


def test_resolve_an_ambiguous_substring():
    with pytest.raises(InputError) as e:
        mic.resolve("usb", devices())
    assert e.value.exit_code == 3
    assert str(e.value) == NOT_LISTED.format("usb")


def test_resolve_an_unknown_name():
    fake = devices()
    with pytest.raises(InputError) as e:
        mic.resolve("AirPods", fake)
    assert e.value.exit_code == 3
    assert str(e.value) == NOT_LISTED.format("AirPods")
    assert not fake.opened


class NoPortAudio(importlib.abc.MetaPathFinder):
    """Makes `import sounddevice` fail as it does on Linux without libportaudio2."""

    def find_spec(self, name, path=None, target=None):
        if name == "sounddevice":
            raise OSError("PortAudio library not found")


def test_portaudio_missing(monkeypatch):
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.setattr(sys, "meta_path", [NoPortAudio(), *sys.meta_path])
    for call in (lambda: mic.input_devices(), lambda: mic.resolve(None)):
        with pytest.raises(InputError) as e:
            call()
        assert e.value.exit_code == 3
        assert str(e.value) == (
            "--device: can't load PortAudio (PortAudio library not found); on Linux, install libportaudio2"
        )


def test_importing_mic_does_not_load_sounddevice():
    code = "import sys, lmt.live.mic; assert 'sounddevice' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_the_stand_ins_stream_delivers_its_blocks_from_its_own_thread():
    data = np.arange(2600, dtype=np.float32).reshape(1300, 2)
    fake = FakeSoundDevice([device("Mic", 2)], data=data, blocksize=512, overflow={1})
    got = []

    def callback(indata, frames, time_info, status):
        got.append((indata, frames, bool(status), threading.current_thread().name))

    assert not fake.opened
    with fake.InputStream(device=0, samplerate=48000, channels=2, dtype="float32", callback=callback) as stream:
        assert stream.done.wait(5)
    assert fake.opened and stream.closed
    assert [g[1] for g in got] == [512, 512, 276]
    assert [g[2] for g in got] == [False, True, False]
    assert all(name != threading.current_thread().name for *_, name in got)
    np.testing.assert_array_equal(np.concatenate([g[0] for g in got]), data)


def test_the_stand_ins_stream_stops_when_asked():
    fake = FakeSoundDevice([device("Mic", 1)], data=np.zeros((48000, 1)), blocksize=10)
    calls = []

    def callback(indata, frames, time_info, status):
        calls.append(frames)
        time.sleep(0.001)

    stream = fake.InputStream(device=0, samplerate=48000, channels=1, dtype="float32", callback=callback)
    stream.start()
    time.sleep(0.02)
    stream.stop()
    n = len(calls)
    assert 0 < n < 4800
    time.sleep(0.02)
    assert len(calls) == n


def test_no_input_device_for_real():
    """G2-5 (ii) for real: the CI runner has no input device."""
    try:
        found = mic.input_devices()
    except InputError as e:
        if os.environ.get("CI"):
            pytest.fail(str(e))
        pytest.skip(str(e))
    if not os.environ.get("CI"):
        pytest.skip(f"not in CI; this machine lists {len(found)} input device(s)")
    assert found == []
