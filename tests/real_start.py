"""The worker parts of jig start and jig close in the real account: real tmux, real claude.

Run by make real-start, not by make check: it needs this account's conf file,
its -dev clone with a clean tree, and its Claude Code login. The start test
starts one small session, starts it again, and removes only that session and
that lock. The close test leaves a session at the folder-trust dialog and
closes it as a dead unit. The hook test starts a fixture project twice, with and
without the term check on origin, and reads jig's pre-commit hook in its product
clone. The version line test reads a spec tag of a version line from a fixture
prompt with jig start's own readers, and starts the fixture project with it. The
tests use their own test IDs, which no real unit uses.
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

START = ROOT / "libexec" / "jig-start"
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
# A fixture project of the hook test, whose files all go when the test ends.
HOOK_PROJECT = "hookfx"
HOOK_UNIT = "x9hok"
HOOK_SESSION = f"{HOOK_PROJECT}-impl-{HOOK_UNIT}"
HOOK_TAG = "spec-p0"
# The approval tag of the specs of the version line 1.2.
LINE_TAG = "spec-1.2"
HOOK_CONF = HOME / ".config" / "jig" / f"{HOOK_PROJECT}.conf"
HOOK_PROMPTS = HOME / "ws" / "prompts" / HOOK_PROJECT
# Where a fixture case with clones in this account's ~/ws/src puts them: the -dev
# clone, the product clone beside it, and the approved worktree that the start makes.
HOOK_HOME_CLONES = tuple(HOME / "ws" / "src" / name for name in
                         (f"{HOOK_PROJECT}-dev", HOOK_PROJECT, f"{HOOK_PROJECT}-dev-approved"))
HOOK_REVIEWS = HOME / "ws" / "reviews" / HOOK_PROJECT


def load_script(name, path):
    """Load a script of libexec, which has no .py name, as a module."""
    sys.dont_write_bytecode = True
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def load_start_worker():
    return load_script("jig_start_worker", WORKER)


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


def remove_tree(path):
    """Remove a fixture tree; an approved worktree in it is read-only, so it opens first."""
    if path.exists():
        capture("chmod", "-R", "u+w", str(path))
        shutil.rmtree(path, ignore_errors=True)


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


class FixtureProjectCase(unittest.TestCase):
    """A fixture project of this account, which the worker part starts with real tmux and claude.

    The project's -dev clone, its product clone and both bare origins live in a new
    directory under /tmp. The worker reads the project's conf file from this account's
    ~/.config/jig, and puts the prompt and the tee under ~/ws; the test removes all of
    them. The session waits at the folder-trust dialog in the new folder, so the worker
    stops there after the install, and the test ends that session and frees its lock.

    The product clone is inside the -dev clone, the place of the releases before 0.6.
    A case with IN_HOME true puts the two clones side by side in ~/ws/src instead, where
    the role's list of directories names the product clone; it refuses to start when
    one of those places exists, and removes them at its end.
    """

    IN_HOME = False

    def setUp(self):
        places = (HOOK_CONF, HOOK_PROMPTS, HOOK_REVIEWS,
                  *(HOOK_HOME_CLONES if self.IN_HOME else ()))
        for path in places:
            self.assertFalse(path.exists() or path.is_symlink(), f"{path} is not this test's")
        self.assertFalse(session_alive(HOOK_SESSION), f"{HOOK_SESSION} is running")
        self.base = pathlib.Path(tempfile.mkdtemp(prefix="jig-test."))
        self.addCleanup(self.remove_base)
        if self.IN_HOME:
            for path in HOOK_HOME_CLONES:
                self.addCleanup(remove_tree, path)
        self.addCleanup(shutil.rmtree, HOOK_PROMPTS, ignore_errors=True)
        self.addCleanup(shutil.rmtree, HOOK_REVIEWS, ignore_errors=True)
        self.addCleanup(HOOK_CONF.unlink, missing_ok=True)
        self.addCleanup(self.stop_session)
        self.lock_dir = self.base / "lock"
        self.lock_dir.mkdir()
        # The settle time only matters when no dialog shows; the dialog stops it sooner.
        self.env = {**os.environ, "JIG_LOCK_DIR": str(self.lock_dir),
                    "JIG_START_SETTLE": "15"}

        self.dev_origin = dev_origin = self.base / f"{HOOK_PROJECT}-dev.git"
        product_origin = self.base / f"{HOOK_PROJECT}.git"
        self.seed = self.base / "seed"
        if self.IN_HOME:
            self.dev, self.product = HOOK_HOME_CLONES[0], HOOK_HOME_CLONES[1]
        else:
            self.dev = self.base / f"{HOOK_PROJECT}-dev"
            self.product = self.dev / HOOK_PROJECT
        for origin in (dev_origin, product_origin):
            self.git("init", "-q", "--bare", "-b", "master", str(origin))
        self.git("init", "-q", "-b", "master", str(self.seed))
        self.git("-C", str(self.seed), "remote", "add", "origin", str(dev_origin))
        # The -dev clone ignores the local settings that the start writes, and the product.
        (self.seed / ".gitignore").write_text(f".claude/settings.local.json\n/{HOOK_PROJECT}/\n",
                                              encoding="utf-8")
        self.push_checks(terms=True)
        self.git("-C", str(self.seed), "tag", HOOK_TAG)
        self.git("-C", str(self.seed), "push", "-q", "origin", HOOK_TAG)
        self.git("clone", "-q", "-b", "master", str(dev_origin), str(self.dev))

        self.git("init", "-q", "-b", "master", str(self.product))
        (self.product / "README").write_text("the fixture product\n", encoding="utf-8")
        self.git("-C", str(self.product), "add", "-A")
        self.git("-C", str(self.product), "commit", "-q", "--no-verify", "-m", "fixture")
        self.git("-C", str(self.product), "remote", "add", "origin", str(product_origin))
        self.git("-C", str(self.product), "push", "-q", "origin", "master")
        # As make check of a product sets it: the hook path then lies in the work tree.
        self.git("-C", str(self.product), "config", "--local", "core.hooksPath", "hooks")

        HOOK_CONF.parent.mkdir(parents=True, exist_ok=True)
        HOOK_CONF.write_text(f"REPO={self.dev}\nBRANCH=master\n"
                             f"IMPL_ACCOUNT={getpass.getuser()}\n", encoding="utf-8")
        self.prompt = self.base / "prompt.md"
        self.prompt.write_text("Say OK, then stop.\n", encoding="utf-8")

    def git(self, *args):
        result = capture("git", *args)
        self.assertEqual(result.returncode, 0, f"git {' '.join(args)}: {result.stderr}")
        return result.stdout.strip()

    def push_checks(self, terms):
        rows = ["name\tcommand\taccount\tcap_seconds", "make-check\ttrue\timplementer\t60"]
        if terms:
            rows.append(f"terms\tjig terms check {HOOK_PROJECT}\timplementer\t60")
        (self.seed / "checks.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.git("-C", str(self.seed), "add", "-A")
        self.git("-C", str(self.seed), "commit", "-q", "--no-verify", "-m", "fixture checks")
        self.git("-C", str(self.seed), "push", "-q", "origin", "master")

    def remove_base(self):
        remove_tree(self.base)

    def stop_session(self):
        capture("tmux", "kill-session", "-t", f"={HOOK_SESSION}")
        lock = self.lock_dir / f"{HOOK_PROJECT}-{getpass.getuser()}"
        lock.unlink(missing_ok=True)
        lock.with_name(lock.name + ".expected").unlink(missing_ok=True)

    def run_worker(self, *tag_args, add_dirs=()):
        """Run the worker part alone; accept its stop at the folder-trust dialog.

        tag_args are the --tag and --approved-tag options; without them, the phase tag
        that the fixture holds is both. add_dirs are the --add-dir words. Returns the
        command of the session's pane, read before the session ends.
        """
        tag_args = tag_args or ("--approved-tag", HOOK_TAG, "--tag", HOOK_TAG)
        result = capture(str(WORKER), HOOK_PROJECT, "impl", HOOK_UNIT, str(self.prompt),
                         "--expected", EXPECTED, "--model", MODEL, *tag_args, *add_dirs,
                         env=self.env)
        stopped = result.returncode == 1 and "refused: dialog:" in result.stderr
        self.assertTrue(result.returncode == 0 or stopped, result.stderr)
        print(f"worker: {'stopped at the folder-trust dialog' if stopped else 'started'}")
        command = capture("tmux", "display-message", "-p", "-t", f"={HOOK_SESSION}",
                          "#{pane_start_command}").stdout
        self.stop_session()
        self.assertFalse(session_alive(HOOK_SESSION))
        return command

    def hook_path(self):
        named = self.git("-C", str(self.product), "rev-parse", "--git-path", "hooks/pre-commit")
        print(f"git rev-parse --git-path hooks/pre-commit: {named}")
        return self.product / named



class RealHookTest(FixtureProjectCase):
    """jig's pre-commit hook in the product clone of the fixture project."""

    def excluded(self):
        exclude = self.product / ".git" / "info" / "exclude"
        return exclude.read_text(encoding="utf-8").splitlines()

    def test_real_start_installs_and_removes_the_pre_commit_hook(self):
        # With the row terms on origin, the start installs jig's hook at the hooks path
        # and excludes that path; the product clone stays clean.
        self.run_worker()
        hook = self.hook_path()
        self.assertEqual(hook.read_bytes(), (ROOT / "hooks" / "pre-commit").read_bytes())
        self.assertTrue(os.access(hook, os.X_OK))
        line_2 = hook.read_text(encoding="utf-8").splitlines()[1]
        print(f"line 2 of the hook: {line_2}")
        self.assertEqual(line_2, "# jig pre-commit hook")
        self.assertIn("/hooks/pre-commit", self.excluded())
        self.assertEqual(self.git("-C", str(self.product), "status", "--porcelain"), "")

        # Without the row on origin, the next start removes the hook and keeps the line.
        self.push_checks(terms=False)
        self.run_worker()
        hook = self.hook_path()
        print(f"hook after the start without the row: {'present' if hook.exists() else 'absent'}")
        self.assertFalse(hook.exists())
        self.assertIn("/hooks/pre-commit", self.excluded())


class RealSiblingStartTest(FixtureProjectCase):
    """jig start of a fixture project whose two clones are siblings in ~/ws/src."""

    IN_HOME = True

    def test_real_start_gives_the_product_clone_beside_and_installs_the_hook_there(self):
        # The list of directories comes from jig start itself, as it builds the --add-dir
        # words for an implementer unit; the worker expands it in this account. The
        # product clone ~/ws/src/hookfx exists, so the session gets it, and the hook goes
        # into that clone.
        start = load_script("jig_start", START)
        directories = start.jig_dirs.of_role("impl", HOOK_PROJECT)
        print(f"jig start lists for impl: {' '.join(directories)}")
        self.assertIn(f"~/ws/src/{HOOK_PROJECT}", directories)
        command = self.run_worker(add_dirs=[word for directory in directories
                                            for word in ("--add-dir", directory)])
        print(f"the pane's command names the product clone: "
              f"{str(self.product) in command}")
        self.assertIn(f"--add-dir {shlex.quote(str(self.product))} ", command)
        hook = self.hook_path()
        self.assertEqual(hook.read_bytes(), (ROOT / "hooks" / "pre-commit").read_bytes())
        self.assertEqual(self.git("-C", str(self.product), "status", "--porcelain"), "")


class RealVersionLineTagTest(FixtureProjectCase):
    """jig start with the spec tag of a version line on the ROLE line of a prompt.

    jig start reads the tags from the prompt in this account and hands them to the
    worker part, which runs in the worker's account. The test takes the first half from
    jig start itself, so that a change to how it reads the ROLE line shows here, and runs
    the second half for real on the fixture project.
    """

    def test_real_start_puts_the_approved_worktree_at_a_version_line_tag(self):
        # The line's spec comes in a commit after the phase tag, so the approved worktree
        # can only reach it through the line's own tag.
        (self.seed / "specs").mkdir()
        (self.seed / "specs" / "spec.md").write_text("the spec of the line\n", encoding="utf-8")
        self.git("-C", str(self.seed), "add", "-A")
        self.git("-C", str(self.seed), "commit", "-q", "--no-verify", "-m", "line spec")
        self.git("-C", str(self.seed), "push", "-q", "origin", "master")
        self.git("-C", str(self.seed), "tag", LINE_TAG)
        self.git("-C", str(self.seed), "push", "-q", "origin", LINE_TAG)
        self.prompt.write_text("ROLE: implementer. Rules: CLAUDE.md, the section Comment\n"
                               f"  standard. Spec tag: {LINE_TAG}. Prohibited sources: none.\n"
                               "STATE: Say OK, then stop.\n", encoding="utf-8")

        start = load_script("jig_start", START)
        role_line = start.role_section(self.prompt.read_text(encoding="utf-8"))
        tags = list(dict.fromkeys(start.ROLE_TAG.findall(role_line)))
        approved = start.SPEC_TAG.search(role_line)
        print(f"jig start reads the tags {tags} and the spec tag "
              f"{approved[1] if approved else None}")
        self.assertEqual(tags, [LINE_TAG])
        self.assertEqual(approved[1], LINE_TAG)

        self.run_worker(*[word for tag in tags for word in ("--tag", tag)],
                        "--approved-tag", approved[1])
        worktree = pathlib.Path(f"{self.dev}-approved")
        at = self.git("-C", str(worktree), "rev-parse", "HEAD")
        named = self.git("-C", str(worktree), "describe", "--tags", "--exact-match")
        print(f"approved worktree of the fixture: at {named} {at}")
        self.assertEqual(named, LINE_TAG)
        self.assertEqual(at, self.git("-C", str(self.dev_origin), "rev-parse",
                                      f"{LINE_TAG}^{{commit}}"))
        self.assertEqual((worktree / "specs" / "spec.md").read_text(encoding="utf-8"),
                         "the spec of the line\n")


if __name__ == "__main__":
    unittest.main()
