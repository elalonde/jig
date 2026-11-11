import pathlib
import stat
import unittest

from tests.jigfn_support import HOST, JIGFN, ClientTestCase, write
from tests.support import run


class JiginTest(ClientTestCase):
    def test_jigin_scp_arguments_name_the_project_inbox(self):
        notes = write(self.tmp / "notes.md", "a review\n")
        result = self.client("jigin", "demo", notes)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.scp_calls()
        self.assertEqual(len(calls), 1, calls)
        # What goes is a copy under the same name, gone after the call.
        self.assertEqual(calls[0][0], "--")
        self.assertEqual(pathlib.Path(calls[0][1]).name, "notes.md")
        self.assertFalse(pathlib.Path(calls[0][1]).exists())
        self.assertEqual(calls[0][2], f"{HOST}:/srv/jig/in/demo/")
        landed = self.remote_path("/srv/jig/in/demo/notes.md")
        self.assertEqual(landed.read_text(encoding="utf-8"), "a review\n")

    def test_jigin_private_file_lands_readable_by_the_group(self):
        notes = write(self.tmp / "private.md", "for the orchestrator\n")
        notes.chmod(0o600)
        result = self.client("jigin", "demo", notes)
        self.assertEqual(result.returncode, 0, result.stderr)
        landed = self.remote_path("/srv/jig/in/demo/private.md")
        self.assertTrue(landed.stat().st_mode & stat.S_IRGRP, oct(landed.stat().st_mode))
        self.assertEqual(stat.S_IMODE(notes.stat().st_mode), 0o600)

    def test_jigin_returns_the_scp_failure(self):
        notes = write(self.tmp / "notes.md", "a review\n")
        self.double("scp", "#!/bin/bash\nexit 3\n")
        self.assertEqual(self.client("jigin", "demo", notes).returncode, 3)

    def test_jigin_refuses_without_jig_host_and_sends_nothing(self):
        notes = write(self.tmp / "notes.md", "a review\n")
        result = self.client("jigin", "demo", notes, env=self.client_env(host=None))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "jigin: JIG_HOST is not set\n")
        self.assertEqual(self.scp_calls(), [])

    def test_jigin_refuses_a_project_that_leaves_its_path(self):
        notes = write(self.tmp / "notes.md", "a review\n")
        result = self.client("jigin", "../demo", notes)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bad project", result.stderr)
        self.assertEqual(self.scp_calls(), [])

    def test_jigin_refuses_a_missing_file(self):
        result = self.client("jigin", "demo", self.tmp / "absent.md")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.scp_calls(), [])

    def test_jigfn_loads_silently_in_a_non_interactive_strict_shell(self):
        script = 'set -euo pipefail; source "$0"; declare -F jigin jigblock jigshot'
        result = run(["bash", "-c", script, JIGFN], env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["jigin", "jigblock", "jigshot"])
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
