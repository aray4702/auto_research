"""Usage against the caps (blueprint §5): what a run reports, and whether another may start.

A run's stream (claude -p --output-format stream-json) ends with a `result` event carrying its
cost, turns and denials, and carries `rate_limit_event`s with the account's 5-hour and 7-day
utilization. The system's own share is its runs' cost times caps.toml's points_per_usd.
"""

import json
import re
import shlex
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STATUS = {"success": "finished", "error_max_turns": "cap: turns", "error_max_budget_usd": "cap: budget"}
COMPOUND = " (compound)"


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
            # What was denied, as the tool and the command's first two words (#44): enough to spot a
            # recurring permission gap, nothing more of the command.
            "denied": sorted({denial_signature(d) for d in result.get("permission_denials") or []}),
        }
    if reading:
        out["five_hour"] = reading.get("five_hour", {}).get("utilization")
        out["seven_day"] = reading.get("seven_day", {}).get("utilization")
        out["seven_day_resets_at"] = reading.get("seven_day", {}).get("resetsAt")
    return out


def compound(command):
    """Whether a command pipes, chains, redirects, substitutes or runs a second line: a shell operator or
    line break outside quotes. Such a command is denied when any part isn't allowed, even if its first is."""
    lexer = shlex.shlex(command.strip(), posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace, lexer.whitespace_split = " \t\r", True
    try:
        return any(token and set(token) <= set("();<>|&\n") for token in lexer) or "`" in command
    except ValueError:
        return False  # unbalanced quotes: unknown, so recorded as a plain command


def denial_signature(denial):
    """The tool, and for Bash up to two words that look like a command or subcommand. It stops at any
    assignment, path, quote or flag, so no argument, value or secret (#44) can reach the record. A
    compound command is marked as one (#140): its denial is the one-command rule, not a permission gap."""
    tool = denial.get("tool_name", "?")
    command = (denial.get("tool_input") or {}).get("command")
    if tool != "Bash" or not command:
        return tool
    words = []
    for word in command.split():
        if "=" in word and not words:
            continue  # a leading VAR=value assignment: skip it, never record it
        # The command may carry digits or dots (python3, post.py); a second word only counts if it is
        # subcommand-shaped, so a lowercase token, hex string or filename argument is never kept.
        shape = r"[a-z][a-z0-9._-]*" if not words else r"[a-z]+(-[a-z]+)*"
        if not re.fullmatch(shape, word) or len(word) > 20:
            break
        words.append(word)
        if len(words) == 2:
            break
    return "Bash: " + (" ".join(words) or "?") + (COMPOUND if compound(command) else "")


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
    lifted = (caps.get("override") or {}).get("no_daily_run_caps_until")  # the Owner's, for a day (#101)
    if started_today >= role_caps["max_runs_per_day"] and not (lifted and now < datetime.fromisoformat(lifted)):
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
