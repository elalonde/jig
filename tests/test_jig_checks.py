import importlib.machinery
import importlib.util
import os
import pathlib
import pwd
import stat
import sys
import unittest
from unittest import mock

from tests.support import ROOT, TempTestCase

sys.path.insert(0, str(ROOT / "lib"))

import jig_checks  # noqa: E402

ACCOUNT = pwd.getpwuid(os.getuid()).pw_name
HOOK = ROOT / "hooks" / "pre-commit"

# The rows that both parses read: each is a line of checks.tsv after the header.
ROWS = [
    "make-check\tmake check\timplementer\t300",
    "build\tmake all\timplementer\t300\theavy",
    "lint\tmake lint\timplementer\t60\tlight",
    "docs\tmake docs\timplementer\t60\t",
    "big\tmake all\timplementer\t300\thuge",
    "six\tmake all\timplementer\t300\theavy\textra",
    "nocap\tmake all\timplementer\tsoon",
    "zero\tmake all\timplementer\t0",
    "short\tmake all",
]


def load_hook():
    """Load the pre-commit hook as a module; it runs nothing until main is called."""
    loader = importlib.machinery.SourceFileLoader("jig_pre_commit_hook", str(HOOK))
    spec = importlib.util.spec_from_loader("jig_pre_commit_hook", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def outcome(parse, error, line):
    """Return the fields that a parse gives for the line, or the text of its refusal."""
    try:
        return parse(line)
    except error as problem:
        return f"refused: {problem}"


class ParseRowTest(unittest.TestCase):
    def test_a_row_of_four_fields_is_light(self):
        self.assertEqual(jig_checks.parse_row(ROWS[0]),
                         ("make-check", "make check", "implementer", 300, "light"))

    def test_the_fifth_field_gives_the_weight_and_an_empty_one_is_light(self):
        self.assertEqual(jig_checks.parse_row(ROWS[1])[4], "heavy")
        self.assertEqual(jig_checks.parse_row(ROWS[2])[4], "light")
        self.assertEqual(jig_checks.parse_row(ROWS[3])[4], "light")

    def test_another_weight_and_a_sixth_field_are_refused_with_the_two_weights(self):
        # Both refusals tell the writer of the row which words the weight may be.
        for line in (ROWS[4], ROWS[5]):
            with self.subTest(line=line):
                with self.assertRaises(jig_checks.BadRow) as caught:
                    jig_checks.parse_row(line)
                self.assertIn("heavy or light", str(caught.exception))

    def test_a_cap_that_is_not_a_whole_number_of_1_or_more_is_refused(self):
        for line in (ROWS[6], ROWS[7], ROWS[8]):
            with self.subTest(line=line):
                self.assertRaises(jig_checks.BadRow, jig_checks.parse_row, line)

    def test_the_hook_parses_every_row_as_the_module_does(self):
        # The hook keeps its own copy of the parse; each row gives the same fields, or
        # the same refusal, from both.
        hook = load_hook()
        for line in ROWS:
            with self.subTest(line=line):
                self.assertEqual(outcome(hook.parse_row, hook.BadRow, line),
                                 outcome(jig_checks.parse_row, jig_checks.BadRow, line))


class SlotCase(TempTestCase):
    """A host config and a slot directory of the test's own."""

    def setUp(self):
        super().setUp()
        self.srv = self.tmp / "srv"
        self.srv.mkdir()
        self.heavy = self.tmp / "heavy"
        patcher = mock.patch.dict(os.environ, {"JIG_SRV": str(self.srv),
                                               "JIG_HEAVY_DIR": str(self.heavy),
                                               "JIG_HEAVY_POLL": "0.05"})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(jig_checks.HELD, None)
        self.said = []

    def host_conf(self, text):
        (self.srv / "host.conf").write_text(text, encoding="utf-8")

    def take(self, row="build"):
        slot, waited = jig_checks.take("demo", "ab12", row, self.said.append)
        if slot is not None:
            self.addCleanup(slot.release)
        return slot, waited


class HostConfTest(SlotCase):
    CASES = [
        ("no key", "HOST=somewhere\n", None),
        ("two, with spaces and comments", "# the slots\n  HEAVY_SLOTS = 2  \nA=b\n", 2),
        ("one", "HEAVY_SLOTS=1\n", 1),
        ("the last line counts", "HEAVY_SLOTS=1\nHEAVY_SLOTS=3\n", 3),
        ("a commented key", "# HEAVY_SLOTS=4\n", None),
    ]
    BAD = ["HEAVY_SLOTS=0\n", "HEAVY_SLOTS=two\n", "HEAVY_SLOTS=-1\n", "HEAVY_SLOTS=\n"]

    def test_no_host_conf_means_no_limit(self):
        self.assertIsNone(jig_checks.heavy_slots())

    def test_the_key_is_read_as_jig_new_reads_host_conf(self):
        for name, text, expected in self.CASES:
            with self.subTest(name):
                self.host_conf(text)
                self.assertEqual(jig_checks.heavy_slots(), expected)

    def test_a_value_that_is_not_a_whole_number_of_1_or_more_names_the_file_and_key(self):
        for text in self.BAD:
            with self.subTest(text):
                self.host_conf(text)
                with self.assertRaises(jig_checks.SlotError) as caught:
                    jig_checks.heavy_slots()
                self.assertIn(str(self.srv / "host.conf"), str(caught.exception))
                self.assertIn("HEAVY_SLOTS", str(caught.exception))

    def test_the_hook_reads_host_conf_as_the_module_does(self):
        hook = load_hook()
        for text in [case[1] for case in self.CASES] + self.BAD:
            with self.subTest(text):
                self.host_conf(text)
                self.assertEqual(outcome(lambda _: hook.heavy_slots(), hook.SlotError, None),
                                 outcome(lambda _: jig_checks.heavy_slots(),
                                         jig_checks.SlotError, None))


class TakeTest(SlotCase):
    def test_no_limit_takes_no_slot_and_says_nothing(self):
        self.assertEqual(self.take(), (None, None))
        self.assertEqual(self.said, [])
        self.assertFalse(self.heavy.exists())

    def test_a_missing_directory_and_slot_file_are_made_for_every_account(self):
        # The umask takes the group's bits away, as in an account that keeps its files
        # private; the directory and the slot file still get the shared modes.
        self.host_conf("HEAVY_SLOTS=1\n")
        previous = os.umask(0o077)
        try:
            slot, waited = self.take()
        finally:
            os.umask(previous)
        self.assertEqual((slot.number, waited), (1, None))
        self.assertEqual(stat.S_IMODE(self.heavy.stat().st_mode), 0o2775)
        self.assertEqual(stat.S_IMODE((self.heavy / "1").stat().st_mode), 0o664)

    def test_an_existing_directory_is_taken_as_it_is(self):
        self.heavy.mkdir(mode=0o755)
        self.heavy.chmod(0o755)
        self.host_conf("HEAVY_SLOTS=1\n")
        self.assertEqual(self.take()[0].number, 1)
        self.assertEqual(stat.S_IMODE(self.heavy.stat().st_mode), 0o755)

    def test_a_directory_that_cannot_be_made_is_refused(self):
        os.environ["JIG_HEAVY_DIR"] = str(self.tmp / "missing" / "heavy")
        self.host_conf("HEAVY_SLOTS=1\n")
        with self.assertRaises(jig_checks.SlotError) as caught:
            self.take()
        self.assertIn("cannot make", str(caught.exception))

    def test_the_holder_writes_one_line_of_six_fields(self):
        self.host_conf("HEAVY_SLOTS=1\n")
        self.take()
        line = (self.heavy / "1").read_text(encoding="utf-8")
        self.assertTrue(line.endswith("\n"))
        fields = line.rstrip("\n").split("\t")
        self.assertEqual(fields[:4], ["demo", "ab12", "build", ACCOUNT])
        self.assertRegex(fields[4], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}[+-][0-9:]{5}$")
        self.assertEqual(fields[5], str(os.getpid()))

    def test_a_waiters_open_does_not_erase_the_holders_text(self):
        # A flock belongs to one open of the file, so a second open in this process
        # stands for another account's waiter: it finds the slot held, and the
        # holder's line is still there.
        self.host_conf("HEAVY_SLOTS=1\n")
        self.take()
        before = (self.heavy / "1").read_text(encoding="utf-8")
        self.assertIsNone(jig_checks.try_slot(self.heavy / "1"))
        self.assertEqual((self.heavy / "1").read_text(encoding="utf-8"), before)
        self.assertEqual(jig_checks.describe(self.heavy / "1"),
                         "project demo, unit ab12, row build")

    def test_a_slot_with_no_usable_text_is_described_as_unknown(self):
        self.heavy.mkdir()
        for text in ("", "half\tof"):
            with self.subTest(text=text):
                (self.heavy / "1").write_text(text, encoding="utf-8")
                self.assertEqual(jig_checks.describe(self.heavy / "1"), "?")

    def test_a_higher_number_of_slots_uses_the_new_files(self):
        # Slot 1 is held from an earlier run with one slot; with two, slot 2 is taken.
        self.host_conf("HEAVY_SLOTS=1\n")
        self.take()
        self.host_conf("HEAVY_SLOTS=2\n")
        slot, waited = self.take()
        self.assertEqual((slot.number, waited), (2, None))

    def test_a_slot_freed_while_waiting_is_taken_and_the_wait_is_said(self):
        # Slot 1 is held, so the waiter says whom it waits for; once the holder lets go,
        # the waiter takes the slot and says how long it waited.
        self.host_conf("HEAVY_SLOTS=1\n")
        held, _ = self.take("first")
        real_sleep = jig_checks.time.sleep

        def sleep_and_release(seconds):
            held.release()
            real_sleep(seconds)

        with mock.patch.object(jig_checks.time, "sleep", sleep_and_release):
            slot, waited = self.take("second")
        self.assertEqual(slot.number, 1)
        self.assertIsNotNone(waited)
        self.assertEqual(len(self.said), 2, self.said)
        self.assertEqual(self.said[0], "every heavy slot is held, waiting: "
                                       "slot 1: project demo, unit ab12, row first")
        self.assertRegex(self.said[1], r"^took heavy slot 1 after [0-9]+ s$")

    def test_a_nested_take_runs_at_once_without_a_slot(self):
        # The process tree already holds a slot, so a take inside it does not wait for
        # the slot it holds.
        self.host_conf("HEAVY_SLOTS=1\n")
        self.take()
        os.environ[jig_checks.HELD] = "1"
        self.addCleanup(os.environ.pop, jig_checks.HELD, None)
        self.assertEqual(self.take(), (None, None))
        self.assertEqual(self.said, [])

    @unittest.skipIf(os.geteuid() == 0, "root opens a file of any mode")
    def test_a_slot_file_that_cannot_be_opened_is_refused_after_three_more_tries(self):
        # The slot file allows no one to open it, as one of another account without
        # group write: the take tries again at the next polls, then names the file and
        # its owner.
        self.heavy.mkdir()
        path = self.heavy / "1"
        path.write_text("", encoding="utf-8")
        path.chmod(0)
        self.host_conf("HEAVY_SLOTS=1\n")
        with self.assertRaises(jig_checks.SlotError) as caught:
            self.take()
        self.assertIn(str(path), str(caught.exception))
        self.assertIn(f"a file of {ACCOUNT}", str(caught.exception))

    def test_the_poll_time_may_be_a_decimal_and_must_be_above_0(self):
        self.assertEqual(jig_checks.poll_seconds(), 0.05)
        for text in ("0", "soon", "-1"):
            with self.subTest(text):
                os.environ["JIG_HEAVY_POLL"] = text
                self.assertRaises(jig_checks.SlotError, jig_checks.poll_seconds)


if __name__ == "__main__":
    unittest.main()
