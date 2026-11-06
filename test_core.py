"""The single door to Hermes internals (catalog rule 9): one file touches them, and a missing one degrades."""
import ast
import builtins
import importlib.util
import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import test_calling_mom  # sets up the Hermes test home and loads the plugin the way the other suites do

HERE = Path(__file__).resolve().parent
GONE = {"hermes_cli.kanban_db_boards", "hermes_cli.config", "hermes_cli.profiles",
        "hermes_cli.model_switch_providers", "agent.plugin_llm", "hermes_cli.__version__",
        "hermes_cli.curses_ui"}
PRIVATE_ROOTS = ("hermes_cli", "agent", "tools", "gateway")


def load(name="calling_mom_core_under_test"):
    spec = importlib.util.spec_from_file_location(name, HERE / "_core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_imports(path):
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
            [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
        found += [(path.name, n, node.lineno) for n in names if n.split(".")[0] in PRIVATE_ROOTS]
    return found


class OneDoorTest(unittest.TestCase):
    def test_only_core_imports_hermes_internals(self):
        files = [p for p in HERE.glob("*.py") if not p.name.startswith("test_") and p.name not in {"_core.py", "linecov.py", "check.py"}]
        files += list((HERE / "scripts").glob("*.py")) + list((HERE / "dashboard").glob("*.py"))
        offenders = [hit for p in files for hit in private_imports(p) if p.name != "smoke.py"]
        self.assertEqual(offenders, [], "route these through _core.py")

    def test_core_loads_against_this_hermes(self):
        core = load()
        self.assertIsNone(core.ERROR)
        self.assertTrue(callable(core.kb.get_task) and callable(core.kbb.list_boards) and callable(core.redact_for_egress))


class MissingInternalsTest(unittest.TestCase):
    def setUp(self):
        real = builtins.__import__

        def blocked(name, globals=None, locals=None, fromlist=(), level=0):
            wanted = {name, *(f"{name}.{f}" for f in fromlist or ())}
            if level == 0 and wanted & GONE:
                raise ImportError(f"No module named {sorted(wanted & GONE)[0]!r}")
            return real(name, globals, locals, fromlist, level)

        self.hide = mock.patch("builtins.__import__", blocked)
        self.hide.start()
        self.addCleanup(self.hide.stop)

    def test_a_missing_module_is_reported_not_raised(self):
        core = load()
        self.assertIn("kanban_db_boards", core.ERROR)
        self.assertIsNone(core.kb)

    def test_the_plugin_loads_and_registers_nothing(self):
        plugin = test_calling_mom.plugin
        ctx = NS(hooks=[], register_hook=lambda n, f: ctx.hooks.append(n))
        with mock.patch.object(plugin._core, "ERROR", "ImportError: gone"), self.assertLogs(plugin.logger, "WARNING") as logs:
            plugin.register(ctx)
        self.assertEqual(ctx.hooks, [])
        self.assertIn("gone", logs.output[0])
        self.assertIn("hermes calling-mom doctor", logs.output[0])

    def test_helpers_degrade_to_safe_values(self):
        core = load()
        self.assertEqual(core.load_config(), {})
        self.assertFalse(core.profile_exists("anyone"))
        self.assertEqual(core.list_providers({}), [])
        self.assertEqual(core.list_board_slugs(), [])
        self.assertFalse(core.has_menu())


class BoardReadTest(unittest.TestCase):
    """The reads of the sweep (who else touched a board) live in _core.py, with a doctor check."""

    def setUp(self):
        self.core = load()
        self.tid = test_calling_mom.with_conn(lambda c: test_calling_mom.kb.create_task(c, title="read me", assignee="w"))
        test_calling_mom.with_conn(lambda c: test_calling_mom.kb.add_comment(c, self.tid, "default", "hello"))

    def test_board_activity_lists_every_card_with_its_comments_and_events(self):
        cards = test_calling_mom.with_conn(self.core.board_activity)
        task, comments, events = next(c for c in cards if c[0].id == self.tid)
        self.assertEqual(([c.author for c in comments], "created" in [e.kind for e in events]), (["default"], True))

    def test_the_last_event_of_a_kind_and_none_when_there_is_none(self):
        found = test_calling_mom.with_conn(lambda c: self.core.last_event_id(c, self.tid, "created"))
        self.assertIsInstance(found, int)
        self.assertIsNone(test_calling_mom.with_conn(lambda c: self.core.last_event_id(c, self.tid, "no_such_kind")))

    def test_the_doctor_check_reads_the_first_board_and_says_nothing_when_it_works(self):
        with mock.patch.object(self.core, "list_board_slugs", return_value=["default"]):
            self.assertIsNone(self.core.check_board_read())

    def test_the_doctor_check_without_a_board_has_nothing_to_read(self):
        with mock.patch.object(self.core, "list_board_slugs", return_value=[]):
            self.assertIsNone(self.core.check_board_read())

    def test_the_doctor_check_names_the_error_when_the_read_breaks(self):
        with mock.patch.object(self.core, "list_board_slugs", return_value=["default"]), \
                mock.patch.object(self.core, "board_activity", side_effect=AttributeError("list_events is gone")):
            self.assertEqual(self.core.check_board_read(), "AttributeError: list_events is gone")


class QuotaReadTest(unittest.TestCase):
    """WP2 reads: the cards that run (stalled check), the links between cards (pace), the profile a process works for."""

    def setUp(self):
        self.core = load()
        kb = test_calling_mom.kb
        self.a = test_calling_mom.with_conn(lambda c: kb.create_task(c, title="parent", assignee="w"))
        self.b = test_calling_mom.with_conn(lambda c: kb.create_task(c, title="child", assignee="w", parents=[self.a]))

    def test_running_cards_are_only_the_running_ones(self):
        def run(conn):
            with conn:
                conn.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (self.a,))
            return [t.id for t in self.core.running_cards(conn)]
        self.assertIn(self.a, test_calling_mom.with_conn(run))
        self.assertNotIn(self.b, test_calling_mom.with_conn(run))

    def test_board_links_are_parent_child_pairs(self):
        tasks, links = test_calling_mom.with_conn(self.core.board_cards)
        self.assertIn((self.a, self.b), links)
        self.assertEqual({self.a, self.b} <= {t.id for t in tasks}, True)

    def test_link_and_unlink_report_what_happened(self):
        c = test_calling_mom.with_conn(lambda c: test_calling_mom.kb.create_task(c, title="third", assignee="w"))
        test_calling_mom.with_conn(lambda conn: self.core.link(conn, self.b, c))
        self.assertTrue(test_calling_mom.with_conn(lambda conn: self.core.unlink(conn, self.b, c)))
        self.assertFalse(test_calling_mom.with_conn(lambda conn: self.core.unlink(conn, self.b, c)))
        with self.assertRaises(ValueError):   # a link that would close a cycle is refused by Hermes
            test_calling_mom.with_conn(lambda conn: self.core.link(conn, self.b, self.a))

    def test_the_doctor_check_covers_the_new_reads(self):
        with mock.patch.object(self.core, "list_board_slugs", return_value=["default"]), \
                mock.patch.object(self.core, "running_cards", side_effect=AttributeError("last_heartbeat_at is gone")):
            self.assertEqual(self.core.check_board_read(), "AttributeError: last_heartbeat_at is gone")

    def test_the_profile_name_is_the_one_hermes_names(self):
        with mock.patch("hermes_cli.profiles.current_profile_name", return_value="daidalos"):
            self.assertEqual(load().profile_name(), "daidalos")
        with mock.patch("hermes_cli.profiles.current_profile_name", return_value=None):
            self.assertEqual(load().profile_name(), "default")

    def test_without_the_profile_module_the_profile_is_default(self):
        with mock.patch("hermes_cli.profiles.current_profile_name", side_effect=RuntimeError("gone")):
            self.assertEqual(load().profile_name(), "default")


class HookBudgetTest(unittest.TestCase):
    """Hermes bounds hot-path hooks by plugins.hook_callback_timeout; the plugin sizes all its waits from it."""

    def budget(self, plugins):
        cfg = {} if plugins is None else {"plugins": plugins}
        with mock.patch("hermes_cli.config.load_config", return_value=cfg):
            return load().hook_budget()

    def test_default_is_hermes_default(self):
        self.assertEqual(self.budget(None), 30)
        self.assertEqual(self.budget({}), 30)
        self.assertEqual(self.budget({"hook_callback_timeout": None}), 30)

    def test_configured_value_is_used(self):
        self.assertEqual(self.budget({"hook_callback_timeout": 12}), 12)
        self.assertEqual(self.budget({"hook_callback_timeout": "7.5"}), 7.5)

    def test_value_is_clamped_to_1_and_600(self):
        self.assertEqual(self.budget({"hook_callback_timeout": 0}), 1)
        self.assertEqual(self.budget({"hook_callback_timeout": 0.2}), 1)
        self.assertEqual(self.budget({"hook_callback_timeout": 5000}), 600)

    def test_junk_and_negative_values_fall_back_like_hermes_does(self):
        for junk in ("soon", [5], -3):
            with self.subTest(junk=junk):
                self.assertEqual(self.budget({"hook_callback_timeout": junk}), 30)
        self.assertEqual(self.budget("not a dict"), 30)


class HelpersTest(unittest.TestCase):
    def test_helpers_work_against_this_hermes(self):
        core = load()
        self.assertIsInstance(core.load_config(), dict)
        self.assertIsInstance(core.profile_exists("no-such-profile-xyz"), bool)
        self.assertIsInstance(core.list_providers({}), list)
        self.assertIsInstance(core.list_board_slugs(), list)
        self.assertTrue(core.hermes_version())

    def test_menu_is_available_and_callable(self):
        core = load()
        self.assertTrue(core.has_menu())
        with mock.patch("hermes_cli.curses_ui.curses_radiolist", return_value=2) as radio:
            self.assertEqual(core.menu("Mom", ["a", "b", "c"], 1), 2)
        radio.assert_called_once_with("Mom", ["a", "b", "c"], 1, searchable=True, search_labels=None, cancel_returns=-1)

    def test_esc_and_ctrl_c_come_back_as_none_not_as_the_default(self):
        core = load()
        with mock.patch("hermes_cli.curses_ui.curses_radiolist", return_value=-1):
            self.assertIsNone(core.menu("Mom", ["a", "b"], 1))


if __name__ == "__main__":
    unittest.main()
