"""Tests for 1.7.0 "Answer from your phone": a card that needs a person is sent once through Hermes' own delivery,
and the person's reply answers the card through the ``calling_mom_answer`` tool.

Reuses the Desktop fixtures (one temporary board ``t-board`` where Mom answers first, a state file, a log).
"""
import json
import unittest
from unittest import mock

import test_dashboard as dash

plugin, kb, with_conn, CMD = dash.plugin, dash.kb, dash.with_conn, dash.CMD


class NotifyFixture(dash.DashboardFixture):
    def setUp(self):
        super().setUp()
        plugin.DEFAULTS["notify"]["target"] = "telegram"
        self.sent, self.result = [], {"success": True}
        self._send, plugin._core.send_message = plugin._core.send_message, self.send
        self.t = 1_000_000  # the clock of notify_waiting: a test moves it to wait out a backoff

    def tearDown(self):
        plugin._core.send_message = self._send
        super().tearDown()

    def send(self, target, text):
        self.sent.append((target, text))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def notify(self, at=0):
        return plugin.notify_waiting("t-board", now=self.t + at)

    def state(self):
        return plugin._load_holds()

    def rebrief(self, tid, question="and which plan?"):
        """The card is blocked again and Mom writes a second brief for it."""
        with_conn(lambda c: kb.claim_task(c, tid))  # the answered card runs again, then asks again
        with_conn(lambda c: kb.block_task(c, tid, reason=question, kind="capability"))  # needs_input twice goes to triage
        with_conn(lambda c: kb.add_comment(c, tid, "calling-mom", dash.base.BRIEF_COMMENT.replace("auth provider do we use", "plan do we buy")))


class DecideLaterTest(NotifyFixture, unittest.TestCase):
    def later(self, tid, what="later"):
        return self.client.post(f"/{what}", json={"board": "t-board", "task_id": tid})

    def test_a_card_decided_later_is_not_sent_and_goes_out_once_after_the_resume(self):
        tid = self.brief_card()
        self.later(tid)
        self.notify()
        self.assertEqual(self.sent, [])
        self.later(tid, "resume")
        self.notify(5)
        self.notify(10)
        self.assertEqual(len(self.sent), 1)

    def test_a_resumed_card_that_was_sent_before_is_not_sent_again(self):
        tid = self.brief_card()
        self.notify()
        self.later(tid)
        self.later(tid, "resume")
        self.notify(5)
        self.assertEqual(len(self.sent), 1)


class DeliveryTest(NotifyFixture, unittest.TestCase):
    def test_it_is_off_until_a_target_is_set(self):
        plugin.DEFAULTS["notify"]["target"] = ""
        self.brief_card()
        self.notify()
        self.assertEqual(self.sent, [])

    def test_a_brief_is_sent_once_to_the_target_with_the_card_and_a_reply_hint(self):
        tid = self.brief_card()
        self.notify()
        self.notify(5)
        self.assertEqual(len(self.sent), 1)
        target, text = self.sent[0]
        self.assertEqual(target, "telegram")
        for part in (tid, "fix the parser", "Which auth provider do we use?", "A) Okta B) Auth0", "Recommendation: B, it is cheaper.",
                     "If nobody answers: the card stays blocked.", "Reply: A, B, or your own answer", "t-board"):
            self.assertIn(part, text)

    def test_a_new_brief_on_the_same_card_is_sent_again(self):
        tid = self.brief_card()
        self.notify()
        self.assertTrue(plugin.answer_card("t-board", tid, "B", by="cli")["ok"])
        self.rebrief(tid)
        self.notify(5)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("Which plan do we buy?", self.sent[1][1])

    def test_a_held_command_is_sent_with_its_reason_and_the_way_to_allow_it(self):
        tid = self.hold_card()
        self.notify()
        text = self.sent[0][1]
        for part in (tid, "it rewrites shared history", "push a branch", "allow"):
            self.assertIn(part, text)

    def test_a_worker_question_without_a_brief_is_sent(self):
        tid = self.card()
        with_conn(lambda c: kb.block_task(c, tid, reason="Use the old or the new API?", kind="needs_input"))
        self.notify()
        self.assertIn("Use the old or the new API?", self.sent[0][1])

    def test_a_card_nobody_waits_on_sends_nothing(self):
        self.card()
        self.notify()
        self.assertEqual(self.sent, [])

    def test_a_board_that_did_not_opt_in_sends_nothing(self):
        self.brief_card()
        plugin.DEFAULTS["answer"]["boards"], plugin.DEFAULTS["advisor"]["boards"] = [], []
        self.notify()
        self.assertEqual(self.sent, [])

    def test_a_local_only_card_sends_nothing(self):
        tid = self.card(title="[local-only] fix the parser")
        self.llm._res.text = dash.base.BRIEF
        self.ask(tid=tid)
        self.notify()
        self.assertEqual(self.sent, [])

    def test_the_text_is_redacted_like_an_outbound_prompt(self):
        self.llm._res.text = dash.base.BRIEF.replace("Which auth provider do we use?", "Use https://bob:hunter2@git.example/x.git or sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789?")
        self.ask()
        self.notify()
        text = self.sent[0][1]
        self.assertNotIn("hunter2", text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789", text)

    def test_nothing_is_sent_when_the_redactor_is_missing(self):
        self.brief_card()
        with mock.patch.object(plugin._core, "redact_for_egress", None):
            self.notify()
        self.assertEqual(self.sent, [])

    def test_the_sweep_sends_it(self):
        self.brief_card()
        plugin._last_scan.clear()
        plugin.sweep("t-board")
        self.assertEqual(len(self.sent), 1)


class FailureTest(NotifyFixture, unittest.TestCase):
    def test_a_failed_send_keeps_the_card_waiting_and_logs_the_class_only(self):
        tid = self.brief_card()
        self.result = RuntimeError("chat 4711 refused: secret text")
        self.notify()
        self.assertEqual(self.status(tid), "blocked")
        self.assertEqual(len(dash.api._needs_you()), 1)
        last = plugin.notify_last()
        self.assertEqual((last["ok"], last["error"], last["target"]), (False, "RuntimeError", "telegram"))
        self.assertNotIn("4711", json.dumps(self.state()))

    def test_an_error_result_of_hermes_is_a_failure_too(self):
        self.brief_card()
        self.result = {"error": "Telegram is not configured: chat 4711"}
        self.notify()
        self.assertEqual(plugin.notify_last()["error"], "send_failed")
        self.assertNotIn("4711", json.dumps(self.state()))

    def test_a_failed_send_is_tried_again_after_a_pause_and_given_up_after_three_tries(self):
        self.brief_card()
        self.result = RuntimeError("down")
        for at in (0, 10, 130, 500, 5000):  # 10 s later: too early; then two real retries; the 4th is not made
            self.notify(at)
        self.assertEqual(len(self.sent), 3)

    def test_a_retry_that_works_ends_the_tries(self):
        self.brief_card()
        self.result = RuntimeError("down")
        self.notify()
        self.result = {"success": True}
        self.notify(200)
        self.notify(1000)
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(plugin.notify_last()["ok"])

    def test_a_send_that_hangs_does_not_hold_the_sweep(self):
        self.brief_card()
        gate = __import__("threading").Event()
        self._hang, plugin._core.send_message = plugin._core.send_message, lambda target, text: gate.wait(5)
        with mock.patch.object(plugin, "NOTIFY_WAIT_S", 0.05):
            started = __import__("time").monotonic()
            self.notify()
            self.assertLess(__import__("time").monotonic() - started, 2)
        gate.set()
        for thread in __import__("threading").enumerate():  # let it finish before the next test swaps the state file
            if thread.name == "calling-mom-notify":
                thread.join(2)


class ReplyTest(NotifyFixture, unittest.TestCase):
    def reply(self, **args):
        return json.loads(plugin.handle_answer(args, session_id="agent:main:telegram:-100123"))

    def test_only_the_session_the_notice_went_to_may_answer(self):
        tid = self.brief_card()
        other = json.loads(plugin.handle_answer({"card_id": tid, "answer": "A"}, session_id="agent:main:discord:9"))
        self.assertIn("only in the session", other["error"])
        self.assertFalse(json.loads(plugin.handle_answer({"card_id": tid, "answer": "A"}))["ok"])
        self.assertEqual(self.status(tid), "blocked")  # neither one answered the card

    def test_a_target_that_names_one_chat_binds_the_answer_to_it(self):
        tid = self.brief_card()
        plugin.DEFAULTS["notify"]["target"] = "telegram:-100123"
        self.assertFalse(json.loads(plugin.handle_answer({"card_id": tid, "answer": "A"}, session_id="agent:main:telegram:42"))["ok"])
        self.assertTrue(json.loads(plugin.handle_answer({"card_id": tid, "answer": "A"}, session_id="agent:main:telegram:-100123"))["ok"])

    def test_a_letter_answers_with_the_option_it_stands_for(self):
        tid = self.brief_card()
        out = self.reply(card_id=tid, answer="a")
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.status(tid), "ready")
        self.assertIn("[you via calling-mom] A) Okta", self.comments(tid))
        self.assertEqual(dash.base.log_rows("human_answer")[-1]["via"], "phone")

    def test_own_words_are_written_as_they_are(self):
        tid = self.brief_card()
        self.assertTrue(self.reply(card_id=tid, answer="Use Auth0 but on the EU region")["ok"])
        self.assertIn("[you via calling-mom] Use Auth0 but on the EU region", self.comments(tid))

    def test_accept_takes_the_recommendation(self):
        tid = self.brief_card()
        self.assertTrue(self.reply(card_id=tid, answer="accept")["ok"])
        self.assertIn("[you via calling-mom] B, it is cheaper.", self.comments(tid))

    def test_a_letter_without_a_matching_option_is_written_as_it_is(self):
        tid = self.brief_card()
        self.assertTrue(self.reply(card_id=tid, answer="C")["ok"])
        self.assertIn("[you via calling-mom] C", self.comments(tid))

    def test_allow_lets_the_held_command_run_once(self):
        tid = self.hold_card()
        self.assertTrue(self.reply(card_id=tid, answer="allow")["ok"])
        self.assertEqual(self.status(tid), "ready")
        self.assertIn(plugin.cmd_hash(CMD), plugin._load_holds()["allowances"][tid])

    def test_allow_on_a_card_without_a_hold_is_refused(self):
        tid = self.brief_card()
        out = self.reply(card_id=tid, answer="allow")
        self.assertFalse(out["ok"])
        self.assertEqual(self.status(tid), "blocked")

    def test_the_board_is_found_from_the_card_id_or_taken_from_the_call(self):
        tid = self.brief_card()
        self.assertTrue(self.reply(card_id=tid, answer="B", board="t-board")["ok"])

    def test_an_unknown_card_or_board_or_an_empty_answer_is_an_error_and_changes_nothing(self):
        tid = self.brief_card()
        self.assertIn("no card", self.reply(card_id="t_nope", answer="A")["error"])
        self.assertIn("did not opt in", self.reply(card_id=tid, answer="A", board="other")["error"])
        self.assertFalse(self.reply(card_id=tid, answer="  ")["ok"])
        self.assertFalse(self.reply(card_id="", answer="A")["ok"])
        self.assertEqual(self.status(tid), "blocked")

    def test_a_store_that_cannot_be_opened_is_an_error_not_a_crash(self):
        tid = self.brief_card()
        with mock.patch.object(plugin, "answer_card", side_effect=OSError("disk gone")):
            out = self.reply(card_id=tid, answer="A", board="t-board")
        self.assertEqual(out, {"ok": False, "error": "OSError: disk gone"})

    def test_a_card_that_is_not_blocked_cannot_be_answered(self):
        tid = self.card()
        self.assertIn("not blocked", self.reply(card_id=tid, answer="A", board="t-board")["error"])

    def test_the_tool_is_off_in_a_kanban_worker_and_where_no_target_is_set(self):
        self.assertFalse(plugin._answer_tool_on())  # the fixture's env says: a kanban worker
        for key in ("HERMES_KANBAN_TASK",):
            dash.os.environ.pop(key, None)
        self.assertTrue(plugin._answer_tool_on())
        plugin.DEFAULTS["notify"]["target"] = ""
        self.assertFalse(plugin._answer_tool_on())


class DesktopTest(NotifyFixture, unittest.TestCase):
    def test_a_delivered_card_says_where_it_went(self):
        self.brief_card()
        self.assertIsNone(self.get("/needs-you")[0]["sent_to"])
        self.notify()
        self.assertEqual(self.get("/needs-you")[0]["sent_to"], "telegram")

    def test_a_failed_card_says_nothing(self):
        self.brief_card()
        self.result = RuntimeError("down")
        self.notify()
        self.assertIsNone(self.get("/needs-you")[0]["sent_to"])

    def test_a_new_brief_clears_the_note_until_it_is_sent(self):
        tid = self.brief_card()
        self.notify()
        plugin.answer_card("t-board", tid, "B", by="cli")
        self.rebrief(tid)
        self.assertIsNone(self.get("/needs-you")[0]["sent_to"])


class RegisterTest(unittest.TestCase):
    def test_the_answer_tool_is_registered_and_listed_in_the_manifest(self):
        seen = {}
        ctx = mock.MagicMock()
        ctx.register_tool.side_effect = lambda **kw: seen.__setitem__(kw["name"], kw)
        plugin.register(ctx)
        self.assertIn("calling_mom_answer", seen)
        self.assertEqual(seen["calling_mom_answer"]["toolset"], "calling-mom")
        self.assertEqual(seen["calling_mom_answer"]["schema"]["parameters"]["required"], ["card_id", "answer"])
        manifest = (dash.PLUGIN_DIR / "plugin.yaml").read_text()
        self.assertIn("  - calling_mom_answer\n", manifest)


class SendMessageTest(unittest.TestCase):
    def test_the_core_calls_the_hermes_send_cli_and_returns_its_json(self):
        done = mock.Mock(returncode=0, stdout='[secrets] applied\n{\n  "success": true,\n  "chat_id": "-100"\n}')
        with mock.patch("subprocess.run", return_value=done) as run, mock.patch.object(plugin._core.shutil, "which", return_value="/opt/hermes/bin/hermes"):
            self.assertEqual(plugin._core.send_message("telegram:42", "hi"), {"success": True, "chat_id": "-100"})
        self.assertEqual(run.call_args.args[0], ["/opt/hermes/bin/hermes", "send", "--to", "telegram:42", "--json", "hi"])

    def test_a_cli_that_is_not_on_the_path_is_called_by_its_name(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=2, stdout="")) as run, \
                mock.patch.object(plugin._core.shutil, "which", return_value=None):
            with self.assertRaises(RuntimeError):
                plugin._core.send_message("telegram", "hi")
        self.assertEqual(run.call_args.args[0][0], "hermes")

    def test_output_without_a_json_object_raises(self):
        for stdout in ("no json here", "{not json} then {still not}"):
            with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=stdout)):
                with self.assertRaises(ValueError):
                    plugin._core.send_message("telegram", "hi")
