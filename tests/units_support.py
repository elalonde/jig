"""What the tests of jig status, jig wait and jig cat share: doubles for ssh and tmux.

No test reaches a real account or a real tmux server. The ssh double runs the remote
command in a directory that stands for the account's home; the tmux double reads the
screen from files there.
"""

import datetime
import os
import re

from tests.support import ROOT, TempTestCase, kickoff_record, push_kickoff, run

TEMPLATE = ROOT / "templates" / "project.conf"

# Stands in for ssh: <account>@localhost runs the command in accounts/<account>, as it.
SSH_DOUBLE = """#!/bin/bash
account=${1%@*}
echo "$1" >> "$FAKE/ssh-targets"
[ -f "$FAKE/ssh-fails" ] && exit 255
home=$FAKE/accounts/$account
[ -d "$home" ] || exit 255
cd "$home" || exit 255
HOME=$home exec bash -c "$2"
"""

# Stands in for tmux. Each call counts; past the count in tmux-end-after the session is
# gone. A screen comes from screen.sh, given the count, or else from the file screen.
TMUX_DOUBLE = """#!/bin/bash
echo "$*" >> "$HOME/tmux-args"
n=$(( $(cat "$HOME/tmux-calls" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$HOME/tmux-calls"
if [ -f "$HOME/tmux-end-after" ] && [ "$n" -gt "$(cat "$HOME/tmux-end-after")" ]; then
    echo "no server running" >&2; exit 1
fi
screen() {
    if [ -f "$HOME/screen.sh" ]; then bash "$HOME/screen.sh" "$n"
    elif [ -f "$HOME/screen" ]; then cat "$HOME/screen"
    else echo "can't find session" >&2; return 1
    fi
}
case $1 in
    capture-pane) screen ;;
    has-session) screen > /dev/null ;;
    *) echo "tmux double: $1 is not handled" >&2; exit 1 ;;
esac
"""

# A Claude Code screen at work: output, the input bar, the line under it.
NORMAL_SCREEN = """● Running the tests

────────────────────────────────────────
>
────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle)
"""

DIALOG_SCREEN = """● Bash command

   rm -rf build

 Do you want to proceed?
 ❯ 1. Yes
   2. No, and tell Claude what to do differently

 Esc to cancel
"""


class UnitsTestCase(TempTestCase):
    """An orchestrator home with a clone and a conf file, worker homes, a lock directory."""

    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake"
        (self.fake / "accounts").mkdir(parents=True)
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self.ssh = self.script("ssh-double", SSH_DOUBLE)
        self.script("tmux", TMUX_DOUBLE)
        self.lock_dir = self.tmp / "lock"
        self.lock_dir.mkdir()
        self.batch_dir = self.tmp / "batch"
        self.batch_dir.mkdir()

        self.origin = self.tmp / "origin.git"
        self.ok(["git", "init", "-q", "--bare", "-b", "master", self.origin])
        self.seed = self.git_init(self.tmp / "seed")
        self.ok(["git", "-C", self.seed, "remote", "add", "origin", self.origin])
        self.push_subject("first")
        self.clone = self.home / "ws" / "src" / "jig-dev"
        self.clone.parent.mkdir(parents=True)
        self.ok(["git", "clone", "-q", "-b", "master", self.origin, self.clone])
        self.write_conf("jig")

    def script(self, name, text):
        path = self.bin_dir / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
        return path

    def base_env(self, **extra):
        env = self.env(PATH=f"{self.bin_dir}:{os.environ['PATH']}", JIG_SSH=str(self.ssh),
                       FAKE=str(self.fake), JIG_LOCK_DIR=str(self.lock_dir),
                       JIG_BATCH_DIR=str(self.batch_dir))
        env.update(extra)
        return env

    def ok(self, command):
        result = run(command, env=self.base_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def tool(self, name, *args, **extra):
        return run([ROOT / "libexec" / f"jig-{name}", *args], env=self.base_env(**extra))

    def write_conf(self, project, **changes):
        text = TEMPLATE.read_text(encoding="utf-8").replace("<project>", project)
        text = re.sub(r"^JIG_VERSION=.*\n", "", text, flags=re.MULTILINE)
        for key, value in changes.items():
            text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.MULTILINE)
        conf = self.home / ".config" / "jig" / f"{project}.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(text, encoding="utf-8")

    def push_subject(self, subject):
        self.ok(["git", "-C", self.seed, "commit", "-q", "--no-verify", "--allow-empty",
                 "-m", subject])
        self.ok(["git", "-C", self.seed, "push", "-q", "origin", "master"])

    def set_profile(self, *lines):
        """Push a kickoff tag, one above the last, whose profile section holds these lines."""
        self.kickoffs = getattr(self, "kickoffs", 0) + 1
        push_kickoff(self.base_env(), self.seed, self.kickoffs, kickoff_record(*lines))

    def account(self, name):
        home = self.fake / "accounts" / name
        home.mkdir(exist_ok=True)
        return home

    def write_lock(self, account="implementer", session="jig-impl-demo", project="jig",
                   start=None, expected=45):
        start = start or datetime.datetime.now().astimezone().replace(microsecond=0)
        lock = self.lock_dir / f"{project}-{account}"
        lock.write_text(f"session={session}\nstart={start.isoformat()}\n", encoding="utf-8")
        lock.with_name(lock.name + ".expected").write_text(f"{expected}\n", encoding="utf-8")
        return start

    def set_screen(self, account, text):
        (self.account(account) / "screen").write_text(text, encoding="utf-8")

    def set_screen_script(self, account, text):
        (self.account(account) / "screen.sh").write_text(text, encoding="utf-8")

    def end_session_after(self, account, calls):
        (self.account(account) / "tmux-end-after").write_text(f"{calls}\n", encoding="utf-8")

    def ssh_targets(self):
        path = self.fake / "ssh-targets"
        return path.read_text(encoding="utf-8").split() if path.exists() else []
