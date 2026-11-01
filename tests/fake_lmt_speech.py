"""A stand-in lmt-speech (design/internal/g4-stage1.md §3, "Tests"): it replays a recorded reply and records
what it was given, so tests run apple.py against the helper's replies recorded on the Mac.

`install(directory, replies)` writes an executable `lmt-speech` into `directory` and returns its path, for
tests to give `apple.build`. `replies` maps a verb to a fixture file (`tests/fixtures/apple/`, or one a test
writes). Each run appends {"argv": [...], "stdin": <file>} to `directory/calls.jsonl`, with stdin's bytes in
that file, then reads stdin to its end, prints the verb's reply, and exits 3 after an error line, else 0.
"""

import json
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "apple"


def install(directory, replies) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / "replies.json"
    config.write_text(json.dumps({verb: str(path) for verb, path in replies.items()}))
    binary = directory / "lmt-speech"
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).resolve()}" "{config}" "$@"\n')
    binary.chmod(0o755)
    return binary


def calls(directory) -> list[dict]:
    log = Path(directory) / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def main(config, *argv):
    directory = Path(config).parent
    replies = json.loads(Path(config).read_text())
    log = directory / "calls.jsonl"
    stdin = directory / f"stdin-{len(calls(directory))}.bin"
    with open(log, "a") as f:
        f.write(json.dumps({"argv": [str(directory / "lmt-speech"), *argv], "stdin": str(stdin)}) + "\n")
    stdin.write_bytes(sys.stdin.buffer.read())
    sys.stderr.write("lmt-speech: a stand-in\n")
    if not argv or argv[0] not in replies:
        print(json.dumps({"type": "error", "code": "failed", "message": f"no reply for {argv}"}), flush=True)
        return 3
    lines = Path(replies[argv[0]]).read_text().splitlines()
    for line in lines:
        print(line, flush=True)
    try:
        last = json.loads(lines[-1]) if lines else {}
    except ValueError:
        return 0
    return 3 if last.get("type") == "error" else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
