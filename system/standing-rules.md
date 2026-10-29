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

- **Who decides:** the Dispatcher, mechanically (`merge_rules.sr1`, used by `merge_plan`).
- **What:** it turns on auto-merge for a protected PR, as it does for an unprotected one. The Owner's code-owner approval stays the decision; only the merge click goes.
- **Conditions, all checked by the script:**
  1. the gate is green on the PR's head;
  2. the Reviewer App's latest review is APPROVED, on the current head;
  3. the Owner's code-owner account's latest review is APPROVED, on the current head, or carried to it by an SR2 sign-off, or the builder approved the head under SR3;
  4. it's next in the merge queue and up to date with `main`, as for any PR;
  5. it doesn't touch `system/standing-rules.md` or `system/dispatcher/merge_rules.py`, which hold this rule and its check. Those the Owner still merges by hand.
- **Granted:** by the Owner, 2026-10-27, about 20:43 PT, in the local session: "Standing rule: approve = merge (Recommended)", after asking "let Dispatcher to merge #113 and #103" ([#116](https://github.com/aray4702/auto_research/issues/116)).

### SR2: others judge, before the Owner, whether the Owner's approval still covers a PR that changed since

- **Who decides, by kind of change since the Owner's approval:**
  - **Only main changed:** the PR's own added and removed lines are unchanged and in the same order, but main edited code next to them. The Coordinator decides first; if it can't tell, the builder; if the builder can't tell, the Owner.
  - **The PR's own lines changed:** the builder decides, unless the builder wrote the PR; if it can't tell, the Owner. A PR the builder wrote goes straight to the Owner: the builder never judges its own changes in the Owner's place.
- **What:** whether the Owner's approval of an earlier commit still covers a protected PR at its head. A PR that SR3 covers goes to SR3 instead. The decision is a sign-off (`post.py sign-off`), which SR1 counts as the Owner's approval of that head. A later conflict-free refresh keeps the sign-off, as it keeps an approval.
- **Conditions checked by the script before anyone is asked (`merge_rules.sr2_due`):**
  1. the gate is green on the PR's head;
  2. the Reviewer App's latest review is APPROVED and covers the current head;
  3. the Owner's latest review is APPROVED, on an earlier commit;
  4. someone may judge this kind of change (`merge_rules.sr2_signers`, above);
  5. the PR touches none of the files SR1 excludes (`standing-rules.md`, `merge_rules.py`). A change to the standing rules or their check stays with the Owner (blueprint §6).
- **Conditions on the sign-off, checked by the script (`merge_rules.signed_off`):**
  - it is posted through `post.py` by a run the Dispatcher recorded on this PR, of a role that may judge this kind of change;
  - it names the commit the Owner approved.
- **The judgment, which no script can check:**
  - **Only main changed:** do main's edits next to the PR's lines change what the Owner approved, for example by changing what those lines call or the conditions they run under?
  - **Own lines changed:** do the changes stay within what the Owner approved (for example, fixes the Reviewer asked for), or change what the PR does in a way the Owner should see?
  - The builder signs off on the Owner's behalf only after the Owner has said yes to that PR.
- **Granted:** by the Owner, 2026-10-28, in the local session:
  - between 11:22 and 11:29 PT: "For such situation, let coordinator make a judgement and approve first, if coordinator cannot, escalate to builder to make a judgement and approve. if builder cannot, finally escalate to me.";
  - between 11:36 and 11:43 PT: "If the PR's own lines changed or the PR changes the standing rules or plan.py, I will let builder to make a judgement and approve first. If builder cannot, escalate to me."; on the follow-up questions, "Not its own PRs (Recommended)" and, for the standing rules and `plan.py`, "Keep them with me (Recommended)".
  - between 11:47 and 11:54 PT: "update #151 so that changes to plan.py will go to builder first, then me." So the merge queue and these rules' checks moved from `plan.py` to `merge_rules.py`, which SR1 excludes. `plan.py`, now only the dispatch order, is an ordinary protected file under SR1 and SR2.
  - Item: [#150](https://github.com/aray4702/auto_research/issues/150).

### SR3: the builder approves a protected PR in the Owner's place

- **Who decides:** the builder, in a run the Dispatcher starts on the PR, never the run that wrote it. If the builder can't tell, or it's an Owner matter, the Owner decides.
- **What:** the Owner's code-owner approval of a protected PR at its head. The decision is an approval (`post.py builder-approve`), which SR1 counts as the Owner's. A later conflict-free refresh keeps it, as it keeps an approval.
- **Not covered:** a PR that touches any of these stays with the Owner:
  - `blueprint.md`, `project_requirements.md` and `system/standing-rules.md` (the Owner kept them);
  - `system/caps.toml`, since a script can't tell whether a change raises a cap;
  - `system/dispatcher/merge_rules.py`, `system/identity.toml` and `.github/CODEOWNERS`. They hold this rule's check and what it enforces (blueprint §5), which §6 keeps from any standing rule.
- **Conditions checked by the script before the builder is asked (`merge_rules.sr3_due`):**
  1. the gate is green on the PR's head;
  2. the Reviewer App's latest review is APPROVED and covers the current head;
  3. no approval of the Owner's, or the builder's, covers the head yet;
  4. it touches none of the files above.
- **Conditions on the approval, checked by the script (`merge_rules.builder_approved`):**
  - it is posted through `post.py` by a builder run the Dispatcher recorded on this PR;
  - that run started after the Reviewer App's latest approval, as GitHub recorded it, so it isn't the run that wrote the PR. A run that wrote a commit started before the Reviewer approved it, and a later push of the PR's own needs the Reviewer again. (A commit's own date is whatever its writer set, so it can't be the check.)
  - it names one of the PR's commits (`post.py builder-approve` refuses any other).
- **The judgment, which no script can check:** what `system/roles/builder.md` lists. Does the change stay within the blueprint, requirements, spec and design? Does it loosen no check and grant no wider permission than needed? Does a secret reach only what needs it? Is it free of Owner matters?
- **How GitHub counts it:** branch protection no longer requires the code owner's review (`identity.toml`, `require_code_owner_reviews = false`). The Dispatcher enforces the Owner's approval, an SR2 sign-off or an SR3 approval before it turns on auto-merge for a protected PR. No agent may merge.
- **Granted:** by the Owner, 2026-10-28, in the local session:
  - "I will let the other builder to review the approve the PRs of protected files. If builder cannot, escalate to me.";
  - on the follow-up questions, the scope "everthing except blueprint, requirements, standing-rules.md and any change the raises a cap", and the mechanism "Dispatcher enforces it".
  - Item: [#221](https://github.com/aray4702/auto_research/issues/221).
