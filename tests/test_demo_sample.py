"""`lmt demo --sample`'s cache, on fakes: no network or model (design/internal/g3-stage1.md §2b, 2b1)."""

import shutil
from pathlib import Path

import pytest
from isolation import snapshot
from test_models import fake_manifest

from lmt import cli, models, pipeline, transcript
from lmt.demo import sample
from lmt.errors import InputError, ModelError
from lmt.eval import datasets

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLIP = FIXTURES / "two_speakers.wav"
TRANSCRIPT = FIXTURES / "two_speakers.transcript.json"
DOWNLOAD_LINE = "downloading the sample meeting"


class Fakes:
    """`sample_meeting` gives a copy of the two-speaker clip, unless `missing`; `transcribe` gives the fixture
    transcript, or raises `ModelError` when `model_missing`, and counts its calls."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.missing = False
        self.model_missing = False
        self.transcribed = []

    def sample_meeting(self, *, offline=False):
        if self.missing and offline:
            raise InputError("LMT_DATA_DIR", "the sample meeting isn't downloaded; run once without --offline")
        path = self.data_dir / "derived" / datasets.SAMPLE_MEETING / "ES2004a.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CLIP, path)
        self.missing = False
        return path

    def transcribe(self, path, *, offline=False):
        self.transcribed.append((Path(path), offline))
        if self.model_missing:
            raise ModelError("org/tiny/model.bin", "not in the cache, and --offline forbids downloading")
        return transcript.load(TRANSCRIPT)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """A fresh LMT_DATA_DIR, and fresh HOME, TMPDIR, XDG_CACHE_HOME, LMT_CACHE_DIR and working directory."""
    found = {name: tmp_path / name for name in ("data", "home", "tmp", "xdg", "cache", "cwd")}
    for path in found.values():
        path.mkdir()
    for var, name in (("LMT_DATA_DIR", "data"), ("HOME", "home"), ("TMPDIR", "tmp"),
                      ("XDG_CACHE_HOME", "xdg"), ("LMT_CACHE_DIR", "cache")):
        monkeypatch.setenv(var, str(found[name]))
    monkeypatch.chdir(found["cwd"])
    return found


@pytest.fixture
def fakes(dirs, tmp_path, monkeypatch):
    f = Fakes(dirs["data"])
    monkeypatch.setattr(datasets, "sample_meeting", f.sample_meeting)
    monkeypatch.setattr(pipeline, "transcribe", f.transcribe)
    monkeypatch.setattr(models, "MANIFEST_PATH", fake_manifest(tmp_path))
    return f


def expected_key():
    sha = datasets.load_manifests(datasets.MANIFEST_DIR).datasets[datasets.SAMPLE_MEETING].derived_sha256
    return sample.cache_key(sha, models.default_models(models.load_manifest()))


def test_first_run_transcribes_once_and_the_second_uses_the_cache(fakes, dirs, capsys):
    path, audio = sample.prepare(offline=False)
    assert path == dirs["data"] / "sample" / expected_key() / "ES2004a.json"
    assert audio == dirs["data"] / "derived" / datasets.SAMPLE_MEETING / "ES2004a.wav"
    assert transcript.load(path) == transcript.load(TRANSCRIPT)
    assert fakes.transcribed == [(audio, False)]
    assert "transcribing the sample meeting once" in capsys.readouterr().err

    assert sample.prepare(offline=False) == (path, audio)
    assert len(fakes.transcribed) == 1
    assert "transcribing" not in capsys.readouterr().err


def test_a_different_model_revision_gives_another_key(fakes, tmp_path, monkeypatch):
    first, _ = sample.prepare(offline=False)
    manifest = models.MANIFEST_PATH.read_text(encoding="utf-8")
    other = tmp_path / "other" / "models.toml"
    other.parent.mkdir()
    other.write_text(manifest.replace(f'revision = "{"a" * 40}"', f'revision = "{"b" * 40}"', 1), encoding="utf-8")
    monkeypatch.setattr(models, "MANIFEST_PATH", other)

    second, _ = sample.prepare(offline=False)
    assert second.parent.name != first.parent.name
    assert second.is_file() and first.is_file()
    assert len(fakes.transcribed) == 2


def test_a_corrupt_cached_file_is_transcribed_again(fakes):
    path, _ = sample.prepare(offline=False)
    path.write_text("{", encoding="utf-8")
    assert sample.prepare(offline=False)[0] == path
    assert len(fakes.transcribed) == 2
    assert transcript.load(path) == transcript.load(TRANSCRIPT)


def test_a_missing_sample_downloads_once_with_its_line(fakes, capsys):
    fakes.missing = True
    sample.prepare(offline=False)
    assert capsys.readouterr().err.count(DOWNLOAD_LINE) == 1
    sample.prepare(offline=False)
    assert DOWNLOAD_LINE not in capsys.readouterr().err


@pytest.mark.criterion("G1-6")
def test_offline_with_the_sample_missing_is_exit_3(fakes, dirs, capsys):
    fakes.missing = True
    assert cli.main(["demo", "--sample", "--offline"]) == 3
    err = capsys.readouterr().err
    assert err.startswith("lmt: error: LMT_DATA_DIR: ")
    assert DOWNLOAD_LINE not in err
    assert fakes.transcribed == []
    assert not (dirs["data"] / "sample").exists()


@pytest.mark.criterion("G1-6")
def test_offline_with_a_model_missing_is_exit_5(fakes, dirs, capsys):
    fakes.model_missing = True
    assert cli.main(["demo", "--sample", "--offline"]) == 5
    err = capsys.readouterr().err
    assert "lmt: error: org/tiny/model.bin: " in err
    assert DOWNLOAD_LINE not in err
    assert [offline for _, offline in fakes.transcribed] == [True]
    assert not (dirs["data"] / "sample" / expected_key() / "ES2004a.json").exists()


def test_nothing_is_written_outside_lmt_data_dir(fakes, dirs):
    others = [dirs[name] for name in ("home", "tmp", "xdg", "cache", "cwd")]
    before = snapshot(*others)
    fakes.missing = True
    sample.prepare(offline=False)
    sample.prepare(offline=False)
    assert snapshot(*others) == before
