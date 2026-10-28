# Public API: Local Meeting Transcriber

| Date (PT) | By | Change |
| --- | --- | --- |
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

Each command's `--help` lists exactly the options below (G1-6). Messages and progress go to stderr. On success, `transcribe` prints the path of each file it wrote to stdout, one per line.

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

**Stdout.** Without `--jsonl`, each segment starts a line, `[HH:MM:SS] Speaker 1: `, and its words are added to the line as they're committed. Times are from the start of the session.

**Stopping.** The first Ctrl-C stops capture, finishes the audio already captured, writes the outputs and exits 0. A second Ctrl-C while it's saving exits 130 and writes none of the final formats. `<stem>.txt` then keeps the segments completed before the stop, and `<stem>.wav` the audio. This is the one exception to "a failed run leaves no outputs" below: a live session's text is kept as it goes.

#### Live events, version 1.0

With `--jsonl`, each line is one event. `t` is the session time in seconds, how much audio had been captured when the event was written.

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

Needs the `eval` extra (`lmt[eval]`).

| Option | Effect |
| --- | --- |
| `--set ci` | Downloads and caches the CI subset (LS-ci, AMI-ci), scores it, and writes `results/ci.json`. |
| `--set full` | Runs `--set ci` twice and compares the two runs (G1-12). Then it scores LS full, AMI full and the one-hour recording, and writes `results/full.json`. |
| `--set live` | Plays AMI-ci three times through `lmt live --from-file … --jsonl` in real time, measures each committed word's delay, scores the live transcript, and writes `results/live.json` (G2-6). |
| `--out DIR` | Where the results files go. The default is `results/`. |
| `--update-readme` | After `--set full`, regenerates `README.md`'s results table from `results/full.json`. |
| `--check-readme` | Writes nothing, and runs no evaluation. Exits 0 if `README.md`'s results table equals what `results/full.json` renders, and 6 if it differs, printing the difference. |

### Global options

`lmt --help`, `lmt --version`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Internal error: a bug. It prints an error report naming the input format, the model revisions and the failed stage (Q6), and never a bare traceback. |
| 2 | Usage error: an unknown option, a bad value, conflicting options, or an existing output without `--overwrite`. |
| 3 | Input or output error: a missing, unreadable, empty, corrupt or unsupported input; an input with no audio track; or an unwritable output directory. For `lmt live`: no input device, an unknown one, one that can't be opened, or one that gives only silence (exact zeros for 3 s, as macOS does when the microphone permission is denied). |
| 4 | No speech found in the input. |
| 5 | Model error: a model is missing in `--offline` mode, a download failed, or a checksum didn't match. |
| 6 | Check failed: `lmt eval --check-readme` found that `README.md`'s results table differs from what `results/full.json` renders. A failure of the check itself (a missing or unreadable file) is 3, and a bug is 1, as for every command. |
| 130 | Interrupted (Ctrl-C). Partial outputs are deleted. For `lmt live`, the first Ctrl-C is the normal stop (exit 0), and 130 is a second Ctrl-C while saving (above). |

Every error prints a single line to stderr: `lmt: error: <file or option>: <problem>`. Outputs are written to temporary files and renamed into place only when every format is done. So a failed run leaves no outputs (G1-11).

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
- **WebVTT (`.vtt`):** a `WEBVTT` header, then one cue per segment, with times as `HH:MM:SS.mmm`. The speaker is a voice tag, `<v Speaker 1>text`, and long segments are split as in SRT.
- **JSON (`.json`):** valid against `schema/transcript.schema.json`, as follows.

### Transcript JSON, version 1.1

Version 1.1 (G2) adds live sessions. In a live session's transcript, `source.file` is `null` for a device, and the base name for `--from-file`. `source` also holds `"live": true` and, for a device, `"device"`, its name. Both are optional, and absent in `transcribe`'s output, which is otherwise unchanged from 1.0.

```json
{
  "schema_version": "1.1",
  "lmt_version": "0.1.0",
  "source": {"file": "meeting.m4a", "duration": 3600.125},
  "models": {
    "asr": {"name": "distil-large-v3", "repository": "Systran/faster-distil-whisper-large-v3", "revision": "<commit hash>"},
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
- **No volatile fields:** there are no timestamps or absolute paths (`source.file` is the input's base name). So two runs on the same input and models give the same file (G1-13).
- **Compatibility:** a minor version only adds optional fields, and readers ignore fields they don't know. A major version changes or removes fields.

## Results file, version 1.0

`results/<set>.json`, written by `lmt eval`:

```json
{
  "schema_version": "1.0",
  "set": "full",
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

- **Values:** `system` is `product`, `whisper-alone`, `pyannote-alone` or, in `results/live.json`, `product-live`. A figure that doesn't apply is `null`.
- **Live figures** (`results/live.json`, G2-6): `p50_delay_seconds`, `p95_delay_seconds`, `max_delay_seconds`, `matched_words`, `runs` (each run's `p95_delay_seconds` and `behind_seconds`, how far behind real time it was at the end), plus `wer` and `der`.
- **Timing fields:** `date`, `wall_seconds`, `rtf`, `peak_rss_bytes`, and the live figures' delays and `behind_seconds`. Two runs are compared with these removed (G1-5, G1-12).
- **`dirty`:** `true` when the working tree had uncommitted changes. A results file that the README's table is rendered from must have `dirty: false`.
