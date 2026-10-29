"""One scored file's product run, in a process of its own, recording each stage's output and time
(design/internal/g1-stage2.md, "The runs").

    python -m lmt.eval.worker STAGES_OUT transcribe INPUT [--no-diarize]

It wraps `asr.load` and `diarize.load` for this process only, and the `transcribe` and `diarize` methods of
what they return. Each wrapper adds its wall time to its stage's seconds, and the two stage wrappers keep
what they return. It then calls `lmt.cli.main` unchanged, so the product's own code runs; the wrappers only
observe it. If `main` returns 0, it writes STAGES_OUT, by a temporary file renamed into place:

    {"words": [[start, end, text], ...], "turns": [[start, end, speaker], ...] or null,
     "asr_seconds": ..., "diarize_seconds": ... or null}

It exits with `main`'s code.
"""

import contextlib
import functools
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from lmt import asr, cli, diarize


class Stages:
    """What the wrapped stages gave, and their seconds. ASR and diarization may run in two threads."""

    def __init__(self):
        self.words = None
        self.turns = None
        self.seconds = {"asr": 0.0, "diarize": 0.0}
        self.ran = set()
        self._lock = threading.Lock()

    def timed(self, stage, function, keep=False):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = function(*args, **kwargs)
            finally:
                with self._lock:
                    self.seconds[stage] += time.perf_counter() - start
                    self.ran.add(stage)
            if keep:
                setattr(self, "words" if stage == "asr" else "turns", list(result))
            return result

        return wrapper

    def loader(self, stage, load, method):
        """`load` timed, with the returned object's `method` timed and its result kept."""

        def wrapped(*args, **kwargs):
            engine = self.timed(stage, load)(*args, **kwargs)
            setattr(engine, method, self.timed(stage, getattr(engine, method), keep=True))
            return engine

        return functools.wraps(load)(wrapped)

    def to_dict(self):
        diarized = "diarize" in self.ran
        return {
            "words": [[w.start, w.end, w.text] for w in self.words or ()],
            "turns": [[t.start, t.end, t.speaker] for t in self.turns or ()] if diarized else None,
            "asr_seconds": self.seconds["asr"],
            "diarize_seconds": self.seconds["diarize"] if diarized else None,
        }


@contextlib.contextmanager
def observed(stages):
    """asr.load and diarize.load wrapped until the block ends."""
    saved = asr.load, diarize.load
    asr.load = stages.loader("asr", asr.load, "transcribe")
    diarize.load = stages.loader("diarize", diarize.load, "diarize")
    try:
        yield stages
    finally:
        asr.load, diarize.load = saved


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) < 2:
        print("usage: python -m lmt.eval.worker STAGES_OUT transcribe INPUT [OPTIONS]", file=sys.stderr)
        return 2
    out, command = Path(argv[0]), argv[1:]
    with observed(Stages()) as stages:
        code = cli.main(command)
    if code == 0:
        _write(out, stages.to_dict())
    return code


def _write(path, doc):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    sys.exit(main())
