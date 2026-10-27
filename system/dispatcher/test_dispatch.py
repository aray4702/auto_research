"""Tests for the Dispatcher: its snapshot parsing, its plan, its notices and one dry-run pass."""

import base64
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from system.dispatcher import dispatch, github_state, notify, plan
from system.tools import post

ROOT = Path(__file__).resolve().parents[2]
IDENTITY = post.load_identity()
CAPS = tomllib.loads((ROOT / "system" / "caps.toml").read_text())
NOW = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)
BOT, SECOND = IDENTITY["reviewer_app"]["bot_login"], IDENTITY["second_login"]


def issue(n, *labels, title="t"):
    return {"number": n, "title": title, "labels": set(labels), "body": "", "url": f"u/{n}"}


def pr(n, role="developer", gate="SUCCESS", head="h2", merge_state="BLOCKED", decision="REVIEW_REQUIRED",
       reviews=(), labels=(), protected=False, auto_merge=False, draft=False, resolves=()):
    return {"number": n, "title": "p", "draft": draft, "head": head, "branch": "b", "merge_state": merge_state,
            "resolves": list(resolves),
            "review_decision": decision, "auto_merge": auto_merge, "author_role": role, "labels": set(labels),
            "files": [], "protected": protected, "gate": gate,
            "reviews": [{"login": login, "state": s, "commit": c, "at": None} for login, s, c in reviews]}


def snap(issues=(), prs=(), order=()):
    return {"issues": list(issues), "prs": list(prs), "work_order": list(order)}


class SnapshotParsingTest(unittest.TestCase):
    def test_work_order_rows_in_order_with_parallel_safety(self):
        body = "note\n\n| Order | Item | Parallel-safe | Note |\n| --- | --- | --- | --- |\n| 1 | #23 | yes | a |\n| 2 | #9 | no | |\n"
        self.assertEqual(github_state.work_order(body), [(23, True), (9, False)])

    def test_protected_paths_follow_codeowners(self):
        patterns = github_state.protected_patterns((ROOT / ".github" / "CODEOWNERS").read_text())
        self.assertTrue(github_state.is_protected("system/caps.toml", patterns))
        self.assertTrue(github_state.is_protected("blueprint.md", patterns))
        self.assertTrue(github_state.is_protected(".github/workflows/gate.yml", patterns))
        self.assertFalse(github_state.is_protected("src/app.py", patterns))
        self.assertFalse(github_state.is_protected("design/internal/x.md", patterns))

    def test_pr_author_role_is_trusted_only_from_the_owners_account(self):
        body = "<!-- role:developer run:r -->\nResolves #3"
        self.assertEqual(github_state.author_role("aray4702", body, IDENTITY), "developer")
        self.assertIsNone(github_state.author_role("stranger", body, IDENTITY))
        self.assertIsNone(github_state.author_role("aray4702", "plain", IDENTITY))

    def test_gate_comes_from_github_actions_only(self):
        def node(*suites):
            return {"commits": {"nodes": [{"commit": {"checkSuites": {"nodes": list(suites)}}}]}}
        suite = lambda app, status, conclusion: {"app": {"databaseId": app}, "checkRuns": {"nodes": [{"status": status, "conclusion": conclusion}]}}
        self.assertEqual(github_state.gate_of(node(suite(15368, "COMPLETED", "SUCCESS")), 15368), "SUCCESS")
        self.assertEqual(github_state.gate_of(node(suite(15368, "COMPLETED", "CANCELLED")), 15368), "FAILURE")
        self.assertIsNone(github_state.gate_of(node(suite(15368, "IN_PROGRESS", None)), 15368))
        self.assertIsNone(github_state.gate_of(node(suite(999, "COMPLETED", "SUCCESS")), 15368))

    def test_app_reviews_are_matched_by_the_bot_login_identity_uses(self):
        # GraphQL reports the App's review author as login "aray4702-reviewer-app", __typename "Bot";
        # REST and identity.toml say "aray4702-reviewer-app[bot]". Missing this made every App review invisible.
        node = {"number": 23, "title": "t", "body": "<!-- role:architect run:a -->\nx", "isDraft": False,
                "headRefOid": "h", "headRefName": "b", "mergeStateStatus": "BLOCKED", "reviewDecision": "CHANGES_REQUESTED",
                "author": {"login": "aray4702"}, "autoMergeRequest": None, "files": {"nodes": []},
                "commits": {"nodes": []},
                "reviews": {"nodes": [
                    {"author": {"login": "aray4702-reviewer-app", "__typename": "Bot"}, "state": "CHANGES_REQUESTED", "commit": {"oid": "h"}},
                    {"author": {"login": "sloan3165", "__typename": "User"}, "state": "APPROVED", "commit": {"oid": "h"}},
                ]}}
        p = github_state.pull_request(node, set(), IDENTITY, [])
        self.assertEqual([r["login"] for r in p["reviews"]], [BOT, SECOND])
        self.assertEqual((p["author"], p["author_role"]), ("aray4702", "architect"))
        got = plan.candidates({"issues": [], "prs": [p], "work_order": []}, set(), IDENTITY)
        self.assertEqual([(c[0], c[1], c[3]) for c in got], [("architect", 23, "pr-work")])


class CandidatesTest(unittest.TestCase):
    def test_dispatch_order(self):
        s = snap(
            issues=[issue(1, "type:fix", "sev:1"), issue(2, "type:fix", "needs-decision:architect"), issue(3),
                    issue(4, "type:feature", "state:ready"), issue(11, "record:work-order")],
            prs=[pr(20, gate="FAILURE"), pr(21)],
            order=[(4, False)],
        )
        got = [(role, item, why) for role, item, _, why in plan.candidates(s, set(), IDENTITY)]
        self.assertEqual(got, [
            ("developer", 1, "sev:1"), ("developer", 20, "pr-work"), ("reviewer", 21, "review"),
            ("architect", 2, "decision"), ("coordinator", None, "triage"), ("developer", 4, "work-order"),
        ])

    def test_closing_events_and_plan_reviews(self):
        s = snap(issues=[issue(3), issue(21, "record:goal", "state:review")])
        s["closed"] = [20]
        got = [(r, i, t, w) for r, i, t, w in plan.candidates(s, set(), IDENTITY)]
        self.assertEqual([(r, i, w) for r, i, _, w in got], [("reviewer", 21, "plan-review"), ("coordinator", None, "triage+closed")])
        self.assertEqual(got[1][2], "Triage these issues: #3. Items closed since your last run: #20. "
                                    "Record their goals' progress, close what is done, and chart and schedule what comes next.")

    def test_a_plan_reviews_changes_on_a_goal_issue_start_the_coordinator(self):
        # #30: the Reviewer escalates a plan's changes with needs-decision:coordinator on the goal issue.
        goal = issue(21, "record:goal", "needs-decision:coordinator")
        got = plan.candidates(snap([goal]), set(), IDENTITY)
        self.assertEqual([(r, i, w) for r, i, _, w in got], [("coordinator", 21, "decision")])
        # The Coordinator resolves it and removes the label: nothing on the next pass.
        self.assertEqual(plan.candidates(snap([issue(21, "record:goal")]), set(), IDENTITY), [])

    def test_an_owner_answer_starts_the_role_that_asked(self):
        goal = issue(21, "record:goal", "needs-decision:owner")
        got = plan.candidates(snap([goal]), set(), IDENTITY, answered={21: "coordinator"})
        self.assertEqual([(r, i, w) for r, i, _, w in got], [("coordinator", 21, "answered")])
        self.assertIn("remove needs-decision:owner", got[0][2])
        claimed = issue(21, "record:goal", "needs-decision:owner", "state:claimed")
        self.assertEqual(plan.candidates(snap([claimed]), set(), IDENTITY, answered={21: "coordinator"}), [])

    def test_a_new_goal_in_the_requirements_starts_the_coordinator(self):
        text = ("## 2. Goals\n\n| ID  | Goal | Outcome | Deadline |\n| --- | --- | --- | --- |\n"
                "| G0  | the starting point (§0) | its done-check |  |\n| ~~S1~~ | ~~seed~~ | ~~x~~ | none |\n"
                "| G1  | Transcribe calls | WER < 10 % | 2026-12-01 |\n| G2  |  |  |  |\n\n## 3. Use cases\n| U1 | x |\n")
        self.assertEqual(github_state.requirement_goals(text), ["G1"])
        s = snap()
        s["new_goals"] = ["G1"]
        got = plan.candidates(s, set(), IDENTITY)
        self.assertEqual([(r, i, w) for r, i, _, w in got], [("coordinator", None, "new-goal")])
        self.assertIn("New goals in the requirements without a goal issue: G1.", got[0][2])

    def test_the_snapshot_offers_only_goals_on_main_without_a_goal_issue(self):
        gh = FakeGitHub()
        gh.requirements += "| G1 | Transcribe calls | WER < 10 % | 2026-12-01 |\n"
        self.assertEqual(github_state.snapshot(gh, IDENTITY)["new_goals"], ["G1"])
        self.assertEqual(github_state.snapshot(FakeGitHub(), IDENTITY)["new_goals"], [])

    def test_claimed_running_and_records_are_never_offered(self):
        s = snap(issues=[issue(1, "type:fix", "sev:1", "state:claimed"), issue(2, "type:fix", "sev:1"), issue(13, "record:decision-log")])
        self.assertEqual(plan.candidates(s, {2}, IDENTITY), [])

    def test_a_reviewed_head_is_not_reviewed_again_until_it_changes(self):
        s = snap(prs=[pr(5, reviews=[(BOT, "APPROVED", "h2")])])
        self.assertEqual(plan.candidates(s, set(), IDENTITY), [])
        s = snap(prs=[pr(5, reviews=[(BOT, "APPROVED", "h1")])])
        self.assertEqual([c[3] for c in plan.candidates(s, set(), IDENTITY)], ["review"])
        self.assertEqual(plan.candidates(s, set(), IDENTITY, skip_review={5}), [])

    def test_changes_requested_on_the_head_sends_it_back_to_its_author(self):
        s = snap(prs=[pr(5, role="builder", reviews=[(BOT, "CHANGES_REQUESTED", "h2")])])
        self.assertEqual([(c[0], c[3]) for c in plan.candidates(s, set(), IDENTITY)], [("builder", "pr-work")])

    def test_owner_prs_and_drafts_are_left_alone(self):
        s = snap(prs=[pr(5, role=None, gate="FAILURE"), pr(6, draft=True, gate="FAILURE")])
        self.assertEqual(plan.candidates(s, set(), IDENTITY), [])

    def test_an_item_whose_pr_is_open_is_in_review_not_dispatched_again(self):
        items = [issue(20, "type:definition", "state:ready"), issue(30, "type:fix", "state:ready")]
        prs = [pr(23, role="architect", resolves=[20], reviews=[(BOT, "APPROVED", "h2")])]
        got = plan.candidates(snap(items, prs, order=[(20, True), (30, False)]), set(), IDENTITY)
        self.assertEqual(got, [], "#20 is in review, and while it is, only parallel-safe items may start")
        got = plan.candidates(snap(items, prs, order=[(20, True), (30, True)]), set(), IDENTITY)
        self.assertEqual([c[1] for c in got], [30])

    def test_resolves_is_read_from_the_pr_body(self):
        node_body = "<!-- role:architect run:a -->\nResolves #20. Also fixes #7; closes #7."
        self.assertEqual(sorted({int(n) for n in github_state.RESOLVES.findall(node_body)}), [7, 20])

    def test_only_parallel_safe_items_start_while_one_is_busy(self):
        items = [issue(4, "type:fix", "state:ready", "state:claimed"), issue(5, "type:fix", "state:ready"),
                 issue(6, "type:fix", "state:ready")]
        got = plan.candidates(snap(items, order=[(4, False), (5, False), (6, True)]), set(), IDENTITY)
        self.assertEqual([c[1] for c in got], [6])
        free = [issue(5, "type:fix", "state:ready"), issue(6, "type:definition", "state:ready")]
        got = plan.candidates(snap(free, order=[(5, False), (6, False)]), set(), IDENTITY)
        self.assertEqual([c[1] for c in got], [5])


class ChooseTest(unittest.TestCase):
    def test_caps_hold_the_rest(self):
        s = snap(issues=[issue(1, "type:fix", "sev:1"), issue(2, "type:fix", "sev:1"), issue(3)], prs=[pr(21)])
        starts, skipped = plan.choose(s, [], CAPS, IDENTITY, NOW)
        self.assertEqual([(r, i) for r, i, _, _ in starts], [("developer", 1), ("reviewer", 21)])
        self.assertEqual([(r, i) for r, i, _, _ in skipped], [("developer", 2), ("coordinator", None)])


class MergePlanTest(unittest.TestCase):
    def test_one_pr_at_a_time_refreshing_then_merging(self):
        s = snap(prs=[pr(8, merge_state="BEHIND", decision="APPROVED"), pr(9, merge_state="CLEAN", decision="APPROVED")])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [("refresh", 8, "behind main")])
        s = snap(prs=[pr(9, merge_state="CLEAN", decision="APPROVED")])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [("auto-merge", 9, "h2")])
        s = snap(prs=[pr(9, merge_state="CLEAN", decision="APPROVED", protected=True)])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [("ask-owner-to-merge", 9, "h2")])
        s = snap(prs=[pr(7, merge_state="CLEAN", decision="APPROVED", auto_merge=True), pr(9, merge_state="CLEAN", decision="APPROVED")])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [])

    def test_a_protected_pr_is_refreshed_before_the_owner_is_asked_once(self):
        # Behind main and waiting only on the Owner: refresh first, ask nothing yet.
        s = snap(prs=[pr(23, role="architect", protected=True, merge_state="BEHIND", reviews=[(BOT, "APPROVED", "h1")], head="h1")])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [("refresh", 23, "behind main")])
        # Refreshed: the App's approval is on the old head, so renew it, and still don't ask the Owner.
        s = snap(prs=[pr(23, role="architect", protected=True, merge_state="BLOCKED", reviews=[(BOT, "APPROVED", "h1")], head="h2")])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [("renew", 23, "h1")])
        # Renewed on the new head and up to date: now ask the Owner, once.
        s = snap(prs=[pr(23, role="architect", protected=True, merge_state="BLOCKED", reviews=[(BOT, "APPROVED", "h2")], head="h2")])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [("ask-owner-to-approve", 23, "h2")])
        # The Owner approved: ask them to merge (protected PRs are theirs to merge).
        s = snap(prs=[pr(23, role="architect", protected=True, merge_state="CLEAN", decision="APPROVED",
                         reviews=[(BOT, "APPROVED", "h2"), (SECOND, "APPROVED", "h2")], head="h2")])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [("ask-owner-to-merge", 23, "h2")])

    def test_the_owner_is_not_asked_while_github_is_still_working_out_the_merge_state(self):
        # Right after main moves, GitHub reports mergeStateStatus UNKNOWN; #34 was really 12 commits behind.
        s = snap(prs=[pr(34, protected=True, merge_state="UNKNOWN", reviews=[(BOT, "APPROVED", "h2")])])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [])
        s = snap(prs=[pr(34, protected=True, merge_state="BLOCKED", reviews=[(BOT, "APPROVED", "h2")])])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [("ask-owner-to-approve", 34, "h2")])

    def test_renewals_and_owner_approvals(self):
        s = snap(prs=[pr(9, reviews=[(BOT, "APPROVED", "h1")])])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [("renew", 9, "h1")])
        s = snap(prs=[pr(9, protected=True, reviews=[(BOT, "APPROVED", "h2")])])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [("ask-owner-to-approve", 9, "h2")])
        s = snap(prs=[pr(9, protected=True, reviews=[(BOT, "APPROVED", "h1"), (SECOND, "APPROVED", "h1")])])
        self.assertEqual(plan.merge_plan(s, IDENTITY), [("renew", 9, "h1"), ("ask-owner-to-reapprove", 9, "h2")])


def comment(cid, login, body, minutes):
    at = (NOW + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
    return {"id": cid, "user": {"login": login}, "body": body, "created_at": at, "html_url": f"c/{cid}"}


class NotifyTest(unittest.TestCase):
    ASK = "<!-- role:coordinator run:c1 -->\n**Needs a decision: owner.**\n\nShip it?\n1. Yes (recommended)\n2. No"

    def test_a_question_is_sent_once_then_reminded_once_until_answered(self):
        item = {"number": 7, "title": "Release"}
        comments = [comment(1, "aray4702", self.ASK, 0)]
        got = notify.owner_questions(item, comments, IDENTITY, [], NOW, post.MARKER)
        self.assertEqual([k for k, _ in got], ["ask:1"])
        self.assertIn("Ship it?", got[0][1])
        self.assertNotIn("Needs a decision", got[0][1])
        sent = [{"key": "ask:1", "at": NOW.isoformat()}]
        self.assertEqual(notify.owner_questions(item, comments, IDENTITY, sent, NOW + timedelta(hours=1), post.MARKER), [])
        later = NOW + timedelta(hours=25)
        self.assertEqual([k for k, _ in notify.owner_questions(item, comments, IDENTITY, sent, later, post.MARKER)], ["remind:1"])
        sent.append({"key": "remind:1", "at": later.isoformat()})
        self.assertEqual(notify.owner_questions(item, comments, IDENTITY, sent, later + timedelta(days=3), post.MARKER), [])

    def test_only_the_owner_answers(self):
        item = {"number": 7, "title": "Release"}
        outsider = [comment(1, "aray4702", self.ASK, 0), comment(2, "stranger", "1", 5)]
        self.assertTrue(notify.owner_questions(item, outsider, IDENTITY, [], NOW, post.MARKER))
        answered = [comment(1, "aray4702", self.ASK, 0), comment(2, "aray4702", "1", 5)]
        self.assertEqual(notify.owner_questions(item, answered, IDENTITY, [], NOW, post.MARKER), [])

    def test_latest_ask_names_the_asker_and_whether_the_owner_answered(self):
        ask = comment(1, "aray4702", self.ASK, 0)
        self.assertEqual(notify.latest_ask([ask], IDENTITY, post.MARKER)[1:], ("coordinator", False))
        self.assertEqual(notify.latest_ask([ask, comment(2, "sloan3165", "1", 5)], IDENTITY, post.MARKER)[1:], ("coordinator", True))
        self.assertEqual(notify.latest_ask([ask, comment(2, "stranger", "1", 5)], IDENTITY, post.MARKER)[1:], ("coordinator", False))
        self.assertIsNone(notify.latest_ask([comment(3, "aray4702", "plain", 0)], IDENTITY, post.MARKER))

    def test_unsent_filters_by_key(self):
        self.assertEqual(notify.unsent([{"key": "a"}], [("a", "x"), ("b", "y")]), [("b", "y")])


class FakeGitHub:
    repo = "aray4702/auto_research"
    requirements = "## 2. Goals\n\n| ID | Goal | Outcome | Deadline |\n| --- | --- | --- | --- |\n| S1 | seed | x | none |\n"

    def __init__(self, repo=None):
        self.writes = []

    def api(self, method, path, body=None, paginate=False):
        if method != "GET":
            self.writes.append((method, path))
        if path.endswith("actions/variables/AGENTS_PAUSED"):
            raise SystemExit("failed: Not Found (HTTP 404)")
        if path == f"repos/{self.repo}/contents/project_requirements.md?ref=main":
            return {"encoding": "base64", "content": base64.b64encode(self.requirements.encode()).decode()}
        if "labels=record%3Agoal" in path:
            return [{"number": 21, "title": "Goal S1: seed goal (README)", "labels": [{"name": "record:goal"}]}]
        if path.startswith(f"repos/{self.repo}/issues?state=closed"):
            assert "since=" in path, path
            return [{"number": 20, "title": "Def", "labels": [{"name": "type:definition"}], "closed_at": "2026-10-27T02:30:00Z"},
                    {"number": 13, "title": "Log", "labels": [{"name": "record:decision-log"}], "closed_at": "2026-10-27T02:40:00Z"},
                    {"number": 5, "title": "Old", "labels": [], "closed_at": "2026-10-10T00:00:00Z"}]
        if path.startswith(f"repos/{self.repo}/issues?"):
            return [
                {"number": 1, "title": "Crash", "labels": [{"name": "type:fix"}, {"name": "sev:1"}], "html_url": "u", "body": ""},
                {"number": 2, "title": "Idea", "labels": [], "html_url": "u", "body": ""},
            ]
        return []

    def graphql(self, query, **variables):
        return {"repository": {"pullRequests": {"nodes": []}}}


class FakeStore:
    def __init__(self, *args):
        self.appended = []

    def read(self, kind):
        return []

    def append(self, kind, record):
        self.appended.append((kind, record))


class WordingTest(unittest.TestCase):
    def test_articles(self):
        self.assertEqual([dispatch.article(r) for r in ("architect", "developer", "reviewer")], ["an", "a", "a"])


class DryRunTest(unittest.TestCase):
    def test_a_dry_run_plans_and_changes_nothing(self):
        stores = []
        with mock.patch.object(post, "GitHub", FakeGitHub), \
             mock.patch("system.dispatcher.state.Store", side_effect=lambda *a: stores.append(FakeStore()) or stores[-1]), \
             mock.patch.object(dispatch, "spawn") as spawn, \
             mock.patch.object(dispatch.relay, "run", return_value=[]):  # never Discord from a test (test_relay.py)
            lines = dispatch.dispatch_pass("reconcile", dry_run=True, now=NOW)
        self.assertEqual(lines, [
            "would start a developer run on #1 (sev:1): Item #1 is sev:1: revert first (blueprint §3), then fix the cause.",
            "would start a coordinator run (triage+closed): Triage these issues: #2. Items closed since your last run: #20. "
            "Record their goals' progress, close what is done, and chart and schedule what comes next.",
        ])
        spawn.assert_not_called()
        self.assertEqual(stores[0].appended, [])


if __name__ == "__main__":
    unittest.main()
