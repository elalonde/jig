import datetime
import sys
import time
import unittest

from tests.support import NO_CLEAN_ROOM, ROOT
from tests.units_support import DIALOG_SCREEN, NORMAL_SCREEN, UnitsTestCase

sys.path.insert(0, str(ROOT / "lib"))

import jig_tmux  # noqa: E402

# The line under the input bar in each permission mode, as Claude Code draws it.
MODE_BARS = (
    "  ⏵⏵ auto mode on (shift+tab to cycle)",
    "  ⏵⏵ accept edits on (shift+tab to cycle)",
    "  ⏸ plan mode on (shift+tab to cycle)",
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    "  ? for shortcuts",
)
RULE = "─" * 40

# Short settings: a check every 0.4 s and a poll every 0.05 s.
FAST = {"JIG_WAIT_POLL": "0.05", "JIG_WAIT_CHECK": "0.4"}
# A check that never comes during a test, for the ends that must come before it.
NO_CHECK = {"JIG_WAIT_POLL": "0.05", "JIG_WAIT_CHECK": "600"}

REPORT, REFUSED, USAGE, DIALOG, TIMEOUT, NO_CHANGE, ENDED, UNREACHABLE = 0, 1, 2, 3, 4, 5, 6, 7

# A screen that differs at every capture, in its letters.
CHANGING_SCREEN = """printf '● step %s\\n' "$(printf 'x%.0s' $(seq 1 "$1"))"
printf '> \\n  ⏵⏵ auto mode on (shift+tab to cycle)\\n'
"""

# A screen whose only changes are digits, a spinner line and the line under the bar.
SELF_MOVING_SCREEN = """printf '● Running the tests, %s passed\\n' "$1"
printf '✻ Thinking… (%ss · esc to interrupt)\\n' "$1"
printf '> \\n  ⏵⏵ auto mode on · ctx %sk\\n' "$1"
"""

# The bar is missing for one capture only, as when a pasted text hides it.
PASTE_THEN_NORMAL = """if [ "$1" = 1 ]; then printf '[Pasted text #1 +40 lines]\\n'
else printf '● working %s\\n> \\n  ⏵⏵ auto mode on\\n' "$(printf 'y%.0s' $(seq 1 "$1"))"; fi
"""


class JigWaitTest(UnitsTestCase):

    def wait(self, settings=FAST, unit="demo"):
        began = time.monotonic()
        result = self.tool("wait", "jig", unit, **settings)
        return result, time.monotonic() - began

    def test_wait_ends_on_the_report_commit(self):
        self.write_lock()
        self.set_screen("implementer", NORMAL_SCREEN)
        self.push_subject("status: demo start")
        self.push_subject("log: demo")
        result, _ = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, REPORT, result.stdout + result.stderr)
        self.assertIn("report commit", result.stdout)

    def test_wait_ends_on_a_report_commit_that_lands_while_it_waits(self):
        self.write_lock()
        self.push_subject("status: demo start")
        # The test pushes from inside the double, after a few polls.
        self.set_screen_script("implementer", CHANGING_SCREEN + (
            f'[ "$1" = 4 ] && git -C {self.seed} commit -q --no-verify --allow-empty '
            f'-m "log: demo" && git -C {self.seed} push -q origin master; true\n'))
        result, _ = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, REPORT, result.stdout + result.stderr)

    def test_wait_does_not_take_a_report_from_before_the_last_start(self):
        self.write_lock()
        self.set_screen_script("implementer", CHANGING_SCREEN)
        self.end_session_after("implementer", 3)
        self.push_subject("log: demo")
        self.push_subject("status: demo start")
        result, _ = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, ENDED, result.stdout + result.stderr)

    def test_wait_ends_on_session_end(self):
        self.write_lock()
        self.set_screen_script("implementer", CHANGING_SCREEN)
        self.end_session_after("implementer", 3)
        result, _ = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, ENDED, result.stdout + result.stderr)
        self.assertIn("ended", result.stdout)

    def test_wait_ends_on_timeout_counted_from_the_session_start(self):
        started = datetime.datetime.now().astimezone() - datetime.timedelta(minutes=4)
        self.write_lock(start=started, expected=1)
        self.set_screen_script("implementer", CHANGING_SCREEN)
        result, _ = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, TIMEOUT, result.stdout + result.stderr)
        self.assertIn("timeout", result.stdout)

    def test_wait_does_not_time_out_before_three_times_the_expected_time(self):
        started = datetime.datetime.now().astimezone() - datetime.timedelta(minutes=2)
        self.write_lock(start=started, expected=1)
        self.set_screen_script("implementer", CHANGING_SCREEN)
        self.end_session_after("implementer", 4)
        result, _ = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, ENDED, result.stdout + result.stderr)

    def test_wait_reports_a_dialog_at_once(self):
        self.write_lock()
        self.set_screen("implementer", DIALOG_SCREEN)
        result, elapsed = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, DIALOG, result.stdout + result.stderr)
        self.assertLess(elapsed, 30)
        self.assertIn("Do you want to proceed?", result.stdout)

    def test_wait_reads_the_bar_of_every_permission_mode_as_no_dialog(self):
        home = self.account("implementer")
        for bar in MODE_BARS:
            with self.subTest(bar=bar):
                (home / "tmux-calls").unlink(missing_ok=True)
                self.write_lock()
                self.set_screen("implementer", f"● Running the tests\n\n{RULE}\n>\n{RULE}\n{bar}\n")
                # Four polls in which a missing bar would have been a dialog, then the end.
                self.end_session_after("implementer", 6)
                result, _ = self.wait(NO_CHECK)
                self.assertEqual(result.returncode, ENDED, result.stdout + result.stderr)

    def test_dialog_test_finds_each_bar_and_no_bar_on_a_dialog(self):
        for bar in MODE_BARS:
            self.assertTrue(jig_tmux.has_input_bar(f"● done\n{RULE}\n>\n{RULE}\n{bar}\n"), bar)
        self.assertFalse(jig_tmux.has_input_bar(DIALOG_SCREEN))
        self.assertFalse(jig_tmux.has_input_bar(f"● done\n{RULE}\n>\n{RULE}\n"))

    def test_wait_takes_a_bar_missing_for_one_poll_for_no_dialog(self):
        self.write_lock()
        self.set_screen_script("implementer", PASTE_THEN_NORMAL)
        self.end_session_after("implementer", 5)
        result, _ = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, ENDED, result.stdout + result.stderr)

    def test_wait_ends_on_a_screen_with_no_change(self):
        self.write_lock()
        self.set_screen("implementer", NORMAL_SCREEN)
        result, _ = self.wait()
        self.assertEqual(result.returncode, NO_CHANGE, result.stdout + result.stderr)
        self.assertIn("Running the tests", result.stdout)

    def test_wait_does_not_end_on_a_screen_that_changes(self):
        self.write_lock()
        self.set_screen_script("implementer", CHANGING_SCREEN)
        # About 2 s of polls: several checks pass before the session ends.
        self.end_session_after("implementer", 40)
        result, _ = self.wait()
        self.assertEqual(result.returncode, ENDED, result.stdout + result.stderr)
        self.assertIn("check ok", result.stdout)

    def test_wait_leaves_out_digits_spinner_lines_and_the_bar_line(self):
        self.write_lock()
        self.set_screen_script("implementer", SELF_MOVING_SCREEN)
        result, _ = self.wait()
        self.assertEqual(result.returncode, NO_CHANGE, result.stdout + result.stderr)

    def test_wait_prints_no_screen_for_the_specwriter_account(self):
        self.write_lock(account="specwriter", session="jig-spec-demo")
        self.set_screen("specwriter", "REFERENCE TEXT\n" + NORMAL_SCREEN)
        result, _ = self.wait()
        self.assertEqual(result.returncode, NO_CHANGE, result.stdout + result.stderr)
        self.assertNotIn("REFERENCE TEXT", result.stdout + result.stderr)
        self.assertIn("tmux attach -t jig-spec-demo", result.stdout)

    def test_wait_prints_no_dialog_screen_for_the_specwriter_account(self):
        self.write_lock(account="specwriter", session="jig-rev-demo")
        self.set_screen("specwriter", "REFERENCE TEXT\n" + DIALOG_SCREEN)
        result, _ = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, DIALOG, result.stdout + result.stderr)
        self.assertNotIn("REFERENCE TEXT", result.stdout + result.stderr)
        self.assertNotIn("proceed", result.stdout + result.stderr)
        self.assertIn("tmux attach -t jig-rev-demo", result.stdout)

    def test_wait_prints_no_screen_for_another_spec_account_of_the_conf(self):
        self.write_conf("jig", SPEC_ACCOUNT="writer2")
        self.write_lock(account="writer2", session="jig-spec-demo")
        self.set_screen("writer2", "REFERENCE TEXT\n" + NORMAL_SCREEN)
        result, _ = self.wait()
        self.assertEqual(result.returncode, NO_CHANGE, result.stdout + result.stderr)
        self.assertNotIn("REFERENCE TEXT", result.stdout + result.stderr)

    def test_wait_prints_no_screen_for_the_specwriter_account_with_the_clean_room_on(self):
        self.set_profile("clean room: on")
        self.write_lock(account="specwriter", session="jig-spec-demo")
        self.set_screen("specwriter", "REFERENCE TEXT\n" + NORMAL_SCREEN)
        result, _ = self.wait()
        self.assertEqual(result.returncode, NO_CHANGE, result.stdout + result.stderr)
        self.assertNotIn("REFERENCE TEXT", result.stdout + result.stderr)
        self.assertIn("tmux attach -t jig-spec-demo", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_wait_prints_the_specwriter_screen_with_the_clean_room_off(self):
        self.set_profile("clean room: off")
        self.write_lock(account="specwriter", session="jig-spec-demo")
        self.set_screen("specwriter", "REFERENCE TEXT\n" + NORMAL_SCREEN)
        result, _ = self.wait()
        self.assertEqual(result.returncode, NO_CHANGE, result.stdout + result.stderr)
        self.assertIn("REFERENCE TEXT", result.stdout)
        self.assertNotIn("tmux attach", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_wait_with_no_value_prints_no_specwriter_screen_and_says_so(self):
        # No kickoff tag yet, then a profile with two lines for the clean room.
        for lines in ((), ("clean room: off", "clean room: off")):
            with self.subTest(lines):
                if lines:
                    self.set_profile(*lines)
                self.write_lock(account="specwriter", session="jig-spec-demo")
                self.set_screen("specwriter", "REFERENCE TEXT\n" + NORMAL_SCREEN)
                result, _ = self.wait()
                self.assertEqual(result.returncode, NO_CHANGE, result.stdout + result.stderr)
                self.assertNotIn("REFERENCE TEXT", result.stdout + result.stderr)
                self.assertIn("tmux attach -t jig-spec-demo", result.stdout)
                self.assertEqual(result.stderr.splitlines(), [NO_CLEAN_ROOM])

    def test_wait_reads_no_profile_for_a_session_in_another_account(self):
        self.write_lock()
        self.set_screen("implementer", "REFERENCE TEXT\n" + NORMAL_SCREEN)
        result, _ = self.wait()
        self.assertEqual(result.returncode, NO_CHANGE, result.stdout + result.stderr)
        self.assertIn("REFERENCE TEXT", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_wait_reaches_the_lock_account_by_ssh(self):
        self.write_lock()
        self.set_screen("implementer", DIALOG_SCREEN)
        self.wait(NO_CHECK)
        self.assertEqual(set(self.ssh_targets()), {"implementer@localhost"})

    def test_wait_ends_when_the_account_cannot_be_reached(self):
        self.write_lock()
        (self.fake / "ssh-fails").write_text("", encoding="utf-8")
        result, _ = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, UNREACHABLE, result.stdout + result.stderr)

    def test_wait_refuses_without_a_lock_for_the_unit(self):
        self.write_lock(session="jig-impl-other")
        result, _ = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, REFUSED, result.stdout + result.stderr)
        self.assertIn("no lock", result.stderr)

    def test_wait_without_a_lock_ends_on_a_report_already_on_origin(self):
        self.push_subject("log: demo")
        result, _ = self.wait(NO_CHECK)
        self.assertEqual(result.returncode, REPORT, result.stdout + result.stderr)

    def test_wait_usage_names_every_exit_code(self):
        result = self.tool("wait", "jig")
        self.assertEqual(result.returncode, USAGE)
        for code in range(8):
            self.assertRegex(result.stderr, rf"(?m)^  {code}  ")

    def test_wait_refuses_a_setting_that_is_not_a_positive_number(self):
        self.write_lock()
        result = self.tool("wait", "jig", "demo", JIG_WAIT_POLL="0")
        self.assertEqual(result.returncode, REFUSED)


if __name__ == "__main__":
    unittest.main()
