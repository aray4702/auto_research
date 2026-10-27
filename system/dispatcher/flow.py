#!/usr/bin/env python3
"""Where finished items spend their time (blueprint §4.5): each item's life, from opening to close, split
into stages. The Dispatcher's daily pass records one row per item closed as completed (flow.jsonl on
dispatcher/state) and raises the signals below; the builder's daily run reads the report.

  flow.py [--days N]    the report: stages per finished item, against the four weeks before

Pure functions, except fetch(), which reads GitHub, and main(). Standard library only.
"""

import argparse
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from system.dispatcher import github_state, state  # noqa: E402
from system.tools import runner  # noqa: E402

# In the order the report lists them. A moment of an item's life takes the first stage that applies (stage_at).
STAGES = ("running", "owner", "decision", "blocked", "rework", "review", "merging", "queued", "triage")
WAITING = ("queued", "review")  # waits a run would end: with no run going at all, the system idles (§4.5)
HISTORY_DAYS = 28
NEVER = datetime.max.replace(tzinfo=timezone.utc)


def at(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def labels_at(changes, t):
    """The labels held at t, from [(at, "labeled"|"unlabeled", name)]."""
    have = set()
    for when, event, name in sorted(changes):
        if when > t:
            break
        (have.add if event == "labeled" else have.discard)(name)
    return have


def stage_at(t, item, prs, runs, bot, code_owner):
    """The one stage an item is in at t. runs are (start, end, role) on the item or its PRs."""
    if any(s <= t < e for s, e, _ in runs):
        return "running"
    open_prs = [p for p in prs if p["opened"] <= t < (p["closed"] or NEVER)]
    labels = labels_at(item["labels"], t).union(*(labels_at(p["labels"], t) for p in open_prs))
    if "needs-decision:owner" in labels:
        return "owner"
    if any(x.startswith("needs-decision:") for x in labels):
        return "decision"
    if "state:blocked" in labels:
        return "blocked"
    if not open_prs:
        return "triage" if "state:triage" in labels else "queued"
    pr = max(open_prs, key=lambda p: p["opened"])
    verdicts = [r for r in pr["reviews"] if r[1] == bot and r[0] <= t]
    if not verdicts:
        return "review"
    last = verdicts[-1][0]
    if verdicts[-1][2] == "CHANGES_REQUESTED":
        fixed = any(last < e <= t and role != "reviewer" for _, e, role in runs)
        return "review" if fixed else "rework"
    owner_ok = [r[0] for r in pr["reviews"] if r[1] == code_owner and r[2] == "APPROVED" and r[0] >= last]
    return "owner" if owner_ok and t < owner_ok[0] else "merging"


def record(item, prs, runs, bot, code_owner):
    """One finished item's row: hours per stage, and its waiting while nothing ran at all.
    runs are every run, (start, end, role, item); the item's own are those on it or its PRs."""
    mine = {item["number"], *(p["number"] for p in prs)}
    own = [(s, e, role) for s, e, role, n in runs if n in mine]
    start, end = item["opened"], item["closed"]
    points = {start, end}
    points.update(t for changes in [item["labels"], *(p["labels"] for p in prs)] for t, *_ in changes)
    points.update(t for p in prs for t in (p["opened"], p["closed"]) if t)
    points.update(r[0] for p in prs for r in p["reviews"])
    points.update(t for s, e, *_ in runs for t in (s, e))
    points = sorted(t for t in points if start <= t <= end)
    stages, idle = {}, 0.0
    for a, b in zip(points, points[1:]):
        stage, hours = stage_at(a, item, prs, own, bot, code_owner), (b - a).total_seconds() / 3600
        stages[stage] = stages.get(stage, 0.0) + hours
        if stage in WAITING and not any(s <= a < e for s, e, *_ in runs):
            idle += hours
    return {"item": item["number"], "closed": end.isoformat(), "prs": sorted(p["number"] for p in prs),
            "hours": round((end - start).total_seconds() / 3600, 2),
            "stages": {k: round(v, 2) for k, v in stages.items() if v}, "idle_hours": round(idle, 2)}


def run_spans(runs):
    """(start, end, role, item) for every run that ended, from runs.jsonl."""
    starts = {r["run_id"]: r for r in runs if r["event"] == "start"}
    return [(at(starts[r["run_id"]]["at"]), at(r["at"]), r["role"], r.get("item"))
            for r in runs if r["event"] == "end" and r["run_id"] in starts]


def changes(timeline):
    return [(at(e["created_at"]), e["event"], e["label"]["name"]) for e in timeline if e["event"] in ("labeled", "unlabeled")]


def fetch(gh, issue, runs, bot, code_owner):
    """The flow row of one closed issue, read from its timeline and those of the PRs that resolve it."""
    timeline = gh.api("GET", f"repos/{gh.repo}/issues/{issue['number']}/timeline?per_page=100", paginate=True)
    prs, seen = [], set()
    for e in timeline:
        src = (e.get("source") or {}).get("issue") or {}
        if e["event"] != "cross-referenced" or "pull_request" not in src or src["number"] in seen:
            continue
        if issue["number"] not in {int(n) for n in github_state.RESOLVES.findall(src.get("body") or "")}:
            continue
        seen.add(src["number"])
        events = gh.api("GET", f"repos/{gh.repo}/issues/{src['number']}/timeline?per_page=100", paginate=True)
        prs.append({"number": src["number"], "opened": at(src["created_at"]),
                    "closed": at(src["closed_at"]) if src.get("closed_at") else None, "labels": changes(events),
                    "reviews": sorted((at(e["submitted_at"]), (e.get("user") or {}).get("login"), e["state"].upper())
                                      for e in events if e["event"] == "reviewed" and e.get("submitted_at"))})
    item = {"number": issue["number"], "opened": at(issue["created_at"]), "closed": at(issue["closed_at"]),
            "labels": changes(timeline)}
    return record(item, prs, run_spans(runs), bot, code_owner)


def window(records, since, until):
    return [r for r in records if since <= at(r["closed"]) < until]


def median_of(rows, stage):
    return statistics.median(r["stages"].get(stage, 0.0) for r in rows) if rows else 0.0


def week_key(now):
    """Signals here look at a rolling week, so each is raised at most once per calendar week."""
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def bottlenecks(records, now):
    """A stage whose median hours per finished item this week is more than double its median over the
    four weeks before, and over an hour longer. Each side needs three items, so one item can't decide."""
    week = window(records, now - timedelta(days=7), now)
    before = window(records, now - timedelta(days=7 + HISTORY_DAYS), now - timedelta(days=7))
    if len(week) < 3 or len(before) < 3:
        return []
    out = []
    for stage in STAGES:
        this, usual = median_of(week, stage), median_of(before, stage)
        if this > 2 * usual and this - usual > 1:
            out.append((f"bottleneck:{stage}:{week_key(now)}", "builder", f"Flow bottleneck: `{stage}` has more than doubled",
                        f"Median {this:.1f} h per finished item over {len(week)} items this week, against {usual:.1f} h over "
                        f"{len(before)} items in the four weeks before. Report: `python3 -I system/dispatcher/flow.py`.", None))
    return out


def idle_waits(records, now, threshold_hours=2):
    """Items that waited for a run, or for the Reviewer, while nothing ran at all: §4.5's 'waits while a
    role idles'. Raised when the week's total passes the threshold."""
    week = [r for r in window(records, now - timedelta(days=7), now) if r.get("idle_hours")]
    total = sum(r["idle_hours"] for r in week)
    if total <= threshold_hours:
        return []
    worst = ", ".join(f"#{r['item']} {r['idle_hours']:.1f} h" for r in sorted(week, key=lambda r: -r["idle_hours"])[:5])
    return [(f"idle:{week_key(now)}", "builder", "Items waited while nothing ran",
             f"{total:.1f} h this week across {len(week)} items ({worst}): queued or waiting for the Reviewer with no run "
             f"going. Find what kept runs from starting (a cap, the kill switch, the Mac asleep, a missed poll).", None)]


def metrics(records, now):
    """For the watch list: median hours from opening to close of this week's finished items."""
    week = window(records, now - timedelta(days=7), now)
    return {"median_item_hours": round(statistics.median(r["hours"] for r in week), 1) if week else 0.0}


def report(records, now, days=7, zone="America/Los_Angeles"):
    week = window(records, now - timedelta(days=days), now)
    before = window(records, now - timedelta(days=days + HISTORY_DAYS), now - timedelta(days=days))
    local = now.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M %Z")
    if not week:
        return f"No items closed as completed in the {days} days to {local}."
    total = sum(r["hours"] for r in week) or 1
    lines = [f"Flow of {len(week)} items closed in the {days} days to {local}, against {len(before)} in the "
             f"{HISTORY_DAYS} days before.", "", "| Stage | Median h/item | Share of time | Median before |", "| --- | --- | --- | --- |"]
    shares = {s: sum(r["stages"].get(s, 0.0) for r in week) / total for s in STAGES}
    for s in STAGES:
        if shares[s] or median_of(before, s):
            usual = f"{median_of(before, s):.1f}" if before else "–"
            lines.append(f"| {s} | {median_of(week, s):.1f} | {shares[s]:.0%} | {usual} |")
    top = max(STAGES, key=lambda s: shares[s])
    lines += ["", f"Bottleneck: `{top}`, {shares[top]:.0%} of the time items were open.",
              f"Median from opening to close: {statistics.median(r['hours'] for r in week):.1f} h."]
    idle = sum(r.get("idle_hours", 0) for r in week)
    if idle:
        lines.append(f"Waiting while nothing ran: {idle:.1f} h.")
    slow = sorted(week, key=lambda r: -r["hours"])[:3]
    lines.append("Slowest: " + "; ".join(
        f"#{r['item']} {r['hours']:.1f} h (mostly {max(r['stages'], key=r['stages'].get) if r['stages'] else '–'})" for r in slow) + ".")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args(argv)
    config = runner.load_all()
    store = state.Store(runner.home(config) / "state", f"https://github.com/{config['identity']['repo']}.git")
    print(report(store.read("flow"), datetime.now(timezone.utc), args.days, config["identity"]["time_zone"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
