"""README.md's results table (G1-14), rendered from results/full.json (design/internal/g1-stage1.md §6).

Standard library only, so the `readme` job needs no extras.
"""

import difflib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from lmt.errors import InputError

START, END = "<!-- results:start -->", "<!-- results:end -->"
PLACEHOLDER = ("No full-set results yet. The Owner's run of `lmt eval --set full` fills this table "
               "(G1 stage 3).")
NULL = "—"


def render(results: dict, source: str = "results/full.json") -> str:
    """The text between the markers for a results file, without its leading and trailing newlines."""
    if results.get("dirty") is not False:
        raise InputError(source, "made from a dirty working tree; it can't be published")
    figures = results["figures"]
    if not figures:
        return PLACEHOLDER
    m, models = results["machine"], results["models"]
    day = datetime.fromisoformat(results["date"]).date().isoformat()
    determinism = results["determinism"]
    identical = NULL if determinism is None else ("yes" if determinism["identical"] else "no")
    lines = [
        (f"Measured on {m['cpu']} ({m['arch']}, {m['memory_bytes'] / 2**30:.1f} GB, {m['os']}) on {day}, "
         f"at commit `{results['commit'][:12]}`, with lmt {results['lmt_version']}."),
        f"Models: ASR {_model(models['asr'])}; diarization {_model(models['diarization'])}.",
        f"Two runs of the CI subset gave identical accuracy figures: {identical}.",
        "",
        "| Set | System | WER | DER | RTF |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for f in figures:
        lines.append(f"| {f['dataset']} | {f['system']} | {_percent(f['wer'])} | {_percent(f['der'])} | "
                     f"{NULL if f['rtf'] is None else format(f['rtf'], '.2f')} |")
    return "\n".join(lines)


def region(text: str, source: str = "README.md") -> tuple[int, int]:
    """The span between the markers: InputError if there isn't exactly one pair, in order."""
    starts, ends = text.count(START), text.count(END)
    if starts != 1 or ends != 1:
        raise InputError(source, f"needs exactly one {START} and one {END}, not {starts} and {ends}")
    start, end = text.index(START) + len(START), text.index(END)
    if end < start:
        raise InputError(source, f"{END} comes before {START}")
    return start, end


def check(readme: Path, results: Path) -> str | None:
    """None if README.md's table equals what the results file renders, else a unified diff."""
    text = _read(readme)
    start, end = region(text, str(readme))
    have = text[start:end].strip("\n")
    want = _render_file(results)
    if have == want:
        return None
    return "".join(difflib.unified_diff(
        (have + "\n").splitlines(keepends=True), (want + "\n").splitlines(keepends=True),
        fromfile=f"{readme} (results table)", tofile=f"rendered from {results}"))


def update(readme: Path, results: Path) -> None:
    """Replace README.md's table with what the results file renders, by a temporary file renamed into place."""
    text = _read(readme)
    start, end = region(text, str(readme))
    new = text[:start] + "\n" + _render_file(results) + "\n" + text[end:]
    readme = Path(readme)
    fd, tmp = tempfile.mkstemp(dir=readme.parent, prefix=f".{readme.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        os.replace(tmp, readme)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _model(model: dict) -> str:
    return f"`{model['name']}` (`{model['repository']}` at `{model['revision'][:12]}`)"


def _percent(value: float | None) -> str:
    return NULL if value is None else f"{value * 100:.1f} %"


def _read(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(str(path), f"can't read it: {e}") from e


def _render_file(path: Path) -> str:
    try:
        results = json.loads(_read(path))
    except json.JSONDecodeError as e:
        raise InputError(str(path), f"isn't valid JSON: {e}") from e
    if not isinstance(results, dict):
        raise InputError(str(path), "isn't a results file: not a JSON object")
    try:
        return render(results, str(path))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise InputError(str(path), f"isn't a version 1.0 results file: {type(e).__name__}: {e}") from e
