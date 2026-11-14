"""The READ check of jig validate prompt: each path against the list of the role."""

import unittest

from tests.support import NO_CLEAN_ROOM, git_ok, kickoff_record, make_dev_repos, push_kickoff
from tests.test_validate import ValidateCase, prompt_parts, prompt_text, write_conf

ROLES = {"spec": "specwriter", "impl": "implementer", "rev": "implementer"}


def prompt_with(read, role="impl", account=None, project="jig"):
    """Return the good prompt with another READ section, for that role."""
    _, blocks, last = prompt_parts()
    account = account or ROLES[role]
    first = f"# demo  paste into: {project}-{role}-demo ({account}, ~/ws/src/{project}-dev)"
    return prompt_text(first, {**blocks, "READ": read.split("\n")}, last)


class ReadCheckCase(ValidateCase):
    def assert_passes(self, path, role="impl", **given):
        with self.subTest(path=path, role=role):
            self.assert_valid("prompt", prompt_with(f"READ: {path} by grep.", role, **given))

    def assert_refused(self, path, role="impl", named=None, **given):
        with self.subTest(path=path, role=role):
            failures = self.assert_invalid("prompt",
                                           prompt_with(f"READ: {path} by grep.", role, **given),
                                           f"READ path {named or path} ")
            self.assertEqual(len(failures), 1, failures)


class ReadPathTest(ReadCheckCase):
    def test_read_path_outside_the_list_fails(self):
        for role in ROLES:
            for path in ("/etc/passwd", "~/.ssh/id_rsa", "~/ws/src/other-dev/x", "~", "/",
                         "~/ws/prompts", "~/ws/reviews/other/x"):
                self.assert_refused(path, role)

    def test_read_path_in_the_list_passes(self):
        lists = {
            "spec": ("~/ws/prompts/jig/demo.md", "~/ws/reviews/jig/out/demo.txt"),
            "impl": ("~/ws/prompts/jig", "~/ws/reviews/jig/", "~/ws/src/jig-dev-approved/specs/x"),
            "rev": ("~/ws/prompts/jig/demo.md", "~/ws/src/jig-dev-approved/specs/x"),
        }
        for role, paths in lists.items():
            for path in paths:
                self.assert_passes(path, role)

    def test_read_path_in_the_product_clone_passes_for_each_worker_role_only(self):
        # The product clone sits beside the -dev clone, so a path in it is outside the
        # repo; it is in the list of every worker role, written from the home or from
        # the working directory, and the orchestrator, who reads no product, is refused.
        for role in ROLES:
            for path in ("~/ws/src/jig", "~/ws/src/jig/lib/jig_clones.py", "../jig/lib/x"):
                self.assert_passes(path, role)
        self.assert_refused("~/ws/src/jig/lib/jig_clones.py", "orch", account="orchestrator")

    def test_read_path_of_the_approved_worktree_fails_for_the_spec_writer(self):
        self.assert_refused("~/ws/src/jig-dev-approved/specs/x", "spec")

    def test_read_path_of_an_inbox_passes_only_for_the_orchestrator(self):
        path = "/srv/jig/in/jig/brief.md"
        self.assert_valid("prompt", prompt_with(f"READ: {path}.", "orch", "orchestrator"))
        for role in ROLES:
            self.assert_refused(path, role)

    def test_read_path_under_the_working_directory_passes(self):
        for path in ("~/ws/src/jig-dev", "~/ws/src/jig-dev/specs/jig-spec.md",
                     "/home/implementer/ws/src/jig-dev/specs/jig-spec.md",
                     "~implementer/ws/src/jig-dev/specs/jig-spec.md",
                     "~/ws/src/jig-dev/specs/../impl/0-plan.md",
                     "../jig-dev-approved/specs/x"):
            self.assert_passes(path)

    def test_read_path_under_the_working_directory_uses_the_project_of_the_first_line(self):
        self.assert_passes("~/ws/src/my-app-dev/x", project="my-app")
        self.assert_passes("~/ws/prompts/my-app/x", project="my-app")
        self.assert_refused("~/ws/src/jig-dev/x", project="my-app")

    def test_read_path_of_the_working_directory_of_another_name_fails(self):
        for path in ("~/ws/src/jig-dev2/x", "~/ws/src/jig2/x", "~/ws/prompts/jig2/x",
                     "~/ws/reviews/jig-other", "~/ws/src/jig-dev-approved2/x"):
            self.assert_refused(path)

    def test_read_path_under_ws_ref_fails_for_every_role(self):
        for role, account in ROLES.items():
            for path in ("~/ws/ref", "~/ws/ref/jig", "~/ws/ref/jig/src/main.c", "~/ws/ref/other/x",
                         f"/home/{account}/ws/ref/jig/x", f"~{account}/ws/ref/jig/x"):
                self.assert_refused(path, role)
        self.assert_invalid("prompt", prompt_with("READ: ~/ws/ref/jig/x.", "orch", "orchestrator"),
                            "READ path ~/ws/ref/jig/x ")

    def test_read_path_under_ws_ref_of_another_account_fails(self):
        for role in ROLES:
            self.assert_refused("~specwriter/ws/ref/jig/x", role, account="reviewer")
            self.assert_refused("/home/specwriter/ws/ref/jig/x", role, account="reviewer")

    def test_read_path_with_dots_is_read_after_it_is_shortened(self):
        for path in ("~/ws/prompts/jig/../../ref/jig/x", "~/ws/src/jig-dev/../../ref/jig/x",
                     "~/ws/prompts/jig/..", "~/ws/prompts/jig/../../..", "~/../specwriter/x",
                     "../../ref/jig/x", "specs/../../../ref/jig/x", "../other-dev/x",
                     "/home/implementer/ws/prompts/jig/../../ref/jig/x", "/./etc/passwd",
                     "//etc/passwd"):
            self.assert_refused(path)

    def test_read_path_under_ws_ref_fails_in_each_form_of_home(self):
        self.assert_refused("$HOME/ws/ref/jig/x", named="~/ws/ref/jig/x")
        self.assert_refused("${HOME}/ws/ref/jig/x", named="~/ws/ref/jig/x")
        self.assert_refused("$HOME/ws/ref/jig/x", "spec", named="~/ws/ref/jig/x")

    def test_read_path_is_found_beside_any_punctuation(self):
        for text in ("`~/ws/ref/jig/x`", "'~/ws/ref/jig/x'", '"~/ws/ref/jig/x"', "(~/ws/ref/jig/x)",
                     "[~/ws/ref/jig/x]", "see:~/ws/ref/jig/x", "dir=~/ws/ref/jig/x",
                     "a,~/ws/ref/jig/x", "a;~/ws/ref/jig/x", "<~/ws/ref/jig/x>",
                     "git show tag:~/ws/ref/jig/x", "cat ~/ws/ref/jig/x | grep y"):
            with self.subTest(text):
                failures = self.assert_invalid("prompt", prompt_with(f"READ: {text}"),
                                               "READ path ~/ws/ref/jig/x ")
                self.assertEqual(len(failures), 1, failures)

    def test_read_path_with_a_broken_up_name_fails(self):
        for path, named in (("~/ws/re\\f/jig/x", "~/ws/re\\f/jig/x"),
                            ("~/ws/'ref'/jig/x", "~/ws/"), ('~/ws/"ref"/jig/x', "~/ws/"),
                            ("~/ws/{ref,src}/x", "~/ws/"), ("~/ws/*/jig/x", "~/ws/*/jig/x")):
            with self.subTest(path):
                self.assert_invalid("prompt", prompt_with(f"READ: {path}"), f"READ path {named} ")

    def test_read_path_on_a_continuation_line_fails(self):
        read = "READ: the spec;\n  and ~/ws/ref/jig/x, and more.\n  DO NOT READ: transcripts."
        self.assert_invalid("prompt", prompt_with(read), "READ path ~/ws/ref/jig/x ")

    def test_read_path_is_named_with_its_line_for_each_failure(self):
        read = "READ: /etc/passwd\n  and ~/ws/ref/jig/x."
        failures = self.assert_invalid("prompt", prompt_with(read))
        self.assertEqual(len(failures), 2, failures)
        self.assertIn("line 12: READ path /etc/passwd ", failures[0])
        self.assertIn("line 13: READ path ~/ws/ref/jig/x ", failures[1])

    def test_read_path_ending_a_sentence_loses_only_the_full_stop(self):
        self.assert_valid("prompt", prompt_with("READ: ~/ws/prompts/jig/demo.md."))
        self.assert_invalid("prompt", prompt_with("READ: ~/ws/ref/jig."), "READ path ~/ws/ref/jig ")
        for text, named in (("~/ws/prompts/jig/..", "~/ws/prompts/jig/.."),
                            ("~/ws/prompts/jig/...", "~/ws/prompts/jig/.."),
                            ("~/ws/src/jig-dev/../..", "~/ws/src/jig-dev/../..")):
            with self.subTest(text):
                self.assert_invalid("prompt", prompt_with(f"READ: {text}"), f"READ path {named} ")

    def test_read_path_of_a_second_read_section_fails(self):
        _, blocks, last = prompt_parts()
        first = "# demo  paste into: jig-impl-demo (implementer, ~/ws/src/jig-dev)"
        blocks = {**blocks, "STEPS": [*blocks["STEPS"], "READ: ~/ws/ref/jig/x."]}
        self.assert_invalid("prompt", prompt_text(first, blocks, last), "READ path ~/ws/ref/jig/x ")

    def test_read_path_for_a_role_without_a_list_fails(self):
        self.assert_invalid("prompt", prompt_with("READ: ~/ws/prompts/jig/x.", "other", "someone"),
                            "READ path ~/ws/prompts/jig/x ", "role other")
        self.assert_valid("prompt", prompt_with("READ: ~/ws/src/jig-dev/x.", "other", "someone"))


class ReadOtherTextTest(ReadCheckCase):
    def test_read_check_stops_at_do_not_read(self):
        for read in ("READ: the spec.\n  DO NOT READ: ~/ws/ref/jig/x; /etc/passwd.",
                     "READ: the spec. DO NOT READ: ~/ws/ref/jig/x; /etc/passwd."):
            self.assert_valid("prompt", prompt_with(read))

    def test_read_check_reads_up_to_do_not_read_on_its_line(self):
        read = "READ: ~/ws/ref/jig/x. DO NOT READ: transcripts."
        self.assert_invalid("prompt", prompt_with(read), "READ path ~/ws/ref/jig/x ")

    def test_read_check_leaves_other_sections_alone(self):
        _, blocks, last = prompt_parts()
        first = "# demo  paste into: jig-impl-demo (implementer, ~/ws/src/jig-dev)"
        blocks = {**blocks, "AUTHORITY": ["AUTHORITY: write in /etc and ~/ws/ref/jig."]}
        self.assert_valid("prompt", prompt_text(first, blocks, last))

    def test_read_check_passes_text_that_names_no_such_path(self):
        for text in ("impl/0-plan.md, row 10a", "and/or", "specs/jig-spec.md, section 6",
                     "git show spec-p0r2:specs/jig-spec.md", "jig/tests/test_start.py by grep",
                     "https://example.com/docs/page", "http://example.com/a/../b",
                     "ws/ref/notes.md", "a..b/c", "<files and sections>"):
            with self.subTest(text):
                self.assert_valid("prompt", prompt_with(f"READ: {text}."))


class ReadCleanRoomTest(ReadCheckCase):
    """The ~/ws/ref rule follows the clean room of the project's profile."""

    def use_profile(self, *records):
        """Point the jig conf at a -dev clone of its own, with one kickoff tag per record."""
        self.profiles = getattr(self, "profiles", 0) + 1
        origin, work, clone = make_dev_repos(self.tmp / f"profile{self.profiles}", self.env())
        for number, record in enumerate(records, 1):
            push_kickoff(self.env(), work, number, record)
        write_conf(self.home, "jig", clone)
        return origin, clone

    def assert_no_value(self, read="READ: ~/ws/ref/jig/x.", role="spec"):
        # As with the clean room on: the path is refused, and stderr says why.
        result = self.validate("prompt", prompt_with(read, role))
        self.assertEqual(result.returncode, 1, result.stderr)
        lines = result.stderr.splitlines()
        self.assertEqual(lines[0], NO_CLEAN_ROOM, result.stderr)
        self.assertEqual(lines.count(NO_CLEAN_ROOM), 1, result.stderr)
        self.assertIn("READ path ~/ws/ref/jig/x is under ~/ws/ref", result.stderr)

    def test_read_path_under_ws_ref_passes_for_the_spec_writer_with_the_clean_room_off(self):
        self.use_profile(kickoff_record("clean room: off"))
        for path in ("~/ws/ref/jig", "~/ws/ref/jig/src/main.c", "/home/specwriter/ws/ref/jig/x"):
            self.assert_passes(path, "spec")

    def test_read_path_under_ws_ref_keeps_the_role_lists_with_the_clean_room_off(self):
        # Off lifts only the rule for ~/ws/ref: the reference is still on no other list.
        self.use_profile(kickoff_record("clean room: off"))
        for role in ("impl", "rev"):
            self.assert_refused("~/ws/ref/jig/x", role)
        self.assert_refused("~/ws/ref/other/x", "spec")
        self.assert_refused("~/ws/ref", "spec")
        failures = self.assert_invalid("prompt", prompt_with("READ: ~/ws/ref/jig/x.", "impl"),
                                       "outside the repo and the list of role impl")
        self.assertNotIn("under ~/ws/ref", "\n".join(failures))

    def test_read_path_under_ws_ref_fails_with_the_clean_room_on(self):
        self.use_profile(kickoff_record("clean room: off"), kickoff_record("clean room: on"))
        failures = self.assert_invalid("prompt", prompt_with("READ: ~/ws/ref/jig/x.", "spec"),
                                       "READ path ~/ws/ref/jig/x is under ~/ws/ref")
        self.assertEqual(len(failures), 1, failures)

    def test_no_kickoff_tag_is_no_value(self):
        self.use_profile()
        self.assert_no_value()

    def test_two_clean_room_lines_or_a_bad_word_are_no_value(self):
        for lines in (("clean room: off", "clean room: off"), ("clean room: of",),
                      ("reference: no",)):
            with self.subTest(lines):
                self.use_profile(kickoff_record(*lines))
                self.assert_no_value()

    def test_no_conf_file_for_the_project_is_no_value(self):
        (self.home / ".config" / "jig" / "jig.conf").unlink()
        self.assert_no_value()

    def test_a_repo_that_is_not_a_clone_is_no_value(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        write_conf(self.home, "jig", plain)
        self.assert_no_value()

    def test_an_origin_that_cannot_be_read_is_no_value(self):
        _, clone = self.use_profile(kickoff_record("clean room: off"))
        git_ok(self.env(), "-C", clone, "remote", "set-url", "origin", self.tmp / "gone.git")
        self.assert_no_value()

    def test_a_first_line_that_does_not_parse_is_no_value(self):
        # Even with the clean room of jig off, a prompt that names no project gets none.
        self.use_profile(kickoff_record("clean room: off"))
        text = prompt_with("READ: ~/ws/ref/jig/x.", "spec").replace("  paste into:", " paste into:")
        result = self.validate("prompt", text)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr.splitlines()[0], NO_CLEAN_ROOM, result.stderr)
        self.assertIn("first line is not", result.stderr)

    def test_the_profile_is_read_for_the_project_of_the_first_line(self):
        # jig's clean room is off; my-app keeps the class's profile, with it on.
        self.use_profile(kickoff_record("clean room: off"))
        self.assert_passes("~/ws/ref/jig/x", "spec")
        self.assert_refused("~/ws/ref/my-app/x", "spec", project="my-app")


if __name__ == "__main__":
    unittest.main()
