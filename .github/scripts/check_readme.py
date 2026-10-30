"""The CI checks of S1-1 to S1-5 (spec/acceptance.md, S1's done-check) on README.md.

Usage: python3 -I .github/scripts/check_readme.py [--root DIR] [--no-install] [--criteria-out PATH]

DIR is a checkout of the commit under test (default: the current directory). Each `sh`
block runs as one `sh -e` script: the Build block in a new empty directory, with the
clone URL redirected to DIR; the Checks block from DIR. --no-install skips S1-3 and S1-4,
the two checks that run a block, as tier 0 does. --criteria-out writes the job's record
(design/internal/criteria.md §3), after the last check. Standard library only.
"""

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

TITLE = "# Local Meeting Transcriber"
CLONE_URL = "https://github.com/aray4702/auto_research.git"
CLONE_LINES = [f"git clone {CLONE_URL}", "cd auto_research"]
BUILD_LAST_LINE = "uv run lmt --help"
GATE_COMMAND = "python3 -I system/gate/generic.py --base origin/main"
# S1-4: the product's commands the README names as inline code, each also run by product.yml's jobs.
PRODUCT_COMMANDS = ["uv run --frozen pytest", "uv run --frozen lmt eval --set ci"]
PRODUCT_YML = ".github/workflows/product.yml"

FENCE = re.compile(r"^```(\S*)[^\n]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]*)>?(?:\s+[\"'(][^)]*)?\)")
REFERENCE_LINK = re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?", re.MULTILINE)
SCHEME = re.compile(r"^(?:https?|mailto):", re.IGNORECASE)


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


def uv_version(root):
    """toolchain.toml's [uv] version, or None."""
    try:
        return tomllib.loads((Path(root) / "toolchain.toml").read_text())["uv"]["version"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        return None


def names_version(text, version):
    """Whether the text names the version on its own, not as part of a longer one (0.9.1 in 0.9.10)."""
    return re.search(rf"(?<![\w.]){re.escape(version)}(?!\.?\w)", text) is not None


def run_sh(script, cwd, env=None):
    """Run a whole block as one `sh -e` script; return its exit code and the end of its output."""
    r = subprocess.run(["sh", "-e", "-c", script], cwd=cwd, env=env, capture_output=True, text=True,
                       check=False)
    return r.returncode, (r.stdout + r.stderr)[-2000:]


def head_of(path):
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True,
                       check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def heading_and_block(text, heading):
    """Problems with the section's heading, and its one `sh` block (None if not exactly one)."""
    n = len(re.findall(rf"^## {re.escape(heading)}$", text, re.MULTILINE))
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
    n = len(re.findall(r"^## What it is$", text, re.MULTILINE))
    return [] if n == 1 else [f"'## What it is' appears {n} times, not once"]


def check_build(root, text):
    problems, block = heading_and_block(text, "Build")
    if block is None:
        return problems
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if lines[:2] != CLONE_LINES:
        problems.append(f"the Build block doesn't start with the lines {CLONE_LINES}")
    if lines[-1:] != [BUILD_LAST_LINE]:
        problems.append(f"the Build block's last line isn't {BUILD_LAST_LINE!r}")
    version = uv_version(root)
    if version is None:
        problems.append("toolchain.toml has no [uv] version")
    elif not names_version(text, version):
        problems.append(f"README.md doesn't name toolchain.toml's uv version, {version}")
    if problems:
        return problems
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
    prose = FENCE.sub("", text)
    try:
        workflow = (Path(root) / PRODUCT_YML).read_text()
    except OSError:
        workflow = ""
    for command in PRODUCT_COMMANDS:
        if f"`{command}`" not in prose:
            problems.append(f"README.md doesn't name `{command}` as inline code outside fenced code")
        if command not in workflow:
            problems.append(f"{PRODUCT_YML} doesn't run {command!r}")
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
# The checks that run a block, so need the install; --no-install skips them.
INSTALL_CHECKS = (check_build, check_checks)
NO_INSTALL_REASON = "tier 0 runs no install"


def criteria_module():
    """criteria.py, loaded by path (`-I` keeps this directory off sys.path): its record writer."""
    spec = importlib.util.spec_from_file_location("lmt_ci_criteria", Path(__file__).with_name("criteria.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--no-install", action="store_true", help="skip S1-3 and S1-4")
    parser.add_argument("--criteria-out", metavar="PATH",
                        help="write each check's outcome to PATH, criteria-readme.json (design/internal/criteria.md §3)")
    args = parser.parse_args(argv)
    readme = Path(args.root) / "README.md"
    text = readme.read_text() if readme.is_file() else None

    lines, records, failed = [], [], 0
    for name, check in CHECKS:
        record = {"check": f"check_readme.py::{name}", "criteria": [name.split()[0]]}
        if args.no_install and check in INSTALL_CHECKS:
            lines.append(f"SKIP {name} (--no-install)")
            records.append(record | {"outcome": "not-run", "reason": NO_INSTALL_REASON})
            continue
        problems = ["README.md is missing at the repository root"] if text is None else check(args.root, text)
        failed += bool(problems)
        lines.append(f"{'FAIL' if problems else 'PASS'} {name}")
        lines += [f"  - {p}" for p in problems]
        records.append(record | {"outcome": "failed" if problems else "passed"})
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("### README checks\n\n```\n" + report + "\n```\n")
    if args.criteria_out:
        criteria_module().write_record(args.criteria_out, records, complete=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
