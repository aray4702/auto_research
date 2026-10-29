"""`lmt transcribe`: decode, ASR and diarization, alignment and the exports (design/internal/g1-slice.md, S6;
design/architecture.md, "Pipeline").

`cli_transcribe` checks everything it can before loading any model. Every error is an `LmtError`, which
`cli.main` prints as one line with its exit code. G3's `transcribe(path, offline=...)` is this module's.
"""

import concurrent.futures
import sys
import time
from pathlib import Path

import lmt
from lmt import align, asr, audio, export, models
from lmt import diarize as diarizer  # `transcribe`'s `diarize` is the on/off switch
from lmt.errors import NoSpeechError

# ASR and diarization side by side, as the architecture's "Run order" says. False runs ASR, then diarization.
PARALLEL = True


def _timed(timings, stage, work):
    start = time.perf_counter()
    try:
        return work()
    finally:
        timings[stage] = time.perf_counter() - start


def _model_info(model) -> dict:
    return {"name": model.name, "repository": model.repository, "revision": model.revision}


def transcribe(path, *, model=None, offline=False, diarize=True, num_speakers=None, min_speakers=None,
               max_speakers=None, timings=None):
    """The transcript of the recording at `path`, with the default models, or `model` for ASR. `timings`,
    if given, gets each stage's wall time in seconds."""
    timings = {} if timings is None else timings
    manifest = models.load_manifest()
    needed = models.default_models(manifest, asr=model)
    if not diarize:
        needed = needed[:1]
    # Decode first, so a bad input exits 3 without hashing or downloading a model (g1-stage2.md, P1).
    samples = _timed(timings, "decode", lambda: audio.decode(path))
    dirs = _timed(timings, "models", lambda: models.ensure(needed, offline=offline))

    asr_model = needed[0]

    def run_asr():
        return asr.load(dirs[asr_model.name]).transcribe(samples)

    def run_diarize():
        return diarizer.load(dirs).diarize(samples, num_speakers=num_speakers, min_speakers=min_speakers,
                                           max_speakers=max_speakers)

    turns = None
    if diarize and PARALLEL:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            asr_job = pool.submit(_timed, timings, "asr", run_asr)
            diarize_job = pool.submit(_timed, timings, "diarize", run_diarize)
            words = asr_job.result()
            turns = diarize_job.result()
    else:
        words = _timed(timings, "asr", run_asr)
        if diarize:
            turns = _timed(timings, "diarize", run_diarize)

    if not words:
        raise NoSpeechError(Path(path).name, "no speech found")
    diarization = manifest.get(diarizer.DIARIZATION) if diarize else None
    info = {"asr": _model_info(asr_model), "diarization": _model_info(diarization) if diarization else None}
    return _timed(timings, "align", lambda: align.build(
        words, turns or None, duration_samples=len(samples), source_file=Path(path).name, models=info,
        lmt_version=lmt.__version__))


def cli_transcribe(args) -> int:
    manifest = models.load_manifest()
    models.default_models(manifest, asr=args.model)  # an unknown --model is exit 2, before any work
    out_dir, stem = Path(args.output_dir), Path(args.input).stem
    export.check(out_dir, stem, args.formats, overwrite=args.overwrite)

    timings = {}
    transcript = transcribe(args.input, model=args.model, offline=args.offline, diarize=not args.no_diarize,
                            num_speakers=args.num_speakers, min_speakers=args.min_speakers,
                            max_speakers=args.max_speakers, timings=timings)
    paths = _timed(timings, "export", lambda: export.write(transcript, out_dir, stem, args.formats,
                                                            overwrite=args.overwrite))
    for path in paths:
        print(path)
    if args.verbose:
        for stage in ("decode", "models", "asr", "diarize", "align", "export"):
            if stage in timings:
                print(f"lmt: {stage} {timings[stage]:.2f} s", file=sys.stderr)
    return 0
