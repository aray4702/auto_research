# System conventions

These are the names and formats the builder chose where the blueprint leaves them open. This is the builder's record; agents read `system/rules.md` and their role file instead. To change anything agents read, use two steps (blueprint §9.2).

## Layout

- **Governing files:** `blueprint.md` and `project_requirements.md`.
- **`system/`:** the system itself. Product-agnostic and protected.
  - `rules.md` is the one shared rules file; `roles/<role>.md` holds one file per role.
  - `standing-rules.md` lists the standing rules.
  - Also here: the Dispatcher, the tools, the gate, caps and sizing.
- **`spec/`:** the acceptance spec (`acceptance.md`), plus the Tester's PR risk criteria (`risk-criteria.md`). Protected.
- **`design/architecture.md` and `design/public-api.md`:** protected.
- **`design/internal/`:** internal designs. The Architect writes them and the Reviewer reviews them.
- **Product code, tests and harness:** placed wherever the first goal's architecture decides.
- **`.github/workflows/gate.yml`:** the gate, part of the system.
- **`.github/workflows/product.yml`:** the product's CI jobs, a product file.
- **`toolchain.toml`** (product file, protected, created by the first goal): the product's toolchain. Its `[agents] allow` list of permission rules (for example `"Bash(make test:*)"`) is added to the Architect's, Developer's, Reviewer's and Tester's run settings; its `allow_write` list only to those of them with the Write tool (the Architect and the Developer).
- **`system/cli/`:** the Owner's CLI and dashboard, `arsys` (blueprint §7). It's a view of GitHub, `dispatcher/state` and the runner, and keeps no state. Its actions are the Owner's: `answer` posts unmarked as the Owner, `approve` opens the review page for the second account (no token for that account lives on this Mac), and `merge`, `pause`/`resume` and `goal add`/`req add` ask for confirmation. `arsys serve` binds to `127.0.0.1` only. `runner.py apply` links it at `~/.local/bin/arsys`. It isn't called `ar`, which would shadow macOS's archiver.
- **`system/runner.toml`:** the runner's settings: each role's model, effort, tools and permissions, the run prompt and the launchd schedules. `system/tools/runner.py apply` installs them on this Mac:
  - It installs the schedules as `~/Library/LaunchAgents/com.aray4702.auto-research.*.plist`.
  - It runs them from a clone of `main` at `~/auto_research-runs/system`.
  - It logs to `~/Library/Logs/auto_research/`.
  - `runner.py check` compares the installed state with `main`.
- **`.github/CODEOWNERS`:** lists the protected paths, owned by the Owner's second account, `sloan3165`.

## Identities

- **`aray4702`** (the Owner's account) carries every agent post and push.
  - Each non-Owner post starts with the role marker `<!-- role:<role> run:<run-id> -->`, added by `system/tools/post.py`. `<role>` is one of `builder`, `dispatcher`, `architect`, `coordinator`, `developer`, `reviewer` or `tester`.
  - Readers match `<!-- role:<role>`. Posts made before the tool existed carry no run ID.
- **The Reviewer GitHub App** gives the Reviewer's approvals, because GitHub ignores approvals from a PR's author, which is `aray4702`. Its approval satisfies the review requirement on unprotected paths only.
  - It is `aray4702-reviewer-app`, App ID 5320656, owned by `aray4702`.
  - It is installed on this repo and also on `aray4702/streaming_media`. The Owner shares it with that project and keeps its broader permissions (decision of 2026-10-26, 19:13 PT, on #7).
  - So the system mints every installation token scoped to this repo only, with `pull_requests: write`, `contents: read` and `checks: read`.
- **`sloan3165`** is the Owner's alone, and no agent ever uses it. It gives the code-owner approval that a protected PR needs.

## Names

- **Branches:** the builder uses `builder/<topic>`; other roles use `<role>/<item>-<topic>`.
- **Labels and records:** the table in `system/rules.md` is canonical.
- **Secrets,** recorded by location only:
  - Escalation webhook: macOS keychain, service `auto_research.escalation-webhook`, account `aray4702`.
  - Discord bot token, for the answer relay: macOS keychain, service `auto_research_relay.discord-bot`, account `aray4702`.
  - Hugging Face read token, for gated models (pyannote): macOS keychain, service `auto_research.huggingface`, account `aray4702`; and the GitHub Actions secret `HF_TOKEN`, which `gate.yml` passes to `product.yml` alone.
  - Reviewer App private key, base64-encoded: macOS keychain, service `auto_research.reviewer-app-key`, account `aray4702`.

## The gate

There is one required check, `gate`. It passes only when every generic check and every product job passes.

The generic checks are in `system/gate/generic.py`, standard library only:
- no secrets in the tree, or in the lines a change adds;
- every action under `.github/` pinned to a full commit SHA;
- TOML and JSON files parse;
- a change to code also changes a test;
- the system's own tests pass.

There is no baseline yet. Until the harness first measures, the gate's pass/fail checks alone guard `main` (blueprint §1).

## Branch protection on `main`

These are the settings as applied on 2026-10-26. The record the Dispatcher's daily audit checks is the machine-readable copy in `system/identity.toml` `[branch_protection]`. Any live difference is a rule breach. Change both together.

- **Merging:** only by pull request, with 1 approval. Stale approvals are dismissed, and code-owner review is required.
- **Required check:** `gate`, accepted only from GitHub Actions (app ID 15368), so a commit status posted by anyone else can't satisfy it. The branch must be up to date with `main`.
- **Administrators are included.** Agents act as the admin `aray4702`, so an admin bypass would be their bypass too.
- **No force pushes and no deletions.**
- **Repo settings:** auto-merge is allowed, and head branches are deleted after merge.

**Emergency bypass, for the Owner only:** turn off administrator enforcement under Settings → Branches → `main`, merge, then turn it back on. Record the merge and its reason on its item.

**Until the Reviewer runs (step 5),** the Owner merges the builder's PRs by approving them from `sloan3165`.
