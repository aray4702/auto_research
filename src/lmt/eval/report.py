"""The results file, `results/<set>.json` and, for Apple's engine, `results/<set>-apple.json`, version 2.0
(design/public-api.md, "Results file"; design/internal/g1-stage1.md §4)."""

import copy
import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from lmt import __version__

ENGINES = ("kyutai", "apple")  # F11: the default first
TIMING_FIELDS = ("date", "wall_seconds", "rtf", "peak_rss_bytes",
                 "p50_delay_seconds", "p95_delay_seconds", "max_delay_seconds",
                 "p50_label_delay_seconds", "p95_label_delay_seconds", "max_label_delay_seconds")
# in each of a live figure's `runs`
RUN_TIMING_FIELDS = ("p95_delay_seconds", "p95_label_delay_seconds", "behind_seconds")


def file_name(set_name: str, engine: str = "kyutai") -> str:
    """`<set>.json` for Kyutai, the default; `<set>-apple.json` for Apple's engine."""
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}, not {engine!r}")
    return f"{set_name}.json" if engine == "kyutai" else f"{set_name}-{engine}.json"


def build(set_name, figures, models, determinism=None, *, out_dir, engine="kyutai",
          commit=None, dirty=None, date=None, machine=None) -> dict:
    """The results file, in public-api.md's key order. `commit`, `dirty`, `date` and `machine` are for
    tests; left as None, each is read from git, the clock and this machine."""
    file_name(set_name, engine)  # ValueError for an unknown engine
    return {
        "schema_version": "2.0",
        "set": set_name,
        "engine": engine,
        "commit": _commit() if commit is None else commit,
        "dirty": _dirty(out_dir) if dirty is None else dirty,
        "date": datetime.now().astimezone().isoformat(timespec="seconds") if date is None else date,
        "machine": _this_machine() if machine is None else machine,
        "lmt_version": __version__,
        "models": models,
        "figures": list(figures),
        "determinism": determinism,
    }


def write(path, results) -> None:
    """Write the results file by a temporary file in the same directory, renamed into place."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(_serialize(results))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def strip_timing(results) -> dict:
    """A copy without the timing fields, at the top level, in each figure and in each of a live figure's
    runs, keeping the runs' entries (G1-5, G1-12)."""
    stripped = {k: v for k, v in results.items() if k not in TIMING_FIELDS}
    stripped["figures"] = [_strip_figure(f) for f in results["figures"]]
    return copy.deepcopy(stripped)


def _strip_figure(figure) -> dict:
    stripped = {k: v for k, v in figure.items() if k not in TIMING_FIELDS}
    if "runs" in stripped:
        stripped["runs"] = [{k: v for k, v in r.items() if k not in RUN_TIMING_FIELDS} for r in stripped["runs"]]
    return stripped


def same_accuracy(a, b) -> bool:
    """Whether two results files, their timing fields removed, serialize to the same bytes."""
    return _serialize(strip_timing(a)).encode("utf-8") == _serialize(strip_timing(b)).encode("utf-8")


def machine() -> dict:
    """This machine's `arch`, `cpu`, `memory_bytes` and `os`, on macOS or Linux."""
    if sys.platform == "darwin":
        cpu = _run("sysctl", "-n", "machdep.cpu.brand_string")
        memory = int(_run("sysctl", "-n", "hw.memsize"))
        os_name = "macOS " + platform.mac_ver()[0]
    else:
        cpu = _field("/proc/cpuinfo", "model name", ":")
        mem_total = _field("/proc/meminfo", "MemTotal", ":")  # "16384000 kB"
        memory = int(mem_total.split()[0]) * 1024
        os_name = _field("/etc/os-release", "PRETTY_NAME", "=").strip('"')
    return {"arch": platform.machine(), "cpu": cpu, "memory_bytes": memory, "os": os_name}


_this_machine = machine  # build's `machine` argument shadows the function


def _serialize(results) -> str:
    return json.dumps(results, indent=2, ensure_ascii=False) + "\n"


def _commit() -> str | None:
    """`git rev-parse HEAD`, or None outside a git checkout."""
    try:
        return _run("git", "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        return None


def _dirty(out_dir) -> bool | None:
    """Whether the work tree has changes, outside `out_dir` when it's inside the tree; None outside a
    git checkout. An `out_dir` outside the tree can't dirty it, and excluding it would make git fail."""
    try:
        top = _run("git", "rev-parse", "--show-toplevel")
    except (OSError, subprocess.CalledProcessError):
        return None
    command = ["git", "-C", top, "status", "--porcelain"]
    out, root = Path(out_dir).resolve(), Path(top).resolve()
    if out.is_relative_to(root):
        command += ["--", ".", f":(exclude){out.relative_to(root).as_posix()}"]
    return _run(*command) != ""


def _run(*command: str) -> str:
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()


def _field(path: str, key: str, separator: str) -> str:
    """The value of the first `key<separator>value` line in a file."""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition(separator)
        if sep and name.strip() == key:
            return value.strip()
    raise ValueError(f"{path}: no {key} line")
