"""Pure-logic tests for signals.py (no board, no Hermes imports).

    cd <plugin dir> && python -m unittest -v test_signals
"""

import unittest

import signals


def feed(tr, tool="terminal", args=None, ok=True, text="", error_type=None):
    return tr.feed(tool, args or {"command": "x"}, ok, text, error_type)


class SignatureTest(unittest.TestCase):
    def test_numbers_paths_and_hex_do_not_change_signature(self):
        a = signals.signature("FAILED test_x at /tmp/a1b2/run_17.py line 42: boom 0xdeadbeef")
        b = signals.signature("FAILED test_x at /tmp/ffee/run_99.py line 77: boom 0xcafe0001")
        self.assertEqual(a, b)

    def test_different_errors_differ(self):
        self.assertNotEqual(signals.signature("AssertionError: 1 != 2"), signals.signature("ImportError: no module foo"))


class InfraTest(unittest.TestCase):
    def test_network_and_rate_limit_are_infra(self):
        for text in ("Connection refused", "HTTP 503 Service Unavailable", "429 Too Many Requests",
                     "Temporary failure in name resolution", "Read timed out", "ECONNRESET"):
            self.assertTrue(signals.is_infra(text), text)

    def test_board_and_worker_plumbing_failures_are_infra(self):
        # seen 6x in the real coding-board history: a stronger model cannot fix these
        for text in ("pid 24341 exited with code 1 Worker's last output: 'Session: local-cd50 Kanban owner refused: Kanban execution unknown; retained by owner'",
                     "ValueError: kanban_skills_unavailable",
                     "Memory pressure critical: new workers restricted this tick",
                     "stale_kanban_claim",
                     "Provider rejected this profile's credential"):
            self.assertTrue(signals.is_infra(text), text)

    def test_code_errors_are_not_infra(self):
        for text in ("AssertionError: 1 != 2", "SyntaxError: invalid syntax", "NameError: x"):
            self.assertFalse(signals.is_infra(text), text)


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.tr = signals.Tracker(same_error=3, loop=3, malformed=3, steps=5)

    def test_same_error_signature_streak_fires_on_third(self):
        out = [feed(self.tr, ok=False, text="AssertionError: 1 != 2", args={"command": f"pytest {i}"})["signals"] for i in range(3)]
        self.assertEqual(out[:2], [[], []])
        self.assertIn("same_error", out[2])

    def test_red_tests_with_changing_messages_do_not_fire(self):
        out = [feed(self.tr, ok=False, text=f"AssertionError: {i} != {i + 1} in test_{'abcdef'[i]}", args={"command": f"c{i}"})["signals"]
               for i in range(5)]
        self.assertTrue(all("same_error" not in s for s in out))

    def test_success_resets_streak(self):
        feed(self.tr, ok=False, text="boom", args={"command": "a"})
        feed(self.tr, ok=False, text="boom", args={"command": "b"})
        feed(self.tr, ok=True, text="fine", args={"command": "c"})
        s = feed(self.tr, ok=False, text="boom", args={"command": "d"})["signals"]
        self.assertNotIn("same_error", s)

    def test_infra_failures_never_count(self):
        out = [feed(self.tr, ok=False, text="Connection refused", args={"command": f"c{i}"})["signals"] for i in range(5)]
        self.assertTrue(all("same_error" not in s for s in out))

    def test_loop_identical_call_three_times(self):
        out = [feed(self.tr, tool="read_file", args={"path": "/a"})["signals"] for _ in range(3)]
        self.assertIn("loop", out[2])

    def test_rerunning_the_same_check_after_an_edit_is_not_a_loop(self):
        # seen live (cache task): run tests, patch, run the same tests, patch, run again = normal TDD
        out = []
        for i in range(3):
            out.append(feed(self.tr, tool="terminal", args={"command": "python3 test_cache.py"})["signals"])
            feed(self.tr, tool="patch", args={"path": "cache.py", "new_string": str(i)})
        self.assertTrue(all("loop" not in s for s in out))

    def test_identical_reads_without_any_edit_are_still_a_loop(self):
        out = [feed(self.tr, tool="terminal", args={"command": "cat x"})["signals"] for _ in range(3)]
        self.assertIn("loop", out[2])

    def test_kanban_tools_never_count_as_loop(self):
        out = [feed(self.tr, tool="kanban_heartbeat", args={"note": "x"})["signals"] for _ in range(4)]
        self.assertTrue(all("loop" not in s for s in out))

    def test_malformed_calls(self):
        out = [feed(self.tr, ok=False, text="bad", error_type="invalid_arguments", args={"i": i})["signals"] for i in range(3)]
        self.assertIn("malformed", out[2])

    def test_steps_fire_after_n_calls_and_heartbeat_resets(self):
        out = [feed(self.tr, args={"i": i})["signals"] for i in range(5)]
        self.assertIn("steps", out[4])
        tr = signals.Tracker(steps=3)
        feed(tr, args={"i": 1}); feed(tr, args={"i": 2})
        feed(tr, tool="kanban_heartbeat", args={"note": "alive"})
        self.assertNotIn("steps", feed(tr, args={"i": 3})["signals"])

    def test_steps_stay_silent_while_the_worker_edits_files(self):
        # audit: 4 of 6 advisor calls were `steps` on cards that made progress
        tr = signals.Tracker(steps=3)
        feed(tr, args={"i": 1}); feed(tr, tool="patch", args={"path": "a.py"})
        self.assertNotIn("steps", feed(tr, args={"i": 2})["signals"])
        out = [feed(tr, args={"i": i})["signals"] for i in range(3, 5)]
        self.assertIn("steps", out[1])  # third call since the edit: fires again

    def test_signal_resets_after_firing_so_advice_is_not_spammed(self):
        for i in range(3):
            feed(self.tr, ok=False, text="boom", args={"command": f"c{i}"})
        s = feed(self.tr, ok=False, text="boom", args={"command": "c3"})["signals"]
        self.assertNotIn("same_error", s)

    def test_flux_stall_fires_on_flux_dependency_not_ready(self):
        out = feed(self.tr, ok=True, text="dependency 'flux-system/monitoring-prometheus' is not ready")["signals"]
        self.assertIn("flux_stall", out)

    def test_flux_stall_fires_on_applied_revision_sha(self):
        out = feed(self.tr, ok=True, text="Applied revision: refs/heads/main@sha1:468443ea01c5a2b93b7e4835fbccb6963422abba")["signals"]
        self.assertIn("flux_stall", out)

    def test_flux_stall_does_not_fire_on_normal_output(self):
        out = feed(self.tr, ok=True, text="all good")["signals"]
        self.assertNotIn("flux_stall", out)


if __name__ == "__main__":
    unittest.main()
