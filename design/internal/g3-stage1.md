# Internal design: G3 stage 1 (rename, demo, browser test, packaging)

| Date (PT) | By | Change |
| --- | --- | --- |
| 2026-10-29 | architect, #286 | The label pattern ends with `(?![\s\S])`, not `$`, so Python's and ECMA-262's validators agree on a trailing newline |
| 2026-10-28 | architect, #155 | Reviewer's findings on #156: item 3's `lmt rename` writes only the JSON; the network guard allows only `127.0.0.1`; item 4b runs its offline phase as one process inside one namespace, imports `subtitle_checks` lazily, and lists the directories G3-6 compares; the CI cost is corrected to about 960 minutes; `--tag-checkout` takes the README's tag; U+FEFF joins the name tests |
| 2026-10-28 | architect, #155 | First version: phase 3.1's five items, with the CLI wiring and fixtures split out as item 0, the demo split into 2a and 2b, and the full-install script split out as 4b |

This is the detail a Developer or the builder needs to build each of G3 stage 1's items in one run. It works inside `spec/acceptance.md` G3, `design/architecture.md` ("Speaker names (G3, F7)", "Demo (G3, F8)", "Packaging and release (G3, Q5)", "CI"), `design/public-api.md`, the Owner's answers 1a–11a on #83, and `design/internal/g1-stage1.md`. Where this file and those disagree, they win, and this file is the one to fix. The Reviewer reviews this file. The Architect changes it.

## Items, files and order

The coordinator's five items become eight, for two reasons:
- **`cli.py` puts a change in tier 3,** and both `rename` and `demo` need subcommands in it. So item 0 wires both commands to stub modules at once, as G1's item 1a did, with the shared fixtures and the tier exceptions. Items 1, 2a and 2b then never touch `cli.py`: they run at tier 2, and in parallel.
- **Two items were too big for one run.** The demo splits into the server and page (2a) and `--sample` (2b). The full-install sequence (G3-6, G3-8) is a product script of its own (4b), which the builder's workflow calls.

| Item | What | Needs | Files it creates or changes | Tier |
| --- | --- | --- | --- | --- |
| **0** | CLI wiring for `rename` and `demo`, stubs, the transcript and markup fixtures, the network guard, the tier exceptions and the import-graph test | G1.2 (transcript types, schema, exporters, `lmt transcribe`); G1 #107 (`tiers.py`) | `src/lmt/cli.py`, `src/lmt/rename.py` (stub), `src/lmt/demo/__init__.py`, `src/lmt/demo/server.py` (stub), `src/lmt/demo/sample.py` (stub), `tests/fixtures/two_speakers.transcript.json`, `tests/fixtures/markup.py`, `tests/test_fixture_transcript.py`, `tests/netguard/sitecustomize.py`, `tests/test_netguard.py`, `tests/test_import_graph.py`, `tests/test_cli.py` (cases), `.github/scripts/tiers.py`, `.github/scripts/test_tiers.py`, `.github/workflows/product.yml` (`test-models` block) | 3 |
| **1** | `lmt rename` (G3-1 to G3-3) | 0 | `src/lmt/rename.py` (replaces the stub), `tests/test_rename.py`, `tests/test_rename_models.py` | 2 |
| **2a** | `lmt demo`: server and page (G3-4) | 0 | `src/lmt/demo/server.py` (replaces the stub), `src/lmt/demo/static/` (`index.html`, `app.js`, `app.css`, `favicon.svg`), `tests/test_demo_server.py`, `tests/demo_process.py` | 2 |
| **2b** | `lmt demo --sample` (G3-6's mechanics) | 0; G1 #108 (models), #110 (datasets) | `src/lmt/demo/sample.py` (replaces the stub), `tests/test_demo_sample.py`; `src/lmt/eval/datasets.py` only if #110 lacks `sample_meeting` (below) | 2, or 3 with `datasets.py` |
| **3** | The browser test, the `browser` job and the screenshot script (G3-2's Chrome parse, G3-5) | 1, 2a | `pyproject.toml` (Selenium, the `browser` marker), `uv.lock`, `toolchain.toml` (`[licences]` entries, if any), `tests/browser/conftest.py`, `tests/browser/test_page.py`, `tests/browser/screenshot.py`, `tests/browser/test_screenshot.py`, `tests/test_demo_server.py` (the relabel helper), `.github/workflows/product.yml` (`browser` block) | 3 |
| **4a** | Packaging: pins, `## Install`, the install check (G3-7 on Linux) | 0; G1 #122 (licence check) | `pyproject.toml` (pins), `uv.lock`, `tests/test_packaging.py`, `README.md` (`## Install`), `.github/scripts/check_readme.py`, `.github/scripts/test_check_readme.py`, `.github/scripts/check_install.py`, `.github/scripts/test_check_install.py`, `.github/workflows/product.yml` (`test` block) | 3 |
| **4b** | The full-install sequence script (G3-6, G3-8) | 2b, 4a | `.github/scripts/check_full_install.py`, `.github/scripts/test_check_full_install.py` | 1 |
| **5** | Builder: `install-full` and `release.yml` | 4a, 4b; 1, 2a and 2b for `install-full` to pass | `.github/workflows/install-full.yml`, `.github/workflows/release.yml`, `.github/workflows/product.yml` (`install-full` block), `.github/scripts/release_notes.py` | 3 |

**Order.** An item can start once everything it needs has merged:
1. **0.**
2. **After 0:** 1, 2a, 2b and 4a, in parallel.
3. **After 1 and 2a:** 3. **After 2b and 4a:** 4b.
4. **After 4a and 4b:** 5.

**Parallel-safe,** by the files in the table:
- **1, 2a, 2b and 4a** share no file. 4a changes only the `test` block of `product.yml`.
- **3 and 4a** both change `pyproject.toml` and `uv.lock`, and a lock can't be merged by hand. So they don't run at the same time: 4a first, since it is on G3-7's path. If they do overlap, the second updates its branch from `main` and runs `uv lock` again; it never merges `uv.lock`'s text.
- **3 and 4b** share nothing. **5** runs alone.

**Every item's local checks:** `uv run --frozen ruff check`, `uv run --frozen pytest`, and `python3 -I system/gate/generic.py --base origin/main`.

**CI cost.** With the architecture's figures ("CI": 4 runs per PR; tier 2 ≈ 26 and tier 3 ≈ 38 minutes a run with `browser`; tier 1 ≈ 12 with G3-7's install), stage 1 costs about 960 minutes:

| Items | Tier | Minutes |
| --- | --- | --- |
| 0 | 3 | ≈ 150 |
| 1, 2a, 2b | 2 | 3 × 104 ≈ 310 |
| 3 (it touches `pyproject.toml`, `uv.lock` and `product.yml`) | 3 | ≈ 150 |
| 4a | 3 | ≈ 150 |
| 4b | 1 | ≈ 50 |
| 5 | 3 | ≈ 150 |

That is about 250 minutes more than the architecture's "G3's cost" gives for stage 1 (≈ 700). The architecture counts five PRs, with the browser test at tier 2. This design has eight: item 0, 2b and 4b are PRs of their own, and the browser test is tier 3. Running 1, 2a and 2b at tier 2 saves about 144 minutes against running them at tier 3. 2b at tier 3, if it must add `sample_meeting`, adds about 50. So G3's own work comes to about 1,100 minutes, not 850, when the Coordinator plans against the 3,000-minute signal. The jobs that haven't landed yet make the early items cheaper (no `browser` before item 3, no install before 4a), by about 100 minutes in all, so these figures are an upper estimate.

## What stage 1 assumes from G1's stage 2

G1.2 isn't designed yet. These are the interfaces G3's items call. G1.2's design can honour them, or name its own, and this section then follows it. A name below that G1.2 gives differently is a rename for the Developer, not a redesign.

| Interface | Used by | Assumed form |
| --- | --- | --- |
| **The transcript types** (`lmt.transcript`) | 0, 1, 2a, 2b | Frozen dataclasses `Transcript`, `Speaker(id, label)`, `Segment`, `Word`. A `Transcript` carries `schema_version`, `lmt_version`, `source`, `models`, `speakers` and `segments`, as read. `load(path) -> Transcript` reads the file, parses it, validates it against the schema, and checks that `schema_version`'s major part is 1. Any failure is `InputError(str(path), problem)`. |
| **The round trip** | 1 | `load` of a file the JSON exporter wrote, rendered again, gives the same bytes in every format (architecture, "One rendering path"). The JSON exporter writes the transcript's own `lmt_version`, never the running version, so `rename` keeps it (public API, "What it changes"). |
| **The schema** (`lmt.schema`) | 1 | `validate(doc)` raises `InputError`. It holds the label rule, as `public-api.md` "Speaker names" says: `"minLength": 1, "maxLength": 32` and `"pattern": "^[^\\s<>{}=\\x00-\\x1f\\x7f-\\x9f]([^<>{}=\\x00-\\x1f\\x7f-\\x9f]*[^\\s<>{}=\\x00-\\x1f\\x7f-\\x9f])?(?![\\s\\S])"`. The pattern ends with `(?![\s\S])`, not `$`: Python's `$` also matches before a final newline, so under Python's `jsonschema` a `$` pattern accepts `"Ana\n"`, which ECMA-262 rejects (#286). If G1.2 lands without it, item 1 adds it to both copies of the schema, and the PR is tier 3. |
| **The exporters** (`lmt.export`) | 1, 2a | `FORMATS = ("txt", "srt", "vtt", "json")`; `render(transcript, fmt) -> str`, with each `export/<fmt>.py` exposing `render(transcript) -> str`; `write(transcript, out_dir, stem, formats, *, overwrite) -> list[Path]`, which writes temporary files and renames them all into place only when every one is done, and deletes them on failure. It raises `UsageError` for an existing output without `overwrite`, and `InputError` for an unwritable directory. |
| **The WebVTT visible prefix** (6a) | 1, 3 | `export/vtt.py` writes `<v José &amp; Ana>José &amp; Ana: text`, as `public-api.md` already gives it. G1.2 builds it. Item 1 tests it with the rename names (G3-2). |
| **Decode** (`lmt.audio`) | 2a | `decode(path) -> numpy.ndarray`, float32, 16 kHz, mono. Every failure is `InputError(str(path), problem)`. |
| **The pipeline** (`lmt.pipeline`) | 0, 2b | `transcribe(path, *, offline=False) -> Transcript`, with the default models and options, as `lmt transcribe` runs them. |
| **The subtitle checks** (G1-4's test) | 1, 4b | `tests/subtitle_checks.py`: `check_srt(path)` and `check_vtt(path)`, which assert `srt` in strict mode, `webvtt-py`, and the `ffmpeg` re-encode. Whichever jobs run G1-4's test have `ffmpeg`. If the `test` job doesn't, item 1 adds G1.2's install step for it to the `test` block, and its PR becomes tier 3 and protected. |
| **The shared product run** (`tests/conftest.py`) | 0, 1 | A session fixture, `two_speakers_run`, that runs `lmt transcribe` on `tests/fixtures/two_speakers.wav` once, with all four formats, and returns the output directory. |
| **The file snapshot** (G1-13's test) | 2a | `tests/isolation.py`: `snapshot(*dirs) -> dict` of path to (size, SHA-256), and fresh `HOME`, `TMPDIR`, `XDG_CACHE_HOME` and working directory for a subprocess. If G1.2 names it otherwise, 2a uses that. |
| **The CLI table test** (G1-6, item 1a) | 0 | `tests/test_cli.py` compares each command's options in `public-api.md` with the parser's. `public-api.md` has listed `rename` and `demo` since #129. If 1a's test reads every command there, 1a must wire them as stubs itself, and item 0 skips its wiring. Otherwise 1a's test keeps a set of commands it checks, and item 0 adds the two. |

**Two constraints on G1's stage-1 items,** found while designing this:
- **`lmt.eval.datasets` must import nothing from the `eval` extra** (architecture, "Demo": `--sample` uses it on the plain install). G1's design has item 5a (#110) import `Item` from `score.py`, and `score.py` uses `jiwer` and `pyannote.metrics`. So `score.py` must import those two inside the functions that use them (`wer`, `der`), not at the top. Item 0's import-graph test fails otherwise.
- **`--sample` needs AMI-ci's audio alone.** `fetch("ci")` also downloads LibriSpeech's archive (≈ 350 MB) and AMI's annotations. #110 should add `sample_meeting(*, offline=False) -> Path`: AMI-ci's cropped WAV, fetched and verified, downloading only the audio source. Under `offline`, a missing or unverified file is `InputError("LMT_DATA_DIR", "the sample meeting isn't downloaded; run once without --offline")`. If #110 merges without it, item 2b adds it, at tier 3.

## 0. CLI wiring, fixtures and the tier exceptions

### `cli.py`

Two subcommands, with exactly the options in `public-api.md`:
- **`rename`:** `TRANSCRIPT`; `KEY=NAME`, `nargs="*"`; `-o/--output-dir` (default `None`, meaning the transcript's directory); `-f/--formats`. Handler `lmt.rename:cli_rename`.
- **`demo`:** `TRANSCRIPT`, `nargs="?"`; `--audio FILE`; `--sample`; `--port N` (default 0); `--no-browser`; `--offline`. Handler `lmt.demo.server:cli_demo`.
- **`validate(args)`** gains: `--formats` as for `transcribe`; for `demo`, exactly one of `TRANSCRIPT` and `--sample` (a `UsageError` naming `--sample` when both are given, and naming `TRANSCRIPT` when neither is); `--audio` not with `--sample`; `--port` from 0 to 65535.

The stubs raise `NotBuilt("rename")`, `NotBuilt("demo")` and `NotBuilt("demo --sample")`. If 1a's `NotBuilt` names G1's stage in its message, item 0 gives it an optional stage argument, and these pass "G3 stage 1". `lmt.demo.sample`'s stub defines `prepare(*, offline) -> tuple[Path, Path]`, the interface 2a calls and 2b fills in.

### The transcript fixture

`tests/fixtures/two_speakers.transcript.json` is `lmt transcribe tests/fixtures/two_speakers.wav -f json` on Linux, with the default models, renamed from `two_speakers.json`. Its `source.file` is `two_speakers.wav`, which sits beside it, so `lmt demo` finds the audio by default.

- **`tests/test_fixture_transcript.py`,** marked `models`, compares the shared product run's JSON with the committed file, byte for byte (spec, G3 "Terms"). On a mismatch, it copies the run's file into `$LMT_FIXTURE_OUT`, when that is set, and fails.
- **The `test-models` block** sets `LMT_FIXTURE_OUT: ${{ runner.temp }}/fixture` on its pytest step, and adds a last step, `actions/upload-artifact` (pinned SHA), `if: failure()`, with name `transcript-fixture`.
- **How the Developer makes it.** The first commit holds the Developer's own run of the command. If the Linux run differs, the job's artifact holds the Linux file, and the next commit takes it. The PR says which happened. The fixture is never edited by hand.
- **It changes with the version.** The file holds `lmt_version`, so stage 2's version change to `1.0.0` makes this test fail until the fixture is taken again from the artifact. That PR does both.
- **Check of G3-5's segments.** The test also checks that the fixture's first, middle (index `n // 2`) and last segments each last at least 1.6 s. G3-5 checks the marked segment 1 s after the click, so a shorter segment would already be behind the audio. If one is shorter, the Developer reports it on the item, for the Architect, rather than changing the test.

### The markup fixture

`tests/fixtures/markup.py`: `markup_transcript(doc: dict) -> dict` returns a deep copy whose segment 0 text is `<img src=x onerror="window.__lmtXss=1">` and segment 1 text is `<script>window.__lmtXss=1</script>`. Built from the transcript fixture at test time, so it can't fall out of step with it. It still passes the schema, which limits labels, not text.

### The network guard

`tests/netguard/sitecustomize.py`, active only when `LMT_NETGUARD_LOG` is set. It wraps `socket.socket.connect` and `connect_ex`: a connection to any address but `127.0.0.1` is appended, one line, to the log file, and raises `OSError(errno.ENETUNREACH)`. That is G3-4's "allowing only `127.0.0.1`", and it is stricter than G3-8's "loopback", so one guard serves both. `lmt` itself connects to nothing, and the tests' own clients run outside the guard. A caller that swallows the error is still caught, because the test reads the log. Put on `PYTHONPATH` for a subprocess, so it applies to `lmt` without changing it. Item 2a's G3-4 check and item 4b's G3-8 check use it.

`tests/test_netguard.py`: a subprocess with the guard connects to a closed port on `127.0.0.1` (refused, nothing logged), and to `127.0.0.2`, `::1` and `192.0.2.1:80` (each logged, each `OSError`). The guard refuses before the system tries, so these cases don't depend on the machine's interfaces. Only the `::1` case is skipped, saying so, where an IPv6 socket can't be created.

### The tier exceptions

`.github/scripts/tiers.py`'s `TIER3_EXCEPT` gains `"src/lmt/rename.py"` and `"src/lmt/demo/"` (architecture, "CI"). An entry ending `/` matches by prefix, as for the other lists. `test_tiers.py`: `src/lmt/rename.py` → 2, `src/lmt/demo/server.py` → 2, `src/lmt/demo/static/app.js` → 2, and `src/lmt/cli.py` still → 3.

### `tests/test_import_graph.py`

The test that makes those exceptions safe (architecture, "CI"). It runs in the `test` job.
- **`lmt.eval` reaches neither `rename` nor `demo`.** It parses every `.py` under `src/lmt/` with `ast`, builds the graph of `import` and `from … import` edges between `lmt` modules (relative imports resolved, a package import counting as its `__init__`), and asserts that no module under `lmt.eval` reaches `lmt.rename` or anything under `lmt.demo`. Imports inside functions count, so a lazy import can't hide an edge.
- **The plain install's modules load without the `eval` extra.** In a subprocess, a `sys.meta_path` finder raises `ImportError` for `jiwer`, `pyannote.metrics` and their submodules; then `lmt.cli`, `lmt.rename`, `lmt.demo.server` and `lmt.demo.sample` are imported, and must all load.
- **It checks itself:** a fake package in a temporary directory, with an edge from `eval` to `demo` through a third module, fails the first check.

**Test of the item:** `lmt rename --help` and `lmt demo --help` list exactly `public-api.md`'s options (G1-6's table test); each new `validate` rule exits 2 with one line; both stubs exit 1 with one line; the tests above; and the PR's `test-models` run, which passes the fixture test.

## 1. `lmt rename`

### `src/lmt/rename.py`

```python
def parse_pair(arg: str) -> tuple[str, str]        # split at the first "="; UsageError(arg, "expected KEY=NAME")
def check_name(key: str, name: str) -> str          # the trimmed name, or UsageError(key, problem)
def resolve(transcript, pairs) -> dict[str, str]    # speaker id -> new label; UsageError for bad keys
def relabel(transcript, labels: dict[str, str]) -> Transcript   # dataclasses.replace on speakers only
def speaker_table(transcript) -> str
def cli_rename(args) -> int
```

- **`check_name`:** `name.strip()`, then, in this order: empty ("the name is empty"); longer than 32 code points ("the name is N characters; at most 32"); a character in U+0000–U+001F or U+007F–U+009F ("the name has a control character"); one of `<`, `>`, `{`, `}`, `=` ("the name has '<'"). These are exactly the schema's rule: `str.strip()` and the schema's `\s` agree on every character the control ranges don't already exclude. A unit test runs a list of names through both `check_name` and the schema's pattern and asserts they agree, both ways. The list includes U+FEFF at each end of a name: Python's `\s` and `str.strip()` both leave it, so the two agree.
- **The error's subject** is the key, never the name, so a name holding a newline can't split the error line. Text echoed from the user (a key, or an argument without `=`) has its control characters written as `\uXXXX`.
- **`resolve`,** against the transcript as read, before any of this command's renames:
  - a key equal to a speaker's id is that speaker;
  - else a key equal to exactly one speaker's label is that speaker;
  - a key that is the label of several speakers (7a lets names repeat) is `UsageError(key, "is the label of S1 and S3; use an id")`. `public-api.md` doesn't cover this case. This is the Architect's reading of "matched exactly";
  - no match is `UsageError(key, "no speaker has this id or label")`;
  - two pairs that reach the same speaker are `UsageError(key, "names S1 a second time")`.
- **`cli_rename`:**
  1. `transcript = lmt.transcript.load(args.transcript)`. That gives exit 3 for a missing, unreadable, non-JSON or invalid file, or a major version other than 1.
  2. With no pairs: print `speaker_table` to stdout and return 0, writing nothing.
  3. Parse, check and resolve every pair before anything is written, so a bad pair changes nothing.
  4. `paths = lmt.export.write(relabel(...), out_dir, Path(args.transcript).stem, formats, overwrite=True)`, where `out_dir` is `--output-dir` or the transcript's directory. Print each path, one per line, in `--formats` order.
- **`--formats`** is "as for `transcribe`" (`public-api.md`): `rename` writes exactly the formats listed. With `-f srt`, the JSON isn't rewritten, so the names aren't kept in it. This reading follows the option table. The Reviewer may ask for the JSON to be always written instead, which would be a public-API change, for the Owner.
- **`speaker_table`:** tab-separated, a header `ID LABEL SEGMENTS SECONDS FIRST WORDS`, then one line per speaker in `speakers` order. `SECONDS` is the sum of its segments' `end − start`, to one decimal place. `FIRST WORDS` is the first six words of its first segment, with `…` if there are more. A label or word holding a tab is shown with the tab as a space.

### Tests (`tests/test_rename.py`, the `test` job, no model)

Each test copies the transcript fixture into a temporary directory as `two_speakers.json`, so the outputs are `two_speakers.{txt,srt,vtt,json}`. The rename names (spec, G3 "Terms") are the constants `NAME1 = "José & Ana"` and `NAME2 = "Bartholomew J. Fernández-Rodrigo"`, written with precomposed letters, which a test asserts is 32 code points with a space.

- **G3-1:**
  - `S1=NAME1 S2=NAME2` exits 0, and stdout is the four paths. Each file equals `lmt.export.render(t', fmt)`, where `t'` is the loaded fixture with those labels set directly.
  - Each format shows each name where it shows a label: `[HH:MM:SS] <name>: ` in the text, the `<name>: ` cue prefix in the SRT, `<v José &amp; Ana>José &amp; Ana: ` in the WebVTT, and `speakers[].label` in the JSON.
  - The JSON with `speakers` removed equals the fixture with `speakers` removed, and the ids are unchanged.
  - Renaming back with `"José & Ana=Speaker 1" S2="Speaker 2"`, one key by label and one by id, gives the four files the fixture itself renders to (`lmt.export.render(fixture, fmt)`). Item 0's test keeps the fixture equal to `lmt transcribe`'s JSON, and the model test below compares with a real run's files.
  - `S1=" José & Ana "` gives the same bytes as `S1="José & Ana"`.
  - `S1=Alex S2=Alex` exits 0. Both speakers show `Alex`, and the JSON keeps `S1` and `S2`.
  - No pairs: stdout is the table, with the fixture's two speakers, and no file in the directory changes (SHA-256 before and after).
- **G3-2:** with the rename names, `check_srt` and `check_vtt` pass on the outputs. In the WebVTT, both the voice tag's annotation and the cue text have `&amp;` and no bare `&`, and after `html.unescape`, `webvtt-py`'s `voice` for each cue equals its speaker's label. Every cue's lines in both formats are at most two, of at most 42 characters, unless a line is a single word.
- **Names the spec doesn't test:** U+2028 and U+2029 pass the rule, which excludes only control characters, but some parsers treat them as line breaks. A test renames to `"A B"` and runs `check_srt` and `check_vtt`. If they fail, the Developer stops and reports it on the item for the Architect, since tightening the rule is a public-API change. The test is never weakened to pass.
- **U+FEFF in the published schema:** ECMA-262's `\s` includes U+FEFF, but Python's doesn't. So a JavaScript validator of the published schema (F3) rejects a label starting or ending with U+FEFF that `rename` accepts. Item 1 doesn't change the rule. The Architect takes this to the Owner together with the U+2028 result, since either fix changes the schema's pattern.
- **G3-3:** one test per case, each checking the exit code, one stderr line starting `lmt: error:` that names the key or file, and every file in the directory unchanged by SHA-256. The cases: `S9=Bob` (2); `Bob` (2); `S1=` and `S1="   "` (2); a 33-code-point name after trimming (2); `S1="a<b"` (2); `S1="a\nb"` (2); a missing transcript (3); a file holding `{` (3); the fixture with a segment's `start` removed (3); the fixture with `schema_version` `"2.0"` (3); `-o` to a read-only directory (3). Plus the ambiguous label (2) and the same speaker twice (2).
- **`check_name` and the schema agree,** on a list that covers each rule's edge, both ways.
- **G1-6:** one test per option: `-o` writes to another directory and leaves the original's files unchanged; `-f txt,json` writes only those two.

### Test with the models (`tests/test_rename_models.py`, marked `models`)

From the shared product run's directory (copied): `lmt rename` with the rename names, then the rename back. Every file then equals the product run's, byte for byte (G3-1's last sentence).

**Test of the item:** the tests above, in the `test` and `test-models` jobs.

## 2a. `lmt demo`: server and page

### `src/lmt/demo/server.py`

```python
@dataclass
class Demo:
    transcript: Path
    audio: bytes                 # the whole WAV file
    token: str
    port: int = 0                # set once bound
    last_warning: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

def wav_bytes(samples: numpy.ndarray) -> bytes        # 16-bit PCM, 16 kHz, mono, via the wave module into BytesIO
def load(transcript: Path, audio: Path | None, audio_option: bool) -> Demo
def make_server(demo: Demo, port: int) -> ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler)
def cli_demo(args) -> int
```

- **`load`:** `lmt.transcript.load(transcript)` (exit 3 if invalid). The audio is `--audio`, or `source.file` in the transcript's directory. `lmt.audio.decode` turns it into samples, and `wav_bytes` into a WAV: the samples are clipped to [−1, 1], times 32767, rounded, and written as little-endian `int16`. A decode failure is `InputError`, with subject `--audio <path>` when the option gave the file, and `<path>` with the problem ending "; give the recording with --audio" otherwise. A duration that differs from `source.duration` by more than 1 s prints one `lmt: warning: <audio>: lasts <a> s, but the transcript says <b> s`.
- **The token:** `secrets.token_urlsafe(16)`, 128 bits. It is compared with `hmac.compare_digest`.
- **`make_server`:** `ThreadingHTTPServer(("127.0.0.1", port), Handler)`, with `daemon_threads = True` and the `Demo` on the server object. `EADDRINUSE` is `InputError(f"--port {port}", "already in use")` (exit 3). The bound port goes into `demo.port`.
- **`cli_demo`:**
  1. With `--sample`, `transcript, audio = lmt.demo.sample.prepare(offline=args.offline)`, and the audio counts as given by option.
  2. `load`, then `make_server`.
  3. Print `http://127.0.0.1:<port>/<token>/` to stdout and flush.
  4. Unless `--no-browser`, `webbrowser.open(url)` once.
  5. A SIGTERM handler raises a private `_Stop` exception. `serve_forever()` runs until `KeyboardInterrupt` or `_Stop`, then `server_close()` and return 0. So Ctrl-C exits 0, not 130 (`public-api.md`).
  6. `log_message` is a no-op, so stderr carries only warnings and errors.

### The handler

Every response carries `Content-Security-Policy: default-src 'self'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store`. The checks run in this order, so that a page from another site learns nothing:
1. **`Host`** must equal `127.0.0.1:<port>` or `localhost:<port>`, with `localhost` in any case. Otherwise 403, also when the header is missing.
2. **The method** must be GET or HEAD. `BaseHTTPRequestHandler` answers an unknown method with 501, so `Handler.__getattr__` returns a 405 handler, with `Allow: GET, HEAD`, for any `do_<METHOD>` that isn't defined.
3. **The path's first segment** must be the token. Otherwise 404. The query string is ignored.

Routes, under `/<token>/`:

| Path | Content-Type | Body |
| --- | --- | --- |
| `` (the page) | `text/html; charset=utf-8` | `static/index.html` |
| `app.js`, `app.css`, `favicon.svg` | `text/javascript`, `text/css`, `image/svg+xml` (each with `; charset=utf-8`) | the static file |
| `transcript.json` | `application/json` | the file's bytes, once `lmt.transcript.load` accepts them |
| `transcript.vtt` | `text/vtt; charset=utf-8` | `lmt.export.vtt.render(load(...))`, UTF-8 |
| `audio.wav` | `audio/wav` | `demo.audio`, with byte ranges |

Anything else under the token is 404. Static files are read with `importlib.resources.files("lmt.demo") / "static"`, so they come from the wheel.

- **Re-reading the transcript** (G3-4): `transcript.json` and `transcript.vtt` each call `lmt.transcript.load` on every request. On `InputError`, the answer is 500 with a `text/plain` body, the error's text. Under `demo.lock`, the warning `lmt: warning: <file>: <problem>` goes to stderr only if it differs from `demo.last_warning`. A good read clears it. So a file that turns invalid gives one line, not one per request, and the line comes again if the file breaks again after a fix.
- **Byte ranges** (audio only): `Accept-Ranges: bytes` on every audio answer. One range of the form `bytes=a-b`, `bytes=a-` or `bytes=-n` gets 206, with `Content-Range: bytes a-b/<total>` and those bytes. `b` beyond the end is cut to the end. A range starting at or beyond the end gets 416, with `Content-Range: bytes */<total>`. A header that doesn't parse, or several ranges, is ignored, and the whole file comes back with 200, which RFC 9110 allows.
- **HEAD** sends the same status and headers as GET, with no body.

### The page (`src/lmt/demo/static/`)

Plain HTML, JavaScript and CSS, with no build step, no third-party code, and no inline script or style, which the CSP would block. The browser test (item 3) relies on these ids and attributes. They are internal, so 2a and 3 may change them together.

```html
<link rel="icon" href="favicon.svg">           <!-- else Chrome asks for /favicon.ico, gets 404 and logs an error -->
<link rel="stylesheet" href="app.css">
<main>
  <h1>Transcript</h1>
  <p id="error" role="alert" hidden></p>
  <audio id="audio" controls preload="auto" src="audio.wav">
    <track id="track" kind="subtitles" srclang="en" label="Transcript" src="transcript.vtt" default>
  </audio>
  <h2>Speakers</h2>  <ul id="speakers"></ul>
  <h2>Segments</h2>  <ol id="segments"></ol>
</main>
<script src="app.js"></script>
```

`app.js`:
- **Loading:** `fetch("transcript.json", {cache: "no-store"})`. If the answer isn't OK, `#error` shows "The transcript can't be read: " plus the body's text, and is unhidden.
- **Speakers:** one `<li data-speaker="S1">` per speaker, in `speakers` order, reading `<label> · <M:SS>` of speaking time.
- **Segments:** one `<li>` per segment, holding a `<button class="segment" data-index="i" data-start="<start>" data-speaker="<id>">`. The button holds three spans: `.time` (the start as `HH:MM:SS`, rounded down, as the text export writes it), `.speaker` (the label) and `.text`. Every string goes in through `textContent`. `innerHTML`, `insertAdjacentHTML` and `document.write` are never used.
- **Playing:** a button's `click` (which Enter also fires) sets `audio.currentTime = start` and calls `audio.play()`.
- **The current segment:** on `timeupdate` and `seeked`, the current segment is the last one whose `start` ≤ `currentTime`, or none before the first. It gets `aria-current="true"`, every other button loses the attribute, and it is scrolled into view with `{block: "nearest"}`. Using the last start, not `[start, end)`, keeps a segment current through the pause after it.
- **The CSS:** a visible focus outline on buttons, and a distinct background for `[aria-current="true"]`.

### Tests (`tests/test_demo_server.py`, the `test` job, no model)

`tests/demo_process.py` holds `DemoProcess`, a context manager that copies the fixture and the clip into a temporary directory, starts `sys.executable -m lmt demo <args> --no-browser` with fresh `HOME`, `TMPDIR`, `XDG_CACHE_HOME`, working directory and `PYTHONDONTWRITEBYTECODE=1`, and reads the first stdout line within 10 s. It parses the URL as `^http://127\.0\.0\.1:(\d+)/([A-Za-z0-9_-]{22})/$`. On exit it sends SIGINT (or SIGTERM, if asked), waits 10 s, and asserts exit code 0. Requests use `http.client.HTTPConnection`, so the test sets `Host` and the method itself. Item 3 imports `DemoProcess` too.

- **Start and stop:** one URL line within 10 s, and exit 0 on SIGINT and on SIGTERM. Two runs give different tokens.
- **The browser opener:** without `--no-browser`, with `BROWSER` set to `"<python> <recorder.py> %s"`, a script that appends its argument to a file, the file ends with exactly one line, the URL. Python's `webbrowser` reads `BROWSER` on Linux and macOS alike.
- **Loopback only:** in process, `make_server(demo, 0).server_address[0] == "127.0.0.1"`. In the subprocess, a connection to the port on `::1`, and on the machine's first non-loopback IPv4 address if it has one, is refused.
- **Routes:** the page, `app.js`, `app.css` and `favicon.svg` get 200 with their types. `transcript.json` equals the fixture's bytes. `transcript.vtt` equals `export.vtt.render` of the fixture. `audio.wav`'s `wave` header says 16 kHz, mono, 16-bit, with as many frames as `decode` gives. The four security headers are on every answer, the error answers included.
- **Ranges:** `bytes=0-99`, `bytes=1000-`, `bytes=-100` and `bytes=0-<past the end>` give 206 and the bytes that slice gives. `bytes=<total>-` gives 416. `bytes=0-1,5-6` gives 200 and the whole file. A HEAD gives the same headers, with no body.
- **Refusals:** a path without the token, a wrong token, and `/<token>/nothing` give 404. `Host: evil.example:<port>`, `Host: 127.0.0.1:1` and no `Host` give 403. POST, PUT, DELETE, OPTIONS and an invented method, `BREW`, give 405, with `Allow`.
- **Live re-read:** change both labels with `relabel(path, mapping)`, a test helper that writes the JSON alone, through `lmt.export.write(…, ["json"], overwrite=True)`. Item 3 swaps it for `lmt rename -f json`, which writes the same file; plain `lmt rename` would also write a `.vtt` beside it. Then `transcript.json` and `transcript.vtt` show the new labels, with no `.vtt` file anywhere in the directory. Replace the file with `{`: both routes give 500, and the page still gives 200. stderr has exactly one `lmt: warning:` line, naming the file. Write the fixture back, and both routes give 200 again.
- **Errors:** `--audio` naming a missing file (3; stderr names the file and `--audio`); the default audio missing (3); an invalid transcript (3); a port already bound by the test (3); `TRANSCRIPT` with `--sample` (2). A 1-second WAV, written by the test, with the fixture: one `lmt: warning:` line, and it still serves.
- **No file written, no connection out** (G1-13's checks, once more): with the network guard on `PYTHONPATH` and `LMT_NETGUARD_LOG` set, one run serves every route once and is stopped. Then the snapshot of `HOME`, `TMPDIR`, `XDG_CACHE_HOME`, the working directory and the fixture's directory is unchanged, and the guard's log is empty.
- **The page's source:** `index.html` has no `<script>` without `src`, no `style=` attribute, and no URL with a scheme or starting `//`. `app.js` contains none of `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write` and `eval(`.

**Test of the item:** those tests, in the `test` job.

## 2b. `lmt demo --sample`

### `src/lmt/demo/sample.py`

```python
def prepare(*, offline: bool) -> tuple[Path, Path]     # (transcript, audio), both in LMT_DATA_DIR
def cache_key(audio_sha256: str, models: list[Model]) -> str
```

- **The audio:** `lmt.eval.datasets.sample_meeting(offline=offline)`, which is AMI-ci's cropped WAV, verified against `derived_sha256` (G1 design, 5a). Its SHA-256 is the manifest's, so it isn't hashed again.
- **The key:** the SHA-256 of `"<audio sha256>\n" + "\n".join(f"{m.repository}@{m.revision}" for m in default_models(load_manifest()))`, first 16 hex characters. It is computed from the manifest, so a cache hit loads no model.
- **The cache:** `<LMT_DATA_DIR>/sample/<key>/ES2004a.json`.
  - **A hit** is a file that `lmt.transcript.load` accepts. It is used as is.
  - **A miss,** or a file that fails `load`: `lmt.pipeline.transcribe(audio, offline=offline)`. Its `source.file` is the WAV's name. Then `lmt.export.write(t, dir, "ES2004a", ["json"], overwrite=True)`, whose temporary-file rename means a stopped run leaves no half-written transcript.
  - **Under `--offline`,** a missing model is `ModelError` (5), as for `transcribe`.
- **On stderr,** one line before the download ("downloading the sample meeting (AMI ES2004a, CC BY 4.0)") and one before transcribing ("transcribing the sample meeting once; later runs use the cache").
- **Imports:** `lmt.pipeline` is imported inside `prepare`, on a miss only, so a hit never loads torch. Nothing from the `eval` extra is imported (item 0's test).

### Tests (`tests/test_demo_sample.py`, the `test` job, no network or model)

`LMT_DATA_DIR` is a temporary directory. `datasets.sample_meeting` is replaced by a function returning a copy of the two-speaker clip, and `pipeline.transcribe` by one returning the loaded fixture and counting its calls. `load_manifest` is pointed at a small manifest.
- The first `prepare` transcribes once and writes `sample/<key>/ES2004a.json`. The second doesn't call `transcribe`.
- A different model revision in the manifest gives another key, and transcribes again.
- A cached file holding `{` is replaced by a new transcription.
- With `offline=True` and the sample missing, `InputError` with subject `LMT_DATA_DIR` (exit 3 through `cli.main`).
- With `offline=True` and a missing model (`transcribe` raises `ModelError`), exit 5.
- Nothing is written outside `LMT_DATA_DIR`: a snapshot of the other temporary directories is unchanged.
- `cli.main(["demo", "--sample", "--no-browser"])` on the cache serves the cached transcript. Run as `DemoProcess` with `PYTHONPATH` pointing at a test `sitecustomize` that installs the two replacements.

G3-6 itself, with the real download and models, is item 4b's script in item 5's `install-full`.

**Test of the item:** those tests, in the `test` job.

## 3. The browser test

### First step

Before the PR, as G1's item 1b did:
1. Add `selenium>=4.25` to the `dev` group, `uv lock`, and run `check_licences.py --inventory` on both platforms' installs. Post both tables on the item. Selenium's own tree is expected to bring `urllib3`, `trio`, `trio-websocket`, `outcome`, `sniffio`, `sortedcontainers`, `attrs`, `wsproto`, `h11`, `websocket-client`, `certifi`, `PySocks` and `typing_extensions`, all in the classes the Owner accepted (§7). The wheel also carries the Selenium Manager executable (Apache-2.0), which isn't a shared library, so the bundled-library check doesn't see it. The inventory names it all the same. Anything outside the allow list goes to the Owner in one batch, and the item stops before its PR.
2. On the runner, through a draft PR's `browser` job, check that headless Chrome advances `currentTime` while playing with no sound device. If it doesn't, the Developer reports the flags tried on the item before changing anything in G3-5.

### The driver (`tests/browser/conftest.py`)

```python
options = webdriver.ChromeOptions()
for a in ("--headless=new", "--window-size=1280,900", "--autoplay-policy=no-user-gesture-required",
          "--mute-audio", "--no-first-run", "--no-default-browser-check"):
    options.add_argument(a)
options.binary_location = shutil.which("google-chrome")
options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
service = Service(executable_path=os.path.join(os.environ["CHROMEWEBDRIVER"], "chromedriver"))
```

- **No browser is downloaded** (#83, answer 8a). The runner image provides `google-chrome` and `chromedriver`, at matching versions, with the driver's directory in `CHROMEWEBDRIVER`. With an explicit driver path, Selenium doesn't call Selenium Manager. The job also sets `SE_OFFLINE=true`, so it couldn't download anyway, and `SE_AVOID_STATS=true`, which stops Selenium Manager sending usage statistics.
- **Where Chrome or the variable is missing,** the fixture fails with a message naming them. It doesn't skip. On a Developer's Mac, `CHROMEWEBDRIVER` points at a local `chromedriver`.
- **The marker:** `pyproject.toml`'s `markers` gains `browser: drives headless Chrome (the browser job)`, and `addopts` becomes `-m 'not models and not data and not browser'`.

### `tests/browser/test_page.py` (marked `browser`)

On the transcript fixture with its clip, through item 2a's `DemoProcess`:
- **The list (G3-5):** `#segments .segment` has one button per JSON segment, in order. Each one's `.time`, `.speaker` and `.text` equal the segment's `start` as `HH:MM:SS`, its speaker's label, and its text. `#speakers li` lists each label.
- **Clicking (G3-5):** for the first, the middle (`n // 2`) and the last segment:
  1. Click its button, and poll every 100 ms for up to 2 s until `audio.paused` is false and `currentTime` is between `start − 0.05` and `start + 1.0`.
  2. Read `t₀ = currentTime`, wait 1 s, and assert `currentTime ≥ t₀ + 0.5`.
  3. Assert that exactly that button has `aria-current="true"`.
  4. Pause the audio before the next segment.
- **The keyboard (G3-5):** from the page's start, send Tab until the middle segment's button has focus (`document.activeElement`), then Enter. The same checks as for a click.
- **The track, after a rename (G3-2, G3-5):**
  1. Run `lmt rename -f json` on the served copy with the rename names, and reload the page. The list and `#speakers` show the new names.
  2. Wait until `track.readyState === 2`. Read every cue's `startTime`, `endTime` and `getCueAsHTML().textContent` through `execute_script`.
  3. Compare with `webvtt-py`'s parse of `lmt.export.vtt.render(load(copy))`: the same number of cues; the same start and end times, to the millisecond (`round(x * 1000)`); and the same text, `webvtt-py`'s caption text with its tags removed and `html.unescape`d on that side, with lines joined by `\n` on both.
- **Its own origin, and no errors (G3-5):** `performance.getEntriesByType("navigation")` and `("resource")` each have a `name` starting with the page's origin. `driver.get_log("browser")` has no entry at level `SEVERE`.
- **The markup fixture (G3-5):** `markup_transcript` of the fixture, written beside the clip and served:
  - segment 0's and 1's `.text` equal their strings exactly, as text;
  - `#segments` holds no `img` or `script` element;
  - `window.__lmtXss` is `undefined` after 1 s.

In `tests/test_demo_server.py`, the `relabel` helper becomes a call to `lmt rename -f json`, now that both items have landed. The `-f json` keeps the directory free of a `.vtt`, which 2a's live re-read test asserts (G3-4: "with no `.vtt` beside it").

### The screenshot script (`tests/browser/screenshot.py`)

```
uv run --frozen python tests/browser/screenshot.py (--transcript PATH [--audio PATH] | --sample) --out PATH
```

1. Starts `lmt demo … --no-browser` through `DemoProcess`, and opens Chrome as above, with a window of 1600 × 1000 at a device scale factor of 1.
2. Clicks the segment nearest 40 % of the way through, waits 1.5 s, and pauses, so a segment is marked.
3. Saves `driver.get_screenshot_as_png()`, then reads the PNG's `IHDR` width.
4. Fails, naming the figure, unless the width is from 1,000 to 2,000 pixels and the file is at most 1,000,000 bytes (G3-9). It never shrinks the image to pass.

Stage 2 runs it with `--sample` to make `docs/demo.png`. Here `tests/browser/test_screenshot.py` (marked `browser`) runs `main()` on the fixture, into a temporary file, and checks both figures.

### The `browser` job (`product.yml`)

```yaml
browser:
  needs: changes
  if: fromJSON(needs.changes.outputs.tier) >= 2
  runs-on: ubuntu-24.04
  timeout-minutes: 15
  env:
    SE_OFFLINE: "true"
    SE_AVOID_STATS: "true"
  steps:
    - actions/checkout (pinned SHA), persist-credentials: false
    - astral-sh/setup-uv (pinned SHA), version: <toolchain.toml [uv] version>, enable-cache: true
    - run: uv sync --locked --all-extras
    - run: google-chrome --version && "$CHROMEWEBDRIVER/chromedriver" --version
    - run: uv run --frozen pytest -m browser
    - run: uv run --frozen python tests/browser/screenshot.py --transcript tests/fixtures/two_speakers.transcript.json --out "$RUNNER_TEMP/demo.png"
    - actions/upload-artifact (pinned SHA), name: demo-screenshot, path: ${{ runner.temp }}/demo.png
```

The artifact lets the Reviewer look at the page without running it. No model is needed, so the job runs at tier 2 (architecture, "CI").

**Test of the item:** the inventory posted; the `browser` job passing on the PR, with every G3-5 check and G3-2's Chrome comparison; the screenshot artifact.

## 4a. Packaging

### First step

Before the PR, check whether `uv tool install git+file://<checkout>@<tag>` takes torch from the CPU index named in `[tool.uv]`. Install from a scratch tag, then print `torch.__version__`. Post the result on the item: `+cpu` or not. Either way G3-7 passes, since versions are compared without local labels (architecture, "Packaging and release").

### The pins (`pyproject.toml`)

Each of `faster-whisper`, `ctranslate2`, `pyannote.audio`, `torch`, `torchaudio`, `av`, `numpy` and `huggingface-hub` becomes `==<the lock's version>`, without a local label. Under PEP 440, `torch==2.8.0` matches `2.8.0+cpu`. Where the lock gives different public versions on the two platforms, there are two lines, with `; sys_platform == 'linux'` and `; sys_platform == 'darwin'`. The existing bounds on these packages go, since the pin replaces them. Then `uv lock`, which must leave the eight versions where they were.

`tests/test_packaging.py` (the `test` job) reads `pyproject.toml` and `uv.lock` with `tomllib`. For each of the eight, every `[[package]]` entry of that name in the lock must match a pin whose marker covers that entry's platform, with the local label dropped. Each must be pinned with `==`. It also checks that `lmt.__version__` equals `pyproject.toml`'s version.

### `README.md`: `## Install`

A new section after `## What it is`, before `## Build`, which is for developers. Stage 2 moves it after `## Results` and `## Demo` (G3-9).

````markdown
## Install

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), installed by its own installer. Nothing else: uv fetches a suitable Python if you don't have one.

```sh
uv tool install git+https://github.com/aray4702/auto_research@v1.0.0
```

The speaker models are gated on Hugging Face. Once, before the first download: make a free account, accept the conditions on the pages of [pyannote/speaker-diarization-3.1](…), [pyannote/segmentation-3.0](…) and [pyannote/wespeaker-voxceleb-resnet34-LM](…), create a read token, and set it as `HF_TOKEN` for `lmt models download`. After that, nothing needs the network.
````

The tag doesn't exist until the release, but CI checks the command against its own checkout's tag (below), and stage 2's release creates it. `lmt models download` stays inline, so the section has exactly one `sh` block. S1-3 runs only `## Build`'s block, so this block isn't run as part of it.

### `check_readme.py`

A new check, `check_install`:
- `## Install` appears once, with exactly one `sh` block.
- The block has exactly one line that isn't blank or a comment, and it matches `^uv tool install git\+https://github\.com/aray4702/auto_research@v\d+\.\d+\.\d+$`.
- The section links `https://docs.astral.sh/uv/getting-started/installation/` and names `HF_TOKEN`.

`--print-install` prints that line and exits, for `check_install.py`. Unit tests in `test_check_readme.py` cover each rule, passing and failing.

### `.github/scripts/check_install.py`

Standard library only. The `test` job runs it, and so do item 5's `install-full` and `release.yml`.

```
python3 -I .github/scripts/check_install.py --platform {linux-x86_64,macos-arm64} --scratch DIR
                                            (--readme ROOT [--tag-checkout] | --wheel PATH)
```

1. **The command:**
   - With `--readme`, it takes the command from `check_readme.py --print-install`, and replaces `git+https://github.com/aray4702/auto_research@` with `git+file://<ROOT resolved>@`. It rewrites the string rather than using git's `insteadOf`, because uv can resolve a GitHub URL's tag through GitHub's API and skip git. With `--tag-checkout`, it first runs `git -C ROOT tag -f <tag> HEAD`, in that checkout only (spec, G3-7). `<tag>` is the one after `@` in the README's line (`v1.0.0`), not `pyproject.toml`'s version, so the redirected URL resolves. Step 3 still compares `lmt --version` with `pyproject.toml`'s version.
   - With `--wheel`, the command is `uv tool install <wheel>`, for `release.yml`.
2. **A clean environment:** the command runs with `UV_TOOL_DIR=<scratch>/tools`, `UV_TOOL_BIN_DIR=<scratch>/bin`, `UV_CACHE_DIR=<scratch>/cache` (so no cache is reused) and `UV_PYTHON_INSTALL_DIR=<scratch>/python`, and with the working directory `<scratch>`. It must exit 0.
3. **The version:** `<scratch>/bin/lmt --version` prints `lmt <pyproject.toml's version>`.
4. **The pins:** in the tool's environment (`<scratch>/tools/local-meeting-transcriber/`), its Python prints `importlib.metadata.version` of the eight packages. Each, without its local label, equals `uv.lock`'s for the platform.
5. **No `eval` extra:** `jiwer` and `pyannote.metrics` aren't installed.
6. **The modules load:** `import lmt.cli, lmt.rename, lmt.demo.server, lmt.demo.sample` succeeds, and `lmt/demo/static/index.html` is in the installed package.
7. **The licences:** `check_licences.py --site <the tool's site-packages> --platform <platform>` passes (G1-15, on what actually installed).

Each step prints `PASS` or `FAIL` and a line to `GITHUB_STEP_SUMMARY`. It exits 1 if any step fails.

`test_check_install.py` (`unittest`) tests the string rewrite, the tag step on a temporary git repository, the label stripping (`2.8.0+cpu` → `2.8.0`), and the lock lookup per platform. The install itself is tested by the job.

### The `test` job

After its existing steps:

```yaml
- name: The one-command install (G3-7, Linux)
  run: python3 -I .github/scripts/check_install.py --platform linux-x86_64 --scratch "$RUNNER_TEMP/install" --readme . --tag-checkout
```

**Test of the item:** `test_packaging.py`, the new `check_readme` and `check_install` unit tests, and the `test` job's install step passing on the PR (about 2 minutes, as the architecture estimates; the PR reports the figure).

## 4b. The full-install sequence (`.github/scripts/check_full_install.py`)

The steps of G3-6 and G3-8 against an installed `lmt`, so that item 5's workflow only calls it. It runs from a checkout with `uv run --frozen`, after the install.

```
uv run --frozen python .github/scripts/check_full_install.py --lmt <scratch>/bin/lmt --platform {linux-x86_64,macos-arm64} --work DIR
```

**Imports.** The module imports only the standard library at the top, so `test_check_full_install.py` can import it in the `readme` job, which runs `python3 -I -m unittest discover -s .github/scripts` with no venv. `tests/subtitle_checks.py` (G1.2), which needs `srt` and `webvtt-py`, is imported only inside `subtitle_checkers()`, the one function that uses it. That function first puts the checkout's `tests/` (`Path(__file__).resolve().parents[2] / "tests"`) on `sys.path`. The file snapshot is the module's own, a few lines of `os.walk` and `hashlib`, rather than `tests/isolation.py`'s.

**The directories.** Under `<work>`: `LMT_CACHE_DIR=<work>/models` and `LMT_DATA_DIR=<work>/data` for every command, and `<work>/out` for the transcripts. Every `lmt` command gets its own fresh, empty `HOME`, `TMPDIR`, `XDG_CACHE_HOME` and working directory, under `<work>/runs/<step>/`, as G1-13's do, with `HF_TOKEN` passed to `models download` alone. The tool's own directory isn't compared: Python's bytecode cache goes there, as for any installed program, and G1-13 compares the same set.

**Steps that run `lmt demo`** (`run_demo`), the same online and offline: start it with `subprocess.Popen`, read the first stdout line within the step's limit, and parse the URL as 2a's `DemoProcess` does. GET the page and `audio.wav` with `http.client` from this same process, each within 10 s, expecting 200 and, for the audio, a body starting `RIFF`. Then `send_signal(SIGTERM)` to the `lmt` process, which is this process's own child, and `wait(10)`. The step passes only if the exit code is 0. A missed limit kills the process with SIGKILL and fails the step, naming the limit.

1. **Online,** in the job's own environment:
   1. `lmt models download` exits 0.
   2. Snapshot `<work>/models`, now that the download has finished, so its own writes don't count.
   3. **G3-6's first run:** `run_demo` on `lmt demo --sample --no-browser`, with a limit of 30 minutes for the URL, since it downloads and transcribes. Afterwards `<work>/data` holds the sample's WAV and one `sample/*/ES2004a.json`. The run's four fresh directories are still empty, and `<work>/models` is unchanged.
2. **Offline.** The script runs this phase as a second process of itself, `python -I check_full_install.py offline …`, with `sys.executable`, so that every command in it, and every fetch, runs in one network namespace:
   - **On Linux,** `offline_command()` gives this argv, so no path passes through a shell string:
     ```
     sudo -n unshare --net -- sh -c 'ip link set lo up && u=$1 g=$2 && shift 2 && exec setpriv --reuid="$u" --regid="$g" --init-groups -- "$@"' \
       sh <uid> <gid> env -i HOME=<work>/runs/offline/home PATH=<the job's PATH> PYTHONDONTWRITEBYTECODE=1 \
       <sys.executable> -I <this script> offline --lmt … --platform … --work …
     ```
     `unshare` without `--fork`, `sh`'s `exec`, `setpriv` and `env` each replace themselves, so the phase's Python runs as the runner's user and is `sudo`'s only child. `sudo` exits with its child's code. The phase's own process starts every `lmt` command as its child, with the environment it builds (the directories above, and the guard's two variables), so neither `sudo`'s reset nor its signal relay is in the way.
   - **On macOS,** the same argv from `env -i` on, with no namespace.
   - **The outer process** waits for the phase with no limit of its own. Every step inside has one, and the job's `timeout-minutes` is the last resort. The phase writes its results to `<work>/offline.json` and exits 0 only if every step passed. The outer process fails the phase if the file is missing, naming the exit code (such as `sudo` failing), or if the exit code and the file disagree.

   Inside the phase, with the network guard (item 0) on `PYTHONPATH` and `LMT_NETGUARD_LOG=<work>/netguard.log` for every `lmt` command:
   1. **On Linux, the cut is real:** `socket.create_connection(("192.0.2.1", 80), timeout=2)` from the phase itself fails with `ENETUNREACH`. Otherwise the phase fails, "the namespace has a route out".
   2. `lmt transcribe <checkout>/tests/fixtures/two_speakers.wav -f txt,srt,vtt,json -o <work>/out` exits 0 within 15 minutes, and `check_srt` and `check_vtt` pass on its files.
   3. `lmt rename <work>/out/two_speakers.json "S1=José & Ana"` exits 0 within 2 minutes.
   4. **G3-6's second run:** snapshot `<work>/data`, then `run_demo` on `lmt demo --sample --no-browser`, with a limit of 10 s for the URL. It has no `--offline`, as in G3-8: the network is simply gone, as when a user turns Wi-Fi off. Afterwards its four fresh directories are still empty, and `<work>/data` and `<work>/models` are unchanged, which with the 10 s shows that it served the cache without transcribing again.
   5. The guard's log is empty or absent.

Each step prints `PASS` or `FAIL`, and the outer process writes every step to the step summary, the offline phase's from its file. Exit 1 on any failure.

`test_check_full_install.py` (`unittest`, standard library only) uses a fake `lmt`, a small Python script that records its arguments, writes placeholder outputs, and for `demo` prints a URL and serves a fixed page and a short `RIFF` body until SIGTERM. It calls the online and offline steps in process, with `subtitle_checkers` replaced by a stub, and covers the order, each limit (a fake that never prints its URL, one that ignores SIGTERM), an exit code other than 0, a file written outside `LMT_DATA_DIR`, a line in the guard's log, and a missing or contradicting `offline.json`. It checks `offline_command()`'s argv for both platforms without running `sudo`. The real run is item 5's first `workflow_dispatch`.

**Test of the item:** the unit tests, in the `readme` job's `unittest discover` (tier 1).

## 5. Builder: `install-full` and `release.yml`

`.github/workflows/` is the builder's. This is what the product side gives it, and what it needs to do.

### From the product side

- **`check_install.py`** (4a): the install and G3-7's checks, from the README's command (`--readme ROOT --tag-checkout`) or from a wheel (`--wheel PATH`).
- **`check_full_install.py`** (4b): G3-6 and G3-8 after the install. It needs `HF_TOKEN` in its environment, a checkout with `uv sync --locked --all-extras` for `tests/subtitle_checks.py` and `ffmpeg`, and, on Linux, passwordless `sudo`, `unshare`, `setpriv` and `ip`, which GitHub's `ubuntu-24.04` image has.
- **The uv version:** `toolchain.toml`'s `[uv] version`.
- **The results table:** between `README.md`'s `<!-- results:start -->` and `<!-- results:end -->` markers (G1's design, item 6). The install command is `check_readme.py --print-install`'s line.

### `install-full` (`.github/workflows/install-full.yml`)

A reusable workflow (`workflow_call`, secret `HF_TOKEN`), with one job on a matrix of `ubuntu-24.04` and `macos-15`, `timeout-minutes: 45`, and `permissions: contents: read`. Its steps, in a clean environment (spec, G3 "Terms"): no `actions/cache`, and no `enable-cache` on setup-uv.
1. Install uv at the pinned version, with `astral-sh/setup-uv` (pinned SHA), its version passed in as an input. A first job reads it from `toolchain.toml` in a sparse checkout and outputs it.
2. Check out into `$RUNNER_TEMP/src`, with `persist-credentials: false`. Nothing there is synced before the install, so the checkout is only the git source the README's URL is redirected to. This is the Architect's reading of "no checkout before the install".
3. `check_install.py --platform <platform> --scratch "$RUNNER_TEMP/install" --readme "$RUNNER_TEMP/src" --tag-checkout`.
4. In `$RUNNER_TEMP/src`: `uv sync --locked --all-extras`, then install `ffmpeg` as G1.2's jobs do.
5. `check_full_install.py --lmt "$RUNNER_TEMP/install/bin/lmt" --platform <platform> --work "$RUNNER_TEMP/work"`, with `HF_TOKEN` in this step's environment only.

`product.yml` gains an `install-full` job that `uses: ./.github/workflows/install-full.yml` when `github.event_name == 'workflow_dispatch'`, passing `HF_TOKEN`. A run of the gate by `schedule` doesn't run it (#83, answer 10a: by hand and at release). A job skipped by its `if` passes, as for the tiers.

### `release.yml`

`workflow_dispatch` with two inputs: `version` (`1.0.0`), and `dry_run` (boolean, default `false`). Without `dry_run` it refuses any ref but `refs/heads/main`. A dry run skips the `draft` job, and its `check` job skips the tag and version tests, so it can run on today's `0.1.0`.
1. **`check`** (`contents: read`, `actions: read`):
   - `pyproject.toml`'s version equals the input.
   - The tag `v<version>` doesn't exist yet.
   - The commit has a successful run of the gate workflow started by `workflow_dispatch`: `gh run list --workflow gate.yml --commit <sha> --event workflow_dispatch --json conclusion`. G3-10 asks for that run, so a run started by a push isn't enough.
2. **`install-full`:** the reusable workflow, on both runners.
3. **`build`** (`contents: read`):
   - `uv build` (sdist and wheel) and `sha256sum * > SHA256SUMS` in `dist/`.
   - `check_install.py --wheel dist/<wheel> --platform linux-x86_64 --scratch …`.
   - `release_notes.py` writes the notes: the install command, then the README's results region exactly as it stands.
   - The three files and the notes are uploaded as an artifact.
4. **`draft`** (`needs: [check, install-full, build]`, `contents: write`, this job only): `gh release create v<version> --draft --target <sha> --title "v<major>.<minor>" --notes-file notes.md dist/*`. The Owner publishes the draft, which creates the tag (G3-10). A draft that already exists for the tag is an error, never overwritten.

`.github/scripts/release_notes.py` (standard library, with a `unittest`) reads `README.md`, and fails if either the markers or the install line is missing.

`check_release.py` (G3-10's check of the published release) is stage 2's, not this item's.

**Test of the item:**
- **Before merging:** a `workflow_dispatch` gate run on the PR's branch in which `install-full` passes on both runners, with each step's summary.
- **After merging:** GitHub dispatches only workflows that are on the default branch. So the builder records a `dry_run` of `release.yml` on `main` on the item, with its artifact's three files and notes. No draft is created before the release.
