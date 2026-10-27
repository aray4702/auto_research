# System conventions

These are the names and formats the builder chose where the blueprint leaves them open. Agents read this file. To change any of them, use two steps (blueprint §9.2).

## Layout

- **Governing files:** `blueprint.md` and `project_requirements.md`.
- **`system/`:** the system itself: rules, roles, the Dispatcher, tools, the gate, caps and sizing. Product-agnostic and protected.
- **`spec/`:** the acceptance spec. Protected.
- **`design/architecture.md` and `design/public-api.md`:** protected.
- **`design/internal/`:** internal designs. The Architect writes them and the Reviewer reviews them.
- **Product code, tests and harness:** placed wherever the first goal's architecture decides.
- **`.github/workflows/gate.yml`:** the gate, part of the system.
- **`.github/workflows/product.yml`:** the product's CI jobs, a product file.
- **`.github/CODEOWNERS`:** lists the protected paths, owned by the Owner's second account, `sloan3165`.

## Identities

- **`aray4702`** (the Owner's account) carries every agent post and push. Each non-Owner post starts with the role marker `<!-- role:<role> -->`, where `<role>` is one of `builder`, `dispatcher`, `architect`, `coordinator`, `developer`, `reviewer` or `tester`.
- **The Reviewer GitHub App** (owned by `aray4702` and installed on this repo only) gives the Reviewer's approvals. GitHub ignores approvals from a PR's author, which is `aray4702`.
- **`sloan3165`** is the Owner's alone, and no agent ever uses it. It gives the code-owner approval that a protected PR needs.

## Names

- **Builder branches:** `builder/<topic>`.
- **Secrets,** recorded by location only:
  - Escalation webhook: macOS keychain, service `auto_research.escalation-webhook`, account `aray4702`.
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
