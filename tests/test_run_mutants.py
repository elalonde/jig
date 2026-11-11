import unittest

from tests.support import ROOT, TempTestCase, run

RUNNER = ROOT / "tools" / "run-mutants"

GOOD_TEST = """import pathlib
import unittest


class ThingTest(unittest.TestCase):
    def test_thing_is_good(self):
        self.assertEqual(pathlib.Path("thing.txt").read_text(), "good\\n")
"""

BREAKS_THE_THING = """--- a/thing.txt
+++ b/thing.txt
@@ -1 +1 @@
-good
+bad
"""

CHANGES_NOTHING_TESTED = """--- a/other.txt
+++ b/other.txt
@@ -1 +1 @@
-one
+two
"""

DOES_NOT_APPLY = """--- a/thing.txt
+++ b/thing.txt
@@ -1 +1 @@
-something else
+bad
"""


class RunMutantsTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.product = self.tmp / "product"
        (self.product / "tests").mkdir(parents=True)
        (self.product / "tests" / "__init__.py").write_text("")
        (self.product / "tests" / "test_some_thing.py").write_text(GOOD_TEST)
        (self.product / "thing.txt").write_text("good\n")
        (self.product / "other.txt").write_text("one\n")
        self.mutants = self.tmp / "mutants"
        self.mutants.mkdir()
        (self.mutants / "tools.txt").write_text("# tools under test\nsome-thing\n")

    def patch(self, text, name="some-thing.patch"):
        (self.mutants / name).write_text(text)

    def runner(self, *tools):
        return run([RUNNER, "--root", self.product, self.mutants, *tools], env=self.env())

    def assert_fails(self, needle, *tools):
        result = self.runner(*tools)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(needle, result.stderr)

    def test_run_mutants_passes_when_the_patch_makes_the_test_fail(self):
        self.patch(BREAKS_THE_THING)
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("some-thing", result.stdout)

    def test_run_mutants_fails_when_a_tool_has_no_patch(self):
        self.assert_fails("no patch")

    def test_run_mutants_fails_when_a_patch_does_not_apply(self):
        self.patch(DOES_NOT_APPLY)
        self.assert_fails("does not apply")

    def test_run_mutants_fails_when_a_patch_leaves_the_test_green(self):
        self.patch(CHANGES_NOTHING_TESTED)
        self.assert_fails("still passes")

    def test_run_mutants_fails_on_an_empty_patch(self):
        self.patch("")
        self.assert_fails("does not apply")

    def test_run_mutants_fails_when_the_test_is_red_before_the_patch(self):
        self.patch(BREAKS_THE_THING)
        (self.product / "thing.txt").write_text("bad\n")
        self.assert_fails("without the patch")

    def test_run_mutants_fails_when_the_tool_has_no_test(self):
        self.patch(BREAKS_THE_THING)
        (self.product / "tests" / "test_some_thing.py").unlink()
        self.assert_fails("without the patch")

    def test_run_mutants_fails_when_no_tool_is_listed(self):
        (self.mutants / "tools.txt").write_text("# nothing yet\n")
        self.assert_fails("names no tool")

    def test_run_mutants_fails_when_the_listing_is_missing(self):
        (self.mutants / "tools.txt").unlink()
        self.assert_fails("cannot read")

    def test_run_mutants_fails_on_a_patch_no_tool_names(self):
        self.patch(BREAKS_THE_THING)
        self.patch(BREAKS_THE_THING, "forgotten.patch")
        self.assert_fails("forgotten.patch")

    def test_run_mutants_runs_only_the_named_tool(self):
        self.patch(BREAKS_THE_THING)
        (self.mutants / "tools.txt").write_text("some-thing\nother-tool\n")
        result = self.runner("some-thing")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_mutants_fails_on_a_named_tool_that_is_not_listed(self):
        self.patch(BREAKS_THE_THING)
        self.assert_fails("not in tools.txt", "other-tool")

    def test_run_mutants_reports_every_failing_tool(self):
        self.patch(CHANGES_NOTHING_TESTED)
        (self.mutants / "tools.txt").write_text("some-thing\nother-tool\n")
        result = self.runner()
        self.assertEqual(result.returncode, 1)
        self.assertIn("still passes", result.stderr)
        self.assertIn("other-tool: no patch", result.stderr)

    def test_run_mutants_leaves_the_tree_as_it_was(self):
        self.patch(BREAKS_THE_THING)
        self.assertEqual(self.runner().returncode, 0)
        self.assertEqual((self.product / "thing.txt").read_text(), "good\n")
        self.assertFalse((self.product / ".git").exists())


if __name__ == "__main__":
    unittest.main()
