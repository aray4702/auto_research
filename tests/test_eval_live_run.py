"""`run_once` and `run_live` on tests/fake_live.py (design/internal/g2-stage1.md §5, item 5b). No model, and no
`lmt live`."""

import json
import sys
from pathlib import Path

import pytest

from lmt import models
from lmt.errors import InputError
from lmt.eval import live

FAKE = Path(__file__).with_name("fake_live.py")


def test_run_once_stamps_each_event_from_the_start_events_arrival(tmp_path):
    log = live.run_once([sys.executable, str(FAKE), "live", "-n", "clip", "-o", str(tmp_path)], tmp_path, "clip")

    assert [event["type"] for _, event in log.lines] == ["start", "words", "stop"]
    arrivals = [arrival for arrival, _ in log.lines]
    assert arrivals[0] == 0.0
    assert arrivals[1] == pytest.approx(0.3, abs=0.1)
    assert arrivals[2] == pytest.approx(0.5, abs=0.1)
    assert log.wall_seconds >= arrivals[2]
    assert log.transcript == json.loads((tmp_path / "clip.json").read_text(encoding="utf-8"))
    words, _ = live.read_events(log.lines)
    assert [w.text for w, _ in words] == ["okay", "so"]


def test_run_once_raises_exit_3_with_stderrs_last_line(tmp_path, monkeypatch):
    monkeypatch.setenv("LMT_FAKE_LIVE_EXIT", "1")

    with pytest.raises(InputError) as raised:
        live.run_once([sys.executable, str(FAKE), "live", "-n", "clip", "-o", str(tmp_path)], tmp_path, "clip")

    assert raised.value.exit_code == 3
    assert str(raised.value) == "lmt live: exited 1: lmt: error: the fake failed"


def test_run_live_ensures_the_download_defaults_then_runs_three_times(tmp_path, monkeypatch):
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("LMT_FAKE_LIVE_ARGV", str(argv_log))
    ensured = []

    def ensure(needed, offline=False):
        ensured.append((needed, offline, argv_log.exists()))
        return {}

    monkeypatch.setattr(models, "ensure", ensure)
    wav, work = tmp_path / "ami.wav", tmp_path / "work"

    logs = live.run_live(wav, work, command_prefix=[sys.executable, str(FAKE)])

    assert ensured == [(models.download_defaults(models.load_manifest()), False, False)]  # before the first run
    argvs = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    assert argvs == [
        ["live", "--from-file", str(wav), "--jsonl", "--offline", "-f", "json", "-n", f"run{i}", "-o", str(work),
         "--overwrite"]
        for i in (1, 2, 3)
    ]
    assert [log.transcript["source"]["file"] for log in logs] == ["run1", "run2", "run3"]


def test_run_live_defaults_to_python_m_lmt(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(models, "ensure", lambda needed, offline=False: {})
    monkeypatch.setattr(live, "run_once", lambda command, out_dir, stem: commands.append(command))

    live.run_live(tmp_path / "ami.wav", tmp_path, runs=1)

    assert commands[0][:4] == [sys.executable, "-m", "lmt", "live"]
