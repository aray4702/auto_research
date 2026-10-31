"""`lmt eval --set live`'s measures: matching committed words to the reference, their delays, nearest-rank
percentiles and reading a run's event log (design/internal/g2-stage1.md §5, item 5a), as spec/acceptance.md
G2 "Terms" defines them; the runs of `lmt live` they're measured on (item 5b); the figure that pools
the runs (item 5c); and `lmt eval --set live`, which writes it to `results/live.json` (item 5d).

jiwer is imported inside `match_words`, as `score` imports it, so this module imports without the `eval` extra.
"""

import json
import math
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from lmt import models, transcript
from lmt.errors import InputError
from lmt.eval import datasets, readme, report, runner, score
from lmt.transcript import Word


class RefWord(NamedTuple):  # a reference word with its times; datasets' triples are RefWord(*w)
    start: float
    end: float
    text: str


def key(text: str) -> str:
    """The form both sides are matched in: casefolded, Unicode punctuation (category P) dropped, stripped."""
    folded = text.casefold()
    return "".join(c for c in folded if not unicodedata.category(c).startswith("P")).strip()


def match_words(ref: Sequence[RefWord], hyp: Sequence[tuple[Word, float]]) -> list[tuple[RefWord, float]]:
    """Each matched word, as its reference word with its committed word's time, in reference order: both sides
    keyed, empty keys dropped, and every `equal` chunk of jiwer's alignment pairs a reference word with a
    committed word."""
    import jiwer

    ref_kept = [(w, k) for w in ref if (k := key(w.text))]
    hyp_kept = [(t, k) for w, t in hyp if (k := key(w.text))]
    if not ref_kept or not hyp_kept:
        return []
    out = jiwer.process_words(" ".join(k for _, k in ref_kept), " ".join(k for _, k in hyp_kept))
    matches = []
    for chunk in out.alignments[0]:
        if chunk.type != "equal":
            continue
        refs = range(chunk.ref_start_idx, chunk.ref_end_idx)
        hyps = range(chunk.hyp_start_idx, chunk.hyp_end_idx)
        matches.extend((ref_kept[r][0], hyp_kept[h][0]) for r, h in zip(refs, hyps))
    return matches


def delays(matches: Sequence[tuple[RefWord, float]]) -> list[float]:
    """Each matched word's delay: its committed word's time minus its reference word's end."""
    return [t - ref.end for ref, t in matches]


def percentile(values: Sequence[float], q: float) -> float:
    """The nearest-rank percentile: the ⌈q·n⌉-th smallest of the n values, for 0 < q ≤ 1."""
    if not values:
        raise ValueError("no values")
    if not 0 < q <= 1:
        raise ValueError(f"q must be in (0, 1], not {q}")
    rank = math.ceil(round(q * len(values), 9))  # rounded first, so 0.07 · 100 is rank 7, not 8
    return sorted(values)[rank - 1]


@dataclass(frozen=True)
class RunLog:  # one run of `lmt live --jsonl`
    lines: list[tuple[float, dict]]  # (arrival in seconds from the start event's arrival, event)
    wall_seconds: float
    transcript: dict


def read_events(lines: Sequence[tuple[float, dict]]) -> tuple[list[tuple[Word, float]], float]:
    """Each committed word with its event's arrival, and `behind_seconds`: the stop event's arrival minus its `t`.
    A stream that doesn't start with `start`, doesn't end with `stop`, or whose `t` falls is an InputError."""
    if not lines or lines[0][1].get("type") != "start":
        raise InputError("lmt live", "the event stream doesn't start with a start event")
    if lines[-1][1].get("type") != "stop":
        raise InputError("lmt live", "the event stream doesn't end with a stop event")
    words = []
    previous = None
    for n, (arrival, event) in enumerate(lines, start=1):
        t = event["t"]
        if previous is not None and t < previous:
            raise InputError("lmt live", f"event {n}'s t {t} falls below the previous event's {previous}")
        previous = t
        if event["type"] == "words":
            for w in event["words"]:
                words.append((Word(w["start"], w["end"], w["text"], w["probability"]), arrival))
    stop_arrival, stop = lines[-1]
    return words, stop_arrival - stop["t"]


def read_text(lines: Sequence[tuple[float, dict]]) -> list[tuple[Word, float]]:
    """The words the `text` events carry (live events 1.1), each with the arrival of the event carrying its last
    piece (design/internal/g4-stage1.md §4): pieces joined at each `starts_word` (a first piece without it starts a
    word too), a word's text its pieces' text joined and stripped, its start its first piece's and its end its last
    piece's. The probability isn't sent, so it is 1.0. A 1.0 stream, with no `text` event, gives no word."""
    words: list[tuple[Word, float]] = []
    texts: list[str] = []
    start = end = arrival = 0.0
    for at, event in lines:
        if event.get("type") != "text":
            continue
        if texts and event["starts_word"]:
            words.append((Word(start, end, "".join(texts).strip(), 1.0), arrival))
            texts = []
        if not texts:
            start = event["start"]
        texts.append(event["text"])
        end, arrival = event["end"], at
    if texts:
        words.append((Word(start, end, "".join(texts).strip(), 1.0), arrival))
    return words


def run_once(command: list[str], out_dir: Path, stem: str) -> RunLog:
    """Run `command` (an `lmt live --jsonl` run writing `<out_dir>/<stem>.json`), stamping each event line with
    time.monotonic() as readline() returns it. Arrivals count from the start event's arrival; `wall_seconds`
    from process start to exit. A non-zero exit is an InputError with stderr's last line."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stderr_path = out_dir / ".lmt-live-stderr.txt"
    stamped = []
    with open(stderr_path, "wb") as stderr:
        began = time.monotonic()
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr)
        with proc.stdout:
            for line in iter(proc.stdout.readline, b""):
                arrived = time.monotonic()
                if line.strip():
                    stamped.append((arrived, line))
        code = proc.wait()
        wall_seconds = time.monotonic() - began
    if code != 0:
        lines = stderr_path.read_text(encoding="utf-8", errors="replace").splitlines()
        last = next((s for s in reversed(lines) if s.strip()), "")
        raise InputError("lmt live", f"exited {code}: {last}")
    events = []
    for n, (arrived, line) in enumerate(stamped, start=1):
        try:
            events.append((arrived, json.loads(line)))
        except ValueError:
            raise InputError("lmt live", f"line {n} of its output isn't JSON") from None
    origin = next((a for a, e in events if isinstance(e, dict) and e.get("type") == "start"), began)
    return RunLog(
        lines=[(arrived - origin, event) for arrived, event in events],
        wall_seconds=wall_seconds,
        transcript=transcript.to_dict(transcript.load(out_dir / f"{stem}.json")),
    )


def run_live(wav: Path, work_dir: Path, *, runs=3, command_prefix=None, engine="kyutai") -> list[RunLog]:
    """Ensure the models `lmt models download` fetches, so no run downloads, then run `lmt live` on `wav`
    `runs` times, one after another, with `engine`, each writing `run<i>.json` under `work_dir`.
    `command_prefix` replaces `python -m lmt`, as stage 2's tuning entry point does."""
    models.ensure(models.download_defaults(models.load_manifest()), offline=False)
    prefix = list(command_prefix) if command_prefix is not None else [sys.executable, "-m", "lmt"]
    logs = []
    for i in range(1, runs + 1):
        stem = f"run{i}"
        command = prefix + ["live", "--from-file", str(wav), "--jsonl", "--offline", "-f", "json", "-n", stem,
                            "-o", str(work_dir), "--overwrite", *runner.engine_options(engine)]
        logs.append(run_once(command, Path(work_dir), stem))
    return logs


def figure(item: score.Item, ref_words, logs: Sequence[RunLog]) -> dict:
    """The runs pooled, in public-api.md's key order: G1's figure for the runs as that many items, then the
    word delays (at the `text` events, `read_text`) and the label delays (at the `words` events, `read_events`)
    over every matched word of every run, `matched_words` (the word delay's), and each run's p95 delays and
    `behind_seconds` (design/internal/g4-stage1.md §4). A delay with no matched word is None."""
    refs = [RefWord(*w) for w in ref_words]
    hyps = []
    pooled = []
    pooled_label = []
    runs = []
    for log in logs:
        hyp = score.hypothesis_from_transcript(log.transcript)
        hyps.append(replace(hyp, wall_seconds=log.wall_seconds))
        labelled, behind = read_events(log.lines)
        run_delays = delays(match_words(refs, read_text(log.lines)))
        run_label = delays(match_words(refs, labelled))
        pooled += run_delays
        pooled_label += run_label
        runs.append({"p95_delay_seconds": _delay(run_delays, 0.95), "p95_label_delay_seconds": _delay(run_label, 0.95),
                     "behind_seconds": round(behind, 3)})
    return {
        **score._figure(item.dataset, "product-live", [item] * len(logs), hyps),
        "p50_delay_seconds": _delay(pooled, 0.5),
        "p95_delay_seconds": _delay(pooled, 0.95),
        "max_delay_seconds": _delay(pooled, 1.0),
        "p50_label_delay_seconds": _delay(pooled_label, 0.5),
        "p95_label_delay_seconds": _delay(pooled_label, 0.95),
        "max_label_delay_seconds": _delay(pooled_label, 1.0),
        "matched_words": len(pooled),
        "runs": runs,
    }


def cli_eval_live(args) -> int:
    """`lmt eval --set live`: AMI-ci's three runs with `--engine`, in a temporary directory under `--out` that's
    removed after, pooled into <out>/live.json (live-apple.json for Apple's engine) with the first run's models.
    With `--update-readme`, README.md's table is then rendered from every results file in `--out`. Prints the
    file's path, as `--set ci` does."""
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    item, ref_words = datasets.live_reference()
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".lmt-eval-live-") as work_dir:
        logs = run_live(item.audio, Path(work_dir), engine=args.engine)
    results = report.build("live", [figure(item, ref_words, logs)], logs[0].transcript["models"],
                           determinism=None, out_dir=out_dir, engine=args.engine)
    path = out_dir / report.file_name("live", args.engine)
    report.write(path, results)
    if args.update_readme:
        readme.update(Path("README.md"), out_dir)
    print(path)
    return 0


def _delay(values: Sequence[float], q: float) -> float | None:
    """The q-th nearest-rank percentile rounded to 3 decimal places (q = 1 is the max), or None for no values."""
    return round(percentile(values, q), 3) if values else None
