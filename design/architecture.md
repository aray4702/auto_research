# Architecture: Local Meeting Transcriber

| Date (PT) | By | Change |
| --- | --- | --- |
| 2026-10-27 | architect, #82 | Reviewer's findings on #125: a delay budget that fits G2-6c's bound, with the diarization latency cut to 1.0 s and added to the stage-2 sweep; a silent device's first 3 s give a warning, not an error, so loopback devices work |
| 2026-10-27 | architect, #82 | Live mode (G2): the sources, streaming ASR, incremental diarization, the join, stopping, what's written, the live model and how it's chosen, and `sounddevice`. G2's tests in CI. Each choice is the recommended option on #82, waiting for the Owner's approval |
| 2026-10-27 | architect, #81 | Reviewer's second review of #89: tier 3 lists only what it leaves out, and the workflows run tiers 1 to 3; the monthly estimate is ≈ 1,850 minutes; the system's own CI minutes are #91; LLVM's OpenMP runtime is listed |
| 2026-10-27 | architect, #81 | Reviewer's findings on #89: CI runs in tiers by the paths a change touches, sized against the 3,000-minute signal; the expected licences outside MIT, BSD and Apache-2.0, and how bundled libraries are listed |
| 2026-10-27 | architect, #81 | First version, for G1. Each choice is the recommended option on #81, waiting for the Owner's approval |

This records how the product is built: its stages, engines, models, toolchain and layout. The public interface (CLI, transcript JSON, results file) is in [`public-api.md`](public-api.md). What each choice is judged by is in [`../spec/acceptance.md`](../spec/acceptance.md), G1. The Owner approves every change to this file. Internal designs, one per phase, go in `design/internal/`.

## Pipeline

```
input file ─► decode ─► audio (16 kHz mono float32)
                          ├─► ASR ──────────► words (text, start, end, probability)
                          └─► diarization ──► speaker turns (start, end, speaker)
                                               │
                    words + turns ─► alignment ─► transcript (speakers, segments, words)
                                                    └─► export: txt, srt, vtt, json
```

- **Decode** (`audio`): PyAV opens the input, which can be WAV, FLAC, MP3, M4A/AAC, or the first audio track of an MP4/MOV. It resamples to 16 kHz mono float32 in memory. This is the only stage that parses untrusted bytes (Q4, Q9). Every failure here becomes an input error (exit 3), and audio with no speech becomes "no speech found" (exit 4).
- **ASR** (`asr`): faster-whisper (CTranslate2) with int8 compute, language fixed to `en` (F9), temperature 0 with no fallback, a fixed beam size, word timestamps on, and its Silero VAD filter on. The VAD stops Whisper inventing text in silence (Q4) and finds where speech starts and ends.
- **Diarization** (`diarize`): `pyannote/speaker-diarization-3.1` on pyannote.audio 3.x, run locally, with no speaker count unless the user gives one (F2). It yields speaker turns, which may overlap.
- **Alignment** (`align`): each word goes to the speaker whose turn overlaps it most. A word that falls in no turn goes to the nearest turn within 1 s, and otherwise to the previous word's speaker. Segments are runs of consecutive words with the same speaker. A segment also breaks at a pause of 1 s or more, at the end of a sentence, and at 30 s. Speakers are numbered by first appearance, `S1`, `S2`, … (shown as "Speaker 1", …). The internal design fixes the details.
- **Export** (`export`): writes the transcript as plain text, SRT, WebVTT and JSON (F3). All four are rendered from the same in-memory transcript, so they can't disagree.
- **Run order:** ASR and diarization both read the decoded audio and are independent. They run concurrently, in two threads: both spend their time in native code that releases the GIL. So the wall-clock time is roughly the slower of the two, which is what makes Q2 (RTF ≤ 0.5) reachable on CPU. If memory (Q3) or determinism (Q7) needs it, the internal design may run them one after the other.
- **Device:** CPU everywhere for ASR, since CTranslate2 has no Metal backend. For diarization, CPU by default. The Mac's GPU (MPS) is used only if the stage-2 investigation shows that it is faster *and* gives identical output on repeated runs (Q7).

**Options considered (#81, question 6):**
- **A. ASR and diarization side by side, joined at the word level** (above). *Recommended.* It is simple, it runs in parallel, ASR gets whole-file context (better WER), and G2 can reuse every stage.
- **B. Diarize first, then transcribe each speaker turn.** Overlapping speech gets cleaner boundaries, but there are hundreds of short ASR calls with little context: worse WER, and slower.
- **C. WhisperX.** It adds wav2vec2 forced alignment for sharper word times, at the cost of a large, separately pinned dependency tree and less control over determinism. The word times faster-whisper gives are enough for the alignment rules above.

## Engines and models

| Stage | Engine | Default model | Source (pinned by revision and SHA-256 in the model manifest) | Licence | Size on disk |
| --- | --- | --- | --- | --- | --- |
| ASR | faster-whisper (MIT) on CTranslate2 (MIT) | `distil-large-v3`, provisional (below) | `Systran/faster-distil-whisper-large-v3` on Hugging Face | MIT | ≈ 1.5 GB |
| VAD | Silero VAD, bundled with faster-whisper | Silero VAD v5 | inside the faster-whisper wheel | MIT | ≈ 2 MB |
| Live ASR (G2) | the same | the live default, provisionally `distil-large-v3` ("Live mode (G2)") | as for ASR | MIT | none more while it's the offline default; ≈ 0.15–0.33 GB for the smaller candidates |
| Diarization | pyannote.audio 3.x (MIT) | `pyannote/speaker-diarization-3.1`, which uses `pyannote/segmentation-3.0` and `pyannote/wespeaker-voxceleb-resnet34-LM` | Hugging Face, gated: a free account that has accepted the models' conditions, and a token (§7) | MIT | ≈ 35 MB |

The default models total about 1.55 GB, under Q3's 2 GB. A separate live default would count towards the same 2 GB.

- **The ASR model is provisional.** A stage-2 investigation measures three candidates on the Mac, on LS full and AMI-ci, through the same pipeline: `small.en` (≈ 0.5 GB), `distil-large-v3` and `large-v3-turbo` (≈ 1.6 GB). The default becomes the most accurate on AMI that meets Q2's RTF ≤ 0.5 over AMI full and Q3's limits. `distil-large-v3` is provisional because it is English-only (F9), trained for long-form audio, and has a 2-layer decoder, which makes it fast on CPU. The pyannote 3.1 model card reports a DER of 18.8 % on AMI headset-mix, scored without a collar and with overlap included. That is the same scoring as Q1, and inside its 25 %.
- **CI uses the default models,** so that CI's subset checks (G1-8c) test what the Mac measures. The model files go into CI's cache, keyed by the model manifest's hash, so they are downloaded once and not on every run.
- **Options considered (#81, questions 7 and 8):**
  - **ASR:** faster-whisper is *recommended*: one engine for Linux CI and the Mac, CPU int8, deterministic, with word timestamps and VAD built in. mlx-whisper is fast on Apple GPUs but doesn't run on Linux, so CI would test a different engine. whisper.cpp (`pywhispercpp`) has Metal on the Mac and CPU on Linux, but its Python binding is less mature. Either alternative could become a later Mac-only speed backend, if Q2 needs one.
  - **Diarization:** `speaker-diarization-3.1` on pyannote.audio 3.x is *recommended*: MIT, and it meets Q1 by its own model card. `speaker-diarization-community-1` on pyannote.audio 4.x is reported to be more accurate, but its model licence (CC BY 4.0) isn't on §7's list, and 4.x's telemetry would need checking against Q8. sherpa-onnx (Apache-2.0) needs no token, but its DER on AMI is unmeasured.

**Model handling (`models`).** The model manifest (`src/lmt/models.toml`) lists each model's repository, revision (a commit hash) and every file with its SHA-256 and size. `lmt models download` fetches exactly those files with `huggingface_hub`, verifies each one, and deletes any file that doesn't match (exit 5, Q9). The cache is `LMT_CACHE_DIR` (default `~/.cache/lmt/models`). Once every file there is verified, the product sets `HF_HUB_OFFLINE=1` before importing any model library, so nothing tries the network (Q8, L4). `lmt transcribe` downloads missing models on first use, saying so on stderr. `--offline` forbids that.

**Determinism (Q7).** Models are pinned by hash, decoding is fixed (above), and the thread counts are fixed for CTranslate2 and torch. Torch also runs with `torch.use_deterministic_algorithms(True)`, and pyannote's clustering is seeded. `lmt eval` compares two runs (G1-5, G1-12).

## Evaluation harness (`lmt.eval`)

- **Dataset manifests** (`src/lmt/eval/manifests/*.toml`) list every file of LS full, AMI full and the CI subset, with its URL and SHA-256. They also give the recipes for the one-hour recording, the reference clip and the two-speaker clip. Sources: LibriSpeech test-clean from openslr.org; AMI Mix-Headset audio and manual annotations v1.6.2 from the AMI corpus site; and the `only_words` RTTMs from `BUTSpeechFIT/AMI-diarization-setup` at a pinned commit. All are CC BY 4.0. Downloads go to `LMT_DATA_DIR` (default `~/.cache/lmt/data`) and are verified by checksum.
- **Scoring:** WER with `jiwer` (Apache-2.0), after Whisper's `EnglishTextNormalizer`. The normalizer is vendored at a pinned openai-whisper commit, with its MIT notice and its `english.json`, which avoids depending on openai-whisper itself. DER with `pyannote.metrics` (MIT). Both follow the spec's "Terms".
- **Systems:** the product run; Whisper alone (the ASR stage's words, scored for WER); and pyannote alone (the diarization stage's turns, scored for DER). Each stage's output is computed once per file and reused.
- **Output:** `results/<set>.json`, and the README table rendered from `results/full.json` (format in `public-api.md`).
- **Eval-only dependencies** are an extra, `lmt[eval]`, so a user's install doesn't carry them.

## Live mode (G2)

G2 reuses decode, the ASR engine, the diarization models, alignment's rules, export and the transcript types. It adds an audio source and streaming forms of ASR and diarization, behind the same plain-data interfaces (audio arrays, lists of words and turns, never files). What it is judged by is in the spec, G2. Each choice below is the recommended option on #82, waiting for the Owner's approval.

```
microphone (sounddevice) ─┐
                          ├─► blocks (16 kHz mono float32) ─┬─► streaming ASR ──────────► committed words
file (--from-file, paced) ┘                                 └─► incremental diarization ─► final speaker activity
                                                                                              │
                                        committed words + speaker activity ─► join ─► events (stdout), <stem>.txt
                                                                                  └─► at stop: txt, srt, vtt, json
```

- **Sources** (`live/source.py`). The **microphone source** opens the chosen input device with `sounddevice` (PortAudio) at the device's own rate and channels, and resamples to 16 kHz mono with decode's resampler. The device's callback only copies blocks into a queue, so a slow stage never makes it drop audio. The **file source** decodes with `audio.decode`, then hands over 0.1 s blocks at real-time pace. Its **lockstep** mode, for tests, waits for both streaming stages before each block (spec, G2 "Terms"). If a device gives exact zeros for its first 3 s, stderr warns that the microphone may be blocked, naming macOS's setting, and capture goes on. macOS gives a denied app silence rather than an error, but a loopback device such as BlackHole also gives exact zeros while nothing plays, as at the start of a call, so silence alone can't be an error. The check is only for a device, and only for its first 3 s.
- **Streaming ASR** (`live/asr.py`). It keeps a buffer of the audio since the last commit, up to 15 s. Every update interval (1.0 s, provisional), it transcribes the buffer with the ASR engine's offline settings (G1: language `en`, temperature 0, a fixed beam, word timestamps), with the last committed words as the prompt. It commits the longest run of new words on which this update and the one before agree: the LocalAgreement-2 policy of Macháček et al. (2023), "Turning Whisper into Real-Time Transcription System". At a pause of 0.5 s or more, found by the Silero VAD, the whole buffer is committed, so the end of a turn doesn't wait for the next update. The buffer is then cut at the last commit. Whisper pads every input to 30 s, so each update costs one full encoder pass, whatever the buffer's length. That makes the update interval and the model's size the trade-off against Q10.
- **Incremental diarization** (`live/diarize.py`). This is the online method of Coria et al. (2021), "Overlap-aware low-latency online speaker diarization based on end-to-end local segmentation", on the same pinned models as G1. Every step (0.5 s), `segmentation-3.0` runs on the latest 5 s window, giving up to three local speakers. Each local speaker active long enough gets a `wespeaker` embedding from its frames. Local speakers are matched to the speakers found so far by cosine distance, two local speakers never taking the same one. A local speaker farther than a threshold from every centroid becomes a new speaker, up to `--max-speakers`. Centroids are updated as each speaker is matched. Speaker activity is final once a frame is a fixed latency old (1.0 s, provisional), and it never changes after that. The thresholds and latency are tuned on AMI's dev meetings, never on its test meetings, and the internal design records them. It is implemented here, on pyannote.audio's model API, rather than taken from the `diart` package (below).
- **Join** (`live/join.py`). A committed word is labelled once speaker activity is final up to its end, by alignment's rules (G1, "Pipeline"). Segments break by the same rules. Each labelled word goes out at once as a `words` event, and a `segment` event closes its segment. So a word's delay is the later of its ASR commit and its diarization latency, plus the compute time.
- **Delay budget** (G2-6c, Q10). A word's stream delay (spec, G2 "Terms") is the later of two waits. *ASR:* a word that ends at t is first in the update at or after t, and is committed at the next update if the two agree. So it waits U to 2U, where U is the update interval, and each disagreement adds U. A pause commits sooner. *Diarization:* activity is final at the first step at which it is the latency L old, so at most L + S, where S is the step. The design keeps max(2U, L + S) ≤ 2.0 s, G2-6c's bound. The update interval and the step are multiples of the 0.1 s block, so updates fall on block boundaries and the block adds nothing. At the provisional U = 1.0 s, L = 1.0 s and S = 0.5 s, that is max(2.0, 1.5) = 2.0 s. The margin is thin: words agreed at the first chance already spread from 1.0 to 2.0 s, so G2-6c holds only if no more than about 5 % of words wait for a third update. If that fails, the first remedy is a shorter U from the sweep below, not a looser bound. On the Mac, Q10's 3.0 s then leaves 1.0 s for compute, from an update's last audio to its event. That compute must also take less than U, or the pipeline falls behind.
- **Threads.** The device callback, the ASR worker and the diarization worker each run in a thread of their own. The join and the writers run in the main thread. A worker that falls behind takes in all the new audio at its next update, so it catches up rather than drifting, and never skips audio. If the pipeline is more than 5 s behind real time, stderr says so.
- **Stopping.** Ctrl-C (SIGINT), SIGTERM, `--duration` and the end of a `--from-file` input all stop the source at once. The workers then finish the audio already captured: ASR commits what's left in its buffer, and diarization makes the rest final. Then the join labels the remaining words and the outputs are written. A second Ctrl-C during that abandons the save (exit 130).
- **What's written** (#82, question 5). Segments are printed to stdout as they're recognised: as text lines, or as JSON events with `--jsonl`. If `txt` is among the formats, `<stem>.txt` gets each segment as it completes, flushed, so a crash keeps what was said. At the stop, every requested format is written from the same transcript as the live stream, `txt` included, so what's saved is what was shown. With `--save-audio`, `<stem>.wav` is written as the audio arrives (16 kHz mono, 16-bit PCM, about 115 MB an hour), so `lmt transcribe` can redo the session at offline quality afterwards. Nothing else is written (Q8).
- **Memory** stays bounded however long the meeting: the ASR buffer is at most 15 s plus one update, the diarization window is 5 s, and there is one centroid per speaker. Only the transcript grows.
- **The live model** (#82, question 3). Streaming makes many encoder passes a minute, so live mode may need a smaller ASR model than the offline default. The manifest has a separate live default, provisionally the same as the offline one. A stage-2 investigation measures, on the Mac, on the Q10 clip and with diarization running: `distil-large-v3`, `distil-small.en` (Systran's CTranslate2 conversion, MIT, ≈ 0.33 GB) and `base.en` (≈ 0.15 GB), each at update intervals of 0.5 and 1.0 s and diarization latencies of 0.5, 1.0 and 1.5 s. An interval of 1.5 s is left out, since 2U = 3.0 s breaks the delay budget. The latency trades against Q10 as the model and the interval do: a longer one gives the labels more context. The default becomes the most accurate on the clip (its live WER) whose p95 delay meets Q10, at the longest latency that still meets it. The pinned values must also keep G2-3 and G2-6c passing in CI, and the default models together must stay within Q3's 2 GB on disk. So `small.en` (≈ 0.48 GB) is left out: with `distil-large-v3` it would come to about 2.04 GB.

**Options considered (#82):**
- **The command (question 6).** *A, recommended:* a subcommand, `lmt live`, that runs in the foreground until Ctrl-C, `--duration` or the end of `--from-file`. *B:* `lmt transcribe --live`, which mixes two option sets that mostly don't apply to each other. *C:* `lmt live start` and `lmt live stop`, with a background process: more to get wrong (a stray process holding the microphone, its state on disk), for no gain over a terminal window.
- **Streaming ASR (question 3).** *A, recommended:* LocalAgreement-2 over a rolling buffer, with the same engine as G1, as above. *B:* cut the audio at pauses found by the VAD, and transcribe each piece once. That is simpler, but a word at the start of a 10 s turn waits about 10 s, which fails Q10 in meetings, where turns are long. *C:* a streaming-native engine, such as sherpa-onnx's streaming Zipformer (Apache-2.0). Its delay is low, but it is a second ASR engine, with a second model to pin and its accuracy on AMI unmeasured.
- **Incremental diarization (question 4).** *A, recommended:* the online method above, implemented here on the pinned pyannote models. *B:* the `diart` package (MIT), which implements that method. It brings its own dependency tree and pins of pyannote.audio, and gives less control over determinism and threads. *C:* re-run G1's offline diarization on everything so far, every few seconds. It is accurate, but its cost grows with the meeting's length, and labels already shown could change.
- **What's saved at the stop (question 5).** *A, recommended:* exactly what was shown live, with `--save-audio` for an offline-quality pass afterwards. *B:* re-run offline diarization on the whole session at the stop, so the saved file gets G1's speaker accuracy. That costs minutes at the end of an hour's meeting, and the saved labels could differ from those shown.
- **Capture.** *Recommended:* `sounddevice` (MIT). Its macOS wheel bundles PortAudio (MIT); on Linux it uses the system's `libportaudio2`, which is imported only when a device is opened, so CI and `lmt transcribe` never need it. The alternatives: PyAV's `avfoundation` input, which depends on PyAV's wheel being built with FFmpeg's device library, and is macOS-only; or PyAudio (MIT), whose macOS install needs PortAudio from Homebrew.
- **Audio from the call itself (question 2).** On a call, the microphone picks up only the room, so remote speakers are captured only through the computer's speakers. *Recommended:* G2 records from the microphone or from any input device the user picks with `--device`. So a loopback device the user has installed works, but none is bundled: BlackHole, the common one, is GPL-3.0. Capturing system audio directly (ScreenCaptureKit, or macOS 14.4's audio taps) would be a later goal.

## Toolchain and layout

- **Python 3.11** (the lowest §7 allows), pinned in `.python-version`.
- **uv** (MIT/Apache-2.0), at a version pinned in `toolchain.toml`, with `pyproject.toml` and a single `uv.lock` for both platforms. On Linux the lock takes torch's CPU-only wheels from the PyTorch CPU index, so CI doesn't download CUDA. On macOS it takes PyPI's wheels. A user's install is one command (`uv tool install`, or `pipx install`, from the repository), and G3 settles packaging for release. *Option (#81, question 11):* pip with hashed requirement files, one per platform, generated by pip-tools. That is more familiar, but there are two locks to keep in step, and no one-command dev setup.
- **Tests:** pytest (MIT). **Lint:** ruff (MIT).
- **Licences beyond MIT, BSD and Apache-2.0 (#81, question 2).** The planned direct dependencies are faster-whisper, pyannote.audio 3.x and huggingface_hub, plus jiwer and pyannote.metrics in the `eval` extra. G2 adds `sounddevice` (MIT), whose macOS wheel bundles PortAudio (MIT), listed in `toolchain.toml` like the other bundled libraries. Their trees go beyond §7's three licences. The list below comes from the packages' published metadata, and stage 1's first item confirms it by locking the tree for both platforms (spec, G1 "Stages").
  - **Other permissive licences:** PSF-2.0 (`typing_extensions`, from torch and huggingface_hub; `aiohappyeyeballs`, from aiohttp; `greenlet`, MIT AND PSF-2.0, from SQLAlchemy); ISC (`shellingham`, from typer); Unlicense (`filelock`); HPND (`pillow`, if matplotlib comes in). Torch's macOS arm64 wheel probably bundles LLVM's OpenMP runtime (`libomp.dylib`), under `Apache-2.0 WITH LLVM-exception`. The exception only adds permissions, and the allow list spells out that SPDX form, so the check doesn't stop on it as unlisted.
  - **File-level copyleft, used unmodified:** MPL-2.0 (`certifi`, from requests; `tqdm`, MPL-2.0 AND MIT, from faster-whisper and huggingface_hub).
  - **LGPL, as bundled shared libraries:** FFmpeg's libraries inside PyAV's wheels, which faster-whisper depends on, and which decode AAC, MP4 and MOV (F1). libsndfile, with the mpg123 and LAME libraries built into it, inside `soundfile`'s wheels, which pyannote.audio depends on. Both bindings are permissively licensed, and MIT code may use dynamically linked LGPL libraries.
  - **GPL-3.0 with the GCC Runtime Library Exception:** `libgomp`, `libgfortran` and `libquadmath` inside the Linux wheels of torch, numpy and scipy. The exception places no conditions on programs that use them.
  - **Intel Simplified Software License:** Intel's OpenMP runtime (`libiomp5`), and possibly MKL, inside CTranslate2's Linux x86_64 wheel. So this is in CI only: the macOS wheel uses Apple's Accelerate.
  - **How the check sees them:** a wheel's metadata gives only its own licence (PyAV's says BSD), so the bundled libraries are listed in `toolchain.toml`, each with its wheel and licence, and the licence check fails on any shared library that isn't listed (spec, G1-15).

```
pyproject.toml, uv.lock, .python-version
src/lmt/
  cli.py            commands and options; maps errors to exit codes
  errors.py         InputError, NoSpeechError, ModelError, UsageError
  pipeline.py       runs the stages, times each one (Q6), builds the transcript
  audio.py          decode
  asr.py            ASR engine wrapper
  diarize.py        diarization engine wrapper
  align.py          words + turns → segments
  transcript.py     the transcript types
  export/           txt.py, srt.py, vtt.py, json.py
  live/             source.py, asr.py, diarize.py, join.py, events.py (G2)
  models.py, models.toml
  schema/transcript.schema.json
  eval/             datasets.py, score.py, normalizer/, report.py, manifests/
tests/              unit tests, fixtures and their recipes, fuzz and network tests
results/            committed results files (the measurements, by commit)
```

`schema/transcript.schema.json` at the repository root is the published copy of the schema (F3). A test keeps it identical to the package's copy. G2's `schema/live-events.schema.json` is published the same way.

## CI (`product.yml`)

All jobs run on `ubuntu-24.04` (Linux x86_64, CPU only, §6; 4 vCPUs on a public repository). The gate needs each one, and each runs in a **tier**, chosen by the paths a change touches, so that CI stays inside the 3,000-minute monthly signal (§6, `system/caps.toml` `[ci]`):

| Tier | Runs when the change touches | Jobs | Estimated minutes per run |
| --- | --- | --- | --- |
| 0 | anything | the gate's `generic` and `gate` jobs; `readme`'s checks that need no install (S1-1, S1-2's heading, S1-5) | 3 |
| 1 | `src/`, `tests/`, `schema/`, `results/`, `pyproject.toml`, `uv.lock`, `toolchain.toml`, `.python-version`, `README.md`, `.github/scripts/` or `.github/workflows/` | tier 0; `readme`'s install (S1-3, S1-4) and `lmt eval --check-readme` (G1-14); `test`: `uv sync --locked`, the licence check, ruff, and the tests that need no model | 10 |
| 2 | tier 1's paths, other than `README.md`, `results/`, `src/lmt/eval/` and `.github/scripts/` | tier 1; `test-models`: the tests that run the models, behind G1-1 to G1-4, G1-6, G1-11 and G1-13 | 22 |
| 3 | anything in `src/lmt/` except `export/txt.py`, `export/srt.py` and `export/vtt.py`; or `pyproject.toml`, `uv.lock`, `toolchain.toml`, `.python-version` or `.github/workflows/` | tier 2; `eval-ci`: `lmt eval --set ci` twice, with the bounds of G1-8c and the comparison of G1-5 | 34 |

- **Tier 3 is defined by what it leaves out,** so a new module starts in the strictest tier. The three exporters it leaves out can't reach `lmt eval`: the harness scores the JSON export (DER's hypothesis, spec "Terms") and the words in memory. So `export/json.py`, `schema/`, `cli.py`, `transcript.py` and `errors.py` all run `eval-ci`. Only the Architect narrows tier 3, here, and only for a path shown not to reach `lmt eval`.
- **The workflows** (`gate.yml` and `product.yml`) run tiers 1 to 3, because they decide which jobs run and what reaches them, `HF_TOKEN` included.
- **The filter** is a step in `product.yml` that compares the change's base and head with git, so it needs no third-party action. If it can't compute the change, it runs every tier. A run started by hand (`workflow_dispatch`) or by the weekly schedule on `main` runs every tier. A job skipped by its tier passes.
- **How the estimates are made.** GitHub bills each job in whole minutes. Each job's setup (checkout, `uv sync` and cache restores) is about 2–3 minutes. `test-models` makes about 8 runs on the 60-second clips and about 10 on a 10-second clip, sharing each (input, options) run between tests. Most of G1-11's end-to-end cases fail at decode, before any model loads. `eval-ci` transcribes 7.5 minutes of audio twice, at an assumed RTF of 0.5 on the runner. The first runs of each job measure these figures, and the Architect re-sizes the tiers here if a job takes more than 1.5 times its estimate.
- **Per month.** A product PR is assumed to take about 3 gate runs (opened, one round of fixes, one update from `main`), plus the run on `main` after it merges. So a tier-3 PR costs about 4 × 34 ≈ 136 minutes, and a tier-1 PR about 40. A tier-2 PR costs about 88. G1's stages 1 and 2 are about 14 items: say 4 at tier 1, 2 at tier 2 and 8 at tier 3 (the CLI and JSON-export items are tier 3). That is about 160 + 180 + 1,090 ≈ 1,430 minutes. Add about 140 a month for the weekly full run. Add about 270 for two builder PRs a month that touch the workflows, such as passing `HF_TOKEN`; `main` has two such commits so far. So the month G1 lands comes to ≈ 1,850 minutes. Without tiers, every one of the system's own PRs would also run the 34-minute tiers: at 26–27 October's pace of about 30 merged PRs a day, that would pass 3,000 minutes in two days.
- **G2's tests** go into `test-models`, and `src/lmt/live/` is in tier 3 under the rule above. Lockstep on the two 60-second clips, the real-time runs of G2-4 (about 60 s and four 20 s stops) and the reruns of G1-11 and G1-13 add an estimated 5 minutes, so tier 2 becomes about 27 and tier 3 about 39 minutes a run. They exist only once G2's items land, after G1's. G2's stage 1 is five items, about four at tier 3 and one (the harness) at tier 1: about 4 × 4 × 39 + 40 ≈ 660 minutes. If it lands in the same month as G1, that month comes to about 2,500 minutes, still under the 3,000-minute signal.
- **The system's own runs** (tier 0) cost about 3 minutes each whatever the product does. At that same pace of about 60 gate runs a day, they come to about 180 minutes a day, so they would reach the signal alone within about 17 days if the pace held. That is the builder's item, #91.

The model and dataset caches are keyed by their manifests' hashes. `HF_TOKEN` reaches the jobs only for the first model download. Passing it needs `gate.yml` to hand secrets to `product.yml`, which is a builder change. The Mac's checks aren't in CI: the Owner runs `lmt eval --set full` before release (#81, question 5). A self-hosted runner on a public repository would run pull-request code on the Owner's Mac.
