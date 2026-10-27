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

    def test_automation_stays_off_until_step_7(self):
        self.assertFalse(any(s["enabled"] for s in CONFIG["runner"]["schedules"].values()))


class SettingsTest(unittest.TestCase):
    def test_roles_get_the_common_rules_plus_their_own(self):
        settings = runner.role_settings(CONFIG, "coordinator")
        self.assertIn("Bash(python3 -I system/tools/post.py:*)", settings["permissions"]["allow"])
        self.assertNotIn("Edit", settings["permissions"]["allow"])
        self.assertIn("Bash(git push --force:*)", settings["permissions"]["deny"])
        self.assertIn("Bash(security:*)", settings["permissions"]["deny"])
        self.assertFalse(settings["autoMemoryEnabled"])
        self.assertIn("Edit", runner.role_settings(CONFIG, "developer")["permissions"]["allow"])

    def test_product_tools_reach_only_the_roles_that_take_them(self):
        config = with_toolchain(["Bash(make test:*)"])
        self.assertIn("Bash(make test:*)", runner.role_settings(config, "developer")["permissions"]["allow"])
        self.assertIn("Bash(make test:*)", runner.role_settings(config, "reviewer")["permissions"]["allow"])
        self.assertNotIn("Bash(make test:*)", runner.role_settings(config, "coordinator")["permissions"]["allow"])

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


class LaunchdTest(unittest.TestCase):
    def test_jobs_render_deterministically_and_disabled(self):
        first, second = runner.plists(CONFIG), runner.plists(CONFIG)
        self.assertEqual(first, second)
        jobs = {label: plistlib.loads(data) for label, data in first.items()}
        self.assertEqual(sorted(label.rsplit(".", 1)[1] for label in jobs), [
            "builder-daily", "dispatcher-daily", "dispatcher-poll", "dispatcher-reconcile",
        ])
        self.assertTrue(all(job["Disabled"] for job in jobs.values()))
        poll = jobs["com.aray4702.auto-research.dispatcher-poll"]
        self.assertEqual(poll["StartInterval"], 120)
        self.assertEqual(poll["ProgramArguments"][:2], [CONFIG["runner"]["runner"]["python"], "-I"])
        self.assertEqual(jobs["com.aray4702.auto-research.builder-daily"]["StartCalendarInterval"], {"Hour": 8, "Minute": 0})

    def test_check_finds_missing_changed_stray_and_unapplied(self):
        with tempfile.TemporaryDirectory() as d:
            agents = Path(d)
            not_loaded = lambda job_label: False
            found = runner.problems(CONFIG, agents, not_loaded, "abc", "abc")
            self.assertEqual(len(found), 4)
            self.assertTrue(all("is not installed" in p for p in found))
            for job_label, data in runner.plists(CONFIG).items():
                (agents / f"{job_label}.plist").write_bytes(data)
            self.assertEqual(runner.problems(CONFIG, agents, not_loaded, "abc", "abc"), [])
            (agents / "com.aray4702.auto-research.dispatcher-poll.plist").write_bytes(b"changed")
            (agents / "com.aray4702.auto-research.old-job.plist").write_bytes(b"stray")
            found = runner.problems(CONFIG, agents, not_loaded, "abc", "def")
            self.assertEqual(found, [
                "com.aray4702.auto-research.dispatcher-poll.plist differs from runner.toml",
                "com.aray4702.auto-research.old-job.plist is installed but not in runner.toml",
                "the applied checkout is at abc, main is at def",
            ])
            loaded_anyway = lambda job_label: True
            self.assertTrue(any("is loaded" in p for p in runner.problems(CONFIG, agents, loaded_anyway, "abc", "def")))


if __name__ == "__main__":
    unittest.main()
