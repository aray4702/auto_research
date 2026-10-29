"""What the Dispatcher would do now: pure decisions from a snapshot, the run records and the caps.

Dispatch order (blueprint §4.3): severe problems first, then open PRs that need work, then the
rest, then the work order in sequence; while a work-order item is busy, only parallel-safe items
start. Within that order, work for a goal goes ahead of the system's own upkeep (#229). The merge queue and the standing rules' checks are in merge_rules.py, which SR1 never covers.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from system.dispatcher import usage
from system.dispatcher.merge_rules import current, latest_review

CLAIMED = "state:claimed"
DAILY_GRACE = timedelta(minutes=30)  # the launchd job's own start comes first; the poll only catches up (#186)
TYPE_ROLE = {
    "type:fix": "developer", "type:feature": "developer", "type:investigation": "developer",
    "type:definition": "architect", "type:release": "coordinator",
}
DECISION_ROLE = {
    "needs-decision:coordinator": "coordinator", "needs-decision:architect": "architect",
    "needs-decision:builder": "builder",
}


SEVERE = ("sev:1", "sev:2")


def upkeep(candidate, snap):
    """Whether a candidate is the system's own upkeep rather than work for a goal: a builder PR or a `type:system`
    item, unless the work is sev:1 or sev:2. A builder's SR3 approval of a product PR is goal work: the PR is what
    counts, not the role. Runs without an item keep their place: the builder's daily run stays ahead of triage, or a
    busy goal day would starve it again (#186)."""
    role, item, _, why = candidate
    if item is None:
        return False
    issues = {i["number"]: i["labels"] for i in snap["issues"]}
    pr = next((p for p in snap["prs"] if p["number"] == item), None)
    if pr:
        labels = set().union(*[issues.get(n, set()) for n in pr.get("resolves", [])])
        return pr["author_role"] == "builder" and not labels & set(SEVERE)
    labels = issues.get(item, set())
    return "type:system" in labels and not labels & set(SEVERE)


def held(labels):
    return CLAIMED in labels or "state:blocked" in labels or any(x.startswith("needs-decision:") for x in labels)


def daily_due(schedule, runs, now, zone):
    """The builder's daily task, when its scheduled run hasn't started today and is DAILY_GRACE past its time, else
    None. The 08:00 job doesn't start while another builder run is active (a cap), and launchd never retries it (#186)."""
    if not schedule or not schedule.get("enabled"):
        return None
    cmd = schedule["command"]
    task = cmd[cmd.index("--task") + 1]
    local = now.astimezone(ZoneInfo(zone))
    due = local.replace(hour=schedule["hour"], minute=schedule["minute"], second=0, microsecond=0)
    if local < due + DAILY_GRACE:
        return None
    started = any(r["event"] == "start" and r["role"] == "builder" and (r.get("task") or "") == task
                  and datetime.fromisoformat(r["at"]).astimezone(local.tzinfo).date() == local.date() for r in runs)
    return None if started else task


def answered_since(pr, review, finished):
    """Whether the PR's author has had a run on it, started after this review and now ended: it answered the review,
    with a change or by comment. finished: {(item, role): the latest start of an ended run}."""
    at = finished.get((pr["number"], pr["author_role"]))
    return bool(at and review.get("at") and at > datetime.fromisoformat(review["at"].replace("Z", "+00:00")))


def candidates(snap, active_items, identity, skip_review=(), answered=None, daily=None, finished=None):
    """(role, item, task, why) in dispatch order. Nothing already claimed or running is offered. daily: the
    builder's overdue daily task (daily_due), offered after PR work: it is scheduled, and already late. finished:
    for `answered_since`."""
    bot = identity["reviewer_app"]["bot_login"]
    finished = finished or {}
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
        # Once the author has answered, by a change or a comment, the PR goes back to the Reviewer (#257): an answer by
        # comment leaves the head where it was, and restarting the author every poll only says "no change".
        if (review and review["state"] == "CHANGES_REQUESTED" and review["commit"] == pr["head"]
                and not answered_since(pr, review, finished)):
            why.append("the Reviewer asked for changes")
        if pr["merge_state"] == "DIRTY":
            why.append("it conflicts with main")
        if why and free(pr["number"], pr["labels"]):
            out.append((pr["author_role"], pr["number"], f"PR #{pr['number']} needs work: {', '.join(why)}.", "pr-work"))
    if daily:
        out.append(("builder", None, daily, "daily-catch-up"))
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
        if reviewed_head and review["state"] == "CHANGES_REQUESTED" and answered_since(pr, review, finished):
            reviewed_head = False  # answered: the Reviewer reads the answer (#257)
        # A PR held for a decision waits for it: reviewing it again only starves the decision, which
        # comes later in this order and on the same item (#192). It's reviewed once the label is gone.
        waiting = any(x.startswith("needs-decision:") for x in pr["labels"])
        if (pr["gate"] == "SUCCESS" and not reviewed_head and not waiting and pr["number"] not in skip_review
                and free(pr["number"], pr["labels"])):
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
    # Goal work first: the system's own upkeep waits behind it, whatever the kind of either, so an older system PR
    # doesn't hold a goal's PR or decision (#229). sev:1 stays first; the order within each group stays.
    return [c for c in out if not upkeep(c, snap)] + [c for c in out if upkeep(c, snap)]


def choose(snap, runs, caps, identity, now, skip_review=(), answered=None, daily_schedule=None):
    """(starts, skipped): the candidates that fit the caps now, simulating each start in turn."""
    active_items = {r.get("item") for r in usage.active(runs) if r.get("item")}
    daily = daily_due(daily_schedule, runs, now, identity["time_zone"])
    starts, skipped, simulated = [], [], list(runs)
    ended = {r["run_id"] for r in runs if r["event"] == "end"}
    finished = {}
    for r in runs:
        if r["event"] == "start" and r["run_id"] in ended and r.get("item"):
            at = datetime.fromisoformat(r["at"])
            finished[(r["item"], r["role"])] = max(finished.get((r["item"], r["role"]), at), at)
    for role, item, task, why in candidates(snap, active_items, identity, skip_review, answered, daily, finished):
        if item and any(s[1] == item for s in starts):
            continue
        ok, reasons, _ = usage.may_start(caps, simulated, role, now, identity["time_zone"])
        if not ok:
            skipped.append((role, item, why, "; ".join(reasons)))
            continue
        starts.append((role, item, task, why))
        simulated.append({"event": "start", "run_id": f"planned-{len(starts)}", "role": role, "item": item, "at": now.isoformat()})
    return starts, skipped
