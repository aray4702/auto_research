"""`lmt eval --set full` and `--update-readme` on fakes, with no model (design/internal/g1-stage2.md, P8):
P2a's fake worker in place of the worker, and test_eval_runner's fake items as the ci and full sets."""

import dataclasses
import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from test_eval_runner import FAKE_WORKER, items
from test_readme_table import README

from lmt import cli
from lmt.errors import InputError
from lmt.eval import readme, runner

pytestmark = pytest.mark.criterion("G1-6")

FULL = {"LS-ci": "LS full", "AMI-ci": "AMI full"}


def changed_items(dir):
    """The fake items, with each LibriSpeech transcript beside its audio in `dir` saying other words."""
    changed = []
    for item in items():
        if item.dataset == "LS-ci":
            doc = json.loads(item.audio.with_suffix(".json").read_text(encoding="utf-8"))
            for segment in doc["segments"]:
                segment["text"] = "nothing like the reference"
            audio = dir / item.audio.name
            audio.with_suffix(".json").write_text(json.dumps(doc), encoding="utf-8")
            item = dataclasses.replace(item, audio=audio)
        changed.append(item)
    return changed


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """The fake worker in place of the worker; the fake items as the ci set and, renamed, as the full set; and
    an empty temporary root. `fake.second_ci_differs` makes the second ci fetch give other words."""
    monkeypatch.setattr(runner, "WORKER", (str(FAKE_WORKER),))
    monkeypatch.delenv("FAKE_WORKER_LOG", raising=False)
    monkeypatch.delenv("FAKE_WORKER_EXIT", raising=False)
    changed = tmp_path / "changed"
    changed.mkdir()

    class Fake:
        pass

    f = Fake()
    f.fetched, f.second_ci_differs = [], False

    def fetch(set_name):
        f.fetched.append(set_name)
        if set_name == "full":
            return [dataclasses.replace(i, dataset=FULL[i.dataset]) for i in items()]
        if f.second_ci_differs and f.fetched.count("ci") == 2:
            return changed_items(changed)
        return items()

    monkeypatch.setattr(runner.datasets, "fetch", fetch)
    f.tmp = tmp_path / "tmp"
    f.tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(f.tmp))
    return f


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repository with a committed README.md, as the current directory."""
    path = tmp_path / "repo"
    path.mkdir()
    (path / "README.md").write_text(README, encoding="utf-8")
    git = ("git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false")
    for command in (("init", "-q"), ("add", "README.md"), ("commit", "-q", "-m", "README")):
        subprocess.run([*git, *command], cwd=path, check=True, capture_output=True)
    monkeypatch.chdir(path)
    return path


def test_set_full_writes_full_json_with_determinism(fake, tmp_path, capsys):
    out = tmp_path / "new" / "results"  # --out is created when it's missing
    code = cli.main(["eval", "--set", "full", "--out", str(out)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out == f"{out / 'full.json'}\n"
    assert fake.fetched == ["ci", "ci", "full"]
    # Only full.json is written: the two ci runs' files went with their temporary directory.
    assert sorted(p.name for p in out.iterdir()) == ["full.json"]
    assert list(fake.tmp.iterdir()) == []
    results = json.loads((out / "full.json").read_text(encoding="utf-8"))
    assert (results["set"], results["determinism"]) == ("full", {"runs": 2, "identical": True})
    figures = {(f["dataset"], f["system"]): f for f in results["figures"]}
    assert list(figures) == [("LS full", "product"), ("LS full", "whisper-alone"), ("AMI full", "product"),
                             ("AMI full", "whisper-alone"), ("AMI full", "pyannote-alone")]
    # The hand-computed figures of test_eval_runner, as the full set is the same fake items.
    assert figures["LS full", "product"]["wer"] == 0.263158
    assert figures["AMI full", "pyannote-alone"]["der"] == 0.117647
    # One progress line per item for each of the three runs.
    assert len(captured.err.splitlines()) == 18


def test_different_words_on_the_second_ci_run_are_not_identical(fake, tmp_path):
    fake.second_ci_differs = True
    results = runner.evaluate_full(tmp_path, update_readme=False)
    assert results["determinism"] == {"runs": 2, "identical": False}
    assert json.loads((tmp_path / "full.json").read_text(encoding="utf-8"))["determinism"]["identical"] is False


def test_update_readme_rewrites_the_table_and_check_readme_then_exits_0(fake, repo, capsys):
    code = cli.main(["eval", "--set", "full", "--update-readme"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out == f"{Path('results') / 'full.json'}\n"
    results = json.loads((repo / "results" / "full.json").read_text(encoding="utf-8"))
    assert results["dirty"] is False
    text = (repo / "README.md").read_text(encoding="utf-8")
    table = readme.render(results)
    assert text == README.replace(f"{readme.START}\n{readme.END}", f"{readme.START}\n{table}\n{readme.END}")
    assert "| LS full | product | 26.3 % | — |" in table
    assert "Two runs of the CI subset gave identical accuracy figures: yes." in table
    assert cli.main(["eval", "--check-readme"]) == 0
    assert capsys.readouterr().out == ""


def test_update_readme_in_a_dirty_tree_exits_3_after_writing_full_json(fake, repo, capsys):
    (repo / "notes.txt").write_text("uncommitted\n", encoding="utf-8")
    code = cli.main(["eval", "--set", "full", "--update-readme"])
    captured = capsys.readouterr()
    assert code == 3
    assert captured.err.splitlines()[-1] == ("lmt: error: results/full.json: made from a dirty working tree; "
                                             "it can't be published")
    assert json.loads((repo / "results" / "full.json").read_text(encoding="utf-8"))["dirty"] is True
    assert (repo / "README.md").read_text(encoding="utf-8") == README


def test_a_failed_full_run_raises_exit_3_and_writes_nothing(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER_EXIT", "4")
    with pytest.raises(InputError) as e:
        runner.evaluate_full(tmp_path, update_readme=True)
    assert e.value.exit_code == 3
    assert list(tmp_path.glob("*.json")) == []
    assert list(fake.tmp.iterdir()) == []
