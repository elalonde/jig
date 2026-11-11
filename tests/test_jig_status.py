import unittest

from tests.units_support import NORMAL_SCREEN, UnitsTestCase

HEADER = ["project", "role", "tmux", "started", "k/n", "last status"]
LAST = "2026-11-07T10:00:00-08:00 demo start started 0123abcd"


class JigStatusTest(UnitsTestCase):

    def setUp(self):
        super().setUp()
        log = self.clone / "impl" / "log" / "status.log"
        log.parent.mkdir(parents=True)
        log.write_text("2026-11-06T09:00:00-08:00 old end OK 89abcdef\n" + LAST + "\n",
                       encoding="utf-8")

    def status(self, *args):
        result = self.tool("status", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line.split("\t") for line in result.stdout.splitlines()]
        self.assertEqual(lines[0], HEADER)
        return lines[1:]

    def test_status_prints_each_field(self):
        started = self.write_lock()
        self.set_screen("implementer", NORMAL_SCREEN)
        (self.batch_dir / "jig").write_text("3/7 batch-a\n", encoding="utf-8")
        self.assertEqual(self.status("jig"),
                         [["jig", "impl", "jig-impl-demo live", started.isoformat(), "3/7", LAST]])

    def test_status_prints_a_dash_for_k_n_without_a_batch_file(self):
        self.write_lock()
        self.set_screen("implementer", NORMAL_SCREEN)
        self.assertEqual(self.status("jig")[0][4], "-")

    def test_status_names_a_session_that_ended(self):
        self.write_lock(account="specwriter", session="jig-spec-demo")
        self.account("specwriter")
        self.assertEqual(self.status("jig")[0][1:3], ["spec", "jig-spec-demo ended"])

    def test_status_names_an_account_it_cannot_reach(self):
        self.write_lock()
        (self.fake / "ssh-fails").write_text("", encoding="utf-8")
        self.assertEqual(self.status("jig")[0][2], "jig-impl-demo unreachable")

    def test_status_prints_a_project_with_no_running_unit(self):
        self.assertEqual(self.status("jig"), [["jig", "-", "-", "-", "-", LAST]])

    def test_status_prints_every_project_with_no_argument(self):
        self.write_conf("other", REPO="~/ws/src/other-dev")
        self.write_lock(project="other", session="other-rev-x1")
        self.account("implementer")
        rows = self.status()
        self.assertEqual([row[0] for row in rows], ["jig", "other"])
        self.assertEqual(rows[1][1:3], ["rev", "other-rev-x1 ended"])
        self.assertEqual(rows[1][5], "-")

    def test_status_keeps_the_locks_of_another_project_apart(self):
        self.write_conf("jig-x")
        self.write_lock(project="jig-x", account="implementer", session="jig-x-impl-demo")
        self.assertEqual(self.status("jig")[0][1:4], ["-", "-", "-"])

    def test_status_refuses_a_project_with_no_conf_file(self):
        result = self.tool("status", "nothing")
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
