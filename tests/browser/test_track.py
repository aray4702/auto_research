"""The demo page's track after a rename, against Chrome's own WebVTT parser (G3-2), and the markup fixture
(G3-5) (design/internal/g3-stage1.md §3, 3c). Marked `browser`."""

import html
import json
import time

import pytest
import webvtt
from demo_process import TRANSCRIPT, fixture_dir
from fixtures.markup import IMG, SCRIPT, markup_transcript
from selenium.webdriver.common.by import By
from test_rename import NAME1, NAME2

from browser.test_page import loaded
from lmt import cli
from lmt.export import vtt
from lmt.transcript import load

LABELS = {"S1": NAME1, "S2": NAME2}
CUES = """return Array.from(document.getElementById('track').track.cues,
    c => [c.startTime, c.endTime, c.getCueAsHTML().textContent])"""


def milliseconds(timestamp: str) -> int:
    """`HH:MM:SS.mmm` as whole milliseconds."""
    clock, ms = timestamp.split(".")
    h, m, s = (int(x) for x in clock.split(":"))
    return ((h * 60 + m) * 60 + s) * 1000 + int(ms)


def texts(driver, selector, name=None):
    elements = driver.find_elements(By.CSS_SELECTOR, selector)
    if name is not None:
        elements = [e.find_element(By.CLASS_NAME, name) for e in elements]
    return [e.get_property("textContent") for e in elements]


@pytest.mark.browser
@pytest.mark.criterion("G3-2", "G3-5")
def test_track_after_a_rename(driver, page, tmp_path, capsys):
    with page(driver, tmp_path) as demo:
        path = demo.dir / TRANSCRIPT
        n = len(load(path).segments)
        loaded(driver, n)
        pairs = [f"{speaker}={name}" for speaker, name in LABELS.items()]
        assert cli.main(["rename", "-f", "json", str(path), *pairs]) == 0
        _, err = capsys.readouterr()
        assert err == ""
        renamed = load(path)
        assert [s.label for s in renamed.speakers] == [NAME1, NAME2]

        driver.refresh()
        loaded(driver, n)
        labels = {s.id: s.label for s in renamed.speakers}
        assert texts(driver, "#segments .segment", "speaker") == [labels[s.speaker] for s in renamed.segments]
        items = texts(driver, "#speakers li")
        assert len(items) == len(renamed.speakers)
        for item, speaker in zip(items, renamed.speakers, strict=True):
            assert item.startswith(speaker.label + " · "), (item, speaker.label)

        chrome = driver.execute_script(CUES)
        expected = list(webvtt.from_string(vtt.render(renamed)))
        assert len(chrome) == len(expected)
        for i, ((start, end, text), caption) in enumerate(zip(chrome, expected, strict=True)):
            assert round(start * 1000) == milliseconds(caption.start), i
            assert round(end * 1000) == milliseconds(caption.end), i
            assert html.unescape(text) == html.unescape(caption.text), i
        assert any(NAME1 in text for _, _, text in chrome)
        assert any(NAME2 in text for _, _, text in chrome)


@pytest.mark.browser
@pytest.mark.criterion("G3-5")
def test_markup_shows_as_text(driver, page, tmp_path):
    directory = fixture_dir(tmp_path)
    doc = markup_transcript(json.loads((directory / TRANSCRIPT).read_text(encoding="utf-8")))
    path = directory / "markup.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with page(driver, tmp_path, str(path)):
        loaded(driver, len(doc["segments"]))
        assert texts(driver, "#segments .segment", "text")[:2] == [IMG, SCRIPT]
        assert driver.find_elements(By.CSS_SELECTOR, "#segments img, #segments script") == []
        time.sleep(1)
        assert driver.execute_script("return typeof window.__lmtXss") == "undefined"
