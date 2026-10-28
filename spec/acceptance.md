# Acceptance spec

| Date (PT) | By | Change |
| --- | --- | --- |
| 2026-10-27 | architect, #81 | G1's done-check, G1-1 to G1-15. S1-1 to S1-4 revised now that §1, §6 and §7 are filled in. Rests on the recommended answers on #81 |
| 2026-10-26 | architect, #20 | S1-3 and S1-4: the README's `sh` blocks run as one `sh -e` script against the commit under test, with the clone URL redirected to CI's checkout (Reviewer's finding on #23) |
| 2026-10-26 | architect, #20 | First entry: S1's done-check. Rests on the Owner's answers on #20: 1a 2a 3a 4a |

This file maps each requirement in `project_requirements.md` to the check that work is judged by, and turns each goal's measurable outcome into its **done-check**. Each check says where it runs. The Owner approves every change to it.

The generic requirements (blueprint §1) are mapped by a later Definition item. Until then, the gate's generic checks (`system/conventions.md`, "The gate") are what guard them. G1-15 covers the part G1's toolchain brings: pinned and vetted dependencies.

## S1: seed goal, `README.md`

**Goal:** `project_requirements.md` §2, S1. **Measurable outcome:** "`README.md` says what the product is, how to build it and how to run its checks."

**Scope.** S1 is struck through, but its criteria stay: they keep the README true. The seed version described the product as named but not yet defined. G1's Definition (#81) revised S1-1 to S1-4 below to match §1, §6 and §7 of requirements v1.4. **When they take effect:** the README and `.github/scripts/check_readme.py` change together, in one PR, in G1's first stage (G1's "Stages"). Until that PR merges, CI checks the seed wording.

### Done-check

S1 is done when every criterion below holds on `main`.

| ID | Criterion | How it's checked | Runs |
| --- | --- | --- | --- |
| S1-1 | `README.md` exists at the repository root. Its first line is a level-1 heading naming the product as §1 does: "Local Meeting Transcriber". | `head -n 1 README.md` prints `# Local Meeting Transcriber` (case-insensitive). | CI |
| S1-2 | **What it is.** A `## What it is` section says, in at most four sentences: the product's name; its mission, in §1's words (it runs entirely on the user's Mac, and the audio never leaves it); that it is built by this repository's development system (links `blueprint.md`); and where its requirements are (links `project_requirements.md`). It claims as working only what `main`'s gate tests. Anything else is labelled "planned". | The heading exists: `grep -c '^## What it is$' README.md` prints `1`. The wording is checked by reading it against `project_requirements.md` §1–2 and the gate's jobs. | CI (heading); Reviewer, by reading (wording) |
| S1-3 | **How to build.** A `## Build` section lists the prerequisites (git; Python 3.11 or later; the toolchain's installer, `uv`, at the version `toolchain.toml` pins) and, in one fenced `sh` block, the commands to get a working copy and install the product with its locked dependencies. The block starts with `git clone https://github.com/aray4702/auto_research.git` and then `cd auto_research`, and its last line is `uv run lmt --help`. Until the package exists, the section says there is no product code yet, as the seed version does. | The heading exists, as in S1-2. The block runs as described under "How the `sh` blocks run": in a new empty directory, with the clone URL redirected to CI's checkout. It passes when the script exits 0 and `git -C auto_research rev-parse HEAD` in that directory equals the commit under test. | CI |
| S1-4 | **How to run the checks.** A `## Checks` section says that the gate (`.github/workflows/gate.yml`) is the one required check, and gives, in one fenced `sh` block, the command that runs its generic checks locally: `python3 -I system/gate/generic.py --base origin/main`. It says that the product's CI jobs live in `.github/workflows/product.yml`. Once the package exists, the section also names, as inline code outside the block, the product's test command and the CI evaluation subset's command (G1-5). | The heading exists, as in S1-2. `grep -F 'python3 -I system/gate/generic.py --base origin/main' README.md` matches. The block runs as described under "How the `sh` blocks run", from the root of CI's checkout, and exits 0. The two product commands are found by `grep -F`, with the strings `product.yml`'s jobs run. | CI |
| S1-5 | Every relative link in `README.md` points to a file or directory that exists in the commit. | For each Markdown link target without a scheme (`http:`, `https:`, `mailto:`), `test -e <target>` from the repository root succeeds (an `#anchor` suffix stripped). | CI |

**How the `sh` blocks run.** Each check runs the commit under test, never `main`, and needs no credentials or network beyond the install in S1-3.

- **CI's checkout.** The `readme` job checks out the commit under test with `actions/checkout`, `fetch-depth: 0` (so `origin/main` exists) and `persist-credentials: false`, as the gate's `generic` job does.
- **One script.** The whole block is extracted from `README.md` and run as one script with `sh -e`, so a `cd` carries over to the lines after it, and the first failing line fails the check.
- **The clone line (S1-3).** The script runs in a new empty directory. Git is configured for that run only (`GIT_CONFIG_COUNT`, or `git config --global` in the job) with `url.<path of CI's checkout>.insteadOf https://github.com/aray4702/auto_research.git`. So the README's clone line, unchanged, clones CI's checkout, whose `HEAD` is the commit under test. The `HEAD` comparison in S1-3 confirms this. If it doesn't hold, the check fails rather than testing another commit.
- **The install (S1-3).** The install downloads the locked packages from the package indexes the lock file names. It doesn't download models: `lmt --help` needs none.

**Where CI runs it.** The `readme` job in `.github/workflows/product.yml` runs the CI checks of S1-1 to S1-5. The gate fails any change that breaks them, so the README stays true. The job's details are the Developer's, within this table.

**Fresh evidence for closing S1:** a green `gate` run on `main` that includes the `readme` job, plus the Reviewer's reading for S1-2 on the PR that added the README. (S1 closed on this evidence on 2026-10-26. The revised criteria get the same evidence on the PR that brings them into effect.)

### Stages

One stage, one phase, one Developer item: write `README.md` and add the `readme` job, in one PR. No architecture or public API is needed.

## G1: offline speaker-labelled transcription, and a reproducible evaluation

**Goal:** `project_requirements.md` §2, G1. **Measurable outcome:** "F1–F5, F9; Q1–Q4, Q7, Q8 met on the named evaluation sets; the README's results table regenerated by one command (F4)." **Deadline:** 2026-10-28.

The names below (`lmt`, its options, exit codes, the transcript schema, file paths) are the ones `design/public-api.md` and `design/architecture.md` propose. If the Owner picks other options on #81, this section follows.

### Terms

- **The product run:** `lmt transcribe <input>` with default options, with the default models already downloaded and verified (`lmt models download`). No speaker count is given.
- **Word error rate (WER):** the reference and the hypothesis both go through Whisper's English text normalizer (`EnglishTextNormalizer`, the copy the harness vendors at a pinned commit). Then `jiwer` scores the whole set at once: total substitutions, deletions and insertions over total reference words, not a mean of per-file rates.
  - **LibriSpeech:** each utterance is transcribed on its own, with diarization off (`--no-diarize`), as one file.
  - **AMI:** each meeting is transcribed whole, by the product run. The reference is every word of every speaker in the AMI manual annotations (v1.6.2), sorted by start time. The hypothesis is the transcript's words in order. Speaker labels don't count towards WER (#81, question 3).
- **Diarization error rate (DER):** `pyannote.metrics` `DiarizationErrorRate`, with `collar=0.0` and `skip_overlap=False`, scored over the whole set. The hypothesis is the exported transcript: each JSON segment's `start`, `end` and `speaker` (#81, question 4). The reference is the `only_words` RTTM files of the AMI diarization setup (`BUTSpeechFIT/AMI-diarization-setup`, at the commit the dataset manifest pins). The scored region is the whole file, so there is no UEM.
- **Real-time factor (RTF):** the product run's wall-clock time, from process start to exit, over the audio's duration. It includes model loading and excludes model download. Over a set, it is total time over total duration.
- **Evaluation sets** (each pinned by a manifest that lists every file's URL and SHA-256):
  - **LS full:** LibriSpeech test-clean, all 2,620 utterances.
  - **AMI full:** the Mix-Headset recordings of the 16 meetings in AMI's standard Full-corpus-ASR test split: EN2002a–d, ES2004a–d, IS1009a–d, TS3003a–d.
  - **CI subset**, which CI downloads and caches:
    - **LS-ci:** 20 fixed LibriSpeech test-clean utterances, about 2.5 minutes. Their IDs are listed in the manifest.
    - **AMI-ci:** the first 300 s of ES2004a's Mix-Headset recording. Its references are cropped to the same span.
    - Together that is about 7.5 minutes of audio, sized so that G1-5's double run fits in a CI job of about 20 minutes. If the first measurement says otherwise, the Architect resizes it in this section.
- **The one-hour recording:** AMI full's meetings in the order listed above, concatenated and cut at 3,600 s. Its references are cropped to match. A manifest recipe builds it.
- **The reference clip:** `tests/fixtures/clip.wav`, about 60 s of LibriSpeech test-clean speech from one speaker. A recipe in the repository builds it from the pinned source utterances, along with its encodings: FLAC; MP3 at 128 kbit/s; M4A (AAC-LC) at 128 kbit/s; and MP4 and MOV files with an AAC track and a still video track.
- **The two-speaker clip:** `tests/fixtures/two_speakers.wav`, about 60 s. A recipe builds it from LibriSpeech test-clean utterances: four alternating turns by one female and one male speaker, with 0.5 s gaps. Its RTTM and word references come with it.
- **Results file:** `results/<set>.json`, which `lmt eval` writes. It holds the figures for each set and system, the commit, the model revisions, the machine (`uname -m`, CPU model, memory) and the date. `public-api.md` describes the format.

### Done-check

G1 is done when every criterion below holds on `main`. **Where it runs:**
- **CI:** the gate's product jobs on `ubuntu-24.04`, Linux x86_64, CPU only.
- **Mac:** a check the Owner runs before release on the Owner's Mac (Apple M4, 16 GB, A1), from one command, `lmt eval --set full` (#81, question 5). Its results file goes into `results/` by PR. CI records its own figures for these checks but never fails on them.

| ID | Requirement | Criterion | How it's checked | Runs |
| --- | --- | --- | --- | --- |
| G1-1 | F1 | The reference clip, as WAV, transcribes with WER ≤ 5.0 %. Every segment has 0 ≤ `start` < `end` ≤ the clip's duration, and segments are in order of `start`. | A test runs the product run on the clip, then scores the JSON's text against the clip's reference and checks the times. | CI |
| G1-2 | F1 | **Formats.** The reference clip as FLAC, MP3, M4A, MP4 and MOV each transcribes with a WER within 1.0 percentage point of the WAV's WER. | The same test, run once per format. | CI |
| G1-3 | F2 | **Speakers.** The two-speaker clip, with no speaker count given, gives exactly two speaker labels. For each reference speaker, at least 90 % of their words carry the label most of their words have, and the two speakers' majority labels differ. AMI's DER is G1-8's. | A test runs the product run on the clip, then counts labels per reference speaker by word time. | CI |
| G1-4 | F3 | **Exports.** `lmt transcribe` with `--formats txt,srt,vtt,json` writes four files. Every segment has a speaker label and timestamps in every format. The JSON validates against `schema/transcript.schema.json` (JSON Schema 2020-12, validated with `jsonschema`). The SRT parses with the `srt` package in strict mode, and the WebVTT with `webvtt-py`. Both decode with `ffmpeg -v error -i <file> -f null -`, which exits 0 and prints nothing. All four formats hold the same words in the same order. | A test on the reference clip and the two-speaker clip. | CI |
| G1-4m | F3 | "The SRT plays in a standard player": the two-speaker clip's SRT and WebVTT, loaded beside the clip, show their cues at the right times with the speaker's label, in QuickTime Player and in VLC. | The Owner opens them and records the result on G1's release item. | Mac (Owner, by eye) |
| G1-5 | F4 | **Harness, one command.** `lmt eval --set ci` downloads and caches the CI subset, then writes `results/ci.json` with WER (LS-ci, AMI-ci), DER (AMI-ci) and RTF for each system: the product, Whisper alone and pyannote alone (Q1's comparison). Two runs on the same machine give identical accuracy figures: the two files, with their timing fields removed, are byte-identical. | The `eval-ci` job runs the command twice and compares the files. | CI |
| G1-6 | F5 | **CLI.** `lmt --help`, and `--help` on every subcommand, exit 0. Together they list exactly the commands and options in `design/public-api.md`. Each documented option has a test that uses it and checks its effect. Bad input exits with the documented non-zero code and prints one line to stderr, starting `lmt: error:` and naming the file or option at fault. The cases: a missing file, a directory, an unknown option, a bad option value, an output that exists without `--overwrite`, an unwritable output directory. | A test compares the parser's options with the table in `public-api.md`, plus one test per option and per bad input. | CI |
| G1-7 | F9 | **English only.** The evaluation sets are English: the harness's manifests name only LibriSpeech and AMI. The ASR stage always decodes with language `en`, and the CLI has no language option. | A test asserts the decode language and the manifests' sources. The Reviewer reads `public-api.md` for a language option. | CI |
| G1-8 | Q1 | **Accuracy, full sets.** LS full WER ≤ 5.0 %; AMI full WER ≤ 25.0 %; AMI full DER ≤ 25.0 %. The results file also holds the same figures for Whisper alone (the same ASR model and settings, without diarization or alignment) and for pyannote alone (the diarization model's own turns, scored as DER). | `lmt eval --set full`, which writes `results/full.json`. | Mac |
| G1-8c | Q1 | **Accuracy, CI subset** (a pass/fail proxy for G1-8, on the same bounds): LS-ci WER ≤ 5.0 %; AMI-ci WER ≤ 25.0 %; AMI-ci DER ≤ 25.0 %. If the first measurement shows the subset failing a bound while the full set meets it, the Architect proposes a subset bound to the Owner. Nobody else changes these bounds. | The `eval-ci` job reads `results/ci.json` against the bounds. | CI |
| G1-9 | Q2 | **Speed.** RTF ≤ 0.50 over AMI full, and ≤ 0.50 on the one-hour recording. | `lmt eval --set full` measures RTF for the product run, one file at a time, with no other evaluation running. | Mac. CI records its own RTF in `results/ci.json` and doesn't fail on it |
| G1-10 | Q3 | **Resources.** On the one-hour recording, the product run's peak resident memory is ≤ 4.0 × 10⁹ bytes. The default models take ≤ 2.0 × 10⁹ bytes on disk. | Memory: `/usr/bin/time -l lmt transcribe …` gives the "maximum resident set size" in bytes. The harness records it. Disk: `lmt models list --bytes` totals the verified files of the default models. | Mac (memory); CI and Mac (disk, since the same files are used on both) |
| G1-11 | Q4 | **Bad input never crashes it.** Each of the following ends within 120 s with exit code 3, or with 4 ("no speech found") for the silence and noise files. It prints one `lmt: error:` line that names the problem, with no traceback, and leaves no output files. The inputs: an empty file; a zero-length WAV; a WAV with a header but truncated data; random bytes named `.wav`, `.mp3` and `.m4a`; a text file named `.mp4`; an MP4 with no audio track; an unsupported format (`.txt`, `.docx`); 60 s of digital silence; 60 s of −60 dBFS noise; and 200 mutations of the reference clip's encodings (bytes flipped, truncated or spliced, from fixed seeds). A mutated file that still decodes may instead succeed with exit 0. It must then write valid outputs. | The `fuzz` test runs every input through `lmt transcribe` in a subprocess and checks the exit code, stderr and output directory. | CI |
| G1-12 | Q7 | **Reproducible.** Every model file is pinned by repository, revision and SHA-256 in the model manifest, and is verified before use. Decoding is deterministic: temperature 0 with no fallback, and a fixed beam. Two harness runs on the same machine give identical accuracy figures, as in G1-5. On the Mac, `lmt eval --set ci` runs twice before the full run, and the two files compare the same way. | CI: G1-5's comparison, and a test that the manifest pins every file. Mac: the harness compares the two runs and records the result in `results/full.json`. | CI and Mac |
| G1-13 | Q8 | **Nothing leaves the computer, and nothing else is written.** After `lmt models download`, the product run on the two-speaker clip makes no network connection, and its outputs equal those of a run with the network up. The models are read from `LMT_CACHE_DIR`, filled beforehand. `HOME`, `TMPDIR`, `XDG_CACHE_HOME` and the working directory are fresh empty directories. After the run, the only new files under them and the output directory are the requested outputs, and nothing in `LMT_CACHE_DIR` has changed. No package sends telemetry. | Network: the run is wrapped in `sudo unshare --net`, dropping back to the runner's user. A test also blocks `socket.connect` in the process and fails if anything calls it. Files: the directory trees, `LMT_CACHE_DIR`'s included, are compared before and after the run, by path, size and SHA-256. | CI. The Owner also runs the two-speaker clip with Wi-Fi off before release (Mac, by eye) |
| G1-14 | F4 | **README results table.** `README.md` has a `## Results` section. Between the markers `<!-- results:start -->` and `<!-- results:end -->`, it holds the table `lmt eval --update-readme` renders from `results/full.json`: WER, DER and RTF per set for the product, Whisper alone and pyannote alone. It also gives the commit, model revisions, machine and date. One command regenerates the table: `lmt eval --set full --update-readme`. | CI: `lmt eval --check-readme` exits 0 when the table equals what `results/full.json` renders, so the table can't drift from its results file. Mac: the Owner's full run regenerates it. | CI (drift); Mac (the figures) |
| G1-15 | §7, Q9 | **Pinned and vetted dependencies.** Every dependency is locked in `uv.lock`, by version and hash. Each installed distribution's licence is MIT, BSD (2- or 3-clause), Apache-2.0, or an exception the Owner accepted (#81, question 2), as the allow list in `toolchain.toml` names them. Models come only from the sources the model manifest pins, with their licences recorded there. A file whose SHA-256 doesn't match is deleted, and the run fails with exit code 5. | The `test` job runs `uv sync --locked` and a licence check over the installed distributions' metadata. A test feeds the downloader a file with a bad checksum. | CI |

**Q6** (`--verbose` and error reports) isn't in G1's outcome, so G1 doesn't check it. The public API still reserves its options.

**Fresh evidence for closing G1:**
- a green `gate` run on `main` whose product jobs include `test`, `eval-ci` and `readme`;
- `results/full.json` from the Owner's run of `lmt eval --set full`, at a commit whose gate passed, merged by PR with the regenerated README table, and meeting G1-8, G1-9, G1-10 and G1-12;
- the Owner's readings for G1-4m and G1-13, recorded on G1's release item.

### Stages

These are the Architect's proposal. The Coordinator charts the plan.

1. **Toolchain, CI and the harness, wired to the CLI.** It leaves `main` with an installable `lmt` whose `eval` scores fixed transcripts, and with CI running on Linux x86_64.
   - The package skeleton: `pyproject.toml`, `uv.lock`, `toolchain.toml`, `lmt --help`, the error types and exit codes. Plus the `test` job in `product.yml`, and the licence check (G1-15). The README and `check_readme.py` take on the revised S1-1 to S1-4 in the same PR.
   - A builder item: the gate passes `HF_TOKEN` to the product jobs, which the pyannote model download needs. `gate.yml` is a system file.
   - Models: the manifest, download, SHA-256 check, cache and `lmt models` (G1-12, G1-15), with a CI cache of the model files.
   - The harness: dataset manifests and their cache, the vendored normalizer, WER, DER and RTF, the results file and the README table (G1-5's mechanics, G1-14's drift check). It is tested on fixed hypothesis files, so it needs no model yet.
2. **The pipeline:** decode (G1-2, G1-11), ASR, diarization, alignment, and the four exports with the schema (G1-4). Then `lmt transcribe` with its options (G1-6, G1-7), the `eval-ci` job with its bounds and the double run (G1-5, G1-8c, G1-12), the fuzz corpus (G1-11) and the network and file test (G1-13). An investigation runs alongside: it measures the candidate ASR models (#81, question 7) on the Mac, so the default is pinned before the stage closes.
3. **Full evaluation and the results table:** the Owner's `lmt eval --set full`, the README's results table, and the Owner's readings (G1-8 to G1-10, G1-12, G1-14, G1-4m, G1-13).

**The deadline, 2026-10-28.** Stages 1 and 2 are about a dozen items, most of them on protected paths that the Owner merges. Fitting them into the day is possible but tight. Stage 3 then needs about 6–8 hours of the Mac's time for the full sets and the baselines, after stage 2 lands. G1 can meet its CI-checked criteria on 2026-10-28. Its Mac criteria will most likely land on 2026-10-29 (#81, question 1).
