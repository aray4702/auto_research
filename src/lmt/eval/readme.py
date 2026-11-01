"""README.md's results table (G1-14), rendered from every results file present in the results directory:
`full.json` (Kyutai), `full-apple.json`, `live.json` and `live-apple.json` (design/internal/g1-stage1.md §6;
public-api.md, "G4's changes to the commands").

Standard library only, so the `readme` job needs no extras.
"""

import difflib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from lmt.errors import InputError

START, END = "<!-- results:start -->", "<!-- results:end -->"
FILES = ("full", "full-apple", "live", "live-apple")  # the table's order; full.json is required
PLACEHOLDER = ("No full-set results yet. The Owner's run of `lmt eval --set full` fills this table "
               "(G1 stage 3).")
LIVE_PLACEHOLDER = "No live results yet. The Owner's run of `lmt eval --set live` fills this table."
NULL = "—"
ENGINES = {"kyutai": "Kyutai STT 1B", "apple": "Apple's SpeechTranscriber"}
# The three RTFs' timing bases (design/internal/g1-stage2.md, "The runs"), stated where readers see them.
RTF_NOTE = ("RTF is wall-clock time over audio duration. For the product, it runs from process start to exit, "
            "model loading included. {alone} and pyannote alone come from that same run: each is its own "
            "stage's model loading and inference, without start-up, decoding or export, and, with diarization "
            "on, while the other stage runs beside it. So the three RTFs aren't like for like.")
DELAY_NOTE = ("Delays are measured from the end of each matched word, pooled over the runs, each played back in "
              "real time: the word delay to its text, the label delay to its speaker label (Q10).")


def render(results: dict, source: str = "results/full.json") -> str:
    """A full-set results file's block, without its leading and trailing newlines."""
    if results.get("dirty") is not False:
        raise InputError(source, "made from a dirty working tree; it can't be published")
    figures = results["figures"]
    if not figures:
        return PLACEHOLDER
    determinism = results["determinism"]
    if determinism is not None:
        repeat = ("Two runs of the CI subset gave identical accuracy figures: "
                  f"{'yes' if determinism['identical'] else 'no'}.")
    elif results.get("engine") == "apple":
        repeat = "Apple's engine is exempt from the repeat comparison (Q7)."
    else:
        repeat = f"Two runs of the CI subset gave identical accuracy figures: {NULL}."
    lines = [*_provenance(results), repeat, "",
             "| Set | System | WER | DER | RTF |",
             "| --- | --- | ---: | ---: | ---: |"]
    for f in figures:
        lines.append(f"| {f['dataset']} | {f['system']} | {_percent(f['wer'])} | {_percent(f['der'])} | "
                     f"{NULL if f['rtf'] is None else format(f['rtf'], '.2f')} |")
    alone = "Whisper alone" if any(f["system"] == "whisper-alone" for f in figures) else "The engine alone"
    lines += ["", RTF_NOTE.format(alone=alone)]
    return "\n".join(lines)


def render_live(results: dict, source: str = "results/live.json") -> str:
    """A live results file's block (G2-6, G4-8), without its leading and trailing newlines."""
    if results.get("dirty") is not False:
        raise InputError(source, "made from a dirty working tree; it can't be published")
    figures = results["figures"]
    if not figures:
        return LIVE_PLACEHOLDER
    lines = [*_provenance(results), "",
             ("| Set | System | Runs | Matched words | Word delay p50 / p95 / max | "
              "Label delay p50 / p95 / max | WER | DER |"),
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for f in figures:
        word = " / ".join(_seconds(f[f"{q}_delay_seconds"]) for q in ("p50", "p95", "max"))
        label = " / ".join(_seconds(f.get(f"{q}_label_delay_seconds")) for q in ("p50", "p95", "max"))
        lines.append(f"| {f['dataset']} | {f['system']} | {len(f['runs'])} | {f['matched_words']} | {word} | "
                     f"{label} | {_percent(f['wer'])} | {_percent(f['der'])} |")
    lines += ["", DELAY_NOTE]
    return "\n".join(lines)


def render_all(results_dir: Path) -> str:
    """The text between the markers: one block per results file present, in FILES order, each under a line
    naming its set, engine and file. `full.json` must be present; a file is read whichever version it is, so
    the Whisper-era `full.json` (1.0) renders until stage 2's runs replace it."""
    blocks = []
    for name in FILES:
        path = Path(results_dir) / f"{name}.json"
        if name != "full" and not path.exists():
            continue
        results = _load(path)
        source = path.as_posix()
        try:
            block = (render_live if name.startswith("live") else render)(results, source)
            engine = ENGINES.get(results.get("engine")) or f"`{results['models']['asr']['name']}`"
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise InputError(source, f"isn't a results file: {type(e).__name__}: {e}") from e
        kind = "Live delays" if name.startswith("live") else "Full sets"
        # Named as published, so the table doesn't depend on --out.
        blocks.append(f"**{kind}, {engine}** (`results/{name}.json`)\n\n{block}")
    return "\n\n".join(blocks)


def region(text: str, source: str = "README.md") -> tuple[int, int]:
    """The span between the markers: InputError if there isn't exactly one pair, in order."""
    starts, ends = text.count(START), text.count(END)
    if starts != 1 or ends != 1:
        raise InputError(source, f"needs exactly one {START} and one {END}, not {starts} and {ends}")
    start, end = text.index(START) + len(START), text.index(END)
    if end < start:
        raise InputError(source, f"{END} comes before {START}")
    return start, end


def check(readme: Path, results_dir: Path) -> str | None:
    """None if README.md's table equals what the results files render, else a unified diff."""
    text = _read(readme)
    start, end = region(text, str(readme))
    have = text[start:end].strip("\n")
    want = render_all(results_dir)
    if have == want:
        return None
    return "".join(difflib.unified_diff(
        (have + "\n").splitlines(keepends=True), (want + "\n").splitlines(keepends=True),
        fromfile=f"{readme} (results table)", tofile=f"rendered from {results_dir}"))


def update(readme: Path, results_dir: Path) -> None:
    """Replace README.md's table with what the results files render, by a temporary file renamed into place."""
    text = _read(readme)
    start, end = region(text, str(readme))
    new = text[:start] + "\n" + render_all(results_dir) + "\n" + text[end:]
    readme = Path(readme)
    fd, tmp = tempfile.mkstemp(dir=readme.parent, prefix=f".{readme.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        os.replace(tmp, readme)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _provenance(results: dict) -> list[str]:
    """The machine, date, commit and version line, and the models line."""
    m, models = results["machine"], results["models"]
    day = datetime.fromisoformat(results["date"]).date().isoformat()
    diarization = NULL if models["diarization"] is None else _model(models["diarization"])
    return [
        (f"Measured on {m['cpu']} ({m['arch']}, {m['memory_bytes'] / 2**30:.1f} GB, {m['os']}) on {day}, "
         f"at commit `{results['commit'][:12]}`, with lmt {results['lmt_version']}."),
        f"Models: ASR {_model(models['asr'])}; diarization {diarization}.",
    ]


def _model(model: dict) -> str:
    # A commit hash is shortened; Apple's revision, its macOS version and locale (Q7), is shown whole.
    revision = model["revision"]
    if re.fullmatch(r"[0-9a-f]{40}", revision):
        revision = revision[:12]
    return f"`{model['name']}` (`{model['repository']}` at `{revision}`)"


def _percent(value: float | None) -> str:
    return NULL if value is None else f"{value * 100:.1f} %"


def _seconds(value: float | None) -> str:
    return NULL if value is None else f"{value:.2f} s"


def _read(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(str(path), f"can't read it: {e}") from e


def _load(path: Path) -> dict:
    try:
        results = json.loads(_read(path))
    except json.JSONDecodeError as e:
        raise InputError(str(path), f"isn't valid JSON: {e}") from e
    if not isinstance(results, dict):
        raise InputError(str(path), "isn't a results file: not a JSON object")
    return results
