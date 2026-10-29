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
nav{display:flex;gap:4px;padding:0 24px;background:var(--panel);border-bottom:1px solid var(--line)}
nav a{padding:10px 14px;color:var(--muted);border-bottom:2px solid transparent;font-weight:600}
nav a[aria-current=page]{color:var(--ink);border-bottom-color:var(--accent)}nav a:hover{text-decoration:none;color:var(--ink)}
.grid{overflow-x:auto}.grid table td,.grid table th{padding:6px 10px 6px 0;text-align:left;vertical-align:top;border-top:1px solid var(--line)}
.grid table th{font-size:12px;color:var(--muted);font-weight:600;border-top:0}.grid table td.num{text-align:right;white-space:nowrap}
.grid table td.when{white-space:nowrap}
@media (max-width:600px){header{padding:12px 16px}main{padding:12px 16px}nav{padding:0 12px}}
"""

# Progress is the default tab, then Roles (#246). Overview is the page / showed before.
TABS = (("/", "Progress"), ("/roles", "Roles"), ("/overview", "Overview"))


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
    return frame(d, "/overview", "".join(parts))


def frame(d, here, body):
    """A dashboard page: the header, the tabs with the current one marked, and its sections. Tabs may repeat
    each other's facts (#241)."""
    favicon_data = "data:image/svg+xml," + ICON_SVG.replace('"', "'").replace("#", "%23")
    tabs = "".join(f'<a href="{path}"{" aria-current=page" if path == here else ""}>{name}</a>' for path, name in TABS)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="60">'
        f'<title>auto_research · {dict(TABS)[here]}</title>'
        f'<link rel="icon" type="image/svg+xml" href="/favicon.svg"><link rel="alternate icon" href="{favicon_data}">'
        f'<link rel="apple-touch-icon" href="/favicon.svg"><style>{CSS}</style></head><body>'
        f'<header>{ICON_SVG}<h1>auto_research</h1><span class="when">{local(d["now"], tz=d["time_zone"])} · refreshes every minute</span></header>'
        f'<nav aria-label="Tabs">{tabs}</nav><main>{body}</main></body></html>'
    )


def met_line(p):
    """"Met: n of 9 features, n of 10 requirements", from the Progress table."""
    count = lambda k: (sum(r["status"] == "met" for r in p["rows"] if r["id"][0] == k), sum(r["id"][0] == k for r in p["rows"]))
    (f, fn), (q, qn) = count("F"), count("Q")
    return f"Met: {f} of {fn} features, {q} of {qn} requirements"


STATUS_NOTE = ("Nothing records each criterion's check yet (#239), so status comes from the items: a criterion is "
               "built when every feature item naming it is closed. Built isn't verified, so nothing shows as met. "
               "Start is the first run on its items; End is when its last criterion was built.")


def when(t, tz):
    return t.astimezone(ZoneInfo(tz)).strftime("%m-%d %H:%M") if t else "—"


def started(r, tz):
    return when(r["started"], tz)


def progress_text(p):
    tz = p["time_zone"]
    lines = [f"Progress — {local(p['now'], tz)}", met_line(p), STATUS_NOTE, ""]
    for r in p["rows"]:
        lines.append(f"  {r['id']:<4} {r['name']}")
        lines.append(f"       {', '.join(r['criteria']) or 'no criteria'} · {r['status']} · started {started(r, tz)}"
                     f" · ended {when(r['ended'], tz)} · due {r['due'] or '—'} · ${r['cost']:.2f}")
    lines += ["", f"Cost since {started({'started': p['since']}, tz)}: ${p['total']:.2f}"]
    lines += [f"  {k}: ${v:.2f}" for k, v in p["costs"]]
    return "\n".join(lines)


def progress_page(p):
    e, tz = html.escape, p["time_zone"]
    pill = lambda r: ("bad" if r["status"].startswith("no criterion") else "good" if r["status"] == "met"
                      else "you" if r["status"].startswith(("in progress", "built")) else "")
    rows = "".join(
        f'<tr><td><b>{r["id"]}</b></td><td>{e(r["name"])}</td><td class="muted">{e(", ".join(r["criteria"]) or "none")}</td>'
        f'<td><span class="pill {pill(r)}">{e(r["status"])}</span></td><td class="when">{started(r, tz)}</td>'
        f'<td class="when">{when(r["ended"], tz)}</td>'
        f'<td class="when">{e(r["due"] or "—")}</td><td class="num">${r["cost"]:.2f}</td></tr>' for r in p["rows"])
    head = "<tr><th>ID</th><th>Feature or requirement</th><th>Criteria</th><th>Status</th><th>Start</th><th>End</th><th>Due</th><th>Cost</th></tr>"
    split = "".join(f'<tr><td>{e(k)}</td><td>${v:.2f}</td></tr>' for k, v in p["costs"])
    body = (f'<section class="wide"><h2>{e(met_line(p))}</h2><p class="muted">{e(STATUS_NOTE)}</p>'
            f'<div class="grid"><table>{head}{rows}</table></div></section>'
            f'<section><h2>Cost since {started({"started": p["since"]}, tz)}: ${p["total"]:.2f}</h2><table>{split}</table>'
            f'<p class="muted">API list prices, from each run\'s own report. The Max plan has no charge per run.</p></section>')
    return frame(p, "/", body)


def role_lines(x, tz):
    """(state, what) for a role: what it's working on, or since when it has been idle."""
    ref = lambda i: f"#{i['number']} {i['title']}" if i["number"] else i["title"]
    if x["working"]:
        return "working", [f"{ref(w)}, since {local(w['since'], tz)} ({w['run_id']})" for w in x["working"]]
    last = x["last"]
    return "idle", [f"since {local(last['at'], tz)}, after {ref(last)} ({last['status'] or 'ended'})" if last else "no run yet"]


def runs_today(x, d):
    return f"{x['today']}, daily cap lifted" if d.get("caps_lifted_until") else f"{x['today']} of {x['cap']}"


def roles_text(d):
    tz = d["time_zone"]
    lines = [f"Roles — {local(d['now'], tz)}", ""]
    for x in d["roles"]:
        state, what = role_lines(x, tz)
        lines.append(f"  {x['role']:<12} {state:<8} {what[0]}" + "".join(f"\n  {'':<12} {'':<8} {w}" for w in what[1:]))
        lines.append(f"  {'':<12} {'':<8} runs today: {runs_today(x, d)}")
    return "\n".join(lines)


def roles_page(d):
    e, tz = html.escape, d["time_zone"]
    link_to = lambda i: (f'<a href="https://github.com/{d["repo"]}/{i["kind"]}/{i["number"]}">#{i["number"]}</a> {e(i["title"])}'
                         if i["number"] else e(i["title"]))
    rows = []
    for x in d["roles"]:
        if x["working"]:
            what = "".join(f'<div>{link_to(w)}</div><div class="muted">since {local(w["since"], tz)} · {e(w["run_id"])}</div>'
                           for w in x["working"])
        elif x["last"]:
            what = (f'<div class="muted">since {local(x["last"]["at"], tz)}, after {link_to(x["last"])} '
                    f'({e(x["last"]["status"] or "ended")})</div>')
        else:
            what = '<div class="muted">no run yet</div>'
        state = "working" if x["working"] else "idle"
        rows.append(f'<tr><td><b>{e(x["role"])}</b></td><td><span class="pill {"good" if x["working"] else ""}">{state}</span></td>'
                    f'<td>{what}</td><td class="num">{e(runs_today(x, d))}</td></tr>')
    head = "<tr><th>Role</th><th>State</th><th>Working on</th><th>Runs today</th></tr>"
    busy = sum(bool(x["working"]) for x in d["roles"])
    lifted = (f'<p class="muted">Daily run caps are lifted until {local(d["caps_lifted_until"], tz)}, by your override.</p>'
              if d.get("caps_lifted_until") else "")
    body = (f'<section class="wide"><h2>{busy} of {len(d["roles"])} roles working</h2>{lifted}'
            f'<div class="grid"><table>{head}{"".join(rows)}</table></div></section>')
    return frame(d, "/roles", body)
