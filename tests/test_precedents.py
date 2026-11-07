"""Tests for precedents (WP5): precedents.py, the capture in the plugin, the prompt and ``hermes calling-mom precedents``.

    cd <plugin dir> && python -m unittest -v test_precedents

A precedent is a person's answer to a ``[for you]`` brief, stored as redacted text (the one place the plugin keeps prose).
"""
import argparse
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_calling_mom as base
import test_cli
from test_calling_mom import BRIEF, kb, plugin, with_conn

PLUGIN_DIR = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("calling_mom_precedents_under_test", PLUGIN_DIR / "precedents.py")
precedents = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(precedents)
cli = test_cli.cli


def entry(pid="p1", board="b", question="Which auth provider do we use?", answer="Okta.", task="t_1", ts=100):
    return {"id": pid, "board": board, "question": question, "answer": answer, "task": task, "ts": ts}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "p.jsonl"

    def test_a_missing_file_is_an_empty_list(self):
        self.assertEqual(precedents.read(self.path), [])

    def test_add_then_read_gives_the_entry_back(self):
        self.assertTrue(precedents.add(self.path, entry()))
        self.assertEqual(precedents.read(self.path), [entry()])

    def test_the_same_id_is_stored_once(self):
        precedents.add(self.path, entry())
        self.assertFalse(precedents.add(self.path, entry(answer="Auth0.")))
        self.assertEqual([e["answer"] for e in precedents.read(self.path)], ["Okta."])

    def test_a_broken_line_is_skipped(self):
        precedents.add(self.path, entry())
        with self.path.open("a") as fh:
            fh.write('not json\n[1]\n{"question": "no id"}\n')
        self.assertEqual(len(precedents.read(self.path)), 1)

    def test_forget_removes_only_that_id(self):
        precedents.add(self.path, entry("p1"))
        precedents.add(self.path, entry("p2"))
        self.assertTrue(precedents.forget(self.path, "p1"))
        self.assertEqual([e["id"] for e in precedents.read(self.path)], ["p2"])

    def test_forget_of_an_unknown_id_changes_nothing(self):
        precedents.add(self.path, entry("p1"))
        self.assertFalse(precedents.forget(self.path, "nope"))
        self.assertEqual(len(precedents.read(self.path)), 1)

    def test_clear_removes_everything_and_counts(self):
        precedents.add(self.path, entry("p1"))
        precedents.add(self.path, entry("p2"))
        self.assertEqual(precedents.clear(self.path), 2)
        self.assertEqual(precedents.read(self.path), [])
        self.assertEqual(precedents.clear(self.path), 0)

    def test_the_id_is_stable_and_differs_per_board_task_and_brief(self):
        a = precedents.make_id("b", "t_1", 7)
        self.assertEqual(a, precedents.make_id("b", "t_1", 7))
        self.assertEqual(len({a, precedents.make_id("c", "t_1", 7), precedents.make_id("b", "t_2", 7),
                              precedents.make_id("b", "t_1", 8)}), 4)

    def test_the_path_sits_next_to_the_log(self):
        self.assertEqual(precedents.path_for(Path("/x/logs/calling-mom.jsonl")), Path("/x/logs/calling-mom-precedents.jsonl"))


class RankTest(unittest.TestCase):
    def test_the_most_overlapping_question_comes_first(self):
        weak = entry("weak", question="Which database should we use for billing?")
        strong = entry("strong", question="Which auth provider should we use for login?")
        got = precedents.rank([weak, strong], "Which auth provider for the login page?", "b")
        self.assertEqual([e["id"] for e in got], ["strong", "weak"])

    def test_no_shared_word_means_not_included(self):
        self.assertEqual(precedents.rank([entry()], "Rename the variable", "b"), [])

    def test_short_words_do_not_count(self):
        self.assertEqual(precedents.rank([entry(question="Do we go on to it?")], "Is it to be or not to be it?", "b"), [])

    def test_on_equal_overlap_the_same_board_wins(self):
        other, same = entry("other", board="x", ts=200), entry("same", board="b", ts=100)
        self.assertEqual([e["id"] for e in precedents.rank([other, same], "Which auth provider?", "b")], ["same", "other"])

    def test_on_equal_overlap_and_board_the_newer_wins(self):
        old, new = entry("old", ts=1), entry("new", ts=2)
        self.assertEqual([e["id"] for e in precedents.rank([old, new], "Which auth provider?", "b")], ["new", "old"])

    def test_at_most_three(self):
        many = [entry(f"p{i}", ts=i) for i in range(6)]
        self.assertEqual(len(precedents.rank(many, "Which auth provider?", "b")), 3)


class PromptTextTest(unittest.TestCase):
    def test_each_line_names_the_card_the_question_and_the_answer(self):
        text = precedents.prompt_text([entry(task="t_9")])
        self.assertIn("t_9", text)
        self.assertIn("Which auth provider do we use?", text)
        self.assertIn("Okta.", text)

    def test_no_entries_no_text(self):
        self.assertEqual(precedents.prompt_text([]), "")


class PrecedentFixture(base.QuestionFixture):
    """A card whose brief a person answers; the file lives in a fresh directory per test."""

    def setUp(self):
        super().setUp()
        self.file = precedents.path_for(plugin._log_path())
        self.llm._res.text = BRIEF
        self.ask()                                   # a blocked card with Mom's `[for you]` brief

    def answer(self, text=None, tid=None, **kw):
        kw.setdefault("by", "cli")
        return plugin.answer_card("t-board", tid or self.tid, text, **kw)

    def stored(self):
        return precedents.read(self.file)

    def brief_card(self, question, reply=None):
        """A second card with its own brief; ``reply`` is the model text."""
        tid = self.card()
        self.llm._res.text = reply or f"ASK_HUMAN:\nQuestion: {question}\nOptions: A) one B) two\nRecommendation: A.\nIf nobody answers: wait."
        self.ask(tid=tid, reason=question)
        return tid


class CaptureTest(PrecedentFixture, unittest.TestCase):
    def test_a_typed_answer_through_the_cli_is_stored(self):
        self.answer("Use Okta, it is licensed.")
        (p,) = self.stored()
        self.assertEqual((p["board"], p["task"], p["question"], p["answer"]),
                         ("t-board", self.tid, "Which auth provider do we use?", "Use Okta, it is licensed."))
        self.assertIsInstance(p["ts"], int)
        self.assertTrue(p["id"])

    def test_a_typed_answer_from_the_desktop_is_stored(self):
        self.answer("Use Okta.", by="desktop")
        self.assertEqual([p["answer"] for p in self.stored()], ["Use Okta."])

    def test_accepting_moms_recommendation_stores_her_recommendation_as_the_answer(self):
        self.answer(accept=True)
        self.assertEqual([p["answer"] for p in self.stored()], ["B, it is cheaper."])

    def test_a_comment_and_an_unblock_without_our_help_is_stored_when_the_card_is_claimed(self):
        with_conn(lambda c: kb.add_comment(c, self.tid, "user", "Use Okta, we have the licence."))
        with_conn(lambda c: kb.unblock_task(c, self.tid))
        plugin.on_claimed(task_id=self.tid, board="t-board")
        (p,) = self.stored()
        self.assertEqual((p["answer"], p["task"], p["board"]), ("Use Okta, we have the licence.", self.tid, "t-board"))

    def test_the_newest_human_comment_is_the_answer(self):
        with_conn(lambda c: kb.add_comment(c, self.tid, "user", "Maybe Auth0?"))
        with_conn(lambda c: kb.add_comment(c, self.tid, "user", "No: Okta."))
        with_conn(lambda c: kb.unblock_task(c, self.tid))
        plugin.on_claimed(task_id=self.tid, board="t-board")
        self.assertEqual([p["answer"] for p in self.stored()], ["No: Okta."])

    def test_an_unblock_without_any_comment_stores_nothing(self):
        with_conn(lambda c: kb.unblock_task(c, self.tid))
        plugin.on_claimed(task_id=self.tid, board="t-board")
        self.assertEqual(self.stored(), [])

    def test_comments_of_the_plugin_are_not_an_answer(self):
        for n, body in enumerate(("[mom answered] x", "[olymp answered] x", "[you via calling-mom] x", "[for you] Question: later brief")):
            with self.subTest(body=body):
                tid = self.brief_card(f"Which provider number {n}?")
                with_conn(lambda c: kb.add_comment(c, tid, "worker", body))
                with_conn(lambda c: kb.unblock_task(c, tid))
                plugin.on_claimed(task_id=tid, board="t-board")
                self.assertEqual(self.stored(), [])

    def test_an_answer_given_here_is_not_stored_again_when_the_card_is_claimed(self):
        self.answer("Use Okta.")
        plugin.on_claimed(task_id=self.tid, board="t-board")
        self.assertEqual(len(self.stored()), 1)

    def test_a_hold_release_without_text_stores_nothing(self):
        plugin._record_hold(self.tid, plugin.cmd_hash("git push --force origin main"), "history", "push a branch")
        self.answer(allow_once=True)
        self.assertEqual(self.stored(), [])

    def test_a_card_without_a_brief_stores_nothing(self):
        tid = self.card()
        with_conn(lambda c: kb.block_task(c, tid, reason="x", kind="needs_input"))
        plugin._record_hold(tid, plugin.cmd_hash("git push --force origin main"), "history", "push a branch")
        self.answer("Go.", tid=tid)
        self.assertEqual(self.stored(), [])

    def test_a_brief_written_by_hand_still_gives_a_question(self):
        tid = self.card()
        with_conn(lambda c: kb.block_task(c, tid, reason="x", kind="needs_input"))
        with_conn(lambda c: kb.add_comment(c, tid, "worker", "[for you] Question: what now? (by hand)"))
        self.answer("This.", tid=tid)
        self.assertEqual([p["question"] for p in self.stored()], ["Question: what now? (by hand)"])

    def test_a_topic_in_the_answer_alone_is_enough_to_keep_it_out(self):
        self.answer("Use Okta, the password is in the vault.")
        self.assertEqual(self.stored(), [])

    def test_the_setting_off_stores_nothing(self):
        plugin.DEFAULTS["answer"]["precedents"] = False
        self.answer("Use Okta.")
        self.assertEqual(self.stored(), [])

    def test_a_board_where_mom_does_not_answer_stores_nothing(self):
        plugin.DEFAULTS["answer"].update(enabled=False, boards=[])
        self.answer("Use Okta.")
        self.assertEqual(self.stored(), [])

    def test_a_local_only_card_stores_nothing(self):
        tid = self.card(title="[local-only] fix the parser")
        with_conn(lambda c: kb.block_task(c, tid, reason="x", kind="needs_input"))
        with_conn(lambda c: kb.add_comment(c, tid, "worker", "[for you] Question: which file?"))
        self.answer("This one.", tid=tid)
        self.assertEqual(self.stored(), [])

    def test_a_brief_about_something_only_the_owner_decides_is_never_stored(self):
        tid = self.brief_card("Which password should the deploy user get?")
        self.answer("Use hunter2-but-longer.", tid=tid)
        self.assertEqual(self.stored(), [])

    def test_the_stored_text_is_redacted_and_cut_at_500_characters(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        self.answer(f"Use {secret} and https://bob:hunter2@host/x. " + "y" * 900)
        (p,) = self.stored()
        self.assertNotIn(secret, p["answer"])
        self.assertNotIn("hunter2", p["answer"])
        self.assertEqual(len(p["answer"]), 500)

    def test_a_long_question_is_cut_at_500_characters(self):
        tid = self.card()
        with_conn(lambda c: kb.block_task(c, tid, reason="x", kind="needs_input"))
        with_conn(lambda c: kb.add_comment(c, tid, "worker", "[for you] Question: which " + "long " * 200 + "thing?"))
        self.answer("This.", tid=tid)
        self.assertEqual(len(self.stored()[0]["question"]), 500)

    def test_a_redactor_that_fails_stores_nothing_and_the_answer_still_goes_through(self):
        with mock.patch.object(plugin._core, "redact_for_egress", side_effect=RuntimeError("boom")):
            out = self.answer("Use Okta.")
        self.assertTrue(out["ok"])
        self.assertEqual(self.status(), "ready")
        self.assertEqual(self.stored(), [])

    def test_the_log_stays_prose_free(self):
        self.answer("Use Okta, it is licensed.")
        self.assertNotIn("Okta", json.dumps(self.rows("human_answer")))


class PromptTest(PrecedentFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.answer("Use Okta, it is licensed.")
        self.llm.calls.clear()
        self.llm._res.text = "ANSWER: Okta, as decided on " + self.tid

    def ask_similar(self, question="Which auth provider should the new login page use?", **kw):
        tid = self.card()
        self.ask(tid=tid, reason=question, **kw)
        return tid

    def test_a_similar_question_gets_the_precedent_in_the_prompt(self):
        self.ask_similar()
        prompt = self.prompt()
        self.assertIn("Use Okta, it is licensed.", prompt)
        self.assertIn(self.tid, prompt)
        self.assertIn("as decided on", prompt)

    def test_a_dissimilar_question_gets_none(self):
        self.ask_similar("Rename the variable foo?")
        self.assertNotIn("Okta", self.prompt())

    def test_at_most_three_precedents_reach_the_prompt(self):
        for i in range(5):
            precedents.add(self.file, entry(f"x{i}", board="t-board", answer=f"ANSWER-{i}", task=f"t_x{i}", ts=10 + i))
        self.ask_similar()
        self.assertEqual(self.prompt().count("\n- (card "), 3)

    def test_the_card_title_counts_for_the_match(self):
        tid = self.card(title="Pick the auth provider for login")
        self.ask(tid=tid, reason="What should we choose?")
        self.assertIn("Use Okta, it is licensed.", self.prompt())

    def test_olymp_sees_the_precedents_too(self):
        plugin.DEFAULTS["olymp"].update(model="m/olymp", provider="p/olymp")
        self.seq("ESCALATE: expensive to undo", "ANSWER: Okta, as decided on " + self.tid)
        self.ask_similar()
        self.assertEqual(len(self.llm.calls), 2)
        self.assertIn("Use Okta, it is licensed.", self.prompt(1))

    def test_the_setting_off_keeps_them_out(self):
        plugin.DEFAULTS["answer"]["precedents"] = False
        self.ask_similar()
        self.assertNotIn("Okta", self.prompt())

    def test_a_question_that_only_a_person_may_decide_gets_none(self):
        self.ask_similar("Which auth provider password should the login page use?")
        self.assertNotIn("Use Okta, it is licensed.", self.prompt())

    def test_the_answer_goes_through_the_egress_redaction_with_the_rest(self):
        precedents.add(self.file, entry("s", board="t-board", answer="key sk-abcdefghijklmnopqrstuvwxyz123456", ts=999))
        self.ask_similar()
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", self.prompt())

    def test_mom_citing_the_precedent_answers_the_card(self):
        tid = self.ask_similar()
        self.assertEqual(self.comments(tid)[-1], "[mom answered] Okta, as decided on " + self.tid)
        self.assertEqual(self.status(tid), "ready")


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "p.jsonl"
        self.s = test_cli.Script()
        self.env = cli.Env(load=lambda: {}, save=lambda c: None, providers=lambda: [], boards=lambda: [], spent=lambda: {},
                           probe=lambda p, m: None, version="0.22.5", internals=True, ask=self.s.ask, say=self.s.say,
                           cron=lambda: None, sweeps=lambda: [], precedents_path=lambda: self.path)

    def test_list_of_nothing_says_so(self):
        self.assertEqual(cli.cmd_precedents(self.env), 0)
        self.assertIn("No precedents", self.s.text)

    def test_list_shows_id_board_card_question_and_answer(self):
        precedents.add(self.path, entry("abc123", board="coding", task="t_7"))
        self.assertEqual(cli.cmd_precedents(self.env, "list"), 0)
        for part in ("abc123", "coding", "t_7", "Which auth provider do we use?", "Okta."):
            self.assertIn(part, self.s.text)

    def test_forget_removes_one(self):
        precedents.add(self.path, entry("abc123"))
        precedents.add(self.path, entry("def456"))
        self.assertEqual(cli.cmd_precedents(self.env, "forget", "abc123"), 0)
        self.assertEqual([e["id"] for e in precedents.read(self.path)], ["def456"])

    def test_forget_of_an_unknown_id_fails(self):
        self.assertEqual(cli.cmd_precedents(self.env, "forget", "nope"), 1)
        self.assertIn("nope", self.s.text)

    def test_forget_without_an_id_fails(self):
        self.assertEqual(cli.cmd_precedents(self.env, "forget"), 1)

    def test_clear_removes_all(self):
        precedents.add(self.path, entry("a"))
        precedents.add(self.path, entry("b"))
        self.assertEqual(cli.cmd_precedents(self.env, "clear"), 0)
        self.assertEqual(precedents.read(self.path), [])
        self.assertIn("2", self.s.text)

    def test_the_parser_knows_the_command(self):
        parser = argparse.ArgumentParser()
        cli.setup_parser(parser)
        args = parser.parse_args(["precedents"])
        self.assertEqual((args.cm_cmd, args.action, args.id), ("precedents", "list", None))
        args = parser.parse_args(["precedents", "forget", "abc"])
        self.assertEqual((args.action, args.id), ("forget", "abc"))
        self.assertEqual(parser.parse_args(["precedents", "clear"]).action, "clear")


class DefaultsTest(unittest.TestCase):
    def test_precedents_are_on_by_default_and_only_meaningful_with_answer(self):
        self.assertIs(plugin.DEFAULTS["answer"]["precedents"], True)
        self.assertFalse(plugin.DEFAULTS["answer"]["enabled"])


if __name__ == "__main__":
    unittest.main()
