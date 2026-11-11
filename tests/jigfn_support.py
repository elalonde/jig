"""What the tests of the client functions share: share/jigfn.sh and a double for scp.

The scp double logs each call and copies files, reading <host>:<path> as
<remote root>/<path>, so a test sees what reached the host and with which
arguments. Like scp, it keeps the source's mode. No network is used.
"""

import os
import pathlib

from tests.support import ROOT, TempTestCase, run

JIGFN = ROOT / "share" / "jigfn.sh"
HOST = "person@example.invalid"
SEPARATOR = "\x1f"

SCP_DOUBLE = r"""#!/bin/bash
printf '%s\x1f' "$@" >>"$SCP_LOG"
echo >>"$SCP_LOG"
paths=()
for word in "$@"; do
    case $word in
        -*) ;;
        /*) paths+=("$word") ;;
        *:*) paths+=("$FAKE_REMOTE/${word#*:}") ;;
        *) paths+=("$word") ;;
    esac
done
# scp gives a new file the mode of its source, whatever the umask.
exec cp --preserve=mode -- "${paths[@]}"
"""


class ClientTestCase(TempTestCase):
    """A test with an scp double on PATH and a directory that stands for the host."""

    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.double("scp", SCP_DOUBLE)
        self.scp_log = self.tmp / "scp.log"
        self.remote = self.tmp / "remote"
        for part in ("in", "out", "blocks"):
            (self.remote / "srv" / "jig" / part / "demo").mkdir(parents=True)

    def double(self, name, text):
        path = self.bin / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
        return path

    def remote_path(self, path):
        return self.remote / path.lstrip("/")

    def client_env(self, host=HOST, **extra):
        env = self.env(PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                       SCP_LOG=str(self.scp_log), FAKE_REMOTE=str(self.remote), **extra)
        if host is not None:
            env["JIG_HOST"] = host
        return env

    def client(self, *command, env=None):
        """Source the file in a non-interactive bash and run one function."""
        return run(["bash", "-c", 'source "$0" && "$@"', JIGFN, *command],
                   env=self.client_env() if env is None else env, cwd=self.tmp)

    def scp_calls(self):
        if not self.scp_log.exists():
            return []
        lines = self.scp_log.read_text(encoding="utf-8").splitlines()
        return [line.split(SEPARATOR)[:-1] for line in lines]


def write(path, text):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
