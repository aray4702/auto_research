"""Tests for the product and system audits' reports (audit.py): pure functions, nothing touches the network."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from system.dispatcher import audit

NOW = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)
ZONE = "America/Los_Angeles"
ROOT = Path(__file__).resolve().parents[2]


def start(run_id, role="developer", hours_ago=1, task="t"):
    return {"event": "start", "run_id": run_id, "role": role, "item": None, "task": task,
            "at": (NOW - timedelta(hours=hours_ago)).isoformat()}


def end(run_id, status="finished", hours_ago=0.5):
    return {"event": "end", "run_id": run_id, "role": "developer", "status": status, "at": (NOW - timedelta(hours=hours_ago)).isoformat()}


class RequestTest(unittest.TestCase):
    def test_only_the_exact_requests_run_an_audit(self):
        self.assertEqual(audit.requested("audit"), ("product", "system"))
        self.assertEqual(audit.requested("Audit product"), ("product",))
        self.assertEqual(audit.requested(" audit system. "), ("system",))
        self.assertEqual(audit.requested("audit all"), ("product", "system"))
        for text in ("audits", "audit the product", "#84 1", "please audit", "", None):
            self.assertIsNone(audit.requested(text), text)


class ProductTest(unittest.TestCase):
    def test_deadlines_come_from_the_goals_table(self):
        found = audit.deadlines((ROOT / "project_requirements.md").read_text())
        self.assertIn("G1", found)
        self.assertNotIn("G0", found)
        self.assertNotIn("S1", found, "struck through")
        table = "## 2. Goals\n\n| ID | Goal | Outcome | Deadline |\n| --- | --- | --- | --- |\n| G1 | a | b | 2026-10-30 |\n| G2 | c | d | |\n\n## 3. Next"
        self.assertEqual(audit.deadlines(table), {"G1": "2026-10-30", "G2": "none"})

    def test_an_items_state_is_its_first_label_that_applies(self):
        self.assertEqual(audit.item_state({"state:ready", "needs-decision:owner"}, False), "waiting on the Owner")
        self.assertEqual(audit.item_state({"state:claimed"}, True), "in progress")
        self.assertEqual(audit.item_state({"state:ready"}, True), "in review")
        self.assertEqual(audit.item_state({"state:blocked"}, False), "blocked")
        self.assertEqual(audit.item_state(set(), False), "in triage")

    def test_the_report_counts_each_goals_items_and_what_waits_on_the_owner(self):
        facts = {
            "goals": [{"number": 84, "title": "Goal G1: offline"}, {"number": 85, "title": "Goal G2: live"}],
            "deadlines": {"G1": "2026-10-30"},
            "milestones": [{"title": "G1.1", "open": 3, "closed": 2}, {"title": "G1.2", "open": 0, "closed": 1},
                           {"title": "G10.1", "open": 9, "closed": 0}],
            "items": [{"number": 106, "title": "skeleton", "milestone": "G1.1", "state": "waiting on the Owner"},
                      {"number": 107, "title": "tiers", "milestone": "G1.1", "state": "blocked"},
                      {"number": 108, "title": "models", "milestone": "G1.1", "state": "blocked"}],
            "questions": [(84, "Goal G1", NOW - timedelta(hours=2))],
            "approvals": [(134, "tier filter")],
            "merged": [(125, "G2 design")],
            "gate": "gate on main (abc1234): success (run u)",
            "watch": (12, "first risk: deadline"),
        }
        headline, body = audit.product_report(facts, NOW, ZONE)
        self.assertEqual(headline, "G1 3/6 items done; G2 0/0 items done; 2 waiting on you", "G10 isn't G1's")
        self.assertIn("**Deadline:** 2026-10-30", body)
        self.assertIn("`G1.1`: 2 done, 3 open (2 blocked, 1 waiting on the Owner)", body)
        self.assertIn("#106 waiting on the Owner: skeleton", body)
        self.assertIn("**Deadline:** not in the requirements", body)
        self.assertIn("**Question** on #84, asked 2.0 h ago", body)
        self.assertIn("**Approval** of PR #134", body)
        self.assertIn("Merged #125: G2 design", body)
        quiet = dict(facts, questions=[], approvals=[], merged=[], watch=None)
        headline, body = audit.product_report(quiet, NOW, ZONE)
        self.assertTrue(headline.endswith("nothing waiting on you"))
        self.assertIn("Nothing merged.", body)


class SystemTest(unittest.TestCase):
    def facts(self, **kw):
        base = {"findings": [("k1", "builder", "Recurring permission denial: Bash: cat", True),
                             ("k2", "coordinator", "Bumpy item #104", False)],
                "open_system": [(152, "runner.py check: tools on PATH", "sev:2"), (153, "docs", "sev:3")],
                "flow": "Bottleneck: `owner`, 61%.", "paused": (False, "AGENTS_PAUSED is 'false'"),
                "runs": {"developer": (3, 1)}, "usage": (["80% of the weekly points"], []),
                "last_reconcile": NOW - timedelta(minutes=55), "builder_daily": None}
        return {**base, **kw}

    def test_the_report_tells_new_findings_from_ones_still_holding(self):
        headline, body = audit.system_report(self.facts(), NOW, ZONE)
        self.assertEqual(headline, "1 new finding, 1 still open; 2 open system items")
        self.assertIn("**New:** Recurring permission denial: Bash: cat → builder", body)
        self.assertIn("Raised before, still holds: Bumpy item #104 → coordinator", body)
        self.assertIn("#152 sev:2", body)
        self.assertNotIn("#153", body, "only sev:1 and sev:2 are listed")
        self.assertIn("developer 3 (1 not finished)", body)
        self.assertIn("**Usage:** 80% of the weekly points", body)
        self.assertIn("Builder's daily run, last started:** none recorded", body)
        headline, _ = audit.system_report(self.facts(paused=(True, "AGENTS_PAUSED is 'true'"), findings=[]), NOW, ZONE)
        self.assertEqual(headline, "0 new findings, 0 still open; 2 open system items; KILL SWITCH THROWN")

    def test_runs_in_the_last_twelve_hours_by_role(self):
        runs = [start("a"), end("a"), start("b"), end("b", status="timeout"), start("c", hours_ago=13), start("d", role="reviewer")]
        self.assertEqual(audit.runs_summary(runs, NOW), {"developer": (2, 1), "reviewer": (1, 0)})

    def test_a_builder_daily_run_missing_for_over_a_day_is_a_finding(self):
        daily = lambda h: start(f"b{h}", role="builder", hours_ago=h, task="Daily run: follow the daily run in your role file.")  # noqa: E731
        old = start("x", hours_ago=72)
        self.assertEqual(audit.missed_builder_daily([start("y", hours_ago=5)], NOW, ZONE), [], "no history yet")
        self.assertEqual(audit.missed_builder_daily([old, daily(12)], NOW, ZONE), [])
        self.assertEqual(audit.missed_builder_daily([old, start("z", role="builder", hours_ago=2)], NOW, ZONE)[0][1], "builder",
                         "a builder run on an item isn't the daily run")
        found = audit.missed_builder_daily([old, daily(36)], NOW, ZONE)
        self.assertEqual((found[0][0], found[0][1]), ("builder-daily-missed:2026-10-28", "builder"))
        self.assertEqual(audit.builder_daily([old, daily(36), daily(60)]), NOW - timedelta(hours=36))


if __name__ == "__main__":
    unittest.main()
