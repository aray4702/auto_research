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
  3. the Owner's code-owner account's latest review is APPROVED, on the current head, or carried to it by an SR2 sign-off;
  4. it's next in the merge queue and up to date with `main`, as for any PR;
  5. it doesn't touch `system/standing-rules.md` or `system/dispatcher/plan.py`, which hold this rule and its check. Those the Owner still merges by hand.
- **Granted:** by the Owner, 2026-10-27, about 20:43 PT, in the local session: "Standing rule: approve = merge (Recommended)", after asking "let Dispatcher to merge #113 and #103" ([#116](https://github.com/aray4702/auto_research/issues/116)).

### SR2: the Coordinator, then the builder, judges whether the Owner's approval survives a refresh

- **Who decides:** the Coordinator first. If it can't tell, the builder. If the builder can't tell, the Owner.
- **What:** whether the Owner's approval of an earlier commit still covers a protected PR at its head. They decide by a sign-off (`post.py sign-off`), which SR1 then counts as the Owner's approval of that head. A later conflict-free refresh keeps the sign-off, as it keeps an approval.
- **Conditions, checked by the script before the Coordinator is asked (`plan.sr2_due`):**
  1. the gate is green on the PR's head;
  2. the Reviewer App's latest review is APPROVED and covers the current head;
  3. the Owner's latest review is APPROVED, on an earlier commit;
  4. since that commit, the PR's own added and removed lines are unchanged and in the same order. Only main changed the code around them. Any change to the PR's own lines needs the Owner's approval again;
  5. the PR touches none of the files SR1 excludes.
- **Conditions on the sign-off, checked by the script (`plan.signed_off`):** it is posted through `post.py` by a Coordinator or builder run that the Dispatcher recorded on this PR, and it names the commit the Owner approved.
- **The judgment, not checkable:** do main's changes next to the PR's lines change what the Owner approved, for example by changing what those lines call or the conditions they run under? A builder signs off on the Owner's behalf only when the Owner has said yes to that PR.
- **Granted:** by the Owner, 2026-10-28, between 11:22 and 11:29 PT, in the local session: "For such situation, let coordinator make a judgement and approve first, if coordinator cannot, escalate to builder to make a judgement and approve. if builder cannot, finally escalate to me." ([#150](https://github.com/aray4702/auto_research/issues/150))
