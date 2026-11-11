"""Tests of jig batch, the runner that starts the units of a batch file one at a time.

jig batch runs jig start, jig wait, jig report and jig close from the directory it
lives in. Each test copies it into a tree of its own whose libexec directory holds
doubles of those four tools, so no session starts and no account is reached. The
doubles act on a fixture origin through a worker clone: the start double commits the
start line of impl/log/status.log, the wait double commits the unit's report as
"log: <ID>", the report double commits "report: <ID>", and the close double commits
the end line. What each double does next is set by files in the fake directory.
"""

import re
import shutil
import time

from tests.support import ROOT, run
from tests.units_support import UnitsTestCase

# One script for the four doubles; the file name says which tool it stands for. Each
# call appends "<tool> <ID>" to calls. The exit code comes from codes/<tool>-<ID>, one
# code per call, 0 when the list is used up; a wait first sleeps the seconds that
# sleep/wait-<ID> gives for that call. A wait that lands the report also commits
# findings-<ID>, when it exists, as impl/log/<ID>-findings.md, the file that a pre-check
# or review session writes. jig batch also imports jig close for its prompt reader;
# then the double is the real jig close.
DOUBLE = """#!/usr/bin/env python3
import datetime
import os
import pathlib
import signal
import subprocess
import sys
import time

if __name__ != "__main__":
    __file__ = "@REAL_CLOSE@"
    exec(compile(pathlib.Path(__file__).read_text(), __file__, "exec"))
else:
    fake = pathlib.Path(os.environ["FAKE"])
    clone = os.environ["DOUBLE_CLONE"]
    tool = pathlib.Path(sys.argv[0]).name.removeprefix("jig-")
    args = sys.argv[1:]
    unit = args[2] if tool == "start" else args[1]

    def record(line):
        with open(fake / "calls", "a") as stream:
            stream.write(line + "\\n")

    def take(name, default):
        path = fake / name
        words = path.read_text().split() if path.exists() else []
        if not words:
            return default
        path.write_text(" ".join(words[1:]))
        return words[0]

    def git(*words):
        subprocess.run(["git", "-C", clone, *words], check=True, capture_output=True)

    def commit(subject, append=None, write=None, findings=None):
        log = pathlib.Path(clone) / "impl" / "log"
        log.mkdir(parents=True, exist_ok=True)
        if append:
            head = subprocess.run(["git", "-C", clone, "rev-parse", "HEAD"], check=True,
                                  capture_output=True, text=True).stdout.strip()
            now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
            with open(log / "status.log", "a") as stream:
                stream.write(f"{now} {unit} {append} {head}\\n")
        if write:
            (log / f"{unit}-report.md").write_text(write)
        if findings is not None:
            (log / f"{unit}-findings.md").write_text(findings)
        git("add", "-A")
        git("commit", "-q", "--no-verify", "--allow-empty", "-m", subject)
        git("push", "-q", "origin", "HEAD")

    if tool == "start":
        progress = pathlib.Path(os.environ["JIG_BATCH_DIR"]) / args[0]
        shown = progress.read_text().strip() if progress.exists() else "none"
        record(f"start {unit} role={args[1]} prompt={args[3]} {args[4]}={args[5]} "
               f"progress={shown}")
    else:
        record(f"{tool} {unit}")
    if tool == "wait":
        def ended(number, frame):
            record(f"ended wait {unit}")
            sys.exit(143)
        signal.signal(signal.SIGTERM, ended)
        time.sleep(float(take(f"sleep/wait-{unit}", "0")))
    code = int(take(f"codes/{tool}-{unit}", "0"))
    if tool == "start" and code == 0:
        commit(f"status: {unit} start", append="start started")
    elif tool == "wait" and code == 0:
        hold = fake / f"hold-after-{unit}"
        if hold.exists():
            hold.unlink()
            (pathlib.Path(os.environ["JIG_HOLD_DIR"]) / args[0]).write_text("held")
        status = take(f"status-{unit}", "OK:_done").replace("_", " ")
        findings = fake / f"findings-{unit}"
        commit(f"log: {unit}", write=status + "\\n",
               findings=findings.read_text() if findings.exists() else None)
    elif tool == "wait":
        print(f"screen of {unit} after exit {code}")
    elif tool == "report" and code == 0:
        commit(f"report: {unit}")
    elif tool == "close" and code == 0:
        report = pathlib.Path(clone) / "impl" / "log" / f"{unit}-report.md"
        word = report.read_text().split(":", 1)[0].strip()
        commit(f"status: {unit} end", append=f"end {word}")
    sys.exit(code)
"""

# Stands in for the installed jig on PATH, which jig batch must never call.
INSTALLED_JIG = """#!/bin/bash
echo "installed jig $*" >> "$FAKE/calls"
exit 99
"""

# Findings files as a pre-check or review session writes them: a heading per finding,
# then its fields. The info file also mentions a must-fix heading in the middle of a
# line, which must not count.
FINDING_FIELDS = "file: libexec/jig-x  line: 3  commit: abc1234\nrule: r\nproblem: p\noptions: o\n"
MUST_FIX = "### F-1 must-fix\n" + FINDING_FIELDS + "### F-2 info\n" + FINDING_FIELDS
SHOULD_FIX = "### F-1 should-fix\n" + FINDING_FIELDS + "### F-2 should-fix\n" + FINDING_FIELDS
INFO_ONLY = ("### F-1 info\n" + FINDING_FIELDS +
             "problem: the prompt says ### F-9 must-fix in the middle of a line\n")
NO_FINDING = "No findings: the prompt agrees with the tree and the spec.\n"

TIMESTAMPED = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}[+-][0-9:]{5} \S")


class BatchCase(UnitsTestCase):
    """A tree with jig batch and the doubles, prompts in the orchestrator's home, a hold
    directory, and a worker clone the doubles push from."""

    def setUp(self):
        super().setUp()
        self.tree = self.tmp / "tree"
        (self.tree / "libexec").mkdir(parents=True)
        (self.tree / "lib").symlink_to(ROOT / "lib")
        shutil.copy(ROOT / "libexec" / "jig-batch", self.tree / "libexec" / "jig-batch")
        double = DOUBLE.replace("@REAL_CLOSE@", str(ROOT / "libexec" / "jig-close"))
        for name in ("start", "wait", "report", "close"):
            path = self.tree / "libexec" / f"jig-{name}"
            path.write_text(double, encoding="utf-8")
            path.chmod(0o755)
        self.script("jig", INSTALLED_JIG)
        for name in ("codes", "sleep"):
            (self.fake / name).mkdir()
        self.hold_dir = self.tmp / "hold"
        self.hold_dir.mkdir()
        self.prompts = self.home / "ws" / "prompts" / "jig"
        self.prompts.mkdir(parents=True)

    def prompt(self, unit, account="implementer", role="impl"):
        (self.prompts / f"{unit}.md").write_text(
            f"# {unit}  paste into: jig-{role}-{unit} ({account}, ~/ws/src/jig-dev)\n",
            encoding="utf-8")

    def row(self, unit, minutes="30", depends="-", kind=None, account="implementer",
            role="impl"):
        self.prompt(unit, account, role)
        fields = [unit, f"~/ws/prompts/jig/{unit}.md", minutes, depends]
        return "\t".join(fields + ([kind] if kind else []))

    def control(self, name, text):
        (self.fake / name).write_text(text, encoding="utf-8")

    def batch(self, *rows, delay="0", **extra):
        path = self.tmp / "batch.tsv"
        path.write_text("".join(row + "\n" for row in rows), encoding="utf-8")
        env = self.base_env(DOUBLE_CLONE=str(self.seed), JIG_HOLD_DIR=str(self.hold_dir),
                            JIG_BATCH_CLOSE_DELAY=delay, **extra)
        return run([self.tree / "libexec" / "jig-batch", "jig", path], env=env)

    def calls(self):
        path = self.fake / "calls"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        return [" ".join(line.split()[:2]) if line.startswith("start ") else line
                for line in lines]

    def start_lines(self):
        return [line for line in (self.fake / "calls").read_text().splitlines()
                if line.startswith("start ")]

    def assert_stopped(self, result, code, text):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        stops = [line for line in result.stdout.splitlines() if " stop: " in line]
        self.assertEqual(len(stops), 1, result.stdout)
        self.assertIn(text, stops[0])

    def one_unit(self, unit):
        return [f"start {unit}", f"wait {unit}", f"report {unit}", f"close {unit}"]

    def earlier_unit(self, unit, report=True):
        """Put on origin a unit of an earlier batch that reported OK and was closed."""
        log = self.seed / "impl" / "log"
        log.mkdir(parents=True, exist_ok=True)
        (log / f"{unit}-report.md").write_text("OK: done\n")
        with open(log / "status.log", "a") as stream:
            stream.write(f"2026-11-01T10:00:00+00:00 {unit} start started abc\n")
        self.ok(["git", "-C", self.seed, "add", "-A"])
        self.push_subject(f"log: {unit}")
        if report:
            self.push_subject(f"report: {unit}")
        with open(log / "status.log", "a") as stream:
            stream.write(f"2026-11-01T11:00:00+00:00 {unit} end OK abc\n")
        self.ok(["git", "-C", self.seed, "add", "-A"])
        self.push_subject(f"status: {unit} end")


class BatchRunTest(BatchCase):

    def test_three_ok_units_run_in_file_order_each_reported_and_closed(self):
        # Three units in one account report OK: each is started, waited for, reported
        # and closed before the next starts, and the batch exits 0.
        result = self.batch(self.row("u1"), self.row("u2", minutes="45"), self.row("u3"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(),
                         self.one_unit("u1") + self.one_unit("u2") + self.one_unit("u3"))
        second = self.start_lines()[1]
        self.assertIn("role=impl", second)
        self.assertIn(f"prompt={self.prompts / 'u2.md'}", second)
        self.assertIn("--expected=45", second)
        lines = result.stdout.splitlines()
        self.assertTrue(lines)
        for line in lines:
            self.assertRegex(line, TIMESTAMPED)
        self.assertIn("report u2: OK", result.stdout)

    def test_the_batch_runs_the_tools_beside_it_and_never_the_installed_jig(self):
        # The installed jig on PATH would record its call and fail; the doubles in
        # the batch's own directory are the ones that run.
        result = self.batch(self.row("u1"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), self.one_unit("u1"))

    def test_the_progress_file_is_written_before_each_start_and_removed_at_exit(self):
        # Each start sees "<k>/<n> <ID>" in the batch directory; after the batch the
        # file is gone.
        result = self.batch(self.row("u1"), self.row("u2"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        starts = self.start_lines()
        self.assertTrue(starts[0].endswith("progress=1/2 u1"), starts[0])
        self.assertTrue(starts[1].endswith("progress=2/2 u2"), starts[1])
        self.assertFalse((self.batch_dir / "jig").exists())

    def test_the_progress_directory_is_made_when_it_is_missing(self):
        # A batch directory that does not exist yet is made before the first start.
        missing = self.tmp / "run" / "batch"
        result = self.batch(self.row("u1"), JIG_BATCH_DIR=str(missing))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(missing.is_dir())
        self.assertFalse((missing / "jig").exists())

    def test_an_unchanged_screen_runs_jig_wait_again_without_showing_the_screen(self):
        # Two waits end with no change on the screen; the third sees the report. The
        # batch goes on and does not print the unchanged screens.
        self.control("codes/wait-u1", "5 5 0")
        result = self.batch(self.row("u1"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["start u1", "wait u1", "wait u1", "wait u1",
                                        "report u1", "close u1"])
        self.assertEqual(result.stdout.count("no change on the screen of u1"), 2)
        self.assertNotIn("screen of u1 after exit", result.stdout)


class BatchStopTest(BatchCase):

    def test_a_needs_eric_report_stops_the_batch_and_closes_the_worker(self):
        # The first unit reports NEEDS ERIC: the second never starts, and the worker
        # that reported is still closed before the batch exits.
        self.control("status-u1", "NEEDS_ERIC:_a_choice")
        result = self.batch(self.row("u1"), self.row("u2"))
        self.assert_stopped(result, 3, "u1: NEEDS ERIC")
        self.assertEqual(self.calls(), self.one_unit("u1"))

    def test_a_failed_report_stops_the_batch_and_a_rerun_starts_that_unit_again(self):
        # jig report fails for the second unit: the batch stops before the third, and
        # leaves the second unclosed. A rerun of the same file skips the first and
        # starts the second again.
        self.control("codes/report-u2", "1")
        rows = (self.row("u1"), self.row("u2"), self.row("u3"))
        result = self.batch(*rows)
        self.assert_stopped(result, 4, "u2: jig report failed")
        self.assertEqual(self.calls(), self.one_unit("u1") + ["start u2", "wait u2",
                                                              "report u2"])
        (self.fake / "calls").unlink()
        again = self.batch(*rows)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("skipped u1: closed OK already", again.stdout)
        self.assertEqual(self.calls(), self.one_unit("u2") + self.one_unit("u3"))

    def test_a_hold_stops_before_the_next_unit_and_a_rerun_after_unhold_starts_it(self):
        # The hold is set while the first unit runs: it still reports and is closed,
        # and the second does not start. After jig unhold, a rerun skips the first
        # and starts the second.
        self.control("hold-after-u1", "")
        rows = (self.row("u1"), self.row("u2"))
        result = self.batch(*rows)
        self.assert_stopped(result, 5, "jig is held")
        self.assertEqual(self.calls(), self.one_unit("u1"))
        self.assertFalse((self.batch_dir / "jig").exists())
        unhold = run([ROOT / "libexec" / "jig-unhold", "jig"],
                     env=self.base_env(JIG_HOLD_DIR=str(self.hold_dir)))
        self.assertEqual(unhold.returncode, 0, unhold.stderr)
        (self.fake / "calls").unlink()
        again = self.batch(*rows)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("skipped u1: closed OK already", again.stdout)
        self.assertEqual(self.calls(), self.one_unit("u2"))

    def check_wait_stop(self, wait_code, batch_code, text):
        # jig wait ends with the given code on the first unit: the batch stops with
        # its own code before the second unit, and nothing is reported or closed.
        self.control("codes/wait-u1", str(wait_code))
        result = self.batch(self.row("u1"), self.row("u2"))
        self.assert_stopped(result, batch_code, f"u1: {text}")
        self.assertEqual(self.calls(), ["start u1", "wait u1"])
        return result

    def test_a_dialog_stops_the_batch_and_shows_the_screen(self):
        result = self.check_wait_stop(3, 6, "a dialog in the session")
        self.assertIn("screen of u1 after exit 3", result.stdout)

    def test_a_timeout_stops_the_batch(self):
        self.check_wait_stop(4, 7, "the timeout of 3x the expected time")

    def test_a_session_that_ended_without_a_report_stops_the_batch(self):
        self.check_wait_stop(6, 8, "the session ended without a report")

    def test_an_unreachable_account_stops_the_batch(self):
        self.check_wait_stop(7, 9, "the worker account could not be reached")

    def test_a_refusal_by_jig_wait_stops_the_batch(self):
        self.check_wait_stop(1, 10, "jig wait refused, exit 1")

    def test_a_refused_start_stops_the_batch(self):
        # jig start refuses the first unit, as it does for a unit with a live lock.
        self.control("codes/start-u1", "1")
        result = self.batch(self.row("u1"), self.row("u2"))
        self.assert_stopped(result, 11, "jig start refused u1")
        self.assertEqual(self.calls(), ["start u1"])

    def test_a_refused_close_stops_the_batch_before_the_next_start(self):
        # The close of the first worker, due before the second start in the same
        # account, is refused: the second unit does not start.
        self.control("codes/close-u1", "1")
        result = self.batch(self.row("u1"), self.row("u2"))
        self.assert_stopped(result, 12, "jig close refused u1")
        self.assertEqual(self.calls(), self.one_unit("u1"))


class BatchCloseTest(BatchCase):

    def test_the_worker_is_closed_before_the_next_start_in_its_account(self):
        # With a close delay longer than the units take, the first worker is closed
        # right before the second start in the same account; the last one is closed
        # when its delay has passed, before the batch exits.
        began = time.monotonic()
        result = self.batch(self.row("u1"), self.row("u2"), delay="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), self.one_unit("u1") + self.one_unit("u2"))
        self.assertGreaterEqual(time.monotonic() - began, 2)

    def test_a_close_in_another_account_ends_jig_wait_and_starts_it_again(self):
        # The first unit runs as specwriter, the second as implementer. The first's
        # close falls due during the second's wait: that wait is ended, the close
        # runs, and a new wait sees the report. The first wait would have slept 5 s.
        self.control("sleep/wait-u2", "5 0")
        began = time.monotonic()
        result = self.batch(self.row("u1", account="specwriter", role="spec"),
                            self.row("u2"), delay="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["start u1", "wait u1", "report u1",
                                        "start u2", "wait u2", "ended wait u2", "close u1",
                                        "wait u2", "report u2", "close u2"])
        self.assertLess(time.monotonic() - began, 5)
        self.assertIn("ended jig wait on u2 for a close that fell due", result.stdout)
        start_u2 = self.start_lines()[1]
        self.assertIn("role=impl", start_u2)


class BatchDependsTest(BatchCase):

    def test_needs_spec_blocks_only_the_rows_that_depend_on_it(self):
        # The first unit reports NEEDS SPEC. The second names it and is blocked, the
        # fourth names the second and is blocked too; the third runs. The batch ends
        # with the code for rows left.
        self.control("status-u1", "NEEDS_SPEC:_IQ-1")
        result = self.batch(self.row("u1"), self.row("u2", depends="u1"), self.row("u3"),
                            self.row("u4", depends="u3,u2"))
        self.assertEqual(result.returncode, 13, result.stdout + result.stderr)
        self.assertEqual(self.calls(), self.one_unit("u1") + self.one_unit("u3"))
        self.assertIn("blocked u2: u1 reported NEEDS SPEC", result.stdout)
        self.assertIn("blocked u4: u2 did not run", result.stdout)
        self.assertIn("not OK: u1 u2 u4", result.stdout)

    def test_a_batch_left_with_only_blocked_rows_ends_with_its_own_code(self):
        # After the NEEDS SPEC report, the only row left depends on it.
        self.control("status-u1", "NEEDS_SPEC:_IQ-2")
        result = self.batch(self.row("u1"), self.row("u2", depends="u1"))
        self.assertEqual(result.returncode, 13, result.stdout + result.stderr)
        self.assertEqual(self.calls(), self.one_unit("u1"))
        self.assertIn("stop: no runnable row remains", result.stdout)

    def test_a_unit_of_an_earlier_batch_closed_ok_counts_as_ok(self):
        # e1 was closed OK with its report filled in an earlier batch, so u1 runs.
        # e2 was closed OK but its report was never filled, so u2 does not start.
        self.earlier_unit("e1")
        self.earlier_unit("e2", report=False)
        result = self.batch(self.row("u1", depends="e1"), self.row("u2", depends="e2"))
        self.assertEqual(result.returncode, 13, result.stdout + result.stderr)
        self.assertEqual(self.calls(), self.one_unit("u1"))
        self.assertIn("not started u2: its depends-on ID e2 is not closed OK", result.stdout)


class BatchFindingsTest(BatchCase):
    """Rows of kind pre-check and review, and the stops on their findings files."""

    def checked_unit(self):
        # A pre-check row first, then the unit it checks, which waits for it.
        return self.row("c1", kind="pre-check"), self.row("u1", depends="c1", kind="unit")

    def reviewed_unit(self):
        # A unit, the review of it, then a unit that does not depend on either.
        return (self.row("u1", kind="unit"), self.row("r1", depends="u1", kind="review"),
                self.row("u2", kind="unit"))

    def test_a_pre_check_with_a_must_fix_finding_stops_the_batch_before_its_unit(self):
        # The pre-check reports OK with one must-fix finding: the batch stops with the
        # findings code, and the unit it checks never starts.
        self.control("findings-c1", MUST_FIX)
        result = self.batch(*self.checked_unit())
        self.assert_stopped(result, 15, "pre-check row c1: 1 must-fix and 0 should-fix "
                                        "findings in impl/log/c1-findings.md")
        self.assertEqual(self.calls(), self.one_unit("c1"))

    def test_a_pre_check_with_a_should_fix_finding_stops_the_batch_before_its_unit(self):
        # Should-fix findings alone stop the batch after a pre-check too.
        self.control("findings-c1", SHOULD_FIX)
        result = self.batch(*self.checked_unit())
        self.assert_stopped(result, 15, "pre-check row c1: 0 must-fix and 2 should-fix")
        self.assertEqual(self.calls(), self.one_unit("c1"))

    def test_a_pre_check_with_only_info_findings_lets_its_unit_start(self):
        # Info findings, and a heading quoted in the middle of a line, do not count:
        # the checked unit starts and the batch ends OK.
        self.control("findings-c1", INFO_ONLY)
        result = self.batch(*self.checked_unit())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), self.one_unit("c1") + self.one_unit("u1"))
        self.assertIn("findings of pre-check row c1: 0 must-fix and 0 should-fix",
                      result.stdout)

    def test_a_pre_check_whose_file_says_there_is_no_finding_lets_its_unit_start(self):
        # A findings file of one line that says there is none lets the unit start.
        self.control("findings-c1", NO_FINDING)
        result = self.batch(*self.checked_unit())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), self.one_unit("c1") + self.one_unit("u1"))

    def test_a_review_with_a_must_fix_finding_stops_the_batch(self):
        # The review of the first unit has a must-fix finding: the batch stops with the
        # findings code, and the unit after the review does not start.
        self.control("findings-r1", MUST_FIX)
        result = self.batch(*self.reviewed_unit())
        self.assert_stopped(result, 15, "review row r1: 1 must-fix and 0 should-fix")
        self.assertEqual(self.calls(), self.one_unit("u1") + self.one_unit("r1"))

    def test_a_review_with_only_should_fix_findings_lets_the_batch_go_on(self):
        # A review stops the batch on must-fix findings only, so the next unit starts.
        self.control("findings-r1", SHOULD_FIX)
        result = self.batch(*self.reviewed_unit())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), self.one_unit("u1") + self.one_unit("r1") +
                         self.one_unit("u2"))
        self.assertIn("findings of review row r1: 0 must-fix and 2 should-fix",
                      result.stdout)

    def test_a_pre_check_without_a_findings_file_stops_the_batch(self):
        # The pre-check reports OK but commits no findings file: the batch stops with
        # its own code and names the missing path; the checked unit does not start.
        result = self.batch(*self.checked_unit())
        self.assert_stopped(result, 16, "pre-check row c1: no impl/log/c1-findings.md "
                                        "on origin/")
        self.assertEqual(self.calls(), self.one_unit("c1"))

    def test_a_review_without_a_findings_file_stops_the_batch(self):
        # The same holds for a review: no findings file, no next unit.
        result = self.batch(*self.reviewed_unit())
        self.assert_stopped(result, 16, "review row r1: no impl/log/r1-findings.md")
        self.assertEqual(self.calls(), self.one_unit("u1") + self.one_unit("r1"))

    def test_a_rerun_of_the_same_file_stops_again_at_the_skipped_pre_check(self):
        # After a stop on findings the pre-check is closed OK. A rerun of the same
        # file skips it, reads its findings again and stops, so the checked unit
        # still does not start.
        self.control("findings-c1", MUST_FIX)
        rows = self.checked_unit()
        self.assert_stopped(self.batch(*rows), 15, "pre-check row c1")
        (self.fake / "calls").unlink()
        again = self.batch(*rows)
        self.assert_stopped(again, 15, "pre-check row c1: 1 must-fix and 0 should-fix")
        self.assertIn("skipped pre-check c1: closed OK already", again.stdout)
        self.assertEqual(self.calls(), [])

    def test_a_rerun_of_the_same_file_stops_again_at_the_skipped_review(self):
        # The same rerun with a review that had a must-fix finding: the unit before it
        # and the review are skipped, and the batch stops at the review.
        self.control("findings-r1", MUST_FIX)
        rows = self.reviewed_unit()
        self.assert_stopped(self.batch(*rows), 15, "review row r1")
        (self.fake / "calls").unlink()
        again = self.batch(*rows)
        self.assert_stopped(again, 15, "review row r1: 1 must-fix")
        self.assertIn("skipped review r1: closed OK already", again.stdout)
        self.assertEqual(self.calls(), [])

    def test_a_stop_on_findings_closes_the_worker_that_reported_before_the_exit(self):
        # With a close delay of one second, the batch stops on the findings first and
        # then waits to close the pre-check's worker before it exits.
        self.control("findings-c1", MUST_FIX)
        result = self.batch(*self.checked_unit(), delay="1")
        self.assert_stopped(result, 15, "pre-check row c1")
        self.assertEqual(self.calls(), self.one_unit("c1"))
        lines = result.stdout.splitlines()
        stop = next(index for index, line in enumerate(lines) if " stop: " in line)
        closed = next(index for index, line in enumerate(lines)
                      if "closed c1 in implementer" in line)
        self.assertLess(stop, closed, result.stdout)

    def test_the_progress_file_counts_the_rows_of_every_kind(self):
        # A pre-check, its unit and a review of that unit: each start sees its place
        # among all three rows, and each kind starts with the role of its prompt.
        self.control("findings-c1", INFO_ONLY)
        self.control("findings-r1", NO_FINDING)
        result = self.batch(self.row("c1", kind="pre-check"),
                            self.row("u1", depends="c1", kind="unit"),
                            self.row("r1", depends="u1", kind="review", role="rev"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), self.one_unit("c1") + self.one_unit("u1") +
                         self.one_unit("r1"))
        starts = self.start_lines()
        self.assertTrue(starts[0].endswith("progress=1/3 c1"), starts[0])
        self.assertTrue(starts[1].endswith("progress=2/3 u1"), starts[1])
        self.assertTrue(starts[2].endswith("progress=3/3 r1"), starts[2])
        self.assertIn("role=rev", starts[2])
        self.assertIn("started pre-check c1 (1/3) in implementer", result.stdout)
        self.assertIn("started review r1 (3/3) in implementer", result.stdout)


class BatchFileTest(BatchCase):

    def assert_refused(self, result, text):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(text, result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.batch_dir / "jig").exists())

    def test_a_bad_row_is_refused_before_any_start(self):
        # Each case puts a good row first and a bad one second, and expects the
        # refusal to name line 2.
        good = self.row("u1")
        self.prompt("u2")
        cases = {
            "3 columns": "u2\t~/ws/prompts/jig/u2.md\t30",
            "where the rows before have 4": self.row("u2", kind="unit"),
            "bad ID": self.row("U-2"),
            "u1 appears twice": self.row("u1"),
            "not ~/ws/prompts/jig/u2.md": "u2\t~/ws/prompts/other/u2.md\t30\t-",
            "not a positive integer": self.row("u2", minutes="0"),
            "'x'": self.row("u2", minutes="x"),
            "bad depends-on ID ''": self.row("u2", depends="u1,"),
            "u2 depends on itself": self.row("u2", depends="u2"),
        }
        for text, bad in cases.items():
            with self.subTest(text):
                result = self.batch(good, bad)
                self.assert_refused(result, "line 2: ")
                self.assertIn(text, result.stderr)
        with self.subTest("unknown kind"):
            result = self.batch(self.row("u1", kind="unit"), self.row("u2", kind="job"))
            self.assert_refused(result, "line 2: unknown kind 'job'")

    def test_a_pre_check_row_that_no_unit_names_is_refused_before_any_start(self):
        # A pre-check that no unit row waits for is refused, also when only a review
        # row names it.
        cases = {
            "no row names it": (self.row("u1", kind="unit"),
                                self.row("c1", kind="pre-check")),
            "only a review names it": (self.row("u1", kind="unit"),
                                       self.row("c1", kind="pre-check"),
                                       self.row("r1", depends="c1,u1", kind="review")),
        }
        for name, rows in cases.items():
            with self.subTest(name):
                self.assert_refused(self.batch(*rows), "line 2: the pre-check row c1 is "
                                    "among the depends-on IDs of no row of kind unit")

    def test_a_review_row_that_names_no_unit_is_refused_before_any_start(self):
        # A review must name a unit row of the same file: naming none, only a
        # pre-check, or only a unit of an earlier batch is refused.
        self.earlier_unit("u0")
        checked = (self.row("c1", kind="pre-check"), self.row("u1", depends="c1", kind="unit"))
        cases = {
            "it names nothing": self.row("r1", kind="review"),
            "it names a pre-check": self.row("r1", depends="c1", kind="review"),
            "it names an earlier unit": self.row("r1", depends="u0", kind="review"),
        }
        for name, review in cases.items():
            with self.subTest(name):
                self.assert_refused(self.batch(*checked, review), "line 3: the review row "
                                    "r1 names no row of kind unit among its depends-on IDs")

    def test_a_pre_check_or_review_in_another_account_than_its_unit_is_refused(self):
        # The account comes from each prompt's first line; a pre-check or review row
        # whose account differs from that of a unit it belongs to is refused.
        cases = {
            "pre-check": ((self.row("c1", kind="pre-check", account="specwriter",
                                    role="spec"),
                           self.row("u1", depends="c1", kind="unit")),
                          "line 1: the pre-check row c1 runs in specwriter, but the unit "
                          "u1 it belongs to runs in implementer"),
            "review": ((self.row("u1", kind="unit"),
                        self.row("u2", kind="unit", account="specwriter", role="spec"),
                        self.row("r1", depends="u1,u2", kind="review")),
                       "line 3: the review row r1 runs in implementer, but the unit u2 it "
                       "belongs to runs in specwriter"),
        }
        for name, (rows, text) in cases.items():
            with self.subTest(name):
                self.assert_refused(self.batch(*rows), text)

    def test_a_file_with_a_pre_check_row_has_five_columns_in_every_row(self):
        # A unit row of 4 columns after a pre-check row of 5 is refused.
        rows = (self.row("c1", kind="pre-check"), self.row("u1", depends="c1"))
        self.assert_refused(self.batch(*rows), "line 2: 4 columns, where the rows before "
                                               "have 5")

    def test_six_units_with_a_pre_check_each_are_not_refused(self):
        # Pre-check rows do not count toward the 6 units: twelve rows pass the file
        # check, and the hold set beforehand stops the batch before the first start.
        rows = []
        for number in range(1, 7):
            rows.append(self.row(f"c{number}", kind="pre-check"))
            rows.append(self.row(f"u{number}", depends=f"c{number}", kind="unit"))
        (self.hold_dir / "jig").write_text("held", encoding="utf-8")
        result = self.batch(*rows)
        self.assert_stopped(result, 5, "jig is held")
        self.assertEqual(self.calls(), [])
        more = rows + [self.row("c7", kind="pre-check"),
                       self.row("u7", depends="c7", kind="unit")]
        self.assert_refused(self.batch(*more), "line 14: more than 6 rows of kind unit")

    def test_the_help_text_names_the_findings_file_and_every_exit_code(self):
        # jig batch --help shows the row kinds, the findings file and the codes of the
        # two stops on findings.
        result = run([self.tree / "libexec" / "jig-batch", "--help"], env=self.base_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("pre-check", "review", "impl/log/<ID>-findings.md",
                     "### F-<n> must-fix", "### F-<n> should-fix", " 15  ", " 16  "):
            self.assertIn(text, result.stdout)

    def test_more_than_six_units_are_refused_before_any_start(self):
        rows = [self.row(f"u{number}") for number in range(1, 8)]
        self.assert_refused(self.batch(*rows), "line 7: more than 6 rows of kind unit")

    def test_comments_and_blank_lines_are_skipped(self):
        # A comment and a blank line do not count as rows, and the line numbers of
        # a refusal still count them.
        result = self.batch("# the next row runs", "", self.row("u1"), "u2\tx")
        self.assert_refused(result, "line 4: 2 columns")

    def test_a_prompt_with_an_unreadable_first_line_is_refused_before_any_start(self):
        rows = (self.row("u1"), self.row("u2"))
        (self.prompts / "u2.md").write_text("no first line\n", encoding="utf-8")
        self.assert_refused(self.batch(*rows), "line 2: prompt: ")

    def test_a_usage_error_exits_2(self):
        result = run([self.tree / "libexec" / "jig-batch", "jig"], env=self.base_env())
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("usage: jig batch", result.stderr)
