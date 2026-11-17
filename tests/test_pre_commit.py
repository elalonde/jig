import os
import pathlib
import pwd
import shutil
import signal
import subprocess
import sys
import threading
import time
import unittest

from tests import clone_cases
from tests.support import ROOT, TempTestCase, group_members, run

sys.path.insert(0, str(ROOT / "lib"))

import jig_checks  # noqa: E402
import jig_units  # noqa: E402

HOOK = ROOT / "hooks" / "pre-commit"
# jig's commit-msg hook, as jig start and jig new write it from the pre-commit hook.
MSG_HOOK_TEXT = jig_units.worker.msg_hook_text(HOOK.read_text(encoding="utf-8"))
# The private word of the fixture project, on line 2 of its terms file.
TERM = "Plonk" + "quiver"
TERM_LABEL = "terms.txt line 2"
# The row of a project that uses jig's own term check.
JIG_TERMS_ROW = ("terms", "jig terms check demo", "implementer", "60")
HEADER = "name\tcommand\taccount\tcap_seconds"
# What the real term check prints for a match: the place, and never the term.
PLACE = "demo/notes.txt:3: terms.txt line 1"
ACCOUNT = pwd.getpwuid(os.getuid()).pw_name
# The line that another run leaves in a slot it holds.
HOLDER = "other\tab12\tbuild\tsomeone\t-\t1\n"
# A check that keeps running until it is stopped, and says where its group is.
LONG = "echo $$ > sh.pid; sleep 30 & echo $! > child.pid; wait"


class PreCommitCase(TempTestCase):
    """A -dev clone with the product clone beside it, and jig's hook in the product."""

    def setUp(self):
        super().setUp()
        self.dev = self.git_init(self.tmp / "demo-dev")
        self.write_rows()
        self.git_commit_all(self.dev)
        self.product = self.git_init(self.tmp / "demo")
        (self.product / "notes.txt").write_text("first\n", encoding="utf-8")

    def write_rows(self, *rows):
        lines = [HEADER, "make-check\tmake check\timplementer\t60",
                 *("\t".join(row) for row in rows)]
        (self.dev / "checks.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def git(self, *args, cwd=None):
        return run(["git", "-C", cwd or self.product, *args], env=self.env())

    def hook_path(self):
        named = self.git("rev-parse", "--git-path", "hooks/pre-commit").stdout.strip()
        return self.product / named

    def install(self):
        path = self.hook_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HOOK, path)
        return path

    def commit(self):
        self.git("add", "-A")
        return self.git("commit", "-q", "-m", "Change the notes")

    def commits(self):
        return int(self.git("rev-list", "--count", "--all").stdout.strip() or 0)


class PreCommitTest(PreCommitCase):
    def test_hook_marks_itself_on_its_second_line(self):
        # jig start and jig new tell their own file by the second line, and the third line
        # tells a reader so.
        lines = HOOK.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[1], "# jig pre-commit hook")
        self.assertTrue(lines[2].startswith("# "))
        self.assertIn("jig start and jig new find their own file by", lines[2])
        self.assertTrue(os.access(HOOK, os.X_OK))

    def test_hook_lets_a_commit_through_when_the_term_check_passes(self):
        # The row prints where it runs and exits 0: the commit is made, and the command
        # ran from the root of the -dev clone, not from the product clone.
        self.write_rows(("terms", "pwd > ran-in.txt", "implementer", "60"))
        self.install()
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commits(), 1)
        ran_in = (self.dev / "ran-in.txt").read_text(encoding="utf-8").strip()
        self.assertEqual(os.path.realpath(ran_in), os.path.realpath(self.dev))

    def test_hook_refuses_the_commit_when_the_term_check_exits_1(self):
        # The row prints the place of a match and exits 1: nothing is committed, and git
        # shows the place with the hook's refusal.
        self.write_rows(("terms", f"echo '{PLACE}'; exit 1", "implementer", "60"))
        self.install()
        result = self.commit()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commits(), 0)
        shown = result.stdout + result.stderr
        self.assertIn(PLACE, shown)
        self.assertIn("the term check exited 1", shown)
        self.assertIn("the commit is refused", shown)

    def test_hook_refuses_the_commit_when_checks_tsv_has_no_row_terms(self):
        # Without the row there is no term check to run, and the commit does not go
        # through unchecked.
        self.install()
        result = self.commit()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commits(), 0)
        self.assertIn("has no row terms", result.stderr)

    def test_hook_refuses_the_commit_when_there_is_no_checks_tsv(self):
        (self.dev / "checks.tsv").unlink()
        self.install()
        result = self.commit()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot read", result.stderr)
        self.assertEqual(self.commits(), 0)

    def test_hook_refuses_a_product_clone_with_no_dev_clone(self):
        # A clone on its own has no -dev clone beside it or around it, so there is no
        # checks.tsv to read, and the commit is refused.
        alone = self.git_init(self.tmp / "alone")
        (alone / "notes.txt").write_text("first\n", encoding="utf-8")
        named = self.git("rev-parse", "--git-path", "hooks/pre-commit", cwd=alone).stdout
        path = alone / named.strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HOOK, path)
        self.git("add", "-A", cwd=alone)
        result = self.git("commit", "-q", "-m", "Change the notes", cwd=alone)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no -dev clone beside", result.stderr)

    def test_hook_stops_a_term_check_past_its_cap(self):
        # The row's cap is one second and the command sleeps longer: the commit is
        # refused once the cap passes.
        self.write_rows(("terms", "sleep 30", "implementer", "1"))
        self.install()
        result = self.commit()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("past its cap of 1 s", result.stderr)

    def test_hook_runs_git_of_the_check_in_the_dev_clone(self):
        # A commit with -a gives the hook a variable that names the product's temporary
        # index. The check lists the files of the -dev clone with git, and must see that
        # clone's files only.
        self.write_rows(("terms", "git ls-files > listed.txt", "implementer", "60"))
        self.git_commit_all(self.dev)
        self.git_commit_all(self.product)
        self.install()
        (self.product / "notes.txt").write_text("second\n", encoding="utf-8")
        result = self.git("commit", "-a", "-q", "-m", "Change the notes")
        self.assertEqual(result.returncode, 0, result.stderr)
        listed = (self.dev / "listed.txt").read_text(encoding="utf-8").split()
        self.assertEqual(listed, ["checks.tsv"])


class PreCommitHeavyTest(PreCommitCase):
    """The host config allows one heavy row at a time, and the test can hold that slot."""

    def setUp(self):
        self.srv_dir = None
        super().setUp()
        self.srv_dir = self.tmp / "srv"
        self.srv_dir.mkdir()
        (self.srv_dir / "host.conf").write_text("HEAVY_SLOTS=1\n", encoding="utf-8")
        self.heavy = self.tmp / "heavy"
        self.heavy.mkdir()

    def env(self, **extra):
        if self.srv_dir is not None:
            extra.update(JIG_SRV=str(self.srv_dir), JIG_HEAVY_DIR=str(self.heavy))
            extra.setdefault("JIG_HEAVY_POLL", "0.1")
        return super().env(**extra)

    def hold(self, seconds):
        """Hold the only slot from this process, and let it go after that many seconds.

        The result lets it go at once.
        """
        slot = jig_checks.Slot(1, jig_checks.try_slot(self.heavy / "1"))
        self.assertIsNotNone(slot.descriptor)
        self.addCleanup(slot.release)
        os.write(slot.descriptor, HOLDER.encode("utf-8"))
        timer = threading.Timer(seconds, slot.release)
        timer.start()
        self.addCleanup(timer.cancel)
        return slot.release

    def start(self, **extra):
        """Start the hook in the product clone, as git does, and return at once.

        What the hook says goes to a file, so that the test can read it while the hook
        still runs.
        """
        self.said_file = self.tmp / "hook.err"
        stream = open(self.said_file, "w", encoding="utf-8")
        self.addCleanup(stream.close)
        hook = subprocess.Popen([HOOK], cwd=self.product, env=self.env(**extra),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=stream)
        self.addCleanup(self.end, hook)
        return hook

    def end(self, hook):
        """Leave no hook running after a failed test."""
        if hook.poll() is None:
            hook.kill()
            hook.wait()

    def said(self):
        return self.said_file.read_text(encoding="utf-8")

    def wait_until(self, ready, what):
        for _ in range(200):
            if ready():
                return
            time.sleep(0.05)
        self.fail(f"{what} did not happen; the hook said: {self.said()!r}")

    def assert_slot_free(self):
        descriptor = jig_checks.try_slot(self.heavy / "1")
        self.assertIsNotNone(descriptor, "the slot is still held")
        os.close(descriptor)

    def test_sigterm_while_a_heavy_term_check_runs_ends_its_group_and_frees_the_slot(self):
        # The hook gets SIGTERM while the check and its child sleep: it kills their
        # group, refuses the commit with 128 plus the signal, and the slot is free at
        # once for the next run.
        self.write_rows(("terms", LONG, "implementer", "60", "heavy"))
        hook = self.start()
        child = self.dev / "child.pid"
        self.wait_until(lambda: child.exists() and child.read_text().strip(), "the check")
        group = int((self.dev / "sh.pid").read_text(encoding="utf-8"))
        hook.send_signal(signal.SIGTERM)
        self.assertEqual(hook.wait(timeout=20), 143, self.said())
        self.assertEqual(group_members(group), [])
        self.assertIn("stopped by SIGTERM; the commit is refused", self.said())
        self.assert_slot_free()

    def test_sigterm_while_the_hook_waits_for_a_slot_stops_it_without_a_slot(self):
        # The only slot is held for longer than the test runs. SIGTERM during the wait
        # ends the hook with 143; the check never ran, and the holder's line is still in
        # the slot, so the hook never took it.
        self.write_rows(("terms", "echo ran > ran.txt", "implementer", "60", "heavy"))
        release = self.hold(30)
        hook = self.start()
        self.wait_until(lambda: "every heavy slot is held" in self.said(), "the wait")
        hook.send_signal(signal.SIGTERM)
        self.assertEqual(hook.wait(timeout=20), 143, self.said())
        self.assertIn("stopped by SIGTERM; the commit is refused", self.said())
        self.assertFalse((self.dev / "ran.txt").exists())
        release()
        self.assertEqual((self.heavy / "1").read_text(encoding="utf-8"), HOLDER)
        self.assert_slot_free()

    def test_a_heavy_term_check_inside_a_heavy_row_runs_while_the_slot_is_held(self):
        # The variable says that the process tree around the hook already holds a slot,
        # as for a commit made by the command of a heavy row: the hook does not wait for
        # the slot that its own tree holds, and says nothing about slots.
        self.write_rows(("terms", "echo ran > ran.txt", "implementer", "60", "heavy"))
        self.hold(5)
        began = time.monotonic()
        hook = self.start(JIG_HEAVY_SLOT="1")
        self.assertEqual(hook.wait(timeout=20), 0, self.said())
        self.assertLess(time.monotonic() - began, 3)
        self.assertTrue((self.dev / "ran.txt").exists())
        self.assertNotIn("slot", self.said())

    @unittest.skipIf(os.geteuid() == 0, "root opens a file of any mode")
    def test_a_slot_file_that_cannot_be_opened_is_refused_after_three_more_polls(self):
        # The slot file allows no one to open it, as one of another account without
        # group write would. The hook tries again at each of the next three polls of
        # 0.2 s, then refuses the commit with a line that names the file and its owner.
        path = self.heavy / "1"
        path.write_text("", encoding="utf-8")
        path.chmod(0)
        self.write_rows(("terms", "echo ran > ran.txt", "implementer", "60", "heavy"))
        began = time.monotonic()
        hook = self.start(JIG_HEAVY_POLL="0.2")
        self.assertEqual(hook.wait(timeout=20), 1, self.said())
        self.assertGreaterEqual(time.monotonic() - began, 0.6)
        self.assertIn(f"cannot open {path}, a file of {ACCOUNT}, after 3 more tries; "
                      "the commit is refused", self.said())
        self.assertFalse((self.dev / "ran.txt").exists())

    def test_a_number_of_slots_that_is_not_a_whole_number_of_1_or_more_is_refused(self):
        self.write_rows(("terms", "echo ran > ran.txt", "implementer", "60", "heavy"))
        conf = self.srv_dir / "host.conf"
        for value in ("0", "two"):
            with self.subTest(value):
                conf.write_text(f"HEAVY_SLOTS={value}\n", encoding="utf-8")
                hook = self.start()
                self.assertEqual(hook.wait(timeout=20), 1, self.said())
                self.assertIn(f"{conf}: HEAVY_SLOTS='{value}' is not a whole number of 1 "
                              "or more; the commit is refused", self.said())
                self.assertFalse((self.dev / "ran.txt").exists())

    @unittest.skipIf(os.geteuid() == 0, "root reads a file of any mode")
    def test_an_unreadable_host_conf_refuses_a_heavy_term_check_and_not_a_light_one(self):
        # host.conf exists and this account cannot read it: the heavy check cannot
        # know the host's limit, so the commit is refused with a line that names the
        # file; a light check never reads it and runs.
        conf = self.srv_dir / "host.conf"
        conf.chmod(0)
        self.write_rows(("terms", "echo ran > ran.txt", "implementer", "60", "heavy"))
        hook = self.start()
        self.assertEqual(hook.wait(timeout=20), 1, self.said())
        self.assertIn(f"cannot read {conf}", self.said())
        self.assertFalse((self.dev / "ran.txt").exists())
        self.write_rows(("terms", "echo ran > ran.txt", "implementer", "60", "light"))
        hook = self.start()
        self.assertEqual(hook.wait(timeout=20), 0, self.said())
        self.assertTrue((self.dev / "ran.txt").exists())

    def test_a_heavy_term_check_waits_for_the_slot_and_its_cap_counts_after(self):
        # The slot is held for about 1.5 s and the row's cap is one second: the hook
        # says whom it waits for, takes the slot, and the commit goes through. The slot
        # then holds the hook's line: the project from the -dev clone's name, no unit
        # outside tmux, and the row.
        self.write_rows(("terms", "sleep 0.2", "implementer", "1", "heavy"))
        self.install()
        self.hold(1.5)
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commits(), 1)
        self.assertIn("every heavy slot is held, waiting: slot 1: project other, unit ab12, "
                      "row build", result.stderr)
        self.assertRegex(result.stderr, r"took heavy slot 1 after [0-9]+ s")
        fields = (self.heavy / "1").read_text(encoding="utf-8").split("\t")
        self.assertEqual(fields[:3], ["demo", "-", "terms"])

    def test_a_light_term_check_does_not_wait_for_a_held_slot(self):
        # The test holds the slot for longer than the test runs; a row light by its
        # fifth field, or with four fields, commits at once.
        self.hold(30)
        for weight in ("light", None):
            with self.subTest(weight=weight):
                row = ("terms", "true", "implementer", "60")
                self.write_rows(row + ((weight,) if weight else ()))
                self.install()
                (self.product / "notes.txt").write_text(f"{weight}\n", encoding="utf-8")
                result = self.commit()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("slot", result.stderr)

    def test_a_weight_other_than_heavy_or_light_and_a_sixth_field_are_refused(self):
        for extra in (("huge",), ("heavy", "extra")):
            with self.subTest(extra=extra):
                self.write_rows(("terms", "true", "implementer", "60", *extra))
                self.install()
                result = self.commit()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.commits(), 0)
                self.assertIn("the row terms of", result.stderr)
                self.assertIn("heavy or light", result.stderr)


def jig_script(log, which, other=None):
    """Return the text of a fixture jig for PATH.

    It writes each call's arguments to log, answers jig which with the shell lines of
    which, and runs any other subcommand with the lines of other, or else from this
    tree, as the dispatcher would.
    """
    run_other = other or f'sub=$1\nshift\nexec "{ROOT}/libexec/jig-$sub" "$@"'
    return (f'#!/bin/sh\necho "$*" >> "{log}"\nif [ "$1" = which ]; then\n{which}\nfi\n'
            f"{run_other}\n")


def install_hooks(env, clone, pre_commit=True, commit_msg=True):
    """Put jig's hooks where git looks for them in the clone, as jig start does."""
    for name, wanted, text in (("pre-commit", pre_commit, HOOK.read_text(encoding="utf-8")),
                               ("commit-msg", commit_msg, MSG_HOOK_TEXT)):
        named = run(["git", "-C", clone, "rev-parse", "--git-path", f"hooks/{name}"],
                    env=env).stdout.strip()
        path = pathlib.Path(clone) / named
        if not wanted:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)


class PreCommitSharedLayoutTest(TempTestCase):
    """Both hooks find the -dev clone by the rule that lib/jig_clones.py holds."""

    def setUp(self):
        super().setUp()
        # jig's term check, run by a jig that has the options for what a commit adds:
        # each run writes where it ran, beside the checks.tsv it read.
        tree = self.tmp / "opt" / "0.8.2"
        tree.mkdir(parents=True)
        (self.tmp / "bin").mkdir()
        jig = self.tmp / "bin" / "jig"
        jig.write_text(jig_script(self.tmp / "jig.log", f"echo '{tree}'\nexit 0",
                                  "pwd >> ran-in.txt"), encoding="utf-8")
        jig.chmod(0o755)

    def env(self, **extra):
        extra.setdefault("PATH", f"{self.tmp / 'bin'}:{os.environ['PATH']}")
        return super().env(**extra)

    def plant_rows(self, base, dirs):
        for relative in dirs:
            if relative.endswith("-dev") and (base / relative).is_dir():
                rows = [HEADER, "\t".join(JIG_TERMS_ROW)]
                (base / relative / "checks.tsv").write_text("\n".join(rows) + "\n",
                                                           encoding="utf-8")

    def assert_hooks_find(self, base, start, expected, why="no -dev clone beside"):
        """Commit in start with both hooks: each runs the row of the expected -dev clone
        once; with none expected, each hook alone refuses the commit with one line that
        holds why."""
        install_hooks(self.env(), start)
        git = ["git", "-C", start]
        run([*git, "add", "-A"], env=self.env())
        result = run([*git, "commit", "-q", "-m", "Change the notes"], env=self.env())
        if expected is None:
            for hook in ("pre-commit", "commit-msg"):
                install_hooks(self.env(), start, pre_commit=hook == "pre-commit",
                              commit_msg=hook == "commit-msg")
                result = run([*git, "commit", "-q", "-m", "Change the notes"], env=self.env())
                self.assertNotEqual(result.returncode, 0)
                refusals = [line for line in result.stderr.splitlines()
                            if line.startswith("jig ")]
                self.assertEqual(len(refusals), 1, result.stderr)
                self.assertTrue(refusals[0].startswith(f"jig {hook} hook: "), refusals[0])
                self.assertIn(why, refusals[0])
            self.assertEqual(list(base.rglob("ran-in.txt")), [])
            return
        self.assertEqual(result.returncode, 0, result.stderr)
        ran = sorted(path.parent for path in base.rglob("ran-in.txt"))
        self.assertEqual(ran, [base / expected])
        ran_in = (base / expected / "ran-in.txt").read_text(encoding="utf-8").split()
        self.assertEqual([os.path.realpath(line) for line in ran_in],
                         [os.path.realpath(base / expected)] * 2)

    def test_hooks_run_the_term_check_of_the_dev_clone_of_each_shared_layout(self):
        # Each -dev clone of the layout has the row terms. A commit in the start clone
        # runs the row of the -dev clone that the case expects, once for each hook;
        # with no -dev clone, each hook refuses the commit with one line.
        for number, case in enumerate(clone_cases.FROM_PRODUCT):
            with self.subTest(case=case.name):
                base = clone_cases.build(self.tmp / f"case{number}", case.layout)
                self.plant_rows(base, clone_cases.LAYOUTS[case.layout])
                product = self.git_init(base / case.start)
                (product / "notes.txt").write_text("first\n", encoding="utf-8")
                self.assert_hooks_find(base, product, case.expected)

    def test_hooks_run_the_term_check_of_the_dev_clone_of_each_git_layout(self):
        # A linked work tree in another folder runs the row of the -dev clone beside
        # its main clone; a git directory not named .git is refused with the line
        # that names it, although a -dev clone stands beside the clone.
        for number, case in enumerate(clone_cases.GIT_CASES):
            with self.subTest(case=case.name):
                base = self.tmp / f"git{number}"
                start = case.build(base, self.env())
                self.plant_rows(base, (clone_cases.DEV,))
                (start / "notes.txt").write_text("second\n", encoding="utf-8")
                why = f"the git directory {(base / 'store' / 'demo.git').resolve()} is not named .git"
                self.assert_hooks_find(base, start, case.expected, why)


class AddedOnlyCase(TempTestCase):
    """A product whose HEAD holds the fixture term, with both of jig's hooks, the row of
    jig's own term check, and a jig on PATH whose installed tree reads as 0.8.2.

    The tree is reached through a symbolic link, as /opt/jig/current is, so the hooks
    read the version only once they follow the link.
    """

    def setUp(self):
        super().setUp()
        self.dev = self.git_init(self.tmp / "demo-dev")
        (self.dev / "terms.txt").write_text(f"# fixture words\n{TERM}\n", encoding="utf-8")
        self.write_row(JIG_TERMS_ROW)
        self.git_commit_all(self.dev)
        conf = self.home / ".config" / "jig" / "demo.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f"REPO={self.dev}\nBRANCH=0.8\n", encoding="utf-8")
        self.opt = self.tmp / "opt"
        for name in ("0.8.1", "0.8.2", "0.10.0", "jig"):
            (self.opt / name).mkdir(parents=True)
        (self.opt / "current").symlink_to("0.8.2")
        (self.tmp / "bin").mkdir()
        self.log = self.tmp / "jig.log"
        self.use_which(f"echo '{self.opt / 'current'}'\nexit 0")
        self.product = self.make_product()

    def env(self, **extra):
        extra.setdefault("PATH", f"{self.tmp / 'bin'}:{os.environ['PATH']}")
        return super().env(**extra)

    def write_row(self, row):
        lines = [HEADER, "\t".join(row)]
        (self.dev / "checks.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def use_which(self, lines):
        jig = self.tmp / "bin" / "jig"
        jig.write_text(jig_script(self.log, lines), encoding="utf-8")
        jig.chmod(0o755)

    def make_product(self):
        """Make the product clone afresh: notes, a clean file, and an old match on line
        2 of old.txt, committed before the hooks are in place."""
        product = self.tmp / "demo"
        shutil.rmtree(product, ignore_errors=True)
        self.git_init(product)
        self.write({"notes.txt": "first\nsecond\n", "clean.txt": "clean\n",
                    "old.txt": f"plain\nan old {TERM}\n"}, product)
        self.git_commit_all(product)
        install_hooks(self.env(), product)
        return product

    def write(self, files, root=None):
        for name, text in files.items():
            path = (root or self.product) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(text, bytes):
                path.write_bytes(text)
            else:
                path.write_text(text, encoding="utf-8")

    def git(self, *args, cwd=None):
        return run(["git", "-C", cwd or self.product, *args], env=self.env())

    def commit(self, *args, cwd=None, message=("Change the notes",)):
        """Stage everything and commit it with the hooks; the message parts are its
        paragraphs."""
        self.git("add", "-A", cwd=cwd)
        return self.commit_only(*args, cwd=cwd, message=message)

    def commit_only(self, *args, cwd=None, message=("Change the notes",)):
        parts = [part for paragraph in message for part in ("-m", paragraph)]
        return self.git("commit", "-q", *parts, *args, cwd=cwd)

    def commits(self, cwd=None):
        return int(self.git("rev-list", "--count", "HEAD", cwd=cwd).stdout.strip() or 0)

    def calls(self):
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def places(self, result):
        """Return the lines of the check in the output, after checking that the term is
        nowhere in it."""
        shown = result.stdout + result.stderr
        self.assertNotIn(TERM.casefold(), shown.casefold())
        return [line for line in shown.splitlines()
                if line.startswith(("demo/", "message:"))]

    def assert_refused(self, result, hook, places, before):
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.places(result), places)
        self.assertIn(f"jig {hook} hook: the term check exited 1", result.stderr)
        self.assertIn("the commit is refused", result.stderr)
        self.assertEqual(self.commits(), before)


class AddedOnlyTest(AddedOnlyCase):
    def test_a_commit_that_adds_no_match_passes_while_head_holds_one(self):
        # HEAD holds the term in old.txt, and the commit changes only the notes: both
        # hooks run the check of what the commit adds, from the -dev clone, and the
        # commit is made.
        self.write({"notes.txt": "first\nsecond\nthird\n"})
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commits(), 2)
        calls = self.calls()
        self.assertIn(f"terms check demo --staged {os.path.realpath(self.product)}", calls)
        self.assertTrue(any(call.startswith("terms check demo --message /") for call in calls),
                        calls)
        self.assertNotIn("terms check demo", calls)

    def test_each_kind_of_added_match_is_refused_with_its_place_and_not_the_term(self):
        # Each change adds the term in one place: the commit is refused, the check
        # names the path and the line, or the path alone, and never the old match of
        # HEAD or the term itself.
        def added_line():
            self.write({"notes.txt": f"first\nsecond\na new {TERM}\n"})

        def changed_line():
            self.write({"notes.txt": f"first {TERM}\nsecond\n"})

        def added_path():
            self.write({f"{TERM}.txt": "clean\n"})

        def renamed_path():
            self.assertEqual(self.git("mv", "clean.txt", f"moved-{TERM}.txt").returncode, 0)

        def added_binary():
            self.write({"blob.bin": b"\x00\x01\x02\n" + TERM.encode() + b"\n"})

        cases = ((added_line, "demo/notes.txt:3: " + TERM_LABEL),
                 (changed_line, "demo/notes.txt:1: " + TERM_LABEL),
                 (added_path, "demo/***.txt: " + TERM_LABEL + " in the path"),
                 (renamed_path, "demo/moved-***.txt: " + TERM_LABEL + " in the path"),
                 (added_binary, "demo/blob.bin:2: " + TERM_LABEL))
        for change, place in cases:
            with self.subTest(change=change.__name__):
                change()
                self.assert_refused(self.commit(), "pre-commit", [place], 1)
                self.git("reset", "-q", "--hard")
                self.git("clean", "-q", "-f", "-d")

    def test_a_match_in_the_message_is_refused_with_its_line(self):
        # The change is clean and the third line of the message holds the term: the
        # commit-msg hook refuses it and names that line.
        self.write({"notes.txt": "first\nsecond\nthird\n"})
        result = self.commit(message=("Change the notes", f"Say {TERM} here."))
        self.assert_refused(result, "commit-msg", ["message:3: " + TERM_LABEL], 1)

    def test_a_moved_file_with_an_old_match_in_a_line_passes(self):
        # old.txt holds the term in a line and moves to a path without it: the move adds
        # no line, so the commit is made.
        self.assertEqual(self.git("mv", "old.txt", "old-notes.txt").returncode, 0)
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commits(), 2)

    def test_the_check_reads_the_index_that_the_commit_takes(self):
        # The term is only in the work tree's notes. A commit of another staged file
        # passes, since the default index holds no match; git commit -a and git commit
        # with the path stage the notes in an index of their own, and both are refused.
        self.write({"notes.txt": f"first\nsecond\na new {TERM}\n", "extra.txt": "extra\n"})
        self.git("add", "extra.txt")
        result = self.commit_only()
        self.assertEqual(result.returncode, 0, result.stderr)
        for args in (("-a",), ("--", "notes.txt")):
            with self.subTest(args=args):
                result = self.commit_only(*args)
                self.assert_refused(result, "pre-commit", ["demo/notes.txt:3: " + TERM_LABEL],
                                    2)

    def test_the_first_commit_of_a_repository_counts_every_line_and_path(self):
        # A repository with no commit yet: the old match is in a staged line, so the
        # first commit is refused; without that file it is made.
        shutil.rmtree(self.product / ".git")
        self.git_init(self.product)
        install_hooks(self.env(), self.product)
        self.git("add", "-A")
        result = self.commit_only()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.places(result), ["demo/old.txt:2: " + TERM_LABEL])
        (self.product / "old.txt").unlink()
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commits(), 1)

    def test_a_jig_newer_by_its_numbers_checks_only_what_the_commit_adds(self):
        # 0.10.0 is newer than 0.8.2 when the parts are compared as numbers, though not
        # as text: the commit with the old match in HEAD is made.
        self.use_which(f"echo '{self.opt / '0.10.0'}'\nexit 0")
        self.write({"notes.txt": "first\nsecond\nthird\n"})
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"terms check demo --staged {os.path.realpath(self.product)}",
                      self.calls())

    def test_an_older_jig_or_one_the_hooks_cannot_read_gets_the_whole_check(self):
        # The jig's tree reads as 0.8.1, jig which fails, or the tree's name is not a
        # version: the pre-commit hook runs the row as it is, so the old match in HEAD
        # refuses a clean change. Once HEAD is clean, a message with the term passes,
        # since the commit-msg hook then checks nothing.
        cases = (("older", f"echo '{self.opt / '0.8.1'}'\nexit 0"),
                 ("which fails", "exit 1"),
                 ("not a version", f"echo '{self.opt / 'jig'}'\nexit 0"))
        for name, which in cases:
            with self.subTest(case=name):
                self.product = self.make_product()
                self.log.unlink(missing_ok=True)
                self.use_which(which)
                self.write({"notes.txt": "first\nsecond\nthird\n"})
                self.assert_refused(self.commit(), "pre-commit",
                                    ["demo/old.txt:2: " + TERM_LABEL], 1)
                (self.product / "old.txt").unlink()
                result = self.commit(message=("Remove the old notes", f"Say {TERM}."))
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.calls()
                self.assertEqual([call for call in calls if call.startswith("terms")],
                                 ["terms check demo"] * 2)
                self.assertEqual(calls.count("which demo"), 3)

    def test_another_command_in_the_row_runs_as_it_is_and_no_message_is_checked(self):
        # A project with its own term check: the pre-commit hook runs that command once,
        # the commit-msg hook checks nothing, and the term in the message passes.
        self.write_row(("terms", "echo ran >> ran.txt", "implementer", "60"))
        self.write({"notes.txt": "first\nsecond\nthird\n"})
        result = self.commit(message=("Change the notes", f"Say {TERM}."))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.dev / "ran.txt").read_text(encoding="utf-8"), "ran\n")
        self.assertEqual(self.calls(), [])

    def test_a_linked_work_tree_in_another_folder_checks_what_its_commit_adds(self):
        # A work tree of the product in a folder that is not beside the -dev clone: a
        # clean commit passes with the work tree as the root of the check, and a match
        # in an added line or in the message is refused there.
        tree = self.tmp / "elsewhere" / "work"
        self.assertEqual(self.git("worktree", "add", "-q", "-b", "side", tree).returncode, 0)
        self.write({"notes.txt": "first\nsecond\nthird\n"}, tree)
        result = self.commit(cwd=tree)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"terms check demo --staged {os.path.realpath(tree)}", self.calls())
        self.write({"notes.txt": f"first\nsecond\nthird\n{TERM}\n"}, tree)
        refused = self.commit(cwd=tree)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(self.places(refused), ["demo/notes.txt:4: " + TERM_LABEL])
        self.write({"notes.txt": "first\nsecond\nthird\nfourth\n"}, tree)
        refused = self.commit(cwd=tree, message=("Change the notes", f"Say {TERM}."))
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(self.places(refused), ["message:3: " + TERM_LABEL])
        self.assertEqual(self.commits(cwd=tree), 2)


class PreCommitHooksPathTest(PreCommitCase):
    """The product clone sets core.hooksPath, as make check of a product does."""

    def setUp(self):
        super().setUp()
        self.git("config", "--local", "core.hooksPath", "hooks")

    def test_hook_at_the_hooks_path_runs_and_refuses(self):
        # git names hooks/pre-commit of the work tree; the hook there refuses a commit
        # whose term check exits 1, and lets one through once the check passes.
        self.assertEqual(self.hook_path(), self.product / "hooks" / "pre-commit")
        self.write_rows(("terms", f"echo '{PLACE}'; exit 1", "implementer", "60"))
        self.install()
        refused = self.commit()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(PLACE, refused.stdout + refused.stderr)
        self.assertEqual(self.commits(), 0)

        self.write_rows(("terms", "true", "implementer", "60"))
        accepted = self.commit()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(self.commits(), 1)

    def test_hook_in_the_git_directory_does_not_run_when_the_hooks_path_is_set(self):
        # A hook left at the default place is not the one git runs: only the path that
        # git names counts.
        self.write_rows(("terms", "exit 1", "implementer", "60"))
        stale = self.product / ".git" / "hooks" / "pre-commit"
        stale.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HOOK, stale)
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(pathlib.Path(self.hook_path()), stale)


if __name__ == "__main__":
    unittest.main()
