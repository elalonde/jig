"""The project profile reader: each switch of the kickoff record, read from origin's tags."""

import json
import unittest

from tests.support import ROOT, TempTestCase, git_ok, kickoff_record, make_dev_repos, \
    push_kickoff, run

# Prints the profile of a -dev clone as JSON, with null for a switch that has no value.
READ_PROFILE = """
import json, sys
sys.path.insert(0, sys.argv[1])
import jig_profile
profile = jig_profile.read_profile(sys.argv[2])
print(json.dumps({name: None if value is jig_profile.NO_VALUE else value
                  for name, value in profile.items()}))
"""

SWITCHES = ("reference", "clean room", "oracle", "differences review", "pre-check",
            "review after a risky unit")

# The six switch lines of jig's own kickoff record at kickoff-r5, byte for byte.
JIG_PROFILE_LINES = (
    "reference: no",
    "clean room: off",
    "oracle: none",
    "differences review: none",
    "pre-check: on",
    "review after a risky unit: off",
)

ALL_CLASSES = ("none", "patch", "minor", "major")


class ProfileCase(TempTestCase):
    def setUp(self):
        super().setUp()
        self.origin, self.work, self.clone = make_dev_repos(self.tmp, self.env())

    def kickoff(self, text, number=None, annotated=True):
        """Push a kickoff tag; without a number it is one above the last, so it is newest."""
        self.number = number if number is not None else getattr(self, "number", 0) + 1
        push_kickoff(self.env(), self.work, self.number, text, annotated)

    def profile(self, clone=None):
        result = run(["python3", "-I", "-c", READ_PROFILE, ROOT / "lib", clone or self.clone],
                     env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        profile = json.loads(result.stdout)
        self.assertEqual(sorted(profile), sorted(SWITCHES))
        return profile

    def clean_room_of(self, *lines):
        self.kickoff(kickoff_record(*lines))
        return self.profile()["clean room"]


class ProfileNoValueTest(ProfileCase):
    def test_no_kickoff_tag_on_origin_gives_no_value_for_any_switch(self):
        self.assertEqual(self.profile(), {name: None for name in SWITCHES})

    def test_a_kickoff_tag_only_in_the_local_clone_is_not_read(self):
        # The clone has its own tag with a profile; origin has none, so nothing counts.
        (self.clone / "specs").mkdir()
        (self.clone / "specs" / "00-kickoff.md").write_text(
            kickoff_record("clean room: off"), encoding="utf-8")
        git_ok(self.env(), "-C", self.clone, "add", "-A")
        git_ok(self.env(), "-C", self.clone, "commit", "-q", "--no-verify", "-m", "local")
        git_ok(self.env(), "-C", self.clone, "tag", "kickoff-r9")
        self.assertIsNone(self.profile()["clean room"])

    def test_a_tag_without_the_record_gives_no_value(self):
        self.kickoff(None)
        self.assertEqual(self.profile(), {name: None for name in SWITCHES})

    def test_a_record_without_the_profile_section_gives_no_value(self):
        record = "# Kickoff record\n\n## 1. Purpose\n\nclean room: off\n"
        self.kickoff(record)
        self.assertEqual(self.profile(), {name: None for name in SWITCHES})

    def test_a_section_without_the_line_gives_no_value_for_that_switch_alone(self):
        self.kickoff(kickoff_record("reference: no", "oracle: none"))
        profile = self.profile()
        self.assertIsNone(profile["clean room"])
        self.assertEqual(profile["reference"], "no")
        self.assertEqual(profile["oracle"], "none")

    def test_two_lines_for_the_switch_give_no_value_even_when_they_agree(self):
        self.assertIsNone(self.clean_room_of("clean room: off", "clean room: on"))
        self.assertIsNone(self.clean_room_of("clean room: on", "clean room: on"))

    def test_a_word_that_is_not_a_value_word_gives_no_value(self):
        for line in ("clean room: yes", "clean room: On", "clean room: on off",
                     "clean room: offf", "clean room: on, as before", "clean room:",
                     "clean room:   ", "clean room: minor=on"):
            with self.subTest(line):
                self.assertIsNone(self.clean_room_of(line))

    def test_a_switch_line_outside_the_section_is_not_read(self):
        # The section ends at the next heading of its level; a line under it is prose.
        record = kickoff_record("reference: no") + "\nclean room: off\n"
        self.kickoff("clean room: off\n\n" + record)
        self.assertIsNone(self.profile()["clean room"])

    def test_a_switch_set_in_each_of_two_profile_sections_gives_no_value(self):
        # A second section is read with the first, not instead of it.
        self.kickoff(kickoff_record("clean room: on") + kickoff_record("clean room: off"))
        self.assertIsNone(self.profile()["clean room"])
        self.kickoff(kickoff_record("clean room: off") + kickoff_record("reference: no"))
        self.assertEqual(self.profile()["clean room"], "off")

    def test_a_heading_of_another_name_is_not_the_section(self):
        for heading in ("## Project profile, draft", "## project profile", "### Project profile",
                        "## Project profile "):
            with self.subTest(heading):
                self.kickoff(f"# Record\n\n{heading}\n\nclean room: off\n")
                self.assertIsNone(self.profile()["clean room"])

    def test_a_clone_that_cannot_read_its_origin_gives_no_value(self):
        self.kickoff(kickoff_record("clean room: off"))
        git_ok(self.env(), "-C", self.clone, "remote", "set-url", "origin",
               self.tmp / "no-such-origin.git")
        self.assertEqual(self.profile(), {name: None for name in SWITCHES})

    def test_a_directory_that_is_not_a_clone_gives_no_value(self):
        # A plain directory inside a clone must not borrow that clone's origin.
        self.kickoff(kickoff_record("clean room: off"))
        inside = self.clone / "plain"
        inside.mkdir()
        for path in (inside, self.tmp / "absent"):
            with self.subTest(path=path):
                self.assertEqual(self.profile(path), {name: None for name in SWITCHES})


class ProfileValueTest(ProfileCase):
    def test_jig_own_profile_gives_its_values(self):
        record = "\n".join([
            "# Kickoff record", "",
            "## 4. Accounts", "",
            "clean room: on", "",
            "## Project profile", "",
            "Each switch below is set by a revision of this record, and by nothing else.",
            "reference material: there is none for this project.",
            "pre-check units run before minor and major units.",
            "",
            *JIG_PROFILE_LINES,
            "",
            "The values are plain words. A line that names no switch is left alone.",
            "",
            "## 5. Decisions", "",
            "oracle: something else", "",
        ])
        self.kickoff(record, 5)
        self.assertEqual(self.profile(), {
            "reference": "no",
            "clean room": "off",
            "oracle": "none",
            "differences review": "none",
            "pre-check": "on",
            "review after a risky unit": "off",
        })

    def test_the_newest_tag_is_the_highest_number_not_the_latest_or_the_first_in_order(self):
        # As text, the tag numbered 10 sorts before the one numbered 2, and it is pushed
        # first, so only a comparison of numbers picks it.
        self.kickoff(kickoff_record("clean room: on"), 10)
        self.kickoff(kickoff_record("clean room: off"), 2)
        self.assertEqual(self.profile()["clean room"], "on")

    def test_a_tag_that_is_not_a_kickoff_number_is_left_out(self):
        self.kickoff(kickoff_record("clean room: on"))
        git_ok(self.env(), "-C", self.work, "tag", "kickoff-r99x")
        git_ok(self.env(), "-C", self.work, "tag", "kickoff-r099")
        git_ok(self.env(), "-C", self.work, "push", "-q", "origin", "--tags")
        self.assertEqual(self.profile()["clean room"], "on")

    def test_reading_fetches_what_it_needs_and_moves_no_ref_of_the_clone(self):
        # The -dev clone lacks the tagged commit, so the reader fetches it; the clone's
        # tags, branches and FETCH_HEAD stay as they were.
        self.kickoff(kickoff_record("clean room: off"))
        refs = git_ok(self.env(), "-C", self.clone, "for-each-ref")
        self.assertEqual(self.profile()["clean room"], "off")
        self.assertEqual(git_ok(self.env(), "-C", self.clone, "for-each-ref"), refs)
        self.assertFalse((self.clone / ".git" / "FETCH_HEAD").exists())

    def test_a_lightweight_tag_is_read_as_well(self):
        self.kickoff(kickoff_record("clean room: off"), annotated=False)
        self.assertEqual(self.profile()["clean room"], "off")

    def test_a_local_tag_of_the_same_name_does_not_replace_the_one_on_origin(self):
        self.kickoff(kickoff_record("clean room: on"), 3)
        git_ok(self.env(), "-C", self.clone, "tag", "kickoff-r3")
        self.assertEqual(self.profile()["clean room"], "on")

    def test_each_value_word_of_each_switch_is_read(self):
        words = {
            "reference": ("yes", "no"),
            "clean room": ("on", "off"),
            "oracle": ("none", "capture-tool"),
            "differences review": ("none", "minor-and-major", "every-release", "when-changed"),
            "pre-check": ("on", "off"),
            "review after a risky unit": ("on", "off"),
        }
        for name, values in words.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    self.kickoff(kickoff_record(f"{name}: {value}"))
                    self.assertEqual(self.profile()[name], value)

    def test_an_oracle_name_is_one_word_without_an_equals_sign(self):
        for value in ("two words", "class=none", "none=capture-tool"):
            with self.subTest(value):
                self.kickoff(kickoff_record(f"oracle: {value}"))
                self.assertIsNone(self.profile()["oracle"])

    def test_a_class_list_gives_off_or_none_to_each_class_it_does_not_name(self):
        self.kickoff(kickoff_record("pre-check: minor=on major=on",
                                       "differences review: major=every-release"))
        profile = self.profile()
        self.assertEqual(profile["pre-check"],
                         {"none": "off", "patch": "off", "minor": "on", "major": "on"})
        self.assertEqual(profile["differences review"],
                         {"none": "none", "patch": "none", "minor": "none",
                          "major": "every-release"})

    def test_a_class_list_naming_every_class_keeps_each_value(self):
        line = "pre-check: none=on patch=off minor=on major=off"
        self.kickoff(kickoff_record(line))
        self.assertEqual(self.profile()["pre-check"], dict(zip(ALL_CLASSES,
                                                               ("on", "off", "on", "off"))))

    def test_a_bad_class_list_gives_no_value_for_its_switch_alone(self):
        for value in ("huge=on", "minor=on minor=off", "minor=on minor=on", "minor=maybe",
                      "minor=", "=on", "minor=on, major=on", "minor=on major", "Minor=on",
                      "minor=minor-and-major"):
            with self.subTest(value):
                self.kickoff(kickoff_record(f"pre-check: {value}", "clean room: on"))
                profile = self.profile()
                self.assertIsNone(profile["pre-check"])
                self.assertEqual(profile["clean room"], "on")

    def test_a_switch_without_classes_takes_no_class_list(self):
        for name in ("reference", "clean room", "review after a risky unit"):
            with self.subTest(name):
                self.kickoff(kickoff_record(f"{name}: minor=on"))
                self.assertIsNone(self.profile()[name])

    def test_blanks_around_a_value_are_not_part_of_it(self):
        self.kickoff(kickoff_record("clean room:   off   ", "pre-check:  minor=on   major=on"))
        profile = self.profile()
        self.assertEqual(profile["clean room"], "off")
        self.assertEqual(profile["pre-check"]["major"], "on")


if __name__ == "__main__":
    unittest.main()
