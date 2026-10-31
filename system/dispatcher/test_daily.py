"""Tests for the Dispatcher's daily signals and rule audit: each signal raises, and its near miss doesn't."""

import json
import unittest
from datetime import datetime, timedelta, timezone

from system.dispatcher import daily, usage
from system.tools import post, runner

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

    def test_a_compound_command_denied_in_three_runs_is_raised_as_compound_not_a_gap(self):
        sig = usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": "git show HEAD:a.py | sed -n '1,80p'"}})
        runs = [end(x, denied=[sig]) for x in "abc"]
        got = daily.denials(runs, NOW)
        self.assertEqual([(k, lvl) for k, lvl, *_ in got], [("compound:Bash: git show |", "builder")], "the one-command rule (#140)")
        self.assertIn("one command at a time", got[0][2])
        self.assertEqual(daily.denials(runs[:2], NOW), [], "2 runs are not yet a pattern")

    def test_runs_already_raised_under_another_kind_dont_raise_it_again_and_three_later_runs_do(self):
        sig = "Bash: python3 > ;"
        runs = [end(x, denied=[sig], hours_ago=10) for x in "abc"]
        before = [{"key": f"denial:{sig}", "at": (NOW - timedelta(hours=9)).isoformat()}]  # #183, before #147 sorted it as compound
        self.assertEqual(daily.denials(runs, NOW, raised=before), [], "#322 raised #183's three runs again")
        self.assertEqual(daily.denials(runs, NOW, raised=[{"key": "denial:Bash: gh pr", "at": before[0]["at"]}])[0][0],
                         f"compound:{sig}", "another signature's raise doesn't count")
        later = runs + [end(x, denied=[sig], hours_ago=2) for x in "def"]
        got = daily.denials(later, NOW, raised=before)
        self.assertEqual([k for k, *_ in got], [f"compound:{sig} since {before[0]['at']}"], "a new key, so it's raised again")
        self.assertIn("runs this week: d, e, f.", got[0][3])
        self.assertIn("didn't hold", got[0][3])
        again = before + [{"key": got[0][0], "at": (NOW - timedelta(hours=1)).isoformat()}]
        self.assertEqual(daily.denials(later, NOW, raised=again), [], "nor are those raised a third time")
        self.assertEqual(daily.denials(runs, NOW, raised=[{"key": f"denial:{sig}"}])[0][0], f"compound:{sig}", "a record with no time")

    def test_a_path_outside_the_worktree_is_raised_as_outside_not_a_gap(self):
        wt = "/Users/x/runs/reviewer-1"
        for command, want in [("git diff --no-index /Users/x/runs/system/a.md a.md", "Bash: git diff [outside]"),
                              ("git diff HEAD -- ../reviewer-2", "Bash: git diff [outside]"),
                              ("cat ~/notes.md", "Bash: cat [outside]"), ("git diff --output=/tmp/x.diff", "Bash: git diff [outside]"),
                              ("git diff HEAD -- '/Users/x/runs/reviewer-1/../system/a.md'", "Bash: git diff [outside]"),
                              ("git diff HEAD -- /Users/x/runs/reviewer-1/system/a.md", "Bash: git diff"),
                              ("git diff origin/main...HEAD -- system/a.md", "Bash: git diff"),
                              ("git diff HEAD -- system/../system/a.md", "Bash: git diff"),
                              ("/opt/homebrew/bin/python3 -I system/tools/post.py read 1", "Bash: …/python3 -I system/tools/post.py"),
                              ("X=/tmp ~/.local/bin/arsys status", "Bash: …/arsys status"),
                              ("/usr/bin/git diff /tmp/a.md", "Bash: …/git diff [outside]")]:
            self.assertEqual(usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": command}}, wt), want, command)
        self.assertEqual(usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": "cat /etc/hosts"}}), "Bash: cat [outside]",
                         "with no worktree known, any absolute path")
        runs = [end(x, denied=["Bash: git diff [outside]"]) for x in "abc"]
        got = daily.denials(runs, NOW)
        self.assertEqual([(k, lvl) for k, lvl, *_ in got], [("outside:Bash: git diff [outside]", "builder")], "#177")
        self.assertIn("outside the worktree", got[0][2])
        self.assertEqual(daily.denials(runs[:2], NOW), [], "2 runs are not yet a pattern")
        result = {"type": "result", "subtype": "success", "permission_denials": [
            {"tool_name": "Bash", "tool_input": {"command": "git diff HEAD -- ../reviewer-2"}}]}
        self.assertEqual(usage.summarize([json.dumps(result)], wt)["denied"], ["Bash: git diff [outside]"])

    def test_a_denial_the_rules_explain_is_not_raised(self):
        runs = [end(x, denied=["Bash: cat << |"]) for x in "abc"]
        self.assertEqual(daily.denials(runs, NOW, ["Bash: cat << |"]), [])
        self.assertEqual(len(daily.denials(runs, NOW, ["Bash: cat |"])), 1, "only the exact signature is explained")
        explained = runner.load_all()["runner"]["denials"]["explained"]
        sig = usage.denial_signature({"tool_name": "Bash", "tool_input": {
            "command": "cat <<'EOF' | python3 -I system/tools/post.py comment 138 --body-file -\nText | with; pipes\nEOF"}})
        self.assertIn(sig, explained, "the rules' cat pipe, #138")
        sig = usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": "sed -n '1,80p' system/rules.md"}})
        self.assertIn(sig, explained, "sed, which can write files, #184")
        allowed = runner.load_all()["runner"]["common"]["allow"]
        self.assertFalse([a for a in allowed if a.startswith("Bash(sed")], "the explanation holds only while sed is denied")

    def test_uv_run_python_c_is_explained_only_while_no_role_may_run_it(self):
        config = runner.load_all()
        explained = config["runner"]["denials"]["explained"]
        for command in ("uv run --frozen python -c 'import lmt'", "uv run python -c 'print(1)'"):
            sig = usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": command}})
            self.assertIn(sig, explained, "it runs any code, the denied commands included (#92, #324)")
        for role in config["runner"]["roles"]:
            allow = runner.role_settings(config, role)["permissions"]["allow"]
            granted = [a for a in allow if a in ("Bash(uv run:*)", "Bash(uv run --frozen:*)")
                       or a.startswith(("Bash(uv run python", "Bash(uv run --frozen python")) and "-m " not in a]
            self.assertEqual(granted, [], f"{role}: the explanation holds only while uv runs no code it is handed")

    def test_file_tools_are_explained_only_while_every_role_has_them_without_limit(self):
        config = runner.load_all()["runner"]
        for tool in ("Glob", "Grep", "Read"):
            sig = usage.denial_signature({"tool_name": tool, "tool_input": {"pattern": "*", "path": "/elsewhere"}})
            self.assertIn(sig, config["denials"]["explained"], "outside the worktree, --restricted denies it (#185)")
            self.assertIn(tool, config["common"]["allow"], "else a denial could be a real gap")
            self.assertFalse([d for d in config["common"]["deny"] if d.startswith(tool)], "else a denial could be a real gap")
            for role in config["roles"].values():
                self.assertIn(tool, role["tools"], "else a denial could be a real gap")
        self.assertIn("--restricted", config["runner"]["flags"])

    def test_a_plain_ls_is_explained_only_while_every_role_may_run_it_and_a_compound_ls_is_not(self):
        config = runner.load_all()["runner"]
        explained = config["denials"]["explained"]
        sig = usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": "ls ~/Library/Logs/auto_research"}})
        self.assertEqual(sig, "Bash: ls [outside]", "#177")
        self.assertIn(sig, explained, "outside the worktree, --restricted denies it (#182)")
        self.assertIn("Bash: ls", explained, "the form runs recorded before #177")
        self.assertIn("Bash(ls:*)", config["common"]["allow"], "else a denial could be a real gap")
        self.assertFalse([d for d in config["common"]["deny"] if d.startswith("Bash(ls")], "else a denial could be a real gap")
        for role in config["roles"].values():
            self.assertIn("Bash", role["tools"], "else a denial could be a real gap")
        self.assertIn("--restricted", config["runner"]["flags"])
        compound = usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": "ls system | head"}})
        self.assertEqual(len(daily.denials([end(x, denied=[compound]) for x in "abc"], NOW, explained)), 1, "still the one-command rule")

    def test_signatures_keep_the_first_lines_shell_operators_and_no_words(self):
        for command, want in [("cat a.txt | head", "Bash: cat |"), ("cat > notes.md <<'EOF'\nx | y\nEOF", "Bash: cat > <<"),
                              ("cat 'a | b' \"c; d\"", "Bash: cat"), ("cat a.txt 2>/dev/null || ls","Bash: cat > ||"),
                              ("echo $(cat key) && ls; date", "Bash: echo $( && ;"), ("cat a\\|b", "Bash: cat"),
                              ('git diff "$(git merge-base origin/main HEAD)"', "Bash: git diff $("),  # #177
                              ('git diff "`git merge-base origin/main HEAD`" | head', "Bash: git diff ` |"),
                              ("git diff '$(not expanded)'", "Bash: git diff")]:
            self.assertEqual(usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": command}}), want, command)

    def test_a_second_line_is_marked_so_a_multiline_denial_is_raised_as_compound(self):
        for command, want in [("git fetch origin main\ngh pr diff 187", "Bash: git fetch ⏎"),
                              ("git fetch origin main\n", "Bash: git fetch"), ("git fetch \\\n  origin main", "Bash: git fetch"),
                              ("cat > a.md <<'EOF'\nx\nEOF", "Bash: cat > <<"), ("git log | head\ndate", "Bash: git log | ⏎"),
                              ('git commit -m "Fix x\n\nCo-Authored-By: y"', "Bash: git commit"),
                              ('gh pr create --body "a\nb \\" c\nd" | cat', "Bash: gh pr |"),
                              ("git fetch \\\n  origin main\ngh pr diff 187", "Bash: git fetch ⏎"),
                              ("cat <<EOF > a.md\ngh x\nEOF\ngh pr diff 187", "Bash: cat << > ⏎"),
                              ('git diff "$(git merge-base\\\n origin/main HEAD)"\ndate', "Bash: git diff $( ⏎"),  # #177 with #178
                              ('git commit -m "a\n\\$(b)"', "Bash: git commit")]:
            self.assertEqual(usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": command}}), want, command)
        runs = [end(x, denied=["Bash: git fetch ⏎"]) for x in "abc"]
        self.assertEqual([k for k, *_ in daily.denials(runs, NOW)], ["compound:Bash: git fetch ⏎"], "#178: not a git fetch gap")

    def test_signatures_keep_only_the_tool_and_first_two_words(self):
        self.assertEqual(usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": "gh pr checks 34 --watch"}}), "Bash: gh pr")
        self.assertEqual(usage.denial_signature({"tool_name": "Write", "tool_input": {"file_path": "/tmp/x"}}), "Write")
        token = "gh" + "p_" + "A1b2" * 9
        for command, want in [(f"GH_TOKEN={token} gh pr list", "Bash: gh pr"), (f"export API_KEY={token}", "Bash: export"),
                              (f"curl -H 'Authorization: {token}' x", "Bash: curl"), ("cat /Users/x/secret.txt", "Bash: cat [outside]"),
                              ("echo " + "a1b2c3d4" * 5, "Bash: echo"), ("echo xoxb-" + "1234-5678-abcd", "Bash: echo"),
                              ("cat secret.txt", "Bash: cat"), ("git push", "Bash: git push"), ("gh pr checks 34", "Bash: gh pr")]:
            sig = usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": command}})
            self.assertEqual(sig, want, command)
            self.assertNotIn(token, sig)
            self.assertNotIn("a1b2c3d4", sig)
            self.assertNotIn("xoxb", sig)
        result = {"type": "result", "subtype": "success", "permission_denials": [{"tool_name": "WebFetch", "tool_input": {}}]}
        self.assertEqual(usage.summarize([json.dumps(result)])["denied"], ["WebFetch"])

    def test_an_interpreters_signature_names_what_it_runs(self):
        token = "gh" + "p_" + "A1b2" * 9
        for command, want in [("python3 -I system/tools/post.py comment 141 --body-file -", "Bash: python3 -I system/tools/post.py"),
                              ("python3 -I .github/scripts/tiers.py 3", "Bash: python3 -I .github/scripts/tiers.py"),
                              ("python3 -I -m unittest discover -s system", "Bash: python3 -I -m unittest"),
                              ("python3 -m pytest -q", "Bash: python3 -m pytest"),
                              (f"python3 -c \"print('{token}')\"", "Bash: python3 -c"),
                              (f"GH_TOKEN={token} python3 -I system/tools/post.py read 1", "Bash: python3 -I system/tools/post.py"),
                              ("python3 /Users/x/secret.py", "Bash: python3 [outside]"), ("python3 -I secrets/" + "a" * 60 + ".py", "Bash: python3 -I"),
                              (f"python3 -I system/tools/post.py {token}", "Bash: python3 -I system/tools/post.py"),
                              ("python3", "Bash: python3")]:
            sig = usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": command}})
            self.assertEqual(sig, want, command)
            self.assertNotIn(token, sig)

    def test_a_uv_run_signature_names_what_it_runs(self):
        token = "gh" + "p_" + "A1b2" * 9
        for command, want in [("uv run --frozen pytest tests/test_cli.py -k transcribe", "Bash: uv run --frozen pytest"),
                              ("uv run pytest -q", "Bash: uv run pytest"),
                              (f"uv run --frozen python -c \"print('{token}')\"", "Bash: uv run --frozen python -c"),
                              ("uv run --frozen python -m lmt.eval.datasets fetch ci", "Bash: uv run --frozen python -m lmt.eval.datasets"),
                              ("uv run --frozen python scripts/hash_clip.py tests/fixtures/clip.wav", "Bash: uv run --frozen python scripts/hash_clip.py"),
                              (f"uv run --with {token} pytest", "Bash: uv run"), (f"uv run --frozen {token.upper()}", "Bash: uv run --frozen"),
                              ("uv run --frozen pytest -q 2>&1", "Bash: uv run --frozen pytest >"), ("uv run", "Bash: uv run"),
                              ("uv pip install requests", "Bash: uv pip")]:
            sig = usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": command}})
            self.assertEqual(sig, want, command)
            self.assertNotIn(token.lower(), sig.lower())

    def test_a_first_word_that_isnt_a_command_is_recorded_by_its_shape(self):
        token = "gh" + "p_" + "A1b2" * 9
        for command, want in [("/opt/homebrew/bin/python3 -I system/tools/post.py read 1", "Bash: …/python3 -I system/tools/post.py"),
                              ("~/.local/bin/arsys status", "Bash: …/arsys status"), ("./run.sh --x", "Bash: …/run.sh"),
                              ("\"/Users/x/bin/gh\" pr list", "Bash: …/gh pr"), (f"/tmp/{token}", "Bash: …/?"),
                              (f"/Users/x/{'a' * 21}", "Bash: …/?"), ("(cd /tmp; ls)", "Bash: (… ; [outside]"),
                              (f"\"{token}\"", "Bash: \"…"), (f"${token} x", "Bash: $…"), ("Rscript a.R", "Bash: ?"),
                              (f"{token} x", "Bash: ?")]:
            sig = usage.denial_signature({"tool_name": "Bash", "tool_input": {"command": command}})
            self.assertEqual(sig, want, command)
            self.assertNotIn(token, sig)
            self.assertNotIn("/Users", sig)

    def test_a_recurring_unrecognized_denial_asks_for_its_shape_not_a_grant(self):
        for sig in ("Bash: ?", "Bash: …/?"):
            got = daily.denials([end(x, denied=[sig]) for x in "abc"], NOW)
            self.assertEqual([(k, lvl) for k, lvl, *_ in got], [(f"denial:{sig}", "builder")], "same key as before #176")
            self.assertIn("unrecognized", got[0][2])
            self.assertNotIn("Grant it", got[0][3])
        got = daily.denials([end(x, denied=["Bash: …/python3 -I"]) for x in "abc"], NOW)
        self.assertIn("Grant it by rule", got[0][3], "a named command is a permission gap")


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
            "enforce_admins": {"enabled": False},
            "required_pull_request_reviews": {"required_approving_review_count": 1, "dismiss_stale_reviews": False,
                                              "require_code_owner_reviews": False},
            "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False}}

    def test_the_recorded_settings_pass_and_any_change_is_a_breach_for_the_owner(self):
        self.assertEqual(daily.protection_breaches(self.LIVE, EXPECTED), [])
        loosened = json.loads(json.dumps(self.LIVE))
        loosened["allow_force_pushes"]["enabled"] = True
        got = daily.protection_breaches(loosened, EXPECTED)
        self.assertEqual([x[1] for x in got], ["owner"])
        self.assertIn("allow_force_pushes: is True, recorded False", got[0][3])
        self.assertIn("kill switch", got[0][3])


class DailyPassTest(unittest.TestCase):
    """The responses (blueprint §4.5), with a fake GitHub and state store: nothing is posted or pushed."""

    def run_pass(self, live_protection, raised=(), runs=()):
        from unittest import mock
        from system.dispatcher import dispatch
        store = mock.Mock()
        store.read.side_effect = lambda kind: {"runs": list(runs), "anomalies": [{"key": k} for k in raised]}.get(kind, [])
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
        loosened["allow_force_pushes"]["enabled"] = True
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
