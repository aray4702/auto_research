"""Tests for the gate's generic checks. Each builds a throwaway git repo.

Fake secrets are assembled at run time so this file never matches the scanner itself.
"""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from system.gate import generic

FAKE_GITHUB_TOKEN = "gh" + "p_" + "A1b2" * 9
FAKE_WEBHOOK = "https://discord.com/api/web" + "hooks/123456789/abcDEF_ghi-jkl"
FAKE_KEY_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"

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


class Repo:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, files):
        """Write files ({path: text}, None deletes) and commit; return the commit SHA."""
        for path, text in files.items():
            p = self.root / path
            if text is None:
                p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "change")
        return self.git("rev-parse", "HEAD")

    def close(self):
        self._tmp.cleanup()


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.base = self.repo.commit({"README.md": "hello\n"})

    def tearDown(self):
        self.repo.close()


class SecretsTest(RepoTest):
    def test_clean_tree_passes(self):
        self.assertEqual(generic.check_secrets(self.repo.root, self.base, "HEAD"), [])

    def test_secret_in_tree_is_named_but_never_printed(self):
        self.repo.commit({"notes.md": f"token: {FAKE_GITHUB_TOKEN}\n"})
        problems = generic.check_secrets(self.repo.root, "", "HEAD")
        self.assertEqual(problems, ["notes.md:1: looks like a GitHub token"])
        self.assertNotIn(FAKE_GITHUB_TOKEN, " ".join(problems))

    def test_each_pattern_is_found(self):
        self.repo.commit({"a.txt": FAKE_WEBHOOK + "\n", "b.pem": FAKE_KEY_HEADER + "\n"})
        problems = " ".join(generic.check_secrets(self.repo.root, "", "HEAD"))
        self.assertIn("Discord webhook URL", problems)
        self.assertIn("private key", problems)

    def test_secret_added_and_removed_within_the_change_is_caught(self):
        self.repo.commit({"config.txt": FAKE_WEBHOOK + "\n"})
        self.repo.commit({"config.txt": "moved to the keychain\n"})
        self.assertEqual(generic.check_secrets(self.repo.root, "", "HEAD"), [])
        problems = generic.check_secrets(self.repo.root, self.base, "HEAD")
        self.assertEqual(len(problems), 1)
        self.assertIn("adds what looks like a Discord webhook URL", problems[0])


class PinnedActionsTest(RepoTest):
    def workflow(self, uses):
        self.repo.commit({".github/workflows/ci.yml": f"jobs:\n  a:\n    steps:\n      - uses: {uses}\n"})
        return generic.check_pinned_actions(self.repo.root, "", "HEAD")

    def test_tag_is_rejected(self):
        problems = self.workflow("actions/checkout@v7")
        self.assertEqual(len(problems), 1)
        self.assertIn("not pinned", problems[0])

    def test_full_sha_passes(self):
        self.assertEqual(self.workflow(f"actions/checkout@{SHA} # v7.0.1"), [])

    def test_local_and_digest_references_pass(self):
        self.assertEqual(self.workflow("./.github/workflows/product.yml"), [])
        self.assertEqual(self.workflow("docker://alpine@sha256:" + "0" * 64), [])

    def test_commented_out_use_is_ignored(self):
        self.repo.commit({".github/workflows/x.yml": "# uses: actions/checkout@v7\n"})
        self.assertEqual(generic.check_pinned_actions(self.repo.root, "", "HEAD"), [])


class ConfigParsesTest(RepoTest):
    def test_valid_files_pass(self):
        self.repo.commit({"a.toml": "x = 1\n", "b.json": '{"x": 1}\n'})
        self.assertEqual(generic.check_config_parses(self.repo.root, "", "HEAD"), [])

    def test_invalid_files_fail(self):
        self.repo.commit({"a.toml": "x = \n", "b.json": "{x: 1}\n"})
        problems = generic.check_config_parses(self.repo.root, "", "HEAD")
        self.assertEqual([p.split(":")[0] for p in problems], ["a.toml", "b.json"])


class TestsCarriedTest(RepoTest):
    def test_code_without_test_fails(self):
        self.repo.commit({"src/app.py": "x = 1\n"})
        problems = generic.check_tests_carried(self.repo.root, self.base, "HEAD")
        self.assertEqual(problems, ["changes code without changing a test: src/app.py"])

    def test_code_with_test_passes(self):
        self.repo.commit({"src/app.py": "x = 1\n", "tests/test_app.py": "assert True\n"})
        self.assertEqual(generic.check_tests_carried(self.repo.root, self.base, "HEAD"), [])

    def test_docs_only_change_passes(self):
        self.repo.commit({"guide.md": "words\n"})
        self.assertEqual(generic.check_tests_carried(self.repo.root, self.base, "HEAD"), [])

    def test_without_a_base_it_is_skipped(self):
        self.repo.commit({"src/app.py": "x = 1\n"})
        self.assertEqual(generic.check_tests_carried(self.repo.root, "", "HEAD"), [])

    def test_test_file_names(self):
        for path in ["tests/a.py", "pkg/test_a.py", "a_test.go", "a.test.ts", "a.spec.js", "FooTests.swift"]:
            self.assertTrue(generic.is_test(path), path)
        for path in ["src/a.py", "contest.py", "latest.ts"]:
            self.assertFalse(generic.is_test(path), path)


class MainTest(RepoTest):
    def test_all_zero_base_means_tree_only_and_exit_code_reflects_problems(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(generic.main(["--root", str(self.repo.root), "--base", "0" * 40]), 0)
            self.repo.commit({"notes.md": FAKE_GITHUB_TOKEN + "\n"})
            self.assertEqual(generic.main(["--root", str(self.repo.root)]), 1)


if __name__ == "__main__":
    unittest.main()
