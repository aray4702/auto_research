"""Tests for the Dispatcher's daily signals and rule audit: each signal raises, and its near miss doesn't."""

import json
import unittest
from datetime import datetime, timedelta, timezone

from system.dispatcher import daily, usage
from system.tools import post

NOW = datetime(2026, 10, 28, 7, 30, tzinfo=timezone.utc)
EXPECTED = post.load_identity()["branch_protection"]


def end(run_id, item=None, status="finished", hours_ago=1, cost=0.5, denied=(), role="developer"):
    return {"event": "end", "run_id": run_id, "role": role, "item": item, "status": status, "cost_usd": cost,
            "denied": list(denied), "denials": len(denied), "at": (NOW - timedelta(hours=hours_ago)).isoformat()}


class DenialsTest(unittest.TestCase):
    def test_the_same_denial_in_three_runs_is_a_gap_and_in_two_is_not(self):
        runs = [end("a", denied=["Bash: gh pr"]), end("b", denied=["Bash: gh pr", "WebFetch"]), end("c", denied=["Bash: gh pr"])]
        got = daily.denials(runs, NOW)
        self.assertEqual([(k, lvl) for k, lvl, *_ in got], [("denial:Bash: gh pr", "builder")], "3 runs, as #44 sets")
        self.assertEqual(daily.denials(runs[:2], NOW), [], "2 runs are not yet a pattern")

    def test_signatures_keep_only_the_tool_and_first_two_words(self):
        self.assertEqual(usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": "gh pr checks 34 --watch"}}), "Bash: gh pr")
        self.assertEqual(usage.denial_signature({"tool_name": "Write", "tool_input": {"file_path": "/tmp/x"}}), "Write")
        token = "gh" + "p_" + "A1b2" * 9
        for command, want in [(f"GH_TOKEN={token} gh pr list", "Bash: gh pr"), (f"export API_KEY={token}", "Bash: export"),
                              (f"curl -H 'Authorization: {token}' x", "Bash: curl"), ("cat /Users/x/secret.txt", "Bash: cat")]:
            sig = usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": command}})
            self.assertEqual(sig, want, command)
            self.assertNotIn(token, sig)
        result = {"type": "result", "subtype": "success", "permission_denials": [{"tool_name": "WebFetch", "tool_input": {}}]}
        self.assertEqual(usage.summarize([json.dumps(result)])["denied"], ["WebFetch"])


class BumpyTest(unittest.TestCase):
    def test_two_unfinished_runs_or_three_runs_make_an_open_item_bumpy(self):
        open_items = {7, 8, 9}
        self.assertEqual([x[4] for x in daily.bumpy([end("a", 7, "failed"), end("b", 7, "timeout")], NOW, open_items)], [7])
        self.assertEqual([x[4] for x in daily.bumpy([end(f"r{i}", 8) for i in range(3)], NOW, open_items)], [8])
        self.assertEqual(daily.bumpy([end("a", 9, "failed"), end("b", 9)], NOW, open_items), [])
        self.assertEqual(daily.bumpy([end("a", 9, "failed", hours_ago=200), end("b", 9, "failed")], NOW, open_items), [], "only this week")
        self.assertEqual(daily.bumpy([end(f"r{i}", 50) for i in range(3)], NOW, open_items), [], "closed items and PRs are left out")

    def test_a_pr_at_the_review_rounds_cap_goes_to_the_coordinator(self):
        bot = "aray4702-reviewer-app[bot]"
        pr = lambda n, k: {"number": n, "reviews": [{"login": bot, "state": "CHANGES_REQUESTED"}] * k + [{"login": bot, "state": "APPROVED"}]}
        self.assertEqual([(x[1], x[4]) for x in daily.review_rounds([pr(60, 3), pr(61, 2)], bot, 3)], [("coordinator", 60)])


class NewFailureTest(unittest.TestCase):
    def test_a_status_never_seen_before_is_new(self):
        runs = [end("a", status="failed: launcher: TypeError")]
        self.assertEqual([x[1] for x in daily.new_failures(runs, NOW, [])], ["builder"])
        self.assertEqual(daily.new_failures(runs, NOW, [end("old", status="failed: launcher: TypeError", hours_ago=300)]), [])
        self.assertEqual(daily.new_failures([end("b", status="timeout")], NOW, []), [])


class CostTest(unittest.TestCase):
    def test_cost_per_finished_item_more_than_double_its_week(self):
        runs = [end("a", hours_ago=2, cost=20.0)] + [end(f"w{i}", hours_ago=48 + i, cost=2.0) for i in range(5)]
        closed = [NOW - timedelta(hours=1)] + [NOW - timedelta(days=3)] * 5
        self.assertEqual([x[1] for x in daily.cost_per_item(runs, NOW, closed)], ["builder"])
        cheap = [end("a", hours_ago=2, cost=3.0)] + runs[1:]
        self.assertEqual(daily.cost_per_item(cheap, NOW, closed), [])
        # A closure earlier today counts: two items closed today halve the day's cost per item.
        self.assertEqual(daily.cost_per_item(runs, NOW, closed + [NOW - timedelta(minutes=30)] * 9), [])


class WatchListTest(unittest.TestCase):
    BODY = ("| Risk | Signal | Threshold | Since | Note |\n| --- | --- | --- | --- | --- |\n"
            "| The Owner waits too long | `owner_wait_hours` | > 24 | 2026-10-27 | |\n"
            "| A free-text risk | someone notices | n/a | | |\n")

    def test_a_measured_signal_past_its_threshold_raises_and_free_text_is_left(self):
        m = daily.metrics([], NOW, owner_wait_hours=30.0)
        self.assertEqual([x[2] for x in daily.watch_list(self.BODY, m)], ["Watch-list risk past its threshold: The Owner waits too long"])
        self.assertEqual(daily.watch_list(self.BODY, daily.metrics([], NOW, owner_wait_hours=2.0)), [])


class ProtectionTest(unittest.TestCase):
    LIVE = {"required_status_checks": {"strict": True, "checks": [{"context": "gate", "app_id": 15368}]},
            "enforce_admins": {"enabled": True},
            "required_pull_request_reviews": {"required_approving_review_count": 1, "dismiss_stale_reviews": True,
                                              "require_code_owner_reviews": True},
            "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False}}

    def test_the_recorded_settings_pass_and_any_change_is_a_breach_for_the_owner(self):
        self.assertEqual(daily.protection_breaches(self.LIVE, EXPECTED), [])
        loosened = json.loads(json.dumps(self.LIVE))
        loosened["enforce_admins"]["enabled"] = False
        got = daily.protection_breaches(loosened, EXPECTED)
        self.assertEqual([x[1] for x in got], ["owner"])
        self.assertIn("enforce_admins: is False, recorded True", got[0][3])
        self.assertIn("kill switch", got[0][3])


class DailyPassTest(unittest.TestCase):
    """The responses (blueprint §4.5), with a fake GitHub and state store: nothing is posted or pushed."""

    def run_pass(self, live_protection, raised=(), runs=()):
        from unittest import mock
        from system.dispatcher import dispatch
        store = mock.Mock()
        store.read.side_effect = lambda kind: list(runs) if kind == "runs" else [{"key": k} for k in raised]
        gh = mock.Mock(repo="aray4702/auto_research")
        gh.api.side_effect = lambda method, path, body=None, paginate=False: (
            live_protection if path.endswith("/protection") else [] if paginate else {})
        snap = {"issues": [{"number": 12, "title": "Watch list", "labels": {"record:watch-list"}, "body": ""}], "prs": [], "work_order": []}
        made = iter(["#70 u", "#71 u", "#72 u"])
        with mock.patch.object(dispatch.post, "GitHub", return_value=gh), \
             mock.patch.object(dispatch.state, "Store", return_value=store), \
             mock.patch.object(dispatch.github_state, "snapshot", return_value=snap), \
             mock.patch.object(dispatch.runner, "commits", return_value=("a", "a")), \
             mock.patch.object(dispatch.runner, "problems", return_value=[]), \
             mock.patch.object(dispatch.runner, "claude_login", return_value=("claude.ai", "max")), \
             mock.patch.object(dispatch.post, "cmd_issue_create", side_effect=lambda *a: next(made)) as create, \
             mock.patch.object(dispatch.post, "cmd_escalate") as escalate, \
             mock.patch.object(dispatch.subprocess, "run") as sh:
            lines = dispatch.daily_pass(dry_run=False, now=NOW)
        return lines, store, create, escalate, sh

    def test_a_breach_throws_the_kill_switch_and_goes_to_the_owner_once(self):
        loosened = json.loads(json.dumps(ProtectionTest.LIVE))
        loosened["enforce_admins"]["enabled"] = False
        lines, store, create, escalate, sh = self.run_pass(loosened)
        self.assertEqual(lines, ["raise (owner): Rule breach: branch protection on main changed"])
        self.assertIn(["gh", "variable", "set", "AGENTS_PAUSED", "--body", "true", "--repo", "aray4702/auto_research"],
                      [c.args[0] for c in sh.call_args_list])
        self.assertIn("sev:1", create.call_args.args[3])
        self.assertEqual((escalate.call_args.args[2], escalate.call_args.args[3]), (70, "owner"))
        key = store.append.call_args.args[1]["key"]
        lines, store, create, escalate, sh = self.run_pass(loosened, raised=[key])
        self.assertEqual(lines, ["no anomalies"], "an anomaly already raised isn't raised again")
        self.assertFalse(sh.called or create.called)

    def test_a_builder_anomaly_becomes_an_item_held_for_the_builder(self):
        lines, store, create, escalate, sh = self.run_pass(ProtectionTest.LIVE, runs=[end("x", status="failed: launcher: Boom")])
        self.assertEqual(lines, ["raise (builder): New kind of failure: failed: launcher: Boom"])
        self.assertEqual(create.call_args.args[3], ["type:system", "state:ready", "sev:3"])
        self.assertEqual((escalate.call_args.args[2], escalate.call_args.args[3]), (70, "builder"))
        self.assertFalse(sh.called, "only a breach touches the kill switch")


if __name__ == "__main__":
    unittest.main()
