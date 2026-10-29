"""A stand-in for `python -m lmt.eval.worker STAGES_OUT transcribe INPUT [--no-diarize]`, with no model
(design/internal/g1-stage2.md, "Tests").

INPUT's fixture transcript is the file beside it with the suffix .json (tests/fixtures/eval/ls/u1.json for
u1.flac). It is copied into the working directory as <stem>.json, as `lmt transcribe` writes it. The stages
file's words are its segments' texts split on white space, each with its segment's times, and its turns are
its segments' turns, or null under --no-diarize.

FAKE_WORKER_LOG, if set, is a file that gets this process's argv as one JSON line. FAKE_WORKER_EXIT, if set,
is an exit code to give at once, after one line on stderr, writing nothing.
"""

import json
import os
import shutil
import sys
from pathlib import Path

ASR_SECONDS, DIARIZE_SECONDS = 0.25, 0.5


def main(argv):
    if log := os.environ.get("FAKE_WORKER_LOG"):
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(argv) + "\n")
    stages_out, command, audio, *options = argv
    assert command == "transcribe", argv
    if code := int(os.environ.get("FAKE_WORKER_EXIT", "0")):
        print(f"lmt: error: {Path(audio).name}: fake exit {code}", file=sys.stderr)
        return code
    fixture = Path(audio).with_suffix(".json")
    shutil.copyfile(fixture, Path.cwd() / f"{Path(audio).stem}.json")
    segments = json.loads(fixture.read_text(encoding="utf-8"))["segments"]
    diarize = "--no-diarize" not in options
    stages = {
        "words": [[s["start"], s["end"], word] for s in segments for word in s["text"].split()],
        "turns": [[s["start"], s["end"], s["speaker"]] for s in segments] if diarize else None,
        "asr_seconds": ASR_SECONDS,
        "diarize_seconds": DIARIZE_SECONDS if diarize else None,
    }
    Path(stages_out).write_text(json.dumps(stages), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
