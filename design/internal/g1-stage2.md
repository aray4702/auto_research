# Internal design: G1 stage 2, the rest (G1-3 to G1-7, G1-8c, G1-11, G1-13)

| Date (PT) | By | Change |
| --- | --- | --- |
| 2026-10-29 | architect, #289 | First version: ten Developer items on top of the slice, G1-2's design and #110, with the two-speaker clip's rule for #123 and #286's two findings decided |

This is what a Developer needs to build each item below in one run. It finishes G1's stage 2 for **G1-3, G1-4, G1-5, G1-6, G1-7, G1-8c, G1-11 and G1-13** in `spec/acceptance.md`, each exactly as the spec gives it, with no bound, scoring or fixture loosened. It also adds `lmt.transcript.load`, which G3 assumes, and decides #286. G1-2 is `g1-formats.md`'s. It extends the code on `main`: `g1-slice.md`'s pipeline and product run, `g1-stage1.md`'s harness (§4, §5a, §6), #110's CI subset (PR #298), and `criteria.md`'s record. Where this file and those, `architecture.md` or `public-api.md` disagree, they win, and this file is the one to fix. The Reviewer reviews this file. The Architect changes it.

**What doesn't change.** Every bound (G1-8c's 5.0 %, 25.0 % and 25.0 %, inclusive), the scoring (#109's `wer` and `der`), the fixtures (the clip, its encodings, the two-speaker clip and the CI subset, each fixed before it is measured) and the product's defaults. If a measurement misses, the product is what gets fixed. G1-8c's subset bound changes only by the Architect's proposal to the Owner, as the spec says.

## Items, files and order

| Item | What | Needs | Files it creates or changes | Tier |
| --- | --- | --- | --- | --- |
| **P1** | Decode before the models; a truncated WAV is an input error | — | `src/lmt/pipeline.py`, `src/lmt/audio.py`, `tests/test_pipeline.py`, `tests/test_audio.py` | 3 |
| **P2** | `lmt eval --set ci`, and the `eval-ci` job's double run and bounds (G1-5, G1-8c, G1-12's CI half) | #110 (PR #298) | `src/lmt/eval/runner.py` (new), `src/lmt/eval/worker.py` (new), `src/lmt/eval/cli.py`, `tests/test_eval_runner.py` (new), `tests/fixtures/eval/fake_worker.py` (new), `tests/test_eval_ci.py` (new), `.github/workflows/product.yml` (`eval-ci` only) | 3 |
| **P3** | One test per CLI option and per bad input (G1-6); no language option (G1-7) | P1 | `tests/test_g1_6.py` (new), `tests/test_cli.py`, `tests/test_models.py` (tags), `tests/test_readme_table.py` (tag) | 2 |
| **P4** | Bad input never crashes it (G1-11) | P1, E1 (#296/#299) | `tests/bad_inputs.py` (new), `tests/test_g1_11.py` (new), `tests/test_decode_fuzz.py` (new) | 2 |
| **P5** | The two-speaker product run and G1-3's test | #123's two-speaker clip | `tests/conftest.py`, `tests/test_g1_3.py` (new) | 2 |
| **P6** | The exports' checks (G1-4) | P5 | `tests/subtitle_checks.py` (new), `tests/test_g1_4.py` (new), `.github/workflows/product.yml` (`test-models` only) | 3 |
| **P7** | The network and files (G1-13) | P5 | `tests/isolation.py` (new), `tests/netguard/sitecustomize.py` (new), `tests/test_netguard.py` (new), `tests/test_g1_13.py` (new) | 2 |
| **P8** | `lmt eval --set full` and `--update-readme`, on fakes (G1-6's last two options) | P2 | `src/lmt/eval/runner.py`, `src/lmt/eval/cli.py`, `tests/test_eval_full.py` (new) | 3 |
| **P9** | `lmt.transcript.load`, `jsonschema` at run time, #286's two findings | — | `src/lmt/transcript.py`, `src/lmt/schema/transcript.schema.json`, `schema/transcript.schema.json`, `src/lmt/export/__init__.py`, `pyproject.toml`, `uv.lock`, `tests/test_transcript.py`, `tests/test_export.py` | 3 |
| **P10** | The registry: `complete = true` for each criterion whose checks have passed on `main` | the items it marks | `tests/criteria.toml`, `.github/scripts/test_criteria.py` | 2 |

**Parallel-safe,** by the files above, except two pairs. P1 and P3 both rely on decode coming first, so P3 waits for P1. P2 and P6 both edit `product.yml`, in different jobs (`eval-ci` and `test-models`), so git merges them cleanly; whichever merges second refreshes from `main`. No other two items share a file. Only P10 edits the registry, so the others never conflict there: each item's PR shows its criterion `partial` with every check passing, and P10 turns that into `pass`.

**Order, for G1's CI deadline (2026-10-30, end of day).** The shortest path to the most criteria met. Two Developer runs at a time (`system/caps.toml`):

1. **P1 ∥ P2.** P2 needs #110's PR #298 merged first. P2 is the first measurement of G1-8c, the criterion most likely to need a product fix or an Owner question, so it goes early. P1 is small.
2. **P3 ∥ P4** (P4 once E1 has merged). G1-7 needs only P3; G1-11 needs P4.
3. **#123's two-speaker clip**, then **P5**, then **P6 ∥ P7**. These carry G1-3, G1-4 and G1-13. **Recommendation to the Coordinator:** #123 lands the two-speaker clip first, as an item of its own (rules, "If the item is bigger than one run"), with LS full, AMI full and the one-hour recording after it, since only stage 3 needs those.
4. **P8**, which completes G1-6.
5. **P10**, once the others have passed on `main`. If the deadline nears first, it marks what has passed, and a second P10 run marks the rest.
6. **P9** whenever a run is free: no G1 criterion waits on it, but G3's stage 1 does.

**What the deadline can carry.** It's 01:10 PT on 2026-10-29. Every item is one Developer run, and most are tier 2 or 3, so roughly an hour of CI each, plus review and the Owner's merge where the path is protected. All ten fit by the end of 2026-10-30 if #110 merges this morning and #123's two-speaker clip by the evening. The criteria at risk are: G1-3, G1-4 and G1-13, behind #123; and G1-8c, if its first reading misses a bound. Next most exposed is G1-6, which waits for P8 behind P2.

**Every item's local checks:** `uv run --frozen ruff check`, `uv run --frozen pytest`, `uv run --frozen pytest -m models` where it has model tests and the models are in `LMT_CACHE_DIR`, and `python3 -I system/gate/generic.py --base origin/main`. Each tagged test carries its criterion (`criteria.md` §1). Each PR shows `criteria.py --ref refs/pull/<n>/merge` giving its criteria `partial`, with no failed check.

## P1. Decode before the models; a truncated WAV

**`pipeline.transcribe`** decodes first, then calls `models.ensure`. So a bad input exits 3 at once, without hashing or downloading 1.5 GB of models, and G1-6's and G1-11's decode cases need no model. Nothing else moves: `cli_transcribe` still checks `--model` and the outputs before `transcribe`, and `timings` keeps both stages.

**`audio.decode`** gains one check, before `av.open`. FFmpeg decodes a WAV whose data is cut short without an error, so the audio that's left would be transcribed. G1-11 requires exit 3 for "a WAV with a header but truncated data". So, when the file's first 12 bytes are `RIFF<4 bytes>WAVE`, decode walks its chunks with `struct` (`<4sI`: id and size), from offset 12, to the first `data` chunk, skipping each other chunk by `8 + size + (size & 1)`. If the `data` chunk's size isn't 0 or `0xFFFFFFFF` (the sizes streaming writers leave) and its end, `offset + 8 + size`, is past the end of the file, it raises `InputError(str(path), f"truncated: its data chunk declares {size} bytes, the file holds {held}")`. A walk that reaches the end of the file without a `data` chunk leaves the file to PyAV.

**Tests.** `tests/test_audio.py`: the clip's 44-byte header with its first 16,000 bytes of data, and nothing after them, raises `InputError` whose problem starts `truncated`; a complete WAV with a `LIST` chunk before `data` decodes; a header declaring `0xFFFFFFFF` raises no truncation error. `tests/test_pipeline.py`: with `models.ensure` faked to fail the test if called, a missing input exits 3 and the fake is never called.

**Test of the item:** those tests, in the `test` job, and G1-1's test still passing in `test-models`.

## P2. `lmt eval --set ci` and the `eval-ci` job

### The runs

Each scored file goes through **the product run**, in a process of its own, as the spec's "Terms" define it: LibriSpeech utterances with `--no-diarize`, and AMI with no option. The architecture has each stage's output "computed once per file and reused". So the same process also records ASR's words and diarization's turns, which give Whisper alone and pyannote alone (`architecture.md`, "Evaluation harness").

**`src/lmt/eval/worker.py`**, run as `python -m lmt.eval.worker STAGES_OUT transcribe INPUT [--no-diarize]`:

- It wraps four functions for this process only: `asr.load` and `asr.Engine.transcribe`, then `diarize.load` and `diarize.Diarizer.diarize`. Each wrapper adds its wall time to `asr_seconds` or `diarize_seconds`, and the two stage wrappers keep what they return.
- It then calls `lmt.cli.main(["transcribe", INPUT, *options])`, unchanged. If that returns 0, it writes `STAGES_OUT` (a temporary file, then renamed): `{"words": [[start, end, text], …], "turns": [[start, end, speaker], …] or null, "asr_seconds": …, "diarize_seconds": … or null}`. It exits with `main`'s code.
- So the product's own code runs, untouched. The wrappers only observe it, and nothing in `lmt` knows about them.

**`src/lmt/eval/runner.py`:**

```python
WORKER = ("-m", "lmt.eval.worker")              # tests point it at tests/fixtures/eval/fake_worker.py
@dataclass(frozen=True)
class Run:  transcript: dict; words: list; turns: list | None; wall_seconds: float
            peak_rss_bytes: int; asr_seconds: float; diarize_seconds: float | None
def run_product(item: Item, work_dir: Path) -> Run
def hypotheses(item: Item, run: Run) -> dict[tuple[str, str, str], Hypothesis]
def evaluate(set_name: str, *, out_dir: Path, determinism=None) -> dict    # writes <out_dir>/<set>.json, returns it
```

- **`run_product`.** Diarization is on when the item has reference turns (`item.ref_turns is not None`), which holds for AMI and not for LibriSpeech. The process is `[sys.executable, *WORKER, <stages file>, "transcribe", str(item.audio)]`, plus `--no-diarize` when diarization is off. It runs in a new empty directory under `work_dir`, with stdout to `DEVNULL` and stderr to a file there. `wall_seconds` runs from just before `Popen` to `os.wait4(pid, 0)`'s return: process start to exit, model loading included (spec, RTF). `peak_rss_bytes` is `wait4`'s `ru_maxrss`, × 1024 on Linux, as is on macOS. That is the counter `/usr/bin/time -l` prints, which G1-10 names. A non-zero exit raises `InputError(str(item.audio), f"lmt transcribe exited {code}: {stderr's last line}")`. The transcript is `<dir>/<audio stem>.json`.
- **`hypotheses`.** `product`: `hypothesis_from_transcript(doc)`, with the run's `wall_seconds` and `peak_rss_bytes`. `whisper-alone`: the words' texts joined by single spaces, no turns, `wall_seconds = asr_seconds`. `pyannote-alone`, only when diarization ran: the raw turns as `score.Turn`s, no text, `wall_seconds = diarize_seconds`. The two stages run side by side within the product run (`PARALLEL`), so their times are measured under each other's load. That's recorded as it is: CI never fails on RTF.
- **`evaluate`.** It calls `datasets.fetch(set_name)`, which downloads, caches and verifies the set (G1-5's "downloads and caches"). Then it runs every item in order, one at a time, in a `tempfile.TemporaryDirectory()`, with one stderr line per item: `lmt: eval: <dataset> <id> (<i>/<n>) <wall> s`. It calls `score.score(items, hyps)`, then `report.build(set_name, figures, models, determinism, out_dir=out_dir)` and `report.write(out_dir / f"{set_name}.json", …)`. `models` is the first diarized run's transcript `models`, or the first run's when none is diarized. It writes nothing but the results file, and the temporary directory goes when it's done.
- **The figures** for `ci`, in `score`'s order: LS-ci `product` and `whisper-alone` (WER and RTF); AMI-ci `product` (WER, DER and RTF), `whisper-alone` (WER and RTF) and `pyannote-alone` (DER and RTF). That is G1-5's list: WER on both, DER on AMI-ci, RTF for each system.

**`src/lmt/eval/cli.py`:** `--set ci` creates `--out` if needed (`mkdir(parents=True, exist_ok=True)`), then calls `runner.evaluate("ci", out_dir=Path(args.out))`, and prints the results file's path to stdout. `--set full` stays `NotBuilt` until P8.

### The `eval-ci` job

On top of #110's steps, which restore the data cache and run `pytest -m data`:

1. The model cache, the download on a miss and `lmt models verify`: the same three steps as `test-models`, with the same key, and `HF_TOKEN` on the download step only.
2. #110's separate `python -m lmt.eval.datasets fetch ci` step goes. The first run below is what fetches on a cache miss, as G1-5 says the command does.
3. `uv run --frozen lmt eval --set ci`, which writes `results/ci.json`.
4. `cp results/ci.json "$RUNNER_TEMP/ci-1.json"`.
5. `uv run --frozen lmt eval --set ci` again. `report`'s `dirty` excludes `results/`, so the first file doesn't make the second run's tree dirty (`g1-stage1.md` §4).
6. #110's `pytest -m data --criteria-out=criteria-eval-ci.json` step moves here, after the two runs, with `env: LMT_EVAL_CI_FIRST: ${{ runner.temp }}/ci-1.json`. So one pytest run writes the job's one record.
7. Before #110's record upload, an upload of both results files, `if: ${{ !cancelled() }}`, named `eval-ci-results` (the same pinned `actions/upload-artifact`).

**Time.** One run is 20 LibriSpeech processes, each loading the model (≈ 10 s) for about 8 s of audio, plus AMI-ci's 300 s at an RTF near 0.5. That's about 7 minutes, so the two come to about 15, inside the job's 45-minute timeout. The PR shows the job's time. If it passes 1.5 times the architecture's 34-minute tier-3 estimate, the Developer says so on the item.

### Tests

- **`tests/test_eval_runner.py`** (the `test` job, no model):
  - The runner runs `tests/fixtures/eval/fake_worker.py` in place of the worker, on fake items built from `tests/fixtures/eval/ls/` and `ami/` (#109's fixtures). The fake copies the fixture's hypothesis transcript into its directory as `<stem>.json`. It writes stages whose words are that transcript's words, and whose turns are its segments' turns.
  - The results file's figures are then the hand-computed ones: LS WER 0.263158 for `product` and `whisper-alone`, and AMI DER 0.117647 for `product` and `pyannote-alone`. Every figure's `rtf` and `wall_seconds` is a number, and `peak_rss_bytes` is a number for `product` only.
  - Diarization is off exactly for the LibriSpeech items: the fake records its argv. A fake that exits 4 makes `evaluate` raise exit 3, naming the item. `--set ci` and `--out DIR` through `cli.main` write `DIR/ci.json` and print its path (tagged `G1-6`).
  - `worker.main` runs in process, with `asr.load` and `diarize.load` faked as in `tests/test_pipeline.py`. Its stages file holds the fakes' words and turns, and its exit code is `cli.main`'s. With a fake ASR that finds no words, it exits 4 and writes no stages file.
- **`tests/test_eval_ci.py`**, marked `data`, reads the job's two files: `$LMT_EVAL_CI_FIRST` and `results/ci.json`. Without the variable it skips, saying so, except under `CI`, where it fails.
  - **G1-5 and G1-12** (tagged both): both files exist with `set` `"ci"`, they hold the figures listed above with those values non-null, and `report.same_accuracy(first, second)` is true. On failure it prints both stripped files' unified diff.
  - **G1-8c** (tagged): from `results/ci.json`'s `product` figures, LS-ci WER ≤ 0.050, AMI-ci WER ≤ 0.250 and AMI-ci DER ≤ 0.250. Each bound is a constant whose comment cites the spec. The file rounds to 6 decimal places. For any set under 10⁶ reference words, a rate above a bound exceeds it by at least 10⁻⁶ before rounding, so the rounding can't let a miss pass. Every figure goes to the test's output and to `$GITHUB_STEP_SUMMARY`, pass or fail.

**Test of the item:** the tests above, in the `test` and `eval-ci` jobs of the PR (tier 3), with the summary's figures and `criteria-eval-ci` giving G1-5 and G1-8c `passed`. **If G1-8c misses a bound** while the double run passes, the Developer reports the figures on the item and holds it for the Architect (`needs-decision:architect`), without changing the bounds or the subset. The Architect then decides between a product fix and the spec's route: a subset bound proposed to the Owner, only if the full set meets G1-8, which needs the Mac's run.

## P3. G1-6's tests, and G1-7's

**`tests/test_g1_6.py`** (the `test` job, no model), tagged `G1-6`. It reuses `tests/test_pipeline.py`'s fakes (`from test_pipeline import Fakes`), and has one test per option, each checking the option's effect:

| Option | Effect checked |
| --- | --- |
| `-o`, `--output-dir` | The outputs land in `DIR`, and none in the working directory. |
| `-f`, `--formats` | `-f srt,json` writes exactly those two, and prints them in that order. |
| `--num-speakers` | The fake diarizer gets `num_speakers=3`, and `min_speakers` and `max_speakers` `None`. |
| `--min-speakers`, `--max-speakers` | Each reaches the diarizer, alone and together. |
| `--no-diarize` | Diarization is never loaded, every segment is `S1`, and `models.diarization` is `null`. |
| `--model` | With a test manifest of two ASR models, `asr.load` gets the named one's directory, and the JSON's `models.asr.name` is that name. |
| `--offline` | A missing model file exits 5, with the download fake never called. With every file present, the run succeeds without it. |
| `--overwrite` | An existing output is replaced. Without the option, it exits 2 and the file is unchanged. |
| `-v`, `--verbose` | Six stage lines on stderr, and none without it. |

The other commands' options are tagged where their tests already are: `models download --model`, `list --bytes` and `verify` in `tests/test_models.py`; `eval --check-readme` in `tests/test_readme_table.py`; and `--version` and `--help` already in `tests/test_cli.py`. `eval --set ci` and `--out` are P2's, and `--set full` and `--update-readme` P8's.

**Bad input,** through `lmt transcribe` in a subprocess (`conftest.run_lmt`), with no model needed since P1:

| Case | Exit | The one stderr line names |
| --- | --- | --- |
| A missing file | 3 | its path |
| A directory | 3 | its path |
| An unknown option, `--language en` | 2 | `--language` |
| A bad value, `--num-speakers 0` | 2 | `--num-speakers` |
| `clip.json` already in `-o DIR`, without `--overwrite` | 2 | that path |
| An output directory with mode `0o500` | 3 | the directory |

Each prints exactly one line on stderr, starting `lmt: error: `, with no `Traceback`, and the output directory holds no new file. The last case skips, saying so, when the tests run as root, who can write anywhere.

**G1-7** (`tests/test_cli.py`, tagged `G1-7`): no option string of any parser that `build_parser()` builds, at any depth, contains `lang`. With `test_asr.py`'s decode language and `test_manifests.py`'s sources, both already tagged, that is G1-7's test. The Reviewer's reading of `public-api.md` for a language option happens on P10's PR.

**Test of the item:** the tests above, in the `test` job.

## P4. Bad input never crashes it (G1-11)

### `tests/bad_inputs.py`

G2 calls these too (`g2-stage1.md`):

```python
SOURCES = ("wav", "flac", "mp3", "m4a", "mp4", "mov")    # tests/fixtures/clip.<suffix>
OPS = ("flip", "truncate", "splice")
E2E_MUTATIONS = (0, 7, 14, 21, 28)
def mutation(i: int) -> tuple[str, bytes]               # (suffix, bytes)
def build(name: str, directory: Path) -> Path           # one end-to-end input, by its name below
```

- **`mutation(i)`** reads `clip.<SOURCES[i % 6]>` and applies `OPS[(i // 6) % 3]`, with `rng = numpy.random.default_rng(i)`. **flip:** `n = rng.integers(1, 33)` positions from `rng.integers(0, len, n)`, each byte XORed with `rng.integers(1, 256)`. **truncate:** the first `rng.integers(0, len)` bytes. **splice:** `n = rng.integers(1, 4097)`, `p, q = rng.integers(0, len − n, 2)`, and bytes `[p, p + n)` replaced by `[q, q + n)`. So `E2E_MUTATIONS` covers a flipped WAV, a truncated FLAC, a spliced MP3, a flipped M4A and a truncated MP4.
- **The end-to-end inputs** (`build`): `empty.wav` (0 bytes); `zero.wav` (the `wave` module, 16 kHz mono 16-bit, no frames); `truncated.wav` (`clip.wav`'s first 44 + 32,000 bytes, so its header declares 56.565 s and it holds 1 s); `random.wav`, `random.mp3` and `random.m4a` (65,536 bytes from `default_rng(1)`, `(2)` and `(3)`); `text.mp4` (a line of English text); `video-only.mp4` (three 64×64 black frames in `mpeg4`, written with PyAV, with no audio stream); `notes.txt`; `notes.docx` (a zip with `[Content_Types].xml` and `word/document.xml`); `silence.wav` (60 s of zeros); `noise.wav` (60 s of white noise, `default_rng(4).standard_normal`, scaled to an RMS of 10^(−60/20) of full scale, × 32,767, rounded and clipped to 16 bits); and `mutation-<i>.<suffix>` for each of `E2E_MUTATIONS`.

### Tests

- **`tests/test_g1_11.py`**, tagged `G1-11`, one test per input, each run as `run_lmt(["transcribe", <its absolute path>], cwd=<a fresh empty directory>)` with a 120 s `timeout`. A timeout fails the test.
  - **The decode cases**, in the `test` job: every input except silence, noise and the mutations. Each exits 3.
  - **The rest**, marked `models`, in `test-models`. `silence.wav` and `noise.wav` exit 4. A mutation exits 3 or 4, or exits 0.
  - **For exits 3 and 4,** stderr is exactly one line, starting `lmt: error: `, holding the input's file name and a problem after it. stderr contains no `Traceback`, and the directory is still empty.
  - **For exit 0,** the four outputs exist and the JSON validates against the schema.
- **`tests/test_decode_fuzz.py`** (the `test` job), tagged `G1-11`, parametrized over `range(200)`. Mutation `i` is written to a temporary file with its suffix and passed to `audio.decode` in the test's process. It returns a one-dimensional float32 array with at least one sample, or raises `InputError` (exit code 3). Any other exception fails, naming `i`. The time is measured with `perf_counter` and must be ≤ 10 s. `faulthandler.dump_traceback_later(60, exit=True)` around each call turns a hang into a failure with a traceback, rather than a job timeout.

**Test of the item:** the tests above in the `test` and `test-models` jobs of the PR. The PR lists each end-to-end case's exit code and each mutation's outcome. **If a case fails,** for example random bytes that FFmpeg decodes, or library output on stderr, that is a product finding. The Developer reports it on the item for the Architect, who designs the fix in decode. Neither the input nor the expected code changes.

## P5. The two-speaker product run and G1-3

**`tests/conftest.py`** gains `TWO_SPEAKERS = ROOT / "tests" / "fixtures" / "two_speakers.wav"` and the session fixture `two_speakers_run`, made exactly as `clip_run` is: `lmt transcribe <its absolute path>`, with no option, in a fresh directory. G2's and G3's designs assume this name.

**`tests/test_g1_3.py`**, marked `models` and tagged `G1-3`. It uses the run's `two_speakers.json` and the clip's committed RTTM from #123:

- The run exits 0. `speakers` has exactly two entries, and the segments use both.
- **Each hypothesis word** (every segment's `words`, with its segment's `speaker`) goes to a reference speaker. That is the RTTM turn holding the word's midpoint, or else the nearest turn by gap, the earlier on a tie. No word is dropped.
- **For each reference speaker,** the label most of their words carry has at least 90 % of their words, and the two speakers' majority labels differ. A reference speaker with no words fails.
- Each speaker's word count and majority share go to the output and `$GITHUB_STEP_SUMMARY`, pass or fail.

### The two-speaker clip's rule (for #123)

`g1-stage1.md` §5b describes the clip but doesn't fix which utterances it uses. This rule fixes them now, before anything is measured on the clip. #123 applies it, and records the result in a comment in `sets.toml` as S1 did:

1. **The speakers.** From `LibriSpeech/SPEAKERS.TXT` (a member to add to the archive source), the test-clean speaker with the lowest id whose sex is `F`, and the one with the lowest id whose sex is `M`.
2. **The turns** alternate, F, M, F, M. Each speaker's utterances are taken in ascending id order, continuing from where that speaker's previous turn stopped. A turn is the shortest run of them that lasts at least 13.5 s.
3. **The pieces** are the turns' utterances in order, joined with `gap = 0.5`, by S1's recipe.
4. **The references.** `tests/fixtures/two_speakers.rttm` has one `SPEAKER two_speakers 1 <start> <duration> <NA> <NA> <LibriSpeech speaker id> <NA> <NA>` line per piece, at its offset in the clip, with times to the millisecond. `two_speakers.txt` holds the texts in order, as `clip.txt` does.

## P6. The exports' checks (G1-4)

**`tests/subtitle_checks.py`** (G3's items call these names):

```python
def check_srt(path)          # srt.parse, strict; the ffmpeg re-encode to srt
def check_vtt(path)          # webvtt.read; the ffmpeg re-encode to webvtt
def ffmpeg_reencode(path, fmt)   # the spec's command, exactly; asserts exit 0 and empty stdout and stderr
def words(path, fmt, labels) -> list[str]
```

- **`check_srt`:** `list(srt.parse(text))` with its default strictness, which raises on anything it can't parse, and at least one cue. Then `ffmpeg -v error -i <file> -map 0:s:0 -c:s srt -f srt -y /dev/null`.
- **`check_vtt`:** `webvtt.read(path)`, with at least one caption. Then the same command with `webvtt` in place of both `srt`s.
- **`ffmpeg_reencode`** fails, saying so, when `shutil.which("ffmpeg")` is `None`: it never skips.
- **`words`** gives each format's words in order. For `json`, each segment's `text` split on white space. For `txt`, each line after its `] <label>: `. For `srt`, each cue's lines joined, after its `<label>: `. For `vtt`, each cue's text after its voice tag and its `<label>: `, with character references decoded (`html.unescape`).

**`tests/test_g1_4.py`**, marked `models` and tagged `G1-4`, runs for both `clip_run` and `two_speakers_run`, their default four formats:

1. stdout lists four paths, and `<stem>.txt`, `.srt`, `.vtt` and `.json` exist.
2. The JSON validates against `schema/transcript.schema.json` with `jsonschema.Draft202012Validator`.
3. Every segment has a speaker label and timestamps in every format. In the JSON, each segment has `start`, `end` and a `speaker` among `speakers`' ids. In the text file, one line per segment, each `[HH:MM:SS] <label>: `. In the SRT, each cue has `start < end`, and its first line starts `<label>: `. In the WebVTT, each cue starts `<v <label>>` and then `<label>: `, both escaped as `public-api.md` says. Here `<label>` is always one of the JSON's labels.
4. `check_srt` and `check_vtt`.
5. `words(...)` is the same list for all four formats.

Each ffmpeg command and its exit code go to `$GITHUB_STEP_SUMMARY`, which is the spec's "the PR shows both commands exiting 0 on the fixtures' files".

**`product.yml`, `test-models`:** a step before pytest, `command -v ffmpeg || { sudo apt-get update && sudo apt-get install -y --no-install-recommends ffmpeg; }`, then `ffmpeg -version | head -n 1` to the log. The ffmpeg CLI is only CI's checking tool, never shipped, so G1-15's inventory doesn't change.

**Test of the item:** the test passing in the PR's `test-models` (tier 3, for `product.yml`), with the commands in the summary.

## P7. The network and files (G1-13)

**`tests/netguard/sitecustomize.py`**, active only when `LMT_NETGUARD_LOG` is set. It wraps `socket.socket.connect` and `connect_ex`. A connection to an address not in `LMT_NETGUARD_ALLOW` (comma-separated, empty by default) appends one line to the log, `<family> <address>`, and raises `OSError(errno.ENETUNREACH)`. Every family counts, `AF_UNIX` included. It is put on `PYTHONPATH` for a subprocess, so it applies to `lmt` without changing it. G3's guard (`g3-stage1.md`, "The network guard") is this file, with `LMT_NETGUARD_ALLOW=127.0.0.1`.

**`tests/isolation.py`** (G2's and G3's designs assume these names):

```python
def snapshot(*dirs) -> dict[str, tuple]     # absolute path -> ("file", size, sha256) | ("dir",) | ("link", target), recursive
def run_isolated(argv, *, env, cwd) -> Isolated     # CompletedProcess fields, plus .isolated: bool
```

`run_isolated` runs `python -m lmt <argv>`. Where `sudo -n true` succeeds and `unshare` and `setpriv` are on `PATH` (the CI runner), it wraps the run: `sudo -n unshare --net -- setpriv --reuid=<uid> --regid=<gid> --clear-groups -- env -i <env as K=V …> <sys.executable> -m lmt <argv>`, with `isolated = True`. So the run has no network interface but a down loopback, and runs as the runner's own user. Elsewhere it runs `lmt` with `env` alone, and sets `isolated = False`.

**`tests/test_netguard.py`** (the `test` job). A subprocess with the guard: a connection to `127.0.0.1` is logged and refused; with `LMT_NETGUARD_ALLOW=127.0.0.1`, a connection to a closed local port is refused by the system and not logged; `192.0.2.1:80` is logged, and so is `::1` (skipped, saying so, where an IPv6 socket can't be made). Where `run_isolated` isolates, a child that calls `socket.create_connection(("192.0.2.1", 80), timeout=3)` inside it fails with `OSError`. That shows the namespace has no route.

**`tests/test_g1_13.py`**, marked `models`, tagged `G1-13`:

1. Under `tmp_path`: empty `home`, `tmp`, `xdg` and `work` directories, and `net.log` beside them, outside every watched directory.
2. `env`: `PATH` (the job's), `HOME`, `TMPDIR` and `XDG_CACHE_HOME` set to the three, `LMT_CACHE_DIR` set to the job's model cache (`models.cache_dir()`), `PYTHONPATH=tests/netguard` (absolute), `LMT_NETGUARD_LOG` set to `net.log`, and `LANG=C.UTF-8`. Nothing else: no `HF_TOKEN`.
3. `before = snapshot(home, tmp, xdg, work, cache)`. Then `run_isolated(["transcribe", <two_speakers.wav, absolute>], env=env, cwd=work)`, which is the product run, writing to `work`.
4. It exits 0. Under `CI`, `isolated` is true. `net.log` doesn't exist or is empty.
5. `after = snapshot(...)`. The only difference from `before` is four new files, `work/two_speakers.{txt,srt,vtt,json}`. Nothing under `home`, `tmp`, `xdg` or the cache is new, changed or gone, the cache compared by path, size and SHA-256.
6. Each of the four is byte-identical to `two_speakers_run`'s, the run with the network up.

**Test of the item:** the tests above in the `test` and `test-models` jobs of the PR. **If a library writes a file** (a config or cache under `home`, say) **or opens a socket,** that is a product finding. The Developer reports the path or address on the item for the Architect. The test doesn't allow it.

## P8. `lmt eval --set full` and `--update-readme`

G1-6 needs a test of every documented option, and these two are `eval`'s last. The Owner's run of them is stage 3's. They're built now, on P2's runner, and tested on fakes.

- **`runner.evaluate_full(out_dir, *, update_readme)`.** It runs `evaluate("ci", …)` twice, into a temporary directory, then sets `determinism = {"runs": 2, "identical": report.same_accuracy(first, second)}` (G1-12's Mac half). Then it runs `evaluate("full", out_dir=out_dir, determinism=determinism)`, over the sets #123 adds (LS full, AMI full and the one-hour recording), one file at a time (G1-9). With `update_readme`, it ends with `readme.update(Path("README.md"), out_dir / "full.json")`. A tree with uncommitted changes gives `dirty: true`, so `readme.render` refuses the update with exit 3, after `full.json` is written.
- **`cli.py`:** `--set full` calls it and prints `full.json`'s path.
- **`tests/test_eval_full.py`** (the `test` job, tagged `G1-6`), with `datasets.fetch` faked for `ci` and `full`, and P2's fake worker. `--set full` writes `full.json` with `determinism` `{"runs": 2, "identical": true}`. With the fake changed to give different words on the second `ci` run, `identical` is `false`. `--set full --update-readme`, in a temporary git repository with a committed README, rewrites the table between the markers, and `--check-readme` then exits 0.

**Test of the item:** that test, in the `test` job.

## P9. `lmt.transcript.load`, `jsonschema` at run time, and #286

**`load(path) -> Transcript`** (`g3-stage1.md` assumes it):

1. It reads the bytes. An `OSError` is `InputError(str(path), "can't read: <strerror>")`.
2. It parses them as UTF-8 JSON. A failure is `InputError(str(path), "not JSON: <msg>")`, and a top level that isn't an object is `"not a transcript"`.
3. If `schema_version` is a string whose part before the first `.` isn't `1`, that is `InputError(str(path), f"schema version {v} isn't supported; this lmt reads 1.x")`. This check comes before the schema's, so the message is clear.
4. It calls `schema.validate(doc)`, and re-raises its error as `InputError(str(path), e.problem)`.
5. It returns `from_dict(doc)`.

**`jsonschema` at run time.** It moves from the `dev` group to `[project] dependencies`, at the lock's version (`jsonschema>=<locked major.minor>`), and `uv lock` refreshes `uv.lock`. Its tree is already locked and licence-checked as part of `dev`, so the inventory doesn't change. The PR shows `check_licences.py` passing on both platforms.

**#286, finding 1: the patterns' end.** Python's `jsonschema` matches a pattern with `re.search`, whose `$` also matches before a final newline. So `"Ana\n"` passes in Python and fails in a JavaScript validator, and `"S1\n"` and `"1.1\n"` slip through the same way. In all three patterns (`schema_version`, `speaker_id` and the label), the final `$` becomes `(?![\s\S])`, written `(?![\\s\\S])` in JSON. That means the end of the string in ECMA-262 and in Python alike. (`\Z` isn't ECMA-262, and in JavaScript's non-Unicode mode it would match a literal `Z`.) The rule in `public-api.md` doesn't change: the schema now states it exactly. `g3-stage1.md`'s copy of the label pattern changes with this design.

**#286, finding 2: outputs' file mode.** It isn't intended. An output should get the mode any newly created file gets, `0o666` less the user's umask, as `open(path, "w")` would give it, not `mkstemp`'s `0o600`. So `export.write` creates each temporary file with `os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)`, where `temp` is `out_dir / f".{stem}.{secrets.token_hex(8)}.{fmt}.tmp"`, trying a new name on `FileExistsError`. The kernel applies the umask. Everything else in `write` stays: the renames happen only once every file is written, and the clean-up runs on any exception.

**Tests.** `tests/test_transcript.py`:
- `load` of the committed export golden (`tests/fixtures/export/transcript.json`) renders each format byte-identical to its golden.
- A missing file, a directory, `{` alone, `[]`, a document without `segments`, and `schema_version` `"2.0"` each raise `InputError` naming the path, with the problems above.

`tests/test_export.py`:
- The schema rejects the labels `"Ana\n"`, `" Ana"`, `""` and 33 code points, and the values `"S1\n"` for an id and `"1.1\n"` for `schema_version`. It accepts `"José & Ana"` and 32 code points.
- Under `os.umask(0o022)`, `write`'s outputs have mode `0o644`. Under `0o077`, they have `0o600`. The test restores the umask.

**Test of the item:** those tests, in the `test` job, and the licence check passing.

## P10. The registry

`tests/criteria.toml` gains a block for each criterion whose checks have all passed on `main`, each with a comment naming its tests, as `[G1-1]` and `[G1-2]` have:

| Criterion | Complete when | Its tests |
| --- | --- | --- |
| G1-3 | P5 | `tests/test_g1_3.py` |
| G1-4 | P6 | `tests/test_g1_4.py` |
| G1-5 | P2 | `tests/test_eval_ci.py` |
| G1-6 | P3, P2 and P8 | `tests/test_cli.py`, `tests/test_g1_6.py`, `tests/test_models.py`, `tests/test_readme_table.py`, `tests/test_eval_runner.py`, `tests/test_eval_full.py` |
| G1-7 | P3 and #110, with the Reviewer's reading of `public-api.md` on this PR | `tests/test_asr.py`, `tests/test_manifests.py`, `tests/test_cli.py` |
| G1-8c | P2 | `tests/test_eval_ci.py` |
| G1-11 | P4 | `tests/test_g1_11.py`, `tests/test_decode_fuzz.py` |
| G1-13 | P7 (its `owner` line stays) | `tests/test_g1_13.py` |

**G1-12 stays incomplete.** Its Mac half, the two runs before the full run, is read from `results/full.json` by stage 3's `check_results.py` (`criteria.md` §2).

`.github/scripts/test_criteria.py`'s `RealSpecTest.test_real_registry_loads` lists the complete criteria, and gains the same IDs. #300 says `against_base.py` refuses a change to that file. If #300 isn't fixed by then, the PR says so for the Reviewer.

**Test of the item:** `tests/test_criteria.py` and `.github/scripts/test_criteria.py`. `criteria.py` on `main`'s latest records then gives `pass` for each criterion marked, and the PR shows that output.

## Risks

- **G1-8c's first reading** may miss a bound, most likely AMI-ci's DER or WER on one 300 s meeting. That goes to the Architect (P2, above). The bounds don't move.
- **Determinism of the double run.** ASR and diarization run side by side. If the two runs differ, the Developer sets `pipeline.PARALLEL = False`, as `g1-slice.md` S6 allows, and says so on the item. That changes the product, so the PR is tier 3.
- **`test-models` time.** These items add about 10 product runs on 60 s clips (P4's silence, noise and mutations; P5; P7), on top of G1-1's and G1-2's six. The total comes to about 16 runs, 20 to 25 minutes, inside the job's 30-minute timeout and over the architecture's estimate of about 8 runs. The first PR that passes 1.5 times the estimate says so on its item, and the Architect re-sizes the job in `architecture.md` ("CI"), or splits it.
- **FFmpeg's own log** may reach stderr on a corrupt input, breaking "one line". Then decode sets FFmpeg's log level to quiet through PyAV's `av.logging` before opening the file. That's a product fix, to be found by P4's test.
