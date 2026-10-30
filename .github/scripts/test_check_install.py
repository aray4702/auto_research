"""Tests for the one-command install's checks (4a3), with the install and the tool's Python replaced by fakes.

Run: python3 -I -m unittest discover -s .github/scripts -t .github/scripts
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_install as cin

README = """\
# Local Meeting Transcriber

## Install

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). Set `HF_TOKEN` for the models.

```sh
uv tool install git+https://github.com/aray4702/auto_research@v1.0.0
```

## Build
"""
PYPROJECT = '[project]\nname = "local-meeting-transcriber"\nversion = "0.1.0"\n'
# A small lock of uv.lock's shape: av-like entries split at 3.12, a torch-like one split by platform, and the
# rest without markers.
LOCK = """\
version = 1

[[package]]
name = "av"
version = "18.1.0"
resolution-markers = [
    "python_full_version < '3.12' and platform_machine == 'arm64' and sys_platform == 'darwin'",
    "python_full_version < '3.12' and platform_machine == 'x86_64' and sys_platform == 'linux'",
]

[[package]]
name = "av"
version = "19.0.2"
resolution-markers = [
    "python_full_version >= '3.12' and platform_machine == 'arm64' and sys_platform == 'darwin'",
    "python_full_version >= '3.12' and platform_machine == 'x86_64' and sys_platform == 'linux'",
]

[[package]]
name = "torch"
version = "2.8.0"
resolution-markers = [
    "python_full_version >= '3.12' and platform_machine == 'arm64' and sys_platform == 'darwin'",
    "python_full_version < '3.12' and platform_machine == 'arm64' and sys_platform == 'darwin'",
]

[[package]]
name = "torch"
version = "2.8.0+cpu"
resolution-markers = [
    "python_full_version >= '3.12' and platform_machine == 'x86_64' and sys_platform == 'linux'",
    "python_full_version < '3.12' and platform_machine == 'x86_64' and sys_platform == 'linux'",
]
""" + "".join(f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n' for name, version in [
    ("faster-whisper", "1.2.1"), ("ctranslate2", "4.8.4"), ("pyannote-audio", "3.4.0"), ("torchaudio", "2.8.0"),
    ("numpy", "2.4.6"), ("huggingface-hub", "2.3.0")])
# What the fake tool's Python reports, matching LOCK on Linux with Python 3.11.
VERSIONS = {"faster-whisper": "1.2.1", "ctranslate2": "4.8.4", "pyannote-audio": "3.4.0", "torch": "2.8.0+cpu",
            "torchaudio": "2.8.0", "av": "18.1.0", "numpy": "2.4.6", "huggingface-hub": "2.3.0"}

# On PATH as `uv`: logs its argv, cwd and UV_* environment, then installs the fake lmt and tool Python.
FAKE_UV = r'''
import json, os, sys
from pathlib import Path
modes = os.environ.get("FAKE_MODE", "").split(",")
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(),
                        "env": {k: v for k, v in os.environ.items() if k.startswith("UV_")}}) + "\n")
if "install-fails" in modes:
    sys.exit(2)
for path, source in [(Path(os.environ["UV_TOOL_BIN_DIR"]) / "lmt", os.environ["FAKE_LMT"]),
                     (Path(os.environ["UV_TOOL_DIR"]) / "local-meeting-transcriber" / "bin" / "python",
                      os.environ["FAKE_PYTHON"])]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(Path(source).read_text())
    path.chmod(0o755)
'''
FAKE_LMT = r'''
import os
print(os.environ.get("FAKE_LMT_VERSION", "lmt 0.1.0"))
'''
# The tool's Python: answers the probe from FAKE_VERSIONS, and the module load per FAKE_MODE.
FAKE_PYTHON = r'''
import json, os, sys
modes = os.environ.get("FAKE_MODE", "").split(",")
code = sys.argv[sys.argv.index("-c") + 1]
if "sysconfig" in code:
    versions = json.loads(os.environ["FAKE_VERSIONS"])
    print(json.dumps({"python": os.environ.get("FAKE_PY", "3.11.9"), "site": os.environ["FAKE_SITE"],
                      "versions": {n: versions.get(n) for n in sys.argv[sys.argv.index("-c") + 2:]}}))
elif "modules-fail" in modes:
    sys.exit("ModuleNotFoundError: No module named 'lmt.rename'")
'''
FAKE_LICENCES = r'''
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"licences": sys.argv[1:]}) + "\n")
sys.exit(1 if "licences-fail" in os.environ.get("FAKE_MODE", "").split(",") else 0)
'''


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                           *args], capture_output=True, text=True, check=True).stdout.strip()


def make_repo(root, readme=README):
    root.mkdir(parents=True)
    (root / "README.md").write_text(readme)
    (root / "pyproject.toml").write_text(PYPROJECT)
    (root / "uv.lock").write_text(LOCK)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")


def script(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(0o755)
    return path


class Fake:
    """A source checkout, a scratch directory, the fakes and their log."""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.root = self.tmp / "src"
        self.scratch = self.tmp / "scratch"
        self.log = self.tmp / "fake.log"
        self.summary = self.tmp / "summary.md"
        make_repo(self.root)
        bin_dir = self.tmp / "fakebin"
        script(bin_dir / "uv", FAKE_UV)
        self.licences = script(self.tmp / "fake_licences.py", FAKE_LICENCES)
        self.env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_LOG": str(self.log),
            "FAKE_LMT": str(script(self.tmp / "fake_lmt", FAKE_LMT)),
            "FAKE_PYTHON": str(script(self.tmp / "fake_python", FAKE_PYTHON)),
            "FAKE_SITE": str(self.tmp / "site"),
            "FAKE_VERSIONS": json.dumps(VERSIONS),
            "FAKE_MODE": "",
            "GITHUB_STEP_SUMMARY": str(self.summary),
        }

    def main(self, *extra, modes=(), env=None, source=None):
        """Run check_install's main: (exit code, stdout)."""
        source = ["--readme", str(self.root), "--tag-checkout"] if source is None else source
        argv = ["--platform", "linux-x86_64", "--scratch", str(self.scratch), *source, *extra]
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.env | {"FAKE_MODE": ",".join(modes)} | (env or {})), \
                mock.patch.object(cin, "LICENCES", self.licences), contextlib.redirect_stdout(out):
            code = cin.main(argv)
        return code, out.getvalue()

    def entries(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]


class CommandTest(unittest.TestCase):
    def test_rewrites_the_github_url_to_the_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            command, tag = cin.local_command("uv tool install git+https://github.com/aray4702/auto_research@v1.0.0",
                                             tmp)
        self.assertEqual(command, f"uv tool install git+file://{Path(tmp).resolve()}@v1.0.0")
        self.assertEqual(tag, "v1.0.0")

    def test_a_command_without_the_url_fails(self):
        with self.assertRaises(cin.StepFailed):
            cin.local_command("uv tool install git+https://example.com/x@v1.0.0", "/src")

    def test_tag_checkout_tags_head_and_moves_an_old_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "src"
            make_repo(root)
            git(root, "tag", "v1.0.0")
            git(root, "commit", "-q", "--allow-empty", "-m", "second")
            cin.tag_checkout(root, "v1.0.0")
            self.assertEqual(git(root, "rev-parse", "v1.0.0^{commit}"), git(root, "rev-parse", "HEAD"))


class LockTest(unittest.TestCase):
    lock = tomllib.loads(LOCK)

    def test_strips_the_local_label(self):
        self.assertEqual(cin.strip_local("2.8.0+cpu"), "2.8.0")
        self.assertEqual(cin.strip_local("2.8.0"), "2.8.0")

    def test_av_follows_the_python_version(self):
        for platform in cin.PLATFORMS:
            self.assertEqual(cin.lock_version(self.lock, "av", platform, "3.11.9"), "18.1.0")
            self.assertEqual(cin.lock_version(self.lock, "av", platform, "3.12.0"), "19.0.2")
            self.assertEqual(cin.lock_version(self.lock, "av", platform, "3.13.1"), "19.0.2")

    def test_torch_follows_the_platform(self):
        self.assertEqual(cin.lock_version(self.lock, "torch", "linux-x86_64", "3.11.9"), "2.8.0+cpu")
        self.assertEqual(cin.lock_version(self.lock, "torch", "macos-arm64", "3.12.3"), "2.8.0")

    def test_an_entry_without_markers_applies_everywhere(self):
        self.assertEqual(cin.lock_version(self.lock, "numpy", "macos-arm64", "3.13.0"), "2.4.6")

    def test_no_applicable_entry_fails(self):
        with self.assertRaisesRegex(cin.StepFailed, "0 entries of jiwer"):
            cin.lock_version(self.lock, "jiwer", "linux-x86_64", "3.11.9")

    def test_more_than_one_applicable_entry_fails(self):
        lock = tomllib.loads('[[package]]\nname = "x"\nversion = "1"\n[[package]]\nname = "x"\nversion = "2"\n')
        with self.assertRaisesRegex(cin.StepFailed, "2 entries of x"):
            cin.lock_version(lock, "x", "linux-x86_64", "3.11.9")

    def test_an_unknown_clause_fails_even_after_a_marker_that_holds(self):
        for marker in ["implementation_name == 'cpython'",
                       "sys_platform == 'linux' or sys_platform == 'darwin'",
                       "sys_platform < 'linux'"]:
            lock = {"package": [{"name": "x", "version": "1", "resolution-markers": [
                "platform_machine == 'x86_64' and sys_platform == 'linux'", marker]}]}
            with self.subTest(marker), self.assertRaises(cin.StepFailed):
                cin.lock_version(lock, "x", "linux-x86_64", "3.11.9")


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = Fake(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def outcomes(self, out):
        return [line.split()[0] for line in out.splitlines() if line.startswith(("PASS", "FAIL"))]

    def test_all_steps_pass_with_the_readme_command(self):
        code, out = self.fake.main()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.outcomes(out), ["PASS"] * 7)
        root, scratch = self.fake.root.resolve(), self.fake.scratch.resolve()
        install, licences = self.fake.entries()
        self.assertEqual(install["argv"], ["tool", "install", f"git+file://{root}@v1.0.0"])
        self.assertEqual(install["cwd"], str(scratch))
        uv_dirs = ("UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR")
        self.assertEqual({k: install["env"].get(k) for k in uv_dirs}, {
            "UV_TOOL_DIR": str(scratch / "tools"), "UV_TOOL_BIN_DIR": str(scratch / "bin"),
            "UV_CACHE_DIR": str(scratch / "cache"), "UV_PYTHON_INSTALL_DIR": str(scratch / "python")})
        self.assertEqual(licences["licences"][:4], ["--site", self.fake.env["FAKE_SITE"], "--platform", "linux-x86_64"])
        self.assertEqual(git(root, "rev-parse", "v1.0.0^{commit}"), git(root, "rev-parse", "HEAD"))
        summary = self.fake.summary.read_text()
        self.assertIn("### Install (linux-x86_64)", summary)
        self.assertIn("Python 3.11.9", summary)
        self.assertIn("torch 2.8.0+cpu", summary)

    def test_without_tag_checkout_no_tag_is_made(self):
        code, out = self.fake.main(source=["--readme", str(self.fake.root)])
        self.assertEqual(code, 0, out)
        self.assertEqual(git(self.fake.root, "tag"), "")

    def test_the_wheel_is_installed_as_given(self):
        wheel = self.fake.tmp / "dist" / "local_meeting_transcriber-0.1.0-py3-none-any.whl"
        code, out = self.fake.main(source=["--wheel", str(wheel)])
        self.assertEqual(code, 0, out)
        self.assertEqual(self.fake.entries()[0]["argv"], ["tool", "install", str(wheel.resolve())])

    def test_a_readme_without_the_command_fails_every_step(self):
        (self.fake.root / "README.md").write_text("# Local Meeting Transcriber\n")
        code, out = self.fake.main()
        self.assertEqual(code, 1)
        self.assertEqual(self.outcomes(out), ["FAIL"] * 7)
        self.assertFalse(self.fake.log.exists())

    def test_a_failed_install_fails_the_steps_after_it(self):
        code, out = self.fake.main(modes=["install-fails"])
        self.assertEqual(code, 1)
        self.assertEqual(self.outcomes(out), ["PASS"] + ["FAIL"] * 6)
        self.assertEqual(out.count("not reached: step 2 failed"), 5)

    def test_each_failing_check_fails_its_own_step(self):
        version_mismatch = {"FAKE_LMT_VERSION": "lmt 0.0.9"}
        pin_mismatch = {"FAKE_VERSIONS": json.dumps(VERSIONS | {"torch": "2.7.1+cpu"})}
        eval_extra = {"FAKE_VERSIONS": json.dumps(VERSIONS | {"jiwer": "3.1.0"})}
        python_312 = {"FAKE_PY": "3.12.4"}  # av 18.1.0 is the lock's for 3.11, not 3.12
        for env, modes, step in [(version_mismatch, (), "3"), (pin_mismatch, (), "4"), (python_312, (), "4"),
                                 (eval_extra, (), "5"), ({}, ["modules-fail"], "6"), ({}, ["licences-fail"], "7")]:
            with self.subTest(step=step, env=env, modes=modes):
                code, out = self.fake.main(env=env, modes=modes)
                self.assertEqual(code, 1)
                outcomes = self.outcomes(out)
                self.assertEqual(outcomes, ["FAIL" if str(i + 1) == step else "PASS" for i in range(7)], out)

    def test_the_record_has_seven_g3_7_checks(self):
        path = self.fake.tmp / "criteria-test-3.json"
        code, _ = self.fake.main("--criteria-out", str(path), env={"LMT_TIER": "1"})
        self.assertEqual(code, 0)
        record = json.loads(path.read_text())
        self.assertEqual(record["job"], "test")
        self.assertIs(record["complete"], True)
        self.assertEqual(record["tier"], 1)
        self.assertEqual([c["check"] for c in record["checks"]], [
            f"check_install.py::linux-x86_64 {name}"
            for name in ["command", "install", "version", "pins", "no eval extra", "modules", "licences"]])
        for c in record["checks"]:
            self.assertEqual(c["criteria"], ["G3-7"])
            self.assertEqual(c["outcome"], "passed")

    def test_the_record_fails_the_steps_after_a_failed_install(self):
        path = self.fake.tmp / "criteria-test-3.json"
        code, _ = self.fake.main("--criteria-out", str(path), modes=["install-fails"])
        self.assertEqual(code, 1)
        record = json.loads(path.read_text())
        self.assertEqual(record["job"], "test")
        self.assertIs(record["complete"], True)
        self.assertEqual([c["outcome"] for c in record["checks"]], ["passed"] + ["failed"] * 6)
        self.assertTrue(all(c["criteria"] == ["G3-7"] for c in record["checks"]))


if __name__ == "__main__":
    unittest.main()
