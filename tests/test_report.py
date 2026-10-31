"""The results file: its format, the timing-free comparison, and `dirty` (design/internal/g1-stage1.md §4)."""

import copy
import re
import subprocess
from pathlib import Path

import pytest

from lmt.eval import report

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "eval" / "results_golden.json"

COMMIT = "0123456789abcdef0123456789abcdef01234567"
DATE = "2026-10-28T22:30:00-07:00"
MACHINE = {"arch": "arm64", "cpu": "Apple M4", "memory_bytes": 17179869184, "os": "macOS 27.0"}
MODELS = {
    "asr": {"name": "distil-large-v3", "repository": "Systran/faster-distil-whisper-large-v3",
            "revision": "c9f6a3b2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7a6"},
    "diarization": {"name": "speaker-diarization-3.1", "repository": "pyannote/speaker-diarization-3.1",
                    "revision": "84fd25912480287da0247647c3d2b4853cb3ee5d"},
}
# The two fixture sets' figures, as tests/test_score.py computes them by hand.
FIGURES = [
    {"dataset": "LS-ci", "system": "product", "wer": 0.263158, "der": None,
     "audio_seconds": 8.0, "wall_seconds": 3.2, "rtf": 0.4, "peak_rss_bytes": 2000},
    {"dataset": "LS-ci", "system": "asr-alone", "wer": 0.263158, "der": None,
     "audio_seconds": 8.0, "wall_seconds": None, "rtf": None, "peak_rss_bytes": None},
    {"dataset": "AMI-ci", "system": "product", "wer": None, "der": 0.117647,
     "audio_seconds": 16.0, "wall_seconds": 6.0, "rtf": 0.375, "peak_rss_bytes": 3000},
    {"dataset": "AMI-ci", "system": "pyannote-alone", "wer": None, "der": 0.117647,
     "audio_seconds": 16.0, "wall_seconds": None, "rtf": None, "peak_rss_bytes": None},
]


def fixed(figures=FIGURES, date=DATE, out_dir="results"):
    return report.build("ci", copy.deepcopy(figures), MODELS, out_dir=out_dir,
                        commit=COMMIT, dirty=False, date=date, machine=MACHINE)


def test_the_written_file_matches_the_golden_file_byte_for_byte(tmp_path):
    report.write(tmp_path / "ci.json", fixed())
    assert (tmp_path / "ci.json").read_bytes() == GOLDEN.read_bytes()
    assert [p.name for p in tmp_path.iterdir()] == ["ci.json"]  # the temporary file was renamed into place


def test_write_replaces_an_existing_file(tmp_path):
    (tmp_path / "ci.json").write_text("old\n", encoding="utf-8")
    report.write(tmp_path / "ci.json", fixed())
    assert (tmp_path / "ci.json").read_bytes() == GOLDEN.read_bytes()


def test_version_2_0_names_the_engine_and_apples_file():
    # public-api.md, "Results file, version 2.0": `engine` after `set`; Apple's figures in <set>-apple.json.
    results = report.build("full", [], MODELS, out_dir="results", engine="apple", commit=COMMIT, dirty=False,
                           date=DATE, machine=MACHINE)
    assert list(results)[:3] == ["schema_version", "set", "engine"]
    assert (results["schema_version"], results["engine"]) == ("2.0", "apple")
    assert fixed()["engine"] == "kyutai"
    assert [report.file_name(s, e) for s in ("full", "live") for e in ("kyutai", "apple")] == [
        "full.json", "full-apple.json", "live.json", "live-apple.json"]
    with pytest.raises(ValueError, match="not 'whisper'"):
        report.file_name("full", "whisper")
    with pytest.raises(ValueError, match="not 'whisper'"):
        report.build("full", [], MODELS, out_dir="results", engine="whisper")


def test_label_delays_are_timing_fields():
    figure = {"dataset": "AMI-ci", "system": "product-live", "wer": 0.2, "p95_delay_seconds": 0.9,
              "p95_label_delay_seconds": 2.1, "p50_label_delay_seconds": 1.0, "max_label_delay_seconds": 3.3,
              "matched_words": 9,
              "runs": [{"p95_delay_seconds": 0.9, "p95_label_delay_seconds": 2.1, "behind_seconds": 0.3}]}
    stripped = report.strip_timing(fixed([figure]))
    assert stripped["figures"] == [{"dataset": "AMI-ci", "system": "product-live", "wer": 0.2, "matched_words": 9,
                                    "runs": [{}]}]


def test_two_runs_differing_only_in_timing_have_the_same_accuracy():
    first = fixed()
    other = copy.deepcopy(FIGURES)
    other[0].update(wall_seconds=3.9, rtf=0.488, peak_rss_bytes=2600)
    other[2].update(wall_seconds=5.1, rtf=0.319, peak_rss_bytes=2900)
    second = fixed(other, date="2026-10-28T23:05:41-07:00")
    assert first != second
    assert report.same_accuracy(first, second)

    stripped = report.strip_timing(second)
    assert "date" not in stripped
    assert all(set(f).isdisjoint(report.TIMING_FIELDS) for f in stripped["figures"])
    assert second["figures"][0]["wall_seconds"] == 3.9  # a copy: the original keeps its timing

    other[1]["wer"] = 0.263159
    assert not report.same_accuracy(first, fixed(other))


# The dirty cases, in a temporary git repository with one commit.

def git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=lmt", "-c", "user.email=lmt@example.invalid", "-c", "commit.gpgsign=false",
         *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """No git repository above tmp_path counts, and no outer git environment leaks in."""
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return tmp_path


@pytest.fixture
def repo(isolated, monkeypatch):
    repo = isolated / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").write_text("A repository.\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "first")
    monkeypatch.chdir(repo)
    return repo


def run(out_dir) -> dict:
    """A run as `lmt eval` makes it: commit and dirty read from git."""
    return report.build("ci", FIGURES, MODELS, out_dir=out_dir, date=DATE, machine=MACHINE)


def test_writing_into_an_output_directory_inside_the_tree_leaves_it_clean(repo):
    (repo / "results").mkdir()
    first = run("results")
    assert first["commit"] == git(repo, "rev-parse", "HEAD").strip()
    assert first["dirty"] is False
    report.write(repo / "results" / "ci.json", first)
    assert git(repo, "status", "--porcelain") != ""  # git sees the new file; only the exclude hides it
    assert run("results")["dirty"] is False


def test_an_output_directory_outside_the_tree_leaves_it_clean_with_no_git_error(repo, isolated):
    elsewhere = isolated / "elsewhere"
    elsewhere.mkdir()
    report.write(elsewhere / "ci.json", run(elsewhere))
    assert run(elsewhere)["dirty"] is False  # an exclude outside the repository would make git fail


@pytest.mark.parametrize("where", ["inside", "outside"])
def test_an_untracked_file_elsewhere_in_the_tree_makes_it_dirty(repo, isolated, where):
    out_dir = repo / "results" if where == "inside" else isolated / "elsewhere"
    out_dir.mkdir()
    (repo / "notes.txt").write_text("not committed\n", encoding="utf-8")
    assert run(out_dir)["dirty"] is True


def test_outside_a_git_checkout_commit_and_dirty_are_null(isolated, monkeypatch):
    plain = isolated / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    results = run("results")
    assert (results["commit"], results["dirty"]) == (None, None)


def test_build_reads_the_date_and_machine_when_not_given(repo):
    results = report.build("ci", [], MODELS, out_dir="results")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", results["date"])
    assert list(results["machine"]) == ["arch", "cpu", "memory_bytes", "os"]


def test_machine_describes_this_machine():
    m = report.machine()
    assert list(m) == ["arch", "cpu", "memory_bytes", "os"]
    assert m["arch"] and m["cpu"] and m["os"]
    assert isinstance(m["memory_bytes"], int) and m["memory_bytes"] > 2**28
