"""Tests for the reader of the Mac's figures (design/internal/criteria.md §2), on results files built here.

Run: python3 -I -m unittest discover -s .github/scripts -t .github/scripts
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_results

REPO_ROOT = Path(__file__).resolve().parents[2]


def row(dataset, system, wer=None, der=None, rtf=None, rss=None):
    return {"dataset": dataset, "system": system, "wer": wer, "der": der, "audio_seconds": 3600.0,
            "wall_seconds": 1000.0, "rtf": rtf, "peak_rss_bytes": rss}


# A results file that meets every bound.
GOOD = {
    "schema_version": "1.0", "set": "full", "commit": "a" * 40, "dirty": False,
    "date": "2026-10-30T11:38:02-07:00",
    "machine": {"arch": "arm64", "cpu": "Apple M4", "memory_bytes": 17179869184, "os": "macOS 27.0.1"},
    "lmt_version": "0.1.0", "models": {"asr": {"name": "x"}, "diarization": {"name": "y"}},
    "figures": [
        row("LS full", "product", wer=0.023, rtf=0.30, rss=2.2e9),
        row("LS full", "whisper-alone", wer=0.023, rtf=0.25),
        row("AMI full", "product", wer=0.243, der=0.235, rtf=0.45, rss=3.9e9),
        row("AMI full", "whisper-alone", wer=0.243, rtf=0.20),
        row("AMI full", "pyannote-alone", der=0.188, rtf=0.40),
        row("one-hour", "product", wer=0.337, der=0.309, rtf=0.50, rss=4.0e9),
        row("one-hour", "whisper-alone", wer=0.337, rtf=0.24),
        row("one-hour", "pyannote-alone", der=0.244, rtf=0.45),
    ],
    "determinism": {"runs": 2, "identical": True},
}
PLACEHOLDER = {"schema_version": "1.0", "set": "full", "commit": None, "dirty": False, "date": None,
               "machine": None, "lmt_version": "0.1.0", "models": None, "figures": [], "determinism": None}
NAMES = ["G1-8 LS full WER", "G1-8 AMI full WER", "G1-8 AMI full DER", "G1-8 Whisper alone and pyannote alone",
         "G1-9 AMI full RTF", "G1-9 one-hour RTF", "G1-10 one-hour peak RSS", "G1-12 two runs identical",
         "G1-14 the table's figures"]


def setting(results, dataset, system, **values):
    results = copy.deepcopy(results)
    for f in results["figures"]:
        if (f["dataset"], f["system"]) == (dataset, system):
            f.update(values)
    return results


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.results = self.dir / "full.json"
        self.out = self.dir / "criteria-test-2.json"
        env = mock.patch.dict(os.environ, {"GITHUB_SHA": "c" * 40, "GITHUB_REF": "refs/heads/main",
                                           "GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "1", "LMT_TIER": "1"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def main(self, results, raw=None):
        self.results.write_text(raw if raw is not None else json.dumps(results))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = check_results.main(["--results", str(self.results), "--criteria-out", str(self.out)])
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code

    def outcomes(self, results):
        self.assertEqual(self.main(results), 0, self.stderr)
        record = json.loads(self.out.read_text())
        return {c["check"].removeprefix("check_results.py::"): c["outcome"] for c in record["checks"]}

    def assertOnlyFailed(self, results, *names):
        outcomes = self.outcomes(results)
        self.assertEqual({n for n, o in outcomes.items() if o != "passed"}, set(names), outcomes)
        self.assertTrue(all(outcomes[n] == "failed" for n in names), outcomes)


class RecordTest(Fixture):
    def test_good_file_passes_every_check(self):
        self.assertEqual(self.main(GOOD), 0)
        record = json.loads(self.out.read_text())
        self.assertEqual({k: record[k] for k in ("format", "job", "commit", "ref", "run_id", "tier", "complete")},
                         {"format": 1, "job": "test", "commit": "c" * 40, "ref": "refs/heads/main", "run_id": 7,
                          "tier": 1, "complete": True})
        self.assertEqual([c["check"] for c in record["checks"]], [f"check_results.py::{n}" for n in NAMES])
        self.assertEqual({c["outcome"] for c in record["checks"]}, {"passed"})
        criteria = {c["check"].split("::")[1]: c["criteria"] for c in record["checks"]}
        self.assertEqual(criteria["G1-10 one-hour peak RSS"], ["G1-10"])
        self.assertEqual(criteria["G1-14 the table's figures"], ["G1-14"])

    def test_placeholder_skips_every_check_with_its_reason(self):
        self.assertEqual(self.main(PLACEHOLDER), 0)
        checks = json.loads(self.out.read_text())["checks"]
        self.assertEqual(len(checks), len(NAMES))
        for c in checks:
            self.assertEqual(c["outcome"], "skipped")
            self.assertIn("has no figures yet (the Owner's run of lmt eval --set full)", c["reason"])
        self.assertTrue(json.loads(self.out.read_text())["complete"])

    def test_record_is_one_the_merge_reads(self):
        sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
        import criteria
        self.main(GOOD)
        ids = criteria.spec_ids(REPO_ROOT / "spec" / "acceptance.md")
        criteria.load_record(self.out, ids, ["test"])


class BoundsTest(Fixture):
    """Each bound is inclusive; just past it, the check fails and the script still exits 0."""

    def test_ls_wer(self):
        self.assertOnlyFailed(setting(GOOD, "LS full", "product", wer=0.05))
        self.assertOnlyFailed(setting(GOOD, "LS full", "product", wer=0.0501), "G1-8 LS full WER")

    def test_ami_wer(self):
        self.assertOnlyFailed(setting(GOOD, "AMI full", "product", wer=0.25))
        self.assertOnlyFailed(setting(GOOD, "AMI full", "product", wer=0.2501), "G1-8 AMI full WER")

    def test_ami_der(self):
        self.assertOnlyFailed(setting(GOOD, "AMI full", "product", der=0.25))
        self.assertOnlyFailed(setting(GOOD, "AMI full", "product", der=0.2501), "G1-8 AMI full DER")

    def test_rtf_ami_and_one_hour(self):
        self.assertOnlyFailed(setting(GOOD, "AMI full", "product", rtf=1.0))
        self.assertOnlyFailed(setting(GOOD, "AMI full", "product", rtf=1.0001), "G1-9 AMI full RTF")
        self.assertOnlyFailed(setting(GOOD, "one-hour", "product", rtf=1.0))
        self.assertOnlyFailed(setting(GOOD, "one-hour", "product", rtf=1.0001), "G1-9 one-hour RTF")

    def test_peak_rss(self):
        self.assertOnlyFailed(setting(GOOD, "one-hour", "product", peak_rss_bytes=4_000_000_001),
                              "G1-10 one-hour peak RSS")

    def test_a_missed_bound_is_reported(self):
        self.main(setting(GOOD, "AMI full", "product", rtf=1.2))
        self.assertIn("FAIL G1-9 AMI full RTF: AMI full rtf 1.200 > 1.000", self.stdout)
        self.assertIn("1 missed", self.stdout)


class MissingFiguresTest(Fixture):
    def test_missing_baseline(self):
        self.assertOnlyFailed(setting(GOOD, "AMI full", "pyannote-alone", der=None),
                              "G1-8 Whisper alone and pyannote alone", "G1-14 the table's figures")

    def test_missing_product_figure_fails_its_check_and_the_table(self):
        results = copy.deepcopy(GOOD)
        results["figures"] = [f for f in results["figures"] if f["dataset"] != "one-hour"]
        self.assertOnlyFailed(results, "G1-9 one-hour RTF", "G1-10 one-hour peak RSS", "G1-14 the table's figures")

    def test_determinism(self):
        for d in (None, {"runs": 2, "identical": False}, {"runs": 1, "identical": True}):
            self.assertOnlyFailed(dict(GOOD, determinism=d), "G1-12 two runs identical")

    def test_table_needs_the_run_and_a_clean_tree(self):
        self.assertOnlyFailed(dict(GOOD, commit=None), "G1-14 the table's figures")
        self.assertOnlyFailed(dict(GOOD, machine=None), "G1-14 the table's figures")
        self.assertOnlyFailed(dict(GOOD, dirty=True), "G1-14 the table's figures")


class MalformedTest(Fixture):
    def assertMalformed(self, results=None, raw=None, says=""):
        self.assertEqual(self.main(results, raw), check_results.EXIT_ERROR)
        self.assertEqual(len(self.stderr.splitlines()), 1, self.stderr)
        self.assertTrue(self.stderr.startswith("check_results.py: error: "), self.stderr)
        self.assertIn(says, self.stderr)
        self.assertFalse(self.out.exists())

    def test_missing_file(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = check_results.main(["--results", str(self.dir / "none.json")])
        self.assertEqual(code, check_results.EXIT_ERROR)
        self.assertIn("none.json", err.getvalue())

    def test_not_json(self):
        self.assertMalformed(raw="{", says="not JSON")

    def test_other_schema_version(self):
        self.assertMalformed(dict(GOOD, schema_version="2.0"), says="version 1.0")

    def test_other_set(self):
        self.assertMalformed(dict(GOOD, set="ci"), says="set is 'ci'")

    def test_figures_not_a_list(self):
        self.assertMalformed(dict(GOOD, figures={}), says="`figures` must be a list")

    def test_unknown_system(self):
        results = copy.deepcopy(GOOD)
        results["figures"][0]["system"] = "other"
        self.assertMalformed(results, says="`system`")

    def test_figure_not_a_number(self):
        self.assertMalformed(setting(GOOD, "LS full", "product", wer="0.02"), says="`wer` must be a number")

    def test_duplicate_row(self):
        results = copy.deepcopy(GOOD)
        results["figures"].append(results["figures"][0])
        self.assertMalformed(results, says="appears twice")


class RepositoryTest(Fixture):
    def test_committed_results_file_is_read(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = check_results.main(["--results", str(REPO_ROOT / "results" / "full.json"),
                                       "--criteria-out", str(self.out)])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(self.out.read_text())["checks"]), len(NAMES))


# A set `live` results file (src/lmt/eval/live.py), at #537's figures with the p95 at 14.0 s.
LIVE_FIXTURE = Path(__file__).resolve().parent / "testdata" / "live.json"  # #537's results/live.json
LIVE = {
    "schema_version": "1.0", "set": "live", "commit": "b" * 40, "dirty": False,
    "date": "2026-10-31T00:30:35-07:00", "machine": GOOD["machine"], "lmt_version": "0.2.0",
    "models": GOOD["models"],
    "figures": [{"dataset": "AMI-ci", "system": "product-live", "wer": 0.17, "der": 0.40, "audio_seconds": 900.0,
                 "wall_seconds": 975.6, "rtf": 1.084, "peak_rss_bytes": None, "p50_delay_seconds": 8.9,
                 "p95_delay_seconds": 14.0, "max_delay_seconds": 45.8, "matched_words": 928,
                 "runs": [{"p95_delay_seconds": 13.0, "behind_seconds": 2.5},
                          {"p95_delay_seconds": 14.5, "behind_seconds": 1.6},
                          {"p95_delay_seconds": 13.9, "behind_seconds": 2.2}]}],
    "determinism": None,
}
LIVE_NAME = "G2-6 live p95 delay"


def live(**values):
    results = copy.deepcopy(LIVE)
    results["figures"][0].update(values)
    return results


class LiveTest(Fixture):
    """G2-6 from results/live.json: the pooled p95 delay ≤ 15.0 s (v0.3), a miss recorded and exit 0."""

    def test_passes_at_14_s(self):
        self.assertEqual(self.outcomes(LIVE), {LIVE_NAME: "passed"})
        self.assertIn("PASS G2-6 live p95 delay: pooled p95 delay 14.00 s over 3 runs ≤ 15.00 s", self.stdout)

    def test_bound_is_inclusive_and_fails_above_15_s(self):
        self.assertEqual(self.outcomes(live(p95_delay_seconds=15.0)), {LIVE_NAME: "passed"})
        self.assertEqual(self.outcomes(live(p95_delay_seconds=15.01)), {LIVE_NAME: "failed"})
        self.assertIn("FAIL G2-6 live p95 delay: pooled p95 delay 15.01 s over 3 runs > 15.00 s", self.stdout)
        self.assertIn("1 missed", self.stdout)

    def test_record_carries_g2_6_and_the_merge_reads_it(self):
        sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
        import criteria
        self.main(LIVE)
        record = json.loads(self.out.read_text())
        self.assertEqual([(c["check"], c["criteria"]) for c in record["checks"]],
                         [(f"check_results.py::{LIVE_NAME}", ["G2-6"])])
        self.assertTrue(record["complete"])
        criteria.load_record(self.out, criteria.spec_ids(REPO_ROOT / "spec" / "acceptance.md"), ["test"])

    def test_missing_figures_fail(self):
        self.assertEqual(self.outcomes(live(p95_delay_seconds=None)), {LIVE_NAME: "failed"})
        self.assertEqual(self.outcomes(live(runs=LIVE["figures"][0]["runs"][:2])), {LIVE_NAME: "failed"})
        self.assertEqual(self.outcomes(dict(LIVE, commit=None)), {LIVE_NAME: "failed"})
        self.assertEqual(self.outcomes(dict(LIVE, dirty=True)), {LIVE_NAME: "failed"})

    def test_placeholder_skips(self):
        self.assertEqual(self.outcomes(dict(LIVE, figures=[])), {LIVE_NAME: "skipped"})
        self.assertIn("the Owner's run of lmt eval --set live", self.stdout)

    def test_pr_537_file_passes(self):
        self.assertEqual(self.outcomes(json.loads(LIVE_FIXTURE.read_text())), {LIVE_NAME: "passed"})


class LiveMalformedTest(Fixture):
    assertMalformed = MalformedTest.assertMalformed

    def test_wrong_set(self):
        self.assertMalformed(dict(LIVE, set="ci"), says="set is 'ci', not 'full' or 'live'")

    def test_not_json_live(self):
        self.assertMalformed(raw='{"set": "live"', says="not JSON")

    def test_two_figures(self):
        results = copy.deepcopy(LIVE)
        results["figures"].append(results["figures"][0])
        self.assertMalformed(results, says="holds one object")

    def test_delay_not_a_number(self):
        self.assertMalformed(live(p95_delay_seconds="14"), says="`p95_delay_seconds` must be a number")

    def test_runs_not_a_list(self):
        self.assertMalformed(live(runs={}), says="`runs` must be a list")

    def test_run_field_not_a_number(self):
        runs = copy.deepcopy(LIVE["figures"][0]["runs"])
        runs[1]["behind_seconds"] = True
        self.assertMalformed(live(runs=runs), says="run 2: `behind_seconds` must be a number")


if __name__ == "__main__":
    unittest.main()
