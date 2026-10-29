"""Tests for the runner settings tool. Nothing here touches launchd or the network."""

import copy
import plistlib
import tempfile
import unittest
from pathlib import Path

from system.tools import runner

CONFIG = runner.load_all()


def with_toolchain(allow):
    config = copy.deepcopy(CONFIG)
    config["toolchain"] = {"agents": {"allow": allow}}
    return config


class ConsistencyTest(unittest.TestCase):
    def test_every_llm_role_has_caps_and_an_identity_and_a_role_file(self):
        for role in CONFIG["runner"]["roles"]:
            self.assertIn(role, CONFIG["caps"]["roles"], role)
            self.assertIn(role, CONFIG["identity"]["roles"], role)
            self.assertTrue((runner.ROOT / "system" / "roles" / f"{role}.md").exists(), role)

    def test_models_and_efforts_match_the_sizing(self):
        efforts = {role: r["effort"] for role, r in CONFIG["runner"]["roles"].items()}
        self.assertEqual(efforts, {
            "architect": "high", "coordinator": "medium", "developer": "high",
            "reviewer": "high", "tester": "medium", "builder": "high",
        })
        self.assertEqual({r["model"] for r in CONFIG["runner"]["roles"].values()}, {"claude-opus-5-5"})

    def test_automation_is_on_since_step_7(self):
        self.assertTrue(all(s["enabled"] for s in CONFIG["runner"]["schedules"].values()))


class SettingsTest(unittest.TestCase):
    def test_roles_get_the_common_rules_plus_their_own(self):
        settings = runner.role_settings(CONFIG, "coordinator")
        self.assertIn("Bash(python3 -I system/tools/post.py:*)", settings["permissions"]["allow"])
        self.assertNotIn("Edit", settings["permissions"]["allow"])
        self.assertIn("Bash(git push --force:*)", settings["permissions"]["deny"])
        self.assertIn("Bash(security:*)", settings["permissions"]["deny"])
        self.assertFalse(settings["autoMemoryEnabled"])
        self.assertIn("Edit", runner.role_settings(CONFIG, "developer")["permissions"]["allow"])
        for role in ("architect", "developer", "builder"):
            self.assertIn("Bash(mkdir -p:*)", runner.role_settings(CONFIG, role)["permissions"]["allow"], role)
        self.assertNotIn("Bash(mkdir -p:*)", settings["permissions"]["allow"])

    def test_every_role_may_compare_a_prs_revisions(self):
        # Reviewers and the Coordinator (SR2) ask whether a PR's own commits changed or only main (#181).
        for role in CONFIG["runner"]["roles"]:
            self.assertIn("Bash(git range-diff:*)", runner.role_settings(CONFIG, role)["permissions"]["allow"], role)

    def test_product_tools_reach_only_the_roles_that_take_them(self):
        config = with_toolchain(["Bash(make test:*)"])
        self.assertIn("Bash(make test:*)", runner.role_settings(config, "developer")["permissions"]["allow"])
        self.assertIn("Bash(make test:*)", runner.role_settings(config, "reviewer")["permissions"]["allow"])
        self.assertIn("Bash(make test:*)", runner.role_settings(config, "architect")["permissions"]["allow"], "it designs the toolchain (#90)")
        self.assertNotIn("Bash(make test:*)", runner.role_settings(config, "coordinator")["permissions"]["allow"])

    def test_uv_runs_only_the_products_own_tools_and_writes_only_where_the_role_may_write(self):
        product_roles = [r for r, s in CONFIG["runner"]["roles"].items() if s["product_tools"]]
        self.assertEqual(sorted(product_roles), ["architect", "developer", "reviewer", "tester"])
        for role in product_roles:
            allow = runner.role_settings(CONFIG, role)["permissions"]["allow"]
            self.assertIn("Bash(uv run --frozen pytest)", allow, f"{role} (#90)")
            for rule in allow:  # a bare `uv run` (or ffmpeg) would get around the deny list (#92)
                self.assertFalse(rule.startswith("Bash(uv run") and not rule.startswith(("Bash(uv run --frozen pytest",
                                 "Bash(uv run --frozen ruff check", "Bash(uv run --frozen ruff format", "Bash(uv run --frozen lmt")), f"{role}: {rule}")
                self.assertFalse(rule.startswith(("Bash(ffmpeg", "Bash(ffprobe")), f"{role}: {rule}")
            writes = {"Bash(uv lock:*)", "Bash(uv sync:*)", "Bash(uv add:*)", "Bash(uv remove:*)", "Bash(uv pip compile:*)",
                      "Bash(uv run --frozen pytest:*)", "Bash(uv run --frozen ruff check:*)"}
            writer = "Write" in CONFIG["runner"]["roles"][role]["tools"]
            self.assertEqual(writes <= set(allow), writer, f"{role}: write rules only for roles with the Write tool (#92)")
            if not writer:  # `ruff check --fix` rewrites code and `pytest --basetemp` deletes a directory (#92)
                open_ended = [r for r in allow if r.startswith(("Bash(uv run --frozen pytest", "Bash(uv run --frozen ruff")) and r.endswith(":*)")]
                self.assertEqual(open_ended, [], role)

    def test_every_role_may_list_a_commits_files(self):
        # A :* rule matches whole words, so git ls-files doesn't cover git ls-tree (#179).
        for role in CONFIG["runner"]["roles"]:
            self.assertIn("Bash(git ls-tree:*)", runner.role_settings(CONFIG, role)["permissions"]["allow"], role)

    def test_every_word_prefix_rule_can_match_a_command(self):
        # A :* rule matches its prefix as whole words, so a prefix ending in "/" matches no script (#41).
        for role in CONFIG["runner"]["roles"]:
            for rule in runner.role_settings(CONFIG, role)["permissions"]["allow"]:
                self.assertFalse(rule.endswith("/:*)"), f"{role}: {rule}")

    def test_the_builder_may_run_its_daily_checks_but_not_a_live_dispatch(self):
        allow = runner.role_settings(CONFIG, "builder")["permissions"]["allow"]
        prefixes = [a[len("Bash("):-len(":*)")] for a in allow if a.startswith("Bash(") and a.endswith(":*)")]
        allowed = lambda cmd: any(cmd == p or cmd.startswith(p + " ") for p in prefixes)
        for cmd in ("python3 -I system/tools/runner.py check", "python3 -I system/tools/runner.py apply",
                    "python3 -I system/tools/runner.py settings builder", "python3 -I system/tools/tracker.py check",
                    "python3 -I system/tools/calibrate.py --record",
                    "python3 -I system/dispatcher/dispatch.py poll --dry-run",
                    "python3 -I system/dispatcher/dispatch.py reconcile --dry-run"):
            self.assertTrue(allowed(cmd), cmd)
        for cmd in ("python3 -I system/dispatcher/dispatch.py poll", "python3 -I system/dispatcher/run.py builder"):
            self.assertFalse(allowed(cmd), cmd)

    def test_no_role_may_run_raw_gh_writes(self):
        for role in CONFIG["runner"]["roles"]:
            allow = runner.role_settings(CONFIG, role)["permissions"]["allow"]
            writes = [a for a in allow if a.startswith("Bash(gh ") and not any(
                a.startswith(f"Bash(gh {verb}") for verb in ("pr view", "pr list", "pr checks", "run list", "run view", "issue list", "issue view")
            )]
            self.assertEqual(writes, [], role)


class CommandTest(unittest.TestCase):
    def test_a_run_is_restricted_capped_and_pointed_at_its_role_file(self):
        cmd = runner.claude_command(CONFIG, "developer", "Your item: #12.", Path("/tmp/s.json"))
        self.assertEqual(cmd[:2], [CONFIG["runner"]["runner"]["claude"], "-p"])
        prompt = cmd[2]
        self.assertIn("system/rules.md", prompt)
        self.assertIn("system/roles/developer.md", prompt)
        self.assertTrue(prompt.endswith("Your item: #12."))
        for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence"):
            self.assertIn(flag, cmd)
        pairs = dict(zip(cmd[3::1], cmd[4::1]))
        self.assertEqual(pairs["--permission-mode"], "dontAsk")
        self.assertEqual(pairs["--permission-prompts"], "none")
        self.assertEqual(pairs["--model"], "claude-opus-5-5")
        self.assertEqual(pairs["--max-turns"], str(CONFIG["caps"]["roles"]["developer"]["max_turns"]))
        self.assertEqual(pairs["--max-budget-usd"], str(CONFIG["caps"]["roles"]["developer"]["max_budget_usd"]))
        self.assertEqual(pairs["--settings"], "/tmp/s.json")


class LoginTest(unittest.TestCase):
    def test_only_the_subscription_login_may_run(self):
        self.assertIsNone(runner.login_problem(CONFIG, ("claude.ai", "max")))
        for login in [("oauth_token", None), ("apiKey", None), ("claude.ai", "pro"), (None, None)]:
            self.assertIn("claude auth login", runner.login_problem(CONFIG, login), login)

    def test_check_reports_a_wrong_login(self):
        with tempfile.TemporaryDirectory() as d:
            for job_label, data in runner.plists(CONFIG).items():
                (Path(d) / f"{job_label}.plist").write_bytes(data)
            ok = runner.problems(CONFIG, Path(d), lambda job_label: True, "abc", "abc", login=("claude.ai", "max"))
            self.assertEqual(ok, [])
            bad = runner.problems(CONFIG, Path(d), lambda job_label: True, "abc", "abc", login=("oauth_token", None))
            self.assertEqual(len(bad), 1)
            self.assertIn("not the max subscription", bad[0])


class CliLinkTest(unittest.TestCase):
    def test_check_reports_a_missing_or_wrong_cli_link(self):
        with tempfile.TemporaryDirectory() as d:
            agents, link, target = Path(d, "agents"), Path(d, "bin", "arsys"), Path(d, "system", "arsys.py")
            agents.mkdir()
            for job_label, data in runner.plists(CONFIG).items():
                (agents / f"{job_label}.plist").write_bytes(data)
            on = lambda job_label: True
            self.assertEqual(runner.problems(CONFIG, agents, on, "a", "a", cli=(link, target)), [f"{link} doesn't link to {target}"])
            link.parent.mkdir()
            link.symlink_to(target)
            self.assertEqual(runner.problems(CONFIG, agents, on, "a", "a", cli=(link, target)), [])


class LaunchdTest(unittest.TestCase):
    def test_jobs_render_deterministically_and_enabled(self):
        first, second = runner.plists(CONFIG), runner.plists(CONFIG)
        self.assertEqual(first, second)
        jobs = {label: plistlib.loads(data) for label, data in first.items()}
        self.assertEqual(sorted(label.rsplit(".", 1)[1] for label in jobs), [
            "builder-daily", "dispatcher-daily", "dispatcher-poll", "dispatcher-reconcile",
        ])
        self.assertFalse(any(job["Disabled"] for job in jobs.values()))
        self.assertTrue(all(job["AbandonProcessGroup"] for job in jobs.values()))
        poll = jobs["com.aray4702.auto-research.dispatcher-poll"]
        self.assertEqual(poll["StartInterval"], 120)
        self.assertEqual(poll["ProgramArguments"][:2], [CONFIG["runner"]["runner"]["python"], "-I"])
        self.assertEqual(jobs["com.aray4702.auto-research.builder-daily"]["StartCalendarInterval"], {"Hour": 8, "Minute": 0})
        audits = jobs["com.aray4702.auto-research.dispatcher-daily"]
        self.assertEqual(audits["StartCalendarInterval"], {"Hour": 7, "Minute": 30}, "once a day (#159)")
        self.assertEqual(audits["ProgramArguments"][-1], "audit")

    def test_check_finds_missing_changed_stray_and_unapplied(self):
        with tempfile.TemporaryDirectory() as d:
            agents = Path(d)
            loaded = lambda job_label: True
            found = runner.problems(CONFIG, agents, loaded, "abc", "abc")
            self.assertEqual(len(found), 4)
            self.assertTrue(all("is not installed" in p for p in found))
            for job_label, data in runner.plists(CONFIG).items():
                (agents / f"{job_label}.plist").write_bytes(data)
            self.assertEqual(runner.problems(CONFIG, agents, loaded, "abc", "abc"), [])
            (agents / "com.aray4702.auto-research.dispatcher-poll.plist").write_bytes(b"changed")
            (agents / "com.aray4702.auto-research.old-job.plist").write_bytes(b"stray")
            found = runner.problems(CONFIG, agents, loaded, "abc", "def")
            self.assertEqual(found, [
                "com.aray4702.auto-research.dispatcher-poll.plist differs from runner.toml",
                "com.aray4702.auto-research.old-job.plist is installed but not in runner.toml",
                "the applied checkout is at abc, main is at def",
            ])
            not_loaded = lambda job_label: False
            found = runner.problems(CONFIG, agents, not_loaded, "abc", "def")
            self.assertTrue(any("is not loaded, but runner.toml has it enabled" in p for p in found), found)

    def test_apply_installs_from_the_updated_checkout(self):
        # The first apply after a runner.toml change installed the jobs from the file loaded before the update (#65).
        calls, real = [], (runner.update_checkout, runner.os.execv)
        runner.update_checkout = lambda config: calls.append("update") or []
        runner.os.execv = lambda path, argv: calls.append(argv) or (_ for _ in ()).throw(SystemExit(0))
        try:
            with self.assertRaises(SystemExit):
                runner.main(["apply"])
        finally:
            runner.update_checkout, runner.os.execv = real
        script = str(runner.home(CONFIG) / "system" / "system" / "tools" / "runner.py")
        self.assertEqual(calls, ["update", [CONFIG["runner"]["runner"]["python"], "-I", script, "install"]])

    def test_install_runs_only_from_the_applied_checkout(self):
        real = (runner.ROOT, runner.install)
        runner.ROOT = Path(tempfile.gettempdir(), "not-the-applied-checkout")
        runner.install = lambda config: self.fail("installed from outside the applied checkout")
        try:
            with self.assertRaises(SystemExit):
                runner.main(["install"])
        finally:
            runner.ROOT, runner.install = real

    def test_the_checkout_update_holds_the_shared_lock(self):
        held, real = [], (runner.locked, runner.git)
        @runner.contextlib.contextmanager
        def recording(checkout):
            held.append(True)
            yield
            held.pop()
        runner.locked = recording
        runner.git = lambda *args, cwd=None: "abc" if args[0] == "rev-parse" else self.assertTrue(held, args)
        try:
            with tempfile.TemporaryDirectory() as d:
                config = copy.deepcopy(CONFIG)
                config["runner"]["runner"]["home"] = d
                Path(d, "system", ".git").mkdir(parents=True)
                self.assertEqual(runner.update_checkout(config), ["applied checkout at abc"])
        finally:
            runner.locked, runner.git = real

    def test_check_finds_a_disabled_job_still_loaded(self):
        config = copy.deepcopy(CONFIG)
        config["runner"]["schedules"]["builder-daily"]["enabled"] = False
        with tempfile.TemporaryDirectory() as d:
            agents = Path(d)
            for job_label, data in runner.plists(config).items():
                (agents / f"{job_label}.plist").write_bytes(data)
            loaded = lambda job_label: True
            self.assertEqual(runner.problems(config, agents, loaded, "abc", "abc"), [
                "com.aray4702.auto-research.builder-daily is loaded, but runner.toml has it disabled",
            ])


if __name__ == "__main__":
    unittest.main()
