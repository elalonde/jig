"""The worker parts of jig start and jig close in the real account: real tmux, real claude.

Run by make real-start, not by make check: it needs this account's conf file,
its -dev clone with a clean tree, and its Claude Code login. The start test
starts one small session, starts it again, and removes only that session and
that lock. The close test leaves a session at the folder-trust dialog and
closes it as a dead unit. Both tests use their own test IDs, which no real unit
uses.
"""

import getpass
import grp
import importlib.machinery
import importlib.util
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from tests.support import ROOT

WORKER = ROOT / "libexec" / "jig-start-worker"
CLOSE_WORKER = ROOT / "libexec" / "jig-close-worker"
PROJECT = "jig"
UNIT = "p0st0"
SESSION = f"{PROJECT}-impl-{UNIT}"
CLOSE_UNIT = "x9cls"
CLOSE_SESSION = f"{PROJECT}-impl-{CLOSE_UNIT}"
TAG = "spec-p1"
MODEL = "claude-haiku-5-5"
EXPECTED = "5"
OUTPUT_WAIT_SECONDS = 120

HOME = pathlib.Path.home()
CLONE = HOME / "ws" / "src" / f"{PROJECT}-dev"
APPROVED = HOME / "ws" / "src" / f"{PROJECT}-dev-approved"
TEE = HOME / "ws" / "reviews" / PROJECT / "out" / f"{UNIT}.txt"
CLOSE_TRANSCRIPTS = HOME / "ws" / "reviews" / PROJECT / "transcripts" / CLOSE_UNIT
LOCK_NAME = f"{PROJECT}-{getpass.getuser()}"
REAL_LOCK = pathlib.Path("/run/jig/lock") / LOCK_NAME
DIALOG_WAIT_SECONDS = 60


def load_start_worker():
    sys.dont_write_bytecode = True
    loader = importlib.machinery.SourceFileLoader("jig_start_worker", str(WORKER))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def lock_holder(lock):
    """Return the session that a lock names, or None when there is no lock."""
    try:
        text = lock.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    found = re.search(r"^session=(.*)$", text, re.MULTILINE)
    return found[1].strip() if found else ""


def capture(*command, env=None):
    return subprocess.run(command, capture_output=True, text=True, check=False, env=env)


def session_alive(session=SESSION):
    return capture("tmux", "has-session", "-t", f"={session}").returncode == 0


def session_groups():
    """Return what id -nG prints in the session. The exec chain leaves it on the pane's pid."""
    pane = capture("tmux", "list-panes", "-t", f"={SESSION}:", "-F", "#{pane_pid}").stdout.split()[0]
    status = pathlib.Path("/proc", pane, "status").read_text(encoding="utf-8")
    effective = re.search(r"^Gid:\s+\d+\s+(\d+)", status, re.MULTILINE)[1]
    others = re.search(r"^Groups:\s*(.*)$", status, re.MULTILINE)[1].split()
    return [grp.getgrgid(int(gid)).gr_name for gid in dict.fromkeys([effective, *others])]


class RealStartTest(unittest.TestCase):
    def setUp(self):
        # A running unit holds the real lock, and the start test must not wait for it
        # to end or take it: the worker then takes a lock in a scratch directory.
        self.env = dict(os.environ)
        self.lock = REAL_LOCK
        holder = lock_holder(REAL_LOCK)
        if holder is not None and holder != SESSION:
            scratch = tempfile.mkdtemp(prefix="jig-real-start-lock.")
            self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
            self.env["JIG_LOCK_DIR"] = scratch
            self.lock = pathlib.Path(scratch) / LOCK_NAME
            print(f"{REAL_LOCK} names {holder or 'no session'}: this test locks in {scratch}")
        self.beside = self.lock.with_name(self.lock.name + ".expected")

    def run_worker(self, prompt):
        return capture(str(WORKER), PROJECT, "impl", UNIT, str(prompt), "--expected", EXPECTED,
                       "--model", MODEL, "--approved-tag", TAG, "--tag", TAG, env=self.env)

    def stop_session(self):
        capture("tmux", "kill-session", "-t", f"={SESSION}")
        self.lock.unlink(missing_ok=True)
        self.beside.unlink(missing_ok=True)

    def wait_for_output(self, longer_than):
        deadline = time.monotonic() + OUTPUT_WAIT_SECONDS
        while time.monotonic() < deadline:
            if TEE.exists() and TEE.stat().st_size > longer_than:
                return
            time.sleep(1)
        self.fail(f"{TEE} did not grow past {longer_than} bytes")

    def test_real_start_runs_and_restarts_in_this_account(self):
        self.assertFalse(self.lock.exists(),
                         f"{self.lock} belongs to another unit; this test leaves it")
        self.assertFalse(session_alive(), f"{SESSION} is running")
        size_before = TEE.stat().st_size if TEE.exists() else 0
        self.addCleanup(self.stop_session)

        with tempfile.TemporaryDirectory(prefix="jig-real-start.") as scratch:
            prompt = pathlib.Path(scratch) / "prompt.md"
            prompt.write_text("Say OK, then stop.\n", encoding="utf-8")

            first = self.run_worker(prompt)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertTrue(self.lock.is_file())
            self.assertEqual(self.beside.read_text(encoding="utf-8").strip(), EXPECTED)
            self.assertTrue(session_alive())
            self.wait_for_output(size_before)

            groups = session_groups()
            print(f"session id -nG: {' '.join(groups)}")
            self.assertIn("jig", groups)

            spec = APPROVED / "specs" / "jig-spec.md"
            self.assertTrue(spec.is_file(), spec)
            self.assertEqual(capture("git", "-C", str(APPROVED), "rev-parse", "HEAD").stdout,
                             capture("git", "-C", str(CLONE), "rev-parse",
                                     f"{TAG}^{{commit}}").stdout)
            self.assertFalse(os.access(spec, os.W_OK), spec)

            refused = self.run_worker(prompt)
            self.assertEqual(refused.returncode, 1, refused.stderr)
            self.assertIn("refused: lock:", refused.stderr)

            self.stop_session()
            kept = TEE.read_bytes()
            second = self.run_worker(prompt)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.wait_for_output(len(kept))
            self.assertTrue(TEE.read_bytes().startswith(kept))


class RealCloseTest(unittest.TestCase):
    def stop_close_session(self):
        capture("tmux", "kill-session", "-t", f"={CLOSE_SESSION}")

    def pane_text(self):
        pane = capture("tmux", "capture-pane", "-p", "-t", f"={CLOSE_SESSION}:").stdout
        return " ".join(pane.split())

    def test_real_close_dead_ends_a_session_left_at_the_trust_dialog(self):
        # A real claude in a new folder that nobody trusted waits at the folder-trust
        # dialog, as after a refused start. The dead close must end it, free its
        # scratch lock and say whether Claude Code wrote a transcript before the dialog.
        self.assertFalse(session_alive(CLOSE_SESSION), f"{CLOSE_SESSION} is running")
        self.assertFalse(CLOSE_TRANSCRIPTS.exists(), f"{CLOSE_TRANSCRIPTS} is not this test's")
        dialog = load_start_worker().TRUST_DIALOG
        folder = tempfile.mkdtemp(prefix="jig-real-close.")
        lock_dir = pathlib.Path(tempfile.mkdtemp(prefix="jig-real-close-lock."))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.addCleanup(shutil.rmtree, lock_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, CLOSE_TRANSCRIPTS, ignore_errors=True)
        self.addCleanup(self.stop_close_session)

        prompt = pathlib.Path(folder) / "prompt.md"
        prompt.write_text(f"# {CLOSE_UNIT}  a close test\nSay OK, then stop.\n", encoding="utf-8")
        # As jig start does: the pane gets this PATH, and no variable that marks a
        # session started from inside another Claude Code session.
        inner = (f"export PATH={shlex.quote(os.environ.get('PATH', ''))}:$PATH; "
                 "unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT; "
                 f'exec claude --model {MODEL} "$(cat {shlex.quote(str(prompt))})"')
        opened = capture("tmux", "new-session", "-d", "-s", CLOSE_SESSION, "-x", "200", "-y", "50",
                         "-c", folder, f"bash -c {shlex.quote(inner)}")
        self.assertEqual(opened.returncode, 0, opened.stderr)
        deadline = time.monotonic() + DIALOG_WAIT_SECONDS
        while dialog not in self.pane_text() and time.monotonic() < deadline:
            time.sleep(1)
        self.assertTrue(session_alive(CLOSE_SESSION))
        self.assertIn(dialog, self.pane_text())

        lock = lock_dir / LOCK_NAME
        lock.write_text(f"session={CLOSE_SESSION}\nstart=now\n", encoding="utf-8")
        beside = lock.with_name(lock.name + ".expected")
        beside.write_text(f"{EXPECTED}\n", encoding="utf-8")
        closed = capture(str(CLOSE_WORKER), PROJECT, "impl", CLOSE_UNIT, "--dead",
                         env={**os.environ, "JIG_LOCK_DIR": str(lock_dir)})
        self.assertEqual(closed.returncode, 0, closed.stderr)
        print(closed.stdout.strip())
        self.assertFalse(session_alive(CLOSE_SESSION))
        self.assertFalse(lock.exists())
        self.assertFalse(beside.exists())
        copied = sorted(path.name for path in (CLOSE_TRANSCRIPTS / "1").iterdir())
        print(f"transcript after a close at the trust dialog: {', '.join(copied) or 'none'}")


if __name__ == "__main__":
    unittest.main()
