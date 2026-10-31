"""`lmt live`'s signals, the second Ctrl-C, a bug's exit 1 and `-v`, on fake stages in subprocesses at real-time
pace, with no model (design/internal/g2-stage1.md §3c, item 3c2). Each case is `tests/live_fakes.py live
--from-file <a 30 s WAV> --jsonl -f txt,srt,vtt,json`; the cases run at the same time as each other.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from lmt import schema

pytestmark = pytest.mark.criterion("G2-4")

ROOT = Path(__file__).resolve().parents[1]
RATE = 16000
FORMATS = ("txt", "srt", "vtt", "json")
TIMEOUT = 60


def tone_wav(path, seconds=30.0):
    t = np.arange(int(seconds * RATE)) / RATE
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.round(0.1 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2").tobytes())
    return path


def run_case(wav, out_dir, *, signals=(), env=None, extra=()):
    """Runs one case: after the `start` event, sleeps and sends each `(delay, signal)` in turn. Returns the exit
    code, stdout, stderr and `<stem>.txt` as it was just before the first signal."""
    out_dir.mkdir()
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "live_fakes.py"), "live", "--from-file", str(wav),
                             "--jsonl", "-f", ",".join(FORMATS), "-o", str(out_dir), "-n", "m", *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=out_dir,
                            env={**os.environ, **(env or {})})
    first = proc.stdout.readline()
    text_before = None
    for i, (delay, sig) in enumerate(signals):
        time.sleep(delay)
        if i == 0:
            text_before = (out_dir / "m.txt").read_text(encoding="utf-8")
        proc.send_signal(sig)
    try:
        out, err = proc.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    return {"code": proc.returncode, "out": first + out, "err": err, "dir": out_dir, "text_before": text_before}


CASES = {
    "sigint": {"signals": [(3, signal.SIGINT)]},
    "sigterm": {"signals": [(3, signal.SIGTERM)]},
    "second-sigint": {"signals": [(3, signal.SIGINT), (0.1, signal.SIGINT)], "env": {"LMT_FAKE_LIVE_SLOW": "2"}},
    "second-sigterm": {"signals": [(3, signal.SIGTERM), (0.1, signal.SIGTERM)], "env": {"LMT_FAKE_LIVE_SLOW": "2"}},
    "fail": {"env": {"LMT_FAKE_LIVE_FAIL": "asr"}, "extra": ["--save-audio"]},
    "verbose": {"extra": ["-v", "--duration", "2"]},
}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("signals")
    wav = tone_wav(tmp / "talk.wav")
    with ThreadPoolExecutor(len(CASES)) as pool:
        futures = {name: pool.submit(run_case, wav, tmp / name, **case) for name, case in CASES.items()}
        return {name: f.result() for name, f in futures.items()}


def events(run):
    return [json.loads(line) for line in run["out"].splitlines()]


def names(directory):
    return sorted(p.name for p in Path(directory).iterdir())


def assert_saved(run):
    assert run["code"] == 0, run["err"]
    assert names(run["dir"]) == [f"m.{fmt}" for fmt in sorted(FORMATS)]
    doc = json.loads((run["dir"] / "m.json").read_text(encoding="utf-8"))
    schema.validate(doc)
    evs = events(run)
    assert evs[0]["type"] == "start"
    assert evs[-1]["type"] == "stop"
    return doc, evs


@pytest.mark.parametrize("case", ["sigint", "sigterm"])
def test_a_signal_stops_and_saves(runs, case):
    run = runs[case]
    doc, evs = assert_saved(run)
    assert evs[-1]["reason"] == "signal"
    assert 2.5 < doc["source"]["duration"] < 10, doc["source"]  # stopped, well before the file's 30 s
    committed = [w["text"] for e in evs if e["type"] == "words" for w in e["words"]]
    assert [s["text"] for s in doc["segments"]] == committed
    assert run["err"] == ""


def test_a_second_sigint_while_saving_exits_130(runs):
    run = runs["second-sigint"]
    assert run["code"] == 130, run["err"]
    assert names(run["dir"]) == ["m.txt"]  # no final format, and no temporary file
    text = (run["dir"] / "m.txt").read_text(encoding="utf-8")
    assert text.startswith(run["text_before"])  # as written before the stop, plus a line still in flight at most
    assert len(text.splitlines()) <= len(run["text_before"].splitlines()) + 1
    assert re.search(r"\bWord0\.", text)
    assert "stop" not in [e["type"] for e in events(run)]


def test_a_second_sigterm_is_ignored(runs):
    run = runs["second-sigterm"]
    _, evs = assert_saved(run)
    assert evs[-1]["reason"] == "signal"


def test_a_bug_exits_1_and_keeps_the_text_and_the_audio(runs):
    run = runs["fail"]
    assert run["code"] == 1, run["err"]
    lines = run["err"].splitlines()
    assert len(lines) == 1, run["err"]
    assert lines[0].startswith("lmt: error: internal: RuntimeError: fake ASR failure"), run["err"]
    assert names(run["dir"]) == ["m.txt", "m.wav"]
    with wave.open(str(run["dir"] / "m.wav"), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (RATE, 1, 2)
        assert w.getnframes() > 0


def test_verbose_prints_each_stage_and_behind(runs):
    run = runs["verbose"]
    doc, evs = assert_saved(run)
    assert evs[-1]["reason"] == "duration"
    assert doc["source"]["duration"] == 2.0
    lines = run["err"].splitlines()
    assert len(lines) == 3, run["err"]
    number = r"\d+\.\d{3}"
    assert re.fullmatch(rf"lmt: asr \d+ updates, mean {number} s, max {number} s", lines[0]), lines[0]
    assert re.fullmatch(rf"lmt: diarize \d+ steps, mean {number} s, max {number} s", lines[1]), lines[1]
    assert re.fullmatch(r"lmt: behind max \d+\.\d s", lines[2]), lines[2]
    assert int(lines[0].split()[2]) >= 2  # at least one feed and the flush


# In process: `cli.main` on the fake stages in lockstep (unpaced), so a handler can be run at an exact point.

def _fakes():
    import importlib.util

    spec = importlib.util.spec_from_file_location("live_fakes", ROOT / "tests" / "live_fakes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def in_process(tmp_path, monkeypatch):
    import lmt.live.command
    from lmt import cli

    monkeypatch.setattr(lmt.live.command, "load_stages", _fakes().load_stages)
    monkeypatch.setattr(lmt.live.command, "LOCKSTEP", True)
    wav = tone_wav(tmp_path / "talk.wav", seconds=3.0)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def main():
        return cli.main(["live", "--from-file", str(wav), "--jsonl", "-f", ",".join(FORMATS), "-o", str(out_dir),
                         "-n", "m"])

    return main, out_dir


def test_a_second_sigint_as_save_checks_abandon_exits_130_not_0_with_nothing_saved(in_process, monkeypatch):
    # The two signals land inside `abandon.is_set()`, just before it reads the event: either the handler raises
    # (130, no final format), or `save` sees the event (abandoned) and it must not then return 0.
    import threading

    import lmt.live.command

    main, out_dir = in_process
    original = threading.Event.is_set
    landed = []

    def is_set(self):
        if isinstance(self, lmt.live.command._Abandon) and not landed:
            landed.append(True)
            handler = signal.getsignal(signal.SIGINT)
            handler(signal.SIGINT, None)  # the first: stops the source, which has already ended
            handler(signal.SIGINT, None)  # the second
        return original(self)

    monkeypatch.setattr(threading.Event, "is_set", is_set)
    code = main()
    assert landed
    assert (code, names(out_dir)) == (130, ["m.txt"])


def test_the_previous_handlers_come_back_after_a_save(in_process):
    main, out_dir = in_process
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    assert main() == 0
    assert names(out_dir) == [f"m.{fmt}" for fmt in sorted(FORMATS)]
    assert {sig: signal.getsignal(sig) for sig in previous} == previous


def test_the_previous_handlers_come_back_after_a_bug(in_process, monkeypatch):
    main, out_dir = in_process
    monkeypatch.setenv("LMT_FAKE_LIVE_FAIL", "asr")
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    assert main() == 1
    assert names(out_dir) == ["m.txt"]
    assert {sig: signal.getsignal(sig) for sig in previous} == previous
