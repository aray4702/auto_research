"""The live default model in the manifest, and `download_defaults` for `lmt models`
(design/internal/g2-stage1.md §1, "The live default model")."""

import pytest
from test_models import CONFIG, GOOD, fake_manifest, run

from lmt import models
from lmt.errors import InputError


def live_manifest(tmp_path, live="other"):
    """test_models' fake manifest (default_asr "tiny"), with `default_live_asr = live`, or none."""
    path = fake_manifest(tmp_path)
    if live is not None:
        default = 'default_asr = "tiny"\n'
        text = path.read_text(encoding="utf-8").replace(default, f'{default}default_live_asr = "{live}"\n')
        path.write_text(text, encoding="utf-8")
    return path


def names(found):
    return [m.name for m in found]


def test_download_defaults_adds_the_live_default_after_the_default(tmp_path):
    manifest = models.load_manifest(live_manifest(tmp_path))
    assert models.default_live_asr(manifest).name == "other"
    assert names(models.download_defaults(manifest)) == ["tiny", "other", "diar", "seg", "emb"]
    assert names(models.default_models(manifest)) == ["tiny", "diar", "seg", "emb"]


@pytest.mark.parametrize("live", ["tiny", None])
def test_a_live_default_equal_to_the_default_or_absent_isnt_repeated(tmp_path, live):
    manifest = models.load_manifest(live_manifest(tmp_path, live))
    assert models.default_live_asr(manifest).name == "tiny"
    assert names(models.download_defaults(manifest)) == ["tiny", "diar", "seg", "emb"]


@pytest.mark.parametrize("asr", ["tiny", "other"])
def test_a_given_asr_model_holds_no_live_default(tmp_path, asr):
    manifest = models.load_manifest(live_manifest(tmp_path, "other" if asr == "tiny" else "tiny"))
    assert models.download_defaults(manifest, asr=asr) == models.default_models(manifest, asr=asr)
    assert names(models.download_defaults(manifest, asr=asr)) == [asr, "diar", "seg", "emb"]


def test_models_download_fetches_download_defaults(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(models, "MANIFEST_PATH", live_manifest(tmp_path))
    fetched = []
    monkeypatch.setattr(models, "download", lambda found, token=None: fetched.append(found))
    assert run(["models", "download"], capsys) == (0, "", "")
    assert fetched == [models.download_defaults(models.load_manifest())]
    assert names(fetched[0]) == ["tiny", "other", "diar", "seg", "emb"]


def test_list_bytes_marks_the_live_default_and_counts_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(models, "MANIFEST_PATH", live_manifest(tmp_path))
    monkeypatch.setenv("LMT_CACHE_DIR", str(tmp_path / "cache"))
    manifest = models.load_manifest()
    served = {("tiny", "config.json"): CONFIG, ("tiny", "model.bin"): GOOD, ("other", "model.bin"): b"other\n",
              ("diar", "config.yaml"): CONFIG, ("seg", "pytorch_model.bin"): b"seg\n",
              ("emb", "pytorch_model.bin"): b"emb\n"}
    for (name, path), data in served.items():
        target = models.model_dir(manifest.get(name)) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    code, out, err = run(["models", "list", "--bytes"], capsys)
    assert (code, err) == (0, "")
    rows = [line.split("\t") for line in out.splitlines()]
    assert {row[0]: row[3] for row in rows[1:-1]} == {"tiny": "yes", "other": "yes", "diar": "yes", "seg": "yes",
                                                      "emb": "yes"}
    assert rows[-1] == ["total-default", str(sum(len(data) for data in served.values()))]


@pytest.mark.parametrize("live", ["diar", "missing"])
def test_a_live_default_that_isnt_an_asr_model_fails_the_manifest(tmp_path, live):
    path = live_manifest(tmp_path, live)
    with pytest.raises(InputError) as e:
        models.load_manifest(path)
    assert str(e.value) == f"{path}: default_live_asr must name an asr model"


def test_the_real_manifests_live_default():
    assert models.default_live_asr(models.load_manifest()).name == "distil-large-v3"
