import datetime
import stat
import subprocess
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

(REPORT, REFUSED, USAGE, DIALOG, TIMEOUT, NO_CHANGE, ENDED, UNREACHABLE,
 UNPUSHED) = 0, 1, 2, 3, 4, 5, 6, 7, 8

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
        self.assertIn("usage: jig wait <project> <ID> [--expected <minutes>]", result.stderr)
        for code in range(9):
            self.assertRegex(result.stderr, rf"(?m)^  {code}  ")

    def test_wait_refuses_a_setting_that_is_not_a_positive_number(self):
        self.write_lock()
        result = self.tool("wait", "jig", "demo", JIG_WAIT_POLL="0")
        self.assertEqual(result.returncode, REFUSED)



class WaitUnpushedTest(UnitsTestCase):
    """The end of a wait on a unit that waits at its prompt with a commit not pushed.

    The worker account holds the two clones of the project beside each other, cloned
    from the fixture origin; a test adds a commit to one of them and does not push it.
    """

    # A check every 0.2 s and a poll every 0.02 s.
    QUICK = {"JIG_WAIT_POLL": "0.02", "JIG_WAIT_CHECK": "0.2"}
    # A turn at work: the spinner line just above the input box.
    RUNNING_SCREEN = NORMAL_SCREEN.replace(
        "● Running the tests\n", "● Running the tests\n✽ Ruminating… (18s · ↓ 1.2k tokens)\n")

    def setUp(self):
        super().setUp()
        self.write_lock()
        src = self.account("implementer") / "ws" / "src"
        src.mkdir(parents=True)
        self.clones = {name: src / name for name in ("jig-dev", "jig")}
        for path in self.clones.values():
            self.ok(["git", "clone", "-q", "-b", "master", self.origin, path])

    def commit_in(self, name):
        self.ok(["git", "-C", self.clones[name], "commit", "-q", "--no-verify",
                 "--allow-empty", "-m", "a report that is not pushed"])

    def wait(self):
        return self.tool("wait", "jig", "demo", **self.QUICK)

    def test_unchanged_screen_at_the_bar_with_an_unpushed_commit_ends_with_its_own_code(self):
        # The same screen at the check, the input bar and no running turn: an unpushed
        # commit in either clone ends the wait with code 8, its line and the screen.
        for name in ("jig-dev", "jig"):
            with self.subTest(name):
                self.set_screen("implementer", NORMAL_SCREEN)
                self.commit_in(name)
                result = self.wait()
                self.assertEqual(result.returncode, UNPUSHED, result.stdout + result.stderr)
                self.assertIn("waits at its input prompt with a commit that is not pushed "
                              f"in ~/ws/src/{name} ", result.stdout)
                self.assertIn("Running the tests", result.stdout)
                self.ok(["git", "-C", self.clones[name], "reset", "-q", "--hard",
                         "origin/master"])

    def test_unchanged_screen_with_every_commit_pushed_ends_as_no_change(self):
        self.set_screen("implementer", NORMAL_SCREEN)
        result = self.wait()
        self.assertEqual(result.returncode, NO_CHANGE, result.stdout + result.stderr)

    def test_unchanged_screen_with_a_running_turn_ends_as_no_change(self):
        # A unit that runs a long command between its commit and its push shows the
        # same screen at each check, with its spinner line: that is no stop.
        self.set_screen("implementer", self.RUNNING_SCREEN)
        self.commit_in("jig-dev")
        result = self.wait()
        self.assertEqual(result.returncode, NO_CHANGE, result.stdout + result.stderr)

    def test_changed_screen_with_an_unpushed_commit_does_not_end_the_wait(self):
        # Checks pass while the screen changes, until the session ends.
        self.set_screen_script("implementer", CHANGING_SCREEN)
        self.end_session_after("implementer", 14)
        self.commit_in("jig-dev")
        result = self.wait()
        self.assertEqual(result.returncode, ENDED, result.stdout + result.stderr)
        self.assertIn("check ok", result.stdout)

    def test_dialog_with_an_unpushed_commit_is_a_dialog(self):
        self.set_screen("implementer", DIALOG_SCREEN)
        self.commit_in("jig")
        result = self.wait()
        self.assertEqual(result.returncode, DIALOG, result.stdout + result.stderr)


class WaitExpectedTest(UnitsTestCase):
    """jig wait --expected puts a new expected time beside the lock of a running unit."""

    def expected_file(self):
        return self.lock_dir / "jig-implementer.expected"

    def start_wait(self, *extra):
        """Start a jig wait that keeps running, and return once it has polled the screen."""
        process = subprocess.Popen(
            [ROOT / "libexec" / "jig-wait", "jig", "demo", *extra],
            env=self.base_env(**NO_CHECK), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
        self.addCleanup(self.stop, process)
        for _ in range(200):
            if self.ssh_targets():
                return process
            time.sleep(0.05)
        self.fail("the running jig wait never polled the screen")

    def stop(self, process):
        if process.poll() is None:
            process.kill()
        process.communicate()

    def ended(self, process, seconds=10):
        try:
            out, err = process.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            self.fail("the running jig wait did not end")
        return process.returncode, out + err

    def lock_from_minutes_ago(self, minutes, expected):
        started = datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes)
        self.write_lock(start=started, expected=expected)
        self.set_screen_script("implementer", CHANGING_SCREEN)

    def test_expected_gives_a_running_wait_a_shorter_timeout(self):
        # The session started 4 minutes ago with 5 expected, so a wait now runs on. A
        # second wait sets 1 minute: both end at once on the timeout, since 3x the new
        # value counts from the session start, not from either wait.
        self.lock_from_minutes_ago(4, 5)
        running = self.start_wait()
        result = self.tool("wait", "jig", "demo", "--expected", "1", **NO_CHECK)
        self.assertEqual(result.returncode, TIMEOUT, result.stdout + result.stderr)
        self.assertIn("expected 1 minutes", result.stdout)
        self.assertEqual(self.expected_file().read_text(encoding="utf-8"), "1\n")
        code, output = self.ended(running)
        self.assertEqual(code, TIMEOUT, output)
        self.assertIn("timeout: 3x 1 minutes", output)

    def test_expected_replaces_a_read_only_file_and_leaves_it_group_readable(self):
        # The old file belongs to the worker account and may not be writable here: the
        # new value still replaces it, readable by the group as jig start leaves it.
        self.lock_from_minutes_ago(4, 5)
        self.expected_file().chmod(0o444)
        result = self.tool("wait", "jig", "demo", "--expected", "1", **NO_CHECK)
        self.assertEqual(result.returncode, TIMEOUT, result.stdout + result.stderr)
        self.assertEqual(self.expected_file().read_text(encoding="utf-8"), "1\n")
        self.assertEqual(stat.S_IMODE(self.expected_file().stat().st_mode), 0o664)
        self.assertEqual(sorted(path.name for path in self.lock_dir.iterdir()),
                         ["jig-implementer", "jig-implementer.expected"])

    def test_a_wait_keeps_the_last_value_when_the_file_holds_none(self):
        # A running wait rereads the file at each poll. While the file is empty or holds
        # a word, it keeps the 5 minutes it had and runs on; a valid 1 then ends it.
        self.lock_from_minutes_ago(4, 5)
        running = self.start_wait()
        for text in ("", "soon\n"):
            self.expected_file().write_text(text, encoding="utf-8")
            time.sleep(0.4)
            self.assertIsNone(running.poll(), f"the wait ended on {text!r}")
        self.expected_file().write_text("1\n", encoding="utf-8")
        code, output = self.ended(running)
        self.assertEqual(code, TIMEOUT, output)

    def test_expected_with_no_lock_refuses_in_one_line_and_writes_nothing(self):
        # Only another unit holds a lock, and the report of this one is on origin: a
        # wait without --expected would end with 0, but a new value has nowhere to go.
        self.write_lock(session="jig-impl-other")
        self.push_subject("log: demo")
        before = self.expected_file().read_text(encoding="utf-8")
        result = self.tool("wait", "jig", "demo", "--expected", "60", **NO_CHECK)
        self.assertEqual(result.returncode, REFUSED, result.stdout + result.stderr)
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn("no lock", result.stderr)
        self.assertEqual(self.expected_file().read_text(encoding="utf-8"), before)
        self.assertEqual(sorted(path.name for path in self.lock_dir.iterdir()),
                         ["jig-implementer", "jig-implementer.expected"])

    def test_expected_that_is_not_a_whole_number_above_zero_is_a_usage_error(self):
        # Zero, a word, a negative or fractional number, and a missing value are each a
        # usage error with the usage text, and the file beside the lock stays as it was.
        self.lock_from_minutes_ago(1, 45)
        for value in (["0"], ["soon"], ["-5"], ["1.5"], []):
            with self.subTest(value=value):
                result = self.tool("wait", "jig", "demo", "--expected", *value)
                self.assertEqual(result.returncode, USAGE, result.stdout + result.stderr)
                self.assertIn("usage: jig wait", result.stderr)
                self.assertEqual(self.expected_file().read_text(encoding="utf-8"), "45\n")


if __name__ == "__main__":
    unittest.main()
