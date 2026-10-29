"""Usage against the caps (blueprint §5): what a run reports, and whether another may start.

A run's stream (claude -p --output-format stream-json) ends with a `result` event carrying its
cost, turns and denials, and carries `rate_limit_event`s with the account's 5-hour and 7-day
utilization. The system's own share is its runs' cost times caps.toml's points_per_usd.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STATUS = {"success": "finished", "error_max_turns": "cap: turns", "error_max_budget_usd": "cap: budget"}


def summarize(lines, worktree=None):
    """A run's outcome and usage from its stream-json lines. The worktree tells which paths were outside it."""
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
            "denied": sorted({denial_signature(d, worktree) for d in result.get("permission_denials") or []}),
        }
    if reading:
        out["five_hour"] = reading.get("five_hour", {}).get("utilization")
        out["seven_day"] = reading.get("seven_day", {}).get("utilization")
        out["seven_day_resets_at"] = reading.get("seven_day", {}).get("resetsAt")
    return out


def denial_signature(denial, worktree=None):
    """The tool, and for Bash up to two words that look like a command or subcommand. It stops at any
    assignment, path, quote or flag, so no argument, value or secret (#44) can reach the record. An
    interpreter is followed by what it runs instead (see interpreted). A first word that isn't
    command-shaped is recorded by its shape (see first_word), not as a bare "?" (#176). Shell operators
    and OUTSIDE follow as symbols: `git diff` is allowed, yet denied with `$(…)` or a path outside the
    worktree (#177)."""
    tool = denial.get("tool_name", "?")
    command = (denial.get("tool_input") or {}).get("command")
    if tool != "Bash" or not command:
        return tool
    words, split = [], command.split()
    for i, word in enumerate(split):
        if "=" in word and not words:
            continue  # a leading VAR=value assignment: skip it, never record it
        if words and words[0].rsplit("/", 1)[-1] in INTERPRETERS:
            words += interpreted(split[i:])
            break
        # The command may carry digits or dots (python3, post.py); a second word only counts if it is
        # subcommand-shaped, so a lowercase token, hex string or filename argument is never kept.
        if not words:
            words.append(first_word(word))
            if not re.fullmatch(COMMAND, words[0].removeprefix("…/")):
                break
            continue
        if not re.fullmatch(r"[a-z]+(-[a-z]+)*", word) or len(word) > 20:
            break
        words.append(word)
        if len(words) == 2:
            break
    outside = [OUTSIDE] if reaches_outside(command, worktree) else []
    return " ".join(["Bash:", " ".join(words) or "?", *shell_operators(command), *outside])


COMMAND = r"[a-z][a-z0-9._-]{0,19}"
SHAPES = "({[$\"'.~\\!"


def first_word(word):
    """The command, or what its first word looks like (#176): a path's command name after "…/", else the
    symbol it starts with and "…", else "?". Never a path's directories, a quote's or a variable's text."""
    if re.fullmatch(COMMAND, word):
        return word
    if "/" in word:
        name = word.strip("'\"").rstrip("/").rsplit("/", 1)[-1]
        return "…/" + (name if re.fullmatch(COMMAND, name) else "?")
    return word[0] + "…" if word[0] in SHAPES else "?"


OPERATORS = ("$(", "<<", "||", "&&", ">>", "|", ";", ">", "<", "`")
# A second command on a later line (#178): `git fetch` then a line that isn't allowed is denied, and
# without this mark it read as a `git fetch` gap. Not one of OPERATORS, as no command line contains it.
NEWLINE = "⏎"
COMPOUND = OPERATORS + (NEWLINE,)
EXPANDED = ("$(", "`")  # the shell expands these inside double quotes too
OUTSIDE = "[outside]"


def shell_operators(command):
    """The shell operators in the command's first command, outside quotes, in order and once each (#138):
    `cat … |` is denied where `cat` alone is allowed. Symbols only, so no word of the command is kept.
    `$(` and `` ` `` count inside double quotes too, as the shell expands them there (#177).
    Then NEWLINE if another command follows it on a later line. A newline inside quotes, after a `\\`
    or in a heredoc's body doesn't end the first command (#198)."""
    text = command.strip()
    out, quote, heredocs, i = [], None, [], 0
    while i < len(text):
        c = text[i]
        if quote == '"' and (op := next((o for o in EXPANDED if text.startswith(o, i)), None)):
            out += [op] if op not in out else []
            i += len(op)
            continue
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "\\":
            i += 1
        elif c == "\n":
            if text[after_heredocs(text, i + 1, heredocs):].strip():
                out.append(NEWLINE)
            return out
        else:
            op = next((o for o in OPERATORS if text.startswith(o, i)), None)
            if op:
                out += [op] if op not in out else []
                heredoc = re.match(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", text[i:]) if op == "<<" else None
                heredocs += [heredoc.group(2)] if heredoc else []
                i += len(op)
                continue
        i += 1
    return out


def reaches_outside(command, worktree=None):
    """Whether a path on the command's first line leads outside the worktree, which --restricted denies.
    A word counts as a path once its quotes and any `--flag=` are stripped; with no worktree known, any
    absolute, `~` or `..` path does. The command's own path isn't counted: first_word records it, and a
    command run by path misses the allow rules wherever it is (#176)."""
    words = command.split("\n", 1)[0].split()
    while words and "=" in words[0]:
        words.pop(0)  # a leading VAR=value assignment, as in denial_signature
    for word in words[1:]:
        word = re.sub(r"^--?[\w-]+=", "", word.strip("'\""))
        if word.startswith("~"):
            return True
        if not (word.startswith("/") or word == ".." or word.startswith("../") or "/../" in word):
            continue
        if not worktree:
            return True
        path = os.path.normpath(os.path.join(worktree, word))
        if os.path.commonpath([path, os.path.normpath(worktree)]) != os.path.normpath(worktree):
            return True
    return False


def after_heredocs(text, i, delimiters):
    """Where the text resumes after the bodies of these heredocs, which start at i: past each body's
    closing delimiter line, or at the end if one never closes."""
    for delimiter in delimiters:
        while i < len(text):
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            line, i = text[i:end], end + 1
            if line.strip() == delimiter:
                break
    return min(i, len(text))


INTERPRETERS = {"python3", "python"}
SCRIPT = r"\.?[a-z][a-z0-9_]*(/[a-z][a-z0-9_]*)*\.py"


def interpreted(words):
    """What an interpreter runs (#141): its one-letter flags, then the module after -m or the relative
    .py path it's given. `python3 -I` alone hides which script was denied. Never -c's code, an absolute
    path, or any argument after the script."""
    out = []
    for word in words:
        if re.fullmatch(r"-[A-Za-z]", word):
            out.append(word)
            if word == "-c":
                break
        elif len(word) <= 60 and (out[-1:] == ["-m"] and re.fullmatch(r"[a-z][a-z0-9_.]*", word) or re.fullmatch(SCRIPT, word)):
            out.append(word)
            break
        else:
            break
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
