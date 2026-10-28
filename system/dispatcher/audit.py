"""The product and system audits (blueprint §4.5): reports for the Owner, daily at 07:30 (runner.toml) and on
demand (`arsys audit`, or `audit` in the Discord channel).

Each report is a new comment on its record issue (`record:audit-product`, `record:audit-system`) and a Discord
notice with its headline and a link. The system audit is the daily pass: it also raises each new finding once.
The reports state facts; the Coordinator and the builder judge them. Pure functions here; dispatch.py gathers
the facts and posts. Standard library only.
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KINDS = ("product", "system")
RECORD = {"product": ("record:audit-product", "Audit: product"), "system": ("record:audit-system", "Audit: system")}
REQUEST = re.compile(r"^audit(?:\s+(product|system|all))?\s*[.!]?$", re.I)
BUILDER_DAILY = "Daily run"
GOAL_ID = re.compile(r"^Goal (G\d+)\b")


def requested(text):
    """The audits a chat message asks for (`audit`, `audit product`, `audit system`), or None."""
    m = REQUEST.match((text or "").strip())
    if not m:
        return None
    return KINDS if (m[1] or "all").lower() == "all" else (m[1].lower(),)


def local(t, zone):
    return t.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M %Z")


def deadlines(requirements):
    """{goal ID: deadline text} from the requirements' Goals table: rows not struck through, G0 left out."""
    section = requirements.split("## 2. Goals", 1)[1].split("\n## ", 1)[0] if "## 2. Goals" in requirements else ""
    out = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"G[1-9]\d*", cells[0]):
            out[cells[0]] = cells[3] or "none"
    return out


def item_state(labels, in_review):
    """Where an open item stands, by its labels: the first that applies."""
    for level in ("owner", "coordinator", "architect", "builder"):
        if f"needs-decision:{level}" in labels:
            return f"waiting on the {'Owner' if level == 'owner' else level}"
    if "state:claimed" in labels:
        return "in progress"
    if in_review:
        return "in review"
    if "state:blocked" in labels:
        return "blocked"
    if "state:ready" in labels:
        return "ready"
    return "in triage"


def hours_ago(at, now):
    return f"{(now - at).total_seconds() / 3600:.1f} h"


def product_report(f, now, zone):
    """(headline, markdown). f: goals [{number, title}], deadlines {ID: text}, milestones [{title, open, closed}],
    items [{number, title, milestone, state}], questions [(n, title, asked datetime)], approvals [(n, title)],
    merged [(n, title)] since the last report, gate (text), watch (n, latest note) or None."""
    lines, heads = [f"## Product audit, {local(now, zone)}", ""], []
    for g in f["goals"]:
        gid = (GOAL_ID.match(g["title"]) or [None, None])[1]
        stones = [m for m in f["milestones"] if gid and (m["title"] == gid or m["title"].startswith(gid + "."))]
        done, total = sum(m["closed"] for m in stones), sum(m["open"] + m["closed"] for m in stones)
        due = f["deadlines"].get(gid, "not in the requirements")
        heads.append(f"{gid} {done}/{total} items done")
        lines += [f"### {g['title']} (#{g['number']})", f"- **Deadline:** {due}", f"- **Items:** {done} of {total} done"]
        for m in sorted(stones, key=lambda m: m["title"]):
            mine = [i for i in f["items"] if i["milestone"] == m["title"]]
            counts = {}
            for i in mine:
                counts[i["state"]] = counts.get(i["state"], 0) + 1
            spread = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no open items"
            lines.append(f"  - `{m['title']}`: {m['closed']} done, {m['open']} open ({spread})")
        held = [i for i in f["items"] if i["milestone"] in {m["title"] for m in stones} and i["state"].startswith("waiting on")]
        for i in held:
            lines.append(f"  - #{i['number']} {i['state']}: {i['title']}")
        lines.append("")
    waiting = len(f["questions"]) + len(f["approvals"])
    lines.append("### Waiting on the Owner")
    if not waiting:
        lines.append("Nothing.")
    for n, title, asked in f["questions"]:
        lines.append(f"- **Question** on #{n}, asked {hours_ago(asked, now)} ago: {title}")
    for n, title in f["approvals"]:
        lines.append(f"- **Approval** of PR #{n} (the Reviewer approved it): {title}")
    lines += ["", "### Since the last product audit"]
    lines += [f"- Merged #{n}: {title}" for n, title in f["merged"]] or ["- Nothing merged."]
    lines += ["", "### Health", f"- {f['gate']}"]
    if f["watch"]:
        n, note = f["watch"]
        lines.append(f"- Watch list (#{n}), latest note: {note}")
    headline = "; ".join(heads + [f"{waiting} waiting on you" if waiting else "nothing waiting on you"])
    return headline, "\n".join(lines)


def system_report(f, now, zone):
    """(headline, markdown). f: findings [(key, level, title, new)], open_system [(n, title, sev)], flow (text),
    paused (thrown, why), runs {role: (started, not finished)} in the last 12 hours, usage (warnings, reasons),
    last_reconcile datetime or None, builder_daily datetime or None."""
    new = [x for x in f["findings"] if x[3]]
    lines = [f"## System audit, {local(now, zone)}", "", "### Findings"]
    if not f["findings"]:
        lines.append("None.")
    for _, level, title, fresh in f["findings"]:
        lines.append(f"- {'**New:** ' if fresh else 'Raised before, still holds: '}{title} → {level}")
    lines += ["", f"### Open system items ({len(f['open_system'])})"]
    lines += [f"- #{n} {sev}: {title}" for n, title, sev in f["open_system"] if sev in ("sev:1", "sev:2")] or ["- None at sev:1 or sev:2."]
    thrown, why = f["paused"]
    lines += ["", "### Health",
              f"- **Kill switch:** {'thrown' if thrown else 'not thrown'} ({why})",
              f"- **Last hourly reconcile:** {local(f['last_reconcile'], zone) if f['last_reconcile'] else 'none recorded'}",
              f"- **Builder's daily run, last started:** {local(f['builder_daily'], zone) if f['builder_daily'] else 'none recorded'}",
              "- **Runs in the last 12 hours:** " + (", ".join(
                  f"{role} {started}" + (f" ({bad} not finished)" if bad else "") for role, (started, bad) in sorted(f["runs"].items()))
                  or "none")]
    warnings, reasons = f["usage"]
    lines += [f"- **Usage:** {w}" for w in warnings + reasons] or ["- **Usage:** within the caps"]
    lines += ["", "### Flow", f["flow"]]
    headline = (f"{len(new)} new finding{'s' * (len(new) != 1)}, {len(f['findings']) - len(new)} still open; "
                f"{len(f['open_system'])} open system items" + ("; KILL SWITCH THROWN" if thrown else ""))
    return headline, "\n".join(lines)


def runs_summary(runs, now, hours=12):
    """{role: (runs started, of them ended other than finished)} for runs started in the last `hours`."""
    since = now - timedelta(hours=hours)
    started = {r["run_id"]: r["role"] for r in runs if r["event"] == "start" and datetime.fromisoformat(r["at"]) >= since}
    out = {}
    for run_id, role in started.items():
        n, bad = out.get(role, (0, 0))
        out[role] = (n + 1, bad)
    for r in runs:
        if r["event"] == "end" and r["run_id"] in started and r.get("status") != "finished":
            n, bad = out[started[r["run_id"]]]
            out[started[r["run_id"]]] = (n, bad + 1)
    return out


def builder_daily(runs):
    """When the builder's scheduled daily run last started, or None."""
    starts = [datetime.fromisoformat(r["at"]) for r in runs
              if r["event"] == "start" and r["role"] == "builder" and (r.get("task") or "").startswith(BUILDER_DAILY)]
    return max(starts, default=None)


def missed_builder_daily(runs, now, zone):
    """A finding when the builder's daily run (08:00) hasn't started for more than a day, once runs have history:
    a skipped run isn't retried (it doesn't start while another builder run is active)."""
    last, first = builder_daily(runs), min((datetime.fromisoformat(r["at"]) for r in runs), default=None)
    if first is None or now - first < timedelta(hours=26) or (last and now - last < timedelta(hours=26)):
        return []
    day = now.astimezone(ZoneInfo(zone)).date().isoformat()
    since = f"since {local(last, zone)}" if last else "ever"
    return [(f"builder-daily-missed:{day}", "builder", "The builder's daily run hasn't started for over a day",
             f"Last start of a builder run with the task \"{BUILDER_DAILY}…\": {since}. The 08:00 job doesn't start while another "
             f"builder run is active, and isn't retried (builder-daily.log). Run it now, and make a skipped run start later.", None)]
