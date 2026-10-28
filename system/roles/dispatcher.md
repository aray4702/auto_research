# Dispatcher

The Dispatcher is a script (`system/dispatcher/`), not an LLM run. It does everything mechanical and makes no judgment calls. This file says what it does, for the builder and the Owner.

## Each poll (every 2 minutes)

1. **Kill switch.** Stop at once when the kill switch is thrown.
2. **Check runs.** Read the state of the runs it started. Judge a run by the runner's state, not its silence. Release the claim of a run that ended without finishing, and stop a run past its timeout.
3. **Start work, in this order, within the caps:**
   1. severe problems first;
   2. then open PRs that need work (feedback, red CI);
   3. then the work order, in sequence.

   While an item is busy, only parallel-safe items start. Claim each item before starting its run, and never run two on one item. Never start a run that would cross what remains of a cap.
4. **Merge one PR at a time,** by auto-merge only.
   - Turn auto-merge on for unprotected PRs, and for protected ones under standing rule SR1: the Reviewer App and the Owner have both approved the current head (`system/standing-rules.md`). The Owner merges the rest of the protected ones.
   - Refresh only the PR that's next in line, protected ones included.
   - When a refresh dismisses an approval and the PR's own changes are unchanged, renew the approval once the gate is green. Ask the Owner to re-approve a code-owner approval, which can't be renewed.
5. **Escalations.** Forward each new `needs-decision:owner` question to the Owner's Discord with its link and the CLI command that answers it. Remind once; never treat silence as an answer. Reopen an item that closed while its question to the Owner was unanswered, once per question, so the question still goes out (#114).
6. **The Owner's Discord answers** (`relay.py`). While paused, none is relayed: each waits at the relay's cursor until resume (the Owner's decision on #77). Only the Owner's Discord user, only to an item held at `needs-decision:owner`, only answers. Each is posted unmarked, with a link to the message, and confirmed in Discord; a refusal says why; anyone else gets no reply.

## Every hour

Full reconcile against GitHub, as the safety net for missed polls.

## Audits (07:30 PT, and on demand)

The Owner asks for one with `arsys audit [product|system]` or `audit [product|system]` in the chat. Each run posts two reports (`audit.py`), each a comment on its record issue (`record:audit-product`, `record:audit-system`), and sends one notice with their headlines and links:
- **Product:** each goal's deadline and items by milestone and state; what waits on the Owner; what merged since the last report; main's gate; the watch list's latest note.
- **System:** the findings below, new or still holding; open system items; the kill switch, the last reconcile, the builder's last daily run, recent runs, usage and flow.

The findings, each raised once:
- **Anomaly signals** (blueprint §4.5), each raised once per key with its evidence (`daily.py`):
  - watch-list risks past their thresholds. A row's Signal names a measured metric (`failed_runs_per_day`, `denials_per_day`, `cost_per_day_usd`, `owner_wait_hours`, `median_item_hours`), and its Threshold is a comparison, such as `> 3`. These go to the Coordinator.
  - bumpy items: two or more unfinished runs, or three or more runs, in a week. These go to the Coordinator, to re-plan.
  - failures of a new kind, a recurring permission denial, cost per finished item more than double its week, and runner drift. These go to the builder, as an item held at `needs-decision:builder`.
  - flow (`flow.py`): a stage whose median hours per finished item this week is more than double its four weeks before and over an hour longer, and any stretch of 30 minutes or more in which a `state:ready` item of a work-order type, or a PR waiting for the Reviewer, waited with no run going. These go to the builder, the same way.
- **Flow:** record each item closed as completed in `flow.jsonl`: its hours in each stage, from opening to close (`running`, `owner`, `decision`, `blocked`, `rework`, `review`, `merging`, `queued`, `triage`).
- **Rule audit:** check every rule a script can check after the fact, including the branch protection recorded in `system/identity.toml`. A breach throws the kill switch and goes to the Owner (blueprint §4.5).
- **Usage:** record it by role and item. Warn the Owner as a cap nears; pause at it.
