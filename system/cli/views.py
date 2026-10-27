"""The two faces of the same data: the CLI's text and the dashboard's HTML (blueprint §7)."""

import html
from datetime import datetime
from zoneinfo import ZoneInfo

# The system's mark: a loop (the build–review–merge cycle) around a dot (the Owner), on blue.
ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="auto_research">'
    '<rect width="64" height="64" rx="14" fill="#1f6feb"/>'
    '<path d="M46 23a16 16 0 1 0 3 12" fill="none" stroke="#fff" stroke-width="5.5" stroke-linecap="round"/>'
    '<path d="M42.5 11.5 52 21.5l-13 3.2z" fill="#fff"/>'
    '<circle cx="32" cy="32" r="5.5" fill="#fff"/></svg>'
)


def local(iso, tz):
    if not iso:
        return "never"
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(tz)).strftime("%a %H:%M %Z")


def pct(x):
    return "n/a" if x is None else f"{x:.0%}"


def text(d):
    tz, u = d["time_zone"], d["usage"]
    lines = [f"auto_research — {local(d['now'], tz)}", ""]
    lines.append("KILL SWITCH: THROWN — no runs start (" + d["pause_reason"] + ")" if d["paused"] else "Kill switch: off")
    lines += ["", f"Waiting on you ({sum(not x['answered'] for x in d['decisions'])} decisions):"]
    for x in d["decisions"]:
        state = "answered, waiting for the " + (x["asker"] or "asker") if x["answered"] else f"asked by the {x['asker'] or '?'}"
        lines.append(f"  #{x['number']} {x['title']} — {state}")
    for p in d["prs"]:
        if p["waiting_on"].startswith("you"):
            lines.append(f"  PR #{p['number']} {p['title']} — {p['waiting_on']}")
    lines += ["", "Pull requests:"] + ([f"  #{p['number']} [{p['gate']}] {p['title']} ({p['author']}) — {p['waiting_on']}" for p in d["prs"]] or ["  none"])
    lines += ["", "Work order:"] + [
        f"  {w['order']}. #{w['number']} {w['title']}{' (parallel-safe)' if w['parallel_safe'] else ''}" for w in d["work_order"]
    ]
    lines += ["", f"Usage: {u['points_used']} of {u['weekly_points']} weekly points (warn at {u['warn_at']}); "
                  f"${u['cost_window']} this window",
              f"  account: 5-hour {pct(u['five_hour'])} (new runs hold at {pct(u['hold_at'])}), "
              f"7-day {pct(u['seven_day'])} (pause at {pct(u['pause_at'])})",
              "  runs today: " + ", ".join(f"{r} {v['today']}/{v['cap']}" for r, v in u["per_role"].items()),
              f"  active runs: {len(u['active'])} of {u['max_runs']}" + "".join(
                  f"\n    {a['role']} on #{a['item']} since {local(a['since'], tz)}" for a in u["active"])]
    r = d["runner"]
    if r is not None:
        lines += ["", "Runner: " + ("matches main" if r["ok"] else "PROBLEMS")] + [f"  - {p}" for p in r["problems"]]
    lines += [f"Dispatcher heartbeat: {local(d['last_heartbeat'], tz)}; last run: "
              + (f"{d['last_run']['run_id']} ({d['last_run']['event']})" if d["last_run"] else "none")]
    lines += ["", "Merges from your account (recognise each one):"] + ([
        f"  #{m['number']} {m['title']} — {local(m['mergedAt'], tz)}" for m in d["merges"]] or ["  none"])
    return "\n".join(lines)


CSS = """
:root{--bg:#f6f8fa;--panel:#fff;--ink:#1f2328;--muted:#59636e;--line:#d1d9e0;--accent:#1f6feb;--warn:#9a6700;--bad:#cf222e;--good:#1a7f37}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--panel:#151b23;--ink:#e6edf3;--muted:#9198a1;--line:#3d444d;--accent:#4493f8;--warn:#d29922;--bad:#f85149;--good:#3fb950}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{display:flex;align-items:center;gap:12px;padding:16px 24px;border-bottom:1px solid var(--line);background:var(--panel)}
header svg{width:36px;height:36px;flex:none}header h1{font-size:18px;margin:0}header .when{margin-left:auto;color:var(--muted);font-size:13px}
main{max-width:1100px;margin:0 auto;padding:16px;display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}section.wide{grid-column:1/-1}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:0 0 8px}
ul{list-style:none;margin:0;padding:0}li{padding:6px 0;border-top:1px solid var(--line)}li:first-child{border-top:0}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.muted{color:var(--muted)}
.pill{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted);margin-left:6px}
.you{border-color:var(--warn);color:var(--warn)}.bad{border-color:var(--bad);color:var(--bad)}.good{border-color:var(--good);color:var(--good)}
.banner{grid-column:1/-1;padding:12px 16px;border-radius:10px;font-weight:600}.banner.bad{background:color-mix(in srgb,var(--bad) 12%,transparent)}
.bar{height:8px;border-radius:4px;background:var(--line);overflow:hidden;margin:4px 0 10px}.bar>div{height:100%;background:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:14px}td{padding:4px 0;border-top:1px solid var(--line)}td:last-child{text-align:right}
@media (max-width:600px){header{padding:12px 16px}main{padding:12px 16px}}
"""


def link(d, n, kind="issues"):
    return f'<a href="https://github.com/{d["repo"]}/{kind}/{n}">#{n}</a>'


def html_page(d):
    e, tz, u = html.escape, d["time_zone"], d["usage"]
    parts = []
    if d["paused"]:
        parts.append(f'<div class="banner bad">Kill switch thrown: no runs start. {e(d["pause_reason"])}</div>')
    yours = [f'<li>{link(d, x["number"])} {e(x["title"])}<span class="pill {"good" if x["answered"] else "you"}">'
             f'{"answered" if x["answered"] else "asked by the " + e(x["asker"] or "?")}</span>'
             f'<div class="muted">{e(x["question"][:400])}</div></li>' for x in d["decisions"]]
    yours += [f'<li>{link(d, p["number"], "pull")} {e(p["title"])}<span class="pill you">{e(p["waiting_on"])}</span></li>'
              for p in d["prs"] if p["waiting_on"].startswith("you")]
    parts.append(f'<section class="wide"><h2>Waiting on you</h2><ul>{"".join(yours) or "<li class=muted>Nothing.</li>"}</ul></section>')
    prs = [f'<li>{link(d, p["number"], "pull")} {e(p["title"])}<span class="pill {"bad" if p["gate"] == "FAILURE" else "good" if p["gate"] == "SUCCESS" else ""}">'
           f'gate {e(p["gate"].lower())}</span><span class="pill">{e(p["author"])}</span>'
           f'<div class="muted">waiting on {e(p["waiting_on"])}</div></li>' for p in d["prs"]]
    parts.append(f'<section><h2>Pull requests</h2><ul>{"".join(prs) or "<li class=muted>None open.</li>"}</ul></section>')
    wo = [f'<li>{w["order"]}. {link(d, w["number"])} {e(w["title"])}'
          f'{"<span class=pill>parallel-safe</span>" if w["parallel_safe"] else ""}</li>' for w in d["work_order"]]
    parts.append(f'<section><h2>Work order</h2><ul>{"".join(wo) or "<li class=muted>Empty.</li>"}</ul></section>')
    share = min(100, 100 * u["points_used"] / u["weekly_points"]) if u["weekly_points"] else 0
    rows = "".join(f'<tr><td>{e(r)}</td><td>{v["today"]} / {v["cap"]} runs today · ${v["cost"]}</td></tr>' for r, v in u["per_role"].items())
    active = "".join(f'<li>{e(a["role"])} on {link(d, a["item"]) if a["item"] else "—"} since {local(a["since"], tz)}</li>' for a in u["active"])
    parts.append(
        f'<section><h2>Usage against the caps</h2>'
        f'<div>System: <b>{u["points_used"]}</b> of {u["weekly_points"]} weekly points (warn at {u["warn_at"]}) · ${u["cost_window"]} this window</div>'
        f'<div class="bar"><div style="width:{share:.1f}%"></div></div>'
        f'<div class="muted">Account: 5-hour {pct(u["five_hour"])} (hold at {pct(u["hold_at"])}) · 7-day {pct(u["seven_day"])} (pause at {pct(u["pause_at"])})</div>'
        f'<table>{rows}</table><h2 style="margin-top:12px">Active runs ({len(u["active"])} of {u["max_runs"]})</h2>'
        f'<ul>{active or "<li class=muted>None.</li>"}</ul></section>')
    r = d["runner"]
    health = []
    if r is not None:
        health.append(f'<li>Runner <span class="pill {"good" if r["ok"] else "bad"}">{"matches main" if r["ok"] else "problems"}</span>'
                      + "".join(f'<div class="muted">{e(p)}</div>' for p in r["problems"]) + "</li>")
    health.append(f'<li>Dispatcher heartbeat <span class="pill">{local(d["last_heartbeat"], tz)}</span></li>')
    lr = d["last_run"]
    health.append(f'<li>Last run <span class="pill">{e(lr["run_id"]) + " · " + e(lr["event"]) if lr else "none"}</span></li>')
    parts.append(f'<section><h2>Health</h2><ul>{"".join(health)}</ul></section>')
    merges = [f'<li>{link(d, m["number"], "pull")} {e(m["title"])} <span class="muted">· {local(m["mergedAt"], tz)}</span></li>' for m in d["merges"]]
    parts.append(f'<section><h2>Merges from your account</h2><ul>{"".join(merges) or "<li class=muted>None.</li>"}</ul></section>')
    items = [f'<li>{link(d, i["number"])} {e(i["title"])}' + "".join(f'<span class="pill">{e(x)}</span>' for x in i["labels"]) + "</li>" for i in d["items"]]
    parts.append(f'<section class="wide"><h2>Open items</h2><ul>{"".join(items) or "<li class=muted>None.</li>"}</ul></section>')
    favicon_data = "data:image/svg+xml," + ICON_SVG.replace('"', "'").replace("#", "%23")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="60">'
        '<title>auto_research</title>'
        f'<link rel="icon" type="image/svg+xml" href="/favicon.svg"><link rel="alternate icon" href="{favicon_data}">'
        f'<link rel="apple-touch-icon" href="/favicon.svg"><style>{CSS}</style></head><body>'
        f'<header>{ICON_SVG}<h1>auto_research</h1><span class="when">{local(d["now"], tz)} · refreshes every minute</span></header>'
        f'<main>{"".join(parts)}</main></body></html>'
    )
