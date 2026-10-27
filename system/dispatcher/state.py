"""The Dispatcher's own state, in a home of its own on GitHub: the branch dispatcher/state.

Each kind of record is a JSON-lines file on that branch (runs.jsonl, ...). Appending commits and
pushes, so the history is append-only and anyone can read it. Standard library only.
"""

import contextlib
import fcntl
import json
import subprocess
from pathlib import Path

BRANCH = "dispatcher/state"
AUTHOR = ["-c", "user.name=auto_research dispatcher", "-c", "user.email=aray4702@users.noreply.github.com"]
README = "The Dispatcher's state (blueprint §3): one JSON-lines file per kind of record. Append-only.\n"


class Store:
    def __init__(self, path, remote):
        self.path, self.remote = Path(path), remote

    @contextlib.contextmanager
    def locked(self):
        """One user of the local clone at a time: launchers, the Dispatcher and arsys share it, and two
        at once collided on git's own files (a launcher's checkout failed, 2026-10-27 00:43 PT)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.parent / f"{self.path.name}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def git(self, *args, check=True):
        return subprocess.run(
            ["git", *AUTHOR, *args], cwd=self.path, check=check, capture_output=True, text=True
        )

    def sync(self, create=False):
        """Make the local copy current. With create, start the branch on the remote if it's missing.
        Returns False when the branch doesn't exist (and wasn't created)."""
        if not (self.path / ".git").exists():
            self.path.mkdir(parents=True, exist_ok=True)
            self.git("init", "--quiet")
            self.git("remote", "add", "origin", self.remote)
        heads = self.git("ls-remote", "--heads", "origin", BRANCH).stdout.strip()
        if not heads:
            if not create:
                return False
            self.git("checkout", "--quiet", "--orphan", BRANCH)
            (self.path / "README.md").write_text(README)
            self.git("add", "README.md")
            self.git("commit", "--quiet", "-m", "Start the Dispatcher's state")
            self.git("push", "--quiet", "-u", "origin", BRANCH)
            return True
        self.git("fetch", "--quiet", "origin", BRANCH)
        if self.git("rev-parse", "--verify", "--quiet", BRANCH, check=False).returncode:
            self.git("checkout", "--quiet", "-B", BRANCH, f"origin/{BRANCH}")
        else:
            self.git("checkout", "--quiet", BRANCH)
            self.git("rebase", "--quiet", f"origin/{BRANCH}")
        return True

    def read(self, kind):
        with self.locked():
            if not self.sync():
                return []
            path = self.path / f"{kind}.jsonl"
            if not path.exists():
                return []
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def append(self, kind, record, attempts=3):
        with self.locked():
            self._append(kind, record, attempts)

    def _append(self, kind, record, attempts):
        for attempt in range(attempts):
            self.sync(create=True)
            with open(self.path / f"{kind}.jsonl", "a") as f:
                f.write(json.dumps(record, sort_keys=True) + "\n")
            self.git("add", f"{kind}.jsonl")
            summary = " ".join(str(record.get(k, "")) for k in ("event", "run_id")).strip()
            self.git("commit", "--quiet", "-m", f"{kind}: {summary or 'record'}")
            if self.git("push", "--quiet", "origin", BRANCH, check=False).returncode == 0:
                return
            # Someone else pushed first: drop the local commit, take theirs, write again.
            self.git("reset", "--quiet", "--hard", f"origin/{BRANCH}")
        raise RuntimeError(f"could not push {kind} to {BRANCH} after {attempts} attempts")
