import hashlib
import unittest

from tests.jigfn_support import HOST, ClientTestCase, write


class JigblockTest(ClientTestCase):
    def block(self, text, block_id="b1"):
        path = write(self.remote_path(f"/srv/jig/blocks/demo/{block_id}.sh"), text)
        return hashlib.sha256(path.read_bytes()).hexdigest()[:8]

    def test_jigblock_runs_the_block_and_sends_the_readback(self):
        hash8 = self.block("echo step-1\necho step-2 >&2\n")
        result = self.client("jigblock", "demo", "b1", hash8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("step-1", result.stdout)
        readback = self.remote_path("/srv/jig/out/demo/b1.txt").read_text(encoding="utf-8")
        self.assertEqual(readback.split(), ["step-1", "step-2"])
        calls = self.scp_calls()
        self.assertEqual(calls[0][:2], ["--", f"{HOST}:/srv/jig/blocks/demo/b1.sh"])
        self.assertEqual(calls[1][2], f"{HOST}:/srv/jig/out/demo/")

    def test_jigblock_refuses_a_wrong_hash_and_runs_nothing(self):
        marker = self.tmp / "ran"
        hash8 = self.block(f"touch '{marker}'\n")
        wrong = format(int(hash8, 16) ^ 1, "08x")
        result = self.client("jigblock", "demo", "b1", wrong)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"hash {hash8}, expected {wrong}", result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse(self.remote_path("/srv/jig/out/demo/b1.txt").exists())
        self.assertEqual(len(self.scp_calls()), 1)

    def test_jigblock_keeps_the_blocks_exit_code_and_still_sends_the_readback(self):
        hash8 = self.block("echo before\nexit 3\n")
        result = self.client("jigblock", "demo", "b1", hash8)
        self.assertEqual(result.returncode, 3)
        self.assertTrue(self.remote_path("/srv/jig/out/demo/b1.txt").exists())

    def test_jigblock_refuses_a_missing_block(self):
        result = self.client("jigblock", "demo", "b9", "0123abcd")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no block b9 for demo", result.stderr)

    def test_jigblock_refuses_without_jig_host_and_fetches_nothing(self):
        hash8 = self.block("echo step-1\n")
        result = self.client("jigblock", "demo", "b1", hash8,
                             env=self.client_env(host=None))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "jigblock: JIG_HOST is not set\n")
        self.assertEqual(self.scp_calls(), [])


if __name__ == "__main__":
    unittest.main()
