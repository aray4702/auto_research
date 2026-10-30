"""`lmt eval --set live`: AMI-ci's reference with its word times, the command and `results/live.json`
(design/internal/g2-stage1.md §5, item 5d). The runs are replaced by the fixture logs. No model."""

import json

import pytest
from test_datasets import CI, CROP, REF_WORDS
from test_eval_live_figure import ITEM, logs, ref_words

from lmt import cli
from lmt.eval import datasets, live


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
    assert (wav, kwargs) == (ITEM.audio, {})
    assert work_dir.parent == out
    assert sorted(p.name for p in out.iterdir()) == ["live.json"]  # the runs' directory is removed

    results = json.loads((out / "live.json").read_text(encoding="utf-8"))
    assert (results["schema_version"], results["set"], results["determinism"]) == ("1.0", "live", None)
    assert results["models"] == logs()[0].transcript["models"]
    assert results["figures"] == [live.figure(ITEM, ref_words(), logs())]  # 5c's test checks this figure


def test_eval_help_lists_live_among_the_sets(capsys):
    assert cli.main(["eval", "--help"]) == 0
    assert "{ci,full,live}" in capsys.readouterr().out


def test_update_readme_with_set_live_exits_2(fixture_runs, tmp_path, capsys):
    assert cli.main(["eval", "--set", "live", "--update-readme", "--out", str(tmp_path)]) == 2
    assert "--update-readme" in capsys.readouterr().err
    assert fixture_runs == []


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
