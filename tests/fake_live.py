"""A stand-in for `lmt live --jsonl` (design/internal/g2-stage1.md §5, item 5b), run as
`[sys.executable, <this file>, *args]`. Standard library only, so it needs no model.

It prints a scripted `start`, sleeps 0.3 s, prints a `words` event, sleeps 0.2 s, prints `stop`, and writes a
valid transcript to `<-o>/<-n>.json`. It appends its argv, as one JSON line, to the file LMT_FAKE_LIVE_ARGV
names, if set. With LMT_FAKE_LIVE_EXIT set, it writes two lines to stderr and exits with that code instead.
"""

import json
import os
import sys
import time
from pathlib import Path

MODELS = {
    "asr": {"name": "distil-large-v3", "repository": "Systran/faster-distil-whisper-large-v3",
            "revision": "c3058b475261292e64a0412df1d2681c06260fab"},
    "diarization": {"name": "speaker-diarization-3.1", "repository": "pyannote/speaker-diarization-3.1",
                    "revision": "84fd25912480287da0247647c3d2b4853cb3ee5d"},
}
WORDS = [{"start": 0.5, "end": 0.8, "text": "okay", "probability": 0.9},
         {"start": 0.9, "end": 1.0, "text": "so", "probability": 0.9}]


def option(args, name):
    return args[args.index(name) + 1]


def emit(event):
    print(json.dumps(event), flush=True)


def main(args):
    argv_log = os.environ.get("LMT_FAKE_LIVE_ARGV")
    if argv_log:
        with open(argv_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(args) + "\n")
    code = os.environ.get("LMT_FAKE_LIVE_EXIT")
    if code:
        print("loading the models", file=sys.stderr)
        print("lmt: error: the fake failed", file=sys.stderr)
        return int(code)
    stem, out_dir = option(args, "-n"), Path(option(args, "-o"))
    if os.environ.get("LMT_APP_CHILD") and "--jsonl" in args:  # lmt app's child protocol (lmt.live.command)
        emit({"type": "loading", "stage": "engine", "engine": option(args, "--engine")})
        emit({"type": "loading", "stage": "speakers"})
        emit({"type": "ready"})
        line = sys.stdin.readline().split()
        if not line or line[0] != "go":
            return 0
        if len(line) > 1:
            stem = line[1]
    emit({"type": "start", "schema_version": "1.0", "t": 0, "source": {"file": stem, "live": True},
          "models": MODELS})
    time.sleep(0.3)
    emit({"type": "words", "t": 1.0, "segment": 1, "speaker": "S1", "label": "Speaker 1", "words": WORDS})
    time.sleep(0.2)
    emit({"type": "stop", "t": 1.0, "reason": "end-of-input"})
    doc = {
        "schema_version": "1.1", "lmt_version": "0.1.0", "source": {"file": stem, "duration": 1.0, "live": True},
        "models": MODELS, "speakers": [{"id": "S1", "label": "Speaker 1"}],
        "segments": [{"start": 0.5, "end": 1.0, "speaker": "S1", "text": "okay so", "words": WORDS}],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    if "--save-audio" in args:  # 1 s of silence, as `lmt live --save-audio` writes <stem>.wav
        import wave

        with wave.open(str(out_dir / f"{stem}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\0\0" * 16000)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
