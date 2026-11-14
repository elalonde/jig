import os
import sys

from tests import clone_cases
from tests.support import ROOT, TempTestCase, run

sys.path.insert(0, str(ROOT / "lib"))

import jig_clones  # noqa: E402
import lint_a  # noqa: E402

LINT_A = ROOT / "lib" / "lint_a.py"
# A word that only this test plants as a private term.
TERM = "plantedclonesterm"


class OtherCloneTest(TempTestCase):
    def test_other_clone_finds_the_clone_of_each_shared_layout(self):
        # Each case builds its layout and starts from one clone: the function gives the
        # clone that the case expects, or None when the layout has no such clone.
        for number, case in enumerate(clone_cases.CASES):
            with self.subTest(case=case.name):
                base = clone_cases.build(self.tmp / f"case{number}", case.layout)
                found = jig_clones.other_clone(base / case.start)
                expected = None if case.expected is None else (base / case.expected).resolve()
                self.assertEqual(found, expected)

    def test_other_clone_reads_a_relative_path_from_the_working_directory(self):
        # A tool may hand over the -dev clone as ".", the directory it runs in; the
        # product clone beside it is still found.
        base = clone_cases.build(self.tmp / "base", "beside")
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(base / clone_cases.DEV)
        self.assertEqual(jig_clones.other_clone("."), (base / clone_cases.PROJECT).resolve())

    def test_a_directory_named_only_dev_has_no_product_clone(self):
        # "-dev" alone names no project, so nothing is looked for beside it.
        (self.tmp / "-dev").mkdir()
        (self.tmp / "-dev" / "x").mkdir()
        self.assertIsNone(jig_clones.other_clone(self.tmp / "-dev"))


class LintATermsTest(TempTestCase):
    """Lint A looks for terms.txt in the -dev clone that the shared rule finds."""

    def plant(self, base, case):
        for relative in clone_cases.LAYOUTS[case.layout]:
            if relative.endswith("-dev"):
                (base / relative / "terms.txt").write_text(f"{TERM}\n", encoding="utf-8")
        product = self.git_init(base / case.start)
        (product / "notes.txt").write_text(f"a note on {TERM}\n", encoding="utf-8")
        return product

    def test_lint_a_reads_the_terms_of_the_dev_clone_of_each_shared_layout(self):
        # With no LINT_TERMS, lint A of a product clone reads terms.txt of the -dev
        # clone the rule finds, and refuses the planted term in the product. With no
        # -dev clone it fails on the missing file, at the place beside the product.
        for number, case in enumerate(clone_cases.FROM_PRODUCT):
            with self.subTest(case=case.name):
                base = clone_cases.build(self.tmp / f"case{number}", case.layout)
                product = self.plant(base, case)
                place = (base / (case.expected or clone_cases.DEV)).resolve() / "terms.txt"
                self.assertEqual(lint_a.terms_place(product), place)
                env = self.env()
                self.assertNotIn("LINT_TERMS", env)
                result = run(["python3", LINT_A, product], env=env)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                if case.expected is None:
                    self.assertIn(f"terms file {place} is missing", result.stderr)
                else:
                    self.assertIn(f"notes.txt:1: private term: {TERM}", result.stderr)
