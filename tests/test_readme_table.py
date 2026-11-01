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

# A fixed results file with figures, version 2.0 (public-api.md, "Results file").
RESULTS = {
    "schema_version": "2.0",
    "set": "full",
    "engine": "kyutai",
    "commit": "0123456789abcdef0123456789abcdef01234567", "dirty": False,
    "date": "2026-10-28T14:00:00-07:00",
    "machine": {"arch": "arm64", "cpu": "Apple M4", "memory_bytes": 17179869184, "os": "macOS 27.0"},
    "lmt_version": "0.1.0",
    "models": {
        "asr": {"name": "stt-1b-en_fr", "engine": "kyutai", "repository": "kyutai/stt-1b-en_fr-mlx",
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
Models: ASR `stt-1b-en_fr` (`kyutai/stt-1b-en_fr-mlx` at `c9f6a3b2d1e0`); \
diarization `speaker-diarization-3.1` (`pyannote/speaker-diarization-3.1` at `84fd25912480`).
Two runs of the CI subset gave identical accuracy figures: yes.

| Set | System | WER | DER | RTF |
| --- | --- | ---: | ---: | ---: |
| LS full | product | 3.1 % | — | 0.21 |
| AMI full | product | 21.2 % | 20.1 % | 0.30 |
| AMI full | pyannote-alone | — | 18.8 % | — |

RTF is wall-clock time over audio duration. For the product, it runs from process start to exit, model loading \
included. The engine alone and pyannote alone come from that same run: each is its own stage's model loading and \
inference, without start-up, decoding or export, and, with diarization on, while the other stage runs beside it. \
So the three RTFs aren't like for like."""
TABLE = f"**Full sets, Kyutai STT 1B** (`results/full.json`)\n\n{EXPECTED}"

APPLE_ASR = {"name": "SpeechTranscriber", "engine": "apple", "repository": "com.apple.Speech",
             "revision": "macOS 26.6.2 (25G5034) en_US"}
FULL_APPLE = dict(copy.deepcopy(RESULTS), engine="apple", determinism=None,
                  machine=dict(RESULTS["machine"], os="macOS 26.6.2 (25G5034)"),
                  models=dict(RESULTS["models"], asr=APPLE_ASR))
LIVE = {
    "schema_version": "2.0", "set": "live", "engine": "kyutai",
    "commit": "fedcba9876543210fedcba9876543210fedcba98", "dirty": False,
    "date": "2026-10-31T16:00:00-07:00", "machine": RESULTS["machine"], "lmt_version": "0.3.0",
    "models": RESULTS["models"],
    "figures": [{"dataset": "AMI-ci", "system": "product-live", "wer": 0.171, "der": 0.304,
                 "audio_seconds": 900.0, "wall_seconds": 905.0, "rtf": 1.006, "peak_rss_bytes": None,
                 "p50_delay_seconds": 0.612, "p95_delay_seconds": 0.894, "max_delay_seconds": 1.402,
                 "p50_label_delay_seconds": 1.9, "p95_label_delay_seconds": 2.604, "max_label_delay_seconds": 3.9,
                 "matched_words": 950,
                 "runs": [{"p95_delay_seconds": 0.9, "p95_label_delay_seconds": 2.6, "behind_seconds": 0.2}] * 3}],
    "determinism": None,
}
LIVE_APPLE = dict(copy.deepcopy(LIVE), engine="apple", machine=FULL_APPLE["machine"], models=FULL_APPLE["models"])
# By hand: the blocks of the four files, in the order full, full-apple, live, live-apple.
APPLE_HEAD = """\
**Full sets, Apple's SpeechTranscriber** (`results/full-apple.json`)

Measured on Apple M4 (arm64, 16.0 GB, macOS 26.6.2 (25G5034)) on 2026-10-28, at commit `0123456789ab`, with \
lmt 0.1.0.
Models: ASR `SpeechTranscriber` (`com.apple.Speech` at `macOS 26.6.2 (25G5034) en_US`); diarization \
`speaker-diarization-3.1` (`pyannote/speaker-diarization-3.1` at `84fd25912480`).
Apple's engine is exempt from the repeat comparison (Q7).
"""
LIVE_BLOCK = """\
**Live delays, Kyutai STT 1B** (`results/live.json`)

Measured on Apple M4 (arm64, 16.0 GB, macOS 27.0) on 2026-10-31, at commit `fedcba987654`, with lmt 0.3.0.
Models: ASR `stt-1b-en_fr` (`kyutai/stt-1b-en_fr-mlx` at `c9f6a3b2d1e0`); diarization `speaker-diarization-3.1` \
(`pyannote/speaker-diarization-3.1` at `84fd25912480`).

| Set | System | Runs | Matched words | Word delay p50 / p95 / max | Label delay p50 / p95 / max | WER | DER |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| AMI-ci | product-live | 3 | 950 | 0.61 s / 0.89 s / 1.40 s | 1.90 s / 2.60 s / 3.90 s | 17.1 % | 30.4 % |

Delays are measured from the end of each matched word, pooled over the runs, each played back in real time: the \
word delay to its text, the label delay to its speaker label (Q10)."""

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

    def test_live_placeholder(self):
        self.assertEqual(readme.render_live(dict(LIVE, figures=[])), "No live results yet. The Owner's run of "
                         "`lmt eval --set live` fills this table.")

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

    def write_results(self, results, name="full"):
        (self.results.parent / f"{name}.json").write_text(json.dumps(results, indent=2) + "\n")

    def test_update_then_check(self):
        readme.update(self.readme, self.results.parent)
        self.assertIsNone(readme.check(self.readme, self.results.parent))
        # Only the text between the markers changes, and no temporary file is left behind.
        markers = f"{readme.START}\n{readme.END}"
        self.assertEqual(self.readme.read_text(),
                         README.replace(markers, f"{readme.START}\n{TABLE}\n{readme.END}"))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["README.md", "results"])

    @pytest.mark.criterion("G4-8")
    def test_all_four_files_in_order(self):
        # G1-14: each engine's full figures and live delays, from every results file present.
        for name, results in (("live-apple", LIVE_APPLE), ("live", LIVE), ("full-apple", FULL_APPLE)):
            self.write_results(results, name)
        table = readme.render_all(self.results.parent)
        live_apple = LIVE_BLOCK.replace("Kyutai STT 1B** (`results/live.json`", "Apple's SpeechTranscriber** "
                                        "(`results/live-apple.json`").replace("macOS 27.0)", "macOS 26.6.2 (25G5034))")
        live_apple = live_apple.replace("`stt-1b-en_fr` (`kyutai/stt-1b-en_fr-mlx` at `c9f6a3b2d1e0`)",
                                        "`SpeechTranscriber` (`com.apple.Speech` at `macOS 26.6.2 (25G5034) en_US`)")
        blocks = table.split("\n\n**")
        self.assertEqual(len(blocks), 4)
        self.assertEqual(blocks[0], TABLE)
        self.assertTrue(f"**{blocks[1]}".startswith(APPLE_HEAD), blocks[1])
        self.assertIn("| AMI full | pyannote-alone | — | 18.8 % | — |", blocks[1])
        self.assertEqual(f"**{blocks[2]}", LIVE_BLOCK)
        self.assertEqual(f"**{blocks[3]}", live_apple)
        # Each is optional but full.json; update and check read them all.
        readme.update(self.readme, self.results.parent)
        self.assertIsNone(readme.check(self.readme, self.results.parent))
        (self.results.parent / "full-apple.json").unlink()
        self.assertIn("-**Full sets, Apple's SpeechTranscriber**", readme.check(self.readme, self.results.parent))

    def test_whisper_era_file_renders_as_before(self):
        # results/full.json at 1.0, until G4's stage 2 replaces it: its own model's name, and Whisper alone.
        whisper = copy.deepcopy(RESULTS)
        del whisper["engine"]
        whisper.update(schema_version="1.0")
        whisper["models"]["asr"] = {"name": "distil-large-v3", "repository": "Systran/faster-distil-whisper-large-v3",
                                    "revision": "c9f6a3b2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7a6"}
        whisper["figures"].append(dict(whisper["figures"][0], system="whisper-alone"))
        self.write_results(whisper)
        table = readme.render_all(self.results.parent)
        self.assertTrue(table.startswith("**Full sets, `distil-large-v3`** (`results/full.json`)\n\n"), table)
        self.assertIn("| LS full | whisper-alone | 3.1 % | — | 0.21 |", table)
        self.assertIn("Whisper alone and pyannote alone come from that same run", table)

    @pytest.mark.criterion("G1-6")
    def test_fresh_table_exits_0(self):
        readme.update(self.readme, self.results.parent)
        self.assertEqual(run("eval", "--check-readme"), (0, "", ""))

    def test_changed_figure_exits_6(self):
        readme.update(self.readme, self.results.parent)
        changed = copy.deepcopy(RESULTS)
        changed["figures"][1]["wer"] = 0.25
        self.write_results(changed)
        code, out, err = run("eval", "--check-readme")
        self.assertEqual(code, 6)
        self.assertIn("-| AMI full | product | 21.2 % | 20.1 % | 0.30 |\n", out)
        self.assertIn("+| AMI full | product | 25.0 % | 20.1 % | 0.30 |\n", out)
        self.assertEqual(err, "lmt: error: README.md: the results table differs from the results files in "
                              "results; run lmt eval --set full --update-readme\n")

    def test_a_new_live_file_exits_6(self):
        readme.update(self.readme, self.results.parent)
        self.write_results(LIVE, "live")
        code, out, _ = run("eval", "--check-readme")
        self.assertEqual(code, 6)
        self.assertIn("+**Live delays, Kyutai STT 1B** (`results/live.json`)\n", out)

    def test_out_names_the_results_directory(self):
        other = self.dir / "other"
        other.mkdir()
        (other / "full.json").write_text(json.dumps(dict(RESULTS, figures=[])))
        readme.update(self.readme, self.results.parent)
        code, _, err = run("eval", "--check-readme", "--out", "other")
        self.assertEqual(code, 6)
        self.assertIn("differs from the results files in other", err)

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
        readme.update(self.readme, self.results.parent)
        self.write_results(dict(RESULTS, dirty=True))
        self.assertInputError("results/full.json")

    def test_a_dirty_or_broken_optional_file_exits_3(self):
        readme.update(self.readme, self.results.parent)
        self.write_results(dict(LIVE, dirty=True), "live")
        self.assertInputError("results/live.json")
        self.write_results({"dirty": False, "figures": [{"dataset": "AMI-ci"}]}, "live-apple")
        (self.results.parent / "live.json").unlink()
        self.assertInputError("results/live-apple.json")


class RepositoryTest(unittest.TestCase):
    def test_readme_matches_results(self):
        self.assertIsNone(readme.check(ROOT / "README.md", ROOT / "results"))

    def test_readme_job_runs_the_check(self):
        workflow = (ROOT / ".github" / "workflows" / "product.yml").read_text()
        job = re.search(r"^  readme:\n(.*?)(?=^  \S)", workflow, re.MULTILINE | re.DOTALL).group(1)
        self.assertRegex(job, r"(?m)^\s+run: uv sync --locked$")
        self.assertRegex(job, r"(?m)^\s+run: uv run --frozen lmt eval --check-readme$")
