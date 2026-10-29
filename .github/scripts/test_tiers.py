"""Tests for the tier filter (design/internal/g1-stage1.md §2).

Run: python3 -I -m unittest discover -s .github/scripts -t .github/scripts
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tiers


class TierForPathTest(unittest.TestCase):
    def assertTier(self, path, tier):
        self.assertEqual(tiers.tier_for_path(path), tier, path)

    def test_one_path_per_tier(self):
        self.assertTier("docs.md", 0)
        self.assertTier("README.md", 1)
        self.assertTier("tests/test_cli.py", 2)
        self.assertTier("src/lmt/cli.py", 3)

    def test_eval_is_tier_3_as_part_of_src_lmt(self):
        self.assertTier("src/lmt/eval/score.py", 3)

    def test_results_and_schema(self):
        self.assertTier("results/full.json", 1)
        self.assertTier("schema/transcript.schema.json", 2)

    def test_exporters_left_out_of_tier_3(self):
        for name in ("txt", "srt", "vtt"):
            self.assertTier(f"src/lmt/export/{name}.py", 2)
        self.assertTier("src/lmt/export/json.py", 3)

    def test_g3_files_left_out_of_tier_3(self):
        self.assertTier("src/lmt/rename.py", 2)
        self.assertTier("src/lmt/demo/page.html", 2)

    def test_workflows_are_tier_3(self):
        self.assertTier(".github/workflows/gate.yml", 3)
        self.assertTier(".github/workflows/product.yml", 3)

    def test_scripts_are_tier_1_but_the_filter_is_tier_3(self):
        self.assertTier(".github/scripts/x.py", 1)
        self.assertTier(".github/scripts/check_readme.py", 1)
        self.assertTier(".github/scripts/tiers.py", 3)

    def test_toolchain_files_are_tier_3(self):
        for path in ("uv.lock", "pyproject.toml", "toolchain.toml", ".python-version"):
            self.assertTier(path, 3)

    def test_exact_entries_match_only_exactly(self):
        self.assertTier("docs/README.md", 0)
        self.assertTier("uv.lock.bak", 0)
        self.assertTier("src/lmt/rename.py.orig", 3)
        self.assertTier("srcx/lmt/cli.py", 0)

    def test_paths(self):
        self.assertEqual(tiers.tier_for_paths([]), 0)
        self.assertEqual(tiers.tier_for_paths(["docs.md", "README.md", "tests/test_cli.py"]), 2)


class DecideTest(unittest.TestCase):
    """The change from git, in a throwaway repo."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        self.base = self.commit({"docs.md": "x\n", "src/lmt/cli.py": "x\n"})

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, files):
        for path, text in files.items():
            p = self.root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change")
        return self.git("rev-parse", "HEAD")

    def decide(self, event="pull_request", base=None, head="HEAD"):
        return tiers.decide(event, self.base if base is None else base, head, str(self.root))

    def test_the_change_sets_the_tier(self):
        self.commit({"docs.md": "y\n", "tests/test_cli.py": "x\n"})
        tier, paths, _ = self.decide()
        self.assertEqual((tier, paths), (2, ["tests/test_cli.py"]))

    def test_a_change_to_docs_only_is_tier_0(self):
        self.commit({"docs.md": "y\n"})
        self.assertEqual(self.decide()[0], 0)

    def test_compares_from_the_merge_base(self):
        # main moves on with a tier-3 change; the branch's own change is tier 0.
        self.commit({"src/lmt/cli.py": "y\n"})
        main = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "topic", self.base)
        self.commit({"docs.md": "y\n"})
        self.assertEqual(self.decide(base=main)[0], 0)

    def test_a_rename_counts_its_old_path(self):
        self.git("mv", "src/lmt/cli.py", "notes.md")
        self.git("commit", "-q", "-m", "move")
        tier, paths, _ = self.decide()
        self.assertEqual((tier, paths), (3, ["src/lmt/cli.py"]))

    def test_an_all_zeros_base_runs_every_tier(self):
        self.commit({"docs.md": "y\n"})
        self.assertEqual(self.decide(base="0" * 40)[0], 3)

    def test_an_empty_base_runs_every_tier(self):
        self.commit({"docs.md": "y\n"})
        self.assertEqual(self.decide(base="")[0], 3)

    def test_a_base_git_cant_resolve_runs_every_tier(self):
        self.commit({"docs.md": "y\n"})
        tier, _, reason = self.decide(base="1" * 40)
        self.assertEqual(tier, 3)
        self.assertIn("git can't compute", reason)

    def test_dispatch_and_schedule_run_every_tier(self):
        self.commit({"docs.md": "y\n"})
        for event in ("workflow_dispatch", "schedule"):
            self.assertEqual(self.decide(event=event)[0], 3, event)


class MainTest(unittest.TestCase):
    def test_writes_the_tier_and_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, summary = Path(tmp) / "output", Path(tmp) / "summary"
            env = {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()) as out:
                code = tiers.main(["--event", "workflow_dispatch", "--base", "", "--head", "HEAD"])
            self.assertEqual(code, 0)
            self.assertEqual(output.read_text(), "tier=3\n")
            self.assertIn("workflow_dispatch", summary.read_text())
            self.assertIn("tier 3", out.getvalue())


if __name__ == "__main__":
    unittest.main()
