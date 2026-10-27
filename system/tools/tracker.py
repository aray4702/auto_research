#!/usr/bin/env python3
"""The tracker's labels and records on GitHub (blueprint §3), as system/tracker.toml defines them.

  tracker.py check    exit 1 unless rules.md and tracker.toml agree, every label exists as
                      defined, and each record exists exactly once as an open issue
  tracker.py setup    create what is missing and correct label colours and descriptions, then check

Records are created through the posting tool, so they carry the caller's marker
(AGENT_ROLE, AGENT_RUN_ID). Standard library only.
"""

import argparse
import re
import sys
import time
import tomllib
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from system.tools import post  # noqa: E402

CONFIG = ROOT / "system" / "tracker.toml"
RULES = ROOT / "system" / "rules.md"
LABEL = re.compile(r"`((?:[a-z-]+|…):[a-z0-9-]+)`")


def load_config(path=CONFIG):
    with open(path, "rb") as f:
        return tomllib.load(f)


def labels_in_rules(text):
    """Label names in the first column of the rules' labels table; `…:x` repeats the prefix before it."""
    section = text.split("## Items and labels", 1)[1].split("\n## ", 1)[0]
    names = []
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        prefix = None
        for token in LABEL.findall(line.split("|")[1]):
            head, tail = token.split(":", 1)
            if head == "…":
                token = f"{prefix}:{tail}"
            else:
                prefix = head
            names.append(token)
    return names


def quote(name):
    return urllib.parse.quote(name, safe="")


def existing_labels(gh):
    return {label["name"]: label for label in gh.api("GET", f"repos/{gh.repo}/labels?per_page=100", paginate=True)}


def record_issues(gh, label):
    # Filter here rather than with ?labels=: GitHub's label filter lags a few seconds behind a new
    # issue, which once made a fresh record look missing (and a second setup could duplicate it).
    issues = gh.api("GET", f"repos/{gh.repo}/issues?state=open&per_page=100", paginate=True)
    return [i for i in issues if "pull_request" not in i and label in {x["name"] for x in i["labels"]}]


def differs(have, want):
    return have["color"].lower() != want["color"] or (have.get("description") or "") != want["description"]


def problems(gh, config):
    have = existing_labels(gh)
    out = []
    for want in config["labels"]:
        if want["name"] not in have:
            out.append(f"label {want['name']} is missing")
        elif differs(have[want["name"]], want):
            out.append(f"label {want['name']} differs in colour or description")
    for record in config["records"]:
        found = record_issues(gh, record["label"])
        if len(found) != 1:
            numbers = ", ".join(f"#{i['number']}" for i in found) or "none"
            out.append(f"record {record['label']}: expected one open issue, found {numbers}")
    return out


def setup(gh, config, caller):
    have, done = existing_labels(gh), []
    for want in config["labels"]:
        fields = {"color": want["color"], "description": want["description"]}
        if want["name"] not in have:
            gh.api("POST", f"repos/{gh.repo}/labels", {"name": want["name"], **fields})
            done.append(f"created label {want['name']}")
        elif differs(have[want["name"]], want):
            gh.api("PATCH", f"repos/{gh.repo}/labels/{quote(want['name'])}", fields)
            done.append(f"updated label {want['name']}")
    for record in config["records"]:
        if not record_issues(gh, record["label"]):
            made = post.cmd_issue_create(gh, caller, record["title"], [record["label"]], record["body"].strip() + "\n")
            done.append(f"created record {record['label']}: {made}")
    return done


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check", "setup"])
    args = parser.parse_args(argv)
    config = load_config()
    disagree = sorted(set(labels_in_rules(RULES.read_text())) ^ {label["name"] for label in config["labels"]})
    if disagree:
        print("FAIL rules.md and tracker.toml disagree on: " + ", ".join(disagree))
        return 1
    identity = post.load_identity()
    gh = post.GitHub(identity["repo"])
    created = []
    if args.command == "setup":
        created = setup(gh, config, post.caller_from_env(identity))
        for line in created or ["nothing to do"]:
            print(line)
    found = problems(gh, config)
    for _ in range(3 if created and found else 0):
        time.sleep(5)  # let GitHub catch up with what setup just created
        found = problems(gh, config)
    print("FAIL tracker" if found else "PASS tracker: every label and record exists as defined")
    for p in found:
        print(f"  - {p}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
