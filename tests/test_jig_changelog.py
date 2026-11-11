import os

from tests.support import ROOT, TempTestCase, run

CHANGELOG = ROOT / "libexec" / "jig-changelog"

# A change log in its form, newest first, where the patch 0.3.2 of an older line
# was released after 0.4.0 and so stands above it.
ENTRIES = """# jig change log

Some words before the first entry.

## 0.4.1

Fixed the wait.

## 0.3.2

Fixed the older line.

## 0.4.0

What changed: the release.

Requested by: Eric.

## 0.3.0

The records.

## 0.2.0

The batches.
"""


class ChangelogTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "opt"
        (self.root / "0.4.1").mkdir(parents=True)
        os.symlink("0.4.1", self.root / "current")
        self.file = self.root / "0.4.1" / "CHANGELOG.md"
        self.file.write_text(ENTRIES)

    def changelog(self, *args):
        return run([CHANGELOG, *args], env=self.env(JIG_ROOT=str(self.root)))

    def headings(self, result):
        return [line for line in result.stdout.splitlines() if line.startswith("## ")]

    def test_changelog_prints_the_entries_above_a_listed_version_in_file_order(self):
        # 0.3.2 is lower than 0.4.0 but came after it, so it is printed too.
        result = self.changelog("--since", "0.4.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         "## 0.4.1\n\nFixed the wait.\n\n## 0.3.2\n\nFixed the older line.\n")

    def test_changelog_prints_whole_entries_with_every_part(self):
        result = self.changelog("--since", "0.3.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.headings(result), ["## 0.4.1", "## 0.3.2", "## 0.4.0"])
        self.assertIn("What changed: the release.\n\nRequested by: Eric.\n", result.stdout)
        self.assertNotIn("before the first entry", result.stdout)

    def test_changelog_since_the_newest_version_prints_nothing(self):
        result = self.changelog("--since", "0.4.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_changelog_since_a_version_with_no_entry_prints_the_higher_versions(self):
        # 0.3.1 has no entry, so the versions above it by number are printed, in
        # file order; 0.3.0 and 0.2.0 are not.
        result = self.changelog("--since", "0.3.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.headings(result), ["## 0.4.1", "## 0.3.2", "## 0.4.0"])

    def test_changelog_since_a_version_higher_than_any_entry_prints_nothing(self):
        result = self.changelog("--since", "1.0.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_changelog_compares_versions_by_number_not_by_text(self):
        self.file.write_text("## 0.10.0\n\nTen.\n\n## 0.9.0\n\nNine.\n")
        result = self.changelog("--since", "0.8.5")
        self.assertEqual(self.headings(result), ["## 0.10.0", "## 0.9.0"])
        result = self.changelog("--since", "0.9.5")
        self.assertEqual(self.headings(result), ["## 0.10.0"])

    def test_changelog_reads_the_current_version_under_jig_root(self):
        # Another installed version with its own change log is not read.
        (self.root / "0.4.0").mkdir()
        (self.root / "0.4.0" / "CHANGELOG.md").write_text("## 9.9.9\n\nWrong tree.\n")
        result = self.changelog("--since", "0.4.0")
        self.assertNotIn("Wrong tree", result.stdout)
        self.assertIn("## 0.4.1", result.stdout)

    def test_changelog_with_a_missing_change_log_fails_with_one_message(self):
        self.file.unlink()
        result = self.changelog("--since", "0.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn("cannot read", result.stderr)

    def test_changelog_with_no_current_version_fails_with_one_message(self):
        os.unlink(self.root / "current")
        result = self.changelog("--since", "0.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)

    def test_changelog_with_an_unreadable_change_log_fails_with_one_message(self):
        # A directory in the place of the file cannot be read by any account, so
        # the test holds also where file modes do not stop the reader.
        self.file.unlink()
        self.file.mkdir()
        result = self.changelog("--since", "0.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn("cannot read", result.stderr)

    def test_changelog_refuses_a_heading_that_names_no_version(self):
        self.file.write_text("## 0.4.1\n\nFixed.\n\n## Unreleased\n\nMore.\n")
        result = self.changelog("--since", "0.4.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn(":5: an entry heading is not", result.stderr)

    def test_changelog_without_since_is_a_usage_error(self):
        for args in ((), ("0.4.0",), ("--since",)):
            result = self.changelog(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn("usage", result.stderr)

    def test_changelog_with_a_malformed_version_is_a_usage_error(self):
        for version in ("0.4", "v0.4.0", "0.4.0.1", "current", ""):
            result = self.changelog("--since", version)
            self.assertEqual(result.returncode, 2, version)
            self.assertIn("not a version", result.stderr)

    def test_jig_dispatches_changelog_to_the_current_tree(self):
        # bin/jig finds the subcommand by its name in libexec of the tree it runs.
        result = run([ROOT / "bin" / "jig", "changelog", "--since", "0.4.0"], cwd=self.tmp,
                     env=self.env(JIG_ROOT=str(self.tmp / "none")))
        self.assertEqual(result.returncode, 1)
        self.assertIn("jig changelog: cannot read", result.stderr)
        self.assertIn(str(self.tmp / "none" / "current" / "CHANGELOG.md"), result.stderr)
