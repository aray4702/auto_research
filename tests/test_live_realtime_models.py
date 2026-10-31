"""G2-4 at real-time pace, on the models (spec/acceptance.md; design/internal/g2-stage1.md §3c, item 3c4): each case
is `lmt live --from-file two_speakers.wav --jsonl -f txt,srt,vtt,json` in a subprocess, threaded and paced as a user
runs it, not in lockstep. The stop cases run at the same time as each other, since they check behaviour, not speed;
the run to the end of the file runs alone, and its p95 delay goes to the log, unchecked.

Each event line is stamped as `lmt.eval.live.run_once` stamps it (time.monotonic() as readline() returns it,
counted from the `start` event's arrival), but read here, so `<stem>.txt` can be checked at each `segment` event's
arrival and signals sent at 20 s."""

import json
import signal
import subprocess
import sys
import threading
import time

import jsonschema
import pytest
from conftest import TWO_SPEAKERS
from test_g1_1 import report
from test_live_session_models import offline_words
from test_live_shared_run import TRANSCRIPT_SCHEMA

pytestmark = [pytest.mark.models, pytest.mark.criterion("G2-4")]

FORMATS = ("txt", "srt", "vtt", "json")
STEM = "m"
STOP_AT = 20.0  # s after the start event's arrival, spec/acceptance.md G2-4
SECOND_AFTER = 0.1  # s, the second SIGINT after the first
LATEST_END = 21.0  # s, no segment ends after this, after a stop at 20 s
STOP_TIMEOUT = 300  # s, a stop case: model loading, 20 s of audio and the save, with room to spare
EOF_TIMEOUT = 600  # s, the whole clip (about 66 s) at real-time pace


def run_case(out_dir, *, signals=(), extra=(), timeout):
    """Runs one case, sending each `(seconds after the start event's arrival, signal)` from a timer thread, and
    reading stdout as it arrives. At each `segment` event's arrival, records `<stem>.txt`'s lines and the segments
    closed so far. Returns the exit code, the stamped events, stderr, the checks and `<stem>.txt` as it was just
    before the first signal. A run past `timeout` is killed and fails."""
    out_dir.mkdir()
    txt = out_dir / f"{STEM}.txt"
    proc = subprocess.Popen([sys.executable, "-m", "lmt", "live", "--from-file", str(TWO_SPEAKERS), "--jsonl",
                             "-f", ",".join(FORMATS), "-o", str(out_dir), "-n", STEM, *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=out_dir)
    killed = threading.Event()

    def kill():
        killed.set()
        proc.kill()

    watchdog = threading.Timer(timeout, kill)
    watchdog.start()
    stderr_lines = []
    reader = threading.Thread(target=lambda: stderr_lines.extend(proc.stderr), daemon=True)
    reader.start()
    state = {"text_before": None}
    timers = []

    def send(first, sig):
        if first:
            state["text_before"] = txt.read_text(encoding="utf-8")
        if proc.poll() is None:
            proc.send_signal(sig)

    lines, checks, closed = [], [], []
    origin = None
    try:
        for line in iter(proc.stdout.readline, ""):
            arrived = time.monotonic()
            if not line.strip():
                continue
            event = json.loads(line)
            if origin is None and event.get("type") == "start":
                origin = arrived
                for i, (at, sig) in enumerate(signals):
                    timer = threading.Timer(at, send, (i == 0, sig))
                    timer.start()
                    timers.append(timer)
            if event.get("type") == "segment":
                closed.append(event)
                checks.append((list(closed), txt.read_text(encoding="utf-8").splitlines()))
            lines.append((arrived, event))
        code = proc.wait()
    finally:
        watchdog.cancel()
        for timer in timers:
            timer.cancel()
        reader.join(timeout=10)
    assert not killed.is_set(), f"the run took longer than {timeout} s"
    start = origin if origin is not None else 0.0
    return {"code": code, "lines": [(a - start, e) for a, e in lines], "err": "".join(stderr_lines),
            "dir": out_dir, "checks": checks, "text_before": state["text_before"]}


def events(run):
    return [e for _, e in run["lines"]]


def names(directory):
    return sorted(p.name for p in directory.iterdir())


STOP_CASES = {
    "duration": {"extra": ["--duration", "20"]},
    "sigint": {"signals": [(STOP_AT, signal.SIGINT)]},
    "sigterm": {"signals": [(STOP_AT, signal.SIGTERM)]},
    "second-sigint": {"signals": [(STOP_AT, signal.SIGINT), (STOP_AT + SECOND_AFTER, signal.SIGINT)]},
}


@pytest.fixture(scope="module")
def stop_runs(tmp_path_factory):
    """The four stop cases, at the same time as each other."""
    from concurrent.futures import ThreadPoolExecutor

    tmp = tmp_path_factory.mktemp("realtime_stops")
    with ThreadPoolExecutor(len(STOP_CASES)) as pool:
        futures = {name: pool.submit(run_case, tmp / name, timeout=STOP_TIMEOUT, **case)
                   for name, case in STOP_CASES.items()}
        return {name: f.result() for name, f in futures.items()}


@pytest.fixture(scope="module")
def eof_run(tmp_path_factory):
    """The run to the end of the file, alone."""
    return run_case(tmp_path_factory.mktemp("realtime_eof") / "run", timeout=EOF_TIMEOUT)


def assert_saved(run, reason):
    """Exit 0, `stop`'s reason, the four formats and nothing else, the JSON valid, and its segments holding exactly
    the events' committed words, in order, with the same speakers. Returns the JSON."""
    assert run["code"] == 0, run["err"]
    evs = events(run)
    assert evs[0]["type"] == "start"
    assert (evs[-1]["type"], evs[-1].get("reason")) == ("stop", reason), evs[-1]
    assert names(run["dir"]) == [f"{STEM}.{fmt}" for fmt in sorted(FORMATS)]
    doc = json.loads((run["dir"] / f"{STEM}.json").read_text(encoding="utf-8"))
    errors = [e.message for e in jsonschema.Draft202012Validator(
        json.loads(TRANSCRIPT_SCHEMA.read_text(encoding="utf-8"))).iter_errors(doc)]
    assert not errors, errors
    streamed = [(w, e["speaker"]) for e in evs if e["type"] == "words" for w in e["words"]]
    saved = [(w, s["speaker"]) for s in doc["segments"] for w in s["words"]]
    assert streamed, "nothing was committed"
    assert saved == streamed, "the JSON's words differ from the event stream's"
    return doc


def assert_text_grew(run):
    """At each `segment` event's arrival, `<stem>.txt` held a line for every segment closed so far."""
    assert run["checks"], "no segment was closed"
    for closed, text_lines in run["checks"]:
        assert len(text_lines) >= len(closed), (len(text_lines), len(closed))
        for line, segment in zip(text_lines, closed):
            assert line.endswith(f": {segment['text']}"), (line, segment)


def test_the_end_of_the_file(eof_run, two_speakers_run):
    from lmt.eval.live import delays, match_words, percentile, read_events

    run = eof_run
    doc = assert_saved(run, "end-of-input")
    assert_text_grew(run)
    words, behind = read_events(run["lines"])
    matches = match_words(offline_words(two_speakers_run), words)
    p95 = percentile(delays(matches), 0.95) if matches else None
    report(f"G2-4: at real-time pace to the end of the file, {len(words)} committed words, {len(matches)} matched, "
           f"{len(doc['segments'])} segments; p95 delay "
           f"{'none' if p95 is None else f'{p95:.2f} s'}, behind at the end {behind:.2f} s (not checked)")


def test_duration_20(stop_runs):
    run = stop_runs["duration"]
    doc = assert_saved(run, "duration")
    assert_text_grew(run)
    assert doc["source"]["duration"] == 20.0, doc["source"]


@pytest.mark.parametrize("case", ["sigint", "sigterm"])
def test_a_signal_at_20_s(stop_runs, case):
    run = stop_runs[case]
    doc = assert_saved(run, "signal")
    assert_text_grew(run)
    ends = [s["end"] for s in doc["segments"]] + [e["end"] for e in events(run) if e["type"] == "segment"]
    report(f"G2-4: {case} at {STOP_AT:.0f} s: source.duration {doc['source']['duration']} s, "
           f"latest segment end {max(ends):.2f} s")
    assert max(ends) <= LATEST_END, max(ends)


def test_a_second_sigint_while_saving_exits_130(stop_runs):
    run = stop_runs["second-sigint"]
    assert run["code"] == 130, run["err"]
    assert names(run["dir"]) == [f"{STEM}.txt"]  # no final format, and no temporary file
    text = (run["dir"] / f"{STEM}.txt").read_text(encoding="utf-8")
    before = run["text_before"]
    assert before is not None, "the first signal wasn't sent"
    assert text.startswith(before)  # as written before the stop, plus a line still in flight at most
    assert len(text.splitlines()) <= len(before.splitlines()) + 1
    assert "stop" not in [e["type"] for e in events(run)]

