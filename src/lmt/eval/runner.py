"""The runs behind `lmt eval`: each scored file through the product run, in a process of its own, and the
results file (design/internal/g1-stage2.md, "The runs").

The worker (`lmt.eval.worker`) runs `lmt transcribe` unchanged and records ASR's words and diarization's
turns, which give Whisper alone and pyannote alone from the same run, as `architecture.md` has each stage's
output computed once per file and reused.

The three RTFs' timing bases differ, and that is stated, not measured away. The product's runs from process
start to exit, model loading included (spec, RTF). Whisper alone's and pyannote alone's is their own stage's
time within that same run: the model's loading and its inference, without interpreter start, imports, decode
or export; with diarization on, the two stages run side by side, so each is timed under the other's load.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from lmt.errors import InputError
from lmt.eval import datasets, report
from lmt.eval.score import Hypothesis, Item, Turn, hypothesis_from_transcript, score

WORKER = ("-m", "lmt.eval.worker")  # tests point it at tests/fixtures/eval/fake_worker.py
STAGES = ".lmt-eval-stages.json"  # beside the outputs, under names no input stem gives them
STDERR = ".lmt-eval-stderr.txt"


@dataclass(frozen=True)
class Run:
    transcript: dict
    words: list
    turns: list | None
    wall_seconds: float
    peak_rss_bytes: int
    asr_seconds: float
    diarize_seconds: float | None


def run_product(item: Item, work_dir: Path) -> Run:
    """The product run on one item, in a new empty directory under `work_dir`: `lmt transcribe`, with
    `--no-diarize` unless the item has reference turns. A non-zero exit raises InputError."""
    run_dir = Path(tempfile.mkdtemp(dir=work_dir, prefix="run-"))
    stages = run_dir / STAGES
    argv = [sys.executable, *WORKER, str(stages), "transcribe", str(item.audio)]
    if item.ref_turns is None:
        argv.append("--no-diarize")
    with open(run_dir / STDERR, "wb") as err:
        start = time.perf_counter()
        process = subprocess.Popen(argv, cwd=run_dir, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=err)
        _, status, usage = os.wait4(process.pid, 0)
        wall = time.perf_counter() - start
    code = process.returncode = os.waitstatus_to_exitcode(status)
    if code != 0:
        lines = (run_dir / STDERR).read_text(encoding="utf-8", errors="replace").strip().splitlines()
        last = lines[-1] if lines else "nothing on stderr"
        raise InputError(str(item.audio), f"lmt transcribe exited {code}: {last}")
    # ru_maxrss is in kilobytes on Linux and bytes on macOS: the counter `/usr/bin/time -l` prints (G1-10).
    peak = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
    recorded = json.loads(stages.read_text(encoding="utf-8"))
    transcript = json.loads((run_dir / f"{Path(item.audio).stem}.json").read_text(encoding="utf-8"))
    return Run(transcript=transcript, words=recorded["words"], turns=recorded["turns"], wall_seconds=wall,
               peak_rss_bytes=peak, asr_seconds=recorded["asr_seconds"],
               diarize_seconds=recorded["diarize_seconds"])


def hypotheses(item: Item, run: Run) -> dict[tuple[str, str, str], Hypothesis]:
    """The product's, Whisper alone's and, when diarization ran, pyannote alone's hypotheses for one item,
    keyed by (system, dataset, item id), each with its own timing basis (the module's docstring)."""
    product = hypothesis_from_transcript(run.transcript)
    hyps = {
        ("product", item.dataset, item.id): Hypothesis(product.text, product.turns, run.wall_seconds,
                                                       run.peak_rss_bytes),
        ("whisper-alone", item.dataset, item.id): Hypothesis(" ".join(w[2] for w in run.words), None,
                                                             run.asr_seconds),
    }
    if run.turns is not None:
        turns = tuple(Turn(float(start), float(end), speaker) for start, end, speaker in run.turns)
        hyps["pyannote-alone", item.dataset, item.id] = Hypothesis(None, turns, run.diarize_seconds)
    return hyps


def evaluate(set_name: str, *, out_dir: Path, determinism=None) -> dict:
    """Fetch the set, run every item in order, one at a time, score them, and write <out_dir>/<set>.json.
    Returns the results. Nothing but the results file is left behind."""
    items = datasets.fetch(set_name)
    runs = []
    with tempfile.TemporaryDirectory(prefix="lmt-eval-") as work:
        for i, item in enumerate(items, start=1):
            run = run_product(item, Path(work))
            print(f"lmt: eval: {item.dataset} {item.id} ({i}/{len(items)}) {run.wall_seconds:.1f} s",
                  file=sys.stderr)
            runs.append(run)
    hyps = {}
    for item, run in zip(items, runs, strict=True):
        hyps.update(hypotheses(item, run))
    figures = score(items, hyps)
    diarized = [run for run in runs if run.turns is not None]
    models = (diarized or runs)[0].transcript["models"]
    results = report.build(set_name, figures, models, determinism, out_dir=out_dir)
    report.write(Path(out_dir) / f"{set_name}.json", results)
    return results
