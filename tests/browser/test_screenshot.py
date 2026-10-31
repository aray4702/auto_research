"""The screenshot script (design/internal/g3-stage1.md §3, 3d): on the fixture, a PNG from 1,000 to 2,000 pixels
wide and at most 1,000,000 bytes (G3-9's figures; untagged, G3-9 is stage 2's). Marked `browser`; the figures'
check isn't, so the `test` job runs it."""

import struct
import zlib

import pytest
from demo_process import FIXTURES, TRANSCRIPT

from browser import screenshot


def png(width: int, padding: int = 0) -> bytes:
    """A minimal PNG signature and IHDR of `width` × 1, then `padding` bytes."""
    ihdr = struct.pack(">IIBBBBB", width, 1, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return b"\x89PNG\r\n\x1a\n" + chunk + b"\0" * padding


@pytest.mark.browser
def test_screenshot_of_the_fixture(tmp_path):
    out = tmp_path / "demo.png"
    assert screenshot.main(["--transcript", str(FIXTURES / TRANSCRIPT), "--out", str(out)]) == 0
    data = out.read_bytes()
    assert 1000 <= screenshot.png_width(data) <= 2000
    assert len(data) <= 1_000_000


def test_figures_fail_naming_the_figure():
    screenshot.check(png(1600))
    with pytest.raises(SystemExit, match=r"width 999 px"):
        screenshot.check(png(999))
    with pytest.raises(SystemExit, match=r"width 2001 px"):
        screenshot.check(png(2001))
    with pytest.raises(SystemExit, match=r"1000001 bytes, over 1000000"):
        screenshot.check(png(1600, 1_000_001 - len(png(1600))))
