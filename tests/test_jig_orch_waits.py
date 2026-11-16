"""jig orch: the activity file, the waits file, the stall and resumed lines, the status
lines of both, and the rollover of the session from inside its pane.

The state rule, the since of a state and the stall logic are tested as plain functions
with a given clock, given screens and given files. A few tests start the loop itself,
with the private tmux server and the double for claude of tests/test_jig_orch.py, and
a few run jig orch as a command.
"""

import datetime
import fcntl
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import unittest
import uuid
from unittest import mock

from tests.support import TempTestCase, run
from tests.test_jig_orch import ORCH, PROJECT, SESSION, OrchTestCase, load_orch
from tests.units_support import DIALOG_SCREEN

orch = load_orch()

OTHER = "other"
RULE = "─" * 60
BAR = "  ⏵⏵ auto mode on (shift+tab to cycle)"
RUNNING = "✽ Ruminating… (18s · ↓ 1.2k tokens)"
DONE_LINE = "✻ Baked for 24s · done 9:44 AM"
TASK_RUNNING = "✢ Running the tests of the supervisor loop… (2m 3s · ↓ 3.1k tokens)"
TASK_LIST = ["  ⎿  ☒ Read the brief", "     ☐ Run the tests of the supervisor loop",
             "     ☐ Write the report"]
POLL = 60.0


def screen(*lines):
    """A Claude Code screen: the given lines, then an empty input box and the bar."""
    return "\n".join(["● ready", *lines, "", RULE, "❯ ", RULE, BAR]) + "\n"


def at(minutes):
    """A fixed clock: minutes after 2026-03-02 10:00 local time."""
    start = datetime.datetime(2026, 3, 2, 10, 0).astimezone()
    return start + datetime.timedelta(minutes=minutes)


class TestRunningTurn(unittest.TestCase):
    def test_spinner_line_just_above_the_box_is_a_running_turn(self):
        # The plain spinner line, and the active text of a task with several words and
        # its task list under it, each just above the input box, read as a running turn.
        self.assertTrue(orch.jig_tmux.running_turn(screen(RUNNING)))
        self.assertTrue(orch.jig_tmux.running_turn(screen(TASK_RUNNING, *TASK_LIST)))

    def test_done_line_and_a_spinner_line_quoted_far_above_are_no_running_turn(self):
        # The line left after a turn is no running turn. Nor is a spinner line that a
        # printed report quotes far above the box while the done line sits just above it.
        self.assertFalse(orch.jig_tmux.running_turn(screen(DONE_LINE)))
        quoted = [RUNNING] + [f"  line {number} of the printed report" for number in range(30)]
        self.assertFalse(orch.jig_tmux.running_turn(screen(*quoted, DONE_LINE)))

    def test_screen_without_an_input_box_shows_no_running_turn(self):
        self.assertFalse(orch.jig_tmux.running_turn(RUNNING + "\n" + DIALOG_SCREEN))


class TestActivityState(unittest.TestCase):
    def test_each_state_in_the_order_of_its_test(self):
        # A unit wins over everything; then a pane that is not live; then a dialog,
        # which is idle even with a spinner line above it once DIALOG_POLLS polls had
        # no input bar; then a running turn; else idle.
        dialog = RUNNING + "\n" + DIALOG_SCREEN
        polls = orch.jig_tmux.DIALOG_POLLS
        self.assertEqual(orch.activity_state(True, False, polls, None), "unit")
        self.assertEqual(orch.activity_state(False, False, 0, screen(RUNNING)), "down")
        self.assertEqual(orch.activity_state(False, True, polls, dialog), "idle")
        self.assertEqual(orch.activity_state(False, True, 0, screen(RUNNING)), "busy")
        self.assertEqual(orch.activity_state(False, True, 0, screen(DONE_LINE)), "idle")
        self.assertEqual(orch.activity_state(False, True, 0, None), "idle")


class TestUnitLock(TempTestCase):
    """The unit state, from the lock directory and the accounts of the conf file."""

    conf = {"ORCH_ACCOUNT": "orchestrator", "SPEC_ACCOUNT": "specwriter",
            "IMPL_ACCOUNT": "implementer", "REV_ACCOUNT": "specwriter reviewer"}

    def setUp(self):
        super().setUp()
        self.locks = self.tmp / "lock"
        self.locks.mkdir()
        patcher = mock.patch.dict(os.environ, {"JIG_LOCK_DIR": str(self.locks)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def lock(self, name, started, expected=None):
        session = f"{PROJECT}-impl-u1"
        (self.locks / name).write_text(f"session={session}\nstart={orch.stamp(started)}\n",
                                       encoding="utf-8")
        if expected is not None:
            (self.locks / f"{name}.expected").write_text(f"{expected}\n", encoding="utf-8")

    def test_fresh_lock_of_an_account_of_the_conf_file_is_a_unit(self):
        # A lock of the second reviewer account, 10 minutes old with 60 expected, and
        # a lock with no expected file at all, each make the state unit.
        self.lock(f"{PROJECT}-reviewer", at(0), 60)
        self.assertTrue(orch.unit_running(self.conf, PROJECT, at(10)))
        (self.locks / f"{PROJECT}-reviewer").unlink()
        self.lock(f"{PROJECT}-implementer", at(0))
        self.assertTrue(orch.unit_running(self.conf, PROJECT, at(1000)))

    def test_stale_lock_and_the_files_of_other_names_are_no_unit(self):
        # A lock past 3 times its expected minutes is the lock of a dead unit. The lock
        # of a project whose name starts with this one, an expected file alone, and a
        # lock of an account that the conf file does not name, are not this project's.
        self.lock(f"{PROJECT}-implementer", at(0), 30)
        self.assertTrue(orch.unit_running(self.conf, PROJECT, at(89)))
        self.assertFalse(orch.unit_running(self.conf, PROJECT, at(90)))
        (self.locks / f"{PROJECT}-implementer").unlink()
        self.lock(f"{PROJECT}-x-implementer", at(0), 60)
        (self.locks / f"{PROJECT}-specwriter.expected").write_text("60\n", encoding="utf-8")
        self.lock(f"{PROJECT}-somebody", at(0), 60)
        self.assertFalse(orch.unit_running(self.conf, PROJECT, at(1)))


class TestSince(unittest.TestCase):
    def test_since_stays_while_the_state_stays_and_changes_with_it(self):
        first = orch.kept_since(None, "idle", at(0), POLL)
        self.assertEqual(first, at(0))
        self.assertEqual(orch.kept_since(("idle", first, at(0)), "idle", at(1), POLL), at(0))
        self.assertEqual(orch.kept_since(("idle", at(0), at(1)), "busy", at(2), POLL), at(2))

    def test_restart_keeps_the_since_of_a_fresh_old_file_only(self):
        # The old file was written 3 polls ago: kept. Written 4 polls ago, as by a
        # loop that was killed: a new since.
        old = ("idle", at(0), at(10))
        self.assertEqual(orch.kept_since(old, "idle", at(13), POLL), at(0))
        self.assertEqual(orch.kept_since(old, "idle", at(14), POLL), at(14))

    def test_activity_line_parses_and_a_bad_line_does_not(self):
        line = f"idle\t{orch.stamp(at(0))}\t{orch.stamp(at(1))}\n"
        self.assertEqual(orch.parse_activity(line), ("idle", at(0), at(1)))
        for bad in ("", "idle\n", line + line, line.replace("idle", "asleep"),
                    line.rstrip("\n"), "idle\t2026-03-02T10:00:00\t2026-03-02T10:01:00\n"):
            self.assertIsNone(orch.parse_activity(bad), repr(bad))


class TestStalls(unittest.TestCase):
    """The stall and resumed lines of one waiting loop, one activity poll a minute."""

    def setUp(self):
        self.stalls = orch.Stalls(15 * 60, POLL)
        self.waits = [(OTHER, orch.stamp(at(0)), "the step 10 block")]
        self.found = (None, "missing")
        self.minute = 0
        # Whether the other loop goes on writing its file each minute.
        self.writing = True

    def other(self, state, since):
        """The other project's activity file as its loop writes it this minute."""
        self.found = ((state, at(since), at(self.minute)), None)

    def step(self, minutes=1):
        """Run the given minutes of polls; return the lines they sent."""
        lines = []
        for _ in range(minutes):
            self.minute += 1
            if self.writing and self.found[0] is not None:
                self.found = ((*self.found[0][:2], at(self.minute)), None)
            lines += self.stalls.step(self.waits, lambda other: self.found, at(self.minute))
        return lines

    def test_idle_past_the_stall_time_sends_one_stall_line_over_many_polls(self):
        self.other("idle", 0)
        self.assertEqual(self.step(14), [])
        self.assertEqual(self.step(1),
                         ["stall: other idle since 10:00 (waits on: the step 10 block)"])
        self.assertEqual(self.step(30), [])

    def test_stale_or_missing_activity_file_counts_as_down(self):
        # The other loop wrote its file at minute 0 and then nothing: after 3 polls it
        # is down since minute 0, and stalls at minute 15.
        self.found, self.writing = (("busy", at(0), at(0)), None), False
        self.assertEqual(self.step(14), [])
        self.assertEqual(self.step(1),
                         ["stall: other down since 10:00 (waits on: the step 10 block)"])
        missing = orch.Stalls(15 * 60, POLL)
        lines = [missing.step(self.waits, lambda other: (None, "missing"), at(minute))
                 for minute in range(5, 25)]
        self.assertEqual(lines[15], ["stall: other down since 10:05 (waits on: the step 10 "
                                     "block)"])
        self.assertEqual(sum(lines, []), lines[15])

    def test_busy_then_idle_then_busy_on_3_polls_sends_stall_then_resumed(self):
        self.other("busy", 0)
        self.assertEqual(self.step(5), [])
        self.other("idle", 5)
        self.assertEqual(self.step(15), ["stall: other idle since 10:05 (waits on: the step "
                                         "10 block)"])
        self.other("busy", 20)
        self.assertEqual(self.step(2), [])
        self.assertEqual(self.step(1), ["resumed: other busy"])
        # A later stall gets a line of its own.
        self.other("idle", 23)
        self.assertEqual(self.step(15), ["stall: other idle since 10:23 (waits on: the step "
                                         "10 block)"])

    def test_short_busy_between_two_idles_sends_neither_resumed_nor_a_second_stall(self):
        # The other orchestrator takes a one-minute turn on the stall line of its own
        # loop, then waits again: the stall goes on, and nothing more is sent.
        self.other("idle", 0)
        self.assertEqual(len(self.step(15)), 1)
        self.other("busy", 15)
        self.assertEqual(self.step(1), [])
        self.other("idle", 16)
        self.assertEqual(self.step(40), [])

    def test_unit_never_stalls_and_ends_a_stall_at_once(self):
        self.other("unit", 0)
        self.assertEqual(self.step(60), [])
        self.other("idle", 60)
        self.assertEqual(len(self.step(15)), 1)
        self.other("unit", 75)
        self.assertEqual(self.step(1), ["resumed: other unit"])

    def test_cleared_wait_sends_nothing_more(self):
        self.other("idle", 0)
        self.assertEqual(len(self.step(15)), 1)
        self.waits = []
        self.other("busy", 15)
        self.assertEqual(self.step(10), [])
        self.other("idle", 25)
        self.assertEqual(self.step(30), [])

    def test_stall_since_another_day_names_the_date(self):
        self.assertEqual(orch.clock(at(0), at(60)), "10:00")
        self.assertEqual(orch.clock(at(0), at(24 * 60)), "2026-03-02 10:00")


class TestStallTime(TempTestCase):
    def conf(self, text):
        (self.tmp / "host.conf").write_text(text, encoding="utf-8")
        return orch.stall_time(self.tmp)

    def test_key_of_the_host_config_and_its_default(self):
        self.assertEqual(orch.stall_time(self.tmp), (900.0, None))
        self.assertEqual(self.conf("# the host\nHEAVY_SLOTS=1\n"), (900.0, None))
        self.assertEqual(self.conf("ORCH_STALL_MINUTES = 5\n"), (300.0, None))

    def test_bad_value_or_unreadable_file_uses_15_minutes_with_a_problem(self):
        for value in ("abc", "0", "-3", "1.5", ""):
            seconds, problem = self.conf(f"ORCH_STALL_MINUTES={value}\n")
            self.assertEqual(seconds, 900.0, value)
            self.assertIn("is not a whole number of 1 or more", problem)
        (self.tmp / "host.conf").unlink()
        (self.tmp / "host.conf").mkdir()
        seconds, problem = orch.stall_time(self.tmp)
        self.assertEqual(seconds, 900.0)
        self.assertIn("cannot read", problem)


class TestQuietLines(OrchTestCase):
    def test_stall_line_does_not_turn_the_next_hourly_tick_into_an_hourly_update(self):
        # A stall line goes from the queue into the event file. The hourly tick after it
        # finds no change: it writes the idle line and queues no hourly update, and the
        # line goes into no record of the loop.
        (self.reviews / "orch").mkdir()
        environment = {"HOME": str(self.home), "JIG_SRV": str(self.srv),
                       "JIG_LOCK_DIR": str(self.tmp / "lock")}
        with mock.patch.dict(os.environ, environment):
            loop = orch.Orch(PROJECT)
            stall = "stall: other idle since 10:00 (waits on: a block)"
            loop.queue, loop.stopping, loop.held = [(stall, orch.QUIET)], False, None
            loop.changed, loop.last_change = False, orch.now_text()
            loop.append_queue()
            self.assertEqual(self.events(), [stall])
            self.assertFalse(loop.changed)
            loop.next_tick, loop.hourly = 0.0, 3600.0
            loop.reminded, loop.remind_problem = set(), None
            loop.tick()
        self.assertEqual(loop.queue, [])
        self.assertIn("idle: waits for the orchestrator", self.read(
            self.clone / "impl" / "orch" / "updates.log"))
        for record in ("reminded", "reported", "announced"):
            self.assertFalse((self.reviews / "orch" / record).exists(), record)


class WaitsTestCase(TempTestCase):
    """jig orch <project> wait-on, run as a command in a home with two orch directories."""

    def setUp(self):
        super().setUp()
        self.mine = self.home / "ws" / "reviews" / PROJECT / "orch"
        self.theirs = self.home / "ws" / "reviews" / OTHER / "orch"
        self.mine.mkdir(parents=True)
        self.theirs.mkdir(parents=True)
        self.fresh()

    def fresh(self, state="busy", since=None, written=None):
        """Write the other project's activity file as its running loop does."""
        now = datetime.datetime.now().astimezone()
        written = written or now
        (self.theirs / "activity").write_text(
            f"{state}\t{orch.stamp(since or written)}\t{orch.stamp(written)}\n",
            encoding="utf-8")

    def wait_on(self, *args, **extra):
        return run([ORCH, PROJECT, "wait-on", *args], env=self.env(**extra))

    def waits(self):
        path = self.mine / "waits"
        return [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines()] \
            if path.exists() else []


class TestWaitOn(WaitsTestCase):
    def test_write_replace_and_kept_since(self):
        # The first call writes the line; the same text again keeps its since; a new
        # text replaces the line with a new since. Tabs and newlines become spaces.
        result = self.wait_on(OTHER, "the", "step\t10\nblock")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("warning", result.stdout)
        [[other, since, what]] = self.waits()
        self.assertEqual((other, what), (OTHER, "the step 10 block"))
        self.assertIsNotNone(orch.parse_stamp(since))
        time.sleep(1.1)
        self.assertEqual(self.wait_on(OTHER, "the step 10 block").returncode, 0)
        self.assertEqual(self.waits(), [[OTHER, since, "the step 10 block"]])
        self.assertEqual(self.wait_on(OTHER, "a release").returncode, 0)
        [[_, new_since, what]] = self.waits()
        self.assertEqual(what, "a release")
        self.assertNotEqual(new_since, since)
        self.assertEqual(list(self.mine.glob(".waits.*")), [])

    def test_clear_and_clear_of_a_missing_line(self):
        third = self.home / "ws" / "reviews" / "third" / "orch"
        third.mkdir(parents=True)
        self.wait_on(OTHER, "a block")
        self.wait_on("third", "a review")
        result = self.wait_on(OTHER, "--clear")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([fields[0] for fields in self.waits()], ["third"])
        result = self.wait_on(OTHER, "--clear")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("did not wait on other", result.stdout)
        self.assertEqual([fields[0] for fields in self.waits()], ["third"])

    def test_list_shows_each_wait_with_the_state_of_the_other_or_no_waits(self):
        result = self.wait_on()
        self.assertEqual((result.returncode, result.stdout.strip()),
                         (0, f"jig orch: {PROJECT}: no waits"))
        self.wait_on(OTHER, "a block")
        since = datetime.datetime.now().astimezone() - datetime.timedelta(minutes=20)
        self.fresh("idle", since)
        result = self.wait_on()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, rf"^jig orch: {PROJECT}: waits on other: idle since "
                                        rf"{re.escape(orch.stamp(since))}; waited on since \S+: a "
                                        rf"block\n$")
        (self.theirs / "activity").unlink()
        self.assertIn("waits on other: no activity file;", self.wait_on().stdout)

    def test_usage_errors_and_the_refusal(self):
        for args in ((PROJECT, "a block"), ("Not/A-name", "a block"), (OTHER,),
                     (OTHER, " \t "), (OTHER, "--clear", "now")):
            result = self.wait_on(*args)
            self.assertEqual(result.returncode, 2, args)
        self.assertEqual(self.waits(), [])
        result = self.wait_on("nobody", "a block")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)
        self.assertIn("no orchestrator of nobody", result.stderr)
        self.assertEqual(self.waits(), [])

    def test_refusal_when_the_project_itself_has_no_orch_directory(self):
        # The first name is mistyped, so its orch directory does not exist. The wait
        # would sit in a file that no loop reads; the command refuses in one line that
        # names the directory, and makes nothing.
        result = run([ORCH, "nobody", "wait-on", OTHER, "a block"], env=self.env())
        missing = self.home / "ws" / "reviews" / "nobody"
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stderr.count("\n"), 1, result.stderr)
        self.assertIn(f"{missing / 'orch'} does not exist", result.stderr)
        self.assertFalse(missing.exists())

    def test_missing_or_stale_activity_file_gives_a_warning_after_the_write(self):
        (self.theirs / "activity").unlink()
        result = self.wait_on(OTHER, "a block")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.waits()), 1)
        warning = [line for line in result.stdout.splitlines() if "warning" in line]
        self.assertEqual(len(warning), 1)
        self.assertIn("older than 0.8.0 or no loop runs", warning[0])
        # With polls of a minute, a file written just now is fresh however slowly the
        # command starts, and one written an hour ago is stale; no test waits on a clock.
        self.fresh()
        result = self.wait_on(OTHER, "a block", JIG_ORCH_ACTIVITY="60")
        self.assertNotIn("warning", result.stdout)
        hour_ago = datetime.datetime.now().astimezone() - datetime.timedelta(hours=1)
        self.fresh(written=hour_ago)
        result = self.wait_on(OTHER, "a block", JIG_ORCH_ACTIVITY="60")
        self.assertIn(f"warning: other has no activity file since {orch.stamp(hour_ago)}",
                      result.stdout)


class TestStatusLines(OrchTestCase):
    def test_status_prints_the_activity_and_each_wait(self):
        # No loop runs; the activity file is the one the last loop left, and the waits
        # file holds one wait on a project whose loop writes no activity file.
        orch_files = self.reviews / "orch"
        orch_files.mkdir()
        now = datetime.datetime.now().astimezone()
        (orch_files / "activity").write_text(f"down\t{orch.stamp(now)}\t{orch.stamp(now)}\n",
                                             encoding="utf-8")
        (orch_files / "waits").write_text(f"{OTHER}\t{orch.stamp(now)}\ta block\n",
                                          encoding="utf-8")
        result = self.orch("status")
        self.assertEqual(result.returncode, 3, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[1], f"jig orch: {PROJECT}: activity: down since "
                                   f"{orch.stamp(now)}")
        self.assertEqual(lines[2], f"jig orch: {PROJECT}: waits on other: no activity file; "
                                   f"waited on since {orch.stamp(now)}: a block")


class LoopWaitsCase(OrchTestCase):
    """A running loop with activity polls of a fraction of a second."""

    activity = "0.3"
    stall = "1.5"

    def base_env(self):
        env = super().base_env()
        env.update(JIG_ORCH_ACTIVITY=self.activity, JIG_ORCH_STALL_SECONDS=self.stall)
        return env

    def activity_file(self):
        return orch.parse_activity(self.read(self.reviews / "orch" / "activity"))


class TestLoopActivity(LoopWaitsCase):
    def test_loop_writes_its_state_keeps_since_and_writes_down_when_it_stops(self):
        # The session waits at its input box: idle, with a since that stays across
        # writes. A fresh unit lock makes it unit with a new since; the stop writes down.
        self.start()
        self.until(lambda: self.activity_file() and self.activity_file()[0] == "idle",
                   what="the idle activity line")
        first = self.activity_file()
        self.until(lambda: self.activity_file()[2] > first[2], what="a second write")
        self.assertEqual(self.activity_file()[:2], first[:2])
        locks = self.tmp / "lock"
        locks.mkdir()
        (locks / f"{PROJECT}-implementer").write_text(
            f"session={PROJECT}-impl-u1\nstart={orch.now_text()}\n", encoding="utf-8")
        self.until(lambda: self.activity_file()[0] == "unit", what="the unit state")
        self.assertGreater(self.activity_file()[1], first[1])
        self.assertEqual(self.orch("stop").returncode, 0, self.log())
        self.assertEqual(self.activity_file()[0], "down")


class TestLoopStall(LoopWaitsCase):
    def test_stall_and_resumed_lines_reach_the_event_file_in_time(self):
        # The project waits on another whose loop writes no activity file, so it is
        # down from the first poll; host.conf holds a bad stall key, which the loop
        # logs once. The stall line comes after the stall time; then the other loop
        # writes busy, and the resumed line comes after 3 busy polls.
        (self.srv / "host.conf").write_text("ORCH_STALL_MINUTES=soon\n", encoding="utf-8")
        # The wait is written before the first start, so the project's own orch
        # directory is made here, as an earlier start of its loop would have made it.
        (self.reviews / "orch").mkdir(parents=True)
        (self.home / "ws" / "reviews" / OTHER / "orch").mkdir(parents=True)
        result = run([ORCH, PROJECT, "wait-on", OTHER, "the step 10 block"],
                     env=self.base_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        began = time.monotonic()
        self.start()
        self.until(lambda: any(line.startswith("stall: ") for line in self.events()),
                   what="the stall line")
        stalled = time.monotonic() - began
        [stall] = [line for line in self.events() if line.startswith("stall: ")]
        self.assertRegex(stall, r"^stall: other down since \d\d:\d\d \(waits on: the step 10 "
                                r"block\)$")
        self.assertGreaterEqual(stalled, float(self.stall))
        busy, done = time.monotonic(), threading.Event()

        def other_loop():
            since = orch.stamp(datetime.datetime.now().astimezone())
            while not done.is_set():
                now = orch.stamp(datetime.datetime.now().astimezone())
                orch.write_whole(self.home / "ws" / "reviews" / OTHER / "orch" / "activity",
                                 f"busy\t{since}\t{now}\n")
                time.sleep(0.1)

        writer = threading.Thread(target=other_loop)
        writer.start()
        try:
            self.until(lambda: "resumed: other busy" in self.events(), what="the resumed line")
            resumed = time.monotonic() - busy
            # Read while the other loop still writes: once it stops, its file goes stale
            # and a second stall line follows, as it should.
            lines = [line for line in self.events() if line.startswith(("stall", "resumed"))]
        finally:
            done.set()
            writer.join()
        self.assertGreaterEqual(resumed, 2 * float(self.activity))
        self.assertEqual(lines, [stall, "resumed: other busy"])
        self.assertEqual(self.log().count("ORCH_STALL_MINUTES='soon' is not a whole number"), 1)
        print(f"\nstall line {stalled:.1f} s after the start, stall time {self.stall} s; "
              f"resumed line {resumed:.1f} s after the other turned busy, "
              f"3 polls of {self.activity} s", file=sys.stderr)


FIXTURE = [sys.executable, "-c",
           "import pathlib, signal, sys, time\n"
           "def ended(number, frame):\n"
           "    pathlib.Path(sys.argv[1]).write_text('SIGTERM\\n')\n"
           "    sys.exit(0)\n"
           "signal.signal(signal.SIGTERM, ended)\n"
           "time.sleep(120)\n"]


class TestRollover(OrchTestCase):
    """jig orch rollover. In the refusal tests the test itself holds the loop's lock, in
    place of a loop, and makes the session with a fixture process in place of Claude
    Code that notes a SIGTERM in a file."""

    def setUp(self):
        super().setUp()
        (self.reviews / "orch").mkdir()
        self.claude_id = str(uuid.uuid4())

    def hold_lock(self):
        descriptor = os.open(self.reviews / "orch" / "loop.lock", os.O_RDWR | os.O_CREAT)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(descriptor, b"1\n")
        self.addCleanup(os.close, descriptor)

    def fixture_session(self, record=True, command=None):
        """Start the session with the fixture, and record it as the loop's own."""
        noted = self.tmp / "fixture-signal"
        words = command or [*FIXTURE, str(noted), "--session-id", self.claude_id]
        result = self.tmux("new-session", "-d", "-s", SESSION, "-x", "80", "-y", "20",
                           "exec " + subprocess.list2cmdline(words))
        self.assertEqual(result.returncode, 0, result.stderr)
        tmux_id = self.tmux("display-message", "-p", "-t", f"={SESSION}:",
                            "#{session_id}").stdout.strip()
        if record:
            (self.reviews / "orch" / "session-ids").write_text(
                f"{tmux_id} {self.claude_id}\n", encoding="utf-8")
        if command is None:
            self.until(lambda: self.claude_id in self.arguments(), what="the fixture process")
        return noted

    def arguments(self):
        """The arguments of the process of the session's pane, as one text."""
        try:
            return pathlib.Path(f"/proc/{self.pane()['pid']}/cmdline").read_text(
                errors="replace").replace("\0", " ")
        except (OSError, TypeError):
            return ""

    def where(self):
        """The TMUX and TMUX_PANE values of a shell inside the session's pane."""
        shown = self.tmux("display-message", "-p", "-t", f"={SESSION}:",
                          "#{socket_path}\t#{pane_id}").stdout.strip().split("\t")
        return {"TMUX": f"{shown[0]},1,0", "TMUX_PANE": shown[1]}

    def rollover(self, **extra):
        env = self.base_env()
        env.update(extra)
        return run([ORCH, PROJECT, "rollover"], env=env)

    def assert_refused(self, result, words, pid=None):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1, result.stderr)
        self.assertIn(words, result.stderr)
        self.assertEqual(result.stdout, "")
        if pid is not None:
            os.kill(pid, 0)
            self.assertFalse((self.tmp / "fixture-signal").exists())

    def test_refusals_signal_nothing(self):
        # No loop runs: refused, though the session is its own and the caller in it.
        noted = self.fixture_session()
        pid, inside = self.pane()["pid"], self.where()
        self.assert_refused(self.rollover(**inside), "no loop runs", pid)
        self.hold_lock()
        # The caller outside the pane: another pane ID, or the same ID on another server.
        self.assert_refused(self.rollover(TMUX=inside["TMUX"], TMUX_PANE="%99"),
                            "does not run inside the pane", pid)
        self.assert_refused(self.rollover(TMUX="/tmp/no-such-socket,1,0",
                                          TMUX_PANE=inside["TMUX_PANE"]),
                            "does not run inside the pane", pid)
        self.assert_refused(self.rollover(), "does not run inside the pane", pid)
        # A session whose IDs differ from the record is not the loop's own.
        (self.reviews / "orch" / "session-ids").write_text(f"$99 {uuid.uuid4()}\n",
                                                          encoding="utf-8")
        self.assert_refused(self.rollover(**inside), "is not the loop's own", pid)
        self.assertFalse(noted.exists())

    def test_session_that_is_not_live_or_runs_another_process_is_refused(self):
        self.hold_lock()
        self.assert_refused(self.rollover(), "is not live")
        # The pane's start command names the session ID, but the process that runs
        # there, after the shell's exec, does not hold it among its arguments.
        self.fixture_session(command=["sleep", "120", ";", "--session-id", self.claude_id])
        tmux_id = self.tmux("display-message", "-p", "-t", f"={SESSION}:",
                            "#{session_id}").stdout.strip()
        (self.reviews / "orch" / "session-ids").write_text(f"{tmux_id} {self.claude_id}\n",
                                                          encoding="utf-8")
        pid = self.pane()["pid"]
        self.assert_refused(self.rollover(**self.where()), "does not run with --session-id",
                            pid)
        self.tmux("kill-session", "-t", f"={SESSION}")
        self.until(lambda: self.pane() is None, what="the end of the session")
        self.assert_refused(self.rollover(), "is not live")

    def test_own_pane_gets_sigterm_and_the_loop_starts_the_next_session(self):
        # A real loop runs the double for claude in its pane. A process outside the pane
        # that carries the same session ID, as a fork would, is not signalled.
        self.start()
        first = self.pane()
        bystander = subprocess.Popen([*FIXTURE, str(self.tmp / "bystander-signal"),
                                      "--session-id", self.session_ids()[0]])
        self.addCleanup(bystander.kill)
        result = self.rollover(**self.where())
        self.assertEqual(result.returncode, 0, result.stderr + self.log())
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertIn(f"ending Claude Code session {self.session_ids()[0]}", result.stdout)
        self.until(lambda: len(self.calls()) == 2 and self.ready(),
                   what="the next session")
        self.assertEqual(self.pane()["id"], first["id"])
        self.assertIsNone(bystander.poll())
        self.assertFalse((self.tmp / "bystander-signal").exists())
        os.kill(self.loop_pid(), 0)


if __name__ == "__main__":
    unittest.main()
