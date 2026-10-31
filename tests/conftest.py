"""The `criterion` marker's plugin: checks each tag against the spec, and writes the job's record
with --criteria-out PATH (design/internal/criteria.md §1, §3). And the product run, which the criteria's
tests share (design/internal/g1-slice.md, S6)."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "tests" / "fixtures" / "clip.wav"
TWO_SPEAKERS = ROOT / "tests" / "fixtures" / "two_speakers.wav"
SPEC = ROOT / "spec" / "acceptance.md"
# Exit statuses after which the session ran to its end: 0, 1 (tests failed) and 5 (no tests collected).
FINISHED = (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED, pytest.ExitCode.NO_TESTS_COLLECTED)
RANK = {"passed": 0, "skipped": 1, "failed": 2}  # a test's outcome is its worst phase's


def _load_criteria():
    """.github/scripts/criteria.py, loaded by path: its parser and record writer are the only ones."""
    spec = importlib.util.spec_from_file_location("lmt_ci_criteria", ROOT / ".github" / "scripts" / "criteria.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


criteria = _load_criteria()


def item_criteria(item):
    return list(dict.fromkeys(cid for mark in item.iter_markers("criterion") for cid in mark.args))


def phase_outcome(report):
    """(outcome, reason) for one phase's report."""
    if hasattr(report, "wasxfail"):  # xfail and xpass: a criterion is met only by tests that pass outright
        return "failed", None
    if report.skipped:
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        return "skipped", reason.removeprefix("Skipped: ")
    return ("failed", None) if report.failed else ("passed", None)


class CriteriaRecord:
    def __init__(self, config):
        self.config = config
        self.tagged = {}  # nodeid: [criterion id], for the selected tests
        self.outcomes = {}  # nodeid: (outcome, reason)
        self.interrupted = False

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items):
        """An unknown ID stops the session at collection, a deselected test's included."""
        tagged = [(item, item_criteria(item)) for item in items]
        if not any(ids for _, ids in tagged):
            return
        known = criteria.spec_ids(SPEC)
        for item, ids in tagged:
            for cid in ids:
                if cid not in known:
                    raise pytest.UsageError(
                        f"{item.nodeid}: criterion {cid!r} is not an ID in {SPEC.name}'s done-checks")

    def pytest_collection_finish(self, session):
        self.tagged = {item.nodeid: ids for item in session.items if (ids := item_criteria(item))}

    def pytest_runtest_logreport(self, report):
        if report.nodeid not in self.tagged:
            return
        outcome = phase_outcome(report)
        previous = self.outcomes.get(report.nodeid)
        if previous is None or RANK[outcome[0]] > RANK[previous[0]]:
            self.outcomes[report.nodeid] = outcome

    def pytest_keyboard_interrupt(self):
        self.interrupted = True  # Ctrl-C and pytest.exit both

    def pytest_internalerror(self):
        self.interrupted = True

    def pytest_sessionfinish(self, exitstatus):
        path = self.config.getoption("criteria_out")
        if not path:
            return
        checks = []
        for nodeid, ids in self.tagged.items():
            if nodeid in self.outcomes:
                outcome, reason = self.outcomes[nodeid]
                checks.append({"check": nodeid, "criteria": ids, "outcome": outcome}
                              | ({"reason": reason} if outcome == "skipped" else {}))
        # Complete only when the session ran to its end and every selected tagged test reported.
        complete = exitstatus in FINISHED and not self.interrupted and len(checks) == len(self.tagged)
        criteria.write_record(path, checks, complete)


def pytest_addoption(parser):
    parser.addoption("--criteria-out", metavar="PATH",
                     help="write the tagged tests' outcomes to PATH, named criteria-<job>.json "
                          "(design/internal/criteria.md §3)")


def pytest_configure(config):
    path = config.getoption("criteria_out")
    if path and criteria.job_of(path) is None:
        raise pytest.UsageError(f"--criteria-out {path}: the name must be criteria-<job>.json or "
                                "criteria-<job>-<n>.json")
    config.pluginmanager.register(CriteriaRecord(config), "criteria-record")


class ProductRun(NamedTuple):
    returncode: int
    stdout: str
    stderr: str
    out_dir: Path  # the run's working directory, where its outputs land


def run_lmt(argv, *, cwd=None, env=None) -> subprocess.CompletedProcess:
    """`python -m lmt <argv>` in a process of its own, as a user runs it, with text output captured."""
    return subprocess.run([sys.executable, "-m", "lmt", *argv], cwd=cwd, env=env, capture_output=True,
                          text=True, check=False)


@pytest.fixture(scope="session")
def clip_run(tmp_path_factory) -> ProductRun:
    """The product run (spec/acceptance.md G1 "Terms") on the reference clip: `lmt transcribe <clip>` with
    no option at all. It runs in a fresh directory, which -o defaults to, and inherits LMT_CACHE_DIR, where
    the job has downloaded and verified the default models. Once per session."""
    out_dir = tmp_path_factory.mktemp("clip_run")
    r = run_lmt(["transcribe", str(CLIP)], cwd=out_dir)
    return ProductRun(r.returncode, r.stdout, r.stderr, out_dir)


@pytest.fixture(scope="session")
def two_speakers_run(tmp_path_factory) -> ProductRun:
    """The product run on the two-speaker clip, made exactly as `clip_run` is: `lmt transcribe <clip>` with
    no option, in a fresh directory (design/internal/g1-stage2.md, P5). Once per session."""
    out_dir = tmp_path_factory.mktemp("two_speakers_run")
    r = run_lmt(["transcribe", str(TWO_SPEAKERS)], cwd=out_dir)
    return ProductRun(r.returncode, r.stdout, r.stderr, out_dir)


class LiveRun(NamedTuple):
    returncode: int
    stdout: str
    stderr: str
    isolated: bool  # run without the network (tests/isolation.py)
    events: list[dict]  # stdout's JSONL, one event per line
    out_dir: Path  # -o
    stem: str  # the default --name, read from <stem>.wav
    connections: list[str]  # the guard's log
    watched: int  # entries snapshotted before the run
    new: list[str]
    changed: list[str]
    gone: list[str]


@pytest.fixture(scope="session")
def live_run(tmp_path_factory) -> LiveRun:
    """The shared run (design/internal/g2-stage1.md §3b, item 3b5): `tests/live_lockstep.py live --from-file
    two_speakers.wav --jsonl --save-audio -o <dir>` in a process of its own, through tests/isolation.py, with
    fresh HOME, TMPDIR, XDG_CACHE_HOME and working directory, the network guard on, and the watched directories
    snapshotted before and after, as G1-13's test does. Once per session."""
    import json
    import os

    from isolation import Isolated, snapshot, wrap

    from lmt import models

    top = tmp_path_factory.mktemp("live_run")
    home, tmp, xdg, work, out_dir = (top / name for name in ("home", "tmp", "xdg", "work", "out"))
    for directory in (home, tmp, xdg, work, out_dir):
        directory.mkdir()
    net_log = top / "net.log"  # beside the watched directories, outside every one
    cache = models.cache_dir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "XDG_CACHE_HOME": str(xdg),
        "LMT_CACHE_DIR": str(cache),
        "PYTHONPATH": str(ROOT / "tests" / "netguard"),
        "LMT_NETGUARD_LOG": str(net_log),
        "LANG": "C.UTF-8",
    }
    argv = ["live", "--from-file", str(TWO_SPEAKERS), "--jsonl", "--save-audio", "-o", str(out_dir)]
    # isolation.run_isolated runs `-m lmt`; lockstep is reached only through tests/live_lockstep.py, so its
    # command goes through the same wrap.
    command, isolated = wrap([sys.executable, str(ROOT / "tests" / "live_lockstep.py"), *argv], env=env)

    before = snapshot(home, tmp, xdg, work, out_dir, cache)
    r = Isolated(subprocess.run(command, env=env, cwd=work, capture_output=True, text=True, check=False), isolated)
    after = snapshot(home, tmp, xdg, work, out_dir, cache)

    connections = net_log.read_text(encoding="utf-8").splitlines() if net_log.exists() else []
    events = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    wavs = sorted(out_dir.glob("*.wav"))
    return LiveRun(r.returncode, r.stdout, r.stderr, r.isolated, events, out_dir,
                   wavs[0].stem if len(wavs) == 1 else "", connections, len(before),
                   new=sorted(set(after) - set(before)),
                   changed=sorted(path for path in set(before) & set(after) if before[path] != after[path]),
                   gone=sorted(set(before) - set(after)))
