"""Tests for the README checks. Each builds a throwaway git repo holding a README.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_readme  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

GOOD = """# voice-app example

## What it is

This repository builds voice-app example, with the system in [`blueprint.md`](blueprint.md). See [the requirements](project_requirements.md#2-goals).

## Build

```sh
git clone https://github.com/aray4702/auto_research.git
cd auto_research
# A shell comment doesn't end the section.
```

## Checks

The gate is the one required check. Its [site](https://example.com/x) is external.

```sh
python3 -I system/gate/generic.py --base origin/main
```
"""

# Stands in for the gate's generic checks; FAIL_GATE makes it fail.
FAKE_GATE = "import os, sys\nsys.exit(1 if os.path.exists('FAIL_GATE') else 0)\n"

_saved_env = {}


def setUpModule():
    # Keep the developer's own git config (hooks, signing, templates) out of the test repos.
    for key, value in {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}.items():
        _saved_env[key] = os.environ.get(key)
        os.environ[key] = value


def tearDownModule():
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
            "README.md": readme,
            **(files or {}),
        }
        for path, text in files.items():
            p = self.root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change")

    def run_checks(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check_readme.main(["--root", str(self.root)])
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
        self.assertEqual(out.count("PASS"), 5, out)

    def test_missing_readme_fails(self):
        self.commit(GOOD)
        self.git("rm", "-q", "README.md")
        code, out = self.run_checks()
        self.assertEqual(code, 1, out)

    def test_title_must_name_the_product(self):
        self.assertFails(GOOD.replace("# voice-app example", "# voice app"), "S1-1")

    def test_title_is_case_insensitive(self):
        self.commit(GOOD.replace("# voice-app example", "# Voice-App Example"))
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

    def test_broken_relative_link_fails(self):
        readme = GOOD.replace("(blueprint.md)", "(docs/blueprint.md)")
        self.assertIn("docs/blueprint.md", self.assertFails(readme, "S1-5"))

    def test_broken_reference_link_fails(self):
        self.assertFails(GOOD + "\n[spec]: spec/acceptance.md\n", "S1-5")

    def test_links_in_code_and_external_links_are_skipped(self):
        readme = GOOD + "\n```text\n[not a link](missing.md)\n```\n\n[mail](mailto:a@example.invalid)\n"
        self.commit(readme)
        self.assertEqual(self.run_checks()[0], 0)


class RealReadmeTest(unittest.TestCase):
    """This repository's own README. The Checks block runs the gate, so only CI's job runs it."""

    def test_build_block_clones_this_checkout(self):
        text = (REPO_ROOT / "README.md").read_text()
        self.assertEqual(check_readme.check_build(REPO_ROOT, text), [])

    def test_static_checks_pass(self):
        text = (REPO_ROOT / "README.md").read_text()
        for check in (check_readme.check_title, check_readme.check_what_it_is,
                      check_readme.check_links):
            self.assertEqual(check(REPO_ROOT, text), [], check.__name__)
        self.assertIn(check_readme.GATE_COMMAND, text)


if __name__ == "__main__":
    unittest.main()
