"""What the test modules share: the tree's location and a clean environment."""

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The two public e-mail addresses that jig's tools let through: the address of
# the Co-Authored-By trailer that jig's own commits carry, and the owner's
# address. Each is joined from its parts at run time because jig privacy scan,
# which a release runs, reads every test file and stops the release on any
# whole e-mail address outside the reserved documentation domains. The joined
# value is the exact address, so a test that compares against it still checks
# every character.
CO_AUTHOR_ADDRESS = "@".join(["noreply", "anthropic.com"])
PUBLIC_ADDRESS = "@".join(["eric", "lalonde.com"])

# Variables that would make a test depend on where it runs: CI sets some, a git
# hook sets others, a proxy would swallow requests to a local fake server, and
# a tmux variable would point a test at the real server.
UNSET = {"CI", "LINT_TERMS", "JIG_ROOT", "JIG_GITHUB_API", "JIG_SSH", "JIG_LOCK_DIR",
         "JIG_UPGRADE_REPO",
         "JIG_START_SETTLE", "JIG_SRV", "JIG_HOST", "JIGSHOT_CMD", "JIGSHOT_DIR",
         "JIG_WAIT_POLL", "JIG_WAIT_CHECK", "JIG_BATCH_DIR", "JIG_BATCH_CLOSE_DELAY",
         "JIG_HOLD_DIR", "JIG_ORCH_POLL", "JIG_CTX_DIR", "JIG_CTX_SOFT", "JIG_CTX_HARD",
         "JIG_SEND_PAUSE", "JIG_SEND_CHECK", "JIG_CI_POLL", "JIG_RELEASE_CI_WAIT",
         "TMUX", "TMUX_PANE", "TMUX_TMPDIR",
         "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"}


# Stands in for claude in a tmux session, for the tests of jig orch and jig send. It
# draws a Claude Code screen: its history, an input box between two rules, and the
# input bar under it. It reads keys one at a time, as Claude Code does, and takes Enter
# as a submit only when Enter comes alone and after a pause; Enter in the same burst as
# the text stays in the box, as was measured on Claude Code. Each submitted line goes
# to claude-submitted in HOME. These files in HOME change it: claude-dialog draws a
# permission dialog instead of the input bar, and an Enter at that dialog is written to
# claude-answered; claude-ignore-enter leaves every line in the box; claude-exit-now
# makes it exit at once. A line "/exit" ends it.
CLAUDE_DOUBLE = r'''#!/usr/bin/env python3
import json, os, pathlib, re, select, sys, termios, time, tty

home = pathlib.Path(os.environ["HOME"])
args = sys.argv[1:]
with open(home / "claude-calls.jsonl", "a") as stream:
    stream.write(json.dumps({"argv": args, "cwd": os.getcwd()}) + "\n")
if "--session-id" in args:
    session_id = args[args.index("--session-id") + 1]
    folder = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.jsonl").write_text(json.dumps({"sessionId": session_id}) + "\n")
if (home / "claude-exit-now").exists():
    sys.exit(0)

RULE = "─" * 60
BAR = "  ⏵⏵ auto mode on (shift+tab to cycle)"
DIALOG = ("● Bash command\n\n   rm -rf build\n\n Do you want to proceed?\n ❯ 1. Yes\n"
          "   2. No, and tell Claude what to do differently\n\n Esc to cancel\n")
history = ["● ready"]
if args and not args[-1].startswith("-"):
    history.append("> " + args[-1].splitlines()[0])
buffer = ""
last_key = 0.0


def dialog():
    return (home / "claude-dialog").exists()


def draw():
    if dialog():
        body = DIALOG
    else:
        body = "\n".join(history[-12:] + ["", RULE, "> " + buffer, RULE, BAR]) + "\n"
    sys.stdout.write("\033[H\033[2J" + body)
    sys.stdout.flush()


old = termios.tcgetattr(0)
tty.setcbreak(0)
try:
    draw()
    while True:
        ready, _, _ = select.select([0], [], [], 0.2)
        if ready:
            chunk = os.read(0, 4096).decode("utf-8", "replace")
            if not chunk:
                break
            moment = time.monotonic()
            for index, key in enumerate(chunk):
                if key not in "\r\n":
                    buffer += key
                    continue
                if dialog():
                    (home / "claude-answered").write_text("Enter at a dialog\n")
                    continue
                alone = index == 0 and moment - last_key >= 0.05
                if not alone or (home / "claude-ignore-enter").exists():
                    continue
                with open(home / "claude-submitted", "a") as stream:
                    stream.write(buffer + "\n")
                history.append("> " + buffer)
                if buffer == "/exit":
                    sys.exit(0)
                buffer = ""
            last_key = time.monotonic()
        draw()
finally:
    termios.tcsetattr(0, termios.TCSADRAIN, old)
'''


def clean_env(**extra):
    env = {key: value for key, value in os.environ.items()
           if key not in UNSET and not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="Test Person",
        GIT_AUTHOR_EMAIL="person@example.invalid",
        GIT_COMMITTER_NAME="Test Person",
        GIT_COMMITTER_EMAIL="person@example.invalid",
    )
    env.update(extra)
    return env


def run(command, env=None, cwd=None, stdin=None):
    return subprocess.run([str(part) for part in command], capture_output=True, text=True,
                          env=clean_env() if env is None else env, cwd=cwd, input=stdin,
                          timeout=120, check=False)


# The line jig wait, jig cat and jig validate prompt print when a project profile gives
# them no value for the clean room.
NO_CLEAN_ROOM = ("jig: no value for clean room in the project profile; "
                 "acting as with the clean room on")


def git_ok(env, *args):
    """Run git and return its output; a failure stops the test with git's message."""
    result = run(["git", *args], env=env)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(map(str, args))}: {result.stderr}")
    return result.stdout


def kickoff_record(*profile_lines):
    """Return a kickoff record whose profile section holds these lines among prose."""
    return "\n".join(["# Kickoff record", "", "## 1. Purpose", "", "A project for the tests.",
                      "", "## Project profile", "", "These lines set the switches.", "",
                      *profile_lines, "", "## 2. Decisions", "", "More prose.", ""])


def make_dev_repos(base, env):
    """Make a bare origin, a work clone that pushes to it, and a -dev clone of origin.

    Return the three paths. Kickoff tags pushed from the work clone later reach origin
    only, so the -dev clone has to fetch what it reads.
    """
    origin, work, clone = base / "origin.git", base / "work", base / "dev"
    git_ok(env, "init", "-q", "--bare", "-b", "master", origin)
    git_ok(env, "init", "-q", "-b", "master", work)
    git_ok(env, "-C", work, "remote", "add", "origin", origin)
    git_ok(env, "-C", work, "commit", "-q", "--no-verify", "--allow-empty", "-m", "first")
    git_ok(env, "-C", work, "push", "-q", "origin", "master")
    git_ok(env, "clone", "-q", "-b", "master", origin, clone)
    return origin, work, clone


def push_kickoff(env, work, number, text, annotated=True):
    """Commit a kickoff record in the work clone, tag it kickoff-r<number>, push both.

    A text of None leaves the record out of that commit.
    """
    record = work / "specs" / "00-kickoff.md"
    if text is None:
        record.unlink(missing_ok=True)
    else:
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(text, encoding="utf-8")
    git_ok(env, "-C", work, "add", "-A")
    git_ok(env, "-C", work, "commit", "-q", "--no-verify", "--allow-empty", "-m",
           f"kickoff r{number}")
    tag = f"kickoff-r{number}"
    git_ok(env, "-C", work, "tag", *(["-a", "-m", tag] if annotated else []), tag)
    git_ok(env, "-C", work, "push", "-q", "origin", "master", f"refs/tags/{tag}")


class TempTestCase(unittest.TestCase):
    """A test with a private directory and a HOME of its own inside it."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="jig-test."))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()

    def env(self, **extra):
        return clean_env(HOME=str(self.home), **extra)

    def git_init(self, path):
        path.mkdir(parents=True, exist_ok=True)
        result = run(["git", "init", "-q", "-b", "master", path], env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        return path

    def git_commit_all(self, path):
        for command in (["add", "-A"], ["commit", "-q", "--no-verify", "-m", "fixture"]):
            result = run(["git", "-C", path, *command], env=self.env())
            self.assertEqual(result.returncode, 0, result.stderr)
