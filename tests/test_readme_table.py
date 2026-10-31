"""README.md's results table and `lmt eval --check-readme` (G1-14, design §6)."""

import contextlib
import copy
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import pytest

from lmt import cli
from lmt.errors import InputError
from lmt.eval import readme

pytestmark = pytest.mark.criterion("G1-14")

ROOT = Path(__file__).resolve().parents[1]

# A fixed results file with figures, version 1.0 (public-api.md, "Results file").
RESULTS = {
    "schema_version": "1.0",
    "set": "full",
    "commit": "0123456789abcdef0123456789abcdef01234567", "dirty": False,
    "date": "2026-10-28T14:00:00-07:00",
    "machine": {"arch": "arm64", "cpu": "Apple M4", "memory_bytes": 17179869184, "os": "macOS 27.0"},
    "lmt_version": "0.1.0",
    "models": {
        "asr": {"name": "distil-large-v3", "repository": "Systran/faster-distil-whisper-large-v3",
                "revision": "c9f6a3b2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7a6"},
        "diarization": {"name": "speaker-diarization-3.1", "repository": "pyannote/speaker-diarization-3.1",
                        "revision": "84fd25912480287da0247647c3d2b4853cb3ee5d"},
    },
    "figures": [
        {"dataset": "LS full", "system": "product", "wer": 0.031234, "der": None,
         "audio_seconds": 19452.5, "wall_seconds": 4085.025, "rtf": 0.21, "peak_rss_bytes": 3100000000},
        {"dataset": "AMI full", "system": "product", "wer": 0.212, "der": 0.20149,
         "audio_seconds": 32400.0, "wall_seconds": 9720.0, "rtf": 0.3, "peak_rss_bytes": 3100000000},
        {"dataset": "AMI full", "system": "pyannote-alone", "wer": None, "der": 0.1875,
         "audio_seconds": 32400.0, "wall_seconds": None, "rtf": None, "peak_rss_bytes": None},
    ],
    "determinism": {"runs": 2, "identical": True},
}

# By hand: 17179869184 bytes is 16.0 GB (2^30 bytes each); 0.031234 is 3.1 %, 0.20149 is 20.1 %.
EXPECTED = """\
Measured on Apple M4 (arm64, 16.0 GB, macOS 27.0) on 2026-10-28, at commit `0123456789ab`, with lmt 0.1.0.
Models: ASR `distil-large-v3` (`Systran/faster-distil-whisper-large-v3` at `c9f6a3b2d1e0`); \
diarization `speaker-diarization-3.1` (`pyannote/speaker-diarization-3.1` at `84fd25912480`).
Two runs of the CI subset gave identical accuracy figures: yes.

| Set | System | WER | DER | RTF |
| --- | --- | ---: | ---: | ---: |
| LS full | product | 3.1 % | — | 0.21 |
| AMI full | product | 21.2 % | 20.1 % | 0.30 |
| AMI full | pyannote-alone | — | 18.8 % | — |

RTF is wall-clock time over audio duration. For the product, it runs from process start to exit, model loading \
included. Whisper alone and pyannote alone come from that same run: each is its own stage's model loading and \
inference, without start-up, decoding or export, and, with diarization on, while the other stage runs beside it. \
So the three RTFs aren't like for like."""

README = """\
# Title

## Results

Some prose.

<!-- results:start -->
<!-- results:end -->

## After
"""


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class RenderTest(unittest.TestCase):
    def test_figures(self):
        self.assertEqual(readme.render(RESULTS), EXPECTED)

    def test_identical_no(self):
        results = copy.deepcopy(RESULTS)
        results["determinism"]["identical"] = False
        self.assertIn("identical accuracy figures: no.", readme.render(results))

    def test_placeholder(self):
        # A results file with no figures, as `results/full.json` was until G1 stage 3's run filled it.
        placeholder = dict(copy.deepcopy(RESULTS), figures=[])
        self.assertEqual(readme.render(placeholder), "No full-set results yet. The Owner's run of "
                         "`lmt eval --set full` fills this table (G1 stage 3).")

    def test_dirty_raises(self):
        with self.assertRaises(InputError) as e:
            readme.render(dict(RESULTS, dirty=True))
        self.assertEqual(str(e.exception), "results/full.json: made from a dirty working tree; it can't be published")

    def test_region_needs_one_pair_in_order(self):
        for text in ("no markers", f"{readme.START}\n", f"{readme.START}{readme.END}{readme.END}",
                     f"{readme.END}\n{readme.START}"):
            with self.subTest(text=text), self.assertRaises(InputError):
                readme.region(text)


class CheckReadmeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.readme = self.dir / "README.md"
        self.results = self.dir / "results" / "full.json"
        self.results.parent.mkdir()
        self.readme.write_text(README)
        self.write_results(RESULTS)
        self.enterContext(contextlib.chdir(self.dir))

    def write_results(self, results):
        self.results.write_text(json.dumps(results, indent=2) + "\n")

    def test_update_then_check(self):
        readme.update(self.readme, self.results)
        self.assertIsNone(readme.check(self.readme, self.results))
        # Only the text between the markers changes, and no temporary file is left behind.
        markers = f"{readme.START}\n{readme.END}"
        self.assertEqual(self.readme.read_text(),
                         README.replace(markers, f"{readme.START}\n{EXPECTED}\n{readme.END}"))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["README.md", "results"])

    @pytest.mark.criterion("G1-6")
    def test_fresh_table_exits_0(self):
        readme.update(self.readme, self.results)
        self.assertEqual(run("eval", "--check-readme"), (0, "", ""))

    def test_changed_figure_exits_6(self):
        readme.update(self.readme, self.results)
        changed = copy.deepcopy(RESULTS)
        changed["figures"][1]["wer"] = 0.25
        self.write_results(changed)
        code, out, err = run("eval", "--check-readme")
        self.assertEqual(code, 6)
        self.assertIn("-| AMI full | product | 21.2 % | 20.1 % | 0.30 |\n", out)
        self.assertIn("+| AMI full | product | 25.0 % | 20.1 % | 0.30 |\n", out)
        self.assertEqual(err, "lmt: error: README.md: the results table differs from results/full.json; "
                              "run lmt eval --set full --update-readme\n")

    def test_out_names_the_results_directory(self):
        other = self.dir / "other"
        other.mkdir()
        (other / "full.json").write_text(json.dumps(dict(RESULTS, figures=[])))
        readme.update(self.readme, self.results)
        code, _, err = run("eval", "--check-readme", "--out", "other")
        self.assertEqual(code, 6)
        self.assertIn("differs from other/full.json", err)

    def assertInputError(self, subject):
        code, out, err = run("eval", "--check-readme")
        self.assertEqual((code, out), (3, ""))
        self.assertEqual(err.count("\n"), 1, err)
        self.assertTrue(err.startswith(f"lmt: error: {subject}: "), err)

    def test_missing_results_exits_3(self):
        self.results.unlink()
        self.assertInputError("results/full.json")

    def test_invalid_json_exits_3(self):
        self.results.write_text("{")
        self.assertInputError("results/full.json")

    def test_incomplete_results_exits_3(self):
        self.write_results({"dirty": False, "figures": [{"dataset": "LS full"}]})
        self.assertInputError("results/full.json")

    def test_missing_markers_exit_3(self):
        self.readme.write_text("# Title\n\n## Results\n")
        self.assertInputError("README.md")

    def test_missing_readme_exits_3(self):
        self.readme.unlink()
        self.assertInputError("README.md")

    def test_dirty_results_exit_3(self):
        readme.update(self.readme, self.results)
        self.write_results(dict(RESULTS, dirty=True))
        self.assertInputError("results/full.json")


class RepositoryTest(unittest.TestCase):
    def test_readme_matches_results(self):
        self.assertIsNone(readme.check(ROOT / "README.md", ROOT / "results" / "full.json"))

    def test_readme_job_runs_the_check(self):
        workflow = (ROOT / ".github" / "workflows" / "product.yml").read_text()
        job = re.search(r"^  readme:\n(.*?)(?=^  \S)", workflow, re.MULTILINE | re.DOTALL).group(1)
        self.assertRegex(job, r"(?m)^\s+run: uv sync --locked$")
        self.assertRegex(job, r"(?m)^\s+run: uv run --frozen lmt eval --check-readme$")
