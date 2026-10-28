#!/usr/bin/env python3
"""A PR's tests against its base (the Reviewer's checklist, item 2): they pass at the head and fail
without the change. Standard library only.

Usage: python3 -I system/gate/against_base.py <head> [--base origin/main] [--root DIR]

In a throwaway worktree of <head>, it runs each test file the PR changes, then reverts the PR's
other files to their merge base with --base and runs them again. A run can't do this by hand:
`git checkout` is denied in Reviewer runs (#139), and this leaves the run's own worktree alone.
Exit 0 when every test passes at the head and at least one fails at the base, or when the PR
changes no test; 1 otherwise.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from system.gate.generic import git, is_test  # noqa: E402

# A path from the PR is data: only plain relative .py paths become arguments, never an option.
SAFE_TEST = re.compile(r"[A-Za-z0-9_][\w./-]*\.py")
TIMEOUT_S = 600


def changes(root, base, head):
    """[(status, path)] for the PR's own changes, from the merge base, renames as delete and add."""
    out = git(root, "diff", "--name-status", "--no-renames", base, head)
    return [tuple(line.split("\t", 1)) for line in out.splitlines()]


def test_command(tree, path):
    """System tests run as the gate runs them; others with uv once the product has a pyproject."""
    p = Path(path)
    if p.parts[0] != "system" and (tree / "pyproject.toml").exists():
        return ["uv", "run", "--frozen", "pytest", "-q", "--", path]
    top = "." if p.parts[0] == "system" else str(p.parent)
    return [sys.executable, "-I", "-m", "unittest", "discover", "-s", str(p.parent), "-t", top, "-p", p.name]


def run_tests(tree, tests):
    """{path: (passed, output tail)}."""
    results = {}
    for path in tests:
        try:
            r = subprocess.run(test_command(tree, path), cwd=tree, capture_output=True, text=True, timeout=TIMEOUT_S)
            results[path] = (r.returncode == 0, (r.stdout + r.stderr)[-1500:])
        except subprocess.TimeoutExpired:
            results[path] = (False, f"timed out after {TIMEOUT_S} s")
    return results


def revert_to_base(tree, merge_base, others):
    for status, path in others:
        if status == "A":
            (tree / path).unlink()
        else:
            git(tree, "checkout", merge_base, "--", path)


def check(root, head, base):
    """(ok, report lines)."""
    merge_base = git(root, "merge-base", base, head).strip()
    changed = changes(root, merge_base, head)
    tests = [p for s, p in changed if s != "D" and is_test(p) and p.endswith(".py")]
    others = [(s, p) for s, p in changed if not is_test(p)]
    unsafe = [p for p in tests if not SAFE_TEST.fullmatch(p)]
    if unsafe:
        return False, [f"FAIL: test paths this script won't pass to a command: {', '.join(unsafe)}"]
    if not tests:
        return True, ["SKIP: the PR changes no test file"]
    if not others:
        return True, ["SKIP: the PR changes only tests, so the base is the head"]

    tree = Path(tempfile.mkdtemp(prefix="against-base-")) / "tree"
    try:
        git(root, "worktree", "add", "--quiet", "--detach", str(tree), head)
        at_head = run_tests(tree, tests)
        revert_to_base(tree, merge_base, others)
        at_base = run_tests(tree, tests)
    finally:
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(tree)], capture_output=True)
        shutil.rmtree(tree.parent, ignore_errors=True)

    lines = [f"head {head}, merge base {merge_base[:7]}; {len(others)} other file(s) reverted at the base"]
    for path in tests:
        lines.append(f"{'pass' if at_head[path][0] else 'FAIL'} at head, {'pass' if at_base[path][0] else 'fail'} at base: {path}")
    failed_at_head = [p for p in tests if not at_head[p][0]]
    for path in failed_at_head:
        lines += ["  " + line for line in at_head[path][1].splitlines()]
    if failed_at_head:
        return False, lines + ["FAIL: the tests don't pass at the head"]
    if all(at_base[p][0] for p in tests):
        return False, lines + ["FAIL: the tests pass without the change, so they don't test it"]
    return True, lines + ["PASS: the tests pass at the head and fail without the change"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("head")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    ok, lines = check(args.root, args.head, args.base)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
