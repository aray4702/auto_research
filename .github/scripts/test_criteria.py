"""Tests for the merge into each criterion's state (design/internal/criteria.md §4).

Run: python3 -I -m unittest discover -s .github/scripts -t .github/scripts
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import criteria

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN = "refs/heads/main"

# A small spec: a goal without a Requirement column, and one with it.
SPEC = """\
# Acceptance

## Contents

| ID | Not a criterion |
| --- | --- |
| G9-1 | outside a done-check |

## S1: seed

### Done-check

| ID | Criterion | How it's checked | Runs |
| --- | --- | --- | --- |
| S1-1 | One. | x | CI |

## G1: first goal

### Terms

| ID | Not a criterion either |
| --- | --- |
| G1-99 | a term |

### Done-check

| ID | Requirement | Criterion | How it's checked | Runs |
| --- | --- | --- | --- | --- |
| G1-1 | F1 | No check. | x | CI |
| G1-2 | F2 | Failing. | x | CI |
| G1-3 | F3 | Never run. | x | CI |
| G1-4 | F4 | Waiting. | x | CI |
| G1-5 | F5 | Partial. | x | CI |
| G1-6 | §7, Q9 | Passing. | x | CI |
| G1-6m | F5 | By eye. | x | Mac |
| G1-6c | Q1 | Proxy. | x | CI |

### Stages

| ID | Not a criterion |
| --- | --- |
| G1-7 | a stage |
"""

REGISTRY = """\
jobs = ["readme", "test"]

[G1-6]
complete = true

[G1-6m]
ci = false
owner = "Look at it"
"""


def check(name, ids, outcome, reason=None):
    c = {"check": name, "criteria": ids, "outcome": outcome}
    return c | ({"reason": reason} if reason is not None else {})


def rec(job, run_id, checks, commit=None, ref=MAIN, complete=True, attempt=1):
    return {"format": 1, "job": job, "commit": commit or f"c{run_id}", "ref": ref, "event": "push",
            "run_id": run_id, "run_attempt": attempt, "tier": 1, "complete": complete, "checks": checks}


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.spec = self.write("acceptance.md", SPEC)
        self.registry = self.write("criteria.toml", REGISTRY)
        self.n = 0

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return path

    def records(self, *records):
        paths = []
        for r in records:
            self.n += 1
            paths.append(self.write(f"r{self.n}.json", r if isinstance(r, str) else json.dumps(r)))
        return paths

    def run_merge(self, *records, ref=None):
        argv = ["--spec", str(self.spec), "--registry", str(self.registry)]
        argv += ["--ref", ref] if ref else []
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = criteria.main(argv + [str(p) for p in self.records(*records)])
        return code, out.getvalue(), err.getvalue()

    def merge(self, *records, ref=None):
        code, out, err = self.run_merge(*records, ref=ref)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        result["by_id"] = {c["id"]: c for c in result["criteria"]}
        return result

    def states(self, *records, ref=None):
        return {c["id"]: c["ci"] for c in self.merge(*records, ref=ref)["criteria"]}


class RealSpecTest(unittest.TestCase):
    def test_counts_and_requirements(self):
        found = criteria.parse_spec((REPO_ROOT / "spec" / "acceptance.md").read_text())
        self.assertEqual(len(found), 44)
        by_goal = {}
        for c in found:
            by_goal[c["goal"]] = by_goal.get(c["goal"], 0) + 1
        self.assertEqual(by_goal, {"S1": 5, "G1": 17, "G2": 10, "G3": 12})
        by_id = {c["id"]: c for c in found}
        self.assertEqual(by_id["G1-15"]["requirements"], ["§7", "Q9"])
        self.assertEqual(by_id["S1-1"]["requirements"], ["S1"])
        self.assertEqual(by_id["G2-3"]["requirements"], ["F6", "U2"])
        self.assertEqual([c["id"] for c in found][:6], ["S1-1", "S1-2", "S1-3", "S1-4", "S1-5", "G1-1"])

    def test_real_registry_loads(self):
        ids = criteria.spec_ids(REPO_ROOT / "spec" / "acceptance.md")
        jobs, entries = criteria.load_registry(REPO_ROOT / "tests" / "criteria.toml", ids)
        self.assertEqual(jobs, ["readme", "test", "test-models"])
        self.assertEqual({k for k, e in entries.items() if e["complete"]}, {"S1-1", "S1-2", "S1-3", "S1-5"})
        self.assertEqual({k for k, e in entries.items() if e["owner"]},
                         {"G1-4m", "G1-13", "G2-5m", "G3-5m", "G3-8m"})


class SpecTest(Fixture):
    def test_only_done_check_rows_count(self):
        found = criteria.parse_spec(SPEC)
        self.assertEqual([c["id"] for c in found],
                         ["S1-1", "G1-1", "G1-2", "G1-3", "G1-4", "G1-5", "G1-6", "G1-6m", "G1-6c"])
        self.assertEqual(found[0], {"id": "S1-1", "goal": "S1", "requirements": ["S1"]})
        self.assertEqual(found[6]["requirements"], ["§7", "Q9"])


class StateTest(Fixture):
    """§4's table, rule by rule."""

    def test_each_state(self):
        result = self.merge(
            rec("test", 1, [
                check("t::fail_a", ["G1-2"], "passed"), check("t::fail_b", ["G1-2"], "failed"),
                check("t::notrun", ["G1-3"], "not-run", "tier 0 runs no install"),
                check("t::wait", ["G1-4"], "skipped", "no figures yet"), check("t::wait2", ["G1-4"], "passed"),
                check("t::partial", ["G1-5"], "passed"),
                check("t::pass", ["G1-6"], "passed"),
            ]))
        by_id = result["by_id"]
        self.assertEqual({k: v["ci"] for k, v in by_id.items()}, {
            "S1-1": "no-check", "G1-1": "no-check", "G1-2": "fail", "G1-3": "not-run", "G1-4": "waiting",
            "G1-5": "partial", "G1-6": "pass", "G1-6m": "not-in-ci", "G1-6c": "no-check"})
        self.assertEqual(by_id["G1-4"]["reasons"], ["no figures yet"])
        self.assertEqual(by_id["G1-3"]["reasons"], ["tier 0 runs no install"])
        self.assertEqual(by_id["G1-2"]["checks"], {"passed": 1, "failed": 1, "skipped": 0, "not-run": 0})
        self.assertEqual(by_id["G1-6"]["commits"], ["c1"])
        self.assertEqual(by_id["G1-6"]["requirements"], ["§7", "Q9"])
        self.assertEqual(by_id["G1-6m"]["owner"], "Look at it")
        self.assertIsNone(by_id["G1-5"]["owner"])
        self.assertEqual(result["jobs"], {"readme": None, "test": {"commit": "c1", "run_id": 1, "run_attempt": 1}})

    def test_rules_apply_in_order(self):
        # fail before not-run before waiting, whatever the checks' order.
        self.assertEqual(self.states(rec("test", 1, [
            check("a", ["G1-5"], "skipped", "r"), check("b", ["G1-5"], "not-run", "r"),
            check("c", ["G1-5"], "failed")]))["G1-5"], "fail")
        self.assertEqual(self.states(rec("test", 2, [
            check("a", ["G1-5"], "skipped", "r"), check("b", ["G1-5"], "not-run", "r")]))["G1-5"], "not-run")

    def test_complete_with_a_failure_is_fail(self):
        self.assertEqual(self.states(rec("test", 1, [check("a", ["G1-6"], "failed")]))["G1-6"], "fail")

    def test_one_check_counts_for_each_of_its_criteria(self):
        states = self.states(rec("test", 1, [check("a", ["G1-5", "G1-6"], "passed")]))
        self.assertEqual((states["G1-5"], states["G1-6"]), ("partial", "pass"))

    def test_checks_from_several_jobs_combine(self):
        states = self.states(rec("readme", 1, [check("r", ["G1-6"], "passed")]),
                             rec("test", 2, [check("t", ["G1-6"], "failed")]))
        self.assertEqual(states["G1-6"], "fail")

    def test_no_records(self):
        result = self.merge()
        self.assertEqual(result["jobs"], {"readme": None, "test": None})
        self.assertEqual({c["ci"] for c in result["criteria"]}, {"no-check", "not-in-ci"})


class HistoryTest(Fixture):
    def test_not_run_takes_the_earlier_outcome(self):
        result = self.merge(
            rec("readme", 1, [check("S1-3", ["G1-5"], "failed")]),
            rec("readme", 2, [check("S1-3", ["G1-5"], "passed")]),
            rec("readme", 3, [check("S1-3", ["G1-5"], "not-run", "tier 0 runs no install")]),
            rec("readme", 4, [check("S1-3", ["G1-5"], "not-run", "tier 0 runs no install")]))
        g = result["by_id"]["G1-5"]
        self.assertEqual((g["ci"], g["commits"], g["reasons"]), ("partial", ["c2"], []))
        self.assertEqual(result["jobs"]["readme"]["run_id"], 4)

    def test_not_run_without_an_earlier_outcome_stays(self):
        g = self.merge(rec("readme", 1, [check("S1-3", ["G1-5"], "not-run", "r")]))["by_id"]["G1-5"]
        self.assertEqual((g["ci"], g["checks"]["not-run"]), ("not-run", 1))

    def test_latest_record_decides(self):
        states = self.states(rec("test", 2, [check("a", ["G1-5"], "passed")]),
                             rec("test", 1, [check("a", ["G1-5"], "failed")]))
        self.assertEqual(states["G1-5"], "partial")
        states = self.states(rec("test", 3, [check("a", ["G1-5"], "passed")], attempt=1),
                             rec("test", 3, [check("a", ["G1-5"], "failed")], attempt=2))
        self.assertEqual(states["G1-5"], "fail")

    def test_other_refs_are_ignored(self):
        records = [rec("test", 1, [check("a", ["G1-5"], "passed")]),
                   rec("test", 2, [check("a", ["G1-5"], "failed")], ref="refs/heads/topic"),
                   rec("test", 3, [check("a", ["G1-5"], "skipped", "r")], ref="refs/pull/9/merge")]
        self.assertEqual(self.states(*records)["G1-5"], "partial")
        self.assertEqual(self.states(*records, ref="refs/pull/9/merge")["G1-5"], "waiting")

    def test_a_dropped_check_drops_out(self):
        states = self.states(rec("test", 1, [check("a", ["G1-5"], "failed"), check("b", ["G1-6"], "passed")]),
                             rec("test", 2, [check("b", ["G1-6"], "passed")]))
        self.assertEqual((states["G1-5"], states["G1-6"]), ("no-check", "pass"))

    def test_an_incomplete_latest_record_is_skipped(self):
        result = self.merge(rec("test", 1, [check("a", ["G1-5"], "passed"), check("b", ["G1-6"], "passed")]),
                            rec("test", 2, [check("a", ["G1-5"], "failed")], complete=False))
        self.assertEqual((result["by_id"]["G1-5"]["ci"], result["by_id"]["G1-6"]["ci"]), ("partial", "pass"))
        self.assertEqual(result["jobs"]["test"], {"commit": "c1", "run_id": 1, "run_attempt": 1})

    def test_a_job_with_only_incomplete_records_is_null(self):
        result = self.merge(rec("test", 1, [check("a", ["G1-5"], "passed")], complete=False))
        self.assertIsNone(result["jobs"]["test"])
        self.assertEqual(result["by_id"]["G1-5"]["ci"], "no-check")

    def test_one_runs_files_are_joined(self):
        result = self.merge(rec("test", 5, [check("a", ["G1-5"], "passed")]),
                            rec("test", 5, [check("b", ["G1-6"], "passed")]))
        self.assertEqual((result["by_id"]["G1-5"]["ci"], result["by_id"]["G1-6"]["ci"]), ("partial", "pass"))

    def test_joined_files_are_skipped_together_when_one_is_incomplete(self):
        result = self.merge(rec("test", 4, [check("a", ["G1-5"], "failed"), check("b", ["G1-6"], "failed")]),
                            rec("test", 5, [check("a", ["G1-5"], "passed")]),
                            rec("test", 5, [check("b", ["G1-6"], "passed")], complete=False))
        self.assertEqual((result["by_id"]["G1-5"]["ci"], result["by_id"]["G1-6"]["ci"]), ("fail", "fail"))
        self.assertEqual(result["jobs"]["test"]["run_id"], 4)


class OutputTest(Fixture):
    def test_out_file(self):
        out = self.dir / "criteria.json"
        code = criteria.main(["--spec", str(self.spec), "--registry", str(self.registry), "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.read_text())["format"], 1)


class MalformedTest(Fixture):
    def assertError(self, *records, says):
        code, out, err = self.run_merge(*records)
        self.assertEqual((code, out), (3, ""))
        self.assertEqual(err.count("\n"), 1, err)
        self.assertTrue(err.startswith("criteria.py: error: "), err)
        self.assertIn(says, err)

    def test_record_of_another_format(self):
        self.assertError(dict(rec("test", 1, []), format=2), says="format 1")

    def test_record_without_complete(self):
        r = rec("test", 1, [])
        del r["complete"]
        self.assertError(r, says="`complete` is missing")

    def test_record_not_json(self):
        self.assertError("{", says="not JSON")

    def test_missing_record(self):
        argv = ["--spec", str(self.spec), "--registry", str(self.registry), str(self.dir / "nope.json")]
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = criteria.main(argv)
        self.assertEqual(code, 3)
        self.assertIn("nope.json", err.getvalue())

    def test_unknown_id_in_a_record(self):
        self.assertError(rec("test", 1, [check("a", ["G1-99"], "passed")]), says="'G1-99'")

    def test_job_not_in_the_registry(self):
        self.assertError(rec("eval-ci", 1, []), says="'eval-ci'")

    def test_unknown_outcome(self):
        self.assertError(rec("test", 1, [check("a", ["G1-5"], "xfailed")]), says="'xfailed'")

    def test_skipped_without_a_reason(self):
        self.assertError(rec("test", 1, [check("a", ["G1-5"], "skipped")]), says="needs a reason")

    def test_unknown_id_in_the_registry(self):
        self.registry.write_text(REGISTRY + "\n[G1-7]\ncomplete = true\n")
        self.assertError(says="[G1-7]")

    def test_unknown_registry_key(self):
        self.registry.write_text(REGISTRY + "\n[G1-5]\ndone = true\n")
        self.assertError(says="'done'")
        self.registry.write_text(REGISTRY.replace("jobs =", "tier = 1\njobs ="))
        self.assertError(says="'tier'")

    def test_registry_without_jobs(self):
        self.registry.write_text(REGISTRY.replace('jobs = ["readme", "test"]', ""))
        self.assertError(says="`jobs`")

    def test_registry_wrong_type(self):
        self.registry.write_text(REGISTRY.replace("complete = true", 'complete = "yes"'))
        self.assertError(says="complete must be a bool")

    def test_ci_false_needs_owner(self):
        self.registry.write_text(REGISTRY + "\n[G1-5]\nci = false\n")
        self.assertError(says="without owner")

    def test_complete_with_ci_false(self):
        self.registry.write_text(REGISTRY.replace('owner = "Look at it"', 'owner = "x"\ncomplete = true'))
        self.assertError(says="complete with ci = false")

    def test_registry_not_toml(self):
        self.registry.write_text("[")
        self.assertError(says="not TOML")

    def test_missing_spec(self):
        self.spec.unlink()
        self.assertError(says="acceptance.md")


class WriteRecordTest(unittest.TestCase):
    def test_job_of(self):
        self.assertEqual(criteria.job_of("x/criteria-test.json"), "test")
        self.assertEqual(criteria.job_of("criteria-test-2.json"), "test")
        self.assertEqual(criteria.job_of("criteria-test-models.json"), "test-models")
        self.assertEqual(criteria.job_of("criteria-eval-ci-1.json"), "eval-ci")
        self.assertIsNone(criteria.job_of("record.json"))

    def test_writes_once_with_the_run_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "criteria-test-1.json"
            env = {"GITHUB_SHA": "abc", "GITHUB_REF": MAIN, "GITHUB_EVENT_NAME": "push",
                   "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "1", "LMT_TIER": "2"}
            with mock.patch.dict(os.environ, env):
                criteria.write_record(path, [check("a", ["G1-5"], "passed")], complete=True)
            self.assertEqual(os.listdir(tmp), [path.name])
            self.assertEqual(json.loads(path.read_text()), rec("test", 12, [check("a", ["G1-5"], "passed")],
                                                               commit="abc", attempt=1) | {"tier": 2})

    def test_bad_name_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            criteria.write_record(Path(tmp) / "out.json", [], complete=True)


if __name__ == "__main__":
    unittest.main()
