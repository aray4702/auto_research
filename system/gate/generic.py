#!/usr/bin/env python3
"""The gate's generic checks (blueprint §1). Product-agnostic, standard library only.

Usage: python3 -I system/gate/generic.py [--root DIR] [--base SHA] [--head SHA]

Checks that look at a change (secrets in added lines, tests carried) cover base..head.
Without a base, or with an all-zero one (a new branch), they cover the tree at head only.
Problems name what was found and where, never a secret's value.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

SECRET_PATTERNS = {
    "Discord webhook URL": re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    "private key": re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    "AWS access key ID": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "Anthropic API key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "Slack token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "Slack webhook URL": re.compile(r"hooks\.slack\.com/services/T[\w-]+/B[\w-]+/[\w-]+"),
}

CODE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".cjs", ".go", ".h", ".hpp", ".java", ".js", ".jsx", ".kt",
    ".m", ".mjs", ".mm", ".php", ".py", ".rb", ".rs", ".scala", ".sh", ".swift", ".ts", ".tsx",
}
TEST_DIR = re.compile(r"(^|/)(tests?|__tests__|spec)/")
TEST_NAME = re.compile(r"^test_|_test\.|\.test\.|\.spec\.|Tests?\.\w+$")

USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^'\"\s#]+)")
PINNED_USES = re.compile(
    r"^(\./\S+|docker://\S+@sha256:[0-9a-f]{64}|[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40})$"
)


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def tracked_files(root, head):
    return git(root, "ls-tree", "-r", "--name-only", head).splitlines()


def text_at(root, head, path):
    """The file's text at head, or None for a binary file."""
    data = subprocess.run(
        ["git", "-C", str(root), "show", f"{head}:{path}"], check=True, capture_output=True
    ).stdout
    return None if b"\0" in data else data.decode("utf-8", "replace")


def secret_names(line):
    return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(line)]


def is_test(path):
    return bool(TEST_DIR.search(path) or TEST_NAME.search(Path(path).name))


def is_code(path):
    return Path(path).suffix in CODE_SUFFIXES


def check_secrets(root, base, head):
    problems = set()
    for path in tracked_files(root, head):
        text = text_at(root, head, path)
        for n, line in enumerate((text or "").splitlines(), 1):
            for name in secret_names(line):
                problems.add(f"{path}:{n}: looks like a {name}")
    if base:
        # Added lines in every commit of the change, so a secret added and removed again is caught.
        commit = None
        for line in git(root, "log", "-p", "--no-color", "--format=commit %H", f"{base}..{head}").splitlines():
            if line.startswith("commit "):
                commit = line[7:14]
            elif line.startswith("+") and not line.startswith("+++"):
                for name in secret_names(line):
                    problems.add(f"commit {commit} adds what looks like a {name}")
    return sorted(problems)


def check_pinned_actions(root, base, head):
    problems = []
    for path in tracked_files(root, head):
        if not (path.startswith(".github/") and path.endswith((".yml", ".yaml"))):
            continue
        for n, line in enumerate((text_at(root, head, path) or "").splitlines(), 1):
            m = USES_LINE.match(line)
            if m and not PINNED_USES.match(m.group(1)):
                problems.append(f"{path}:{n}: '{m.group(1)}' is not pinned to a full commit SHA")
    return problems


def check_config_parses(root, base, head):
    problems = []
    for path in tracked_files(root, head):
        if path.endswith(".toml"):
            try:
                tomllib.loads(text_at(root, head, path) or "")
            except tomllib.TOMLDecodeError as e:
                problems.append(f"{path}: {e}")
        elif path.endswith(".json"):
            try:
                json.loads(text_at(root, head, path) or "")
            except json.JSONDecodeError as e:
                problems.append(f"{path}: {e}")
    return problems


def check_tests_carried(root, base, head):
    if not base:
        return []
    changed = git(root, "diff", "--name-only", "--diff-filter=ACMR", base, head).splitlines()
    code = [p for p in changed if is_code(p) and not is_test(p)]
    if code and not any(is_test(p) for p in changed):
        return ["changes code without changing a test: " + ", ".join(code)]
    return []


def check_system_tests(root, base, head):
    """The system's own tests, run in the working tree (CI checks out head)."""
    if not (Path(root) / "system").is_dir():
        return []
    r = subprocess.run(
        [sys.executable, "-I", "-m", "unittest", "discover", "-s", "system", "-t", "."],
        cwd=root, capture_output=True, text=True,
    )
    return [] if r.returncode == 0 else ["system tests fail:\n" + r.stderr[-4000:]]


CHECKS = [
    ("secrets", check_secrets),
    ("pinned-actions", check_pinned_actions),
    ("config-parses", check_config_parses),
    ("tests-carried", check_tests_carried),
    ("system-tests", check_system_tests),
]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    base = "" if set(args.base) <= {"0"} else args.base

    lines, failed = [], 0
    for name, check in CHECKS:
        problems = check(args.root, base, args.head)
        failed += bool(problems)
        lines.append(f"{'FAIL' if problems else 'PASS'} {name}")
        lines += [f"  - {p}" for p in problems]
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("### Generic checks\n\n```\n" + report + "\n```\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
