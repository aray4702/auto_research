"""Tests for flow: each stage is assigned from a timeline, each signal raises and its near miss doesn't."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from system.dispatcher import flow

T0 = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 28, 7, 30, tzinfo=timezone.utc)
BOT, OWNER = "aray4702-reviewer-app[bot]", "sloan3165"


def h(hours):
    return T0 + timedelta(hours=hours)


def row(item, closed_days_ago, stages, idle=0.0, spans=()):
    return {"item": item, "closed": (NOW - timedelta(days=closed_days_ago)).isoformat(), "prs": [],
            "hours": sum(stages.values()), "stages": stages, "idle_hours": idle,
            "idle_spans": [[h(a).isoformat(), h(b).isoformat()] for a, b in spans]}


def ready(kind="type:feature"):
    return [(h(0), "labeled", kind), (h(0), "labeled", "state:ready")]


class StagesTest(unittest.TestCase):
    def test_a_protected_item_through_every_stage(self):
        # Opened 0, ready until a Developer run 1–2 opens PR 9; the Reviewer asks for changes 3–4; the fix
        # runs 5–6; re-review 7–8 approves; the code owner approves at 10; merged and closed at 11.
        item = {"number": 8, "opened": h(0), "closed": h(11), "labels": ready()}
        pr = {"number": 9, "opened": h(2), "closed": h(11), "labels": [],
              "reviews": [(h(4), BOT, "CHANGES_REQUESTED"), (h(8), BOT, "APPROVED"), (h(10), OWNER, "APPROVED")]}
        runs = [(h(1), h(2), "developer", 8), (h(3), h(4), "reviewer", 9), (h(5), h(6), "developer", 9),
                (h(7), h(8), "reviewer", 9)]
        got = flow.record(item, [pr], runs, BOT, OWNER)
        self.assertEqual(got["stages"], {"queued": 1, "running": 4, "review": 2, "rework": 1, "owner": 2, "merging": 1})
        self.assertEqual(got["hours"], 11)
        self.assertEqual(got["idle_hours"], 3, "queued 0–1, review 2–3 and 6–7 with nothing running")
        self.assertEqual(got["idle_spans"], [[h(a).isoformat(), h(b).isoformat()] for a, b in ((0, 1), (2, 3), (6, 7))])

    def test_holds_and_triage_take_precedence_over_waiting(self):
        item = {"number": 8, "opened": h(0), "closed": h(5), "labels": [
            (h(0), "labeled", "state:triage"), (h(1), "unlabeled", "state:triage"), (h(1), "labeled", "needs-decision:owner"),
            (h(3), "unlabeled", "needs-decision:owner"), (h(3), "labeled", "needs-decision:architect"),
            (h(4), "unlabeled", "needs-decision:architect"), (h(4), "labeled", "state:blocked")]}
        got = flow.record(item, [], [], BOT, OWNER)
        self.assertEqual(got["stages"], {"triage": 1, "owner": 2, "decision": 1, "blocked": 1})
        self.assertEqual(got["idle_hours"], 0, "a hold isn't a wait a run would end")

    def test_another_items_run_means_the_system_was_not_idle(self):
        item = {"number": 8, "opened": h(0), "closed": h(2), "labels": ready("type:fix")}
        got = flow.record(item, [], [(h(0), h(1), "developer", 30)], BOT, OWNER)
        self.assertEqual(got["stages"], {"queued": 2})
        self.assertEqual(got["idle_hours"], 1)

    def test_only_waits_the_dispatcher_would_end_are_idle(self):
        # #142: #49 waited 15 h, unscheduled, on the Owner's tag; #43 waited, ready, for the builder outside the order.
        for labels in ([], ready("type:system"), [(h(0), "labeled", "type:fix")]):
            got = flow.record({"number": 8, "opened": h(0), "closed": h(3), "labels": labels}, [], [], BOT, OWNER)
            self.assertEqual((got["stages"], got["idle_hours"], got["idle_spans"]), ({"queued": 3}, 0, []), labels)

    def test_a_claim_is_a_run(self):
        # The Owner's local session claims its item but records no run.
        item = {"number": 8, "opened": h(0), "closed": h(3),
                "labels": ready() + [(h(1), "labeled", "state:claimed"), (h(2), "unlabeled", "state:claimed")]}
        got = flow.record(item, [], [], BOT, OWNER)
        self.assertEqual((got["stages"], got["idle_hours"]), ({"queued": 2, "running": 1}, 2))

    def test_a_stretch_runs_across_stages_and_short_ones_are_not_kept(self):
        # Queued 0–1, then PR 9 waits for the Reviewer 1–2, then queued again until a run at 2.25: one stretch.
        item = {"number": 8, "opened": h(0), "closed": h(4), "labels": ready()}
        pr = {"number": 9, "opened": h(1), "closed": h(2), "labels": [], "reviews": []}
        got = flow.record(item, [pr], [(h(2.25), h(4), "developer", 8)], BOT, OWNER)
        self.assertEqual(got["idle_hours"], 2.25)
        self.assertEqual(got["idle_spans"], [[h(0).isoformat(), h(2.25).isoformat()]])
        got = flow.record(item, [pr], [(h(0.4), h(1), "developer", 8), (h(1.4), h(4), "reviewer", 9)], BOT, OWNER)
        self.assertEqual((got["idle_hours"], got["idle_spans"]), (0.8, []), "0–0.4 and 1–1.4 are each under half an hour")
        got = flow.record(item, [pr], [(h(0.5), h(1), "developer", 8), (h(1.4), h(4), "reviewer", 9)], BOT, OWNER)
        self.assertEqual(got["idle_spans"], [[h(0).isoformat(), h(0.5).isoformat()]], "exactly half an hour is kept")

    def test_an_unprotected_pr_goes_from_approval_straight_to_merging(self):
        item = {"number": 8, "opened": h(0), "closed": h(3), "labels": []}
        pr = {"number": 9, "opened": h(0), "closed": h(3), "labels": [], "reviews": [(h(1), BOT, "APPROVED")]}
        self.assertEqual(flow.record(item, [pr], [], BOT, OWNER)["stages"], {"review": 1, "merging": 2})

    def test_fetch_reads_the_timelines_of_the_prs_that_resolve_the_item(self):
        def api(method, path, paginate=False):
            if path.endswith("issues/8/timeline?per_page=100"):
                return [{"event": "labeled", "created_at": "2026-10-27T08:00:00Z", "label": {"name": "state:ready"}},
                        {"event": "cross-referenced", "source": {"issue": {"number": 11, "pull_request": {}, "body": "See #8"}}},
                        {"event": "cross-referenced", "source": {"issue": {"number": 9, "pull_request": {}, "body": "Resolves #8",
                         "created_at": "2026-10-27T10:00:00Z", "closed_at": "2026-10-27T11:00:00Z"}}}]
            if path.endswith("issues/9/timeline?per_page=100"):
                return [{"event": "reviewed", "submitted_at": "2026-10-27T10:30:00Z", "user": {"login": BOT}, "state": "approved"}]
            raise AssertionError(path)
        gh = mock.Mock(repo="aray4702/auto_research", api=mock.Mock(side_effect=api))
        issue = {"number": 8, "created_at": "2026-10-27T08:00:00Z", "closed_at": "2026-10-27T11:00:00Z"}
        got = flow.fetch(gh, issue, [], BOT, OWNER)
        self.assertEqual((got["prs"], got["stages"]), ([9], {"queued": 2, "review": 0.5, "merging": 0.5}),
                         "only the PR that resolves it; a mention is not a PR of the item")


class SignalsTest(unittest.TestCase):
    BEFORE = [row(n, 10 + n, {"review": 1.0, "running": 0.2}) for n in range(3)]

    def test_a_stage_more_than_double_its_history_and_over_an_hour_longer_is_a_bottleneck(self):
        week = [row(10 + n, 1, {"review": 3.5, "running": 0.2}) for n in range(3)]
        got = flow.bottlenecks(self.BEFORE + week, NOW)
        self.assertEqual([(k, lvl) for k, lvl, *_ in got], [("bottleneck:review:2026-W44", "builder")])
        near = [row(10 + n, 1, {"review": 1.9, "running": 0.2}) for n in range(3)]
        self.assertEqual(flow.bottlenecks(self.BEFORE + near, NOW), [], "doubled, but not by over an hour")
        self.assertEqual(flow.bottlenecks(self.BEFORE + week[:2], NOW), [], "two items can't decide")

    def test_a_stretch_of_waiting_while_nothing_ran_is_raised(self):
        got = flow.idle_waits([row(1, 1, {"queued": 3}, idle=1.5, spans=[(0, 1.5)]),
                               row(2, 2, {"queued": 1}, idle=0.6, spans=[(3, 3.6)])], NOW)
        self.assertEqual([(k, lvl) for k, lvl, *_ in got], [("idle-span:2026-W44", "builder")])
        self.assertIn("2 stretches this week of 30 minutes or more", got[0][3])
        self.assertIn("#1 10-27 01:00–02:30 PDT (1.5 h); #2 10-27 04:00–04:36 PDT (0.6 h)", got[0][3])
        short = [row(n, 1, {"queued": 1}, idle=0.4) for n in range(40)]
        before = {k: v for k, v in row(41, 1, {"queued": 3}, idle=3).items() if k != "idle_spans"}
        self.assertEqual(flow.idle_waits(short + [before, row(50, 9, {"queued": 5}, idle=5, spans=[(0, 5)])], NOW), [],
                         "short waits don't add up to a stretch, rows from before stretches and last week's items don't count")

    def test_metrics_and_report(self):
        week = [row(1, 1, {"queued": 1, "running": 1}), row(2, 2, {"owner": 6, "running": 1}, idle=0.5)]
        self.assertEqual(flow.metrics(week, NOW), {"median_item_hours": 4.5})
        self.assertEqual(flow.metrics([], NOW), {"median_item_hours": 0.0})
        text = flow.report(self.BEFORE + week, NOW)
        self.assertIn("Flow of 2 items closed in the 7 days to 2026-10-28 00:30 PDT, against 3 in the 28 days before.", text)
        self.assertIn("| owner | 3.0 | 67% | 0.0 |", text)
        self.assertIn("Bottleneck: `owner`, 67% of the time items were open.", text)
        self.assertIn("Waiting while nothing ran: 0.5 h.", text)
        self.assertIn("Slowest: #2 7.0 h (mostly owner); #1 2.0 h (mostly queued).", text)
        self.assertIn("No items closed", flow.report([], NOW))


if __name__ == "__main__":
    unittest.main()
