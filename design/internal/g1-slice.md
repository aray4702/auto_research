# Internal design: the G1-1 slice

| Date (PT) | By | Change |
| --- | --- | --- |
| 2026-10-28 | architect, #260 | First version: the shortest path to G1-1 as the spec gives it, in six Developer items, with what moves out of #110 and #123 |

This is the detail a Developer needs to build each of the slice's items in one run. The slice is the shortest path to **G1-1** in `spec/acceptance.md`, unchanged: the reference clip, as WAV, transcribes with WER ≤ 5.0 %, and every segment has 0 ≤ `start` < `end` ≤ the clip's duration, in order of `start`, checked in CI. It works inside `spec/acceptance.md`, `design/architecture.md`, `design/public-api.md` and `design/internal/g1-stage1.md`, and assumes #108 (models) and #109 (scoring) as §3 and §4 there design them. Where this file and those disagree, they win, and this file is the one to fix. The Reviewer reviews this file. The Architect changes it.

It is G1's stage 2 cut down to what G1-1 needs, built so that the rest of stage 2 extends it rather than redoes it. It honours the interfaces G2's and G3's designs assume from stage 2 (`g2-stage1.md` and `g3-stage1.md`, "What stage 1 assumes from G1's stage 2"), with the names they give.

## What G1-1 needs, and why it is more than WAV and ASR

G1-1's check is "a test runs **the product run** on the clip, then scores the JSON's text against the clip's reference and checks the times". The spec defines the product run as `lmt transcribe <input>` **with default options**, the default models downloaded and verified, and no speaker count. The defaults are diarization on and `--formats txt,srt,vtt,json`. So the run G1-1 judges goes through every stage: decode, ASR, diarization, alignment and all four exporters. A test with `--no-diarize` or `-f json` would judge a different run, which would loosen G1-1, and the Owner's grant rules that out. The slice therefore builds each stage, in its final form but without the checks of other criteria (G1-2 to G1-4, G1-6's effects, G1-11, G1-13), which later items add on top.

**What doesn't change.** G1-1's bound (≤ 5.0 %, inclusive), its scoring (#109's `wer`, after the vendored normalizer, pooled as the spec's "Terms" say) and its fixture (the clip, from pinned utterances, by a rule fixed below before any WER is measured). No item may pick other utterances, change the reference, change the normalizer or relax the time checks because of a measured WER. If the WER misses the bound, the product is what gets fixed.

## Items, files and order

| Item | What | Needs | Files it creates or changes | Tier |
| --- | --- | --- | --- | --- |
| **S1** | The reference clip: the source manifest and fetch, the concatenation recipe, `clip.wav` and its reference | #122 | `src/lmt/eval/datasets.py`, `src/lmt/eval/manifests/sources.toml`, `src/lmt/eval/manifests/sets.toml`, `tests/fixtures/clip.wav`, `tests/fixtures/clip.txt`, `tests/test_datasets.py`, `tests/test_manifests.py` | 3 |
| **S2** | Decode and the ASR stage | #108, #109, S1 | `src/lmt/audio.py`, `src/lmt/asr.py`, `src/lmt/transcript.py` (`Word` only, if S4 hasn't landed), `tests/test_audio.py`, `tests/test_asr.py` | 3 |
| **S3** | The diarization stage | #108, S1 | `src/lmt/diarize.py`, `tests/test_diarize.py` | 3 |
| **S4** | The transcript, alignment, the JSON export and the schema | #122 | `src/lmt/transcript.py`, `src/lmt/align.py`, `src/lmt/export/__init__.py`, `src/lmt/export/json.py`, `src/lmt/schema/__init__.py`, `src/lmt/schema/transcript.schema.json`, `schema/transcript.schema.json`, `tests/test_transcript.py`, `tests/test_align.py`, `tests/test_export.py` | 3 |
| **S5** | The text, SRT and WebVTT exporters | S4 | `src/lmt/export/txt.py`, `src/lmt/export/srt.py`, `src/lmt/export/vtt.py`, `src/lmt/export/cues.py`, `src/lmt/export/__init__.py` (registers them), `tests/test_export_formats.py` | 3 (`__init__.py` and the new `cues.py`; the three exporters alone would be 2) |
| **S6** | `lmt transcribe` and G1-1's test | S1–S5, #109 | `src/lmt/pipeline.py` (replaces the stub), `tests/conftest.py` (the product-run helper), `tests/test_pipeline.py`, `tests/test_g1_1.py`, `tests/test_cli.py` (the stub test), `tests/criteria.toml` (`[G1-1]`), `.github/workflows/product.yml` (`test-models`, only if #108 left a step out, below) | 3 |

**Order.** An item can start once everything it needs has merged. Developer runs go one at a time (`system/caps.toml`, `max_runs_per_role = 1`), so the order below is a recommendation to the Coordinator. It puts the riskiest work first: the model stages, where a surprise would cost the most time.

1. #122, then #108 and #109 (charted already).
2. **S1**, which S2 and S3 need for their model tests. It needs only #122, so it can run before #108 lands.
3. **S2**, then **S3**. S2's model test gives the first WER on the clip, as early warning.
4. **S4**, then **S5**. S4 needs only #122, so it can fill any wait for #108.
5. **S6**, last. Its PR is the one that makes G1-1 pass in CI.

**Parallel-safe,** by the files in the table. S2 and S4 both touch `transcript.py`: S2 adds only `Word` if S4 hasn't landed, and S4 then adds the rest around it. Only S6 touches `product.yml`, and only if it must (below). No two items share a test file.

**Every item's local checks:** `uv run --frozen ruff check`, `uv run --frozen pytest`, `uv run --frozen pytest -m models` where the item has model tests and the models are in `LMT_CACHE_DIR` (`lmt models download` with `HF_TOKEN`), and `python3 -I system/gate/generic.py --base origin/main`. Each PR shows the model tests' output from the Developer's machine, and the `test-models` job's run.

**Criterion tags** (`design/internal/criteria.md` §1). Only S6's `tests/test_g1_1.py` carries `G1-1`, because only it runs the product run. S2's options test carries `G1-7` and `G1-12` (the decode language, temperature 0 without fallback and the fixed beam), and S1's manifest test carries `G1-7` (the manifests' sources). Neither of those is complete, so their registry entries don't change. No other slice test is tagged: they test parts, not a criterion's check.

### What moves out of #110 and #123

- **Out of #123 (5b), into S1:** the concatenation recipe (pieces, `gap`, `derived_sha256`, the text reference) and the reference clip, `tests/fixtures/clip.wav` with its reference. **#123 keeps:** LS full and AMI full, the recipe's `cut` and its shifting and cropping of word and turn references, the one-hour recording, and `tests/fixtures/two_speakers.wav` with its RTTM and word references. It extends S1's recipe rather than writing its own.
- **Out of #110 (5a), into S1:** `data_dir()`, the format of `sources.toml`, and the fetch of one source (download, verify, extract the listed archive members, delete the archive), with its tests that need no network. **#110 keeps:** the CI subset's sources and `[[dataset]]` entries, the LS-ci and AMI recipes, `fetch(set)`, `python -m lmt.eval.datasets fetch ci`, the `data` tests and the `eval-ci` job's steps. It adds its sources and members to S1's files, and its fetch of the LibriSpeech archive extracts the clip's members too, since they are listed in the same source.
- **Out of stage 2's other items, into the slice:** decode, the ASR and diarization stages, alignment, the transcript types, the four exporters, the schema, and `lmt transcribe` with its documented options' plumbing. **They keep** their criteria's tests and what those need: the clip's other encodings and the fuzz corpus (G1-2, G1-11), the two-speaker test (G1-3), the subtitle checks with `ffmpeg` (G1-4), one test per option's effect (G1-6), the network and file test (G1-13), and `lmt eval --set ci` with the `eval-ci` steps (G1-5, G1-8c, G1-12).

The Coordinator edits #110's and #123's bodies to match, and opens S1 to S6 as Developer items.

## S1. The reference clip

### The clip's recipe

**Which utterances.** Fixed by a rule, so anyone gets the same clip, and fixed now, before any WER is measured:

1. Take LibriSpeech test-clean's speakers in ascending numeric order.
2. For a speaker, list its utterances in ascending id order (chapter, then utterance number), and take the shortest prefix of that list whose total duration is at least 55.0 s. A duration is the FLAC's frame count over 16,000.
3. Use the first speaker whose prefix totals at most 70.0 s. If a speaker's prefix is longer, go to the next speaker.

The Developer applies the rule to the downloaded archive, and records the speaker, the utterance ids and the total duration in a comment in `sets.toml`. The rule is the fixture's definition. It is never changed to get another clip.

**How it's built.** Each utterance is read with `soundfile.read(path, dtype="int16")`, which decodes FLAC exactly. Each must be 16 kHz and mono, or the recipe raises `ValueError` naming it. The pieces are joined in order, with `round(gap × 16000)` zero samples between them (`gap = 0` for the clip), and written with the standard `wave` module: one channel, 2 bytes a sample, 16,000 Hz. So the bytes are the same on every platform, and the SHA-256 is recorded in the manifest.

**The reference,** `tests/fixtures/clip.txt`: each utterance's line in its chapter's `.trans.txt`, without the id, joined by single spaces, in the clip's order, with one final newline. It stays in LibriSpeech's upper case. Scoring normalizes both sides (#109).

### `sources.toml` and `sets.toml`

`sources.toml` takes the format in `g1-stage1.md` §5a, with one source for now:

```toml
schema_version = 1

[[source]]
id = "librispeech-test-clean"
urls = ["https://www.openslr.org/resources/12/test-clean.tar.gz",
        "https://us.openslr.org/resources/12/test-clean.tar.gz"]     # tried in order
sha256 = "<64 hex>"            # cross-checked against openslr's MD5 and torchaudio's published checksum
size = <bytes>
licence = "CC-BY-4.0"
keep = false

[[source.member]]              # one per clip utterance, plus the .trans.txt of each chapter it uses
path = "LibriSpeech/test-clean/<speaker>/<chapter>/<utterance id>.flac"
sha256 = "<64 hex>"
```

`sets.toml` gets the clip as a `[[fixture]]`. Its concatenation keys are those `g1-stage1.md` §5b names, with `member` added to a piece so that a piece can be one archive member:

```toml
schema_version = 1

[[fixture]]
name = "clip"
path = "tests/fixtures/clip.wav"          # from the repository root
reference = "tests/fixtures/clip.txt"
gap = 0.0
derived_sha256 = "<64 hex of clip.wav>"
reference_sha256 = "<64 hex of clip.txt>"
# Speaker <n>: utterances <first id> to <last id>, <k> utterances, <total> s (the rule in design/internal/g1-slice.md, S1).
concat = [
  {source = "librispeech-test-clean", member = "LibriSpeech/test-clean/<speaker>/<chapter>/<id>.flac"},
]
```

A piece is `{source, member}` for an archive member, or `{source, start, end}` for a slice of a single-file source. S1 builds only the first form. #123 adds the second, with `cut`, for the one-hour recording. #110 adds `[[dataset]]` entries beside the fixture.

### `src/lmt/eval/datasets.py`

```python
data_dir() -> Path                                    # LMT_DATA_DIR, else ~/.cache/lmt/data
load_manifests(dir=MANIFEST_DIR) -> Manifests          # both files; checks the format and that every id resolves
fetch_source(source: Source) -> Path                   # the source's cache directory, verified
build_fixture(fixture: Fixture, manifests) -> tuple[bytes, str]   # the WAV's bytes and the reference text
main(argv) -> int                                      # python -m lmt.eval.datasets build-fixture clip [--write]
```

- **The cache and the fetch** are as `g1-stage1.md` §5a gives them: `<LMT_DATA_DIR>/sources/<source id>/<member path>`; download to a temporary file, check its size and SHA-256, rename it into place; extract only the listed members, each checked against its own hash; delete the archive when `keep = false`. A mismatch deletes the file and raises `InputError("<url>", "SHA-256 doesn't match the manifest")`. A failed download tries the next URL, then raises `InputError`. It uses `urllib.request`, `tarfile` and `hashlib`, so it adds no dependency. Archive members are extracted by their listed paths only, never by a path the archive gives, so an archive can't write outside the cache.
- **Nothing here imports the eval extra** (`jiwer`, `pyannote.metrics`), as the architecture requires of everything but `eval/score.py`. `soundfile` comes with pyannote.audio, in the base install.
- **`build-fixture clip`** fetches the source, builds the clip, and compares both files' SHA-256 with `derived_sha256` and `reference_sha256`. It exits 0 if they match, and 3 with one `lmt: error:` line naming the file if they don't. `--write` writes the two files to their paths first, which is how the Developer makes them.

### Tests

- `tests/test_manifests.py` (tagged `G1-7`): every source has a 64-hex SHA-256, a positive size and at least one URL, and every member a SHA-256. Every URL starts with one of the prefixes in `g1-stage1.md` §5a, so the manifests name only LibriSpeech and AMI sources. Every fixture's pieces resolve to a listed member. #110 and #123 extend this test to their entries.
- `tests/test_datasets.py`, with no network: `urllib.request.urlopen` replaced, serving a small `.tar.gz` built in the test. A good archive's members are extracted and verified, and the archive deleted; a bad archive exits 3 and leaves nothing in the cache; the second URL is used when the first fails; a member whose hash doesn't match is deleted, with exit 3. The recipe, on three small FLAC files the test writes with `soundfile`: the samples come out in order, a `gap` of 0.25 s inserts 4,000 zero samples, the reference is the lines joined by single spaces, and a 44.1 kHz or stereo piece raises `ValueError`.
- **The committed clip:** `tests/fixtures/clip.wav`'s and `clip.txt`'s SHA-256 equal the manifest's, the WAV is 16 kHz mono 16-bit, and its duration is 55.0–70.0 s. This runs in the `test` job, so the fixture can't change without its manifest.
- **A `data` test** rebuilds the clip from the cache with `build_fixture` and compares the bytes. It runs once #110's `eval-ci` steps fill the data cache. Until then, the PR shows `python -m lmt.eval.datasets build-fixture clip` exiting 0 on the Developer's machine.

**Test of the item:** the tests above in the `test` job; the PR shows the rule applied (each candidate speaker's prefix and total, down to the one chosen) and the rebuild exiting 0.

## S2. Decode and the ASR stage

### `src/lmt/audio.py`

```python
SAMPLE_RATE = 16_000
def decode(path: str | Path) -> numpy.ndarray     # float32, mono, 16 kHz, one dimension
```

- **How.** `av.open(str(path))`; the first audio stream (`container.streams.audio[0]`); `av.AudioResampler(format="flt", layout="mono", rate=16000)`; every decoded frame through the resampler, then a final `resample(None)` to flush it; each output frame's `to_ndarray()` flattened, and the pieces joined with `numpy.concatenate`. The container is closed in a `finally`. A 16-bit WAV at 16 kHz mono goes through unchanged, as sample ÷ 32,768.
- **Errors,** each an `InputError(str(path), problem)` (exit 3): a path that doesn't exist ("no such file"), a directory ("is a directory"), an empty file ("empty file"), any exception PyAV raises while opening or decoding ("can't decode: <its first line>"), no audio stream ("no audio track"), and no samples ("no audio"). Nothing else escapes decode: any other exception from PyAV is caught and becomes an `InputError` too. FFmpeg's own log stays off (`av.logging` at its default, `None`), so a bad input gives stderr only the one `lmt: error:` line.
- **Later.** G1-2's encodings and G1-11's fuzzing test this function as it is. G2's item 4 extracts the resampler into `Resampler` (`g2-stage1.md`), leaving decode's output unchanged.

### `src/lmt/asr.py`

```python
BEAM_SIZE = 5
THREADS = min(8, os.cpu_count() or 1)        # fixed per machine (Q7)

def load(model_dir: Path) -> Engine
class Engine:
    def transcribe(self, audio: numpy.ndarray, *, initial_prompt: str | None = None) -> list[Word]
```

- **`load`** imports `faster_whisper` inside the function, after `models.ensure` has run (so `HF_HUB_OFFLINE=1` is set), and builds `WhisperModel(str(model_dir), device="cpu", compute_type="int8", cpu_threads=THREADS, num_workers=1, local_files_only=True)`. The model comes from its verified directory, never by name.
- **`transcribe`** calls `model.transcribe(audio, language="en", task="transcribe", beam_size=BEAM_SIZE, temperature=0.0, condition_on_previous_text=False, word_timestamps=True, vad_filter=True, initial_prompt=initial_prompt)` and consumes the segment generator to the end. A single temperature means no fallback (G1-12). `condition_on_previous_text=False` is the distil-Whisper model card's recommendation for long-form audio, where carrying text over can make the decoder repeat itself. It is fixed here, before any measurement, and stage 2's model investigation keeps it for every candidate. Each word becomes `Word(start, end, text, probability)`, with `text` stripped of white space, and words that are empty after stripping dropped. Times are seconds from the start of `audio`, unrounded, as G2 assumes. VAD settings are faster-whisper's defaults.
- **`Word`** is the frozen dataclass of `lmt.transcript` (S4). If S4 hasn't landed, S2 creates `transcript.py` with `Word` alone.

### Tests

- `tests/test_audio.py`, with no model: a 1 s 440 Hz tone written as 16-bit 16 kHz mono WAV decodes to 16,000 samples equal to sample ÷ 32,768 to within 1 × 10⁻⁷; a 44.1 kHz stereo WAV decodes to 16 kHz mono, of length 16,000 ± 16; `tests/fixtures/clip.wav` decodes to as many samples as its WAV header has frames. Each error above gives `InputError` with its exit code 3: a missing file, a directory, an empty file, a WAV header with no data, 1,000 random bytes from a fixed seed named `.wav`, and a text file named `.wav`. No other exception type escapes.
- `tests/test_asr.py`, with no model (tagged `G1-7`, `G1-12`): with `faster_whisper.WhisperModel` replaced by a fake that records its arguments, `load` passes the directory, `cpu`, `int8` and `local_files_only=True`, and `transcribe` passes `language="en"`, `temperature=0.0`, `beam_size=5`, `word_timestamps=True` and `vad_filter=True`. Words are stripped and empty ones dropped.
- One test marked `models`: `asr.transcribe` on the clip, with the default ASR model from `models.ensure`, returns words whose times lie within the clip. It writes the words' WER against `clip.txt`, scored with #109's `wer`, to the test's output and to `$GITHUB_STEP_SUMMARY` when that is set. It doesn't assert the bound: that is G1-1's, on the product run. This is the slice's first reading of the WER, so that a problem shows before S6.

**Test of the item:** the tests above, in the `test` job and in `test-models`; the PR shows the model test's WER.

## S3. The diarization stage

### `src/lmt/diarize.py`

```python
THREADS = asr.THREADS
SEED = 0

def configure_torch() -> None              # torch.set_num_threads(THREADS), use_deterministic_algorithms(True), manual_seed(SEED)
def load_segmentation(model_dir: Path)     # pyannote.audio.Model.from_pretrained(model_dir / "pytorch_model.bin")
def load_embedding(model_dir: Path)        # the same, for wespeaker-voxceleb-resnet34-LM
def load(dirs: Mapping[str, Path]) -> Diarizer     # keys: the manifest's names of the three pyannote models
class Diarizer:
    def diarize(self, audio, *, num_speakers=None, min_speakers=None, max_speakers=None) -> list[Turn]
```

- **Loading, never by name.** `load` reads `speaker-diarization-3.1`'s `config.yaml` from its verified directory with `yaml.safe_load` (PyYAML comes with pyannote.audio). It builds `pyannote.audio.pipelines.SpeakerDiarization` from the config's `pipeline.params`, but with `segmentation=load_segmentation(...)` and `embedding=load_embedding(...)`, the loaded models in place of the names the config gives. Then it calls `instantiate(config["params"])`, the config's tuned thresholds. Nothing resolves a model name over the network, and `HF_HUB_OFFLINE=1` is already set by `models.ensure`.
- **If loading fails on the locked torch** because `torch.load` defaults to `weights_only=True`, the remedy, in order: `torch.serialization.add_safe_globals` for the classes it names; failing that, `weights_only=False` for these files only. That is safe here because each file's SHA-256 is pinned and verified before use. The Developer records which was needed.
- **`diarize`** calls `configure_torch()`, then the pipeline on `{"waveform": torch.from_numpy(audio)[None, :], "sample_rate": 16000}` with the speaker options as given. It returns `Turn(start, end, speaker)` for each track of the annotation (`itertracks(yield_label=True)`), sorted by `(start, end, speaker)`, unrounded. Turns may overlap. CPU only.
- **Quiet.** Loading and running happen inside `warnings.catch_warnings()` with `simplefilter("ignore")`, and the `lightning`, `pytorch_lightning` and `pyannote` loggers are set to `ERROR`, so a successful run prints no library warnings.
- **`Turn`** is `lmt.transcript.Turn` (S4), or created in `transcript.py` here as `Word` is in S2.

### Tests

- `tests/test_diarize.py`, with no model: `load` on a fake config and fake models passes the loaded objects, not names, to the pipeline, and instantiates it with the config's `params`; `diarize` turns an annotation into sorted `Turn`s.
- One test marked `models`: `diarize` on the clip, with the three pyannote models from `models.ensure` and `socket.socket.connect` patched to raise, returns at least one turn, each within the clip's duration with `start < end`. The patched socket shows the loading touches no network. The number of speakers isn't asserted: that is G1-3's, on the two-speaker clip.

**Test of the item:** the tests above, in the `test` job and in `test-models`; the PR shows the model test's turns.

## S4. The transcript, alignment, the JSON export and the schema

### `src/lmt/transcript.py`

```python
SCHEMA_VERSION = "1.1"

@dataclass(frozen=True)
class Word:      start: float; end: float; text: str; probability: float
@dataclass(frozen=True)
class Turn:      start: float; end: float; speaker: str
@dataclass(frozen=True)
class Speaker:   id: str; label: str
@dataclass(frozen=True)
class Segment:   start: float; end: float; speaker: str; text: str; words: tuple[Word, ...]
@dataclass(frozen=True)
class Transcript:
    schema_version: str; lmt_version: str; source: dict; models: dict
    speakers: tuple[Speaker, ...]; segments: tuple[Segment, ...]

def to_dict(t: Transcript) -> dict         # the JSON's keys, in public-api.md's order
def from_dict(doc: dict) -> Transcript
```

- **Version 1.1,** as `public-api.md` gives it and `g2-stage1.md` recommends, so G2 needn't change the schema. `transcribe`'s `source` is `{"file": <the input's base name>, "duration": <seconds>}`.
- **Key order** in `to_dict`: `schema_version`, `lmt_version`, `source`, `models`, `speakers`, `segments`; a segment's `start`, `end`, `speaker`, `text`, `words`; a word's `start`, `end`, `text`, `probability`.
- **`lmt_version`** is the transcript's own field. The JSON exporter writes it, never the running version, so G3's `rename` keeps it. The pipeline sets it from `lmt.__version__`.
- **G3's `load(path)`,** which validates at run time, comes with G3's item 1. It needs `jsonschema` as a run-time dependency, which #122 puts only in `dev`, so that item moves it.

### `src/lmt/align.py`

The rules are the architecture's ("Pipeline", alignment), as functions G2 reuses (`g2-stage1.md`):

```python
PAUSE = 1.0; MAX_SEGMENT = 30.0; NEAREST = 1.0

def assign_speaker(word, turns, previous: str | None) -> str | None
def ends_sentence(word) -> bool
def breaks(previous_word, word, segment_start: float) -> bool
def segment_text(words) -> str
def build(words, turns | None, *, duration_samples: int, source_file: str, models: dict,
          lmt_version: str) -> Transcript
```

- **Times, rounded once** (architecture, "One rendering path"). `build` first turns every word's times into whole milliseconds: `round(t × 1000)`. `D`, the duration in milliseconds, is `duration_samples // 16`, rounded down so that no time passes the audio's real end. Then, word by word, in the order ASR gave them: `start = min(max(start, previous word's start, 0), D − 1)`, and `end = min(max(end, start + 1), D)`. So every word, and so every segment, has 0 ≤ `start` < `end` ≤ `D`, starts never decrease, and the words keep their order, so the text is unchanged. The stored values are milliseconds ÷ 1000. `source.duration` is `D ÷ 1000`. Words come out of `build` with these times, and a word's `probability` is rounded to 3 decimal places.
- **`assign_speaker`.** The turn overlapping the word most, by `min(ends) − max(starts)` over positive overlaps; on a tie, the earliest turn in `turns`' order. Else the turn nearest the word, by the gap between them, if that gap is at most 1 s; on a tie, the earlier turn. A word of zero length inside a turn is at gap 0 from it. Else `previous`. It works on the unrounded turns and the rounded word.
- **The first word,** if no turn is near it, takes the speaker of the earliest turn. With no turns at all (`--no-diarize`, or diarization finding none), every word is one speaker.
- **Segments.** A segment is a run of consecutive words with the same speaker. `breaks` also starts a new one when the gap from the previous word's end to this word's start is at least 1 s, when `ends_sentence(previous_word)`, or when this word's end minus `segment_start` would pass 30 s. `ends_sentence` is true when the word's text, with any closing `"`, `'`, `)` or `]` removed from its end, ends in `.`, `?` or `!`. A segment's `start` is its first word's, its `end` the largest of its words' ends, and its text `segment_text(words)`: the words' texts joined by single spaces.
- **Speakers** are numbered by first appearance in the words, `S1`, `S2`, …, labelled `Speaker 1`, …. A diarization speaker with no words doesn't appear.

### `src/lmt/export/` and the JSON export

```python
# export/__init__.py
FORMATS = ("txt", "srt", "vtt", "json")
def render(transcript, fmt) -> str               # the format's module's render(transcript)
def outputs(out_dir: Path, stem: str, formats) -> list[Path]
def check(out_dir: Path, stem: str, formats, *, overwrite: bool) -> list[Path]
def write(transcript, out_dir, stem, formats, *, overwrite) -> list[Path]
# export/json.py
def render(transcript) -> str                    # json.dumps(to_dict(t), indent=2, ensure_ascii=False) + "\n"
```

- **`check`** runs before any work. It raises `InputError(str(out_dir), …)` (exit 3) if the directory doesn't exist, isn't a directory or isn't writable (`os.access(out_dir, os.W_OK | os.X_OK)`), and `UsageError(str(path), "exists; use --overwrite to replace it")` (exit 2) for the first existing output without `overwrite`. It returns the paths, in `formats` order. The directory is never created: `public-api.md` calls a missing one an error.
- **`write`** calls `check`, renders every format, writes each to a temporary file in `out_dir` (`tempfile.mkstemp(prefix=f".{stem}.", suffix=f".{fmt}.tmp", dir=out_dir)`), and only when every one is written renames them all into place with `os.replace`. On any exception, `KeyboardInterrupt` included, it deletes the temporary files and re-raises. So a failed run leaves no outputs (`public-api.md`, "Exit codes"). It returns the paths.
- **S4 registers only `json`** in `render`'s table. A format that isn't registered raises `NotBuilt("--formats")`, until S5 registers the other three.

### The schema

`src/lmt/schema/transcript.schema.json`, JSON Schema 2020-12, and `schema/transcript.schema.json` at the root, identical (architecture, layout). `src/lmt/schema/__init__.py` has `SCHEMA_PATH` and `validate(doc)`, which imports `jsonschema` inside the function and raises `InputError("transcript", <the first error's message and path>)`.

- **Required:** `schema_version` (pattern `^1\.[0-9]+$`), `source` (`file`, a string; `duration`, a number ≥ 0; optional `live`, `const: true`; optional `device`, a string), `models` (`asr`, an object with string `name`, `repository` and `revision`; `diarization`, the same or `null`), `speakers` (each with `id`, pattern `^S[1-9][0-9]*$`, and `label`), and `segments` (each with `start` and `end`, numbers ≥ 0, `speaker`, pattern as `id`, and `text`, a string; optional `words`, each with `start`, `end`, `text` and `probability`). `lmt_version` is a string, and optional, as `public-api.md` lists it outside "Required".
- **The label rule** of `public-api.md`'s "Speaker names", now, exactly as `g3-stage1.md` gives it (`minLength` 1, `maxLength` 32, and its `pattern`), so G3 needn't touch the schema.
- **Unknown fields are allowed** everywhere (no `additionalProperties: false`), since readers ignore fields they don't know.
- **What a schema can't say** (times in order, `start` < `end` ≤ `source.duration`, speakers that exist) is `align.build`'s to guarantee, and the tests check it.

### Tests

- `tests/test_align.py`, one test per rule: the most overlap wins; a tie goes to the earlier turn; a word in no turn takes the nearest within 1 s, and at 1.2 s away the previous word's speaker; the first word with no turn near it takes the earliest turn's speaker; no turns gives one speaker. Breaks at a pause of exactly 1.0 s and not at 0.999 s; after `end.`, `end?"` and `end!)`, and not after `Mr`; at 30 s. Speakers numbered by first word, and a speaker with no words left out. The times: rounding to the millisecond; a word with `end < start`, a zero-length word, a word past the end and a negative start each come out with 0 ≤ `start` < `end` ≤ `D`; a word starting before its predecessor is moved up to it, and the text order is kept; `D` is rounded down (`duration_samples = 16_015` gives `D = 1000`).
- `tests/test_transcript.py`: `from_dict(to_dict(t)) == t` on a transcript with two speakers; `to_dict`'s key order.
- `tests/test_export.py`: the JSON export of a small fixed transcript equals a committed golden file byte for byte, and validates against the schema; a document missing each required field, or with a bad speaker id or label, fails `validate`; the two schema copies are identical. `write`: four paths in `formats` order; an existing output without `overwrite` raises exit 2 and writes nothing; an unwritable or missing directory raises exit 3; an exception from a renderer leaves no file, temporary ones included.

**Test of the item:** the tests above, in the `test` job.

## S5. The text, SRT and WebVTT exporters

The formats are `public-api.md`'s ("Output formats"). Times are whole milliseconds from the transcript, formatted from `round(t × 1000)`, so they can't drift from the JSON.

- **`txt.py`.** `render_segment(segment, label) -> str` is `[HH:MM:SS] <label>: <text>\n`, with the time the segment's start, rounded down to the second. `render` joins the segments' lines. G2's growing `<stem>.txt` uses `render_segment`, so the two can't differ.
- **`srt.py` and `vtt.py` share the cue-splitting,** in `export/cues.py`: `cues(segment, label) -> list[Cue(start, end, lines)]`. The visible prefix `<label>: ` comes first on each cue's first line. Words are packed greedily into lines of at most 42 code points, counting the prefix and not any tag, and a word longer than 42 takes a line of its own. When a word would need a third line, the cue closes, and the next cue starts with that word and its own prefix. A cue's start is its first word's start, and the first cue's is the segment's. Its end is its last word's end, and the last cue's is the segment's. A segment without `words`, which `lmt` never writes, is split the same way on its text's spaces, with its time span shared among its cues in proportion to their characters, in whole milliseconds.
- **SRT:** cues numbered from 1 across the file, `HH:MM:SS,mmm --> HH:MM:SS,mmm`, then the cue's lines, then a blank line.
- **WebVTT:** `WEBVTT`, a blank line, then for each cue `HH:MM:SS.mmm --> HH:MM:SS.mmm` and its lines, the first starting with the voice tag, `<v <label>>`, then a blank line. In the text, `&`, `<` and `>` are written `&amp;`, `&lt;` and `&gt;`. In the voice tag's annotation, `&` is written `&amp;`. So `José & Ana` gives `<v José &amp; Ana>José &amp; Ana: text`.
- **`export/__init__.py`** registers the three, so `NotBuilt("--formats")` goes.

**Tests (`tests/test_export_formats.py`):** each format's rendering of a small fixed transcript, two speakers and five segments, equals a committed golden file byte for byte. The transcript includes: a segment that wraps to two lines; one long enough to split into three cues, whose cue times are its words' times; a word of 50 characters on a line of its own; a label `José & Ana`, escaped as above in WebVTT and unescaped in SRT and text; and a segment at 3,725.5 s, for the hours. Every line of every cue is at most 42 code points, counting the prefix, except the long word's. G1-4's checks of these files (strict `srt`, `webvtt-py`, `ffmpeg`, the same words in every format) are G1-4's item's.

**Test of the item:** the tests above, in the `test` job.

## S6. `lmt transcribe` and G1-1's test

### `src/lmt/pipeline.py`

```python
PARALLEL = True

def transcribe(path, *, model=None, offline=False, diarize=True, num_speakers=None,
               min_speakers=None, max_speakers=None, timings=None) -> Transcript
def cli_transcribe(args) -> int
```

`cli_transcribe` checks everything it can before loading any model, then works:

1. **The models.** `manifest = models.load_manifest()`. A `--model` the manifest doesn't list as an ASR model is `UsageError("--model", …)`. The models are `models.default_models(manifest, asr=args.model)`, without the three pyannote models under `--no-diarize`.
2. **The outputs.** `export.check(Path(args.output_dir), Path(args.input).stem, args.formats, overwrite=args.overwrite)`: exit 3 for an unusable directory, 2 for an existing output.
3. **`transcribe(...)`,** which:
   1. `dirs = models.ensure(models, offline=offline)`. It downloads what's missing, saying so on stderr, or exits 5 under `--offline`. It sets `HF_HUB_OFFLINE=1`.
   2. `audio = audio.decode(path)`: exit 3 for a bad input.
   3. ASR and diarization, each loading its model and running on `audio`. With `PARALLEL`, they run in a `ThreadPoolExecutor(max_workers=2)`, as the architecture's "Run order" says. Both spend their time in native code that releases the GIL. If two runs on the same machine differ, or the two native runtimes fail side by side, the Developer sets `PARALLEL = False`, which runs ASR and then diarization, and says so on the item. The architecture allows that ("Run order").
   4. No words from ASR is `NoSpeechError(<the input's base name>, "no speech found")`, exit 4.
   5. `align.build(words, turns or None, duration_samples=len(audio), source_file=Path(path).name, models=<the manifest's name, repository and revision for the ASR model and for speaker-diarization-3.1, or null>, lmt_version=lmt.__version__)`.
4. **The outputs.** `export.write(transcript, out_dir, stem, args.formats, overwrite=args.overwrite)`, and each path printed to stdout, one per line.
5. **`--verbose`** prints each stage's wall time to stderr, one line each: `lmt: decode 0.41 s`, and the same for `models`, `asr`, `diarize`, `align` and `export`. `timings` is the dict `transcribe` fills for it.

Every error is an `LmtError`, which `cli.main` already prints as one line with its exit code. G3's `pipeline.transcribe(path, offline=...)` is this function with its defaults.

### The product run in tests (`tests/conftest.py`)

```python
def run_lmt(argv, *, cwd=None, env=None) -> subprocess.CompletedProcess   # [sys.executable, "-m", "lmt", *argv], text, captured
@pytest.fixture(scope="session")
def clip_run(tmp_path_factory) -> ProductRun        # returncode, stdout, stderr, out_dir
```

`clip_run` runs `lmt transcribe tests/fixtures/clip.wav -o <a fresh directory>`, with **no other option**: the product run. It inherits `LMT_CACHE_DIR`, where the job has already downloaded and verified the models. It runs once per session, and every test that needs it shares it. G3's `two_speakers_run` (`g3-stage1.md`) is the same kind of fixture, added with the two-speaker clip.

### G1-1's test (`tests/test_g1_1.py`)

Marked `models` and `criterion("G1-1")`. It checks exactly G1-1's criterion, from the product run's JSON, computing what it checks independently of the product's code:

- The run exits 0, and `out_dir / "clip.json"` exists.
- **WER.** The hypothesis is `lmt.eval.score.hypothesis_from_transcript(doc).text`, the segments' texts joined (#109). The reference is `tests/fixtures/clip.txt`. `wer([(reference, hypothesis)])` is at most `WER_BOUND = 0.050`, a constant whose comment cites `spec/acceptance.md` G1-1. The WER goes into the assertion's message, the test's output and `$GITHUB_STEP_SUMMARY`, pass or fail.
- **Times.** The clip's duration is read from the WAV with the `wave` module, frames over rate, unrounded, never from the JSON. Every segment has 0 ≤ `start` < `end` ≤ that duration, and each segment's `start` is at least the one before it.

A second test, untagged, uses the same run: stdout lists the four default outputs, and each exists; `clip.json` validates against the schema; and stderr holds no line starting `lmt: error:`.

### Other tests (`tests/test_pipeline.py`, no model)

With `asr.load`, `diarize.load` and `models.ensure` replaced by fakes that return fixed words and turns: `cli.main(["transcribe", <a short WAV>, "-o", <dir>])` writes the four files and prints their paths; `--no-diarize` never loads diarization and gives `models.diarization` `null`; an existing output without `--overwrite` exits 2 before any fake is called; a missing output directory exits 3 before any fake is called; an unknown `--model` exits 2; a fake ASR with no words exits 4 and writes nothing; `-f json` writes one file; `-v` prints six stage lines. `tests/test_cli.py`'s stub test drops `transcribe`, which is no longer a stub.

### The CI job

No new job. `test-models` (tier ≥ 2), as #108 leaves it, restores the model cache, downloads on a miss with `HF_TOKEN`, verifies, then runs `uv run --frozen pytest -m models`, which runs G1-1's test with the others. `criteria.md` §3 has #108 add `--criteria-out=criteria-test-models.json` to that step, the record's upload as the job's last step, and `test-models` to the registry's `jobs`. **If #108 lands without any of those,** S6 adds it, and its PR then touches `.github/workflows/product.yml`, a protected path. The job's 30-minute timeout holds: the slice's model tests make one product run and two stage runs on a 60 s clip, a few minutes after the cache restore, against about 20 minutes estimated for the whole job (`design/architecture.md`, "CI").

The registry, `tests/criteria.toml`, gains:

```toml
# G1-1: tests/test_g1_1.py runs the product run on the reference clip in test-models
# (design/internal/g1-slice.md, S6), which is the whole of its check.
[G1-1]
complete = true
```

**Test of the item:** the tests above; G1-1's test passing in the PR's `test-models` job (tier 3, since `pipeline.py` is in `src/lmt/`), with the WER in the job's summary; and the PR's `criteria-test-models` record giving `G1-1` `passed`. `criteria.py --ref refs/pull/<n>/merge` on the PR's records then gives `pass` for G1-1. The PR shows that output.

## Risks

- **pyannote on the locked torch** (S3): `weights_only` loading, above. If pyannote.audio 3.x can't run on the locked torch at all, S3 stops and escalates to the Architect, since the remedy (a torch pin, or another diarization version) changes #122's lock or the architecture.
- **Two native thread pools at once** (S6): CTranslate2's OpenMP runtime and torch's in one process. `PARALLEL = False` is the fallback the architecture allows.
- **The WER itself.** distil-large-v3's published LibriSpeech test-clean WER is well inside 5 %, but one 60 s clip can sit above a set's average. S2's model test reads it first. If it misses, the remedies are the product's, inside the architecture, and none touches the bound, the scoring or the clip: the ASR settings (with the Architect, here), or stage 2's model investigation brought forward.
