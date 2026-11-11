import os
import shutil
import unittest

from tests.support import ROOT, TempTestCase, run

MAKE = shutil.which("make")


class MakeCheckTest(TempTestCase):
    def make_deps(self, *present):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name in present:
            os.symlink(shutil.which(name), bin_dir / name)
        return run([MAKE, "-C", ROOT, "deps"], env=self.env(PATH=str(bin_dir)))

    def test_make_check_fails_when_shellcheck_is_missing(self):
        result = self.make_deps("git", "python3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shellcheck is missing", result.stderr)

    def test_make_check_fails_when_python_is_missing(self):
        result = self.make_deps("git", "shellcheck")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("python3 is missing", result.stderr)

    def test_make_check_passes_deps_when_all_are_present(self):
        result = self.make_deps("git", "python3", "shellcheck", "ssh-keygen", "tmux")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_make_check_fails_when_ssh_keygen_is_missing(self):
        result = self.make_deps("git", "python3", "shellcheck", "tmux")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ssh-keygen is missing", result.stderr)

    def test_make_check_fails_when_tmux_is_missing(self):
        result = self.make_deps("git", "python3", "shellcheck", "ssh-keygen")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tmux is missing", result.stderr)

    def test_make_check_runs_every_stage_after_the_dependencies(self):
        text = (ROOT / "Makefile").read_text()
        self.assertIn("\ncheck: deps hook test shellcheck lint-a\n", text)


class CheckWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    def test_the_one_workflow_is_ci_yml_named_ci_as_in_every_new_project(self):
        # jig release verify reads the badge of ci.yml, the file that jig new gives
        # every project, so jig's own workflow has that file name and that name, and
        # no second workflow stands beside it.
        names = sorted(path.name for path in (ROOT / ".github" / "workflows").iterdir())
        self.assertEqual(names, ["ci.yml"])
        self.assertEqual(self.text.splitlines()[0], "name: ci")

    def test_make_check_workflow_runs_on_every_push(self):
        self.assertIn("\non: push\n", self.text)

    def test_make_check_workflow_runs_in_debian_13(self):
        self.assertIn("container: debian:13\n", self.text)

    def test_make_check_workflow_installs_the_dependencies(self):
        install = next(line for line in self.text.splitlines() if "apt-get install" in line)
        for package in ("git", "make", "python3", "shellcheck", "tmux"):
            self.assertIn(f" {package}", install)

    def test_make_check_workflow_runs_make_check(self):
        self.assertIn("run: make check\n", self.text)

    def test_make_check_workflow_holds_no_secret(self):
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("github.token", self.text)


if __name__ == "__main__":
    unittest.main()
