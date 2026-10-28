"""Tests for against_base.py: a PR's tests, at its head and with its other changes reverted (#139)."""

import unittest

from system.gate import against_base
from system.gate.test_generic import Repo, setUpModule, tearDownModule  # noqa: F401  (the git config they set)

BUGGY = "def double(x):\n    return x\n"
FIXED = "def double(x):\n    return x * 2\n"
REAL_TEST = "import unittest\nimport calc\n\n\nclass T(unittest.TestCase):\n    def test_double(self):\n        self.assertEqual(calc.double(2), 4)\n"
EMPTY_TEST = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_nothing(self):\n        self.assertTrue(True)\n"


class AgainstBaseTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.repo.commit({"calc.py": BUGGY, "README.md": "hello\n"})
        self.repo.git("checkout", "-q", "-b", "pr")

    def tearDown(self):
        self.repo.close()

    def check(self, files):
        head = self.repo.commit(files)
        return against_base.check(self.repo.root, head, "main")

    def test_a_test_of_the_change_passes_at_the_head_and_fails_at_the_base(self):
        ok, lines = self.check({"calc.py": FIXED, "test_calc.py": REAL_TEST})
        self.assertTrue(ok, lines)
        self.assertIn("pass at head, fail at base: test_calc.py", lines)
        self.assertEqual(self.repo.git("worktree", "list", "--porcelain").count("worktree "), 1, "its worktree is gone")
        self.assertEqual(self.repo.git("status", "--porcelain"), "", "the run's own worktree is left alone")

    def test_a_test_that_passes_without_the_change_fails_the_check(self):
        ok, lines = self.check({"calc.py": FIXED, "test_calc.py": EMPTY_TEST})
        self.assertFalse(ok)
        self.assertEqual(lines[-1], "FAIL: the tests pass without the change, so they don't test it")

    def test_a_test_that_fails_at_the_head_fails_the_check(self):
        ok, lines = self.check({"test_calc.py": REAL_TEST, "README.md": "changed\n"})
        self.assertFalse(ok)
        self.assertEqual(lines[-1], "FAIL: the tests don't pass at the head")

    def test_a_file_the_pr_adds_is_gone_at_the_base(self):
        ok, lines = self.check({"triple.py": "def triple(x):\n    return 3 * x\n",
                                "test_triple.py": REAL_TEST.replace("calc", "triple").replace("double(2), 4", "triple(2), 6")})
        self.assertTrue(ok, lines)

    def test_a_helper_under_tests_is_not_run_as_a_test(self):
        in_tests = "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        ok, lines = self.check({"calc.py": FIXED, "tests/test_calc.py": in_tests + REAL_TEST, "tests/helpers.py": "TWO = 2\n"})
        self.assertTrue(ok, lines)
        self.assertIn("pass at head, fail at base: tests/test_calc.py", lines)
        self.assertFalse([line for line in lines if "helpers.py" in line], lines)

    def test_no_test_changed_is_a_skip(self):
        self.assertEqual(self.check({"calc.py": FIXED}), (True, ["SKIP: the PR changes no test file"]))

    def test_a_test_path_that_looks_like_an_option_is_never_run(self):
        ok, lines = self.check({"calc.py": FIXED, "-x_test.py": REAL_TEST})
        self.assertFalse(ok)
        self.assertIn("-x_test.py", lines[0])


if __name__ == "__main__":
    unittest.main()
