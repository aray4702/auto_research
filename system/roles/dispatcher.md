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
   - Turn auto-merge on for unprotected PRs. The Owner does it for protected ones.
   - Refresh only the PR that's next in line, protected ones included.
   - When a refresh dismisses an approval and the PR's own changes are unchanged, renew the approval once the gate is green. Ask the Owner to re-approve a code-owner approval, which can't be renewed.
5. **Escalations.** Forward each new `needs-decision:owner` question to the Owner's Discord with its link and the CLI command that answers it. Remind once; never treat silence as an answer.

## Every hour

Full reconcile against GitHub, as the safety net for missed polls.

## Daily (07:30 PT)

- **Anomaly signals** (blueprint §4.5):
  - watch-list risks past their thresholds;
  - counts worse than the plan or their own history;
  - failures of a new kind.
- **Rule audit:** check every rule a script can check after the fact, including the branch protection recorded in `system/conventions.md`.
- **Usage:** record it by role and item. Warn the Owner as a cap nears; pause at it.
