import os
import pathlib
import shutil
import sys
import threading
import unittest

from tests import clone_cases
from tests.support import ROOT, TempTestCase, run

sys.path.insert(0, str(ROOT / "lib"))

import jig_checks  # noqa: E402

HOOK = ROOT / "hooks" / "pre-commit"
HEADER = "name\tcommand\taccount\tcap_seconds"
# What the real term check prints for a match: the place, and never the term.
PLACE = "demo/notes.txt:3: terms.txt line 1"


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
            extra.update(JIG_SRV=str(self.srv_dir), JIG_HEAVY_DIR=str(self.heavy),
                         JIG_HEAVY_POLL="0.1")
        return super().env(**extra)

    def hold(self, seconds):
        """Hold the only slot from this process, and let it go after that many seconds."""
        slot = jig_checks.Slot(1, jig_checks.try_slot(self.heavy / "1"))
        self.assertIsNotNone(slot.descriptor)
        self.addCleanup(slot.release)
        os.write(slot.descriptor, b"other\tab12\tbuild\tsomeone\t-\t1\n")
        timer = threading.Timer(seconds, slot.release)
        timer.start()
        self.addCleanup(timer.cancel)

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


class PreCommitSharedLayoutTest(TempTestCase):
    """The hook finds the -dev clone by the rule that lib/jig_clones.py holds."""

    def test_hook_runs_the_term_check_of_the_dev_clone_of_each_shared_layout(self):
        # Each -dev clone of the layout has a row terms that writes where it ran. A
        # commit in the start clone runs the row of the -dev clone that the case
        # expects; with no -dev clone, the commit is refused with one line.
        for number, case in enumerate(clone_cases.FROM_PRODUCT):
            with self.subTest(case=case.name):
                base = clone_cases.build(self.tmp / f"case{number}", case.layout)
                for relative in clone_cases.LAYOUTS[case.layout]:
                    if relative.endswith("-dev"):
                        rows = [HEADER, "terms\tpwd > ran-in.txt\timplementer\t60"]
                        (base / relative / "checks.tsv").write_text("\n".join(rows) + "\n",
                                                                   encoding="utf-8")
                product = self.git_init(base / case.start)
                (product / "notes.txt").write_text("first\n", encoding="utf-8")
                hook = product / ".git" / "hooks" / "pre-commit"
                hook.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(HOOK, hook)
                git = ["git", "-C", product]
                run([*git, "add", "-A"], env=self.env())
                result = run([*git, "commit", "-q", "-m", "Change the notes"], env=self.env())
                if case.expected is None:
                    self.assertNotEqual(result.returncode, 0)
                    refusals = [line for line in result.stderr.splitlines()
                                if line.startswith("jig pre-commit hook:")]
                    self.assertEqual(len(refusals), 1, result.stderr)
                    self.assertIn("no -dev clone beside", refusals[0])
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                ran = sorted(path.parent for path in base.rglob("ran-in.txt"))
                self.assertEqual(ran, [base / case.expected])
                ran_in = (base / case.expected / "ran-in.txt").read_text(encoding="utf-8")
                self.assertEqual(os.path.realpath(ran_in.strip()),
                                 os.path.realpath(base / case.expected))


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
