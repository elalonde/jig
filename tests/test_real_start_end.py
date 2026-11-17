"""The end of a run of real-start, on a fixture run in a fixture home.

Each test runs a small test module as real-start runs its own: in a fixture home, with
a private tmux server that it names, and with the same end. The module's one test does
what a run of real-start leaves behind: it starts a session in a temporary folder, the
folder gets an entry in ~/.claude.json, and the approved worktree moves to another
commit. The session
stands in for Claude Code: at its end it writes ~/.claude.json from its own earlier
read, which still holds the entry, and only then exits. Then the test passes, fails,
or waits for a signal, and the end must leave nothing of the run behind in each case.
"""

import getpass
import json
import os
import pathlib
import signal
import subprocess
import textwrap
import time
import unittest

from tests.support import ROOT, TempTestCase, clean_env, run

SESSION = "endfx-session"
# The private tmux server of these tests, in their own TMUX_TMPDIR; the server of the
# account is never touched.
TMUX_SERVER = "jig-test-end"

# The fixture run. RUN_MODE says how its test ends: pass, fail, signal, or broken,
# which passes after it makes the home read-only, so that no new ~/.claude.json can be
# made beside the old one.
RUN_MODULE = textwrap.dedent('''
    import json, os, pathlib, subprocess, sys, time, unittest
    sys.path.insert(0, os.environ["RUN_ROOT"])
    sys.path.insert(0, os.path.join(os.environ["RUN_ROOT"], "lib"))
    from tests import real_start_end
    import jig_trust

    HOME = pathlib.Path.home()
    TMUX = ["tmux", "-L", os.environ["RUN_TMUX_SERVER"]]
    END = real_start_end.RunEnd(HOME / "ws" / "src" / "jig-dev-approved", tmux=TMUX)
    setUpModule, tearDownModule = real_start_end.module_hooks(END)

    class FixtureRun(unittest.TestCase):
        def test_run(self):
            approved = str(END.approved)
            folder = END.make_folder()
            END.session("@SESSION@")
            jig_trust.mark_all([(str(folder), jig_trust.TRUST_KEY)], "fixture")
            subprocess.run([*TMUX, "new-session", "-d", "-s", "@SESSION@", "-c",
                            str(folder), sys.executable + " " + os.environ["RUN_SESSION"]],
                           check=True)
            while not (HOME / "session-read").exists():
                time.sleep(0.02)
            subprocess.run(["chmod", "-R", "u+w", approved], check=True)
            subprocess.run(["git", "-C", approved, "checkout", "-q", "--detach",
                            os.environ["RUN_OTHER"]], check=True)
            subprocess.run(["chmod", "-R", "a-w", approved], check=True)
            mode = os.environ["RUN_MODE"]
            (HOME / "run-ready").write_text("", encoding="utf-8")
            if mode == "broken":
                HOME.chmod(0o555)
            if mode == "fail":
                self.fail("a forced failure")
            if mode == "signal":
                time.sleep(60)
''').replace("@SESSION@", SESSION)

# The stand-in for a Claude Code session: it reads the file once at its start and, at
# the hangup of tmux kill-session, writes that old copy back after RUN_SAVE_SECONDS, as
# Claude Code saves its state while it exits. The pause is longer than the rest of the
# end takes, so an end that does not wait for the session removes the entries too soon.
SESSION_SCRIPT = textwrap.dedent('''
    import os, pathlib, signal, sys, time
    state = pathlib.Path.home() / ".claude.json"
    old = state.read_text(encoding="utf-8")
    (pathlib.Path.home() / "session-read").write_text("", encoding="utf-8")

    def save_and_exit(signum, frame):
        time.sleep(float(os.environ["RUN_SAVE_SECONDS"]))
        state.write_text(old, encoding="utf-8")
        (pathlib.Path.home() / "session-saved").write_text("", encoding="utf-8")
        sys.exit(0)

    signal.signal(signal.SIGHUP, save_and_exit)
    signal.signal(signal.SIGTERM, save_and_exit)
    while True:
        time.sleep(1)
''')


class RealStartEndTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.temp = self.tmp / "temp"
        self.temp.mkdir()
        dev = self.home / "ws" / "src" / "jig-dev"
        self.git_init(dev)
        (dev / "spec.md").write_text("first\n", encoding="utf-8")
        self.git_commit_all(dev)
        self.first = self.git(dev, "rev-parse", "HEAD")
        (dev / "spec.md").write_text("second\n", encoding="utf-8")
        self.git_commit_all(dev)
        self.other = self.git(dev, "rev-parse", "HEAD")
        self.approved = self.home / "ws" / "src" / "jig-dev-approved"
        self.git(dev, "worktree", "add", "-q", "--detach", str(self.approved), self.first)
        run(["chmod", "-R", "a-w", self.approved])
        self.addCleanup(run, ["chmod", "-R", "u+w", self.tmp])
        self.kept = str(dev)
        self.state = self.home / ".claude.json"
        self.state.write_text(json.dumps({"theme": "dark", "projects": {
            self.kept: {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
        (self.tmp / "fixture_run.py").write_text(RUN_MODULE, encoding="utf-8")
        (self.tmp / "session.py").write_text(SESSION_SCRIPT, encoding="utf-8")
        self.addCleanup(self.end_tmux)

    def git(self, repo, *args):
        result = run(["git", "-C", repo, *args], env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def run_env(self, mode, save_seconds):
        return clean_env(HOME=str(self.home), TMPDIR=str(self.temp),
                         RUN_SAVE_SECONDS=str(save_seconds),
                         TMUX_TMPDIR=str(self.tmp), RUN_TMUX_SERVER=TMUX_SERVER,
                         RUN_ROOT=str(ROOT), RUN_MODE=mode,
                         RUN_OTHER=self.other, RUN_SESSION=str(self.tmp / "session.py"),
                         PYTHONDONTWRITEBYTECODE="1")

    def end_tmux(self):
        run(["tmux", "-L", TMUX_SERVER, "kill-server"],
            env=clean_env(TMUX_TMPDIR=str(self.tmp)))

    def start_run(self, mode, save_seconds=0.2):
        return subprocess.Popen(["python3", "-m", "unittest", "fixture_run"], cwd=self.tmp,
                                env=self.run_env(mode, save_seconds), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def run_to_end(self, mode, save_seconds=0.2):
        process = self.start_run(mode, save_seconds)
        out, err = process.communicate(timeout=60)
        return process.returncode, err

    def assert_nothing_left(self):
        """No session, no temporary folder, the worktree back, no entry of the run."""
        listed = run(["tmux", "-L", TMUX_SERVER, "has-session", "-t", f"={SESSION}"],
                     env=clean_env(TMUX_TMPDIR=str(self.tmp)))
        self.assertNotEqual(listed.returncode, 0, "the session of the run still runs")
        self.assertEqual(list(self.temp.iterdir()), [])
        self.assertEqual(self.git(self.approved, "rev-parse", "HEAD"), self.first)
        self.assertFalse(os.access(self.approved / "spec.md", os.W_OK))

    def assert_entries_removed(self):
        # An end that did not wait for the session would be over before the session
        # saves; the check then waits for that save, so the old copy is seen.
        deadline = time.monotonic() + 5
        while not (self.home / "session-saved").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((self.home / "session-saved").exists(), "the session never saved")
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state, {"theme": "dark", "projects": {
            self.kept: {"hasTrustDialogAccepted": True}}})

    def test_a_run_that_passes_leaves_nothing_and_the_session_puts_no_entry_back(self):
        code, err = self.run_to_end("pass", save_seconds=0.5)
        self.assertEqual(code, 0, err)
        self.assert_nothing_left()
        self.assert_entries_removed()

    def test_a_run_that_fails_leaves_nothing(self):
        code, err = self.run_to_end("fail")
        self.assertEqual(code, 1, err)
        self.assertIn("a forced failure", err)
        self.assert_nothing_left()
        self.assert_entries_removed()

    def test_a_run_stopped_by_sigterm_ends_also_through_a_second_sigterm(self):
        # The second SIGTERM comes while the end waits for the session to save and exit.
        process = self.start_run("signal")
        deadline = time.monotonic() + 30
        while not (self.home / "run-ready").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        process.send_signal(signal.SIGTERM)
        time.sleep(0.1)
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        self.assertIn("real-start: stopped by SIGTERM; the end ran", err)
        self.assert_nothing_left()
        self.assert_entries_removed()

    @unittest.skipIf(os.geteuid() == 0, "root writes in a directory of any mode")
    def test_a_file_that_the_end_cannot_use_fails_the_run_with_its_line(self):
        code, err = self.run_to_end("broken")
        self.home.chmod(0o755)
        self.assertNotEqual(code, 0, err)
        lines = [line for line in err.splitlines() if line.startswith("jig: ")]
        self.assertEqual(len(lines), 1, err)
        self.assertTrue(lines[0].startswith(
            f"jig: cannot remove the entries of folders from {self.state} of "
            f"{getpass.getuser()}: cannot write it"), lines[0])
        # The rest of the end still ran.
        self.assert_nothing_left()
