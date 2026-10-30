"""Tests for the user-facing commands: setup (interactive), status, doctor."""
import importlib.util
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


class UpdatesTest(unittest.TestCase):
    def test_sibling_and_mom_pairs_and_allowlists(self):
        upd = cli.updates((SUB, "claude-sonnet-5-5"), (SUB, "claude-opus-5-5"), ["coding"], {})
        self.assertEqual(upd["calling_mom.sibling.model"], "claude-sonnet-5-5")
        self.assertEqual(upd["calling_mom.sibling.provider"], SUB)
        self.assertEqual(upd["calling_mom.mom.model"], "claude-opus-5-5")
        self.assertEqual(upd["calling_mom.mom.provider"], SUB)
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

    def test_no_mom_leaves_mom_off(self):
        upd = cli.updates((SUB, "claude-sonnet-5-5"), None, [], {})
        self.assertEqual(upd["calling_mom.mom.model"], "")
        self.assertEqual(upd["calling_mom.mom.provider"], "")
        self.assertNotIn("claude-opus-5-5", upd["plugins.entries.calling-mom.llm.allowed_models"])

    def test_apply_creates_nested_keys_and_keeps_the_rest(self):
        cfg = {"keep": 1, "calling_mom": {"marker": "[crit]"}}
        out = cli.apply_updates(cfg, {"calling_mom.mom.model": "m", "plugins.entries.calling-mom.llm.x": True})
        self.assertEqual(out["calling_mom"], {"marker": "[crit]", "mom": {"model": "m"}})
        self.assertIs(out["plugins"]["entries"]["calling-mom"]["llm"]["x"], True)
        self.assertEqual(out["keep"], 1)
        self.assertNotIn("mom", cfg["calling_mom"])  # input untouched


class SetupFlowTest(unittest.TestCase):
    def run_setup(self, *answers, preset=None, boards=("default", "coding"), cfg=None):
        s = Script(*answers)
        result = cli.setup(cfg or {}, LISTING, list(boards), s.ask, s.say, preset=preset)
        return result, s

    def test_interactive_pick_of_provider_and_model_for_both_tiers(self):
        # sibling: provider 2 (subscription), model 1 (sonnet); mom: provider 2, model 2 (opus); board 2 (coding)
        (upd, _), s = self.run_setup("2", "1", "2", "2", "2")
        self.assertEqual((upd["calling_mom.sibling.provider"], upd["calling_mom.sibling.model"]), (SUB, "claude-sonnet-5-5"))
        self.assertEqual((upd["calling_mom.mom.provider"], upd["calling_mom.mom.model"]), (SUB, "claude-opus-5-5"))
        self.assertEqual(upd["calling_mom.advisor.boards"], ["coding"])
        self.assertIn("OpenRouter", s.text)           # providers are listed by name
        self.assertIn("claude-opus-5-5", s.text)      # models are listed

    def test_enter_everywhere_keeps_mom_off(self):
        (upd, _), _ = self.run_setup("", "", "", "")
        self.assertEqual(upd["calling_mom.mom.model"], "")
        self.assertNotEqual(upd["calling_mom.sibling.model"], "")

    def test_current_setup_is_preselected(self):
        cfg = {"calling_mom": {"sibling": {"provider": SUB, "model": "claude-opus-5-5"},
                               "mom": {"provider": SUB, "model": "claude-sonnet-5-5"}}}
        (upd, _), s = self.run_setup("", "", "", "", "", cfg=cfg)
        self.assertEqual((upd["calling_mom.sibling.provider"], upd["calling_mom.sibling.model"]), (SUB, "claude-opus-5-5"))
        self.assertEqual((upd["calling_mom.mom.provider"], upd["calling_mom.mom.model"]), (SUB, "claude-sonnet-5-5"))
        self.assertIn("[2] >", s.text)

    def test_mom_can_be_skipped(self):
        # sibling 1/1, mom: choose the "no mom" entry (3 = after two providers), board: all
        (upd, _), _ = self.run_setup("1", "1", "3", "3")
        self.assertEqual(upd["calling_mom.mom.model"], "")
        self.assertEqual(upd["calling_mom.advisor.boards"], ["*"])

    def test_preset_needs_no_questions_but_one_board(self):
        (upd, _), s = self.run_setup("1", preset="subscription")
        self.assertEqual(upd["calling_mom.mom.model"], "claude-opus-5-5")
        self.assertEqual(upd["calling_mom.sibling.model"], "claude-sonnet-5-5")
        self.assertEqual(upd["calling_mom.advisor.boards"], ["default"])

    def test_unknown_preset_is_an_error(self):
        with self.assertRaises(ValueError):
            self.run_setup(preset="nope")

    def test_provider_without_a_model_list_asks_for_the_model(self):
        s = Script("1", "typed/model", "1", "1")
        upd, _ = cli.setup({}, [{"slug": "custom", "name": "Custom", "models": []}], [], s.ask, s.say)
        self.assertEqual((upd["calling_mom.sibling.provider"], upd["calling_mom.sibling.model"]), ("custom", "typed/model"))

    def test_without_a_provider_list_the_user_types_the_pair(self):
        s = Script("my-provider", "my/model", "", "", "")
        upd, _ = cli.setup({}, [], [], s.ask, s.say)
        self.assertEqual((upd["calling_mom.sibling.provider"], upd["calling_mom.sibling.model"]), ("my-provider", "my/model"))


class StatusTest(unittest.TestCase):
    CFG = {
        "sibling": {"model": "claude-sonnet-5-5", "provider": SUB},
        "model": "deepseek/x", "provider": "openrouter",
        "mom": {"model": "claude-opus-5-5", "provider": SUB, "max_per_card": 1, "max_per_day": 6},
        "advisor": {"enabled": True, "boards": ["coding"], "daily_usd_cap": 2.0, "daily_token_cap": 200000},
    }

    def test_sibling_block_beats_the_legacy_top_level_pair(self):
        text = "\n".join(cli.status_lines(self.CFG, {"usd": 0.1, "tokens": 5, "mom": 2}))
        self.assertIn("claude-sonnet-5-5", text)
        self.assertNotIn("deepseek/x", text)
        self.assertIn("claude-opus-5-5", text)
        self.assertIn("2 / 6", text)                  # mom calls today / limit
        self.assertIn("coding", text)

    def test_warns_when_the_advisor_is_on_without_a_board(self):
        cfg = {**self.CFG, "advisor": {"enabled": True, "boards": [], "daily_usd_cap": 2.0, "daily_token_cap": 1}}
        self.assertIn("no board", "\n".join(cli.status_lines(cfg, {"usd": 0, "tokens": 0, "mom": 0})))

    def test_mom_off_is_said_plainly(self):
        cfg = {**self.CFG, "mom": {"model": "", "provider": "", "max_per_card": 1, "max_per_day": 6}}
        self.assertIn("off", "\n".join(cli.status_lines(cfg, {"usd": 0, "tokens": 0, "mom": 0})).lower())


class DoctorTest(unittest.TestCase):
    LLM = {"allow_model_override": True, "allow_provider_override": True,
           "allowed_models": ["claude-sonnet-5-5", "claude-opus-5-5"], "allowed_providers": [SUB]}

    def cfg(self, **over):
        base = {"sibling": {"model": "claude-sonnet-5-5", "provider": SUB},
                "mom": {"model": "claude-opus-5-5", "provider": SUB},
                "advisor": {"enabled": True, "boards": ["coding"]}}
        return {**base, **over}

    def run_doctor(self, cfg=None, llm=None, probe=None, version="0.22.5", internals=True):
        probe = probe or (lambda provider, model: None)
        return cli.run_checks(cfg or self.cfg(), self.LLM if llm is None else llm, probe, version, internals)

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
        cfg = self.cfg(sibling={"model": "claude-sonnet-5-5", "provider": ""})
        self.assertEqual(self.by_name(self.run_doctor(cfg))["sibling pair"][0], "fail")

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
        self.assertEqual(checks["sibling call"][0], "ok")
        self.assertEqual(checks["mom call"][0], "fail")
        self.assertIn("Key limit exceeded", checks["mom call"][1])

    def test_probe_is_skipped_when_the_config_is_already_broken(self):
        calls = []
        llm = {**self.LLM, "allowed_models": []}
        self.run_doctor(llm=llm, probe=lambda p, m: calls.append(m))
        self.assertEqual(calls, [])

    def test_mom_off_is_fine(self):
        cfg = self.cfg(mom={"model": "", "provider": ""})
        checks = self.by_name(self.run_doctor(cfg))
        self.assertEqual(checks["mom pair"][0], "ok")
        self.assertNotIn("mom call", checks)

    def test_advisor_off_or_without_board_is_a_warning(self):
        cfg = self.cfg(advisor={"enabled": True, "boards": []})
        self.assertEqual(self.by_name(self.run_doctor(cfg))["advisor"][0], "warn")
        cfg = self.cfg(advisor={"enabled": False, "boards": ["coding"]})
        self.assertEqual(self.by_name(self.run_doctor(cfg))["advisor"][0], "warn")

    def test_exit_code_counts_failures_only(self):
        self.assertEqual(cli.exit_code([("a", "ok", ""), ("b", "warn", "")]), 0)
        self.assertEqual(cli.exit_code([("a", "ok", ""), ("b", "fail", "")]), 1)

    def test_version_compare_is_numeric(self):
        self.assertTrue(cli.version_ok("0.22.5"))
        self.assertTrue(cli.version_ok("0.100.0"))
        self.assertFalse(cli.version_ok("0.9.9"))
        self.assertFalse(cli.version_ok("garbage"))


class CommandWiringTest(unittest.TestCase):
    def test_handlers_run_with_injected_io(self):
        s = Script("1", "1", "3", "1")
        saved = {}
        env = cli.Env(load=lambda: saved, save=lambda cfg: saved.update(cfg), providers=lambda: LISTING,
                      boards=lambda: ["default"], spent=lambda: {"usd": 0, "tokens": 0, "mom": 0},
                      probe=lambda p, m: None, version="0.22.5", internals=True,
                      ask=s.ask, say=s.say, cron=lambda: s.say("cron created"), cron_exists=lambda: False)
        self.assertEqual(cli.cmd_setup(env, preset=None, yes=True), 0)
        self.assertEqual(saved["calling_mom"]["sibling"]["provider"], "openrouter")
        self.assertIn("cron created", s.text)
        self.assertEqual(cli.cmd_status(env), 0)
        self.assertEqual(cli.cmd_doctor(env), 0)

    def test_setup_without_confirmation_does_not_write(self):
        s = Script("1", "1", "3", "1", "n")
        saved = {}
        env = cli.Env(load=lambda: {}, save=lambda cfg: saved.update(cfg), providers=lambda: LISTING,
                      boards=lambda: ["default"], spent=lambda: {}, probe=lambda p, m: None, version="0.22.5",
                      internals=True, ask=s.ask, say=s.say, cron=lambda: None, cron_exists=lambda: True)
        self.assertEqual(cli.cmd_setup(env, preset=None, yes=False), 1)
        self.assertEqual(saved, {})

    def test_doctor_returns_one_on_a_failed_check(self):
        s = Script()
        env = cli.Env(load=lambda: {}, save=lambda c: None, providers=lambda: [], boards=lambda: [],
                      spent=lambda: {}, probe=lambda p, m: None, version="0.1.0", internals=True,
                      ask=s.ask, say=s.say, cron=lambda: None, cron_exists=lambda: True)
        self.assertEqual(cli.cmd_doctor(env), 1)
        self.assertIn("FAIL", s.text)


if __name__ == "__main__":
    unittest.main()
