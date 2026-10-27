"""Usage against the caps (blueprint §5): what a run reports, and whether another may start.

A run's stream (claude -p --output-format stream-json) ends with a `result` event carrying its
cost, turns and denials, and carries `rate_limit_event`s with the account's 5-hour and 7-day
utilization. The system's own share is its runs' cost times caps.toml's points_per_usd.
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STATUS = {"success": "finished", "error_max_turns": "cap: turns", "error_max_budget_usd": "cap: budget"}


def summarize(lines):
    """A run's outcome and usage from its stream-json lines."""
    result, reading = None, None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result = event
        elif event.get("type") == "rate_limit_event":
            reading = event.get("rate_limit_info", {}).get("unifiedWindows")
    out = {"status": "cut-off"}
    if result:
        status = STATUS.get(result.get("subtype"), "failed")
        if status == "finished" and result.get("is_error"):
            status = "failed"
        out = {
            "status": status,
            "cost_usd": round(result.get("total_cost_usd") or 0, 6),
            "turns": result.get("num_turns"),
            "denials": len(result.get("permission_denials") or []),
        }
    if reading:
        out["five_hour"] = reading.get("five_hour", {}).get("utilization")
        out["seven_day"] = reading.get("seven_day", {}).get("utilization")
        out["seven_day_resets_at"] = reading.get("seven_day", {}).get("resetsAt")
    return out


def parse_time(iso):
    return datetime.fromisoformat(iso)


def active(runs):
    """Runs that have started and not ended."""
    ended = {r["run_id"] for r in runs if r["event"] == "end"}
    return [r for r in runs if r["event"] == "start" and r["run_id"] not in ended]


def latest_reading(runs):
    for r in reversed(runs):
        if r["event"] == "end" and r.get("seven_day") is not None:
            return r
    return None


def window_start(runs, now):
    """Start of the current 7-day plan window: from the latest reset time a run reported."""
    reading = latest_reading(runs)
    if reading and reading.get("seven_day_resets_at"):
        resets = datetime.fromtimestamp(reading["seven_day_resets_at"], timezone.utc)
        while resets <= now:
            resets += timedelta(days=7)
        return resets - timedelta(days=7)
    return now - timedelta(days=7)


def points_used(caps, runs, now):
    start = window_start(runs, now)
    cost = sum(r.get("cost_usd") or 0 for r in runs if r["event"] == "end" and parse_time(r["at"]) >= start)
    return cost * caps["claude"]["points_per_usd"]


def may_start(caps, runs, role, now, time_zone="America/Los_Angeles"):
    """(ok, reasons, warnings) for starting one more run of this role now."""
    claude, role_caps = caps["claude"], caps["roles"][role]
    reasons, warnings = [], []
    running = active(runs)
    if len(running) >= caps["concurrency"]["max_runs"]:
        reasons.append(f"{len(running)} runs are active; the cap is {caps['concurrency']['max_runs']}")
    if sum(r["role"] == role for r in running) >= caps["concurrency"]["max_runs_per_role"]:
        reasons.append(f"a {role} run is already active")
    tz = ZoneInfo(time_zone)
    today = now.astimezone(tz).date()
    started_today = sum(
        r["event"] == "start" and r["role"] == role and parse_time(r["at"]).astimezone(tz).date() == today
        for r in runs
    )
    if started_today >= role_caps["max_runs_per_day"]:
        reasons.append(f"{started_today} {role} runs today; the cap is {role_caps['max_runs_per_day']}")
    used = points_used(caps, runs, now)
    worst = role_caps["max_budget_usd"] * claude["points_per_usd"]
    if used + worst > claude["weekly_points"]:
        reasons.append(f"the system has used {used:.2f} of {claude['weekly_points']} weekly points; this run could take {worst:.2f}")
    elif used >= claude["warn_at_points"]:
        warnings.append(f"the system has used {used:.2f} of {claude['weekly_points']} weekly points")
    reading = latest_reading(runs)
    if reading:
        if (reading.get("five_hour") or 0) >= claude["hold_new_runs_at_five_hour"]:
            reasons.append(f"the account's 5-hour use is {reading['five_hour']:.0%}; new runs hold at {claude['hold_new_runs_at_five_hour']:.0%}")
        if (reading.get("seven_day") or 0) >= claude["pause_at_seven_day"]:
            reasons.append(f"the account's 7-day use is {reading['seven_day']:.0%}; the system pauses at {claude['pause_at_seven_day']:.0%}")
    return not reasons, reasons, warnings
