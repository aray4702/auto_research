# Public API: Local Meeting Transcriber

| Date (PT) | By | Change |
| --- | --- | --- |
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

### `lmt models`

| Subcommand | Effect |
| --- | --- |
| `lmt models download [--model NAME]` | Downloads the default models, or the named ASR model plus the diarization models, and verifies every file. A download of a gated pyannote model needs `HF_TOKEN`. |
| `lmt models list [--bytes]` | Lists each model in the manifest: its name, revision, whether it's the default, and whether it's downloaded and verified. `--bytes` adds each one's size on disk and the default models' total (G1-10). |
| `lmt models verify` | Re-checks every downloaded file's SHA-256. A file that doesn't match is deleted, and the command exits 5. |

### `lmt eval`

Needs the `eval` extra (`lmt[eval]`).

| Option | Effect |
| --- | --- |
| `--set ci` | Downloads and caches the CI subset (LS-ci, AMI-ci), scores it, and writes `results/ci.json`. |
| `--set full` | Runs `--set ci` twice and compares the two runs (G1-12). Then it scores LS full, AMI full and the one-hour recording, and writes `results/full.json`. |
| `--out DIR` | Where the results files go. The default is `results/`. |
| `--update-readme` | After `--set full`, regenerates `README.md`'s results table from `results/full.json`. |
| `--check-readme` | Writes nothing, and runs no evaluation. Exits 0 if `README.md`'s results table equals what `results/full.json` renders, and 1 otherwise, printing the difference. |

### Global options

`lmt --help`, `lmt --version`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Internal error: a bug. It prints an error report naming the input format, the model revisions and the failed stage (Q6), and never a bare traceback. |
| 2 | Usage error: an unknown option, a bad value, conflicting options, or an existing output without `--overwrite`. |
| 3 | Input or output error: a missing, unreadable, empty, corrupt or unsupported input; an input with no audio track; or an unwritable output directory. |
| 4 | No speech found in the input. |
| 5 | Model error: a model is missing in `--offline` mode, a download failed, or a checksum didn't match. |
| 130 | Interrupted (Ctrl-C). Partial outputs are deleted. |

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

### Transcript JSON, version 1.0

```json
{
  "schema_version": "1.0",
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

- **Values:** `system` is `product`, `whisper-alone` or `pyannote-alone`. A figure that doesn't apply is `null`.
- **Timing fields:** `date`, `wall_seconds`, `rtf` and `peak_rss_bytes`. Two runs are compared with these removed (G1-5, G1-12).
- **`dirty`:** `true` when the working tree had uncommitted changes. A results file that the README's table is rendered from must have `dirty: false`.
