import pathlib
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from tests.support import ROOT, clean_env, run

MAKE = shutil.which("make")
VERSION = "0.4.0"
# The files of the release Exit list, which each release carries at its top.
EXIT_LIST_FILES = ("README.md", "LICENSE", "THIRD-PARTY-NOTICES", "SOURCE-OFFER")


class ReleaseTargetsTest(unittest.TestCase):
    """The make targets of a release, run on a copy of the product made into a clone.

    The copy is made once for the class, with the version bumped and committed,
    because each test only reads it or works on files made from it.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="jig-test."))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.home = cls.tmp / "home"
        cls.home.mkdir()
        cls.product = cls.tmp / "product"
        shutil.copytree(ROOT, cls.product, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "dist"))
        # An unpacked release already holds VERSION; the bump below must write it anew.
        (cls.product / "VERSION").unlink(missing_ok=True)
        cls.git("init", "-q", "-b", "master")
        cls.git("add", "-A")
        cls.git("commit", "-q", "--no-verify", "-m", "fixture")
        cls.bump = cls.make("version-bump", f"VERSION={VERSION}")
        cls.git("add", "VERSION")
        cls.git("commit", "-q", "--no-verify", "-m", "version")
        cls.made = cls.make("package", "package-check", "source-archive")
        cls.package = cls.product / "dist" / f"jig-{VERSION}.tar.gz"
        cls.source = cls.product / "dist" / f"jig-{VERSION}-src.tar.gz"

    @classmethod
    def env(cls):
        return clean_env(HOME=str(cls.home))

    @classmethod
    def git(cls, *args):
        result = run(["git", "-C", cls.product, *args], env=cls.env())
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        return result.stdout

    @classmethod
    def make(cls, *args, directory=None):
        return run([MAKE, "-C", directory or cls.product, *args], env=cls.env())

    def tracked(self):
        return sorted(self.git("ls-files").splitlines())

    def files_in(self, archive):
        with tarfile.open(archive) as tar:
            return sorted(member.name for member in tar.getmembers() if member.isfile())

    def repack(self, name, leave_out=None):
        """Write a copy of the package under another name, without one of its files."""
        target = self.tmp / "repacked" / name
        target.parent.mkdir(exist_ok=True)
        with tarfile.open(self.package) as source, tarfile.open(target, "w:gz") as copy:
            for member in source.getmembers():
                if member.name != leave_out:
                    copy.addfile(member, source.extractfile(member) if member.isfile() else None)
        return target

    def test_version_bump_writes_the_version_as_one_line(self):
        # The class fixture ran make version-bump VERSION=0.4.0.
        self.assertEqual(self.bump.returncode, 0, self.bump.stderr)
        self.assertEqual((self.product / "VERSION").read_text(), f"{VERSION}\n")

    def test_version_bump_refuses_a_version_without_three_numbers(self):
        # A short version and a missing one are refused, and VERSION keeps its value.
        for argument in ("VERSION=0.4", "VERSION="):
            result = self.make("version-bump", argument)
            self.assertNotEqual(result.returncode, 0, argument)
            self.assertIn("VERSION=<X.Y.Z>", result.stderr)
        self.assertEqual((self.product / "VERSION").read_text(), f"{VERSION}\n")

    def test_package_and_package_check_pass_on_a_release_commit(self):
        self.assertEqual(self.made.returncode, 0, self.made.stderr)
        self.assertIn(f"jig-{VERSION}.tar.gz: ok", self.made.stdout)
        self.assertTrue(self.package.is_file())

    def test_package_holds_the_tree_of_the_release_commit_at_its_top(self):
        self.assertEqual(self.files_in(self.package), self.tracked())
        for name in (*EXIT_LIST_FILES, "CHANGELOG.md", "prefs.md", "VERSION"):
            self.assertIn(name, self.files_in(self.package))

    def test_source_archive_holds_the_same_files_under_the_version_directory(self):
        self.assertEqual(self.files_in(self.source),
                         [f"jig-{VERSION}/{name}" for name in self.tracked()])

    def test_dist_is_ignored_by_git(self):
        # The release files are written into the clone, which stays clean.
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_package_check_fails_on_a_package_without_a_file_of_the_exit_list(self):
        for name in EXIT_LIST_FILES:
            package = self.repack(f"jig-{VERSION}.tar.gz", leave_out=name)
            result = self.make("package-check", f"PACKAGE={package}")
            self.assertNotEqual(result.returncode, 0, name)
            self.assertIn(f"{name} is missing", result.stderr)

    def test_package_check_fails_when_the_version_differs_from_the_name(self):
        package = self.repack("jig-0.4.1.tar.gz")
        result = self.make("package-check", f"PACKAGE={package}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("VERSION holds '0.4.0', not 0.4.1", result.stderr)

    def test_package_refuses_a_version_bump_that_is_not_committed(self):
        # A package named for a version its commit does not hold would fail its check later.
        version_file = self.product / "VERSION"
        version_file.write_text("0.4.1\n")
        try:
            result = self.make("package")
        finally:
            version_file.write_text(f"{VERSION}\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("commit it first", result.stderr)
        self.assertFalse((self.product / "dist" / "jig-0.4.1.tar.gz").exists())

    def test_short_target_runs_in_the_unpacked_source_archive(self):
        # Outside a clone, make deps passes, make hook skips the hook without
        # making git data, and the shell files are listed as in the clone.
        unpacked = self.tmp / "unpacked"
        unpacked.mkdir()
        with tarfile.open(self.source) as tar:
            tar.extractall(unpacked, filter="tar")
        tree = unpacked / f"jig-{VERSION}"
        result = self.make("deps", directory=tree)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.make("hook", directory=tree)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not a git clone", result.stdout)
        self.assertFalse((tree / ".git").exists())
        listed = {}
        for root in (self.product, tree):
            result = subprocess.run(["python3", root / "lib" / "lint_a.py", "--shell-files", root],
                                    capture_output=True, text=True, env=self.env(), check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            listed[root] = sorted(pathlib.Path(line).relative_to(root)
                                  for line in result.stdout.splitlines())
        self.assertTrue(listed[tree])
        self.assertEqual(listed[tree], listed[self.product])


class ReleaseFilesTest(unittest.TestCase):
    def test_release_files_are_at_the_top_of_the_product(self):
        for name in (*EXIT_LIST_FILES, "CHANGELOG.md", "prefs.md"):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_change_log_entries_start_with_a_version_heading_newest_first(self):
        headings = [line[3:] for line in (ROOT / "CHANGELOG.md").read_text().splitlines()
                    if line.startswith("## ")]
        self.assertIn("0.3.0", headings)
        keys = [tuple(int(part) for part in heading.split(".")) for heading in headings]
        self.assertEqual(keys, sorted(keys, reverse=True))


if __name__ == "__main__":
    unittest.main()
