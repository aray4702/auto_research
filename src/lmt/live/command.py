"""`lmt live` (design/internal/g2-stage1.md §3b, item 3b3, and §3c, item 3c2): the checks before capture, the
stages, the session and the outputs, with the signals that stop it. Every error that can be found before capture is
found before it, and nothing is written until capture starts (G2-7). Positions are samples at 16 kHz.
"""

import dataclasses
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

from lmt import export, models
from lmt.live import mic, output
from lmt.live.events import EventWriter
from lmt.live.session import Sinks, run_lockstep, run_threaded
from lmt.live.source import FileSource

LOCKSTEP = False  # set only by tests/live_lockstep.py: lockstep, not reachable from the CLI
RATE = 16000


def _model_info(model) -> dict:
    return {"name": model.name, "repository": model.repository, "revision": model.revision}


def load_stages(args, manifest):
    """Steps 5 and 6: load the engine (G4-2: before any audio is captured), ensure and load the diarization models,
    then build the stages, with the settings read through their modules. Returns the ASR stage, the diarizer and the
    models' field of the transcript and `start`. Tests replace it."""
    from lmt import diarize, engines
    from lmt.live import asr as live_asr
    from lmt.live import diarize as live_diarize

    engine = engines.load(args.engine, offline=args.offline)
    needed = [] if args.no_diarize else [manifest.get(diarize.SEGMENTATION), manifest.get(diarize.EMBEDDING)]
    dirs = models.ensure(needed, offline=args.offline)

    rate = getattr(engine, "rate", RATE)  # Kyutai's 24 kHz: the stream takes it from the source (#603)
    stream = engine.stream(rate) if rate != RATE else engine.stream()
    stage = (live_asr.StreamingASR(stream, engine.word_gap) if rate == RATE
             else live_asr.StreamingASR(stream, engine.word_gap, rate))
    if args.no_diarize:
        diarizer, diarization = live_diarize.NullDiarizer(), None
    else:
        diarize.scratch_matplotlib()
        config = live_diarize.DEFAULT_DIAR_CONFIG
        if args.max_speakers is not None:
            config = dataclasses.replace(config, max_speakers=args.max_speakers)
        with diarize._quiet():  # the library warnings, as diarize.load keeps them off stderr
            segmentation = diarize.load_segmentation(Path(dirs[diarize.SEGMENTATION]))
            embedding = diarize.load_embedding(Path(dirs[diarize.EMBEDDING]))
        diarizer = live_diarize.OnlineDiarizer(segmentation, embedding, config)
        diarization = _model_info(manifest.get(diarize.DIARIZATION))
    return stage, diarizer, {"asr": dict(engine.info), "diarization": diarization}


def _list_devices() -> int:
    devices = mic.input_devices()
    if not devices:
        print("No input devices.")
    for d in devices:
        print(f"{d.index}{'*' if d.default else ''}\t{d.name}\t{d.channels} ch\t{d.rate} Hz")
    return 0


class _Capture:
    """The source, with capture started before the session: the stream opened (a device) or the first block
    taken (a file), so a device that fails to open is an error before anything is written, and the outputs that
    grow are created at that moment, not before."""

    def __init__(self, source):
        self._source = source
        self._blocks = source.blocks()
        self._first = next(self._blocks, None)

    @property
    def position(self) -> int:
        return self._source.position

    @property
    def reason(self) -> str:
        return self._source.reason

    def blocks(self):
        if self._first is not None:
            first, self._first = self._first, None
            yield first
            yield from self._blocks


class _Abandon(threading.Event):
    """`save`'s `abandon`, which also records that `save` has checked it: after that check its renames start, and a
    second SIGINT can no longer stop them."""

    def __init__(self):
        super().__init__()
        self.checked = False

    def is_set(self) -> bool:
        # The event is read before `checked` is set: a second SIGINT between the two then raises rather than only
        # setting an event already read as clear, which would leave `save` abandoned and the command exiting 0.
        value = super().is_set()
        self.checked = True
        return value


class _Signals:
    """The first SIGINT, or SIGTERM, stops the source: capture ends, the workers finish the captured audio and the
    outputs are written. A second SIGINT raises `KeyboardInterrupt` (130), unless `save` has started its renames,
    when it only sets `abandon`. Further SIGTERMs are ignored. The previous handlers come back on `restore`."""

    def __init__(self, source, abandon: _Abandon):
        self._source = source
        self._abandon = abandon
        self._stopped = False
        self._interrupted = False
        self._previous = {sig: signal.signal(sig, self._handle) for sig in (signal.SIGINT, signal.SIGTERM)}

    def _handle(self, signum, frame):
        if not self._stopped:
            self._stopped = True
            self._source.stop()
        elif signum == signal.SIGINT and not self._interrupted:
            self._interrupted = True  # once: what follows, the cleanup among it, isn't interrupted again
            self._abandon.set()
            if not self._abandon.checked:
                raise KeyboardInterrupt

    def restore(self) -> None:
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)


def _print_stats(stats, source) -> None:
    """-v: each stage's count, mean and maximum seconds, the largest `behind`, and a device's input overflows."""
    print(f"lmt: asr {stats.asr.count} updates, mean {stats.asr.mean:.3f} s, max {stats.asr.max:.3f} s",
          file=sys.stderr)
    print(f"lmt: diarize {stats.diarization.count} steps, mean {stats.diarization.mean:.3f} s, "
          f"max {stats.diarization.max:.3f} s", file=sys.stderr)
    print(f"lmt: behind max {stats.behind:.1f} s", file=sys.stderr)
    if isinstance(source, mic.MicSource):
        print(f"lmt: input overflows {source.overflows}", file=sys.stderr)


def cli_live(args) -> int:
    if args.list_devices:  # step 1
        return _list_devices()

    stem = args.name or "live-" + datetime.now().strftime("%Y%m%d-%H%M%S")  # step 2
    out_dir = Path(args.output_dir)
    export.check(out_dir, stem, args.formats + ["wav"] * args.save_audio, overwrite=args.overwrite)  # step 3

    limit = None if args.duration is None else round(args.duration * RATE)  # step 4
    if args.from_file is not None:
        source = FileSource(args.from_file, paced=not LOCKSTEP, limit=limit)
        source_info = {"file": source.name, "live": True}
    else:
        device = mic.resolve(args.device)
        source = mic.MicSource(device, limit=limit)
        source_info = {"file": stem, "live": True, "device": device.name}

    manifest = models.load_manifest()  # steps 5 and 6
    asr, diarizer, models_info = load_stages(args, manifest)
    if hasattr(asr, "attach"):
        asr.attach(source)  # before capture starts: the ASR side's audio at the engine's rate (#603)

    abandon = _Abandon()
    signals = _Signals(source, abandon)
    try:
        capture = _Capture(source)
        text = output.GrowingText(out_dir / f"{stem}.txt") if "txt" in args.formats else None
        wav = output.WavWriter(out_dir / f"{stem}.wav") if args.save_audio else None
        try:
            sinks = Sinks(on_block=wav.write if wav else lambda b: None,
                          on_segment=text.append if text else lambda s, l: None)
            writer = EventWriter(sys.stdout, jsonl=args.jsonl, clock_t=lambda: source.position)
            run = run_lockstep if LOCKSTEP else run_threaded
            result = run(capture, asr, diarizer, writer, sinks, source_info=source_info, models=models_info)
        finally:  # kept as written so far, however the session ends (public-api.md, "What a failed session keeps")
            source.stop()  # after a failure, the capture thread takes no more blocks
            for f in (text, wav):
                if f is not None:
                    f.close()
        transcript = output.live_transcript(result, source_info, models_info)
        output.save(transcript, out_dir, stem, args.formats, abandon=abandon)
        if args.verbose:
            _print_stats(result.stats, source)
        return 0
    finally:
        signals.restore()
