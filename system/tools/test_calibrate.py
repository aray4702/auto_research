"""Tests for calibration: pricing local usage, and comparing it with the plan's 7-day reading."""

import json
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from system.tools import calibrate

ROOT = Path(__file__).resolve().parents[2]
CAPS = tomllib.loads((ROOT / "system" / "caps.toml").read_text())
PRICES = tomllib.loads((ROOT / "system" / "prices.toml").read_text())["models"]
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)
RESETS = int((NOW + timedelta(days=2)).timestamp())  # the window began 5 days ago


def msg(model, at, **usage):
    return json.dumps({"type": "assistant", "timestamp": at.isoformat().replace("+00:00", "Z"),
                       "message": {"model": model, "usage": usage}})


def reading(at, seven_day, cost=0.0):
    return {"event": "end", "run_id": f"r{at.timestamp()}", "role": "reviewer", "at": at.isoformat(),
            "cost_usd": cost, "five_hour": 0.1, "seven_day": seven_day, "seven_day_resets_at": RESETS}


class CostTest(unittest.TestCase):
    def test_a_message_is_priced_by_model_including_cache_writes(self):
        # 1M input at $4, 1M 1-hour cache write at 2x input, 1M cache read at $0.20, 1M output at $20.
        usage = {"input_tokens": 1_000_000, "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000},
                 "cache_read_input_tokens": 1_000_000, "output_tokens": 1_000_000}
        self.assertAlmostEqual(calibrate.message_cost(usage, PRICES["claude-opus-5-5"]), 4 + 8 + 0.2 + 20)

    def test_only_the_window_counts_and_unknown_models_are_named(self):
        start, end = NOW - timedelta(days=5), NOW
        lines = [msg("claude-opus-5-5", NOW - timedelta(hours=1), output_tokens=1_000_000),
                 msg("claude-opus-5-5", NOW - timedelta(days=6), output_tokens=1_000_000),
                 msg("claude-mystery-9", NOW - timedelta(hours=2), output_tokens=10),
                 '{"type": "user"}', "not json"]
        cost, unknown = calibrate.local_cost(lines, PRICES, start, end)
        self.assertAlmostEqual(cost, 20.0)
        self.assertEqual(unknown, ["claude-mystery-9"])


class CalibrateTest(unittest.TestCase):
    def test_average_marginal_and_whether_the_caps_are_safe(self):
        at = NOW - timedelta(minutes=10)
        lines = [msg("claude-opus-5-5", NOW - timedelta(days=1), output_tokens=5_000_000)]  # $100 of local sessions
        runs = [reading(at, 0.07, cost=10.0)]  # 7 points; the system's runs add $10 (they keep no session logs)
        c = calibrate.calibrate(CAPS, runs, [], lines, PRICES, NOW)
        self.assertEqual((c["local_cost_usd"], c["system_runs_usd"], c["seven_day_points"]), (110.0, 10.0, 7.0))
        self.assertAlmostEqual(c["average_points_per_usd"], round(7 / 110, 4))
        self.assertIsNone(c["marginal_points_per_usd"])
        # An earlier calibration in the same window: 4 points at $60 -> 3 points for $50 since = 0.06/$.
        earlier = [{"window_start": c["window_start"], "reading_at": (at - timedelta(hours=5)).isoformat(),
                    "seven_day_points": 4.0, "local_cost_usd": 60.0}]
        c = calibrate.calibrate(CAPS, runs, earlier, lines, PRICES, NOW)
        self.assertAlmostEqual(c["marginal_points_per_usd"], 0.06)
        self.assertEqual(c["measured_points_per_usd"], max(c["average_points_per_usd"], 0.06))
        self.assertEqual(c["caps_safe"], CAPS["claude"]["points_per_usd"] >= c["measured_points_per_usd"])
        self.assertIn("points/$", calibrate.report(c))

    def test_no_reading_yet(self):
        c = calibrate.calibrate(CAPS, [], [], [], PRICES, NOW)
        self.assertFalse(c["ok"])


if __name__ == "__main__":
    unittest.main()
