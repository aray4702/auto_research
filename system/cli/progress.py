"""Progress as the Owner measures it: features and requirements met, not items closed (#241).

A feature (F1–F9) or requirement (Q1–Q10) in the requirements file is met when every acceptance-spec criterion
that names it is. Until the product records each criterion's check (#239), a criterion counts as built when every
feature item naming it is closed, which is short of verified. Pure functions: data.py reads, views.py shows.
"""

import re
from collections import defaultdict
from datetime import datetime

from system.dispatcher import audit

FEATURE = re.compile(r"^\| (F\d+) +\| ([^|]+)\|", re.M)
REQUIREMENT = re.compile(r"^\| (Q\d+) +\| [^|]+\| ([^|]+)\|", re.M)
CRITERION = re.compile(r"^\| (G\d+-\d+[a-z]?) \| ([^|]+)\|", re.M)
MENTION = re.compile(r"\bG\d+-\d+[a-z]?\b")
RESOLVES = re.compile(r"(?i)\bresolves #(\d+)")
WORK = {"type:feature", "type:fix", "type:investigation"}


def short(text, n=64):
    """The text's first sentence, cut at n characters."""
    first = re.split(r"\.(?:\s|$)", text.strip(), maxsplit=1)[0]
    return first if len(first) <= n else first[: n - 1].rstrip() + "…"


def wanted(requirements):
    """[(ID, short name)] for the requirements file's features and requirements, in their order."""
    return ([(m[1], short(m[2])) for m in FEATURE.finditer(requirements)]
            + [(m[1], short(m[2])) for m in REQUIREMENT.finditer(requirements)])


def criteria(spec):
    """{criterion ID: [the F and Q IDs it names]} from the acceptance spec's done-check tables."""
    return {m[1]: re.findall(r"\b([FQ]\d+)\b", m[2]) for m in CRITERION.finditer(spec)}


def due(requirements):
    """{goal ID: its deadline's dates, as "MM-DD / MM-DD"} from the requirements' Goals table."""
    return {g: " / ".join(dict.fromkeys(d[5:] for d in re.findall(r"\d{4}-\d{2}-\d{2}", text))) or text
            for g, text in audit.deadlines(requirements).items()}


def kind_of(issue):
    """Which kind of work an item's runs count as, for the cost split."""
    labels = issue["labels"]
    if any(x.startswith("record:") for x in labels):
        return "records"
    if "type:system" in labels:
        return "system"
    if "type:definition" in labels:
        return "definition and design"
    if labels & WORK:
        return "feature items"
    return "other"


def costs(issues, prs, runs):
    """({item: cost}, {item: first start}, {kind: cost}, total). A run on a PR counts for the items it resolves,
    split evenly; a run without an item counts as Coordinator passes and daily runs."""
    resolves = {p["number"]: [int(n) for n in RESOLVES.findall(p.get("body") or "")] for p in prs}
    item_cost, first, by_kind, total = defaultdict(float), {}, defaultdict(float), 0.0
    for r in runs:
        targets = (resolves.get(r["item"]) or [r["item"]]) if r.get("item") else []
        if r["event"] == "start":
            at = datetime.fromisoformat(r["at"])
            for t in targets:
                first[t] = min(first.get(t, at), at)
            continue
        if r["event"] != "end":
            continue
        c = r.get("cost_usd") or 0
        total += c
        if not targets:
            by_kind["Coordinator passes and daily runs"] += c
        for t in targets:
            item_cost[t] += c / len(targets)
            by_kind[kind_of(issues[t]) if t in issues else "other"] += c / len(targets)
    return item_cost, first, by_kind, total


def table(requirements, spec, issues, prs, runs):
    """The Progress tab. issues: {number: {title, body, state, labels (set)}}, closed ones included; prs: [{number,
    body}]; runs: the run records. Each row: id, name, criteria, built, status, started (datetime or None), goals,
    cost."""
    crits = criteria(spec)
    building = defaultdict(set)  # criterion -> the feature items naming it
    for n, i in issues.items():
        if i["labels"] & WORK:
            for c in set(MENTION.findall(i["title"] + " " + (i.get("body") or ""))):
                building[c].add(n)
    item_cost, first, by_kind, total = costs(issues, prs, runs)
    dates = due(requirements)
    rows = []
    for ident, name in wanted(requirements):
        mine = [c for c, refs in crits.items() if ident in refs]
        items = set().union(*(building[c] for c in mine)) if mine else set()
        built = [c for c in mine if building[c] and all(issues[n]["state"] == "CLOSED" for n in building[c])]
        started = min((first[n] for n in items if n in first), default=None)
        if not mine:
            status = "no criterion in the spec"
        elif len(built) == len(mine):
            status = "built, not yet verified"
        elif started:
            status = f"in progress: {len(built)} of {len(mine)} criteria built"
        else:
            status = "not started"
        goals = sorted({c.split("-")[0] for c in mine})
        rows.append({"id": ident, "name": name, "criteria": mine, "built": built, "status": status, "started": started,
                     "due": "; ".join(f"{g} {dates[g]}" for g in goals if g in dates), "cost": sum(item_cost[n] for n in items)})
    order = ["feature items", "definition and design", "system", "Coordinator passes and daily runs", "records", "other"]
    split = [(k, by_kind[k]) for k in order if by_kind.get(k)]
    return {"rows": rows, "costs": split, "total": total,
            "since": min((datetime.fromisoformat(r["at"]) for r in runs), default=None)}
