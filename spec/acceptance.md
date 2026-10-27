# Acceptance spec

| Date (PT) | By | Change |
| --- | --- | --- |
| 2026-10-26 | architect, #20 | First entry: S1's done-check. Rests on the recommended answers to the questions on #20, pending the Owner's approval |

This file maps each requirement in `project_requirements.md` to the check that work is judged by, and turns each goal's measurable outcome into its **done-check**. Each check says where it runs. The Owner approves every change to it.

The generic requirements (blueprint §1) are mapped by a later Definition item. Until then, the gate's generic checks (`system/conventions.md`, "The gate") are what guard them.

## S1: seed goal, `README.md`

**Goal:** `project_requirements.md` §2, S1. **Measurable outcome:** "`README.md` says what the product is, how to build it and how to run its checks."

**Scope for the seed.** §1 (vision, mission) and §6–7 (platforms, toolchain) are still empty, so the README describes the product as it stands now: named, not yet defined, with no product code to build. When G1 fills those sections, Definition for G1 updates S1-2 and S1-3 here, and the README follows.

### Done-check

S1 is done when every criterion below holds on `main`.

| ID | Criterion | How it's checked | Runs |
| --- | --- | --- | --- |
| S1-1 | `README.md` exists at the repository root, and its first line is a level-1 heading naming the product as the requirements file's title does: "voice-app example". | `head -n 1 README.md` prints `# voice-app example` (case-insensitive). | CI |
| S1-2 | **What it is.** A `## What it is` section says, in at most three sentences: the product's name; that it is built by this repository's development system (links `blueprint.md`); and that its vision and requirements are still being defined, linking `project_requirements.md`. It claims no feature the requirements don't state. | The heading exists: `grep -c '^## What it is$' README.md` prints `1`. The wording is checked by reading it against `project_requirements.md` §1–2. | CI (heading); Reviewer, by reading (wording) |
| S1-3 | **How to build.** A `## Build` section lists the prerequisites (git; Python 3, as on CI's `ubuntu-24.04`) and the commands to get a working copy from a fresh clone, in one fenced `sh` block. While there is no product code, it says so in one sentence. | The heading exists, as in S1-2. Each line of the section's `sh` block, run in order from a fresh clone of the commit on `ubuntu-24.04`, exits 0. | CI |
| S1-4 | **How to run the checks.** A `## Checks` section says that the gate (`.github/workflows/gate.yml`) is the one required check, and gives, in one fenced `sh` block, the command that runs its generic checks locally: `python3 -I system/gate/generic.py --base origin/main`. It says that the product's CI jobs live in `.github/workflows/product.yml`. | The heading exists, as in S1-2. `grep -F 'python3 -I system/gate/generic.py --base origin/main' README.md` matches. Each line of the section's `sh` block, run from a fresh clone of the commit (with `origin/main` fetched) on `ubuntu-24.04`, exits 0. | CI |
| S1-5 | Every relative link in `README.md` points to a file or directory that exists in the commit. | For each Markdown link target without a scheme (`http:`, `https:`, `mailto:`), `test -e <target>` from the repository root succeeds (an `#anchor` suffix stripped). | CI |

**Where CI runs it.** The item that writes `README.md` also adds a `readme` job to `.github/workflows/product.yml` that runs the CI checks of S1-1 to S1-5. The gate then fails any later change that breaks them, so the README stays true. The job's details are the Developer's, within this table.

**Fresh evidence for closing S1:** a green `gate` run on `main` that includes the `readme` job, plus the Reviewer's reading for S1-2 on the PR that added the README.

### Stages

One stage, one phase, one Developer item: write `README.md` and add the `readme` job, in one PR. No architecture or public API is needed.
