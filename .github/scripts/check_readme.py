#!/usr/bin/env python3
"""The CI checks of S1-1 to S1-5 (spec/acceptance.md, S1's done-check) on README.md.

Usage: python3 -I .github/scripts/check_readme.py [--root DIR]

DIR is a checkout of the commit under test (default: the current directory). Each `sh`
block runs as one `sh -e` script: the Build block in a new empty directory, with the
clone URL redirected to DIR; the Checks block from DIR. Standard library only.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TITLE = "# voice-app example"
CLONE_URL = "https://github.com/aray4702/auto_research.git"
CLONE_LINES = [f"git clone {CLONE_URL}", "cd auto_research"]
GATE_COMMAND = "python3 -I system/gate/generic.py --base origin/main"

FENCE = re.compile(r"^```(\S*)[^\n]*\n(.*?)^```[ \t]*$", re.M | re.S)
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]*)>?(?:\s+[\"'(][^)]*)?\)")
REFERENCE_LINK = re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?", re.M)
SCHEME = re.compile(r"^(?:https?|mailto):", re.I)


def section(text, heading):
    """The body of the `## heading` section, up to the next level-1 or level-2 heading.

    Lines inside fenced code are never headings, so a shell comment doesn't end the section.
    """
    body, inside, fenced = [], False, False
    for line in text.splitlines(keepends=True):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(r"##? ", line):
            if inside:
                break
            inside = line.rstrip("\n") == f"## {heading}"
            continue
        if inside:
            body.append(line)
    return "".join(body)


def link_targets(text):
    """Markdown link targets outside fenced code."""
    prose = FENCE.sub("", text)
    return INLINE_LINK.findall(prose) + REFERENCE_LINK.findall(prose)


def run_sh(script, cwd, env=None):
    """Run a whole block as one `sh -e` script; return its exit code and the end of its output."""
    r = subprocess.run(["sh", "-e", "-c", script], cwd=cwd, env=env, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)[-2000:]


def head_of(path):
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def heading_and_block(text, heading):
    """Problems with the section's heading, and its one `sh` block (None if not exactly one)."""
    n = len(re.findall(rf"^## {re.escape(heading)}$", text, re.M))
    if n != 1:
        return [f"'## {heading}' appears {n} times, not once"], None
    blocks = [code for lang, code in FENCE.findall(section(text, heading)) if lang == "sh"]
    if len(blocks) != 1:
        return [f"'## {heading}' has {len(blocks)} fenced sh blocks, not 1"], None
    return [], blocks[0]


def check_title(root, text):
    first = text.split("\n", 1)[0].rstrip()
    if first.lower() != TITLE.lower():
        return [f"the first line is {first!r}, not {TITLE!r}"]
    return []


def check_what_it_is(root, text):
    n = len(re.findall(r"^## What it is$", text, re.M))
    return [] if n == 1 else [f"'## What it is' appears {n} times, not once"]


def check_build(root, text):
    problems, block = heading_and_block(text, "Build")
    if block is None:
        return problems
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if lines[:2] != CLONE_LINES:
        return [f"the Build block doesn't start with the lines {CLONE_LINES}"]
    # Redirect the clone URL to the checkout, for this run only.
    env = dict(os.environ)
    n = int(env.get("GIT_CONFIG_COUNT") or 0)
    env.update({
        "GIT_CONFIG_COUNT": str(n + 1),
        f"GIT_CONFIG_KEY_{n}": f"url.{Path(root).resolve()}.insteadOf",
        f"GIT_CONFIG_VALUE_{n}": CLONE_URL,
    })
    with tempfile.TemporaryDirectory() as tmp:
        code, out = run_sh(block, tmp, env)
        if code != 0:
            return [f"the Build block exits {code}:\n{out}"]
        got = head_of(Path(tmp) / "auto_research")
    want = head_of(root)
    if not want or got != want:
        return [f"the clone's HEAD is {got}, not the commit under test {want}"]
    return []


def check_checks(root, text):
    problems, block = heading_and_block(text, "Checks")
    if GATE_COMMAND not in text:
        problems.append(f"README.md doesn't contain {GATE_COMMAND!r}")
    if block is not None:
        code, out = run_sh(block, root)
        if code != 0:
            problems.append(f"the Checks block exits {code}:\n{out}")
    return problems


def check_links(root, text):
    problems = []
    for target in link_targets(text):
        path = target.split("#", 1)[0]
        if SCHEME.match(target) or not path:
            continue
        if not (Path(root) / path).exists():
            problems.append(f"the relative link '{target}' points to nothing")
    return problems


CHECKS = [
    ("S1-1 title", check_title),
    ("S1-2 what it is", check_what_it_is),
    ("S1-3 build", check_build),
    ("S1-4 checks", check_checks),
    ("S1-5 relative links", check_links),
]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    readme = Path(args.root) / "README.md"
    if not readme.is_file():
        print("FAIL S1-1 title\n  - README.md is missing at the repository root")
        return 1

    text = readme.read_text()
    lines, failed = [], 0
    for name, check in CHECKS:
        problems = check(args.root, text)
        failed += bool(problems)
        lines.append(f"{'FAIL' if problems else 'PASS'} {name}")
        lines += [f"  - {p}" for p in problems]
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("### README checks\n\n```\n" + report + "\n```\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
