"""`lmt demo --sample`'s cache: the sample meeting and its transcript (g3-stage1.md §2b).

The audio is `lmt.eval.datasets.sample_meeting`'s, and its transcript is cached at
`<LMT_DATA_DIR>/sample/<key>/ES2004a.json`, keyed by the audio's SHA-256 and the default models' revisions.
The key comes from the manifests alone, so a hit loads no model, and `lmt.pipeline` (torch) is imported on
a miss only.
"""

import hashlib
import sys
from pathlib import Path

from lmt import export, transcript
from lmt.errors import InputError
from lmt.eval import datasets
from lmt.models import Model, default_models, load_manifest

STEM = "ES2004a"


def prepare(*, offline: bool) -> tuple[Path, Path]:
    """The sample's (transcript, audio) paths in `LMT_DATA_DIR`, made on the first run."""
    try:
        audio = datasets.sample_meeting(offline=True)
    except InputError:
        if offline:
            raise
        print("lmt: downloading the sample meeting (AMI ES2004a, CC BY 4.0)", file=sys.stderr)
        audio = datasets.sample_meeting(offline=False)
    audio_sha256 = datasets.load_manifests(datasets.MANIFEST_DIR).datasets[datasets.SAMPLE_MEETING].derived_sha256
    key = cache_key(audio_sha256, default_models(load_manifest()))
    directory = datasets.data_dir() / "sample" / key
    path = directory / f"{STEM}.json"
    try:
        transcript.load(path)
        return path, audio
    except InputError:  # missing, or not a transcript: made again
        pass
    from lmt import pipeline  # a miss only, so a hit never loads torch

    print("lmt: transcribing the sample meeting once; later runs use the cache", file=sys.stderr)
    t = pipeline.transcribe(audio, offline=offline)
    directory.mkdir(parents=True, exist_ok=True)
    export.write(t, directory, STEM, ["json"], overwrite=True)
    return path, audio


def cache_key(audio_sha256: str, models: list[Model]) -> str:
    """The first 16 hex characters of the SHA-256 of the audio's SHA-256 and each model's repository@revision."""
    text = f"{audio_sha256}\n" + "\n".join(f"{m.repository}@{m.revision}" for m in models)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
