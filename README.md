# Local Meeting Transcriber

## What it is

Local Meeting Transcriber turns a meeting recording into an accurate transcript with timestamps that says who said what, running entirely on your Mac, so the audio never leaves it, with its accuracy measured and published. Its `lmt` command works now for recordings: it transcribes one offline with speaker labels and timestamps, renames its speakers, and shows the transcript with its audio in a local page; live transcription is planned. It is built by this repository's development system, which [`blueprint.md`](blueprint.md) describes. Its requirements are in [`project_requirements.md`](project_requirements.md).

## Install

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), installed by its own installer. Nothing else: uv fetches a suitable Python if you don't have one.

```sh
uv tool install git+https://github.com/aray4702/auto_research@v0.2.0
```

The speaker models are gated on Hugging Face. Once, before the first download: make a free account, accept the conditions on the pages of [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1), [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0) and [pyannote/wespeaker-voxceleb-resnet34-LM](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM), create a read token, and set it as `HF_TOKEN` for `lmt models download`. After that, nothing needs the network.

## Use

Download the models once, with your Hugging Face token set as `HF_TOKEN` (see Install). This is the only step that needs the network: after it, everything below works offline, except that `lmt demo --sample`'s first run downloads the sample meeting.

```sh
HF_TOKEN=<your token> lmt models download
```

Transcribe a recording: WAV, FLAC, MP3, M4A, MP4 or MOV. It writes `<name>.txt`, `.srt`, `.vtt` and `.json` in the current directory (`-o DIR` for another), with each segment's timestamps and speaker (Speaker 1, Speaker 2, …).

```sh
lmt transcribe meeting.m4a
```

Give the speakers names. With only the JSON, `lmt rename` lists each speaker's id, label, speaking time and first words; then give each one as `KEY=NAME`, where the key is its id (`S1`) or current label (`Speaker 1`). It rewrites the JSON with the names and renders the other formats from it again, so the TXT, SRT and WebVTT show the names too.

```sh
lmt rename meeting.json
lmt rename meeting.json S1=Alice "Speaker 2=Bob"
```

Show a transcript in a page on your computer, with its speakers and audio: clicking a segment plays the audio from it. `lmt demo` opens it in your browser (`--no-browser` prints the URL instead); press Ctrl-C to stop it. `--sample` shows a sample meeting instead, which its first run downloads and transcribes.

```sh
lmt demo meeting.json
lmt demo --sample
```

## Build

Prerequisites:

- git;
- Python 3.11 or later;
- [uv](https://docs.astral.sh/uv/), the toolchain's installer, at version 0.13.6, as [`toolchain.toml`](toolchain.toml) pins it.

Get a working copy, install the product with its locked dependencies ([`uv.lock`](uv.lock)), and run it:

```sh
git clone https://github.com/aray4702/auto_research.git
cd auto_research
uv sync --locked
uv run lmt --help
```

## Checks

The gate ([`.github/workflows/gate.yml`](.github/workflows/gate.yml)) is the one required check: a change merges only when it passes. Run its generic checks locally, from the root of the working copy:

```sh
python3 -I system/gate/generic.py --base origin/main
```

The product's CI jobs live in [`.github/workflows/product.yml`](.github/workflows/product.yml), which the gate calls. Among them, the `readme` job checks this file against its criteria in [`spec/acceptance.md`](spec/acceptance.md), and the `test` job runs the product's tests, which you can run with `uv run --frozen pytest`. The `eval-ci` job runs the evaluation on the CI subset twice, with `uv run --frozen lmt eval --set ci`, which downloads and caches the subset and writes `results/ci.json`; it checks that the two runs give identical accuracy figures and that the figures meet the accuracy bounds.

## Results

The table below is rendered from [`results/full.json`](results/full.json), the results of the full evaluation. `lmt eval --set full --update-readme` regenerates it (planned: G1 stage 2 builds the evaluation run). `lmt eval --check-readme` checks that it equals what `results/full.json` renders, and the `readme` job runs that check, so the table can't drift from its results file.

<!-- results:start -->
Measured on Apple M4 (arm64, 16.0 GB, macOS 27.0.1) on 2026-10-30, at commit `47cdcf28e374`, with lmt 0.1.0.
Models: ASR `distil-large-v3` (`Systran/faster-distil-whisper-large-v3` at `c3058b475261`); diarization `speaker-diarization-3.1` (`pyannote/speaker-diarization-3.1` at `84fd25912480`).
Two runs of the CI subset gave identical accuracy figures: yes.

| Set | System | WER | DER | RTF |
| --- | --- | ---: | ---: | ---: |
| LS full | product | 2.3 % | — | 0.88 |
| LS full | whisper-alone | 2.3 % | — | 0.77 |
| AMI full | product | 24.3 % | 23.5 % | 0.84 |
| AMI full | whisper-alone | 24.3 % | — | 0.20 |
| AMI full | pyannote-alone | — | 18.8 % | 0.84 |
| one-hour | product | 33.7 % | 30.9 % | 0.89 |
| one-hour | whisper-alone | 33.7 % | — | 0.24 |
| one-hour | pyannote-alone | — | 24.4 % | 0.89 |

RTF is wall-clock time over audio duration. For the product, it runs from process start to exit, model loading included. Whisper alone and pyannote alone come from that same run: each is its own stage's model loading and inference, without start-up, decoding or export, and, with diarization on, while the other stage runs beside it. So the three RTFs aren't like for like.
<!-- results:end -->

## Licences

The product is licensed under the MIT License ([`LICENSE`](LICENSE)); [`NOTICE`](NOTICE) gives the licence of each model it downloads. The speech recognition model, [`Systran/faster-distil-whisper-large-v3`](https://huggingface.co/Systran/faster-distil-whisper-large-v3), by SYSTRAN, converting Hugging Face's distil-whisper/distil-large-v3, is under the MIT License. The diarization pipeline, [`pyannote/speaker-diarization-3.1`](https://huggingface.co/pyannote/speaker-diarization-3.1), by pyannote (Hervé Bredin and Alexis Plaquet), is under the MIT License. Its segmentation model, [`pyannote/segmentation-3.0`](https://huggingface.co/pyannote/segmentation-3.0), by pyannote (Hervé Bredin and Alexis Plaquet), is under the MIT License. Its speaker embedding model, [`pyannote/wespeaker-voxceleb-resnet34-LM`](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM), by the WeSpeaker project, packaged for pyannote.audio by pyannote, is under CC BY 4.0, with its attribution in [`NOTICE`](NOTICE).
