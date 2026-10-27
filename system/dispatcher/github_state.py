"""A snapshot of what the Dispatcher acts on: open items, open PRs, the work order and the
protected paths. Reads only.
"""

import base64
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUERY = (Path(__file__).parent / "prs.graphql").read_text()
ROW = re.compile(r"^\|\s*\d+\s*\|\s*#(\d+)\b[^|]*\|\s*([^|]*?)\s*\|")
MARKER = re.compile(r"^<!-- role:([a-z]+)")
RESOLVES = re.compile(r"\b(?:resolves|fixes|closes)\s+#(\d+)", re.IGNORECASE)


def work_order(body):
    """[(item, parallel_safe)] from the work order's table, in order."""
    rows = []
    for line in (body or "").splitlines():
        m = ROW.match(line.strip())
        if m:
            rows.append((int(m[1]), m[2].strip().lower() in {"yes", "y", "true", "✓"}))
    return rows


GOAL_ROW = re.compile(r"^\|\s*([A-Z]+\d+)\s*\|\s*([^|]*?)\s*\|")
GOAL_TITLE = re.compile(r"^Goal ([A-Z]+\d+):")


def requirement_goals(text):
    """Goal IDs the requirements set: rows of the Goals table with a goal written in, not struck, not G0."""
    section = text.split("## 2. Goals", 1)[1].split("\n## ", 1)[0] if "## 2. Goals" in text else ""
    goals = []
    for line in section.splitlines():
        m = GOAL_ROW.match(line.strip())
        if m and m[1] != "G0" and m[2] and not m[2].startswith("~~"):
            goals.append(m[1])
    return goals


def requirements_on_main(gh):
    """project_requirements.md as merged to main: the applied checkout only moves on `runner.py apply`."""
    f = gh.api("GET", f"repos/{gh.repo}/contents/project_requirements.md?ref=main")
    return base64.b64decode(f["content"]).decode()


def protected_patterns(text):
    return [line.split()[0] for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def is_protected(path, patterns):
    for pattern in patterns:
        p = pattern.lstrip("/")
        if (p.endswith("/") and path.startswith(p)) or path == p:
            return True
    return False


def author_role(login, body, identity):
    """The role that opened a PR, or None for the Owner's own PRs and outsiders'."""
    m = MARKER.match(body or "")
    return m[1] if m and login == identity["owner_login"] else None


def login_of(actor):
    """A login as REST spells it: GraphQL drops the "[bot]" that REST and identity.toml use for Apps."""
    actor = actor or {}
    login = actor.get("login")
    return f"{login}[bot]" if login and actor.get("__typename") == "Bot" else login


def gate_of(node, gate_app_id):
    """'SUCCESS', 'FAILURE' (any other completed conclusion) or None while pending or absent."""
    commits = node["commits"]["nodes"]
    suites = commits[0]["commit"]["checkSuites"]["nodes"] if commits else []
    for suite in suites:
        if (suite.get("app") or {}).get("databaseId") != gate_app_id:
            continue
        for check in suite["checkRuns"]["nodes"]:
            if check["status"] == "COMPLETED":
                return "SUCCESS" if check["conclusion"] == "SUCCESS" else "FAILURE"
    return None


def pull_request(node, labels, identity, patterns):
    files = [f["path"] for f in node["files"]["nodes"]]
    return {
        "number": node["number"], "title": node["title"], "draft": node["isDraft"],
        "head": node["headRefOid"], "branch": node["headRefName"],
        "merge_state": node["mergeStateStatus"], "review_decision": node["reviewDecision"],
        "auto_merge": node["autoMergeRequest"] is not None,
        "author": (node.get("author") or {}).get("login"),
        "author_role": author_role((node.get("author") or {}).get("login"), node["body"], identity),
        "labels": labels, "files": files, "protected": any(is_protected(f, patterns) for f in files),
        "resolves": sorted({int(n) for n in RESOLVES.findall(node["body"] or "")}),
        "gate": gate_of(node, identity["gate_app_id"]),
        "reviews": [
            {"login": login_of(r.get("author")), "state": r["state"], "commit": (r.get("commit") or {}).get("oid"),
             "at": r.get("submittedAt")}
            for r in node["reviews"]["nodes"]
        ],
    }


def snapshot(gh, identity, closed_since=None, codeowners=ROOT / ".github" / "CODEOWNERS"):
    """closed_since: also list the items (not records, not PRs) closed after this time."""
    owner, name = identity["repo"].split("/")
    everything = gh.api("GET", f"repos/{gh.repo}/issues?state=open&per_page=100", paginate=True)
    labels_of = {i["number"]: {x["name"] for x in i["labels"]} for i in everything}
    issues = [
        {"number": i["number"], "title": i["title"], "labels": labels_of[i["number"]], "body": i.get("body") or "",
         "url": i["html_url"]}
        for i in everything if "pull_request" not in i
    ]
    patterns = protected_patterns(codeowners.read_text()) if codeowners.exists() else []
    nodes = gh.graphql(QUERY, owner=owner, name=name)["repository"]["pullRequests"]["nodes"]
    prs = [pull_request(n, labels_of.get(n["number"], set()), identity, patterns) for n in nodes]
    order_issue = next((i for i in issues if "record:work-order" in i["labels"]), None)
    closed = []
    if closed_since:
        since = closed_since.isoformat().replace("+00:00", "Z")
        for i in gh.api("GET", f"repos/{gh.repo}/issues?state=closed&since={since}&per_page=100", paginate=True):
            labels = {x["name"] for x in i["labels"]}
            if "pull_request" in i or any(x.startswith("record:") for x in labels):
                continue
            if i.get("closed_at") and datetime.fromisoformat(i["closed_at"].replace("Z", "+00:00")) > closed_since:
                closed.append(i["number"])
    goals = requirement_goals(requirements_on_main(gh))
    records = gh.api("GET", f"repos/{gh.repo}/issues?state=all&labels=record%3Agoal&per_page=100", paginate=True)
    have = {m[1] for i in records if (m := GOAL_TITLE.match(i["title"]))}
    return {"issues": issues, "prs": prs, "work_order": work_order(order_issue["body"] if order_issue else ""),
            "closed": sorted(closed), "new_goals": [g for g in goals if g not in have]}
