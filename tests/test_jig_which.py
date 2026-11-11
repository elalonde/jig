import unittest

from tests.support import ROOT, TempTestCase, run

TOOL = ROOT / "libexec" / "jig-which"


class JigWhichTest(TempTestCase):
    def test_jig_which_prints_its_own_tree(self):
        result = run([TOOL], env=self.env(), cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(ROOT))

    def test_jig_which_takes_a_project_and_prints_the_same_tree(self):
        result = run([TOOL, "demo"], env=self.env(), cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(ROOT))

    def test_jig_which_follows_a_link_to_its_real_tree(self):
        link = self.tmp / "jig-which"
        link.symlink_to(TOOL)
        result = run([link], env=self.env(), cwd=self.tmp)
        self.assertEqual(result.stdout.strip(), str(ROOT))


if __name__ == "__main__":
    unittest.main()
