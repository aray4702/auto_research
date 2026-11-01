"""G4-9 (spec/acceptance.md): Whisper retired, and CI on macOS. The lock, the model manifest, lmt's imports and the
product's workflows, read from the tree."""

import ast
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GONE = ("faster-whisper", "ctranslate2", "openai-whisper")
GONE_MODULES = ("faster_whisper", "ctranslate2", "whisper")
WORKFLOWS = ("product.yml", "install-full.yml")

pytestmark = pytest.mark.criterion("G4-9")


def test_neither_the_project_nor_the_lock_holds_whisper():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = project["project"]["dependencies"] + [d for extra in project["project"].get("optional-dependencies", {}).values()
                                                 for d in extra]
    assert not [d for d in deps if d.split(";")[0].split("=")[0].split(">")[0].strip().lower() in GONE], deps
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert not [p["name"] for p in lock["package"] if p["name"] in GONE]


def test_the_manifest_pins_no_whisper_model():
    manifest = tomllib.loads((ROOT / "src" / "lmt" / "models.toml").read_text(encoding="utf-8"))
    names = [(m["name"], m["repository"]) for m in manifest["model"]]
    assert not [n for n in names if "whisper" in (n[0] + n[1]).lower()], names
    assert "whisper" not in manifest["default_asr"].lower()


def test_no_module_of_lmt_imports_whisper():
    found = []
    for path in sorted((ROOT / "src" / "lmt").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            found += [f"{path.relative_to(ROOT)}: {n}" for n in names if n.split(".")[0] in GONE_MODULES]
    assert not found, found


def test_the_product_workflows_run_only_on_macos_26():
    for name in WORKFLOWS:
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        runners = {line.split(":", 1)[1].strip() for line in text.splitlines() if line.strip().startswith("runs-on:")}
        runners |= {line.split(":", 1)[1].strip() for line in text.splitlines() if line.strip().startswith("- os:")}
        assert runners <= {"macos-26", "${{ matrix.os }}"}, (name, runners)
        assert "ubuntu" not in text, name
