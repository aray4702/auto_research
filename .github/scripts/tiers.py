"""The tier filter: which of product.yml's tiers a change runs, by the paths it touches.

Usage: python3 -I .github/scripts/tiers.py --event EVENT --base BASE --head HEAD

A run started by hand (workflow_dispatch) or by the schedule runs every tier, as does a change
git can't compute. A pull request runs tier 1 at most: the model tiers run on main (the Owner,
2026-10-29, "further cut CI job"), and before pushing, a Developer runs `pytest -m models` locally. Writes `tier=<n>` to $GITHUB_OUTPUT and the paths that set it to the step
summary. Standard library only.
"""

import argparse
import os
import re
import subprocess
import sys

# The path rules copy design/architecture.md's "CI" table. Change them together.
# An entry ending "/" matches by prefix, any other exactly.
TIER1 = ("src/", "tests/", "schema/", "results/", "pyproject.toml", "uv.lock", "toolchain.toml",
         ".python-version", "README.md", ".github/scripts/", ".github/workflows/")
NOT_TIER2 = ("README.md", "results/", "src/lmt/eval/", ".github/scripts/")
TIER3_EXCEPT = ("src/lmt/export/txt.py", "src/lmt/export/srt.py", "src/lmt/export/vtt.py",
                "src/lmt/rename.py", "src/lmt/demo/")
TIER3_FILES = ("pyproject.toml", "uv.lock", "toolchain.toml", ".python-version",
               ".github/scripts/tiers.py")
# Tier 3: src/lmt/ except TIER3_EXCEPT, TIER3_FILES, or .github/workflows/.
ALL_TIERS = 3
EVERY_TIER_EVENTS = ("workflow_dispatch", "schedule")
PULL_REQUEST_MAX_TIER = 1
ZEROS = re.compile(r"^0+$")


def matches(path, rules):
    return any(path.startswith(r) if r.endswith("/") else path == r for r in rules)


def tier_for_path(path):
    """The highest tier whose rule matches the path, else 0."""
    if (matches(path, ("src/lmt/",)) and not matches(path, TIER3_EXCEPT)
            or matches(path, TIER3_FILES + (".github/workflows/",))):
        return 3
    if matches(path, TIER1):
        return 1 if matches(path, NOT_TIER2) else 2
    return 0


def tier_for_paths(paths):
    """The highest tier over all paths (0 for none)."""
    return max((tier_for_path(p) for p in paths), default=0)


def decide(event, base, head, repo="."):
    """(tier, the paths that set it, why) for a run."""
    if event in EVERY_TIER_EVENTS:
        return ALL_TIERS, [], f"the event is {event}, which runs every tier"
    if not base or ZEROS.match(base):
        return ALL_TIERS, [], f"the base {base!r} gives no change to compare"
    # --no-renames lists a renamed file's old path too, so moving it out of a tier can't hide it.
    r = subprocess.run(["git", "-C", repo, "diff", "--name-only", "--no-renames", f"{base}...{head}"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return ALL_TIERS, [], f"git can't compute the change {base}...{head}: {r.stderr.strip()}"
    paths = [p for p in r.stdout.splitlines() if p]
    tier = tier_for_paths(paths)
    why = f"{len(paths)} paths changed"
    if event == "pull_request" and tier > PULL_REQUEST_MAX_TIER:
        return PULL_REQUEST_MAX_TIER, [p for p in paths if tier_for_path(p) == tier], \
            f"{why}; tier {tier} by its paths, but a pull request runs tier {PULL_REQUEST_MAX_TIER} at most (main runs the rest)"
    return tier, [p for p in paths if tier_for_path(p) == tier], why


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    tier, paths, reason = decide(args.event, args.base, args.head or "HEAD")
    report = "\n".join([f"tier {tier}: {reason}"] + [f"  {p}" for p in paths])
    print(report)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"tier={tier}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"### Tier {tier}\n\n```\n{report}\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
