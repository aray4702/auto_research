"""The Mac's figures, read from a committed results file against the spec's bounds.

Usage: python3 -I .github/scripts/check_results.py [--results results/full.json] [--criteria-out PATH]

Design: design/internal/criteria.md §2 and §3. Reads one of `lmt eval`'s results files (public-api.md,
"Results file, version 2.0") and checks it against spec/acceptance.md, by its set and engine:
- `results/full.json` (Kyutai): G1-8, G1-9, G1-10's memory, G1-12's Mac half and G1-14's figures;
- `results/live.json` (Kyutai): G2-6, the p95 word delay < 1.0 s and the p95 label delay ≤ 3.0 s;
- `results/full-apple.json`: G4-3 (the reference clip in both modes), G4-4, G4-5, G4-6 and G1-14's figures;
- `results/live-apple.json`: G4-6 and G4-8, whose figures have no bound.
The Whisper-era `results/full.json` (version 1.0, `whisper-alone`) is read as a full file for G1, until
G4's stage 2 replaces it. A missed bound is a `failed` check and the script still exits 0: CI never fails on
the Mac's figures (G1, "Where it runs"). While the file holds no figures (the placeholder, g1-stage1.md §6),
every check is `skipped`. Exits 3 with one `check_results.py: error:` line, and writes no record, when the
file is missing or malformed. Standard library only.
"""

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
EXIT_ERROR = 3
VERSIONS = {"1.0": "whisper-alone", "2.0": "asr-alone"}  # each version's name for the engine alone
ENGINES = ("kyutai", "apple")
FIELDS = ("wer", "der", "audio_seconds", "wall_seconds", "rtf", "peak_rss_bytes")

# spec/acceptance.md's bounds, each inclusive unless it says otherwise.
WER_LS = 0.050         # G1-8, G4-4: LS full WER ≤ 5.0 %
WER_AMI = 0.250        # G1-8, G4-4: AMI full WER ≤ 25.0 %
DER_AMI = 0.250        # G1-8, G4-4: AMI full DER ≤ 25.0 %
WER_CLIP = 0.050       # G4-3: the reference clip's WER ≤ 5.0 %, in batch and in live mode
RTF = 1.0              # G1-9: RTF ≤ 1.0 over AMI full, and on the one-hour recording. The Owner relaxed
                       # Q2 for v0.1 on #123 (2026-10-30); 0.50 stays v1.0's target.
PEAK_RSS = 4.0e9       # G1-10, G4-5: the one-hour product run's peak resident memory ≤ 4.0 × 10⁹ bytes
P95_WORD = 1.0         # G2-6: the pooled p95 word delay < 1.0 s, strictly (#565)
P95_LABEL = 3.0        # G2-6: the pooled p95 label delay ≤ 3.0 s
LIVE_RUNS = 3          # G2-6, G4-8: three runs played back in real time
LIVE_FIELDS = ("p50_delay_seconds", "p95_delay_seconds", "max_delay_seconds", "p50_label_delay_seconds",
               "p95_label_delay_seconds", "max_label_delay_seconds", "matched_words")
LIVE_RUN_FIELDS = ("p95_delay_seconds", "p95_label_delay_seconds", "behind_seconds")
CLIP = "reference clip"  # G4-3's dataset in full-apple.json: `product` is batch mode, `product-live` live mode
# Apple's version record (spec, G4 "Terms"; public-api.md, transcript JSON 1.2): `machine.os` is
# "macOS <version> (<build>)", and `models.asr` names the recognizer, with the version, build and locale.
APPLE_OS = re.compile(r"macOS \d+(\.\d+)* \(\w+\)")
APPLE_ASR = {"engine": "apple", "name": "SpeechTranscriber", "repository": "com.apple.Speech"}

RUN_FIELDS = ("commit", "date", "machine", "models")


def table_rows(alone):
    """G1-14's table: WER, DER and RTF per set for the product, the engine alone and pyannote alone. LS full
    runs without diarization, so it has no pyannote-alone row; each row's figures that apply."""
    return {
        ("LS full", "product"): ("wer", "rtf"),
        ("LS full", alone): ("wer", "rtf"),
        ("AMI full", "product"): ("wer", "der", "rtf"),
        ("AMI full", alone): ("wer", "rtf"),
        ("AMI full", "pyannote-alone"): ("der", "rtf"),
        ("one-hour", "product"): ("wer", "der", "rtf"),
        ("one-hour", alone): ("wer", "rtf"),
        ("one-hour", "pyannote-alone"): ("der", "rtf"),
    }


class Malformed(Exception):
    """The results file isn't a results file this script reads: exit 3."""


class Missed(Exception):
    """A check's figure is missing or misses its bound: the check fails."""


def number_or_null(path, where, obj, keys):
    for k in keys:
        v = obj.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            raise Malformed(f"{path}: {where}: `{k}` must be a number or null")


def load(path):
    """(data, rows, kind): rows keyed by (dataset, system); kind is (set, engine), engine None for 1.0."""
    try:
        data = json.loads(Path(path).read_text())
    except OSError as e:
        raise Malformed(f"{path}: {e.strerror}") from None
    except json.JSONDecodeError as e:
        raise Malformed(f"{path}: not JSON: {e}") from None
    if not isinstance(data, dict) or data.get("schema_version") not in VERSIONS:
        raise Malformed(f"{path}: not a version 2.0 (or the Whisper-era 1.0) results file")
    version = data["schema_version"]
    engine = data.get("engine") if version == "2.0" else None
    if version == "2.0" and engine not in ENGINES:
        raise Malformed(f"{path}: engine is {engine!r}, not one of {', '.join(ENGINES)}")
    sets = ("full", "live") if version == "2.0" else ("full",)
    if data.get("set") not in sets:
        raise Malformed(f"{path}: set is {data.get('set')!r}, not {' or '.join(repr(s) for s in sets)}")
    figures = data.get("figures")
    if not isinstance(figures, list):
        raise Malformed(f"{path}: `figures` must be a list")
    systems = ("product-live",) if data["set"] == "live" else ("product", VERSIONS[version], "pyannote-alone")
    if engine == "apple" and data["set"] == "full":
        systems += ("product-live",)  # G4-3's live run of the reference clip
    rows = {}
    for f in figures:
        if not (isinstance(f, dict) and isinstance(f.get("dataset"), str) and f.get("system") in systems):
            raise Malformed(f"{path}: a figure needs `dataset` and a `system` of {', '.join(systems)}")
        where = f"{f['dataset']} {f['system']}"
        number_or_null(path, where, f, FIELDS + (LIVE_FIELDS if f["system"] == "product-live" else ()))
        runs = f.get("runs")
        if runs is not None:
            if not isinstance(runs, list) or not all(isinstance(r, dict) for r in runs):
                raise Malformed(f"{path}: {where}: `runs` must be a list of objects or null")
            for i, r in enumerate(runs, 1):
                number_or_null(path, f"{where} run {i}", r, LIVE_RUN_FIELDS)
        key = (f["dataset"], f["system"])
        if key in rows:
            raise Malformed(f"{path}: {key[0]} {key[1]} appears twice")
        rows[key] = f
    if data["set"] == "live" and len(rows) > 1:
        raise Malformed(f"{path}: a live file's `figures` holds one figure")
    determinism = data.get("determinism")
    if determinism is not None and not isinstance(determinism, dict):
        raise Malformed(f"{path}: `determinism` must be an object or null")
    return data, rows, (data["set"], engine)


def figure(rows, dataset, system, field):
    value = rows.get((dataset, system), {}).get(field)
    if value is None:
        raise Missed(f"no {field} for {dataset} {system}")
    return value


def at_most(rows, dataset, field, bound, unit="{:.3f}", system="product"):
    value = figure(rows, dataset, system, field)
    label = dataset if system == "product" else f"{dataset} {system}"
    shown = f"{label} {field} {unit.format(value)}"
    if value > bound:
        raise Missed(f"{shown} > {unit.format(bound)}")
    return f"{shown} ≤ {unit.format(bound)}"


def baselines(rows, alone):
    for dataset, system, field in (("LS full", alone, "wer"), ("AMI full", alone, "wer"),
                                   ("AMI full", "pyannote-alone", "der")):
        figure(rows, dataset, system, field)
    return "the engine alone's WER on both sets, and pyannote alone's DER on AMI full"


def determinism(data):
    d = data.get("determinism") or {}
    if d.get("runs") != 2 or d.get("identical") is not True:
        raise Missed(f"determinism is {data.get('determinism')!r}, not two identical runs")
    return "two runs of lmt eval --set ci, identical"


def published(data):
    """The run's fields the table shows, from a clean tree: Missed names what's lacking."""
    missing = [k for k in RUN_FIELDS if not data.get(k)]
    if missing:
        raise Missed("missing: " + ", ".join(missing))
    if data.get("dirty") is not False:
        raise Missed("made from a dirty working tree")


def table(data, rows, alone):
    expected = table_rows(alone)
    missing = [f"{dataset} {system} {field}" for (dataset, system), fields in expected.items()
               for field in fields if rows.get((dataset, system), {}).get(field) is None]
    if missing:
        raise Missed("missing: " + ", ".join(missing))
    published(data)
    return f"{len(expected)} rows, from commit {str(data['commit'])[:12]}"


def version_record(data):
    """G4-6: Apple's version record in `machine.os` and `models.asr`, and no determinism."""
    os_name = (data.get("machine") or {}).get("os")
    asr = (data.get("models") or {}).get("asr") or {}
    if not (isinstance(os_name, str) and APPLE_OS.fullmatch(os_name)):
        raise Missed(f"machine.os is {os_name!r}, not the macOS version and build, \"macOS <version> (<build>)\"")
    wrong = [f"{k} {asr.get(k)!r}" for k, v in APPLE_ASR.items() if asr.get(k) != v]
    if wrong:
        raise Missed("models.asr isn't Apple's version record: " + ", ".join(wrong))
    if not (isinstance(asr.get("revision"), str) and asr["revision"].startswith("macOS ")):
        raise Missed(f"models.asr.revision is {asr.get('revision')!r}, not the macOS version, build and locale")
    if data.get("determinism") is not None:
        raise Missed(f"determinism is {data['determinism']!r}, not null (Apple's engine is exempt, Q7)")
    return f"{os_name}, {asr['revision']}, determinism null"


def live_figure(data, rows):
    """The live figure, its three runs and every field G2-6 lists, from a clean tree: Missed if not."""
    (f,) = rows.values()
    runs = f.get("runs") or []
    if len(runs) != LIVE_RUNS:
        raise Missed(f"{len(runs)} runs, not {LIVE_RUNS}")
    missing = [k for k in LIVE_FIELDS + ("wer", "der") if f.get(k) is None]
    missing += [f"run {i} {k}" for i, r in enumerate(runs, 1) for k in LIVE_RUN_FIELDS if r.get(k) is None]
    if missing:
        raise Missed("missing: " + ", ".join(missing))
    published(data)
    return f


def word_delay(data, rows):
    f = live_figure(data, rows)
    shown = f"pooled p95 word delay {f['p95_delay_seconds']:.2f} s over {LIVE_RUNS} runs"
    if not f["p95_delay_seconds"] < P95_WORD:
        raise Missed(f"{shown}, not < {P95_WORD:.2f} s")
    return f"{shown} < {P95_WORD:.2f} s"


def label_delay(data, rows):
    f = live_figure(data, rows)
    shown = f"pooled p95 label delay {f['p95_label_delay_seconds']:.2f} s over {LIVE_RUNS} runs"
    if f["p95_label_delay_seconds"] > P95_LABEL:
        raise Missed(f"{shown} > {P95_LABEL:.2f} s")
    return f"{shown} ≤ {P95_LABEL:.2f} s"


def apple_live(data, rows):
    f = live_figure(data, rows)
    return (f"p95 word delay {f['p95_delay_seconds']:.2f} s, p95 label delay {f['p95_label_delay_seconds']:.2f} s, "
            f"over {LIVE_RUNS} runs (no bound)")


# (name, criteria, check) per (set, engine): each check returns what it read, or raises Missed.
def full_checks(alone):
    """Kyutai's full file, or the Whisper-era one: `alone` is the version's name for the engine alone."""
    return [
        ("G1-8 LS full WER", ["G1-8"], lambda d, r: at_most(r, "LS full", "wer", WER_LS)),
        ("G1-8 AMI full WER", ["G1-8"], lambda d, r: at_most(r, "AMI full", "wer", WER_AMI)),
        ("G1-8 AMI full DER", ["G1-8"], lambda d, r: at_most(r, "AMI full", "der", DER_AMI)),
        ("G1-8 the engine alone and pyannote alone", ["G1-8"], lambda d, r: baselines(r, alone)),
        ("G1-9 AMI full RTF", ["G1-9"], lambda d, r: at_most(r, "AMI full", "rtf", RTF)),
        ("G1-9 one-hour RTF", ["G1-9"], lambda d, r: at_most(r, "one-hour", "rtf", RTF)),
        ("G1-10 one-hour peak RSS", ["G1-10"],
         lambda d, r: at_most(r, "one-hour", "peak_rss_bytes", PEAK_RSS, "{:.3e}")),
        ("G1-12 two runs identical", ["G1-12"], lambda d, r: determinism(d)),
        ("G1-14 the table's figures", ["G1-14"], lambda d, r: table(d, r, alone)),
    ]


FULL_APPLE = [
    ("G4-3 reference clip WER, batch", ["G4-3"], lambda d, r: at_most(r, CLIP, "wer", WER_CLIP)),
    ("G4-3 reference clip WER, live", ["G4-3"],
     lambda d, r: at_most(r, CLIP, "wer", WER_CLIP, system="product-live")),
    ("G4-4 LS full WER", ["G4-4"], lambda d, r: at_most(r, "LS full", "wer", WER_LS)),
    ("G4-4 AMI full WER", ["G4-4"], lambda d, r: at_most(r, "AMI full", "wer", WER_AMI)),
    ("G4-4 AMI full DER", ["G4-4"], lambda d, r: at_most(r, "AMI full", "der", DER_AMI)),
    ("G4-4 the engine alone and pyannote alone", ["G4-4"], lambda d, r: baselines(r, "asr-alone")),
    ("G4-5 one-hour peak RSS, lmt and the helper", ["G4-5"],
     lambda d, r: at_most(r, "one-hour", "peak_rss_bytes", PEAK_RSS, "{:.3e}")),
    ("G4-6 Apple's version record", ["G4-6"], lambda d, r: version_record(d)),
    ("G1-14 the table's figures", ["G1-14"], lambda d, r: table(d, r, "asr-alone")),
]
CHECKS = {  # keyed by (set, engine), engine None for the Whisper-era 1.0 file
    ("full", None): full_checks("whisper-alone"),
    ("full", "kyutai"): full_checks("asr-alone"),
    ("full", "apple"): FULL_APPLE,
    ("live", "kyutai"): [
        ("G2-6 live p95 word delay", ["G2-6"], word_delay),
        ("G2-6 live p95 label delay", ["G2-6"], label_delay),
    ],
    ("live", "apple"): [
        ("G4-8 Apple's live delays", ["G4-8"], apple_live),
        ("G4-6 Apple's version record", ["G4-6"], lambda d, r: version_record(d)),
    ],
}


def run(path):
    """[(check, report line)] for each of the file's checks, in order."""
    data, rows, kind = load(path)
    out = []
    for name, ids, fn in CHECKS[kind]:
        check = {"check": f"check_results.py::{name}", "criteria": ids}
        if not rows:
            reason = f"{path} has no figures yet (the Owner's run of lmt eval --set {kind[0]})"
            out.append((dict(check, outcome="skipped", reason=reason), f"SKIP {name}: {reason}"))
            continue
        try:
            out.append((dict(check, outcome="passed"), f"PASS {name}: {fn(data, rows)}"))
        except Missed as e:
            out.append((dict(check, outcome="failed"), f"FAIL {name}: {e}"))
    return out


def criteria_module():
    """criteria.py, loaded by path (`-I` keeps this directory off sys.path): its record writer."""
    spec = importlib.util.spec_from_file_location("lmt_ci_criteria", SCRIPTS / "criteria.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", default="results/full.json")
    parser.add_argument("--criteria-out", metavar="PATH",
                        help="write each check's outcome to PATH (design/internal/criteria.md §3)")
    args = parser.parse_args(argv)
    try:
        results = run(args.results)
    except Malformed as e:
        print(f"check_results.py: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    lines = [line for _, line in results]
    failed = sum(c["outcome"] == "failed" for c, _ in results)
    lines.append(f"{len(results)} checked, {failed} missed (recorded; CI doesn't fail on the Mac's figures)")
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"### The Mac's figures ({args.results})\n\n```\n{report}\n```\n")
    if args.criteria_out:
        criteria_module().write_record(args.criteria_out, [c for c, _ in results], complete=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
