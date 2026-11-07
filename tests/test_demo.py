"""Tests for scripts/demo.py: the scratch repo, the four cards, the reset guard and the trigger watcher."""
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("demo", Path(__file__).resolve().parent.parent / "scripts" / "demo.py")
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class ScratchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "demo"

    def test_remote_is_a_local_bare_repo(self):
        demo.scratch(self.root)
        remote = self.root / "remote.git"
        self.assertEqual(git(remote, "rev-parse", "--is-bare-repository"), "true")
        self.assertEqual(git(self.root / "repo", "remote", "get-url", "origin"), str(remote))
        self.assertEqual(git(remote, "rev-parse", "main"), git(self.root / "repo", "rev-parse", "main"))

    def test_repo_has_a_git_identity_so_the_first_amend_does_not_fail(self):
        # A live run: the amend failed for lack of an identity, the retry cost one more pre-flight call of four.
        demo.scratch(self.root)
        for key in ("user.name", "user.email"):
            out = subprocess.run(["git", "config", key], cwd=self.root / "repo", capture_output=True, text=True)
            self.assertTrue(out.stdout.strip(), key)

    def test_second_run_starts_clean(self):
        demo.scratch(self.root)
        (self.root / "repo" / "stray.txt").write_text("x")
        demo.scratch(self.root)
        self.assertFalse((self.root / "repo" / "stray.txt").exists())

    def test_build_folder_fails_the_same_way_every_time(self):
        demo.scratch(self.root)
        outs = [subprocess.run(["sh", "build.sh"], cwd=self.root / "build", capture_output=True, text=True)
                for _ in range(2)]
        self.assertTrue(all(o.returncode != 0 for o in outs))
        self.assertEqual(outs[0].stdout + outs[0].stderr, outs[1].stdout + outs[1].stderr)
        # the cause a tip must find: `out` is a file, not a folder
        self.assertTrue((self.root / "build" / "out").is_file())

    def test_verify_script_wants_three_bullets(self):
        demo.scratch(self.root)
        folder = self.root / "verify"
        run = lambda: subprocess.run(["python3", "check_notes.py"], cwd=folder, capture_output=True).returncode
        self.assertNotEqual(run(), 0)
        (folder / "notes.txt").write_text("- a\n- b\n- c\n")
        self.assertEqual(run(), 0)

    def test_reset_refuses_a_folder_it_did_not_make(self):
        self.root.mkdir()
        (self.root / "precious.txt").write_text("x")
        demo.remove(self.root)
        self.assertTrue((self.root / "precious.txt").exists())

    def test_reset_removes_what_it_made(self):
        demo.scratch(self.root)
        demo.remove(self.root)
        self.assertFalse(self.root.exists())


class LogPathTest(unittest.TestCase):
    def path(self, **env):
        old = {k: os.environ.pop(k, None) for k in ("CALLING_MOM_LOG", "HERMES_HOME")}
        os.environ.update(env)
        try:
            return demo.default_log()
        finally:
            for k in ("CALLING_MOM_LOG", "HERMES_HOME"):
                os.environ.pop(k, None)
                if old[k] is not None:
                    os.environ[k] = old[k]

    def test_profile_home_uses_the_root_log(self):
        self.assertEqual(self.path(HERMES_HOME="/h/profiles/w"), Path("/h/logs/calling-mom.jsonl"))

    def test_plain_home_and_override(self):
        self.assertEqual(self.path(HERMES_HOME="/h"), Path("/h/logs/calling-mom.jsonl"))
        self.assertEqual(self.path(CALLING_MOM_LOG="/x.jsonl"), Path("/x.jsonl"))


class CardsTest(unittest.TestCase):
    def setUp(self):
        self.cards = demo.cards(Path("/tmp/x"))

    def test_four_cards_with_a_marker_in_the_title(self):
        self.assertEqual([c["key"] for c in self.cards], ["error", "question", "hold", "verify"])
        self.assertTrue(all(c["title"].startswith(demo.PREFIX) for c in self.cards))

    def test_every_workspace_is_under_the_root(self):
        self.assertTrue(all(c["workspace"].startswith("dir:/tmp/x/") for c in self.cards))

    def test_the_cards_ask_for_what_triggers_each_rung(self):
        by = {c["key"]: c["body"] for c in self.cards}
        self.assertIn("three runs in total", by["error"])
        self.assertIn("needs_input", by["question"])
        self.assertIn("amend the last commit", by["hold"])
        self.assertIn("in place of the old one", by["hold"])
        self.assertIn("git push --force origin main", by["hold"])  # the spec: the card leads to this exact push
        self.assertIn("\nverify: python3 check_notes.py", by["verify"])


ROWS = [
    {"ts": 100, "kind": "advisor", "task": "t_1", "signal": "same_error"},
    {"ts": 130, "kind": "question", "task": "t_2", "outcome": "answered", "by": "mom"},
    {"ts": 140, "kind": "question", "task": "t_2", "outcome": "human", "by": "mom"},
    {"ts": 160, "kind": "preflight", "task": "t_3", "verdict": "go"},
    {"ts": 170, "kind": "preflight", "task": "t_3", "verdict": "hold"},
    {"ts": 175, "kind": "preflight", "task": "t_9", "verdict": "hold"},       # another card
    {"ts": 50, "kind": "verify", "task": "t_4", "state": "pass"},            # before the start
    {"ts": 200, "kind": "verify", "task": "t_4", "state": "started"},
    {"ts": 210, "kind": "verify", "task": "t_4", "state": "pass"},
]
IDS = {"error": "t_1", "question": "t_2", "hold": "t_3", "verify": "t_4"}


class WatchTest(unittest.TestCase):
    def test_first_matching_row_of_each_card_after_the_start(self):
        self.assertEqual(demo.fired(ROWS, IDS, since=90),
                         {"error": 100, "question": 130, "hold": 170, "verify": 210})

    def test_missing_trigger_is_missing(self):
        self.assertEqual(demo.fired(ROWS[:2], IDS, since=90), {"error": 100, "question": 130})

    def test_read_rows_skips_broken_lines(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "log.jsonl"
            path.write_text(json.dumps(ROWS[0]) + "\nnot json\n\n" + json.dumps(ROWS[1]) + "\n")
            self.assertEqual(demo.read_rows(path), ROWS[:2])
            self.assertEqual(demo.read_rows(Path(d) / "missing"), [])

    def test_report_line_counts_minutes(self):
        self.assertEqual(demo.clock(0), "00:00")
        self.assertEqual(demo.clock(125.4), "02:05")


class FlowTest(unittest.TestCase):
    """main() with the hermes CLI and the clock replaced."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "demo"
        self.calls = []
        self.status = {}
        self.log = Path(self.tmp.name) / "calling-mom.jsonl"
        self._orig = demo.hermes
        demo.hermes = self.fake_hermes
        self.addCleanup(lambda: setattr(demo, "hermes", self._orig))

    def fake_hermes(self, *args, stdin=None):
        self.calls.append((args, stdin))
        if "create" in args and "--json" in args:
            n = sum(1 for a, _ in self.calls if "create" in a and "--json" in a)
            return json.dumps({"id": "t_%d" % n})
        if "list" in args:
            return json.dumps([{"id": i, "title": demo.PREFIX + "old", "status": "blocked"} for i in ("t_old",)]
                              + [{"id": "t_keep", "title": "mine", "status": "done"}])
        return ""

    def run_main(self, *extra):
        return demo.main(["--assignee", "worker", "--root", str(self.root), "--log", str(self.log), *extra],
                         sleep=lambda s: None)

    def test_creates_four_cards_on_the_board_for_the_worker(self):
        self.assertEqual(self.run_main("--wait", "0"), 0)
        creates = [a for a, _ in self.calls if "create" in a and "--json" in a]
        self.assertEqual(len(creates), 4)
        for a in creates:
            self.assertIn("calling-mom-demo", a)
            self.assertEqual(a[a.index("--assignee") + 1], "worker")
        self.assertTrue((self.root / "remote.git").is_dir())

    def test_old_demo_cards_are_archived_and_others_are_left(self):
        self.run_main("--wait", "0")
        archived = [a for a, _ in self.calls if "archive" in a]
        self.assertEqual(archived, [("kanban", "--board", "calling-mom-demo", "archive", "t_old")])

    def test_reset_removes_everything_and_creates_nothing(self):
        demo.scratch(self.root)
        self.assertEqual(demo.main(["--assignee", "worker", "--root", str(self.root), "--reset"]), 0)
        self.assertFalse(self.root.exists())
        self.assertFalse([a for a, _ in self.calls if "create" in a])

    def test_waits_until_all_four_fired_and_reports_success(self):
        self.log.write_text("".join(json.dumps(dict(r, ts=r["ts"] + 10**10)) + "\n" for r in ROWS))
        clock = iter(range(0, 10**4, 5))
        code = demo.main(["--assignee", "worker", "--root", str(self.root), "--log", str(self.log), "--wait", "15"],
                         sleep=lambda s: None, now=lambda: next(clock) + 10**10)
        self.assertEqual(code, 0)

    def test_exit_1_when_a_trigger_is_missing(self):
        self.log.write_text("")
        clock = iter(range(0, 10**5, 400))
        code = demo.main(["--assignee", "worker", "--root", str(self.root), "--log", str(self.log), "--wait", "1"],
                         sleep=lambda s: None, now=lambda: next(clock))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
