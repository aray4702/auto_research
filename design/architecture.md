# Architecture: Local Meeting Transcriber

| Date (PT) | By | Change |
| --- | --- | --- |
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

## Toolchain and layout

- **Python 3.11** (the lowest §7 allows), pinned in `.python-version`.
- **uv** (MIT/Apache-2.0), at a version pinned in `toolchain.toml`, with `pyproject.toml` and a single `uv.lock` for both platforms. On Linux the lock takes torch's CPU-only wheels from the PyTorch CPU index, so CI doesn't download CUDA. On macOS it takes PyPI's wheels. A user's install is one command (`uv tool install`, or `pipx install`, from the repository), and G3 settles packaging for release. *Option (#81, question 11):* pip with hashed requirement files, one per platform, generated by pip-tools. That is more familiar, but there are two locks to keep in step, and no one-command dev setup.
- **Tests:** pytest (MIT). **Lint:** ruff (MIT).
- **Decoding libraries and their licences (#81, question 2):** PyAV's wheels bundle FFmpeg's libraries under LGPL 2.1. faster-whisper itself depends on PyAV. pyannote.audio reads audio through `soundfile`, whose wheels bundle libsndfile (also LGPL 2.1). Both are dynamically linked shared libraries inside permissively licensed bindings, which MIT code may use. There's no permissive way to decode AAC and MP4 (F1) without them. They need the Owner's acceptance as exceptions to §7.

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
  models.py, models.toml
  schema/transcript.schema.json
  eval/             datasets.py, score.py, normalizer/, report.py, manifests/
tests/              unit tests, fixtures and their recipes, fuzz and network tests
results/            committed results files (the measurements, by commit)
```

`schema/transcript.schema.json` at the repository root is the published copy of the schema (F3). A test keeps it identical to the package's copy.

## CI (`product.yml`)

All jobs run on `ubuntu-24.04` (Linux x86_64, CPU only, §6), and the gate needs each one:
- **`readme`:** S1-1 to S1-5.
- **`test`:** `uv sync --locked`, the licence check, ruff, and pytest. That includes the tests behind G1-1 to G1-4, G1-6, G1-7, G1-11 and G1-13.
- **`eval-ci`:** `lmt eval --set ci` twice, with the bounds of G1-8c and the comparison of G1-5, plus `lmt eval --check-readme`.

The model and dataset caches are keyed by their manifests' hashes. `HF_TOKEN` reaches the jobs only for the first model download. Passing it needs `gate.yml` to hand secrets to `product.yml`, which is a builder change. The Mac's checks aren't in CI: the Owner runs `lmt eval --set full` before release (#81, question 5). A self-hosted runner on a public repository would run pull-request code on the Owner's Mac.
