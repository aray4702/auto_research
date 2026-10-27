"""Tests for the Owner's Discord answers: each refusal and its near miss, once per message, and the post."""

import unittest
from datetime import datetime, timezone
from unittest import mock

from system.dispatcher import dispatch, notify, relay
from system.tools import post

NOW = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
OWNER, STRANGER = "111", "999"
IDENTITY = {"repo": "aray4702/auto_research", "escalation": {
    "keychain_account": "aray4702", "keychain_service": "auto_research.escalation-webhook",
    "discord_bot_keychain_service": "auto_research_relay.discord-bot", "discord_guild_id": "5",
    "discord_channel_id": "6", "owner_discord_user_id": OWNER}}
CFG = relay.settings(IDENTITY)
NOTICE = {"id": "100", "webhook_id": "w", "author": {"id": "w", "bot": True},
          "content": "**Decision needed on #67: Flow**\nShip it?\n1. Yes\n2. No (see #12)"}


def msg(mid, content, author=OWNER, ref=None):
    return {"id": str(mid), "content": content, "author": {"id": author}, "referenced_message": ref}


class DecideTest(unittest.TestCase):
    def test_a_reply_to_a_notice_or_a_leading_number_names_the_item(self):
        self.assertEqual(relay.decide(msg(1, "1", ref=NOTICE), CFG, {67}), (67, "1"), "the notice's first #N, not a later one")
        self.assertEqual(relay.decide(msg(1, "#67: 2, but wait a day"), CFG, {67}), (67, "2, but wait a day"))
        self.assertEqual(relay.decide(msg(1, "#12 1", ref=NOTICE), CFG, {12, 67}), (12, "1"), "a leading #N wins")

    def test_a_leading_mention_of_the_bot_is_skipped_and_of_a_person_is_not(self):
        bot, person = {"id": "77", "bot": True}, {"id": "88"}
        self.assertEqual(relay.decide({**msg(1, "<@77> #67 1"), "mentions": [bot]}, CFG, {67}), (67, "1"))
        self.assertEqual(relay.decide({**msg(1, "<@!77> 1", ref=NOTICE), "mentions": [bot]}, CFG, {67}), (67, "1"))
        item, said = relay.decide({**msg(1, "<@88> #67 1"), "mentions": [person]}, CFG, {67})
        self.assertIsNone(item)
        self.assertIn("start with its item number", said)
        self.assertEqual(relay.decide({**msg(1, "<@77>"), "mentions": [bot]}, CFG, {67})[0], None, "a bare mention is no answer")

    def test_anyone_but_the_owner_and_the_systems_own_messages_get_no_reply(self):
        self.assertEqual(relay.decide(msg(1, "#67 1", author=STRANGER), CFG, {67}), (None, None))
        self.assertEqual(relay.decide(dict(NOTICE), CFG, {67}), (None, None))
        self.assertEqual(relay.decide({**msg(1, "#67 1"), "author": {"id": OWNER, "bot": True}}, CFG, {67}), (None, None))

    def test_refusals_say_why(self):
        cases = [(msg(1, "", ref=NOTICE), "Message Content intent"),
                 (msg(1, "sounds good"), "reply to a forwarded question"),
                 (msg(1, "1", ref=msg(9, "#67 from a person")), "reply to a forwarded question"),
                 (msg(1, "#67"), "no answer after #67"),
                 (msg(1, "#67 <!-- role:builder -->\n1"), "HTML comment"),
                 (msg(1, "#68 1"), "#68 isn't waiting on a decision of yours")]
        for message, why in cases:
            item, said = relay.decide(message, CFG, {67})
            self.assertIsNone(item, message)
            self.assertIn(why, said)

    def test_off_until_every_setting_is_there(self):
        self.assertIsNotNone(CFG)
        for key in relay.SETTINGS:
            self.assertIsNone(relay.settings({"escalation": {**IDENTITY["escalation"], key: ""}}), key)
        self.assertEqual(relay.settings(post.load_identity())["owner_discord_user_id"], "1334397293616562197",
                         "the relay is on, for the Owner's Discord user (#71)")

    def test_the_post_is_unmarked_and_links_back(self):
        text = relay.body("1", CFG, "42")
        self.assertIsNone(post.MARKER.match(text))
        self.assertEqual(text, "1\n\n(from Discord: https://discord.com/channels/5/6/42)")

    def test_the_forwarded_question_says_how_to_answer(self):
        ask = {"id": 1, "user": {"login": "aray4702"}, "created_at": "2026-10-27T00:00:00Z", "html_url": "u",
               "body": "<!-- role:coordinator run:c1 -->\n**Needs a decision: owner.**\n\nShip it?"}
        identity = {"owner_login": "aray4702", "second_login": "sloan3165"}
        plain = notify.owner_questions({"number": 7, "title": "t"}, [ask], identity, [], NOW, post.MARKER)
        replied = notify.owner_questions({"number": 7, "title": "t"}, [ask], identity, [], NOW, post.MARKER, reply=True)
        self.assertTrue(plain[0][1].endswith("Answer in one line on the item."))
        self.assertTrue(replied[0][1].endswith("Answer in one line: reply to this message, or on the item."))


class RunTest(unittest.TestCase):
    def run_relay(self, handled, channel, held=(67,), dry_run=False, comments=()):
        store, gh = mock.Mock(), mock.Mock(repo="aray4702/auto_research")
        store.read.return_value = list(handled)
        gh.api.side_effect = lambda method, path, body=None, paginate=False: (
            list(comments) if method == "GET" and "/comments" in path else
            [{"number": n} for n in held] if method == "GET" else {"html_url": f"c/{path.split('/')[-2]}"})
        with mock.patch.object(relay, "token", return_value="secret"), \
             mock.patch.object(relay, "messages", return_value=channel) as fetch, \
             mock.patch.object(relay, "reply") as replies:
            lines = relay.run(gh, store, IDENTITY, dry_run, NOW)
        return lines, store, gh, fetch, replies

    def test_the_first_run_starts_after_the_newest_message_and_relays_nothing(self):
        lines, store, gh, fetch, replies = self.run_relay([], [NOTICE, msg(101, "1", ref=NOTICE)])
        self.assertEqual(lines, ["start relaying the Owner's Discord answers after message 101"])
        self.assertEqual(fetch.call_args.args, ("secret", "6", None))
        self.assertEqual([c.args[1]["result"] for c in store.append.call_args_list], ["start"])
        gh.api.assert_not_called()
        replies.assert_not_called()

    def test_each_message_is_handled_once_in_order(self):
        channel = [msg(101, "1", ref=NOTICE), msg(102, "hi"), msg(103, "#67 1", author=STRANGER)]
        lines, store, gh, fetch, replies = self.run_relay([{"message_id": "100"}], channel)
        self.assertEqual(fetch.call_args.args, ("secret", "6", 100))
        self.assertEqual(lines, ["relay the Owner's Discord answer to #67",
                                 "tell the Owner why a Discord message wasn't relayed: "
                                 "reply to a forwarded question, or start with its item number, as in `#67 1`."])
        posted = [c for c in gh.api.call_args_list if c.args[0] == "POST"]
        self.assertEqual([(c.args[1], c.args[2]["body"]) for c in posted],
                         [("repos/aray4702/auto_research/issues/67/comments", "1\n\n(from Discord: https://discord.com/channels/5/6/101)")])
        self.assertEqual([(c.args[1]["message_id"], c.args[1]["result"]) for c in store.append.call_args_list],
                         [("101", "relayed"), ("102", "refused"), ("103", "seen")], "the stranger's message moves the cursor only")
        self.assertEqual([c.args[2] for c in replies.call_args_list], ["101", "102"])
        self.assertIn("Posted on #67", replies.call_args_list[0].args[3])

    def test_nothing_new_reads_no_items(self):
        lines, store, gh, fetch, replies = self.run_relay([{"message_id": "100"}], [])
        self.assertEqual(lines, [])
        gh.api.assert_not_called()
        store.append.assert_not_called()

    def test_a_dry_run_changes_nothing(self):
        lines, store, gh, fetch, replies = self.run_relay([{"message_id": "100"}], [msg(101, "1", ref=NOTICE)], dry_run=True)
        self.assertEqual(lines, ["would relay the Owner's Discord answer to #67"])
        self.assertEqual([c.args[0] for c in gh.api.call_args_list], ["GET"])
        store.append.assert_not_called()
        replies.assert_not_called()

    def test_an_answer_already_on_its_item_is_recorded_not_posted_again(self):
        # A pass that posted and then died before recording (#77, finding 2).
        posted = {"html_url": "c/old", "body": relay.body("1", CFG, "101")}
        lines, store, gh, fetch, replies = self.run_relay([{"message_id": "100"}], [msg(101, "1", ref=NOTICE)], comments=[posted])
        self.assertEqual([c.args[0] for c in gh.api.call_args_list], ["GET", "GET"], "no POST")
        self.assertEqual(store.append.call_args.args[1]["comment"], "c/old")
        self.assertIn("c/old", replies.call_args.args[3])

    def test_a_failing_relay_never_stops_the_pass(self):
        for error in (OSError("network down"), SystemExit("gh api GET x failed: HTTP 502")):  # GitHub errors are SystemExit (#77)
            with mock.patch.object(relay, "run", side_effect=error):
                self.assertEqual(dispatch.relay_answers(None, None, IDENTITY, False, NOW),
                                 [f"relay failed: {type(error).__name__}: {error}"])

    def test_nothing_is_relayed_while_paused(self):
        gh = mock.Mock(repo="aray4702/auto_research")
        with mock.patch.object(dispatch.post, "GitHub", return_value=gh), \
             mock.patch.object(dispatch.killswitch, "paused", return_value=(True, "AGENTS_PAUSED is 'true'")), \
             mock.patch.object(dispatch, "relay_answers") as relayed:
            self.assertEqual(dispatch.dispatch_pass("poll", dry_run=False, now=NOW), ["paused: AGENTS_PAUSED is 'true'"])
        relayed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
