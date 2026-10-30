"""The microphone's devices (design/internal/g2-stage1.md §4, item 4c). `sounddevice`, and so PortAudio, is
imported only here, inside the functions, never at the top, so `lmt transcribe` and the rest never load it.
Tests pass `tests/fake_sounddevice.py` as `backend` instead.
"""

from dataclasses import dataclass

from lmt.errors import InputError


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
    elif spec.strip().isdigit():
        found = [d for d in devices if d.index == int(spec)]
    else:
        found = [d for d in devices if d.name == spec]
        if not found:
            found = [d for d in devices if spec.casefold() in d.name.casefold()]
    if len(found) != 1:
        raise InputError("--device", f"no input device '{spec}' (lmt live --list-devices lists them)")
    return found[0]
