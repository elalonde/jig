"""The tracked files of the product as a whole."""

import unittest

from tests.support import ROOT, run

# jig sets no message-of-the-day lines on a host, so no file may install, check or
# describe one. The word is built in two parts so that this file does not hold it.
MESSAGE_OF_THE_DAY = "mo" + "td"
# The change log keeps the history of the versions that still had such lines.
HISTORY = ("CHANGELOG.md",)


class TrackedTreeTest(unittest.TestCase):

    # The list comes from git, so a copy of the tree without git data has nothing
    # to ask.
    @unittest.skipUnless((ROOT / ".git").exists(), "the tree is not a git clone")
    def test_no_tracked_file_but_the_change_log_names_message_of_the_day_lines(self):
        # Every tracked file, its path and its text, is searched without regard to
        # case; only the change log may hold the word.
        # The real home, not a test home: a checkout owned by another user is
        # trusted only through the global git config there.
        listed = run(["git", "-C", ROOT, "ls-files", "-z"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        paths = [path for path in listed.stdout.split("\0") if path]
        self.assertIn("README.md", paths)
        found = []
        for path in paths:
            if path in HISTORY:
                continue
            if MESSAGE_OF_THE_DAY in path.lower():
                found.append(path)
                continue
            data = (ROOT / path).read_bytes() if (ROOT / path).is_file() else b""
            if MESSAGE_OF_THE_DAY.encode() in data.lower():
                found.append(path)
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
