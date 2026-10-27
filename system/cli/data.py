"""What the Owner's dashboard and CLI show (blueprint §7). Everything is read from GitHub, the
dispatcher/state branch and this Mac's runner; nothing is kept. Reads only.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from system.dispatcher import github_state, killswitch, notify, plan, state, usage
from system.tools import post, runner

MERGES = """query($owner: String!, $name: String!, $after: String) { repository(owner: $owner, name: $name) {
  pullRequests(states: MERGED, first: 100, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
    pageInfo { hasNextPage endCursor } nodes { number title mergedAt url mergedBy { login } } } } }"""


def author(pr, identity):
    """A role, "owner" for the Owner's own PRs, or an outsider's login: github_state gives no role to either."""
    if pr["author_role"]:
        return pr["author_role"]
    return "owner" if pr["author"] == identity["owner_login"] else f"outsider {pr['author'] or '(deleted account)'}"


def waiting_on(pr, identity):
    """Who or what a PR waits on, in the merge flow of blueprint §4.4."""
    bot = identity["reviewer_app"]["bot_login"]
    if pr["draft"]:
        return "draft"
    if not pr["author_role"] and pr["author"] != identity["owner_login"]:
        return "you: triage (outside contributor)"
    if pr["gate"] is None:
        return "CI"
    app = plan.latest_review(pr, bot)
    if pr["gate"] == "FAILURE":
        return "author: red gate"
    if app and app["state"] == "CHANGES_REQUESTED" and app["commit"] == pr["head"]:
        return "author: changes requested"
    if pr["merge_state"] == "DIRTY":
        return "author: conflict"
    if not pr["author_role"]:
        return "you: approve (second account), then merge"
    if not (app and app["state"] == "APPROVED" and app["commit"] == pr["head"]):
        return "Reviewer"
    if pr["merge_state"] == "BEHIND":
        return "Dispatcher: refresh"
    if pr["review_decision"] != "APPROVED":
        return "you: approve (second account)"
    if pr["auto_merge"]:
        return "auto-merge"
    return "you: merge" if pr["protected"] else "Dispatcher: auto-merge"


def decisions(gh, snap, identity):
    """Every item held for the Owner, with its latest question and whether it's answered."""
    out = []
    items = snap["issues"] + [{"number": p["number"], "title": p["title"], "labels": p["labels"]} for p in snap["prs"]]
    for item in items:
        if "needs-decision:owner" not in item["labels"]:
            continue
        comments = gh.api("GET", f"repos/{gh.repo}/issues/{item['number']}/comments?per_page=100", paginate=True)
        found = notify.latest_ask(comments, identity, post.MARKER)
        entry = {"number": item["number"], "title": item["title"], "asker": None, "question": "(no question found)",
                 "answered": False, "url": f"https://github.com/{gh.repo}/issues/{item['number']}"}
        if found:
            ask, role, answered = found
            entry.update(asker=role, answered=answered, url=ask["html_url"],
                         question=post.MARKER.sub("", ask["body"], count=1).replace(notify.ASK, "").strip())
        out.append(entry)
    return out


def usage_view(caps, runs, now, time_zone):
    """The system's use of every cap it can count (blueprint §5)."""
    tz = ZoneInfo(time_zone)
    window = usage.window_start(runs, now)
    ended = [r for r in runs if r["event"] == "end" and datetime.fromisoformat(r["at"]) >= window]
    today = now.astimezone(tz).date()
    per_role = {}
    for role, c in caps["roles"].items():
        started = sum(r["event"] == "start" and r["role"] == role and datetime.fromisoformat(r["at"]).astimezone(tz).date() == today
                      for r in runs)
        per_role[role] = {"today": started, "cap": c["max_runs_per_day"],
                          "cost": round(sum(r.get("cost_usd") or 0 for r in ended if r["role"] == role), 2)}
    reading = usage.latest_reading(runs)
    return {
        "points_used": round(usage.points_used(caps, runs, now), 2), "weekly_points": caps["claude"]["weekly_points"],
        "warn_at": caps["claude"]["warn_at_points"], "window_start": window.isoformat(),
        "cost_window": round(sum(r.get("cost_usd") or 0 for r in ended), 2),
        "five_hour": reading.get("five_hour") if reading else None, "seven_day": reading.get("seven_day") if reading else None,
        "hold_at": caps["claude"]["hold_new_runs_at_five_hour"], "pause_at": caps["claude"]["pause_at_seven_day"],
        "active": [{"run_id": r["run_id"], "role": r["role"], "item": r.get("item"), "since": r["at"]} for r in usage.active(runs)],
        "per_role": per_role, "max_runs": caps["concurrency"]["max_runs"],
    }


def merges_by_owner_account(gh, identity, limit=None, max_pages=20):
    """Every merge made from the Owner's account, newest first, for the Owner to recognise (blueprint §2's
    accepted risk). All merged PRs are paged through: a window of recent ones hid older merges from
    the account behind newer ones from the second account."""
    owner, name = identity["repo"].split("/")
    mine, after = [], None
    for _ in range(max_pages):
        page = gh.graphql(MERGES, owner=owner, name=name, after=after)["repository"]["pullRequests"]
        mine += [n for n in page["nodes"] if n and (n.get("mergedBy") or {}).get("login") == identity["owner_login"]]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    mine.sort(key=lambda n: n["mergedAt"], reverse=True)
    return mine[:limit] if limit else mine


def runner_view(config):
    """The runner check, as the builder's daily run makes it. Errors are shown, not raised."""
    try:
        try:
            runner.fetch_main(config)  # under the shared checkout's lock, so it never fails a launcher's fetch
            stale = []
        except Exception as e:
            stale = [f"couldn't fetch origin/main, so main below may be stale: {e}"]
        applied, main = runner.commits(config)
        found = stale + runner.problems(config, applied_commit=applied, main_commit=main, login=runner.claude_login(config),
                                        cli=runner.cli_link(config))
        return {"ok": not found, "problems": found, "applied": (applied or "")[:7], "main": (main or "")[:7]}
    except Exception as e:  # the dashboard must still render
        return {"ok": False, "problems": [f"runner check failed: {e}"], "applied": "", "main": ""}


def collect(config, gh=None, store=None, now=None, with_runner=True):
    identity = config["identity"]
    gh = gh or post.GitHub(identity["repo"])
    store = store or state.Store(runner.home(config) / "state", f"https://github.com/{identity['repo']}.git")
    now = now or datetime.now(timezone.utc)
    paused, why = killswitch.paused(gh)
    runs, heartbeats = store.read("runs"), store.read("heartbeats")
    snap = github_state.snapshot(gh, identity)
    by_number = {i["number"]: i for i in snap["issues"]}
    return {
        "now": now.isoformat(), "repo": identity["repo"], "time_zone": identity["time_zone"],
        "paused": paused, "pause_reason": why,
        "decisions": decisions(gh, snap, identity),
        "prs": [{"number": p["number"], "title": p["title"], "author": author(p, identity),
                 "gate": p["gate"] or "pending", "protected": p["protected"], "waiting_on": waiting_on(p, identity)}
                for p in snap["prs"]],
        "work_order": [{"order": i + 1, "number": n, "parallel_safe": safe,
                        "title": by_number.get(n, {}).get("title", "(closed)"),
                        "labels": sorted(by_number.get(n, {}).get("labels", []))}
                       for i, (n, safe) in enumerate(snap["work_order"])],
        "items": [{"number": i["number"], "title": i["title"], "labels": sorted(i["labels"])}
                  for i in snap["issues"] if not any(x.startswith("record:") for x in i["labels"])],
        "usage": usage_view(config["caps"], runs, now, identity["time_zone"]),
        "last_heartbeat": heartbeats[-1]["at"] if heartbeats else None,
        "last_run": runs[-1] if runs else None,
        "runner": runner_view(config) if with_runner else None,
        "merges": merges_by_owner_account(gh, identity),
    }
