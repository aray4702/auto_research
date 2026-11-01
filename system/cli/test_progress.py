"""Tests for the Progress tab's table (#241). Nothing here touches GitHub."""

import unittest
from datetime import datetime, timezone

from system.cli import progress, views

REQUIREMENTS = """## 2. Goals

| ID  | Goal | Outcome | Deadline |
| --- | ---- | ------- | -------- |
| G1  | Offline | F1 | 2026-10-30 for the CI checks; 2026-10-31 for the Mac |
| G2  | Live | F2 | 2026-10-31 |

## 4. Functional requirements

| ID  | Requirement | Priority |
| --- | ----------- | -------- |
| F1  | Transcribe a recording. Inputs: WAV | must |
| F2  | Label each segment with its speaker | must |

## 5. Quality

| ID  | Attribute | Target | Conditions | Priority |
| --- | --------- | ------ | ---------- | -------- |
| Q1  | accuracy | WER ≤ 5 % on LibriSpeech | AMI | must |
| Q2  | observability | `--verbose` shows the time of each stage | | should |
"""

SPEC = """| ID | Covers | Check |
| --- | --- | --- |
| G1-1 | F1 | The clip transcribes |
| G1-2 | F1, Q1 | Formats |
| G2-1 | F2, U2 | Live segments |
"""

at = lambda h, m=0: datetime(2026, 10, 28, h, m, tzinfo=timezone.utc).isoformat()
ISSUES = {
    10: {"title": "G1 1.1: transcribe (G1-1)", "body": "", "state": "CLOSED", "labels": {"type:feature"}, "closed_at": "2026-10-28T19:00:00Z"},
    11: {"title": "G1 1.2: formats", "body": "Builds G1-2.", "state": "OPEN", "labels": {"type:feature"}},
    15: {"title": "G2 2.1: live (G2-1)", "body": "", "state": "CLOSED", "labels": {"type:feature"}, "closed_at": "2026-10-28T21:00:00Z"},
    16: {"title": "G2 2.2: live, part two (G2-1)", "body": "", "state": "CLOSED", "labels": {"type:feature"}, "closed_at": "2026-10-28T22:30:00Z"},
    12: {"title": "G1 design", "body": "Covers G1-1, G1-2 and G2-1.", "state": "CLOSED", "labels": {"type:definition"}},
    13: {"title": "Dispatcher fix", "body": "", "state": "CLOSED", "labels": {"type:system"}},
    14: {"title": "Goal G1", "body": "G1-1", "state": "OPEN", "labels": {"record:goal"}},
}
PRS = [{"number": 20, "body": "Resolves #10\nResolves #11"}, {"number": 21, "body": "Resolves #13"}]
RUNS = [
    {"event": "start", "item": 20, "at": at(18)}, {"event": "end", "item": 20, "at": at(19), "cost_usd": 4.0},
    {"event": "start", "item": 12, "at": at(10)}, {"event": "end", "item": 12, "at": at(11), "cost_usd": 3.0},
    {"event": "start", "item": 21, "at": at(9)}, {"event": "end", "item": 21, "at": at(9, 30), "cost_usd": 2.0},
    {"event": "start", "item": None, "at": at(8)}, {"event": "end", "item": None, "at": at(8, 30), "cost_usd": 1.0},
    {"event": "start", "item": 14, "at": at(7)}, {"event": "end", "item": 14, "at": at(7, 30), "cost_usd": 0.5},
]


class ProgressTableTest(unittest.TestCase):
    def setUp(self):
        self.p = progress.table(REQUIREMENTS, SPEC, ISSUES, PRS, RUNS)
        self.row = {r["id"]: r for r in self.p["rows"]}

    def test_every_feature_and_requirement_in_order_with_its_criteria(self):
        self.assertEqual([r["id"] for r in self.p["rows"]], ["F1", "F2", "Q1", "Q2"])
        self.assertEqual(self.row["F1"]["criteria"], ["G1-1", "G1-2"])
        self.assertEqual(self.row["Q1"]["criteria"], ["G1-2"])
        self.assertEqual(self.row["F1"]["name"], "Transcribe a recording", "its first sentence")
        self.assertEqual(self.row["Q1"]["name"], "WER ≤ 5 % on LibriSpeech", "a requirement's target")

    def test_status_comes_from_the_feature_items_and_is_never_met_until_checks_report(self):
        self.assertEqual(self.row["F1"]["status"], "in progress: 1 of 2 criteria built")
        self.assertEqual(self.row["F1"]["built"], ["G1-1"])
        self.assertEqual(self.row["F2"]["status"], "built, not yet verified", "every feature item naming G2-1 is closed")
        self.assertEqual(self.row["Q2"]["status"], "no criterion in the spec")
        self.assertFalse(any(r["status"] == "met" for r in self.p["rows"]))

    def test_it_ends_when_its_last_criterion_is_built(self):
        self.assertEqual(self.row["F2"]["ended"], datetime(2026, 10, 28, 22, 30, tzinfo=timezone.utc), "the later of #15 and #16")
        self.assertIsNone(self.row["F1"]["ended"], "G1-2 isn't built yet: #11 is open")
        self.assertIsNone(self.row["Q2"]["ended"])

    def test_start_due_and_cost_follow_the_items_and_their_prs(self):
        self.assertEqual(self.row["F1"]["started"], datetime(2026, 10, 28, 18, tzinfo=timezone.utc), "the first run on its PR")
        self.assertEqual(self.row["F1"]["cost"], 4.0, "the PR resolving #10 and #11 counts for both, split between them")
        self.assertEqual(self.row["Q1"]["cost"], 2.0, "#11 alone builds G1-2")
        self.assertEqual(self.row["F1"]["due"], "G1 10-30 / 10-31")
        self.assertEqual(self.row["F2"]["due"], "G2 10-31")

    def test_the_cost_splits_by_kind_of_work(self):
        self.assertEqual(dict(self.p["costs"]), {"feature items": 4.0, "definition and design": 3.0, "system": 2.0,
                                                  "Coordinator passes and daily runs": 1.0, "records": 0.5})
        self.assertEqual(self.p["total"], 10.5)
        self.assertEqual(self.p["since"], datetime(2026, 10, 28, 7, tzinfo=timezone.utc))


class ProgressViewTest(unittest.TestCase):
    def page_data(self):
        p = progress.table(REQUIREMENTS.replace("Transcribe a recording", "Transcribe <b>a</b> recording"), SPEC, ISSUES, PRS, RUNS)
        p.update(now=at(20), repo="o/r", time_zone="America/Los_Angeles")
        return p

    def test_the_page_marks_its_tab_escapes_github_text_and_says_status_isnt_verified(self):
        page = views.progress_page(self.page_data())
        self.assertIn('<a href="/" aria-current=page>Progress</a>', page, "Progress is the default tab (#246)")
        self.assertIn('<a href="/roles">Roles</a>', page)
        self.assertIn('<a href="/waiting">Waiting on you</a>', page)
        self.assertIn("<th>End</th>", page)
        self.assertIn("Transcribe &lt;b&gt;a&lt;/b&gt; recording", page)
        self.assertNotIn("<b>a</b>", page)
        self.assertIn("Met: 0 of 2 features, 0 of 2 requirements", page)
        self.assertIn("#239", page)
        self.assertIn("$10.50", page)

    def test_the_text_gives_each_row_and_the_cost_split(self):
        out = views.progress_text(self.page_data())
        self.assertIn("F1   Transcribe <b>a</b> recording", out)
        self.assertIn("G1-1, G1-2 · in progress: 1 of 2 criteria built · started 10-28 11:00 · ended — · due G1 10-30 / 10-31 · $4.00", out)
        self.assertIn("G2-1 · built, not yet verified · started — · ended 10-28 15:30 · due G2 10-31", out)
        self.assertIn("  system: $2.00", out)



class RolesTest(unittest.TestCase):
    NOW = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)

    def roles(self, override=None):
        from unittest import mock
        from system.cli import data
        runs = [{"event": "start", "run_id": "d-1", "role": "developer", "item": 122, "at": at(18), "task": "Work on your item."},
                {"event": "end", "run_id": "d-1", "role": "developer", "item": 122, "at": at(19), "status": "finished"},
                {"event": "start", "run_id": "r-1", "role": "reviewer", "item": 245, "at": at(23), "task": "Review PR #245."},
                {"event": "start", "run_id": "c-1", "role": "coordinator", "item": None, "at": at(22)},
                {"event": "end", "run_id": "c-1", "role": "coordinator", "item": None, "at": at(22, 5), "status": "finished"}]
        store, gh = mock.Mock(), mock.Mock(repo="o/r")
        store.read.return_value = runs
        gh.api.side_effect = lambda method, path, **kw: {
            "repos/o/r/issues/122": {"title": "G1 1.1b: deps"},
            "repos/o/r/issues/245": {"title": "Requirements <v1.11>", "pull_request": {}}}[path]
        from system.tools import runner
        config = runner.load_all()
        config = {**config, "caps": {**config["caps"], "override": override or {}}}
        return data.roles(config, gh=gh, store=store, now=self.NOW)

    def test_each_role_is_working_on_an_item_or_idle_since_its_last_run(self):
        d = {x["role"]: x for x in self.roles()["roles"]}
        self.assertEqual(list(d), ["architect", "coordinator", "developer", "reviewer", "tester", "builder"])
        self.assertEqual([(w["number"], w["kind"]) for w in d["reviewer"]["working"]], [(245, "pull")])
        self.assertEqual(d["developer"]["working"], [])
        self.assertEqual((d["developer"]["last"]["number"], d["developer"]["last"]["kind"]), (122, "issues"))
        self.assertEqual(d["coordinator"]["last"]["title"], "a general pass")
        self.assertIsNone(d["tester"]["last"])

    def test_the_page_marks_its_tab_escapes_titles_and_says_when_caps_are_lifted(self):
        d = self.roles()
        page = views.roles_page(d)
        self.assertIn('<a href="/roles" aria-current=page>Roles</a>', page)
        self.assertIn("1 of 6 roles working", page)
        self.assertIn('<a href="https://github.com/aray4702/local_meeting_transcriber/pull/245">#245</a> Requirements &lt;v1.11&gt;', page)
        self.assertIn("no run yet", page)
        self.assertNotIn("cap lifted", page)
        lifted = self.roles(override={"no_daily_run_caps_until": "2026-10-29T07:00:00+00:00"})
        self.assertIn("daily cap lifted", views.roles_page(lifted))
        out = views.roles_text(lifted)
        self.assertIn("reviewer     working  #245 Requirements <v1.11>", out)
        self.assertIn("developer    idle     since", out)
        expired = self.roles(override={"no_daily_run_caps_until": "2026-10-29T02:00:00+00:00"})
        self.assertIn("runs today: 0 of", views.roles_text(expired), "an override that has ended shows the cap again")


if __name__ == "__main__":
    unittest.main()