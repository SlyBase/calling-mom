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
        files += list((HERE / "scripts").glob("*.py"))
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
        radio.assert_called_once_with("Mom", ["a", "b", "c"], 1, searchable=True, search_labels=None)


if __name__ == "__main__":
    unittest.main()
