"""Tests for the README checks. Each builds a throwaway git repo holding a README.

Run: python3 -I -m unittest discover -s .github/scripts -t .github/scripts
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_readme

REPO_ROOT = Path(__file__).resolve().parents[2]

GOOD = """# Local Meeting Transcriber

## What it is

This repository builds Local Meeting Transcriber, with the system in [`blueprint.md`](blueprint.md). See [the requirements](project_requirements.md#2-goals).

## Install

You need [uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
# A comment isn't a command.

uv tool install git+https://github.com/aray4702/auto_research@v1.0.0
```

Set `HF_TOKEN` for the models.

## Build

Needs uv 0.9.1.

```sh
git clone https://github.com/aray4702/auto_research.git
cd auto_research
# A shell comment doesn't end the section.
uv sync --locked
uv run lmt --help
```

## Checks

The gate is the one required check. Its [site](https://example.com/x) is external. The tests: `uv run --frozen pytest`.
The harness: `uv run --frozen lmt eval --set ci`.

```sh
python3 -I system/gate/generic.py --base origin/main
```
"""

# Stands in for the gate's generic checks; FAIL_GATE makes it fail.
FAKE_GATE = "import os, sys\nsys.exit(1 if os.path.exists('FAIL_GATE') else 0)\n"
# Stands in for uv, so the Build block's install needs no network; FAIL_UV in the clone makes it fail.
FAKE_UV = "#!/bin/sh\n[ ! -e FAIL_UV ]\n"
TOOLCHAIN = '[uv]\nversion = "0.9.1"\n'
PRODUCT_YML = ("jobs:\n  test:\n    steps:\n      - run: uv run --frozen pytest\n"
               "  eval-ci:\n    steps:\n      - run: uv run --frozen lmt eval --set ci\n")

_saved_env = {}
_fake_bin = tempfile.TemporaryDirectory()


def setUpModule():
    uv = Path(_fake_bin.name) / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)
    # Keep the developer's own git config (hooks, signing, templates) out of the test repos.
    for key, value in {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                       "PATH": _fake_bin.name + os.pathsep + os.environ.get("PATH", "")}.items():
        _saved_env[key] = os.environ.get(key)
        os.environ[key] = value


def tearDownModule():
    _fake_bin.cleanup()
    for key, value in _saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class ReadmeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, readme, files=None):
        files = {
            "blueprint.md": "x\n",
            "project_requirements.md": "x\n",
            "system/gate/generic.py": FAKE_GATE,
            "toolchain.toml": TOOLCHAIN,
            ".github/workflows/product.yml": PRODUCT_YML,
            "README.md": readme,
            **(files or {}),
        }
        for path, text in files.items():
            p = self.root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change")

    def run_checks(self, *flags):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check_readme.main(["--root", str(self.root), *flags])
        return code, out.getvalue()

    def assertFails(self, readme, criterion, files=None):
        self.commit(readme, files)
        code, out = self.run_checks()
        self.assertEqual(code, 1, out)
        self.assertIn(f"FAIL {criterion}", out)
        return out

    def test_good_readme_passes(self):
        self.commit(GOOD)
        code, out = self.run_checks()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("PASS"), 6, out)

    def test_missing_readme_fails(self):
        self.commit(GOOD)
        self.git("rm", "-q", "README.md")
        code, out = self.run_checks()
        self.assertEqual(code, 1, out)

    def test_title_must_name_the_product(self):
        self.assertFails(GOOD.replace("# Local Meeting Transcriber", "# voice-app example"), "S1-1")

    def test_title_is_case_insensitive(self):
        self.commit(GOOD.replace("# Local Meeting Transcriber", "# local meeting TRANSCRIBER"))
        self.assertEqual(self.run_checks()[0], 0)

    def test_missing_what_it_is_section_fails(self):
        self.assertFails(GOOD.replace("## What it is", "## About"), "S1-2")

    def test_missing_build_section_fails(self):
        self.assertFails(GOOD.replace("## Build", "## Setup"), "S1-3")

    def test_build_block_must_start_with_the_clone(self):
        readme = GOOD.replace("git clone", "true\ngit clone")
        self.assertFails(readme, "S1-3")

    def test_build_block_runs_as_one_failing_script(self):
        readme = GOOD.replace("# A shell", "false\ntrue\n# A shell")
        self.assertIn("exits 1", self.assertFails(readme, "S1-3"))

    def test_build_clone_must_be_the_commit_under_test(self):
        self.commit(GOOD)
        readme = GOOD.replace("# A shell", "git checkout -q HEAD~1\n# A shell")
        out = self.assertFails(readme, "S1-3")
        self.assertIn("not the commit under test", out)

    def test_build_clones_a_detached_checkout(self):
        # CI checks out a PR's merge commit with a detached HEAD.
        self.commit(GOOD)
        self.commit(GOOD + "\nLater.\n")
        self.git("checkout", "-q", "--detach", "HEAD~1")
        code, out = self.run_checks()
        self.assertEqual(code, 0, out)

    def test_build_block_must_end_with_lmt_help(self):
        out = self.assertFails(GOOD.replace("uv run lmt --help\n", "uv run lmt --help\ntrue\n"), "S1-3")
        self.assertIn("last line", out)

    def test_build_block_runs_uv_in_the_clone(self):
        self.assertIn("exits 1", self.assertFails(GOOD, "S1-3", {"FAIL_UV": ""}))

    def test_readme_must_name_the_uv_version(self):
        out = self.assertFails(GOOD.replace("uv 0.9.1", "uv"), "S1-3")
        self.assertIn("uv version, 0.9.1", out)

    def test_uv_version_must_match_whole(self):
        self.assertFails(GOOD.replace("uv 0.9.1", "uv 0.9.10"), "S1-3")
        self.assertFails(GOOD.replace("uv 0.9.1", "uv 10.9.1"), "S1-3")

    def test_uv_version_at_the_end_of_a_sentence(self):
        self.commit(GOOD.replace("uv 0.9.1.", "uv at version 0.9.1."))
        self.assertEqual(self.run_checks()[0], 0)

    def test_toolchain_without_uv_version_fails(self):
        out = self.assertFails(GOOD, "S1-3", {"toolchain.toml": "[agents]\nallow = []\n"})
        self.assertIn("no [uv] version", out)

    def test_two_build_blocks_fail(self):
        readme = GOOD.replace("## Checks", "```sh\ntrue\n```\n\n## Checks")
        self.assertFails(readme, "S1-3")

    def test_missing_checks_section_fails(self):
        self.assertFails(GOOD.replace("## Checks", "## Tests"), "S1-4")

    def test_checks_command_must_be_the_gate(self):
        readme = GOOD.replace("--base origin/main", "--base main")
        self.assertFails(readme, "S1-4")

    def test_failing_checks_block_fails(self):
        self.assertFails(GOOD, "S1-4", {"FAIL_GATE": ""})

    def test_product_command_must_be_inline_code(self):
        out = self.assertFails(GOOD.replace(" The tests: `uv run --frozen pytest`.", ""), "S1-4")
        self.assertIn("inline code", out)

    def test_product_command_in_a_fenced_block_only_fails(self):
        readme = GOOD.replace(" The tests: `uv run --frozen pytest`.", "").replace(
            "--base origin/main\n```", "--base origin/main\n```\n\n```text\n`uv run --frozen pytest`\n```")
        self.assertFails(readme, "S1-4")

    def test_product_command_must_be_in_product_yml(self):
        out = self.assertFails(GOOD, "S1-4", {".github/workflows/product.yml": "jobs: {}\n"})
        self.assertIn("product.yml doesn't run", out)

    def test_broken_relative_link_fails(self):
        readme = GOOD.replace("(blueprint.md)", "(docs/blueprint.md)")
        self.assertIn("docs/blueprint.md", self.assertFails(readme, "S1-5"))

    def test_broken_reference_link_fails(self):
        self.assertFails(GOOD + "\n[spec]: spec/acceptance.md\n", "S1-5")

    def test_links_in_code_and_external_links_are_skipped(self):
        readme = GOOD + "\n```text\n[not a link](missing.md)\n```\n\n[mail](mailto:a@example.invalid)\n"
        self.commit(readme)
        self.assertEqual(self.run_checks()[0], 0)

    def test_no_install_skips_the_blocks(self):
        # Both blocks would fail; tier 0 runs neither.
        self.commit(GOOD, {"FAIL_UV": "", "FAIL_GATE": ""})
        code, out = self.run_checks("--no-install")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("PASS"), 4, out)
        self.assertIn("SKIP S1-3", out)
        self.assertIn("SKIP S1-4", out)

    def test_missing_install_section_fails(self):
        self.assertIn("appears 0 times", self.assertFails(GOOD.replace("## Install", "## Setup"), "G3-7"))

    def test_two_install_sections_fail(self):
        self.assertFails(GOOD.replace("## Build", "## Install\n\n## Build"), "G3-7")

    def test_install_needs_exactly_one_sh_block(self):
        self.assertIn("2 fenced sh blocks", self.assertFails(
            GOOD.replace("Set `HF_TOKEN`", "```sh\ntrue\n```\n\nSet `HF_TOKEN`"), "G3-7"))
        self.assertIn("0 fenced sh blocks", self.assertFails(GOOD.replace("```sh\n# A comment", "```\n# A comment"),
                                                             "G3-7"))

    def test_install_block_needs_exactly_one_command(self):
        self.assertIn("2 commands", self.assertFails(GOOD.replace("# A comment isn't a command.", "uv --version"),
                                                     "G3-7"))
        self.assertIn("0 commands", self.assertFails(
            GOOD.replace("uv tool install git+https://github.com/aray4702/auto_research@v1.0.0\n", ""), "G3-7"))

    def test_install_command_must_match(self):
        command = "uv tool install git+https://github.com/aray4702/auto_research@v1.0.0"
        for wrong in ("uv tool install git+https://github.com/aray4702/auto_research@main",
                      "uv tool install git+https://github.com/aray4702/auto_research@v1.0",
                      "uv tool install git+https://github.com/someone/auto_research@v1.0.0",
                      "uv tool install git+https://github.com/aray4702/auto_research@v1.0.0 --force",
                      "pip install git+https://github.com/aray4702/auto_research@v1.0.0"):
            with self.subTest(wrong=wrong):
                self.assertIn("doesn't match", self.assertFails(GOOD.replace(command, wrong), "G3-7"))

    def test_install_must_link_the_uv_installer(self):
        out = self.assertFails(GOOD.replace("getting-started/installation/", ""), "G3-7")
        self.assertIn("doesn't link", out)
        # The URL as plain text, or a link in another section, isn't the section's link.
        self.assertFails(GOOD.replace("[uv](https://docs.astral.sh/uv/getting-started/installation/)",
                                      "uv (https://docs.astral.sh/uv/getting-started/installation/)"), "G3-7")
        self.assertFails(GOOD.replace("[uv](https://docs.astral.sh/uv/getting-started/installation/)", "uv").replace(
            "Needs uv 0.9.1.", "Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.9.1."), "G3-7")

    def test_install_must_name_hf_token(self):
        self.assertIn("HF_TOKEN", self.assertFails(GOOD.replace("Set `HF_TOKEN`", "Set a token"), "G3-7"))

    def test_install_block_isnt_run(self):
        # The Install block isn't part of S1-3: a failing uv fails only the Build block.
        self.commit(GOOD, {"FAIL_UV": ""})
        code, out = self.run_checks()
        self.assertEqual(code, 1, out)
        self.assertIn("PASS G3-7", out)
        self.assertIn("FAIL S1-3", out)

    def test_no_install_still_checks_the_install_section(self):
        self.commit(GOOD.replace("Set `HF_TOKEN`", "Set a token"))
        code, out = self.run_checks("--no-install")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL G3-7", out)

    def print_install(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check_readme.main(["--root", str(self.root), "--print-install"])
        return code, out.getvalue(), err.getvalue()

    def test_print_install_prints_the_command(self):
        # Only the command: the checks don't run, so a failing Build block doesn't matter.
        self.commit(GOOD, {"FAIL_UV": "", "FAIL_GATE": ""})
        self.assertEqual(self.print_install(),
                         (0, "uv tool install git+https://github.com/aray4702/auto_research@v1.0.0\n", ""))

    def test_print_install_fails_on_a_bad_section(self):
        self.commit(GOOD.replace("@v1.0.0", "@main"))
        code, out, err = self.print_install()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("doesn't match", err)

    def test_print_install_fails_without_a_readme(self):
        self.commit(GOOD)
        self.git("rm", "-q", "README.md")
        code, out, err = self.print_install()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("README.md is missing", err)

    def run_record(self, *flags):
        path = self.root / "out" / "criteria-readme.json"
        path.parent.mkdir(exist_ok=True)
        code, _ = self.run_checks("--criteria-out", str(path), *flags)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [path.name])  # no temporary left
        return code, json.loads(path.read_text())

    def test_record_lists_every_check(self):
        self.commit(GOOD.replace("(blueprint.md)", "(docs/blueprint.md)"))
        with mock.patch.dict(os.environ, {"GITHUB_SHA": "abc", "GITHUB_REF": "refs/heads/main",
                                          "GITHUB_EVENT_NAME": "push", "GITHUB_RUN_ID": "7",
                                          "GITHUB_RUN_ATTEMPT": "2", "LMT_TIER": "1"}):
            code, record = self.run_record()
        self.assertEqual(code, 1)
        self.assertEqual({k: v for k, v in record.items() if k != "checks"}, {
            "format": 1, "job": "readme", "commit": "abc", "ref": "refs/heads/main", "event": "push",
            "run_id": 7, "run_attempt": 2, "tier": 1, "complete": True})
        self.assertEqual(record["checks"], [
            {"check": "check_readme.py::S1-1 title", "criteria": ["S1-1"], "outcome": "passed"},
            {"check": "check_readme.py::S1-2 what it is", "criteria": ["S1-2"], "outcome": "passed"},
            {"check": "check_readme.py::S1-3 build", "criteria": ["S1-3"], "outcome": "passed"},
            {"check": "check_readme.py::S1-4 checks", "criteria": ["S1-4"], "outcome": "passed"},
            {"check": "check_readme.py::S1-5 relative links", "criteria": ["S1-5"], "outcome": "failed"},
            {"check": "check_readme.py::G3-7 install", "criteria": ["G3-7"], "outcome": "passed"},
        ])

    def test_record_with_no_install(self):
        self.commit(GOOD, {"FAIL_UV": "", "FAIL_GATE": ""})
        env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k != "LMT_TIER"}
        with mock.patch.dict(os.environ, env, clear=True):
            code, record = self.run_record("--no-install")
        self.assertEqual(code, 0)
        self.assertEqual((record["job"], record["complete"], record["commit"], record["run_id"], record["tier"]),
                         ("readme", True, None, None, None))
        self.assertEqual([(c["criteria"], c["outcome"], c.get("reason")) for c in record["checks"]], [
            (["S1-1"], "passed", None), (["S1-2"], "passed", None),
            (["S1-3"], "not-run", "tier 0 runs no install"), (["S1-4"], "not-run", "tier 0 runs no install"),
            (["S1-5"], "passed", None), (["G3-7"], "passed", None)])

    def test_record_without_a_readme_fails_every_check(self):
        self.commit(GOOD)
        self.git("rm", "-q", "README.md")
        code, record = self.run_record("--no-install")
        self.assertEqual(code, 1)
        self.assertEqual([c["outcome"] for c in record["checks"]],
                         ["failed", "failed", "not-run", "not-run", "failed", "failed"])

    def test_no_record_without_the_option(self):
        self.commit(GOOD)
        self.run_checks("--no-install")
        self.assertEqual(list(self.root.glob("criteria-*")), [])

    def test_no_install_still_runs_the_static_checks(self):
        self.commit(GOOD.replace("# Local Meeting Transcriber", "# voice-app example"))
        self.assertEqual(self.run_checks("--no-install")[0], 1)
        self.commit(GOOD.replace("## What it is", "## About"))
        self.assertEqual(self.run_checks("--no-install")[0], 1)
        self.commit(GOOD.replace("(blueprint.md)", "(docs/blueprint.md)"))
        self.assertEqual(self.run_checks("--no-install")[0], 1)


class RealReadmeTest(unittest.TestCase):
    """This repository's own README. The Checks block runs the gate, so only CI's job runs it."""

    def test_build_block_clones_this_checkout(self):
        text = (REPO_ROOT / "README.md").read_text()
        self.assertEqual(check_readme.check_build(REPO_ROOT, text), [])

    def test_static_checks_pass(self):
        text = (REPO_ROOT / "README.md").read_text()
        for check in (check_readme.check_title, check_readme.check_what_it_is,
                      check_readme.check_links, check_readme.check_install):
            self.assertEqual(check(REPO_ROOT, text), [], check.__name__)
        self.assertIn(check_readme.GATE_COMMAND, text)
        workflow = (REPO_ROOT / check_readme.PRODUCT_YML).read_text()
        for command in check_readme.PRODUCT_COMMANDS:
            self.assertIn(f"`{command}`", check_readme.FENCE.sub("", text))
            self.assertIn(command, workflow)


if __name__ == "__main__":
    unittest.main()
