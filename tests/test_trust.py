"""The trust mark of Claude Code, and the folders of the section Directories.

The mark is set in a fixture home's .claude.json, never in the home of the account
that runs the tests. The kickoff records are text, with fixture homes for the accounts.
"""

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

from tests.support import ROOT, TempTestCase

sys.path.insert(0, str(ROOT / "lib"))

import jig_dirs  # noqa: E402
import jig_trust  # noqa: E402

PROJECT = "demo"


class TrustMarkCase(TempTestCase):
    def setUp(self):
        super().setUp()
        self.folder = str(self.home / "ws" / "src" / f"{PROJECT}-dev")
        self.state = self.home / ".claude.json"

    def mark(self, **extra):
        return jig_trust.mark(self.folder, "implementer", home=self.home, **extra)

    def write(self, data, mode=0o600):
        text = data if isinstance(data, str) else json.dumps(data)
        self.state.write_text(text, encoding="utf-8")
        self.state.chmod(mode)

    def read(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def entry(self):
        return self.read()["projects"][self.folder]

    def assert_unusable(self, data, reason):
        # The step leaves the file as it was, and names the account, the file and why.
        self.write(data)
        before = self.state.read_bytes()
        value, problem = self.mark()
        self.assertEqual(value, "not marked")
        self.assertIn(f"{self.state} of implementer", problem)
        self.assertIn(reason, problem)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(len(problem.splitlines()), 1)


class TrustMarkTest(TrustMarkCase):
    def test_a_missing_file_is_made_with_mode_600_and_the_mark(self):
        self.assertEqual(self.mark(), ("trusted", None))
        self.assertEqual(self.read(), {"projects": {self.folder: {
            "hasTrustDialogAccepted": True}}})
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_a_file_without_projects_gets_them_and_keeps_its_keys(self):
        self.write({"numStartups": 7, "theme": "dark"})
        self.assertEqual(self.mark(), ("trusted", None))
        self.assertEqual(self.read(), {"numStartups": 7, "theme": "dark", "projects": {
            self.folder: {"hasTrustDialogAccepted": True}}})

    def test_a_missing_entry_is_made_beside_the_other_entries(self):
        self.write({"projects": {"/elsewhere": {"hasTrustDialogAccepted": False}}})
        self.assertEqual(self.mark(), ("trusted", None))
        self.assertEqual(self.read()["projects"], {
            "/elsewhere": {"hasTrustDialogAccepted": False},
            self.folder: {"hasTrustDialogAccepted": True}})

    def test_an_entry_without_the_key_gets_it_and_keeps_its_other_keys(self):
        self.write({"projects": {self.folder: {"allowedTools": ["x"],
                                               "hasTrustDialogAccepted": False}}})
        self.assertEqual(self.mark(), ("trusted", None))
        self.assertEqual(self.entry(), {"allowedTools": ["x"], "hasTrustDialogAccepted": True})

    def test_a_mark_that_is_there_writes_nothing(self):
        self.write({"projects": {self.folder: {"hasTrustDialogAccepted": True}}}, mode=0o640)
        before = os.stat(self.state)
        self.assertEqual(self.mark(), ("kept", None))
        after = os.stat(self.state)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), [".claude.json"])

    def test_the_mode_of_the_old_file_is_kept(self):
        self.write({}, mode=0o640)
        self.assertEqual(self.mark(), ("trusted", None))
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o640)

    def test_the_new_file_replaces_the_old_one_by_a_rename(self):
        # A reader that holds the old file keeps the whole old text: the step does not
        # write into the file in place.
        self.write({"theme": "dark"})
        before = self.state.stat().st_ino
        with open(self.state, encoding="utf-8") as old:
            self.assertEqual(self.mark(), ("trusted", None))
            self.assertEqual(json.load(old), {"theme": "dark"})
        self.assertNotEqual(self.state.stat().st_ino, before)
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), [".claude.json"])

    def test_the_new_file_is_on_the_disk_with_all_its_data_before_the_rename(self):
        # A power loss just after the rename must not leave the file empty or cut off,
        # so the new file is synced, whole, before it takes the old one's name.
        self.write({"theme": "dark"})
        calls = []
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(descriptor):
            calls.append(("fsync", os.fstat(descriptor).st_size))
            real_fsync(descriptor)

        def replace(source, target):
            calls.append(("replace", os.stat(source).st_size))
            real_replace(source, target)

        with mock.patch.object(jig_trust.os, "fsync", fsync), \
                mock.patch.object(jig_trust.os, "replace", replace):
            self.assertEqual(self.mark(), ("trusted", None))
        size = self.state.stat().st_size
        self.assertEqual(calls, [("fsync", size), ("replace", size)])

    def test_the_key_of_an_added_folder_is_written_when_it_is_asked_for(self):
        self.assertEqual(self.mark(key="someOtherKey"), ("trusted", None))
        self.assertEqual(self.entry(), {"someOtherKey": True})

    def test_a_file_that_is_not_json_is_left_as_it_is(self):
        self.assert_unusable('{"projects": {', "it is not JSON")

    def test_a_top_level_that_is_not_an_object_is_left_as_it_is(self):
        self.assert_unusable([1, 2], "its top level is not an object")

    def test_a_projects_value_that_is_not_an_object_is_left_as_it_is(self):
        self.assert_unusable({"projects": ["a"]}, 'its value of "projects" is not an object')

    def test_an_entry_that_is_not_an_object_is_left_as_it_is(self):
        self.assert_unusable({"projects": {self.folder: True}}, "is not an object")

    @unittest.skipIf(os.geteuid() == 0, "root writes in a directory of any mode")
    def test_a_file_that_cannot_be_written_is_left_as_it_is(self):
        # The new file cannot be made beside the old one in a home without write access.
        self.write({"theme": "dark"})
        self.home.chmod(0o555)
        self.addCleanup(self.home.chmod, 0o755)
        value, problem = self.mark()
        self.assertEqual(value, "not marked")
        self.assertIn(f"{self.state} of implementer: cannot write it", problem)
        self.assertEqual(self.read(), {"theme": "dark"})

    def test_a_file_of_another_account_is_left_as_it_is(self):
        self.write({"theme": "dark"})
        with mock.patch.object(jig_trust.os, "getuid", return_value=os.getuid() + 1):
            value, problem = self.mark()
        self.assertEqual(value, "not marked")
        self.assertIn("it belongs to another account", problem)
        self.assertEqual(self.read(), {"theme": "dark"})

    def test_a_read_after_the_rename_without_the_mark_is_not_marked(self):
        # As when a Claude Code session saves its older copy of the file just after
        # the rename: the step reads the file again and reports the mark as not set.
        self.write({"theme": "dark"})
        old = self.state.read_text(encoding="utf-8")
        real_replace = os.replace

        def replace_then_lose(source, target):
            real_replace(source, target)
            self.state.write_text(old, encoding="utf-8")

        with mock.patch.object(jig_trust.os, "replace", replace_then_lose):
            value, problem = self.mark()
        self.assertEqual(value, "not marked")
        self.assertIn(f"{self.state} of implementer: the mark is not there", problem)
        # The next run finds no mark, and sets it again.
        self.assertEqual(self.mark(), ("trusted", None))

    def test_the_command_prints_the_trust_line_and_exits_0_also_when_not_marked(self):
        self.write("not json")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), \
                contextlib.redirect_stdout(out):
            self.assertEqual(jig_trust.main(["implementer", self.folder]), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn(f"{self.state} of implementer", lines[0])
        self.assertEqual(lines[1], "trust implementer: not marked")


class DirectoriesCase(unittest.TestCase):
    homes = {"orchestrator": "/h/orch", "specwriter": "/h/spec", "implementer": "/h/impl"}

    def read(self, *lines, homes=None):
        text = "\n".join(["# Kickoff record", "", "## Directories", "",
                          "The folders beyond the defaults.", *lines, "", "## Next", ""])
        return jig_dirs.read_directories(text, homes or self.homes, PROJECT)

    def unreadable(self, line):
        # The section's lines start at line 6 of the record.
        folders, problems = self.read(line)
        self.assertEqual(folders, {"orchestrator": [], "specwriter": [], "implementer": []})
        self.assertEqual(problems, [jig_dirs.UNREADABLE.format(number=6)])
        self.assertEqual(problems[0], "jig: cannot read line 6 of the section Directories "
                                      "of the kickoff record; line ignored")


class DirectoriesTest(DirectoriesCase):
    def test_no_record_and_no_section_give_no_folder(self):
        empty = {"orchestrator": [], "specwriter": [], "implementer": []}
        self.assertEqual(jig_dirs.read_directories(None, self.homes, PROJECT), (empty, []))
        self.assertEqual(jig_dirs.read_directories("# Kickoff\n\nimplementer: /data\n",
                                                   self.homes, PROJECT), (empty, []))

    def test_a_folder_of_each_account_in_its_own_home(self):
        folders, problems = self.read("orchestrator: ~/notes", "specwriter: /srv/oracle ",
                                      "implementer:  ~/data/set")
        self.assertEqual(problems, [])
        self.assertEqual(folders, {"orchestrator": ["/h/orch/notes"],
                                   "specwriter": ["/srv/oracle"],
                                   "implementer": ["/h/impl/data/set"]})

    def test_only_lines_of_the_form_inside_the_section_count(self):
        text = "\n".join(["implementer: /before", "## Directories", "Prose: not a line.",
                          " implementer: /indented", "implementer /no-colon", "reviewer: /x",
                          "implementer: /inside", "## Other", "implementer: /after"])
        folders, problems = jig_dirs.read_directories(text, self.homes, PROJECT)
        self.assertEqual(folders["implementer"], ["/inside"])
        self.assertEqual(problems, [])

    def test_two_lines_with_one_folder_give_one_folder(self):
        folders, problems = self.read("implementer: ~/data", "implementer: /h/impl/data/",
                                      "implementer: ~/data")
        self.assertEqual(folders["implementer"], ["/h/impl/data"])
        self.assertEqual(problems, [])

    def test_the_spec_writer_may_get_a_folder_of_the_reference(self):
        folders, problems = self.read("specwriter: ~/ws/ref/demo/extra")
        self.assertEqual((folders["specwriter"], problems), (["/h/spec/ws/ref/demo/extra"], []))

    def test_an_empty_folder_is_not_read(self):
        # The spec writer has no kept folder, so only the empty folder stops the line.
        self.unreadable("specwriter:   ")

    def test_a_folder_without_a_root_or_home_is_not_read(self):
        self.unreadable("implementer: data/set")

    def test_a_part_dot_dot_is_not_read(self):
        self.unreadable("implementer: ~/data/../ws/ref")

    def test_a_part_dot_is_not_read(self):
        self.unreadable("implementer: /data/./set")

    def test_two_slashes_together_are_not_read(self):
        self.unreadable("implementer: /data//set")

    def test_the_reference_is_not_read_for_another_account(self):
        self.unreadable("implementer: ~/ws/ref/demo")

    def test_the_reference_of_another_home_is_not_read_for_the_orchestrator(self):
        self.unreadable("orchestrator: /h/spec/ws/ref")

    def test_the_product_clone_is_not_read_for_the_orchestrator(self):
        self.unreadable("orchestrator: ~/ws/src/demo")

    def test_a_folder_under_the_product_clone_of_another_home_is_not_read_for_the_orchestrator(
            self):
        self.unreadable("orchestrator: /h/impl/ws/src/demo/lib")

    def test_a_folder_that_holds_a_kept_folder_is_not_read(self):
        self.unreadable("implementer: ~/ws")

    def test_a_folder_that_holds_a_kept_folder_of_another_home_is_not_read(self):
        self.unreadable("orchestrator: /h")

    def test_the_line_number_is_the_number_in_the_file_and_good_lines_stay(self):
        folders, problems = self.read("implementer: /data", "orchestrator: ~/ws/src")
        self.assertEqual(folders["implementer"], ["/data"])
        self.assertEqual(problems, [jig_dirs.UNREADABLE.format(number=7)])

    def test_a_home_jig_does_not_know_gives_no_folder_under_it(self):
        homes = {**self.homes, "implementer": None}
        folders, problems = self.read("implementer: ~/data", "implementer: /data", homes=homes)
        self.assertEqual(folders["implementer"], ["/data"])
        self.assertEqual(problems, [jig_dirs.UNREADABLE.format(number=6)])


class ApprovedListTest(unittest.TestCase):
    conf = {"ORCH_ACCOUNT": "orchestrator", "SPEC_ACCOUNT": "specwriter",
            "IMPL_ACCOUNT": "implementer", "REV_ACCOUNT": "specwriter implementer"}

    def test_the_list_of_an_account_holds_the_clone_its_roles_and_its_folders(self):
        listed = jig_dirs.approved_list(self.conf, "implementer", PROJECT, ["/data"])
        self.assertEqual(listed, ["~/ws/src/demo-dev",
                                  *jig_dirs.of_role("impl", PROJECT),
                                  *jig_dirs.of_role("rev", PROJECT), "/data"])
        spec = jig_dirs.approved_list(self.conf, "specwriter", PROJECT, [])
        self.assertIn("~/ws/ref/demo", spec)

    def test_a_conf_file_without_the_key_of_an_account_gives_it_its_own_word(self):
        # A hand-written conf file may leave out the account keys. Each of the three
        # accounts then has its own word as its account, as elsewhere in jig, so the
        # orchestrator account still gets the orchestrator's list; a reviewer, which
        # has no word of its own, gets nothing.
        conf = {"REV_ACCOUNT": "specwriter"}
        orch = jig_dirs.approved_list(conf, "orchestrator", PROJECT, [])
        self.assertEqual(orch, ["~/ws/src/demo-dev", *jig_dirs.of_role("orch", PROJECT)])
        impl = jig_dirs.approved_list({}, "implementer", PROJECT, [])
        self.assertEqual(impl, ["~/ws/src/demo-dev", *jig_dirs.of_role("impl", PROJECT)])
        # A key that is there still decides, also against the default.
        moved = jig_dirs.approved_list({"ORCH_ACCOUNT": "boss"}, "orchestrator", PROJECT, [])
        self.assertEqual(moved, ["~/ws/src/demo-dev"])

    def test_the_orchestrators_list_follows_the_jig_root(self):
        listed = jig_dirs.approved_list(self.conf, "orchestrator", PROJECT, [], "/tmp/root")
        self.assertIn("/tmp/root/in/demo", listed)
        self.assertNotIn("/srv/jig/in/demo", listed)
        self.assertNotIn("~/ws/src/demo", listed)

    def test_a_folder_outside_the_list_is_found(self):
        listed = jig_dirs.approved_list(self.conf, "implementer", PROJECT, [])
        self.assertIsNone(jig_dirs.first_outside(jig_dirs.of_role("impl", PROJECT), listed,
                                                 "/h/impl"))
        self.assertEqual(jig_dirs.first_outside(["~/ws/prompts/demo", "/elsewhere"], listed,
                                                "/h/impl"), "/elsewhere")

    def test_folders_compare_as_absolute_paths_without_a_slash_at_the_end(self):
        self.assertIsNone(jig_dirs.first_outside(["/h/impl/ws/reviews/demo/"],
                                                 ["~/ws/reviews/demo"], "/h/impl"))
        self.assertEqual(jig_dirs.first_outside(["/h/spec/ws/reviews/demo"],
                                                ["~/ws/reviews/demo"], "/h/impl"),
                         "/h/spec/ws/reviews/demo")


if __name__ == "__main__":
    unittest.main()
