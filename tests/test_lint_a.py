import pathlib
import sys
import unittest

from tests.support import PUBLIC_ADDRESS, ROOT, TempTestCase, run

sys.path.insert(0, str(ROOT / "lib"))

import jig_ids  # noqa: E402

LINT = ROOT / "tools" / "lint-a.sh"
# jig's own tests and pattern file, found from this module so that a copy of the
# tree, such as the one the mutant run makes, reads its own.
OWN_TESTS = pathlib.Path(__file__).resolve().parent
OWN_PATTERNS = OWN_TESTS.parent / "tools" / "lint-a-patterns.txt"
TERM = "zanthorpe"


class LintATest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.product = self.git_init(self.tmp / "product")
        self.terms = self.tmp / "planted-terms"
        self.terms.write_text(f"# planted for this test\n\n{TERM}\n10.77.1.2  # an address\n")

    def write(self, name, text, mode=0o644):
        path = self.product / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)

    def lint(self, **extra):
        extra.setdefault("LINT_TERMS", str(self.terms))
        return run([LINT, self.product], env=self.env(**extra))

    def assert_refused(self, needle):
        result = self.lint()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(needle, result.stderr)

    def assert_clean(self):
        result = self.lint()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_lint_a_passes_a_clean_tree(self):
        self.write("notes.md", "Nothing private here.\n")
        self.assert_clean()

    def test_lint_a_refuses_a_planted_term(self):
        self.write("notes.md", f"Built on {TERM} last week.\n")
        self.assert_refused(TERM)

    def test_lint_a_term_match_is_case_insensitive(self):
        self.write("notes.md", f"Built on {TERM.upper()} last week.\n")
        self.assert_refused(TERM.upper())

    def test_lint_a_term_match_is_whole_word(self):
        self.write("notes.md", f"{TERM}s and x{TERM} and {TERM}_y are other words.\n")
        self.assert_clean()

    def test_lint_a_term_next_to_punctuation_is_a_word(self):
        self.write("notes.md", f"see ({TERM}-host).\n")
        self.assert_refused(TERM)

    def test_lint_a_refuses_a_term_in_code(self):
        self.write("tool.py", f'HOST = "{TERM}"\n')
        self.assert_refused(TERM)

    def test_lint_a_refuses_a_term_in_a_path(self):
        self.write(f"{TERM}/notes.md", "Nothing private here.\n")
        self.assert_refused("in the path")

    def test_lint_a_refuses_an_address_term(self):
        self.write("tool.py", 'HOST = "10.77.1.2"\n')
        self.assert_refused("10.77.1.2")

    def test_lint_a_refuses_another_email_address(self):
        self.write("notes.md", "Write to someone@example.org about it.\n")
        self.assert_refused("someone@example.org")

    def test_lint_a_refuses_a_lookalike_of_the_public_address(self):
        self.write("notes.md", "Write to eric@lalonde.com.example.org.\n")
        self.assert_refused("e-mail address")

    def test_lint_a_allows_the_public_name_and_address(self):
        self.write("notes.md", f"Eric Lalonde <{PUBLIC_ADDRESS}> and {PUBLIC_ADDRESS.upper()}\n")
        self.assert_clean()

    def test_lint_a_allows_role_accounts_and_process_words(self):
        self.write("notes.md", "The orchestrator, specwriter and implementer run a batch, "
                               "a unit, a phase gate and a spec review.\n")
        self.assert_clean()

    def test_lint_a_allows_host_config_paths(self):
        self.write("notes.md", "Blocks live in /srv/jig/blocks/demo/ on the host.\n")
        self.assert_clean()

    def test_lint_a_refuses_an_ipv4_address(self):
        self.write("notes.md", "It answers on 172.16.4.9 today.\n")
        self.assert_refused("172.16.4.9")

    def test_lint_a_skips_numbers_that_are_not_addresses(self):
        self.write("notes.md", "Version 1.2.3 and 999.1.1.1 are not addresses.\n")
        self.assert_clean()

    def test_lint_a_refuses_home_paths(self):
        for path in ("/home/someone", "/Users/someone", "/root/ws"):
            self.write("notes.md", f"Look in {path}/deeper for it.\n")
            self.assert_refused(path)

    def test_lint_a_refuses_an_address_ending_a_sentence(self):
        self.write("notes.md", "It answers on 172.16.4.9.\n")
        self.assert_refused("172.16.4.9")

    def test_lint_a_allows_a_tilde_path_with_no_name(self):
        self.write("notes.md", "Look in ~/.config/jig/demo.conf for it.\n")
        self.assert_clean()

    def test_lint_a_refuses_a_unit_id(self):
        self.write("notes.md", "Fixed by zq7ab in the last batch.\n")
        self.assert_refused("zq7ab")

    def test_lint_a_refuses_a_record_id(self):
        self.write("notes.md", "Required by R-12 of the register.\n")
        self.assert_refused("R-12")

    def test_lint_a_allows_common_technical_words(self):
        self.write("notes.md", "Hash with sha256, not md5, over ipv4 in utf8; since v12.\n")
        self.assert_clean()

    def test_lint_a_reads_python_comments(self):
        self.write("tool.py", "VALUE = 1  # from someone@example.org\n")
        self.assert_refused("someone@example.org")

    def test_lint_a_reads_python_docstrings(self):
        self.write("tool.py", 'def run():\n    """Ask someone@example.org."""\n')
        self.assert_refused("someone@example.org")

    def test_lint_a_generic_patterns_skip_python_strings(self):
        self.write("tool.py", 'FIXTURE = "someone@example.org at 172.16.4.9"\n')
        self.assert_clean()

    def test_lint_a_reads_a_python_file_that_does_not_parse(self):
        self.write("tool.py", 'def (\nFIXTURE = "someone@example.org"\n')
        self.assert_refused("someone@example.org")

    def test_lint_a_reads_shell_comments(self):
        self.write("tool.sh", "#!/bin/bash\necho hello  # from 172.16.4.9\n")
        self.assert_refused("172.16.4.9")

    def test_lint_a_finds_shell_by_its_first_line(self):
        self.write("tool", "#!/bin/bash\n# from 172.16.4.9\n")
        self.assert_refused("172.16.4.9")

    def test_lint_a_generic_patterns_skip_shell_strings(self):
        self.write("tool.sh", '#!/bin/bash\necho "someone@example.org # 172.16.4.9"\n')
        self.assert_clean()

    def test_lint_a_reads_yaml_and_makefile_comments(self):
        for name in ("flow.yml", "Makefile"):
            self.write(name, "# from 172.16.4.9\n")
            self.assert_refused(f"{name}:1")
            (self.product / name).unlink()

    def test_lint_a_reads_the_whole_of_an_unknown_file(self):
        self.write("share/table.tsv", "tool\tasks someone@example.org\n")
        self.assert_refused("someone@example.org")

    def assert_doc_line(self, line, refused, name="notes.md"):
        self.write(name, line + "\n")
        result = self.lint()
        expected = 1 if refused else 0
        self.assertEqual(result.returncode, expected, (line, result.stderr))
        (self.product / name).unlink()

    def test_lint_a_refuses_as_required_but_not_a_longer_phrase(self):
        self.assert_doc_line("Retry as required.", refused=True)
        self.assert_doc_line("Retry AS  Required by the caller.", refused=True)
        self.assert_doc_line("Retry as often as the caller requires.", refused=False)
        self.assert_doc_line("The form has required fields.", refused=False)

    def test_lint_a_refuses_the_rule_above_but_not_rules_above(self):
        self.assert_doc_line("This follows the rule above.", refused=True)
        self.assert_doc_line("The Rule Above applies.", refused=True)
        self.assert_doc_line("The rules above the table apply.", refused=False)

    def test_lint_a_refuses_the_rule_below_but_not_a_rule_below_it(self):
        self.assert_doc_line("See the rule below.", refused=True)
        self.assert_doc_line("THE RULE BELOW holds.", refused=True)
        self.assert_doc_line("A rule below the limit holds.", refused=False)

    def test_lint_a_refuses_the_section_sign_but_not_its_name(self):
        self.assert_doc_line("Read § 4 first.", refused=True)
        self.assert_doc_line("Read the section sign as a pointer.", refused=False)

    def test_lint_a_refuses_a_section_number_but_not_the_word_section(self):
        self.assert_doc_line("This follows section 4.", refused=True)
        self.assert_doc_line("As SECTION 12 says.", refused=True)
        self.assert_doc_line("The sections of the file are sorted.", refused=False)
        self.assert_doc_line("Each section 'name' is unique.", refused=False)
        self.assert_doc_line("This follows the sections 3 and 4.", refused=False)

    def test_lint_a_refuses_a_step_label_at_the_start_of_a_doc_line(self):
        for line in ("Step 3", "Step 3.", "Step 12:", "Step 3)", "  Step 3.  ",
                     "- Step 3:", "* Step 3.", "+ Step 3", "2. Step 3.", "2) Step 3",
                     "## Step 3"):
            self.assert_doc_line(line, refused=True)

    def test_lint_a_passes_a_step_number_that_is_not_a_label(self):
        for line in ("Run Step 3 again.", "steps 3 and 4 run in order.", "step 3.",
                     "Step 3 runs last.", "Step 3a.", "Steps 3.", "Step three."):
            self.assert_doc_line(line, refused=False)

    def test_lint_a_refuses_a_case_label_at_the_start_of_a_doc_line(self):
        for line in ("Case 2", "Case 2:", "- Case 10.", "1. Case 2)"):
            self.assert_doc_line(line, refused=True)

    def test_lint_a_passes_a_case_number_that_is_not_a_label(self):
        for line in ("In case 2 fails, retry.", "case 2:", "Case 2 fails first.",
                     "Cases 2."):
            self.assert_doc_line(line, refused=False)

    def test_lint_a_reads_step_labels_after_the_comment_sign(self):
        self.assert_doc_line("VALUE = 1  # Step 3.", refused=True, name="tool.py")
        self.assert_doc_line("## Case 2:", refused=True, name="tool.py")
        self.assert_doc_line("#!/bin/bash\n# Step 3", refused=True, name="tool.sh")
        self.assert_doc_line("VALUE = 1  # run Step 3.", refused=False, name="tool.py")

    def test_lint_a_reads_step_labels_after_the_opening_docstring_quote(self):
        self.assert_doc_line('def run():\n    """Step 3.\n\n    Then return.\n    """',
                             refused=True, name="tool.py")
        self.assert_doc_line('def run():\n    """Step 3"""', refused=True, name="tool.py")
        self.assert_doc_line('def run():\n    """Run step 3, then Step 4."""',
                             refused=False, name="tool.py")

    def test_lint_a_reads_the_comment_patterns_in_comments_only(self):
        self.assert_doc_line("# Read section 4 as required.", refused=True, name="tool.py")
        self.assert_doc_line('NOTE = "Read section 4 as required, § 2."\nSTEP = "Step 3."',
                             refused=False, name="tool.py")
        self.assert_doc_line('#!/bin/bash\necho "section 4 # the rule above"',
                             refused=False, name="tool.sh")

    def test_lint_a_names_the_line_and_the_wording(self):
        self.write("notes.md", "Fine.\nStep 3: see section 4.\n")
        result = self.lint()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("notes.md:2: step or case label: Step 3", result.stderr)
        self.assertIn("notes.md:2: cross-reference: section 4", result.stderr)

    # The test checks run on an invented language, so that they depend on no real
    # one: a test is a line 'it "<name>"' in a shell file under checks/, and a
    # string is anything between single quotes. Being shell files, lint A reads
    # only their '#' comments for the other patterns, so a refusal in these tests
    # can come from the test checks alone. The invented ID class CTL stands for a
    # project's control IDs.
    def write_patterns(self, controls=True, language=True, allow=False):
        lines = ["# Patterns for this test.", "[process IDs]", *jig_ids.PROCESS_ID_PATTERNS,
                 "[control IDs]"]
        if controls:
            lines.append("CTL CTL-[0-9]+")
        if language:
            lines += ["[language bdd]", r"files checks/[a-z_]+\.sh", 'names ^it "[^"]*"',
                      "strings '[^']*'"]
        if allow:
            lines.append("allow process IDs in test strings")
        self.write("tools/lint-a-patterns.txt", "\n".join(lines) + "\n")

    def write_check(self, *lines):
        self.write("checks/reads.sh", "\n".join(lines) + "\n")

    def test_lint_a_refuses_a_control_id_in_a_test_name(self):
        # A test named after the control it proves is refused, with the file, the
        # line and the ID.
        self.write_patterns()
        self.write_check("setup", 'it "reads the CTL-4 input"', "  expect 'done'")
        self.assert_refused("checks/reads.sh:2: control ID in a test name: CTL-4")

    def test_lint_a_refuses_a_process_id_in_a_test_string(self):
        # A string that cites a record is refused, with the file, the line and the ID.
        self.write_patterns()
        self.write_check('it "reads the input"', "  expect 'see R-12 for why'")
        self.assert_refused("checks/reads.sh:2: process ID in a test string: R-12")

    def test_lint_a_refuses_an_approval_tag_in_a_test_string_as_a_whole(self):
        # The tag of a spec revision is reported whole, not as the shorter spec tag.
        self.write_patterns()
        self.write_check('it "reads the input"', "  expect 'from spec-p2r1 on'")
        self.assert_refused("process ID in a test string: spec-p2r1")

    def test_lint_a_allowance_line_passes_process_ids_in_strings_only(self):
        # With the allowance line a record ID in a string passes, but a control ID
        # in a test name is still refused.
        self.write_patterns(allow=True)
        self.write_check('it "reads the input"', "  expect 'see R-12 for why'")
        self.assert_clean()
        self.write_check('it "reads the CTL-4 input"', "  expect 'see R-12 for why'")
        result = self.lint()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("control ID in a test name: CTL-4", result.stderr)
        self.assertNotIn("process ID", result.stderr)

    def test_lint_a_passes_utf_8_in_a_test_string(self):
        # The name of the encoding ends like a finding ID, but the letter before it
        # makes it part of a longer word; text in that encoding passes too.
        self.write_patterns()
        self.write_check('it "reads the input"', "  expect 'UTF-8 text: naïve café'")
        self.assert_clean()

    def test_lint_a_passes_process_id_lookalikes_in_a_test_string(self):
        # Inside a longer word, after a hyphen, before a letter or digit, and in
        # another letter case, the text is no process ID and the file passes.
        self.write_patterns()
        self.write_check('it "reads the input"',
                         "  expect 'COVID-19'", "  expect 'pre-R-1'", "  expect 'R-1a'",
                         "  expect 'R-1x2'", "  expect 'r-1'", "  expect 'iq-3'",
                         "  expect 'Spec-p2'", "  expect 'IMPL-P1'")
        self.assert_clean()

    def test_lint_a_refuses_a_process_id_followed_by_a_hyphen(self):
        # Only a letter or digit after the number makes a longer word; a hyphen
        # joins a word to the ID, which is still refused.
        self.write_patterns()
        self.write_check('it "reads the input"', "  expect 'R-1-based'")
        self.assert_refused("checks/reads.sh:2: process ID in a test string: R-1")

    def test_lint_a_with_empty_parts_finds_no_test_file(self):
        # With no control class and no language, no file is a test file, so neither
        # ID in the file below is refused.
        self.write_patterns(controls=False, language=False)
        self.write_check('it "reads the CTL-4 input"', "  expect 'see R-12 for why'")
        self.assert_clean()

    def test_lint_a_without_a_pattern_file_checks_no_test(self):
        # A product with no pattern file gets no test check at all.
        self.write_check('it "reads the CTL-4 input"', "  expect 'see R-12 for why'")
        self.assert_clean()

    def test_lint_a_refuses_a_pattern_file_out_of_form(self):
        # A pattern that does not compile would switch a check off unseen, so lint
        # A fails and names the line.
        self.write("tools/lint-a-patterns.txt", "[process IDs]\nR-[0-9\n[control IDs]\n")
        self.write_check('it "reads the input"')
        self.assert_refused("lint-a-patterns.txt:2: the pattern does not compile")

    def test_lint_a_refusal_of_a_pattern_starting_with_a_bracket_names_the_group_form(self):
        # A process ID pattern that starts with a class is read as a mistyped
        # heading; lint A fails on its line and shows how to write it in a group.
        self.write("tools/lint-a-patterns.txt",
                   "[process IDs]\n[A-Z]+-[0-9]+\n[control IDs]\n")
        self.write_check('it "reads the input"')
        self.assert_refused("lint-a-patterns.txt:2: [A-Z]+-[0-9]+ is not a heading")
        self.assert_refused("write such a pattern in a group, as (?:[A-Z]+)-[0-9]+")

    def test_lint_a_refuses_each_kind_of_pattern_file_out_of_form(self):
        # Each file below breaks the form of the pattern file in one way. Lint A
        # must fail on each, naming the line, or the file alone when what is
        # wrong is something absent, so that no typing error turns a check off.
        heads = ["[process IDs]", "R-[0-9]+", "[control IDs]"]
        language = ["[language bdd]", r"files checks/[a-z_]+\.sh", 'names ^it "[^"]*"',
                    "strings '[^']*'"]
        cases = {
            "a heading that comes twice": (
                [*heads, "[process IDs]"],
                "lint-a-patterns.txt:4: the part [process IDs] appears twice"),
            "a class that comes twice": (
                [*heads, "CTL CTL-[0-9]+", "CTL CTL-[0-9]+"],
                "lint-a-patterns.txt:5: the class CTL appears twice"),
            "a key that comes twice": (
                [*heads, *language, "files other/.*"],
                "lint-a-patterns.txt:8: files appears twice in [language bdd]"),
            "a missing heading": (
                heads[:2],
                "lint-a-patterns.txt: the part [control IDs] is missing"),
            "a language with a missing key": (
                [*heads, *language[:3]],
                "lint-a-patterns.txt: [language bdd] has no strings line"),
            "an unknown key under a language": (
                [*heads, *language, "tests .*"],
                "lint-a-patterns.txt:8: a language line is files, names or strings"),
            "a control line with no pattern": (
                [*heads, "CTL"],
                "lint-a-patterns.txt:4: a control ID line needs a class and a pattern"),
            "a pattern before the first heading": (
                ["R-[0-9]+", *heads],
                "lint-a-patterns.txt:1: a pattern before the first part"),
        }
        self.write_check('it "reads the input"')
        for situation, (lines, needle) in cases.items():
            with self.subTest(situation):
                self.write("tools/lint-a-patterns.txt", "\n".join(lines) + "\n")
                self.assert_refused(needle)

    def copy_own_patterns(self, controls=()):
        """Copy jig's pattern file into the fixture, with extra control ID lines."""
        text = OWN_PATTERNS.read_text(encoding="utf-8")
        self.assertIn("[control IDs]\n", text)
        text = text.replace("[control IDs]\n", "[control IDs]\n" + "".join(
            f"{line}\n" for line in controls), 1)
        self.write("tools/lint-a-patterns.txt", text)
        return text

    def test_jig_own_pattern_file_passes_jig_own_tests(self):
        # jig's tests, copied with its pattern file into a fixture product, pass
        # lint A; the private terms are left out, since the lint A test itself
        # holds a planted term. With the allowance line taken out, the same copy
        # is refused, which shows that the patterns do find jig's test files and
        # the record IDs in their strings.
        text = self.copy_own_patterns()
        for path in sorted(OWN_TESTS.glob("*.py")):
            self.write(f"tests/{path.name}", path.read_text(encoding="utf-8"))
        result = self.lint(CI="true", LINT_TERMS=str(self.tmp / "absent"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("lint-a: clean", result.stdout)
        self.write("tools/lint-a-patterns.txt",
                   text.replace("allow process IDs in test strings\n", ""))
        result = self.lint(CI="true", LINT_TERMS=str(self.tmp / "absent"))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("process ID in a test string", result.stderr)

    def test_jig_own_patterns_refuse_a_control_id_in_a_python_or_bash_test_name(self):
        # With jig's patterns and an invented control class added, a Python test
        # method and a bash test function named after a control are refused, with
        # the file, the line and the ID; the same ID in a string passes, as jig
        # allows record IDs there.
        self.copy_own_patterns(controls=["CTL CTL[0-9]+"])
        self.write("tests/test_reads.py", "import unittest\n\n\n"
                   "class ReadsTest(unittest.TestCase):\n"
                   "    def test_reads_CTL4_input(self):\n"
                   "        self.assertEqual('CTL4', 'CTL4')\n")
        self.write("tests/test_reads.sh", "#!/bin/bash\n"
                   "test_reads_CTL4_input() {\n    echo 'CTL4'\n}\n")
        result = self.lint()
        self.assertEqual(result.returncode, 1, result.stdout)
        found = [line for line in result.stderr.splitlines() if "control ID" in line]
        self.assertEqual(found, [
            "lint-a: tests/test_reads.py:5: control ID in a test name: CTL4",
            "lint-a: tests/test_reads.sh:2: control ID in a test name: CTL4",
        ])

    def test_lint_a_missing_terms_file_fails(self):
        self.write("notes.md", "Nothing private here.\n")
        result = self.lint(LINT_TERMS=str(self.tmp / "absent"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing", result.stderr)

    def test_lint_a_missing_terms_file_fails_unless_ci_is_exactly_true(self):
        self.write("notes.md", "Nothing private here.\n")
        result = self.lint(LINT_TERMS=str(self.tmp / "absent"), CI="1")
        self.assertEqual(result.returncode, 1)

    def test_lint_a_ci_without_terms_runs_generic_only_and_says_so(self):
        self.write("notes.md", f"Built on {TERM} last week.\n")
        result = self.lint(LINT_TERMS=str(self.tmp / "absent"), CI="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("generic patterns only", result.stdout)

    def test_lint_a_ci_without_terms_still_refuses_generic_patterns(self):
        self.write("notes.md", "Write to someone@example.org about it.\n")
        result = self.lint(LINT_TERMS=str(self.tmp / "absent"), CI="true")
        self.assertEqual(result.returncode, 1)

    def test_lint_a_ci_with_terms_still_reads_them(self):
        self.write("notes.md", f"Built on {TERM} last week.\n")
        result = self.lint(CI="true")
        self.assertEqual(result.returncode, 1)

    def test_lint_a_terms_file_with_no_terms_fails(self):
        self.write("notes.md", "Nothing private here.\n")
        self.terms.write_text("# only a comment\n\n")
        result = self.lint()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no terms", result.stderr)

    def test_lint_a_default_terms_file_is_beside_the_product(self):
        self.write("notes.md", f"Built on {TERM} last week.\n")
        (self.tmp / "terms.txt").write_text(f"{TERM}\n")
        result = run([LINT, self.product], env=self.env())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(TERM, result.stderr)

    def test_lint_a_tree_with_no_files_fails(self):
        result = self.lint()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no files", result.stderr)

    def test_lint_a_skips_what_git_ignores(self):
        self.write(".gitignore", "built/\n")
        self.write("built/notes.md", "Write to someone@example.org about it.\n")
        self.assert_clean()

    def unpacked(self):
        """Return a tree with files but no git data, as an unpacked source archive is."""
        tree = self.tmp / "unpacked"
        (tree / "built").mkdir(parents=True)
        (tree / "tools").mkdir()
        (tree / ".gitignore").write_text("# made by the build\nbuilt/\n*.log\n")
        (tree / "notes.md").write_text("Write to someone@example.org about it.\n")
        (tree / "built" / "notes.md").write_text("Also someone@example.net.\n")
        (tree / "run.log").write_text("Mail went to someone@example.com.\n")
        (tree / "tools" / "runner").write_text("#!/bin/bash\necho hello\n")
        return tree

    def test_lint_a_reads_a_tree_that_is_not_a_clone_and_skips_what_gitignore_names(self):
        # The address in a listed file is refused; those in the ignored directory and
        # the ignored log file are not read.
        tree = self.unpacked()
        result = run([LINT, tree], env=self.env(LINT_TERMS=str(self.terms)))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        found = [line for line in result.stderr.splitlines() if "e-mail" in line]
        self.assertEqual(found, ["lint-a: notes.md:1: e-mail address: someone@example.org"])

    def test_lint_a_outside_a_clone_without_terms_runs_generic_only_and_says_so(self):
        tree = self.unpacked()
        (tree / "notes.md").write_text(f"Built on {TERM} last week.\n")
        result = run([LINT, tree], env=self.env(LINT_TERMS=str(self.tmp / "absent")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not a git clone", result.stdout)
        self.assertIn("generic patterns only", result.stdout)

    def test_lint_a_lists_shell_files_of_a_tree_that_is_not_a_clone(self):
        tree = self.unpacked()
        result = run(["python3", ROOT / "lib" / "lint_a.py", "--shell-files", tree],
                     env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [str(tree / "tools" / "runner")])

    def test_lint_a_lists_shell_files_by_name_and_first_line(self):
        self.write("a.sh", "echo hello\n")
        self.write("runner", "#!/bin/bash\necho hello\n")
        self.write("tool.py", "VALUE = 1\n")
        self.write("other", "#!/usr/bin/env python3\n")
        result = run(["python3", ROOT / "lib" / "lint_a.py", "--shell-files", self.product],
                     env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        names = sorted(line.rsplit("/", 1)[1] for line in result.stdout.splitlines())
        self.assertEqual(names, ["a.sh", "runner"])


if __name__ == "__main__":
    unittest.main()
