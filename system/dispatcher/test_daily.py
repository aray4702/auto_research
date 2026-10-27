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
    def test_the_same_denial_in_two_runs_is_a_gap_and_in_one_is_not(self):
        runs = [end("a", denied=["Bash: gh pr"]), end("b", denied=["Bash: gh pr", "WebFetch"])]
        got = daily.denials(runs, NOW)
        self.assertEqual([(k, lvl) for k, lvl, *_ in got], [("denial:Bash: gh pr", "builder")])

    def test_signatures_keep_only_the_tool_and_first_two_words(self):
        self.assertEqual(usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": "gh pr checks 34 --watch"}}), "Bash: gh pr")
        self.assertEqual(usage.denial_signature({"tool_name": "Write", "tool_input": {"file_path": "/tmp/x"}}), "Write")
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
        closed = {(NOW - timedelta(hours=2)).date(): 1, (NOW - timedelta(days=3)).date(): 5}
        self.assertEqual([x[1] for x in daily.cost_per_item(runs, NOW, closed)], ["builder"])
        cheap = [end("a", hours_ago=2, cost=3.0)] + runs[1:]
        self.assertEqual(daily.cost_per_item(cheap, NOW, closed), [])


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


if __name__ == "__main__":
    unittest.main()
