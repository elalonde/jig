"""Tests of jig branch: the move of a project to another version line in each account.

The orchestrator's account is this test's HOME and is worked in place. Every other
account is a directory under fake/accounts, which an ssh double enters as that account.
The two origins are bare repositories, built once for the class.
"""

import os
import pathlib
import shutil
import tempfile
import unittest

from tests.support import ROOT, TempTestCase, clean_env, git_ok, run
from tests.units_support import SSH_DOUBLE

BRANCH = ROOT / "libexec" / "jig-branch"
PROJECT = "demo"
LINE = "0.10"

# The ssh double of the other tests, which also stands for an account where ssh gets in
# but the command fails: with the file script-fails in the account's home, it reads its
# input and exits 1 with a message, as python3 would on a broken install.
BRANCH_SSH = SSH_DOUBLE.replace(
    'cd "$home" || exit 255\n',
    'cd "$home" || exit 255\n'
    '[ -f "$home/script-fails" ] && { cat > /dev/null; echo "python3: broken" >&2; exit 1; }\n')


class BranchTest(TempTestCase):
    """A fixture origin for the product and for -dev, with the lines 0.9 and 0.10 and a
    branch side, and a clone of each that a test copies into each account.

    Both origins have the same history. 0.9 is the first commit. 0.10 adds two commits
    to it; the first of them adds the file new.txt. side adds one other commit to 0.9.
    The clones are on 0.9.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base = pathlib.Path(tempfile.mkdtemp(prefix="jig-test."))
        cls.addClassCleanup(shutil.rmtree, cls.base, ignore_errors=True)
        env = clean_env(HOME=str(cls.base))
        work = cls.base / "work"
        git_ok(env, "init", "-q", "-b", "0.9", work)
        (work / "file.txt").write_text("first\n")
        git_ok(env, "-C", work, "add", "-A")
        git_ok(env, "-C", work, "commit", "-q", "-m", "first")
        git_ok(env, "-C", work, "switch", "-q", "-c", "side")
        (work / "file.txt").write_text("side\n")
        git_ok(env, "-C", work, "commit", "-q", "-am", "side")
        git_ok(env, "-C", work, "switch", "-q", "-c", LINE, "0.9")
        (work / "new.txt").write_text("from the line\n")
        git_ok(env, "-C", work, "add", "-A")
        git_ok(env, "-C", work, "commit", "-q", "-m", "second")
        (work / "file.txt").write_text("third\n")
        git_ok(env, "-C", work, "commit", "-q", "-am", "third")
        cls.commits = {name: git_ok(env, "-C", work, "rev-parse", ref).strip()
                       for name, ref in (("first", "0.9"), ("side", "side"),
                                         ("second", f"{LINE}~1"), ("third", LINE))}
        cls.origins, cls.templates = {}, {}
        for repo in (f"{PROJECT}-dev", PROJECT):
            bare = cls.base / "origin" / f"{repo}.git"
            git_ok(env, "clone", "-q", "--bare", work, bare)
            clone = cls.base / "template" / repo
            git_ok(env, "clone", "-q", "-b", "0.9", bare, clone)
            cls.origins[repo], cls.templates[repo] = bare, clone

    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake"
        self.locks = self.tmp / "lock"
        self.batches = self.tmp / "batch"
        for folder in (self.fake / "accounts", self.locks, self.batches):
            folder.mkdir(parents=True)
        self.ssh = self.tmp / "ssh-double"
        self.ssh.write_text(BRANCH_SSH, encoding="utf-8")
        self.ssh.chmod(0o755)

    def home_of(self, account):
        return self.home if account == "orchestrator" else self.fake / "accounts" / account

    def conf(self, account, text):
        path = self.home_of(account) / ".config" / "jig" / f"{PROJECT}.conf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def clone(self, account, repo):
        """Copy the template clone of repo into an account and return its path."""
        path = self.home_of(account) / "ws" / "src" / repo
        shutil.copytree(self.templates[repo], path, symlinks=True)
        return path

    def git(self, path, *words):
        return git_ok(self.env(), "-C", path, *words).strip()

    def branch(self, *args):
        return run([BRANCH, *args], env=self.env(
            JIG_SSH=str(self.ssh), JIG_LOCK_DIR=str(self.locks),
            JIG_BATCH_DIR=str(self.batches), FAKE=str(self.fake)), cwd=self.tmp)


class TestStates(BranchTest):
    def test_each_clone_and_conf_file_gets_its_state_and_a_kept_clone_gives_exit_1(self):
        # One run over seven accounts, with no ORCH_ACCOUNT in the orchestrator's conf
        # file, so the orchestrator role is the account "orchestrator". Each clone and
        # each conf file sets up one case. The kept clones of the earlier accounts do
        # not stop the run: the later accounts' clones are still moved or named, and
        # the exit is 1 after the whole readback.
        dev, product = f"{PROJECT}-dev", PROJECT
        first, side = self.commits["first"], self.commits["side"]
        second, third = self.commits["second"], self.commits["third"]
        orchestrator_conf = self.conf("orchestrator", (
            "REPO=x\nBRANCH=0.9\nSPEC_ACCOUNT=specwriter\nIMPL_ACCOUNT=implementer\n"
            "REV_ACCOUNT=checker auditor gone broken ignorer\n"))
        # The orchestrator: a clean -dev clone with no local branch of the line is moved
        # and gets the branch with origin/0.10 as upstream; a product clone with a
        # tracked change is kept, with its untracked file and its stash.
        self.clone("orchestrator", dev)
        kept = self.clone("orchestrator", product)
        (kept / "file.txt").write_text("stashed\n")
        self.git(kept, "stash", "-q")
        (kept / "file.txt").write_text("changed\n")
        (kept / "notes.txt").write_text("mine\n")
        # specwriter: no conf file and no product clone; its -dev clone has a local
        # branch of the line behind origin, which is fast-forwarded.
        behind = self.clone("specwriter", dev)
        self.git(behind, "branch", "-q", LINE, second)
        # implementer: a conf file with no BRANCH line gets one; a detached -dev clone
        # and a product clone with a commit that origin does not have are kept.
        implementer_conf = self.conf("implementer", "REPO=x\nJIG_VERSION=0.9.2\n")
        self.git(self.clone("implementer", dev), "switch", "-q", "--detach")
        ahead = self.clone("implementer", product)
        (ahead / "file.txt").write_text("local\n")
        self.git(ahead, "commit", "-q", "-am", "local")
        # checker: a conf file that holds the line already is not written; a local
        # branch of the line on another branch of origin is not a fast-forward; an
        # untracked file that the switch would overwrite makes git refuse it.
        checker_conf = self.conf("checker", f"REPO=x\nBRANCH={LINE}\n")
        unchanged = checker_conf.stat()
        self.git(self.clone("checker", dev), "branch", "-q", LINE, side)
        blocked = self.clone("checker", product)
        (blocked / "new.txt").write_text("in the way\n")
        # auditor: a clone on the line at origin is the same, also with a tracked
        # change; a clone on the line at an old origin/0.10 whose fetch fails is kept
        # with git's message, not taken as the same; a conf path that is a folder
        # cannot be read, which is a failure, and its value is shown as -.
        auditor_conf = self.home_of("auditor") / ".config" / "jig" / f"{PROJECT}.conf"
        auditor_conf.mkdir(parents=True)
        current = self.clone("auditor", dev)
        self.git(current, "switch", "-q", LINE)
        (current / "file.txt").write_text("edited\n")
        stale = self.clone("auditor", product)
        self.git(stale, "update-ref", f"refs/remotes/origin/{LINE}", second)
        self.git(stale, "switch", "-q", "-c", LINE, second)
        self.git(stale, "remote", "set-url", "origin", str(self.tmp / "no-such-origin"))
        # gone has no account directory, so ssh fails; broken is reached, but the
        # command fails there.
        (self.fake / "accounts" / "broken").mkdir()
        (self.fake / "accounts" / "broken" / "script-fails").write_text("")
        # ignorer, the last account, after the failures above: an untracked new.txt
        # that the clone's own exclude file ignores, where the line tracks new.txt.
        # git would overwrite an ignored file without a word, so the switch must be
        # refused as for any untracked file, and the file kept. The clean product
        # clone after it is still moved.
        ignorer_conf = self.conf("ignorer", "REPO=x\n")
        ignored = self.clone("ignorer", dev)
        (ignored / ".git" / "info" / "exclude").write_text("new.txt\n")
        (ignored / "new.txt").write_text("ignored but mine\n")
        self.clone("ignorer", product)

        result = self.branch(PROJECT, LINE)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        home, fake = self.home, self.fake / "accounts"
        expected_clones = [
            ("orchestrator", home / "ws/src" / dev, LINE, third, "moved"),
            ("orchestrator", home / "ws/src" / product, "0.9", first, "kept: tracked changes"),
            ("specwriter", fake / "specwriter/ws/src" / dev, LINE, third, "moved"),
            ("specwriter", fake / "specwriter/ws/src" / product, "-", None, "missing"),
            ("implementer", fake / "implementer/ws/src" / dev, "-", first,
             "kept: detached HEAD"),
            ("implementer", fake / "implementer/ws/src" / product, "0.9",
             self.git(ahead, "rev-parse", "HEAD"), "kept: local commits"),
            ("checker", fake / "checker/ws/src" / dev, "0.9", first, "kept: not a fast-forward"),
            ("checker", fake / "checker/ws/src" / product, "0.9", first,
             "kept: error: The following untracked working tree files would be "
             "overwritten by checkout:"),
            ("auditor", fake / "auditor/ws/src" / dev, LINE, third, "same"),
            ("auditor", fake / "auditor/ws/src" / product, LINE, second,
             f"kept: fatal: '{self.tmp / 'no-such-origin'}' does not appear to be a "
             "git repository"),
        ]
        shown = []
        for account, path, branch, head, state in expected_clones:
            head_text = head[:12] if head else "-"
            shown.append(f"clone {account} {path} {branch} {head_text} {state}")
        for account in ("gone", "broken"):
            reason = ("cannot reach gone" if account == "gone" else
                      "the account script failed in broken: python3: broken")
            for repo in (dev, product):
                shown.append(f"clone {account} ~/ws/src/{repo} - - kept: {reason}")
        shown += [
            f"clone ignorer {fake / 'ignorer/ws/src' / dev} 0.9 {first[:12]} "
            "kept: error: The following untracked working tree files would be "
            "overwritten by checkout:",
            f"clone ignorer {fake / 'ignorer/ws/src' / product} {LINE} {third[:12]} moved",
        ]
        shown += [
            f"conf orchestrator {orchestrator_conf} BRANCH={LINE} set",
            f"conf specwriter {fake / 'specwriter/.config/jig' / (PROJECT + '.conf')} "
            "BRANCH=- failed: cannot read: No such file or directory",
            f"conf implementer {implementer_conf} BRANCH={LINE} set",
            f"conf checker {checker_conf} BRANCH={LINE} same",
            f"conf auditor {auditor_conf} BRANCH=- failed: cannot read: Is a directory",
            f"conf gone ~/.config/jig/{PROJECT}.conf BRANCH=- failed: cannot reach gone",
            f"conf broken ~/.config/jig/{PROJECT}.conf BRANCH=- failed: the account "
            "script failed in broken: python3: broken",
            f"conf ignorer {ignorer_conf} BRANCH={LINE} set",
        ]
        self.assertEqual(result.stdout.splitlines(), shown)

        # The moved clone without a local branch got one that tracks origin.
        self.assertEqual(self.git(home / "ws/src" / dev, "rev-parse", "--abbrev-ref",
                                  f"{LINE}@{{upstream}}"), f"origin/{LINE}")
        # The kept clone still has its change, its untracked file and its stash.
        self.assertEqual((kept / "file.txt").read_text(), "changed\n")
        self.assertEqual((kept / "notes.txt").read_text(), "mine\n")
        self.assertEqual(len(self.git(kept, "stash", "list").splitlines()), 1)
        self.assertEqual((blocked / "new.txt").read_text(), "in the way\n")
        self.assertEqual((ignored / "new.txt").read_text(), "ignored but mine\n")
        self.assertEqual((current / "file.txt").read_text(), "edited\n")
        # Conf files: the other lines stay, a file that held the line was not written.
        self.assertEqual(orchestrator_conf.read_text(), (
            f"REPO=x\nBRANCH={LINE}\nSPEC_ACCOUNT=specwriter\nIMPL_ACCOUNT=implementer\n"
            "REV_ACCOUNT=checker auditor gone broken ignorer\n"))
        self.assertEqual(implementer_conf.read_text(),
                         f"REPO=x\nJIG_VERSION=0.9.2\nBRANCH={LINE}\n")
        after = checker_conf.stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns),
                         (unchanged.st_ino, unchanged.st_mtime_ns))

    @unittest.skipIf(os.geteuid() == 0, "root writes in a directory of any mode")
    def test_a_conf_file_with_no_value_that_cannot_be_written_is_a_failure(self):
        # The conf file has no BRANCH line and is in a folder that cannot be written:
        # its line shows BRANCH=- and the failure, the clones still move, the file is as
        # it was, and the exit is 1.
        conf = self.conf("orchestrator", "REPO=x\n")
        for repo in (f"{PROJECT}-dev", PROJECT):
            self.clone("orchestrator", repo)
        conf.parent.chmod(0o500)
        self.addCleanup(conf.parent.chmod, 0o700)
        result = self.branch(PROJECT, LINE)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        states = [text.rsplit(" ", 1)[1] for text in result.stdout.splitlines()[:2]]
        self.assertEqual(states, ["moved", "moved"])
        self.assertEqual(result.stdout.splitlines()[2],
                         f"conf orchestrator {conf} BRANCH=- failed: cannot write")
        self.assertEqual(conf.read_text(), "REPO=x\n")

    def test_a_second_run_after_a_clean_move_changes_nothing_and_exits_0(self):
        # Three accounts with clean clones and conf files on the old line, and no
        # product clone for specwriter. The first run moves and sets everything and
        # exits 0; the second writes no file, moves no clone, says same on each line
        # that is not missing, and exits 0.
        dev, product = f"{PROJECT}-dev", PROJECT
        confs = [self.conf("orchestrator", "ORCH_ACCOUNT=orchestrator\nBRANCH=0.9\n"
                           "SPEC_ACCOUNT=specwriter\nIMPL_ACCOUNT=implementer\n"),
                 self.conf("specwriter", "BRANCH=0.9\n"),
                 self.conf("implementer", "BRANCH=0.9\n")]
        clones = [self.clone("orchestrator", dev), self.clone("orchestrator", product),
                  self.clone("specwriter", dev), self.clone("implementer", dev),
                  self.clone("implementer", product)]
        first = self.branch(PROJECT, LINE)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        states = [text.rsplit(" ", 1)[1] for text in first.stdout.splitlines()]
        self.assertEqual(states, ["moved", "moved", "moved", "missing", "moved", "moved",
                                  "set", "set", "set"])
        before = [(path.stat().st_ino, path.stat().st_mtime_ns) for path in confs]
        heads = [self.git(path, "rev-parse", "HEAD") for path in clones]
        second = self.branch(PROJECT, LINE)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        for text in second.stdout.splitlines():
            self.assertTrue(text.endswith((" same", " missing")), text)
        self.assertEqual(sum(text.endswith(" missing") for text in
                             second.stdout.splitlines()), 1)
        self.assertEqual([(path.stat().st_ino, path.stat().st_mtime_ns) for path in confs],
                         before)
        self.assertEqual([self.git(path, "rev-parse", "HEAD") for path in clones], heads)
        self.assertEqual({self.git(path, "symbolic-ref", "--short", "HEAD")
                          for path in clones}, {LINE})


class TestRefusals(BranchTest):
    def test_each_refusal_changes_nothing(self):
        # Each case alone: a lock of a role account, a running batch, a line that
        # origin of either repository does not have, and no clone of a repository in
        # any role account. Each exits 1 with one line, and leaves every conf file and
        # clone as it was. A line that is not X.Y is a usage error.
        dev, product = f"{PROJECT}-dev", PROJECT
        conf = self.conf("orchestrator", "BRANCH=0.9\nSPEC_ACCOUNT=specwriter\n")
        worker_conf = self.conf("specwriter", "BRANCH=0.9\n")
        clones = {dev: self.clone("orchestrator", dev),
                  product: self.clone("specwriter", product)}

        def unchanged():
            self.assertEqual(conf.read_text(), "BRANCH=0.9\nSPEC_ACCOUNT=specwriter\n")
            self.assertEqual(worker_conf.read_text(), "BRANCH=0.9\n")
            for path in clones.values():
                self.assertEqual(self.git(path, "symbolic-ref", "--short", "HEAD"), "0.9")

        lock = self.locks / f"{PROJECT}-specwriter"
        batch = self.batches / PROJECT
        cases = [("lock", lambda: lock.write_text("x\n"), lock.unlink, LINE,
                  f"{lock} exists"),
                 ("batch", lambda: batch.write_text("1/2 a1\n"), batch.unlink, LINE,
                  f"{batch} exists"),
                 ("line", None, None, "0.11", f"origin of {dev} has no branch 0.11")]
        for name, make, undo, line, reason in cases:
            with self.subTest(case=name):
                if make:
                    make()
                result = self.branch(PROJECT, line)
                if undo:
                    undo()
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(result.stdout, "")
                self.assertIn(reason, result.stderr)
                self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
                unchanged()
        with self.subTest(case="product line"):
            # Only the product origin lacks the line.
            git_ok(self.env(), "-C", self.origins[product], "branch", "-q", "0.12", "0.9")
            git_ok(self.env(), "-C", self.origins[dev], "branch", "-q", "0.13", "0.9")
            self.addCleanup(git_ok, self.env(), "-C", self.origins[product], "branch",
                            "-q", "-D", "0.12")
            self.addCleanup(git_ok, self.env(), "-C", self.origins[dev], "branch",
                            "-q", "-D", "0.13")
            result = self.branch(PROJECT, "0.13")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(f"origin of {product} has no branch 0.13", result.stderr)
            unchanged()
        with self.subTest(case="no clone"):
            hidden = clones[product].with_name("elsewhere")
            clones[product].rename(hidden)
            result = self.branch(PROJECT, LINE)
            hidden.rename(clones[product])
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(f"no role account has a clone ~/ws/src/{product}", result.stderr)
            unchanged()
        for word in ("10", "0.10.0", "v0.10", "0.x", ""):
            with self.subTest(usage=word):
                result = self.branch(PROJECT, word)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("usage: jig branch", result.stderr)
                unchanged()


if __name__ == "__main__":
    unittest.main()
