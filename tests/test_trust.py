"""The trust mark of Claude Code, and the folders of the section Directories.

The mark is set in a fixture home's .claude.json, never in the home of the account
that runs the tests. The kickoff records are text, with fixture homes for the accounts.
"""

import contextlib
import importlib.machinery
import importlib.util
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


class TrustMarkAllTest(TrustMarkCase):
    """The marks of one start: the -dev clone and two added folders, in one write.

    A Claude Code session writes the whole file from its own read, so a write of the
    file by the step can lose a write of a session. These tests stand in for such a
    session by changing the file while the step runs.
    """

    def setUp(self):
        super().setUp()
        self.added = [str(self.tmp / "notes"), str(self.tmp / "data")]
        self.folders = [self.folder, *self.added]

    def mark_all(self):
        return jig_trust.mark_all([(folder, jig_trust.TRUST_KEY) for folder in self.folders],
                                  "implementer", home=self.home)

    def counting(self, name, calls, then=None):
        """Patch a function of the module so that each call is counted, then runs then."""
        real = getattr(jig_trust, name) if name != "replace" else os.replace

        def wrapper(*args):
            result = real(*args)
            calls.append(name)
            if then:
                then(len([call for call in calls if call == name]))
            return result

        if name == "replace":
            return mock.patch.object(jig_trust.os, "replace", wrapper)
        return mock.patch.object(jig_trust, name, wrapper)

    def session_writes(self, key):
        """Write the file as a session would: from a read, with one more key."""
        state = self.read()
        state[key] = True
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def test_one_start_writes_the_file_once_for_all_its_marks(self):
        self.write({"theme": "dark"})
        calls = []
        with self.counting("replace", calls):
            results = self.mark_all()
        self.assertEqual(results, [(folder, "trusted", None) for folder in self.folders])
        self.assertEqual(calls, ["replace"])
        self.assertEqual(self.read(), {"theme": "dark", "projects": {
            folder: {"hasTrustDialogAccepted": True} for folder in self.folders}})

    def test_a_change_after_the_read_and_before_the_check_is_kept_with_the_marks(self):
        # The session writes just after the new file is made: the check before the
        # rename sees it, so the step reads the file again and keeps the session's key.
        self.write({"theme": "dark"})
        calls = []

        def write_once(count):
            if count == 1:
                self.session_writes("sessionKey")

        with self.counting("make_new_file", calls, write_once), \
                self.counting("replace", calls):
            results = self.mark_all()
        self.assertEqual(results, [(folder, "trusted", None) for folder in self.folders])
        self.assertEqual(calls, ["make_new_file", "make_new_file", "replace"])
        state = self.read()
        self.assertIs(state["sessionKey"], True)
        self.assertEqual(state["projects"], {
            folder: {"hasTrustDialogAccepted": True} for folder in self.folders})
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), [".claude.json"])

    def test_a_change_at_both_checks_gives_one_more_read_and_then_the_rename(self):
        # The session writes after each new file: the step reads once more, and then
        # renames all the same. The first write is kept, the second one is lost.
        self.write({"theme": "dark"})
        calls = []

        def write_each(count):
            self.session_writes(f"sessionKey{count}")

        with self.counting("load", calls), self.counting("make_new_file", calls, write_each), \
                self.counting("replace", calls):
            results = self.mark_all()
        self.assertEqual(results, [(folder, "trusted", None) for folder in self.folders])
        # The two reads before the rename, and the read after it.
        self.assertEqual(calls, ["load", "make_new_file", "load", "make_new_file", "replace",
                                 "load"])
        state = self.read()
        self.assertIs(state["sessionKey1"], True)
        self.assertNotIn("sessionKey2", state)
        self.assertEqual(len(state["projects"]), 3)

    def test_every_mark_there_writes_nothing(self):
        self.write({"projects": {folder: {"hasTrustDialogAccepted": True}
                                 for folder in self.folders}})
        before = os.stat(self.state)
        calls = []
        with self.counting("make_new_file", calls):
            results = self.mark_all()
        self.assertEqual(results, [(folder, "kept", None) for folder in self.folders])
        self.assertEqual(calls, [])
        after = os.stat(self.state)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))

    def test_an_entry_that_is_not_an_object_leaves_the_file_and_names_each_folder(self):
        # The -dev clone's mark is there; the first added folder's entry is unusable, so
        # nothing is written and both added folders are reported as not marked.
        self.write({"projects": {self.folder: {"hasTrustDialogAccepted": True},
                                 self.added[0]: True}})
        before = self.state.read_bytes()
        results = self.mark_all()
        self.assertEqual([value for _, value, _ in results], ["kept", "not marked", "not marked"])
        for _, _, problem in results[1:]:
            self.assertIn(f"{self.state} of implementer", problem)
            self.assertIn(f"the entry of {self.added[0]} is not an object", problem)
        self.assertEqual(self.state.read_bytes(), before)

    def test_the_worker_of_jig_start_marks_its_folders_with_one_write(self):
        # The worker part runs this module's text in the worker's account; its lines
        # name each folder whose mark it set.
        self.write({"theme": "dark"})
        worker = load_worker()
        out = io.StringIO()
        calls = []
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), \
                self.counting("replace", calls), contextlib.redirect_stdout(out):
            worker.set_trust_marks("implementer", self.folder, self.added)
        self.assertEqual(calls, ["replace"])
        self.assertEqual(out.getvalue().splitlines(),
                         [f"trust implementer {folder}: trusted" for folder in self.folders])


class RemoveEntriesTest(TrustMarkCase):
    """The removal of entries of "projects", by the same one read and one rename."""

    def remove(self, prefix):
        return jig_trust.remove_entries(lambda name: name.startswith(prefix), "implementer",
                                        home=self.home)

    def test_the_selected_entries_go_and_every_other_key_stays(self):
        gone = [str(self.tmp / "run" / "a"), str(self.tmp / "run" / "b")]
        self.write({"theme": "dark", "projects": {
            self.folder: {"hasTrustDialogAccepted": True},
            **{folder: {"hasTrustDialogAccepted": True} for folder in gone}}})
        calls = []
        real_replace = os.replace

        def replace(source, target):
            calls.append("replace")
            real_replace(source, target)

        with mock.patch.object(jig_trust.os, "replace", replace):
            removed, problem = self.remove(str(self.tmp / "run"))
        self.assertEqual((sorted(removed), problem), (gone, None))
        self.assertEqual(calls, ["replace"])
        self.assertEqual(self.read(), {"theme": "dark", "projects": {
            self.folder: {"hasTrustDialogAccepted": True}}})

    def test_nothing_to_remove_writes_nothing(self):
        self.write({"projects": {self.folder: {}}})
        before = os.stat(self.state)
        self.assertEqual(self.remove(str(self.tmp / "run")), ([], None))
        after = os.stat(self.state)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))

    def test_a_file_that_is_not_json_is_left_with_one_line(self):
        self.write("{oops")
        removed, problem = self.remove(str(self.tmp))
        self.assertEqual(removed, [])
        self.assertIn(f"{self.state} of implementer: it is not JSON", problem)
        self.assertEqual(len(problem.splitlines()), 1)
        self.assertEqual(self.state.read_text(encoding="utf-8"), "{oops")


def load_worker():
    """Load the worker part of jig start, which has no .py name, as a module."""
    loader = importlib.machinery.SourceFileLoader("jig_start_worker_under_test",
                                                  str(ROOT / "libexec" / "jig-start-worker"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,
                                                                             loader))
    loader.exec_module(module)
    return module


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
