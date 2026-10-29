"""Each acceptance criterion's state, from the spec, the registry and the jobs' records.

Usage: python3 -I .github/scripts/criteria.py --spec spec/acceptance.md --registry tests/criteria.toml
                  [--ref REF] [--out FILE] RECORD...

Design: design/internal/criteria.md §4. Writes criteria.json to FILE, or to stdout, and exits 0;
exits 3 with one `criteria.py: error:` line when an input is missing or malformed. The pytest
plugin (tests/conftest.py) and check_readme.py load this file by path for its parser and its
record writer, so there is one of each. Standard library only.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path

FORMAT = 1
ID = re.compile(r"^S\d+-\d+$|^G\d+-\d+[mc]?$")
GOAL = re.compile(r"^## ([A-Z]\d+):")
OUTCOMES = ("passed", "failed", "skipped", "not-run")
REGISTRY_KEYS = {"ci": bool, "complete": bool, "owner": str}
# criteria-<job>.json, or criteria-<job>-<n>.json when a job's checks span several programs (§3).
RECORD_NAME = re.compile(r"^criteria-(?P<job>[a-z0-9]+(?:-[a-z][a-z0-9]*)*)(?:-\d+)?\.json$")
EXIT_ERROR = 3


class InputError(Exception):
    """An input that is missing or malformed: exit 3."""


def cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_spec(text):
    """[{id, goal, requirements}] in the spec's order, from each goal's `### Done-check` tables."""
    criteria, goal, in_check, header = [], None, False, None
    for line in text.splitlines():
        if line.startswith("## "):
            m = GOAL.match(line)
            goal, in_check, header = (m.group(1) if m else None), False, None
        elif line.startswith("### "):
            in_check, header = goal is not None and line.strip() == "### Done-check", None
        elif in_check and line.startswith("|"):
            row = cells(line)
            if header is None:
                header = row
            elif ID.match(row[0]):
                if "Requirement" in header:
                    requirements = row[header.index("Requirement")].split(", ")
                else:
                    requirements = [goal]
                criteria.append({"id": row[0], "goal": goal, "requirements": requirements})
        elif in_check and not line.strip():
            header = None
    return criteria


def spec_ids(path):
    return {c["id"] for c in parse_spec(Path(path).read_text())}


def load_registry(path, ids):
    """The registry's `jobs` and its entries ({id: {ci, complete, owner}}), checked against the spec's ids."""
    try:
        data = tomllib.loads(Path(path).read_text())
    except OSError as e:
        raise InputError(f"{path}: {e.strerror}") from None
    except tomllib.TOMLDecodeError as e:
        raise InputError(f"{path}: not TOML: {e}") from None
    jobs = data.pop("jobs", None)
    if not isinstance(jobs, list) or not all(isinstance(j, str) for j in jobs):
        raise InputError(f"{path}: `jobs` must be a list of job names")
    entries = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            raise InputError(f"{path}: unknown key {key!r}")
        if key not in ids:
            raise InputError(f"{path}: [{key}] is not a criterion in the spec")
        for k, v in entry.items():
            if k not in REGISTRY_KEYS:
                raise InputError(f"{path}: [{key}] has unknown key {k!r}")
            if not isinstance(v, REGISTRY_KEYS[k]):
                raise InputError(f"{path}: [{key}] {k} must be a {REGISTRY_KEYS[k].__name__}")
        ci = entry.get("ci", True)
        if not ci and "owner" not in entry:
            raise InputError(f"{path}: [{key}] has ci = false without owner, so it can't be met")
        if not ci and "complete" in entry:
            raise InputError(f"{path}: [{key}] sets complete with ci = false")
        entries[key] = {"ci": ci, "complete": entry.get("complete", False), "owner": entry.get("owner")}
    return jobs, entries


# Writing a record (§3).

def _env_int(name):
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else None


def job_of(path):
    """The job a record file belongs to, from its name; None if it isn't `criteria-<job>[-<n>].json`."""
    m = RECORD_NAME.match(Path(path).name)
    return m.group("job") if m else None


def record(job, checks, complete):
    """A job's record, with the run's fields from the environment (null outside CI)."""
    return {
        "format": FORMAT,
        "job": job,
        "commit": os.environ.get("GITHUB_SHA") or None,
        "ref": os.environ.get("GITHUB_REF") or None,
        "event": os.environ.get("GITHUB_EVENT_NAME") or None,
        "run_id": _env_int("GITHUB_RUN_ID"),
        "run_attempt": _env_int("GITHUB_RUN_ATTEMPT"),
        "tier": _env_int("LMT_TIER"),
        "complete": complete,
        "checks": checks,
    }


def write_record(path, checks, complete):
    """Write the record to a temporary name beside PATH, then rename it, so no partial file is left."""
    path = Path(path)
    job = job_of(path)
    if job is None:
        raise ValueError(f"{path.name}: a record's name is criteria-<job>.json or criteria-<job>-<n>.json")
    text = json.dumps(record(job, checks, complete), indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent or ".")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# The merge (§4).

def load_record(path, ids, jobs):
    try:
        data = json.loads(Path(path).read_text())
    except OSError as e:
        raise InputError(f"{path}: {e.strerror}") from None
    except json.JSONDecodeError as e:
        raise InputError(f"{path}: not JSON: {e}") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise InputError(f"{path}: not a record of format {FORMAT}")
    if not isinstance(data.get("complete"), bool):
        raise InputError(f"{path}: `complete` is missing")
    if data.get("job") not in jobs:
        raise InputError(f"{path}: job {data.get('job')!r} is not in the registry's jobs")
    for key in ("run_id", "run_attempt"):
        if data.get(key) is not None and not isinstance(data[key], int):
            raise InputError(f"{path}: `{key}` must be an integer")
    checks = data.get("checks")
    if not isinstance(checks, list):
        raise InputError(f"{path}: `checks` must be a list")
    for check in checks:
        if not (isinstance(check, dict) and isinstance(check.get("check"), str)
                and isinstance(check.get("criteria"), list)):
            raise InputError(f"{path}: a check needs `check` and `criteria`")
        if check.get("outcome") not in OUTCOMES:
            raise InputError(f"{path}: {check['check']}: unknown outcome {check.get('outcome')!r}")
        if check["outcome"] in ("skipped", "not-run") and not isinstance(check.get("reason"), str):
            raise InputError(f"{path}: {check['check']}: a {check['outcome']} check needs a reason")
        for cid in check["criteria"]:
            if cid not in ids:
                raise InputError(f"{path}: {check['check']}: {cid!r} is not a criterion in the spec")
    return data


def runs_by_job(records, ref):
    """{job: [complete record, ...]} oldest first, one record per run, with the run's files joined."""
    runs = {}
    for r in records:
        if r.get("ref") != ref:
            continue
        key = (r["job"], r.get("run_id"), r.get("run_attempt"))
        if key not in runs:
            runs[key] = dict(r, checks=[])
        runs[key]["complete"] = runs[key]["complete"] and r["complete"]
        runs[key]["checks"] = runs[key]["checks"] + r["checks"]
    by_job = {}
    for (job, run_id, attempt), r in sorted(runs.items(), key=lambda kv: (kv[0][1] or 0, kv[0][2] or 0)):
        if r["complete"]:
            by_job.setdefault(job, []).append(r)
    return by_job


def current_checks(history):
    """The job's current checks, from its latest record: [(check, commit)], with each `not-run`
    check given its outcome from the latest earlier record in which it ran."""
    *earlier, latest = history
    out = []
    for check in latest["checks"]:
        commit = latest.get("commit")
        if check["outcome"] == "not-run":
            for r in reversed(earlier):
                ran = [c for c in r["checks"] if c["check"] == check["check"] and c["outcome"] != "not-run"]
                if ran:
                    check, commit = dict(ran[-1], criteria=check["criteria"]), r.get("commit")
                    break
        out.append((check, commit))
    return out


def state(entry, outcomes):
    if not entry["ci"]:
        return "not-in-ci"
    if not outcomes:
        return "no-check"
    for outcome, name in (("failed", "fail"), ("not-run", "not-run"), ("skipped", "waiting")):
        if outcome in outcomes:
            return name
    return "pass" if entry["complete"] else "partial"


def merge(criteria, jobs, entries, records, ref):
    by_job = runs_by_job(records, ref)
    checks = [pair for job in jobs if job in by_job for pair in current_checks(by_job[job])]
    out = []
    for c in criteria:
        entry = entries.get(c["id"], {"ci": True, "complete": False, "owner": None})
        mine = [(check, commit) for check, commit in checks if c["id"] in check["criteria"]]
        outcomes = [check["outcome"] for check, _ in mine]
        reasons = [check["reason"] for check, _ in mine if check["outcome"] in ("skipped", "not-run")]
        commits = [commit for _, commit in mine if commit is not None]
        out.append({
            "id": c["id"], "goal": c["goal"], "requirements": c["requirements"],
            "ci": state(entry, outcomes),
            "checks": {o: outcomes.count(o) for o in OUTCOMES},
            "reasons": list(dict.fromkeys(reasons)),
            "commits": list(dict.fromkeys(commits)),
            "owner": entry["owner"],
        })
    latest = {job: by_job[job][-1] if job in by_job else None for job in jobs}
    return {
        "format": FORMAT,
        "jobs": {job: r and {"commit": r.get("commit"), "run_id": r.get("run_id"),
                             "run_attempt": r.get("run_attempt")} for job, r in latest.items()},
        "criteria": out,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--ref", default="refs/heads/main")
    parser.add_argument("--out")
    parser.add_argument("records", nargs="*", metavar="RECORD")
    args = parser.parse_args(argv)
    try:
        try:
            criteria = parse_spec(Path(args.spec).read_text())
        except OSError as e:
            raise InputError(f"{args.spec}: {e.strerror}") from None
        if not criteria:
            raise InputError(f"{args.spec}: no criteria found")
        ids = {c["id"] for c in criteria}
        jobs, entries = load_registry(args.registry, ids)
        records = [load_record(p, ids, jobs) for p in args.records]
    except InputError as e:
        print(f"criteria.py: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    text = json.dumps(merge(criteria, jobs, entries, records, args.ref), indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
