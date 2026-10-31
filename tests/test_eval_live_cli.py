"""`lmt eval --set live`: AMI-ci's reference with its word times, the command and `results/live.json`
(design/internal/g2-stage1.md §5, item 5d). The runs are replaced by the fixture logs. No model."""

import json
from pathlib import Path

import pytest
from test_datasets import CI, CROP, REF_WORDS
from test_eval_full import repo  # noqa: F401  (a fixture)
from test_eval_live_figure import ITEM, logs, ref_words
from test_readme_table import README, RESULTS

from lmt import cli
from lmt.eval import datasets, live, readme


@pytest.fixture
def fixture_runs(monkeypatch):
    """`live_reference` gives the fixture item and words; `run_live` records its call and gives the logs."""
    calls = []

    def run_live(wav, work_dir, **kwargs):
        calls.append((wav, work_dir, kwargs))
        assert work_dir.is_dir()
        return logs()

    monkeypatch.setattr(datasets, "live_reference", lambda: (ITEM, ref_words()))
    monkeypatch.setattr(live, "run_live", run_live)
    return calls


def test_set_live_writes_live_json_and_prints_its_path(fixture_runs, tmp_path, capsys):
    out = tmp_path / "new" / "results"  # --out is created when it's missing
    code = cli.main(["eval", "--set", "live", "--out", str(out)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out == f"{out / 'live.json'}\n"

    [(wav, work_dir, kwargs)] = fixture_runs
    assert (wav, kwargs) == (ITEM.audio, {"engine": "kyutai"})
    assert work_dir.parent == out
    assert sorted(p.name for p in out.iterdir()) == ["live.json"]  # the runs' directory is removed

    results = json.loads((out / "live.json").read_text(encoding="utf-8"))
    assert (results["schema_version"], results["set"], results["engine"], results["determinism"]) == (
        "2.0", "live", "kyutai", None)
    assert results["models"] == logs()[0].transcript["models"]
    assert results["figures"] == [live.figure(ITEM, ref_words(), logs())]  # 5c's test checks this figure


@pytest.mark.criterion("G4-8")
def test_engine_apple_writes_live_apple_json(fixture_runs, tmp_path, capsys):
    assert cli.main(["eval", "--set", "live", "--engine", "apple", "--out", str(tmp_path)]) == 0
    assert capsys.readouterr().out == f"{tmp_path / 'live-apple.json'}\n"
    [(_, _, kwargs)] = fixture_runs
    assert kwargs == {"engine": "apple"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["live-apple.json"]
    assert json.loads((tmp_path / "live-apple.json").read_text(encoding="utf-8"))["engine"] == "apple"


def test_eval_help_lists_live_among_the_sets(capsys):
    assert cli.main(["eval", "--help"]) == 0
    assert "{ci,full,live}" in capsys.readouterr().out


def test_update_readme_with_set_ci_exits_2(fixture_runs, tmp_path, capsys):
    assert cli.main(["eval", "--set", "ci", "--update-readme", "--out", str(tmp_path)]) == 2
    assert "--update-readme" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.criterion("G1-14")
@pytest.mark.usefixtures("repo")  # a git repository with README.md, as the current directory
def test_update_readme_after_set_live_renders_every_file(fixture_runs, capsys):
    Path("results").mkdir()
    Path("results", "full.json").write_text(json.dumps(RESULTS), encoding="utf-8")
    assert cli.main(["eval", "--set", "live", "--update-readme"]) == 0, capsys.readouterr().err
    table = readme.render_all(Path("results"))
    assert "**Live delays, Kyutai STT 1B** (`results/live.json`)" in table
    assert Path("README.md").read_text(encoding="utf-8") == README.replace(
        f"{readme.START}\n{readme.END}", f"{readme.START}\n{table}\n{readme.END}")
    capsys.readouterr()
    assert cli.main(["eval", "--check-readme"]) == 0


class TestLiveReference:
    def test_it_is_ami_cis_item_with_the_words_its_ref_text_is_made_from(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LMT_DATA_DIR", str(tmp_path / "cache"))
        CI(tmp_path, monkeypatch)
        fetched = []
        fetch_source = datasets.fetch_source
        monkeypatch.setattr(datasets, "fetch_source",
                            lambda source: fetched.append(source.id) or fetch_source(source))

        item, words = datasets.live_reference()

        # AMI-ci's own sources alone, so no LibriSpeech.
        assert fetched == ["ami-ES2004a", "ami-annotations-1.6.2", "ami-rttm-ES2004a"]
        assert item == datasets.fetch("ci")[-1]
        assert " ".join(text for _, _, text in words) == item.ref_text == REF_WORDS
        assert all((start + end) / 2 < CROP for start, end, _ in words)
