import grp
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import time
import unittest

from tests.support import CO_AUTHOR_ADDRESS, ROOT, TempTestCase, run

sys.path.insert(0, str(ROOT / "lib"))

import jig_dirs  # noqa: E402
import jig_ids  # noqa: E402

JIG = ROOT / "bin" / "jig"
WORKER = ROOT / "libexec" / "jig-start-worker"
HOOK = ROOT / "hooks" / "pre-commit"
GOOD_PROMPT = ROOT / "tests" / "fixtures" / "validate" / "good-prompt.md"
TEMPLATE = ROOT / "templates" / "project.conf"

SESSION = "jig-impl-demo"
# The supervisor appends to this file of the clone and leaves the commit to the orchestrator.
UPDATES = "impl/orch/updates.log"
DIALOG = ("Quick safety check: Is this a project you created or one you trust?\n"
          " 1. No, exit\n 2. Yes, I trust this folder\n Enter to confirm · Esc to cancel\n")

# Stands in for claude: says what it was given, then waits as a live session does.
CLAUDE_DOUBLE = """#!/bin/bash
printf '%s\\0' "$@" > "$HOME/claude-args"
pwd > "$HOME/claude-cwd"
echo "double claude output"
[ -f "$HOME/claude-exits" ] && exit 3
[ -f "$HOME/claude-dialog" ] && cat "$HOME/claude-dialog"
read -r -t 300 answer && echo "$answer" > "$HOME/claude-answered"
"""

# Stands in for ssh: runs the remote command in the worker's home, as the worker.
SSH_DOUBLE = """#!/bin/bash
echo "$1" >> "$WORKER_HOME/ssh-targets"
[ -f "$WORKER_HOME/ssh-fails" ] && exit 255
cd "$WORKER_HOME" || exit 255
HOME=$WORKER_HOME exec bash -c "$2"
"""

# Stands in for sg: says what it was given, then runs the command as the real one does.
SG_DOUBLE = """#!/bin/bash
printf '%s\\0' "$@" >> "$WORKER_HOME/sg-args"
exec /bin/sh -c "$3"
"""

# Runs the worker with another group in its constant: the tests need a group that the
# account running them is, or is not, a member of, and CI has no group jig.
WORKER_WITH_GROUP = """
import importlib.machinery, importlib.util, sys
sys.dont_write_bytecode = True
loader = importlib.machinery.SourceFileLoader("jig_start_worker", sys.argv[1])
spec = importlib.util.spec_from_loader("jig_start_worker", loader)
worker = importlib.util.module_from_spec(spec)
loader.exec_module(worker)
worker.SESSION_GROUP = sys.argv[2]
sys.exit(worker.main(sys.argv[3:]))
"""

# Each line would change if one level of the start command read it as shell.
TRICKY_PROMPT = """Line one has 'single' and "double" quotes, and an ' alone.
A $HOME and ${HOME} and $(touch injected) and `touch injected` stay as they are.
Back\\slash, \\n, 100% of it; a & b | c > d * ? ~ # ! end

After an empty line.
  An indented line.
"""


class StartCase(TempTestCase):
    """A bare origin, an orchestrator clone and a worker clone, with both homes."""

    def setUp(self):
        super().setUp()
        self.worker_home = self.tmp / "worker"
        self.worker_home.mkdir()
        self.lock_dir = self.tmp / "lock"
        self.lock_dir.mkdir()
        self.tmux_dir = self.tmp / "tmux"
        self.tmux_dir.mkdir()
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self.script("claude", CLAUDE_DOUBLE)
        self.ssh = self.script("ssh-double", SSH_DOUBLE)

        self.origin = self.tmp / "origin.git"
        self.ok(["git", "init", "-q", "--bare", "-b", "master", self.origin])
        seed = self.git_init(self.tmp / "seed")
        self.ok(["git", "-C", seed, "remote", "add", "origin", self.origin])
        (seed / ".gitignore").write_text(".claude/settings.local.json\n", encoding="utf-8")
        (seed / "specs").mkdir()
        (seed / "specs" / "spec.md").write_text("the approved spec\n", encoding="utf-8")
        self.git_commit_all(seed)
        self.seed = seed
        self.ok(["git", "-C", seed, "tag", "spec-p0"])
        self.ok(["git", "-C", seed, "push", "-q", "origin", "master", "spec-p0"])

        self.orch_clone = self.clone_to(self.home / "ws" / "src" / "jig-dev")
        self.worker_clone = self.clone_to(self.worker_home / "ws" / "src" / "jig-dev")
        self.write_conf(self.home)
        self.write_conf(self.worker_home)
        self.prompt = self.home / "ws" / "prompts" / "jig" / "demo.md"
        self.prompt.parent.mkdir(parents=True)
        self.prompt.write_text(GOOD_PROMPT.read_text(encoding="utf-8"), encoding="utf-8")
        self.addCleanup(self.end_tmux)
        self.addCleanup(self.make_writable)

    def script(self, name, text):
        path = self.bin_dir / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
        return path

    def base_env(self, **extra):
        # An empty install root makes the dispatcher run this tree, not an installed version.
        env = self.env(JIG_ROOT=str(self.tmp / "no-install"),
                       PATH=f"{self.bin_dir}:{os.environ['PATH']}", JIG_SSH=str(self.ssh),
                       WORKER_HOME=str(self.worker_home), JIG_LOCK_DIR=str(self.lock_dir),
                       TMUX_TMPDIR=str(self.tmux_dir), JIG_START_SETTLE="0.8")
        env.update(extra)
        return env

    def ok(self, command):
        result = run(command, env=self.base_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def clone_to(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ok(["git", "clone", "-q", "-b", "master", self.origin, path])
        return path

    def write_conf(self, home, **changes):
        text = TEMPLATE.read_text(encoding="utf-8").replace("<project>", "jig")
        text = re.sub(r"^JIG_VERSION=.*\n", "", text, flags=re.MULTILINE)
        for key, value in changes.items():
            text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.MULTILINE)
        conf = home / ".config" / "jig" / "jig.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(text, encoding="utf-8")

    def make_writable(self):
        subprocess.run(["chmod", "-R", "u+w", str(self.tmp)], capture_output=True, check=False)

    def end_tmux(self):
        run(["tmux", "kill-server"], env=self.base_env())

    def tmux(self, *args):
        return run(["tmux", *args], env=self.base_env())

    def start(self, *extra, unit="demo", role="impl", prompt=None):
        return run([JIG, "start", "jig", role, unit, prompt or self.prompt, "--expected", "45",
                    *extra], env=self.base_env())

    def worker_alone(self, *extra, unit="demo", role="impl", prompt=None):
        return run([WORKER, "jig", role, unit, prompt or self.prompt, "--account", "implementer",
                    "--expected", "45", "--model", "claude-haiku-5-5", *extra],
                   env=self.base_env(HOME=str(self.worker_home)))

    def lock(self):
        return self.lock_dir / "jig-implementer"

    def origin_head(self):
        return self.ok(["git", "-C", self.origin, "rev-parse", "master"])

    def origin_subjects(self):
        return self.ok(["git", "-C", self.origin, "log", "--format=%s", "master"]).splitlines()

    def worker_file(self, name):
        return self.worker_home / name

    def wait_for(self, path, text=None):
        for _ in range(100):
            if path.exists() and (text is None or text in path.read_text(encoding="utf-8")):
                return
            time.sleep(0.1)
        self.fail(f"{path} never held {text!r}")

    def assert_refused(self, result, name):
        """A refusal names its precondition and leaves every later step undone."""
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"refused: {name}:", result.stderr)
        self.assertEqual(self.origin_subjects(), ["fixture"] * len(self.origin_subjects()))
        self.assertNotEqual(self.tmux("has-session", "-t", f"={SESSION}").returncode, 0)
        self.assertFalse(self.worker_file("claude-args").exists())
        self.assertFalse((self.worker_home / "ws" / "prompts" / "jig" / "demo.md").exists())

    def replace_in_prompt(self, old, new):
        text = self.prompt.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.prompt.write_text(text.replace(old, new), encoding="utf-8")

    def push_from_seed(self, name="more.md"):
        (self.seed / name).write_text("more\n", encoding="utf-8")
        self.git_commit_all(self.seed)
        self.ok(["git", "-C", self.seed, "push", "-q", "origin", "master"])

    def track_update_log(self):
        """Put the orchestrator's update log on origin and in the orchestrator's clone.

        Then append a line to it in that clone, as the supervisor does between commits,
        and return the line. The seed clone stays at origin.
        """
        path = self.seed / UPDATES
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("2026-11-09T09:00:00-08:00 the first hourly update\n", encoding="utf-8")
        self.git_commit_all(self.seed)
        self.ok(["git", "-C", self.seed, "push", "-q", "origin", "master"])
        self.ok(["git", "-C", self.orch_clone, "pull", "-q", "--ff-only", "origin", "master"])
        return self.append_idle_line(self.orch_clone)

    def append_idle_line(self, clone):
        line = f"2026-11-09T10:00:00-08:00 idle: waits for the orchestrator since {clone.name}\n"
        path = clone / UPDATES
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(line)
        return line

    def race_next_push(self):
        """Make the orchestrator's next push lose a race: another commit reaches origin
        first, so the push is refused and the tool must rebase once."""
        hook = self.orch_clone / ".git" / "hooks" / "pre-push"
        hook.write_text(f"""#!/bin/bash
[ -e "{self.tmp}/raced" ] && exit 0
touch "{self.tmp}/raced"
cd "{self.seed}" && unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE
echo raced > raced.md && git add raced.md && git commit -q --no-verify -m raced
git push -q origin master
""", encoding="utf-8")
        hook.chmod(0o755)

    def lose_origin_at_next_push(self):
        """Make origin unreachable from the orchestrator's clone as its next push starts,
        so the push and the fetch after it both fail, as when the network is down."""
        hook = self.orch_clone / ".git" / "hooks" / "pre-push"
        hook.write_text(f"""#!/bin/bash
git config remote.origin.url "{self.tmp}/gone.git"
exit 1
""", encoding="utf-8")
        hook.chmod(0o755)

    def assert_line_kept(self, line):
        """The supervisor's line is still in the orchestrator's clone, and still uncommitted."""
        self.assertTrue((self.orch_clone / UPDATES).read_text(encoding="utf-8").endswith(line))
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "status", "--porcelain"]),
                         f"M {UPDATES}")

    def in_worker(self, path):
        return str(self.worker_home / path)

    def claude_args(self):
        self.wait_for(self.worker_file("claude-cwd"))
        return self.worker_file("claude-args").read_text(encoding="utf-8").split("\0")[:-1]

    def given_dirs(self):
        """Return the --add-dir values, each of which must stand before the other options."""
        args = self.claude_args()
        first_other = args.index("--model")
        self.assertEqual(args[0:first_other:2], ["--add-dir"] * (first_other // 2))
        self.assertEqual(first_other % 2, 0)
        return args[1:first_other:2]


class StartPreconditionTest(StartCase):
    def test_start_refuses_a_prompt_that_fails_validate(self):
        self.replace_in_prompt("UNATTENDED:", "UNWATCHED:")
        self.assert_refused(self.start(), "validate")

    def test_start_refuses_a_prompt_for_another_unit(self):
        self.assert_refused(self.start(unit="other"), "validate")
        self.assert_refused(self.start(role="rev"), "validate")

    def test_start_refuses_when_the_lock_exists(self):
        self.lock().write_text("session=jig-impl-old\n", encoding="utf-8")
        self.assert_refused(self.start(), "lock")
        self.assertEqual(self.lock().read_text(encoding="utf-8"), "session=jig-impl-old\n")

    def test_start_refuses_when_the_conf_account_differs(self):
        self.write_conf(self.home, IMPL_ACCOUNT="someone")
        self.assert_refused(self.start(), "account")

    def test_start_refuses_when_origin_cannot_be_fetched(self):
        self.ok(["git", "-C", self.worker_clone, "remote", "set-url", "origin",
                 self.tmp / "gone.git"])
        self.assert_refused(self.start(), "fetch")

    def test_start_refuses_when_another_branch_is_checked_out(self):
        self.ok(["git", "-C", self.worker_clone, "checkout", "-q", "-b", "elsewhere"])
        self.assert_refused(self.start(), "branch")

    def test_start_refuses_a_dirty_tree(self):
        (self.worker_clone / "stray.txt").write_text("stray\n", encoding="utf-8")
        self.assert_refused(self.start(), "clean")

    def test_start_accepts_a_worker_clone_whose_only_change_is_the_update_log(self):
        # The supervisor appends to the update log between commits, so a clone with that
        # one file changed counts as clean, whether the file is tracked yet or not.
        self.append_idle_line(self.worker_clone)
        self.assertEqual(self.worker_alone("--approved-tag", "spec-p0", "--stage", "check")
                         .returncode, 0)
        (self.worker_clone / UPDATES).unlink()
        self.track_update_log()
        self.ok(["git", "-C", self.worker_clone, "pull", "-q", "--ff-only", "origin", "master"])
        self.append_idle_line(self.worker_clone)
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_start_refuses_a_worker_clone_with_another_change_beside_the_update_log(self):
        self.append_idle_line(self.worker_clone)
        (self.worker_clone / "stray.txt").write_text("stray\n", encoding="utf-8")
        result = self.start()
        self.assert_refused(result, "clean")
        self.assertIn("stray.txt", result.stderr)

    def test_start_refuses_a_branch_that_cannot_fast_forward(self):
        (self.worker_clone / "local.md").write_text("local\n", encoding="utf-8")
        self.git_commit_all(self.worker_clone)
        self.push_from_seed()
        self.assert_refused(self.start(), "fast-forward")

    def test_start_refuses_a_head_ahead_of_origin(self):
        (self.worker_clone / "local.md").write_text("local\n", encoding="utf-8")
        self.git_commit_all(self.worker_clone)
        self.assert_refused(self.start(), "head")

    def test_start_refuses_a_tag_that_is_only_local(self):
        self.replace_in_prompt("Spec tag: spec-p0.", "Spec tag: spec-p0, kickoff-r3.")
        self.ok(["git", "-C", self.worker_clone, "tag", "kickoff-r3"])
        self.assert_refused(self.start(), "tag")

    def test_start_refuses_a_spec_tag_missing_on_origin(self):
        self.replace_in_prompt("Spec tag: spec-p0.", "Spec tag: spec-p9.")
        self.assert_refused(self.start(), "tag")

    def test_start_refuses_a_spec_tag_of_another_form(self):
        original = self.prompt.read_text(encoding="utf-8")
        for tag in ("spec-p0x2", "spec-p0r", "spec-p0r2x", "spec-p0r2-1"):
            with self.subTest(tag=tag):
                # On origin, so that only its form can refuse it.
                self.ok(["git", "-C", self.seed, "tag", tag])
                self.ok(["git", "-C", self.seed, "push", "-q", "origin", tag])
                self.prompt.write_text(original.replace("Spec tag: spec-p0.",
                                                        f"Spec tag: {tag}."), encoding="utf-8")
                self.assert_refused(self.start(), "tag")

    def test_start_refuses_a_revision_tag_on_the_role_line_missing_on_origin(self):
        self.replace_in_prompt("Spec tag: spec-p0.", "Spec tag: spec-p0, spec-p0r3.")
        self.assert_refused(self.start(), "tag")

    def test_start_refuses_an_implementer_prompt_without_a_spec_tag(self):
        self.replace_in_prompt("Spec tag: spec-p0.", "Spec tag: none.")
        self.assert_refused(self.start(), "tag")

    def test_start_refuses_a_model_other_than_the_prompts(self):
        self.assert_refused(self.start("--model", "claude-sonnet-5-5"), "model")
        self.assert_refused(self.start("--model", "sonnet"), "model")

    def test_start_refuses_a_model_it_cannot_select(self):
        self.replace_in_prompt("this unit needs Opus.", "this unit needs Gemini.")
        self.assert_refused(self.start(), "model")

    def test_start_refuses_in_the_order_of_the_spec(self):
        self.replace_in_prompt("UNATTENDED:", "UNWATCHED:")
        self.lock().write_text("old\n", encoding="utf-8")
        self.write_conf(self.home, IMPL_ACCOUNT="someone")
        self.ok(["git", "-C", self.worker_clone, "remote", "set-url", "origin",
                 self.tmp / "gone.git"])
        self.assert_refused(self.start("--model", "sonnet"), "validate")
        self.replace_in_prompt("UNWATCHED:", "UNATTENDED:")
        self.assert_refused(self.start("--model", "sonnet"), "lock")
        self.lock().unlink()
        self.assert_refused(self.start("--model", "sonnet"), "account")
        self.write_conf(self.home)
        self.assert_refused(self.start("--model", "sonnet"), "fetch")
        self.ok(["git", "-C", self.worker_clone, "remote", "set-url", "origin", self.origin])
        self.ok(["git", "-C", self.worker_clone, "checkout", "-q", "-b", "elsewhere"])
        self.assert_refused(self.start("--model", "sonnet"), "branch")
        self.ok(["git", "-C", self.worker_clone, "checkout", "-q", "master"])
        (self.worker_clone / "stray.txt").write_text("stray\n", encoding="utf-8")
        self.assert_refused(self.start("--model", "sonnet"), "clean")
        (self.worker_clone / "stray.txt").unlink()
        self.assert_refused(self.start("--model", "sonnet"), "model")

    def test_start_refuses_when_the_worker_cannot_be_reached(self):
        (self.worker_home / "ssh-fails").touch()
        self.assert_refused(self.start(), "ssh")

    def test_start_refuses_when_the_two_accounts_disagree_on_the_branch(self):
        self.write_conf(self.worker_home, BRANCH="other")
        self.assert_refused(self.start(), "conf")

    def test_start_refuses_without_a_conf_file(self):
        (self.home / ".config" / "jig" / "jig.conf").unlink()
        self.assert_refused(self.start(), "conf")

    def test_start_refuses_a_live_session_of_that_name(self):
        self.assertEqual(self.tmux("new-session", "-d", "-s", SESSION, "sleep 300").returncode, 0)
        result = self.start()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("refused: session:", result.stderr)
        self.assertFalse(self.lock().exists())
        self.assertEqual(set(self.origin_subjects()), {"fixture"})


class StartRunTest(StartCase):
    def test_start_commits_the_status_line_alone_and_brings_the_worker_to_it(self):
        before = self.origin_head()
        (self.orch_clone / "not-mine.txt").write_text("the orchestrator's\n", encoding="utf-8")
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[0], "status: demo start")
        body = self.ok(["git", "-C", self.origin, "log", "-1", "--format=%b", "master"])
        self.assertEqual(body, f"Co-Authored-By: Claude <{CO_AUTHOR_ADDRESS}>")
        self.assertEqual(self.ok(["git", "-C", self.origin, "show", "--name-only", "--format=",
                                  "master"]), "impl/log/status.log")
        log = (self.orch_clone / "impl" / "log" / "status.log").read_text(encoding="utf-8")
        self.assertRegex(log, rf"^\S+T\S+ demo start started {before}\n\Z")
        self.assertEqual(self.ok(["git", "-C", self.worker_clone, "rev-parse", "HEAD"]),
                         self.origin_head())
        self.assertTrue((self.orch_clone / "not-mine.txt").exists())

    def test_start_reaches_the_prompts_account_by_the_ssh_variable(self):
        self.assertEqual(self.start().returncode, 0)
        targets = self.worker_file("ssh-targets").read_text(encoding="utf-8").split()
        self.assertEqual(set(targets), {"implementer@localhost"})

    def test_start_pushes_after_one_rebase_when_origin_moved(self):
        hook = self.orch_clone / ".git" / "hooks" / "pre-push"
        hook.write_text(f"""#!/bin/bash
[ -e "{self.tmp}/raced" ] && exit 0
touch "{self.tmp}/raced"
cd "{self.seed}" && unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE
echo raced > raced.md && git add raced.md && git commit -q --no-verify -m raced
git push -q origin master
""", encoding="utf-8")
        hook.chmod(0o755)
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[:2], ["status: demo start", "raced"])
        self.assertEqual(self.ok(["git", "-C", self.worker_clone, "rev-parse", "HEAD"]),
                         self.origin_head())

    def test_start_keeps_an_uncommitted_update_log_line_through_a_refused_push(self):
        # The supervisor's idle line waits uncommitted in the orchestrator's clone when
        # the status push loses a race; the one rebase carries the line along.
        line = self.track_update_log()
        self.race_next_push()
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[:2], ["status: demo start", "raced"])
        self.assert_line_kept(line)

    def test_start_starts_nothing_when_the_status_push_is_refused(self):
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        before = self.origin_head()
        self.assert_refused(self.start(), "status")
        self.assertFalse(self.lock().exists())
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "rev-parse", "HEAD"]), before)
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "status", "--porcelain"]), "")

    def test_start_refuses_an_orchestrator_clone_with_unpushed_commits(self):
        (self.orch_clone / "mine.md").write_text("mine\n", encoding="utf-8")
        self.git_commit_all(self.orch_clone)
        self.assert_refused(self.start(), "status")

    def test_start_merges_the_effort_and_keeps_the_other_settings(self):
        path = self.worker_clone / ".claude" / "settings.local.json"
        path.parent.mkdir()
        path.write_text(json.dumps({
            "permissions": {"defaultMode": "auto"},
            "modelSettings": {"claude-sonnet-5-5": {"effortLevel": "low", "other": 1}},
        }), encoding="utf-8")
        account_settings = self.worker_home / ".claude" / "settings.json"
        account_settings.parent.mkdir()
        account_settings.write_text('{"effortLevel": "low"}\n', encoding="utf-8")

        self.assertEqual(self.start("--effort", "max").returncode, 0)
        settings = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(settings["permissions"]["defaultMode"], "auto")
        self.assertEqual(settings["effortLevel"], "max")
        self.assertEqual(settings["modelSettings"]["claude-opus-5-5"], {"effortLevel": "max"})
        self.assertEqual(settings["modelSettings"]["claude-sonnet-5-5"],
                         {"effortLevel": "low", "other": 1})
        self.assertEqual(account_settings.read_text(encoding="utf-8"), '{"effortLevel": "low"}\n')

    def test_start_sets_auto_mode_in_a_clone_with_no_local_settings(self):
        path = self.worker_clone / ".claude" / "settings.local.json"
        self.assertFalse(path.exists())
        self.assertEqual(self.start().returncode, 0)
        settings = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(settings["permissions"], {"defaultMode": "auto"})

    def test_start_keeps_a_permission_mode_the_clone_names(self):
        path = self.worker_clone / ".claude" / "settings.local.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"permissions": {"defaultMode": "acceptEdits",
                                                    "allow": ["Bash(make:*)"]}}),
                        encoding="utf-8")
        self.assertEqual(self.start().returncode, 0)
        settings = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(settings["permissions"],
                         {"defaultMode": "acceptEdits", "allow": ["Bash(make:*)"]})

    def test_start_sets_high_effort_by_default(self):
        self.assertEqual(self.start().returncode, 0)
        path = self.worker_clone / ".claude" / "settings.local.json"
        settings = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(settings["effortLevel"], "high")
        self.assertEqual(settings["modelSettings"]["claude-opus-5-5"]["effortLevel"], "high")

    def test_start_copies_the_prompt_and_its_inbox(self):
        inbox = self.prompt.parent / "demo"
        inbox.mkdir()
        (inbox / "note.txt").write_text("for the unit\n", encoding="utf-8")
        self.assertEqual(self.start().returncode, 0)
        target = self.worker_home / "ws" / "prompts" / "jig"
        self.assertEqual((target / "demo.md").read_text(encoding="utf-8"),
                         self.prompt.read_text(encoding="utf-8"))
        self.assertEqual((target / "demo" / "note.txt").read_text(encoding="utf-8"),
                         "for the unit\n")

    def test_start_puts_the_approved_worktree_at_the_tag_read_only(self):
        self.assertEqual(self.start().returncode, 0)
        worktree = self.worker_home / "ws" / "src" / "jig-dev-approved"
        spec = worktree / "specs" / "spec.md"
        self.assertEqual(spec.read_text(encoding="utf-8"), "the approved spec\n")
        self.assertEqual(self.ok(["git", "-C", worktree, "rev-parse", "HEAD"]),
                         self.ok(["git", "-C", self.origin, "rev-parse", "spec-p0^{commit}"]))
        for path in (spec, spec.parent, worktree):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o222, 0, path)

    def test_start_moves_the_approved_worktree_to_a_newer_tag(self):
        self.assertEqual(self.start().returncode, 0)
        self.assertEqual(self.tmux("kill-session", "-t", f"={SESSION}").returncode, 0)
        self.lock().unlink()
        self.ok(["git", "-C", self.seed, "pull", "-q", "origin", "master"])
        (self.seed / "specs" / "spec.md").write_text("the next spec\n", encoding="utf-8")
        self.git_commit_all(self.seed)
        self.ok(["git", "-C", self.seed, "tag", "spec-p1"])
        self.ok(["git", "-C", self.seed, "push", "-q", "origin", "master", "spec-p1"])
        self.replace_in_prompt("Spec tag: spec-p0.", "Spec tag: spec-p1.")
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        spec = self.worker_home / "ws" / "src" / "jig-dev-approved" / "specs" / "spec.md"
        self.assertEqual(spec.read_text(encoding="utf-8"), "the next spec\n")
        self.assertEqual(stat.S_IMODE(spec.stat().st_mode) & 0o222, 0)

    def test_start_puts_the_approved_worktree_at_a_spec_revision_tag(self):
        self.push_from_seed()
        self.ok(["git", "-C", self.seed, "tag", "spec-p0r2"])
        self.ok(["git", "-C", self.seed, "push", "-q", "origin", "spec-p0r2"])
        self.replace_in_prompt("Spec tag: spec-p0.", "Spec tag: spec-p0r2.")
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        worktree = self.worker_home / "ws" / "src" / "jig-dev-approved"
        self.assertEqual(self.ok(["git", "-C", worktree, "rev-parse", "HEAD"]),
                         self.ok(["git", "-C", self.origin, "rev-parse", "spec-p0r2^{commit}"]))
        self.assertTrue((worktree / "more.md").is_file())

    def test_start_gives_a_spec_unit_no_worktree(self):
        self.replace_in_prompt("jig-impl-demo (implementer,", "jig-spec-demo (specwriter,")
        result = self.start(role="spec")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.worker_home / "ws" / "src" / "jig-dev-approved").exists())
        self.assertTrue((self.lock_dir / "jig-specwriter").exists())
        self.assertEqual(self.tmux("has-session", "-t", "=jig-spec-demo").returncode, 0)

    def test_start_gives_a_review_in_either_account_its_own_lock(self):
        self.replace_in_prompt("jig-impl-demo (implementer,", "jig-rev-demo (specwriter,")
        self.assertEqual(self.start(role="rev").returncode, 0)
        self.assertTrue((self.lock_dir / "jig-specwriter").exists())
        self.assertFalse((self.worker_home / "ws" / "src" / "jig-dev-approved").exists())

    def test_start_runs_claude_in_tmux_with_the_prompt_as_first_message(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tmux("has-session", "-t", f"={SESSION}").returncode, 0)
        self.wait_for(self.worker_file("claude-cwd"))
        args = self.worker_file("claude-args").read_text(encoding="utf-8").split("\0")[:-1]
        self.assertEqual(args, ["--add-dir", self.in_worker("ws/prompts/jig"),
                                "--add-dir", self.in_worker("ws/reviews/jig"),
                                "--add-dir", self.in_worker("ws/src/jig-dev-approved"),
                                "--model", "claude-opus-5-5", "--name", SESSION,
                                self.prompt.read_text(encoding="utf-8").rstrip("\n")])
        self.assertEqual(self.worker_file("claude-cwd").read_text(encoding="utf-8").strip(),
                         str(self.worker_clone))

    def test_start_passes_a_named_model_through(self):
        self.replace_in_prompt("this unit needs Opus.", "this unit needs claude-opus-5-1.")
        self.assertEqual(self.start().returncode, 0)
        self.wait_for(self.worker_file("claude-args"))
        self.assertIn("claude-opus-5-1", self.worker_file("claude-args").read_text(encoding="utf-8"))

    def test_start_runs_the_newest_fable_for_the_family_name(self):
        # A review prompt names only the family, so the start must pick the
        # newest Fable model and not an older one.
        self.replace_in_prompt("this unit needs Opus.", "this unit needs Fable.")
        self.assertEqual(self.start().returncode, 0)
        args = self.claude_args()
        self.assertEqual(args[args.index("--model") + 1], "claude-fable-5-2")

    def test_start_takes_the_lock_and_writes_the_expected_minutes_beside_it(self):
        self.assertEqual(self.start().returncode, 0)
        self.assertIn(f"session={SESSION}\n", self.lock().read_text(encoding="utf-8"))
        beside = self.lock_dir / "jig-implementer.expected"
        self.assertEqual(beside.read_text(encoding="utf-8"), "45\n")

    def test_start_defaults_to_the_lock_path_of_the_spec(self):
        text = WORKER.read_text(encoding="utf-8")
        self.assertIn('os.environ.get("JIG_LOCK_DIR", "/run/jig/lock")) / f"{project}-{account}"',
                      text)

    def test_start_tees_and_a_restart_appends(self):
        self.assertEqual(self.start().returncode, 0)
        tee = self.worker_home / "ws" / "reviews" / "jig" / "out" / "demo.txt"
        self.wait_for(tee, "double claude output")
        first = tee.read_text(encoding="utf-8")
        self.tmux("kill-session", "-t", f"={SESSION}")
        self.lock().unlink()
        (self.worker_home / "claude-dialog").write_text("second run marker\n", encoding="utf-8")
        self.assertEqual(self.start().returncode, 0)
        self.wait_for(tee, "second run marker")
        self.assertTrue(tee.read_text(encoding="utf-8").startswith(first))

    def test_start_names_the_trust_dialog_and_does_not_answer_it(self):
        (self.worker_home / "claude-dialog").write_text(DIALOG, encoding="utf-8")
        result = self.start()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("folder-trust dialog", result.stderr)
        self.assertIn(SESSION, result.stderr)
        self.assertEqual(self.tmux("has-session", "-t", f"={SESSION}").returncode, 0)
        self.assertTrue(self.lock().exists())
        self.assertFalse(self.worker_file("claude-answered").exists())

    def test_start_passes_a_pane_without_the_dialog(self):
        (self.worker_home / "claude-dialog").write_text("Quick safety first\n", encoding="utf-8")
        self.assertEqual(self.start().returncode, 0)

    def test_start_keeps_the_lock_for_close_dead_when_the_session_ends_at_once(self):
        (self.worker_home / "claude-exits").touch()
        result = self.start()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("refused: session:", result.stderr)
        self.assertIn("jig close --dead jig demo", result.stderr)
        # The start line is on origin; the lock keeps what jig close --dead needs.
        self.assertEqual(self.origin_subjects()[0], "status: demo start")
        self.assertIn(f"session={SESSION}\nstart=", self.lock().read_text(encoding="utf-8"))
        self.assertTrue((self.lock_dir / "jig-implementer.expected").exists())

    def test_start_worker_alone_frees_the_lock_when_the_session_ends_at_once(self):
        self.write_conf(self.worker_home)
        (self.worker_home / "claude-exits").touch()
        result = self.worker_alone("--approved-tag", "spec-p0")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("refused: session:", result.stderr)
        self.assertFalse(self.lock().exists())
        self.assertFalse((self.lock_dir / "jig-implementer.expected").exists())

    def test_start_loses_a_lock_race_at_the_launch(self):
        self.write_conf(self.worker_home)
        self.lock().write_text("other\n", encoding="utf-8")
        result = self.worker_alone("--stage", "launch", "--approved-tag", "spec-p0")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("refused: lock:", result.stderr)
        self.assertEqual(self.lock().read_text(encoding="utf-8"), "other\n")
        self.assertFalse(self.worker_file("claude-args").exists())


class StartDirectoriesTest(StartCase):
    """The list of the role, as --add-dir."""

    def setUp(self):
        super().setUp()
        self.reference = self.worker_home / "ws" / "ref" / "jig"
        self.approved = self.in_worker("ws/src/jig-dev-approved")
        self.worker_list = [self.in_worker("ws/prompts/jig"), self.in_worker("ws/reviews/jig")]

    def for_session(self, role, account):
        self.replace_in_prompt("jig-impl-demo (implementer,", f"jig-{role}-demo ({account},")
        result = self.start(role=role)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_add_dir_for_the_implementer(self):
        self.reference.mkdir(parents=True)
        self.assertEqual(self.start().returncode, 0)
        self.assertEqual(self.given_dirs(), [*self.worker_list, self.approved])

    def test_add_dir_for_the_spec_writer(self):
        self.reference.mkdir(parents=True)
        self.for_session("spec", "specwriter")
        self.assertEqual(self.given_dirs(), [*self.worker_list, str(self.reference)])

    def test_add_dir_for_a_review_in_the_spec_writers_account(self):
        self.reference.mkdir(parents=True)
        self.for_session("rev", "specwriter")
        self.assertEqual(self.given_dirs(), [*self.worker_list, str(self.reference)])

    def test_add_dir_for_a_review_in_the_implementers_account(self):
        self.reference.mkdir(parents=True)
        self.for_session("rev", "implementer")
        self.assertEqual(self.given_dirs(), [*self.worker_list, self.approved])

    def test_add_dir_leaves_out_a_directory_that_does_not_exist(self):
        self.for_session("spec", "specwriter")
        self.assertEqual(self.given_dirs(), self.worker_list)

    def test_add_dir_values_are_the_worker_accounts_not_the_orchestrators(self):
        (self.home / "ws" / "reviews" / "jig").mkdir(parents=True)
        self.assertEqual(self.start().returncode, 0)
        for path in self.given_dirs():
            self.assertTrue(path.startswith(str(self.worker_home) + "/"), path)
            self.assertTrue(os.path.isdir(path), path)

    def test_add_dir_lists_of_the_module_are_the_lists_of_the_spec(self):
        worker_list = ["~/ws/prompts/p", "~/ws/reviews/p"]
        approved = "~/ws/src/p-dev-approved"
        self.assertEqual(jig_dirs.of_role("spec", "p"), [*worker_list, "~/ws/ref/p"])
        self.assertEqual(jig_dirs.of_role("impl", "p"), [*worker_list, approved])
        self.assertEqual(jig_dirs.of_role("rev", "p"), [*worker_list, approved])
        self.assertEqual(jig_dirs.of_role("orch", "p"),
                         [*worker_list, "/srv/jig/in/p", "/srv/jig/out/p", "/srv/jig/blocks/p"])
        self.assertEqual(jig_dirs.of_role("other", "p"), [])

    def test_add_dir_keeps_the_prompt_unchanged_through_jig_start(self):
        text = self.prompt.read_text(encoding="utf-8")
        self.prompt.write_text(text.replace("PREMISE CHECK:", TRICKY_PROMPT + "PREMISE CHECK:", 1),
                               encoding="utf-8")
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.given_dirs(), [*self.worker_list, self.approved])
        self.assertEqual(self.claude_args()[-5:],
                         ["--model", "claude-opus-5-5", "--name", SESSION,
                          self.prompt.read_text(encoding="utf-8").rstrip("\n")])
        self.assertFalse((self.worker_clone / "injected").exists())

    def test_add_dir_worker_alone_passes_only_what_it_is_given(self):
        result = self.worker_alone("--approved-tag", "spec-p0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.given_dirs(), [])


class StartGroupTest(StartCase):
    """The session's group, from a tmux server that may not hold it."""

    def setUp(self):
        super().setUp()
        self.tricky = self.tmp / "tricky.md"
        self.tricky.write_text(TRICKY_PROMPT, encoding="utf-8")
        self.tee = self.worker_home / "ws" / "reviews" / "jig" / "out" / "demo.txt"

    def own_group(self):
        return grp.getgrgid(pwd.getpwuid(os.getuid()).pw_gid).gr_name

    def group_of_others(self):
        entry = pwd.getpwuid(os.getuid())
        own = os.getgrouplist(entry.pw_name, entry.pw_gid)
        for group in grp.getgrall():
            if group.gr_gid not in own:
                return group.gr_name
        return self.skipTest("this account is a member of every group")

    def start_with_group(self, group, *extra):
        return run([sys.executable, "-c", WORKER_WITH_GROUP, WORKER, group, "jig", "impl", "demo",
                    self.tricky, "--account", "implementer", "--expected", "45",
                    "--model", "claude-haiku-5-5", "--approved-tag", "spec-p0", *extra],
                   env=self.base_env(HOME=str(self.worker_home)))

    def assert_session_ran_with_the_prompt_unchanged(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for(self.worker_file("claude-cwd"))
        args = self.worker_file("claude-args").read_text(encoding="utf-8").split("\0")[:-1]
        self.assertEqual(args, ["--model", "claude-haiku-5-5", "--name", SESSION,
                                TRICKY_PROMPT.rstrip("\n")])
        self.assertEqual(self.worker_file("claude-cwd").read_text(encoding="utf-8").strip(),
                         str(self.worker_clone))
        self.wait_for(self.tee, "double claude output")
        self.assertFalse((self.worker_clone / "injected").exists())

    def test_start_runs_the_session_through_sg_for_a_member(self):
        self.script("sg", SG_DOUBLE)
        group = self.own_group()
        self.assert_session_ran_with_the_prompt_unchanged(self.start_with_group(group))
        given = self.worker_file("sg-args").read_text(encoding="utf-8").split("\0")[:-1]
        self.assertEqual(given[:2], [group, "-c"])
        self.assertEqual(len(given), 3)
        self.assertTrue(self.lock().exists())

    def test_start_keeps_the_prompt_and_a_directory_with_quotes_through_sg(self):
        self.script("sg", SG_DOUBLE)
        odd = "dir with 'single' \"double\" $HOME $(touch injected) `touch injected`"
        (self.worker_home / odd).mkdir()
        result = self.start_with_group(self.own_group(), "--add-dir", f"~/{odd}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for(self.worker_file("claude-cwd"))
        args = self.worker_file("claude-args").read_text(encoding="utf-8").split("\0")[:-1]
        self.assertEqual(args, ["--add-dir", str(self.worker_home / odd),
                                "--model", "claude-haiku-5-5", "--name", SESSION,
                                TRICKY_PROMPT.rstrip("\n")])
        self.assertFalse((self.worker_clone / "injected").exists())

    def test_start_runs_the_session_without_sg_when_the_group_does_not_exist(self):
        self.script("sg", SG_DOUBLE)
        self.assert_session_ran_with_the_prompt_unchanged(self.start_with_group("jig-no-group"))
        self.assertFalse(self.worker_file("sg-args").exists())
        self.assertTrue(self.lock().exists())

    def test_start_runs_the_session_without_sg_for_a_group_of_others(self):
        self.script("sg", SG_DOUBLE)
        group = self.group_of_others()
        self.assert_session_ran_with_the_prompt_unchanged(self.start_with_group(group))
        self.assertFalse(self.worker_file("sg-args").exists())

    def test_start_keeps_the_prompt_through_the_real_sg(self):
        if not shutil.which("sg"):
            self.skipTest("this machine has no sg")
        self.assert_session_ran_with_the_prompt_unchanged(self.start_with_group(self.own_group()))

    def test_start_through_sg_names_the_trust_dialog(self):
        self.script("sg", SG_DOUBLE)
        (self.worker_home / "claude-dialog").write_text(DIALOG, encoding="utf-8")
        result = self.start_with_group(self.own_group())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("folder-trust dialog", result.stderr)
        self.assertTrue(self.lock().exists())

    def test_start_names_the_group_in_one_constant(self):
        text = WORKER.read_text(encoding="utf-8")
        self.assertEqual(text.count('"jig"'), 1)
        self.assertRegex(text, r'(?m)^SESSION_GROUP = "jig"$')


class StartWorkerAloneTest(StartCase):
    def test_start_worker_runs_alone_with_a_one_line_prompt(self):
        prompt = self.tmp / "one-line.md"
        prompt.write_text("say OK, then stop\n", encoding="utf-8")
        result = self.worker_alone("--approved-tag", "spec-p0", prompt=prompt)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.worker_file("ssh-targets").exists())
        self.wait_for(self.worker_file("claude-args"))
        args = self.worker_file("claude-args").read_text(encoding="utf-8").split("\0")[:-1]
        self.assertEqual(args, ["--model", "claude-haiku-5-5", "--name", SESSION,
                                "say OK, then stop"])
        self.assertEqual((self.worker_home / "ws" / "prompts" / "jig" / "demo.md")
                         .read_text(encoding="utf-8"), "say OK, then stop\n")
        self.assertEqual(self.origin_subjects(), ["fixture"])

    def test_start_worker_copies_an_inbox_when_alone(self):
        inbox = self.prompt.parent / "demo"
        inbox.mkdir()
        (inbox / "note.txt").write_text("for the unit\n", encoding="utf-8")
        self.assertEqual(self.worker_alone("--approved-tag", "spec-p0").returncode, 0)
        self.assertTrue((self.worker_home / "ws/prompts/jig/demo/note.txt").is_file())

    def test_start_worker_refuses_its_own_preconditions_alone(self):
        (self.worker_clone / "stray.txt").write_text("stray\n", encoding="utf-8")
        result = self.worker_alone("--approved-tag", "spec-p0")
        self.assert_refused(result, "clean")
        self.assertFalse(self.lock().exists())

    def test_start_worker_refuses_a_role_of_another_account(self):
        result = self.worker_alone("--approved-tag", "spec-p0", role="spec")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused: account:", result.stderr)

    def test_start_worker_refuses_an_implementer_unit_without_a_tag(self):
        self.assert_refused(self.worker_alone(), "tag")

    def test_start_worker_uses_only_the_standard_library(self):
        for line in WORKER.read_text(encoding="utf-8").splitlines():
            if line.startswith(("import ", "from ")):
                self.assertIn(line.split()[1].split(".")[0], sys.stdlib_module_names, line)

    def test_start_worker_agrees_with_the_shared_id_module(self):
        for unit in ("demo", "p0st0", "abcdef", "abcdefg", "Demo", "a-b", "a/b", "../x", ""):
            result = run([WORKER, "jig", "impl", unit, "prompt", "--stage", "check"],
                         env=self.base_env(HOME=str(self.worker_home)))
            self.assertEqual(result.returncode == 2, not jig_ids.is_unit_id(unit), unit)


class StartConfTest(StartCase):
    def test_start_conf_template_holds_the_keys_of_the_spec(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        for key in ("REPO", "BRANCH", "JIG_VERSION", "ORCH_ACCOUNT", "SPEC_ACCOUNT",
                    "IMPL_ACCOUNT", "REV_ACCOUNT"):
            self.assertRegex(text, rf"(?m)^{key}=")

    def test_start_runs_from_a_conf_with_no_pin(self):
        conf = (self.home / ".config" / "jig" / "jig.conf").read_text(encoding="utf-8")
        self.assertNotIn("JIG_VERSION=", conf)
        result = run([JIG, "which", "jig"], env=self.base_env())
        self.assertEqual(result.returncode, 0, result.stderr)


class StartHookTest(StartCase):
    """The worker's -dev clone holds a product clone, and origin may hold the row terms."""

    def setUp(self):
        super().setUp()
        # The -dev clone ignores its nested product clone, as a real one does.
        (self.seed / ".gitignore").write_text(".claude/settings.local.json\n/jig/\n",
                                              encoding="utf-8")
        self.set_terms_row(True)
        self.ok(["git", "-C", self.worker_clone, "pull", "-q", "--ff-only", "origin", "master"])
        self.product = self.git_init(self.worker_clone / "jig")
        (self.product / "README").write_text("the product\n", encoding="utf-8")
        self.git_commit_all(self.product)

    def set_terms_row(self, present):
        """Push a checks.tsv with or without the row terms to origin."""
        rows = ["name\tcommand\taccount\tcap_seconds", "make-check\tmake check\timplementer\t60"]
        if present:
            rows.append("terms\tjig terms check jig\timplementer\t60")
        (self.seed / "checks.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.git_commit_all(self.seed)
        self.ok(["git", "-C", self.seed, "push", "-q", "origin", "master"])

    def use_hooks_path(self):
        self.ok(["git", "-C", self.product, "config", "--local", "core.hooksPath", "hooks"])
        return self.product / "hooks" / "pre-commit"

    def default_hook(self):
        return self.product / ".git" / "hooks" / "pre-commit"

    def exclude_lines(self):
        exclude = self.product / ".git" / "info" / "exclude"
        return exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []

    def assert_jigs_hook(self, path):
        self.assertEqual(path.read_bytes(), HOOK.read_bytes())
        self.assertTrue(os.access(path, os.X_OK))

    def test_start_installs_the_hook_where_git_looks_for_it(self):
        # No hook yet, no hooks path, and the row on origin: jig start writes jig's hook
        # into the git directory, so it needs no exclude line.
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_jigs_hook(self.default_hook())
        self.assertNotIn("/hooks/pre-commit", self.exclude_lines())

    def test_start_installs_the_hook_at_the_hooks_path_and_excludes_it(self):
        # With core.hooksPath, git looks in the work tree: the hook goes there, its path
        # goes into the exclude file once, and the product clone stays clean.
        hook = self.use_hooks_path()
        self.assertEqual(self.start().returncode, 0)
        self.assert_jigs_hook(hook)
        self.assertFalse(self.default_hook().exists())
        self.assertEqual(self.exclude_lines().count("/hooks/pre-commit"), 1)
        self.assertEqual(self.ok(["git", "-C", self.product, "status", "--porcelain"]), "")

    def test_start_replaces_an_older_hook_with_the_marker(self):
        hook = self.use_hooks_path()
        hook.parent.mkdir()
        hook.write_text("#!/bin/sh\n# jig pre-commit hook\nexit 0\n", encoding="utf-8")
        self.assertEqual(self.start().returncode, 0)
        self.assert_jigs_hook(hook)

    def test_start_refuses_another_hook_and_changes_nothing(self):
        # A hook without the marker is the project's own: the check stage names it and
        # stops, before the start line, the lock and the session.
        hook = self.default_hook()
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        result = self.start()
        self.assert_refused(result, "hook")
        self.assertIn(str(hook), result.stderr)
        self.assertFalse(self.lock().exists())
        self.assertEqual(hook.read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n")

    def test_start_refuses_another_hook_also_without_the_row(self):
        self.set_terms_row(False)
        hook = self.use_hooks_path()
        hook.parent.mkdir()
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        result = self.start()
        self.assert_refused(result, "hook")
        self.assertIn(str(hook), result.stderr)
        self.assertFalse(self.lock().exists())
        self.assertTrue(hook.exists())

    def test_start_leaves_a_hook_path_that_the_product_tracks(self):
        # The product keeps its own pre-commit hook in its tree: jig start neither
        # refuses it nor writes over it, and adds no exclude line for it.
        hook = self.use_hooks_path()
        hook.parent.mkdir()
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        self.git_commit_all(self.product)
        self.assertEqual(self.start().returncode, 0)
        self.assertEqual(hook.read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n")
        self.assertEqual(self.exclude_lines().count("/hooks/pre-commit"), 0)

    def test_start_removes_its_hook_without_the_row_and_keeps_the_exclude_line(self):
        # A line of the -dev repo without the term check: jig's hook goes, so that the
        # unit can commit, and the exclude line from an earlier install stays.
        self.set_terms_row(False)
        hook = self.use_hooks_path()
        hook.parent.mkdir()
        shutil.copy2(HOOK, hook)
        exclude = self.product / ".git" / "info" / "exclude"
        exclude.parent.mkdir(exist_ok=True)
        exclude.write_text("/hooks/pre-commit\n", encoding="utf-8")
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(hook.exists())
        self.assertEqual(self.exclude_lines(), ["/hooks/pre-commit"])

    def test_start_worker_alone_installs_the_hook_beside_it(self):
        # Run alone from a jig tree, the worker takes the hook of that tree.
        result = self.worker_alone("--approved-tag", "spec-p0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_jigs_hook(self.default_hook())


if __name__ == "__main__":
    unittest.main()
