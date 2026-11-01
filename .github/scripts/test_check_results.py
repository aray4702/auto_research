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


# A results file, version 2.0 with Kyutai, that meets every bound.
GOOD = {
    "schema_version": "2.0", "set": "full", "engine": "kyutai", "commit": "a" * 40, "dirty": False,
    "date": "2026-10-30T11:38:02-07:00",
    "machine": {"arch": "arm64", "cpu": "Apple M4", "memory_bytes": 17179869184, "os": "macOS 27.0.1"},
    "lmt_version": "0.1.0", "models": {"asr": {"name": "x"}, "diarization": {"name": "y"}},
    "figures": [
        row("LS full", "product", wer=0.023, rtf=0.30, rss=2.2e9),
        row("LS full", "asr-alone", wer=0.023, rtf=0.25),
        row("AMI full", "product", wer=0.243, der=0.235, rtf=0.45, rss=3.9e9),
        row("AMI full", "asr-alone", wer=0.243, rtf=0.20),
        row("AMI full", "pyannote-alone", der=0.188, rtf=0.40),
        row("one-hour", "product", wer=0.337, der=0.309, rtf=0.50, rss=4.0e9),
        row("one-hour", "asr-alone", wer=0.337, rtf=0.24),
        row("one-hour", "pyannote-alone", der=0.244, rtf=0.45),
    ],
    "determinism": {"runs": 2, "identical": True},
}
PLACEHOLDER = {"schema_version": "2.0", "set": "full", "engine": "kyutai", "commit": None, "dirty": False,
               "date": None, "machine": None, "lmt_version": "0.1.0", "models": None, "figures": [],
               "determinism": None}
NAMES = ["G1-8 LS full WER", "G1-8 AMI full WER", "G1-8 AMI full DER", "G1-8 the engine alone and pyannote alone",
         "G1-9 AMI full RTF", "G1-9 one-hour RTF", "G1-10 one-hour peak RSS", "G1-12 two runs identical",
         "G1-14 the table's figures"]


# The Whisper-era file, version 1.0 (results/full.json until G4's stage 2): G1's checks, `whisper-alone` the
# engine alone.
WHISPER_ERA = dict(copy.deepcopy(GOOD), schema_version="1.0",
                   figures=[dict(f, system="whisper-alone" if f["system"] == "asr-alone" else f["system"])
                            for f in GOOD["figures"]])
del WHISPER_ERA["engine"]


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
                              "G1-8 the engine alone and pyannote alone", "G1-14 the table's figures")
        self.assertOnlyFailed(setting(GOOD, "LS full", "asr-alone", wer=None),
                              "G1-8 the engine alone and pyannote alone", "G1-14 the table's figures")

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
        self.assertMalformed(dict(GOOD, schema_version="3.0"), says="not a version 2.0")

    def test_other_set(self):
        self.assertMalformed(dict(GOOD, set="ci"), says="set is 'ci'")

    def test_other_engine(self):
        self.assertMalformed(dict(GOOD, engine="whisper"), says="engine is 'whisper', not one of kyutai, apple")
        results = dict(GOOD)
        del results["engine"]
        self.assertMalformed(results, says="engine is None")

    def test_whisper_alone_only_in_version_1_0(self):
        results = copy.deepcopy(GOOD)
        results["figures"][1]["system"] = "whisper-alone"
        self.assertMalformed(results, says="`system` of product, asr-alone, pyannote-alone")
        self.assertMalformed(dict(WHISPER_ERA, set="live"), says="set is 'live', not 'full'")
        self.assertMalformed(dict(WHISPER_ERA, figures=GOOD["figures"]), says="whisper-alone")

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


class WhisperEraTest(Fixture):
    """The committed 1.0 file is read for G1 as before, until G4's stage 2 replaces it."""

    def test_passes_g1s_checks(self):
        self.assertEqual(self.outcomes(WHISPER_ERA), {n: "passed" for n in NAMES})

    def test_its_engine_alone_is_whisper_alone(self):
        self.assertOnlyFailed(setting(WHISPER_ERA, "AMI full", "whisper-alone", wer=None),
                              "G1-8 the engine alone and pyannote alone", "G1-14 the table's figures")


def live_runs(*p95s, label=1.5, behind=0.4):
    return [{"p95_delay_seconds": p, "p95_label_delay_seconds": label, "behind_seconds": behind} for p in p95s]


# results/live.json with Kyutai, version 2.0 (src/lmt/eval/live.py), inside G2-6's targets.
LIVE = {
    "schema_version": "2.0", "set": "live", "engine": "kyutai", "commit": "b" * 40, "dirty": False,
    "date": "2026-10-31T16:30:35-07:00", "machine": GOOD["machine"], "lmt_version": "0.3.0",
    "models": GOOD["models"],
    "figures": [{"dataset": "AMI-ci", "system": "product-live", "wer": 0.17, "der": 0.30, "audio_seconds": 900.0,
                 "wall_seconds": 905.6, "rtf": 1.006, "peak_rss_bytes": None, "p50_delay_seconds": 0.62,
                 "p95_delay_seconds": 0.89, "max_delay_seconds": 1.4, "p50_label_delay_seconds": 1.9,
                 "p95_label_delay_seconds": 2.6, "max_label_delay_seconds": 3.9, "matched_words": 950,
                 "runs": live_runs(0.87, 0.9, 0.88)}],
    "determinism": None,
}
LIVE_NAMES = ["G2-6 live p95 word delay", "G2-6 live p95 label delay"]


def live(results=None, **values):
    results = copy.deepcopy(LIVE if results is None else results)
    results["figures"][0].update(values)
    return results


class LiveTest(Fixture):
    """G2-6 from results/live.json: p95 word delay < 1.0 s, p95 label delay ≤ 3.0 s; a miss recorded, exit 0."""

    def test_passes(self):
        self.assertEqual(self.outcomes(LIVE), {n: "passed" for n in LIVE_NAMES})
        self.assertIn("PASS G2-6 live p95 word delay: pooled p95 word delay 0.89 s over 3 runs < 1.00 s", self.stdout)
        self.assertIn("PASS G2-6 live p95 label delay: pooled p95 label delay 2.60 s over 3 runs ≤ 3.00 s",
                      self.stdout)

    def test_word_bound_is_strict(self):
        self.assertOnlyFailed(live(p95_delay_seconds=0.999))
        self.assertOnlyFailed(live(p95_delay_seconds=1.0), "G2-6 live p95 word delay")
        self.assertIn("FAIL G2-6 live p95 word delay: pooled p95 word delay 1.00 s over 3 runs, not < 1.00 s",
                      self.stdout)

    def test_label_bound_is_inclusive(self):
        self.assertOnlyFailed(live(p95_label_delay_seconds=3.0))
        self.assertOnlyFailed(live(p95_label_delay_seconds=3.01), "G2-6 live p95 label delay")
        self.assertIn("1 missed", self.stdout)

    def test_record_carries_g2_6_and_the_merge_reads_it(self):
        sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
        import criteria
        self.main(LIVE)
        record = json.loads(self.out.read_text())
        self.assertEqual([(c["check"], c["criteria"]) for c in record["checks"]],
                         [(f"check_results.py::{n}", ["G2-6"]) for n in LIVE_NAMES])
        criteria.load_record(self.out, criteria.spec_ids(REPO_ROOT / "spec" / "acceptance.md"), ["test"])

    def test_missing_figures_fail_both(self):
        both = LIVE_NAMES
        self.assertOnlyFailed(live(p95_label_delay_seconds=None), *both)
        self.assertOnlyFailed(live(matched_words=None), *both)
        self.assertOnlyFailed(live(der=None), *both)
        self.assertOnlyFailed(live(runs=live_runs(0.8, 0.9)), *both)
        self.assertOnlyFailed(live(runs=live_runs(0.8, 0.9, None)), *both)
        self.assertOnlyFailed(dict(LIVE, commit=None), *both)
        self.assertOnlyFailed(dict(LIVE, dirty=True), *both)

    def test_placeholder_skips(self):
        self.assertEqual(self.outcomes(dict(LIVE, figures=[])), {n: "skipped" for n in LIVE_NAMES})
        self.assertIn("the Owner's run of lmt eval --set live", self.stdout)


class LiveMalformedTest(Fixture):
    assertMalformed = MalformedTest.assertMalformed

    def test_two_figures(self):
        results = copy.deepcopy(LIVE)
        results["figures"].append(dict(results["figures"][0], dataset="other"))
        self.assertMalformed(results, says="holds one figure")

    def test_other_system(self):
        self.assertMalformed(live(system="product"), says="`system` of product-live")

    def test_delay_not_a_number(self):
        self.assertMalformed(live(p95_label_delay_seconds="2"), says="`p95_label_delay_seconds` must be a number")

    def test_runs_not_a_list(self):
        self.assertMalformed(live(runs={}), says="`runs` must be a list")

    def test_run_field_not_a_number(self):
        runs = live_runs(0.8, 0.9, 0.9)
        runs[1]["behind_seconds"] = True
        self.assertMalformed(live(runs=runs), says="run 2: `behind_seconds` must be a number")


APPLE_MACHINE = dict(GOOD["machine"], os="macOS 26.6.2 (25G5034)")
APPLE_ASR = {"name": "SpeechTranscriber", "engine": "apple", "repository": "com.apple.Speech",
             "revision": "macOS 26.6.2 (25G5034) en_US"}
# results/full-apple.json: G4-3's reference clip in both modes, then G4-4's and G4-5's figures.
FULL_APPLE = {
    "schema_version": "2.0", "set": "full", "engine": "apple", "commit": "d" * 40, "dirty": False,
    "date": "2026-11-01T09:00:00-07:00", "machine": APPLE_MACHINE, "lmt_version": "0.3.0",
    "models": {"asr": APPLE_ASR, "diarization": {"name": "y"}},
    "figures": [row("reference clip", "product", wer=0.031, rtf=0.2),
                row("reference clip", "product-live", wer=0.042, rtf=1.0)] + copy.deepcopy(GOOD["figures"]),
    "determinism": None,
}
APPLE_NAMES = ["G4-3 reference clip WER, batch", "G4-3 reference clip WER, live", "G4-4 LS full WER",
               "G4-4 AMI full WER", "G4-4 AMI full DER", "G4-4 the engine alone and pyannote alone",
               "G4-5 one-hour peak RSS, lmt and the helper", "G4-6 Apple's version record",
               "G1-14 the table's figures"]
LIVE_APPLE = dict(copy.deepcopy(LIVE), engine="apple", machine=APPLE_MACHINE,
                  models={"asr": APPLE_ASR, "diarization": {"name": "y"}})
LIVE_APPLE_NAMES = ["G4-8 Apple's live delays", "G4-6 Apple's version record"]


class AppleTest(Fixture):
    """results/full-apple.json against G4-3 to G4-6, and results/live-apple.json against G4-6 and G4-8."""

    def test_full_passes_every_check(self):
        self.assertEqual(self.outcomes(FULL_APPLE), {n: "passed" for n in APPLE_NAMES})

    def test_reference_clip_in_both_modes(self):
        self.assertOnlyFailed(setting(FULL_APPLE, "reference clip", "product", wer=0.05))
        self.assertOnlyFailed(setting(FULL_APPLE, "reference clip", "product", wer=0.0501),
                              "G4-3 reference clip WER, batch")
        self.assertOnlyFailed(setting(FULL_APPLE, "reference clip", "product-live", wer=0.0501),
                              "G4-3 reference clip WER, live")
        self.assertIn("FAIL G4-3 reference clip WER, live: reference clip product-live wer 0.050 > 0.050",
                      self.stdout)
        results = copy.deepcopy(FULL_APPLE)
        del results["figures"][1]  # the live run's
        self.assertOnlyFailed(results, "G4-3 reference clip WER, live")

    def test_accuracy_and_memory_bounds(self):
        self.assertOnlyFailed(setting(FULL_APPLE, "LS full", "product", wer=0.0501), "G4-4 LS full WER")
        self.assertOnlyFailed(setting(FULL_APPLE, "AMI full", "product", wer=0.2501), "G4-4 AMI full WER")
        self.assertOnlyFailed(setting(FULL_APPLE, "AMI full", "product", der=0.2501), "G4-4 AMI full DER")
        self.assertOnlyFailed(setting(FULL_APPLE, "one-hour", "product", peak_rss_bytes=4_000_000_001),
                              "G4-5 one-hour peak RSS, lmt and the helper")

    def test_rtf_has_no_bound(self):
        self.assertOnlyFailed(setting(FULL_APPLE, "AMI full", "product", rtf=3.0))

    def test_lacking_the_version_record_fails(self):
        for results in (dict(FULL_APPLE, machine=GOOD["machine"]),
                        dict(FULL_APPLE, models=GOOD["models"]),
                        dict(FULL_APPLE, models={"asr": dict(APPLE_ASR, revision=None), "diarization": None}),
                        dict(FULL_APPLE, determinism={"runs": 2, "identical": True})):
            with self.subTest(results=results):
                self.assertOnlyFailed(results, "G4-6 Apple's version record")
        self.assertOnlyFailed(dict(LIVE_APPLE, machine=GOOD["machine"]), "G4-6 Apple's version record")

    def test_live_has_no_bound(self):
        self.assertEqual(self.outcomes(live(LIVE_APPLE, p95_delay_seconds=4.0, p95_label_delay_seconds=9.0)),
                         {n: "passed" for n in LIVE_APPLE_NAMES})
        self.assertIn("PASS G4-8 Apple's live delays: p95 word delay 4.00 s, p95 label delay 9.00 s", self.stdout)

    def test_live_needs_its_figures(self):
        self.assertOnlyFailed(live(LIVE_APPLE, p95_label_delay_seconds=None), "G4-8 Apple's live delays")
        self.assertOnlyFailed(live(LIVE_APPLE, runs=live_runs(1.0)), "G4-8 Apple's live delays")

    def test_records_carry_g4s_ids_and_the_merge_reads_them(self):
        sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
        import criteria
        ids = criteria.spec_ids(REPO_ROOT / "spec" / "acceptance.md")
        for results in (FULL_APPLE, LIVE_APPLE):
            self.main(results)
            criteria.load_record(self.out, ids, ["test"])

    def test_placeholder_skips(self):
        self.assertEqual(self.outcomes(dict(FULL_APPLE, figures=[])), {n: "skipped" for n in APPLE_NAMES})


if __name__ == "__main__":
    unittest.main()
