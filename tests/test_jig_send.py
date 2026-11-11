"""jig send: a correction typed into a running unit's tmux session and submitted.

Each test has a private tmux server, through TMUX_TMPDIR in the test's own directory,
and runs jig send from this tree through the dispatcher. The ssh double runs the worker
part in a worker home of its own. The unit's session runs the Claude Code screen double
from tests/support.py, which writes each line it takes as submitted to claude-submitted
in the worker home.
"""

import os
import re
import time

from tests.support import CLAUDE_DOUBLE, ROOT, TempTestCase, run
from tests.test_start import SSH_DOUBLE

JIG = ROOT / "bin" / "jig"
TEMPLATE = ROOT / "templates" / "project.conf"
SESSION = "jig-impl-demo"
FIRST_LINE = f"# demo  paste into: {SESSION} (implementer, ~/ws/src/jig-dev)\n"

# Each piece would change if a shell on the way read it: quotes, $ and command
# substitution, backslashes, ; and the other shell signs. The last ";" also tests that
# tmux types a text's final ";", which it would otherwise read as the end of a command.
PIECES = ("it's \"quoted\" here,", "$HOME ${HOME} $(touch injected) `touch injected`",
          "back\\slash \\n \\\\ 100%", "a & b | c > d * ? ~ # ! ;", "{x} [y] <z> =w")


def correction_of_500_characters():
    text = " ".join(PIECES)
    filler = " plain words to make the line long"
    while len(text) + len(filler) < 499:
        text += filler
    return (text + " " * 499)[:499] + ";"


class SendCase(TempTestCase):
    """The orchestrator's prompt and conf, a worker home, and the session's tmux server."""

    def setUp(self):
        super().setUp()
        self.worker_home = self.tmp / "worker"
        self.worker_home.mkdir()
        self.tmux_dir = self.tmp / "tmux"
        self.tmux_dir.mkdir()
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self.script("claude", CLAUDE_DOUBLE)
        self.ssh = self.script("ssh-double", SSH_DOUBLE)
        self.prompt = self.home / "ws" / "prompts" / "jig" / "demo.md"
        self.prompt.parent.mkdir(parents=True)
        self.prompt.write_text(FIRST_LINE + "UNATTENDED: the rest of the prompt\n",
                               encoding="utf-8")
        self.write_conf()
        self.addCleanup(self.end_tmux)

    def script(self, name, text):
        path = self.bin_dir / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
        return path

    def write_conf(self, **changes):
        text = TEMPLATE.read_text(encoding="utf-8").replace("<project>", "jig")
        text = re.sub(r"^JIG_VERSION=.*\n", "", text, flags=re.MULTILINE)
        for key, value in changes.items():
            text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.MULTILINE)
        conf = self.home / ".config" / "jig" / "jig.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(text, encoding="utf-8")

    def base_env(self, **extra):
        # An empty install root makes the dispatcher run this tree. The pause and the
        # check are at test scale; the default check waits five seconds.
        env = self.env(JIG_ROOT=str(self.tmp / "no-install"),
                       PATH=f"{self.bin_dir}:{os.environ['PATH']}", JIG_SSH=str(self.ssh),
                       WORKER_HOME=str(self.worker_home), TMUX_TMPDIR=str(self.tmux_dir),
                       JIG_SEND_PAUSE="0.2", JIG_SEND_CHECK="2")
        env.update(extra)
        return env

    def end_tmux(self):
        run(["tmux", "kill-server"], env=self.base_env())

    def tmux(self, *args):
        return run(["tmux", *args], env=self.base_env())

    def screen(self, session=SESSION):
        return self.tmux("capture-pane", "-p", "-t", f"={session}:").stdout

    def open_session(self, wait_for="auto mode on", session=SESSION):
        """Start the screen double in the worker's home, as the unit's session."""
        created = run(["tmux", "new-session", "-d", "-s", session, "-x", "200", "-y", "50",
                       self.bin_dir / "claude"], env=self.base_env(HOME=str(self.worker_home)))
        self.assertEqual(created.returncode, 0, created.stderr)
        deadline = time.monotonic() + 10
        while wait_for not in self.screen(session):
            if time.monotonic() >= deadline:
                self.fail(f"the double never showed {wait_for!r}")
            time.sleep(0.1)

    def send(self, *words, unit="demo"):
        return run([JIG, "send", "jig", unit, *words], env=self.base_env())

    def submitted(self):
        path = self.worker_home / "claude-submitted"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def ssh_used(self):
        return (self.worker_home / "ssh-targets").exists()

    def assert_refused(self, result, reason):
        """A refusal exits 1, names its reason, and submits nothing."""
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("refused:", result.stderr)
        self.assertIn(reason, result.stderr)
        self.assertEqual(self.submitted(), [])


class SendDeliveryTest(SendCase):
    def test_correction_of_500_characters_with_shell_signs_arrives_whole_and_submitted(self):
        # The text goes in as many words, which jig send joins with single spaces; the
        # double then holds exactly that line as submitted, and no shell ran any of it.
        text = correction_of_500_characters()
        self.assertEqual(len(text), 500)
        self.open_session()
        result = self.send(*text.split(" "))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.submitted(), [text])
        self.assertEqual((self.worker_home / "ssh-targets").read_text(encoding="utf-8"),
                         "implementer@localhost\n")
        self.assertFalse((self.worker_home / "injected").exists())
        self.assertFalse((self.tmp / "injected").exists())

    def test_text_of_one_word_ending_in_a_semicolon_keeps_it(self):
        # tmux would read the final ";" as the end of its command and drop it.
        self.open_session()
        result = self.send("stop\\;")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.submitted(), ["stop\\;"])


class SendRefusalTest(SendCase):
    def test_screen_without_the_input_bar_gets_no_text_and_no_enter(self):
        # The double shows a permission dialog; an Enter there would answer it. The
        # refusal names the reason and shows nothing of the screen.
        (self.worker_home / "claude-dialog").write_text("", encoding="utf-8")
        self.open_session(wait_for="Do you want to proceed?")
        result = self.send("go on with the next step")
        self.assert_refused(result, "no input bar")
        self.assertFalse((self.worker_home / "claude-answered").exists())
        self.assertNotIn("proceed", result.stdout + result.stderr)
        self.assertNotIn("rm -rf", result.stdout + result.stderr)

    def test_unknown_id_is_refused_before_ssh(self):
        self.open_session()
        result = self.send("a correction", unit="other")
        self.assert_refused(result, "prompt")
        self.assertFalse(self.ssh_used())

    def test_prompt_that_names_another_session_is_refused_before_ssh(self):
        self.prompt.write_text(FIRST_LINE.replace(SESSION, "jig-impl-other"), encoding="utf-8")
        self.open_session(session="jig-impl-other")
        result = self.send("a correction")
        self.assert_refused(result, "prompt")
        self.assertFalse(self.ssh_used())

    def test_account_that_the_conf_does_not_give_the_role_is_refused_before_ssh(self):
        self.write_conf(IMPL_ACCOUNT="someone")
        result = self.send("a correction")
        self.assert_refused(result, "account")
        self.assertFalse(self.ssh_used())

    def test_missing_session_is_refused(self):
        result = self.send("a correction")
        self.assert_refused(result, "does not exist")
        self.assertTrue(self.ssh_used())

    def test_text_that_is_not_one_line_is_a_usage_error_before_the_prompt_is_read(self):
        # The ID has no prompt, so a refusal of the prompt would exit 1: exit 2 shows
        # that the text was checked first. Esc and tab count too: Esc would stop the
        # worker's turn.
        for words in (["one", "line\nand another"], ["carriage\rreturn"], [], [""],
                      ["  ", " "], ["escape \x1b here"], ["tab\there"]):
            with self.subTest(words=words):
                result = self.send(*words, unit="other")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("usage: jig send", result.stderr)
        self.assertFalse(self.ssh_used())

    def test_usage_text_gives_the_exit_codes(self):
        result = run([JIG, "send", "--help"], env=self.base_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        for meaning in ("0 when the text was submitted", "1 on a refusal", "2 on a usage error"):
            self.assertIn(meaning, " ".join(result.stdout.split()))
