"""The default models by engine, and `lmt models download --engine` (design/public-api.md, "G4's changes to the
commands"; design/internal/g4-stage1.md §1-§2): one Kyutai model serves both modes, so there is no live default."""

import pytest
from test_models import fake_manifest, run

from lmt import engines, models
from lmt.engines import kyutai


def names(found):
    return [m.name for m in found]


def test_download_defaults_by_engine(tmp_path):
    manifest = models.load_manifest(fake_manifest(tmp_path))  # default_asr "tiny"
    assert names(models.download_defaults(manifest)) == ["tiny", "diar", "seg", "emb"]
    assert models.download_defaults(manifest, engine="kyutai") == models.default_models(manifest)
    assert names(models.download_defaults(manifest, engine="apple")) == ["diar", "seg", "emb"]


def test_a_live_default_in_a_manifest_is_ignored(tmp_path):
    path = fake_manifest(tmp_path)
    text = path.read_text(encoding="utf-8").replace('default_asr = "tiny"\n',
                                                    'default_asr = "tiny"\ndefault_live_asr = "other"\n')
    path.write_text(text, encoding="utf-8")
    assert names(models.download_defaults(models.load_manifest(path))) == ["tiny", "diar", "seg", "emb"]


def test_models_download_fetches_kyutai_s_model_and_the_diarization_models(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(models, "MANIFEST_PATH", fake_manifest(tmp_path))
    fetched, prepared = [], []
    monkeypatch.setattr(models, "download", lambda found, token=None: fetched.append(names(found)))
    monkeypatch.setattr(engines, "download", prepared.append)
    for argv in (["models", "download"], ["models", "download", "--engine", "kyutai"]):
        assert run(argv, capsys) == (0, "", "")
    assert (fetched, prepared) == ([["tiny", "diar", "seg", "emb"]] * 2, [])


def test_models_download_apple_prepares_the_engine_then_the_diarization_models(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(models, "MANIFEST_PATH", fake_manifest(tmp_path))
    calls = []
    monkeypatch.setattr(models, "download", lambda found, token=None: calls.append(names(found)))
    monkeypatch.setattr(engines, "download", calls.append)
    assert run(["models", "download", "--engine", "apple"], capsys) == (0, "", "")
    assert calls == ["apple", ["diar", "seg", "emb"]]


def test_apple_s_engine_until_item_4_is_not_in_this_build(tmp_path, monkeypatch, capsys):
    try:
        engines.module("apple")
    except ModuleNotFoundError:
        pass
    else:
        pytest.skip("Apple's engine is in this build (#576)")
    monkeypatch.setattr(models, "MANIFEST_PATH", fake_manifest(tmp_path))
    monkeypatch.setattr(models, "download", lambda found, token=None: pytest.fail("downloaded"))
    code, out, err = run(["models", "download", "--engine", "apple"], capsys)
    assert (code, out, err) == (5, "", "lmt: error: --engine apple: not in this build\n")
    assert engines.status("apple") == {"name": "apple", "status": "not in this build", "ok": False}


def test_the_real_manifest_s_default_is_kyutai_s_pinned_model():
    manifest = models.load_manifest()
    model = manifest.get(manifest.default_asr)
    assert (model.name, model.role, model.repository) == (kyutai.MODEL, "asr", "kyutai/stt-1b-en_fr-mlx")
    assert {f.path for f in model.files} == {kyutai.CONFIG, kyutai.WEIGHTS, kyutai.MIMI, kyutai.TOKENIZER}
    assert names(models.default_models(manifest))[0] == "stt-1b-en_fr"
