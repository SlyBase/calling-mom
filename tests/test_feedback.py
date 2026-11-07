"""Tests for feedback.py (WP1 of 1.5): the anchors of an advice and what counts as evidence that it was followed.

    cd <plugin dir> && python -m unittest -v test_feedback
"""
import hashlib
import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("calling_mom_feedback_under_test", Path(__file__).resolve().parent.parent / "feedback.py")
feedback = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(feedback)


def kinds(text):
    return [(a.kind, a.text) for a in feedback.anchors(text)]


class AnchorTest(unittest.TestCase):
    def test_commands_paths_and_tool_names_in_backticks_are_anchors(self):
        text = "Run `git status`, then open `src/app/main.py` and `__init__.py` with `read_file`; try `pytest` and `./run.sh --fast`."
        self.assertEqual(kinds(text), [("command", "git status"), ("path", "src/app/main.py"), ("path", "__init__.py"),
                                       ("tool", "read_file"), ("command", "pytest"), ("command", "./run.sh --fast")])

    def test_no_backticks_no_anchors(self):
        self.assertEqual(feedback.anchors("Check the config and run the tests again."), [])

    def test_an_anchor_is_kept_once_and_at_most_eight(self):
        self.assertEqual(kinds("`ls` and `ls`"), [("command", "ls")])
        many = " ".join(f"`cmd{i} x`" for i in range(12))
        self.assertEqual(len(feedback.anchors(many)), 8)

    def test_empty_one_character_and_very_long_spans_are_no_anchors(self):
        self.assertEqual(feedback.anchors("`` `a` `" + "x" * 300 + "`"), [])

    def test_the_hash_is_a_sha256_prefix_of_the_anchor(self):
        (anchor,) = feedback.anchors("`git status`")
        self.assertEqual(anchor.hash, hashlib.sha256(b"git status").hexdigest()[:12])


class MatchTest(unittest.TestCase):
    def match(self, text, tool, args):
        return any(feedback.matches(a, tool, args) for a in feedback.anchors(text))

    def test_a_command_anchor_matches_a_command_that_starts_with_it(self):
        self.assertTrue(self.match("Run `git status` first.", "terminal", {"command": "git status --short"}))
        self.assertTrue(self.match("Run `git status` first.", "terminal", {"command": "  git   status\n"}))  # whitespace is folded
        self.assertTrue(self.match("Run `git status` first.", "execute_code", {"code": "git status"}))

    def test_a_command_anchor_does_not_match_another_command(self):
        self.assertFalse(self.match("Run `git status` first.", "terminal", {"command": "ls"}))
        self.assertFalse(self.match("Run `git status` first.", "terminal", {"command": "cd x && git status"}))  # starts with, not contains

    def test_a_path_anchor_is_contained_in_the_call(self):
        self.assertTrue(self.match("See `src/app.py`.", "read_file", {"path": "/repo/src/app.py"}))
        self.assertTrue(self.match("See `src/app.py`.", "terminal", {"command": "cat /repo/src/app.py | head"}))
        self.assertFalse(self.match("See `src/app.py`.", "read_file", {"path": "/repo/src/other.py"}))

    def test_a_tool_anchor_matches_that_tool_only(self):
        self.assertTrue(self.match("Use `read_file`.", "read_file", {"path": "x"}))
        self.assertFalse(self.match("Use `read_file`.", "terminal", {"command": "cat read_file.py"}))

    def test_the_text_of_a_big_argument_does_not_count(self):
        self.assertFalse(self.match("See `src/app.py`.", "write_file", {"path": "notes.md", "content": "x" * 400 + " src/app.py"}))


if __name__ == "__main__":
    unittest.main()
