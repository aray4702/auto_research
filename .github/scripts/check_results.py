"""The Mac's figures, read from the committed results file against the spec's bounds.

Usage: python3 -I .github/scripts/check_results.py [--results results/full.json] [--criteria-out PATH]

Design: design/internal/criteria.md §2 and §3. Reads `lmt eval --set full`'s results file and checks
G1-8, G1-9, G1-10's memory, G1-12's Mac half and G1-14's figures against spec/acceptance.md. A missed
bound is a `failed` check and the script still exits 0: CI never fails on the Mac's figures (G1, "Where
it runs"). While the file holds no figures (the placeholder, g1-stage1.md §6), every check is `skipped`.
Exits 3 with one `check_results.py: error:` line, and writes no record, when the file is missing or
malformed. Standard library only.
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
EXIT_ERROR = 3
SYSTEMS = ("product", "whisper-alone", "pyannote-alone")
FIELDS = ("wer", "der", "audio_seconds", "wall_seconds", "rtf", "peak_rss_bytes")

# spec/acceptance.md's bounds, each inclusive.
WER_LS = 0.050         # G1-8: LS full WER ≤ 5.0 %
WER_AMI = 0.250        # G1-8: AMI full WER ≤ 25.0 %
DER_AMI = 0.250        # G1-8: AMI full DER ≤ 25.0 %
RTF = 1.0              # G1-9: RTF ≤ 1.0 over AMI full, and on the one-hour recording. The Owner relaxed
                       # Q2 for v0.1 on #123 (2026-10-30); 0.50 stays v1.0's target.
PEAK_RSS = 4.0e9       # G1-10: the one-hour product run's peak resident memory ≤ 4.0 × 10⁹ bytes

# G1-14's table: WER, DER and RTF per set for the product, Whisper alone and pyannote alone. LS full runs
# without diarization, so it has no pyannote-alone row; each row's figures that apply.
TABLE = {
    ("LS full", "product"): ("wer", "rtf"),
    ("LS full", "whisper-alone"): ("wer", "rtf"),
    ("AMI full", "product"): ("wer", "der", "rtf"),
    ("AMI full", "whisper-alone"): ("wer", "rtf"),
    ("AMI full", "pyannote-alone"): ("der", "rtf"),
    ("one-hour", "product"): ("wer", "der", "rtf"),
    ("one-hour", "whisper-alone"): ("wer", "rtf"),
    ("one-hour", "pyannote-alone"): ("der", "rtf"),
}
RUN_FIELDS = ("commit", "date", "machine", "models")


class Malformed(Exception):
    """The results file isn't a version 1.0 results file: exit 3."""


class Missed(Exception):
    """A check's figure is missing or misses its bound: the check fails."""


def load(path):
    try:
        data = json.loads(Path(path).read_text())
    except OSError as e:
        raise Malformed(f"{path}: {e.strerror}") from None
    except json.JSONDecodeError as e:
        raise Malformed(f"{path}: not JSON: {e}") from None
    if not isinstance(data, dict) or data.get("schema_version") != "1.0":
        raise Malformed(f"{path}: not a version 1.0 results file")
    if data.get("set") != "full":
        raise Malformed(f"{path}: set is {data.get('set')!r}, not 'full'")
    figures = data.get("figures")
    if not isinstance(figures, list):
        raise Malformed(f"{path}: `figures` must be a list")
    rows = {}
    for f in figures:
        if not (isinstance(f, dict) and isinstance(f.get("dataset"), str) and f.get("system") in SYSTEMS):
            raise Malformed(f"{path}: a figure needs `dataset` and a `system` of {', '.join(SYSTEMS)}")
        for k in FIELDS:
            v = f.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                raise Malformed(f"{path}: {f['dataset']} {f['system']}: `{k}` must be a number or null")
        key = (f["dataset"], f["system"])
        if key in rows:
            raise Malformed(f"{path}: {key[0]} {key[1]} appears twice")
        rows[key] = f
    determinism = data.get("determinism")
    if determinism is not None and not isinstance(determinism, dict):
        raise Malformed(f"{path}: `determinism` must be an object or null")
    return data, rows


def figure(rows, dataset, system, field):
    value = rows.get((dataset, system), {}).get(field)
    if value is None:
        raise Missed(f"no {field} for {dataset} {system}")
    return value


def at_most(rows, dataset, field, bound, unit="{:.3f}"):
    value = figure(rows, dataset, "product", field)
    shown = f"{dataset} {field} {unit.format(value)}"
    if value > bound:
        raise Missed(f"{shown} > {unit.format(bound)}")
    return f"{shown} ≤ {unit.format(bound)}"


def baselines(rows):
    for dataset, system, field in (("LS full", "whisper-alone", "wer"), ("AMI full", "whisper-alone", "wer"),
                                   ("AMI full", "pyannote-alone", "der")):
        figure(rows, dataset, system, field)
    return "Whisper alone's WER on both sets, and pyannote alone's DER on AMI full"


def determinism(data):
    d = data.get("determinism") or {}
    if d.get("runs") != 2 or d.get("identical") is not True:
        raise Missed(f"determinism is {data.get('determinism')!r}, not two identical runs")
    return "two runs of lmt eval --set ci, identical"


def table(data, rows):
    missing = [f"{dataset} {system} {field}" for (dataset, system), fields in TABLE.items()
               for field in fields if rows.get((dataset, system), {}).get(field) is None]
    missing += [k for k in RUN_FIELDS if not data.get(k)]
    if missing:
        raise Missed("missing: " + ", ".join(missing))
    if data.get("dirty") is not False:
        raise Missed("made from a dirty working tree")
    return f"{len(TABLE)} rows, from commit {str(data['commit'])[:12]}"


# (name, criteria, check): each check returns what it read, or raises Missed.
CHECKS = [
    ("G1-8 LS full WER", ["G1-8"], lambda d, r: at_most(r, "LS full", "wer", WER_LS)),
    ("G1-8 AMI full WER", ["G1-8"], lambda d, r: at_most(r, "AMI full", "wer", WER_AMI)),
    ("G1-8 AMI full DER", ["G1-8"], lambda d, r: at_most(r, "AMI full", "der", DER_AMI)),
    ("G1-8 Whisper alone and pyannote alone", ["G1-8"], lambda d, r: baselines(r)),
    ("G1-9 AMI full RTF", ["G1-9"], lambda d, r: at_most(r, "AMI full", "rtf", RTF)),
    ("G1-9 one-hour RTF", ["G1-9"], lambda d, r: at_most(r, "one-hour", "rtf", RTF)),
    ("G1-10 one-hour peak RSS", ["G1-10"],
     lambda d, r: at_most(r, "one-hour", "peak_rss_bytes", PEAK_RSS, "{:.3e}")),
    ("G1-12 two runs identical", ["G1-12"], lambda d, r: determinism(d)),
    ("G1-14 the table's figures", ["G1-14"], table),
]


def run(path):
    """[(check, report line)] for each of CHECKS, in order."""
    data, rows = load(path)
    out = []
    for name, ids, fn in CHECKS:
        check = {"check": f"check_results.py::{name}", "criteria": ids}
        if not rows:
            reason = f"{path} has no figures yet (the Owner's run of lmt eval --set full)"
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
