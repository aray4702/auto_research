# Architecture: Local Meeting Transcriber

| Date (PT) | By | Change |
| --- | --- | --- |
| 2026-10-27 | architect, #83 | G3: speaker names, the demo server and page, packaging and release; the `browser` and `install-full` jobs; `rename.py` and `demo/` leave tier 3. Each choice is the recommended option on #83, waiting for the Owner's approval |
| 2026-10-27 | architect, #81 | Reviewer's second review of #89: tier 3 lists only what it leaves out, and the workflows run tiers 1 to 3; the monthly estimate is ≈ 1,850 minutes; the system's own CI minutes are #91; LLVM's OpenMP runtime is listed |
| 2026-10-27 | architect, #81 | Reviewer's findings on #89: CI runs in tiers by the paths a change touches, sized against the 3,000-minute signal; the expected licences outside MIT, BSD and Apache-2.0, and how bundled libraries are listed |
| 2026-10-27 | architect, #81 | First version, for G1. Each choice is the recommended option on #81, waiting for the Owner's approval |

This records how the product is built: its stages, engines, models, toolchain and layout. The public interface (CLI, transcript JSON, results file) is in [`public-api.md`](public-api.md). What each choice is judged by is in [`../spec/acceptance.md`](../spec/acceptance.md), G1 and G3. The Owner approves every change to this file. Internal designs, one per phase, go in `design/internal/`.

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
| Diarization | pyannote.audio 3.x (MIT) | `pyannote/speaker-diarization-3.1`, which uses `pyannote/segmentation-3.0` and `pyannote/wespeaker-voxceleb-resnet34-LM` | Hugging Face, gated: a free account that has accepted the models' conditions, and a token (§7) | MIT | ≈ 35 MB |

The default models total about 1.55 GB, under Q3's 2 GB.

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

## Live mode (G2) reuse

G2 reuses decode's resampler, the ASR engine and model, alignment, export and the transcript types. It adds a microphone source, and streaming forms of two stages behind the same interfaces:
- **ASR:** VAD-chunked windows with a short overlap; each window's words are committed once they are stable.
- **Diarization:** the same segmentation and embedding models, with incremental clustering against the speakers found so far.

So the stage interfaces take and return plain data (audio arrays, lists of words and turns), never files. G2's Definition settles the streaming interfaces themselves.

## Speaker names (G3, F7)

- **`rename`** (`rename.py`) reads a transcript JSON, validates it against the schema, replaces labels, and hands the transcript to `export`. It never re-runs a model, so it takes well under a second.
- **One rendering path.** Times are rounded to the millisecond once, when alignment builds the transcript. Every exporter renders from those rounded values, and the JSON stores them. So a transcript read back from its JSON renders byte-identical files (G3-1), and `rename` can't drift from `transcribe`.
- **Escaping lives in the exporters,** so each format's rules are in one place. WebVTT escapes `&`, `<` and `>` in cue text and puts the label both in the voice tag and as visible text. SRT needs no escaping, because names can't hold `<`, `>`, `{` or `}`, the characters some players read as markup.
- **Options considered (#83, question 5):**
  - **A. `lmt rename TRANSCRIPT S1=Alice …`,** which rewrites the JSON and re-exports in place. *Recommended:* it is U4 in one command, and with no names it lists the speakers, so you can tell who is who.
  - **B. `lmt export TRANSCRIPT --name S1=Alice`,** a general re-export command. It is more general, but two steps' worth of options for one task, and nothing else needs a re-export yet.
  - **C. Renaming in the demo page,** saved back to the files. That needs the server to write files, which the demo otherwise never does (Q8). It could come later, through the same `rename` code.

## Demo (G3, F8)

```
lmt demo ─► decode (audio.py) ─► 16 kHz mono WAV, in memory
        └─► transcript JSON, validated
             └─► local HTTP server (127.0.0.1, random token) ─► page: transcript list + <audio>
```

- **Server** (`demo/server.py`): Python's standard `http.server.ThreadingHTTPServer`, bound to `127.0.0.1`, on the given port or one the system picks. Every route sits under a random 128-bit token from `secrets`. A request without it gets 404, and one whose `Host` header isn't `127.0.0.1:<port>` or `localhost:<port>` gets 403. That stops other web pages reaching it through DNS rebinding. Only GET and HEAD are served. Responses carry `Content-Security-Policy: default-src 'self'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.
- **Audio:** decode turns the recording into 16 kHz mono, which is served as 16-bit PCM WAV from memory, with byte ranges. Every browser plays WAV, whatever the input format (MOV and FLAC differ between browsers), and seeking in PCM is exact, where seeking in a variable-bit-rate MP3 isn't. Two hours come to about 230 MB of memory, within Q3. Nothing is written to disk (Q8). The cost is speech-band sound, which is enough to check a transcript.
- **Page** (`demo/static/`): one HTML file, one script and one stylesheet, plain JavaScript with no build step and no third-party code, shipped in the wheel. It fetches the transcript JSON and builds the list. Each segment is a `button` with its time, label and text, so the keyboard reaches it. Clicking sets the audio's `currentTime` to the segment's `start` and plays. As the audio plays, the current segment is marked `aria-current="true"` and scrolled into view. The speakers are listed with their labels and speaking time. All text goes in through `textContent`, never `innerHTML`, so a transcript can't inject markup (Q9). The page also loads the WebVTT as a `<track>`, which the browser test uses to check Chrome's parse (G3-2).
- **`--sample`** fetches the sample meeting through the dataset manifest's cache in `LMT_DATA_DIR`. It transcribes it once with the default models, and keeps the transcript there, keyed by the audio's SHA-256 and the model revisions. A later run with the same models serves the cached transcript, with no model loaded.
- **Options considered (#83, question 8):**
  - **A. The standard library's HTTP server and a plain page** (above). *Recommended:* no new dependency, nothing to lock or vet, works offline, and small enough to review whole.
  - **B. Gradio or Streamlit.** Quick to build, but each brings a large dependency tree, and both collect usage statistics unless turned off, against Q8.
  - **C. FastAPI and uvicorn.** Two dependencies for what the standard library already does here.
  - **D. A single HTML file, written beside the transcript, with no server.** F8 asks for a served page. A file opened from disk can't fetch the JSON in Chrome, and embedding the audio makes a large file, which is a write Q8 doesn't expect.
- **The browser test** uses Selenium WebDriver (Apache-2.0, in the `dev` dependency group) to drive headless Chrome, with the Chrome and `chromedriver` preinstalled on the `ubuntu-24.04` image. So no browser is downloaded or locked. Its dependencies (`urllib3`, `trio`, `websocket-client`, `certifi` and the like) fall in the licence classes the Owner accepted (§7), and the item that adds it posts the inventory. Playwright was the alternative. It downloads its own Chromium and bundles Node.js, whose licences go beyond §7's list. The same script takes `docs/demo.png` (G3-9), in Chrome at a fixed window size, on the sample meeting.

## Packaging and release (G3, Q5)

- **The install** (#83, question 9) is `uv tool install git+https://github.com/aray4702/auto_research@v1.0.0`. `uv tool install` doesn't read `uv.lock`, so `pyproject.toml` pins every package that shapes the output to the lock's exact version: `faster-whisper`, `ctranslate2`, `pyannote.audio`, `torch`, `torchaudio`, `av`, `numpy` and `huggingface-hub`. A test keeps those pins equal to the lock. The rest of the tree can move within the pins' constraints, and G3-7's licence check runs on what actually installed. **Linux's CPU-only torch** comes from an index named in `pyproject.toml`'s `[tool.uv]` settings. The packaging item's first step checks whether a git install reads them. If it doesn't, a Linux user gets PyPI's torch, which also runs on CPU but is larger. G3-7 compares versions without their local label (`+cpu`) either way, and the item reports which one happened.
- **Options considered (#83, question 9):**
  - **A. A git install pinned to the tag** (above). *Recommended:* one command, with nothing to publish beyond GitHub.
  - **B. PyPI,** for `uv tool install local-meeting-transcriber` or `pipx install local-meeting-transcriber`. It is shorter and more familiar, but needs your PyPI account and trusted publishing, and publishes the name. It can follow in a minor release.
  - **C. A `curl … | sh` script** that installs `uv` and then the tool. One line even on a clean account, but it asks the reader to run a script unseen.
- **Hugging Face token.** pyannote's models are gated, so a new user needs a free Hugging Face account, must accept the models' conditions, and must set `HF_TOKEN` once, for `lmt models download`. The README says so in `## Install` (#83, question 11).
- **The release workflow** (`.github/workflows/release.yml`, a builder file) runs by hand on a commit of `main`. It checks that the commit's gate run passed and that `pyproject.toml`'s version matches the tag it is given. Then it runs `uv build`, writes `SHA256SUMS`, installs the wheel in a clean environment as G3-7 does, and creates a **draft** release with the notes rendered from `results/full.json` by the README's renderer. The Owner publishes the draft, which creates the tag. It needs `contents: write`, only in that workflow.
- **`check_release.py`** (`.github/scripts/`) reads a release and its tag through the GitHub API, read-only, and checks G3-10.

## Toolchain and layout

- **Python 3.11** (the lowest §7 allows), pinned in `.python-version`.
- **uv** (MIT/Apache-2.0), at a version pinned in `toolchain.toml`, with `pyproject.toml` and a single `uv.lock` for both platforms. On Linux the lock takes torch's CPU-only wheels from the PyTorch CPU index, so CI doesn't download CUDA. On macOS it takes PyPI's wheels. A user's install is one command (`uv tool install`, or `pipx install`, from the repository), and G3 settles packaging for release ("Packaging and release (G3)"). *Option (#81, question 11):* pip with hashed requirement files, one per platform, generated by pip-tools. That is more familiar, but there are two locks to keep in step, and no one-command dev setup.
- **Tests:** pytest (MIT). **Lint:** ruff (MIT).
- **Licences beyond MIT, BSD and Apache-2.0 (#81, question 2).** The planned direct dependencies are faster-whisper, pyannote.audio 3.x and huggingface_hub, plus jiwer and pyannote.metrics in the `eval` extra. Their trees go beyond §7's three licences. The list below comes from the packages' published metadata, and stage 1's first item confirms it by locking the tree for both platforms (spec, G1 "Stages").
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
  rename.py         speaker names (G3)
  demo/             server.py, static/ (the page), sample.py (G3)
  models.py, models.toml
  schema/transcript.schema.json
  eval/             datasets.py, score.py, normalizer/, report.py, manifests/
tests/              unit tests, fixtures and their recipes, fuzz and network tests
results/            committed results files (the measurements, by commit)
docs/demo.png       the README's demo image (G3)
NOTICE              attributions: models, datasets, the sample meeting
```

`schema/transcript.schema.json` at the repository root is the published copy of the schema (F3). A test keeps it identical to the package's copy.

## CI (`product.yml`)

All jobs run on `ubuntu-24.04` (Linux x86_64, CPU only, §6; 4 vCPUs on a public repository). The gate needs each one, and each runs in a **tier**, chosen by the paths a change touches, so that CI stays inside the 3,000-minute monthly signal (§6, `system/caps.toml` `[ci]`):

| Tier | Runs when the change touches | Jobs | Estimated minutes per run |
| --- | --- | --- | --- |
| 0 | anything | the gate's `generic` and `gate` jobs; `readme`'s checks that need no install (S1-1, S1-2's heading, S1-5) | 3 |
| 1 | `src/`, `tests/`, `schema/`, `results/`, `pyproject.toml`, `uv.lock`, `toolchain.toml`, `.python-version`, `README.md`, `.github/scripts/` or `.github/workflows/` | tier 0; `readme`'s install (S1-3, S1-4) and `lmt eval --check-readme` (G1-14); `test`: `uv sync --locked`, the licence check, ruff, and the tests that need no model | 10 |
| 2 | tier 1's paths, other than `README.md`, `results/`, `src/lmt/eval/` and `.github/scripts/` | tier 1; `test-models`: the tests that run the models, behind G1-1 to G1-4, G1-6, G1-11 and G1-13; `browser`: the browser test (G3-2, G3-5), on fixtures, with no model | 22, plus 4 for `browser` |
| 3 | anything in `src/lmt/` except `export/txt.py`, `export/srt.py`, `export/vtt.py`, `rename.py` and `demo/`; or `pyproject.toml`, `uv.lock`, `toolchain.toml`, `.python-version` or `.github/workflows/` | tier 2; `eval-ci`: `lmt eval --set ci` twice, with the bounds of G1-8c and the comparison of G1-5 | 34, plus 4 for `browser` |

- **Tier 3 is defined by what it leaves out,** so a new module starts in the strictest tier. The three exporters it leaves out can't reach `lmt eval`: the harness scores the JSON export (DER's hypothesis, spec "Terms") and the words in memory. So `export/json.py`, `schema/`, `cli.py`, `transcript.py` and `errors.py` all run `eval-ci`. `rename.py` and `demo/` (G3) can't reach it either: `lmt eval` imports neither, and a test in the `test` job asserts that no module under `lmt.eval` imports them, directly or through another module. Only the Architect narrows tier 3, here, and only for a path shown not to reach `lmt eval`.
- **G3's install checks.** The `test` job also makes G3-7's install on Linux, with no models: `uv tool install` from the checkout into a scratch directory, `lmt --version`, the version comparison and the licence check, for about 2 more minutes. `install-full` (G3-6 to G3-8) runs on `ubuntu-24.04` and `macos-15` with no cache, so each downloads the default models, about 1.5 GB. It runs only in a run started by hand and in the release workflow, at about 15 minutes per runner. A macOS runner is a standard GitHub-hosted runner, free on a public repository, and is the only CI that runs the Mac's wheels (#83, question 10).
- **The workflows** (`gate.yml` and `product.yml`) run tiers 1 to 3, because they decide which jobs run and what reaches them, `HF_TOKEN` included.
- **The filter** is a step in `product.yml` that compares the change's base and head with git, so it needs no third-party action. If it can't compute the change, it runs every tier. A run started by hand (`workflow_dispatch`) or by the weekly schedule on `main` runs every tier. A job skipped by its tier passes.
- **How the estimates are made.** GitHub bills each job in whole minutes. Each job's setup (checkout, `uv sync` and cache restores) is about 2–3 minutes. `test-models` makes about 8 runs on the 60-second clips and about 10 on a 10-second clip, sharing each (input, options) run between tests. Most of G1-11's end-to-end cases fail at decode, before any model loads. `eval-ci` transcribes 7.5 minutes of audio twice, at an assumed RTF of 0.5 on the runner. The first runs of each job measure these figures, and the Architect re-sizes the tiers here if a job takes more than 1.5 times its estimate.
- **Per month.** A product PR is assumed to take about 3 gate runs (opened, one round of fixes, one update from `main`), plus the run on `main` after it merges. So a tier-3 PR costs about 4 × 34 ≈ 136 minutes, and a tier-1 PR about 40. A tier-2 PR costs about 88. G1's stages 1 and 2 are about 14 items: say 4 at tier 1, 2 at tier 2 and 8 at tier 3 (the CLI and JSON-export items are tier 3). That is about 160 + 180 + 1,090 ≈ 1,430 minutes. Add about 140 a month for the weekly full run. Add about 270 for two builder PRs a month that touch the workflows, such as passing `HF_TOKEN`; `main` has two such commits so far. So the month G1 lands comes to ≈ 1,850 minutes. Without tiers, every one of the system's own PRs would also run the 34-minute tiers: at 26–27 October's pace of about 30 merged PRs a day, that would pass 3,000 minutes in two days.
- **G3's cost.** Stage 1 is about five product PRs. `rename` and the CLI wiring for `demo` touch `cli.py`, so those two are tier 3 (2 × 4 × 38 ≈ 300 minutes). The browser test is tier 2 (4 × 26 ≈ 100), and packaging touches `pyproject.toml`, so it is tier 3 (≈ 150). The builder's `install-full` and release workflow touch `.github/workflows/` (≈ 150). Stage 2's README and image PR is tier 1 (≈ 50). Add about three `install-full` runs by hand or at release, at 30 minutes each (≈ 90). That comes to about 850 minutes for G3's own work. Once they land, the `test` job's install adds about 2 minutes to every run from tier 1 up, and `browser` 4 more from tier 2 up, for G1's and G2's PRs too. With G1's ≈ 1,850 and G2's, the three goals would pass 3,000 minutes if they all landed in one calendar month. They straddle 31 October, so the split decides. The warning at 2,400 minutes (`system/caps.toml`) comes first, and the system then asks the Owner.
- **The system's own runs** (tier 0) cost about 3 minutes each whatever the product does. At that same pace of about 60 gate runs a day, they come to about 180 minutes a day, so they would reach the signal alone within about 17 days if the pace held. That is the builder's item, #91.

The model and dataset caches are keyed by their manifests' hashes. `HF_TOKEN` reaches the jobs only for the first model download. Passing it needs `gate.yml` to hand secrets to `product.yml`, which is a builder change. The Mac's checks aren't in CI: the Owner runs `lmt eval --set full` before release (#81, question 5). A self-hosted runner on a public repository would run pull-request code on the Owner's Mac.
