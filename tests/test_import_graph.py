"""The import graph keeps the tier exceptions safe (design/internal/g3-stage1.md §0, 0b).

`rename.py` and `demo/` are left out of tier 3, so `lmt.eval` must reach neither, and the plain
install's modules must load without the `eval` extra.
"""

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

EVAL_EXTRA = ("jiwer", "pyannote.metrics")
PLAIN_MODULES = ("lmt.cli", "lmt.rename", "lmt.demo.server", "lmt.demo.sample")


def modules(src: Path, top: str) -> dict[str, Path]:
    """{dotted name: file} for every `.py` under `src/top`, a package named as its `__init__`."""
    found = {}
    for path in sorted((src / top).rglob("*.py")):
        parts = list(path.relative_to(src).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        found[".".join(parts)] = path
    return found


def imports_of(name: str, path: Path, known: dict[str, Path]) -> set[str]:
    """The modules in `known` that `name` imports anywhere in its file, functions included."""
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    edges = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                base = ".".join(base + ([node.module] if node.module else []))
            else:
                base = node.module
            targets = [base] + [f"{base}.{alias.name}" for alias in node.names]
        else:
            continue
        for target in targets:
            # `import a.b.c` runs each package's `__init__` on the way down.
            parts = target.split(".")
            for i in range(1, len(parts) + 1):
                prefix = ".".join(parts[:i])
                if prefix in known:
                    edges.add(prefix)
    edges.discard(name)
    return edges


def graph(src: Path, top: str) -> dict[str, set[str]]:
    known = modules(src, top)
    return {name: imports_of(name, path, known) for name, path in known.items()}


def reached(edges: dict[str, set[str]], start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        for nxt in edges[todo.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def forbidden_reaches(src: Path, top: str) -> list[str]:
    """`eval` module -> `rename` or `demo` module, for each path the graph allows."""
    edges = graph(src, top)
    bad = []
    for name in sorted(edges):
        if name == f"{top}.eval" or name.startswith(f"{top}.eval."):
            for hit in sorted(reached(edges, name)):
                if hit == f"{top}.rename" or hit == f"{top}.demo" or hit.startswith(f"{top}.demo."):
                    bad.append(f"{name} -> {hit}")
    return bad


def test_eval_reaches_neither_rename_nor_demo():
    assert forbidden_reaches(SRC, "lmt") == []


def test_the_graph_check_fails_on_an_indirect_edge(tmp_path):
    files = {
        "pkg/__init__.py": "",
        "pkg/eval/__init__.py": "",
        "pkg/eval/score.py": "def f():\n    from .. import middle\n",
        "pkg/middle.py": "from pkg.demo import server\n",
        "pkg/demo/__init__.py": "",
        "pkg/demo/server.py": "",
        "pkg/rename.py": "",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)

    assert forbidden_reaches(tmp_path, "pkg") == [
        "pkg.eval.score -> pkg.demo",
        "pkg.eval.score -> pkg.demo.server",
    ]


BLOCK_EVAL_EXTRA = f"""
import importlib, sys

class Block:
    def find_spec(self, name, path=None, target=None):
        if any(name == top or name.startswith(top + ".") for top in {EVAL_EXTRA!r}):
            raise ImportError(f"{{name}} is in the eval extra")
        return None

sys.meta_path.insert(0, Block())
for module in {PLAIN_MODULES!r}:
    importlib.import_module(module)
"""


def test_the_plain_install_loads_without_the_eval_extra():
    result = subprocess.run(
        [sys.executable, "-c", BLOCK_EVAL_EXTRA], check=False, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
