"""The Dispatcher's daily signals and rule audit (blueprint §4.5). Pure checks: each returns
anomalies, and dispatch.py takes them to their responses. Measured by a script, so no role grades
its own work.

An anomaly is (key, level, title, evidence, item): key de-duplicates across days; level is who
responds (builder, coordinator, or owner for a rule breach); item, when set, is where it's raised.
"""

import re
from datetime import datetime, timedelta

from system.dispatcher import usage

FINISHED = "finished"
KNOWN_STATUSES = {"finished", "cap: turns", "cap: budget", "timeout", "cut-off", "failed"}
WATCH_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*`?([a-z_]+)`?\s*\|\s*([<>]=?)\s*([0-9.]+)\s*\|")


def recent(runs, now, days=7):
    since = now - timedelta(days=days)
    return [r for r in runs if datetime.fromisoformat(r["at"]) >= since]


def ends(runs):
    return [r for r in runs if r["event"] == "end"]


def denials(runs, now):
    """The same denial (tool and first words) in three or more runs this week: a permission gap (#44).
    A compound command's denial isn't one (#140): the rules say to run one command at a time."""
    seen = {}
    for r in ends(recent(runs, now)):
        for signature in {s for s in r.get("denied") or [] if not s.endswith(usage.COMPOUND)}:
            seen.setdefault(signature, set()).add(r["run_id"])
    return [(f"denial:{sig}", "builder", f"Recurring permission denial: {sig}",
             f"Denied in {len(ids)} runs this week: {', '.join(sorted(ids))}. Grant it by rule, or say in the rules why not.", None)
            for sig, ids in sorted(seen.items()) if len(ids) >= 3]


def bumpy(runs, now, open_items):
    """An open item that stops, or needs more than one run: the Coordinator re-plans around it. Closed
    items and PRs are left out: a PR's review, fix and re-review is the normal loop (see review_rounds)."""
    by_item = {}
    for r in ends(recent(runs, now)):
        if r.get("item") in open_items:
            by_item.setdefault(r["item"], []).append(r)
    out = []
    for item, rs in sorted(by_item.items()):
        unfinished = [r for r in rs if r.get("status") != FINISHED]
        if len(unfinished) >= 2 or len(rs) >= 3:
            statuses = ", ".join(f"{r['role']} {r.get('status')}" for r in rs)
            out.append((f"bumpy:{item}:{len(rs)}", "coordinator", f"Bumpy item #{item}",
                        f"{len(rs)} runs this week ({statuses}). Re-plan around it: split it, unblock it, or say why not.", item))
    return out


def review_rounds(prs, bot, cap):
    """An open PR whose Reviewer has asked for changes as many times as the review-rounds cap allows."""
    out = []
    for pr in prs:
        rounds = sum(r["login"] == bot and r["state"] == "CHANGES_REQUESTED" for r in pr["reviews"])
        if rounds >= cap:
            out.append((f"rounds:{pr['number']}:{rounds}", "coordinator", f"PR #{pr['number']} has had {rounds} review rounds",
                        f"The cap is {cap} (system/caps.toml [wip] max_review_rounds). Decide: split it, redirect it, or close it.",
                        pr["number"]))
    return out


def new_failures(runs, now, history):
    """A run status never seen in the history before this week: a failure of a new kind."""
    before = {r.get("status") for r in ends(history)}
    out = []
    for r in ends(recent(runs, now)):
        status = r.get("status") or "none"
        if status not in KNOWN_STATUSES and status not in before:
            out.append((f"failure:{status}", "builder", f"New kind of failure: {status}",
                        f"First seen in {r['run_id']} ({r['role']}, item #{r.get('item')}).", None))
            before.add(status)
    return out


def cost_per_item(runs, now, closed_at):
    """Cost per finished item, the last 24 hours against the 7 days before them: worse than double, by
    over $2. Spend and closures are counted over the same datetime windows."""
    day = now - timedelta(days=1)
    spend = lambda since, until: sum(r.get("cost_usd") or 0 for r in ends(runs) if since <= datetime.fromisoformat(r["at"]) < until)
    closed = lambda since, until: sum(since <= at < until for at in closed_at)
    last, last_items = spend(day, now), closed(day, now) or 1
    base, base_items = spend(now - timedelta(days=8), day), closed(now - timedelta(days=8), day)
    if not base_items or not base:
        return []
    today, usual = last / last_items, base / base_items
    if today > 2 * usual and today - usual > 2:
        return [(f"cost:{now.date()}", "builder", "Cost per finished item has more than doubled",
                 f"${today:.2f} per item in the last day against ${usual:.2f} over the week before.", None)]
    return []


def watch_list(body, metrics):
    """Watch-list rows whose signal names a measured metric and whose threshold it crosses."""
    out = []
    for line in (body or "").splitlines():
        m = WATCH_ROW.match(line.strip())
        if not m or m[2] not in metrics:
            continue
        risk, metric, op, threshold = m[1], m[2], m[3], float(m[4])
        value = metrics[metric]
        crossed = {">": value > threshold, ">=": value >= threshold, "<": value < threshold, "<=": value <= threshold}[op]
        if crossed:
            out.append((f"watch:{metric}:{op}{threshold}", "coordinator", f"Watch-list risk past its threshold: {risk}",
                        f"`{metric}` is {value}, threshold {op} {threshold}.", None))
    return out


def metrics(runs, now, owner_wait_hours):
    day = [r for r in ends(runs) if datetime.fromisoformat(r["at"]) >= now - timedelta(days=1)]
    return {
        "failed_runs_per_day": sum(r.get("status") != FINISHED for r in day),
        "denials_per_day": sum(r.get("denials") or 0 for r in day),
        "cost_per_day_usd": round(sum(r.get("cost_usd") or 0 for r in day), 2),
        "owner_wait_hours": owner_wait_hours,
    }


def protection_breaches(live, expected):
    """Branch protection on main against the recorded settings: any difference is a rule breach."""
    have = {
        "checks": [(c.get("context"), c.get("app_id")) for c in (live.get("required_status_checks") or {}).get("checks", [])],
        "strict": (live.get("required_status_checks") or {}).get("strict"),
        "enforce_admins": (live.get("enforce_admins") or {}).get("enabled"),
        "required_approvals": (live.get("required_pull_request_reviews") or {}).get("required_approving_review_count"),
        "dismiss_stale_reviews": (live.get("required_pull_request_reviews") or {}).get("dismiss_stale_reviews"),
        "require_code_owner_reviews": (live.get("required_pull_request_reviews") or {}).get("require_code_owner_reviews"),
        "allow_force_pushes": (live.get("allow_force_pushes") or {}).get("enabled"),
        "allow_deletions": (live.get("allow_deletions") or {}).get("enabled"),
    }
    want = dict(expected)
    want["checks"] = [(expected["required_check"], expected["required_check_app_id"])]
    want.pop("required_check"), want.pop("required_check_app_id")
    diffs = [f"{k}: is {have.get(k)!r}, recorded {v!r}" for k, v in want.items() if have.get(k) != v]
    if not diffs:
        return []
    return [("breach:protection:" + ";".join(sorted(diffs)), "owner", "Rule breach: branch protection on main changed",
             "Live settings differ from system/identity.toml [branch_protection] (system/conventions.md):\n- " + "\n- ".join(diffs)
             + "\n\nThe kill switch is thrown until the Owner answers (blueprint §4.5).", None)]
