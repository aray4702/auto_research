# Public API: Local Meeting Transcriber

| Date (PT) | By | Change |
| --- | --- | --- |
| 2026-10-31 | architect, #570 | Reviewer's findings on #572: live events 1.1's `text` and `words` events carry the same words, in order; exit 6 compares the README's table with every results file present |
| 2026-10-31 | architect, #570 | G4 (#565): `--engine kyutai\|apple` on `transcribe`, `live`, `models download` and `eval`, replacing `--model`; exit 5 when Apple's engine can't run; live events 1.1 add the `text` event, so words go out before their labels; transcript JSON 1.2 adds `models.asr.engine`; results file 2.0 (`engine`, `asr-alone`, the label delays, the `-apple` files). Waiting for the Owner's approval and the answers on #570 |
| 2026-10-29 | architect, #328 | The results file states `rtf`'s basis for each system: the product's from process start to exit, Whisper alone's and pyannote alone's as their stage's time within the product run. Approved by the Owner on #328 (option 1) |
| 2026-10-28 | architect, #82 | Merged `main` (G3, #129) into #125: exit 130's row covers both `lmt live` and `lmt demo`, and `lmt demo`'s stopping bullet no longer calls it the one command whose Ctrl-C isn't 130; the speaker-name rule needs no new schema version, now that the schema is 1.1 |
| 2026-10-27 | architect, #83 | Reviewer's findings on #129: `rename` trims a name's outer white space; WebVTT escapes `&` in the voice tag too; `lmt demo` reads the transcript again on each load and needs no `.vtt`; only `lmt eval` needs the `eval` extra |
| 2026-10-27 | architect, #83 | G3: `lmt rename` and `lmt demo`; speaker names' rules; the WebVTT cue shows the speaker as text as well as in the voice tag; the install command and version 1.0.0. Holds the recommended options on #83, waiting for the Owner's approval |
| 2026-10-27 | architect, #82 | Reviewer's findings on #125: `source.file` stays a string in 1.1; a silent device warns rather than exiting 3; stdout is flushed per write; what a failed live session keeps |
| 2026-10-27 | architect, #82 | G2: `lmt live` and its event stream, `lmt eval --set live` and `results/live.json`, transcript JSON 1.1 for live sessions, and exit codes 3 and 130 for live mode. It holds the recommended options on #82, waiting for the Owner's approval |
| 2026-10-27 | architect, #81 | Reviewer's finding on #89: exit code 6 for `lmt eval --check-readme`'s mismatch, so drift and a bug can't look alike |
| 2026-10-27 | architect, #81 | First version, for G1. It holds the recommended options on #81, waiting for the Owner's approval |

The public API is the command-line interface, its exit codes, the four output formats, the transcript JSON schema, and the results file. There is no public Python API in v1 (#81, question 9): `lmt`'s modules are internal and may change freely. The Owner approves every change to this file. From v1.0 (G3), changes follow semantic versioning: removing or changing anything here is a major change, and adding is a minor one.

## Names

| What | Name |
| --- | --- |
| Distribution (what you install) | `local-meeting-transcriber` |
| Import package | `lmt` |
| Command | `lmt` |

## Commands

Each command's `--help` lists exactly the options below (G1-6). Messages and progress go to stderr. On success, `transcribe` and `rename` print the path of each file they wrote to stdout, one per line.

### `lmt transcribe INPUT`

Transcribes one recording and writes its transcript.

| Option | Default | Effect |
| --- | --- | --- |
| `-o`, `--output-dir DIR` | the current directory | Where the outputs go, as `<DIR>/<input stem>.<ext>`. |
| `-f`, `--formats LIST` | `txt,srt,vtt,json` | A comma-separated subset of `txt`, `srt`, `vtt` and `json`. |
| `--num-speakers N` | not set | The exact number of speakers, from 1 to 20. Can't be combined with the two options below. |
| `--min-speakers N`, `--max-speakers N` | not set | Bounds on the number of speakers, from 1 to 20, with min ≤ max. |
| `--no-diarize` | off | Skips diarization: every segment is speaker `S1`. Can't be combined with the speaker options. |
| `--model NAME` | the manifest's default | The ASR model, one of those `lmt models list` shows. |
| `--offline` | off | Never downloads. Exits 5 if a model is missing. |
| `--overwrite` | off | Replaces existing outputs. Without it, an existing output is an error (exit 2), and nothing is written. |
| `-v`, `--verbose` | off | Prints each stage's time to stderr (Q6). |

There is no language option: the product is English-only (F9).

### `lmt live` (G2)

Transcribes live from an input device, printing segments as they're recognised, until Ctrl-C, SIGTERM, `--duration` or the end of `--from-file`. Then it writes the transcript and exits 0 (#82, question 6).

| Option | Default | Effect |
| --- | --- | --- |
| `-o`, `--output-dir DIR` | the current directory | Where the outputs go, as `<DIR>/<stem>.<ext>`. |
| `-n`, `--name STEM` | `live-YYYYMMDD-HHMMSS`, the local start time | The outputs' file stem. |
| `-f`, `--formats LIST` | `txt,srt,vtt,json` | As for `transcribe`. If `txt` is among them, `<stem>.txt` gets each segment as it completes. |
| `--device NAME_OR_INDEX` | the system's default input | The input device, as `--list-devices` shows it. |
| `--list-devices` | | Lists the input devices, with their index and name, and exits. |
| `--from-file FILE` | not set | Reads `FILE` instead of a device, at real-time pace, as if it were being spoken. Any format `transcribe` takes. For trying it out and for measurement. |
| `--duration SECONDS` | not set | Stops after this much audio. |
| `--max-speakers N` | not set | At most N speakers, from 1 to 20. |
| `--no-diarize` | off | Every segment is speaker `S1`. Can't be combined with `--max-speakers`. |
| `--model NAME` | the manifest's live default | The ASR model. |
| `--save-audio` | off | Also writes the audio as `<stem>.wav` (16 kHz mono, 16-bit), so `lmt transcribe` can redo the session afterwards. |
| `--jsonl` | off | Prints the events below to stdout, one JSON object per line, instead of text. |
| `--offline`, `--overwrite`, `-v`, `--verbose` | off | As for `transcribe`. Every output, `<stem>.wav` included, is checked before capture starts. |

**Stdout.** Without `--jsonl`, each segment starts a line, `[HH:MM:SS] Speaker 1: `, and its words are added to the line as they're committed. Times are from the start of the session. Stdout is flushed after every write, text or event, so a reader through a pipe gets each one at once rather than when a buffer fills (G2-6 measures that arrival).

**Stopping.** The first Ctrl-C stops capture, finishes the audio already captured, writes the outputs and exits 0. A second Ctrl-C while it's saving exits 130 and writes none of the final formats. `<stem>.txt` then keeps the segments completed before the stop, and `<stem>.wav` the audio.

**What a failed session keeps.** A live session that ends after capture has started keeps `<stem>.txt` and, with `--save-audio`, `<stem>.wav`, as written so far, however it ends: a second Ctrl-C (130), a bug (1), or any other failure. This is the one exception to "a failed run leaves no outputs" below. The final formats are still written only on success. An error found before capture starts leaves no outputs, as for `transcribe`.

**A silent device.** If the device gives exact zeros for its first 3 s, stderr gets one line, `lmt: warning: <device>: no sound for 3 s; the microphone may be blocked (System Settings, Privacy & Security, Microphone)`, and capture goes on. A loopback device is silent while nothing plays, so silence is never an error.

#### Live events, version 1.0

With `--jsonl`, each line is one event. `t` is the session time in seconds, how much audio had been captured when the event was written. G4 makes this 1.1 ("G4's changes to the commands", below).

| `type` | Fields | When |
| --- | --- | --- |
| `start` | `schema_version`, `t` (0), `source` (as in the transcript JSON), `models` | Capture starts. |
| `words` | `t`, `segment` (its number, from 1), `speaker` (`S1`, …), `label`, `words` (each with `start`, `end`, `text`, `probability`) | Words are committed. They're never changed or sent again. |
| `segment` | `t`, `segment`, `start`, `end`, `speaker`, `text` | A segment is complete. |
| `stop` | `t`, `reason` (`end-of-input`, `duration`, `signal`) | Capture stops. The final formats follow. |

Every event's fields are required. A minor version only adds fields or event types, and readers ignore those they don't know. `schema/live-events.schema.json` publishes the schema.

### `lmt models`

| Subcommand | Effect |
| --- | --- |
| `lmt models download [--model NAME]` | Downloads the default models (the live default among them, once it differs), or the named ASR model plus the diarization models, and verifies every file. A download of a gated pyannote model needs `HF_TOKEN`. |
| `lmt models list [--bytes]` | Lists each model in the manifest: its name, revision, whether it's the default, and whether it's downloaded and verified. `--bytes` adds each one's size on disk and the default models' total (G1-10). |
| `lmt models verify` | Re-checks every downloaded file's SHA-256. A file that doesn't match is deleted, and the command exits 5. |

### `lmt eval`

Needs the `eval` extra (`lmt[eval]`). No other command does: `lmt demo --sample` uses the dataset manifests, but not the extra.

| Option | Effect |
| --- | --- |
| `--set ci` | Downloads and caches the CI subset (LS-ci, AMI-ci), scores it, and writes `results/ci.json`. |
| `--set full` | Runs `--set ci` twice and compares the two runs (G1-12). Then it scores LS full, AMI full and the one-hour recording, and writes `results/full.json`. |
| `--set live` | Plays AMI-ci three times through `lmt live --from-file … --jsonl` in real time, measures each committed word's delay, scores the live transcript, and writes `results/live.json` (G2-6). |
| `--out DIR` | Where the results files go. The default is `results/`. |
| `--update-readme` | After `--set full`, regenerates `README.md`'s results table from `results/full.json`. |
| `--check-readme` | Writes nothing, and runs no evaluation. Exits 0 if `README.md`'s results table equals what `results/full.json` renders, and 6 if it differs, printing the difference. |

### `lmt rename TRANSCRIPT [KEY=NAME ...]` (G3, F7)

Gives speakers names, then exports again (#83, question 5). `TRANSCRIPT` is a transcript JSON that `lmt` wrote. Each `KEY` is a speaker's id (`S1`) or its current label (`Speaker 1`), matched exactly, and an id is tried first. The text is split at the first `=`. Leading and trailing white space is removed from `NAME` (as Python's `str.strip()` does), and what remains must be an allowed name ("Speaker names", below). That trimmed name is the label written.

| Option | Default | Effect |
| --- | --- | --- |
| `-o`, `--output-dir DIR` | the transcript's directory | Where the outputs go, as `<DIR>/<transcript stem>.<ext>`. |
| `-f`, `--formats LIST` | `txt,srt,vtt,json` | As for `transcribe`. |

- **What it writes:** the JSON with the new labels, and each of the other formats rendered from it. It replaces existing files of those names without `--overwrite`, since replacing them is what it is for. As for `transcribe`, the files are written to temporary files and renamed into place only when every one is done. So a failed rename changes nothing.
- **What it changes:** only `speakers[].label`. Ids, times, words, text, `models` and `lmt_version` stay as `transcribe` wrote them.
- **With no `KEY=NAME`,** it writes nothing, and prints a table to stdout: each speaker's id, label, number of segments, speaking time and first few words. This is how you tell who is who.
- **The same name twice** is allowed (#83, question 7). Every export shows both speakers by that name, and their ids stay distinct. Merging two ids into one is not part of v1.0.
- **Errors:** an unknown key, a pair without `=`, or a name that isn't allowed is a usage error (2). A missing or unreadable transcript, one that fails the schema, or one whose `schema_version` has a major part other than 1, is an input error (3).

### `lmt demo [TRANSCRIPT]` (G3, F8)

Serves a page on this computer that shows a transcript, its speakers and its audio together. Clicking a segment plays the audio from it (#83, question 8).

| Option | Default | Effect |
| --- | --- | --- |
| `--audio FILE` | `source.file`, beside the transcript | The recording to play. Any input `transcribe` accepts. |
| `--sample` | off | Instead of `TRANSCRIPT`, the sample meeting: the first 300 s of AMI's ES2004a (CC BY 4.0). The first run downloads it into `LMT_DATA_DIR`, verifies it, and transcribes it with the default models. Later runs serve it from there, offline. Can't be combined with `TRANSCRIPT`. |
| `--port N` | 0, a free port the system picks | The local port. |
| `--no-browser` | off | Prints the URL without opening a browser. |
| `--offline` | off | As for `transcribe`. Only `--sample`'s first run needs the network. |

- **One of `TRANSCRIPT` and `--sample`** is required.
- **On start,** it prints the page's URL to stdout, `http://127.0.0.1:<port>/<token>/`, where the token is random for each run, then opens it in the default browser.
- **It stops** on Ctrl-C or SIGTERM and exits 0, since stopping is how a server ends. Here Ctrl-C is never exit 130, unlike a second Ctrl-C in `lmt live`.
- **Only this computer can reach it:** it listens on `127.0.0.1` only, and has no option to listen elsewhere (Q8). It answers only requests that carry the token and a `Host` of `127.0.0.1:<port>` or `localhost:<port>`, and only GET and HEAD.
- **It writes nothing,** except `--sample`'s cache in `LMT_DATA_DIR`. It needs no `.vtt` or other export beside the transcript.
- **It reads the transcript again** each time the page loads, so after `lmt rename`, reloading the page shows the new names. If the file has become invalid, the page shows an error, stderr gets one `lmt: warning:` line, and the server keeps running.
- **The page** needs nothing from the network: no fonts, scripts or styles from elsewhere. The routes it uses, and the page itself, are internal and may change in any version. Only the command and its options are public.
- **Errors:** a missing or unreadable audio file is an input error (3), naming the file and `--audio`. So are an invalid transcript and a port in use. An audio file whose duration differs from `source.duration` by more than 1 s gets one `lmt: warning:` line, and the page is still served.

### Global options

`lmt --help`, `lmt --version`, which prints `lmt <version>`.

## G4's changes to the commands

G4 (F11, #565) changes the commands above as follows. The tables under "Commands" describe the parser on `main`, which G1-6's test compares with them, so each change moves into its table in the stage-1 PR that makes it (spec, G4 "Stages"): items 3 and 4 for `--engine`, item 5 for the events, item 6 for `lmt eval`. The exit codes, transcript JSON 1.2 and results file 2.0 below already describe G4.

- **`lmt transcribe` and `lmt live`:** `--engine NAME`, default `kyutai`, replaces `--model NAME`. `kyutai` is Kyutai STT 1B on the GPU; `apple` is Apple's on-device recognizer (macOS 26 or later). If Apple's engine can't run on this Mac, the command exits 5 and says why, before any audio is captured (G4-2). One Kyutai model serves both modes, so there is no live default.
- **`lmt models download [--engine NAME]`**, replacing `[--model NAME]`: without it, downloads Kyutai's model and the diarization models and verifies every file. With `--engine apple`, it builds the helper and asks macOS to install Apple's recognizer for `en-US`, plus the diarization models. It is the only command that asks macOS to install them (G4-7).
- **`lmt models list [--bytes]`:** after the manifest's models, a row for Apple's engine with its status (supported, installed, the helper built) and the macOS version. `--bytes` gives each model's size on disk, which Q3 bounds at 2.5 GB each (G1-10), and no total; Apple's assets show as managed by macOS.
- **`lmt eval`:** `--engine NAME`, default `kyutai`, writing `results/<set>.json`; with `apple`, `results/<set>-apple.json`. `--update-readme` works after `--set full` or `--set live`, and both it and `--check-readme` render the table from every results file present (`full`, `full-apple`, `live`, `live-apple`).
- **`lmt live`'s stdout,** without `--jsonl`: on a terminal, words appear as they're recognised, on a provisional line (with Apple's engine, its volatile text too), and once their speaker is known the line is redrawn as `[HH:MM:SS] Speaker 1: ` with its words. Through a pipe, lines come as in 1.0, as words are labelled.
- **Live events 1.1** add one event, so words go out before their labels; a 1.0 reader ignores it and reads the rest as before. The `start` event's `models.asr` gains `engine`, and `words` events now mark where a word gets its label, which Q10's label delay is measured at.

| `type` | Fields | When |
| --- | --- | --- |
| `text` | `t`, `text` (one piece: part of a word, or a whole word, with a leading space when it starts a word), `start`, `end` (the piece's audio, in session time), `starts_word` | Text is committed, before its speaker is known. It is never changed or sent again. Q10's word delay is measured at the event carrying a word's last piece (spec, G4 "Terms"). Volatile text is never sent. |

The two event types carry the same words: joining the `text` events' pieces at each `starts_word` gives exactly the words of the `words` events, in order, with each word's text its pieces' text joined (without the leading space), its `start` its first piece's and its `end` its last piece's. A word's `words` event comes after the `text` event carrying its last piece. A reader that wants labels can read `words` events alone, as in 1.0; one that wants text first reads `text` events (spec, G2-1).

## Install and versions (G3, Q5)

- **The install command,** for v1.0: `uv tool install git+https://github.com/aray4702/auto_research@v1.0.0` (#83, question 9). `uv` is the one prerequisite. It fetches a suitable Python itself if there isn't one. The product runs on macOS 26 or later, on Apple silicon (§6, #565). Apple's engine also needs Xcode's Command Line Tools, for the Swift compiler that builds its helper on first use; Kyutai, the default, doesn't.
- **Versions** follow semantic versioning, from 1.0.0. A release's tag is `v<version>`, and its GitHub release holds the wheel, the sdist and `SHA256SUMS`. `lmt --version` and every transcript's `lmt_version` give the version.
- **Not on PyPI** in v1.0. Publishing there later adds an install command, and doesn't change this one.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Internal error: a bug. It prints an error report naming the input format, the model revisions and the failed stage (Q6), and never a bare traceback. |
| 2 | Usage error: an unknown option, a bad value, conflicting options, or an existing output without `--overwrite`. |
| 3 | Input or output error: a missing, unreadable, empty, corrupt or unsupported input; an input with no audio track; or an unwritable output directory. For `lmt live`: no input device, an unknown one, or one that can't be opened. A device that gives only silence is a warning, not an error ("A silent device", above). |
| 4 | No speech found in the input. |
| 5 | Model error: a model is missing in `--offline` mode, a download failed, or a checksum didn't match. Or Apple's engine can't run on this Mac: macOS older than 26, the recognizer unsupported or not installed, recognition denied, or no Swift compiler to build the helper (G4-2). |
| 6 | Check failed: `lmt eval --check-readme` found that `README.md`'s results table differs from the table rendered from the results files present: `results/full.json`, and since G4 also `full-apple.json`, `live.json` and `live-apple.json` (spec, G1-14). A failure of the check itself (a missing or unreadable file) is 3, and a bug is 1, as for every command. |
| 130 | Interrupted (Ctrl-C). Partial outputs are deleted. For `lmt live`, the first Ctrl-C is the normal stop (exit 0), and 130 is a second Ctrl-C while saving. `<stem>.txt` and `<stem>.wav` are kept (above). `lmt demo` exits 0 instead, since Ctrl-C is how it stops. |

Every error prints a single line to stderr: `lmt: error: <file or option>: <problem>`. Outputs are written to temporary files and renamed into place only when every format is done. So a failed run leaves no outputs (G1-11), except what a live session keeps ("What a failed session keeps", above).

## Environment

| Variable | Effect |
| --- | --- |
| `LMT_CACHE_DIR` | The model cache. The default is `~/.cache/lmt/models`. |
| `LMT_DATA_DIR` | The evaluation datasets' cache. The default is `~/.cache/lmt/data`. |
| `HF_TOKEN` | A Hugging Face token. Only `lmt models download` reads it, and only for the gated pyannote models. Never logged. |

## Output formats

All four come from the same transcript. Speakers are shown by their `label`. Times are in seconds, rounded to the millisecond.

- **Text (`.txt`):** one line per segment, `[HH:MM:SS] Speaker 1: text`.
- **SRT (`.srt`):** one cue per segment, numbered from 1, with times as `HH:MM:SS,mmm`. The cue text is `Speaker 1: text`, wrapped at 42 characters to at most two lines. A longer segment is split into consecutive cues at word boundaries, using the word times.
- **WebVTT (`.vtt`):** a `WEBVTT` header, then one cue per segment, with times as `HH:MM:SS.mmm`. The speaker is both a voice tag and visible text, `<v Speaker 1>Speaker 1: text`, since players don't show a voice tag (#83, question 6). `&`, `<` and `>` in text are escaped as `&amp;`, `&lt;` and `&gt;`, and `&` in the voice tag's annotation is escaped as `&amp;` too (a label can't hold `<` or `>`). So `José & Ana` is written `<v José &amp; Ana>José &amp; Ana: text`. Long segments are split as in SRT, counting the visible prefix and not the tag.
- **JSON (`.json`):** valid against `schema/transcript.schema.json`, as follows.

### Transcript JSON, version 1.2

Version 1.2 (G4) adds one optional field, `models.asr.engine`: `"kyutai"` or `"apple"`, which every transcript `lmt` writes from G4 on holds. For Kyutai, `models.asr` names its model, repository and revision as before. For Apple's engine, `name` is `"SpeechTranscriber"`, `repository` is `"com.apple.Speech"`, and `revision` is the macOS version and build with the locale, such as `"macOS 26.6.2 (25G…) en_US"`, the closest thing to a pin it has (Q7). A 1.1 reader reads a 1.2 file.

Version 1.1 (G2) adds live sessions. `source.file` stays a string in every case, as in 1.0: in a live session's transcript, it is the base name for `--from-file`, and the session's stem (`--name`) for a device. `source` also holds `"live": true` and, for a device, `"device"`, its name. Both are new optional fields, absent in `transcribe`'s output, which is otherwise unchanged from 1.0. So a 1.0 reader reads a 1.1 file.

```json
{
  "schema_version": "1.2",
  "lmt_version": "0.1.0",
  "source": {"file": "meeting.m4a", "duration": 3600.125},
  "models": {
    "asr": {"name": "stt-1b-en_fr", "engine": "kyutai", "repository": "kyutai/stt-1b-en_fr-mlx", "revision": "<commit hash>"},
    "diarization": {"name": "speaker-diarization-3.1", "repository": "pyannote/speaker-diarization-3.1", "revision": "<commit hash>"}
  },
  "speakers": [
    {"id": "S1", "label": "Speaker 1"},
    {"id": "S2", "label": "Speaker 2"}
  ],
  "segments": [
    {
      "start": 0.48, "end": 3.52, "speaker": "S1", "text": "Shall we get started?",
      "words": [{"start": 0.48, "end": 0.71, "text": "Shall", "probability": 0.97}]
    }
  ]
}
```

- **Required:** `schema_version`, `source`, `models`, `speakers` and `segments`. Every segment has `start`, `end`, `speaker` and `text` (F3). `words` is optional.
- **Rules:** segments are sorted by `start`, and 0 ≤ `start` < `end` ≤ `source.duration`. Each `speaker` is the `id` of an entry in `speakers`. Speaker ids are `S1`, `S2`, … in order of first appearance. `models.diarization` is `null` with `--no-diarize`.
- **Labels:** `label` is what every export shows. It starts as "Speaker N", and renaming (F7, G3) changes only `label`.
- **Speaker names** (G3): a `label` is 1 to 32 Unicode code points, with no leading or trailing white space (`rename` trims it from what it's given), no control characters, and none of `<`, `>`, `{`, `}` and `=`. These keep every subtitle format valid, and a name with its `: ` within an SRT line of 42 characters. The schema states the rule. Every label `lmt` has written meets it, so the rule needs no new schema version. Two speakers may share a label.
- **No volatile fields:** there are no timestamps or absolute paths (`source.file` is the input's base name). So two runs on the same input and models give the same file (G1-13). A live session from a device is the exception: its `source.file` is its stem, which holds the start time by default. G2 doesn't require two live runs to be identical.
- **Compatibility:** a minor version only adds optional fields, and readers ignore fields they don't know. A major version changes or removes fields.

## Results file, version 2.0

`results/<set>.json` for Kyutai and `results/<set>-apple.json` for Apple's engine, written by `lmt eval`. Version 2.0 (G4) adds `engine`, renames `whisper-alone` to `asr-alone`, and adds the label delays; the results files are regenerated with the new engines, so no 1.0 file is read any more.

```json
{
  "schema_version": "2.0",
  "set": "full",
  "engine": "kyutai",
  "commit": "<sha>", "dirty": false,
  "date": "2026-10-28T14:00:00-07:00",
  "machine": {"arch": "arm64", "cpu": "Apple M4", "memory_bytes": 17179869184, "os": "macOS 27.0"},
  "lmt_version": "0.1.0",
  "models": {"asr": {"...": "..."}, "diarization": {"...": "..."}},
  "figures": [
    {"dataset": "AMI full", "system": "product", "wer": 0.212, "der": 0.201,
     "audio_seconds": 32400.0, "wall_seconds": 9720.0, "rtf": 0.30, "peak_rss_bytes": 3100000000}
  ],
  "determinism": {"runs": 2, "identical": true}
}
```

- **Values:** `engine` is `kyutai` or `apple`. `system` is `product`, `asr-alone`, `pyannote-alone` or, in the live files, `product-live`. A figure that doesn't apply is `null`.
- **Apple's engine:** `machine.os` holds the macOS version and build, `models.asr` holds Apple's version record (as in the transcript JSON), `determinism` is `null` (G4-6), and `peak_rss_bytes` is `lmt`'s plus the helper's (G4-5).
- **`rtf`'s basis:** for `product`, the spec's RTF, from process start to exit. For `asr-alone` and `pyannote-alone`, that stage's own time within the same product run: its model's loading and inference, without interpreter start, decoding or export, and, when diarization runs, beside the other stage. So the three aren't like for like.
- **Live figures** (`results/live.json` and `results/live-apple.json`, G2-6, G4-8): the word delay as `p50_delay_seconds`, `p95_delay_seconds` and `max_delay_seconds`; the label delay as `p50_label_delay_seconds`, `p95_label_delay_seconds` and `max_label_delay_seconds`; `matched_words`; `runs` (each run's `p95_delay_seconds`, `p95_label_delay_seconds` and `behind_seconds`, how far behind real time it was at the end); plus `wer` and `der`.
- **Timing fields:** `date`, `wall_seconds`, `rtf`, `peak_rss_bytes`, and the live figures' delays and `behind_seconds`. Two runs are compared with these removed (G1-5, G1-12).
- **`dirty`:** `true` when the working tree had uncommitted changes. A results file that the README's table is rendered from must have `dirty: false`.
