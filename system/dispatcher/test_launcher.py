"""Tests for the run launcher and its parts: the kill switch, the state store, usage and caps."""

import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from system.dispatcher import killswitch, run, state, usage

CAPS = tomllib.loads((Path(__file__).resolve().parents[1] / "caps.toml").read_text())
NOW = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)  # 2026-10-26 20:00 PDT


class FakeGitHub:
    repo = "o/r"

    def __init__(self, answer):
        self.answer = answer

    def api(self, method, path, body=None, paginate=False):
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


class KillSwitchTest(unittest.TestCase):
    def test_thrown_only_when_true_and_unreadable_counts_as_thrown(self):
        self.assertFalse(killswitch.paused(FakeGitHub(SystemExit("gh api GET ... failed: Not Found (HTTP 404)")))[0])
        self.assertTrue(killswitch.paused(FakeGitHub({"value": "true"}))[0])
        self.assertTrue(killswitch.paused(FakeGitHub({"value": " TRUE "}))[0])
        self.assertFalse(killswitch.paused(FakeGitHub({"value": "false"}))[0])
        self.assertTrue(killswitch.paused(FakeGitHub(SystemExit("gh api GET ... failed: HTTP 502")))[0])


class StateStoreTest(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        self.tmp = tempfile.TemporaryDirectory()
        self.remote = Path(self.tmp.name, "remote.git")
        subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(self.remote)], check=True)

    def tearDown(self):
        self.tmp.cleanup()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def store(self, name):
        return state.Store(Path(self.tmp.name, name), str(self.remote))

    def test_first_append_creates_the_branch_and_others_read_it(self):
        a = self.store("a")
        self.assertEqual(a.read("runs"), [])
        heads = subprocess.run(["git", "branch", "--list", state.BRANCH], cwd=self.remote, capture_output=True, text=True)
        self.assertEqual(heads.stdout.strip(), "", "a read must not create the branch")
        a.append("runs", {"event": "start", "run_id": "r1"})
        self.assertEqual(self.store("b").read("runs"), [{"event": "start", "run_id": "r1"}])

    def test_two_writers_both_land_in_order_of_pushing(self):
        a, b = self.store("a"), self.store("b")
        a.append("runs", {"event": "start", "run_id": "r1"})
        b.read("runs")
        a.append("runs", {"event": "end", "run_id": "r1"})
        b.append("runs", {"event": "start", "run_id": "r2"})  # b was behind: it must take a's commit first
        self.assertEqual([r["run_id"] for r in self.store("c").read("runs")], ["r1", "r1", "r2"])
        log = subprocess.run(["git", "log", "--oneline", state.BRANCH], cwd=self.remote, capture_output=True, text=True).stdout
        self.assertEqual(len(log.splitlines()), 4)  # the start commit plus three appends: nothing rewritten


class SummarizeTest(unittest.TestCase):
    READING = {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
        "five_hour": {"utilization": 0.05, "resetsAt": 1793070600},
        "seven_day": {"utilization": 0.04, "resetsAt": 1793289600}}}}

    def lines(self, *events):
        return [json.dumps(e) for e in events] + ["not json"]

    def test_a_finished_run(self):
        result = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 1.2345678,
                  "num_turns": 9, "permission_denials": [{"tool_name": "Bash"}]}
        out = usage.summarize(self.lines({"type": "system"}, self.READING, result))
        self.assertEqual(out, {"status": "finished", "cost_usd": 1.234568, "turns": 9, "denials": 1,
                               "five_hour": 0.05, "seven_day": 0.04, "seven_day_resets_at": 1793289600})

    def test_caps_errors_and_cut_offs(self):
        budget = {"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "total_cost_usd": 0.1, "num_turns": 1}
        self.assertEqual(usage.summarize(self.lines(budget))["status"], "cap: budget")
        failed = {"type": "result", "subtype": "success", "is_error": True, "num_turns": 1}
        self.assertEqual(usage.summarize(self.lines(failed))["status"], "failed")
        self.assertEqual(usage.summarize(self.lines(self.READING))["status"], "cut-off")


def start(run_id, role, at):
    return {"event": "start", "run_id": run_id, "role": role, "at": at.isoformat()}


def end(run_id, role, at, cost=0.0, five_hour=0.05, seven_day=0.04, resets=None):
    return {"event": "end", "run_id": run_id, "role": role, "at": at.isoformat(), "cost_usd": cost,
            "five_hour": five_hour, "seven_day": seven_day, "seven_day_resets_at": resets}


class MayStartTest(unittest.TestCase):
    def test_a_quiet_system_may_start(self):
        self.assertEqual(usage.may_start(CAPS, [], "developer", NOW), (True, [], []))

    def test_concurrency_caps(self):
        runs = [start("a", "developer", NOW)]
        ok, reasons, _ = usage.may_start(CAPS, runs, "developer", NOW)
        self.assertFalse(ok)
        self.assertIn("a developer run is already active", reasons)
        self.assertTrue(usage.may_start(CAPS, runs, "reviewer", NOW)[0])
        runs.append(start("b", "reviewer", NOW))
        self.assertFalse(usage.may_start(CAPS, runs, "coordinator", NOW)[0])

    def test_runs_per_day_count_the_owners_day(self):
        cap = CAPS["roles"]["architect"]["max_runs_per_day"]
        earlier = NOW - timedelta(hours=1)
        runs = [r for i in range(cap) for r in (start(f"r{i}", "architect", earlier), end(f"r{i}", "architect", earlier))]
        self.assertFalse(usage.may_start(CAPS, runs, "architect", NOW)[0])
        tomorrow = NOW + timedelta(hours=12)  # 08:00 PT next day
        self.assertTrue(usage.may_start(CAPS, runs, "architect", tomorrow)[0])

    def test_weekly_points_warn_then_stop(self):
        ppu = CAPS["claude"]["points_per_usd"]
        resets = int((NOW + timedelta(days=2)).timestamp())
        near = [end("x", "developer", NOW - timedelta(days=1), cost=CAPS["claude"]["warn_at_points"] / ppu, resets=resets)]
        ok, _, warnings = usage.may_start(CAPS, near, "coordinator", NOW)
        self.assertTrue(ok)
        self.assertTrue(warnings)
        full = [end("x", "developer", NOW - timedelta(days=1), cost=CAPS["claude"]["weekly_points"] / ppu, resets=resets)]
        self.assertFalse(usage.may_start(CAPS, full, "coordinator", NOW)[0])
        before_window = [end("x", "developer", NOW - timedelta(days=6), cost=CAPS["claude"]["weekly_points"] / ppu, resets=resets)]
        self.assertTrue(usage.may_start(CAPS, before_window, "coordinator", NOW)[0])

    def test_owner_headroom_holds_and_pauses(self):
        busy = [end("x", "developer", NOW, five_hour=0.6)]
        ok, reasons, _ = usage.may_start(CAPS, busy, "coordinator", NOW)
        self.assertFalse(ok)
        self.assertIn("5-hour", reasons[0])
        full_week = [end("x", "developer", NOW, seven_day=0.95)]
        self.assertIn("7-day", usage.may_start(CAPS, full_week, "coordinator", NOW)[1][0])


class LauncherTest(unittest.TestCase):
    def test_a_run_gets_the_runner_environment_and_its_identity_only(self):
        config = run.runner.load_all()
        env = run.environment(config, "reviewer", "reviewer-1")
        self.assertEqual(set(env), {"PATH", "HOME", "USER", "AGENT_ROLE", "AGENT_RUN_ID", "GH_PROMPT_DISABLED", "GIT_TERMINAL_PROMPT"})
        self.assertNotIn("LOGNAME", env)
        self.assertEqual((env["AGENT_ROLE"], env["AGENT_RUN_ID"]), ("reviewer", "reviewer-1"))

    def test_ids_and_task(self):
        self.assertEqual(run.new_run_id("reviewer", NOW), "reviewer-20261027T030000Z")
        self.assertEqual(run.task_text("Review it.", 5), "Review it. Your item: #5.")
        self.assertEqual(run.task_text("Daily run.", None), "Daily run.")

    def test_execute_logs_the_stream_and_enforces_the_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            log, pids = Path(d, "run.jsonl"), []
            cmd = [sys.executable, "-c", "import sys; assert sys.stdin.read() == ''; print('{\"type\": \"result\", \"subtype\": \"success\"}')"]
            self.assertEqual(run.execute(cmd, d, dict(os.environ), log, 30, pids.append), "ended")
            self.assertIn('"subtype": "success"', log.read_text())
            self.assertEqual(len(pids), 1)
            began = time.monotonic()
            self.assertEqual(run.execute([sys.executable, "-c", "import time; time.sleep(60)"], d, dict(os.environ), log, 1), "timeout")
            self.assertLess(time.monotonic() - began, 30)


if __name__ == "__main__":
    unittest.main()
