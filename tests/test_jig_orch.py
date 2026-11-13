"""jig orch: the supervisor loop, its event file and the watcher that reads it, the
announcements, the report lines, the delivery of jig send and the dialog report.

Each test runs a private tmux server (tmux -L with a name of its own, through a tmux
first on PATH) and a double for claude. The tmux on PATH notes each send-keys call, so
that a test can show that no event was typed into the session, and keeps a copy of the
event file as it was when each session was made. The tests of the report lines add an origin repo
that the project's clone was cloned from, and a second clone that pushes the commits a
unit would push. The double for claude is CLAUDE_DOUBLE from tests/support.py, which
jig send's tests share.
"""

import datetime
import hashlib
import importlib.machinery
import importlib.util
import itertools
import json
import os
import pathlib
import re
import select
import shutil
import signal
import subprocess
import sys
import time
import unittest
import uuid
from unittest import mock

from tests.support import CLAUDE_DOUBLE, ROOT, TempTestCase, run
from tests.units_support import DIALOG_SCREEN

sys.path.insert(0, str(ROOT / "lib"))

import jig_tmux  # noqa: E402

ORCH = ROOT / "libexec" / "jig-orch"
LIB = ROOT / "lib"
TEMPLATE = ROOT / "templates" / "project.conf"
PROJECT = "demo"
SESSION = f"{PROJECT}-orch"
POLL = "0.3"
REPORT_POLL = "1"
BRANCH = "master"
STANDING = "You are the orchestrator of demo.\nRead state.md first.\n"
SOCKETS = itertools.count(1)

# A tmux that runs the real one on the test's own server. It notes each send-keys call,
# and, as a session is made, copies the event file as it is at that moment.
TMUX_DOUBLE = '''#!/bin/bash
if [ "$1" = send-keys ]; then printf '%s\\n' "$*" >> "$HOME/tmux-send-keys"; fi
if [ "$1" = new-session ]; then
    cat "$HOME/ws/reviews/demo/orch/events.log" >> "$HOME/events-at-new-session" 2>/dev/null
    echo "--" >> "$HOME/events-at-new-session"
fi
exec @TMUX@ -L @SOCKET@ "$@"
'''

# A git that passes every call to the real one, except the loop's fetch while a flag file
# in HOME asks it to hang or to fail.
GIT_DOUBLE = '''#!/bin/bash
if [ "$3" = fetch ]; then
    if [ -e "$HOME/fetch-stalls" ]; then exec sleep 60; fi
    if [ -e "$HOME/fetch-fails" ]; then
        echo "fatal: origin cannot be reached" >&2
        exit 128
    fi
fi
exec @GIT@ "$@"
'''


class OrchTestCase(TempTestCase):
    def setUp(self):
        super().setUp()
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self.socket = f"jig-orch-test-{os.getpid()}-{next(SOCKETS)}"
        self.script("tmux", TMUX_DOUBLE.replace("@TMUX@", shutil.which("tmux"))
                    .replace("@SOCKET@", self.socket))
        self.script("claude", CLAUDE_DOUBLE)
        self.srv = self.tmp / "srv"
        for part in ("in", "out", "blocks"):
            (self.srv / part / PROJECT).mkdir(parents=True)
        self.clone = self.home / "ws" / "src" / f"{PROJECT}-dev"
        (self.clone / "impl" / "orch").mkdir(parents=True)
        self.standing().write_text(STANDING, encoding="utf-8")
        for part in ("prompts", "reviews"):
            (self.home / "ws" / part / PROJECT).mkdir(parents=True)
        self.reviews = self.home / "ws" / "reviews" / PROJECT
        conf = self.home / ".config" / "jig" / f"{PROJECT}.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(TEMPLATE.read_text(encoding="utf-8").replace("<project>", PROJECT)
                        .replace("JIG_VERSION=<version>\n", ""), encoding="utf-8")
        self.addCleanup(self.end_all)

    def script(self, name, text):
        path = self.bin_dir / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
        return path

    def standing(self):
        return self.clone / "impl" / "orch" / "ORCHESTRATOR.md"

    def base_env(self):
        return self.env(PATH=f"{self.bin_dir}:{os.environ['PATH']}", JIG_SRV=str(self.srv),
                        JIG_ORCH_POLL=POLL,
                        JIG_ORCH_REPORT_POLL=getattr(self, "report_poll", REPORT_POLL),
                        JIG_ORCH_HOURLY=getattr(self, "hourly", "3600"),
                        JIG_ORCH_FORK_WINDOW=getattr(self, "fork_window", "60"))

    def orch(self, *args):
        return run([ORCH, PROJECT, *args], env=self.base_env())

    def tmux(self, *args):
        return run(["tmux", *args], env=self.base_env())

    def end_all(self):
        run([ORCH, PROJECT, "stop"], env=self.base_env())
        pid = self.loop_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        self.tmux("kill-server")
        # tmux can leave the socket file of a server that has ended.
        socket = pathlib.Path(f"/tmp/tmux-{os.getuid()}") / self.socket
        if socket.is_socket():
            socket.unlink()

    def loop_pid(self):
        try:
            return int((self.reviews / "orch" / "loop.lock").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def until(self, test, seconds=20.0, what="the condition"):
        deadline = time.monotonic() + seconds
        while not test():
            if time.monotonic() >= deadline:
                self.fail(f"{what} did not come within {seconds} s; log:\n{self.log()}")
            time.sleep(0.1)

    def read(self, path):
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def log(self):
        return self.read(self.reviews / "orch" / "supervisor.log")

    def tee(self):
        return self.read(self.reviews / "out" / "orch.txt")

    def calls(self):
        return [json.loads(line) for line in self.read(self.home / "claude-calls.jsonl")
                .splitlines()]

    def submitted(self):
        return self.read(self.home / "claude-submitted").splitlines()

    def event_file(self):
        return self.reviews / "orch" / "events.log"

    def event_lines(self):
        """Return every line of the event file, the start lines with them."""
        return self.read(self.event_file()).splitlines()

    def events(self):
        """Return the events in the event file, without the start lines."""
        return [line for line in self.event_lines() if not line.startswith("start ")]

    def keys_sent(self):
        return self.read(self.home / "tmux-send-keys").splitlines()

    def session_ids(self):
        """Return the Claude Code session ID of each start of the double, oldest first."""
        return [call["argv"][call["argv"].index("--session-id") + 1] for call in self.calls()]

    def screen(self, session=SESSION):
        return self.tmux("capture-pane", "-p", "-t", f"={session}:").stdout

    def alive(self, session=SESSION):
        """Say whether the session exists with a live pane, as the loop tells it."""
        result = self.tmux("display-message", "-p", "-t", f"={session}:", "#{pane_dead}")
        return result.returncode == 0 and result.stdout.strip() == "0"

    def ready(self, session=SESSION):
        return "auto mode on" in self.screen(session)

    def pane(self):
        """Return the session's pane as tmux shows it, or None when the session is gone."""
        result = self.tmux("display-message", "-p", "-t", f"={SESSION}:",
                           "#{session_id}\t#{pane_dead}\t#{pane_pid}\t#{pane_start_command}")
        if result.returncode != 0:
            return None
        tmux_id, dead, pid, command = result.stdout.rstrip("\n").split("\t", 3)
        return {"id": tmux_id, "dead": dead == "1", "pid": int(pid), "command": command}

    def recorded_ids(self):
        return self.read(self.reviews / "orch" / "session-ids")

    def new_sessions(self):
        """Count the calls of tmux new-session; a respawn in the same pane adds none."""
        return self.read(self.home / "events-at-new-session").count("--\n")

    def end_claude(self):
        """Make Claude Code in the session's pane exit, as at a rollover."""
        os.kill(self.pane()["pid"], signal.SIGTERM)
        self.until(lambda: self.pane()["dead"] or len(self.calls()) > 1,
                   what="the end of Claude Code")

    def respawn_as_a_person(self, command):
        """Run another command in the session's pane, as a person can with tmux."""
        result = self.tmux("respawn-pane", "-k", "-t", f"={SESSION}:", command)
        self.assertEqual(result.returncode, 0, result.stderr)

    def inbox_line(self, path):
        """Return the line the loop appends for the inbox file as it is now."""
        data = path.read_bytes()
        return f"inbox: {path} ({len(data)}, sha256 {hashlib.sha256(data).hexdigest()[:8]})"

    def start(self):
        result = self.orch("start")
        self.assertEqual(result.returncode, 0, result.stderr + self.log())
        self.until(self.ready, what="the session's input bar")
        return result


class TestStart(OrchTestCase):
    def test_start_refuses_without_orchestrator_md(self):
        self.standing().unlink()
        result = self.orch("start")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ORCHESTRATOR.md", result.stderr)
        self.assertFalse(self.alive())
        self.assertEqual(self.calls(), [])

    def test_start_refuses_when_a_loop_runs(self):
        self.start()
        pid = self.loop_pid()
        result = self.orch("start")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"a loop runs already, pid {pid}", result.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_start_refuses_a_session_it_did_not_start(self):
        self.tmux("new-session", "-d", "-s", SESSION, "sleep 60")
        result = self.orch("start")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"tmux session {SESSION} is not the loop's own", result.stderr)
        self.assertEqual(self.orch("stop").returncode, 0)
        self.assertTrue(self.alive(), "stop ended a session that no loop started")

    def test_start_runs_the_session_with_the_first_message_and_its_options(self):
        self.start()
        call = self.calls()[0]
        argv = call["argv"]
        self.assertEqual(call["cwd"], str(self.clone))
        self.assertEqual(argv[-1], STANDING.rstrip("\n"))
        for option, value in (("--remote-control", SESSION), ("--name", SESSION),
                              ("--model", "claude-opus-5-5"), ("--effort", "high")):
            self.assertEqual(argv[argv.index(option) + 1], value, option)
        added = [argv[index + 1] for index, word in enumerate(argv) if word == "--add-dir"]
        self.assertEqual(added, [str(self.home / "ws" / "prompts" / PROJECT), str(self.reviews),
                                 str(self.srv / "in" / PROJECT), str(self.srv / "out" / PROJECT),
                                 str(self.srv / "blocks" / PROJECT)])
        settings = json.loads((self.clone / ".claude" / "settings.local.json").read_text())
        self.assertEqual(settings["effortLevel"], "high")
        self.assertEqual(settings["modelSettings"]["claude-opus-5-5"]["effortLevel"], "high")
        # The clone had no local settings; the mode is the one whose bar jig reads.
        self.assertEqual(settings["permissions"], {"defaultMode": "auto"})
        status = self.orch("status")
        self.assertEqual(status.returncode, 0, status.stdout)
        self.assertIn(f"session {SESSION}: pane live, its own", status.stdout)

    def test_start_keeps_the_mode_that_the_local_settings_name(self):
        # The clone's local settings already name the mode acceptEdits, a low effort and
        # a rule of their own. After the start the mode and the rule stay, and the
        # effort keys hold the supervisor's effort for its model.
        path = self.clone / ".claude" / "settings.local.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"effortLevel": "low", "permissions": {
            "defaultMode": "acceptEdits", "allow": ["Bash(ls)"]}}), encoding="utf-8")
        self.start()
        settings = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(settings["permissions"],
                         {"defaultMode": "acceptEdits", "allow": ["Bash(ls)"]})
        self.assertEqual(settings["effortLevel"], "high")
        self.assertEqual(settings["modelSettings"], {"claude-opus-5-5": {"effortLevel": "high"}})

    def test_stop_leaves_the_session_with_a_dead_pane_and_the_next_start_respawns_it(self):
        # Stop ends the loop and Claude Code, but the tmux session stays with its pane
        # dead, so that an attached client stays. The next start runs a new Claude Code
        # in that same pane, and records the same tmux ID with the new Claude Code ID.
        self.start()
        before = self.pane()
        result = self.orch("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"session {SESSION} stays with its dead pane", result.stdout)
        after = self.pane()
        self.assertEqual((after["id"], after["dead"]), (before["id"], True))
        status = self.orch("status")
        self.assertEqual(status.returncode, 3, status.stdout)
        self.assertIn("loop stopped by jig orch stop", status.stdout)
        transcripts = list((self.reviews / "transcripts" / "orch").glob("*/*.jsonl"))
        self.assertEqual(len(transcripts), 1)
        self.start()
        first, second = self.session_ids()
        again = self.pane()
        self.assertEqual((again["id"], again["dead"]), (before["id"], False))
        self.assertIn(f"--session-id {second}", again["command"])
        self.assertEqual(self.recorded_ids(), f"{before['id']} {second}\n")
        self.assertEqual(self.new_sessions(), 1)


    def test_start_makes_the_directory_of_the_loop_when_it_is_missing(self):
        # The account has no review directory for the project yet. The start makes it,
        # with orch/ inside, and the event file there gets the session's start line.
        shutil.rmtree(self.reviews)
        self.start()
        self.assertTrue((self.reviews / "orch").is_dir())
        self.assertEqual(self.event_lines(), [f"start {self.session_ids()[0]}"])


class TestAnnouncements(OrchTestCase):
    def test_new_inbox_file_is_announced_within_a_minute_with_bytes_and_hash(self):
        old = self.srv / "in" / PROJECT / "old.md"
        old.write_text("there before the loop\n", encoding="utf-8")
        self.start()
        new = self.srv / "in" / PROJECT / "review.md"
        data = b"a review for the orchestrator\n" * 7
        new.write_bytes(data)
        line = f"inbox: {new} ({len(data)}, sha256 {hashlib.sha256(data).hexdigest()[:8]})"
        began = time.monotonic()
        self.until(lambda: line in self.events(), seconds=60, what="the inbox line")
        self.assertLess(time.monotonic() - began, 60)
        time.sleep(4 * float(POLL))
        self.assertEqual(self.events(), [line], "old.md was announced, or a line twice")

    def test_new_readback_is_announced(self):
        self.start()
        (self.srv / "out" / PROJECT / "b7k2.txt").write_text("exit 0\n", encoding="utf-8")
        self.until(lambda: "readback: b7k2" in self.events(), seconds=60,
                   what="the readback line")

    def test_file_that_lands_while_no_loop_runs_is_announced_at_the_next_start(self):
        self.start()
        first = self.srv / "out" / PROJECT / "b1.txt"
        first.write_text("exit 0\n", encoding="utf-8")
        self.until(lambda: "readback: b1" in self.events(), seconds=60,
                   what="the first readback line")
        self.assertEqual(self.orch("stop").returncode, 0, self.log())
        self.until(lambda: not self.alive(), what="the end of the session")
        (self.srv / "out" / PROJECT / "b2.txt").write_text("exit 0\n", encoding="utf-8")
        self.start()
        self.until(lambda: "readback: b2" in self.events(), seconds=60,
                   what="the readback that came while no loop ran")
        time.sleep(4 * float(POLL))
        lines = [line for line in self.events() if line.startswith("readback:")]
        self.assertEqual(lines, ["readback: b1", "readback: b2"])

    def test_readback_of_a_block_that_pauses_is_announced_once_when_complete(self):
        self.start()
        block = self.srv / "blocks" / PROJECT / "b9.sh"
        # The pause is ten polls: a readback that held still for one would be announced.
        block.write_text("echo start\nsleep 3\necho end\n", encoding="utf-8")
        digest = hashlib.sha256(block.read_bytes()).hexdigest()[:8]
        running = subprocess.Popen([str(ROOT / "libexec" / "jig-run"), PROJECT, "b9", digest],
                                   env=self.base_env(), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        self.addCleanup(running.kill)
        while running.poll() is None:
            self.assertNotIn("readback: b9", self.events(), "announced while the block ran")
            time.sleep(0.1)
        self.assertEqual(running.returncode, 0)
        self.until(lambda: "readback: b9" in self.events(), seconds=60,
                   what="the readback line")
        time.sleep(4 * float(POLL))
        lines = [line for line in self.events() if line.startswith("readback:")]
        self.assertEqual(lines, ["readback: b9"])
        readback = self.srv / "out" / PROJECT / "b9.txt"
        self.assertEqual(readback.read_text(encoding="utf-8"), "start\nend\n")
        self.assertFalse((self.srv / "out" / PROJECT / ".b9.txt").exists())

    def test_file_still_being_written_is_announced_once_when_it_holds_still(self):
        self.start()
        path = self.srv / "in" / PROJECT / "growing.log"
        with open(path, "wb") as stream:
            for _ in range(10):
                stream.write(b"more\n")
                stream.flush()
                time.sleep(float(POLL) * 0.6)
        self.until(lambda: any("growing.log" in line for line in self.events()),
                   seconds=60, what="the line for growing.log")
        time.sleep(4 * float(POLL))
        lines = [line for line in self.events() if "growing.log" in line]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("(50, sha256 ", lines[0])


    def test_readback_written_again_gets_a_second_line_and_after_removal_a_third(self):
        # A block run twice writes its readback twice at one path; each run is a result
        # the orchestrator must read, so each gets its own line.
        self.start()
        readback = self.srv / "out" / PROJECT / "b1.txt"
        readback.write_text("exit 1\n", encoding="utf-8")
        count = lambda: self.events().count("readback: b1")  # noqa: E731
        self.until(lambda: count() == 1, seconds=30, what="the first readback line")
        first = readback.stat()
        # Written again as jig run writes it, to a hidden file renamed over the old one.
        # The modification time is set back, so only the inode tells the two apart.
        hidden = readback.with_name(".b1.txt")
        hidden.write_text("exit 0\n", encoding="utf-8")
        os.utime(hidden, ns=(first.st_atime_ns, first.st_mtime_ns))
        os.replace(hidden, readback)
        self.until(lambda: count() == 2, seconds=30, what="the second readback line")
        # Removed, and once the loop has seen it gone, written again with the old
        # modification time: it is new even if it gets the old inode back.
        second = readback.stat()
        readback.unlink()
        time.sleep(4 * float(POLL))
        readback.write_text("exit 0\n", encoding="utf-8")
        os.utime(readback, ns=(second.st_atime_ns, second.st_mtime_ns))
        self.until(lambda: count() == 3, seconds=30, what="the third readback line")
        time.sleep(4 * float(POLL))
        self.assertEqual(count(), 3)

    def test_inbox_file_that_grew_gets_a_second_line(self):
        # A file that grows after its line was appended is announced again with its new size
        # and hash, so the orchestrator reads the part that came late.
        self.start()
        path = self.srv / "in" / PROJECT / "note.md"
        path.write_bytes(b"first part\n")
        first = self.inbox_line(path)
        self.until(lambda: first in self.events(), seconds=30, what="the first line")
        with open(path, "ab") as stream:
            stream.write(b"second part\n")
        second = self.inbox_line(path)
        self.until(lambda: second in self.events(), seconds=30, what="the second line")
        time.sleep(4 * float(POLL))
        self.assertEqual([line for line in self.events() if "note.md" in line],
                         [first, second])

    def test_old_record_of_paths_announces_nothing_again(self):
        # A record from before inodes were kept lists paths alone. The files at those
        # paths count as announced, so the first start after the upgrade appends only the
        # line of a file that is really new, and the record is rewritten in the new form.
        inbox = self.srv / "in" / PROJECT / "old.md"
        inbox.write_text("announced before the upgrade\n", encoding="utf-8")
        readback = self.srv / "out" / PROJECT / "b1.txt"
        readback.write_text("exit 0\n", encoding="utf-8")
        record = self.reviews / "orch" / "announced"
        record.parent.mkdir(parents=True)
        record.write_text(f"{inbox}\n{readback}\n", encoding="utf-8")
        self.start()
        new = self.srv / "in" / PROJECT / "new.md"
        new.write_bytes(b"new\n")
        line = self.inbox_line(new)
        # Had old.md or b1.txt counted as new, its line would have come first.
        self.until(lambda: line in self.events(), seconds=30, what="the line for new.md")
        self.assertEqual(self.events(), [line])
        lines = record.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3, lines)
        for entry in lines:
            self.assertRegex(entry, r"^\d+ \d+ /.+$")


class TestDelivery(OrchTestCase):
    """The delivery of one line, deliver() in lib/jig_tmux.py, which jig send uses too."""

    def deliver(self, session, text):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import jig_tmux; "
                "failure = jig_tmux.deliver(sys.argv[2], sys.argv[3]); "
                "print(failure or 'delivered'); sys.exit(1 if failure else 0)")
        return run(["python3", "-c", code, LIB, session, text], env=self.base_env())

    def open_double(self, session="demo-send"):
        created = self.tmux("new-session", "-d", "-s", session, "-x", "200", "-y", "50",
                            str(self.bin_dir / "claude"))
        self.assertEqual(created.returncode, 0, created.stderr)
        self.until(lambda: self.ready(session), what="the double's input bar")
        return session

    def test_line_of_500_characters_reaches_the_session_as_submitted(self):
        session = self.open_double()
        line = "".join("abcdefghij klmnopqrs"[index % 20] for index in range(498)) + "<>"
        self.assertEqual(len(line), 500)
        result = self.deliver(session, line)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.submitted(), [line])

    def test_text_left_in_the_input_box_is_not_counted_as_delivered(self):
        session = self.open_double()
        (self.home / "claude-ignore-enter").write_text("", encoding="utf-8")
        result = self.deliver(session, "inbox: stays in the box")
        self.assertEqual(result.returncode, 1)
        self.assertIn("stayed in the input box", result.stdout)
        self.assertEqual(self.submitted(), [])

    def test_nothing_is_typed_into_a_dialog(self):
        (self.home / "claude-dialog").write_text("", encoding="utf-8")
        created = self.tmux("new-session", "-d", "-s", "demo-send", "-x", "200", "-y", "50",
                            str(self.bin_dir / "claude"))
        self.assertEqual(created.returncode, 0, created.stderr)
        self.until(lambda: "Do you want to proceed?" in self.screen("demo-send"))
        result = self.deliver("demo-send", "readback: b7k2")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no input bar", result.stdout)
        self.assertFalse((self.home / "claude-answered").exists())


class FakeSession:
    """tmux and a Claude Code screen for deliver(), with no tmux and no timing.

    Typed text goes into the box. After each Enter the next captures return the
    screens in after_enter, one each, as a redraw would; after them the screen
    shows the box again, with the text kept when keeps_text is set, or a dialog
    for good when dialog_after_enter is set.
    """

    RULE = "─" * 60
    BAR = "  ⏵⏵ auto mode on (shift+tab to cycle)"

    def __init__(self, after_enter=(), keeps_text=False, dialog_after_enter=False):
        self.after_enter = after_enter
        self.keeps_text = keeps_text
        self.dialog_after_enter = dialog_after_enter
        self.box = ""
        self.enters = 0
        self.pending = []

    def tmux(self, *args):
        if "-l" in args:
            self.box += args[-1]
        elif args[-1] == "Enter":
            self.enters += 1
            self.pending = list(self.after_enter)
            if not self.keeps_text:
                self.box = ""
        return subprocess.CompletedProcess(args, 0, "", "")

    def capture(self, session):
        if self.pending:
            return self.pending.pop(0)
        if self.enters and self.dialog_after_enter:
            return DIALOG_SCREEN
        return "\n".join(["● ready", "", self.RULE, "> " + self.box, self.RULE,
                          self.BAR]) + "\n"

    def deliver(self, text):
        with mock.patch.object(jig_tmux, "tmux", self.tmux), \
                mock.patch.object(jig_tmux, "capture", self.capture):
            return jig_tmux.deliver("demo-send", text, pause=0, check=0.2)


class TestDeliveryScreens(unittest.TestCase):
    """deliver() reads the input box only from screens that show the input bar."""

    def test_blank_capture_after_enter_does_not_count_as_delivered(self):
        # The text stays in the box, but the first capture after each Enter is blank,
        # as one taken in the middle of a redraw can be. The blank screen must not
        # count as an empty box: deliver() reports the text as stuck.
        session = FakeSession(after_enter=[""], keeps_text=True)
        failure = session.deliver("inbox: stays in the box")
        self.assertEqual(failure, "the text stayed in the input box after Enter")
        self.assertEqual(session.enters, jig_tmux.ENTER_TRIES)

    def test_half_drawn_screen_after_enter_does_not_count_as_delivered(self):
        # A capture after Enter has the history but not yet the box and the input bar.
        # It must not count as an empty box either.
        session = FakeSession(after_enter=["● ready\n"], keeps_text=True)
        self.assertEqual(session.deliver("inbox: stays in the box"),
                         "the text stayed in the input box after Enter")

    def test_text_that_leaves_the_box_after_a_redraw_is_delivered(self):
        # The text is submitted, and two blank captures come before the screen with
        # the empty box. deliver() waits through them and reports success after one
        # Enter.
        session = FakeSession(after_enter=["", ""])
        self.assertIsNone(session.deliver("inbox: submitted"))
        self.assertEqual(session.enters, 1)

    def test_dialog_after_enter_gets_no_second_enter(self):
        # The text is submitted and a dialog comes up at once. The dialog has no input
        # box, so deliver() cannot see the text leave; it must fail without pressing
        # Enter again, because the dialog would take that Enter as its answer.
        session = FakeSession(dialog_after_enter=True)
        self.assertEqual(session.deliver("inbox: submitted"),
                         "session demo-send shows no input bar after Enter")
        self.assertEqual(session.enters, 1)


class TestRestart(OrchTestCase):
    def test_killed_session_is_restarted_and_its_transcript_copied(self):
        self.start()
        first = self.calls()[0]["argv"]
        first_id = first[first.index("--session-id") + 1]
        self.assertEqual(self.tmux("kill-session", "-t", f"={SESSION}").returncode, 0)
        self.until(lambda: len(self.calls()) == 2, what="the restart")
        self.until(self.ready, what="the restarted session's input bar")
        copied = self.reviews / "transcripts" / "orch"
        self.assertEqual([path.name for path in copied.glob("*/*.jsonl")], [f"{first_id}.jsonl"])
        second = self.calls()[1]["argv"]
        self.assertNotEqual(second[second.index("--session-id") + 1], first_id)
        self.assertEqual(second[-1], STANDING.rstrip("\n"))
        self.assertEqual(self.orch("status").returncode, 0)

    def test_3_quick_exits_stop_the_loop(self):
        (self.home / "claude-exit-now").write_text("", encoding="utf-8")
        self.orch("start")
        self.until(lambda: self.orch("status").returncode == 3, seconds=30,
                   what="the end of the loop")
        self.assertEqual(len(self.calls()), 3)
        self.assertIn(f"stopped: session {SESSION} exited 3 times in 10 minutes", self.log())
        self.assertIn(f"stopped: session {SESSION} exited 3 times in 10 minutes", self.tee())
        time.sleep(4 * float(POLL))
        self.assertEqual(len(self.calls()), 3)
        # Each Claude Code exited at once, and still the session stayed: made once,
        # respawned twice, and left with its dead pane for the next start.
        pane = self.pane()
        self.assertIsNotNone(pane, "the loop left no session")
        self.assertTrue(pane["dead"])
        self.assertEqual(self.recorded_ids(), f"{pane['id']} {self.session_ids()[2]}\n")
        self.assertEqual(self.new_sessions(), 1)


class TestSessionPane(OrchTestCase):
    """The session outlives each Claude Code in it, and the loop touches only its own."""

    def test_session_that_exits_is_respawned_in_the_same_pane(self):
        # Claude Code exits while the loop runs. The loop starts the next one in the
        # same pane of the same tmux session, with a new Claude Code ID in the pane's
        # command, a second start line in the event file, the record of both IDs, and
        # the tee still fed by the pane.
        self.start()
        before = self.pane()
        self.end_claude()
        self.until(lambda: len(self.calls()) == 2, what="the respawn")
        self.until(self.ready, what="the respawned session's input bar")
        first, second = self.session_ids()
        after = self.pane()
        self.assertEqual((after["id"], after["dead"]), (before["id"], False))
        self.assertIn(f"--session-id {second}", after["command"])
        self.assertNotIn(first, after["command"])
        self.assertEqual(self.event_lines(), [f"start {first}", f"start {second}"])
        self.assertEqual(self.recorded_ids(), f"{before['id']} {second}\n")
        self.assertEqual(self.new_sessions(), 1)
        size = len(self.tee())
        self.until(lambda: len(self.tee()) > size, seconds=5,
                   what="new output of the respawned pane in the tee")

    def test_attached_client_stays_across_the_respawn_and_the_stop(self):
        # A tmux client in control mode, attached as a person would be, is still listed
        # by tmux after Claude Code exits and the loop respawns the pane, and after
        # jig orch stop.
        self.start()
        output = open(self.tmp / "control-client.out", "wb")
        self.addCleanup(output.close)
        client = subprocess.Popen([str(self.bin_dir / "tmux"), "-C", "attach", "-t",
                                   f"={SESSION}"], stdin=subprocess.PIPE, stdout=output,
                                  stderr=subprocess.STDOUT, env=self.base_env())

        def detach():
            client.stdin.close()
            try:
                client.wait(5)
            except subprocess.TimeoutExpired:
                client.kill()
                client.wait()

        self.addCleanup(detach)

        def attached():
            listed = self.tmux("list-clients", "-t", f"={SESSION}", "-F", "#{client_pid}")
            return listed.stdout.split() == [str(client.pid)]

        self.until(attached, what="the attached client")
        self.end_claude()
        self.until(lambda: len(self.calls()) == 2, what="the respawn")
        self.until(self.ready, what="the respawned session's input bar")
        self.assertTrue(attached(), "the client left at the respawn")
        self.assertEqual(self.orch("stop").returncode, 0)
        self.assertTrue(self.pane()["dead"])
        self.assertTrue(attached(), "the client left at the stop")
        self.assertIsNone(client.poll())

    def test_start_refuses_and_stop_leaves_a_session_that_is_not_its_own(self):
        # After a stop, two sessions under the loop's name that are not its own, each
        # with a dead pane: the loop's session after a person ran a command with another
        # Claude Code ID in its pane, then a new session of a person whose command names
        # the recorded Claude Code ID. Start refuses each with a line that names it and
        # starts no loop, and stop leaves the pane as it was.
        self.start()
        self.assertEqual(self.orch("stop").returncode, 0)
        for case in ("other Claude Code ID", "other tmux ID"):
            with self.subTest(case):
                if case == "other Claude Code ID":
                    self.respawn_as_a_person(f"true --session-id {uuid.uuid4()}")
                else:
                    recorded = self.recorded_ids().split()[1]
                    # A second session keeps the server up; a new server would number
                    # its sessions from the start and give the loop's tmux ID again.
                    self.tmux("new-session", "-d", "-s", "keeper", "sleep 60")
                    self.tmux("kill-session", "-t", f"={SESSION}")
                    self.tmux("new-session", "-d", "-s", SESSION,
                              f"sleep 0.5; true --session-id {recorded}")
                    self.tmux("set-option", "-w", "-t", f"={SESSION}:", "remain-on-exit", "on")
                self.until(lambda: self.pane()["dead"], what="the end of the person's command")
                before = self.pane()
                result = self.orch("start")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f"tmux session {SESSION} is not the loop's own", result.stderr)
                self.assertEqual(self.orch("status").returncode, 3)
                stopped = self.orch("stop")
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                self.assertIn("left it as it is", stopped.stdout)
                self.assertEqual(self.pane(), before)
                self.assertEqual(len(self.calls()), 1)

    def test_loop_leaves_a_session_that_is_not_its_own(self):
        # While the loop runs, a person runs another command in the pane. With that
        # command live, stop ends the loop and leaves it running. Started again, the
        # loop sees the person's command exit, respawns nothing, logs why and stops.
        self.start()
        other_id = str(uuid.uuid4())
        self.respawn_as_a_person(f"bash -c 'sleep 60' --session-id {other_id}")
        person = self.pane()
        stopped = self.orch("stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertIn("left it as it is", stopped.stdout)
        self.assertEqual(self.pane(), person)
        self.assertIn(f"tmux session {SESSION} is not the loop's own", self.log())
        # The loop's own session again, and then a command of the person that exits.
        self.tmux("kill-session", "-t", f"={SESSION}")
        self.start()
        calls = len(self.calls())
        self.respawn_as_a_person(f"sleep 0.5; true --session-id {other_id}")
        self.until(lambda: self.orch("status").returncode == 3, seconds=10,
                   what="the end of the loop")
        self.assertIn(f"stopped: tmux session {SESSION} is not the loop's own", self.log())
        self.assertEqual(len(self.calls()), calls)
        pane = self.pane()
        self.assertTrue(pane["dead"])
        self.assertIn(other_id, pane["command"])

    def test_status_prints_the_pane_and_whether_the_session_is_its_own(self):
        # Status names the state of the pane and the result of the own-session test,
        # with a loop and without one, and keeps its exit codes: 0 with a loop, 3
        # without. Along the way: no session, the loop's live session, the same with
        # its loop killed, after stop, and with a person's command in the pane.
        status = self.orch("status")
        self.assertEqual(status.returncode, 3)
        self.assertIn(f"session {SESSION}: pane none, not its own", status.stdout)
        self.start()
        status = self.orch("status")
        self.assertEqual(status.returncode, 0)
        self.assertIn(f"session {SESSION}: pane live, its own", status.stdout)
        # A loop that dies leaves its Claude Code running: start refuses that live
        # pane, and stop ends Claude Code itself.
        os.kill(self.loop_pid(), signal.SIGKILL)
        self.until(lambda: self.orch("status").returncode == 3, what="the end of the loop")
        status = self.orch("status")
        self.assertIn(f"session {SESSION}: pane live, its own", status.stdout)
        refused = self.orch("start")
        self.assertEqual(refused.returncode, 1)
        self.assertIn(f"tmux session {SESSION} exists with a live pane, and no loop runs",
                      refused.stderr)
        self.assertEqual(self.orch("stop").returncode, 0)
        status = self.orch("status")
        self.assertEqual(status.returncode, 3)
        self.assertIn(f"session {SESSION}: pane dead, its own", status.stdout)
        self.respawn_as_a_person(f"bash -c 'sleep 60' --session-id {uuid.uuid4()}")
        status = self.orch("status")
        self.assertEqual(status.returncode, 3)
        self.assertIn(f"session {SESSION}: pane live, not its own", status.stdout)


class TestEventFile(OrchTestCase):
    def test_start_line_goes_into_the_event_file_before_the_session_starts(self):
        # Each start of a session, the first and the one after the session was killed,
        # appends "start <ID>" with the ID that the loop gives Claude Code. Each line is
        # in the file already when the tmux session is made.
        self.start()
        self.assertEqual(self.tmux("kill-session", "-t", f"={SESSION}").returncode, 0)
        self.until(lambda: len(self.calls()) == 2, what="the restart")
        self.until(self.ready, what="the restarted session's input bar")
        first, second = self.session_ids()
        self.assertEqual(self.event_lines(), [f"start {first}", f"start {second}"])
        made = self.read(self.home / "events-at-new-session").split("--\n")
        self.assertEqual(made[:2], [f"start {first}\n", f"start {first}\nstart {second}\n"])

    def test_restart_leaves_the_old_events_between_the_last_two_start_lines(self):
        # An inbox file lands while the first session runs, the session is killed and
        # started again, and a readback lands after that. The once-read of the watcher
        # gives the inbox line, the events of the old session, and the follow read
        # starts with the readback line.
        self.start()
        path = self.srv / "in" / PROJECT / "note.md"
        path.write_text("hello\n", encoding="utf-8")
        inbox = self.inbox_line(path)
        self.until(lambda: self.events() == [inbox], what="the inbox line")
        self.assertEqual(self.tmux("kill-session", "-t", f"={SESSION}").returncode, 0)
        self.until(lambda: len(self.calls()) == 2, what="the restart")
        (self.srv / "out" / PROJECT / "b1.txt").write_text("exit 0\n", encoding="utf-8")
        self.until(lambda: "readback: b1" in self.events(), what="the readback line")
        first, second = self.session_ids()
        self.assertEqual(self.event_lines(),
                         [f"start {first}", inbox, f"start {second}", "readback: b1"])
        old = self.orch("events", "--old")
        self.assertEqual((old.returncode, old.stdout), (0, f"{inbox}\n"))
        watcher = start_watcher(self, self.base_env())
        self.assertEqual(read_line(watcher.stdout, 5), b"readback: b1\n")

    def test_no_event_is_typed_into_the_session(self):
        # An inbox file and a readback land while the session waits at an empty input
        # box. Both lines go into the event file, and tmux gets no send-keys call, so
        # the box stays empty and the session submits nothing.
        self.start()
        path = self.srv / "in" / PROJECT / "note.md"
        path.write_text("hello\n", encoding="utf-8")
        (self.srv / "out" / PROJECT / "b1.txt").write_text("exit 0\n", encoding="utf-8")
        self.until(lambda: len(self.events()) == 2, what="both lines")
        time.sleep(6 * float(POLL))
        self.assertEqual(sorted(self.events()), sorted([self.inbox_line(path), "readback: b1"]))
        self.assertEqual(self.keys_sent(), [])
        self.assertEqual(self.submitted(), [])
        # The double draws the box between two rules; an empty box holds only its mark.
        self.assertRegex(self.screen(), f"{'─' * 60}\n> ?\n{'─' * 60}")

    def test_append_that_fails_is_logged_once_and_tried_again_in_order(self):
        # The event file cannot be written: a directory stands at its path. Two inbox
        # files land; the loop logs the failure once, to the tee and its log, and holds
        # both lines. Once the directory is gone, both lines arrive, in the order the
        # files came, each once.
        self.start()
        self.event_file().unlink()
        self.event_file().mkdir()
        first = self.srv / "in" / PROJECT / "first.md"
        first.write_text("one\n", encoding="utf-8")
        self.until(lambda: "cannot append" in self.log(), what="the failure")
        second = self.srv / "in" / PROJECT / "second.md"
        second.write_text("two\n", encoding="utf-8")
        time.sleep(6 * float(POLL))
        self.assertEqual(self.log().count("cannot append"), 1, self.log())
        self.assertIn("cannot append", self.tee())
        self.event_file().rmdir()
        lines = [self.inbox_line(first), self.inbox_line(second)]
        self.until(lambda: self.events() == lines, what="both lines after the failure")
        time.sleep(4 * float(POLL))
        self.assertEqual(self.event_lines(), lines)

    def test_file_whose_line_a_stop_drops_is_announced_once_at_the_next_start(self):
        # The event file cannot be written, so the lines of an inbox file and a
        # readback wait in the queue, and the loop stops before it can append them.
        # Neither file is in the written record then, so the next start appends the
        # line of each, once and in the order the files came.
        self.start()
        self.event_file().unlink()
        self.event_file().mkdir()
        note = self.srv / "in" / PROJECT / "note.md"
        note.write_text("one\n", encoding="utf-8")
        self.until(lambda: "cannot append" in self.log(), what="the failure")
        (self.srv / "out" / PROJECT / "b1.txt").write_text("exit 0\n", encoding="utf-8")
        time.sleep(4 * float(POLL))
        self.assertEqual(self.orch("stop").returncode, 0, self.log())
        self.until(lambda: not self.alive(), what="the end of the session")
        self.assertNotIn(str(note), self.read(self.reviews / "orch" / "announced"))
        self.event_file().rmdir()
        self.start()
        lines = [self.inbox_line(note), "readback: b1"]
        self.until(lambda: self.events() == lines, seconds=60,
                   what="both lines after the restart")
        time.sleep(4 * float(POLL))
        self.assertEqual(self.events(), lines)


# A process that only waits; the arguments after the code are what the loop reads.
WAITER = [sys.executable, "-c", "import time; time.sleep(120)"]


class ForkTestCase(OrchTestCase):
    """Helpers for the tests of the processes that carry on a session that exited."""

    def process(self, *arguments):
        """Start a waiting process with these arguments; the cleanup ends it."""
        child = subprocess.Popen([*WAITER, *arguments], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.end_process, child)
        return child

    def end_process(self, child):
        if child.poll() is None:
            child.kill()
        child.wait()

    def first_id(self):
        argv = self.calls()[0]["argv"]
        return argv[argv.index("--session-id") + 1]

    def restart(self):
        self.assertEqual(self.tmux("kill-session", "-t", f"={SESSION}").returncode, 0)
        self.until(lambda: len(self.calls()) == 2, what="the restart")
        self.until(self.ready, what="the restarted session's input bar")

    def assert_ended(self, child, old):
        self.until(lambda: child.poll() is not None, seconds=10.0,
                   what=f"the end of pid {child.pid}")
        self.assertEqual(child.returncode, -signal.SIGTERM)
        self.assertIn(f"sent SIGTERM to pid {child.pid}, a fork of the old Claude Code "
                      f"session {old}", self.log())


class TestForks(ForkTestCase):
    """The loop ends each process that carries on the session that exited, and no other."""

    # The window closes a second after the restart, so that a test can wait it out.
    fork_window = "1"

    def test_fork_that_resumes_the_old_transcript_ends_when_the_session_exits(self):
        # A fork as Claude Code starts it, with the path of the old transcript, runs
        # when the session exits; the loop ends it and logs its pid with the old ID.
        self.start()
        old = self.first_id()
        fork = self.process("--fork-session", "--resume",
                            str(self.home / ".claude" / "projects" / "x" / f"{old}.jsonl"))
        self.restart()
        self.assert_ended(fork, old)

    def test_process_that_resumes_the_bare_old_id_ends(self):
        self.start()
        old = self.first_id()
        fork = self.process("--resume", old)
        self.restart()
        self.assert_ended(fork, old)

    def test_other_resumes_and_plain_processes_still_run_after_the_window(self):
        # Beside a fork of the old session, which shows that the loop looked, run a
        # process that resumes another session and one that names the old ID without
        # --resume; both still run after the window has closed, and the log names
        # neither.
        self.start()
        old = self.first_id()
        fork = self.process("--resume", old)
        # Session IDs here are joined from their groups so that jig privacy scan,
        # which reads test files before a release, finds no whole UUID in them.
        other_id = "-".join(["0b9e7c52", "6a40", "4d0f", "9a43", "3c1f5f6f0e11"])
        other = self.process("--fork-session", "--resume", f"/tmp/x/{other_id}.jsonl")
        plain = self.process("--session-id", old, f"{old}.jsonl")
        self.restart()
        self.assert_ended(fork, old)
        time.sleep(float(self.fork_window) + 4 * float(POLL))
        self.assertIsNone(other.poll())
        self.assertIsNone(plain.poll())
        for child in (other, plain):
            self.assertNotIn(f"pid {child.pid},", self.log())


class TestForkWithinTheWindow(ForkTestCase):
    fork_window = "30"

    def test_fork_that_starts_after_the_new_session_within_the_window_ends(self):
        # The fork shows up only once the new session runs; the loop still looks for
        # it, and ends it.
        self.start()
        old = self.first_id()
        self.restart()
        fork = self.process("--fork-session", "--resume", f"/tmp/x/{old}.jsonl")
        self.assert_ended(fork, old)


class TestResumes(unittest.TestCase):
    """Which command lines the loop takes as a fork of a session."""

    def test_resume_needs_the_id_or_a_path_that_ends_in_it(self):
        resumes = load_orch().resumes
        # Joined from its groups so that jig privacy scan, which reads test files
        # before a release, finds no whole UUID here.
        old = "-".join(["6f1d2a3b", "0000", "4000", "8000", "000000000001"])
        self.assertTrue(resumes(["claude", "--resume", old], old))
        self.assertTrue(resumes(["claude", "--fork-session", "--resume",
                                 f"/home/a/.claude/projects/p/{old}.jsonl"], old))
        # A file name without a directory, another ID that ends the same way, an ID
        # in another argument, and --resume at the end are no fork.
        self.assertFalse(resumes(["claude", "--resume", f"{old}.jsonl"], old))
        self.assertFalse(resumes(["claude", "--resume", f"/p/x{old}.jsonl"], old))
        self.assertFalse(resumes(["claude", "--session-id", old], old))
        self.assertFalse(resumes(["claude", old, "--resume"], old))


class TestDialog(OrchTestCase):
    def test_dialog_is_reported_at_once_and_events_are_still_appended(self):
        # The session shows a dialog. The loop reports it once, to the tee and its log,
        # within a few polls, and answers nothing. An inbox file that lands meanwhile
        # still gets its line in the event file, and no key reaches the session.
        self.start()
        (self.home / "claude-dialog").write_text("", encoding="utf-8")
        began = time.monotonic()
        report = f"dialog: session {SESSION} shows no input bar"
        self.until(lambda: report in self.tee(), seconds=10, what="the dialog report")
        self.assertLess(time.monotonic() - began, 3 * float(POLL) + 2)
        self.assertIn(report, self.log())
        path = self.srv / "in" / PROJECT / "note.md"
        path.write_text("hello\n", encoding="utf-8")
        self.until(lambda: self.events() == [self.inbox_line(path)],
                   what="the inbox line while the dialog shows")
        time.sleep(6 * float(POLL))
        self.assertFalse((self.home / "claude-answered").exists())
        self.assertEqual(self.submitted(), [])
        self.assertEqual(self.keys_sent(), [])
        self.assertEqual(self.log().count(report), 1, "the dialog was reported twice")


class ReportTestCase(OrchTestCase):
    """The project's clone is a clone of an origin repo; a second clone, the writer,
    pushes the commits that units and jig report push."""

    def setUp(self):
        super().setUp()
        self.origin = self.tmp / "origin.git"
        self.git(self.tmp, "init", "-q", "--bare", "-b", BRANCH, self.origin)
        self.writer = self.tmp / "writer"
        self.git(self.tmp, "clone", "-q", self.origin, self.writer)
        self.git(self.writer, "symbolic-ref", "HEAD", f"refs/heads/{BRANCH}")
        # The standing prompt is part of the project's tree, as in a real clone.
        shutil.copytree(self.clone, self.writer, dirs_exist_ok=True)
        self.push("the project's first commit")
        shutil.rmtree(self.clone)
        self.git(self.tmp, "clone", "-q", self.origin, self.clone)
        self.record = self.reviews / "orch" / "reported"

    def git(self, repo, *args, **extra):
        result = run(["git", "-C", repo, *args], env=self.env(**extra))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def push(self, subject, when=None):
        """Commit what the writer holds with the subject, push it, and return its hash.

        when is a git date such as "1700000000 +0000"; without it the commit is made now.
        """
        dates = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {}
        self.git(self.writer, "add", "-A")
        self.git(self.writer, "commit", "-q", "--allow-empty", "--no-verify", "-m", subject,
                 **dates)
        self.git(self.writer, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
        return self.git(self.writer, "rev-parse", "HEAD")

    def push_log(self, unit, first_line, when=None, subject=None):
        """Push the report of a unit with the first line given, as the unit's last commit.

        A first_line of None pushes the commit without a report file.
        """
        if first_line is not None:
            path = self.writer / "impl" / "log" / f"{unit}-report.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{first_line}\njig-report: 1\nid: {unit}\n", encoding="utf-8")
        return self.push(subject or f"log: {unit}", when)

    def an_hour_ago(self):
        return f"{int(time.time()) - 3600} +0000"

    def reports(self):
        return [line for line in self.events() if line.startswith("report:")]

    def watched(self):
        """Return the commit that the loop's own ref names in the clone, or ""."""
        result = run(["git", "-C", self.clone, "rev-parse", "-q", "--verify",
                      f"refs/jig-orch/{BRANCH}"], env=self.env())
        return result.stdout.strip()

    def settle(self):
        """Wait one report poll and its delivery, for a line that should not come.

        Most tests need no such wait: one fetch queues the log commits oldest first, so a
        line that should not come would come before the line the test waits for.
        """
        time.sleep(float(REPORT_POLL) + 2 * float(POLL))


class TestReportLines(ReportTestCase):
    def test_log_commit_gives_one_report_line_with_its_status(self):
        # A unit's last commit reaches origin: one line names the unit and its status,
        # and grep finds it exactly once in the event file.
        self.start()
        self.push_log("a1", "OK: the tool and its test")
        self.until(lambda: "report: a1 OK" in self.events(), seconds=30,
                   what="the report line")
        self.settle()
        found = run(["grep", "-c", "-x", "report: a1 OK", self.event_file()])
        self.assertEqual(found.stdout.strip(), "1", self.events())

    def test_needs_eric_report_gives_the_status_needs_eric(self):
        # The status is the text before the first colon, so the reason stays out of it.
        self.start()
        self.push_log("a1", "NEEDS ERIC: model is Sonnet 5.5, not Opus")
        self.until(lambda: self.reports(), seconds=30, what="the report line")
        self.assertEqual(self.reports(), ["report: a1 NEEDS ERIC"])

    def test_line_without_a_colon_is_the_status_and_a_missing_report_is_unreadable(self):
        # A first line with no colon is the status whole; a log commit whose report file
        # is not there still gets its line, with the status unreadable.
        self.start()
        self.push_log("a1", "OK")
        self.push_log("a2", None)
        expected = ["report: a1 OK", "report: a2 unreadable"]
        self.until(lambda: self.reports() == expected, seconds=30, what="the two lines")

    def test_report_commit_of_jig_report_gives_no_line(self):
        # jig report commits the filled report as "report: <ID>"; that is not the end of
        # a unit, so only the two log commits get lines.
        self.start()
        self.push_log("a1", "OK")
        self.until(lambda: self.reports() == ["report: a1 OK"], seconds=30,
                   what="the line for a1")
        self.push_log("a1", "OK: filled by jig report", subject="report: a1")
        self.push_log("a2", "OK")
        self.until(lambda: "report: a2 OK" in self.reports(), seconds=30,
                   what="the line for a2")
        self.assertEqual(self.reports(), ["report: a1 OK", "report: a2 OK"])

    def test_restart_of_the_loop_appends_no_report_line_twice(self):
        # A report announced by one loop is not announced by the next, and a report that
        # lands while no loop runs is announced at the next start.
        self.start()
        self.push_log("a1", "OK")
        self.until(lambda: self.reports() == ["report: a1 OK"], seconds=30,
                   what="the line for a1")
        self.assertEqual(self.orch("stop").returncode, 0, self.log())
        self.until(lambda: not self.alive(), what="the end of the session")
        self.push_log("a2", "OK")
        self.start()
        self.until(lambda: "report: a2 OK" in self.reports(), seconds=30,
                   what="the line for a2")
        self.assertEqual(self.reports(), ["report: a1 OK", "report: a2 OK"])

    def test_first_start_takes_the_log_commits_already_there_as_announced(self):
        # With no record yet, a log commit made before the loop started is old news and
        # gets no line; one made after the start does, and both go into the record.
        old = self.push_log("a1", "OK", when=self.an_hour_ago())
        self.start()
        new = self.push_log("a2", "OK")
        self.until(lambda: "report: a2 OK" in self.reports(), seconds=30,
                   what="the line for a2")
        self.assertEqual(self.reports(), ["report: a2 OK"])
        # The loop writes the record after it appends the line, so wait for it too.
        self.until(lambda: sorted(self.read(self.record).split()) == sorted([old, new]),
                   what="both commits in the record")

    def test_failed_first_fetch_loses_no_report(self):
        # The first fetches fail, so no record is made; a report lands meanwhile. When a
        # fetch works, that report still gets its line, the older one does not, and the
        # run of failures was logged once.
        self.script("git", GIT_DOUBLE.replace("@GIT@", shutil.which("git")))
        self.push_log("a1", "OK", when=self.an_hour_ago())
        failing = self.home / "fetch-fails"
        failing.write_text("", encoding="utf-8")
        self.start()
        failure = f"cannot fetch {BRANCH} from origin"
        self.until(lambda: failure in self.log(), seconds=30, what="the fetch failure")
        self.push_log("a2", "OK")
        time.sleep(3 * float(REPORT_POLL))
        self.assertEqual(self.log().count(failure), 1, self.log())
        self.assertIn("fatal: origin cannot be reached", self.log())
        self.assertIn(failure, self.tee())
        self.assertFalse(self.record.exists())
        failing.unlink()
        self.until(lambda: "report: a2 OK" in self.reports(), seconds=30,
                   what="the line for a2")
        self.assertEqual(self.reports(), ["report: a2 OK"])
        self.assertIn("works again", self.log())

    def test_report_line_is_appended_while_a_dialog_shows(self):
        # A unit reports while the session shows a dialog: the report line still goes
        # into the event file, and nothing answers the dialog.
        self.start()
        (self.home / "claude-dialog").write_text("", encoding="utf-8")
        self.until(lambda: "dialog: session" in self.tee(), seconds=10,
                   what="the dialog report")
        self.push_log("a1", "OK")
        self.until(lambda: self.reports() == ["report: a1 OK"], seconds=30,
                   what="the report line while the dialog shows")
        self.assertFalse((self.home / "claude-answered").exists())
        self.assertEqual(self.keys_sent(), [])



class TestEventOrder(ReportTestCase):
    def test_each_kind_of_event_is_one_line_in_the_order_it_came(self):
        # A reminder is due at the start; then an inbox file, a readback and a unit's
        # report come one after another, each after the line before it is in the file.
        # The event file holds the start line and then each event once, in that order,
        # in the forms the session reads.
        today = datetime.date.today()
        (self.clone / "impl" / "orch" / "state.md").write_text(
            f"## Open\n\n- The login of orchestrator, act by {today}.\n", encoding="utf-8")
        self.start()
        reminder = f"reminder: The login of orchestrator, act by {today}."
        self.until(lambda: self.events() == [reminder], what="the reminder")
        path = self.srv / "in" / PROJECT / "note.md"
        path.write_text("hello\n", encoding="utf-8")
        inbox = self.inbox_line(path)
        self.until(lambda: self.events()[-1:] == [inbox], what="the inbox line")
        (self.srv / "out" / PROJECT / "b1.txt").write_text("exit 0\n", encoding="utf-8")
        self.until(lambda: self.events()[-1:] == ["readback: b1"], what="the readback line")
        self.push_log("a1", "OK: done")
        self.until(lambda: self.events()[-1:] == ["report: a1 OK"], seconds=30,
                   what="the report line")
        self.settle()
        self.assertEqual(self.orch("stop").returncode, 0, self.log())
        self.assertEqual(self.event_lines(), [f"start {self.session_ids()[0]}", reminder,
                                              inbox, "readback: b1", "report: a1 OK"])


class TestReportFetch(ReportTestCase):
    def refs(self):
        return {name: self.git(self.clone, "rev-parse", name)
                for name in ("HEAD", f"refs/heads/{BRANCH}", f"refs/remotes/origin/{BRANCH}")}

    def test_watch_moves_no_ref_or_fetch_head_of_the_clone(self):
        # A fetch of a person leaves FETCH_HEAD and the remote-tracking branch as they
        # are; after the loop has fetched a new log commit, both and the branch are the
        # same, and only the loop's own ref names the new commit.
        self.git(self.clone, "fetch", "-q", "origin")
        fetch_head = (self.clone / ".git" / "FETCH_HEAD").read_bytes()
        before = self.refs()
        self.start()
        commit = self.push_log("a1", "OK")
        self.until(lambda: "report: a1 OK" in self.reports(), seconds=30,
                   what="the report line")
        self.assertEqual(self.watched(), commit)
        self.assertEqual(self.refs(), before)
        self.assertEqual((self.clone / ".git" / "FETCH_HEAD").read_bytes(), fetch_head)

    def test_watch_beside_a_loop_of_plain_fetches(self):
        # jig wait and jig batch fetch the same clone with a plain git fetch. Run one
        # after another while the loop watches: every one exits 0, and the report line
        # arrives once.
        self.start()
        failures, count, commit = [], 0, None
        began = time.monotonic()
        while time.monotonic() - began < 3 * float(REPORT_POLL):
            result = run(["git", "-C", self.clone, "fetch", "-q", "origin", BRANCH],
                         env=self.env())
            count += 1
            if result.returncode != 0:
                failures.append(result.stderr)
            if commit is None and time.monotonic() - began > float(REPORT_POLL):
                commit = self.push_log("a1", "OK")
        self.until(lambda: "report: a1 OK" in self.reports(), seconds=30,
                   what="the report line")
        self.assertEqual(failures, [], f"{len(failures)} of {count} plain fetches failed")
        self.assertEqual(self.reports(), ["report: a1 OK"])
        self.assertEqual(self.watched(), commit)

    def test_stalled_fetch_does_not_stop_an_inbox_line(self):
        # The fetch hangs. An inbox line still goes out while it hangs, and the loop ends
        # the fetch at its cap, which is the report poll here, and logs that once.
        self.report_poll = "3"
        self.script("git", GIT_DOUBLE.replace("@GIT@", shutil.which("git")))
        (self.home / "fetch-stalls").write_text("", encoding="utf-8")
        self.start()
        path = self.srv / "in" / PROJECT / "note.md"
        path.write_text("hello\n", encoding="utf-8")
        line = self.inbox_line(path)
        timeout = "no answer within 3 s"
        self.until(lambda: line in self.events(), seconds=30, what="the inbox line")
        self.assertNotIn(timeout, self.log(), "the line came only after the fetch ended")
        self.until(lambda: timeout in self.log(), seconds=30, what="the end of the fetch")
        time.sleep(float(self.report_poll) + 4 * float(POLL))
        self.assertEqual(self.log().count(timeout), 1, self.log())


# What read_line() has read from each pipe beyond the lines it returned.
READ_AHEAD = {}


def start_watcher(test, env):
    """Start the follow read of the watcher with its output on pipes; the test's
    cleanup ends it and closes the pipes."""
    watcher = subprocess.Popen([str(ORCH), PROJECT, "events"], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def end():
        watcher.kill()
        watcher.wait()
        READ_AHEAD.pop(watcher.stdout, None)
        watcher.stdout.close()
        watcher.stderr.close()

    test.addCleanup(end)
    return watcher


def read_line(stream, seconds):
    """Return the next line of a pipe, or b"" when no whole line comes within the time.

    The pipe is read by its descriptor, not through the buffer of the file object: a
    buffered read can take two lines at once, and select would then not see the second.
    """
    descriptor = stream.fileno()
    held = READ_AHEAD.pop(stream, b"")
    deadline = time.monotonic() + seconds
    while b"\n" not in held:
        left = deadline - time.monotonic()
        ready, _, _ = select.select([descriptor], [], [], max(left, 0))
        chunk = os.read(descriptor, 4096) if ready else b""
        if not chunk:
            READ_AHEAD[stream] = held
            return b""
        held += chunk
    line, _, rest = held.partition(b"\n")
    READ_AHEAD[stream] = rest
    return line + b"\n"


class TestWatcher(TempTestCase):
    """jig orch <project> events, the session's watcher on the event file."""

    def setUp(self):
        super().setUp()
        self.path = self.home / "ws" / "reviews" / PROJECT / "orch" / "events.log"
        self.path.parent.mkdir(parents=True)

    def write(self, *lines):
        self.path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")

    def once(self):
        result = run([ORCH, PROJECT, "events", "--old"], env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def follow(self):
        return start_watcher(self, self.env())

    def first_lines(self, watcher, count):
        return [read_line(watcher.stdout, 5).decode().rstrip("\n") for _ in range(count)]

    def test_once_read_gives_the_events_of_the_session_before_the_newest(self):
        # With two start lines and more, the lines between the last two; with one, the
        # lines before it; with none, nothing; with no file, nothing. Each exits 0.
        self.write("report: a1 OK", "start s1", "inbox: one", "start s2", "readback: b1",
                   "start s3", "report: a2 OK", "reminder: two", "start s4", "hourly update")
        self.assertEqual(self.once(), ["report: a2 OK", "reminder: two"])
        self.write("report: a1 OK", "inbox: one", "start s1", "readback: b1")
        self.assertEqual(self.once(), ["report: a1 OK", "inbox: one"])
        self.write("report: a1 OK", "inbox: one")
        self.assertEqual(self.once(), [])
        self.path.unlink()
        self.assertEqual(self.once(), [])

    def test_follow_read_starts_after_the_last_start_line(self):
        # Two sessions have started; the follow read gives only the lines after the
        # second start line, then nothing more until a line is appended.
        self.write("inbox: one", "start s1", "readback: b1", "start s2", "report: a1 OK",
                   "hourly update")
        watcher = self.follow()
        self.assertEqual(self.first_lines(watcher, 2), ["report: a1 OK", "hourly update"])
        self.assertEqual(read_line(watcher.stdout, 1), b"")

    def test_follow_read_with_one_start_line_and_with_none(self):
        # One start line: the lines after it. No start line: every line from the top.
        self.write("inbox: one", "start s1", "readback: b1")
        self.assertEqual(self.first_lines(self.follow(), 1), ["readback: b1"])
        self.write("inbox: one", "readback: b1")
        self.assertEqual(self.first_lines(self.follow(), 2), ["inbox: one", "readback: b1"])

    def test_appended_line_reaches_the_pipe_of_the_running_follow_read(self):
        # The follow read runs with its output on a pipe, as under the harness tool.
        # A line appended to the file, a start line of a new session among them, comes
        # out within a few seconds while the watcher still runs.
        self.write("start s1")
        watcher = self.follow()
        time.sleep(0.5)
        for line in ("report: a1 OK", "start s2", "inbox: one"):
            with open(self.path, "a", encoding="utf-8") as stream:
                stream.write(f"{line}\n")
            began = time.monotonic()
            self.assertEqual(read_line(watcher.stdout, 5), f"{line}\n".encode())
            self.assertLess(time.monotonic() - began, 5)
        self.assertIsNone(watcher.poll(), "the watcher ended")

    def test_follow_read_waits_for_a_missing_file(self):
        # No event file exists yet: the watcher reads it as empty, prints nothing and
        # keeps running. Once the loop writes the file, each of its lines is new and
        # comes out, the start line too.
        watcher = self.follow()
        self.assertEqual(read_line(watcher.stdout, 1), b"")
        self.assertIsNone(watcher.poll(), "the watcher ended")
        self.write("start s1", "reminder: one")
        self.assertEqual(self.first_lines(watcher, 2), ["start s1", "reminder: one"])

    def test_line_still_being_written_comes_out_whole(self):
        # A line arrives in two writes; the watcher prints it once, whole, after its
        # newline.
        self.write("start s1")
        watcher = self.follow()
        with open(self.path, "a", encoding="utf-8") as stream:
            stream.write("report: a1")
            stream.flush()
            self.assertEqual(read_line(watcher.stdout, 1), b"")
            stream.write(" OK\n")
        self.assertEqual(read_line(watcher.stdout, 5), b"report: a1 OK\n")

    def test_stop_signal_ends_the_follow_read_with_no_error(self):
        self.write("start s1")
        watcher = self.follow()
        time.sleep(0.5)
        watcher.terminate()
        self.assertEqual(watcher.wait(timeout=5), 0)
        self.assertEqual(watcher.stderr.read(), b"")


class TestWatcherOfAnEndedSession(OrchTestCase):
    def test_watcher_ends_when_the_session_that_started_it_ends(self):
        # A fixture session in tmux starts the watcher as a background process in a
        # process group of its own, with its output on a pipe, as a harness tool does.
        # When the tmux session is killed, the watcher ends too, within a few seconds.
        (self.reviews / "orch").mkdir()
        self.event_file().write_text("start s1\n", encoding="utf-8")
        pid_file = self.tmp / "watcher.pid"
        runner = (f"import subprocess; "
                  f"w = subprocess.Popen([{str(ORCH)!r}, {PROJECT!r}, 'events'], "
                  f"stdout=subprocess.PIPE, start_new_session=True); "
                  f"open({str(pid_file)!r}, 'w').write(str(w.pid)); "
                  f"[print(line) for line in w.stdout]")
        self.tmux("new-session", "-d", "-s", "fixture", sys.executable, "-c", runner)
        self.until(lambda: self.read(pid_file), what="the watcher")
        pid = int(self.read(pid_file))
        self.assertTrue(running(pid))
        self.tmux("kill-session", "-t", "=fixture")
        self.until(lambda: not running(pid), seconds=5, what="the end of the watcher")


def running(pid):
    """Say whether a process runs; one that has ended but is not yet reaped does not."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stream:
            return stream.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


# A timestamp as date -Iseconds prints it, which starts every line the loop writes.
STAMP = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d"
# The pattern that counts idle lines in the update log, as a person would count them.
IDLE_PATTERN = "^[0-9T:+-]* idle: "
QUEUED = "hourly: something changed"
IDLE = "hourly: nothing changed"


def load_orch():
    """Load the supervisor script as a module, for the tests of its plain functions."""
    loader = importlib.machinery.SourceFileLoader("jig_orch_under_test", str(ORCH))
    spec = importlib.util.spec_from_loader("jig_orch_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class HourlyChecks:
    """Helpers for the tests of the hourly tick, which runs every second here."""

    hourly = "1"

    def updates(self):
        return self.clone / "impl" / "orch" / "updates.log"

    def idle_count(self):
        counted = run(["grep", "-c", IDLE_PATTERN, self.updates()])
        return int(counted.stdout.strip() or 0)

    def hourly_lines(self):
        return [line for line in self.events() if line == "hourly update"]

    def ticks(self):
        """Return the log's record of each tick, oldest first."""
        return [line for line in self.log().splitlines() if " hourly: " in line]

    def stop_loop(self):
        self.assertEqual(self.orch("stop").returncode, 0, self.log())


class TestHourly(HourlyChecks, OrchTestCase):
    def test_quiet_ticks_write_idle_lines_and_append_no_event(self):
        # No event comes and origin cannot be fetched, so nothing changes. Each of three
        # ticks appends an idle line that names the orchestrator and the start of the
        # loop; the event file never gets "hourly update"; every line in the
        # update log and in the loop's log starts with a timestamp.
        before = time.time()
        self.start()
        self.until(lambda: self.idle_count() >= 3, what="three idle lines")
        self.stop_loop()
        self.assertEqual(self.hourly_lines(), [])
        lines = self.read(self.updates()).splitlines()
        self.assertEqual(len(lines), self.idle_count())
        self.assertEqual(len(lines), len([tick for tick in self.ticks() if IDLE in tick]))
        since = set()
        for line in lines:
            match = re.fullmatch(rf"({STAMP}) idle: waits for the orchestrator since ({STAMP})",
                                 line)
            self.assertTrue(match, line)
            since.add(match[2])
        self.assertEqual(len(since), 1, lines)
        started = datetime.datetime.fromisoformat(since.pop()).timestamp()
        self.assertGreaterEqual(started, int(before))
        self.assertLessEqual(started, time.time())
        for line in self.log().splitlines():
            self.assertRegex(line, rf"^{STAMP} ")

    def test_inbox_line_before_a_tick_gives_one_hourly_update_and_no_idle_line(self):
        # An inbox line goes into the event file between two ticks. The next tick
        # appends "hourly update" and writes no idle line; the ticks after it, with
        # nothing new, write idle lines again, since the time of that inbox line, and
        # append no event.
        self.start()
        path = self.srv / "in" / PROJECT / "note.md"
        path.write_text("hello\n", encoding="utf-8")
        self.until(lambda: self.hourly_lines(), what="the hourly update")
        self.until(lambda: IDLE in self.ticks()[-1], what="a quiet tick after it")
        self.stop_loop()
        self.assertEqual(self.hourly_lines(), ["hourly update"])
        log = self.log().splitlines()
        delivered = next(index for index, line in enumerate(log)
                         if f"appended: {self.inbox_line(path)}" in line)
        after = [line for line in log[delivered:] if " hourly: " in line]
        self.assertIn(QUEUED, after[0])
        self.assertEqual(len([tick for tick in self.ticks() if QUEUED in tick]), 1)
        self.assertEqual(self.idle_count(), len([tick for tick in self.ticks() if IDLE in tick]))
        # The idle line gives the time of the inbox line, which the log also stamps.
        since = self.read(self.updates()).splitlines()[-1].rsplit(" since ", 1)[1]
        stamped = log[delivered].split()[0]
        self.assertLessEqual(abs(datetime.datetime.fromisoformat(since).timestamp()
                                 - datetime.datetime.fromisoformat(stamped).timestamp()), 1)

    def test_waited_for_reads_the_newest_line_of_the_status_log(self):
        # A start line as the newest line names that unit; an end line, a log with no
        # lines, or a line of another form names the orchestrator.
        waited_for = load_orch().waited_for
        start = "2026-11-09T12:00:00-08:00 a1 start started 48e7773\n"
        end = "2026-11-09T13:00:00-08:00 a1 end OK 6360901\n"
        self.assertEqual(waited_for(start), "unit a1")
        self.assertEqual(waited_for(start + end), "the orchestrator")
        self.assertEqual(waited_for(start + end + start), "unit a1")
        self.assertEqual(waited_for(start + "\n\n"), "unit a1")
        self.assertEqual(waited_for(""), "the orchestrator")
        self.assertEqual(waited_for(None), "the orchestrator")
        self.assertEqual(waited_for("a1 start\n"), "the orchestrator")
        self.assertEqual(waited_for("2026-11-09T12:00:00-08:00 Bad/ID start started x\n"),
                         "the orchestrator")


class TestHourlyOrigin(HourlyChecks, ReportTestCase):
    def wait_for_fetch(self, commit=None):
        self.until(lambda: self.watched() == commit if commit else self.watched(),
                   seconds=30, what="the loop's fetch")

    def test_pushed_commit_that_changes_only_the_update_log_is_no_change(self):
        # The orchestrator pushes its update log alone. Once the loop has fetched it,
        # two more ticks pass, and both write idle lines and append no event.
        self.start()
        self.wait_for_fetch()
        path = self.writer / "impl" / "orch" / "updates.log"
        path.write_text("2026-11-09T09:00:00-08:00 the first hourly update\n",
                        encoding="utf-8")
        commit = self.push("orch: hourly update")
        self.wait_for_fetch(commit)
        seen = len(self.ticks())
        self.until(lambda: len(self.ticks()) >= seen + 2, what="two ticks after the fetch")
        self.assertEqual([tick for tick in self.ticks() if QUEUED in tick], [])
        self.assertEqual(self.hourly_lines(), [])

    def test_pushed_commit_that_changes_another_file_is_a_change(self):
        # A commit that changes a spec reaches origin after the first fetch: the next
        # tick appends "hourly update".
        self.start()
        self.wait_for_fetch()
        path = self.writer / "specs" / "spec.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("a new rule\n", encoding="utf-8")
        self.push("spec: a new rule")
        self.until(lambda: self.hourly_lines() == ["hourly update"], seconds=30,
                   what="the hourly update")

    def test_idle_line_names_the_unit_that_runs(self):
        # On origin, the newest line of the status log starts unit a1, so a quiet tick
        # says that the project waits for that unit.
        path = self.writer / "impl" / "log" / "status.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("2026-11-09T10:00:00-08:00 z9 start started abc\n"
                        "2026-11-09T11:00:00-08:00 z9 end OK def\n"
                        "2026-11-09T12:00:00-08:00 a1 start started def\n", encoding="utf-8")
        self.push("status: a1 start")
        self.start()
        self.until(lambda: " idle: waits for unit a1 since " in self.read(self.updates()),
                   seconds=30, what="an idle line that names a1")
        self.assertEqual(self.hourly_lines(), [])


class TestReminders(HourlyChecks, OrchTestCase):
    def state(self, due, later):
        """Write state.md with one login item due on the date due and a token due later."""
        (self.clone / "impl" / "orch" / "state.md").write_text(
            "# demo orchestrator state\n\n## Current\n\n"
            "- Heads: none, act by 2000-01-01 stands outside the Open part.\n\n"
            "## Open\n\n"
            "Each item ends with the date to act on it, as `act by 2000-01-01`.\n\n"
            "- The claude.ai login of orchestrator: expires 2099-01-01,\n"
            f"  act by {due}.\n"
            f"- The product push token: expires 2099-01-01, act by {later}.\n"
            "- Eric runs the block new. Act at once.\n\n"
            "## Next units\n\n1. The kickoff.\n", encoding="utf-8")

    def reminders(self):
        return [line for line in self.events() if line.startswith("reminder: ")]

    def two_more_ticks(self):
        seen = len(self.ticks())
        self.until(lambda: len(self.ticks()) >= seen + 2, what="two more ticks")

    def test_due_item_is_appended_once_across_ticks_and_restarts_and_again_for_a_new_date(self):
        # The login item is due today and the token item a week later. The login item
        # gets one line, joined from its two lines, and no other tick or a restart of the
        # loop appends it again; the token item gets none. When the login item gets a new
        # date, already past, the next tick appends it once more.
        today = datetime.date.today()
        later = today + datetime.timedelta(days=7)
        self.state(today, later)
        line = ("reminder: The claude.ai login of orchestrator: expires 2099-01-01, "
                f"act by {today}.")
        self.start()
        self.until(lambda: self.reminders(), what="the reminder")
        self.two_more_ticks()
        self.stop_loop()
        self.until(lambda: not self.alive(), what="the end of the session")
        self.start()
        self.two_more_ticks()
        self.assertEqual(self.reminders(), [line])
        recorded = (self.reviews / "orch" / "reminded").read_text(encoding="utf-8")
        self.assertEqual(recorded, f"{today} {line[len('reminder: '):]}\n")
        yesterday = today - datetime.timedelta(days=1)
        self.state(yesterday, later)
        self.until(lambda: len(self.reminders()) == 2, what="the reminder for the new date")
        self.two_more_ticks()
        self.stop_loop()
        self.assertEqual(self.reminders(), [line, line.replace(str(today), str(yesterday))])

    def test_due_items_reads_only_the_open_part_up_to_today(self):
        # Under Open: an item due yesterday over two lines, one due today written with a
        # capital A, one due tomorrow, one with no date and one with a date that does
        # not exist. Only the first two are due, each as one line; the dated line under
        # Current and the sentence between the heading and the list are not items.
        due_items = load_orch().due_items
        text = ("## Current\n\n- Heads: act by 2026-01-01.\n\n## Open\n\n"
                "Each item ends as `act by 2026-01-01`.\n\n"
                "- Token: expires 2026-03-08,\n  act by 2026-03-01.\n"
                "- Login: expires 2026-03-09, Act by 2026-03-02.\n"
                "* Login: expires 2026-03-10, act by 2026-03-03.\n"
                "- Block: act at once, no date.\n"
                "- Odd: act by 2026-02-30.\n\n## Next units\n\n- act by 2026-01-01\n")
        self.assertEqual(due_items(text, datetime.date(2026, 3, 2)), [
            ("Token: expires 2026-03-08, act by 2026-03-01.", "2026-03-01"),
            ("Login: expires 2026-03-09, Act by 2026-03-02.", "2026-03-02")])
        self.assertEqual(due_items(text, datetime.date(2026, 2, 28)), [])
