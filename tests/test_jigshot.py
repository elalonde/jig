import os
import pathlib
import re
import stat
import unittest

from tests.jigfn_support import HOST, ClientTestCase, write

# A double for the screenshot command: it records its arguments and, when
# SHOT_OUT names a directory, saves one image there after a short wait, so the
# image is newer than the mark on any file system's clock.
SHOT_DOUBLE = r"""#!/bin/bash
printf '%s\n' "$@" >"$SHOT_ARGS"
if [ -n "${SHOT_OUT:-}" ]; then
    sleep 0.05
    printf 'png' >"$SHOT_OUT/new.png"
fi
"""

OLD = 946684800  # 2000-01-01


class JigshotTest(ClientTestCase):
    def setUp(self):
        super().setUp()
        self.shot = self.double("shot-double", SHOT_DOUBLE)
        self.shot_args = self.tmp / "shot.args"
        self.dir = self.tmp / "shots"
        old = write(self.dir / "old.png", "old")
        os.utime(old, (OLD, OLD))

    def shot_env(self, out, **extra):
        env = self.client_env(JIGSHOT_CMD=str(self.shot), SHOT_ARGS=str(self.shot_args),
                              **extra)
        if out is not None:
            env["SHOT_OUT"] = str(out)
        return env

    def test_jigshot_sends_one_file_on_a_new_image(self):
        env = self.shot_env(self.dir, JIGSHOT_DIR=str(self.dir))
        result = self.client("jigshot", "demo", "-q", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.shot_args.read_text(encoding="utf-8").split(), ["-q"])
        calls = self.scp_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0][0], "--")
        name = pathlib.Path(calls[0][1]).name
        self.assertRegex(name, r"^shot-\d{8}T\d{6}\.png$")
        self.assertEqual(calls[0][2], f"{HOST}:/srv/jig/in/demo/")
        sent = self.remote_path(f"/srv/jig/in/demo/{name}")
        self.assertEqual(sent.read_text(encoding="utf-8"), "png")

    def test_jigshot_private_image_lands_readable_by_the_group(self):
        # Screenshot tools save with mode 600.
        double = SHOT_DOUBLE.replace("printf 'png' >", "umask 077; printf 'png' >")
        self.shot = self.double("shot-double", double)
        env = self.shot_env(self.dir, JIGSHOT_DIR=str(self.dir))
        result = self.client("jigshot", "demo", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE((self.dir / "new.png").stat().st_mode), 0o600)
        sent = list(self.remote_path("/srv/jig/in/demo").iterdir())
        self.assertEqual(len(sent), 1, sent)
        self.assertTrue(sent[0].stat().st_mode & stat.S_IRGRP, oct(sent[0].stat().st_mode))

    def test_jigshot_refuses_and_sends_nothing_with_no_new_image(self):
        env = self.shot_env(None, JIGSHOT_DIR=str(self.dir))
        result = self.client("jigshot", "demo", "-q", env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no new image", result.stderr)
        self.assertEqual(self.scp_calls(), [])

    def test_jigshot_reads_the_default_directory(self):
        default = self.home / "Pictures" / "Screenshots"
        default.mkdir(parents=True)
        result = self.client("jigshot", "demo", env=self.shot_env(default))
        self.assertEqual(result.returncode, 0, result.stderr)
        # Only the default directory holds a new image, so one sent is that one.
        self.assertEqual(len(self.scp_calls()), 1)
        self.assertEqual([path.read_text() for path in
                          self.remote_path("/srv/jig/in/demo").iterdir()], ["png"])

    def test_jigshot_refuses_without_jig_host_and_takes_no_image(self):
        env = self.shot_env(self.dir, JIGSHOT_DIR=str(self.dir))
        del env["JIG_HOST"]
        result = self.client("jigshot", "demo", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "jigshot: JIG_HOST is not set\n")
        self.assertFalse(self.shot_args.exists())
        self.assertEqual(self.scp_calls(), [])


if __name__ == "__main__":
    unittest.main()
