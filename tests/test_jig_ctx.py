"""jig ctx: the context reading of a tmux session, and its exit code against the limits.

The context files live in a directory of the test's own, named by JIG_CTX_DIR. Every
tmux the tests run talks to a private server: the plain tests point TMUX_TMPDIR into
the test's directory, and the rollover test uses the supervisor tests' own server.
"""

import datetime
import json
import time

from tests.support import CLAUDE_DOUBLE, ROOT, TempTestCase, run
from tests.test_jig_orch import POLL, OrchTestCase
from tests.test_jig_orch import SESSION as ORCH_SESSION

TOOL = ROOT / "libexec" / "jig-ctx"
SESSION = "ctx-demo"
WINDOW = 1000000


def stamp(moment):
    return moment.isoformat(timespec="seconds")


def context_file(directory, session, tokens, written):
    data = {"session": session, "total_input_tokens": tokens, "context_window_size": WINDOW,
            "used_percentage": round(100 * tokens / WINDOW, 1), "written": stamp(written)}
    # Written aside and renamed into place, as the status line does, so that a reader
    # polling for the file never sees half of it.
    part = directory / f".{session}.part"
    part.write_text(json.dumps(data) + "\n", encoding="utf-8")
    part.rename(directory / f"{session}.json")


class JigCtxTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.ctx_dir = self.tmp / "ctx"
        self.ctx_dir.mkdir()
        self.tmux_dir = self.tmp / "tmux"
        self.tmux_dir.mkdir()
        self.addCleanup(lambda: run(["tmux", "kill-server"], env=self.base_env()))

    def base_env(self, **extra):
        env = self.env(JIG_CTX_DIR=str(self.ctx_dir), TMUX_TMPDIR=str(self.tmux_dir))
        env.update(extra)
        return env

    def write(self, tokens, age=datetime.timedelta(seconds=5), session=SESSION):
        now = datetime.datetime.now().astimezone()
        context_file(self.ctx_dir, session, tokens, now - age)

    def ctx(self, *args, **extra):
        return run([TOOL, *args], env=self.base_env(**extra))

    def assert_reads(self, result, text, code):
        self.assertEqual((result.stdout, result.returncode), (text + "\n", code), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_below_the_soft_limit_prints_the_reading_and_exits_0(self):
        self.write(249999)
        self.assert_reads(self.ctx(SESSION), f"249999/{WINDOW}", 0)

    def test_at_the_soft_limit_exits_3(self):
        # 250000 tokens is the default soft limit itself: the session starts no new step.
        self.write(250000)
        result = self.ctx(SESSION)
        self.assert_reads(result, f"250000/{WINDOW}", 3)
        self.assertIn("soft limit", result.stderr)

    def test_at_the_hard_limit_exits_4(self):
        self.write(280000)
        result = self.ctx(SESSION)
        self.assert_reads(result, f"280000/{WINDOW}", 4)
        self.assertIn("hard limit", result.stderr)

    def test_limits_from_the_environment_replace_the_defaults(self):
        # With a soft limit of 100 and a hard one of 200, 150 tokens is between them.
        self.write(150)
        self.assert_reads(self.ctx(SESSION, JIG_CTX_SOFT="100", JIG_CTX_HARD="200"),
                          f"150/{WINDOW}", 3)
        self.assert_reads(self.ctx(SESSION, JIG_CTX_SOFT="100", JIG_CTX_HARD="150"),
                          f"150/{WINDOW}", 4)

    def test_a_limit_that_is_not_a_number_is_a_usage_error(self):
        self.write(150)
        result = self.ctx(SESSION, JIG_CTX_SOFT="250k")
        self.assertEqual((result.stdout, result.returncode), ("", 2))
        self.assertIn("JIG_CTX_SOFT", result.stderr)

    def test_a_missing_file_prints_none_and_exits_2(self):
        self.assert_reads(self.ctx(SESSION), "none (no file)", 2)

    def test_a_file_older_than_3_minutes_prints_none_with_its_age(self):
        self.write(1000, age=datetime.timedelta(seconds=200))
        result = self.ctx(SESSION)
        self.assertEqual(result.returncode, 2, result.stderr)
        # The age counts on while the tool starts, so allow a few seconds over 200.
        self.assertRegex(result.stdout, r"\Anone \(20[0-9] s old\)\n\Z")

    def test_a_file_just_under_3_minutes_old_still_counts(self):
        self.write(1000, age=datetime.timedelta(seconds=170))
        self.assert_reads(self.ctx(SESSION), f"1000/{WINDOW}", 0)

    def test_a_file_with_a_bad_shape_prints_none_without_a_traceback(self):
        for text in ("{not json", json.dumps({"session": SESSION}), "[]"):
            with self.subTest(text=text):
                (self.ctx_dir / f"{SESSION}.json").write_text(text, encoding="utf-8")
                self.assert_reads(self.ctx(SESSION), "none (bad file)", 2)

    def test_a_session_name_that_leaves_the_directory_is_refused(self):
        result = self.ctx("../ctx/x")
        self.assertEqual((result.stdout, result.returncode), ("", 2))
        self.assertIn("bad tmux session name", result.stderr)

    def test_outside_tmux_with_no_argument_is_a_usage_error(self):
        self.write(1000)
        result = self.ctx()
        self.assertEqual((result.stdout, result.returncode), ("", 2))
        self.assertIn("not inside tmux", result.stderr)

    def test_help_gives_the_exit_codes(self):
        result = self.ctx("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in ("usage: jig ctx [-h] [session]", "  3  at or above the soft limit",
                     "  4  at or above the hard limit", "  2  no reading (none)"):
            self.assertIn(line, result.stdout)

    def start_session(self, command="sleep 60"):
        result = run(["tmux", "new-session", "-d", "-s", SESSION, command], env=self.base_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        created = run(["tmux", "display-message", "-p", "-t", f"={SESSION}:",
                       "#{session_created}"], env=self.base_env())
        self.assertEqual(created.returncode, 0, created.stderr)
        return datetime.datetime.fromtimestamp(int(created.stdout)).astimezone()

    def test_with_no_argument_inside_tmux_it_reads_its_own_session(self):
        # The tool runs inside the tmux session ctx-demo on the private server, once the
        # test has written the file, and writes what it printed and its exit code to files.
        out, code = self.tmp / "out", self.tmp / "code"
        path = self.ctx_dir / f"{SESSION}.json"
        created = self.start_session(f"until [ -e {path} ]; do sleep 0.1; done; "
                                     f"{TOOL} > {out} 2>&1; echo $? > {code}; sleep 60")
        context_file(self.ctx_dir, SESSION, 1000, created)
        deadline = time.monotonic() + 20
        while not code.exists() or not code.read_text():
            self.assertLess(time.monotonic(), deadline, "the tool in tmux did not finish")
            time.sleep(0.1)
        self.assertEqual((out.read_text(), code.read_text()), (f"1000/{WINDOW}\n", "0\n"))

    def test_a_file_written_before_the_running_session_started_is_none(self):
        # The session runs, and the file is from 5 seconds before it was created: it is
        # the figure of an earlier session with the same name.
        created = self.start_session()
        context_file(self.ctx_dir, SESSION, 260000, created - datetime.timedelta(seconds=5))
        self.assert_reads(self.ctx(SESSION), "none (before session start)", 2)

    def test_a_file_written_when_the_running_session_started_counts(self):
        created = self.start_session()
        context_file(self.ctx_dir, SESSION, 1000, created)
        self.assert_reads(self.ctx(SESSION), f"1000/{WINDOW}", 0)

    def test_a_file_written_before_a_session_that_does_not_run_counts(self):
        # No session of that name runs, so the time of the file is not checked against one.
        self.start_session()
        self.write(1000, session="other")
        self.assert_reads(self.ctx("other"), f"1000/{WINDOW}", 0)


# A claude double for the supervisor that first acts as an orchestrator does after each
# step: it runs jig ctx on its own session, and exits when the answer is 3. The first
# session sees no file until the test writes one, then exits; the second sees the same
# file as written before it started, and goes on as the usual double with an input bar.
# The supervisor passes only PATH into the session, so the paths are written in.
ROLLOVER_DOUBLE = '''#!/bin/bash
echo start >> "$HOME/ctx-starts"
export JIG_CTX_DIR=@CTX_DIR@
for attempt in $(seq 150); do
    reading=$(@TOOL@ 2>/dev/null)
    code=$?
    echo "$code $reading" >> "$HOME/ctx-readings"
    if [ "$code" = 3 ]; then
        # The figure carries the creation second of this session; waiting past that
        # second makes sure the next session is created in a later one.
        sleep 1.1
        exit 0
    fi
    [ "$reading" = "none (no file)" ] || break
    sleep 0.2
done
exec claude-double "$@"
'''


class RolloverTest(OrchTestCase):
    def test_a_session_over_the_soft_limit_exits_and_the_fresh_one_stays_up(self):
        ctx_dir = self.tmp / "ctx"
        ctx_dir.mkdir()
        self.script("claude-double", CLAUDE_DOUBLE)
        self.script("claude", ROLLOVER_DOUBLE.replace("@CTX_DIR@", str(ctx_dir))
                    .replace("@TOOL@", str(TOOL)))
        self.assertEqual(self.orch("start").returncode, 0, self.log())
        self.until(lambda: "none (no file)" in self.read(self.home / "ctx-readings"),
                   what="the first session's reading")
        created = self.tmux("display-message", "-p", "-t", f"={ORCH_SESSION}:",
                            "#{session_created}")
        self.assertEqual(created.returncode, 0, created.stderr)
        # The status line of the first session writes 260000 tokens, over the default
        # soft limit of 250000 and under the hard one.
        moment = datetime.datetime.fromtimestamp(int(created.stdout)).astimezone()
        context_file(ctx_dir, ORCH_SESSION, 260000, moment)
        self.until(lambda: "none (before session start)" in
                   self.read(self.home / "ctx-readings"), what="the second session's reading")
        self.until(self.ready, what="the second session's input bar")
        readings = [line for line in self.read(self.home / "ctx-readings").splitlines()
                    if not line.endswith("none (no file)")]
        self.assertEqual(readings, [f"3 260000/{WINDOW}", "2 none (before session start)"])
        # The fresh session stays up: a few polls later there is still no third start.
        time.sleep(6 * float(POLL))
        self.assertTrue(self.alive())
        self.assertEqual(self.read(self.home / "ctx-starts").splitlines(), ["start", "start"])
