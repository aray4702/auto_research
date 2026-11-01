"""G1-5, G1-12's CI half and G1-8c (spec/acceptance.md): the `eval-ci` job's two `lmt eval --set ci` runs, read
from their results files (design/internal/g1-stage2.md, P2b). The job copies the first run's file to
$LMT_EVAL_CI_FIRST before the second run writes results/ci.json."""

import difflib
import json
import os
from pathlib import Path

import pytest

from lmt.eval import report as results

ROOT = Path(__file__).resolve().parents[1]
FIRST_VAR = "LMT_EVAL_CI_FIRST"
SECOND = ROOT / "results" / "ci.json"

# G1-5's figures, in score's order: WER on both sets, DER on AMI-ci, RTF for each system.
FIGURES = {
    ("LS-ci", "product"): ("wer", "rtf"),
    ("LS-ci", "asr-alone"): ("wer", "rtf"),
    ("AMI-ci", "product"): ("wer", "der", "rtf"),
    ("AMI-ci", "asr-alone"): ("wer", "rtf"),
    ("AMI-ci", "pyannote-alone"): ("der", "rtf"),
}

# spec/acceptance.md G1-8c, each inclusive. score._figure rounds WER and DER to 6 places, which can lower a
# value by up to 5e-7. WER is whole errors over N reference words, so a true WER above 0.05 exceeds it by at
# least 1/(20N), and above 0.25 by 1/(4N): more than 5e-7 while N < 100,000. LS-ci and AMI-ci hold on the order
# of a thousand reference words, so the rounded WER <= its bound is the spec's test exactly. Redo this if a CI
# subset ever nears 100,000 reference words.
LS_CI_WER_BOUND = 0.050  # G1-8c: LS-ci WER <= 5.0 %
AMI_CI_WER_BOUND = 0.250  # G1-8c: AMI-ci WER <= 25.0 %
# G1-8c: AMI-ci DER <= 25.0 %. DER is a ratio of durations, with no such spacing (a true 0.2500004 rounds to
# 0.25), so the rounded DER passes only below its bound, and a DER equal to it goes to the Architect.
AMI_CI_DER_BOUND = 0.250


def report(line):
    print(line)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def load(path: Path) -> dict:
    assert path.is_file(), f"{path}: no results file"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def runs() -> tuple[dict, dict]:
    """The first run's results file and the second's. Without the job's variable it skips, except under CI."""
    first = os.environ.get(FIRST_VAR)
    if not first:
        message = f"{FIRST_VAR} isn't set: the eval-ci job sets it to the first run's results file"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    return load(Path(first)), load(SECOND)


def by_key(doc: dict) -> dict:
    return {(f["dataset"], f["system"]): f for f in doc["figures"]}


@pytest.mark.data
@pytest.mark.criterion("G1-5", "G1-12")
class TestTwoRuns:
    def test_both_files_hold_g1_5s_figures(self, runs):
        for name, doc in zip(("first", "second"), runs):
            assert doc["set"] == "ci", f"the {name} run's set is {doc['set']!r}"
            assert [(f["dataset"], f["system"]) for f in doc["figures"]] == list(FIGURES), name
            figures = by_key(doc)
            for key, fields in FIGURES.items():
                for field in fields:
                    assert figures[key][field] is not None, f"the {name} run: {key} {field} is null"

    def test_the_two_runs_give_identical_accuracy_figures(self, runs):
        first, second = runs
        if not results.same_accuracy(first, second):
            diff = difflib.unified_diff(
                json.dumps(results.strip_timing(first), indent=2).splitlines(),
                json.dumps(results.strip_timing(second), indent=2).splitlines(),
                f"${FIRST_VAR}", str(SECOND.relative_to(ROOT)), lineterm="")
            pytest.fail("the two runs' files differ with their timing fields removed:\n" + "\n".join(diff))


@pytest.mark.data
@pytest.mark.criterion("G1-8c")
def test_g1_8c_the_ci_subset_meets_the_accuracy_bounds(runs):
    doc = runs[1]
    report(f"G1-8c: {SECOND.relative_to(ROOT)}, commit {doc['commit']}, dirty {doc['dirty']}")
    report("")
    report("| Dataset | System | WER | DER | RTF | Wall (s) | Peak RSS (bytes) |")
    report("| --- | --- | --- | --- | --- | --- | --- |")
    for f in doc["figures"]:
        report(f"| {f['dataset']} | {f['system']} | {f['wer']} | {f['der']} | {f['rtf']} | {f['wall_seconds']} "
               f"| {f['peak_rss_bytes']} |")
    report("")

    figures = by_key(doc)
    misses = []
    for key, bound in ((("LS-ci", "product"), LS_CI_WER_BOUND), (("AMI-ci", "product"), AMI_CI_WER_BOUND)):
        value = figures.get(key, {}).get("wer")
        if value is None or not value <= bound:
            misses.append(f"{key[0]} WER {value} is above its bound {bound}")
    der = figures.get(("AMI-ci", "product"), {}).get("der")
    if der == AMI_CI_DER_BOUND:
        misses.append(f"AMI-ci DER {der}: DER equals its bound after rounding, so a pass can't be told from a miss")
    elif der is None or not der < AMI_CI_DER_BOUND:
        misses.append(f"AMI-ci DER {der} is above its bound {AMI_CI_DER_BOUND}")
    report("G1-8c: " + ("; ".join(misses) if misses else "every bound met"))
    assert not misses, "; ".join(misses)
