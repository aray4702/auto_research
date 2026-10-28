# Standing rules

A standing rule hands a kind of decision down to a lower level, with its conditions, or lets a kind of change merge on its own under conditions a script can check (blueprint §6). The builder proposes them and the Owner grants them. None may:
- raise a cap;
- change these standing rules, or the checks that enforce them;
- change blueprint §5.

Each rule records:
- its ID;
- who may decide;
- what they may decide;
- the conditions, checkable where possible;
- when the Owner granted it, with a link.

## Granted

### SR1: a protected PR merges once the Reviewer and the Owner have approved its head

- **Who decides:** the Dispatcher, mechanically (`plan.sr1`, used by `merge_plan`).
- **What:** it turns on auto-merge for a protected PR, as it does for an unprotected one. The Owner's code-owner approval stays the decision; only the merge click goes.
- **Conditions, all checked by the script:**
  1. the gate is green on the PR's head;
  2. the Reviewer App's latest review is APPROVED, on the current head;
  3. the Owner's code-owner account's latest review is APPROVED, on the current head;
  4. it's next in the merge queue and up to date with `main`, as for any PR;
  5. it doesn't touch `system/standing-rules.md` or `system/dispatcher/plan.py`, which hold this rule and its check. Those the Owner still merges by hand.
- **Granted:** by the Owner, 2026-10-27, about 20:43 PT, in the local session: "Standing rule: approve = merge (Recommended)", after asking "let Dispatcher to merge #113 and #103" ([#116](https://github.com/aray4702/auto_research/issues/116)).
