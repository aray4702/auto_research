# Local Meeting Transcriber

## What it is

Local Meeting Transcriber is planned to turn a meeting recording into an accurate transcript with timestamps that says who said what, running entirely on your Mac, so the audio never leaves it, with its accuracy measured and published. So far only the `lmt` command's skeleton is built and tested: `lmt --help` works, and each command says it isn't built yet. It is built by this repository's development system, which [`blueprint.md`](blueprint.md) describes. Its requirements are in [`project_requirements.md`](project_requirements.md).

## Build

Prerequisites:

- git;
- Python 3.11 or later;
- [uv](https://docs.astral.sh/uv/), the toolchain's installer, at version <uv version>, as [`toolchain.toml`](toolchain.toml) pins it.

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

The product's CI jobs live in [`.github/workflows/product.yml`](.github/workflows/product.yml), which the gate calls. Among them, the `readme` job checks this file against its criteria in [`spec/acceptance.md`](spec/acceptance.md), and the `test` job runs the product's tests, which you can run with `uv run --frozen pytest`.

## Licences

The product is licensed under the MIT License ([`LICENSE`](LICENSE)); [`NOTICE`](NOTICE) gives the licence of each model it downloads. The speech recognition model, `Systran/faster-distil-whisper-large-v3`, is MIT-licensed. The diarization pipeline, `pyannote/speaker-diarization-3.1`, is MIT-licensed. Its segmentation model, `pyannote/segmentation-3.0`, is MIT-licensed. Its speaker embedding model, `pyannote/wespeaker-voxceleb-resnet34-LM`, is licensed under CC BY 4.0, with its attribution in [`NOTICE`](NOTICE).
