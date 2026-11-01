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


def job_body(job, text):
    return re.search(rf"^  {job}:\n(.*?)(?=^  \S|\Z)", text, re.MULTILINE | re.DOTALL).group(1)


def job_steps(job, text=None):
    """The steps of one job in product.yml (or in `text`), as text, one per step."""
    text = PRODUCT_YML.read_text() if text is None else text
    steps = job_body(job, text).split("\n    steps:\n", 1)[1]
    return re.split(r"\n(?=      - )", steps.strip("\n"))


def called_workflow(job, text=None):
    """The workflow file a product.yml job `uses:`, or None for a job with steps of its own."""
    text = PRODUCT_YML.read_text() if text is None else text
    m = re.search(r"^    uses: \./(\.github/workflows/[\w-]+\.yml)$", job_body(job, text), re.MULTILINE)
    return m and ROOT / m.group(1)


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


# Two writers and a glob, as the `test` job's pytest and check_install.py (design/internal/g3-stage1.md §4a).
NUMBERED = """\
jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - env:
          LMT_TIER: ${{ needs.changes.outputs.tier }}
        run: uv run --frozen pytest --criteria-out=criteria-test-1.json
      - if: ${{ !cancelled() }}
        env:
          LMT_TIER: ${{ needs.changes.outputs.tier }}
        run: python3 -I .github/scripts/check_install.py --criteria-out criteria-test-3.json
      - if: ${{ !cancelled() }}
        uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: criteria-test
          path: criteria-test-*.json
          retention-days: 90
          if-no-files-found: error
"""


# A called workflow's job on a matrix, two writers per runner, as install-full.yml (design/internal/g3-stage1.md §5).
MATRIX = """\
jobs:
  install-full:
    strategy:
      matrix:
        include:
          - os: ubuntu-24.04
            platform: linux-x86_64
            install: 1
            full: 2
          - os: macos-15
            platform: macos-arm64
            install: 3
            full: 4
    runs-on: ${{ matrix.os }}
    steps:
      - env:
          LMT_TIER: ${{ inputs.tier }}
        run: python3 -I check_install.py --criteria-out "$GITHUB_WORKSPACE/criteria-install-full-${{ matrix.install }}.json"
      - if: ${{ !cancelled() }}
        env:
          LMT_TIER: ${{ inputs.tier }}
        run: python3 -I check_full_install.py --criteria-out "$GITHUB_WORKSPACE/criteria-install-full-${{ matrix.full }}.json"
      - if: ${{ !cancelled() }}
        uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: criteria-install-full-${{ matrix.platform }}
          path: criteria-install-full-*.json
          retention-days: 90
          if-no-files-found: error
"""


class UploadTest(unittest.TestCase):
    """Each job in the registry's `jobs` writes its record and uploads it last (§3)."""

    def assert_uploads_its_record_last(self, job, steps, matrix=None):
        """`matrix`: the text of a called workflow, whose job runs on a matrix (g3-stage1.md §5, install-full).
        Its writers name their file by a matrix key, the numbers come from the matrix's rows, each runner
        uploads its own artifact, and the tier comes from the workflow's input."""
        upload = steps[-1]
        self.assertRegex(upload, r"uses: actions/upload-artifact@[0-9a-f]{40} ")
        self.assertIn("if: ${{ !cancelled() }}", upload)
        out = re.compile(r'--criteria-out[= ]"?(?:\$GITHUB_WORKSPACE/)?criteria-' + re.escape(job)
                         + r"(?:-(\d+)|-\$\{\{ matrix\.(\w+) \}\})?\.json\b")
        writers, numbers = [], []
        for step in steps:
            if m := out.search(step):
                writers.append(step)
                if m.group(2):
                    self.assertIsNotNone(matrix, f"criteria-{job}-${{{{ matrix.{m.group(2)} }}}}.json outside a matrix")
                    values = re.findall(rf"^\s+(?:- )?{m.group(2)}: (\d+)$", matrix, re.MULTILINE)
                    self.assertTrue(values, m.group(2))
                    numbers += values
                else:
                    numbers.append(m.group(1))
        self.assertTrue(writers)
        # One file, criteria-<job>.json (written by one step, or by steps of exclusive tiers); or a numbered
        # file per writer (and per matrix row), each its own number, uploaded by a glob.
        if numbers[0] is None:
            self.assertEqual(numbers, [None] * len(numbers))
            path = f"path: criteria-{job}.json"
        else:
            self.assertNotIn(None, numbers)
            self.assertEqual(len(set(numbers)), len(numbers), numbers)
            self.assertGreater(len(numbers), 1)
            path = f"path: criteria-{job}-*.json"
        # A run's artifact names must differ, so each matrix runner uploads its own.
        name = f"name: criteria-{job}" + ("-${{ matrix.platform }}" if matrix is not None else "")
        for line in (name, path, "retention-days: 90", "if-no-files-found: error"):
            self.assertRegex(upload, rf"(?m)^\s+{re.escape(line)}$")
        tier = "inputs.tier" if matrix is not None else "needs.changes.outputs.tier"
        for step in writers:
            self.assertIn(f"LMT_TIER: ${{{{ {tier} }}}}", step)

    def assert_job_records(self, job, text=None):
        """A product.yml job's record: from its own steps, or from those of the workflow it calls, which it
        passes the tier."""
        workflow = called_workflow(job, text)
        if workflow is None:
            self.assert_uploads_its_record_last(job, job_steps(job, text))
            return
        caller = job_body(job, PRODUCT_YML.read_text() if text is None else text)
        self.assertIn("tier: ${{ needs.changes.outputs.tier }}", caller)
        called = workflow.read_text()
        self.assert_uploads_its_record_last(job, job_steps(job, called), matrix=job_body(job, called))

    def test_each_job_uploads_its_record_last(self):
        jobs = tomllib.loads((ROOT / "tests" / "criteria.toml").read_text())["jobs"]
        for job in jobs:
            with self.subTest(job=job):
                self.assert_job_records(job)

    def test_numbered_writers_upload_a_glob(self):
        self.assert_uploads_its_record_last("test", job_steps("test", NUMBERED))

    def test_numbered_writers_with_a_single_file_upload_fail(self):
        single = NUMBERED.replace("path: criteria-test-*.json", "path: criteria-test-1.json")
        with self.assertRaises(AssertionError):
            self.assert_uploads_its_record_last("test", job_steps("test", single))

    def test_numbered_writers_sharing_a_number_fail(self):
        shared = NUMBERED.replace("criteria-test-3.json", "criteria-test-1.json")
        with self.assertRaises(AssertionError):
            self.assert_uploads_its_record_last("test", job_steps("test", shared))

    def assert_matrix(self, text):
        self.assert_uploads_its_record_last("install-full", job_steps("install-full", text),
                                            matrix=job_body("install-full", text))

    def test_matrix_writers_upload_a_glob_per_runner(self):
        self.assert_matrix(MATRIX)

    def test_matrix_rows_sharing_a_number_fail(self):
        with self.assertRaises(AssertionError):
            self.assert_matrix(MATRIX.replace("install: 3", "install: 1"))

    def test_matrix_runners_sharing_an_artifact_name_fail(self):
        with self.assertRaises(AssertionError):
            self.assert_matrix(MATRIX.replace("name: criteria-install-full-${{ matrix.platform }}",
                                              "name: criteria-install-full"))

    def test_matrix_writer_without_the_input_tier_fails(self):
        with self.assertRaises(AssertionError):
            self.assert_matrix(MATRIX.replace("LMT_TIER: ${{ inputs.tier }}",
                                              "LMT_TIER: ${{ needs.changes.outputs.tier }}", 1))

    def test_install_full_calls_its_workflow(self):
        self.assertEqual(called_workflow("install-full"), ROOT / ".github" / "workflows" / "install-full.yml")
        self.assertIsNone(called_workflow("test"))
