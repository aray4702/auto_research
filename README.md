# voice-app example

## What it is

This repository builds the product voice-app example. It is built by this repository's development system, which [`blueprint.md`](blueprint.md) describes. Its vision and requirements are still being defined in [`project_requirements.md`](project_requirements.md).

## Build

Prerequisites:

- git;
- Python 3, as on CI's `ubuntu-24.04` runner (the gate's checks need 3.11 or later).

Get a working copy:

```sh
git clone https://github.com/aray4702/auto_research.git
cd auto_research
```

There is no product code yet, so there is nothing to build beyond the working copy.

## Checks

The gate ([`.github/workflows/gate.yml`](.github/workflows/gate.yml)) is the one required check: a change merges only when it passes. Run its generic checks locally, from the root of the working copy:

```sh
python3 -I system/gate/generic.py --base origin/main
```

The product's CI jobs live in [`.github/workflows/product.yml`](.github/workflows/product.yml), which the gate calls. Among them, the `readme` job checks this file against its criteria in [`spec/acceptance.md`](spec/acceptance.md).
