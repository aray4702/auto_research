"""`lmt demo --sample`'s fakes in a subprocess (design/internal/g3-stage1.md, 2b2's test).

Put this directory on PYTHONPATH and set LMT_TEST_TRANSCRIBE_LOG to a file: `datasets.sample_meeting` then
gives a copy of the two-speaker clip in LMT_DATA_DIR, and `pipeline.transcribe` the fixture transcript,
appending one line, the audio's path, to that file per call. Without LMT_TEST_TRANSCRIBE_LOG it does nothing.
"""

import os
import shutil
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent
CLIP = FIXTURES / "two_speakers.wav"
TRANSCRIPT = FIXTURES / "two_speakers.transcript.json"


def _install(log_path):
    from lmt import pipeline, transcript
    from lmt.eval import datasets

    def sample_meeting(*, offline=False):
        path = datasets.data_dir() / "derived" / datasets.SAMPLE_MEETING / "ES2004a.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CLIP, path)
        return path

    def transcribe(path, *, offline=False):
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"{path}\n")
        return transcript.load(TRANSCRIPT)

    datasets.sample_meeting = sample_meeting
    pipeline.transcribe = transcribe


if os.environ.get("LMT_TEST_TRANSCRIBE_LOG"):
    _install(os.environ["LMT_TEST_TRANSCRIBE_LOG"])
