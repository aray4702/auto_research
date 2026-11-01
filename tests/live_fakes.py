"""`lmt` on fake stages, so the command's signals are checked in a subprocess without a model
(design/internal/g2-stage1.md §3c, item 3c2). It replaces `lmt.live.command.load_stages`, then exits with
`lmt.cli.main(<its arguments>)`. It isn't part of the package.

    python tests/live_fakes.py live --from-file X --jsonl ...

The fake ASR stage commits one word per second of audio, each ending a sentence; the fake diarizer makes everything
speaker 0, final at once. Only tests set these:
- `LMT_FAKE_LIVE_SLOW` (seconds): each fake `flush` sleeps that long, so a signal can land while the workers finish.
- `LMT_FAKE_LIVE_FAIL=asr`: the ASR stage raises on its fifth `feed`.
"""

import os
import sys
import time

import lmt.live.command
from lmt import cli
from lmt.live.asr import AsrUpdate
from lmt.live.diarize import NullDiarizer
from lmt.transcript import Word

RATE = 16000
MODELS = {
    "asr": {"name": "fake-asr", "repository": "test/fake-asr", "revision": "0" * 40},
    "diarization": {"name": "speaker-diarization-3.1", "repository": "pyannote/speaker-diarization-3.1",
                    "revision": "1" * 40},
}


def _slow() -> None:
    time.sleep(float(os.environ.get("LMT_FAKE_LIVE_SLOW", "0")))


class FakeASR:
    def __init__(self, fail: bool):
        self.position = 0
        self.committed = 0
        self.feeds = 0
        self._fail = fail

    def feed(self, samples) -> AsrUpdate:
        self.feeds += 1
        if self._fail and self.feeds == 5:
            raise RuntimeError("fake ASR failure")
        self.position += len(samples)
        words = []
        while (self.committed + 1) * RATE <= self.position:
            k = self.committed
            words.append(Word(k + 0.2, k + 0.6, f"Word{k}.", 0.9))
            self.committed += 1
        return AsrUpdate(words, self.committed * RATE)

    def flush(self) -> AsrUpdate:
        _slow()
        return AsrUpdate([], self.position)


class FakeDiarizer(NullDiarizer):
    def flush(self):
        _slow()
        return super().flush()


def load_stages(args, manifest):
    return FakeASR(os.environ.get("LMT_FAKE_LIVE_FAIL") == "asr"), FakeDiarizer(), MODELS


if __name__ == "__main__":
    lmt.live.command.load_stages = load_stages
    sys.exit(cli.main(sys.argv[1:]))
