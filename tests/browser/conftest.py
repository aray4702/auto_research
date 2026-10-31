"""The browser tests' driver and served page (design/internal/g3-stage1.md §3, 3a): headless Chrome through
Selenium, on the Chrome and chromedriver already installed. Nothing downloads a browser or a driver (#83,
answer 8a)."""

import contextlib
import os
import shutil
import sys
from pathlib import Path

import pytest

# Before Selenium is imported, so Selenium Manager can neither download nor send usage statistics.
os.environ.setdefault("SE_OFFLINE", "true")
os.environ.setdefault("SE_AVOID_STATS", "true")

from demo_process import DemoProcess
from selenium import webdriver
from selenium.webdriver.chrome.service import Service

MAC_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
ARGUMENTS = ("--headless=new", "--window-size=1280,900", "--autoplay-policy=no-user-gesture-required",
             "--mute-audio", "--no-first-run", "--no-default-browser-check")


def chrome_binary() -> str | None:
    """`google-chrome` on PATH, else, on macOS, the app's binary if it exists."""
    found = shutil.which("google-chrome")
    if found is None and sys.platform == "darwin" and MAC_CHROME.exists():
        found = str(MAC_CHROME)
    return found


def chromedriver_path() -> str | None:
    """`$CHROMEWEBDRIVER/chromedriver` when the variable is set (the runner image), else `chromedriver` on PATH."""
    directory = os.environ.get("CHROMEWEBDRIVER")
    if directory:
        path = Path(directory) / "chromedriver"
        return str(path) if path.is_file() else None
    return shutil.which("chromedriver")


def locate() -> tuple[str, str]:
    """Chrome's and chromedriver's paths. Either missing fails the test, naming where it looked: it never skips."""
    chrome, driver = chrome_binary(), chromedriver_path()
    missing = []
    if chrome is None:
        where = "google-chrome on PATH" + (f", then {MAC_CHROME}" if sys.platform == "darwin" else "")
        missing.append(f"Chrome (looked for {where})")
    if driver is None:
        directory = os.environ.get("CHROMEWEBDRIVER")
        where = f"{Path(directory) / 'chromedriver'} ($CHROMEWEBDRIVER)" if directory else "chromedriver on PATH"
        missing.append(f"chromedriver (looked for {where})")
    if missing:
        pytest.fail("the browser tests need an installed Chrome and chromedriver, and download neither; not found: "
                    + "; ".join(missing), pytrace=False)
    return chrome, driver


@pytest.fixture
def driver():
    """Headless Chrome with its console log kept, through the installed chromedriver."""
    chrome, chromedriver = locate()
    options = webdriver.ChromeOptions()
    for a in ARGUMENTS:
        options.add_argument(a)
    options.binary_location = chrome
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    service = Service(executable_path=chromedriver)  # an explicit path: Selenium Manager isn't called
    browser = webdriver.Chrome(options=options, service=service)
    try:
        yield browser
    finally:
        browser.quit()


@contextlib.contextmanager
def _open_page(driver, demo_dir, *args):
    with DemoProcess(demo_dir, *args) as demo:
        driver.get(demo.url)
        yield demo


@pytest.fixture
def page():
    """`page(driver, demo_dir, *args)`: a context manager that starts `lmt demo <args>` through `DemoProcess`
    (the copied fixture with no args) and opens its URL in `driver`, giving the `DemoProcess`."""
    return _open_page
