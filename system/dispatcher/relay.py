"""The Owner's answers from Discord (blueprint §6). Each poll the Dispatcher reads the escalation channel
through a bot and posts the Owner's answer to a forwarded question on its item, as the Owner (unmarked,
as `arsys answer` does), with a link back to the message. Only the Owner's Discord account, only an item
held at needs-decision:owner, and only answers: nothing else the Owner can do is reachable from the chat.

Off while identity.toml [escalation] leaves any discord_* setting empty. The bot token lives only in the
keychain and is never printed. Requests are outgoing only, so the Mac needs no public endpoint.
Standard library only.
"""

import json
import re
import subprocess
import urllib.request

API = "https://discord.com/api/v10"
AGENT = "DiscordBot (https://github.com/aray4702/auto_research, 1)"
SETTINGS = ("discord_bot_keychain_service", "discord_guild_id", "discord_channel_id", "owner_discord_user_id")
HELD = "needs-decision:owner"
ITEM = re.compile(r"#(\d+)")
LEADING = re.compile(r"^#(\d+)\b[\s:,.-]*")
MENTION = re.compile(r"^<@!?(\d+)>\s*")


def settings(identity):
    """The relay's settings, or None while any is missing: then the relay is off."""
    e = identity["escalation"]
    return {k: str(e[k]) for k in SETTINGS} if all(e.get(k) for k in SETTINGS) else None


def token(cfg, account):
    return subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", cfg["discord_bot_keychain_service"], "-w"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def call(secret, method, path, body=None):
    request = urllib.request.Request(
        API + path, data=json.dumps(body).encode() if body is not None else None, method=method,
        headers={"Authorization": f"Bot {secret}", "Content-Type": "application/json", "User-Agent": AGENT},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read() or b"null")


def messages(secret, channel, after=None):
    """The channel's latest messages, or those after a message ID; oldest first."""
    found = call(secret, "GET", f"/channels/{channel}/messages?limit=100" + (f"&after={after}" if after is not None else ""))
    return sorted(found, key=lambda m: int(m["id"]))


def reply(secret, channel, message_id, text):
    call(secret, "POST", f"/channels/{channel}/messages", {
        "content": text[:1900], "allowed_mentions": {"parse": []},
        "message_reference": {"message_id": message_id, "fail_if_not_exists": False}})


def from_system(message):
    """The Dispatcher's own notices (the webhook) and the bot's confirmations."""
    return bool((message or {}).get("webhook_id") or ((message or {}).get("author") or {}).get("bot"))


def decide(message, cfg, held):
    """(item, answer) to relay, or (None, why not). A why of None means no reply at all: the system's own
    messages, and anyone but the Owner, who learns nothing from the bot."""
    if from_system(message) or (message.get("author") or {}).get("id") != cfg["owner_discord_user_id"]:
        return None, None
    content = (message.get("content") or "").strip()
    bots = {u.get("id") for u in message.get("mentions") or [] if u.get("bot")}
    while (m := MENTION.match(content)) and m[1] in bots:  # "@auto_research #67 1" reads as "#67 1"
        content = content[m.end():]
    if not content:
        return None, "the message arrived empty. Turn on the bot's Message Content intent (Developer Portal → Bot)."
    lead, ref = LEADING.match(content), message.get("referenced_message")
    named = ITEM.search(ref.get("content") or "") if from_system(ref) else None
    if lead:
        item, answer = int(lead[1]), content[lead.end():].strip()
    elif named:
        item, answer = int(named[1]), content
    else:
        return None, "reply to a forwarded question, or start with its item number, as in `#67 1`."
    if not answer:
        return None, f"there was no answer after #{item}."
    if answer.startswith("<!--"):
        return None, "an answer can't start with an HTML comment: on GitHub, that's how agents' posts are marked."
    if item not in held:
        return None, f"#{item} isn't waiting on a decision of yours."
    return item, answer


def body(answer, cfg, message_id):
    link = f"https://discord.com/channels/{cfg['discord_guild_id']}/{cfg['discord_channel_id']}/{message_id}"
    return f"{answer}\n\n(from Discord: {link})"


def run(gh, store, identity, dry_run, now):
    """Relay the Owner's new Discord answers; each message is handled once (relays.jsonl). The first run
    records where the channel is and relays nothing older."""
    cfg = settings(identity)
    if not cfg:
        return []
    would = "would " if dry_run else ""
    secret, channel = token(cfg, identity["escalation"]["keychain_account"]), cfg["discord_channel_id"]
    handled = store.read("relays")
    after = max((int(r["message_id"]) for r in handled), default=None)
    new = messages(secret, channel, after)
    if after is None:
        newest = new[-1]["id"] if new else "0"
        if not dry_run:
            store.append("relays", {"message_id": newest, "result": "start", "at": now.isoformat()})
        return [f"{would}start relaying the Owner's Discord answers after message {newest}"]
    if not new:
        return []
    held = {i["number"] for i in gh.api("GET", f"repos/{gh.repo}/issues?state=open&labels=needs-decision%3Aowner&per_page=100",
                                        paginate=True)}
    lines, recorded = [], None
    for m in new:
        item, said = decide(m, cfg, held)
        if item:
            lines.append(f"{would}relay the Owner's Discord answer to #{item}")
            if dry_run:
                continue
            made = gh.api("POST", f"repos/{gh.repo}/issues/{item}/comments", {"body": body(said, cfg, m["id"])})
            store.append("relays", {"message_id": m["id"], "item": item, "result": "relayed", "comment": made["html_url"],
                                    "at": now.isoformat()})
            reply(secret, channel, m["id"], f"Posted on #{item} as your answer: <{made['html_url']}>")
            recorded = m["id"]
        elif said:
            lines.append(f"{would}tell the Owner why a Discord message wasn't relayed: {said}")
            if dry_run:
                continue
            store.append("relays", {"message_id": m["id"], "result": "refused", "why": said, "at": now.isoformat()})
            reply(secret, channel, m["id"], f"Not relayed: {said}")
            recorded = m["id"]
    if not dry_run and recorded != new[-1]["id"]:
        store.append("relays", {"message_id": new[-1]["id"], "result": "seen", "at": now.isoformat()})
    return lines
