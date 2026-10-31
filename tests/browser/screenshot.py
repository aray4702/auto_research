"""The demo page's screenshot (design/internal/g3-stage1.md §3, 3d): `lmt demo … --no-browser` through
`DemoProcess`, headless Chrome at 1600 × 1000 and a device scale factor of 1, a segment marked, and the PNG
checked against G3-9's figures. It never shrinks the image to pass.

    uv run --frozen python tests/browser/screenshot.py (--transcript PATH [--audio PATH] | --sample) --out PATH
"""

import argparse
import struct
import sys
import tempfile
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent
if str(TESTS) not in sys.path:  # run as a script: `demo_process` and `browser` live in tests/
    sys.path.insert(0, str(TESTS))

from demo_process import DemoProcess
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from browser import conftest

WINDOW = "--window-size=1600,1000"
WAIT = 10  # seconds, for the list and the audio's metadata to load
PLAYED = 1.5  # seconds between the click and the pause
AT = 0.4  # the clicked segment starts nearest this fraction of the audio's duration
MIN_WIDTH, MAX_WIDTH = 1000, 2000  # pixels (G3-9)
MAX_BYTES = 1_000_000


def png_width(data: bytes) -> int:
    """The width in a PNG's `IHDR` chunk."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG with IHDR first")
    return struct.unpack(">I", data[16:20])[0]


def check(data: bytes) -> None:
    """Fails, naming the figure, unless the width is from MIN_WIDTH to MAX_WIDTH and the size at most MAX_BYTES."""
    width = png_width(data)
    if not MIN_WIDTH <= width <= MAX_WIDTH:
        raise SystemExit(f"screenshot width {width} px is outside {MIN_WIDTH} to {MAX_WIDTH} px")
    if len(data) > MAX_BYTES:
        raise SystemExit(f"screenshot is {len(data)} bytes, over {MAX_BYTES} bytes")


def chrome() -> webdriver.Chrome:
    """Headless Chrome as the browser tests open it, at 1600 × 1000 and a device scale factor of 1."""
    binary, chromedriver = conftest.locate()
    options = webdriver.ChromeOptions()
    for a in conftest.ARGUMENTS:
        options.add_argument(WINDOW if a.startswith("--window-size=") else a)
    options.add_argument("--force-device-scale-factor=1")
    options.binary_location = binary
    return webdriver.Chrome(options=options, service=Service(executable_path=chromedriver))


def mark_a_segment(driver) -> None:
    """Clicks the segment whose start is nearest AT of the way through the audio, waits PLAYED s, and pauses."""
    WebDriverWait(driver, WAIT).until(lambda d: d.find_elements(By.CSS_SELECTOR, "#segments .segment") and
                                      d.execute_script("return document.getElementById('audio').readyState >= 1"))
    target = AT * driver.execute_script("return document.getElementById('audio').duration")
    buttons = driver.find_elements(By.CSS_SELECTOR, "#segments .segment")
    min(buttons, key=lambda b: abs(float(b.get_attribute("data-start")) - target)).click()
    time.sleep(PLAYED)
    driver.execute_script("document.getElementById('audio').pause()")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--transcript", type=Path)
    source.add_argument("--sample", action="store_true")
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.audio and args.sample:
        parser.error("--audio goes with --transcript, not --sample")
    demo_args = ["--sample"] if args.sample else [str(args.transcript.resolve())]
    if args.audio:
        demo_args += ["--audio", str(args.audio.resolve())]
    driver = chrome()
    try:
        with tempfile.TemporaryDirectory() as tmp, DemoProcess(tmp, *demo_args) as demo:
            driver.get(demo.url)
            mark_a_segment(driver)
            data = driver.get_screenshot_as_png()
    finally:
        driver.quit()
    args.out.write_bytes(data)
    check(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
