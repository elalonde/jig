import os
import unittest

from tests.support import NO_CLEAN_ROOM
from tests.units_support import UnitsTestCase


class JigCatCase(UnitsTestCase):
    """Two worker accounts with reference text in out/ and transcripts/ of their reviews.

    Each also holds a progress file in its reviews directory and a secret file outside it.
    """

    def setUp(self):
        super().setUp()
        for account in ("implementer", "specwriter"):
            reviews = self.account(account) / "ws" / "reviews" / "jig"
            (reviews / "out").mkdir(parents=True)
            (reviews / "transcripts" / "demo" / "1").mkdir(parents=True)
            (reviews / "demo-progress.md").write_text(f"progress in {account}\n",
                                                      encoding="utf-8")
            (reviews / "out" / "demo.txt").write_text("REFERENCE TEXT\n", encoding="utf-8")
            (reviews / "transcripts" / "demo" / "1" / "t.jsonl").write_text(
                "REFERENCE TEXT\n", encoding="utf-8")
            (self.account(account) / "secret.txt").write_text("SECRET\n", encoding="utf-8")

    def cat(self, account, path):
        return self.tool("cat", "jig", account, path)

    def reviews(self, account):
        return self.account(account) / "ws" / "reviews" / "jig"

    def assert_refused(self, result, reached=False):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("refused", result.stderr)
        if not reached:
            self.assertEqual(self.ssh_targets(), [])


class JigCatTest(JigCatCase):
    def test_cat_prints_a_file_under_the_reviews_directory(self):
        for account in ("implementer", "specwriter"):
            result = self.cat(account, "demo-progress.md")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f"progress in {account}\n")
        self.assertEqual(self.ssh_targets(), ["implementer@localhost", "specwriter@localhost"])

    def test_cat_refuses_a_path_outside_the_directory(self):
        self.assert_refused(self.cat("implementer", "/etc/passwd"))
        self.assert_refused(self.cat("implementer", f"{self.account('implementer')}/secret.txt"))

    def test_cat_refuses_a_dot_dot_part_before_it_reaches_the_account(self):
        for path in ("../../../secret.txt", "out/../../../../secret.txt", "a/..", ".."):
            self.assert_refused(self.cat("implementer", path))

    def test_cat_refuses_a_link_that_leads_outside_the_directory(self):
        os.symlink(self.account("implementer") / "secret.txt",
                   self.reviews("implementer") / "link.txt")
        self.assert_refused(self.cat("implementer", "link.txt"), reached=True)

    def test_cat_refuses_out_and_transcripts_under_specwriter(self):
        for path in ("out/demo.txt", "./out/demo.txt", "out//demo.txt", "out",
                     "transcripts/demo/1/t.jsonl", "transcripts"):
            result = self.cat("specwriter", path)
            self.assert_refused(result)
            self.assertNotIn("REFERENCE TEXT", result.stderr)

    def test_cat_refuses_a_link_into_out_under_specwriter(self):
        os.symlink(self.reviews("specwriter") / "out" / "demo.txt",
                   self.reviews("specwriter") / "notes.txt")
        result = self.cat("specwriter", "notes.txt")
        self.assert_refused(result, reached=True)
        self.assertNotIn("REFERENCE TEXT", result.stderr)

    def test_cat_refuses_out_under_another_spec_account_of_the_conf(self):
        self.write_conf("jig", SPEC_ACCOUNT="implementer")
        self.assert_refused(self.cat("implementer", "out/demo.txt"))

    def test_cat_reads_out_and_transcripts_under_the_implementer(self):
        self.assertEqual(self.cat("implementer", "out/demo.txt").returncode, 0)
        self.assertEqual(self.cat("implementer", "transcripts/demo/1/t.jsonl").returncode, 0)

    def test_cat_refuses_an_account_the_conf_does_not_name(self):
        self.account("someone")
        self.assert_refused(self.cat("someone", "demo-progress.md"))

    def test_cat_refuses_a_missing_file(self):
        self.assert_refused(self.cat("implementer", "nothing.md"), reached=True)

    def test_cat_refuses_when_the_account_cannot_be_reached(self):
        (self.fake / "ssh-fails").write_text("", encoding="utf-8")
        self.assert_refused(self.cat("implementer", "demo-progress.md"), reached=True)


class JigCatCleanRoomTest(JigCatCase):
    """The rule for out/ and transcripts/ follows the clean room of the project's profile."""

    def test_cat_refuses_out_and_transcripts_under_specwriter_with_the_clean_room_on(self):
        self.set_profile("clean room: on")
        for path in ("out/demo.txt", "transcripts/demo/1/t.jsonl"):
            result = self.cat("specwriter", path)
            self.assert_refused(result)
            self.assertNotIn(NO_CLEAN_ROOM, result.stderr)

    def test_cat_reads_out_transcripts_and_a_link_under_specwriter_with_the_clean_room_off(self):
        self.set_profile("clean room: off")
        os.symlink(self.reviews("specwriter") / "out" / "demo.txt",
                   self.reviews("specwriter") / "notes.txt")
        for path in ("out/demo.txt", "transcripts/demo/1/t.jsonl", "notes.txt"):
            with self.subTest(path):
                result = self.cat("specwriter", path)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "REFERENCE TEXT\n")
                self.assertEqual(result.stderr, "")

    def test_cat_with_no_value_refuses_out_under_specwriter_and_says_so(self):
        # No kickoff tag yet, then a profile whose clean room line has a bad word.
        for lines in ((), ("clean room: of",)):
            with self.subTest(lines):
                if lines:
                    self.set_profile(*lines)
                result = self.cat("specwriter", "out/demo.txt")
                self.assert_refused(result)
                self.assertEqual(result.stderr.splitlines()[0], NO_CLEAN_ROOM)
                self.assertNotIn("REFERENCE TEXT", result.stderr)

    def test_cat_reads_no_profile_for_an_account_the_switch_does_not_touch(self):
        result = self.cat("implementer", "out/demo.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
