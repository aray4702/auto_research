"""G5-4 (F12): `lmt app`'s page in headless Chrome, with `tests/fake_live.py` standing in for `lmt live`."""

import sys
import threading
from pathlib import Path

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.ui import WebDriverWait

from lmt.demo import app as lmt_app

FAKE = Path(__file__).resolve().parents[1] / "fake_live.py"


@pytest.fixture
def app_url(tmp_path, monkeypatch):
    monkeypatch.setattr(lmt_app, "LIVE", [sys.executable, str(FAKE)])
    (tmp_path / "out").mkdir()
    app = lmt_app.App(tmp_path / "out", source=[], offline=True)
    server = lmt_app.make_server(app, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{app.port}/{app.token}/"
    server.shutdown()
    server.server_close()


@pytest.mark.browser
@pytest.mark.criterion("G5-4")
def test_start_shows_labelled_lines_then_the_saved_transcript_opens(driver, app_url):
    driver.get(app_url)
    wait = WebDriverWait(driver, 30)
    assert driver.find_element(By.ID, "engine").get_attribute("value") == "kyutai"
    driver.find_element(By.ID, "start").click()
    line = wait.until(ec.visibility_of_element_located((By.CSS_SELECTOR, "#lines li")))
    wait.until(lambda d: "okay so" in line.text)
    assert line.text == "Speaker 1: okay so"
    link = wait.until(ec.visibility_of_element_located((By.ID, "view")))
    assert driver.find_element(By.ID, "status").text == "Saved"
    assert not driver.find_element(By.ID, "start").get_attribute("disabled")
    link.click()
    wait.until(ec.visibility_of_element_located((By.CSS_SELECTOR, "#segments li")))
    assert "okay so" in driver.find_element(By.ID, "segments").text
    errors = [e for e in driver.get_log("browser") if e["level"] == "SEVERE"]
    assert not errors, errors
