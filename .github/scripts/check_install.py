"""G3-7's one-command install (spec/acceptance.md): the install into a scratch directory, and its checks.

Usage: python3 -I .github/scripts/check_install.py --platform {linux-x86_64,macos-arm64} --scratch DIR
           (--readme ROOT [--tag-checkout] | --wheel PATH) [--criteria-out PATH]

Design: design/internal/g3-stage1.md §4a. --readme installs with README.md's Install command, pointed at
ROOT's own git checkout (--tag-checkout first tags its HEAD with the command's tag); --wheel installs a
built wheel, for release.yml. Each step prints PASS or FAIL, and the steps go to the step summary.
--criteria-out writes the job's record (design/internal/criteria.md §3), after the last step. Exits 1 on
any failure. Standard library only, so any Python 3.11 runs it.
"""

import argparse
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

PLATFORMS = ("linux-x86_64", "macos-arm64")
# The lock's marker fields for each platform; python_full_version comes from the tool's own Python.
ENVIRONMENTS = {
    "linux-x86_64": {"sys_platform": "linux", "platform_machine": "x86_64"},
    "macos-arm64": {"sys_platform": "darwin", "platform_machine": "arm64"},
}
SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent.parent  # the source of pyproject.toml, uv.lock and toolchain.toml for --wheel
CHECK_README = SCRIPTS / "check_readme.py"
LICENCES = SCRIPTS / "check_licences.py"
GITHUB_URL = "git+https://github.com/aray4702/auto_research@"
TOOL = "local-meeting-transcriber"  # the tool's environment, <scratch>/tools/<TOOL>/
# 4a1's pins, by their names in uv.lock.
PINNED = ("faster-whisper", "ctranslate2", "pyannote-audio", "torch", "torchaudio", "av", "numpy",
          "huggingface-hub")
# The eval extra's own distribution. Its pyannote.metrics also comes with pyannote.audio 3.4.0, so it
# isn't checked (the Owner's answer on #415).
EVAL_EXTRA = ("jiwer",)
MODULES = ("lmt.cli", "lmt.rename", "lmt.demo.server", "lmt.demo.sample")
INDEX_HTML = "demo/static/index.html"  # in the installed lmt package
CRITERIA = ["G3-7"]
MARKER_CLAUSE = re.compile(r"^(python_full_version|sys_platform|platform_machine) (==|!=|<=|>=|<|>) '([^']*)'$")

# Run by the tool's Python: its version, its site-packages and each named distribution's version.
PROBE = """\
import importlib.metadata, json, platform, sys, sysconfig
def version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
print(json.dumps({"python": platform.python_version(), "site": sysconfig.get_paths()["purelib"],
                  "versions": {name: version(name) for name in sys.argv[1:]}}))
"""
# Run by the tool's Python: the modules load, and the page is in the package.
LOAD = f"""\
import importlib, importlib.resources, sys
for name in {MODULES!r}:
    importlib.import_module(name)
if not importlib.resources.files("lmt").joinpath({INDEX_HTML!r}).is_file():
    sys.exit("lmt/{INDEX_HTML} isn't in the installed package")
"""


class StepFailed(Exception):
    """A step's problem, as one line, with the step's report lines if it has any."""

    def __init__(self, problem, lines=()):
        super().__init__(problem)
        self.lines = list(lines)


def one_line(text):
    return "; ".join(line.strip() for line in text.strip().splitlines())


# Step 1's command.

def local_command(line, root):
    """README's command with its GitHub URL pointed at ROOT's checkout, and the tag after `@`."""
    if line.count(GITHUB_URL) != 1:
        raise StepFailed(f"the command {line!r} doesn't name {GITHUB_URL} once")
    tag = line.rsplit("@", 1)[1]
    return line.replace(GITHUB_URL, f"git+file://{Path(root).resolve()}@"), tag


def readme_command(root):
    r = subprocess.run([sys.executable, "-I", str(CHECK_README), "--root", str(root), "--print-install"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise StepFailed(f"check_readme.py --print-install exits {r.returncode}: {one_line(r.stderr)}")
    return r.stdout.strip()


def tag_checkout(root, tag):
    r = subprocess.run(["git", "-C", str(root), "tag", "-f", tag, "HEAD"], capture_output=True, text=True,
                       check=False)
    if r.returncode != 0:
        raise StepFailed(f"git tag -f {tag} HEAD exits {r.returncode}: {one_line(r.stderr)}")


# Step 4's lock lookup.

def strip_local(version):
    """A version without its local label: 2.8.0+cpu is 2.8.0."""
    return version.split("+", 1)[0]


def version_tuple(text):
    if not re.fullmatch(r"\d+(\.\d+)*", text):
        raise StepFailed(f"can't compare the version {text!r} as integers")
    return tuple(int(part) for part in text.split("."))


def compare(op, left, right):
    return {"==": left == right, "!=": left != right, "<": left < right, "<=": left <= right,
            ">": left > right, ">=": left >= right}[op]


def marker_holds(marker, env):
    """Whether an `and`-joined marker of the three fields holds in env; any other clause fails."""
    holds = True
    for clause in marker.split(" and "):
        m = MARKER_CLAUSE.match(clause.strip())
        if not m:
            raise StepFailed(f"can't read the lock's marker clause {clause.strip()!r}")
        field, op, value = m.groups()
        if field == "python_full_version":
            left, right = version_tuple(env[field]), version_tuple(value)
            width = max(len(left), len(right))
            left, right = left + (0,) * (width - len(left)), right + (0,) * (width - len(right))
        elif op in ("==", "!="):
            left, right = env[field], value
        else:
            raise StepFailed(f"can't compare {field} with {op} in the lock's marker clause {clause.strip()!r}")
        holds = compare(op, left, right) and holds  # every clause is read, so none passes unread
    return holds


def lock_version(lock, name, platform, python):
    """The version of the one uv.lock entry for `name` that applies to the platform and Python."""
    env = ENVIRONMENTS[platform] | {"python_full_version": python}
    applicable = []
    for entry in lock.get("package", []):
        if entry.get("name") != name:
            continue
        markers = entry.get("resolution-markers")
        holds = [marker_holds(m, env) for m in markers or []]  # all of them, so none passes unread
        if markers is None or True in holds:
            applicable.append(entry["version"])
    if len(applicable) != 1:
        raise StepFailed(f"uv.lock has {len(applicable)} entries of {name} for {platform} with Python {python}, not 1")
    return applicable[0]


# The steps. Each takes the run's state, and may return lines for the report.

class Run:
    def __init__(self, args):
        self.args = args
        self.scratch = args.scratch
        self.root = args.readme if args.readme else REPO
        self.argv = None
        self._probe = None

    @property
    def lmt(self):
        return self.scratch / "bin" / "lmt"

    @property
    def python(self):
        return self.scratch / "tools" / TOOL / "bin" / "python"

    def install_env(self):
        return dict(os.environ) | {
            "UV_TOOL_DIR": str(self.scratch / "tools"),
            "UV_TOOL_BIN_DIR": str(self.scratch / "bin"),
            "UV_CACHE_DIR": str(self.scratch / "cache"),
            "UV_PYTHON_INSTALL_DIR": str(self.scratch / "python"),
        }

    def tool_python(self, code, *args):
        return subprocess.run([str(self.python), "-I", "-c", code, *args], cwd=self.scratch,
                              capture_output=True, text=True, check=False)

    def probe(self):
        """The tool's Python version, site-packages and distributions' versions, read once."""
        if self._probe is None:
            r = self.tool_python(PROBE, *PINNED, *EVAL_EXTRA)
            if r.returncode != 0:
                raise StepFailed(f"the tool's Python exits {r.returncode}: {one_line(r.stderr)[-500:]}")
            try:
                self._probe = json.loads(r.stdout)
            except json.JSONDecodeError:
                raise StepFailed(f"the tool's Python prints no probe: {r.stdout.strip()[-200:]!r}") from None
        return self._probe


def step_command(run):
    args = run.args
    if args.wheel:
        run.argv = ["uv", "tool", "install", str(args.wheel)]
    else:
        command, tag = local_command(readme_command(args.readme), args.readme)
        if args.tag_checkout:
            tag_checkout(args.readme, tag)
        run.argv = shlex.split(command)
    return [" ".join(run.argv)]


def step_install(run):
    run.scratch.mkdir(parents=True, exist_ok=True)
    code = subprocess.run(run.argv, env=run.install_env(), cwd=run.scratch, check=False).returncode
    if code != 0:
        raise StepFailed(f"{' '.join(run.argv)} exits {code}")


def step_version(run):
    with open(run.root / "pyproject.toml", "rb") as f:
        expected = f"lmt {tomllib.load(f)['project']['version']}"
    r = subprocess.run([str(run.lmt), "--version"], cwd=run.scratch, capture_output=True, text=True,
                       check=False)
    if r.returncode != 0 or r.stdout.strip() != expected:
        raise StepFailed(f"lmt --version exits {r.returncode} and prints {r.stdout.strip()!r}, not {expected!r}")


def step_pins(run):
    probe = run.probe()
    with open(run.root / "uv.lock", "rb") as f:
        lock = tomllib.load(f)
    lines, problems = [f"Python {probe['python']}"], []
    for name in PINNED:
        installed = probe["versions"].get(name)
        lines.append(f"{name} {installed}")
        try:
            locked = lock_version(lock, name, run.args.platform, probe["python"])
        except StepFailed as e:
            problems.append(str(e))
            continue
        if installed is None:
            problems.append(f"{name} isn't installed")
        elif strip_local(installed) != strip_local(locked):
            problems.append(f"{name} {installed} isn't uv.lock's {locked}")
    if problems:
        raise StepFailed("; ".join(problems), lines)
    return lines


def step_no_eval(run):
    installed = [name for name in EVAL_EXTRA if run.probe()["versions"].get(name) is not None]
    if installed:
        raise StepFailed(f"the eval extra's {', '.join(installed)} installed")


def step_modules(run):
    r = run.tool_python(LOAD)
    if r.returncode != 0:
        lines = r.stderr.strip().splitlines()
        raise StepFailed(f"the tool's Python exits {r.returncode}: {lines[-1] if lines else ''}")


def step_licences(run):
    argv = [sys.executable, "-I", str(LICENCES), "--site", run.probe()["site"], "--platform", run.args.platform,
            "--toolchain", str(run.root / "toolchain.toml")]
    code = subprocess.run(argv, check=False).returncode
    if code != 0:
        raise StepFailed(f"check_licences.py exits {code}")


# (step, name, run, the step it needs). Steps 3 to 7 each need only the install.
STEPS = [
    ("1", "command", step_command, None),
    ("2", "install", step_install, "1"),
    ("3", "version", step_version, "2"),
    ("4", "pins", step_pins, "2"),
    ("5", "no eval extra", step_no_eval, "2"),
    ("6", "modules", step_modules, "2"),
    ("7", "licences", step_licences, "2"),
]


def run_steps(run, steps=STEPS):
    """[{step, name, problem, lines}] in order, problem None for a pass. A step whose need failed isn't
    reached, and fails."""
    results, failed = [], set()
    for step, name, function, need in steps:
        problem, lines = None, []
        if need in failed:
            problem = f"not reached: step {need} failed"
        else:
            try:
                lines = function(run) or []
            except StepFailed as e:
                problem, lines = str(e), e.lines
            except (OSError, KeyError, tomllib.TOMLDecodeError) as e:
                problem = f"{type(e).__name__}: {e}"
        if problem:
            failed.add(step)
        results.append({"step": step, "name": name, "problem": problem, "lines": lines})
        print(report(results[-1:]), flush=True)
    return results


# The report and the record.

def report(results):
    out = []
    for r in results:
        out.append(f"{'FAIL' if r['problem'] else 'PASS'} {r['step']} {r['name']}")
        out += [f"  {line}" for line in r["lines"]]
        out += [f"  - {r['problem']}"] if r["problem"] else []
    return "\n".join(out)


def checks(platform, results):
    """One check per step (criteria.md §3)."""
    return [{"check": f"check_install.py::{platform} {r['name']}", "criteria": CRITERIA,
             "outcome": "failed" if r["problem"] else "passed"} for r in results]


def criteria_module():
    """criteria.py, loaded by path (`-I` keeps this directory off sys.path): its record writer."""
    spec = importlib.util.spec_from_file_location("lmt_ci_criteria", SCRIPTS / "criteria.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--scratch", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--readme", metavar="ROOT", type=Path, help="install with ROOT's README command")
    source.add_argument("--wheel", metavar="PATH", type=Path, help="install this wheel")
    parser.add_argument("--tag-checkout", action="store_true",
                        help="tag ROOT's HEAD with the README command's tag first")
    parser.add_argument("--criteria-out", metavar="PATH",
                        help="write each step's outcome to PATH (design/internal/criteria.md §3)")
    args = parser.parse_args(argv)
    if args.tag_checkout and not args.readme:
        parser.error("--tag-checkout needs --readme")
    args.scratch = args.scratch.resolve()
    args.readme = args.readme and args.readme.resolve()
    args.wheel = args.wheel and args.wheel.resolve()

    results = run_steps(Run(args))
    text = report(results)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"### Install ({args.platform})\n\n```\n{text}\n```\n")
    if args.criteria_out:
        criteria_module().write_record(args.criteria_out, checks(args.platform, results), complete=True)
    return 1 if any(r["problem"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
