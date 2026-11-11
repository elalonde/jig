import os
import unittest

from tests.support import ROOT

INSTALLED = "/opt/jig/current/"
ON_PATH = {"/usr/local/bin/jig": "bin/jig"}
TOOL_DIRS = ("bin", "libexec", "hooks", "tools")
# Tools that a shell sources, so they are not executable and the walk does not find them.
SOURCED_TOOLS = ("share/jigfn.sh",)


def rows():
    text = (ROOT / "share" / "motd.tsv").read_text(encoding="utf-8")
    return [line.split("\t") for line in text.splitlines()]


def tree_path(motd_path):
    if motd_path in ON_PATH:
        return ON_PATH[motd_path]
    return motd_path.removeprefix(INSTALLED)


class MotdTest(unittest.TestCase):
    def test_motd_rows_hold_a_path_and_a_text(self):
        for row in rows():
            self.assertEqual(len(row), 2, row)
            self.assertTrue(row[0].startswith("/"), row)
            self.assertTrue(row[1].strip(), row)

    def test_motd_paths_exist_in_the_tree(self):
        for path, _ in rows():
            self.assertTrue((ROOT / tree_path(path)).is_file(), path)

    def test_motd_names_every_tool(self):
        listed = {tree_path(path) for path, _ in rows()}
        for directory in TOOL_DIRS:
            for entry in (ROOT / directory).iterdir():
                if entry.is_file() and os.access(entry, os.X_OK):
                    self.assertIn(f"{directory}/{entry.name}", listed)
        for path in SOURCED_TOOLS:
            self.assertTrue((ROOT / path).is_file(), path)
            self.assertIn(path, listed)

    def test_motd_has_one_line_per_tool(self):
        paths = [path for path, _ in rows()]
        self.assertEqual(len(paths), len(set(paths)))


if __name__ == "__main__":
    unittest.main()
