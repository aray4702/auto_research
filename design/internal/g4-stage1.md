# Internal design: G4 stage 1 (the engine interface, Kyutai, Apple's helper, words before labels)

| Date (PT) | By | Change |
| --- | --- | --- |
| 2026-10-31 | architect, #578 | §5: the reference clip's two figures (G4-3) and G1-13's checks with Apple's engine (G4-7) in `full-apple.json`; item 6 takes `tests/isolation.py`'s code into `lmt.eval.offline` once #586 merges |
| 2026-10-31 | architect, #574 | First version, for stage 1's items 3 to 6 (#575 to #578), on the Owner's answers 1a–5a on #570 |

This is the detail items 3 to 6 of G4's stage 1 need to start. It works inside `spec/acceptance.md` G4, `design/architecture.md` ("Speech engines (G4)", "Live mode (G2)"), `design/public-api.md` ("G4's changes to the commands", "Results file, version 2.0") and the spike on #565. Where this file and those disagree, they win, and this file is the one to fix. The Reviewer reviews it; the Architect changes it. It is short on purpose (G4 is due today): where it is silent, the item's run decides within the documents above and says so on its PR.

## 0. Who builds what

| Item | Builds from | Owns (only it changes these) | Needs (unmerged) |
| --- | --- | --- | --- |
| **3** #575, Kyutai, Whisper out | §1, §2 | `src/lmt/engines/__init__.py`, `engines/kyutai.py`, `asr.py` (removed), `audio.py` (`Resampler`'s `out_rate`), `cli.py`, `pipeline.py`, `models.py`, `models.toml`, `live/asr.py`, `live/command.py`, `live/session.py`, `pyproject.toml`, `uv.lock`, the transcript fixture, `tests/fake_engine.py` | #573 (CI on `macos-26`), this file |
| **4** #576, Apple's engine | §1, §3 | `src/lmt/engines/apple.py`, `src/lmt/helper/lmt-speech/`, `tests/fixtures/apple/`, `tests/fake_lmt_speech.py`, `tests/test_engine_apple*.py` | this file |
| **5** #577, words before labels | §1, §4 | `live/join.py`, `live/events.py`, `live/output.py`, `schema/live-events.schema.json`, `eval/live.py`'s `read_events` and delays | item 3 |
| **6** #578, the harness | §5 | `eval/` except `eval/live.py`'s reading of events, `.github/scripts/check_results.py`, `README.md`'s table; `tests/isolation.py` once #586 has merged | none; item 4 for G4-3's, G4-5's and G4-7's runs with Apple's engine |

- **Items 3 and 4 run side by side.** Item 4 touches no file item 3 owns. Item 3's registry (§1) already names `apple`, and `models download` and `models list` reach Apple's engine through the module functions §1 fixes. So whichever of the two merges second adds the command-level tests that need both: G4-2's `lmt transcribe --engine apple` and `lmt live --engine apple` exiting 5, and `lmt models download --engine apple` sending the install request.
- **The public API's tables** move with the item that changes the parser (`public-api.md`, "G4's changes"): item 3 for `--engine` on `transcribe`, `live` and `models`, item 5 for the events, item 6 for `lmt eval`.

## 1. The engine interface (`src/lmt/engines/__init__.py`, item 3)

```python
@dataclass(frozen=True)
class Piece:                 # one committed unit of text
    text: str                # starts with " " when it starts a word (the `text` event's `text`)
    start: float             # seconds from the start of the audio, rounded to the ms
    end: float
    starts_word: bool
    probability: float

class Stream:                # one live session
    def feed(self, block: numpy.ndarray) -> list[Piece]   # mono float32 at the stream's rate; committed pieces, in order, once each
    def finish(self) -> list[Piece]                       # the rest, after the source stops
    done_until: float        # seconds: every piece of the audio before this is out
    def close(self) -> None  # frees the model state or stops the helper; safe to call twice

class Engine:
    name: str                # "kyutai" | "apple"
    info: dict               # models.asr: name, engine, repository, revision
    word_gap: float          # §1, "Words": 0.24 for Kyutai, 0.0 for Apple
    peak_rss_bytes: int      # a helper process's peak RSS (Apple), else 0; read by the harness (§5)
    def transcribe(self, audio: numpy.ndarray) -> list[Word]
    def stream(self) -> Stream

ENGINES = ("kyutai", "apple")    # the order `--engine`'s choices and `models list` use
def load(name: str, *, offline: bool) -> Engine          # raises ModelError (exit 5) if it can't run here
def module(name: str)                                    # lmt.engines.kyutai | lmt.engines.apple, imported lazily
```

- **Each engine module** provides `load(offline) -> Engine`, `download() -> None` (for `lmt models download --engine NAME`), and `status() -> dict` with `name`, `status` (a short phrase) and `ok` (for `lmt models list`). `engines.load` imports `lmt.engines.<name>` only when that engine is chosen, so `--engine kyutai` never imports anything of Apple's, and the reverse. Until item 4 merges, `module("apple")` fails to import, and `load("apple")` raises `ModelError("--engine apple", "not in this build")`.
- **`load` runs before any audio is captured or output written** (G4-2): `pipeline.transcribe` and `live/command.py` call it before decode's outputs or the source are opened. `--engine` is `choices=ENGINES`, so any other value is argparse's exit 2 (G4-1).
- **`Word` is `transcript.Word`**, unchanged. `transcript.SCHEMA_VERSION` becomes `"1.2"`, and `models.asr` is `engine.info` (item 3).
- **Words** (`engines.Words`, both modes): `Words(gap)` with `add(pieces, done_until) -> list[Word]`, the words complete now, and `finish() -> list[Word]`. A word is a piece with `starts_word` and the pieces after it up to the next such piece (a first piece without `starts_word` starts a word too). Its `text` is its pieces' texts joined and stripped, `start` its first piece's, `end` its last piece's, and `probability` the lowest of its pieces'. The open word is complete when a piece starting a word arrives, when `done_until ≥ end + gap`, or at `finish` (spec G4 "Terms": 3 steps of 80 ms = 0.24 s). `engines.words(pieces)` is `Words(0)`'s `add` and `finish` in one call, for batch mode.
- **Stand-ins** (`tests/fake_engine.py`, item 3): `FakeEngine(pieces)` gives scripted pieces from `feed` by audio time, and its `transcribe` is `words` of them. Every `test`-job test that ran a stand-in Whisper engine uses it.

## 2. Kyutai (`src/lmt/engines/kyutai.py`, item 3)

- **The model.** `models.toml` pins `kyutai/stt-1b-en_fr-mlx` at a commit, with every file the loop reads (the language model's safetensors, Mimi's, the tokenizer, `config.json`), as published in bf16 (#570, 4a). The Whisper entries go, and so do `default_live_asr` and `models.toml`'s live default. `info["name"]` is `"stt-1b-en_fr"`.
- **Loading** follows the spike's script on #565 (`moshi_mlx`'s STT example): the config's `stt_config` and `audio_delay_seconds`, then `nn.quantize(…, bits=8)` on the language model as `--quantized 8` does, then `LmGen` at temperature 0 (text and audio). Torch isn't involved.
- **The loop.** `Engine.rate` is 24 kHz: batch mode decodes the file straight to it (`audio.decode(path, rate)`, `transcribe(samples, rate=…)`), and live mode's sources keep a copy at it (`AsrTap`, #603), so the stream runs without a resampler. A stream at another rate (`stream(rate)`) resamples with `audio.Resampler(…, out_rate=24000)`. Each whole 1 920-sample frame goes through Mimi's `encode_step` and `LmGen.step`, which gives at most one text token. Before the audio, the loop runs `stt_config`'s silence prefix; those steps aren't counted. A token from step *k* (counting the recording's first frame as 0) is a piece with `start = max(0, k·0.08 − 0.5)` and `end = start + 0.08`, rounded to the ms; padding and special tokens give no piece. Its text is the SentencePiece piece with `▁` replaced by a space, `starts_word` is whether it began with `▁`, and `probability` is the softmax of the step's text logits at the token (if `LmGen` doesn't return them, the run computes them from the step's logits and says how on its PR). `done_until` is `(k+1)·0.08 − 0.5` after step *k*, the end of that step's piece, so 3 empty steps after a word's last piece put it at the word's end + 0.24 s.
- **`finish`** pushes `audio_delay_seconds` + 1 s of silence through the loop and flushes the resampler, so the last words come out. `transcribe(audio)` is `stream()`, one `feed` of the whole recording, `finish`, then `engines.words`. So batch and live give the same words for the same audio.
- **No speech:** `transcribe` returning no word stays `NoSpeechError` (exit 4) in `pipeline.py`. The VAD, `quiet_onnxruntime` and `asr.py` go.
- **Live ASR, still labelling as today** (`live/asr.py`): `StreamingASR(stream, gap)` replaces the LocalAgreement class. `feed(samples)` calls `stream.feed`, passes the pieces and `stream.done_until` to a `Words`, and returns `AsrUpdate(words, done_until)`, with `words` the completed words and `done_until` (samples) the open word's start if there is one, else `stream.done_until`. `flush` calls `finish`. So the join and the session are unchanged in item 3, and item 5 adds the pieces (§4). `AsrConfig`, LocalAgreement and their tests go.
- **Tests in `test-models`** (item 3): G1-1 with Kyutai; G4-1's two runs, with and without `--engine kyutai`, giving the same JSON; and live mode in lockstep on the reference clip at WER ≤ 5.0 % (G4-3). The transcript fixture is regenerated from the Mac's run.

## 3. Apple's engine (item 4)

**The helper** (`src/lmt/helper/lmt-speech/main.swift`, with a `Package.swift` for editing only). Its command line, JSON lines and error codes are the architecture's ("Apple's engine"). Details:
- `transcribe` reads stdin as raw float32 little-endian samples at 16 kHz mono, in any chunk sizes, wraps them as `AVAudioPCMBuffer`s, and converts them with `AVAudioConverter` to `SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith:)`. At end of input it calls `finalizeAndFinishThroughEndOfInput()`, prints every remaining final result, then `{"type": "done"}`, and exits 0.
- A final result's words are its `AttributedString`'s runs that carry an `audioTimeRange`, each run's text stripped; a run of white space alone is skipped. `confidence` is the run's `transcriptionConfidence`, or 1.0 if it has none. Times are seconds from the start of the input, rounded to the ms.
- Each JSON line is flushed as it is written.

**Building it** (`engines/apple.py`):
- The binary is `models.cache_dir() / "helper" / <SHA-256 of main.swift's bytes> / "lmt-speech"`. If it exists, it is used.
- Otherwise: macOS must be 26 or later (`platform.mac_ver()`), and `xcode-select -p` must exit 0. That is checked first, since calling `swiftc` without the Command Line Tools opens macOS's install dialog. Then `xcrun swiftc -O main.swift -o <tmp>/lmt-speech`, into a temporary directory in the same parent, renamed into place, with a 300 s timeout. A failed build is `ModelError` naming the compiler's last stderr line.
- `main.swift` ships in the wheel as package data, read with `importlib.resources`. If the build backend doesn't include it already, item 4 adds the line to `pyproject.toml`, the one file of item 3's it touches, and its test builds the wheel and finds the file there.

**Calling it.** `subprocess.Popen([binary, verb, "--locale", "en-US"], stdin=PIPE, stdout=PIPE, stderr=PIPE)`, never a shell, never a user's path (G4-7). A reader thread parses stdout's lines into a queue; a stderr thread keeps the last 20 lines for `--verbose` and for an internal error's report. When the helper exits, `os.wait4` gives its `ru_maxrss` (bytes on macOS), and `peak_rss_bytes` keeps the largest seen (G4-5).
- **`status()` and `load()`** run `status`. Unsupported, not installed, denied, or macOS < 26, or no Command Line Tools: `ModelError("--engine apple", <reason>)` with the reason and its remedy, as G4-2 and `public-api.md`'s exit code 5 give them. `failed`, a line that isn't JSON, or an exit without `done` or `error` is an internal error (exit 1), with the helper's stderr in the report. `info` is the architecture's version record, from the status line's `os`, `build` and `locale`.
- **`download()`** builds the helper and runs `install`. It is the only caller of `install` (G4-7).
- **`transcribe(audio)`** writes the samples (`audio.astype("<f4").tobytes()`, in 1 MB writes, from a writer thread), closes stdin, and reads until `done`. Each final result's words become pieces, each `starts_word=True` with a leading space, so `engines.words` gives one word per piece. Volatile lines are ignored.
- **`stream()`** starts one `transcribe` helper. `feed` queues the block for the writer thread and returns the pieces the reader has parsed so far, without waiting. `done_until` is the latest final result's `end`, and `word_gap` is 0, so each final word is complete once it is out. `finish` closes stdin and waits for `done`. `Stream.volatile` holds the latest volatile text, or `None` once a final result covers it, for item 5's terminal line.
- **Tests** (`test`), on replies recorded on the Mac by item 4's run, into `tests/fixtures/apple/` (`status-ok.jsonl`, `status-unsupported.jsonl`, `status-not-installed.jsonl`, `transcribe-clip.jsonl` from `tests/fixtures/clip.wav`), with the commands used on the PR. `tests/fake_lmt_speech.py` replays a fixture and records its argv and stdin, and tests point `apple.py` at it with `monkeypatch`. They check G4-2's reasons and lines, G4-7's argv (no user path) and stdin (the samples), that only `download` sends `install`, the cache directory's hash, and the pieces from the recorded transcript. On `macos-26` for real: the helper builds and `status` reports `unsupported` (A5).

## 4. Words before labels (item 5)

- **`AsrUpdate` gains `pieces: list[Piece]`**, every piece `stream.feed` returned, in order. The join's `add_words` keeps them, and `poll` returns, first, a `TextOut(piece)` for each, then the `WordsOut` and `SegmentOut` it returns today. So a piece goes out in the same `poll` that it arrives in, before any label. Labelling, segments and `finish` are unchanged: the join still labels the completed words from `update.words`.
- **`events.py`** writes `TextOut` as the `text` event (`public-api.md`), with `start` and `end` in session time, and `schema_version` `"1.1"` in the `start` event, whose `models.asr` holds `engine`. The schema gets the event. A 1.0 reader's tests still pass on 1.1 output, with `text` events ignored.
- **The terminal** (`output.py`, `public-api.md` "`lmt live`'s stdout"): on a TTY, pieces append to a provisional line (with Apple's engine, `stream.volatile` after them, dimmed), which is redrawn as the labelled line when its words are labelled. Through a pipe, lines are as in 1.0.
- **The delays** (`eval/live.py`): `read_events` also returns the words built from the `text` events with `engines.words`, each with the arrival of the event carrying its last piece. The word delay matches those against the reference (`match_words`, as now); the label delay matches the words from `words` events, at their events' arrival, as the word delay does today. The figure's keys are `public-api.md`'s live figures.
- **G2-6c** (`test-models`, lockstep, the Q10 clip): the word delay ≤ 0.7 s and the label delay ≤ 2.0 s at p95, measured as above on the lockstep clock.

## 5. The harness (item 6)

Item 6 needs nothing from items 3 to 5 to start: results file 2.0's fields, the `-apple` files and the live figures' keys are `public-api.md`'s, and its tests use fixed files. What it takes from the others, once they merge:
- `lmt eval --engine NAME` passes `--engine` to the product commands it runs, and `asr-alone` is `engines.load(name, offline=True).transcribe(audio)` (§1).
- For Apple's engine, a figure's `peak_rss_bytes` is the worker's own peak plus `Engine.peak_rss_bytes` (§3), which the worker reports beside its other figures. `models.asr` and `machine.os` take `Engine.info`'s version record (G4-6).
- The live figures come from `eval/live.py`'s `figure`, whose label-delay keys item 5 adds. Until item 5 merges, item 6's `check_results.py` reads them from fixed files.

**`lmt eval --set full --engine apple`'s two extra steps** (`public-api.md`, "Results file, version 2.0"), after LS full, AMI full and the one-hour recording, so the helper is already built in `LMT_CACHE_DIR`:
1. **The reference clip (G4-3).** `sets.toml`'s `clip` fixture: one product run as for the other datasets (`run_product`, `--no-diarize`), giving the `product` figure; then one `eval/live.py` `run_once` on the clip with the Q10 runs' command (`lmt live --from-file … --jsonl --offline …`) plus `--engine apple`, at real-time pace, scored on the `words` events' committed words, giving the `product-live` figure. Both are dataset `reference clip`, scored as in G1-1 (`score.py`'s WER against `clip.txt`).
2. **G1-13 (G4-7)**, in `src/lmt/eval/offline.py`. Once #586 has merged, item 6 moves `tests/isolation.py`'s `snapshot`, `SANDBOX_PROFILE`, `can_isolate` and `wrap` there, and `tests/isolation.py` imports them, so CI's G1-13 test and the harness share one implementation. The harness first runs `lmt transcribe --engine apple` on `sets.toml`'s `two_speakers` fixture with the network up, into a fresh directory. Then it runs it as `test_g1_13.py` does: fresh `HOME`, `TMPDIR`, `XDG_CACHE_HOME` and working directory, the real `LMT_CACHE_DIR`, the repository's `tests/netguard` on `PYTHONPATH` with `LMT_NETGUARD_LOG` beside the watched directories, wrapped by `wrap`, and the five directories snapshotted before and after. It writes the five fields from that: `exit`, `isolated` (`wrap`'s answer), `connections` (the guard's log lines), `other_changes` (the snapshot's new, changed and gone paths, less the four default outputs) and `outputs_equal` (the four outputs byte for byte against the first run's). A failed check is recorded, not raised, so the run still writes the file; `check_results.py` fails G4-7 on it.
- **What the sandbox covers.** `sandbox-exec`'s profile is inherited by `lmt`'s children, so it covers the helper too; the in-process guard covers Python only. Requests that macOS's own speech service makes on the helper's behalf are outside both, as they are for any app using the recognizer. G4-7 checks what G1-13 checks, and no more.
- **Tests** (`test`): `offline.py` on a stand-in product command (`tests/fixtures/eval/fake_worker.py`'s pattern) that, by its arguments, writes an extra file, opens a connection, or writes different outputs, each giving the field it should; and `check_results.py` failing G4-3 and G4-7 on fixed files with a WER over 5.0 %, a missing `reference clip` figure, a missing or `null` `network_and_files`, and each failing field.
