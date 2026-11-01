"""The model manifest, download, checksums, cache and `lmt models` (design/internal/g1-stage1.md §3)."""

import hashlib
import os
import re
import tomllib
from pathlib import Path

import pytest

from lmt import cli, models
from lmt.errors import InputError, ModelError

ROOT = Path(__file__).resolve().parents[1]
DISK_BOUND = 2.5e9  # G1-10 since G4 (#565): each model takes ≤ 2.5 × 10⁹ bytes on disk, counted separately
LICENCE_NAMES = {"MIT": "MIT License", "CC-BY-4.0": "CC BY 4.0"}  # design §3: MIT or CC-BY-4.0 (answer 13a)

GOOD = b"the model's weights\n"
CONFIG = b"config: 1\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fake_manifest(tmp_path, *, gated=False, weights=GOOD):
    """A small manifest: one ASR model with two files, and the three diarization roles."""
    def model(name, role, repo, files, gated):
        lines = [(f'[[model]]\nname = "{name}"\nrole = "{role}"\nrepository = "{repo}"\n'
                  f'revision = "{"a" * 40}"\nlicence = "MIT"\ncreators = "someone"\n'
                  f'card = "https://example.invalid/{repo}"\ngated = {str(gated).lower()}\n')]
        for path, data in files:
            lines.append(f'[[model.file]]\npath = "{path}"\nsha256 = "{sha(data)}"\nsize = {len(data)}\n')
        return "\n".join(lines)

    text = "\n".join([
        'schema_version = 1\ndefault_asr = "tiny"\n',
        model("tiny", "asr", "org/tiny", [("config.json", CONFIG), ("model.bin", weights)], False),
        model("other", "asr", "org/other", [("model.bin", b"other\n")], False),
        model("diar", "diarization", "org/diar", [("config.yaml", CONFIG)], gated),
        model("seg", "segmentation", "org/seg", [("pytorch_model.bin", b"seg\n")], gated),
        model("emb", "embedding", "org/emb", [("pytorch_model.bin", b"emb\n")], gated),
    ])
    path = tmp_path / "models.toml"
    path.write_text(text, encoding="utf-8")
    return path


def hub_env(monkeypatch, cache):
    monkeypatch.setenv("LMT_CACHE_DIR", str(cache))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    # Not delenv: it records nothing when the variable is absent, so teardown wouldn't undo ensure()'s.
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """The fake manifest in MANIFEST_PATH, an empty LMT_CACHE_DIR, no HF_TOKEN, and hf_hub_download
    replaced by one that writes known bytes. `served` maps (repo, path) to the bytes it serves."""
    cache = tmp_path / "cache"
    hub_env(monkeypatch, cache)
    monkeypatch.setattr(models, "MANIFEST_PATH", fake_manifest(tmp_path))
    served = {("org/tiny", "config.json"): CONFIG, ("org/tiny", "model.bin"): GOOD,
              ("org/other", "model.bin"): b"other\n", ("org/diar", "config.yaml"): CONFIG,
              ("org/seg", "pytorch_model.bin"): b"seg\n", ("org/emb", "pytorch_model.bin"): b"emb\n"}
    calls = []

    def fake_download(repo_id, filename, *, revision, local_dir, token):
        calls.append({"repo": repo_id, "path": filename, "revision": revision, "token": token})
        out = Path(local_dir) / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(served[(repo_id, filename)])
        return str(out)

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    return {"cache": cache, "served": served, "calls": calls, "manifest": models.load_manifest(models.MANIFEST_PATH)}


def files_under(path):
    return sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()) if path.exists() else []


def run(argv, capsys):
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


# The real manifest (no model needed).

def real_entries():
    return tomllib.loads(models.MANIFEST_PATH.read_text(encoding="utf-8"))["model"]


@pytest.mark.criterion("G1-12")
def test_real_manifest_pins_every_file():
    entries = real_entries()
    assert len(entries) >= 4
    for entry in entries:
        assert re.fullmatch(r"[0-9a-f]{40}", entry["revision"]), entry["name"]
        assert entry["file"], f"{entry['name']}: no files"
        for f in entry["file"]:
            assert re.fullmatch(r"[0-9a-f]{64}", f["sha256"]), f"{entry['name']}/{f['path']}"
            assert isinstance(f["size"], int) and f["size"] > 0, f"{entry['name']}/{f['path']}"
    manifest = models.load_manifest()  # the loader's own checks agree
    assert [m.role for m in models.default_models(manifest)] == ["asr", "diarization", "segmentation", "embedding"]


@pytest.mark.criterion("G1-15")
def test_real_manifest_licences_are_allowed():
    for entry in real_entries():
        assert entry["licence"] in LICENCE_NAMES, f"{entry['name']}: {entry['licence']}"


@pytest.mark.criterion("G1-15")
def test_notice_and_readme_give_each_models_attribution():
    flat = lambda text: " ".join(text.split())  # NOTICE wraps its lines
    notice = flat((ROOT / "NOTICE").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    licences = re.search(r"^## Licences\n(.*?)(?=^## |\Z)", readme, re.MULTILINE | re.DOTALL)
    assert licences, "README.md has no ## Licences section"
    section = flat(licences.group(1))
    for m in real_entries():
        for where, text in (("NOTICE", notice), ("README.md ## Licences", section)):
            for part in (m["repository"], m["creators"], LICENCE_NAMES[m["licence"]], m["card"]):
                assert part in text, f"{where} lacks {m['name']}'s {part!r}"
        assert f"[`{m['repository']}`]({m['card']})" in section, f"README.md doesn't link {m['name']}'s model card"


@pytest.mark.criterion("G1-10")
def test_every_model_fits_the_disk_bound():
    for model in models.load_manifest().models:
        assert sum(f.size for f in model.files) <= DISK_BOUND, model.name


# A fake manifest, with hf_hub_download replaced.

@pytest.mark.criterion("G1-12")
def test_good_file_is_verified_and_placed(hub):
    tiny = hub["manifest"].get("tiny")
    models.download([tiny])
    assert files_under(hub["cache"]) == [f"org/tiny/{'a' * 40}/config.json", f"org/tiny/{'a' * 40}/model.bin"]
    assert (models.model_dir(tiny) / "model.bin").read_bytes() == GOOD
    assert [models.file_status(tiny, f) for f in tiny.files] == ["verified", "verified"]
    assert [c["revision"] for c in hub["calls"]] == ["a" * 40] * 2
    assert all(c["token"] is False for c in hub["calls"]), "no HF_TOKEN: huggingface_hub mustn't read a token file"
    models.download([tiny])
    assert len(hub["calls"]) == 2, "a verified file isn't downloaded again"


@pytest.mark.criterion("G1-15")
def test_bad_checksum_exits_5_and_leaves_nothing(hub, capsys):
    hub["served"][("org/tiny", "model.bin")] = b"tampered weights\n"
    with pytest.raises(ModelError) as e:
        models.download([hub["manifest"].get("tiny")])
    assert e.value.exit_code == 5
    assert str(e.value) == "org/tiny/model.bin: SHA-256 doesn't match the manifest"
    assert not any("model.bin" in p for p in files_under(hub["cache"])), files_under(hub["cache"])
    assert not (hub["cache"] / ".partial").exists()

    code, out, err = run(["models", "download"], capsys)
    assert (code, out) == (5, "")
    assert err.splitlines()[-1] == "lmt: error: org/tiny/model.bin: SHA-256 doesn't match the manifest"


@pytest.mark.criterion("G1-15", "G1-6")
def test_verify_deletes_a_corrupted_file_and_exits_5(hub, capsys):
    assert run(["models", "download"], capsys)[0] == 0
    weights = models.model_dir(hub["manifest"].get("tiny")) / "model.bin"
    weights.write_bytes(b"the model's weightz\n")  # same size, other bytes

    code, _, err = run(["models", "verify"], capsys)
    assert code == 5
    assert not weights.exists()
    assert err.splitlines()[-1].startswith(f"lmt: error: {weights}: SHA-256 doesn't match the manifest")
    assert "org/other/" in err, "missing files are listed on stderr"
    assert run(["models", "verify"], capsys)[0] == 0, "missing files aren't an error"


def test_ensure_offline_with_a_missing_file_exits_5(hub):
    tiny = hub["manifest"].get("tiny")
    with pytest.raises(ModelError) as e:
        models.ensure([tiny], offline=True)
    assert e.value.exit_code == 5
    assert e.value.subject == "org/tiny/config.json"
    assert hub["calls"] == [] and files_under(hub["cache"]) == []


def test_ensure_downloads_verifies_and_goes_offline(hub):
    tiny = hub["manifest"].get("tiny")
    assert models.ensure([tiny]) == {"tiny": models.model_dir(tiny)}
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert models.ensure([tiny], offline=True) == {"tiny": models.model_dir(tiny)}
    assert len(hub["calls"]) == 2


def test_hub_env_restores_the_offline_flag_ensure_sets(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "unset")  # so this test's own teardown removes it
    monkeypatch.delenv("HF_HUB_OFFLINE")
    with pytest.MonkeyPatch.context() as inner:  # the `hub` fixture's monkeypatch
        hub_env(inner, tmp_path / "cache")
        models.ensure([])
        assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert "HF_HUB_OFFLINE" not in os.environ, "ensure()'s HF_HUB_OFFLINE outlives the hub fixture"


@pytest.mark.criterion("G1-15")
def test_ensure_deletes_a_mismatched_file_and_exits_5(hub):
    tiny = hub["manifest"].get("tiny")
    models.download([tiny])
    (models.model_dir(tiny) / "model.bin").write_bytes(b"corrupted\n")
    with pytest.raises(ModelError) as e:
        models.ensure([tiny])
    assert e.value.exit_code == 5
    assert not (models.model_dir(tiny) / "model.bin").exists()


def test_gated_model_without_hf_token_exits_5_with_one_line(tmp_path, hub, capsys, monkeypatch):
    monkeypatch.setattr(models, "MANIFEST_PATH", fake_manifest(tmp_path, gated=True))
    code, out, err = run(["models", "download"], capsys)
    assert (code, out) == (5, "")
    lines = [line for line in err.splitlines() if line.startswith("lmt: error:")]
    assert lines == ["lmt: error: HF_TOKEN: needed to download the gated model org/diar"]
    assert not any(c["repo"] == "org/diar" for c in hub["calls"])


def test_hf_token_is_passed_and_never_printed(hub, capsys, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_secret_value")
    code, out, err = run(["models", "download"], capsys)
    assert code == 0
    assert {c["token"] for c in hub["calls"]} == {"hf_secret_value"}
    assert len(err.splitlines()) == 5, "one progress line per file of the default models"
    assert "hf_secret_value" not in out + err


@pytest.mark.criterion("G1-6")
def test_download_fetches_the_default_asr_model_and_the_diarization_models(hub, capsys):
    code, out, _ = run(["models", "download"], capsys)
    assert (code, out) == (0, "")
    assert [c["repo"] for c in hub["calls"]] == ["org/tiny", "org/tiny", "org/diar", "org/seg", "org/emb"]
    assert not (hub["cache"] / "org" / "other").exists(), "an ASR model no engine uses isn't downloaded"


@pytest.mark.parametrize("argv", [["--engine", "whisper"], ["--model", "tiny"]])
def test_unknown_engine_or_the_old_model_option_is_a_usage_error(hub, capsys, argv):
    code, _, err = run(["models", "download", *argv], capsys)
    assert code == 2
    assert err.startswith(f"lmt: error: {argv[0]}: ")
    assert hub["calls"] == []


@pytest.mark.criterion("G1-10", "G1-6")
def test_list_bytes_prints_the_documented_columns_and_total(hub, capsys):
    assert run(["models", "download"], capsys)[0] == 0
    (models.model_dir(hub["manifest"].get("seg")) / "pytorch_model.bin").write_bytes(b"sex\n")

    code, out, err = run(["models", "list", "--bytes"], capsys)
    assert (code, err) == (0, "")
    rev = "a" * 40
    assert out.splitlines() == [
        "NAME\tROLE\tREVISION\tDEFAULT\tSTATUS\tBYTES",
        f"tiny\tasr\t{rev}\tyes\tverified\t{len(CONFIG) + len(GOOD)}",
        f"other\tasr\t{rev}\tno\tmissing\t0",
        f"diar\tdiarization\t{rev}\tyes\tverified\t{len(CONFIG)}",
        f"seg\tsegmentation\t{rev}\tyes\tmismatch\t0",
        f"emb\tembedding\t{rev}\tyes\tverified\t4",
    ]
    assert (models.model_dir(hub["manifest"].get("seg")) / "pytorch_model.bin").exists(), "list never deletes"

    code, out, _ = run(["models", "list"], capsys)
    assert out.splitlines()[0] == "NAME\tROLE\tREVISION\tDEFAULT\tSTATUS"
    assert out.splitlines()[1] == f"tiny\tasr\t{rev}\tyes\tverified"


def test_list_bytes_hashes_each_file_once(hub, capsys, monkeypatch):
    assert run(["models", "download"], capsys)[0] == 0
    hashed = []
    real = models.sha256_of
    monkeypatch.setattr(models, "sha256_of", lambda path: hashed.append(path) or real(path))
    assert run(["models", "list", "--bytes"], capsys)[0] == 0
    assert len(hashed) == len(set(hashed)) == 5, hashed  # the default models' five files


def test_malformed_manifest_names_the_file(tmp_path):
    path = fake_manifest(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace(sha(GOOD), "PENDING"), encoding="utf-8")
    with pytest.raises(InputError) as e:
        models.load_manifest(path)
    assert str(e.value).startswith(f"{path}: model 'tiny': model.bin: sha256")


def test_cache_dir_defaults_to_home(monkeypatch):
    monkeypatch.delenv("LMT_CACHE_DIR", raising=False)
    assert models.cache_dir() == Path.home() / ".cache" / "lmt" / "models"


# On the real files, in the test-models job.

@pytest.mark.models
@pytest.mark.criterion("G1-10")
def test_default_models_are_verified_and_fit_the_disk_bound(capsys):
    code, out, err = run(["models", "list", "--bytes"], capsys)
    assert code == 0, err
    rows = [line.split("\t") for line in out.splitlines()]
    assert rows[0] == ["NAME", "ROLE", "REVISION", "DEFAULT", "STATUS", "BYTES"]
    defaults = [r for r in rows[1:] if r[3] == "yes"]
    assert [r[0] for r in defaults] == [m.name for m in models.default_models(models.load_manifest())]
    assert all(r[4] == "verified" for r in defaults), defaults
    assert all(0 < int(r[5]) <= DISK_BOUND for r in defaults), defaults
