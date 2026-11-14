"""jig trace on fixture projects: a -dev clone with the product clone beside it."""

import json
import shutil
import sys
import unittest

from tests.support import ROOT, TempTestCase, git_ok, run

sys.path.insert(0, str(ROOT / "lib"))

import jig_trace  # noqa: E402

TRACE = ROOT / "libexec" / "jig-trace"
PREFIX = "jig trace check: "

# The product of the fixture project: a tool and its test.
TOOL_TEXT = "def main():\n    return 0\n\n\ndef helper():\n    return 0\n"
TEST_TEXT = ("import unittest\n\n\nclass ToolTest(unittest.TestCase):\n"
             "    def test_main_returns_zero(self):\n        self.assertEqual(0, 0)\n")
IDS_HEADER = "class\tregex\tdefined\tcoverage\n"
ID_CLASSES = (IDS_HEADER
              + "R\tR-[0-9]+\tspecs/requirements.md\trequirement\n"
              + "C\tC-[a-z]+-[0-9]+\tspecs/03-controls.md\tcontrol\n")
SPEC_TEXT = "# Spec\n\nThe rule R-1 holds, and the control C-core-2 tests it.\n"
MAP_HEADER = "old\tnew\tdate\treason\n"
OLD_HASH = "1234567890abcdef1234567890abcdef12345678"


def ledger_note(commit, name, rules):
    """A ledger note in the form that jig trace form prints, with the unit filled in."""
    return (f"commit: {commit}\nunit: fx1\n\n| file | function | rules |\n"
            f"| --- | --- | --- |\n| {name} | - | {rules} |\n")


def entry(line, text, ids, nth=1, comments=("why this line is here",)):
    return {"line": line, "text": text, "nth": nth, "ids": list(ids),
            "comments": list(comments)}


class TraceCase(TempTestCase):
    """A fixture project whose trace holds one entry with one ID, which all pass."""

    def setUp(self):
        super().setUp()
        self.dev = self.tmp / "demo-dev"
        self.product = self.git_init(self.tmp / "demo")
        self.write(self.product / "src/tool.py", TOOL_TEXT)
        self.write(self.product / "tests/test_tool.py", TEST_TEXT)
        self.git_commit_all(self.product)
        self.write(self.dev / "specs/ids.tsv", ID_CLASSES)
        self.write(self.dev / "specs/jig-spec.md", SPEC_TEXT)
        # The kickoff record is not a spec document: an ID only it mentions is unknown.
        self.write(self.dev / "specs/00-kickoff.md", "The kickoff names R-77.\n")
        self.trace("src/tool.py", [entry(1, "def main():", ["R-1"])])

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def trace(self, product_path, entries):
        self.write(self.dev / "trace" / f"{product_path}.json", json.dumps(entries, indent=1))

    def head(self):
        return git_ok(self.env(), "-C", self.product, "rev-parse", "HEAD").strip()

    def commit(self, name):
        self.write(self.product / name, "x = 1\n")
        self.git_commit_all(self.product)
        return self.head()

    def check(self, *args):
        return run([TRACE, *(args or ["check"])], env=self.env(), cwd=self.dev)

    def assert_passes(self):
        result = self.check()
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def assert_fails_with(self, line):
        """The check exits 1 and prints exactly this one failure line."""
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr.splitlines(), [PREFIX + line])


class PassingTest(TraceCase):

    def test_good_trace_passes_with_a_test_entry_holding_its_control_ids(self):
        # A file-level entry and the entry of a test, which holds its control ID, join
        # the entry of the tool; there is no commit map, so the ledger is not read.
        self.trace("src/tool.py", [entry(1, "def main():", ["R-1"]),
                                   entry(0, "", ["R-1"]),
                                   entry(9, "return 0", ["R-1"], nth=2)])
        self.trace("tests/test_tool.py",
                   [entry(5, "def test_main_returns_zero(self):", ["C-core-2"])])
        self.assert_passes()

    def test_moved_line_still_resolves_because_its_line_number_is_a_hint(self):
        # The entry says line 40, but the text is found on line 1; that is enough.
        self.trace("src/tool.py", [entry(40, "def main():", ["R-1"])])
        self.assert_passes()

    def test_empty_project_passes(self):
        # A -dev clone with no specs and no trace, and its product clone, has nothing
        # that can fail.
        dev = self.tmp / "empty-dev"
        dev.mkdir()
        self.git_init(self.tmp / "empty")
        result = run([TRACE, "check"], env=self.env(), cwd=dev)
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_commit_map_with_every_later_commit_in_the_ledger_passes(self):
        # Two commits follow the import, and both have a filled ledger note.
        start = self.head()
        self.write(self.dev / "trace/commit-map.tsv",
                   f"{MAP_HEADER}{OLD_HASH}\t{start}\t2026-11-10\tthe import squash\n")
        for name in ("one.py", "two.py"):
            commit = self.commit(name)
            self.write(self.dev / f"trace/ledger/{commit}.md", ledger_note(commit, name, "none"))
        self.assert_passes()

    def test_runs_from_a_directory_inside_the_dev_clone(self):
        # The -dev clone is the nearest directory with that suffix, so a subdirectory works.
        result = run([TRACE, "check"], env=self.env(), cwd=self.dev / "specs")
        self.assertEqual((result.returncode, result.stderr), (0, ""))


class AnchorTest(TraceCase):

    def test_anchor_whose_text_is_gone_fails(self):
        # No line of the tool reads like the entry's text.
        self.trace("src/tool.py", [entry(1, "def gone():", ["R-1"])])
        self.assert_fails_with("trace/src/tool.py.json: entry 1 (line 1): src/tool.py has "
                               "fewer than 1 lines with the text 'def gone():'")

    def test_anchor_asking_for_more_occurrences_than_exist_fails(self):
        # The text occurs twice, and the entry asks for its third occurrence.
        self.trace("src/tool.py", [entry(2, "return 0", ["R-1"], nth=3)])
        self.assert_fails_with("trace/src/tool.py.json: entry 1 (line 2): src/tool.py has "
                               "fewer than 3 lines with the text 'return 0'")

    def test_trace_file_of_another_shape_fails_with_one_line(self):
        # The entry has no comments key; the file gives one line and no more.
        self.write(self.dev / "trace/src/tool.py.json",
                   json.dumps([{"line": 1, "text": "def main():", "nth": 1, "ids": ["R-1"]}]))
        self.assert_fails_with("trace/src/tool.py.json: entry 1 does not have exactly the "
                               "keys line, text, nth, ids, comments")

    def test_trace_file_that_is_an_object_fails_with_one_line(self):
        # A single entry written without the array around it is another shape.
        self.write(self.dev / "trace/src/tool.py.json",
                   json.dumps(entry(1, "def main():", ["R-1"])))
        self.assert_fails_with("trace/src/tool.py.json: is not a JSON array of entries")


class IdTest(TraceCase):

    def test_id_of_no_class_fails(self):
        # The spec writes the ID, but no class regex matches it as a whole.
        self.write(self.dev / "specs/jig-spec.md", "The item Z-1 is written here.\n")
        self.trace("src/tool.py", [entry(1, "def main():", ["Z-1"])])
        self.assert_fails_with("trace/src/tool.py.json: entry 1 (line 1): ID 'Z-1' matches "
                               "no class of specs/ids.tsv")

    def test_id_written_only_inside_a_longer_id_fails(self):
        # The spec writes only a longer ID that starts with the same text; a search
        # must not find the shorter one inside it.
        self.write(self.dev / "specs/jig-spec.md", "Only R-10 is written here.\n")
        self.assert_fails_with("trace/src/tool.py.json: entry 1 (line 1): ID 'R-1' does not "
                               "occur in the spec documents")

    def test_id_written_only_in_the_kickoff_record_fails(self):
        # The kickoff record is not a spec document, so its mention does not count.
        self.trace("src/tool.py", [entry(1, "def main():", ["R-77"])])
        self.assert_fails_with("trace/src/tool.py.json: entry 1 (line 1): ID 'R-77' does not "
                               "occur in the spec documents")

    def test_id_in_a_spec_document_below_specs_passes(self):
        # A spec document in a subdirectory of specs counts like any other.
        self.write(self.dev / "specs/03/controls.md", "R-5 is written here.\n")
        self.trace("src/tool.py", [entry(1, "def main():", ["R-5"])])
        self.assert_passes()

    def test_no_id_classes_file_fails_every_id(self):
        # With no classes declared, the one ID matches no class.
        (self.dev / "specs/ids.tsv").unlink()
        self.assert_fails_with("trace/src/tool.py.json: entry 1 (line 1): ID 'R-1' matches "
                               "no class: there is no specs/ids.tsv")

    def test_id_classes_file_of_another_form_fails_with_one_line(self):
        # A header with a column missing gives the form line only, and no line for the ID.
        self.write(self.dev / "specs/ids.tsv", "class\tregex\tdefined\nR\tR-[0-9]+\tspecs\n")
        self.assert_fails_with("specs/ids.tsv: line 1 is not the header line class, "
                               "regex, defined, coverage, separated by tabs")

    def test_id_classes_row_with_too_few_fields_fails_with_one_line(self):
        # A row with three fields under the right header is another form too.
        self.write(self.dev / "specs/ids.tsv", IDS_HEADER + "R\tR-[0-9]+\tspecs\n")
        self.assert_fails_with("specs/ids.tsv: line 2 has 3 fields, not 4")

    def test_class_regex_that_does_not_compile_fails_with_one_line(self):
        # An unclosed bracket in the regex is a form failure of the classes file.
        self.write(self.dev / "specs/ids.tsv", IDS_HEADER + "R\tR-[0-9\tspecs\tnone\n")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith(
            PREFIX + "specs/ids.tsv: line 2: the regex of class R does not compile ("), lines)

    def test_class_regex_with_a_flag_for_the_whole_regex_names_the_scoped_form(self):
        # The regex compiles alone, but not after the guards of the search; the one
        # line says why and which form works, not that the regex does not compile.
        self.write(self.dev / "specs/ids.tsv",
                   IDS_HEADER + "R\t(?i)r-[0-9]+\tspecs/requirements.md\tnone\n")
        self.assert_fails_with("specs/ids.tsv: line 2: class R: a flag for the whole regex, "
                               "such as (?i), is not allowed in a class regex; a scoped group "
                               "such as (?i:...) is")

    def test_class_regex_with_a_scoped_flag_passes(self):
        # The same letter case rule as a scoped group is a valid class regex.
        self.write(self.dev / "specs/ids.tsv",
                   IDS_HEADER + "R\t(?i:r)-[0-9]+\tspecs/requirements.md\tnone\n"
                   + "C\tC-[a-z]+-[0-9]+\tspecs/03-controls.md\tnone\n")
        self.assert_passes()

    def test_class_regex_matching_outside_the_general_form_fails(self):
        # The class admits a dotted suffix, and a spec writes one; the whole match is
        # outside the general ID form, though its first part alone would fit it.
        self.write(self.dev / "specs/ids.tsv",
                   ID_CLASSES + "S\tS-[0-9]+(?:\\.[0-9]+)?\tspecs/jig-spec.md\tnone\n")
        self.write(self.dev / "specs/jig-spec.md", SPEC_TEXT + "The step S-1.2 follows.\n")
        self.assert_fails_with("specs/ids.tsv: class S matches 'S-1.2' in specs/jig-spec.md, "
                               "which is outside the general ID form")

    def test_class_match_inside_a_longer_word_is_not_counted(self):
        # The class finds a dotted step only with the guards of the general form. The
        # spec writes the step with a letter right before it, inside a longer word,
        # so there is no match to refuse.
        self.write(self.dev / "specs/ids.tsv",
                   ID_CLASSES + "S\tS-[0-9]+(?:\\.[0-9]+)?\tspecs/jig-spec.md\tnone\n")
        self.write(self.dev / "specs/jig-spec.md", SPEC_TEXT + "The word XS-1.2 follows.\n")
        self.assert_passes()

    def test_id_written_only_in_the_id_tables_fails(self):
        # The two ID tables under specs are records, not specs: one ID is written
        # only in the ID map, and the other only in a defined cell of the classes.
        self.write(self.dev / "specs/ids.tsv",
                   ID_CLASSES + "N\tN-[0-9]+\tspecs/R-79.md\tnone\n")
        self.write(self.dev / "specs/id-map.tsv", "old\tnew\nR-78\tR-1\n")
        self.trace("src/tool.py", [entry(1, "def main():", ["R-1", "R-78", "R-79"])])
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr.splitlines(), [
            PREFIX + f"trace/src/tool.py.json: entry 1 (line 1): ID '{record}' does not occur "
            "in the spec documents" for record in ("R-78", "R-79")])


class MirrorTest(TraceCase):

    def test_trace_file_whose_product_file_is_gone_fails_with_one_line(self):
        # The tool was removed from the product; its entries are not checked as well.
        (self.product / "src/tool.py").unlink()
        self.assert_fails_with("trace/src/tool.py.json: the product has no file src/tool.py")

    def test_no_product_clone_fails_with_one_line(self):
        # The -dev clone holds a trace but no product clone, beside it or inside it, to
        # check it against; the one line names the place beside it.
        dev = self.tmp / "lone-dev"
        self.write(dev / "specs/ids.tsv", ID_CLASSES)
        self.write(dev / "specs/jig-spec.md", SPEC_TEXT)
        self.write(dev / "trace/src/tool.py.json",
                   json.dumps([entry(1, "def main():", ["R-1"])]))
        result = run([TRACE, "check"], env=self.env(), cwd=dev)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.splitlines(),
                         [f"{PREFIX}lone-dev: no product clone at {dev.resolve().parent / 'lone'}"])


class ProductPlaceTest(TraceCase):
    """Where jig trace looks for the product clone of the -dev clone it runs in."""

    def move_inside(self):
        inside = self.dev / "demo"
        self.product.rename(inside)
        self.product = inside

    def test_a_product_clone_inside_the_dev_clone_is_checked_when_none_is_beside_it(self):
        # The place of the releases before 0.6: with nothing beside the -dev clone, the
        # clone inside it is the product, and its trace passes.
        self.move_inside()
        self.assert_passes()

    def test_gen_reads_the_clone_beside_when_one_is_inside_too(self):
        # Both places hold a clone, and only the file of the clone beside it has a
        # comment that cites a second requirement: gen writes the entry of that comment.
        leftover = self.git_init(self.dev / "demo")
        self.write(leftover / "src/tool.py", TOOL_TEXT)
        self.write(self.product / "src/tool.py", TOOL_TEXT + "\n\n# Proves R-2.\nX = 1\n")
        result = run([TRACE, "gen", "src/tool.py"], env=self.env(), cwd=self.dev)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = json.loads((self.dev / "trace/src/tool.py.json").read_text(encoding="utf-8"))
        self.assertIn(["R-2"], [item["ids"] for item in entries])


class LedgerTest(TraceCase):

    def map_to_head(self):
        """Write a commit map whose import is the product's current HEAD."""
        start = self.head()
        self.write(self.dev / "trace/commit-map.tsv",
                   f"{MAP_HEADER}{OLD_HASH}\t{start}\t2026-11-10\tthe import squash\n")
        return start

    def test_commit_after_the_import_with_no_ledger_entry_fails(self):
        # One product commit follows the import, and nobody wrote its ledger note.
        self.map_to_head()
        commit = self.commit("later.py")
        self.assert_fails_with(f"trace/ledger/{commit}.md: no ledger entry for the product "
                               f"commit {commit}")

    def test_commit_after_the_import_whose_entry_holds_fill_fails(self):
        # The ledger note exists, but one rules cell was never filled.
        self.map_to_head()
        commit = self.commit("later.py")
        self.write(self.dev / f"trace/ledger/{commit}.md",
                   ledger_note(commit, "later.py", "<fill>"))
        self.assert_fails_with(f"trace/ledger/{commit}.md: a rules cell still holds <fill>")

    def commit_with_subject(self, name, subject):
        """Add one product file in a commit with this subject and return its hash."""
        self.write(self.product / name, "x = 1\n")
        for command in (["add", "-A"], ["commit", "-q", "--no-verify", "-m", subject]):
            git_ok(self.env(), "-C", self.product, *command)
        return self.head()

    def test_release_commit_at_head_needs_no_ledger_entry(self):
        # The release run has just made its commit on top of the import; nobody can
        # have written its note yet, and the check passes without it.
        self.map_to_head()
        self.commit_with_subject("version.py", "Release 1.4.0")
        self.assert_passes()

    def test_release_commit_with_a_later_commit_needs_its_entry(self):
        # A commit follows the release commit and has its own note; the release
        # commit is no longer HEAD, so its missing note is the one failure.
        self.map_to_head()
        release = self.commit_with_subject("version.py", "Release 1.4.0")
        later = self.commit("later.py")
        self.write(self.dev / f"trace/ledger/{later}.md", ledger_note(later, "later.py", "none"))
        self.assert_fails_with(f"trace/ledger/{release}.md: no ledger entry for the product "
                               f"commit {release}")

    def test_release_commit_with_a_later_commit_and_both_entries_passes(self):
        # The first unit after the release wrote the release commit's note too.
        self.map_to_head()
        release = self.commit_with_subject("version.py", "Release 1.4.0")
        later = self.commit("later.py")
        for commit, name in ((release, "version.py"), (later, "later.py")):
            self.write(self.dev / f"trace/ledger/{commit}.md", ledger_note(commit, name, "none"))
        self.assert_passes()

    def test_release_subject_below_head_is_checked_like_any_commit(self):
        # Two commits carry release subjects; only HEAD is exempt, so the earlier
        # one without a note fails.
        self.map_to_head()
        first = self.commit_with_subject("one.py", "Release 1.4.0")
        self.commit_with_subject("two.py", "Release 1.4.1")
        self.assert_fails_with(f"trace/ledger/{first}.md: no ledger entry for the product "
                               f"commit {first}")

    def test_head_whose_subject_only_starts_like_a_release_needs_its_entry(self):
        # A subject with words after the version is not the release commit's subject.
        self.map_to_head()
        commit = self.commit_with_subject("notes.py", "Release 1.4.0 notes")
        self.assert_fails_with(f"trace/ledger/{commit}.md: no ledger entry for the product "
                               f"commit {commit}")

    def test_commit_map_of_another_form_fails_with_one_line(self):
        # A row with a short hash is not the form, and one line says so.
        self.write(self.dev / "trace/commit-map.tsv",
                   f"{MAP_HEADER}1234567\t{self.head()}\t2026-11-10\tsquash\n")
        self.commit("later.py")
        self.assert_fails_with("trace/commit-map.tsv: line 2: old must be a full commit "
                               "hash, and new a full commit hash or empty")

    def test_commit_map_header_failure_names_line_one(self):
        # A header without the reason column is refused on its line.
        self.write(self.dev / "trace/commit-map.tsv",
                   f"old\tnew\tdate\n{OLD_HASH}\t{self.head()}\t2026-11-10\n")
        self.assert_fails_with("trace/commit-map.tsv: line 1 is not the header line old, new, "
                               "date, reason, separated by tabs")

    def test_commit_map_with_a_repeated_old_hash_fails_with_its_line(self):
        # The same old commit is mapped twice, which would give it two
        # replacements; the second row is refused.
        start = self.head()
        self.write(self.dev / "trace/commit-map.tsv",
                   f"{MAP_HEADER}{OLD_HASH}\t{start}\t2026-11-10\tthe import squash\n"
                   f"{OLD_HASH}\t\t2026-11-11\tdropped again\n")
        self.assert_fails_with(f"trace/commit-map.tsv: line 3 repeats the old hash {OLD_HASH}, "
                               "of line 2")

    def test_commit_map_whose_new_hash_is_not_in_the_product_fails(self):
        # The map names a new commit that the product does not hold.
        self.write(self.dev / "trace/commit-map.tsv",
                   f"{MAP_HEADER}{OLD_HASH}\t{'f' * 40}\t2026-11-10\tsquash\n")
        self.assert_fails_with(f"trace/commit-map.tsv: the new hash {'f' * 40} is not in "
                               "the product")

    def test_import_is_the_newest_new_hash(self):
        # Two rewrites: the commits between them are before the import and need no
        # note; only the commit after the newer one does.
        first = self.head()
        self.commit("between.py")
        second = self.head()
        self.write(self.dev / "trace/commit-map.tsv",
                   f"{MAP_HEADER}{second}\t{second}\t2026-11-11\tre-import\n"
                   f"{OLD_HASH}\t{first}\t2026-11-10\tthe import squash\n")
        later = self.commit("later.py")
        self.assert_fails_with(f"trace/ledger/{later}.md: no ledger entry for the product "
                               f"commit {later}")


ID_MAP_HEADER = "old\tnew\n"


class CompletenessTest(TraceCase):
    """The IDs that the product's comments held before they moved into the trace."""

    def pre_import(self, files):
        """Commit files into the product, name that commit in trace/pre-import, then
        take the files out again, as the move does, and return the commit."""
        for name, text in files.items():
            self.write(self.product / name, text)
        self.git_commit_all(self.product)
        commit = self.head()
        for name in files:
            if name == "src/tool.py":
                self.write(self.product / name, TOOL_TEXT)
            else:
                (self.product / name).unlink()
        self.git_commit_all(self.product)
        self.write(self.dev / "trace/pre-import", commit + "\n")
        return commit

    def test_complete_tree_passes(self):
        # The comment before main cited a requirement, and the trace of that file
        # carries it.
        self.pre_import({"src/tool.py": "# R-1 says why main exists.\n" + TOOL_TEXT})
        self.assert_passes()

    def test_id_the_trace_does_not_carry_fails_with_its_line(self):
        # Line 7 of the old tool cited a control that no entry of its trace holds.
        self.pre_import({"src/tool.py": TOOL_TEXT + "# C-core-2 guards this.\n"})
        self.assert_fails_with("src/tool.py:7: C-core-2 of the tree of trace/pre-import is "
                               "in no entry of trace/src/tool.py.json")

    def test_malformed_id_map_fails_with_its_line_only(self):
        # A space after the comma breaks the map; the uncarried ID gives no line.
        self.pre_import({"src/tool.py": TOOL_TEXT + "# C-core-2 guards this.\n"})
        self.write(self.dev / "specs/id-map.tsv", ID_MAP_HEADER + "F-7\tR-7, R-40\n")
        self.assert_fails_with("specs/id-map.tsv: line 2: the new ID ' R-40' does not have "
                               "the general ID form; new IDs are separated by commas, with no "
                               "spaces")

    def test_malformed_id_map_with_no_pre_import_fails_with_its_line(self):
        # With nothing to read it through, the map is still read and refused.
        self.write(self.dev / "specs/id-map.tsv",
                   ID_MAP_HEADER + "F-7\tR-7\nF-7\tR-40\n")
        self.assert_fails_with("specs/id-map.tsv: line 3 repeats the old ID F-7, of line 2")

    def test_no_pre_import_and_no_id_map_passes(self):
        # Without either file there was no move to check.
        self.assertFalse((self.dev / "trace/pre-import").exists())
        self.assert_passes()

    def test_pre_import_in_another_form_fails_with_one_line(self):
        # A short hash is not the full hash of a commit.
        commit = self.pre_import({"src/tool.py": "# R-1 says why.\n" + TOOL_TEXT})
        self.write(self.dev / "trace/pre-import", commit[:7] + "\n")
        self.assert_fails_with("trace/pre-import: does not hold one line with the full hash "
                               "of a product commit")

    def test_pre_import_hash_not_in_the_product_fails_with_one_line(self):
        # A well-formed hash that names no commit of the product.
        self.write(self.dev / "trace/pre-import", "f" * 40 + "\n")
        self.assert_fails_with(f"trace/pre-import: {'f' * 40} is not a commit of the product")

    def test_old_id_needs_its_new_ids_in_the_trace_not_itself(self):
        # The old comment cited an old ID that the map split into two new ones. Both
        # are missing at first, and the old ID itself is never asked for.
        self.write(self.dev / "specs/jig-spec.md", SPEC_TEXT + "Then R-7 and R-40.\n")
        self.write(self.dev / "specs/id-map.tsv", ID_MAP_HEADER + "F-7\tR-7,R-40\n")
        self.pre_import({"src/tool.py": "# F-7 says why.\n" + TOOL_TEXT})
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr.splitlines(), [
            PREFIX + f"src/tool.py:1: {record} of the tree of trace/pre-import is in no entry "
            "of trace/src/tool.py.json" for record in ("R-7", "R-40")])
        self.trace("src/tool.py", [entry(1, "def main():", ["R-1", "R-7", "R-40"])])
        self.assert_passes()

    def test_dropped_id_needs_nothing(self):
        # The cited ID was dropped. Its class would find it, but the old ID found it
        # first and reads as no ID at all.
        self.write(self.dev / "specs/id-map.tsv", ID_MAP_HEADER + "R-8\t\n")
        self.pre_import({"src/tool.py": "# R-8 is gone.\n" + TOOL_TEXT})
        self.assert_passes()

    def test_longest_old_id_first_and_no_match_inside_an_earlier_one(self):
        # The cited old ID, with a dotted suffix, holds a shorter old ID, which its
        # class also finds. Only the longest counts, so the trace needs the new ID of
        # the dotted one and not the new ID of the shorter one.
        self.write(self.dev / "specs/jig-spec.md", SPEC_TEXT + "Then R-91 and R-90.\n")
        self.write(self.dev / "specs/id-map.tsv",
                   ID_MAP_HEADER + "R-9\tR-90\nR-9.1\tR-91\n")
        self.pre_import({"src/tool.py": "# R-9.1 says why.\n" + TOOL_TEXT})
        self.trace("src/tool.py", [entry(1, "def main():", ["R-1", "R-91"])])
        self.assert_passes()

    def test_submodule_entry_at_a_path_with_a_space_is_skipped(self):
        # The old tree holds a submodule entry, whose commit the clone does not
        # have, at a path with a space, and a file that cites an uncarried control.
        # The submodule gives no line and no crash; the file still gives its line.
        self.write(self.product / "src/tool.py", TOOL_TEXT + "# C-core-2 guards this.\n")
        git_ok(self.env(), "-C", self.product, "add", "src/tool.py")
        git_ok(self.env(), "-C", self.product, "update-index", "--add", "--cacheinfo",
               f"160000,{OLD_HASH},lib/the sub")
        # Not git add -A, which would drop the entry: no directory stands for it.
        git_ok(self.env(), "-C", self.product, "commit", "-q", "--no-verify", "-m", "fixture")
        self.assertIn("lib/the sub", git_ok(self.env(), "-C", self.product, "ls-tree", "-r",
                                            "--name-only", "HEAD"))
        self.write(self.dev / "trace/pre-import", self.head() + "\n")
        self.assert_fails_with("src/tool.py:7: C-core-2 of the tree of trace/pre-import is "
                               "in no entry of trace/src/tool.py.json")

    def test_pre_import_that_is_a_directory_fails_with_one_line(self):
        # A directory where the one-line file belongs is refused in one line.
        (self.dev / "trace/pre-import").mkdir(parents=True)
        self.assert_fails_with("trace/pre-import: is a directory, not a file that holds one "
                               "line")

    def test_only_files_still_in_the_product_are_searched(self):
        # The old tree had a file that cited a control, and the product has since
        # removed it, so its IDs need no trace.
        self.pre_import({"src/gone.py": "# C-core-2 guarded this.\nx = 1\n"})
        self.assert_passes()


class UsageTest(TraceCase):

    def test_no_subcommand_is_a_usage_error(self):
        result = run([TRACE], env=self.env(), cwd=self.dev)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: jig trace check", result.stderr)

    def test_gen_with_no_path_and_form_without_one_commit_are_usage_errors(self):
        # gen needs a file to read and form exactly one commit; anything else is
        # a usage error, exit 2, with the usage line.
        for args in (["gen"], ["form"], ["form", "HEAD", "HEAD~1"]):
            result = self.check(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn("usage: jig trace check", result.stderr)

    def test_check_with_an_argument_is_a_usage_error(self):
        result = self.check("check", "extra")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: jig trace check", result.stderr)


class CommitMapReaderTest(TraceCase):
    """The read-through that records citing a hash use: a fixture map over the product."""

    def setUp(self):
        super().setUp()
        self.new = self.head()
        path = self.dev / "trace/commit-map.tsv"
        self.write(path, f"{MAP_HEADER}{OLD_HASH}\t{self.new}\t2026-11-10\tthe import squash\n")
        self.map = jig_trace.CommitMap.read(path)

    def test_hash_in_the_product_reads_as_itself(self):
        # A product commit, cited by its full hash or by seven digits, reads as itself.
        self.assertEqual(self.map.resolve(self.product, self.new), self.new)
        self.assertEqual(self.map.resolve(self.product, self.new[:7]), self.new)

    def test_old_hash_of_a_row_reads_as_the_new_hash(self):
        # A commit the rewrite replaced reads as its replacement, also when abbreviated.
        self.assertEqual(self.map.resolve(self.product, OLD_HASH), self.new)
        self.assertEqual(self.map.resolve(self.product, OLD_HASH[:8]), self.new)

    def test_unknown_hash_is_unresolved(self):
        # A hash that is neither in the product nor in the map gives nothing.
        self.assertIsNone(self.map.resolve(self.product, "abcdef0" * 5 + "abcde"))
        self.assertIsNone(self.map.resolve(self.product, "not a hash"))

    def test_dropped_commit_is_not_the_import_and_reads_as_unresolved(self):
        # A row with no new hash marks a commit the rewrite dropped: the import is the
        # other row's new hash, and the dropped hash leads nowhere.
        dropped = "abcdef12" * 5
        path = self.dev / "trace/commit-map.tsv"
        self.write(path, f"{MAP_HEADER}{dropped}\t\t2026-11-10\tdropped in the squash\n"
                         f"{OLD_HASH}\t{self.new}\t2026-11-10\tthe import squash\n")
        commits = jig_trace.CommitMap.read(path)
        self.assertEqual(commits.import_commit(self.product), self.new)
        self.assertIsNone(commits.resolve(self.product, dropped))
        self.assertEqual(commits.resolve(self.product, OLD_HASH), self.new)

    def test_reader_refuses_a_date_in_another_form(self):
        # The date must be written as year, month and day with hyphens.
        path = self.dev / "trace/commit-map.tsv"
        self.write(path, f"{MAP_HEADER}{OLD_HASH}\t{self.new}\t10.11.2026\tsquash\n")
        with self.assertRaises(jig_trace.FormError):
            jig_trace.CommitMap.read(path)


# A Python product file for gen. Its comments: a header before any code, a block
# of two lines before a function that also holds a word shaped almost like an ID,
# a comment at the end of a code line, a comment with no ID, a docstring that is
# not a comment, a comment before the second of two equal lines, and a last
# comment with no code after it.
COUNT_LINES = [
    "# Counts the lines of a file, after R-1.",
    "import sys",
    "",
    "",
    "# Takes the lines as a list, R-1;",
    "# the old name R-1a is gone.",
    "def count(lines):",
    "    return len(lines)  # The length is the count, C-core-2.",
    "",
    "",
    "# A plain note with no record in it.",
    "def first(lines):",
    '    """Return the first line, as R-1 asks."""',
    "    return 0",
    "",
    "",
    "def last(lines):",
    "    # The second return of the file, for R-1.",
    "    return 0",
    "# Nothing follows this note on R-1.",
]
COUNT_ENTRIES = [
    entry(0, "", ["R-1"], comments=["Counts the lines of a file, after R-1."]),
    entry(7, "def count(lines):", ["R-1"],
          comments=["Takes the lines as a list, R-1;", "the old name R-1a is gone."]),
    entry(8, "return len(lines)  # The length is the count, C-core-2.", ["C-core-2"],
          comments=["The length is the count, C-core-2."]),
    entry(19, "return 0", ["R-1"], nth=2, comments=["The second return of the file, for R-1."]),
    entry(0, "", ["R-1"], comments=["Nothing follows this note on R-1."]),
]
# A bash product file: a header right below the shebang, and a comment at the end
# of a code line.
RUN_LINES = [
    "#!/bin/bash",
    "# Runs the tool, after R-1.",
    "set -eu",
    "",
    "main() {",
    '    echo "go"  # Says go, C-core-2.',
    "}",
]
RUN_ENTRIES = [
    entry(0, "", ["R-1"], comments=["Runs the tool, after R-1."]),
    entry(6, 'echo "go"  # Says go, C-core-2.', ["C-core-2"],
          comments=["Says go, C-core-2."]),
]


class GenTest(TraceCase):
    """jig trace gen on a Python and a bash file of the fixture product."""

    def setUp(self):
        super().setUp()
        self.write(self.product / "src/count.py", "\n".join(COUNT_LINES) + "\n")
        self.write(self.product / "src/run.sh", "\n".join(RUN_LINES) + "\n")
        self.write(self.product / "README.md", "# Demo\n\nSee R-1.\n")
        self.git_commit_all(self.product)

    def gen(self, *paths):
        return run([TRACE, "gen", *paths], env=self.env(), cwd=self.dev)

    def entries(self, product_path):
        path = self.dev / "trace" / f"{product_path}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_gen_ties_python_comments_to_the_next_code_line_or_the_file(self):
        # A block of comments goes to the code line after it, a comment at the end
        # of a line to that line, a comment with no code before or after it to the
        # file as a whole; a comment with no ID and the docstring get no entry,
        # and the word that only looks like an ID stays out of the IDs.
        result = self.gen("src/count.py")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.entries("src/count.py"), COUNT_ENTRIES)

    def test_gen_reads_a_bash_file_and_skips_its_shebang(self):
        # The header below the shebang has no code line before it, so it is
        # file-level; the comment at the end of the echo line is tied to it.
        result = self.gen("src/run.sh")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.entries("src/run.sh"), RUN_ENTRIES)

    def test_gen_changes_no_product_file(self):
        # After gen, the product clone is as it was: nothing new, nothing changed.
        before = {name: (self.product / name).read_bytes()
                  for name in ("src/count.py", "src/run.sh")}
        self.assertEqual(self.gen("src/count.py", "src/run.sh").returncode, 0)
        status = git_ok(self.env(), "-C", self.product, "status", "--porcelain",
                        "--untracked-files=all")
        self.assertEqual(status, "")
        self.assertEqual({name: (self.product / name).read_bytes() for name in before}, before)

    def test_second_run_adds_no_entry_and_changes_no_byte(self):
        # Running gen again on the same file leaves the trace file exactly as it was.
        self.assertEqual(self.gen("src/count.py").returncode, 0)
        path = self.dev / "trace" / "src/count.py.json"
        first = path.read_bytes()
        self.assertEqual(self.gen("src/count.py").returncode, 0)
        self.assertEqual(path.read_bytes(), first)

    def test_moved_comment_updates_its_entry_in_place(self):
        # A code line added above the function moves it down by one; its entry
        # keeps its place in the file and takes the new line number.
        self.assertEqual(self.gen("src/count.py").returncode, 0)
        moved = COUNT_LINES[:2] + ["import os"] + COUNT_LINES[2:]
        self.write(self.product / "src/count.py", "\n".join(moved) + "\n")
        self.assertEqual(self.gen("src/count.py").returncode, 0)
        expected = [dict(item, line=item["line"] + 1 if item["line"] else 0)
                    for item in COUNT_ENTRIES]
        self.assertEqual(self.entries("src/count.py"), expected)

    def test_gen_then_check_passes(self):
        # Every ID gen wrote is of a class of the fixture ID classes and in the
        # fixture spec, so the check passes on the result.
        self.assertEqual(self.gen("src/count.py", "src/run.sh").returncode, 0)
        self.assert_passes()

    def test_gen_fails_one_line_for_each_file_it_cannot_read(self):
        # A missing file, a file outside the product and a file with no comment
        # signs each give one line and exit 1, and the good file is still written.
        result = self.gen("src/gone.py", "../outside.py", "README.md", "src/count.py")
        self.assertEqual(result.returncode, 1)
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 3, result.stderr)
        self.assertIn("jig trace gen: src/gone.py: the product has no file", lines[0])
        self.assertIn("jig trace gen: ../outside.py: is outside the product", lines[1])
        self.assertIn("jig trace gen: README.md: is neither Python nor shell", lines[2])
        self.assertFalse((self.dev / "trace" / "README.md.json").exists())
        self.assertEqual(self.entries("src/count.py"), COUNT_ENTRIES)


# A class with two methods far enough apart that each change gets its own hunk.
# git names a hunk after an indented method only with the Python rule that form
# turns on; its default rule would name the class twice.
def shapes(value):
    body = ["        a = 1", "        b = 2", "        c = 3", f"        d = {value}",
            "        e = 5", "        f = 6", "        return a"]
    return "\n".join(["class Box:", "    def width(self):", *body, "",
                      "    def height(self):", *body]) + "\n"


class FormTest(TraceCase):
    """jig trace form on a fixture commit that changes two methods and a text file."""

    def setUp(self):
        super().setUp()
        self.write(self.product / "src/shapes.py", shapes(4))
        self.write(self.product / "notes.txt", "one\n")
        self.git_commit_all(self.product)
        self.write(self.product / "src/shapes.py", shapes(40))
        self.write(self.product / "notes.txt", "two\n")
        self.git_commit_all(self.product)

    def form(self, commit):
        return run([TRACE, "form", commit], env=self.env(), cwd=self.dev)

    def test_form_prints_a_row_per_function_and_a_row_for_a_file_with_none(self):
        # Each method git names in a hunk header has its row, the text file whose
        # one hunk names nothing has a row with its path, and every cell to fill
        # holds the fill marker.
        commit = self.head()
        result = self.form("HEAD")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(result.stdout.splitlines(), [
            f"commit: {commit}",
            "unit: <fill>",
            "",
            "| file | function | rules |",
            "| --- | --- | --- |",
            "| notes.txt | - | <fill> |",
            "| src/shapes.py | def width(self): | <fill> |",
            "| src/shapes.py | def height(self): | <fill> |",
        ])

    def test_form_of_a_merge_compares_it_with_its_first_parent(self):
        # A side branch adds one file, the main line another, and a merge joins
        # them. Against its first parent the merge brings only the side's file,
        # though it differs from neither parent alone in a combined view.
        git_ok(self.env(), "-C", self.product, "checkout", "-q", "-b", "side")
        self.commit("src/side.py")
        git_ok(self.env(), "-C", self.product, "checkout", "-q", "master")
        self.commit("main.py")
        git_ok(self.env(), "-C", self.product, "merge", "-q", "--no-ff", "-m", "join", "side")
        result = self.form("HEAD")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(result.stdout.splitlines()[3:],
                         ["| file | function | rules |", "| --- | --- | --- |",
                          "| src/side.py | - | <fill> |"])

    def test_form_of_the_root_commit_compares_it_with_the_empty_tree(self):
        # A user setting that hides the diff of the first commit must not empty
        # the outline: each file the first commit adds has its row.
        root = git_ok(self.env(), "-C", self.product, "rev-list", "--max-parents=0",
                      "HEAD").strip()
        result = run([TRACE, "form", root], cwd=self.dev,
                     env=self.env(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="log.showRoot",
                                  GIT_CONFIG_VALUE_0="false"))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(result.stdout.splitlines()[3:],
                         ["| file | function | rules |", "| --- | --- | --- |",
                          "| src/tool.py | - | <fill> |", "| tests/test_tool.py | - | <fill> |"])

    def test_form_writes_nothing(self):
        # The outline goes to standard output only: the product stays clean and
        # the -dev clone gets no ledger note.
        self.assertEqual(self.form("HEAD").returncode, 0)
        status = git_ok(self.env(), "-C", self.product, "status", "--porcelain",
                        "--untracked-files=all")
        self.assertEqual(status, "")
        self.assertFalse((self.dev / "trace" / "ledger").exists())

    def test_form_of_a_commit_not_in_the_product_fails_with_one_line(self):
        result = self.form(OLD_HASH)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.splitlines(),
                         [f"jig trace form: {OLD_HASH}: is not a commit of the product"])


# The records of a fixture project with one class for each coverage check, all of
# them complete. The reference class P is defined by a glob; the class N, with no
# coverage check, only has to be defined once.
COVERAGE_CLASSES = (IDS_HEADER
                    + "R\tR-[0-9]+\tspecs/requirements.md\trequirement\n"
                    + "C\tC-[a-z]+-[0-9]+\tspecs/03-controls.md\tcontrol\n"
                    + "X\tX-[0-9]+\tspecs/fixtures.md\tfixture\n"
                    + "P\tP[0-9]+-D[0-9]+\tspecs/0*-plan.md\treference\n"
                    + "Q\tQ-[0-9]+\tQUESTIONS.md\treference\n"
                    + "N\tN-[0-9]+\tspecs/notes.md, specs/more-notes.md\tnone\n")
REQUIREMENTS = "# Requirements\n\n### R-1 Start\n\nOwner: jig-spec.md\n\n## Later\n"
CONTROLS = ("# Controls\n\n| Control | Guards | Mutation |\n| --- | --- | --- |\n"
            "| C-core-2 | R-1 | the patch drops the guard |\n")
COVERAGE_SPEC = SPEC_TEXT + "The fixture X-1 feeds it, as P2-D1 chose.\n"


class CoverageTest(TraceCase):
    """jig trace coverage on a fixture project whose records are complete."""

    def setUp(self):
        super().setUp()
        self.write(self.dev / "specs/ids.tsv", COVERAGE_CLASSES)
        self.write(self.dev / "specs/requirements.md", REQUIREMENTS)
        self.write(self.dev / "specs/03-controls.md", CONTROLS)
        self.write(self.dev / "specs/fixtures.md",
                   "| Fixture | What |\n| --- | --- |\n| X-1 | a project with no network |\n")
        self.write(self.dev / "specs/02-plan.md", "- **P2-D1** (Eric, 2026-11-10): one tool.\n")
        self.write(self.dev / "specs/notes.md", "- N-1 a note\n")
        self.write(self.dev / "specs/jig-spec.md", COVERAGE_SPEC)
        self.write(self.dev / "QUESTIONS.md", "# Questions\n\n### Q-1 Which form?\n")
        # The question is referenced by the trace only, and the test of the control
        # has a name that holds no control ID: its entry alone says what it tests.
        self.trace("src/tool.py", [entry(1, "def main():", ["R-1", "Q-1"])])
        self.trace("tests/test_tool.py",
                   [entry(5, "def test_main_returns_zero(self):", ["C-core-2"])])

    def coverage(self, *extra):
        return run([TRACE, "coverage", *extra], env=self.env(), cwd=self.dev)

    def assert_covered(self):
        result = self.coverage()
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def assert_gap(self, *lines):
        """Coverage exits 1 and prints exactly these lines, with nothing before them."""
        result = self.coverage()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr.splitlines(), list(lines))

    def test_complete_records_pass(self):
        # Every class has what its check asks for.
        self.assert_covered()

    def test_no_id_classes_file_passes(self):
        # With no classes there is nothing to check, even with a gap in the records.
        self.write(self.dev / "specs/requirements.md", "### R-1 Start\n")
        (self.dev / "specs/ids.tsv").unlink()
        self.assert_covered()

    def test_no_product_clone_is_not_a_failure(self):
        # Coverage reads the records only, so the product clone may be missing.
        shutil.rmtree(self.product)
        self.assert_covered()

    def test_test_named_without_a_control_id_counts_for_its_entry(self):
        # The product's test is named in plain words; its trace entry holds the
        # control, and that alone covers the control.
        self.assertNotIn("core", TEST_TEXT)
        self.assert_covered()

    def test_requirement_with_no_owner_line(self):
        # The heading of the requirement has no Owner line before the next heading.
        self.write(self.dev / "specs/requirements.md", "# Requirements\n\n### R-1 Start\n")
        self.assert_gap("specs/requirements.md:3: R-1: no owner")

    def test_requirement_with_two_owner_lines(self):
        # Two Owner lines under one heading give one line for the heading.
        self.write(self.dev / "specs/requirements.md",
                   REQUIREMENTS.replace("## Later", "Owner: notes.md\n\n## Later"))
        self.assert_gap("specs/requirements.md:3: R-1: two owners")

    def test_owner_that_is_not_a_spec_document(self):
        # The status file sits under specs but is not a spec document.
        self.write(self.dev / "specs/00-status.md", "R-1 is open.\n")
        self.write(self.dev / "specs/requirements.md",
                   REQUIREMENTS.replace("jig-spec.md", "00-status.md"))
        self.assert_gap("specs/requirements.md:3: R-1: owner not a spec document")

    def test_owner_that_does_not_name_the_requirement(self):
        # The owner is a spec document, but it never writes the requirement.
        self.write(self.dev / "specs/requirements.md",
                   REQUIREMENTS.replace("jig-spec.md", "notes.md"))
        self.assert_gap("specs/requirements.md:3: R-1: not in its owner")

    def test_requirement_with_no_control_row(self):
        # The control guards only a longer ID that starts with the requirement's ID.
        self.write(self.dev / "specs/03-controls.md", CONTROLS.replace("| R-1 |", "| R-10 |"))
        self.assert_gap("specs/requirements.md:3: R-1: no control row")

    def test_control_table_with_no_mutation_column(self):
        # The table of the control has no column whose header starts with Mutation.
        self.write(self.dev / "specs/03-controls.md",
                   "| Control | Guards |\n| --- | --- |\n| C-core-2 | R-1 |\n")
        self.assert_gap("specs/03-controls.md:3: C-core-2: no mutation cell")

    def test_control_whose_mutation_cell_holds_only_spaces(self):
        # The column is there, but the cell of the control is blank.
        self.write(self.dev / "specs/03-controls.md",
                   CONTROLS.replace("the patch drops the guard", "   "))
        self.assert_gap("specs/03-controls.md:5: C-core-2: no mutation cell")

    def test_control_with_no_test_in_the_trace(self):
        # No trace entry holds the control. A product test whose name spells the
        # control does not help, because coverage never reads test names.
        self.write(self.product / "tests/test_tool.py",
                   TEST_TEXT + "\n    def test_c_core_2(self):\n        pass\n")
        self.trace("tests/test_tool.py",
                   [entry(5, "def test_main_returns_zero(self):", ["R-1"])])
        self.assert_gap("specs/03-controls.md:5: C-core-2: no test")

    def test_fixture_used_in_a_spec_document_but_not_defined(self):
        # The spec uses a fixture on its line 5, and the fixture table has no row for
        # it.
        self.write(self.dev / "specs/jig-spec.md", COVERAGE_SPEC + "Also X-2 here.\n")
        self.assert_gap("specs/jig-spec.md:5: X-2: not defined")

    def test_fixture_in_a_trace_entry_but_not_defined(self):
        # The line of the failure is the line of the entry in the trace file.
        self.trace("src/tool.py", [entry(1, "def main():", ["R-1", "Q-1"]),
                                   entry(6, "return 0", ["X-3"])])
        self.assert_gap("trace/src/tool.py.json:6: X-3: not defined")

    def test_decision_referenced_only_by_its_own_definition(self):
        # A second decision is written only on the line that defines it, which is no
        # reference.
        self.write(self.dev / "specs/02-plan.md",
                   "- **P2-D1** (Eric, 2026-11-10): one tool.\n- P2-D2 (Eric): two.\n")
        self.assert_gap("specs/02-plan.md:2: P2-D2: not referenced")

    def test_id_defined_twice_in_a_class_with_no_coverage_check(self):
        # The second definition, in another file the class names, gives the line.
        self.write(self.dev / "specs/more-notes.md", "# Notes\n\n## N-1 again\n")
        self.assert_gap("specs/more-notes.md:3: N-1: defined twice")

    def test_id_classes_file_of_another_form_gives_only_its_line(self):
        # A missing column hides every other gap, here a requirement with no owner.
        self.write(self.dev / "specs/requirements.md", "### R-1 Start\n")
        self.write(self.dev / "specs/ids.tsv", "class\tregex\tdefined\nR\tR-[0-9]+\tspecs\n")
        self.assert_gap("specs/ids.tsv: line 1 is not the header line class, regex, "
                        "defined, coverage, separated by tabs")

    def test_trace_file_of_another_shape_gives_its_line(self):
        # A trace file that is an object, not an array, is named in one line.
        self.write(self.dev / "trace/src/other.py.json", json.dumps({"line": 1}))
        self.assert_gap("trace/src/other.py.json: is not a JSON array of entries")

    def test_argument_is_a_usage_error(self):
        # Coverage takes no argument.
        result = self.coverage("specs")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("coverage takes no arguments", result.stderr)


class IdClassFormTest(TraceCase):
    """The two form rules of specs/ids.tsv that both check and coverage refuse."""

    def assert_both_refuse(self, line):
        coverage = run([TRACE, "coverage"], env=self.env(), cwd=self.dev)
        self.assertEqual((coverage.returncode, coverage.stderr.splitlines()), (1, [line]))
        self.assert_fails_with(line)

    def test_coverage_word_outside_the_five_is_a_form_failure(self):
        # The old fixture word "mutation cell" names no coverage check.
        self.write(self.dev / "specs/ids.tsv",
                   IDS_HEADER + "C\tC-[a-z]+-[0-9]+\tspecs/03-controls.md\tmutation cell\n")
        self.assert_both_refuse("specs/ids.tsv: line 2: the coverage 'mutation cell' of class "
                                "C is not one of requirement, control, fixture, reference, "
                                "none")

    def test_empty_defined_cell_is_a_form_failure(self):
        # A class must name the files that define its IDs.
        self.write(self.dev / "specs/ids.tsv", IDS_HEADER + "R\tR-[0-9]+\t \trequirement\n")
        self.assert_both_refuse("specs/ids.tsv: line 2: class R names no file in defined")


if __name__ == "__main__":
    unittest.main()
