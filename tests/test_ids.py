import re
import sys
import unittest

from tests.support import ROOT

sys.path.insert(0, str(ROOT / "lib"))

import jig_ids  # noqa: E402


class IdsTest(unittest.TestCase):
    def test_ids_accepts_the_documented_examples(self):
        for name in ("p6a", "tdeb1", "s3d7v2"):
            self.assertTrue(jig_ids.is_unit_id(name), name)

    def test_ids_refuses_long_upper_case_and_empty_names(self):
        for name in ("abcdefg", "P6a", "", "ab-c", "a b"):
            self.assertFalse(jig_ids.is_unit_id(name), name)

    def test_ids_mention_finds_a_name_inside_a_file_name(self):
        found = jig_ids.UNIT_ID_MENTION.findall("see impl/log/zq7ab-report.md and tab-zq7ab")
        self.assertEqual(found, ["zq7ab", "zq7ab"])

    def test_ids_mention_skips_plain_words_and_long_words(self):
        text = "the phase-0 review of a python3 tool and a version v2"
        self.assertEqual(jig_ids.UNIT_ID_MENTION.findall(text), [])

    def test_ids_record_forms(self):
        for record in ("D-1", "Q-12", "IQ-3", "R-48", "K-5", "F-2", "OI-7", "P2-D4",
                       "C-core-15", "X-fixture-a"):
            self.assertIsNotNone(jig_ids.RECORD_ID.fullmatch(record), record)

    def test_ids_record_forms_skip_lookalikes(self):
        for text in ("X-Frame-Options", "D-Bus", "pre-R-1x", "COVID-19"):
            self.assertIsNone(jig_ids.RECORD_ID.search(text), text)

    def test_general_id_fits_every_process_id_form(self):
        # Decisions, phase decisions, questions, requirements, open items, findings and
        # the four approval tags all fit the one general form as a whole.
        for record in ("D-1", "P2-D4", "Q-12", "IQ-3", "R-48", "OI-7", "F-2",
                       "spec-p2", "spec-p2r1", "impl-p1", "kickoff-r3"):
            self.assertIsNotNone(jig_ids.GENERAL_ID.fullmatch(record), record)

    def test_each_process_id_pattern_matches_its_example_in_the_general_form(self):
        # One example per pattern, in the order of the constant: each pattern matches
        # its example as a whole, and the example fits the general form.
        examples = ("D-1", "P2-D4", "Q-12", "IQ-3", "R-48", "OI-7", "F-2",
                    "spec-p2", "spec-p2r1", "impl-p1", "kickoff-r3")
        self.assertEqual(len(examples), len(jig_ids.PROCESS_ID_PATTERNS))
        for pattern, example in zip(jig_ids.PROCESS_ID_PATTERNS, examples):
            self.assertIsNotNone(re.fullmatch(pattern, example), (pattern, example))
            self.assertIsNotNone(jig_ids.GENERAL_ID.fullmatch(example), example)

    def test_process_id_patterns_leave_out_a_unit_name(self):
        # A prompt name on its own is no process ID: no pattern matches it.
        for pattern in jig_ids.PROCESS_ID_PATTERNS:
            self.assertIsNone(re.fullmatch(pattern, "zq7ab"), pattern)

    def test_general_id_finds_nothing_next_to_a_letter_digit_or_hyphen(self):
        # A letter or hyphen after the digits, or a hyphen before the first letter,
        # makes the text something other than an ID, so a search finds nothing.
        for text in ("R-1a", "R-1-", "-R-1"):
            self.assertIsNone(jig_ids.GENERAL_ID.search(text), text)

    def test_general_id_search_takes_the_whole_number(self):
        # The search must not stop early and report a shorter ID than the one written.
        self.assertEqual(jig_ids.GENERAL_ID.search("see R-12 here").group(0), "R-12")

    def test_general_id_search_stops_at_a_dot_but_a_full_match_refuses_it(self):
        # A dot ends an ID in running text, so a search finds the part before it; the
        # whole text is outside the form, so a full match finds nothing.
        self.assertEqual(jig_ids.GENERAL_ID.search("C-1.2").group(0), "C-1")
        self.assertIsNone(jig_ids.GENERAL_ID.fullmatch("C-1.2"))


if __name__ == "__main__":
    unittest.main()
