"""The end of a run of make real-start, which leaves nothing of the run behind.

A run of real-start starts real Claude Code sessions in this account, makes temporary
folders, and moves the approved worktree ~/ws/src/jig-dev-approved to the tag of its
test. The start marks each folder that it passes as trusted in ~/.claude.json, and a
session adds an entry for the folder it works in. At its end, after a pass, after a
failure, and when the run gets SIGTERM, SIGHUP or SIGINT, the run ends the sessions that
it started, removes its temporary folders, puts the approved worktree back at the commit
where it found it, and then removes the entries of its folders from "projects" of
~/.claude.json. The entries go last because a session that still runs writes the whole
file from its own read and could put an entry back. The next make check in the account
reads the approved worktree, so it must be back at the commit of the running work.
SIGKILL cannot be caught, so a killed run has no end.

Every temporary folder of the run, and of jig's other tests, has the name prefix
PREFIX in the temporary directory, /tmp unless TMPDIR says otherwise.
"""

import getpass
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

import jig_trust  # noqa: E402

PREFIX = "jig-test."
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
# How long the processes of an ended session get to exit on their own; Claude Code
# saves its state as it exits. What is left after that is killed.
SESSION_EXIT_SECONDS = 20
KILLED_EXIT_SECONDS = 2


def capture(*command):
    return subprocess.run(command, capture_output=True, text=True, check=False)


def ignore_stop_signals():
    """Let no further signal cut the end short."""
    for signum in STOP_SIGNALS:
        signal.signal(signum, signal.SIG_IGN)


def session_members(leader):
    """Return the live processes of the terminal session that a pane's process leads.

    tmux starts each pane's command as the leader of a new session, so every process of
    the pane that did not leave it carries that number.
    """
    members = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            text = (entry / "stat").read_text(encoding="utf-8")
        except OSError:
            continue
        # The name in parentheses can hold spaces; the fields after it are plain.
        fields = text.rsplit(")", 1)[1].split()
        if fields[0] != "Z" and int(fields[3]) == leader:
            members.append(int(entry.name))
    return members


def wait_until_gone(leaders, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not any(session_members(leader) for leader in leaders):
            return True
        time.sleep(0.05)
    return not any(session_members(leader) for leader in leaders)


def remove_tree(path):
    """Remove a folder of the run; a worktree in it is read-only, so it opens first."""
    if os.path.lexists(path):
        capture("chmod", "-R", "u+w", str(path))
        shutil.rmtree(path, ignore_errors=True)


class RunEnd:
    """What one run started and made, and the end that removes it.

    A test registers a session before it starts it and a folder when it makes it, so
    that the end touches only what this run made.
    """

    def __init__(self, approved, home=None, tmux=("tmux",)):
        self.approved = pathlib.Path(approved)
        # The command that reaches the tmux server of the sessions; a test passes one
        # with its own server, so that it never touches the server of the account.
        self.tmux = tuple(tmux)
        self.home = pathlib.Path.home() if home is None else pathlib.Path(home)
        self.found_at = None
        self.sessions = []
        self.folders = []
        self.problems = []
        self.ending = False

    def begin(self):
        """Note the commit of the approved worktree before any test moves it."""
        if (self.approved / ".git").exists():
            self.found_at = capture("git", "-C", str(self.approved), "rev-parse",
                                    "HEAD").stdout.strip() or None

    def session(self, name):
        if name not in self.sessions:
            self.sessions.append(name)
        return name

    def folder(self, path):
        if str(path) not in self.folders:
            self.folders.append(str(path))
        return pathlib.Path(path)

    def make_folder(self):
        return self.folder(tempfile.mkdtemp(prefix=PREFIX))

    def stop_session(self, name):
        """End a tmux session and wait until its processes are gone.

        tmux kill-session returns at once, while Claude Code still writes its state as
        it exits; a removal of entries before that would be undone.
        """
        listed = capture(*self.tmux, "list-panes", "-s", "-t", f"={name}", "-F",
                         "#{pane_pid}")
        leaders = [int(word) for word in listed.stdout.split()] if listed.returncode == 0 \
            else []
        capture(*self.tmux, "kill-session", "-t", f"={name}")
        if wait_until_gone(leaders, SESSION_EXIT_SECONDS):
            return
        for leader in leaders:
            for pid in session_members(leader):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        if not wait_until_gone(leaders, KILLED_EXIT_SECONDS):
            self.problems.append(f"real-start: the processes of the session {name} "
                                 f"did not end")

    def put_back(self):
        """Put the approved worktree back at the commit where the run found it."""
        if self.found_at is None:
            return
        tree = str(self.approved)
        if capture("git", "-C", tree, "rev-parse", "HEAD").stdout.strip() == self.found_at:
            return
        # The start leaves the worktree read-only; it goes back to read-only here too.
        capture("chmod", "-R", "u+w", tree)
        moved = capture("git", "-C", tree, "checkout", "-q", "--detach", self.found_at)
        capture("chmod", "-R", "a-w", tree)
        at = capture("git", "-C", tree, "rev-parse", "HEAD").stdout.strip()
        if moved.returncode != 0 or at != self.found_at:
            self.problems.append(f"real-start: cannot put {tree} back at {self.found_at}: "
                                 f"{(moved.stderr.strip().splitlines() or [at])[-1]}")

    def remove_entries(self):
        """Remove the entry of each folder of the run, and of each folder in it."""
        made = list(self.folders)

        def of_the_run(name):
            return any(name == folder or name.startswith(folder + "/") for folder in made)

        problem = jig_trust.remove_entries(of_the_run, getpass.getuser(), home=self.home)[1]
        if problem:
            self.problems.append(problem)

    def end(self):
        """Run the end once, in its order; return the lines of what it could not do."""
        if self.ending:
            return self.problems
        self.ending = True
        ignore_stop_signals()
        for name in self.sessions:
            self.stop_session(name)
        for folder in self.folders:
            remove_tree(folder)
        self.put_back()
        self.remove_entries()
        return self.problems

    def on_signal(self, signum, _frame):
        # A second signal can reach Python before the end ignores signals; it must not
        # start the end again or exit while the first end runs.
        if self.ending:
            return
        # unittest may be in the middle of a line of its own output.
        print(file=sys.stderr)
        for line in self.end():
            print(line, file=sys.stderr, flush=True)
        print(f"real-start: stopped by {signal.Signals(signum).name}; the end ran",
              file=sys.stderr, flush=True)
        sys.stdout.flush()
        # A plain exit would be caught by unittest as an error of the running test,
        # which would then go on with the next one.
        os._exit(128 + signum)


def module_hooks(end):
    """Return setUpModule and tearDownModule for a test module whose run has this end."""

    def set_up_module():
        end.begin()
        for signum in STOP_SIGNALS:
            signal.signal(signum, end.on_signal)

    def tear_down_module():
        problems = end.end()
        if problems:
            # unittest may be in the middle of a line of its own output.
            print(file=sys.stderr)
        for line in problems:
            print(line, file=sys.stderr, flush=True)
        if problems:
            raise RuntimeError("the end of the run left something behind; see the lines "
                               "before this one")

    return set_up_module, tear_down_module
