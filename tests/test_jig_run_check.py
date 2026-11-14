import os
import pathlib
import pwd
import shlex
import time

from tests.support import ROOT, TempTestCase, run

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
        self.runs += 1
        out = self.tmp / f"run{self.runs}"
        environment = " ".join(f"{key}={shlex.quote(value)}"
                               for key, value in self.tmux_env().items() if key.isidentifier())
        script = self.tmp / f"run{self.runs}.sh"
        script.write_text(
            f"#!/bin/sh\nexport {environment}\n"
            f"{shlex.join([str(RUN_CHECK), *args])} > {out}.out 2> {out}.err\n"
            f"echo $? > {out}.part && mv {out}.part {out}.code\n", encoding="utf-8")
        script.chmod(0o755)
        started = run(["tmux", "new-session", "-d", "-s", session, str(script)],
                      env=self.tmux_env())
        self.assertEqual(started.returncode, 0, started.stderr)
        code = pathlib.Path(f"{out}.code")
        for _ in range(600):
            if code.exists():
                break
            time.sleep(0.05)
        else:
            self.fail(f"jig run-check did not finish in the session {session}")
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
