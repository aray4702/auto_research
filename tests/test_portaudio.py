"""sounddevice loads PortAudio (design/internal/g2-stage1.md §4, item 4a). No model.

On Linux PortAudio is the system's libportaudio2, which the test job installs; on macOS it is the library
bundled in sounddevice's wheel. Under CI it must load. Elsewhere a machine without it is skipped, saying so.
"""

import os

import pytest

pytestmark = pytest.mark.criterion("G2-5")


def test_sounddevice_loads_portaudio():
    try:
        import sounddevice
    except OSError as e:  # sounddevice is installed, but PortAudio isn't (Linux without libportaudio2)
        message = f"can't load PortAudio ({e}); on Linux, install libportaudio2"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    version, text = sounddevice.get_portaudio_version()
    assert version > 0
    assert text.startswith("PortAudio V19")
