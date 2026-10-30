"""G3-6 and G3-8's full-install sequence (spec/acceptance.md), against an installed `lmt`.

Usage: uv run --frozen python .github/scripts/check_full_install.py --lmt <scratch>/bin/lmt
           --platform {linux-x86_64,macos-arm64} --work DIR [--criteria-out PATH]

Design: design/internal/g3-stage1.md §4b. Under DIR: LMT_CACHE_DIR=DIR/models and
LMT_DATA_DIR=DIR/data for every command, DIR/out for the transcripts, and each command's own fresh
HOME, TMPDIR, XDG_CACHE_HOME and working directory under DIR/runs/<step>/. This item (4b1) runs the
online phase; offline_command() builds the argv of the offline phase, which 4b2 runs. Each step
prints PASS or FAIL, and the steps go to the step summary. --criteria-out writes the job's record
(design/internal/criteria.md §3), after the last check. Exits 1 on any failure. The top level imports
the standard library only, so the `readme` job's unittest can import it with no venv.
"""

import argparse
import hashlib
import http.client
import importlib.util
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

PLATFORMS = ("linux-x86_64", "macos-arm64")
FRESH = ("home", "tmp", "cache", "cwd")  # HOME, TMPDIR, XDG_CACHE_HOME and the working directory
URL = re.compile(r"^http://127\.0\.0\.1:(\d+)/([A-Za-z0-9_-]{22})/$")  # as 2a4's DemoProcess parses it
FETCH_LIMIT = 10  # seconds, for each GET and for the exit after SIGTERM
FIRST_URL_LIMIT = 30 * 60  # the first --sample run downloads and transcribes
# lmt.eval.datasets: sample_meeting's WAV is derived/<dataset>/<meeting>.wav under LMT_DATA_DIR.
SAMPLE_WAV = "derived/AMI-ci/ES2004a.wav"
SAMPLE_TRANSCRIPT = "ES2004a.json"  # in LMT_DATA_DIR/sample/<key>/
# The offline phase's namespace (§4b): bring loopback up, then drop to the runner's user and exec.
NAMESPACE_SCRIPT = ('ip link set lo up && u=$1 g=$2 && shift 2 && '
                    'exec setpriv --reuid="$u" --regid="$g" --init-groups -- "$@"')
# Each recorded step's criteria (§4b, "Its record"). 1.2, the snapshot, checks nothing and isn't listed.
CRITERIA = {
    "1.1": ["G3-8"],
    "1.3": ["G3-6"],
}


class StepFailed(Exception):
    """A step's problem, as one line."""


# The directories.

def models_dir(work):
    return Path(work) / "models"


def data_dir(work):
    return Path(work) / "data"


def models_snapshot_path(work):
    return Path(work) / "snapshots" / "models.json"


def fresh_dirs(work, step):
    """The command's own HOME, TMPDIR, XDG_CACHE_HOME and working directory, made empty here."""
    dirs = {name: Path(work) / "runs" / step / name for name in FRESH}
    for d in dirs.values():
        d.mkdir(parents=True)  # FileExistsError if the step ran before: they must be fresh
    return dirs


def command_env(work, dirs, *, base=None, hf_token=False):
    """The environment of one `lmt` command: the job's own, with the directories above, and
    HF_TOKEN only when `hf_token` (models download alone)."""
    env = dict(os.environ if base is None else base)
    token = env.pop("HF_TOKEN", None)
    if hf_token and token is not None:
        env["HF_TOKEN"] = token
    env.update({
        "LMT_CACHE_DIR": str(models_dir(work)),
        "LMT_DATA_DIR": str(data_dir(work)),
        "HOME": str(dirs["home"]),
        "TMPDIR": str(dirs["tmp"]),
        "XDG_CACHE_HOME": str(dirs["cache"]),
    })
    return env


# The file snapshot: this module's own, so the offline phase needs nothing outside the standard library.

def snapshot(*dirs):
    """{path: ["file", size, sha256] | ["dir"] | ["link", target]} for everything under each directory.

    Lists rather than tuples, so a snapshot read back from JSON compares equal."""
    out = {}
    for root in dirs:
        for top, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                path = os.path.join(top, name)
                if os.path.islink(path):
                    out[path] = ["link", os.readlink(path)]
                elif os.path.isdir(path):
                    out[path] = ["dir"]
                else:
                    with open(path, "rb") as f:
                        digest = hashlib.file_digest(f, "sha256").hexdigest()
                    out[path] = ["file", os.path.getsize(path), digest]
    return out


def changes(before, after):
    """The paths added, removed or changed between two snapshots, sorted."""
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


def check_unchanged(what, before, after):
    changed = changes(before, after)
    if changed:
        more = f" and {len(changed) - 3} more" if len(changed) > 3 else ""
        raise StepFailed(f"{what} changed: {', '.join(changed[:3])}{more}")


def check_fresh_empty(dirs):
    for name, d in dirs.items():
        found = sorted(snapshot(d))
        if found:
            raise StepFailed(f"the run's fresh {name} directory isn't empty: {found[0]}")


# Running commands.

def run_command(argv, env, cwd, limit=None):
    """Run one command to its end, within `limit` seconds if given; fail on a missed limit or a
    nonzero exit."""
    try:
        code = subprocess.run(argv, env=env, cwd=cwd, timeout=limit, check=False).returncode
    except subprocess.TimeoutExpired:
        raise StepFailed(f"lmt {' '.join(map(str, argv[1:]))} didn't finish within the {limit} s limit") from None
    if code != 0:
        raise StepFailed(f"lmt {' '.join(map(str, argv[1:]))} exits {code}")


def _first_line(stream, lines):
    """Pass on the first line, then drain the rest, so the child never blocks on a full pipe."""
    lines.put(stream.readline())
    for _ in stream:
        pass


def fetch(port, path):
    """GET a path from the demo, within FETCH_LIMIT: (status, body)."""
    start = time.monotonic()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=FETCH_LIMIT)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read()
    except OSError as e:  # a socket timeout included
        raise StepFailed(f"GET {path}: {e or type(e).__name__}") from None
    finally:
        conn.close()
    if time.monotonic() - start > FETCH_LIMIT:
        raise StepFailed(f"GET {path} took more than the {FETCH_LIMIT} s limit")
    return response.status, body


def run_demo(argv, env, cwd, url_limit):
    """Start `lmt demo`, read its URL within `url_limit` seconds, GET the page and audio.wav, then
    SIGTERM it and expect exit 0 within FETCH_LIMIT. A missed limit kills it with SIGKILL."""
    proc = subprocess.Popen(argv, env=env, cwd=cwd, stdout=subprocess.PIPE, text=True)
    try:
        lines = queue.Queue()
        threading.Thread(target=_first_line, args=(proc.stdout, lines), daemon=True).start()
        try:
            line = lines.get(timeout=url_limit)
        except queue.Empty:
            raise StepFailed(f"no URL on stdout within the {url_limit} s limit") from None
        if not line:
            try:
                raise StepFailed(f"exits {proc.wait(FETCH_LIMIT)} without printing its URL")
            except subprocess.TimeoutExpired:
                raise StepFailed("closes stdout without printing its URL") from None
        m = URL.match(line.rstrip("\n"))
        if not m:
            raise StepFailed(f"its first stdout line isn't the demo's URL: {line.rstrip()!r}")
        base = f"/{m.group(2)}/"
        status, _ = fetch(int(m.group(1)), base)
        if status != 200:
            raise StepFailed(f"GET {base} answers {status}, not 200")
        status, body = fetch(int(m.group(1)), base + "audio.wav")
        if status != 200:
            raise StepFailed(f"GET {base}audio.wav answers {status}, not 200")
        if not body.startswith(b"RIFF"):
            raise StepFailed(f"GET {base}audio.wav doesn't start with RIFF: {body[:8]!r}")
        proc.send_signal(signal.SIGTERM)
        try:
            code = proc.wait(FETCH_LIMIT)
        except subprocess.TimeoutExpired:
            raise StepFailed(f"still running {FETCH_LIMIT} s after SIGTERM (the limit)") from None
        if code != 0:
            raise StepFailed(f"exits {code} after SIGTERM, not 0")
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
            proc.wait()
        proc.stdout.close()


# The online phase (§4b, step 1).

def step_models_download(args):
    dirs = fresh_dirs(args.work, "models-download")
    run_command([args.lmt, "models", "download"], command_env(args.work, dirs, hf_token=True), dirs["cwd"])


def step_snapshot_models(args):
    path = models_snapshot_path(args.work)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot(models_dir(args.work)), indent=1, sort_keys=True) + "\n")


def load_models_snapshot(work):
    """The reference for every <work>/models check after step 1.2, the offline phase's included."""
    return json.loads(models_snapshot_path(work).read_text())


def step_first_sample(args):
    dirs = fresh_dirs(args.work, "sample-online")
    run_demo([args.lmt, "demo", "--sample", "--no-browser"], command_env(args.work, dirs), dirs["cwd"],
             FIRST_URL_LIMIT)
    data = data_dir(args.work)
    if not (data / SAMPLE_WAV).is_file():
        raise StepFailed(f"LMT_DATA_DIR has no {SAMPLE_WAV}")
    transcripts = sorted(data.glob(f"sample/*/{SAMPLE_TRANSCRIPT}"))
    if len(transcripts) != 1:
        raise StepFailed(f"LMT_DATA_DIR has {len(transcripts)} sample/*/{SAMPLE_TRANSCRIPT}, not 1")
    check_fresh_empty(dirs)
    check_unchanged("<work>/models", load_models_snapshot(args.work), snapshot(models_dir(args.work)))


ONLINE = [
    ("1.1", "models download", step_models_download),
    ("1.2", "snapshot <work>/models", step_snapshot_models),
    ("1.3", "the first --sample run", step_first_sample),
]


def run_steps(steps, args):
    """[{step, name, problem}] in order, problem None for a pass. After a failure the rest aren't
    reached, and fail."""
    results, failed = [], None
    for step, name, run in steps:
        problem = f"not reached: step {failed} failed" if failed else None
        if failed is None:
            try:
                run(args)
            except StepFailed as e:
                problem = str(e)
            except OSError as e:
                problem = f"{type(e).__name__}: {e}"
            if problem:
                failed = step
        results.append({"step": step, "name": name, "problem": problem})
        print(report(results[-1:]), flush=True)
    return results


# The offline phase's command (§4b, step 2), which 4b2 runs.

def offline_command(lmt, platform, work, *, path=None, uid=None, gid=None):
    """The argv that runs this script's `offline` phase: in its own network namespace as the runner's
    user on Linux, and with only `env -i` on macOS. No path passes through a shell string."""
    path = os.environ.get("PATH", "") if path is None else path
    phase = ["env", "-i", f"HOME={Path(work) / 'runs' / 'offline' / 'home'}", f"PATH={path}",
             "PYTHONDONTWRITEBYTECODE=1",
             sys.executable, "-I", str(Path(__file__).resolve()), "offline",
             "--lmt", str(lmt), "--platform", platform, "--work", str(work)]
    if platform == "macos-arm64":
        return phase
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
    return ["sudo", "-n", "unshare", "--net", "--", "sh", "-c", NAMESPACE_SCRIPT, "sh", str(uid), str(gid),
            *phase]


# The report and the record.

def report(results):
    return "\n".join(f"{'FAIL' if r['problem'] else 'PASS'} {r['step']} {r['name']}"
                     + (f"\n  - {r['problem']}" if r["problem"] else "") for r in results)


def checks(platform, results):
    """One check per recorded step (criteria.md §3)."""
    return [{"check": f"check_full_install.py::{platform} {r['step']} {r['name']}",
             "criteria": CRITERIA[r["step"]],
             "outcome": "failed" if r["problem"] else "passed"}
            for r in results if r["step"] in CRITERIA]


def criteria_module():
    """criteria.py, loaded by path (`-I` keeps this directory off sys.path): its record writer."""
    spec = importlib.util.spec_from_file_location("lmt_ci_criteria", Path(__file__).with_name("criteria.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lmt", required=True, type=Path, help="the installed lmt")
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--criteria-out", metavar="PATH",
                        help="write each step's outcome to PATH (design/internal/criteria.md §3)")
    args = parser.parse_args(argv)
    args.lmt, args.work = args.lmt.resolve(), args.work.resolve()
    for d in (models_dir(args.work), data_dir(args.work), args.work / "out"):
        d.mkdir(parents=True, exist_ok=True)

    results = run_steps(ONLINE, args)
    text = report(results)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"### Full install ({args.platform})\n\n```\n{text}\n```\n")
    if args.criteria_out:
        criteria_module().write_record(args.criteria_out, checks(args.platform, results), complete=True)
    return 1 if any(r["problem"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
