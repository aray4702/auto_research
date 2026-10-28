# Internal design: G2 stage 1 (the live pipeline, checked in CI)

| Date (PT) | By | Change |
| --- | --- | --- |
| 2026-10-28 | architect, #157 | First version: phase 2.1's five items, with `lmt live` split into 3a (join, events, lockstep session), 3b (the command and its outputs) and 3c (real time and signals) |
| 2026-10-28 | architect, #157 | The Reviewer's findings on #163: item 5's figures for `the` corrected; item 5 ensures `default_models`, so it needs nothing from item 1; 3b wires `--max-speakers` and names a test per option, plus G2-5 (iii)'s exit 0 and the silent file; `save` removes its temporary files on any exception; reading 9 |

This is the detail a Developer needs to build each of G2 stage 1's items in one run. It works inside `spec/acceptance.md` G2, `design/architecture.md` ("Live mode (G2)", "CI"), `design/public-api.md` (`lmt live`, live events 1.0, `lmt eval --set live`, `results/live.json`, transcript JSON 1.1), the Owner's answers 1a–8a on #82, and `design/internal/g1-stage1.md`. Where this file and those disagree, they win, and this file is the one to fix. The Reviewer reviews this file. The Architect changes it.

## Items, files and order

The coordinator's five items become seven. `lmt live` (the coordinator's item 3) is too big for one run, so it splits by layer: the join, the events and a lockstep session, tested in process (3a); the command, its checks and its outputs (3b); then real time, with worker threads and signals (3c). Each stage module keeps its own settings, so items 1 and 2 share no file.

| Item | What | Needs | Files it creates or changes | Tier |
| --- | --- | --- | --- | --- |
| **1** | Streaming ASR, the file source and lockstep (G2-2; ASR's half of G2-6c) | G1.2: decode, the ASR engine, the fixture clips and the shared product run; G1 #108 (models); G2 item 5 (word matching) | `src/lmt/live/__init__.py` (empty), `src/lmt/live/source.py`, `src/lmt/live/asr.py`, `src/lmt/models.py` (`default_live_asr`, and `default_models` including it), `src/lmt/models.toml` (one key), `tests/test_live_source.py`, `tests/test_live_asr.py`, `tests/test_live_asr_models.py` | 3 |
| **2** | Online diarization (G2-3's speaker half) | G1.2: diarization (torch settings, the models loaded from the cache); G1 #108 | `src/lmt/live/__init__.py` (empty, only if item 1 hasn't merged), `src/lmt/live/diarize.py`, `tests/test_live_diarize.py`, `tests/test_live_diarize_models.py` | 3 |
| **3a** | The join, the events and the lockstep session (G2-1, G2-3, G2-6c) | 1, 2, 5; G1.2: alignment's rules, the transcript types, the schema module | `src/lmt/live/join.py`, `src/lmt/live/events.py`, `src/lmt/live/session.py`, `schema/live-events.schema.json`, `src/lmt/schema/live-events.schema.json`, `tests/test_live_join.py`, `tests/test_live_events.py`, `tests/test_live_session_models.py`, `tests/test_live_asr_models.py` (drops its two-speaker case), G1.2's schema-copy test (one more pair) | 3 |
| **3b** | `lmt live`: the command, its checks before capture, its outputs, stopping at the end and at `--duration`, transcript 1.1 (G2-7, G2-8, G2-4's outputs) | 3a, 4; G1.2: exporters, schema, `lmt transcribe`, G1-6's table test, G1-11's and G1-13's helpers | `src/lmt/cli.py`, `src/lmt/live/command.py`, `src/lmt/live/output.py`, `tests/live_lockstep.py`, `tests/test_live_cli.py`, `tests/test_live_output.py`, `tests/test_live_shared_run.py`, `tests/test_live_session_models.py` (moves onto the shared run), `tests/test_cli.py` (removes the G2 skip); only if G1.2 left them at 1.0: `schema/transcript.schema.json`, `src/lmt/schema/transcript.schema.json`, `src/lmt/export/json.py`, and any committed transcript fixture | 3 |
| **3c** | Real time: worker threads, signals, the second Ctrl-C, the "behind" warning, `-v`, and the tuning entry point (G2-4) | 3b | `src/lmt/live/session.py` (the threaded run), `src/lmt/live/command.py` (signals, `-v`), `src/lmt/live/tuning.py`, `tests/test_live_realtime.py`, `tests/test_live_realtime_models.py`, `tests/test_live_tuning.py`; `.github/workflows/product.yml` (`test-models` timeout) only if the job's measured time needs it | 3 |
| **4** | The microphone source (G2-5), with `sounddevice` locked and vetted first | G1 #122 (lock and licence check); G1.2: decode's resampler | `pyproject.toml`, `uv.lock`, `toolchain.toml` (`[[licences.library]]` entries), `src/lmt/live/mic.py`, `tests/fake_sounddevice.py`, `tests/test_live_mic.py`, `.github/workflows/product.yml` (`test` block: `libportaudio2`); `src/lmt/audio.py` only if G1.2 doesn't expose its resampler | 3 |
| **5** | `lmt eval --set live` and `results/live.json` (G2-6's mechanics), on fixed event logs, with no model | G1 #108 (models), #109 (scoring, results file), #110 (datasets), #111 (`eval/cli.py`); nothing from G2's items | `src/lmt/eval/live.py`, `src/lmt/eval/cli.py` (one branch), `src/lmt/eval/datasets.py` (AMI word times), `src/lmt/eval/report.py` (`TIMING_FIELDS`), `tests/test_eval_live.py`, `tests/fake_live.py`, `tests/fixtures/eval/live/`; `src/lmt/cli.py` only if 1a's parser restricts `--set`'s choices | 3 |

Every item is tier 3: everything under `src/lmt/` is, except the three exporters, `rename.py` and `demo/` (architecture, "CI"). That includes `src/lmt/eval/`, so the architecture's "one (the harness) at tier 1" is wrong by its own rules; see "CI cost".

**Order.** An item can start once everything it needs has merged:
1. **5** first. It needs only G1's models and harness items (#108, #109, #110, #111), so it can land before G1's stage 2. It needs nothing from item 1: it ensures `models.default_models(manifest)`, which item 1 later extends to include the live default.
2. **4** as soon as #122 and G1.2's decode item have landed. Its first step can stop for the Owner, so it starts early.
3. **1 and 2** once G1.2 has landed (and 5, for item 1), in parallel.
4. **3a** after 1, 2 and 5. **3b** after 3a and 4. **3c** after 3b.

**Parallel-safe,** by the files in the table:
- **1, 2, 4 and 5** share no file except `src/lmt/live/__init__.py`, which is empty in both 1 and 2. Two branches adding the same empty file merge cleanly.
- **4** is the only item that changes `pyproject.toml`, `uv.lock` or the `test` block of `product.yml`. **3c** may change only `test-models`' `timeout-minutes`.
- **5 and 3b** both change `src/lmt/cli.py` only in the case named, and never run at the same time.
- **3a, 3b and 3c** run one after another.

**Every item's local checks:** `uv run --frozen ruff check`, `uv run --frozen pytest`, `uv run --frozen pytest -m models` for an item with model tests (the Developer's machine has the models), and `python3 -I system/gate/generic.py --base origin/main`.

## What stage 1 assumes from G1's stage 2

G1.2 isn't designed yet. These are the interfaces G2's items call. G1.2's design can honour them, or name its own, and this section then follows it. A name below that G1.2 gives differently is a rename for the Developer, not a redesign. Where G3's design (`design/internal/g3-stage1.md`) already assumes an interface, this one assumes the same form.

| Interface | Used by | Assumed form |
| --- | --- | --- |
| **Decode** (`lmt.audio`) | 1, 4 | `decode(path) -> numpy.ndarray`, float32, 16 kHz, mono; every failure is `InputError(str(path), problem)` (as G3 assumes). |
| **Decode's resampler** (`lmt.audio`) | 4 | `Resampler(in_rate: int, channels: int)`, with `push(samples) -> ndarray` (float32, shape `(frames, channels)` in; 16 kHz mono float32 out) and `flush() -> ndarray`. Decode resamples through it, so both paths use the same PyAV `AudioResampler` settings. If G1.2 calls `av.AudioResampler` inside `decode` instead, item 4 extracts this class from it, leaving decode's output and tests unchanged. |
| **The ASR engine** (`lmt.asr`) | 1 | `load(model_dir: Path) -> Engine`. `Engine.transcribe(audio, *, initial_prompt: str \| None = None) -> list[Word]`, with G1's settings (language `en`, temperature 0 with no fallback, the fixed beam, word timestamps, the Silero VAD filter). Times are seconds from the start of `audio`, not rounded. |
| **The transcript types** (`lmt.transcript`) | 1, 3a, 3b | Frozen dataclasses `Transcript`, `Speaker(id, label)`, `Segment(start, end, speaker, text, words)` and `Word(start, end, text, probability)`, with `text` stripped of white space (as G3 assumes). |
| **Alignment's rules** (`lmt.align`) | 3a | `assign_speaker(word, turns, previous: str \| None) -> str \| None`: the turn overlapping the word most, else the nearest turn within 1 s, else `previous`. `breaks(previous_word, word, segment_start) -> bool`: a pause of 1 s or more, a sentence end on `previous_word`, or a segment longer than 30 s. `ends_sentence(word) -> bool`. `segment_text(words) -> str`. If G1.2 has these only inside one `align(words, turns)` function, item 3a factors them out without changing its results. |
| **Diarization's setup** (`lmt.diarize`) | 2 | `configure_torch()`: the fixed thread count, `torch.use_deterministic_algorithms(True)` and the seed. `load_segmentation(model_dir)` and `load_embedding(model_dir)`, loading `segmentation-3.0` and `wespeaker-voxceleb-resnet34-LM` from their verified cache directories, never by name. If G1.2 loads them only through the 3.1 pipeline, item 2 loads them itself with `pyannote.audio.Model.from_pretrained(<dir>/pytorch_model.bin)` and `PretrainedSpeakerEmbedding(<dir>/pytorch_model.bin)`. |
| **The exporters** (`lmt.export`) | 3b | `FORMATS`, `render(transcript, fmt)` and `write(...)`, as G3 assumes. Plus `export.txt.render_segment(segment, label) -> str`, the text file's line for one segment, so the growing `<stem>.txt` and the final one can't differ. |
| **The schema** (`lmt.schema`) | 3a, 3b | `validate(doc)` raises `InputError`. **Recommended to G1.2:** write `"schema_version": "1.1"` and accept the optional `source.live` (`true`) and `source.device` (a string) from the start, since `public-api.md` already gives 1.1. Then item 3b needn't touch the schema or any fixture. |
| **The models** (`lmt.models`, #108) | 1, 2, 3b, 5 | `load_manifest`, `default_models`, `ensure(models, offline)` returning each model's verified directory (`g1-stage1.md`, item 3). |
| **Test helpers** | 1, 2, 3a, 3b | `tests/conftest.py`'s session fixture `two_speakers_run` (as G3 assumes), whose JSON gives the offline word times. The committed fixtures `tests/fixtures/clip.wav` with its reference text, and `tests/fixtures/two_speakers.wav` with its RTTM (#123). `tests/isolation.py`: `snapshot(*dirs)` (as G3 assumes), and `run_isolated(argv, *, env, cwd)`, which runs G1-13's subprocess with the network cut where the platform allows it. G1-11's bad inputs, built by `tests/bad_inputs.py`. |

## Shared conventions

- **Positions are counted in samples** at 16 kHz, as integers, and turned into seconds only for output. A block is 1,600 samples (0.1 s), the update interval U 16,000 (1.0 s), the diarization step S 8,000, its window W 80,000 and its latency L 16,000. So every boundary falls on a block boundary, and floating-point error can't move one.
- **Times written out** (word times, event `t`, segment times) are rounded to the millisecond once, when the word is committed or the event is written, as G1 rounds once (architecture, "One rendering path").
- **A stage** is an object with `feed(samples) -> Update` and `flush() -> Update`. `feed` takes any number of samples: one block in lockstep, or every block that has queued up in real time. It runs at most one update per call for ASR, and the steps the catch-up rule gives for diarization (item 2). Stages never start threads. The session decides when to call them.
- **Settings** are a frozen dataclass in each stage's module, `AsrConfig` and `DiarConfig`, with the provisional values below as defaults, and a module-level `DEFAULT_ASR_CONFIG` or `DEFAULT_DIAR_CONFIG` that the command reads. Only `lmt.live.tuning` (item 3c) replaces those, for stage 2's sweep.

## 1. Streaming ASR, the file source and lockstep

### `src/lmt/live/source.py`

```python
BLOCK = 1600

class FileSource:
    def __init__(self, path, *, paced: bool, limit: int | None = None,
                 clock=time.monotonic, sleep=time.sleep): ...
    name: str                       # the file's base name (source.file)
    position: int                   # samples handed over so far; read from any thread
    reason: str | None              # "end-of-input", "duration" or "signal", once it stops
    def blocks(self) -> Iterator[np.ndarray]
    def stop(self) -> None          # thread-safe; the next block isn't handed over

def lockstep(source, *stages) -> Iterator[tuple[int, list]]
```

- **Decoding comes first.** The constructor calls `audio.decode(path)`, so a missing, empty or corrupt file is an `InputError` (3) before capture starts (G2-7, G2-8).
- **`blocks()`** hands over 1,600-sample blocks, the last one shorter if the file ends mid-block. With `limit` (`--duration`, in samples), it stops after exactly `limit` samples, trimming the last block, with `reason = "duration"`. A `stop()` gives `"signal"`; running out gives `"end-of-input"`. `position` is updated before each block is yielded.
- **Pacing.** With `paced=True`, block i is handed over at `t0 + (i + 1) × 0.1 s`, where `t0` is the clock when `blocks()` is first iterated: a block is handed over once its audio would have finished arriving. A block that is already late goes at once, so a slow consumer never makes the source drift from the clock. With `paced=False`, blocks go as fast as they're taken. That is lockstep's source.
- **`lockstep(source, *stages)`** is lockstep (spec, G2 "Terms"): for each block, every stage's `feed` returns before the next block is taken, and it yields `(source.position, [each stage's update])`. After the last block it yields `(position, [each stage's flush()])`. It is single-threaded, so its output doesn't depend on the machine's speed.

### `src/lmt/live/asr.py`

```python
@dataclass(frozen=True)
class AsrConfig:
    update: int = 16000           # U, samples (1.0 s, provisional; stage 2 sweeps 0.5 and 1.0 s)
    max_buffer: int = 240000      # 15 s
    pause: int = 8000             # 0.5 s of non-speech commits the buffer
    prompt_chars: int = 200

@dataclass(frozen=True)
class AsrUpdate:
    words: list[Word]             # committed now, in order, times absolute, rounded to ms
    done_until: int               # no later word can start before this sample

class StreamingASR:
    def __init__(self, engine, config=DEFAULT_ASR_CONFIG): ...
    def feed(self, samples) -> AsrUpdate
    def flush(self) -> AsrUpdate
```

The method is LocalAgreement-2 over a rolling buffer (architecture, "Streaming ASR"; #82, 3a). The state: the buffer (audio from `buffer_start` to the current position), the committed words, and `previous`, the uncommitted tail of the last update's hypothesis.

**When it updates.** `feed` appends the samples. If the position has reached the next boundary (a multiple of U), it runs one update on everything it holds, and the next boundary becomes the first multiple of U after the position. In lockstep, updates fall exactly on the multiples of U. In real time, a worker that fell behind gets several blocks at once and runs one update over all of them, so it catches up and never skips audio (architecture, "Threads").

**An update:**
1. **Transcribe.** `hyp = engine.transcribe(buffer, initial_prompt=prompt)`, where `prompt` is the committed words' text, joined by spaces, cut to its last `prompt_chars` characters at a word boundary, or `None` before the first commit. Shift the times by `buffer_start`, and clip every end to the buffer's end.
2. **Drop what's already committed.** Drop words that end at or before the last committed word's end. Then, if the first k words of `hyp` (k from 5 down to 1) equal the last k committed words by key (below), and the first starts within 1 s of `buffer_start`, drop those k. This is `whisper_streaming`'s n-gram check, for a word cut in two at the buffer's start.
3. **Agreement.** `A` is the longest common prefix of `previous` and `hyp`, comparing each word's **key**: the text casefolded, with leading and trailing punctuation (Unicode category P) and white space removed. Comparing keys means a flicker in punctuation or case doesn't hold back a commit. A committed word takes the current update's text, times and probability.
4. **Pause.** Run Silero VAD on the buffer, through `faster_whisper.vad.get_speech_timestamps` with `VadOptions(min_silence_duration_ms=500, speech_pad_ms=0)`. A gap is a stretch of at least `pause` samples with no speech: between two speech chunks, or from the last chunk to the buffer's end (the buffer with no speech at all is one gap). Take the last gap, `g`. It counts only if no word of `hyp` straddles it: every word starting before `g.end − 0.2 s` ends by `g.start + 0.2 s`. Then `P` is the prefix of `hyp` whose words end by `g.start + 0.2 s`, and the gap's cut is `g.end − 0.2 s`, keeping 0.2 s of lead-in for what follows.
5. **Commit.** `n = max(len(A), len(P))`; commit `hyp[:n]`.
   - Times are clamped so committed words never overlap: each word's start is at least the previous committed word's end, and its end at least its start plus 10 ms. Then they are rounded to the millisecond.
   - The buffer is cut at the gap's cut if a gap counted and `len(P) ≥ len(A)`, else at the last committed word's end, else (nothing committed) not at all. `previous = hyp[n:]`.
6. **Overflow.** If the buffer is still longer than `max_buffer`, commit every word of `hyp[n:]` that ends at least 2 s before the buffer's end, as in step 5, and cut after them. If none does, cut the buffer to its last `max_buffer` samples. Audio cut this way had no words in it. So memory stays within 15 s plus one update (architecture, "Memory").
7. **`done_until`** is `buffer_start` after the cut.

**`flush()`** transcribes what's left (if at least one block), commits every word, as in step 5, and returns `done_until` = the position.

**The live default model.** `models.toml` gains `default_live_asr = "distil-large-v3"`, the offline default, until stage 2 pins it (architecture, "The live model"). `models.py` gains `default_live_asr(manifest) -> Model`, and `default_models` includes it when it differs, so `lmt models download` fetches it (`public-api.md`, `lmt models`), and so does item 5's harness, which ensures `default_models` before its runs. The model test of #108 that totals the default models' bytes then counts it too. `tests/test_live_asr.py` checks that `default_models` includes the live default once, and only when it differs.

### Tests

- **`tests/test_live_source.py`** (no model): a 1.05 s WAV gives 10 blocks of 1,600 and one of 800, `position` rising before each; `limit` trims at exactly `limit` samples with `reason = "duration"`; `stop()` gives `"signal"`; a missing file and an empty one raise `InputError` from the constructor. Pacing with a fake clock and sleep: block i is released at `t0 + (i + 1) × 0.1`, and a late block goes at once. `lockstep` with two recording stages: each sees each block before the next is taken, and the flushes come last.
- **`tests/test_live_asr.py`** (no model): a fake engine that returns scripted hypotheses, keyed by the buffer's start and length. Cases: nothing is committed on the first update; two agreeing updates commit their common prefix, comparing keys (`"started?"` agrees with `"started"`), with the second update's text; a disagreement holds a word back one update; a gap commits the words before it, cuts at `g.end − 0.2 s`, and isn't used when a word straddles it; the n-gram check drops a repeated word at the buffer's start; times are clamped and never overlap; overflow at 15 s commits and cuts; `flush` commits the rest; a `feed` of 25 blocks at once runs one update. The fake engine records each `initial_prompt`, and the test checks its last 200 characters.
- **`tests/test_live_asr_models.py`** (`models`), in lockstep, ASR only:
  - **G2-2:** on `clip.wav`, the committed words, joined by spaces, score WER ≤ 10.0 % against the clip's reference with G1's `score.wer` (G1-1's scoring).
  - **ASR's half of G2-6c:** on `two_speakers.wav`, each committed word's stream delay is the position at its commit minus the matched offline word's end. Words are matched with `lmt.eval.live.match_words` (item 5) against `two_speakers_run`'s word times. p95 ≤ 2.0 s. A word's full delay is at least this one, so this is a necessary condition for G2-6c. Item 3a removes this case once G2-6c's own test runs, so that CI transcribes the clip in lockstep only once.
  - Both print their figures (WER, p95, the number of matched words) to the job's log.

**Test of the item:** the tests above in the `test` and `test-models` jobs.

## 2. Online diarization

### `src/lmt/live/diarize.py`

```python
@dataclass(frozen=True)
class DiarConfig:
    step: int = 8000              # S, 0.5 s
    window: int = 80000           # W, 5 s
    latency: int = 16000          # L, 1.0 s (provisional; stage 2 sweeps 0.5, 1.0 and 1.5 s)
    min_active: float = 0.5       # s of weighted activity for a local speaker to be embedded
    min_update: float = 1.0       # s of weighted activity for its centroid to be updated
    new_speaker: float = 0.70     # cosine distance beyond which a local speaker is new
    gamma: float = 3.0
    beta: float = 10.0
    max_speakers: int = 20

@dataclass(frozen=True)
class Piece:                      # final activity of one speaker
    start: int                    # samples
    end: int
    speaker: int                  # internal id, 0, 1, … in order of creation

@dataclass(frozen=True)
class DiarUpdate:
    pieces: list[Piece]           # newly final, within [previous final_until, final_until)
    final_until: int

class OnlineDiarizer:
    def __init__(self, segmentation, embedding, config=DEFAULT_DIAR_CONFIG, *, record=None): ...
    def feed(self, samples) -> DiarUpdate
    def flush(self) -> DiarUpdate

class NullDiarizer:               # --no-diarize: every sample is speaker 0, final at once
```

The method is Coria et al. (2021), as `diart` implements it, on the pinned models (architecture, "Incremental diarization"; #82, 4a). `configure_torch()` runs first.

**A step at position p** (a multiple of S):
1. **Segment.** The window is the audio in [p − W, p], padded with zeros on the left while p < W. `segmentation-3.0` gives powerset scores per frame. `pyannote.audio.utils.powerset.Powerset(3, 2).to_multilabel` turns them into hard 0/1 activity for three local speakers. Frame times come from the model's own sliding window (`model.receptive_field` in pyannote.audio 3.1 and later, else `model.example_output.frames`). Frames in the padding are ignored.
2. **Embed.** Each local speaker k gets frame weights `w_k(f) = a_k(f)^γ × softmax(β · a(f))_k^γ`, which keeps its frames and down-weights those it shares with another speaker (Coria et al.'s overlap-aware embedding; γ = 3, β = 10). A speaker whose weights sum to at least `min_active` seconds is **active**, and gets an embedding from `PretrainedSpeakerEmbedding(waveform, masks=weights)`. Its result is L2-normalised. A NaN result (too little audio) makes the speaker inactive.
3. **Match.** The cost of local speaker k against speaker g is the cosine distance between k's embedding and g's centroid. `scipy.optimize.linear_sum_assignment` pairs active local speakers with existing speakers, two local speakers never taking the same one. A pair farther than `new_speaker` is undone. A local speaker left unpaired becomes a new speaker, with its embedding as the centroid, unless there are already `max_speakers`. In that case it takes the nearest speaker not taken in this window, or is dropped if there is none. Ties go to the lower index, so the result is deterministic.
4. **Update centroids.** A matched speaker whose weights sum to at least `min_update` seconds adds its embedding to its centroid. A centroid is a sum of unit vectors, which cosine distance doesn't mind.
5. **Accumulate.** Each frame maps to the absolute frame grid (the model's frame step, from sample 0), at the nearest grid index. For every grid frame, the diarizer keeps how many windows covered it and, per speaker, the sum of its activity. An inactive or dropped local speaker adds nothing.
6. **Finalize.** Every grid frame that starts before `p − L` and isn't yet final becomes final. A speaker is active in it if its mean activity over the covering windows is at least 0.5. Runs of active frames become `Piece`s, and the frame accumulators before `final_until` are dropped, so memory stays bounded.

**When it steps, and catching up.** `feed` appends the samples and steps at the multiples of S it has passed. In lockstep that is at most one. When a worker in real time has fallen behind and several are pending, it steps at the last of them, and at earlier ones spaced `W − L` apart going back (4.0 s at L = 1.0 s), so that every frame is inside the window of the step that makes it final. Frames are never skipped, but a diarizer behind by 12 s takes 3 steps, not 24, to catch up.

**`flush()`** steps once at the end of the audio (window [end − W, end]) and makes every frame final up to the end.

**Labels never change** (#82, 4a): a frame, once final, is never touched again, and a speaker's id is fixed when it is created. The join maps ids to `S1`, `S2`, … (item 3a).

**`record`** is for stage 2's tuning. When given a list, each step appends its local activity, its embeddings and its frame times. `replay(records, config) -> list[Piece]` re-runs steps 3 to 6 alone, so new thresholds can be scored on AMI's dev meetings without running the models again.

**The provisional thresholds.** `new_speaker` = 0.70 is `speaker-diarization-3.1`'s own clustering threshold (0.7045…, in its `config.yaml`), for the same embedding model and distance. `min_active` and `min_update` are set so that a local speaker needs half a second to be embedded and a second to move its centroid. Stage 2 tunes all three on AMI's dev meetings, never its test meetings, with `replay`, and this file then records the values (architecture, "Incremental diarization").

### Tests

- **`tests/test_live_diarize.py`** (no model): fake segmentation and embedding models, scripted per window. Matching: a local speaker near a centroid is matched; one beyond `new_speaker` becomes new; two local speakers never take one speaker; at `max_speakers`, a would-be new speaker takes the nearest free one; an inactive one adds nothing. Weights: a frame shared by two speakers weighs less than one held alone. Finalization: nothing is final before `L + S`; each step makes exactly the frames before `p − L` final; a final frame never changes, which the test checks by feeding windows that disagree with it later. Catch-up: 12 s fed at once steps at 4, 8 and 12 s from a last step at 0, and every frame before 11 s is final and covered. `flush` makes everything final. `replay` with the same config gives the same pieces as the live run. `NullDiarizer` gives speaker 0, final at once.
- **`tests/test_live_diarize_models.py`** (`models`): `two_speakers.wav` in lockstep, diarization alone. The final pieces hold exactly two speakers. For each speaker in the clip's RTTM, at least 90 % of their speech time is covered by the speaker most of it is covered by, and those two differ. This is G2-3's rule on time, not words. G2-3 itself is item 3a's, on words. The test also prints the clip's DER.

**Test of the item:** the tests above in the `test` and `test-models` jobs.

## 3a. The join, the events and the lockstep session

### `src/lmt/live/join.py`

```python
@dataclass(frozen=True)
class WordsOut:      # → a "words" event
    segment: int; speaker: str; label: str; words: list[Word]
@dataclass(frozen=True)
class SegmentOut:    # → a "segment" event
    segment: Segment; number: int

class Join:
    def __init__(self): ...
    def add_words(self, update: AsrUpdate) -> None
    def add_activity(self, update: DiarUpdate) -> None
    def poll(self) -> list[WordsOut | SegmentOut]
    def finish(self) -> list[WordsOut | SegmentOut]
    speakers: list[Speaker]; segments: list[Segment]     # the transcript so far
```

- **Labelling.** Words are labelled in order. A word is labelled once `final_until` has reached its end (architecture, "Join"), with `align.assign_speaker(word, turns, previous)`. `turns` are the final pieces, merged per speaker where they touch, **clipped at the word's end**. So the "nearest turn within 1 s" looks only back from the word. Looking ahead would make every such word wait up to 1 s more, against the delay budget. A word that `assign_speaker` leaves without a speaker (the first words, before any turn) takes the first final turn's speaker, and waits for one. At `finish`, if diarization found no speech at all, those words take a speaker of their own.
- **Ids.** An internal speaker gets the next id, `S1`, `S2`, …, and the label `Speaker N`, when its first word is labelled. So the ids follow the first appearance in the transcript, as `public-api.md` requires, and never change.
- **Segments** follow alignment's rules (`align.breaks`). A segment closes when the next labelled word breaks it; at once, when its last word ends a sentence; when ASR's `done_until` has passed its last word's end by 1 s and no word is waiting for a label, since no later word can then join it; and at `finish`. Its `text` is `align.segment_text(words)`.
- **`poll`** returns, in order, one `WordsOut` for each run of words labelled in the same segment, and a `SegmentOut` for each segment that closes. A segment's `SegmentOut` always comes before the next segment's words.
- **`finish`** labels every word left (final activity then reaches the end) and closes the last segment.
- Turns that ended more than 2 s before the oldest unlabelled word are dropped, so the join's memory stays bounded.

### `src/lmt/live/events.py`

```python
class EventWriter:
    def __init__(self, stream, *, jsonl: bool, clock_t: Callable[[], int]): ...
    def start(self, source: dict, models: dict) -> None
    def emit(self, out: WordsOut | SegmentOut) -> None
    def stop(self, reason: str) -> None
```

- **JSONL** (live events 1.0, `public-api.md`): one `json.dumps(event, ensure_ascii=False, separators=(",", ":"))` per line, with the fields in the table's order, then a flush. `t` is `clock_t()` in seconds, rounded to the millisecond: in lockstep, the position of the last block handed over; in real time, the source's `position` as the event is written. `start`'s `t` is 0, and its `schema_version` is `"1.0"`.
- **`start`'s `source`** is the transcript's `source` without `duration`, which isn't known yet: `file`, `live: true`, and `device` for a device.
- **`stop`** is written once the workers have finished the captured audio and the join has emitted every word, just before the final formats are written. Its `t` is the session's length. So `stop` is always the last event, and the time it arrives, against its `t`, is how far behind real time the pipeline ended (item 5's `behind_seconds`).
- **Text** (without `--jsonl`): a segment's first words start a line, `[HH:MM:SS] Speaker 1: ` followed by the words joined by spaces. Later words in the segment add `" " + words` to it. Its `SegmentOut` ends the line. `HH:MM:SS` is the segment's start, rounded down, as the text export writes it. Every write is flushed.

### `schema/live-events.schema.json`

JSON Schema 2020-12: `oneOf` four objects, selected by `type` (`const`), each with exactly the fields of `public-api.md`'s table required and `additionalProperties` allowed, since a minor version adds fields. `t` is a number ≥ 0; `segment` an integer ≥ 1; `speaker` matches `^S[1-9][0-9]*$`; `words` is a non-empty array of `{start, end, text, probability}`; `reason` is one of `end-of-input`, `duration` or `signal`. `start`'s `models` is the transcript schema's `models` object. A copy goes in `src/lmt/schema/`, and G1.2's test that keeps the published schema identical to the package's copy gains this pair.

### `src/lmt/live/session.py`

```python
@dataclass
class Sinks:
    on_block: Callable[[np.ndarray], None] = lambda b: None      # --save-audio (3b)
    on_segment: Callable[[Segment, str], None] = lambda s, l: None  # the growing <stem>.txt (3b)

@dataclass(frozen=True)
class SessionResult:
    speakers: list[Speaker]; segments: list[Segment]; samples: int; reason: str

def run_lockstep(source, asr, diarizer, writer, sinks=Sinks()) -> SessionResult
```

`run_lockstep` writes `start`, then, through `source.lockstep`, after each block: `sinks.on_block`, the two updates into the join, `poll`, and each output through `sinks.on_segment` (for a `SegmentOut`, first) and the writer. At the end: the two flushes, `finish`, the same again, and `stop`. 3c adds the threaded run beside it.

### Tests

- **`tests/test_live_join.py`** (no model), with scripted `AsrUpdate`s and `DiarUpdate`s: a word waits until `final_until` reaches its end, then takes the overlapping speaker; the 1 s rule looks only back; the first words wait for the first turn; ids follow the first labelled word, not the diarizer's internal order; a speaker change, a 1 s pause, a sentence end, a 30 s segment and `done_until` each close a segment; `SegmentOut` comes before the next segment's words; `finish` labels and closes everything.
- **`tests/test_live_events.py`** (no model): each event validates against `schema/live-events.schema.json` and has its fields in order; a stream's lines arrive one per write through a pipe (`os.pipe`, read in another thread), so flushing works; the text form of a fixed sequence, against an expected string.
- **`tests/test_live_session_models.py`** (`models`), one lockstep run of the whole pipeline on `two_speakers.wav`, through `FileSource(paced=False)`, shared by the three cases below. Offline word times come from `two_speakers_run`, and words are matched with `lmt.eval.live.match_words`.
  - **G2-1:** every event validates; `start` first and `stop` last; every committed word has a speaker; words come in order of `start` and never overlap; no `(start, end, text)` appears twice. Every matched offline word whose end is at least 5 s before the clip's end is committed in an event whose `t` is before the clip's duration, so before the last block. The spec's "every word" is read as every matched word, since an unmatched one can't be found in the live stream; the test prints the share matched.
  - **G2-3:** exactly two speakers. For each reference speaker, a committed word belongs to the RTTM turn holding its midpoint (G1-3's rule, by word time). At least 90 % of each speaker's words carry their majority label, and the two majority labels differ.
  - **G2-6c:** each matched word's stream delay is its event's `t` minus the matched offline word's end. p95 by nearest rank (`lmt.eval.live.percentile`) ≤ 2.0 s. It prints p50, p95, the maximum and the number matched.
- **`tests/test_live_asr_models.py`** loses item 1's two-speaker case, which G2-6c now covers. Its G2-2 case stays.

**Test of the item:** the tests above in the `test` and `test-models` jobs.

## 3b. `lmt live`: the command and its outputs

### `src/lmt/cli.py`

Adds the `live` subcommand with exactly `public-api.md`'s options and defaults, with `set_defaults(handler="lmt.live.command:cli_live")`, as G1's item 1a left room for. `tests/test_cli.py`'s table test stops skipping the "(G2)" section, so G1-6 covers `lmt live` (G2-7).

`validate` gains `lmt live`'s rules, each a `UsageError` (2) naming the option: `--formats` as for `transcribe`; `--max-speakers` from 1 to 20; `--no-diarize` with `--max-speakers`; `--device` with `--from-file`; `--duration` a number above 0; `--name` a plain file name (not empty, no `/`, not starting with `.`); `--model` an ASR model in the manifest.

### `src/lmt/live/command.py`

```python
LOCKSTEP = False      # set only by tests/live_lockstep.py: lockstep, not reachable from the CLI
def cli_live(args) -> int
```

**Before capture** (G2-7: every error that can be found before capture is found before it, and nothing is written):
1. `--list-devices`: print the input devices (item 4's `mic.input_devices()`), one per line, `<index>\t<name>\t<channels> ch\t<rate> Hz`, with `*` after the index of the default. With none, print `No input devices.` Exit 0, whatever else is given.
2. The stem: `--name`, else `live-` and the local start time as `YYYYMMDD-HHMMSS`.
3. The output directory exists and is writable, else `InputError` (3). Each `<stem>.<fmt>`, and `<stem>.wav` with `--save-audio`, doesn't exist, unless `--overwrite`, else `UsageError` (2) naming the file.
4. The source. `--from-file`: `FileSource(path, paced=not LOCKSTEP, limit=…)`, which decodes the file now (3 for a missing, empty, corrupt or unsupported file, as `lmt transcribe` gives; G2-8). A device: `mic.resolve(args.device)` (item 4), 3 for no input device, an unknown one, or PortAudio missing. The stream isn't opened yet.
5. The models: `models.ensure(...)` for the ASR model (`--model`, else the live default) and, unless `--no-diarize`, `segmentation-3.0` and the embedding model, honouring `--offline` (5). Then load them.
6. The stages, from the options:
   - ASR: `StreamingASR(asr.load(<the ASR model's directory>), DEFAULT_ASR_CONFIG)`.
   - Diarization: with `--no-diarize`, `NullDiarizer()`, so every segment is `S1`, and `models.diarization` is `null`. Otherwise `OnlineDiarizer(segmentation, embedding, config)`, where `config` is `DEFAULT_DIAR_CONFIG`, or `dataclasses.replace(DEFAULT_DIAR_CONFIG, max_speakers=N)` with `--max-speakers N`. With the option not set, the default's 20 holds, the most the option allows.

**Capture** starts when the stream opens (a device) or the first block is taken (a file). `<stem>.txt` (if `txt` is among the formats) and `<stem>.wav` (with `--save-audio`) are created at that moment, not before. The session runs: `run_lockstep` in 3b, with a paced source unless `LOCKSTEP`; 3c adds the threaded run. A device that fails to open is `InputError` (3), and nothing has been written.

**The models' field**, in the transcript and the `start` event: `asr` is the live model's manifest entry (`name`, `repository`, `revision`). `diarization` is `speaker-diarization-3.1`'s entry, as `lmt transcribe` writes it, since the live method runs on the models that entry's pipeline names, each pinned in the same manifest. It is `null` with `--no-diarize`. `source.live: true` marks the method.

**Stopping in 3b:** the end of the file and `--duration`. Ctrl-C still raises `KeyboardInterrupt`, which `cli.main` turns into 130. 3c gives it its own meaning.

**Exit codes:** 0 when the session ends and the outputs are written, even with no speech: a live session's silence isn't an error (G2-5 (iii)), so `lmt live` never exits 4. It writes a transcript with no segments.

### `src/lmt/live/output.py`

- **`GrowingText(path)`:** opened at capture start (truncated, since step 3 checked it). `append(segment, label)` writes `export.txt.render_segment(segment, label)` and a newline, then flushes. The session calls it for each `SegmentOut` **before** writing the `segment` event, so a reader who has seen the event finds the line in the file (G2-4).
- **`WavWriter(path)`:** 16 kHz mono 16-bit PCM. It writes a 44-byte RIFF header, then each block as it arrives (samples clipped to [−1, 1], times 32,767, rounded, little-endian `int16`, as G3's demo writes WAV). After each block it rewrites the header's two sizes, so the file is a valid WAV of everything so far if the process dies (`public-api.md`, "What a failed session keeps").
- **`save(transcript, out_dir, stem, formats)`:** renders every format with `export.render`, writes each to a temporary file in `out_dir`, then renames them all into place. Before the renames it checks the session's `abandon` flag (3c), and renames nothing if it is set. The temporary files are created and renamed inside a `try`, whose `finally` removes every temporary file not yet renamed. So any exception while writing them, 3c's `KeyboardInterrupt` from a second SIGINT among them, leaves no temporary file and no final format (`public-api.md`, exit 130: "Partial outputs are deleted"). `<stem>.txt` is replaced by the final rendering, which equals what was appended, since both come from `render_segment`.
- **The transcript** is `Transcript(schema_version="1.1", lmt_version, source, models, speakers, segments)`. `source` is `{"file": <the file's base name, or the stem>, "duration": <samples / 16000, to the ms>, "live": true}`, plus `"device": <name>` for a device (`public-api.md`, transcript JSON 1.1).

### `tests/live_lockstep.py`

The one way to reach lockstep from a subprocess: it sets `lmt.live.command.LOCKSTEP = True`, then exits with `lmt.cli.main(sys.argv[1:])`. It isn't part of the package.

### Tests

- **`tests/test_live_cli.py`** (no model; `models.ensure` and the stages replaced by fakes where a case gets that far): `lmt live --help`; each `validate` rule; each case of G2-7, checking exit code, one `lmt: error:` line naming the file or option, no file written, and no audio captured (the fake device's stream was never opened, and no `start` event): an unknown device (3), a missing `--from-file` input (3), an existing output without `--overwrite` (2), `<stem>.wav` existing with `--save-audio` (2), an unwritable output directory (3). The default `--name` has the documented form. G2-5 (ii) through the command: with the stand-in holding no input device, `lmt live` exits 3 with one `lmt: error:` line, and `lmt live --list-devices` exits 0 and prints `No input devices.`; in CI, the same with the real `sounddevice`, as item 4's test does.
  - **What each option does** (G2-7: each option has a test), with fake stages, `tests/fake_sounddevice.py` in `sys.modules["sounddevice"]` (`mic` imports it inside its functions), and `asr.load` and the diarization loaders replaced by fakes that record what they were given:
    - `--max-speakers 3`: the diarizer is built with `config.max_speakers == 3`; without the option, 20.
    - `--no-diarize`: the diarizer is a `NullDiarizer`, every segment of the JSON is `S1`, and `models.diarization` is `null`.
    - `--model <a second ASR model in a test manifest>`: `models.ensure` is asked for that model, and `asr.load` gets its directory; without the option, the live default's.
    - `--device 1` and `--device <a unique part of device 1's name>`: the stand-in's stream opens with `device=1`, and the JSON's `source.device` is that device's name.
    - `-f srt,json`: no `<stem>.txt` is created, at capture start or after; `<stem>.srt` and `<stem>.json` are.
    - `--offline` with an empty `LMT_CACHE_DIR` and the real `models.ensure`: exit 5, one `lmt: error:` line, no output, and no audio captured.
    - `-o <dir> -n meeting`: the outputs are `<dir>/meeting.<fmt>`, and nothing is written in the working directory.

    `-v` is 3c's.
  - **G2-5 (iii) through the command:** with the stand-in's device silent throughout and `--duration 4`, `lmt live` exits 0, stderr holds exactly one `lmt: warning:` line, the silent-device warning, and the JSON has no segments. With a 4 s WAV of exact zeros, `lmt live --from-file` exits 0, stderr holds no `lmt: warning:` line, and the JSON has no segments.
- **`tests/test_live_output.py`** (no model): `GrowingText` holds one line per appended segment and is flushed; `WavWriter`'s file reads back with `wave` after every block, with the right frame count; `save` renames nothing when `abandon` is set, and leaves no temporary file either way. An exception raised between two temporary writes (a patched writer that raises `KeyboardInterrupt` on the second format) propagates, and leaves no temporary file and no final format in `out_dir`.
- **`tests/test_live_shared_run.py`** (`models`): **the shared run**, a session fixture. It runs `tests/live_lockstep.py live --from-file two_speakers.wav --jsonl --save-audio -o <dir>` through `tests/isolation.py`'s `run_isolated`, with fresh `HOME`, `TMPDIR`, `XDG_CACHE_HOME` and working directory, and snapshots before and after. Its cases:
  - **G2-8, network and files:** exit 0; no connection (G1-13's checks); the only new files are the four outputs and `<stem>.wav`; `LMT_CACHE_DIR` unchanged.
  - **G2-4's outputs, in lockstep:** the JSON validates against `schema/transcript.schema.json`, has `schema_version` `1.1` and `source.live` `true`, and its segments' words are exactly the events' committed words, in order, with the same speakers. The text file equals the text exporter's rendering of the JSON. `<stem>.wav` decodes to the clip's samples, within one 16-bit step.
- **`tests/test_live_session_models.py`:** G2-1, G2-3 and G2-6c move onto the shared run's events, whose `t` in lockstep is the position at each event, and its in-process run goes. So CI runs the two-speaker clip in lockstep once.
- **G2-8, bad input** (no model): G1-11's empty file, random bytes named `.wav`, and a `.txt` file each exit 3 through `lmt live --from-file`, with one `lmt: error:` line and no outputs, as `lmt transcribe` does. These go in `tests/test_live_cli.py`.

**Test of the item:** the tests above in the `test` and `test-models` jobs.

## 3c. Real time, signals and the tuning entry point

### The threaded run (`src/lmt/live/session.py`)

`run_threaded(source, asr, diarizer, writer, sinks, *, abandon) -> SessionResult`, which `cli_live` uses unless `LOCKSTEP`:
- **The capture thread** iterates `source.blocks()`. For each block, it calls `sinks.on_block` (the WAV writer) and puts the block on the ASR queue and the diarization queue. At the end, it puts a stop marker on both.
- **Two worker threads**, one per stage. Each takes every block that has queued up, concatenates them and calls `feed` once, so it catches up rather than drifting. At the stop marker it feeds what came before it, calls `flush` and finishes. Each puts `(stage, update, samples processed)` on the main queue. An exception in a worker goes on the main queue too, and the main thread raises it.
- **The main thread** takes from the main queue with a 0.1 s timeout, feeds the join, and writes what `poll` returns, as `run_lockstep` does, with `t` = `source.position` at each write. After both workers finish: `finish`, then `stop`.
- **Behind.** `behind = source.position − min(samples processed by each worker)`. When it first exceeds 5 s, stderr gets one `lmt: warning: live: <n> s behind real time`. It warns again only after `behind` has dropped below 5 s and risen past it again.
- **The microphone's callback** only copies into a queue (item 4), so the capture thread can't make it drop audio.

### Signals and stopping (`src/lmt/live/command.py`)

- **The first SIGINT, or SIGTERM:** `source.stop()`, with `reason = "signal"`. The SIGINT handler is then swapped for the second one. Capture stops, the workers finish the captured audio, and the outputs are written. Exit 0.
- **The second SIGINT,** at any time after the first until exit (the "saving" phase, `public-api.md`): if `save` has started its renames, it only sets `abandon`, too late to matter, and the save finishes with exit 0. Otherwise it raises `KeyboardInterrupt` in the main thread, which waits on its queue with a timeout, so it is woken at once. `cli.main` gives 130. No final format is written, since `save` writes only through temporary files and renames. If the main thread is inside `save`, writing its temporary files, the exception passes through `save`'s `finally`, which removes them (3b, `output.py`). `<stem>.txt` and `<stem>.wav` stay as written so far. The worker threads are daemon threads, so they don't hold up the exit.
- **Further SIGTERMs** after the first are ignored.
- **A bug** in a worker or the main thread exits 1 with the error report (`public-api.md`, exit codes), and keeps `<stem>.txt` and `<stem>.wav`.

### `-v`

At the end, one stderr line per stage: the number of ASR updates with their mean and maximum seconds; the number of diarization steps, likewise; and the largest `behind`. Stage 2 reads these.

### `src/lmt/live/tuning.py`

```
python -m lmt.live.tuning [--set asr.update=8000] [--set diar.latency=24000] … -- live --from-file X --jsonl …
```

It replaces fields of `DEFAULT_ASR_CONFIG` and `DEFAULT_DIAR_CONFIG` with `dataclasses.replace`, each value parsed as the field's type, prints the effective settings to stderr, then exits with `lmt.cli.main(<the arguments after -->)`. An unknown key, or a value that doesn't parse, exits 2. It is internal, like `python -m lmt.eval.datasets`, and isn't in the public API.

### Tests

- **`tests/test_live_realtime.py`** (no model), with fake stages, some made slow with sleeps: a worker that falls 6 s behind gets one `feed` covering all the queued blocks, and the warning appears once; every block reaches both stages; a worker's exception exits 1 and keeps the growing text; the main thread's `t` follows `source.position`.
- **`tests/test_live_realtime_models.py`** (`models`), G2-4, each `lmt live --from-file two_speakers.wav --jsonl -f txt,srt,vtt,json` in a subprocess at real-time pace. The stop cases run at the same time as each other, since they check behaviour, not speed. The run to the end of the file runs alone.
  - **The end of the file:** exit 0, `stop`'s reason `end-of-input`. Each time a `segment` event arrives, `<stem>.txt` already holds a line for every segment closed so far. Its p95 delay, by item 5's functions, with arrival times against the offline word times, goes to the log, and isn't checked.
  - **`--duration 20`:** exit 0, reason `duration`, `source.duration` 20.0.
  - **SIGINT at 20 s, and SIGTERM at 20 s** (after the `start` event's arrival): exit 0, reason `signal`, and no segment ends after 21 s.
  - **For each of the four:** the final formats exist, the JSON validates, and its segments hold exactly the events' committed words, in order, with the same speakers.
  - **The second SIGINT:** SIGINT at 20 s, then again 0.1 s later, while the workers are still finishing: exit 130; no `.srt`, `.vtt` or `.json`, and no temporary file; `<stem>.txt` holds what was written before the stop.
- **`tests/test_live_tuning.py`** (no model): an override reaches the stage the command builds (a fake stage records its config); an unknown key exits 2.

If these runs push `test-models` past its `timeout-minutes`, the item raises the timeout and reports the job's measured time on the item. That changes no check.

**Test of the item:** the tests above in the `test` and `test-models` jobs.

## 4. The microphone source

**First step, before any PR:** add `sounddevice` to `dependencies`, `uv lock`, and run G1's licence check (`check_licences.py --inventory`) on both platforms' installs, as G1's item 1b did. Post both tables on the item. Expected: `sounddevice` (MIT), with `cffi` (MIT) and `pycparser` (BSD-3-Clause) probably already in the tree through `soundfile`, and on macOS the bundled `_sounddevice_data/portaudio-binaries/libportaudio.dylib` (PortAudio, MIT), which gets a `[[licences.library]]` entry for `macos-arm64`. If anything falls outside the Owner's answer 2a (`project_requirements.md` §7), escalate it to the Owner and stop before the PR.

### `src/lmt/live/mic.py`

```python
@dataclass(frozen=True)
class Device:
    index: int; name: str; channels: int; rate: int; default: bool

def input_devices(backend=None) -> list[Device]
def resolve(spec: str | None, backend=None) -> Device
class MicSource:                  # the same interface as FileSource
    def __init__(self, device: Device, *, limit=None, backend=None, warn=print_warning): ...
```

- **`backend`** is the `sounddevice` module, imported inside these functions, never at the top, so `lmt transcribe` and the rest never load PortAudio (architecture, "Capture"). Tests pass `tests/fake_sounddevice.py` instead. If the import fails (Linux without `libportaudio2`), it is `InputError("--device", "can't load PortAudio (<error>); on Linux, install libportaudio2")` (3).
- **`input_devices`:** `backend.query_devices()`, keeping those with `max_input_channels > 0`. `default` marks `backend.default.device[0]`. `channels` is `min(max_input_channels, 2)`, so a many-channel interface isn't mixed down from inputs that carry nothing. `rate` is the device's `default_samplerate`.
- **`resolve`:** `None` is the default input device. A number is an index. Otherwise an exact name, else a unique case-insensitive substring of one. No input device at all is `InputError("--device", "no input device found")`. An unknown one is `InputError("--device", "no input device '<spec>' (lmt live --list-devices lists them)")`. Both 3.
- **`MicSource`** opens `backend.InputStream(device=index, samplerate=rate, channels=channels, dtype="float32", callback=…)` when `blocks()` is first iterated, which is when capture starts. The callback only copies `indata` into a `queue.SimpleQueue`, which has no bound, so a slow stage never makes it drop audio. A status flag (input overflow) is counted, and `-v` reports the count. `blocks()` takes from the queue, resamples through `audio.Resampler(rate, channels)`, and re-blocks into exact 1,600-sample blocks. At the stop it closes the stream, flushes the resampler, and hands over what's left. `name` is the device's name, `position` and `reason` work as for `FileSource`, and so do `limit` and `stop()`.
- **The silent device** (G2-5 (iii)): the device's own samples, before resampling, are checked for their first 3 s (`3 × rate` frames). If every one is exactly 0.0, `warn` writes once: `lmt: warning: <device name>: no sound for 3 s; the microphone may be blocked (System Settings, Privacy & Security, Microphone)` (`public-api.md`). Capture goes on. Any non-zero sample in the first 3 s means no warning, and nothing is checked after them. `FileSource` has no such check.

### `tests/fake_sounddevice.py`

A stand-in for the module: `query_devices()`, `default.device`, and an `InputStream` with the same constructor and `start`, `stop`, `close`, used as a context manager. Its stream calls the callback from its own thread with an array it was given, in blocks of a size it was given, as `(indata, frames, time, status)`. It records whether it was opened, so G2-7's "no audio captured" can be checked. Constructed with a list of devices, possibly none.

### Tests (`tests/test_live_mic.py`)

- **(i) Resampling:** the test makes `X`: `clip.wav` resampled to 48 kHz with PyAV, duplicated to two channels, and quantized to 16 bits. It writes `X` as a 48 kHz stereo 16-bit WAV. The expected samples are `audio.decode(<that WAV>)`. The stand-in delivers `X / 32768` as float32 in 512-frame blocks, and again in 1,000-frame blocks. Each time, the concatenated blocks `MicSource` hands over have the expected length, and no sample differs by more than 1 × 10⁻⁴.
- **(ii) No device:** with the stand-in holding no input devices (only an output device), `resolve(None)` raises exit 3 with the message above, and `input_devices()` is empty. **For real:** a test calls `input_devices()` with the real `sounddevice`. In CI (`CI=true`) it must return no devices. Elsewhere, such as a Developer's Mac, which has a microphone, it is skipped, saying so. If the runner turns out to list an input device, the Developer reports it to the Architect rather than changing the test. 3b adds the same case through `lmt live` and `lmt live --list-devices`.
- **(iii) Silence:** a device that gives exact zeros for 3 s and then speech gets one warning, in the documented form, and capture continues to the stop. One with a single non-zero sample at 2.9 s gets none. One silent throughout gets one warning, and the session ends normally.
- `resolve` by index, exact name, unique substring, and an ambiguous substring (3). A slow consumer: the callback never blocks, and every frame arrives.

### `product.yml`

The `test` job gains `sudo apt-get install -y libportaudio2` before `uv sync`, so (ii) runs for real on the runner (spec, G2-5). Item 3b's `lmt live` cases then run there too.

**Test of the item:** the inventory posted, the licence check passing on both platforms, and the tests above in the `test` job.

## 5. `lmt eval --set live` and `results/live.json`

### `src/lmt/eval/datasets.py`

AMI's recipe gains word times. `ami_words(meeting, crop) -> list[RefWord]`, where `RefWord(start, end, text)`, comes from the same `<w>` elements, in the same order, with the same crop (a word's midpoint before 300 s) as `ref_text`. A word without an `endtime` takes its start time as its end. `fetch_dataset(name) -> list[Item]` fetches one dataset's sources alone, so `--set live` needn't download LibriSpeech, unless #110 already provides it.

### `src/lmt/eval/live.py`

```python
def key(text) -> str                                  # casefold; drop Unicode punctuation (category P); strip
def match_words(ref: Sequence[RefWord], hyp: Sequence[tuple[Word, float]]) -> list[tuple[RefWord, float]]
def delays(matches) -> list[float]                    # time − ref.end, per matched word
def percentile(values, q) -> float                    # nearest rank: the ⌈q·n⌉-th smallest
@dataclass(frozen=True)
class RunLog: lines: list[tuple[float, dict]]; wall_seconds: float; transcript: dict
def read_events(lines) -> tuple[list[tuple[Word, float]], float]   # committed words with arrival; behind_seconds
def run_once(command: list[str], out_dir: Path) -> RunLog
def run_live(wav: Path, *, runs=3, command_prefix=None) -> list[RunLog]
def figure(item, ref_words, logs) -> dict
def cli_eval_live(args) -> int
```

- **Matched words** (spec, G2 "Terms"): both sides go through `key`, and words whose key is empty are dropped. Then `jiwer.process_words(" ".join(ref_keys), " ".join(hyp_keys))`, and every `equal` chunk of its alignment pairs a reference word with a committed word. Each committed word carries a time: its event's `t` in lockstep (the stream delay, items 1 and 3a), or its event's arrival (Q10's delay). This is the one definition, for CI and the Mac.
- **`run_once`** starts `command` with `stdout=PIPE` and stderr to a file in `out_dir`. It reads lines with `readline()` and stamps each with `time.monotonic()` as it returns. Arrivals are counted from the `start` event's arrival (spec, G2 "Terms"). `wall_seconds` runs from process start to exit. A non-zero exit raises `InputError("lmt live", "exited <code>: <last stderr line>")`.
- **`read_events`** checks that the stream starts with `start`, ends with `stop`, and that `t` never falls. It returns each committed word with its event's arrival, and `behind_seconds` = the `stop` event's arrival minus its `t` (3a writes `stop` once the pipeline has finished).
- **The command** for each of three runs: `[sys.executable, "-m", "lmt", "live", "--from-file", <AMI-ci's WAV>, "--jsonl", "--offline", "-f", "json", "-n", "run<i>", "-o", <a temporary directory>, "--overwrite"]`. Before them, the harness calls `models.ensure(models.default_models(manifest), offline=False)`, so no run downloads. Item 1 makes `default_models` include the live default, so this item needs nothing from item 1 and never names `default_live_asr`. `run_live(command_prefix=…)` takes the part before `live`, so stage 2's sweep can pass `python -m lmt.live.tuning --set … --`.
- **`figure`** pools the three runs, in `public-api.md`'s key order: `dataset` `"AMI-ci"`, `system` `"product-live"`, then `wer` and `der` from G1's `score.wer` and `score.der`, over the three final transcripts as three items (the hypothesis as `hypothesis_from_transcript` makes it). Then `audio_seconds` (3 × the clip's duration), `wall_seconds` (the sum), `rtf`, `peak_rss_bytes` (`null`), `p50_delay_seconds`, `p95_delay_seconds` and `max_delay_seconds` over every matched word of every run, `matched_words`, and `runs`: each run's `p95_delay_seconds` and `behind_seconds`. Delays and seconds are rounded to 3 decimal places, WER and DER to 6, as G1 rounds.
- **`cli_eval_live`:** fetch AMI-ci, run three times, write `results/live.json` (or `--out`) with `report.build("live", [figure], models, determinism=None, out_dir=…)`. `models` is the first run's transcript's.
- **`report.py`:** `TIMING_FIELDS` gains `p50_delay_seconds`, `p95_delay_seconds` and `max_delay_seconds`, and `strip_timing` also removes `p95_delay_seconds` and `behind_seconds` inside each `runs` entry (`public-api.md`, "Timing fields").
- **`eval/cli.py`:** `--set live` calls `cli_eval_live`. If 1a's parser limits `--set` to `ci` and `full`, `cli.py` adds `live`.

### Fixtures (`tests/fixtures/eval/live/`) and the hand-computed figures

`ref_words.json`, six reference words: `okay` 0.50–0.80, `so` 0.90–1.00, `let's` 1.10–1.40, `start` 1.40–1.90, `the` 2.50–2.60, `meeting` 2.60–3.10. Three event logs, `run1.jsonl` to `run3.jsonl`, each line `{"arrival": <s>, "event": {…}}`, with a `start` at arrival 0.000 and a 4.0 s clip:

| Run | `words` events (arrival: texts) | `stop` arrival (`t` 4.0) | Matched delays | Run p95 | Behind |
| --- | --- | --- | --- | --- | --- |
| 1 | 2.300: `Okay,` `so`; 3.200: `let's` `start.`; 4.600: `a` `meeting` | 5.100 | 1.5, 1.3, 1.8, 1.3, 1.5 (`a` against `the` doesn't match) | 1.8 | 1.1 |
| 2 | 1.900: `okay` `so`; 2.900: `let's` `start`; 4.100: `the` `meeting` | 4.300 | 1.1, 0.9, 1.5, 1.0, 1.5, 1.0 | 1.5 | 0.3 |
| 3 | 2.000: `okay`; 2.600: `so` `let's` `start`; 3.900: `the` `meeting` `meeting` | 4.000 | 1.2, 1.6, 1.2, 0.7, 1.3, 0.8 (one `meeting` is an insertion; both arrive at 3.900) | 1.6 | 0.0 |

Pooled over the 17 matched words, sorted: 0.7, 0.8, 0.9, 1.0, 1.0, 1.1, 1.2, 1.2, 1.3, 1.3, 1.3, 1.5, 1.5, 1.5, 1.5, 1.6, 1.8. **p50** is the 9th (⌈8.5⌉) = 1.3. **p95** is the 17th (⌈16.15⌉) = 1.8. **max** 1.8. `matched_words` 17. Linear interpolation would give 1.64 for p95, so the test proves the nearest-rank method. `key` makes `Okay,` match `okay`, `start.` match `start`, and `let's` match `let's` (both `lets`). The test compares each figure with these, after rounding to 3 decimal places as `figure` rounds (4.100 − 2.60 is 1.4999… in floating point), and must never be adjusted to whatever the code prints. If the locked `jiwer` aligns run 3's `meeting`s otherwise, the delay is the same, since both arrive together.

### Tests (`tests/test_eval_live.py`, no model)

- `key`, `match_words`, `delays` and `percentile` on the fixtures, against the table. `percentile` on 1 value, and on 20 values at 0.95 (the 19th).
- `read_events` rejects a stream not starting with `start`, not ending with `stop`, or with `t` falling.
- **`run_once`** on `tests/fake_live.py`, a script that prints a scripted `start`, sleeps 0.3 s, prints a `words` event, sleeps 0.2 s, prints `stop` and exits. The arrivals are 0.3 and 0.5 s, each within ±0.1 s. A script that exits 1 raises exit 3 with its last stderr line.
- **`lmt eval --set live`** through `cli.main`, with `run_once` replaced by one that returns the three fixture logs and transcripts, the dataset by the fixtures, and `models.ensure` by a fake that records its argument, which must equal `models.default_models(manifest)`: `results/live.json` has the figure above, in key order, and `report.same_accuracy` holds between two files that differ only in their delays and `behind_seconds`.

**Test of the item:** the tests above in the `test` job.

## CI cost

Every item is tier 3, at about 39 minutes a run before G2's tests land (architecture, "CI": tier 3 is 34, plus 5 for G3's `browser` and install check once they land). So stage 1's seven PRs come to about 7 × 4 × 39 ≈ 1,100 minutes at the architecture's 4 runs a PR. The architecture's estimate (≈ 660) counted five items, with the harness at tier 1. But `src/lmt/eval/` is tier 3 by its own rules, and two more items come from the split.

**G2's model tests,** once all have landed, add to `test-models`:

| Run | Estimate |
| --- | --- |
| `clip.wav`, lockstep, ASR only (G2-2) | about 60 updates of the live model at 3–4 s each on the runner: 3–4 min |
| `two_speakers.wav`, lockstep, the shared run (G2-1, G2-3, G2-6c, G2-8, G2-4's outputs) | 3–4 min |
| `two_speakers.wav`, diarization only (item 2) | under 1 min |
| G2-4's real-time runs (the stops together, then the whole file) | about 2 min of audio, plus model loading: 3 min |

That is about 10–12 minutes, not the architecture's 5, mostly because each update is a full encoder pass of `distil-large-v3` (architecture, "Streaming ASR"). Tier 3 would then take about 45–50 minutes a run. The first runs measure it. Per the architecture's rule, the Architect re-sizes the tiers there if `test-models` takes more than 1.5 times its estimate, and the architecture's figures for G2 are corrected in the same change. Stage 2 may also pin a smaller live model, which would cut this by about two thirds. The design already keeps CI's cost down: the two-speaker clip runs once in lockstep, shared by five checks, and the real-time stop cases run side by side.

## What stage 2's Mac investigation needs from these items

Stage 2 measures the candidate live models, the update interval and the diarization latency on the Mac against Q10, and pins the defaults (spec, G2 "Stages"; architecture, "The live model").

| It needs | From |
| --- | --- |
| U, L and the diarization thresholds as settings, which can be changed without editing code | items 1 and 2 (`AsrConfig`, `DiarConfig`), and `python -m lmt.live.tuning` (3c) |
| Q10's measurement with any settings and any model | item 5's `run_live(command_prefix=…)` with the tuning entry point; `--model` (3b) |
| Each update's and step's compute time, and how far behind it ended | `-v` (3c); `behind_seconds` in `results/live.json` (5) |
| The diarization thresholds tuned without re-running the models | item 2's `record` and `replay` |
| G2-3 and G2-6c checked for each candidate setting before it's pinned | items 3a/3b's tests, run under `python -m lmt.live.tuning` settings |

**Two things it needs that stage 1 doesn't build,** since no stage-1 check uses them:
- **The candidate models in the manifest:** `distil-small.en` and `base.en`, pinned by revision and SHA-256, as G1's investigation adds its candidates. With `default_live_asr`, stage 2's choice is a one-line change.
- **AMI dev meetings** in the dataset manifests, for tuning the diarization thresholds, never on test meetings (architecture). Recommended: the first 600 s of one meeting from each of the dev split's ES, IS and TS series (ES2011a, IS1008a and TS3004a, once checked against the BUT setup's dev list), with their RTTMs. That is a small item, before the investigation.

**After the investigation,** this file records the tuned thresholds and the pinned U and L, and `models.toml` the live default.

## Readings of the spec and public API

These are interpretations within the approved spec and design (the Architect's to make). The Reviewer should check each one. If one is wrong, it goes to the Owner as a change to the spec or the public API.

1. **The join looks only back** for alignment's "nearest turn within 1 s" (3a). Looking ahead would delay such words by up to 1 s, against G2-6c's budget.
2. **`stop` is the last event,** written once the captured audio is finished, just before the final formats (`public-api.md`: "Capture stops. The final formats follow."). Item 5's `behind_seconds` is read from it.
3. **`start`'s `source` has no `duration`,** since nothing has been captured yet. Otherwise it is the transcript's `source`.
4. **`lmt live` never exits 4.** A silent session writes a transcript with no segments and exits 0, as G2-5 (iii) requires for a silent device, and `--from-file` follows the same path.
5. **A live transcript's `models.diarization`** is `speaker-diarization-3.1`'s manifest entry, as for `lmt transcribe`; `source.live` marks the live method. This adds no field to the schema.
6. **G2-1's "every word"** is every matched offline word, since an unmatched one can't be found in the live stream.
7. **"While it saves"** is everything from the first stop to the exit. A second Ctrl-C that lands during the final renames, a few milliseconds long, lets them finish with exit 0, so a set of final formats is never left half renamed.
8. **The live default model** is a manifest key, `default_live_asr`, as the architecture's "separate live default" requires.
9. **G2-1, G2-3 and G2-6c run on the shared subprocess run** (3b), not in process, though G2-1's "How it's checked" says "a test runs the live pipeline in process". The subprocess runs lockstep in process through `tests/live_lockstep.py`, so each event's `t` is still the stream position, and G2-8 needs that run anyway. 3a checks them in process until 3b moves them, so CI runs the two-speaker clip in lockstep once.
