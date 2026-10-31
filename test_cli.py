"""Tests for the user-facing commands: setup (interactive), status, doctor."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("calling_mom_cli_under_test", PLUGIN_DIR / "cli.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

SUB = "claude-subscription-directsdk-experimental"
LISTING = [
    {"slug": "openrouter", "name": "OpenRouter", "models": ["deepseek/deepseek-v4.1-flash-1017", "x/y"]},
    {"slug": SUB, "name": "Claude subscription", "models": ["claude-sonnet-5-5", "claude-opus-5-5"]},
]


class Script:
    """Fake terminal: answers from a list, records everything said."""

    def __init__(self, *answers):
        self.answers, self.said = list(answers), []

    def ask(self, prompt=""):
        self.said.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def say(self, text=""):
        self.said.append(text)

    @property
    def text(self):
        return "\n".join(self.said)


class PickTest(unittest.TestCase):
    OPTS = [("A", "a"), ("B", "b"), ("C", "c")]

    def pick(self, *answers, default=0):
        s = Script(*answers)
        return cli.pick("Pick", self.OPTS, s.ask, s.say, default=default), s

    def test_number_selects_the_option(self):
        self.assertEqual(self.pick("2")[0], "b")

    def test_empty_answer_takes_the_default(self):
        self.assertEqual(self.pick("", default=2)[0], "c")

    def test_o_then_text_is_a_custom_value(self):
        self.assertEqual(self.pick("o", "my/model")[0], "my/model")

    def test_invalid_answer_asks_again(self):
        value, s = self.pick("9", "x", "3")
        self.assertEqual(value, "c")
        self.assertIn("Please", s.text)

    def test_three_invalid_answers_fall_back_to_the_default(self):
        self.assertEqual(self.pick("9", "9", "9", default=1)[0], "b")

    def test_default_none_returns_none_on_enter(self):
        s = Script("")
        self.assertIsNone(cli.pick("Pick", self.OPTS, s.ask, s.say, default="none", none_label="skip"))

    def test_none_option_is_offered_when_allowed(self):
        s = Script("4")
        self.assertIsNone(cli.pick("Pick", self.OPTS, s.ask, s.say, none_label="skip"))
        self.assertIn("skip", s.text)


ROUTES_CFG = {"delegation": {"backends": {"model_map": {"options": [
    {"name": "fast", "description": "Cheap and quick", "provider": "openrouter", "model": "x/y"},
    {"name": "deep", "description": "Careful", "provider": SUB, "model": "claude-opus-5-5"},
    {"name": "local", "description": "On this machine", "base_url": "http://localhost:8080/v1", "model": "qwen"},
    "junk", {"name": "", "provider": "p", "model": "m"},
]}}}}


class RoutesTest(unittest.TestCase):
    def test_routes_with_provider_and_model_are_usable(self):
        usable, unusable = cli.routes_from(ROUTES_CFG)
        self.assertEqual([r["name"] for r in usable], ["fast", "deep"])
        self.assertEqual(unusable, ["local"])

    def test_no_delegation_block_means_no_routes(self):
        for cfg in ({}, {"delegation": "x"}, {"delegation": {"backends": {"model_map": {"options": "x"}}}}):
            self.assertEqual(cli.routes_from(cfg), ([], []))


class ChooseTest(unittest.TestCase):
    """A menu callback (arrow keys, / filter) replaces the numbered list; None = cancelled."""
    OPTS = [("A", "a"), ("B", "b"), ("C", "c")]

    def test_menu_index_selects_the_option(self):
        s = Script()
        seen = []
        value = cli.pick("Pick", self.OPTS, s.ask, s.say, other=False, choose=lambda t, labels, d: seen.append((t, labels, d)) or 1)
        self.assertEqual(value, "b")
        self.assertEqual(seen, [("Pick", ["A", "B", "C"], 0)])
        self.assertEqual(s.said, [])                       # nothing printed or asked: the menu owns the screen

    def test_none_and_other_entries_are_menu_rows(self):
        s = Script("typed")
        labels = []
        pick = lambda idx: cli.pick("P", self.OPTS, s.ask, s.say, none_label="skip", choose=lambda t, l, d: labels.extend(l) or idx)
        self.assertIsNone(pick(3))
        self.assertEqual(labels, ["A", "B", "C", "skip", "other (type it)"])
        self.assertEqual(pick(4), "typed")

    def test_search_text_is_passed_on_with_the_extra_rows(self):
        seen = []
        cli.pick("P", self.OPTS, Script().ask, Script().say, none_label="skip", choose=lambda t, l, d, search=None: seen.append(search) or 0,
                 search=["a1", "b1", "c1"])
        self.assertEqual(seen, [["a1", "b1", "c1", "skip", "other (type it)"]])

    def test_route_menu_filters_by_route_name_only(self):
        routes = cli.routes_from(ROUTES_CFG)[0]
        seen = {}
        def choose(title, labels, default, search=None):
            seen[title] = search
            return 0
        cli.setup({}, LISTING, [], Script().ask, Script().say, choose=choose, routes=routes)
        self.assertEqual(seen["Mom: which route?"], ["fast", "deep"])

    def test_cancelled_menu_takes_the_default(self):
        s = Script()
        self.assertEqual(cli.pick("P", self.OPTS, s.ask, s.say, default=2, choose=lambda t, l, d: None), "c")
        self.assertIsNone(cli.pick("P", self.OPTS, s.ask, s.say, default="none", none_label="skip", choose=lambda t, l, d: None))


class FlagsTest(unittest.TestCase):
    ROUTES = cli.routes_from(ROUTES_CFG)[0]

    def test_provider_slash_model_splits_at_the_first_slash(self):
        self.assertEqual(cli.parse_pair("openrouter/deepseek/deepseek-v4.1-flash", self.ROUTES), ("openrouter", "deepseek/deepseek-v4.1-flash"))

    def test_route_name_takes_the_values_of_the_route(self):
        self.assertEqual(cli.parse_pair("route:deep", self.ROUTES), (SUB, "claude-opus-5-5"))

    def test_bad_values_raise_with_a_clear_message(self):
        for bad, text in (("nonsense", "provider/model"), ("/m", "provider/model"), ("p/", "provider/model"),
                          ("route:nope", "fast, deep")):
            with self.assertRaises(cli.SetupError, msg=bad) as cm:
                cli.parse_pair(bad, self.ROUTES)
            self.assertIn(text, str(cm.exception))

    def test_olymp_none_switches_it_off_and_absent_keeps_the_current_one(self):
        cfg = {"calling_mom": {"olymp": {"provider": SUB, "model": "claude-opus-5-5"}}}
        self.assertIsNone(cli.resolve("a/b", "none", cfg, self.ROUTES)[1])
        self.assertEqual(cli.resolve("a/b", None, cfg, self.ROUTES)[1], (SUB, "claude-opus-5-5"))
        self.assertIsNone(cli.resolve("a/b", None, {}, self.ROUTES)[1])

    def test_mom_cannot_be_none(self):
        with self.assertRaises(cli.SetupError):
            cli.resolve("none", None, {}, self.ROUTES)

    def test_boards_flag(self):
        self.assertEqual(cli.parse_boards("a, b"), ["a", "b"])
        self.assertEqual(cli.parse_boards("*"), ["*"])


class UpdatesTest(unittest.TestCase):
    def test_mom_and_olymp_pairs_and_allowlists(self):
        upd = cli.updates((SUB, "claude-sonnet-5-5"), (SUB, "claude-opus-5-5"), ["coding"], {})
        self.assertEqual(upd["calling_mom.mom.model"], "claude-sonnet-5-5")
        self.assertEqual(upd["calling_mom.mom.provider"], SUB)
        self.assertEqual(upd["calling_mom.olymp.model"], "claude-opus-5-5")
        self.assertEqual(upd["calling_mom.olymp.provider"], SUB)
        self.assertEqual(upd["calling_mom.advisor.enabled"], True)
        self.assertEqual(upd["calling_mom.advisor.boards"], ["coding"])
        llm = "plugins.entries.calling-mom.llm."
        self.assertEqual(upd[llm + "allowed_models"], ["claude-sonnet-5-5", "claude-opus-5-5"])
        self.assertEqual(upd[llm + "allowed_providers"], [SUB])
        self.assertIs(upd[llm + "allow_model_override"], True)
        self.assertIs(upd[llm + "allow_provider_override"], True)

    def test_existing_allowlist_entries_are_kept_without_duplicates(self):
        existing = {"plugins": {"entries": {"calling-mom": {"llm": {
            "allowed_models": ["old/model", "claude-sonnet-5-5"], "allowed_providers": ["openrouter"]}}}},
            "calling_mom": {"advisor": {"boards": ["default"]}}}
        upd = cli.updates((SUB, "claude-sonnet-5-5"), None, ["coding", "default"], existing)
        llm = "plugins.entries.calling-mom.llm."
        self.assertEqual(upd[llm + "allowed_models"], ["old/model", "claude-sonnet-5-5"])
        self.assertEqual(upd[llm + "allowed_providers"], ["openrouter", SUB])
        self.assertEqual(upd["calling_mom.advisor.boards"], ["default", "coding"])

    def test_no_olymp_leaves_olymp_off(self):
        upd = cli.updates((SUB, "claude-sonnet-5-5"), None, [], {})
        self.assertEqual(upd["calling_mom.olymp.model"], "")
        self.assertEqual(upd["calling_mom.olymp.provider"], "")
        self.assertNotIn("claude-opus-5-5", upd["plugins.entries.calling-mom.llm.allowed_models"])

    def test_setup_removes_the_pre_release_sibling_block(self):
        cfg = {"calling_mom": {"sibling": {"model": "a", "provider": "b"}, "mom": {"model": "old-expensive", "provider": "p"}}}
        out = cli.apply_updates(cfg, cli.updates((SUB, "claude-sonnet-5-5"), None, [], cfg))
        self.assertNotIn("sibling", out["calling_mom"])
        self.assertEqual(out["calling_mom"]["mom"]["model"], "claude-sonnet-5-5")   # the old expensive entry is replaced
        self.assertEqual(out["calling_mom"]["olymp"]["model"], "")

    def test_apply_creates_nested_keys_and_keeps_the_rest(self):
        cfg = {"keep": 1, "calling_mom": {"marker": "[crit]"}}
        out = cli.apply_updates(cfg, {"calling_mom.olymp.model": "m", "plugins.entries.calling-mom.llm.x": True})
        self.assertEqual(out["calling_mom"], {"marker": "[crit]", "olymp": {"model": "m"}})
        self.assertIs(out["plugins"]["entries"]["calling-mom"]["llm"]["x"], True)
        self.assertEqual(out["keep"], 1)
        self.assertNotIn("olymp", cfg["calling_mom"])  # input untouched


class SetupFlowTest(unittest.TestCase):
    def run_setup(self, *answers, preset=None, boards=("default", "coding"), cfg=None):
        s = Script(*answers)
        result = cli.setup(cfg or {}, LISTING, list(boards), s.ask, s.say, preset=preset)
        return result, s

    def test_interactive_pick_of_provider_and_model_for_both_tiers(self):
        # mom: provider 2 (subscription), model 1 (sonnet); olymp: provider 2, model 2 (opus); board 2 (coding)
        (upd, _), s = self.run_setup("2", "1", "2", "2", "2")
        self.assertEqual((upd["calling_mom.mom.provider"], upd["calling_mom.mom.model"]), (SUB, "claude-sonnet-5-5"))
        self.assertEqual((upd["calling_mom.olymp.provider"], upd["calling_mom.olymp.model"]), (SUB, "claude-opus-5-5"))
        self.assertEqual(upd["calling_mom.advisor.boards"], ["coding"])
        self.assertIn("OpenRouter", s.text)           # providers are listed by name
        self.assertIn("claude-opus-5-5", s.text)      # models are listed

    def test_enter_everywhere_keeps_olymp_off(self):
        (upd, _), _ = self.run_setup("", "", "", "")
        self.assertEqual(upd["calling_mom.olymp.model"], "")
        self.assertNotEqual(upd["calling_mom.mom.model"], "")

    def test_current_setup_is_preselected(self):
        cfg = {"calling_mom": {"mom": {"provider": SUB, "model": "claude-opus-5-5"},
                               "olymp": {"provider": SUB, "model": "claude-sonnet-5-5"}}}
        (upd, _), s = self.run_setup("", "", "", "", "", cfg=cfg)
        self.assertEqual((upd["calling_mom.mom.provider"], upd["calling_mom.mom.model"]), (SUB, "claude-opus-5-5"))
        self.assertEqual((upd["calling_mom.olymp.provider"], upd["calling_mom.olymp.model"]), (SUB, "claude-sonnet-5-5"))
        self.assertIn("[2] >", s.text)

    def test_olymp_can_be_skipped(self):
        # mom 1/1, olymp: choose the "no olymp" entry (3 = after two providers), board: all
        (upd, _), _ = self.run_setup("1", "1", "3", "3")
        self.assertEqual(upd["calling_mom.olymp.model"], "")
        self.assertEqual(upd["calling_mom.advisor.boards"], ["*"])

    def test_preset_needs_no_questions_but_one_board(self):
        (upd, _), s = self.run_setup("1", preset="subscription")
        self.assertEqual(upd["calling_mom.olymp.model"], "claude-opus-5-5")
        self.assertEqual(upd["calling_mom.mom.model"], "claude-sonnet-5-5")
        self.assertEqual(upd["calling_mom.advisor.boards"], ["default"])

    def test_unknown_preset_is_an_error(self):
        with self.assertRaises(ValueError):
            self.run_setup(preset="nope")

    def test_provider_without_a_model_list_asks_for_the_model(self):
        s = Script("1", "typed/model", "1", "1")
        upd, _ = cli.setup({}, [{"slug": "custom", "name": "Custom", "models": []}], [], s.ask, s.say)
        self.assertEqual((upd["calling_mom.mom.provider"], upd["calling_mom.mom.model"]), ("custom", "typed/model"))

    def test_without_a_provider_list_the_user_types_the_pair(self):
        s = Script("my-provider", "my/model", "", "", "")
        upd, _ = cli.setup({}, [], [], s.ask, s.say)
        self.assertEqual((upd["calling_mom.mom.provider"], upd["calling_mom.mom.model"]), ("my-provider", "my/model"))


class SetupMenuTest(unittest.TestCase):
    def test_route_source_is_offered_only_when_routes_exist(self):
        s = Script("1", "1", "3", "3")                      # no routes: first question is the provider
        cli.setup({}, LISTING, [], s.ask, s.say)
        self.assertNotIn("delegation route", s.text)

    def test_mom_can_come_from_a_delegation_route(self):
        routes = cli.routes_from(ROUTES_CFG)[0]
        # mom: source 1 (route), route 1 (fast); olymp: source 1 (route), route 2 (deep); boards: all
        s = Script("1", "1", "1", "2", "2")
        upd, _ = cli.setup({}, LISTING, ["default"], s.ask, s.say, routes=routes)
        self.assertEqual((upd["calling_mom.mom.provider"], upd["calling_mom.mom.model"]), ("openrouter", "x/y"))
        self.assertEqual((upd["calling_mom.olymp.provider"], upd["calling_mom.olymp.model"]), (SUB, "claude-opus-5-5"))
        self.assertIn("Cheap and quick", s.text)

    def test_choosing_by_hand_still_works_next_to_routes(self):
        routes = cli.routes_from(ROUTES_CFG)[0]
        s = Script("2", "2", "1", "3", "3", "3")             # mom: by hand, provider 2, model 1; olymp: none; boards: all
        upd, _ = cli.setup({}, LISTING, [], s.ask, s.say, routes=routes)
        self.assertEqual((upd["calling_mom.mom.provider"], upd["calling_mom.mom.model"]), (SUB, "claude-sonnet-5-5"))

    def test_menu_lists_every_model_not_just_the_first_25(self):
        many = [{"slug": "p", "name": "P", "models": [f"m{i}" for i in range(40)]}]
        shown = []
        def choose(title, labels, default):
            shown.append(labels)
            return 0
        cli.setup({}, many, [], Script().ask, Script().say, choose=choose)
        self.assertTrue(any(len(labels) > 25 for labels in shown))

    def test_menu_selection_drives_setup_without_typing(self):
        picks = iter([1, 1, 2])                              # mom: provider 2, model 2; olymp: row 3 = "no olymp"
        s = Script()
        upd, _ = cli.setup({}, LISTING, [], s.ask, s.say, choose=lambda t, l, d: next(picks, 0))
        self.assertEqual((upd["calling_mom.mom.provider"], upd["calling_mom.mom.model"]), (SUB, "claude-opus-5-5"))
        self.assertEqual(s.said.count("Please enter one of the numbers."), 0)


class AutomaticSetupTest(unittest.TestCase):
    def env(self, saved, cfg=None, interactive=True, sweeps=(), created=None, routes=None):
        s = Script()
        e = cli.Env(load=lambda: cfg or {}, save=lambda c: saved.update(c), providers=lambda: LISTING, boards=lambda: ["default", "coding"],
                    spent=lambda: {}, probe=lambda p, m: None, version="0.22.5", internals=True, ask=s.ask, say=s.say,
                    cron=lambda: (created if created is not None else []).append(1), sweeps=lambda: list(sweeps),
                    routes=lambda: cli.routes_from(cfg or {})[0] if routes is None else routes, interactive=interactive)
        return e, s

    def test_flags_set_everything_without_a_single_question(self):
        saved, created = {}, []
        e, s = self.env(saved, created=created, interactive=False)
        rc = cli.cmd_setup(e, mom="openrouter/x/y", olymp="none", boards="default", yes=True, cron=True)
        self.assertEqual(rc, 0)
        cm = saved["calling_mom"]
        self.assertEqual((cm["mom"]["provider"], cm["mom"]["model"]), ("openrouter", "x/y"))
        self.assertEqual(cm["advisor"]["boards"], ["default"])
        self.assertEqual(created, [1])
        self.assertNotIn(">", "".join(x for x in s.said if x.endswith("> ")))   # no prompt was shown

    def test_route_flag_reads_the_delegation_routes(self):
        saved = {}
        e, _ = self.env(saved, cfg=dict(ROUTES_CFG), interactive=False)
        self.assertEqual(cli.cmd_setup(e, mom="route:fast", olymp="route:deep", boards="*", yes=True, cron=False), 0)
        self.assertEqual(saved["calling_mom"]["olymp"]["model"], "claude-opus-5-5")

    def test_no_cron_flag_skips_the_job_and_cron_default_follows_yes(self):
        for cron, expected in ((False, []), (None, [1])):
            created = []
            e, _ = self.env({}, created=created, interactive=False)
            cli.cmd_setup(e, mom="a/b", boards="*", yes=True, cron=cron)
            self.assertEqual(created, expected)

    def test_without_boards_flag_existing_boards_stay_and_a_fresh_setup_allows_all(self):
        saved = {}
        e, _ = self.env(saved, cfg={"calling_mom": {"advisor": {"boards": ["coding"]}}}, interactive=False)
        cli.cmd_setup(e, mom="a/b", yes=True, cron=False)
        self.assertEqual(saved["calling_mom"]["advisor"]["boards"], ["coding"])
        saved.clear()
        e, _ = self.env(saved, interactive=False)
        cli.cmd_setup(e, mom="a/b", yes=True, cron=False)
        self.assertEqual(saved["calling_mom"]["advisor"]["boards"], ["*"])

    def test_bad_flag_value_exits_with_2_and_writes_nothing(self):
        saved = {}
        e, s = self.env(saved, interactive=False)
        self.assertEqual(cli.cmd_setup(e, mom="nonsense", yes=True), 2)
        self.assertEqual(saved, {})
        self.assertIn("provider/model", s.text)

    def test_without_a_terminal_and_without_flags_it_stops_instead_of_waiting(self):
        saved = {}
        e, s = self.env(saved, interactive=False)
        self.assertEqual(cli.cmd_setup(e), 2)
        self.assertIn("--mom", s.text)
        self.assertEqual(saved, {})

    def test_flags_are_the_consent_so_no_yes_is_needed_without_a_terminal(self):
        saved, created = {}, []
        e, s = self.env(saved, interactive=False, created=created)
        self.assertEqual(cli.cmd_setup(e, mom="a/b", boards="*"), 0)
        self.assertEqual(saved["calling_mom"]["mom"]["model"], "b")
        self.assertEqual(created, [1])          # no terminal: nobody to ask, so the cron default applies

    def test_olymp_or_boards_without_mom_are_refused(self):
        for kw in ({"olymp": "a/b"}, {"boards": "*"}):
            e, s = self.env({}, interactive=False)
            self.assertEqual(cli.cmd_setup(e, yes=True, **kw), 2)
            self.assertIn("need --mom", s.text)

    def test_unknown_preset_exits_with_2(self):
        e, s = self.env({}, interactive=False)
        self.assertEqual(cli.cmd_setup(e, preset="nope", yes=True), 2)
        self.assertIn("unknown preset", s.text)

    def test_preset_without_a_terminal_works_with_boards_flag(self):
        saved = {}
        e, _ = self.env(saved, interactive=False)
        self.assertEqual(cli.cmd_setup(e, preset="subscription", boards="*", yes=True, cron=False), 0)
        self.assertEqual(saved["calling_mom"]["olymp"]["model"], "claude-opus-5-5")


class StatusTest(unittest.TestCase):
    CFG = {
        "mom": {"model": "claude-sonnet-5-5", "provider": SUB},
        "model": "deepseek/x", "provider": "openrouter",
        "olymp": {"model": "claude-opus-5-5", "provider": SUB, "max_per_card": 1, "max_per_day": 6},
        "advisor": {"enabled": True, "boards": ["coding"], "daily_usd_cap": 2.0, "daily_token_cap": 200000},
    }

    def test_mom_block_beats_the_legacy_top_level_pair(self):
        text = "\n".join(cli.status_lines(self.CFG, {"usd": 0.1, "tokens": 5, "olymp": 2}))
        self.assertIn("claude-sonnet-5-5", text)
        self.assertNotIn("deepseek/x", text)
        self.assertIn("claude-opus-5-5", text)
        self.assertIn("2 / 6", text)                  # olymp calls today / limit
        self.assertIn("coding", text)

    def test_warns_when_the_advisor_is_on_without_a_board(self):
        cfg = {**self.CFG, "advisor": {"enabled": True, "boards": [], "daily_usd_cap": 2.0, "daily_token_cap": 1}}
        self.assertIn("no board", "\n".join(cli.status_lines(cfg, {"usd": 0, "tokens": 0, "olymp": 0})))

    def test_advisor_errors_today_are_shown_with_the_last_cause(self):
        text = "\n".join(cli.status_lines(self.CFG, {"usd": 0, "tokens": 0, "olymp": 0}, (7, "ClaudeCodeLoggedOut")))
        self.assertIn("7", text)
        self.assertIn("ClaudeCodeLoggedOut", text)
        self.assertIn("doctor", text)

    def test_no_error_line_without_errors(self):
        text = "\n".join(cli.status_lines(self.CFG, {"usd": 0, "tokens": 0, "olymp": 0}, (0, None)))
        self.assertNotIn("errors", text)

    def test_leftover_sibling_block_is_flagged_in_status(self):
        cfg = {**self.CFG, "sibling": {"model": "a", "provider": "b"}}
        self.assertIn("renamed", "\n".join(cli.status_lines(cfg, {"usd": 0, "tokens": 0, "olymp": 0})))

    def test_olymp_off_is_said_plainly(self):
        cfg = {**self.CFG, "olymp": {"model": "", "provider": "", "max_per_card": 1, "max_per_day": 6}}
        self.assertIn("off", "\n".join(cli.status_lines(cfg, {"usd": 0, "tokens": 0, "olymp": 0})).lower())


class DoctorTest(unittest.TestCase):
    LLM = {"allow_model_override": True, "allow_provider_override": True,
           "allowed_models": ["claude-sonnet-5-5", "claude-opus-5-5"], "allowed_providers": [SUB]}

    def cfg(self, **over):
        base = {"mom": {"model": "claude-sonnet-5-5", "provider": SUB},
                "olymp": {"model": "claude-opus-5-5", "provider": SUB},
                "advisor": {"enabled": True, "boards": ["coding"]}}
        return {**base, **over}

    ONE = [("/h", "j1", "scheduled")]

    def run_doctor(self, cfg=None, llm=None, probe=None, version="0.22.5", internals=True, sweeps=None):
        probe = probe or (lambda provider, model: None)
        return cli.run_checks(cfg or self.cfg(), self.LLM if llm is None else llm, probe, version, internals,
                              self.ONE if sweeps is None else sweeps)

    def by_name(self, checks):
        return {n: (s, d) for n, s, d in checks}

    def test_everything_fine(self):
        checks = self.run_doctor()
        self.assertTrue(all(s == "ok" for _, s, _ in checks), checks)

    def test_old_hermes_fails_with_the_minimum_named(self):
        s, d = self.by_name(self.run_doctor(version="0.10.0"))["hermes version"]
        self.assertEqual(s, "fail")
        self.assertIn("0.22", d)

    def test_missing_internals_fail(self):
        self.assertEqual(self.by_name(self.run_doctor(internals=False))["hermes internals"][0], "fail")

    def test_half_pair_fails(self):
        cfg = self.cfg(mom={"model": "claude-sonnet-5-5", "provider": ""})
        self.assertEqual(self.by_name(self.run_doctor(cfg))["mom pair"][0], "fail")

    def test_missing_allowlist_entry_names_the_fix(self):
        llm = {**self.LLM, "allowed_models": ["claude-sonnet-5-5"]}
        s, d = self.by_name(self.run_doctor(llm=llm))["allowlist"]
        self.assertEqual(s, "fail")
        self.assertIn("claude-opus-5-5", d)
        self.assertIn("setup", d)

    def test_override_flags_must_be_on(self):
        llm = {**self.LLM, "allow_model_override": False}
        self.assertEqual(self.by_name(self.run_doctor(llm=llm))["allowlist"][0], "fail")

    def test_probe_error_is_reported_per_tier(self):
        def probe(provider, model):
            if model == "claude-opus-5-5":
                raise RuntimeError("Key limit exceeded")
        checks = self.by_name(self.run_doctor(probe=probe))
        self.assertEqual(checks["mom call"][0], "ok")
        self.assertEqual(checks["olymp call"][0], "fail")
        self.assertIn("Key limit exceeded", checks["olymp call"][1])

    def test_probe_is_skipped_when_the_config_is_already_broken(self):
        calls = []
        llm = {**self.LLM, "allowed_models": []}
        self.run_doctor(llm=llm, probe=lambda p, m: calls.append(m))
        self.assertEqual(calls, [])

    def test_olymp_off_is_fine(self):
        cfg = self.cfg(olymp={"model": "", "provider": ""})
        checks = self.by_name(self.run_doctor(cfg))
        self.assertEqual(checks["olymp pair"][0], "ok")
        self.assertNotIn("olymp call", checks)

    def test_advisor_off_or_without_board_is_a_warning(self):
        cfg = self.cfg(advisor={"enabled": True, "boards": []})
        self.assertEqual(self.by_name(self.run_doctor(cfg))["advisor"][0], "warn")
        cfg = self.cfg(advisor={"enabled": False, "boards": ["coding"]})
        self.assertEqual(self.by_name(self.run_doctor(cfg))["advisor"][0], "warn")

    def test_one_active_sweep_job_is_fine(self):
        self.assertEqual(self.by_name(self.run_doctor())["sweep cron"][0], "ok")

    def test_no_sweep_job_is_a_warning_with_the_fix(self):
        s, d = self.by_name(self.run_doctor(sweeps=[]))["sweep cron"]
        self.assertEqual(s, "warn")
        self.assertIn("setup", d)

    def test_two_active_sweep_jobs_are_a_warning_naming_both_and_the_fix(self):
        sweeps = [("/root", "aaa", "scheduled"), ("/root/profiles/p", "bbb", "scheduled")]
        s, d = self.by_name(self.run_doctor(sweeps=sweeps))["sweep cron"]
        self.assertEqual(s, "warn")
        self.assertIn("aaa", d)
        self.assertIn("bbb", d)
        self.assertIn("cron remove", d)

    def test_a_paused_duplicate_does_not_count_as_active(self):
        sweeps = [("/root", "aaa", "scheduled"), ("/root/profiles/p", "bbb", "paused")]
        s, d = self.by_name(self.run_doctor(sweeps=sweeps))["sweep cron"]
        self.assertEqual(s, "ok")
        self.assertIn("bbb", d)

    def test_only_a_paused_job_is_a_warning(self):
        self.assertEqual(self.by_name(self.run_doctor(sweeps=[("/h", "p", "paused")]))["sweep cron"][0], "warn")

    def test_leftover_sibling_block_is_a_warning_that_explains_the_rename(self):
        cfg = self.cfg(sibling={"model": "a", "provider": "b"})
        s, d = self.by_name(self.run_doctor(cfg))["old names"]
        self.assertEqual(s, "warn")
        self.assertIn("renamed", d)
        self.assertIn("setup", d)

    def test_no_old_names_no_extra_check(self):
        self.assertNotIn("old names", self.by_name(self.run_doctor()))

    def test_exit_code_counts_failures_only(self):
        self.assertEqual(cli.exit_code([("a", "ok", ""), ("b", "warn", "")]), 0)
        self.assertEqual(cli.exit_code([("a", "ok", ""), ("b", "fail", "")]), 1)

    def test_version_compare_is_numeric(self):
        self.assertTrue(cli.version_ok("0.22.5"))
        self.assertTrue(cli.version_ok("0.100.0"))
        self.assertFalse(cli.version_ok("0.9.9"))
        self.assertFalse(cli.version_ok("garbage"))


class CountErrorsTest(unittest.TestCase):
    def test_counts_only_todays_advisor_errors_and_names_the_last(self):
        import time as _t
        now = int(_t.time())
        rows = [{"ts": now - 90000, "kind": "advisor_error", "error": "Old"},
                {"ts": now, "kind": "advisor_error", "error": "A"},
                {"ts": now, "kind": "advisor", "model": "x"},
                {"ts": now, "kind": "advisor_error", "error": "B"}]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "l.jsonl"
            p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
            self.assertEqual(cli.count_errors(p, now=now), (2, "B"))

    def test_missing_log_is_zero(self):
        self.assertEqual(cli.count_errors(Path("/nonexistent/l.jsonl")), (0, None))


class FindSweepsTest(unittest.TestCase):
    def home(self, root, name, jobs):
        d = Path(root) / name / "cron"
        d.mkdir(parents=True)
        (d / "jobs.json").write_text(json.dumps({"jobs": jobs}))
        return str(Path(root) / name)

    def test_finds_sweep_jobs_in_every_home_and_ignores_the_rest(self):
        with tempfile.TemporaryDirectory() as root:
            a = self.home(root, "a", [{"id": "1", "name": cli.CRON_NAME, "state": "scheduled"},
                                      {"id": "2", "name": "other", "state": "scheduled"}])
            b = self.home(root, "b", [{"id": "3", "name": cli.CRON_NAME, "state": "paused"}])
            found = cli.find_sweeps([a, b, str(Path(root) / "missing")])
        self.assertEqual(found, [(a, "1", "scheduled"), (b, "3", "paused")])

    def test_the_job_name_starts_with_the_plugin_name_and_says_what_it_does(self):
        self.assertTrue(cli.CRON_NAME.startswith("calling-mom: "))
        self.assertIn("sweep", cli.CRON_NAME)

    def test_unreadable_job_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            d = Path(root) / "h" / "cron"
            d.mkdir(parents=True)
            (d / "jobs.json").write_text("{not json")
            self.assertEqual(cli.find_sweeps([str(Path(root) / "h")]), [])


class CommandWiringTest(unittest.TestCase):
    def test_handlers_run_with_injected_io(self):
        s = Script("1", "1", "3", "1")
        saved = {}
        env = cli.Env(load=lambda: saved, save=lambda cfg: saved.update(cfg), providers=lambda: LISTING,
                      boards=lambda: ["default"], spent=lambda: {"usd": 0, "tokens": 0, "olymp": 0},
                      probe=lambda p, m: None, version="0.22.5", internals=True,
                      ask=s.ask, say=s.say, cron=lambda: s.say("cron created"), sweeps=lambda: [])
        self.assertEqual(cli.cmd_setup(env, preset=None, yes=True), 0)
        self.assertEqual(saved["calling_mom"]["mom"]["provider"], "openrouter")
        self.assertIn("cron created", s.text)
        self.assertEqual(cli.cmd_status(env), 0)
        self.assertEqual(cli.cmd_doctor(env), 0)

    def test_status_uses_the_error_counter_of_the_env(self):
        s = Script()
        env = cli.Env(load=lambda: {}, save=lambda c: None, providers=lambda: [], boards=lambda: [],
                      spent=lambda: {}, probe=lambda p, m: None, version="0.22.5", internals=True,
                      ask=s.ask, say=s.say, cron=lambda: None, sweeps=lambda: [], errors=lambda: (3, "Boom"))
        cli.cmd_status(env)
        self.assertIn("Boom", s.text)

    def test_setup_never_creates_a_second_sweep_job(self):
        s = Script("1", "1", "3", "1")
        created = []
        env = cli.Env(load=lambda: {}, save=lambda cfg: None, providers=lambda: LISTING, boards=lambda: ["default"],
                      spent=lambda: {}, probe=lambda p, m: None, version="0.22.5", internals=True, ask=s.ask,
                      say=s.say, cron=lambda: created.append(1), sweeps=lambda: [("/root", "2d66", "scheduled")])
        self.assertEqual(cli.cmd_setup(env, preset=None, yes=True), 0)
        self.assertEqual(created, [])
        self.assertIn("already exists", s.text)
        self.assertIn("2d66", s.text)

    def test_the_cron_job_is_created_and_announced_without_a_question(self):
        s = Script("1", "1", "3", "1")
        created = []
        env = cli.Env(load=lambda: {}, save=lambda cfg: None, providers=lambda: LISTING, boards=lambda: ["default"],
                      spent=lambda: {}, probe=lambda p, m: None, version="0.22.5", internals=True, ask=s.ask, say=s.say,
                      cron=lambda: created.append(1), sweeps=lambda: [])
        self.assertEqual(cli.cmd_setup(env), 0)
        self.assertEqual(created, [1])
        self.assertNotIn("[Y/n]", s.text)
        self.assertIn("Creating the sweep cron job", s.text)
        self.assertIn("--no-cron", s.text)

    def test_no_cron_flag_says_nothing_is_created(self):
        s = Script("1", "1", "3", "1")
        created = []
        env = cli.Env(load=lambda: {}, save=lambda cfg: None, providers=lambda: LISTING, boards=lambda: ["default"],
                      spent=lambda: {}, probe=lambda p, m: None, version="0.22.5", internals=True, ask=s.ask, say=s.say,
                      cron=lambda: created.append(1), sweeps=lambda: [])
        cli.cmd_setup(env, cron=False)
        self.assertEqual(created, [])

    def test_what_you_picked_is_written_without_asking_again(self):
        s = Script("1", "1", "3", "1")
        saved = {}
        env = cli.Env(load=lambda: {}, save=lambda cfg: saved.update(cfg), providers=lambda: LISTING,
                      boards=lambda: ["default"], spent=lambda: {}, probe=lambda p, m: None, version="0.22.5",
                      internals=True, ask=s.ask, say=s.say, cron=lambda: None, sweeps=lambda: [("/h", "j1", "scheduled")])
        self.assertEqual(cli.cmd_setup(env, preset=None, yes=False), 0)
        self.assertEqual(saved["calling_mom"]["mom"]["provider"], "openrouter")
        self.assertNotIn("Write it?", s.text)

    def test_doctor_returns_one_on_a_failed_check(self):
        s = Script()
        env = cli.Env(load=lambda: {}, save=lambda c: None, providers=lambda: [], boards=lambda: [],
                      spent=lambda: {}, probe=lambda p, m: None, version="0.1.0", internals=True,
                      ask=s.ask, say=s.say, cron=lambda: None, sweeps=lambda: [("/h", "j1", "scheduled")])
        self.assertEqual(cli.cmd_doctor(env), 1)
        self.assertIn("FAIL", s.text)


if __name__ == "__main__":
    unittest.main()
