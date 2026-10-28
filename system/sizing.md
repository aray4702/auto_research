# System sizing

Blueprint §8 step 2: the smallest system that works, sized from the Project table. The caps themselves live in [caps.toml](caps.toml). This file gives the reasons for them. The builder re-sizes when Definition needs more capacity and at every stage end.

| Version | Date       | By      | Change         |
| ------- | ---------- | ------- | -------------- |
| 1.0     | 2026-10-26 | builder | First proposal |

## What the runner can do (checked 2026-10-26, 18:10–18:25 PT)

The runner is headless Claude Code (`claude -p`, version 2.1.303) on the Owner's Mac, signed in with the Max 20x subscription.

- **Plan usage is readable.** Every run emits a `rate_limit_event` with the account's 5-hour and 7-day utilization (0–1) and their reset times. The 7-day window resets Thursdays at 09:00 PT.
- **Overage is off** (`overageStatus: rejected`), so the system can never spend money beyond the plan.
- **Per-run usage is reported:** tokens by model and an API-equivalent `total_cost_usd`.
- **Per-run hard caps work:** `--max-turns` and `--max-budget-usd`. A budget cap stops the run with `error_max_budget_usd` and overshoots by at most one model call. `--model` and `--effort` select the model and effort level.
- **Calibration (an estimate, ±2–10×):** this 7-day window held about $990 of API-equivalent usage across all local sessions at a reading of 4 %, which puts 1 point at roughly $250. Until the supervised runs of step 5 calibrate it, the caps assume 1 point per $100.
- **Step 5 update (2026-10-26 22:40 PT):** the window had $1,137 at 7 %. That's about $160 per point on average, but about $50 per point at the margin (from 4 % to 7 %). The readings are whole percents, so the margin is coarse: the 4 → 7 % change is 2–4 points, a range of about $37–$75 per point. The caps now assume **1 point per $50**, the marginal point estimate (not a bound), until the builder's calibration tool (#43) measures it. The system's own 18 supervised runs cost $7.53, which is 0.05–0.15 points.
- **First calibration (2026-10-28, #202):** at 16:46 PT `calibrate.py` read 42 % at $1,597 of local usage. That's an average of 0.026 points/$, but 0.0686 at the margin since the window's first run reading (6 % at $1,072 by the same pricing, 2026-10-26 20:47 PT; 0.0667–0.0705 for ±1 point). Step 5's $1,137 at 7 % two hours later is comparable and gives 0.074–0.078. The early window was cheap per point and the cause isn't known. Usage the tool can't see (claude.ai, other devices) would only overstate the margin, which errs on the safe side. The caps now assume **0.08** (1 point = $12.50), at or above every marginal, and `calibrate.py` now measures the marginal from each window's first run reading.

## Roles and parallelism

| Role        | Runs | Notes |
| ----------- | ---- | ----- |
| Builder     | daily at 08:00 PT, and on builder escalations | The Owner's local session is its interactive window |
| Dispatcher  | script, no LLM | A local launchd job (see Cadences) |
| Architect   | on Definition items | |
| Coordinator | on triage, planning and closing events | |
| Developer   | 1 at a time | Raised to 2 once the work order marks parallel-safe items and a queue builds |
| Reviewer    | on green PR heads, plans and designs | Approves through the Reviewer GitHub App |
| Tester      | **off** until the product has a user-facing surface | The builder enables it when a goal's harness exists |

**No roles are merged.** Merging saves almost no usage, because usage scales with work, not with the number of roles, and it would blur the mandates.

**Concurrency:** at most 2 runs at once across all roles, at most 1 per role, and never 2 on one item. Merges go one at a time, so more Developers wouldn't speed anything up yet.

## Models

Every LLM role uses **Claude Opus 5.5** (`claude-opus-5-5`, $4/$20 per MTok), with effort set explicitly because its default is `medium`:

- **`high`:** Architect, Developer, Reviewer and Builder.
- **`medium`:** Coordinator and Tester.

Claude Fable 5.1 costs 2.5× as much. It is a later lever, for example on an item that failed twice, if measurements show Opus falling short.

## Cadences

- **Dispatcher:** a launchd job every 2 minutes. It polls GitHub with conditional requests, which don't use up the rate limit when nothing has changed. A Mac without a public endpoint can't receive webhooks, so this polling is how it detects the events that start runs. It also does a full reconcile every hour, which is its slow safety net, and an audit and anomaly pass daily at 07:30 PT.
- **Builder daily run:** a separate launchd job at 08:00 PT, which also checks that the Dispatcher is alive. launchd runs a missed job when the Mac wakes.
- **While the Mac sleeps:** nothing runs. The Owner accepted this in step 1.

## CI

- **Runners:** GitHub-hosted `ubuntu-24.04` only, pinned rather than `-latest`. They are free for a public repo.
- **When:** every PR and every push to `main`.
- **The gate:** one required check that aggregates all others.
- **Product platforms:** the first goal adds them. Breadth is reviewed against the 3,000 minutes/month signal.

## Where agents run

- **Machine:** this Mac, as the Owner's macOS user.
- **Workspace:** each run gets its own git worktree under `~/auto_research-runs/`, never the Owner's checkout.
- **GitHub:** runs act through `gh` as `aray4702`. Every post carries its role marker, added by the posting tool (step 4).
- **Runner config:** runs read the runner settings from the repo (step 4).

## Expected usage

These are per-run estimates until step 5 measures real runs.

| Role        | Runs/week (active goal) | API-equivalent per run | Per week |
| ----------- | ----------------------- | ---------------------- | -------- |
| Developer   | 12 | ~$5   | ~$60  |
| Reviewer    | 15 | ~$2   | ~$30  |
| Coordinator | 10 | ~$1.5 | ~$15  |
| Architect   | 3  | ~$6   | ~$18  |
| Tester      | 3 once on | ~$3 | ~$9 |
| Builder     | 7 daily + ~3 | ~$4 | ~$40 |
| **Total**   | | | **~$170 ≈ 13.6 points** at the calibrated 0.08 points/$ |

**Idle week, with no goals:** about $20, all from the builder.

Either way the system stays inside its 20 points, an active week by about 6 points. The binding constraints are one-at-a-time merges and the Owner's attention, not usage. Expected Owner attention:
- **During steps 3–7:** reading and merging about 10–15 builder PRs, creating the Reviewer App, and providing secrets.
- **Afterwards:** protected merges, stage-end confirmations and escalation answers, aiming at under 15 minutes a day.

## Caps

The numbers are in [caps.toml](caps.toml).

- **Plan share:** 20 points of 7-day utilization, with a warning at 15 and a pause at 20.
- **System usage:** each run's reported cost × the calibration factor, recorded by role and item. The Dispatcher starts no run whose per-run budget would cross what remains.
- **Calibration:** the builder's daily run re-estimates points per dollar from the latest utilization reading and the total cost of all local Claude Code sessions in the window. For that it reads usage numbers only, never content, from every project's transcripts on this Mac (decision 2).
- **Owner headroom** (decision 1): no new run starts while the account's 5-hour utilization is at or above 50 %, and the system pauses when the 7-day utilization reaches 90 %, whoever used it. This keeps the system from locking the Owner out of their own sessions.
- **Per run:** max turns, a max budget and a wall-clock timeout per role. At the timeout the Dispatcher stops the run and releases its claim.
- **Counts:** runs per role per day, which also backstops a calibration error.
- **Work in progress:** at most 3 open agent PRs and 2 items in progress, and review rounds per PR are capped. A claim expires 15 minutes after its run's timeout.
- **CI:** $0. 3,000 minutes/month is the signal, with a warning at 2,400.

## Owner decisions

The Owner approves this sizing by merging its PR. The Owner decided two choices that go beyond the Project table, answering "1 yes 2 yes" in the local session on 2026-10-26 at 18:36 PT:

1. **Owner headroom limits:** yes. New starts are held at 50 % of the 5-hour window, and the system pauses at 90 % of the 7-day window.
2. **Calibration from all local Claude Code transcripts:** yes. It reads usage numbers only, from every project.
