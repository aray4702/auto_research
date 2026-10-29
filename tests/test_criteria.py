"""The `criterion` marker's plugin, the registry and the upload steps (design/internal/criteria.md §1–§3)."""

import contextlib
import importlib.util
import io
import json
import re
import tempfile
import tomllib
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_YML = ROOT / ".github" / "workflows" / "product.yml"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


criteria = load("lmt_ci_criteria_under_test", ROOT / ".github" / "scripts" / "criteria.py")
# The plugin under test, a second copy of tests/conftest.py, so the runs below are separate sessions.
plugin = load("lmt_criteria_plugin_under_test", Path(__file__).with_name("conftest.py"))

TAGGED = '''\
import pytest

pytestmark = pytest.mark.criterion("G1-6")

def test_passes():
    pass

def test_fails():
    assert False

@pytest.fixture
def broken():
    raise RuntimeError("setup")

def test_errors(broken):
    pass

def test_skipped():
    pytest.skip("no figures yet")

@pytest.mark.xfail(reason="known")
def test_xfail():
    assert False

@pytest.mark.xfail(reason="known")
def test_xpass():
    pass

@pytest.mark.criterion("G1-14", "G1-6")
def test_two_criteria():
    pass

@pytest.mark.criterion("S1-1")
@pytest.mark.slow
def test_deselected():
    pass
'''

UNTAGGED = "def test_untagged():\n    pass\n"


def job_steps(job):
    """The steps of one job in product.yml, as text, one per step."""
    text = PRODUCT_YML.read_text()
    body = re.search(rf"^  {job}:\n(.*?)(?=^  \S|\Z)", text, re.MULTILINE | re.DOTALL).group(1)
    steps = body.split("\n    steps:\n", 1)[1]
    return re.split(r"\n(?=      - )", steps.strip("\n"))


class Session(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "pytest.ini").write_text("[pytest]\nmarkers =\n    criterion: x\n    slow: x\n")
        self.out = self.dir / "criteria-test.json"

    def pytest(self, modules, *args):
        for name, text in modules.items():
            (self.dir / name).write_text(text)
        out, err = io.StringIO(), io.StringIO()
        argv = [str(self.dir), "-c", str(self.dir / "pytest.ini"), "--rootdir", str(self.dir),
                "-p", "no:cacheprovider", "--import-mode=importlib", "-q", "--criteria-out", str(self.out), *args]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pytest.main(argv, plugins=[plugin])
        return code, out.getvalue() + err.getvalue()

    def record(self):
        self.assertEqual(sorted(p.name for p in self.dir.glob("*criteria-test*")), [self.out.name])
        return json.loads(self.out.read_text())


class PluginTest(Session):
    def test_record_of_each_outcome(self):
        code, out = self.pytest({"test_tagged.py": TAGGED, "test_untagged.py": UNTAGGED}, "-m", "not slow")
        self.assertEqual(code, pytest.ExitCode.TESTS_FAILED, out)
        record = self.record()
        self.assertEqual((record["format"], record["job"], record["complete"]), (1, "test", True))
        checks = {c["check"].split("::")[-1]: c for c in record["checks"]}
        self.assertEqual({name: c["outcome"] for name, c in checks.items()}, {
            "test_passes": "passed", "test_fails": "failed", "test_errors": "failed",
            "test_skipped": "skipped", "test_xfail": "failed", "test_xpass": "failed",
            "test_two_criteria": "passed"})
        self.assertEqual(checks["test_skipped"]["reason"], "no figures yet")
        self.assertNotIn("reason", checks["test_passes"])
        self.assertEqual(checks["test_two_criteria"]["criteria"], ["G1-14", "G1-6"])
        self.assertEqual(checks["test_passes"]["check"], "test_tagged.py::test_passes")

    def test_an_unknown_id_fails_collection(self):
        module = UNTAGGED + '\nimport pytest\n\n@pytest.mark.criterion("G1-99")\ndef test_bad():\n    pass\n'
        code, out = self.pytest({"test_bad.py": module})
        self.assertEqual(code, pytest.ExitCode.USAGE_ERROR, out)
        self.assertIn("test_bad.py::test_bad: criterion 'G1-99' is not an ID", out)
        self.assertFalse(self.record()["complete"])

    def test_an_unknown_id_on_a_deselected_test_fails_too(self):
        module = '\nimport pytest\n\n@pytest.mark.criterion("G1-6x")\n@pytest.mark.slow\ndef test_bad():\n    pass\n'
        code, out = self.pytest({"test_bad_deselected.py": UNTAGGED + module}, "-m", "not slow")
        self.assertEqual(code, pytest.ExitCode.USAGE_ERROR, out)
        self.assertIn("test_bad_deselected.py::test_bad: criterion 'G1-6x'", out)

    def test_pytest_exit_writes_an_incomplete_record(self):
        module = TAGGED.replace("def test_fails():\n    assert False", "def test_fails():\n    pytest.exit('stop')")
        code, out = self.pytest({"test_tagged_exit.py": module}, "-m", "not slow")
        self.assertEqual(code, pytest.ExitCode.INTERRUPTED, out)
        self.assertFalse(self.record()["complete"])

    def test_a_session_stopped_early_is_incomplete(self):
        code, out = self.pytest({"test_tagged.py": TAGGED}, "-m", "not slow", "-x")
        self.assertEqual(code, pytest.ExitCode.TESTS_FAILED, out)
        self.assertFalse(self.record()["complete"])

    def test_no_tests_is_complete(self):
        code, out = self.pytest({"test_untagged.py": UNTAGGED}, "-k", "nothing")
        self.assertEqual(code, pytest.ExitCode.NO_TESTS_COLLECTED, out)
        self.assertEqual((self.record()["complete"], self.record()["checks"]), (True, []))

    def test_the_record_needs_a_job_name(self):
        self.out = self.dir / "record.json"
        code, out = self.pytest({"test_untagged.py": UNTAGGED})
        self.assertEqual(code, pytest.ExitCode.USAGE_ERROR, out)
        self.assertIn("criteria-<job>.json", out)


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.registry = tomllib.loads((ROOT / "tests" / "criteria.toml").read_text())
        self.ids = criteria.spec_ids(ROOT / "spec" / "acceptance.md")

    def test_registry_loads_against_the_spec(self):
        criteria.load_registry(ROOT / "tests" / "criteria.toml", self.ids)

    def test_every_key_is_a_spec_id_with_known_keys(self):
        entries = {k: v for k, v in self.registry.items() if k != "jobs"}
        self.assertLessEqual(set(entries), self.ids)
        for key, entry in entries.items():
            with self.subTest(key=key):
                self.assertLessEqual(set(entry), {"ci", "complete", "owner"})

    def test_ci_false_comes_with_owner_and_without_complete(self):
        for key, entry in self.registry.items():
            if key != "jobs" and entry.get("ci") is False:
                with self.subTest(key=key):
                    self.assertIn("owner", entry)
                    self.assertNotIn("complete", entry)

    def test_jobs_are_product_jobs(self):
        product_jobs = set(re.findall(r"^  ([\w-]+):$", PRODUCT_YML.read_text(), re.MULTILINE))
        self.assertTrue(self.registry["jobs"])
        self.assertLessEqual(set(self.registry["jobs"]), product_jobs)


class UploadTest(unittest.TestCase):
    """Each job in the registry's `jobs` writes its record and uploads it last (§3)."""

    def test_each_job_uploads_its_record_last(self):
        jobs = tomllib.loads((ROOT / "tests" / "criteria.toml").read_text())["jobs"]
        for job in jobs:
            with self.subTest(job=job):
                steps = job_steps(job)
                upload = steps[-1]
                self.assertRegex(upload, r"uses: actions/upload-artifact@[0-9a-f]{40} ")
                self.assertIn("if: ${{ !cancelled() }}", upload)
                for line in (f"name: criteria-{job}", f"path: criteria-{job}.json", "retention-days: 90",
                             "if-no-files-found: error"):
                    self.assertIn(line, upload)
                writers = [s for s in steps if re.search(rf"--criteria-out[= ]criteria-{job}\.json\b", s)]
                self.assertTrue(writers)
                for step in writers:
                    self.assertIn("LMT_TIER: ${{ needs.changes.outputs.tier }}", step)
