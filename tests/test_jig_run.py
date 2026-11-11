import hashlib
import unittest

from tests.support import ROOT, TempTestCase, run

TOOL = ROOT / "libexec" / "jig-run"


class JigRunTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.srv = self.tmp / "srv"
        (self.srv / "blocks" / "demo").mkdir(parents=True)

    def block(self, text, block_id="b1"):
        path = self.srv / "blocks" / "demo" / f"{block_id}.sh"
        path.write_text(text, encoding="utf-8")
        return hashlib.sha256(path.read_bytes()).hexdigest()[:8]

    def readback(self, block_id="b1"):
        return self.srv / "out" / "demo" / f"{block_id}.txt"

    def jig_run(self, *args, **extra):
        return run([TOOL, *args], env=self.env(JIG_SRV=str(self.srv), **extra), cwd=self.tmp)

    def test_jig_run_runs_a_block_in_a_login_shell(self):
        hash8 = self.block("shopt -q login_shell && echo login-shell\necho step-1\n")
        result = self.jig_run("demo", "b1", hash8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["login-shell", "step-1"])

    def test_jig_run_readback_lands_in_the_out_path(self):
        hash8 = self.block("echo step-1\necho step-2 >&2\n")
        result = self.jig_run("demo", "b1", hash8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.readback().read_text(encoding="utf-8").split(),
                         ["step-1", "step-2"])

    def test_jig_run_keeps_the_blocks_exit_code(self):
        hash8 = self.block("echo before\nexit 3\n")
        result = self.jig_run("demo", "b1", hash8)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.readback().read_text(encoding="utf-8"), "before\n")

    def test_jig_run_refuses_a_wrong_hash_and_runs_nothing(self):
        marker = self.tmp / "ran"
        hash8 = self.block(f"touch '{marker}'\n")
        wrong = format(int(hash8, 16) ^ 1, "08x")
        result = self.jig_run("demo", "b1", wrong)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"hash {hash8}, expected {wrong}", result.stderr)
        self.assertIn("nothing ran", result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse(self.readback().exists())

    def test_jig_run_takes_the_hash_in_capitals(self):
        hash8 = self.block("echo step-1\n")
        result = self.jig_run("demo", "b1", hash8.upper())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_jig_run_refuses_a_missing_block(self):
        result = self.jig_run("demo", "b9", "0123abcd")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no block b9 for demo", result.stderr)

    def test_jig_run_refuses_an_id_that_leaves_its_path(self):
        result = self.jig_run("demo", "../b1", "0123abcd")
        self.assertEqual(result.returncode, 1)
        self.assertIn("bad ID", result.stderr)

    def test_jig_run_refuses_a_short_hash(self):
        result = self.jig_run("demo", "b1", "0123")
        self.assertEqual(result.returncode, 1)
        self.assertIn("bad hash", result.stderr)

    def test_jig_run_wants_three_arguments(self):
        result = self.jig_run("demo", "b1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: jig run", result.stderr)

    def test_jig_run_reads_srv_jig_by_default(self):
        env = self.env()
        result = run([TOOL, "demo", "absent-block-for-a-test", "0123abcd"], env=env, cwd=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("/srv/jig/blocks/demo/absent-block-for-a-test.sh", result.stderr)

    def test_jig_run_through_the_dispatcher(self):
        hash8 = self.block("echo step-1\n")
        env = self.env(JIG_SRV=str(self.srv), JIG_ROOT=str(self.tmp / "no-install"))
        result = run([ROOT / "bin" / "jig", "run", "demo", "b1", hash8], env=env, cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.readback().read_text(encoding="utf-8"), "step-1\n")


if __name__ == "__main__":
    unittest.main()
