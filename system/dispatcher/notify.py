"""Messages to the Owner's chat (blueprint §6): a Discord webhook. Each notice has a key and is
sent once; the keys sent are recorded on the dispatcher/state branch. The URL is never printed.
"""

import json
import subprocess
import urllib.request
from datetime import datetime, timedelta

REMIND_AFTER = timedelta(hours=24)
ASK = "**Needs a decision: owner.**"


def webhook_url(identity):
    e = identity["escalation"]
    return subprocess.run(
        ["security", "find-generic-password", "-a", e["keychain_account"], "-s", e["keychain_service"], "-w"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def send(url, text):
    body = json.dumps({"content": text[:1900], "allowed_mentions": {"parse": []}}).encode()
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "auto-research-dispatcher"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status


def unsent(notices, wanted):
    sent = {n["key"] for n in notices}
    return [(key, text) for key, text in wanted if key not in sent]


def owner_questions(item, comments, identity, notices, now, marker):
    """Notices for an item held at needs-decision:owner: its latest question, or a single reminder
    a day later. Nothing once the Owner has answered after the question."""
    owner, second = identity["owner_login"], identity["second_login"]
    asks = [c for c in comments if c["user"]["login"] == owner and marker.match(c.get("body") or "") and ASK in c["body"]]
    if not asks:
        return []
    ask = asks[-1]
    answered = any(
        c["user"]["login"] in (owner, second) and not marker.match(c.get("body") or "") and c["created_at"] > ask["created_at"]
        for c in comments
    )
    if answered:
        return []
    sent = {n["key"]: n for n in notices}
    key = f"ask:{ask['id']}"
    if key not in sent:
        question = marker.sub("", ask["body"], count=1).replace(ASK, "").strip()
        return [(key, f"**Decision needed on #{item['number']}: {item['title']}**\n{question[:1200]}\n"
                      f"{ask['html_url']}\nAnswer in one line on the item.")]
    if f"remind:{ask['id']}" not in sent and now - datetime.fromisoformat(sent[key]["at"]) >= REMIND_AFTER:
        return [(f"remind:{ask['id']}", f"Reminder: #{item['number']} is still waiting for your decision. {ask['html_url']}")]
    return []


def merge_notice(action, pr, head, url):
    texts = {
        "ask-owner-to-merge": f"PR #{pr} is approved and green: please turn on auto-merge, or merge it. {url}",
        "ask-owner-to-approve": f"PR #{pr} touches protected paths. The Reviewer approved it and its gate is green: it needs your code-owner approval from the second account. {url}",
        "ask-owner-to-reapprove": f"PR #{pr} was refreshed from main, which dismissed your code-owner approval. Please approve it again from the second account. {url}",
    }
    return (f"{action}:{pr}:{head}", texts[action])
