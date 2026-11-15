import os
import pathlib
import pwd
import shlex
import signal
import sys
import threading
import time
import unittest

from tests.support import ROOT, TempTestCase, group_members, run

sys.path.insert(0, str(ROOT / "lib"))

import jig_checks  # noqa: E402

RUN_CHECK = ROOT / "libexec" / "jig-run-check"
ACCOUNT = pwd.getpwuid(os.getuid()).pw_name

# The rows the tests use, each in the account that runs the tests unless it says so.
ROWS = [
    ("passes", "echo hello from the check", ACCOUNT, "60"),
    ("fails", "echo about to fail; exit 3", ACCOUNT, "60"),
    ("slow", "sleep 60 & echo $! > child.pid; wait", ACCOUNT, "1"),
    ("no-newline", "sleep 1.2; printf 'last words'", ACCOUNT, "60"),
    ("words", "printf '%s\\n'", ACCOUNT, "60"),
    ("where", "pwd", ACCOUNT, "60"),
    ("product", "cat ../jig/marker.txt", ACCOUNT, "60"),
    ("elsewhere", "echo should not run > ran.txt", "someone-else", "60"),
]


# The heavy rows of the slot tests, in both fixture projects. Each writes into the root
# of its -dev clone, so that a test can read when its command ran.
TIMED = "date +%s.%N > started.txt; sleep 1.2; date +%s.%N > ended.txt"
HEAVY_ROWS = [
    ("build", TIMED, ACCOUNT, "60", "heavy"),
    ("capped", "sleep 60 & echo $! > child.pid; wait", ACCOUNT, "1", "heavy"),
    ("quick", "sleep 0.2; echo quick ran", ACCOUNT, "1", "heavy"),
    ("light-row", "echo light ran", ACCOUNT, "60", "light"),
    ("outer", f"{RUN_CHECK} jig inner", ACCOUNT, "30", "heavy"),
    ("inner", "echo inner ran", ACCOUNT, "30", "heavy"),
    ("stoppable", "echo $$ > sh.pid; sleep 60 & echo $! > child.pid; wait", ACCOUNT, "60",
     "heavy"),
    # The shell's parent is jig run-check itself, so the test can signal it.
    ("light-stoppable",
     "echo $$ > sh.pid; echo $PPID > runner.pid; sleep 60 & echo $! > child.pid; wait",
     ACCOUNT, "60", "light"),
    ("big", "echo should not run > ran.txt", ACCOUNT, "60", "huge"),
    ("six", "echo should not run > ran.txt", ACCOUNT, "60", "heavy", "extra"),
]


def runs(pid):
    """Say whether a process still runs; a zombie has ended and only waits to be reaped."""
    # In a container whose first process does not reap orphans, a killed child stays a
    # zombie, and its /proc entry stays with it.
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


class RunCheckCase(TempTestCase):
    """A -dev root with checks.tsv, its conf file, and a private tmux server for the unit."""

    def setUp(self):
        super().setUp()
        self.dev = self.tmp / "jig-dev"
        self.dev.mkdir()
        lines = ["name\tcommand\taccount\tcap_seconds"] + ["\t".join(row) for row in ROWS]
        (self.dev / "checks.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.conf = self.home / ".config" / "jig" / "jig.conf"
        self.conf.parent.mkdir(parents=True)
        self.write_conf(ACCOUNT)
        self.srv = self.tmp / "srv"
        self.reviews = self.home / "ws" / "reviews" / "jig"
        self.tmux_dir = self.tmp / "tmux"
        self.tmux_dir.mkdir()
        self.runs = 0
        self.addCleanup(self.end_tmux)

    def write_conf(self, implementer):
        self.conf.write_text(f"REPO={self.dev}\nBRANCH=master\nIMPL_ACCOUNT={implementer}\n",
                             encoding="utf-8")

    def tmux_env(self):
        return self.env(TMUX_TMPDIR=str(self.tmux_dir), JIG_SRV=str(self.srv))

    def end_tmux(self):
        run(["tmux", "kill-server"], env=self.tmux_env())

    def in_session(self, session, *args):
        """Run jig run-check inside a tmux session of that name; return code, stdout, stderr."""
        return self.finish(self.start(session, *args))

    def start(self, session, *args, umask=None):
        """Start jig run-check inside a tmux session of that name, and return at once.

        The result is the place of its output files, for finish.
        """
        self.runs += 1
        out = self.tmp / f"run{self.runs}"
        environment = " ".join(f"{key}={shlex.quote(value)}"
                               for key, value in self.tmux_env().items() if key.isidentifier())
        script = self.tmp / f"run{self.runs}.sh"
        script.write_text(
            f"#!/bin/sh\nexport {environment}\n"
            + (f"umask {umask}\n" if umask else "")
            + f"{shlex.join([str(RUN_CHECK), *args])} > {out}.out 2> {out}.err\n"
            f"echo $? > {out}.part && mv {out}.part {out}.code\n", encoding="utf-8")
        script.chmod(0o755)
        started = run(["tmux", "new-session", "-d", "-s", session, str(script)],
                      env=self.tmux_env())
        self.assertEqual(started.returncode, 0, started.stderr)
        return out

    def finish(self, out):
        """Wait for the run that start began; return its code, stdout and stderr."""
        code = pathlib.Path(f"{out}.code")
        for _ in range(600):
            if code.exists():
                break
            time.sleep(0.05)
        else:
            self.fail(f"jig run-check did not finish: {out}")
        return (int(code.read_text(encoding="utf-8")),
                pathlib.Path(f"{out}.out").read_text(encoding="utf-8"),
                pathlib.Path(f"{out}.err").read_text(encoding="utf-8"))

    def recorded(self, name, unit="demo"):
        return (self.reviews / f"{unit}-{name}.exit").read_text(encoding="utf-8")

    def output(self, name, unit="demo"):
        return (self.reviews / f"{unit}-{name}.txt").read_text(encoding="utf-8")

    def assert_refused(self, result, code=1):
        """A refusal says why, and leaves no exit file and no output behind."""
        self.assertEqual(result[0], code, result)
        self.assertIn("jig run-check", result[2])
        found = sorted(path.name for path in self.reviews.glob("*")) \
            if self.reviews.exists() else []
        self.assertEqual(found, [])


class RunCheckResultTest(RunCheckCase):
    def test_a_passing_check_records_zero_and_its_output(self):
        # The row's command succeeds: the exit file holds 0 alone, the output file its text.
        result = self.in_session("jig-impl-demo", "jig", "passes")
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.recorded("passes"), "0\n")
        self.assertEqual(self.output("passes").splitlines()[0], "hello from the check")

    def test_a_failing_check_records_its_code_and_exits_with_it(self):
        result = self.in_session("jig-impl-demo", "jig", "fails")
        self.assertEqual(result[0], 3, result)
        self.assertEqual(self.recorded("fails"), "3\n")
        self.assertIn("about to fail", self.output("fails"))

    def test_a_check_past_its_cap_is_killed_with_its_children_and_records_124(self):
        # The command and the child it started both sleep past the one-second cap.
        began = time.monotonic()
        result = self.in_session("jig-impl-demo", "jig", "slow")
        self.assertLess(time.monotonic() - began, 20)
        self.assertEqual(result[0], 124, result)
        self.assertEqual(self.recorded("slow"), "124\n")
        child = int((self.dev / "child.pid").read_text(encoding="utf-8"))
        for _ in range(50):
            if not runs(child):
                break
            time.sleep(0.1)
        self.assertFalse(runs(child), "the child still runs")

    def test_the_output_ends_with_the_wall_time_on_a_line_of_its_own(self):
        # The command's last line has no newline; the wall line still stands alone.
        result = self.in_session("jig-impl-demo", "jig", "no-newline")
        self.assertEqual(result[0], 0, result)
        lines = self.output("no-newline").splitlines()
        self.assertEqual(lines[-2], "last words")
        self.assertRegex(lines[-1], r"^wall: [0-9]+s$")
        self.assertIn(int(lines[-1][len("wall: "):-1]), (1, 2))

    def test_the_id_comes_from_the_session_name(self):
        result = self.in_session("jig-rev-ab12", "jig", "passes")
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.recorded("passes", unit="ab12"), "0\n")
        self.assertFalse((self.reviews / "demo-passes.exit").exists())

    def test_words_after_the_name_reach_the_command_quoted(self):
        # Each word arrives as one argument, with no shell expansion of its own.
        result = self.in_session("jig-impl-demo", "jig", "words", "two words", "$HOME", "a;b")
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.output("words").splitlines()[:3], ["two words", "$HOME", "a;b"])

    def test_the_command_runs_in_the_root_of_the_dev_clone(self):
        self.assertEqual(self.in_session("jig-impl-demo", "jig", "where")[0], 0)
        self.assertEqual(self.output("where").splitlines()[0], str(self.dev))

    def test_a_row_names_the_product_clone_beside_the_dev_clone_as_dotdot(self):
        # The product clone is the sibling of the -dev clone, so a row that runs in the
        # root of the -dev clone reaches a product file as ../jig/<file>.
        product = self.dev.parent / "jig"
        product.mkdir()
        (product / "marker.txt").write_text("the product clone\n", encoding="utf-8")
        self.assertEqual(self.in_session("jig-impl-demo", "jig", "product")[0], 0)
        self.assertEqual(self.output("product").splitlines()[0], "the product clone")

    def test_a_new_run_replaces_the_exit_file_of_an_earlier_one(self):
        self.reviews.mkdir(parents=True)
        (self.reviews / "demo-fails.exit").write_text("0\n", encoding="utf-8")
        self.assertEqual(self.in_session("jig-impl-demo", "jig", "fails")[0], 3)
        self.assertEqual(self.recorded("fails"), "3\n")


class RunCheckRefusalTest(RunCheckCase):
    def test_refuses_outside_tmux(self):
        result = run([RUN_CHECK, "jig", "passes"], env=self.env())
        self.assert_refused((result.returncode, result.stdout, result.stderr))
        self.assertIn("not inside tmux", result.stderr)

    def test_refuses_in_a_session_of_another_project(self):
        result = self.in_session("other-impl-demo", "jig", "passes")
        self.assert_refused(result)
        self.assertIn("session of other", result[2])

    def test_refuses_in_a_session_that_is_not_a_unit(self):
        for session in ("jig-orch", "jig-impl-toolong7", "jig-boss-demo"):
            with self.subTest(session):
                self.assert_refused(self.in_session(session, "jig", "passes"))

    def test_refuses_a_row_of_another_account(self):
        result = self.in_session("jig-impl-demo", "jig", "elsewhere")
        self.assert_refused(result)
        self.assertIn("someone-else", result[2])
        self.assertFalse((self.dev / "ran.txt").exists())

    def test_refuses_an_unknown_row(self):
        result = self.in_session("jig-impl-demo", "jig", "nosuchrow")
        self.assert_refused(result)
        self.assertIn("no row nosuchrow", result[2])

    def test_a_missing_name_is_a_usage_error(self):
        result = self.in_session("jig-impl-demo", "jig")
        self.assert_refused(result, code=2)


class RunCheckCopyTest(RunCheckCase):
    """The rows run in the account of the tests, and the conf names another implementer."""

    def setUp(self):
        super().setUp()
        self.write_conf("someone-else")
        self.git_init(self.dev)
        self.git_commit_all(self.dev)
        self.head = run(["git", "-C", self.dev, "rev-parse", "HEAD"],
                        env=self.env()).stdout.strip()
        self.checks = self.srv / "checks" / "jig"
        self.checks.mkdir(parents=True)

    def copy(self, name):
        return self.checks / f"{name}.exit"

    def test_row_of_another_account_writes_the_copy_with_its_code_and_the_head(self):
        # A pass of an earlier run waits in the copy; the row now fails with 3. The copy
        # then holds 3 and the full head commit of the -dev clone, the local exit file
        # holds 3, the run exits 3, and no partial file is left in the directory.
        self.copy("fails").write_text(f"0\n{self.head}\n", encoding="utf-8")
        result = self.in_session("jig-impl-demo", "jig", "fails")
        self.assertEqual(result[0], 3, result)
        self.assertEqual(self.copy("fails").read_text(encoding="utf-8"), f"3\n{self.head}\n")
        self.assertRegex(self.head, r"^[0-9a-f]{40}$")
        self.assertEqual(self.recorded("fails"), "3\n")
        self.assertEqual(sorted(path.name for path in self.checks.iterdir()), ["fails.exit"])
        self.assertIn(str(self.copy("fails")), result[1])

    def test_row_of_the_implementer_account_writes_no_copy(self):
        # The conf names the account of the tests as the implementer: the row passes,
        # its local exit file holds 0, and the checks directory stays empty.
        self.write_conf(ACCOUNT)
        result = self.in_session("jig-impl-demo", "jig", "passes")
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.recorded("passes"), "0\n")
        self.assertEqual(list(self.checks.iterdir()), [])

    def test_conf_without_an_implementer_account_means_the_implementer(self):
        # A conf file that names no IMPL_ACCOUNT stands for the account implementer, so
        # a row of the account of the tests writes a copy unless the tests run as it.
        self.conf.write_text(f"REPO={self.dev}\nBRANCH=master\n", encoding="utf-8")
        result = self.in_session("jig-impl-demo", "jig", "passes")
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.copy("passes").exists(), ACCOUNT != "implementer")

    def test_copy_that_cannot_be_written_leaves_the_exit_file_and_exits_1(self):
        # The project's checks directory is missing. The row passes, so the local exit
        # file holds 0, but the run exits 1 and names the copy it could not write.
        self.checks.rmdir()
        result = self.in_session("jig-impl-demo", "jig", "passes")
        self.assertEqual(result[0], 1, result)
        self.assertEqual(self.recorded("passes"), "0\n")
        self.assertIn(f"cannot write {self.copy('passes')}", result[2])
        self.assertFalse(self.checks.exists())

    def test_clone_without_a_head_commit_writes_no_copy_and_exits_1(self):
        # The -dev root is not a git clone, so the copy cannot name the commit of the
        # run: the local exit file is written, no copy is, and the run exits 1.
        run(["rm", "-rf", self.dev / ".git"])
        result = self.in_session("jig-impl-demo", "jig", "passes")
        self.assertEqual(result[0], 1, result)
        self.assertEqual(self.recorded("passes"), "0\n")
        self.assertIn("cannot read the head commit", result[2])
        self.assertEqual(list(self.checks.iterdir()), [])


class RunCheckTermsTest(RunCheckCase):
    """The row terms names an implementer account that is not the account of the tests."""

    def setUp(self):
        super().setUp()
        rows = [*ROWS, ("terms", "echo terms ran", "the-implementer", "60"),
                ("other", "echo should not run > ran.txt", "the-implementer", "60")]
        lines = ["name\tcommand\taccount\tcap_seconds"] + ["\t".join(row) for row in rows]
        (self.dev / "checks.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.checks = self.srv / "checks" / "jig"
        self.checks.mkdir(parents=True)

    def write_accounts(self, spec, implementer):
        self.conf.write_text(f"REPO={self.dev}\nBRANCH=master\nIMPL_ACCOUNT={implementer}\n"
                             f"SPEC_ACCOUNT={spec}\n", encoding="utf-8")

    def test_terms_runs_in_the_spec_writer_account_and_writes_no_copy(self):
        # The conf names the account of the tests as the spec writer. The row terms runs
        # there, records 0, and leaves no copy, since its own account is the implementer.
        self.write_accounts(ACCOUNT, "the-implementer")
        result = self.in_session("jig-spec-demo", "jig", "terms")
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.recorded("terms"), "0\n")
        self.assertIn("terms ran", self.output("terms"))
        self.assertEqual(list(self.checks.iterdir()), [])

    def test_another_row_of_the_implementer_stays_in_its_own_account(self):
        # Only the row terms moves: a run of another implementer row in the spec writer's
        # account is refused and runs nothing.
        self.write_accounts(ACCOUNT, "the-implementer")
        result = self.in_session("jig-spec-demo", "jig", "other")
        self.assert_refused(result)
        self.assertFalse((self.dev / "ran.txt").exists())

    def test_terms_is_refused_in_an_account_of_neither_role(self):
        # The account names come from the conf file: when it names other accounts for
        # both roles, the account of the tests may not run the row.
        self.write_accounts("the-spec-writer", "the-implementer")
        result = self.in_session("jig-spec-demo", "jig", "terms")
        self.assert_refused(result)
        self.assertIn("the-implementer", result[2])


class RunCheckHeavyCase(RunCheckCase):
    """Two fixture projects, jig and demo, with heavy rows, a host config and slots."""

    def setUp(self):
        super().setUp()
        self.heavy = self.tmp / "heavy"
        self.other = self.tmp / "demo-dev"
        self.other.mkdir()
        for root in (self.dev, self.other):
            lines = ["name\tcommand\taccount\tcap_seconds"] + \
                ["\t".join(row) for row in [*ROWS, *HEAVY_ROWS]]
            (root / "checks.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (self.conf.parent / "demo.conf").write_text(
            f"REPO={self.other}\nBRANCH=master\nIMPL_ACCOUNT={ACCOUNT}\n", encoding="utf-8")
        self.srv.mkdir()
        self.slots(1)

    def tmux_env(self):
        return self.env(TMUX_TMPDIR=str(self.tmux_dir), JIG_SRV=str(self.srv),
                        JIG_HEAVY_DIR=str(self.heavy), JIG_HEAVY_POLL="0.1")

    def slots(self, value):
        text = "" if value is None else f"HEAVY_SLOTS={value}\n"
        (self.srv / "host.conf").write_text(f"# host config of the test\n{text}",
                                            encoding="utf-8")

    def hold(self, text="demo\tab12\tbuild\tsomeone\t-\t1\n", number=1):
        """Hold a slot from this process, as another run would; return what lets it go."""
        self.heavy.mkdir(exist_ok=True)
        slot = jig_checks.Slot(number, jig_checks.try_slot(self.heavy / str(number)))
        self.assertIsNotNone(slot.descriptor)
        self.addCleanup(slot.release)
        os.ftruncate(slot.descriptor, 0)
        os.write(slot.descriptor, text.encode())
        return slot.release

    def release_later(self, release, seconds):
        timer = threading.Timer(seconds, release)
        timer.start()
        self.addCleanup(timer.cancel)

    def times(self, root):
        return tuple(float((root / name).read_text(encoding="utf-8"))
                     for name in ("started.txt", "ended.txt"))

    def wait_for(self, path):
        for _ in range(200):
            if path.exists() and path.read_text(encoding="utf-8").strip():
                return
            time.sleep(0.05)
        self.fail(f"{path} did not appear")

    def two_builds(self):
        """Start the heavy row build of both projects at once; return both results."""
        first = self.start("jig-impl-one", "jig", "build")
        second = self.start("demo-impl-two", "demo", "build")
        results = {"jig": self.finish(first), "demo": self.finish(second)}
        return results, {"jig": self.times(self.dev), "demo": self.times(self.other)}


class RunCheckHeavyTest(RunCheckHeavyCase):
    def test_with_one_slot_two_heavy_rows_of_two_projects_run_one_after_the_other(self):
        # Both start at once and one slot is free: the one that comes second waits, says
        # whose row holds the slot and which slot it took, and starts only after the
        # first ended.
        results, times = self.two_builds()
        for project, result in results.items():
            self.assertEqual(result[0], 0, (project, result))
        first, second = sorted(times, key=lambda project: times[project][0])
        base = times[first][0]
        print(f"\none slot: {first} ran {times[first][0] - base:.2f} to "
              f"{times[first][1] - base:.2f} s, {second} ran {times[second][0] - base:.2f} to "
              f"{times[second][1] - base:.2f} s", file=sys.stderr)
        self.assertGreaterEqual(times[second][0], times[first][1])
        units = {"jig": "one", "demo": "two"}
        err = results[second][2]
        self.assertIn(f"every heavy slot is held, waiting: slot 1: project {first}, "
                      f"unit {units[first]}, row build", err)
        self.assertRegex(err, r"took heavy slot 1 after [0-9]+ s")
        self.assertNotIn("heavy slot", results[first][2])
        output = self.home / "ws" / "reviews" / second / f"{units[second]}-build.txt"
        self.assertRegex(output.read_text(encoding="utf-8").splitlines()[-2], r"^wait: [0-9]+s$")

    def test_with_two_slots_two_heavy_rows_of_two_projects_overlap(self):
        self.slots(2)
        results, times = self.two_builds()
        for project, result in results.items():
            self.assertEqual(result[0], 0, (project, result))
            self.assertNotIn("heavy slot", result[2])
        base = min(start for start, _ in times.values())
        print("\ntwo slots: " + ", ".join(
            f"{project} ran {start - base:.2f} to {end - base:.2f} s"
            for project, (start, end) in sorted(times.items())), file=sys.stderr)
        self.assertLess(times["jig"][0], times["demo"][1])
        self.assertLess(times["demo"][0], times["jig"][1])

    def test_a_heavy_row_killed_at_its_cap_frees_its_slot_for_a_waiting_row(self):
        # The first row holds the only slot and passes its cap of one second; the row of
        # the other project waits for it, and takes the slot once the first is killed.
        capped = self.start("jig-impl-one", "jig", "capped")
        self.wait_for(self.dev / "child.pid")
        waiting = self.start("demo-impl-two", "demo", "quick")
        killed, taken = self.finish(capped), self.finish(waiting)
        self.assertEqual(killed[0], 124, killed)
        self.assertEqual(taken[0], 0, taken)
        self.assertIn("slot 1: project jig, unit one, row capped", taken[2])
        self.assertRegex(taken[2], r"took heavy slot 1 after [0-9]+ s")

    def test_a_light_row_runs_at_once_while_every_slot_is_held(self):
        # The test holds the only slot; a light row, by its fifth field or with four
        # fields, runs and says nothing about slots.
        self.hold()
        for name in ("light-row", "passes"):
            with self.subTest(name):
                began = time.monotonic()
                result = self.in_session("jig-impl-demo", "jig", name)
                self.assertEqual(result[0], 0, result)
                self.assertLess(time.monotonic() - began, 5)
                self.assertNotIn("slot", result[2])

    def test_a_waiter_keeps_the_holders_text_and_names_its_holder(self):
        # The test holds the only slot with a holder line. While the row waits and polls,
        # the line stays as it was; the row's wait line names that holder.
        text = "demo\tab12\tbuild\tsomeone\t-\t1\n"
        release = self.hold(text)
        waiting = self.start("jig-impl-demo", "jig", "quick")
        time.sleep(0.8)
        self.assertEqual((self.heavy / "1").read_text(encoding="utf-8"), text)
        release()
        result = self.finish(waiting)
        self.assertEqual(result[0], 0, result)
        self.assertIn("slot 1: project demo, unit ab12, row build", result[2])

    def test_a_slot_with_no_usable_text_is_named_by_a_question_mark(self):
        self.release_later(self.hold(""), 0.5)
        result = self.in_session("jig-impl-demo", "jig", "quick")
        self.assertEqual(result[0], 0, result)
        self.assertIn("every heavy slot is held, waiting: slot 1: ?", result[2])

    def test_the_cap_of_a_waiting_row_counts_only_after_its_wait(self):
        # The row's cap is one second, and it waits about two for the slot: it still
        # passes, and its output names the wait before the wall time.
        self.release_later(self.hold(), 2)
        result = self.in_session("jig-impl-demo", "jig", "quick")
        self.assertEqual(result[0], 0, result)
        lines = self.output("quick").splitlines()
        self.assertEqual(lines[0], "quick ran")
        self.assertIn(lines[-2], ("wait: 2s", "wait: 3s"))
        self.assertRegex(lines[-1], r"^wall: [0-9]+s$")

    def test_a_row_that_did_not_wait_has_no_wait_line(self):
        result = self.in_session("jig-impl-demo", "jig", "quick")
        self.assertEqual(result[0], 0, result)
        self.assertEqual([line for line in self.output("quick").splitlines()
                          if line.startswith("wait:")], [])

    def test_a_heavy_row_inside_a_heavy_row_runs_at_once(self):
        # One slot, held by the outer row; its command runs the inner heavy row through
        # jig run-check, which would wait for ever if it waited for the slot.
        result = self.in_session("jig-impl-demo", "jig", "outer")
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.recorded("inner"), "0\n")
        self.assertIn("inner ran", self.output("inner"))
        self.assertNotIn("heavy slot", self.output("outer"))

    def test_sigterm_during_a_heavy_row_ends_its_group_and_frees_the_slot(self):
        # jig run-check gets SIGTERM while the command and its child sleep: it kills
        # their group, exits 143, and the slot is free at once for the next run. A
        # stopped run has no result, so it leaves no exit file that a report could read
        # as one.
        waiting = self.start("jig-impl-demo", "jig", "stoppable")
        self.wait_for(self.dev / "child.pid")
        holder = (self.heavy / "1").read_text(encoding="utf-8").rstrip("\n").split("\t")
        group = int((self.dev / "sh.pid").read_text(encoding="utf-8"))
        os.kill(int(holder[5]), signal.SIGTERM)
        result = self.finish(waiting)
        self.assertEqual(result[0], 143, result)
        self.assertIn("stopped by SIGTERM", result[2])
        self.assertEqual(group_members(group), [])
        self.assertFalse((self.reviews / "demo-stoppable.exit").exists())
        descriptor = jig_checks.try_slot(self.heavy / "1")
        self.assertIsNotNone(descriptor)
        os.close(descriptor)

    def test_sigterm_during_a_light_row_ends_its_group_and_writes_no_exit_file(self):
        # A light row takes no slot, but a signal still must not leave its command
        # running on into its output file: jig run-check kills the group, exits 143 and
        # writes no exit file.
        running = self.start("jig-impl-demo", "jig", "light-stoppable")
        self.wait_for(self.dev / "child.pid")
        group = int((self.dev / "sh.pid").read_text(encoding="utf-8"))
        os.kill(int((self.dev / "runner.pid").read_text(encoding="utf-8")), signal.SIGTERM)
        result = self.finish(running)
        self.assertEqual(result[0], 143, result)
        self.assertEqual(group_members(group), [])
        self.assertIn("stopped by SIGTERM", result[2])
        self.assertFalse((self.reviews / "demo-light-stoppable.exit").exists())

    def test_the_slot_directory_and_file_are_made_for_every_account(self):
        # Under a umask that keeps files private, the missing directory gets mode 2775
        # and the slot file 664, and the file holds this run's holder line.
        result = self.finish(self.start("jig-impl-demo", "jig", "quick", umask="077"))
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.heavy.stat().st_mode & 0o7777, 0o2775)
        self.assertEqual((self.heavy / "1").stat().st_mode & 0o7777, 0o664)
        fields = (self.heavy / "1").read_text(encoding="utf-8").rstrip("\n").split("\t")
        self.assertEqual(fields[:4], ["jig", "demo", "quick", ACCOUNT])
        self.assertRegex(fields[5], r"^[0-9]+$")


class RunCheckSlotsTest(RunCheckHeavyCase):
    """The number of slots that host.conf gives."""

    def test_with_no_key_a_heavy_row_runs_at_once_and_says_nothing_of_slots(self):
        # The test holds slot 1; with no limit, the row does not look at it.
        self.hold()
        for text in ("# no key\n", None):
            with self.subTest(text):
                if text is None:
                    (self.srv / "host.conf").unlink()
                else:
                    (self.srv / "host.conf").write_text(text, encoding="utf-8")
                result = self.in_session("jig-impl-demo", "jig", "quick")
                self.assertEqual(result[0], 0, result)
                self.assertNotIn("slot", result[2])

    def test_with_two_slots_and_one_held_a_heavy_row_takes_the_second(self):
        self.slots(2)
        self.hold()
        result = self.in_session("jig-impl-demo", "jig", "quick")
        self.assertEqual(result[0], 0, result)
        self.assertNotIn("slot", result[2])
        fields = (self.heavy / "2").read_text(encoding="utf-8").split("\t")
        self.assertEqual(fields[:3], ["jig", "demo", "quick"])

    def test_a_bad_number_of_slots_refuses_a_heavy_row_and_not_a_light_one(self):
        for value in ("0", "two"):
            with self.subTest(value):
                self.slots(value)
                result = self.in_session("jig-impl-demo", "jig", "quick")
                self.assert_refused(result)
                self.assertIn(f"{self.srv / 'host.conf'}: HEAVY_SLOTS='{value}'", result[2])
                light = self.in_session("jig-impl-demo", "jig", "light-row")
                self.assertEqual(light[0], 0, light)
                (self.reviews / "demo-light-row.exit").unlink()
                (self.reviews / "demo-light-row.txt").unlink()

    @unittest.skipIf(os.geteuid() == 0, "root reads a file of any mode")
    def test_an_unreadable_host_conf_refuses_a_heavy_row_and_not_a_light_one(self):
        # host.conf exists but this account cannot read it: a heavy row cannot know
        # the host's limit, so it refuses and names the file; a light row never reads
        # the file and runs.
        path = self.srv / "host.conf"
        path.chmod(0)
        result = self.in_session("jig-impl-demo", "jig", "quick")
        self.assert_refused(result)
        self.assertIn(f"cannot read {path}", result[2])
        light = self.in_session("jig-impl-demo", "jig", "light-row")
        self.assertEqual(light[0], 0, light)
        self.assertEqual(self.recorded("light-row"), "0\n")

    def test_a_weight_other_than_heavy_or_light_and_a_sixth_field_are_refused(self):
        for name in ("big", "six"):
            with self.subTest(name):
                result = self.in_session("jig-impl-demo", "jig", name)
                self.assert_refused(result)
                self.assertIn(f"the row {name} of", result[2])
                self.assertIn("heavy or light", result[2])
                self.assertFalse((self.dev / "ran.txt").exists())
