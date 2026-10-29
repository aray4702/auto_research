"""G1-13's isolation and file compare (design/internal/g1-stage2.md, P7b): a tree's snapshot, and the product run
in a network namespace with no interface but a down loopback, where the machine allows one."""

import hashlib
import os
import shutil
import subprocess
import sys

BLOCK = 1 << 20  # a file's SHA-256 is read in 1 MiB blocks


class Isolated(subprocess.CompletedProcess):
    """A CompletedProcess, plus whether the command ran without the network."""

    def __init__(self, completed, isolated):
        super().__init__(completed.args, completed.returncode, completed.stdout, completed.stderr)
        self.isolated = isolated


def sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(BLOCK):
            digest.update(block)
    return digest.hexdigest()


def snapshot(*dirs) -> dict[str, tuple]:
    """absolute path -> ("file", size, sha256) | ("dir",) | ("link", target), for every entry under each
    directory, the directory itself excluded. Links are recorded, never followed."""
    entries = {}
    for top in dirs:
        for root, dirnames, filenames in os.walk(os.path.abspath(top), followlinks=False):
            for name in dirnames + filenames:
                path = os.path.join(root, name)
                if os.path.islink(path):
                    entries[path] = ("link", os.readlink(path))
                elif name in dirnames:
                    entries[path] = ("dir",)
                else:
                    entries[path] = ("file", os.path.getsize(path), sha256(path))
    return entries


def can_isolate() -> bool:
    """The CI runner: passwordless sudo, with unshare and setpriv on PATH."""
    if not (shutil.which("unshare") and shutil.which("setpriv") and shutil.which("sudo")):
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True, check=False).returncode == 0


def wrap(command, *, env) -> tuple[list[str], bool]:
    """The command as run_isolated runs it, and whether it is isolated. Isolated, it runs as this user, with
    exactly `env`; elsewhere it is unchanged, and the caller runs it with `env` alone."""
    if not can_isolate():
        return list(command), False
    return [
        "sudo", "-n", "unshare", "--net", "--",
        "setpriv", f"--reuid={os.getuid()}", f"--regid={os.getgid()}", "--clear-groups", "--",
        "env", "-i", *(f"{key}={value}" for key, value in env.items()), *command,
    ], True


def run_isolated(argv, *, env, cwd) -> Isolated:
    """`lmt <argv>`, isolated where wrap can isolate it."""
    command, isolated = wrap([sys.executable, "-m", "lmt", *argv], env=env)
    completed = subprocess.run(command, env=env, cwd=cwd, capture_output=True, text=True, check=False)
    return Isolated(completed, isolated)
