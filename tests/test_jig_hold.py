"""Tests of jig hold and jig unhold, which set and clear the hold that jig batch stops on.

Each test uses a hold directory of its own in place of /run/jig/hold.
"""

from tests.support import ROOT, TempTestCase, run


class HoldTest(TempTestCase):

    def setUp(self):
        super().setUp()
        self.hold_dir = self.tmp / "hold"
        self.hold_dir.mkdir()

    def tool(self, name, *args, hold_dir=None):
        env = self.env(JIG_HOLD_DIR=str(hold_dir or self.hold_dir))
        return run([ROOT / "libexec" / f"jig-{name}", *args], env=env)

    def test_hold_writes_the_hold_file_and_unhold_removes_it(self):
        held = self.tool("hold", "demo")
        self.assertEqual(held.returncode, 0, held.stderr)
        self.assertTrue((self.hold_dir / "demo").is_file())
        cleared = self.tool("unhold", "demo")
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertFalse((self.hold_dir / "demo").exists())

    def test_a_hold_touches_no_other_project(self):
        (self.hold_dir / "other").write_text("held\n")
        self.assertEqual(self.tool("hold", "demo").returncode, 0)
        self.assertEqual(self.tool("unhold", "demo").returncode, 0)
        self.assertTrue((self.hold_dir / "other").is_file())

    def test_hold_and_unhold_are_safe_to_run_twice(self):
        # The second hold keeps the file and says so; the second unhold finds no file
        # and still exits 0.
        self.assertEqual(self.tool("hold", "demo").returncode, 0)
        again = self.tool("hold", "demo")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("held already", again.stdout)
        self.assertTrue((self.hold_dir / "demo").is_file())
        self.assertEqual(self.tool("unhold", "demo").returncode, 0)
        again = self.tool("unhold", "demo")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("not held", again.stdout)
        self.assertFalse((self.hold_dir / "demo").exists())

    def test_hold_refuses_when_the_hold_directory_is_missing(self):
        # The host makes the directory at boot; without it the hold cannot be set.
        result = self.tool("hold", "demo", hold_dir=self.tmp / "missing")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("refused", result.stderr)

    def test_a_bad_project_name_is_a_usage_error(self):
        for name in ("hold", "unhold"):
            with self.subTest(name):
                result = self.tool(name, "../demo")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(f"usage: jig {name}", result.stderr)
