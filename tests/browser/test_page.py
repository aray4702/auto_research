"""The demo page in headless Chrome: its list, its own origin and its console (design/internal/g3-stage1.md §3,
3a). Marked `browser`; the missing-driver test isn't, so the `test` job runs it."""

import pytest
from demo_process import TRANSCRIPT
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from browser import conftest
from lmt.transcript import load

WAIT = 10  # seconds, for the list and the media to load


def clock(seconds: float) -> str:
    """`HH:MM:SS`, rounded down, as the text export writes it."""
    s = int(seconds)
    return f"{s // 3600:02}:{s // 60 % 60:02}:{s % 60:02}"


def loaded(driver, n):
    """Waits until the list has `n` buttons and the audio's metadata and the track have loaded."""
    WebDriverWait(driver, WAIT).until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "#segments .segment")) == n)
    WebDriverWait(driver, WAIT).until(lambda d: d.execute_script(
        "return document.getElementById('audio').readyState >= 1"
        " && document.getElementById('track').readyState === 2"))


@pytest.mark.browser
@pytest.mark.criterion("G3-5")
def test_list(driver, page, tmp_path):
    with page(driver, tmp_path) as demo:
        transcript = load(demo.dir / TRANSCRIPT)
        loaded(driver, len(transcript.segments))
        labels = {s.id: s.label for s in transcript.speakers}
        buttons = driver.find_elements(By.CSS_SELECTOR, "#segments .segment")
        for i, (button, segment) in enumerate(zip(buttons, transcript.segments, strict=True)):
            def text(name, button=button):
                return button.find_element(By.CLASS_NAME, name).get_property("textContent")

            assert button.tag_name == "button", i
            assert text("time") == clock(segment.start), i
            assert text("speaker") == labels[segment.speaker], i
            assert text("text") == segment.text, i
        items = [li.get_property("textContent") for li in driver.find_elements(By.CSS_SELECTOR, "#speakers li")]
        assert len(items) == len(transcript.speakers)
        for item, speaker in zip(items, transcript.speakers, strict=True):
            assert item.startswith(speaker.label + " · "), (item, speaker.label)


@pytest.mark.browser
@pytest.mark.criterion("G3-5")
def test_own_origin_and_no_errors(driver, page, tmp_path):
    with page(driver, tmp_path) as demo:
        loaded(driver, len(load(demo.dir / TRANSCRIPT).segments))
        origin = f"http://127.0.0.1:{demo.port}/"
        navigation = driver.execute_script(
            "return performance.getEntriesByType('navigation').map(e => e.name)")
        resources = driver.execute_script("return performance.getEntriesByType('resource').map(e => e.name)")
        assert navigation and resources
        for name in navigation + resources:
            assert name.startswith(origin), name
        severe = [e for e in driver.get_log("browser") if e["level"] == "SEVERE"]
        assert severe == []


def test_missing_chrome_and_driver_fail_naming_where_they_looked(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("CHROMEWEBDRIVER", str(tmp_path / "driver"))
    monkeypatch.setattr(conftest, "MAC_CHROME", tmp_path / "Google Chrome")
    with pytest.raises(pytest.fail.Exception) as failed:
        conftest.locate()
    message = str(failed.value)
    assert "download neither" in message
    assert "Chrome (looked for google-chrome on PATH" in message
    assert f"chromedriver (looked for {tmp_path / 'driver' / 'chromedriver'} ($CHROMEWEBDRIVER))" in message
    monkeypatch.delenv("CHROMEWEBDRIVER")
    with pytest.raises(pytest.fail.Exception, match=r"chromedriver \(looked for chromedriver on PATH\)"):
        conftest.locate()
