"""tests/isolation.py, G1-13's isolation and file compare (design/internal/g1-stage2.md, P7b), with no model."""

import errno
import hashlib
import json
import os
import subprocess
import sys

import pytest
from isolation import run_isolated, snapshot, wrap

import lmt

pytestmark = pytest.mark.criterion("G1-13")

# Under CI on Linux (the ubuntu runner) wrap must isolate; elsewhere the isolated case skips.
MUST_ISOLATE = bool(os.environ.get("CI")) and sys.platform.startswith("linux")

PRINT_ENV = "import json, os; print(json.dumps({'environ': dict(os.environ), 'uid': os.getuid()}))"
CONNECT = """
import socket
try:
    socket.create_connection(("192.0.2.1", 80), timeout=3)
except OSError as e:
    print("OSError", type(e).__name__, e.errno)
else:
    print("connected")
"""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def changed(before, after) -> set[str]:
    """The paths new, gone or different between two snapshots."""
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


@pytest.fixture
def tree(tmp_path):
    top = tmp_path / "top"
    (top / "sub").mkdir(parents=True)
    (top / "a.txt").write_bytes(b"hello\n")
    (top / "empty").write_bytes(b"")
    (top / "sub" / "b.bin").write_bytes(bytes(range(256)) * 5000)  # over 1 MiB, so it takes two blocks
    (top / "dangling").symlink_to(tmp_path / "missing")
    return top


class TestSnapshot:
    def test_gives_each_entry(self, tree, tmp_path):
        big = bytes(range(256)) * 5000
        assert snapshot(tree) == {
            str(tree / "a.txt"): ("file", 6, digest(b"hello\n")),
            str(tree / "empty"): ("file", 0, digest(b"")),
            str(tree / "sub"): ("dir",),
            str(tree / "sub" / "b.bin"): ("file", len(big), digest(big)),
            str(tree / "dangling"): ("link", str(tmp_path / "missing")),
        }

    def test_is_stable(self, tree):
        assert snapshot(tree) == snapshot(tree)

    def test_a_changed_byte_changes_only_that_file(self, tree):
        before = snapshot(tree)
        path = tree / "sub" / "b.bin"
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(bytes(data))
        after = snapshot(tree)
        assert changed(before, after) == {str(path)}
        assert after[str(path)][1] == before[str(path)][1]  # same size, a different SHA-256

    def test_an_added_file_is_the_only_change(self, tree):
        before = snapshot(tree)
        (tree / "sub" / "new").write_bytes(b"x")
        after = snapshot(tree)
        assert changed(before, after) == {str(tree / "sub" / "new")}
        assert after[str(tree / "sub" / "new")] == ("file", 1, digest(b"x"))

    def test_a_removed_file_is_the_only_change(self, tree):
        before = snapshot(tree)
        (tree / "a.txt").unlink()
        after = snapshot(tree)
        assert changed(before, after) == {str(tree / "a.txt")}
        assert str(tree / "a.txt") not in after

    def test_a_retargeted_link_is_the_only_change(self, tree, tmp_path):
        before = snapshot(tree)
        (tree / "dangling").unlink()
        (tree / "dangling").symlink_to(tmp_path / "elsewhere")
        after = snapshot(tree)
        assert changed(before, after) == {str(tree / "dangling")}
        assert after[str(tree / "dangling")] == ("link", str(tmp_path / "elsewhere"))

    def test_a_link_to_a_directory_is_not_followed(self, tree, tmp_path):
        (tmp_path / "outside").mkdir()
        (tmp_path / "outside" / "c.txt").write_bytes(b"c")
        (tree / "to_outside").symlink_to(tmp_path / "outside")
        entries = snapshot(tree)
        assert entries[str(tree / "to_outside")] == ("link", str(tmp_path / "outside"))
        assert str(tree / "to_outside" / "c.txt") not in entries


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LMT_TEST_PARENT", "1")
    return {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "LMT_TEST_MARK": "1"}


def run_wrapped(code, env, cwd):
    """[sys.executable, "-c", code], wrapped and run as run_isolated runs it."""
    command, isolated = wrap([sys.executable, "-c", code], env=env)
    completed = subprocess.run(command, env=env, cwd=cwd, capture_output=True, text=True, check=False)
    return completed, isolated


class TestWrap:
    def test_the_child_has_the_given_environment_and_the_tests_uid(self, env, tmp_path):
        completed, _ = run_wrapped(PRINT_ENV, env, tmp_path)
        assert completed.returncode == 0, completed.stderr
        child = json.loads(completed.stdout)
        assert child["environ"]["LMT_TEST_MARK"] == "1"
        assert "LMT_TEST_PARENT" not in child["environ"]
        assert child["uid"] == os.getuid()

    def test_where_it_isolates_the_child_has_no_route(self, env, tmp_path):
        completed, isolated = run_wrapped(CONNECT, env, tmp_path)
        if MUST_ISOLATE:
            assert isolated, "under CI on Linux, wrap must isolate (sudo -n, unshare and setpriv)"
        elif not isolated:
            pytest.skip("wrap can't isolate here: no passwordless sudo, unshare or setpriv")
        assert completed.returncode == 0, completed.stderr
        # No route fails at once with ENETUNREACH; outside a namespace 192.0.2.1 times out, also an OSError.
        assert completed.stdout.split() == ["OSError", "OSError", str(errno.ENETUNREACH)], completed.stdout


def test_run_isolated_runs_lmt(env, tmp_path):
    result = run_isolated(["--version"], env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert lmt.__version__ in result.stdout
    if MUST_ISOLATE:
        assert result.isolated
