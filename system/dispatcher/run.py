#!/usr/bin/env python3
"""Start one agent run, and record it. Used by the Dispatcher, the builder's daily schedule and
supervised runs. Standard library only.

  run.py ROLE --task TEXT [--item N] [--claim] [--dry-run]

Before it starts: the kill switch, that Claude Code signs in with the subscription (never an API
login that could spend money), then the caps (concurrency, runs per day, the weekly points and
the Owner's headroom). The run gets a fresh worktree of origin/main under <home>/runs/, its role's
settings and caps, and a wall-clock timeout; its stream is logged under <logs>/runs/. Afterwards its
outcome and usage are appended to the dispatcher/state branch, and its item's claim is released if
it is still held. With --claim, the item is claimed first, as the Dispatcher does.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from system.dispatcher import killswitch, state, usage  # noqa: E402
from system.tools import post, runner  # noqa: E402


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def new_run_id(role, at):
    return f"{role}-{at:%Y%m%dT%H%M%SZ}"


def paths(config, run_id):
    home = runner.home(config)
    logs = Path(config["runner"]["runner"]["logs"]).expanduser() / "runs"
    return {
        "checkout": home / "system",
        "worktree": home / "runs" / run_id,
        "settings": home / "runs" / f"{run_id}.settings.json",
        "log": logs / f"{run_id}.jsonl",
        "state": home / "state",
    }


def environment(config, role, run_id):
    return {
        **runner.run_env(config), "AGENT_ROLE": role, "AGENT_RUN_ID": run_id,
        "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0",
    }


def task_text(task, item):
    return f"{task} Your item: #{item}." if item else task


def execute(cmd, cwd, env, log_path, timeout_s, on_start=lambda pid: None):
    """Run cmd, streaming its output to log_path. Returns 'ended' or 'timeout'."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "wb") as log:
        # stdin is empty: claude -p otherwise waits for piped input it will never get.
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        on_start(proc.pid)
        try:
            proc.wait(timeout=timeout_s)
            return "ended"
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            return "timeout"


locked = runner.locked  # the one lock on the shared checkout; the CLI and runner.py fetch under it too


def worktree(checkout, path):
    with locked(checkout):
        subprocess.run(["git", "fetch", "--quiet", "origin", "main"], cwd=checkout, check=True)
        subprocess.run(["git", "worktree", "add", "--quiet", "--detach", str(path), "origin/main"], cwd=checkout, check=True)


def remove_worktree(checkout, path):
    with locked(checkout):
        subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=checkout, capture_output=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("role")
    parser.add_argument("--task", required=True)
    parser.add_argument("--item", type=int)
    parser.add_argument("--claim", action="store_true", help="claim the item first, as the Dispatcher does")
    parser.add_argument("--dry-run", action="store_true", help="check and print the run, start nothing")
    args = parser.parse_args(argv)

    config = runner.load_all()
    if args.role not in config["runner"]["roles"]:
        parser.error(f"role must be one of {', '.join(config['runner']['roles'])}")
    identity, caps = config["identity"], config["caps"]
    gh = post.GitHub(identity["repo"])
    at = now_utc()
    run_id = new_run_id(args.role, at)
    p = paths(config, run_id)

    thrown, why = killswitch.paused(gh)
    if thrown:
        print(f"not started: {why}")
        return 3
    refused = runner.login_problem(config, runner.claude_login(config))
    if refused:
        print(f"not started: {refused}")
        return 5
    store = state.Store(p["state"], f"https://github.com/{identity['repo']}.git")
    ok, reasons, warnings = usage.may_start(caps, store.read("runs"), args.role, at, identity["time_zone"])
    for w in warnings:
        print(f"warning: {w}")
    if not ok:
        print("not started: " + "; ".join(reasons))
        return 4

    cmd = runner.claude_command(config, args.role, task_text(args.task, args.item), p["settings"])
    if args.dry_run:
        print(json.dumps({"run_id": run_id, "cwd": str(p["worktree"]), "command": cmd,
                          "settings": runner.role_settings(config, args.role)}, indent=2))
        return 0

    dispatcher = ("dispatcher", run_id)
    if args.claim and args.item:
        minutes = caps["roles"][args.role]["timeout_minutes"] + caps["wip"]["claim_expiry_after_timeout_minutes"]
        print(post.cmd_claim(gh, identity, dispatcher, args.item, minutes))
    ended = {"recorded": False}
    try:
        return launch(args, config, gh, store, p, run_id, cmd, dispatcher, ended)
    except BaseException as e:
        # A launcher that fails must not strand its claim or leave a run that looks active.
        if not ended["recorded"]:
            store.append("runs", {"run_id": run_id, "role": args.role, "item": args.item, "event": "end",
                                  "at": now_utc().isoformat(), "status": f"failed: launcher: {type(e).__name__}"})
        if args.claim and args.item:
            post.cmd_release(gh, dispatcher, args.item)
        raise


def launch(args, config, gh, store, p, run_id, cmd, dispatcher, ended):
    caps = config["caps"]
    p["settings"].parent.mkdir(parents=True, exist_ok=True)
    p["settings"].write_text(json.dumps(runner.role_settings(config, args.role), indent=2))
    worktree(p["checkout"], p["worktree"])
    base = {"run_id": run_id, "role": args.role, "item": args.item}
    try:
        timeout_s = caps["roles"][args.role]["timeout_minutes"] * 60
        on_start = lambda pid: store.append("runs", {**base, "event": "start", "at": now_utc().isoformat(), "pid": pid, "task": args.task})
        outcome = execute(cmd, p["worktree"], environment(config, args.role, run_id), p["log"], timeout_s, on_start)
        summary = usage.summarize(p["log"].read_text(errors="replace").splitlines(), str(p["worktree"]))
        if outcome == "timeout":
            summary["status"] = "timeout"
        store.append("runs", {**base, "event": "end", "at": now_utc().isoformat(), **summary})
        ended["recorded"] = True
    finally:
        remove_worktree(p["checkout"], p["worktree"])
        p["settings"].unlink(missing_ok=True)
    if args.item:
        labels = {label["name"] for label in gh.api("GET", f"repos/{gh.repo}/issues/{args.item}")["labels"]}
        if post.CLAIMED in labels:
            post.cmd_release(gh, dispatcher, args.item)
    print(f"{run_id}: {summary['status']}, ${summary.get('cost_usd', 0)}, {summary.get('turns')} turns, log {p['log']}")
    return 0 if summary["status"] == "finished" else 1


if __name__ == "__main__":
    sys.exit(main())
