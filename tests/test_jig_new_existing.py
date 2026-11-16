"""jig new --existing: a project whose product repo is already on GitHub.

The block of this form is tested in pieces: its functions and steps are cut out of
the rendered block and run with a shell function in place of ssh, which runs each
command in a fixture account home. A local bare repo stands in for GitHub, reached
through the ssh double as the host alias of the deploy key, so nothing leaves the
temporary root.
"""

import hashlib
import os
import re
import shutil
import unittest

from tests.support import ROOT, run
from tests.test_jig_new import GITHUB, HOST, ORCH, OWNER, PLACEHOLDER, PROJECT, JigNewCase

# The URL that every product clone of the block ends with.
PRODUCT_URL = f"github-{PROJECT}:{GITHUB}/{PROJECT}"
# The ssh form of the same repo on GitHub, which the block repoints. GitHub's ssh
# user is git; the block takes any user.
SSH_USER = "git"
SCP_URL = f"{SSH_USER}@github.com:{GITHUB}/{PROJECT}.git"
# Replaces ssh in the cut steps: the command runs in the account's fixture home.
FAKE_SSH = ('ssh() {\n  [ "$1" = -o ] && shift 2\n  local home="$FAKE/accounts/${1%@*}"\n'
            '  shift\n  (cd "$home" && HOME="$home" bash -c "$*")\n}\n')
# Stand in for gh and id in step 1: gh knows the repos listed under $FAKE/gh-repos and
# is logged in; id names the owner.
GH_DOUBLE = """#!/bin/sh
case "$1 $2" in
  "auth status") exit 0 ;;
  "repo view") [ -e "$FAKE/gh-repos/$3" ] && exit 0; exit 1 ;;
esac
echo "gh double: $*" >&2
exit 1
"""
ID_DOUBLE = f"#!/bin/sh\necho {OWNER}\n"


def tree_state(path):
    """Every path under a directory, with the sha256 of each regular file."""
    state = {}
    for top, dirs, files in os.walk(path):
        for name in dirs + files:
            full = os.path.join(top, name)
            digest = None
            if os.path.isfile(full) and not os.path.islink(full):
                with open(full, "rb") as stream:
                    digest = hashlib.sha256(stream.read()).hexdigest()
            state[os.path.relpath(full, path)] = digest
    return state


class JigNewExistingTest(JigNewCase):

    def setUp(self):
        super().setUp()
        self.impl = self.account("implementer")
        # The ssh double reaches the orchestrator's account, which is this test's home.
        (self.fake / "accounts" / ORCH).symlink_to(self.home)
        self.block_bin = self.tmp / "block-bin"
        self.block_bin.mkdir()
        for name, text in (("gh", GH_DOUBLE), ("id", ID_DOUBLE)):
            path = self.block_bin / name
            path.write_text(text, encoding="utf-8")
            path.chmod(0o755)

    def git(self, *args, cwd=None):
        result = run(["git", *args], env=self.env(), cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def made_existing(self):
        result = self.new(PROJECT, "--existing")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def block_text(self):
        return (self.srv / "blocks/demo/new.sh").read_text(encoding="utf-8")

    def make_github(self):
        """A bare repo in the place of the product on GitHub, with two branches and a tag.

        It is reached both as the https URL of the block, here a local directory,
        and as the host alias of the deploy key through the ssh double.
        """
        github = self.tmp / "github"
        bare = github / f"{PROJECT}.git"
        self.git("init", "-q", "--bare", "-b", "master", bare)
        work = self.git_init(self.tmp / "work")
        (work / "README").write_text("product\n", encoding="utf-8")
        self.git_commit_all(work)
        self.git("-C", work, "tag", "-a", "-m", "first", "first")
        self.git("-C", work, "branch", "phase-1")
        self.git("-C", work, "push", "-q", bare, "master", "phase-1", "first")
        alias = self.fake / "accounts" / f"github-{PROJECT}"
        alias.mkdir()
        (alias / GITHUB).symlink_to(github)
        (self.fake / "gh-repos" / GITHUB).mkdir(parents=True)
        (self.fake / "gh-repos" / GITHUB / PROJECT).write_text("", encoding="utf-8")
        self.github = github
        self.bare = bare
        return bare

    def clone_at(self, home, origin):
        """A product clone in an account home, cloned from GitHub, with origin set."""
        path = home / "ws" / "src" / PROJECT
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git("clone", "-q", self.bare, path)
        self.git("-C", path, "remote", "set-url", "origin", origin)
        return path

    def run_block_part(self, body, start='echo "step 1:'):
        """Run the block's header and its functions, then body, on the fixture accounts."""
        text = self.block_text()
        header = text[text.index("set -eu"):text.index(start)]
        overrides = (f"export GIT_SSH_COMMAND={self.ssh} GIT_SSH_VARIANT=simple\n"
                     f"URL={self.github}\nG=(git)\n")
        env = self.env(FAKE=str(self.fake), TMPDIR=str(self.tmp),
                       PATH=f"{self.block_bin}:{self.bin_dir}:{os.environ['PATH']}")
        return run(["bash", "-c", header + FAKE_SSH + overrides + body], env=env)

    def step_one(self):
        text = self.block_text()
        return text[text.index('echo "step 1:'):text.index('echo "step 2:')]

    def test_existing_makes_the_dev_seed_and_no_product_tree(self):
        # A project made for a product on GitHub gets the whole -dev side and the
        # product deploy keys, but no product clone, product seed or hook in the
        # orchestrator's account; the summary says that the block writes nothing
        # into the product.
        result = self.made_existing()
        dev = self.home / "ws/src/demo-dev"
        for name in ("CLAUDE.md", "impl/orch/ORCHESTRATOR.md", "checks.tsv", "terms.txt",
                     "Makefile", ".gitignore"):
            self.assertTrue((dev / name).is_file(), name)
        terms = (dev / "terms.txt").read_text(encoding="utf-8").split()
        for term in (OWNER, HOST, GITHUB):
            self.assertIn(term, terms)
        url = self.git("-C", dev, "remote", "get-url", "origin").strip()
        self.assertEqual(url, f"github-demo-dev:{GITHUB}/demo-dev.git")
        self.assertFalse((self.home / "ws/src/demo").exists())
        self.assertFalse(any(self.home.joinpath("ws/src").glob(".demo-dev.*")))
        for home in (self.home, self.impl):
            self.assertTrue((home / ".ssh/demo_deploy").exists())
            self.assertIn("Host github-demo\n", (home / ".ssh/config").read_text("utf-8"))
        self.assertFalse((self.account("specwriter") / ".ssh/demo_deploy").exists())
        self.assertTrue((self.srv / "checks/demo").is_dir())
        self.assertIn("It writes nothing into the product.", result.stdout)
        self.assertNotIn("made " + str(self.home / "ws/src/demo") + "\n", result.stdout)
        digest = hashlib.sha256((self.srv / "blocks/demo/new.sh").read_bytes()).hexdigest()
        self.assertEqual(result.stdout.splitlines()[-2:],
                         [f"# as {OWNER} on {HOST}", f"jig run demo new {digest[:8]}"])

    def test_existing_writes_no_file_into_product_clones_that_exist(self):
        # The orchestrator and the implementer each hold a clone of the product
        # already. jig new --existing runs past them, and afterwards every path and
        # every file's hash under both clones is as it was.
        self.make_github()
        clones = [self.clone_at(self.home, SCP_URL),
                  self.clone_at(self.impl, str(self.tmp / "old" / "demo.git"))]
        (clones[0] / "notes.txt").write_text("untracked\n", encoding="utf-8")
        before = [tree_state(path) for path in clones]
        self.made_existing()
        self.assertEqual([tree_state(path) for path in clones], before)
        self.assertTrue((self.home / "ws/src/demo-dev/CLAUDE.md").is_file())

    def test_existing_block_creates_and_pushes_only_the_dev_repo(self):
        # The block makes and pushes the -dev repo alone, and gives the product only
        # the read-only keys of the orchestrator and the implementer.
        self.made_existing()
        text = self.block_text()
        self.assertEqual(re.findall(r"gh repo create \S+", text), ['gh repo create "$GH/$P-dev"'])
        pushes = [line for line in text.splitlines() if "push -q" in line]
        self.assertEqual(len(pushes), 1, pushes)
        self.assertIn('"$URL/$P-dev.git"', pushes[0])
        self.assertNotIn("for r in", text)
        keys = [line for line in text.splitlines() if line.startswith("key ")]
        self.assertEqual(keys, ['key "$ORCH" "$P-dev" --allow-write',
                                'key "$SPEC" "$P-dev" --allow-write',
                                'key "$IMPL" "$P-dev" --allow-write',
                                'key "$ORCH" "$P"', 'key "$IMPL" "$P"'])
        self.assertNotIn("core.hooksPath", text)
        self.assertNotIn("pre-commit", text.replace("installs the pre-commit", ""))

    def test_existing_block_is_valid_shell_with_short_lines_and_no_placeholder(self):
        self.made_existing()
        block = self.srv / "blocks/demo/new.sh"
        text = self.block_text()
        lines = text.splitlines()
        self.assertEqual(lines[0], f"# new  as {OWNER} on {HOST} — role: owner")
        self.assertEqual(lines[-1], "# end new")
        self.assertEqual([line for line in lines if len(line) >= 75], [])
        self.assertIsNone(PLACEHOLDER.search(text), PLACEHOLDER.search(text))
        self.assertIn("--- readback new", text)
        self.assertIn("# Readback. Expected:", text)
        for home in (self.home, self.account("specwriter"), self.impl):
            self.assertIn(f"={home}\n", text)
        self.assertEqual(run(["bash", "-n", block]).returncode, 0)
        if shutil.which("shellcheck"):
            checked = run(["shellcheck", "-s", "bash", block])
            self.assertEqual(checked.returncode, 0, checked.stdout)

    def test_clone_part_clones_keeps_and_repoints_and_a_second_run_keeps(self):
        # The orchestrator has no product clone, and the implementer has one whose
        # origin is the GitHub ssh form of the same repo. The first run clones the
        # one and repoints the other, changing only its origin URL; the second run
        # keeps both as they are.
        self.made_existing()
        self.make_github()
        impl = self.clone_at(self.impl, SCP_URL)
        self.git("-C", impl, "config", "user.name", "Kept Name")
        self.git("-C", impl, "checkout", "-q", "phase-1")
        config = self.git("-C", impl, "config", "--local", "--list").splitlines()
        head = self.git("-C", self.bare, "rev-parse", "--short", "master").strip()
        first = self.run_block_part('product "$ORCH"\nproduct "$IMPL"\n')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(first.stdout.splitlines(), [
            f"{ORCH} product cloned, head {head}",
            f"implementer product repointed from {SCP_URL},"
            f" head {head}"])
        orch = self.home / "ws/src/demo"
        for clone in (orch, impl):
            self.assertEqual(self.git("-C", clone, "remote", "get-url", "origin").strip(),
                             PRODUCT_URL)
        self.assertEqual(self.git("-C", orch, "branch", "--show-current"), "master\n")
        self.assertEqual(self.git("-C", impl, "branch", "--show-current"), "phase-1\n")
        changed = self.git("-C", impl, "config", "--local", "--list").splitlines()
        self.assertEqual([line for line in changed if not line.startswith("remote.origin.url=")],
                         [line for line in config if not line.startswith("remote.origin.url=")])
        before = [tree_state(path) for path in (orch, impl)]
        second = self.run_block_part('product "$ORCH"\nproduct "$IMPL"\n')
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(second.stdout.splitlines(), [
            f"{ORCH} product kept, head {head}", f"implementer product kept, head {head}"])
        self.assertEqual([tree_state(path) for path in (orch, impl)], before)

    def test_clone_part_keeps_a_clone_of_its_url_with_git_at_the_end(self):
        # A clone whose origin is the block's URL with .git added is the block's own
        # kind of clone: nothing in it changes.
        self.made_existing()
        self.make_github()
        clone = self.clone_at(self.impl, PRODUCT_URL + ".git")
        before = tree_state(clone)
        result = self.run_block_part('product "$IMPL"\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("implementer product kept, head "))
        self.assertEqual(tree_state(clone), before)

    def local_origin(self):
        """A local bare repo with the same branches and tags as GitHub, and a clone of it.

        GitHub also holds a pull request ref, which a local repo never has, so the
        comparison must leave it out.
        """
        local = self.tmp / "local" / "demo.git"
        self.git("clone", "-q", "--bare", self.bare, local)
        master = self.git("-C", self.bare, "rev-parse", "master").strip()
        self.git("-C", self.bare, "update-ref", "refs/pull/1/head", master)
        clone = self.clone_at(self.impl, str(local))
        return local, clone

    def test_clone_part_repoints_a_clone_of_a_local_repo_that_github_matches(self):
        # The implementer's clone has a local bare repo as its origin, and GitHub holds
        # every branch and tag of it at the same commit. The clone gets the block's
        # URL; the local repo is not changed at all.
        self.made_existing()
        self.make_github()
        local, clone = self.local_origin()
        kept = tree_state(local)
        result = self.run_block_part('product "$IMPL"\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"implementer product repointed from {local},", result.stdout)
        self.assertEqual(self.git("-C", clone, "remote", "get-url", "origin").strip(),
                         PRODUCT_URL)
        self.assertEqual(tree_state(local), kept)

    def test_clone_part_stops_at_another_origin_and_changes_nothing(self):
        # Called on a clone of another repo, the clone part stops the block with one
        # line that names the account and the origin, and leaves the clone alone.
        self.made_existing()
        self.make_github()
        clone = self.clone_at(self.impl, f"github-other:{GITHUB}/other")
        before = tree_state(clone)
        result = self.run_block_part('product "$IMPL"\necho after\n')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            f"new: implementer: ~/ws/src/{PROJECT} has another origin, "
            f"github-other:{GITHUB}/other"])
        self.assertEqual(tree_state(clone), before)

    def run_step_one(self):
        (self.home / ".tmux.conf").write_text("set -g mouse on\n", encoding="utf-8")
        before = self.snapshot()
        result = self.run_block_part(self.step_one())
        self.assertEqual(self.snapshot(), before)
        return result

    def assert_step_one_stops(self, line):
        result = self.run_step_one()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], line)
        self.assertNotIn("step 1 ok", result.stdout)

    def test_step_one_passes_for_an_empty_place_and_a_clone_to_keep(self):
        # The orchestrator has no product clone and the implementer has the block's
        # own kind: step 1 names both places and passes without a change.
        self.made_existing()
        self.make_github()
        self.clone_at(self.impl, PRODUCT_URL)
        result = self.run_step_one()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{ORCH} product place: absent\n"
                      "implementer product place: kept\nstep 1 ok\n", result.stdout)

    def test_step_one_stops_when_the_product_repo_is_missing(self):
        self.made_existing()
        self.make_github()
        (self.fake / "gh-repos" / GITHUB / PROJECT).unlink()
        self.assert_step_one_stops(f"new: no GitHub repo {GITHUB}/{PROJECT}; nothing changed")

    def test_step_one_stops_at_a_clone_of_another_origin(self):
        # The orchestrator's place holds a clone of another repo: step 1 stops with one
        # line that names the orchestrator account and the origin, before any change.
        self.made_existing()
        self.make_github()
        self.clone_at(self.home, f"github-other:{GITHUB}/other")
        self.assert_step_one_stops(
            f"new: {ORCH}: ~/ws/src/{PROJECT} has another origin, "
            f"github-other:{GITHUB}/other; nothing changed")

    def test_step_one_stops_when_the_local_refs_differ_from_github(self):
        # The local repo has a branch that GitHub does not hold: step 1 stops and names
        # that branch, and neither the clone nor the local repo changes.
        self.made_existing()
        self.make_github()
        local, clone = self.local_origin()
        self.git("-C", local, "branch", "topic", "master")
        self.assert_step_one_stops(
            f"new: implementer: refs/heads/topic differs between {local} and GitHub; "
            "nothing changed")
        self.assertEqual(self.git("-C", clone, "remote", "get-url", "origin").strip(),
                         str(local))

    def commit_on_master(self, repo):
        """Make a commit on top of master in the repo, with no branch on it; return it."""
        return self.git("-C", repo, "commit-tree", "master^{tree}", "-p", "master",
                        "-m", "later").strip()

    def test_step_one_stops_at_a_tag_that_only_github_holds(self):
        # GitHub holds a tag that the local repo lacks, so the clone would gain a ref
        # it never had from its origin: step 1 stops and names that tag.
        self.made_existing()
        self.make_github()
        local, clone = self.local_origin()
        master = self.git("-C", self.bare, "rev-parse", "master").strip()
        self.git("-C", self.bare, "update-ref", "refs/tags/github-only", master)
        self.assert_step_one_stops(
            f"new: implementer: refs/tags/github-only differs between {local} and GitHub; "
            "nothing changed")
        self.assertEqual(self.git("-C", clone, "remote", "get-url", "origin").strip(),
                         str(local))

    def test_step_one_stops_at_a_branch_at_another_commit_on_each_side(self):
        # Both sides hold the branch phase-1, but the local repo has it one commit
        # further: step 1 stops and names that branch.
        self.made_existing()
        self.make_github()
        local, clone = self.local_origin()
        self.git("-C", local, "update-ref", "refs/heads/phase-1",
                 self.commit_on_master(local))
        self.assert_step_one_stops(
            f"new: implementer: refs/heads/phase-1 differs between {local} and GitHub; "
            "nothing changed")
        self.assertEqual(self.git("-C", clone, "remote", "get-url", "origin").strip(),
                         str(local))

    def test_clone_part_repoints_when_the_local_head_names_another_branch(self):
        # The branch phase-1 is a commit ahead of master on both sides, and the local
        # repo's HEAD names phase-1 while GitHub's names master. HEAD is no branch of
        # its own, so the refs still match and the clone is repointed.
        self.made_existing()
        self.make_github()
        self.git("-C", self.bare, "update-ref", "refs/heads/phase-1",
                 self.commit_on_master(self.bare))
        local, clone = self.local_origin()
        self.git("-C", local, "symbolic-ref", "HEAD", "refs/heads/phase-1")
        result = self.run_block_part('product "$IMPL"\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"implementer product repointed from {local},", result.stdout)
        self.assertEqual(self.git("-C", clone, "remote", "get-url", "origin").strip(),
                         PRODUCT_URL)

    def test_step_one_stops_at_a_place_that_is_not_a_clone(self):
        self.made_existing()
        self.make_github()
        place = self.impl / "ws/src" / PROJECT
        place.mkdir(parents=True)
        (place / "notes.txt").write_text("keep\n", encoding="utf-8")
        self.assert_step_one_stops(
            f"new: implementer: ~/ws/src/{PROJECT} is not a git clone; nothing changed")

    def test_second_existing_run_changes_nothing(self):
        first = self.made_existing()
        before = self.snapshot()
        second = self.new(PROJECT, "--existing")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("made ", second.stdout)
        self.assertNotIn("The block", second.stdout)
        self.assertEqual(first.stdout.splitlines()[-2:], second.stdout.splitlines()[-2:])

    def test_plain_run_after_an_existing_run_keeps_its_block_and_makes_no_product(self):
        # A project made with --existing, then jig new without the option: the -dev
        # side decides, so no product seed is made, the block stays of the existing
        # form, and only the run line is printed.
        first = self.made_existing()
        block = self.block_text()
        second = self.new(PROJECT)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse((self.home / "ws/src/demo").exists())
        self.assertEqual(self.block_text(), block)
        self.assertNotIn("The block", second.stdout)
        self.assertEqual(first.stdout.splitlines()[-2:], second.stdout.splitlines()[-2:])

    def test_existing_run_after_a_plain_run_keeps_both_clones_and_the_block(self):
        # A project made by jig new without the option, then with it: the seed clones
        # and the block of the first form are kept as they are.
        first = self.made()
        product = tree_state(self.home / "ws/src/demo")
        block = self.block_text()
        second = self.new(PROJECT, "--existing")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(tree_state(self.home / "ws/src/demo"), product)
        self.assertEqual(self.block_text(), block)
        self.assertNotIn("The block", second.stdout)
        self.assertEqual(first.stdout.splitlines()[-2:], second.stdout.splitlines()[-2:])

    def test_a_second_word_other_than_the_option_is_a_usage_error(self):
        before = self.snapshot()
        for words in ((PROJECT, "--exist"), (PROJECT, "--existing", "x"), (PROJECT, "x")):
            result = self.new(*words)
            self.assertEqual(result.returncode, 2, words)
            self.assertIn("usage: jig new <project> [--existing]", result.stderr)
        self.assertIn("cannot test the repo itself", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_docs_say_what_the_option_writes(self):
        # The tool's docstring and the template notes both say that the option
        # writes nothing into the product.
        doc = (ROOT / "libexec" / "jig-new").read_text(encoding="utf-8")
        self.assertIn("usage: jig new <project> [--existing]", doc)
        self.assertIn("jig new cannot test the repository itself", doc)
        readme = (ROOT / "templates" / "new" / "README.md").read_text(encoding="utf-8")
        self.assertIn("block-existing.sh.in", readme)


if __name__ == "__main__":
    unittest.main()
