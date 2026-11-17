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
import stat
import subprocess
import textwrap
import time
import unittest
from unittest import mock

from tests import real_start_end
from tests.support import ROOT, TempTestCase, clean_env, run

SESSION = "endfx-session"
# The private tmux server of these tests, in their own TMUX_TMPDIR; the server of the
# account is never touched.
TMUX_SERVER = "jig-test-end"

# The fixture run. RUN_MODE says how its test ends: pass, fail, signal, broken, which
# passes after it makes the home read-only, so that no new ~/.claude.json can be made
# beside the old one, or worker, which runs CHILD_SCRIPT through the end and waits for
# it. Every run also makes the file RUN_FILE outside its temporary folders, as a run
# makes the conf file of its fixture project.
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
            END.file(os.environ["RUN_FILE"]).write_text("made by the run",
                                                         encoding="utf-8")
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
            if mode == "worker":
                END.run(sys.executable, os.environ["RUN_CHILD"], approved)
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


# The stand-in for a worker of jig start that a signal does not reach: unless it is
# ended, it moves the approved worktree to the other commit CHILD_SECONDS after its
# start, as a worker checks out the tag of the test. Ended with SIGTERM, it still moves
# the worktree, after CHILD_STOP_SECONDS, as a worker that is in the middle of its
# checkout finishes it; so an end that does not wait for it puts the worktree back too
# soon. Both leave the tree open.
CHILD_SECONDS = 2
CHILD_STOP_SECONDS = 1
CHILD_SCRIPT = textwrap.dedent('''
    import os, pathlib, signal, subprocess, sys, time
    approved = sys.argv[1]

    def move():
        subprocess.run(["chmod", "-R", "u+w", approved], check=True)
        subprocess.run(["git", "-C", approved, "checkout", "-q", "--detach",
                        os.environ["RUN_OTHER"]], check=True)

    def stop(signum, frame):
        time.sleep(@STOP@)
        move()
        sys.exit(1)

    signal.signal(signal.SIGTERM, stop)
    (pathlib.Path.home() / "child-started").write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(@SECONDS@)
    move()
''').replace("@STOP@", str(CHILD_STOP_SECONDS)).replace("@SECONDS@", str(CHILD_SECONDS))


class SessionMembersProcTest(TempTestCase):
    """session_members reads every process of the host, and one odd entry must not stop it.

    A fixture folder stands in for /proc; the leader is a made-up session number.
    """

    LEADER = 4000

    def setUp(self):
        super().setUp()
        self.proc = self.tmp / "proc"
        patcher = mock.patch.object(real_start_end, "PROC", self.proc)
        patcher.start()
        self.addCleanup(patcher.stop)

    def entry(self, pid, data):
        folder = self.proc / str(pid)
        folder.mkdir(parents=True)
        (folder / "stat").write_bytes(data)

    def test_a_process_whose_name_is_not_utf8_is_still_a_member(self):
        # A live process of the session has a name of bytes that are not UTF-8, and
        # another a name cut in the middle of a character: both are members.
        self.entry(4001, b"4001 (odd\xff\xfe name) S 1 4001 4000 0")
        self.entry(4002, b"4002 (cut\xe2\x82) S 1 4002 4000 0")
        self.assertEqual(sorted(real_start_end.session_members(self.LEADER)), [4001, 4002])

    def test_entries_that_cannot_be_read_are_skipped(self):
        # One entry has no closing parenthesis, one has no fields after the name, and
        # one a session that is no number; one live member is left to find.
        self.entry(4001, b"4001 (no end S 1 1 4000 0")
        self.entry(4002, b"4002 (empty)")
        self.entry(4003, b"4003 (word) S 1 1 many 0")
        self.entry(4004, b"4004 (sleep) S 1 4004 4000 0")
        self.assertEqual(real_start_end.session_members(self.LEADER), [4004])


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
        (self.tmp / "child.py").write_text(CHILD_SCRIPT, encoding="utf-8")
        # Outside the home, which one case makes read-only.
        self.made = self.tmp / "made" / "fixture.conf"
        self.made.parent.mkdir()
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
                         RUN_CHILD=str(self.tmp / "child.py"), RUN_FILE=str(self.made),
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

    def wait_for(self, name):
        deadline = time.monotonic() + 30
        while not (self.home / name).exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((self.home / name).exists(), f"{name} never came")

    def assert_nothing_left(self):
        """No session, no temporary folder, no file of the run, the worktree back and
        read-only, no entry of the run."""
        listed = run(["tmux", "-L", TMUX_SERVER, "has-session", "-t", f"={SESSION}"],
                     env=clean_env(TMUX_TMPDIR=str(self.tmp)))
        self.assertNotEqual(listed.returncode, 0, "the session of the run still runs")
        self.assertEqual(list(self.temp.iterdir()), [])
        self.assertFalse(self.made.exists(), "the file of the run is still there")
        self.assertEqual(self.git(self.approved, "rev-parse", "HEAD"), self.first)
        # The write bits, not os.access, which says yes to root for every file.
        mode = (self.approved / "spec.md").stat().st_mode
        self.assertEqual(stat.S_IMODE(mode) & 0o222, 0, oct(mode))

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
        self.wait_for("run-ready")
        self.assertTrue(self.made.exists())
        process.send_signal(signal.SIGTERM)
        time.sleep(0.1)
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        self.assertIn("real-start: stopped by SIGTERM; the end ran", err)
        self.assert_nothing_left()
        self.assert_entries_removed()

    def test_a_run_stopped_while_a_worker_runs_ends_the_worker_before_the_rest(self):
        # The signal reaches the run alone, while its worker still runs. The end stops
        # the worker and waits for it before it puts the worktree back, so the worker
        # moves the worktree neither after the end nor during it.
        process = self.start_run("worker")
        self.wait_for("child-started")
        worker = int((self.home / "child-started").read_text(encoding="utf-8"))
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        # A worker that the end left would move the worktree once its time is up.
        deadline = time.monotonic() + CHILD_SECONDS + 3
        while pathlib.Path(f"/proc/{worker}").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(pathlib.Path(f"/proc/{worker}").exists(), "the worker still runs")
        self.assert_nothing_left()
        self.assert_entries_removed()

    def test_put_back_closes_a_worktree_left_open_at_the_commit_where_it_was_found(self):
        # A worker stopped after it opened the worktree and before it moved it leaves
        # the tree writable at the same commit; put_back makes it read-only again.
        end = real_start_end.RunEnd(self.approved)
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            end.begin()
            run(["chmod", "-R", "u+w", self.approved])
            end.put_back()
        self.assertEqual(end.problems, [])
        self.assertEqual(self.git(self.approved, "rev-parse", "HEAD"), self.first)
        mode = (self.approved / "spec.md").stat().st_mode
        self.assertEqual(stat.S_IMODE(mode) & 0o222, 0, oct(mode))

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
