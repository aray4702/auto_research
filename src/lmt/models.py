"""The model manifest, download, checksums and cache, and `lmt models` (design/internal/g1-stage1.md §3).

Every model file is pinned in models.toml by repository, revision, SHA-256 and size (G1-12), and is
verified before use. The cache is our own, `<LMT_CACHE_DIR>/<repository>/<revision>/<path>`, so a stage
loads a model from a local directory and never resolves a name over the network.
"""

import contextlib
import hashlib
import os
import re
import shutil
import sys
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path

from lmt.errors import InputError, ModelError, UsageError

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"  # before huggingface_hub is imported (Q8)

MANIFEST_PATH = Path(__file__).with_name("models.toml")  # tests point it at a small manifest
SCHEMA_VERSION = 1
ROLES = ("asr", "diarization", "segmentation", "embedding")
DIARIZATION_ROLES = ROLES[1:]
CHUNK = 1 << 20  # hash in 1 MiB chunks

_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_verified = set()  # the files ensure() has hashed in this process


@dataclass(frozen=True)
class ModelFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Model:
    name: str
    role: str
    repository: str
    revision: str
    licence: str
    creators: str
    card: str
    gated: bool
    files: tuple[ModelFile, ...]


@dataclass(frozen=True)
class Manifest:
    default_asr: str
    models: tuple[Model, ...]

    def get(self, name):
        return next((m for m in self.models if m.name == name), None)


def cache_dir() -> Path:
    return Path(os.environ.get("LMT_CACHE_DIR") or Path.home() / ".cache" / "lmt" / "models")


def load_manifest(path=None) -> Manifest:
    """Read and check the manifest. A malformed one is an InputError naming the file."""
    path = Path(path or MANIFEST_PATH)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise InputError(str(path), f"can't read the model manifest: {e}") from None

    def bad(problem):
        return InputError(str(path), problem)

    if data.get("schema_version") != SCHEMA_VERSION:
        raise bad(f"schema_version must be {SCHEMA_VERSION}")
    models = []
    for entry in data.get("model", []):
        name = entry.get("name")
        for key, kind in (("name", str), ("role", str), ("repository", str), ("revision", str),
                          ("licence", str), ("creators", str), ("card", str), ("gated", bool)):
            if not isinstance(entry.get(key), kind):
                raise bad(f"model {name!r}: {key} must be a {kind.__name__}")
        if entry["role"] not in ROLES:
            raise bad(f"model {name!r}: role must be one of {', '.join(ROLES)}")
        if not _REVISION.match(entry["revision"]):
            raise bad(f"model {name!r}: revision must be a full 40-hex commit")
        files = []
        for f in entry.get("file", []):
            if not isinstance(f.get("path"), str) or not f["path"] or Path(f["path"]).is_absolute() \
                    or ".." in Path(f["path"]).parts:
                raise bad(f"model {name!r}: a file's path must be relative, inside the repository")
            if not isinstance(f.get("sha256"), str) or not _SHA256.match(f["sha256"]):
                raise bad(f"model {name!r}: {f['path']}: sha256 must be 64 lowercase hex digits")
            if not isinstance(f.get("size"), int) or isinstance(f["size"], bool) or f["size"] <= 0:
                raise bad(f"model {name!r}: {f['path']}: size must be a positive integer")
            files.append(ModelFile(f["path"], f["sha256"], f["size"]))
        if not files:
            raise bad(f"model {name!r}: no files")
        models.append(Model(name, entry["role"], entry["repository"], entry["revision"], entry["licence"],
                            entry["creators"], entry["card"], entry["gated"], tuple(files)))
    names = [m.name for m in models]
    if len(set(names)) != len(names):
        raise bad("a model name is repeated")
    manifest = Manifest(data.get("default_asr"), tuple(models))
    default = manifest.get(manifest.default_asr)
    if default is None or default.role != "asr":
        raise bad("default_asr must name an asr model")
    for role in DIARIZATION_ROLES:
        if sum(m.role == role for m in models) != 1:
            raise bad(f"there must be exactly one {role} model")
    return manifest


def default_models(manifest, asr=None) -> list[Model]:
    """The ASR model (default_asr, or `asr`) and the three pyannote models."""
    name = asr or manifest.default_asr
    model = manifest.get(name)
    if model is None or model.role != "asr":
        choices = ", ".join(m.name for m in manifest.models if m.role == "asr")
        raise UsageError("--model", f"{name!r} isn't an ASR model in the manifest ({choices})")
    return [model] + [m for role in DIARIZATION_ROLES for m in manifest.models if m.role == role]


def model_dir(model) -> Path:
    return cache_dir() / model.repository / model.revision


def sha256_of(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _matches(path, f) -> bool:
    return path.stat().st_size == f.size and sha256_of(path) == f.sha256


def file_status(model, f) -> str:
    """"verified", "missing" or "mismatch". Never deletes."""
    path = model_dir(model) / f.path
    if not path.is_file():
        return "missing"
    return "verified" if _matches(path, f) else "mismatch"


def model_status(model) -> tuple[str, int]:
    """The worst of its files' statuses, and its verified files' bytes. Hashes each file once."""
    statuses = [(f, file_status(model, f)) for f in model.files]
    worst = next(s for s in ("mismatch", "missing", "verified") if any(s == got for _, got in statuses))
    return worst, sum(f.size for f, got in statuses if got == "verified")


def download(models, token=None) -> None:
    """Download every file not already verified, checking its size and SHA-256. Raises ModelError."""
    token = token or os.environ.get("HF_TOKEN") or None
    for model in models:
        todo = [f for f in model.files if file_status(model, f) != "verified"]
        if todo and model.gated and not token:
            raise ModelError("HF_TOKEN", f"needed to download the gated model {model.repository}")
        for f in todo:
            _download_file(model, f, token)


def _download_file(model, f, token):
    from huggingface_hub import hf_hub_download

    print(f"lmt: downloading {model.repository}/{f.path} ({f.size} bytes)", file=sys.stderr)
    target = model_dir(model) / f.path
    staging = cache_dir() / ".partial" / uuid.uuid4().hex
    try:
        try:
            got = Path(hf_hub_download(model.repository, f.path, revision=model.revision,
                                       local_dir=staging, token=token or False))
        except Exception as e:  # noqa: BLE001 -- any network or HTTP error is one line naming the repository
            raise ModelError(model.repository, f"download of {f.path} failed: {type(e).__name__}") from None
        if not _matches(got, f):
            raise ModelError(f"{model.repository}/{f.path}", "SHA-256 doesn't match the manifest")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(got, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        with contextlib.suppress(OSError):
            staging.parent.rmdir()  # .partial/, once no other download uses it


def verify(models) -> list[Path]:
    """Re-hash every file present. Each mismatched file is deleted; returns their paths."""
    deleted = []
    for model in models:
        for f in model.files:
            if file_status(model, f) == "mismatch":
                path = model_dir(model) / f.path
                path.unlink()
                deleted.append(path)
    return deleted


def ensure(models, offline=False) -> dict[str, Path]:
    """For stage 2: name -> verified model directory. Each file is hashed once per process. A mismatched
    file is deleted (exit 5); a missing one downloads, or is an error under `offline`. Then HF_HUB_OFFLINE."""
    for model in models:
        for f in model.files:
            key = (model.repository, model.revision, f.path, str(cache_dir()))
            if key in _verified:
                continue
            status = file_status(model, f)
            if status == "mismatch":
                (model_dir(model) / f.path).unlink()
                raise ModelError(f"{model.repository}/{f.path}",
                                 "SHA-256 doesn't match the manifest; the file was deleted, so run "
                                 "lmt models download")
            if status == "missing":
                if offline:
                    raise ModelError(f"{model.repository}/{f.path}",
                                     "missing, and --offline never downloads; run lmt models download")
                download([model])
            _verified.add(key)
    os.environ["HF_HUB_OFFLINE"] = "1"  # architecture, "Model handling": nothing resolves over the network
    return {model.name: model_dir(model) for model in models}


def cli_models(args) -> int:
    manifest = load_manifest(MANIFEST_PATH)
    if args.action == "download":
        download(default_models(manifest, asr=args.model))
        return 0
    if args.action == "list":
        return _list(manifest, args.bytes)
    return _verify(manifest)


def _list(manifest, with_bytes):
    defaults = {m.name for m in default_models(manifest)}
    header = ["NAME", "ROLE", "REVISION", "DEFAULT", "STATUS"] + (["BYTES"] if with_bytes else [])
    print("\t".join(header))
    total = 0
    for model in manifest.models:
        status, size = model_status(model)
        row = [model.name, model.role, model.revision, "yes" if model.name in defaults else "no", status]
        if with_bytes:
            total += size if model.name in defaults else 0
            row.append(str(size))
        print("\t".join(row))
    if with_bytes:
        print(f"total-default\t{total}")
    return 0


def _verify(manifest):
    present = [m for m in manifest.models if any((model_dir(m) / f.path).is_file() for f in m.files)]
    for model in manifest.models:
        for f in model.files:
            if not (model_dir(model) / f.path).is_file():
                print(f"lmt: missing: {model.repository}/{f.path}", file=sys.stderr)
    deleted = verify(present)
    if deleted:
        raise ModelError(str(deleted[0]), f"SHA-256 doesn't match the manifest; deleted "
                                          f"{len(deleted)} file(s), so run lmt models download")
    return 0
