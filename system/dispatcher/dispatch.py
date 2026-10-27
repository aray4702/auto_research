#!/usr/bin/env python3
"""The Dispatcher (blueprint §2, §4): everything mechanical, no judgment calls.

  dispatch.py poll [--dry-run]        every 2 minutes
  dispatch.py reconcile [--dry-run]   every hour: a poll, plus expired claims and a heartbeat
  dispatch.py daily                   07:30: anomaly signals and the rule audit (blueprint §8 step 7)

Each pass: the kill switch; runs whose launcher died; the merge queue; new runs in dispatch order
within the caps; notices to the Owner. With --dry-run it prints what it would do and changes
nothing. Standard library only.
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from system.dispatcher import daily, github_state, killswitch, notify, plan, state, usage  # noqa: E402
from system.tools import post, runner  # noqa: E402


def article(word):
    return "an" if word[0] in "aeiou" else "a"


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def only_merges_since(gh, base, head):
    """True when every commit from base to head is a merge, so the PR's own changes are unchanged."""
    compare = gh.api("GET", f"repos/{gh.repo}/compare/{base}...{head}")
    return compare["status"] == "ahead" and all(len(c["parents"]) > 1 for c in compare["commits"])


def expired_claims(gh, items, active_items, now):
    out = []
    for n in items:
        if n in active_items:
            continue
        comments = gh.api("GET", f"repos/{gh.repo}/issues/{n}/comments?per_page=100", paginate=True)
        expiries = [post.CLAIM_EXPIRY.search(c.get("body") or "") for c in comments]
        expiries = [datetime.fromisoformat(m["at"]) for m in expiries if m]
        if not expiries or expiries[-1] <= now:
            out.append(n)
    return out


def spawn(config, role, item, task, logs):
    """Start run.py detached; it re-checks the kill switch and the caps, and claims the item."""
    cmd = [config["runner"]["runner"]["python"], "-I", str(ROOT / "system" / "dispatcher" / "run.py"), role, "--task", task]
    if item:
        cmd += ["--item", str(item), "--claim"]
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with open(logs / f"launcher-{role}-{stamp}.log", "ab") as log:
        subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def dispatch_pass(command, dry_run, now=None):
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    config = runner.load_all()
    identity, caps = config["identity"], config["caps"]
    gh = post.GitHub(identity["repo"])
    me = ("dispatcher", f"dispatcher-{now:%Y%m%dT%H%M%SZ}")
    lines = []
    say = lambda text: lines.append(("would " if dry_run else "") + text)

    thrown, why = killswitch.paused(gh)
    if thrown:
        return [f"paused: {why}"]
    store = state.Store(runner.home(config) / "state", f"https://github.com/{identity['repo']}.git")
    runs, notices = store.read("runs"), store.read("notices")

    # Runs whose launcher died without recording an end.
    for r in usage.active(runs):
        if r.get("pid") and not alive(r["pid"]):
            say(f"record {r['run_id']} as cut off" + (f" and release #{r['item']}" if r.get("item") else ""))
            if not dry_run:
                store.append("runs", {"run_id": r["run_id"], "role": r["role"], "item": r.get("item"), "event": "end",
                                      "at": now.isoformat(), "status": "cut-off"})
                if r.get("item"):
                    post.cmd_release(gh, me, r["item"])
    runs = store.read("runs") if not dry_run else runs
    active_items = {r.get("item") for r in usage.active(runs) if r.get("item")}

    # Closing events since the Coordinator's last run ended start it again (blueprint §4.1).
    ends = [datetime.fromisoformat(r['at']) for r in runs if r['event'] == 'end' and r['role'] == 'coordinator']
    snap = github_state.snapshot(gh, identity, closed_since=max(ends) if ends else now - timedelta(days=7))
    url = lambda n: f"https://github.com/{identity['repo']}/pull/{n}"
    wanted, renewed = [], set()

    # The merge queue: one PR at a time.
    for action, n, detail in plan.merge_plan(snap, identity):
        if action == "refresh":
            say(f"refresh PR #{n} from main ({detail})")
            if not dry_run:
                gh.api("PUT", f"repos/{gh.repo}/pulls/{n}/update-branch", {})
        elif action == "auto-merge":
            say(f"turn on auto-merge for PR #{n}")
            if not dry_run:
                subprocess.run(["gh", "pr", "merge", str(n), "--auto", "--merge", "--repo", gh.repo], check=True, capture_output=True)
        elif action == "renew":
            pr = next(p for p in snap["prs"] if p["number"] == n)
            if only_merges_since(gh, detail, pr["head"]):
                say(f"renew the Reviewer App's approval of PR #{n} (only merges from main since {detail[:7]})")
                renewed.add(n)
                if not dry_run:
                    post.cmd_review(identity, me, n, "APPROVE",
                                    f"Renewed after a refresh from main: the PR's own changes are unchanged since the approval of {detail[:7]}.")
        else:
            wanted.append(notify.merge_notice(action, n, detail, url(n)))

    # Questions for the Owner: forwarded once and reminded once; once answered, the asker is started.
    answered = {}
    for item in snap["issues"] + [{"number": p["number"], "title": p["title"], "labels": p["labels"]} for p in snap["prs"]]:
        if "needs-decision:owner" in item["labels"]:
            comments = gh.api("GET", f"repos/{gh.repo}/issues/{item['number']}/comments?per_page=100", paginate=True)
            wanted += notify.owner_questions(item, comments, identity, notices, now, post.MARKER)
            found = notify.latest_ask(comments, identity, post.MARKER)
            if found and found[2] and found[1] in config["runner"]["roles"]:
                answered[item["number"]] = found[1]

    # New runs, in dispatch order, within the caps.
    starts, skipped = plan.choose(snap, runs, caps, identity, now, skip_review=renewed, answered=answered)
    logs = Path(config["runner"]["runner"]["logs"]).expanduser() / "runs"
    for role, item, task, why in starts:
        say(f"start {article(role)} {role} run" + (f" on #{item}" if item else "") + f" ({why}): {task}")
        if not dry_run:
            spawn(config, role, item, task, logs)
    for role, item, why, reasons in skipped:
        lines.append(f"held: {article(role)} {role} run" + (f" on #{item}" if item else "") + f" ({why}): {reasons}")
    ok, reasons, warnings = usage.may_start(caps, runs, "coordinator", now, identity["time_zone"])
    window = usage.window_start(runs, now).date().isoformat()
    for w in warnings:
        wanted.append((f"usage-warning:{window}", f"Usage warning: {w}. The system pauses at the cap."))
    if any("weekly points" in r for r in reasons):
        wanted.append((f"usage-cap:{window}", f"The system has reached its weekly cap and is paused: {reasons[0]}."))


    if command == "reconcile":
        claimed = [i["number"] for i in snap["issues"] if plan.CLAIMED in i["labels"]]
        claimed += [p["number"] for p in snap["prs"] if plan.CLAIMED in p["labels"]]
        for n in expired_claims(gh, claimed, active_items, now):
            say(f"release the expired claim on #{n}")
            if not dry_run:
                post.cmd_release(gh, me, n)
        if not dry_run:
            store.append("heartbeats", {"event": "reconcile", "at": now.isoformat()})

    pending = notify.unsent(notices, wanted)
    if pending and not dry_run:
        hook = notify.webhook_url(identity)
        for key, text in pending:
            notify.send(hook, text)
            store.append("notices", {"key": key, "at": now.isoformat()})
    for key, _ in pending:
        say(f"notify the Owner ({key})")
    return lines or ["nothing to do"]


def daily_pass(dry_run, now=None):
    """Anomaly signals and the rule audit (blueprint §4.5), each taken to its response once per key."""
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    config = runner.load_all()
    identity = config["identity"]
    gh = post.GitHub(identity["repo"])
    me = ("dispatcher", f"dispatcher-{now:%Y%m%dT%H%M%SZ}")
    store = state.Store(runner.home(config) / "state", f"https://github.com/{identity['repo']}.git")
    runs, raised = store.read("runs"), {a["key"] for a in store.read("anomalies")}
    week = now - timedelta(days=7)
    history = [r for r in runs if datetime.fromisoformat(r["at"]) < week]
    snap = github_state.snapshot(gh, identity)

    closed_at = []
    since = (now - timedelta(days=8)).isoformat().replace("+00:00", "Z")
    for i in gh.api("GET", f"repos/{gh.repo}/issues?state=closed&since={since}&per_page=100", paginate=True):
        if "pull_request" not in i and i.get("closed_at") and not any(x["name"].startswith("record:") for x in i["labels"]):
            closed_at.append(datetime.fromisoformat(i["closed_at"].replace("Z", "+00:00")))
    waits = [0.0]
    for item in snap["issues"] + [{"number": p["number"], "labels": p["labels"]} for p in snap["prs"]]:
        if "needs-decision:owner" in item["labels"]:
            comments = gh.api("GET", f"repos/{gh.repo}/issues/{item['number']}/comments?per_page=100", paginate=True)
            found = notify.latest_ask(comments, identity, post.MARKER)
            if found and not found[2]:
                asked = datetime.fromisoformat(found[0]["created_at"].replace("Z", "+00:00"))
                waits.append(round((now - asked).total_seconds() / 3600, 1))
    watch = next((i for i in snap["issues"] if "record:watch-list" in i["labels"]), None)

    open_items = {i["number"] for i in snap["issues"] if not any(x.startswith("record:") for x in i["labels"])}
    found = (daily.denials(runs, now) + daily.bumpy(runs, now, open_items) + daily.new_failures(runs, now, history)
             + daily.review_rounds(snap["prs"], identity["reviewer_app"]["bot_login"], config["caps"]["wip"]["max_review_rounds"])
             + daily.cost_per_item(runs, now, closed_at)
             + daily.watch_list(watch["body"] if watch else "", daily.metrics(runs, now, max(waits))))
    live = gh.api("GET", f"repos/{gh.repo}/branches/main/protection")
    found += daily.protection_breaches(live, identity["branch_protection"])
    applied, main_commit = runner.commits(config)
    drift = runner.problems(config, applied_commit=applied, main_commit=main_commit,
                            login=runner.claude_login(config), cli=runner.cli_link(config))
    if drift:
        found.append((f"runner:{';'.join(drift)}", "builder", "The runner differs from main",
                      "runner.py check:\n- " + "\n- ".join(drift), None))

    lines = []
    for key, level, title, evidence, item in found:
        if key in raised:
            continue
        lines.append(("would raise" if dry_run else "raise") + f" ({level}): {title}")
        if dry_run:
            continue
        body = f"**Anomaly (Dispatcher's daily pass, {now.isoformat()}):** {title}\n\n{evidence}"
        if level == "owner":
            subprocess.run(["gh", "variable", "set", killswitch.VARIABLE, "--body", "true", "--repo", gh.repo],
                           check=True, capture_output=True)
            made = post.cmd_issue_create(gh, me, f"Rule breach: {title}", ["type:system", "sev:1"], body)
            n = int(made.split()[0].lstrip("#"))
            post.cmd_escalate(gh, me, n, "owner", "1. Restore the recorded settings, then resume (`arsys resume`).\n"
                              "2. Keep the new settings: record them in `system/identity.toml` by PR, then resume.\n\n"
                              "Recommended: 1, unless you changed them on purpose.")
        elif level == "coordinator":
            target = item or (watch["number"] if watch else None)
            if target:
                post.cmd_escalate(gh, me, target, "coordinator", body)
        else:
            made = post.cmd_issue_create(gh, me, f"Anomaly: {title}", ["type:system", "state:ready", "sev:3"], body)
            post.cmd_escalate(gh, me, int(made.split()[0].lstrip("#")), "builder", "Respond to this anomaly (blueprint §4.5): fix the cause, "
                              "and add a check and response so a recurrence is handled without you.")
        store.append("anomalies", {"key": key, "level": level, "title": title, "at": now.isoformat()})
    return lines or ["no anomalies"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["poll", "reconcile", "daily"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    if args.command == "daily":
        thrown, why = killswitch.paused(post.GitHub(runner.load_all()["identity"]["repo"]))
        if thrown:  # the audit still runs while paused: a breach must reach the Owner
            print(f"{stamp} daily: paused ({why}); auditing anyway")
        for line in daily_pass(args.dry_run):
            print(f"{stamp} daily: {line}")
        return 0
    for line in dispatch_pass(args.command, args.dry_run):
        print(f"{stamp} {args.command}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
