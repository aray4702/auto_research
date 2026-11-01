"""Playing from a clicked or keyed segment (design/internal/g3-stage1.md §3, 3b): for the first, the middle and
the last segment, the audio starts near the segment's start, moves on, and that segment alone is marked
current. Marked `browser`."""

import time

import pytest
from demo_process import TRANSCRIPT
from fixtures.segments import play_targets
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from browser.test_page import loaded
from lmt.transcript import load

START_LIMIT = 2  # seconds, polled every POLL, for the audio to play near the segment's start
POLL = 0.1
EARLY, LATE = 0.05, 1.0  # currentTime's window around the segment's start
MOVED_AFTER, MOVED_BY = 1, 0.5  # 1 s after t0, currentTime is at least t0 + 0.5


def audio_state(driver):
    return driver.execute_script("const a = document.getElementById('audio'); return [a.paused, a.currentTime]")


def assert_plays_from(driver, segments, i):
    """The checks after a click or Enter on segment `i`'s button; pauses the audio at the end."""
    start = segments[i].start

    def playing_near_start(d):
        paused, now = audio_state(d)
        return not paused and start - EARLY <= now <= start + LATE

    try:
        WebDriverWait(driver, START_LIMIT, poll_frequency=POLL).until(playing_near_start)
    except TimeoutException:
        paused, now = audio_state(driver)
        pytest.fail(f"segment {i}: not playing within [{start - EARLY}, {start + LATE}] after {START_LIMIT} s "
                    f"(paused={paused}, currentTime={now})")
    t0 = audio_state(driver)[1]
    time.sleep(MOVED_AFTER)
    paused, now = audio_state(driver)
    assert now >= t0 + MOVED_BY, (i, t0, now, paused)
    marked = driver.find_elements(By.CSS_SELECTOR, '#segments .segment[aria-current="true"]')
    assert [b.get_attribute("data-index") for b in marked] == [str(i)], i
    driver.execute_script("document.getElementById('audio').pause()")


@pytest.mark.browser
@pytest.mark.criterion("G3-5")
def test_click_plays_from_the_segment(driver, page, tmp_path):
    with page(driver, tmp_path) as demo:
        segments = load(demo.dir / TRANSCRIPT).segments
        loaded(driver, len(segments))
        buttons = driver.find_elements(By.CSS_SELECTOR, "#segments .segment")
        for i in play_targets(segments):
            buttons[i].click()
            assert_plays_from(driver, segments, i)


@pytest.mark.browser
@pytest.mark.criterion("G3-5")
def test_tab_and_enter_play_from_the_segment(driver, page, tmp_path):
    with page(driver, tmp_path) as demo:
        segments = load(demo.dir / TRANSCRIPT).segments
        loaded(driver, len(segments))
        middle = play_targets(segments)[1]
        target = driver.find_elements(By.CSS_SELECTOR, "#segments .segment")[middle]
        # From the page's start: every segment's button and the audio's controls are the only stops before it.
        for _ in range(len(segments) + 20):
            if driver.execute_script("return document.activeElement") == target:
                break
            ActionChains(driver).send_keys(Keys.TAB).perform()
        assert driver.execute_script("return document.activeElement") == target, "Tab never reached the middle segment"
        ActionChains(driver).send_keys(Keys.ENTER).perform()
        assert_plays_from(driver, segments, middle)
