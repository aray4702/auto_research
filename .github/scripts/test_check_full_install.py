"""Tests for the full-install sequence's frame and online phase (4b1), on a fake `lmt`.

Run: python3 -I -m unittest discover -s .github/scripts -t .github/scripts
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_full_install as cfi

TOKEN = "abcdefghijklmnopqrstuv"  # 22 characters, as the demo's URL has
# Stands in for the installed lmt. It appends its argv, cwd and environment to FAKE_LMT_LOG, and
# FAKE_LMT_MODE (comma-separated) picks its faults.
FAKE_LMT = f'TOKEN = "{TOKEN}"\n' + r'''
import http.server, json, os, signal, sys, time
from pathlib import Path

modes = os.environ.get("FAKE_LMT_MODE", "").split(",")
keys = ("HOME", "TMPDIR", "XDG_CACHE_HOME", "LMT_CACHE_DIR", "LMT_DATA_DIR", "HF_TOKEN")
with open(os.environ["FAKE_LMT_LOG"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "pid": os.getpid(),
                        "env": {k: os.environ.get(k) for k in keys}}) + "\n")
cache, data = Path(os.environ["LMT_CACHE_DIR"]), Path(os.environ["LMT_DATA_DIR"])

if sys.argv[1:] == ["models", "download"]:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "model.bin").write_bytes(b"weights")
    sys.exit(1 if "download-fails" in modes else 0)

if sys.argv[1:] == ["demo", "--sample", "--no-browser"]:
    (data / "derived" / "AMI-ci").mkdir(parents=True, exist_ok=True)
    (data / "derived" / "AMI-ci" / "ES2004a.wav").write_bytes(b"RIFF....WAVE")
    (data / "sample" / "0123456789abcdef").mkdir(parents=True, exist_ok=True)
    (data / "sample" / "0123456789abcdef" / "ES2004a.json").write_text("{}")
    if "write-home" in modes:
        Path(os.environ["HOME"], ".lmtrc").write_text("x")
    if "write-models" in modes:
        (cache / "late.bin").write_bytes(b"late")
    if "no-url" in modes:
        while True:
            time.sleep(1)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = {f"/{TOKEN}/": b"<!doctype html>", f"/{TOKEN}/audio.wav": b"RIFF\0\0\0\0WAVE"}.get(self.path)
            self.send_response(200 if body else 404)
            self.end_headers()
            self.wfile.write(body or b"")

        def log_message(self, *args):
            pass

    def stop(*_):
        raise SystemExit(3 if "exit-3" in modes else 0)

    signal.signal(signal.SIGTERM, signal.SIG_IGN if "ignore-term" in modes else stop)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"http://127.0.0.1:{server.server_address[1]}/{TOKEN}/", flush=True)
    server.serve_forever()

sys.exit(2)
'''


class Fake:
    """A work directory, a fake lmt and its log."""

    def __init__(self, tmp, modes=()):
        self.tmp = Path(tmp)
        self.work = self.tmp / "work"
        self.log = self.tmp / "lmt.log"
        self.lmt = self.tmp / "bin" / "lmt"
        self.lmt.parent.mkdir()
        self.lmt.write_text(f"#!{sys.executable} -I\n{FAKE_LMT}")
        self.lmt.chmod(0o755)
        self.env = {"FAKE_LMT_LOG": str(self.log), "FAKE_LMT_MODE": ",".join(modes),
                    "HF_TOKEN": "fake-token-for-the-test"}

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def main(self, *extra):
        """main() on the fake, quietly: (exit code, its stdout)."""
        out = io.StringIO()
        env = dict(self.env, GITHUB_STEP_SUMMARY=str(self.tmp / "summary.md"))
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            code = cfi.main(["--lmt", str(self.lmt), "--platform", "linux-x86_64", "--work", str(self.work),
                             *extra])
        return code, out.getvalue()


class FakeTest(unittest.TestCase):
    modes = ()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fake = Fake(tmp.name, self.modes)

    def fake_with(self, *modes):
        self.fake.env["FAKE_LMT_MODE"] = ",".join(modes)
        return self.fake

    def assert_gone(self, pid):
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class OnlineOrderTest(FakeTest):
    def test_a_passing_run(self):
        code, out = self.fake.main()
        self.assertEqual(code, 0, out)
        self.assertEqual([c["argv"] for c in self.fake.calls()],
                         [["models", "download"], ["demo", "--sample", "--no-browser"]])
        self.assertEqual([line.split()[:2] for line in out.splitlines()],
                         [["PASS", "1.1"], ["PASS", "1.2"], ["PASS", "1.3"]])
        summary = (self.fake.tmp / "summary.md").read_text()
        for step in ("PASS 1.1 models download", "PASS 1.2 snapshot <work>/models", "PASS 1.3 the first --sample run"):
            self.assertIn(step, summary)

    def test_a_failed_step_fails_the_rest(self):
        code, out = self.fake_with("download-fails").main()
        self.assertEqual(code, 1)
        self.assertEqual([c["argv"] for c in self.fake.calls()], [["models", "download"]])
        self.assertIn("lmt models download exits 1", out)
        self.assertIn("FAIL 1.3 the first --sample run\n  - not reached: step 1.1 failed", out)


class RunDemoTest(FakeTest):
    def first_sample(self):
        """Step 1.3 alone, after 1.1 and 1.2: its problem, or None."""
        args = mock.Mock(lmt=self.fake.lmt, work=self.fake.work)
        with mock.patch.dict(os.environ, self.fake.env):
            cfi.step_models_download(args)
            cfi.step_snapshot_models(args)
            try:
                cfi.step_first_sample(args)
            except cfi.StepFailed as e:
                return str(e)
        return None

    def test_passes(self):
        self.assertIsNone(self.first_sample())

    def test_no_url_within_the_limit(self):
        self.fake_with("no-url")
        with mock.patch.object(cfi, "FIRST_URL_LIMIT", 1):
            self.assertEqual(self.first_sample(), "no URL on stdout within the 1 s limit")
        self.assert_gone(self.fake.calls()[-1]["pid"])

    def test_ignores_sigterm(self):
        self.fake_with("ignore-term")
        with mock.patch.object(cfi, "FETCH_LIMIT", 1):
            self.assertEqual(self.first_sample(), "still running 1 s after SIGTERM (the limit)")
        self.assert_gone(self.fake.calls()[-1]["pid"])

    def test_exit_code_other_than_0(self):
        self.fake_with("exit-3")
        self.assertEqual(self.first_sample(), "exits 3 after SIGTERM, not 0")

    def test_a_file_outside_lmt_data_dir(self):
        self.fake_with("write-home")
        self.assertIn("the run's fresh home directory isn't empty", self.first_sample())

    def test_models_written_after_the_snapshot(self):
        self.fake_with("write-models")
        problem = self.first_sample()
        self.assertTrue(problem.startswith("<work>/models changed: "), problem)
        self.assertIn("late.bin", problem)


class DirectoriesTest(FakeTest):
    def test_models_snapshot_is_written_after_the_download(self):
        code, out = self.fake.main()
        self.assertEqual(code, 0, out)
        models = self.fake.work.resolve() / "models"
        saved = json.loads((self.fake.work / "snapshots" / "models.json").read_text())
        self.assertEqual(saved, cfi.snapshot(models))
        self.assertIn(str(models / "model.bin"), saved)

    def test_no_snapshot_when_the_download_fails(self):
        self.fake_with("download-fails").main()
        self.assertFalse((self.fake.work / "snapshots" / "models.json").exists())

    def test_each_command_has_fresh_directories_and_only_download_has_hf_token(self):
        code, out = self.fake.main()
        self.assertEqual(code, 0, out)
        work = self.fake.work.resolve()
        calls = self.fake.calls()
        seen = set()
        for call, step in zip(calls, ("models-download", "sample-online")):
            run = work / "runs" / step
            env = call["env"]
            self.assertEqual([env["HOME"], env["TMPDIR"], env["XDG_CACHE_HOME"]],
                             [str(run / "home"), str(run / "tmp"), str(run / "cache")])
            self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(run / "cwd"))
            self.assertEqual([env["LMT_CACHE_DIR"], env["LMT_DATA_DIR"]], [str(work / "models"), str(work / "data")])
            seen |= {env["HOME"], env["TMPDIR"], env["XDG_CACHE_HOME"], call["cwd"]}
        self.assertEqual(len(seen), 8)
        self.assertEqual([c["env"]["HF_TOKEN"] for c in calls], ["fake-token-for-the-test", None])

    def test_snapshot_kinds(self):
        root = self.fake.tmp / "snap"
        (root / "d").mkdir(parents=True)
        (root / "d" / "f").write_bytes(b"abc")
        (root / "l").symlink_to("d/f")
        self.assertEqual(cfi.snapshot(root), {
            str(root / "d"): ["dir"],
            str(root / "d" / "f"): ["file", 3, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"],
            str(root / "l"): ["link", "d/f"],
        })


class OfflineCommandTest(unittest.TestCase):
    def test_argv_for_both_platforms(self):
        script = str(Path(cfi.__file__).resolve())
        phase = ["env", "-i", "HOME=/w/runs/offline/home", "PATH=/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE=1",
                 sys.executable, "-I", script, "offline",
                 "--lmt", "/s/bin/lmt", "--platform", "{platform}", "--work", "/w"]
        with mock.patch.object(cfi.subprocess, "run") as run, mock.patch.object(cfi.subprocess, "Popen") as popen:
            linux = cfi.offline_command("/s/bin/lmt", "linux-x86_64", "/w", path="/usr/bin:/bin", uid=1001, gid=118)
            macos = cfi.offline_command("/s/bin/lmt", "macos-arm64", "/w", path="/usr/bin:/bin")
        run.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(linux, [
            "sudo", "-n", "unshare", "--net", "--", "sh", "-c",
            'ip link set lo up && u=$1 g=$2 && shift 2 && exec setpriv --reuid="$u" --regid="$g" --init-groups -- "$@"',
            "sh", "1001", "118", *[a.replace("{platform}", "linux-x86_64") for a in phase]])
        self.assertEqual(macos, [a.replace("{platform}", "macos-arm64") for a in phase])

    def test_linux_defaults_to_the_runners_user(self):
        argv = cfi.offline_command("/s/bin/lmt", "linux-x86_64", "/w")
        self.assertEqual(argv[9:11], [str(os.getuid()), str(os.getgid())])
        self.assertEqual(argv[14], f"PATH={os.environ.get('PATH', '')}")


class RecordTest(FakeTest):
    def record(self, *modes):
        out = self.fake.tmp / "criteria-install-full.json"
        code, _ = self.fake_with(*modes).main("--criteria-out", str(out))
        return code, json.loads(out.read_text())

    def test_a_passing_run(self):
        code, record = self.record()
        self.assertEqual(code, 0)
        self.assertEqual((record["job"], record["complete"]), ("install-full", True))
        self.assertEqual(record["checks"], [
            {"check": "check_full_install.py::linux-x86_64 1.1 models download", "criteria": ["G3-8"],
             "outcome": "passed"},
            {"check": "check_full_install.py::linux-x86_64 1.3 the first --sample run", "criteria": ["G3-6"],
             "outcome": "passed"},
        ])

    def test_a_failing_run(self):
        code, record = self.record("download-fails")
        self.assertEqual(code, 1)
        self.assertTrue(record["complete"])
        self.assertEqual([(c["check"], c["outcome"]) for c in record["checks"]], [
            ("check_full_install.py::linux-x86_64 1.1 models download", "failed"),
            ("check_full_install.py::linux-x86_64 1.3 the first --sample run", "failed"),
        ])


if __name__ == "__main__":
    unittest.main()
