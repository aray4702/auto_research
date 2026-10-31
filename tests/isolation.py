"""G1-13's isolation and file compare (design/internal/g1-stage2.md, P7b): a tree's snapshot, and the product run
under macOS's sandbox-exec with a profile that denies the network, where the machine allows it (since G4, #573:
macOS 26 is the one platform, so Linux's network namespace is gone)."""

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


# Everything allowed but the network beyond this machine: localhost works (the demo's server), any other connect
# fails with EPERM, and a name lookup fails.
SANDBOX_PROFILE = ("(version 1)(allow default)(deny network*)(allow network-bind (local ip \"localhost:*\"))"
                   "(allow network-inbound (local ip \"localhost:*\"))(allow network-outbound (remote ip \"localhost:*\"))")


def can_isolate() -> bool:
    """macOS with sandbox-exec, where a sandbox can be applied (it can't inside another sandbox)."""
    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        return False
    probe = ["sandbox-exec", "-p", SANDBOX_PROFILE, "/usr/bin/true"]
    return subprocess.run(probe, capture_output=True, check=False).returncode == 0


def wrap(command, *, env) -> tuple[list[str], bool]:
    """The command as run_isolated runs it, and whether it is isolated. Isolated, it runs as this user, with
    exactly `env`; elsewhere it is unchanged, and the caller runs it with `env` alone."""
    if not can_isolate():
        return list(command), False
    return [
        "sandbox-exec", "-p", SANDBOX_PROFILE,
        "env", "-i", *(f"{key}={value}" for key, value in env.items()), *command,
    ], True


def run_isolated(argv, *, env, cwd) -> Isolated:
    """`lmt <argv>`, isolated where wrap can isolate it."""
    command, isolated = wrap([sys.executable, "-m", "lmt", *argv], env=env)
    completed = subprocess.run(command, env=env, cwd=cwd, capture_output=True, text=True, check=False)
    return Isolated(completed, isolated)
