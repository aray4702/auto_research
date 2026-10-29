#!/usr/bin/env python3
"""Calibrate points_per_usd (system/caps.toml) against what the plan actually counts.

  calibrate.py [--record]

The runs report the account's 7-day utilization; this tool prices all local Claude Code usage in the
same window (usage numbers only, from every project's session logs: the Owner's decision 2 on #3)
and divides. It reports the average points per dollar, the marginal ones since the previous recorded
calibration and since the window's first run reading, and whether caps.toml errs on the safe side
against the highest of them. It never edits caps.toml
(a protected path); with --record it appends the calibration to the dispatcher/state branch.
Standard library only.
"""

import argparse
import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from system.dispatcher import state, usage  # noqa: E402
from system.tools import runner  # noqa: E402

PROJECTS = Path("~/.claude/projects").expanduser()
# The only fields calibration keeps from a session log; everything else (content included) is dropped
# as each line is parsed, never stored or used (the Owner's decision 2 on #3: usage numbers only).
KEEP = {"type", "timestamp", "message", "model", "usage", "input_tokens", "output_tokens", "cache_read_input_tokens",
        "cache_creation_input_tokens", "cache_creation", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"}
# Readings are whole percents, so a marginal needs this many points of change (±1 point = ±20 %).
MARGIN_MIN_POINTS = 5


def usage_entry(line):
    """A session-log line reduced to the usage fields, or None."""
    try:
        return json.loads(line, object_pairs_hook=lambda pairs: {k: v for k, v in pairs if k in KEEP})
    except json.JSONDecodeError:
        return None


def message_cost(usage_fields, price):
    """API-equivalent USD of one assistant message's usage."""
    cache = usage_fields.get("cache_creation")
    if cache:
        write_5m, write_1h = cache.get("ephemeral_5m_input_tokens", 0), cache.get("ephemeral_1h_input_tokens", 0)
    else:  # no breakdown: price at the cheaper 5-minute rate, so the check errs toward "the caps undercount"
        write_5m, write_1h = usage_fields.get("cache_creation_input_tokens", 0), 0
    tokens = (
        usage_fields.get("input_tokens", 0) * price["input"]
        + write_5m * 1.25 * price["input"]
        + write_1h * 2 * price["input"]
        + usage_fields.get("cache_read_input_tokens", 0) * price["cache_read"]
        + usage_fields.get("output_tokens", 0) * price["output"]
    )
    return tokens / 1e6


def local_cost(lines, prices, start, end):
    """(USD, unpriced models) of the assistant messages timestamped in [start, end)."""
    total, unknown = 0.0, set()
    for line in lines:
        if '"usage"' not in line:
            continue
        entry = usage_entry(line)
        if not isinstance(entry, dict):
            continue
        message = entry.get("message") or {}
        if entry.get("type") != "assistant" or not message.get("usage") or not entry.get("timestamp"):
            continue
        at = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
        if not start <= at < end:
            continue
        model = message.get("model")
        if model in prices:
            total += message_cost(message["usage"], prices[model])
        elif model and not model.startswith("<"):
            unknown.add(model)
    return total, sorted(unknown)


def session_lines(start, projects=PROJECTS):
    for path in projects.glob("*/**/*.jsonl"):
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) >= start:
            yield from path.read_text(errors="replace").splitlines()


def window_cost(runs, lines, prices, start, at):
    """(USD, the system's runs' USD, unpriced models) of all usage in the window up to `at`."""
    cost, unknown = local_cost(lines, prices, start, at)
    # The system's own runs keep no session logs (--no-session-persistence): add what they reported.
    runs_cost = sum(r.get("cost_usd") or 0 for r in runs
                    if r["event"] == "end" and start <= datetime.fromisoformat(r["at"]) <= at)
    return cost + runs_cost, runs_cost, unknown


def calibrate(caps, runs, calibrations, lines, prices, now):
    reading = usage.latest_reading(runs)
    if not reading:
        return {"ok": False, "reason": "no run has reported a 7-day reading yet"}
    lines = list(lines)
    start = usage.window_start(runs, now)
    at = datetime.fromisoformat(reading["at"])
    cost, runs_cost, unknown = window_cost(runs, lines, prices, start, at)
    points = reading["seven_day"] * 100
    out = {
        "ok": True, "window_start": start.isoformat(), "reading_at": reading["at"], "seven_day_points": round(points, 1),
        "local_cost_usd": round(cost, 2), "system_runs_usd": round(runs_cost, 2), "unpriced_models": unknown, "caps_points_per_usd": caps["claude"]["points_per_usd"],
        "average_points_per_usd": round(points / cost, 4) if cost else None, "marginal_points_per_usd": None,
        "window_marginal_from": None, "window_marginal_points_per_usd": None,
    }
    earlier = [c for c in calibrations if c["window_start"] == out["window_start"] and c["reading_at"] < out["reading_at"]]
    if earlier:
        prev = earlier[-1]
        d_points, d_cost = points - prev["seven_day_points"], cost - prev["local_cost_usd"]
        if d_cost > 0 and d_points >= MARGIN_MIN_POINTS:
            out["marginal_points_per_usd"] = round(d_points / d_cost, 4)
    # The marginal since the window's first run reading, so a fresh window needs no earlier calibration (#203).
    first = next((r for r in runs if r["event"] == "end" and r.get("seven_day") is not None
                  and start <= datetime.fromisoformat(r["at"]) < at), None)
    if first:
        d_points = points - first["seven_day"] * 100
        d_cost = cost - window_cost(runs, lines, prices, start, datetime.fromisoformat(first["at"]))[0]
        out["window_marginal_from"] = first["at"]
        if d_cost > 0 and d_points >= MARGIN_MIN_POINTS:
            out["window_marginal_points_per_usd"] = round(d_points / d_cost, 4)
    rates = [out[k] for k in ("average_points_per_usd", "marginal_points_per_usd", "window_marginal_points_per_usd")]
    measured = max(x for x in rates if x is not None) if out["average_points_per_usd"] else None
    out["measured_points_per_usd"] = measured
    out["caps_safe"] = measured is None or caps["claude"]["points_per_usd"] >= measured
    return out


def report(c):
    if not c["ok"]:
        return f"not calibrated: {c['reason']}"
    per = lambda x: f"{x} points/$ (1 point ≈ ${1 / x:.0f})" if x else "n/a"
    lines = [
        f"7-day window from {c['window_start']}: {c['seven_day_points']} points at {c['reading_at']}, "
        f"${c['local_cost_usd']} of local usage (API-equivalent), of which the system's runs ${c['system_runs_usd']}",
        f"average: {per(c['average_points_per_usd'])}; marginal since the last calibration: {per(c['marginal_points_per_usd'])}; "
        f"since the window's first run reading ({c['window_marginal_from'] or 'none'}): {per(c['window_marginal_points_per_usd'])}",
        f"caps.toml assumes {per(c['caps_points_per_usd'])}: "
        + ("errs on the safe side" if c["caps_safe"] else
           f"UNDERCOUNTS the system's share; propose points_per_usd = {c['measured_points_per_usd']} by PR"),
    ]
    if c["unpriced_models"]:
        lines.append("unpriced models (add them to system/prices.toml): " + ", ".join(c["unpriced_models"]))
    lines.append("Readings are whole percents: treat a single calibration as ±0.5 points.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", action="store_true", help="append this calibration to dispatcher/state")
    args = parser.parse_args(argv)
    config = runner.load_all()
    store = state.Store(runner.home(config) / "state", f"https://github.com/{config['identity']['repo']}.git")
    with open(ROOT / "system" / "prices.toml", "rb") as f:
        prices = tomllib.load(f)["models"]
    now = datetime.now(timezone.utc)
    runs, calibrations = store.read("runs"), store.read("calibrations")
    c = calibrate(config["caps"], runs, calibrations, session_lines(usage.window_start(runs, now)), prices, now)
    print(report(c))
    if args.record and c["ok"]:
        store.append("calibrations", {k: v for k, v in c.items() if k != "ok"} | {"event": "calibration", "at": now.isoformat()})
    return 0 if c["ok"] and c["caps_safe"] else 1


if __name__ == "__main__":
    sys.exit(main())
