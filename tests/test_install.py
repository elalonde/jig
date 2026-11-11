import filecmp
import shutil
import stat
import unittest

from tests.support import ROOT, TempTestCase, run

INSTALL = ROOT / "tools" / "install.sh"


class InstallTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.git_init(self.tmp / "source")
        (self.source / "bin").mkdir()
        (self.source / "libexec").mkdir()
        shutil.copy(ROOT / "bin" / "jig", self.source / "bin")
        shutil.copy(ROOT / "libexec" / "jig-which", self.source / "libexec")
        shutil.copy(ROOT / "prefs.md", self.source)
        self.git_commit_all(self.source)
        self.root = self.tmp / "opt"

    def install(self, version="0.1.0"):
        return run([INSTALL, self.source, self.root, version], env=self.env())

    def test_install_makes_the_version_and_sets_current(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "0.1.0" / "bin" / "jig").is_file())
        self.assertEqual((self.root / "current").resolve(), (self.root / "0.1.0").resolve())

    def test_install_leaves_the_tree_readable_by_other_accounts(self):
        self.assertEqual(self.install().returncode, 0)
        mode = (self.root / "0.1.0").stat().st_mode
        self.assertTrue(mode & stat.S_IROTH and mode & stat.S_IXOTH)

    def test_install_keeps_the_executable_bit(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertTrue((self.root / "0.1.0" / "bin" / "jig").stat().st_mode & stat.S_IXUSR)

    def test_install_is_safe_to_run_twice(self):
        self.assertEqual(self.install().returncode, 0)
        marker = (self.root / "0.1.0" / "bin" / "jig").stat().st_mtime_ns
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already installed", result.stdout)
        self.assertEqual((self.root / "0.1.0" / "bin" / "jig").stat().st_mtime_ns, marker)
        self.assertEqual((self.root / "current").resolve(), (self.root / "0.1.0").resolve())

    def test_install_leaves_no_scratch_behind(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.install().returncode, 0)
        names = sorted(path.name for path in self.root.iterdir())
        self.assertEqual(names, ["0.1.0", "current"])

    def test_install_refuses_to_replace_a_version_with_other_content(self):
        self.assertEqual(self.install().returncode, 0)
        (self.source / "added").write_text("new content\n")
        self.git_commit_all(self.source)
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("never replaced", result.stderr)
        self.assertFalse((self.root / "0.1.0" / "added").exists())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["0.1.0", "current"])

    def test_install_moves_current_to_the_new_version(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.install("0.2.0").returncode, 0)
        self.assertEqual((self.root / "current").resolve(), (self.root / "0.2.0").resolve())
        self.assertTrue((self.root / "0.1.0").is_dir())

    def test_install_of_an_older_line_leaves_current_at_the_newest(self):
        self.assertEqual(self.install("0.2.0").returncode, 0)
        result = self.install("0.1.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("current stays at 0.2.0", result.stdout)
        self.assertTrue((self.root / "0.1.1").is_dir())
        self.assertEqual((self.root / "current").resolve(), (self.root / "0.2.0").resolve())
        # Numbers, not letters, decide which is newest.
        self.assertEqual(self.install("0.10.0").returncode, 0)
        self.assertEqual((self.root / "current").resolve(), (self.root / "0.10.0").resolve())

    def test_install_twice_ignores_byte_code_in_the_installed_tree(self):
        self.assertEqual(self.install().returncode, 0)
        cache = self.root / "0.1.0" / "libexec" / "__pycache__"
        cache.mkdir()
        (cache / "jig_x.cpython-313.pyc").write_bytes(b"\0")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already installed", result.stdout)

    def test_installed_python_tool_leaves_no_byte_code(self):
        (self.source / "lib").mkdir()
        (self.source / "lib" / "probe_lib.py").write_text("WORD = 'probe ok'\n")
        tool = self.source / "libexec" / "jig-probe"
        tool.write_text("#!/usr/bin/env python3\nimport pathlib, sys\n"
                        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'lib'))\n"
                        "import probe_lib\nprint(probe_lib.WORD)\n")
        tool.chmod(0o755)
        self.git_commit_all(self.source)
        self.assertEqual(self.install().returncode, 0)
        result = run(["jig", "probe"], cwd=self.tmp, env=self.env(
            JIG_ROOT=str(self.root), PATH=f"{self.root / 'current' / 'bin'}:/usr/bin:/bin"))
        self.assertEqual(result.stdout.strip(), "probe ok", result.stderr)
        self.assertEqual(list((self.root / "0.1.0").rglob("__pycache__")), [])

    def test_install_refuses_a_bad_version(self):
        for version in ("current", "../0.1.0", "1.2"):
            self.assertEqual(self.install(version).returncode, 2, version)
        self.assertFalse(self.root.exists())

    def test_install_fails_when_the_source_is_not_a_clone(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        result = run([INSTALL, empty, self.root, "0.1.0"], env=self.env())
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "0.1.0").exists())

    def make_package(self, version="0.1.0"):
        """Commit VERSION in the fixture clone and package that commit as make package does."""
        (self.source / "VERSION").write_text(f"{version}\n")
        self.git_commit_all(self.source)
        made = run(["python3", "-c",
                    "import pathlib, sys; sys.path.insert(0, sys.argv[1]); import jig_package; "
                    "print(jig_package.package(pathlib.Path(sys.argv[2])))",
                    ROOT / "lib", self.source], env=self.env())
        self.assertEqual(made.returncode, 0, made.stderr)
        return self.source / "dist" / f"jig-{version}.tar.gz"

    def test_install_of_a_package_file_makes_the_version_and_sets_current(self):
        package = self.make_package()
        result = run([INSTALL, package, self.root, "0.1.0"], env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "0.1.0" / "VERSION").read_text(), "0.1.0\n")
        self.assertTrue((self.root / "0.1.0" / "bin" / "jig").stat().st_mode & stat.S_IXUSR)
        self.assertEqual((self.root / "current").resolve(), (self.root / "0.1.0").resolve())

    def test_install_from_a_package_and_from_a_clone_of_one_commit_gives_the_same_tree(self):
        # The same commit, installed once from its clone and once from its package
        # into another root, gives two equal trees; installing the package again
        # over the clone's install is then the no-op of a second install.
        package = self.make_package()
        other = self.tmp / "other"
        self.assertEqual(self.install().returncode, 0)
        result = run([INSTALL, package, other, "0.1.0"], env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        compared = filecmp.dircmp(self.root / "0.1.0", other / "0.1.0")
        self.assertEqual(self.differences(compared), [])
        result = run([INSTALL, package, self.root, "0.1.0"], env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already installed", result.stdout)

    def differences(self, compared, prefix=""):
        found = [prefix + name for name in
                 compared.left_only + compared.right_only + compared.diff_files + compared.funny_files]
        for name, sub in compared.subdirs.items():
            found.extend(self.differences(sub, f"{prefix}{name}/"))
        return found

    def test_install_refuses_a_package_of_another_version(self):
        package = self.make_package("0.2.0")
        result = run([INSTALL, package, self.root, "0.1.0"], env=self.env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("is version 0.2.0, not 0.1.0", result.stderr)
        self.assertFalse((self.root / "0.1.0").exists())

    def test_install_refuses_a_package_file_without_a_version(self):
        # A tar file that names no version is not a package of make package.
        archive = self.tmp / "jig-0.1.0.tar.gz"
        result = run(["git", "-C", self.source, "archive", "--format=tar.gz", "-o", archive, "HEAD"],
                     env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run([INSTALL, archive, self.root, "0.1.0"], env=self.env())
        self.assertEqual(result.returncode, 1)
        self.assertIn("holds no VERSION", result.stderr)
        self.assertFalse((self.root / "0.1.0").exists())

    def test_install_refuses_a_clone_whose_version_file_names_another_version(self):
        (self.source / "VERSION").write_text("0.2.0\n")
        self.git_commit_all(self.source)
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("is version 0.2.0, not 0.1.0", result.stderr)

    def test_install_result_dispatches_to_itself(self):
        self.assertEqual(self.install().returncode, 0)
        result = run(["jig", "which"], cwd=self.tmp, env=self.env(
            JIG_ROOT=str(self.root), PATH=f"{self.root / 'current' / 'bin'}:/usr/bin:/bin"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.root / "0.1.0"))

    def test_installed_version_has_the_preferences_at_its_top(self):
        # The fixture clone carries the product's preferences file where the product
        # keeps it, so the installed copy is the one jig new reads.
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual((self.root / "0.1.0" / "prefs.md").read_bytes(),
                         (ROOT / "prefs.md").read_bytes())

    # The check of the seeded breaks runs the tests in a copy of the tree without its
    # git data, where there is nothing to ask.
    @unittest.skipUnless((ROOT / ".git").exists(), "the tree is not a git clone")
    def test_preferences_file_is_tracked_in_the_product(self):
        # install.sh takes only what git tracks, so an untracked file would not reach
        # an installed version.
        # The real home, not the test's: a checkout owned by another user is trusted
        # only through the global git config there.
        result = run(["git", "-C", ROOT, "ls-files", "--error-unmatch", "prefs.md"])
        self.assertEqual((result.returncode, result.stdout), (0, "prefs.md\n"), result.stderr)


if __name__ == "__main__":
    unittest.main()
