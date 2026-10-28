"""What the Dispatcher would do now: pure decisions from a snapshot, the run records and the caps.

Dispatch order (blueprint §4.3): severe problems first, then open PRs that need work, then the
rest, then the work order in sequence; while a work-order item is busy, only parallel-safe items
start. The merge queue and the standing rules' checks are in merge_rules.py, which SR1 never covers.
"""

from system.dispatcher import usage
from system.dispatcher.merge_rules import current, latest_review

CLAIMED = "state:claimed"
TYPE_ROLE = {
    "type:fix": "developer", "type:feature": "developer", "type:investigation": "developer",
    "type:definition": "architect", "type:release": "coordinator",
}
DECISION_ROLE = {
    "needs-decision:coordinator": "coordinator", "needs-decision:architect": "architect",
    "needs-decision:builder": "builder",
}


def held(labels):
    return CLAIMED in labels or "state:blocked" in labels or any(x.startswith("needs-decision:") for x in labels)


def candidates(snap, active_items, identity, skip_review=(), answered=None):
    """(role, item, task, why) in dispatch order. Nothing already claimed or running is offered."""
    bot = identity["reviewer_app"]["bot_login"]
    free = lambda n, labels: n not in active_items and CLAIMED not in labels
    issues = [i for i in snap["issues"] if not any(x.startswith("record:") for x in i["labels"])]
    by_number = {i["number"]: i for i in issues}
    agent_prs = [pr for pr in snap["prs"] if pr["author_role"] and not pr["draft"]]
    # An item whose work is in an open PR is in review: the PR is dispatched, not the item again.
    in_review = {n for pr in snap["prs"] for n in pr.get("resolves", [])}
    out = []
    for i in issues:
        if "sev:1" in i["labels"] and free(i["number"], i["labels"]) and not held(i["labels"]) and i["number"] not in in_review:
            out.append(("developer", i["number"], f"Item #{i['number']} is sev:1: revert first (blueprint §3), then fix the cause.", "sev:1"))
    for pr in agent_prs:
        why = []
        if pr["gate"] == "FAILURE":
            why.append("its gate is red")
        review = latest_review(pr, bot)
        if review and review["state"] == "CHANGES_REQUESTED" and review["commit"] == pr["head"]:
            why.append("the Reviewer asked for changes")
        if pr["merge_state"] == "DIRTY":
            why.append("it conflicts with main")
        if why and free(pr["number"], pr["labels"]):
            out.append((pr["author_role"], pr["number"], f"PR #{pr['number']} needs work: {', '.join(why)}.", "pr-work"))
    # The Owner answered a question: start the role that asked it, on its item (records included).
    for n, role in sorted((answered or {}).items()):
        labels = next((i["labels"] for i in snap["issues"] if i["number"] == n),
                      next((p["labels"] for p in snap["prs"] if p["number"] == n), set()))
        if free(n, labels):
            out.append((role, n, f"The Owner answered your question on #{n}. Act on the answer, then remove needs-decision:owner.", "answered"))
    for pr in agent_prs:
        review = latest_review(pr, bot)
        # Its approval still covers the PR after a conflict-free refresh: no new review (#127).
        reviewed_head = review is not None and (review["commit"] == pr["head"] or current(pr, bot))
        if pr["gate"] == "SUCCESS" and not reviewed_head and pr["number"] not in skip_review and free(pr["number"], pr["labels"]):
            out.append(("reviewer", pr["number"], f"Review PR #{pr['number']}.", "review"))
    # Decisions are dispatched on records too: a plan review's changes reach the Coordinator as
    # needs-decision:coordinator on the goal issue (a record:goal). Triage and the work order skip records.
    for i in snap["issues"] + snap["prs"]:  # a PR too: SR2 asks the Coordinator on the PR itself (#150)
        for label, role in DECISION_ROLE.items():
            if label in i["labels"] and free(i["number"], i["labels"]):
                out.append((role, i["number"], f"Item #{i['number']} needs your decision ({label}).", "decision"))
    for i in snap["issues"]:
        if "state:review" in i["labels"] and free(i["number"], i["labels"]):
            out.append(("reviewer", i["number"], f"Review the plan or design on #{i['number']}.", "plan-review"))
    untriaged = [
        i["number"] for i in issues
        if ("state:triage" in i["labels"] or not any(x.startswith("type:") for x in i["labels"])) and free(i["number"], i["labels"])
    ]
    tasks, whys = [], []
    if untriaged:
        tasks.append("Triage these issues: " + ", ".join(f"#{n}" for n in untriaged) + ".")
        whys.append("triage")
    if snap.get("new_goals"):
        tasks.append("New goals in the requirements without a goal issue: " + ", ".join(snap["new_goals"])
                     + ". Open each one's goal issue (titled \"Goal <ID>: ...\") and its Definition item, as your role file's Goals duty says.")
        whys.append("new-goal")
    if snap.get("closed"):
        tasks.append("Items closed since your last run: " + ", ".join(f"#{n}" for n in snap["closed"])
                     + ". Record their goals' progress, close what is done, and chart and schedule what comes next.")
        whys.append("closed")
    if tasks:
        out.append(("coordinator", None, " ".join(tasks), "+".join(whys)))
    busy = any(n in active_items or n in in_review or CLAIMED in by_number.get(n, {}).get("labels", set())
               for n, _ in snap["work_order"])
    for n, parallel_safe in snap["work_order"]:
        issue = by_number.get(n)
        if not issue or "state:ready" not in issue["labels"] or held(issue["labels"]) or n in active_items or n in in_review:
            continue
        if busy and not parallel_safe:
            continue
        role = next((r for t, r in TYPE_ROLE.items() if t in issue["labels"]), None)
        if role:
            out.append((role, n, "Work on your item, as the work order schedules it.", "work-order"))
            busy = True
    return out


def choose(snap, runs, caps, identity, now, skip_review=(), answered=None):
    """(starts, skipped): the candidates that fit the caps now, simulating each start in turn."""
    active_items = {r.get("item") for r in usage.active(runs) if r.get("item")}
    starts, skipped, simulated = [], [], list(runs)
    for role, item, task, why in candidates(snap, active_items, identity, skip_review, answered):
        if item and any(s[1] == item for s in starts):
            continue
        ok, reasons, _ = usage.may_start(caps, simulated, role, now, identity["time_zone"])
        if not ok:
            skipped.append((role, item, why, "; ".join(reasons)))
            continue
        starts.append((role, item, task, why))
        simulated.append({"event": "start", "run_id": f"planned-{len(starts)}", "role": role, "item": item, "at": now.isoformat()})
    return starts, skipped
