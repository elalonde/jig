import hashlib
import http.server
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import pwd
import shlex
import shutil
import socket
import sys
import threading
import time
import unittest
import urllib.parse

from tests.support import ROOT, TempTestCase, git_ok, run
from tests.test_ci_green import SUCCESS, FakeApi, loopback_works

RELEASE = ROOT / "libexec" / "jig-release"
ACCOUNT = pwd.getpwuid(os.getuid()).pw_name
PROJECT = "demo"
LINE = "0.7"
TOKEN = "planted-token-value"
TERM = "zorblaxian"
OTHER_ACCOUNT = "spec-fixture-account"

# The module itself, for the parts that are pure functions of git state.
_loader = importlib.machinery.SourceFileLoader("jig_release", str(RELEASE))
_spec = importlib.util.spec_from_loader("jig_release", _loader)
release = importlib.util.module_from_spec(_spec)
_loader.exec_module(release)

# The four release targets of a product, made to run in moments. check fails when the
# tree holds a file named break-check, so that a test can break the clean-clone proof.
# check also stands in for lint A: in a git clone it needs the private terms file that
# LINT_TERMS names, and in the unpacked source archive, which is no clone, it must pass
# with no such file named at all.
MAKEFILE = """\
.PHONY: check version-bump package package-check source-archive
check:
\ttest ! -e break-check
\tif [ -e .git ]; then test -r "$$LINT_TERMS"; else test -z "$$LINT_TERMS"; fi
version-bump:
\tprintf '%s\\n' "$(VERSION)" > VERSION
package:
\tmkdir -p dist && git archive --format=tar.gz -o dist/demo-$$(cat VERSION).tar.gz HEAD
package-check:
\ttar -tzf dist/demo-$$(cat VERSION).tar.gz README.md > /dev/null
source-archive:
\tmkdir -p dist && git archive --format=tar.gz --prefix=demo-$$(cat VERSION)/ \\
\t\t-o dist/demo-$$(cat VERSION)-src.tar.gz HEAD
"""

CHANGELOG = """\
# Change log

Entries, newest first.

## 0.6.0

What changed: an older entry.
"""

ENTRY = """\
What changed: a new command.

Files and formats: one new file.

Migration: none.

Requested by: the owner.
"""

# The package rule of MAKEFILE, and one that also packs a file the build writes, so
# that this file is in the package and in no file of the tree. The private term is
# joined from two halves by printf, so that the Makefile itself does not hold it.
PACKAGE_RULE = ("package:\n\tmkdir -p dist && git archive --format=tar.gz "
                "-o dist/demo-$$(cat VERSION).tar.gz HEAD\n")
BUILT_FILE_RULE = (f"package:\n\tmkdir -p dist && printf '%s%s\\n' {TERM[:4]} {TERM[4:]} "
                   f"> built.txt && tar -czf dist/demo-$$(cat VERSION).tar.gz README.md "
                   f"built.txt\n")

TRAILER = "Co-Authored-By: Someone <someone@example.com>"

HOSTS = '[[host]]\nname = "ci.example.net"\naddress = "build.test"\nlocal = false\n'

# Stands in for unshare(1): drops its options and runs the command, so the offline build
# runs here without the namespace, which a test host may not allow.
UNSHARE = '#!/bin/sh\nwhile [ "${1#-}" != "$1" ]; do shift; done\nexec "$@"\n'
UNSHARE_REFUSED = "#!/bin/sh\necho 'unshare: unshare failed: Operation not permitted' >&2\nexit 1\n"


def report(unit, change_class):
    """Return a unit report of that class, whose body names another class that must not count."""
    return (f"OK\njig-report: 1\nid: {unit}\nclass: {change_class}\ncommits: none\n"
            f"checks: none\nproofs: P1=0\nstart: x  end: y  context: none\nnext: none\n---\n"
            f"class: major\n")


@unittest.skipUnless(loopback_works(), "no loopback network for the fake API")
class ReleaseCase(TempTestCase):
    """A product with its bare origin, a -dev repo with its own, and the fake jobs API.

    The product origin sits at someowner/somerepo.git, so that ci-green asks the fake API
    about that repository. The -dev repo has master and the line 0.7; one report is on
    master, from before the line, and three on the line.
    """

    def setUp(self):
        super().setUp()
        self.genv = self.env()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.set_unshare(UNSHARE)
        self.srv = self.tmp / "srv"
        (self.srv / "checks" / PROJECT).mkdir(parents=True)
        (self.srv / "hosts.toml").write_text(HOSTS, encoding="utf-8")
        self.tmux_dir = self.tmp / "tmux"
        self.tmux_dir.mkdir()
        self.addCleanup(lambda: run(["tmux", "kill-server"], env=self.tmux_env()))
        self.runs = 0
        self.make_dev()
        self.make_product()
        config = self.home / ".config" / "jig"
        config.mkdir(parents=True)
        (config / f"{PROJECT}.conf").write_text(
            f"REPO={self.dev}\nBRANCH={LINE}\nIMPL_ACCOUNT={ACCOUNT}\n", encoding="utf-8")
        (config / f"{PROJECT}-token").write_text(TOKEN + "\n", encoding="utf-8")
        self.entry = self.tmp / "entry.md"
        self.entry.write_text(ENTRY, encoding="utf-8")
        self.write_copy("recompute", "0", self.dev_head())

        self.api = FakeApi()
        self.api.runs = [{"id": 11, "status": "completed"}]
        self.api.jobs = {11: [SUCCESS]}
        threading.Thread(target=self.api.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(self.api.server_close)
        self.addCleanup(self.api.shutdown)
        self.ci_wait = "20"
        self.extra_env = {}
        self.staging = self.home / "ws" / "reviews" / PROJECT / "release" / f"{LINE}.0"

    def git(self, *args):
        return git_ok(self.genv, *args)

    def commit(self, clone, message="fixture"):
        self.git("-C", clone, "add", "-A")
        self.git("-C", clone, "commit", "-q", "--no-verify", "-m", message)

    def make_dev(self):
        self.dev_origin = self.tmp / "dev-origin.git"
        self.dev = self.tmp / f"{PROJECT}-dev"
        self.git("init", "-q", "--bare", "-b", "master", self.dev_origin)
        self.git("init", "-q", "-b", "master", self.dev)
        self.git("-C", self.dev, "remote", "add", "origin", self.dev_origin)
        rows = ["name\tcommand\taccount\tcap_seconds", f"passes\techo fine\t{ACCOUNT}\t60",
                f"recompute\tjig recompute {PROJECT} testdata\t{OTHER_ACCOUNT}\t600"]
        self.write(self.dev, {"checks.tsv": "\n".join(rows) + "\n",
                              "terms.txt": f"# private terms\n{TERM}\n",
                              "impl/log/old1-report.md": report("old1", "minor")})
        self.commit(self.dev)
        self.git("-C", self.dev, "push", "-q", "origin", "master")
        self.git("-C", self.dev, "checkout", "-q", "-b", LINE)
        for unit, change_class in (("new1", "minor"), ("new2", "patch"), ("new3", "none")):
            self.write(self.dev, {f"impl/log/{unit}-report.md": report(unit, change_class)})
            self.commit(self.dev, f"log: {unit}")
        self.git("-C", self.dev, "push", "-q", "origin", LINE)

    def make_product(self):
        self.origin = self.tmp / "someowner" / "somerepo.git"
        self.git("init", "-q", "--bare", "-b", LINE, self.origin)
        seed = self.tmp / "seed"
        self.git("init", "-q", "-b", LINE, seed)
        self.write(seed, {"Makefile": MAKEFILE, "CHANGELOG.md": CHANGELOG,
                          "README.md": "A product.\n", "LICENSE": "License text.\n",
                          "THIRD-PARTY-NOTICES": "None.\n", "SOURCE-OFFER": "Ask.\n",
                          ".gitignore": "dist/\n"})
        self.commit(seed)
        self.git("-C", seed, "push", "-q", self.origin, LINE)
        self.product = self.tmp / PROJECT
        self.git("clone", "-q", "-b", LINE, self.origin, self.product)
        self.git("-C", self.product, "config", "remote.origin.pushurl", str(self.origin))

    def write(self, root, files):
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def change_product(self, files=(), remove=()):
        """Commit a change to the product's line and push it, as a unit before the release."""
        self.write(self.product, dict(files))
        for name in remove:
            (self.product / name).unlink()
        self.commit(self.product, "Change the product")
        self.git("-C", self.product, "push", "-q", "origin", LINE)

    def set_unshare(self, text):
        path = self.bin / "unshare"
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def dev_head(self):
        return self.git("-C", self.dev, "rev-parse", "HEAD").strip()

    def write_copy(self, name, *lines):
        path = self.srv / "checks" / PROJECT / f"{name}.exit"
        path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        return path

    def tmux_env(self):
        return self.env(TMUX_TMPDIR=str(self.tmux_dir), JIG_SRV=str(self.srv),
                        JIG_GITHUB_API=self.api.url, JIG_CI_POLL="0.1",
                        JIG_RELEASE_CI_WAIT=self.ci_wait,
                        PATH=f"{self.bin}:{os.environ['PATH']}", **self.extra_env)

    def prepare(self, entry=None):
        """Run prepare inside a unit session of the private tmux server; return code, out, err."""
        self.runs += 1
        out = self.tmp / f"run{self.runs}"
        environment = " ".join(f"{key}={shlex.quote(value)}"
                               for key, value in self.tmux_env().items() if key.isidentifier())
        command = [str(RELEASE), PROJECT, "prepare", "--entry", str(entry or self.entry),
                   "--trailer", TRAILER]
        script = self.tmp / f"run{self.runs}.sh"
        script.write_text(
            f"#!/bin/sh\nexport {environment}\ncd {shlex.quote(str(self.dev))}\n"
            f"{shlex.join(command)} > {out}.out 2> {out}.err\n"
            f"echo $? > {out}.part && mv {out}.part {out}.code\n", encoding="utf-8")
        script.chmod(0o755)
        started = run(["tmux", "new-session", "-d", "-s", f"{PROJECT}-impl-r{self.runs}",
                       str(script)], env=self.tmux_env())
        self.assertEqual(started.returncode, 0, started.stderr)
        code = pathlib.Path(f"{out}.code")
        for _ in range(1200):
            if code.exists():
                break
            time.sleep(0.05)
        else:
            self.fail("jig release prepare did not finish in its session")
        return (int(code.read_text(encoding="utf-8")),
                pathlib.Path(f"{out}.out").read_text(encoding="utf-8"),
                pathlib.Path(f"{out}.err").read_text(encoding="utf-8"))

    def origin_head(self):
        return self.git("--git-dir", self.origin, "rev-parse", LINE).strip()

    def assert_failed_at(self, result, step, reason):
        """prepare exits 1 with one line naming the step and the reason, and stages nothing."""
        code, _, err = result
        self.assertEqual(code, 1, result)
        lines = err.splitlines()
        self.assertEqual(len(lines), 1, result)
        self.assertIn(f"prepare failed at {step}: ", lines[0])
        self.assertIn(reason, lines[0])
        self.assertFalse((self.staging / "SHA256SUMS").exists())


class PreparePassTest(ReleaseCase):
    def test_prepare_commits_pushes_and_stages_the_files_with_their_sums(self):
        # A line with no v tag, green CI, a passing implementer row and a recompute row
        # of another account whose copy holds 0: prepare exits 0, the release commit of
        # 0.7.0 is on origin, and the staging directory holds the package, the source
        # archive and a SHA256SUMS that matches them.
        before = self.origin_head()
        result = self.prepare()
        self.assertEqual(result[0], 0, result)
        head = self.origin_head()
        self.assertNotEqual(head, before)
        message = self.git("--git-dir", self.origin, "log", "-1", "--format=%B", head)
        self.assertEqual(message.strip().splitlines(), ["Release 0.7.0", "", TRAILER])
        shown = self.git("--git-dir", self.origin, "show", f"{head}:VERSION")
        self.assertEqual(shown, "0.7.0\n")
        changelog = self.git("--git-dir", self.origin, "show", f"{head}:CHANGELOG.md")
        self.assertIn("Entries, newest first.\n\n## 0.7.0\n\nWhat changed: a new command.",
                      changelog)
        self.assertIn("Requested by: the owner.\n\nUnits: 1 minor, 1 patch, 1 none\n\n## 0.6.0",
                      changelog)
        staged = sorted(path.name for path in self.staging.iterdir())
        self.assertEqual(staged, ["SHA256SUMS", "demo-0.7.0-src.tar.gz", "demo-0.7.0.tar.gz"])
        sums = (self.staging / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        expected = [f"{hashlib.sha256((self.staging / name).read_bytes()).hexdigest()}  {name}"
                    for name in staged[1:]]
        self.assertEqual(sums, expected)
        exit_file = self.home / "ws" / "reviews" / PROJECT / f"r{self.runs}-passes.exit"
        self.assertEqual(exit_file.read_text(encoding="utf-8"), "0\n")
        for number, name in enumerate(release.STEPS, 1):
            self.assertIn(f"prepare {number}/7: {name}", result[1])

    def test_prepare_waits_for_a_running_ci_job_that_turns_green(self):
        # CI still runs when the release commit is pushed and succeeds two seconds later,
        # inside the 20 s allowed: prepare waits and passes.
        self.api.jobs = {11: [{"name": "build", "conclusion": None}]}
        timer = threading.Timer(2, lambda: self.api.jobs.update({11: [SUCCESS]}))
        timer.start()
        self.addCleanup(timer.cancel)
        result = self.prepare()
        self.assertEqual(result[0], 0, result)
        self.assertTrue((self.staging / "SHA256SUMS").exists())

    def test_the_clean_clone_gets_the_dev_terms_file_and_the_offline_build_gets_none(self):
        # The caller's environment names a terms file that does not exist. The product's
        # check passes in the clean clone only with a readable terms file, and in the
        # unpacked source archive only with none named: prepare passes, so the clone got
        # the -dev clone's terms.txt and the offline build got no LINT_TERMS at all.
        self.extra_env = {"LINT_TERMS": str(self.tmp / "no-such-terms.txt")}
        result = self.prepare()
        self.assertEqual(result[0], 0, result)


class PrepareHeavyTest(ReleaseCase):
    """The host allows one heavy row at a time, and prepare runs one."""

    def test_prepare_waits_for_a_held_heavy_slot_and_then_passes(self):
        # The test holds the only slot until prepare prints that it waits for it, so
        # the wait is seen however slowly prepare reaches its heavy row. The heavy row
        # waits in jig run-check, its wait lines show in prepare's output while it
        # waits, and prepare then passes.
        heavy = self.tmp / "heavy"
        heavy.mkdir()
        (self.srv / "host.conf").write_text("HEAVY_SLOTS=1\n", encoding="utf-8")
        self.extra_env = {"JIG_HEAVY_DIR": str(heavy), "JIG_HEAVY_POLL": "0.1"}
        with open(self.dev / "checks.tsv", "a", encoding="utf-8") as stream:
            stream.write(f"build\techo built\t{ACCOUNT}\t60\theavy\n")
        slot = release.jig_checks.Slot(1, release.jig_checks.try_slot(heavy / "1"))
        self.addCleanup(slot.release)
        os.write(slot.descriptor, b"other\tab12\tbuild\tsomeone\t-\t1\n")
        output = self.tmp / f"run{self.runs + 1}.out"
        stop = threading.Event()

        def release_when_waiting():
            # A minute at most, so that a prepare that never prints the line still ends
            # and the assertion below names what is missing.
            deadline = time.monotonic() + 60
            while not stop.is_set() and time.monotonic() < deadline:
                if output.exists() and "every heavy slot is held, waiting" in \
                        output.read_text(encoding="utf-8"):
                    break
                time.sleep(0.05)
            slot.release()

        watcher = threading.Thread(target=release_when_waiting)
        watcher.start()
        self.addCleanup(watcher.join)
        self.addCleanup(stop.set)
        result = self.prepare()
        self.assertEqual(result[0], 0, result)
        self.assertIn("every heavy slot is held, waiting: slot 1: project other, unit ab12, "
                      "row build", result[1])
        self.assertRegex(result[1], r"took heavy slot 1 after [0-9]+ s")
        exit_file = self.home / "ws" / "reviews" / PROJECT / f"r{self.runs}-build.exit"
        self.assertEqual(exit_file.read_text(encoding="utf-8"), "0\n")


class ReadRowsTest(TempTestCase):
    """prepare reads every row by the rule of jig run-check, also one of another account."""

    def rows(self, *lines):
        (self.tmp / "checks.tsv").write_text(
            "\n".join(["name\tcommand\taccount\tcap_seconds", *lines]) + "\n",
            encoding="utf-8")
        return release.read_rows(self.tmp)

    def test_rows_of_four_and_five_fields_are_read(self):
        self.assertEqual(self.rows("a\ttrue\tsomeone\t60", "b\ttrue\tother\t60\theavy",
                                   "c\ttrue\tother\t60\tlight", "d\ttrue\tother\t60\t"),
                         [("a", "someone"), ("b", "other"), ("c", "other"), ("d", "other")])

    def test_a_bad_weight_a_sixth_field_and_a_bad_cap_stop_prepare(self):
        # The cap is checked for a row of every account: a row that jig run-check would
        # refuse stops the release before any row runs.
        for name, rest, words in (("big", "60\thuge", "heavy or light"),
                                  ("six", "60\theavy\tx", "heavy or light"),
                                  ("nocap", "soon", "cap in seconds")):
            with self.subTest(name):
                with self.assertRaises(release.StepFailed) as caught:
                    self.rows(f"{name}\ttrue\tother\t{rest}")
                self.assertIn(f"the row {name} of", str(caught.exception))
                self.assertIn(words, str(caught.exception))


class PrepareRerunTest(ReleaseCase):
    def release_subjects(self):
        log = self.git("--git-dir", self.origin, "log", "--format=%s", LINE)
        return [line for line in log.splitlines() if line == f"Release {LINE}.0"]

    def test_a_rerun_after_a_later_step_failed_passes_on_the_same_release_commit(self):
        # The first run pushes its release commit and stops at the rows, because the copy
        # of the recompute row is missing. Once the copy is back, the second run makes no
        # commit, passes on the commit the first one pushed, and the line holds one
        # release commit.
        copy = self.srv / "checks" / PROJECT / "recompute.exit"
        copy.unlink()
        self.assert_failed_at(self.prepare(), release.STEPS[5], "the row recompute")
        pushed = self.origin_head()
        self.write_copy("recompute", "0", self.dev_head())
        result = self.prepare()
        self.assertEqual(result[0], 0, result)
        self.assertIn("which this run reuses", result[1])
        self.assertEqual(self.origin_head(), pushed)
        self.assertEqual(len(self.release_subjects()), 1)
        self.assertTrue((self.staging / "SHA256SUMS").exists())

    def test_a_rerun_pushes_the_release_commit_whose_first_push_was_refused(self):
        # The origin refuses the first push, so the release commit stays in the clone,
        # one commit ahead of origin. Once the origin accepts pushes again, the second
        # run pushes that same commit and passes.
        before = self.origin_head()
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        self.assert_failed_at(self.prepare(), release.STEPS[0], f"git push origin {LINE} failed")
        made = self.git("-C", self.product, "rev-parse", "HEAD").strip()
        self.assertNotEqual(made, before)
        self.assertEqual(self.origin_head(), before)
        hook.unlink()
        result = self.prepare()
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.origin_head(), made)
        self.assertEqual(len(self.release_subjects()), 1)

    def test_a_rerun_with_a_changed_entry_makes_a_second_release_commit(self):
        # The entry changes between two runs that both stop at the rows: the tree of the
        # second run differs from the first release commit, so a second one is made.
        (self.srv / "checks" / PROJECT / "recompute.exit").unlink()
        self.assert_failed_at(self.prepare(), release.STEPS[5], "the row recompute")
        self.entry.write_text(ENTRY.replace("a new command", "two new commands"),
                              encoding="utf-8")
        self.assert_failed_at(self.prepare(), release.STEPS[5], "the row recompute")
        self.assertEqual(len(self.release_subjects()), 2)


class PrepareFailTest(ReleaseCase):
    def test_prepare_without_a_trailer_exits_2_and_makes_no_commit(self):
        # The release commit must carry its trailer, so a command line without one is a
        # usage error, found before the clone is touched.
        before = self.git("-C", self.product, "rev-parse", "HEAD")
        result = run([RELEASE, PROJECT, "prepare", "--entry", self.entry], env=self.tmux_env())
        self.assertEqual(result.returncode, 2, result)
        self.assertIn("--trailer <line>", result.stderr)
        self.assertEqual(self.git("-C", self.product, "rev-parse", "HEAD"), before)
        self.assertEqual(self.git("-C", self.product, "status", "--porcelain"), "")
        self.assertEqual(self.origin_head(), before.strip())

    def test_a_private_term_in_a_file_that_only_the_package_holds_stops_prepare(self):
        # The product's package rule writes the term into a file at build time and packs
        # it; no file of the tree holds the term. The scan of the unpacked package finds
        # it, and prepare stops at the terms step.
        self.assertIn(PACKAGE_RULE, MAKEFILE)
        self.change_product({"Makefile": MAKEFILE.replace(PACKAGE_RULE, BUILT_FILE_RULE)})
        self.assert_failed_at(self.prepare(), release.STEPS[4], "jig terms check exited 1")
    def test_an_entry_file_without_its_migration_part_fails_before_any_commit(self):
        self.entry.write_text(ENTRY.replace("Migration: none.\n", ""), encoding="utf-8")
        before = self.origin_head()
        self.assert_failed_at(self.prepare(), release.STEPS[0], "'Migration:'")
        self.assertEqual(self.origin_head(), before)
        self.assertEqual(self.git("-C", self.product, "status", "--porcelain"), "")

    def test_a_clone_without_a_push_url_fails_before_any_commit(self):
        self.git("-C", self.product, "config", "--unset", "remote.origin.pushurl")
        before = self.git("-C", self.product, "rev-parse", "HEAD")
        self.assert_failed_at(self.prepare(), release.STEPS[0], "remote.origin.pushurl")
        self.assertEqual(self.git("-C", self.product, "rev-parse", "HEAD"), before)

    def test_a_red_ci_run_on_the_release_commit_stops_prepare(self):
        # The release commit is pushed, but one CI job of it failed: prepare stops at the
        # CI step, and nothing is staged.
        self.api.jobs = {11: [SUCCESS, {"name": "tests", "conclusion": "failure"}]}
        self.assert_failed_at(self.prepare(), release.STEPS[1], "ci-green exited 1")
        self.assertFalse(self.staging.exists())

    def test_a_ci_job_still_running_at_the_cap_stops_prepare(self):
        self.ci_wait = "1"
        self.api.jobs = {11: [{"name": "build", "conclusion": None}]}
        self.assert_failed_at(self.prepare(), release.STEPS[1], "CI not finished")

    def test_a_failing_check_in_the_clean_clone_stops_prepare(self):
        # The pushed tree holds the file that makes the product's own check fail.
        self.change_product({"break-check": "x\n"})
        self.assert_failed_at(self.prepare(), release.STEPS[2], "make check exited")
        self.assertFalse(self.staging.exists())

    def test_an_unshare_that_is_refused_stops_the_offline_build_with_that_reason(self):
        self.set_unshare(UNSHARE_REFUSED)
        self.assert_failed_at(self.prepare(), release.STEPS[2], "unshare is not allowed")
        self.assertFalse(self.staging.exists())

    def test_a_release_commit_without_a_source_offer_stops_prepare(self):
        self.change_product(remove=["SOURCE-OFFER"])
        self.assert_failed_at(self.prepare(), release.STEPS[3], "no file SOURCE-OFFER")

    def test_a_private_term_in_a_staged_file_stops_prepare(self):
        self.change_product({"notes.txt": f"a note about {TERM.upper()}\n"})
        self.assert_failed_at(self.prepare(), release.STEPS[4], "jig terms check exited 1")

    def test_a_failing_implementer_row_stops_prepare(self):
        with open(self.dev / "checks.tsv", "a", encoding="utf-8") as stream:
            stream.write(f"fails\texit 3\t{ACCOUNT}\t60\n")
        self.assert_failed_at(self.prepare(), release.STEPS[5], "the row fails exited 3")

    def test_a_staging_place_that_cannot_be_made_fails_with_one_line(self):
        # The release directory is a file, so the staging directory cannot be made: the
        # disk error is reported as a failed step, not as a traceback.
        release_dir = self.home / "ws" / "reviews" / PROJECT / "release"
        release_dir.parent.mkdir(parents=True)
        release_dir.write_text("in the way\n", encoding="utf-8")
        result = self.prepare()
        self.assertEqual(result[0], 1, result)
        self.assertEqual(len(result[2].splitlines()), 1, result)
        self.assertIn(f"prepare failed at {release.STEPS[0]}: ", result[2])
        self.assertNotIn("Traceback", result[2])

    def test_a_missing_copy_of_the_recompute_row_stops_prepare(self):
        (self.srv / "checks" / PROJECT / "recompute.exit").unlink()
        self.assert_failed_at(self.prepare(), release.STEPS[5], "the row recompute")


class CopyTest(ReleaseCase):
    """The copy of the exit file of a row that another account runs."""

    def problem(self, path):
        return release.copy_problem(path, self.dev, LINE)

    def push_dev(self, files, clone=None):
        clone = clone or self.dev
        self.write(clone, files)
        self.commit(clone)
        self.git("-C", clone, "push", "-q", "origin", f"HEAD:{LINE}")

    def test_a_copy_of_the_head_that_holds_0_stands(self):
        self.assertIsNone(self.problem(self.write_copy("row", "0", self.dev_head())))

    def test_a_copy_followed_only_by_reports_stands(self):
        path = self.write_copy("row", "0", self.dev_head())
        self.push_dev({"impl/log/new4-report.md": report("new4", "patch")})
        self.assertIsNone(self.problem(path))

    def test_a_copy_that_holds_1_fails(self):
        self.assertIn("holds the exit code 1",
                      self.problem(self.write_copy("row", "1", self.dev_head())))

    def test_a_copy_followed_by_a_change_outside_impl_fails(self):
        path = self.write_copy("row", "0", self.dev_head())
        self.push_dev({"checks.tsv": "name\tcommand\taccount\tcap_seconds\n"})
        self.assertIn("changed outside impl/", self.problem(path))

    def test_a_copy_of_a_commit_not_on_the_line_fails(self):
        self.git("-C", self.dev, "checkout", "-q", "-b", "side")
        self.write(self.dev, {"impl/log/side-report.md": "side\n"})
        self.commit(self.dev)
        self.git("-C", self.dev, "push", "-q", "origin", "side")
        path = self.write_copy("row", "0", self.dev_head())
        self.assertIn(f"not on origin/{LINE}", self.problem(path))

    def test_a_copy_whose_second_line_is_not_a_commit_fails(self):
        self.assertIn("is not a commit",
                      self.problem(self.write_copy("row", "0", "not-a-commit")))

    def test_a_missing_copy_fails(self):
        self.assertIn("no copy", self.problem(self.srv / "checks" / PROJECT / "none.exit"))

    def test_a_copy_of_a_commit_only_on_the_dev_origin_stands_after_the_fetch(self):
        # Another account pushed the commit it ran the row on; this clone has not
        # fetched it yet, and prepare fetches before it judges the copy.
        other = self.tmp / "other-dev"
        self.git("clone", "-q", "-b", LINE, self.dev_origin, other)
        self.push_dev({"impl/log/other-report.md": "other\n"}, clone=other)
        commit = self.git("-C", other, "rev-parse", "HEAD").strip()
        self.assertNotEqual(run(["git", "-C", self.dev, "cat-file", "-e", commit],
                                env=self.genv).returncode, 0)
        self.assertIsNone(self.problem(self.write_copy("row", "0", commit)))


class VersionTest(ReleaseCase):
    def test_a_line_with_no_v_tag_gets_its_first_version(self):
        self.assertEqual(release.next_version(self.product, LINE), ("0.7.0", None))

    def test_a_line_with_v_tags_gets_the_next_patch_and_counts_reports_after_the_tag(self):
        # The line has releases 0.7.0 and 0.7.1 and one report after the tag of 0.7.1;
        # a tag of another line does not count.
        for tag in ("v0.7.0", "v0.7.1", "v0.6.9"):
            self.git("-C", self.product, "tag", tag)
            self.git("-C", self.dev, "tag", tag)
        self.push_report("new5", "patch")
        self.git("-C", self.dev, "fetch", "-q", "origin")
        self.assertEqual(release.next_version(self.product, LINE), ("0.7.2", "v0.7.1"))
        self.assertEqual(release.count_classes(self.dev, LINE, "v0.7.1"), {"patch": 1})

    def test_the_newest_tag_of_a_line_is_found_by_its_numbers(self):
        # Tags of the lines 0.9 and 0.10 are both in the history. The line 0.10 takes
        # only its own tags, and 0.9.10 is newer than 0.9.2 on the line 0.9.
        for tag in ("v0.9.2", "v0.10.0"):
            self.git("-C", self.product, "tag", tag)
        self.assertEqual(release.next_version(self.product, "0.10"), ("0.10.1", "v0.10.0"))
        self.assertEqual(release.next_version(self.product, "0.9"), ("0.9.3", "v0.9.2"))
        self.git("-C", self.product, "tag", "v0.9.10")
        self.assertEqual(release.next_version(self.product, "0.9"), ("0.9.11", "v0.9.10"))
        self.assertEqual(release.next_version(self.product, "0.1"), ("0.1.0", None))

    def test_reports_of_a_line_with_no_release_are_those_master_does_not_hold(self):
        self.git("-C", self.dev, "fetch", "-q", "origin")
        self.assertEqual(release.count_classes(self.dev, LINE, None),
                         {"minor": 1, "patch": 1, "none": 1})

    def push_report(self, unit, change_class):
        self.write(self.dev, {f"impl/log/{unit}-report.md": report(unit, change_class)})
        self.commit(self.dev)
        self.git("-C", self.dev, "push", "-q", "origin", LINE)


class ChangelogEntryTest(unittest.TestCase):
    def test_a_new_entry_replaces_the_entry_an_earlier_attempt_left(self):
        # A failed attempt at 0.7.0 already put its entry first; the next attempt's entry
        # takes its place, and the older entries stay as they were.
        old = CHANGELOG.replace("## 0.6.0", "## 0.7.0\n\nWhat changed: first try.\n\n## 0.6.0")
        merged = release.with_entry(old, "0.7.0", "## 0.7.0\n\nWhat changed: second try.\n")
        self.assertEqual(merged, CHANGELOG.replace(
            "## 0.6.0", "## 0.7.0\n\nWhat changed: second try.\n\n## 0.6.0"))

    def test_a_change_log_with_no_entry_gets_the_entry_after_its_title(self):
        merged = release.with_entry("# Change log\n", "0.1.0", "## 0.1.0\n\nText.\n")
        self.assertEqual(merged, "# Change log\n\n## 0.1.0\n\nText.\n")

    def test_an_entry_file_with_a_heading_of_its_own_is_refused(self):
        self.assertIn("heading", release.entry_problem("## 9.9.9\n" + ENTRY))
        self.assertIsNone(release.entry_problem(ENTRY))

    def test_the_units_line_names_major_only_when_there_is_one(self):
        self.assertEqual(release.units_line({"patch": 2}), "Units: 0 minor, 2 patch, 0 none")
        self.assertEqual(release.units_line({"major": 1, "minor": 1}),
                         "Units: 1 major, 1 minor, 0 patch, 0 none")


SLUG = "someowner/somerepo"
TAG = f"v{LINE}.0"
STAGED = {"demo-0.7.0.tar.gz": b"the package\n", "demo-0.7.0-src.tar.gz": b"the source archive\n"}
SOURCE = "demo-0.7.0-src.tar.gz"
PACKAGE = "demo-0.7.0.tar.gz"
BADGE_PASSING = '<svg xmlns="http://www.w3.org/2000/svg"><text>CI</text><text>passing</text></svg>'
BADGE_FAILING = '<svg xmlns="http://www.w3.org/2000/svg"><text>CI</text><text>failing</text></svg>'

# Runs jig release with its upload limit made small, when a base and a block size are
# given, so that a test can watch an upload pass its limit in moments. Each connection
# that the tool opens appends its timeout to the log, in the order of the requests.
LAUNCHER = """\
import importlib.machinery
import importlib.util
import socket
import sys

tool, log, base, block = sys.argv[1:5]
loader = importlib.machinery.SourceFileLoader("jig_release", tool)
module = importlib.util.module_from_spec(importlib.util.spec_from_loader("jig_release", loader))
loader.exec_module(module)
if base:
    module.UPLOAD_BASE = int(base)
    module.UPLOAD_BLOCK = int(block)
connect = socket.create_connection


def logged(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *args, **options):
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(f"{timeout}\\n")
    return connect(address, timeout, *args, **options)


socket.create_connection = logged
sys.exit(module.main(sys.argv[5:]))
"""


class FakeGitHub(http.server.ThreadingHTTPServer):
    """The parts of GitHub that publish and verify use, for the repository someowner/somerepo.

    It serves releases by tag, makes releases and takes uploads with the token only, serves
    the download URLs of the files, the repository, which hides itself from a request with
    no login while it is private, and the badge of the workflow ci.yml. Every request is
    kept with the Authorization header it carried. As on GitHub, an upload with no
    User-Agent header is refused with 403; the User-Agent of each upload is kept in
    upload_agents.

    While private is set, a request with no login finds no release and no download URL,
    as on GitHub. The release asset API serves each file to the token, with the file
    itself or with a redirect to a storage address, as asset_answer says. The storage
    address refuses any request that carries a login, and the runs of ci.yml are served
    from runs. repo_answer turns the answer about the repository into an HTTP error, a
    body that is not JSON, one with no field private, or a redirect.

    Each file of a release has a state, as on GitHub. A file in a state other than
    uploaded cannot be downloaded: each attempt is answered with 404 and kept in
    unready_reads. The release asset API removes a file for the token, or answers the
    removal with the code that delete_answer gives; "gone" removes the file and still
    answers 404, as when it went away between the list and the removal. held_upload
    names a file whose upload is read with no byte taken ("stall") or a little at a
    time ("slow"), until the client gives up or release_held is set. window, when
    given, shrinks the receive buffer of each connection, so that a large upload cannot
    vanish into the buffers of the loopback network while it is held.
    """

    def __init__(self, window=None):
        self.window = window
        super().__init__(("127.0.0.1", 0), FakeGitHubHandler)
        self.releases = {}
        self.files = {}
        self.states = {}
        self.assets = {}
        self.next_asset = 1
        self.private = False
        self.asset_answer = "redirect"
        self.repo_answer = None
        self.runs = []
        self.badge = BADGE_PASSING
        self.uploads_left = None
        self.delete_answer = None
        self.held_upload = None
        self.release_held = threading.Event()
        self.unready_reads = []
        self.requests = []
        self.upload_agents = []

    def server_bind(self):
        if self.window:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.window)
        super().server_bind()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def make_release(self, tag):
        number = len(self.releases) + 1
        self.releases[tag] = {
            "id": number, "tag_name": tag, "assets": [],
            "upload_url": f"{self.url}/uploads/repos/{SLUG}/releases/{number}/assets{{?name,label}}"}
        return self.releases[tag]

    def add_file(self, tag, name, data, state="uploaded", size=None):
        """Add a file to a release and return its asset ID; size is that of data unless given."""
        path = f"/{SLUG}/releases/download/{tag}/{name}"
        self.files[path] = data
        self.states[path] = state
        number = self.next_asset
        self.next_asset += 1
        self.assets[number] = path
        self.releases[tag]["assets"].append({
            "name": name, "id": number, "state": state,
            "size": len(data) if size is None else size,
            "browser_download_url": self.url + path})
        return number

    def remove_file(self, number):
        path = self.assets.pop(number)
        del self.files[path], self.states[path]
        for release in self.releases.values():
            release["assets"] = [asset for asset in release["assets"] if asset["id"] != number]

    def readable(self, path):
        """Whether a file can be downloaded; an attempt on one that cannot is kept."""
        if self.states.get(path) == "uploaded":
            return True
        self.unready_reads.append(path)
        return False

    def posts(self):
        return [path for method, path, _ in self.requests if method == "POST"]

    def deletes(self, since=0):
        return [(path, login) for method, path, login in self.requests[since:]
                if method == "DELETE"]

    def asked(self, part, since=0):
        """Return the logins of the requests since that index whose path holds part."""
        return [login for _, path, login in self.requests[since:] if part in path]


class FakeGitHubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        hub = self.server
        login = self.headers.get("Authorization")
        hub.requests.append(("GET", self.path, login))
        parsed = urllib.parse.urlparse(self.path)
        release_prefix = f"/repos/{SLUG}/releases/tags/"
        asset_prefix = f"/repos/{SLUG}/releases/assets/"
        hidden = hub.private and login is None
        if parsed.path.startswith(release_prefix):
            release = hub.releases.get(parsed.path[len(release_prefix):])
            if release is None or hidden:
                self.send_error(404)
            else:
                self.answer(200, release)
        elif parsed.path.startswith(asset_prefix):
            self.asset(parsed.path[len(asset_prefix):], login)
        elif parsed.path.startswith("/storage/"):
            # Like the storage that GitHub redirects to: a second login is refused.
            path = hub.assets.get(int(parsed.path.split("/")[2]))
            if login is not None:
                self.send_error(400)
            elif not hub.readable(path):
                self.send_error(404)
            else:
                self.answer(200, hub.files[path], "application/octet-stream")
        elif parsed.path == f"/repos/{SLUG}":
            self.repo(hidden)
        elif parsed.path == f"/repos/{SLUG}/actions/workflows/ci.yml/runs":
            if login != f"Bearer {TOKEN}" or \
                    parsed.query != "branch=master&status=completed&per_page=1":
                self.send_error(404)
            else:
                self.answer(200, {"total_count": len(hub.runs), "workflow_runs": hub.runs})
        elif parsed.path in hub.files and not hidden:
            if hub.readable(parsed.path):
                self.answer(200, hub.files[parsed.path], "application/octet-stream")
            else:
                self.send_error(404)
        elif parsed.path == f"/{SLUG}/actions/workflows/ci.yml/badge.svg" \
                and parsed.query == "branch=master":
            self.answer(200, hub.badge.encode(), "image/svg+xml")
        else:
            self.send_error(404)

    def asset(self, number, login):
        hub = self.server
        path = hub.assets.get(int(number)) if number.isdigit() else None
        if path is None or login != f"Bearer {TOKEN}":
            self.send_error(404)
        elif self.headers.get("Accept") != "application/octet-stream":
            # GitHub answers with the asset's description unless the file is asked for.
            self.answer(200, {"id": int(number), "name": path.rsplit("/", 1)[1]})
        elif not hub.readable(path):
            self.send_error(404)
        elif hub.asset_answer == "direct":
            self.answer(200, hub.files[path], "application/octet-stream")
        else:
            self.send_response(302)
            self.send_header("Location", f"{hub.url}/storage/{number}?signature=fixture")
            self.send_header("Content-Length", "0")
            self.end_headers()

    def repo(self, hidden):
        hub = self.server
        if hidden or hub.repo_answer == "error":
            self.send_error(404 if hidden else 500)
        elif hub.repo_answer == "text":
            self.answer(200, b"<html>not json</html>", "text/html")
        elif hub.repo_answer == "no field":
            self.answer(200, {"full_name": SLUG})
        elif hub.repo_answer == "moved":
            self.send_response(301)
            self.send_header("Location", f"{hub.url}/moved/{SLUG}")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.answer(200, {"full_name": SLUG, "private": hub.private})

    def do_POST(self):
        hub = self.server
        login = self.headers.get("Authorization")
        hub.requests.append(("POST", self.path, login))
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.split("/")
        if hub.held_upload and urllib.parse.parse_qs(parsed.query).get("name") == \
                [hub.held_upload[0]]:
            self.hold_upload(hub.held_upload[1])
            return
        data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if login != f"Bearer {TOKEN}":
            self.send_error(401)
            return
        if parsed.path == f"/repos/{SLUG}/releases":
            asked = json.loads(data)
            if asked["tag_name"] in hub.releases:
                self.send_error(422)
                return
            release = hub.make_release(asked["tag_name"])
            release.update(name=asked["name"], body=asked["body"])
            self.answer(201, release)
        elif parts[:5] == ["", "uploads", "repos", *SLUG.split("/")] and parts[-1] == "assets":
            agent = self.headers.get("User-Agent")
            hub.upload_agents.append(agent)
            if not agent:
                self.send_error(403)
                return
            if hub.uploads_left == 0:
                self.send_error(500)
                return
            if hub.uploads_left is not None:
                hub.uploads_left -= 1
            name = urllib.parse.parse_qs(parsed.query)["name"][0]
            release = next(release for release in hub.releases.values()
                           if release["id"] == int(parts[6]))
            if any(asset["name"] == name for asset in release["assets"]):
                self.send_error(422)
                return
            hub.add_file(release["tag_name"], name, data)
            self.answer(201, release["assets"][-1])
        else:
            self.send_error(404)

    def hold_upload(self, how):
        """Take an upload's body slowly or not at all, until the client gives up."""
        hub = self.server
        while not hub.release_held.is_set():
            if how == "stall":
                hub.release_held.wait(0.05)
                continue
            try:
                taken = self.rfile.read1(16384)
            except OSError:
                return
            if not taken:
                return
            time.sleep(0.02)

    def do_DELETE(self):
        hub = self.server
        login = self.headers.get("Authorization")
        hub.requests.append(("DELETE", self.path, login))
        prefix = f"/repos/{SLUG}/releases/assets/"
        number = self.path[len(prefix):] if self.path.startswith(prefix) else ""
        if login != f"Bearer {TOKEN}" or not number.isdigit() \
                or int(number) not in hub.assets:
            self.send_error(404)
        elif hub.delete_answer == "gone":
            hub.remove_file(int(number))
            self.send_error(404)
        elif hub.delete_answer:
            self.send_error(hub.delete_answer)
        else:
            hub.remove_file(int(number))
            self.send_response(204)
            self.end_headers()


class PublishCase(ReleaseCase):
    """The state after a prepare of 0.7.0, and a GitHub that has no release yet.

    The head of the product's line is the commit "Release 0.7.0", whose VERSION and top
    change log entry name 0.7.0; master of the product's origin is the commit before it,
    and master of the -dev origin is the commit before the line. The staging directory
    holds two files and their SHA256SUMS.
    """

    def setUp(self):
        super().setUp()
        self.git("-C", self.product, "push", "-q", "origin", "HEAD:refs/heads/master")
        changelog = CHANGELOG.replace("## 0.6.0", "## 0.7.0\n\nWhat changed: a new command.\n\n"
                                      "## 0.6.0")
        self.write(self.product, {"VERSION": "0.7.0\n", "CHANGELOG.md": changelog})
        self.commit(self.product, "Release 0.7.0\n\nCo-Authored-By: Someone <someone@example.com>")
        self.git("-C", self.product, "push", "-q", "origin", LINE)
        self.release_commit = self.origin_head()
        self.dev_line = self.git("--git-dir", self.dev_origin, "rev-parse", LINE).strip()
        self.staging.mkdir(parents=True)
        sums = ""
        for name, data in sorted(STAGED.items()):
            (self.staging / name).write_bytes(data)
            sums += f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        (self.staging / "SHA256SUMS").write_text(sums, encoding="utf-8")
        self.start_hub()

    def start_hub(self, window=None):
        self.hub = FakeGitHub(window)
        threading.Thread(target=self.hub.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(self.hub.server_close)
        self.addCleanup(self.hub.shutdown)
        self.addCleanup(self.hub.release_held.set)

    def stage(self, name, data):
        """Put other bytes into a staged file, with SHA256SUMS to match."""
        (self.staging / name).write_bytes(data)
        sums = "".join(f"{release.file_hash(self.staging / staged)}  {staged}\n"
                       for staged in sorted(STAGED))
        (self.staging / "SHA256SUMS").write_text(sums, encoding="utf-8")

    def release(self, *args, launcher=None):
        """Run jig release in the -dev clone with the fake GitHub; return code, out, err.

        launcher, when given, is the list of the launcher script and its arguments, which
        run the tool in place of its own entry point.
        """
        command = [sys.executable, *launcher] if launcher else [RELEASE]
        result = run([*command, PROJECT, *args], cwd=self.dev,
                     env=self.env(JIG_GITHUB_API=self.hub.url, JIG_GITHUB_WEB=self.hub.url))
        return result.returncode, result.stdout, result.stderr

    def launcher(self, base="", block=""):
        """Return the launcher's arguments, with the limit made small if base is given,
        and the path of its log of connection timeouts."""
        script = self.tmp / "launch.py"
        script.write_text(LAUNCHER, encoding="utf-8")
        log = self.tmp / "connections.txt"
        return [script, RELEASE, log, base, block], log

    def refs(self, bare):
        return self.git("--git-dir", bare, "for-each-ref", "--format=%(objectname) %(refname)")

    def all_refs(self):
        return (self.refs(self.origin), self.refs(self.dev_origin),
                self.git("-C", self.product, "tag", "--list"),
                self.git("-C", self.dev, "tag", "--list"))

    def tag_target(self, bare):
        return self.git("--git-dir", bare, "rev-parse", f"{TAG}^{{commit}}").strip()

    def master(self, bare):
        return self.git("--git-dir", bare, "rev-parse", "master").strip()

    def staged_bytes(self):
        return {name: (self.staging / name).read_bytes() for name in
                ("SHA256SUMS", *STAGED)}

    def hub_files(self, tag=TAG):
        return {asset["name"]: self.hub.files[urllib.parse.urlparse(
            asset["browser_download_url"]).path] for asset in self.hub.releases[tag]["assets"]}

    def assert_publish_failed_at(self, result, step, reason):
        """publish exits 1 with one line naming the step and the reason."""
        code, _, err = result
        self.assertEqual(code, 1, result)
        lines = err.splitlines()
        self.assertEqual(len(lines), 1, result)
        self.assertIn(f"publish failed at {step}: ", lines[0])
        self.assertIn(reason, lines[0])

    def assert_verify_failed_at(self, step, reason):
        """verify of 0.7.0 exits 1 with one line naming the step and the reason."""
        code, _, err = result = self.release("verify", "0.7.0")
        self.assertEqual(code, 1, result)
        self.assertEqual(len(err.splitlines()), 1, result)
        self.assertIn(f"verify failed at {step}: ", err)
        self.assertIn(reason, err)

    def assert_untouched(self, before):
        """Neither origin, neither clone's tags and GitHub saw any change."""
        self.assertEqual(self.all_refs(), before)
        self.assertEqual(self.hub.posts(), [])


class PublishPassTest(PublishCase):
    def test_publish_tags_releases_reads_back_and_merges_both_repos(self):
        # A staged 0.7.0 on a line that master trails in both repos: publish exits 0,
        # each origin has the annotated tag at its line's head and master at that head,
        # the release holds the staged files and their sums, the readback carried no
        # login, and neither clone's git config changed. The repo is public, and publish
        # says so before its first action.
        configs = [self.git("-C", clone, "config", "--local", "--list")
                   for clone in (self.product, self.dev)]
        result = self.release("publish")
        self.assertEqual(result[0], 0, result)
        out = result[1]
        self.assertLess(out.index(f"the repo {SLUG} is public\n"),
                        out.index("publish 6/11: the tag in the product\n"))
        self.assertIn("files of the release match the staged files, read with no login\n"
                      "jig release: publish 10/11", out)
        self.assertEqual(self.tag_target(self.origin), self.release_commit)
        self.assertEqual(self.tag_target(self.dev_origin), self.dev_line)
        for bare in (self.origin, self.dev_origin):
            self.assertEqual(self.git("--git-dir", bare, "cat-file", "-t", TAG).strip(), "tag")
        self.assertEqual(self.master(self.origin), self.release_commit)
        self.assertEqual(self.master(self.dev_origin), self.dev_line)
        self.assertEqual(self.hub_files(), self.staged_bytes())
        self.assertEqual(self.hub.releases[TAG]["body"].splitlines()[0],
                         "What changed: a new command.")
        readback = [login for method, path, login in self.hub.requests
                    if method == "GET" and (path.endswith(f"/tags/{TAG}") or "/download/" in path)]
        self.assertIn(None, readback)
        downloads = [login for _, path, login in self.hub.requests if "/download/" in path]
        self.assertEqual(set(downloads), {None})
        last_upload = max(number for number, (method, _, _) in enumerate(self.hub.requests)
                          if method == "POST")
        self.assertEqual(set(self.hub.asked("/", last_upload + 1)), {None})
        self.assertEqual(self.hub.asked("/releases/assets/"), [])
        self.assertEqual([self.git("-C", clone, "config", "--local", "--list")
                          for clone in (self.product, self.dev)], configs)
        for number, name in enumerate(("the staged release", "SHA256SUMS of the staged files",
                                       "the release commit",
                                       "the merge to master can fast-forward",
                                       "the visibility"), 1):
            self.assertIn(f"publish {number}/11: {name}\n", out)
        self.assertIn("publish 9/11: the readback\n", out)

    def test_a_first_publish_keeps_its_lines_and_gives_each_upload_the_limit_of_its_file(self):
        # GitHub has no release yet, and the package is staged with 131073 bytes, one
        # more than 128 KiB. publish prints exactly the steps and lines that it printed
        # before a rerun could keep or remove a release file, with no line about a kept
        # release or a kept file, and nothing on stderr. It opens one connection for each
        # request: each upload gets the limit of its own file, 61 s for SHA256SUMS and
        # the source archive and 62 s for the package, and every other request, the
        # reads of the API and of the files and the request that makes the release, gets
        # 60 s. Each of the three uploads names the tool in its User-Agent header.
        self.stage("demo-0.7.0.tar.gz", b"p" * 131073)
        launcher, log = self.launcher()
        result = self.release("publish", launcher=launcher)
        self.assertEqual(result[0], 0, result)
        self.assertEqual(result[2], "")
        timeouts = [float(text) for text in log.read_text(encoding="utf-8").split()]
        expected = []
        for method, path, _ in self.hub.requests:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            if method == "POST" and "/uploads/" in path:
                size = (self.staging / query["name"][0]).stat().st_size
                expected.append(float(release.upload_limit(size)))
            else:
                expected.append(60.0)
        self.assertEqual(timeouts, expected)
        self.assertEqual(sorted(timeout for timeout in timeouts if timeout != 60), [61, 61, 62])
        self.assertEqual(self.hub.upload_agents, ["jig-release"] * 3)
        steps = ("the staged release", "SHA256SUMS of the staged files", "the release commit",
                 "the merge to master can fast-forward", "the visibility",
                 "the tag in the product", "the tag in -dev", "the GitHub release",
                 "the readback", "the merge to master in the product",
                 "the merge to master in -dev")
        said = {
            1: [f"version 0.7.0, files in {self.staging}"],
            3: [f"release commit {self.release_commit}, -dev commit {self.dev_line}"],
            5: [f"the repo {SLUG} is public"],
            6: [f"{TAG} pushed to the origin of the product"],
            7: [f"{TAG} pushed to the origin of -dev"],
            8: [f"made the release {TAG} of {SLUG}", "uploaded SHA256SUMS",
                "uploaded demo-0.7.0-src.tar.gz", "uploaded demo-0.7.0.tar.gz"],
            9: ["the 3 files of the release match the staged files, read with no login"],
            10: [f"master of the product has the tree of {self.release_commit}"],
            11: [f"master of -dev has the tree of {self.dev_line}"],
        }
        expected = []
        for number, name in enumerate(steps, 1):
            expected.append(f"jig release: publish {number}/11: {name}")
            expected += [f"jig release:   {text}" for text in said.get(number, [])]
        expected.append(f"jig release: {PROJECT} 0.7.0 is published: {TAG} at "
                        f"{self.release_commit}")
        self.assertEqual(result[1].splitlines(), expected)

    def test_a_private_repo_publishes_and_verifies_with_the_token(self):
        # The repo is private, so its download URLs and its badge answer no request with
        # no login, and the release asset API redirects to a storage address that
        # refuses the token. publish says the repo is private before its first action,
        # reads each file back through the asset API with the token, follows each
        # redirect with no login, and touches no download URL. A second publish finds
        # every file on the release with the staged hash, read the same way, and
        # uploads nothing. verify then passes when the newest completed run of ci.yml on
        # master succeeded, and does not fail at the visibility; a run that failed fails
        # it with its ID, commit and conclusion in the line, and no run fails it too.
        self.hub.private = True
        self.hub.runs = [{"id": 41, "head_sha": self.release_commit, "status": "completed",
                          "conclusion": "success"}]
        result = self.release("publish")
        self.assertEqual(result[0], 0, result)
        out = result[1]
        self.assertLess(out.index(f"the repo {SLUG} is private\n"),
                        out.index("publish 6/11: the tag in the product\n"))
        self.assertIn("the 3 files of the release match the staged files, read with the "
                      "token\n", out)
        self.assertEqual(self.hub_files(), self.staged_bytes())
        self.assertEqual(self.master(self.origin), self.release_commit)
        self.assertEqual(self.hub.asked("/download/"), [])
        self.assertEqual(set(self.hub.asked("/releases/assets/")), {f"Bearer {TOKEN}"})
        self.assertEqual(set(self.hub.asked("/storage/")), {None})
        self.assertEqual(len(self.hub.asked("/storage/")), 3)
        seen = len(self.hub.requests)
        posts = len(self.hub.posts())
        again = self.release("publish")
        self.assertEqual(again[0], 0, again)
        self.assertEqual(len(self.hub.posts()), posts)
        self.assertEqual(again[1].count("is on the release already, with the staged size and "
                                        "hash\n"), 3)
        self.assertEqual(len(self.hub.asked("/releases/assets/", seen)), 6)
        self.assertEqual(self.hub.asked("/download/", seen), [])
        verified = self.release("verify", "0.7.0")
        self.assertEqual(verified[0], 0, verified)
        self.assertIn(f"the repo {SLUG} is private\n", verified[1])
        self.assertIn("read with the token\n", verified[1])
        self.assertEqual(self.hub.asked("/download/"), [])
        self.assertEqual(self.hub.asked("/badge.svg"), [])
        self.hub.runs[0]["conclusion"] = "failure"
        self.assert_verify_failed_at(
            "the CI state of master",
            f"run 41 of ci.yml on master, at the commit {self.release_commit}, has the "
            f"conclusion failure")
        self.hub.runs = []
        self.assert_verify_failed_at("the CI state of master",
                                     "ci.yml has no completed run on master")

    def test_dry_run_changes_nothing_and_prints_each_action(self):
        # The same state with --dry-run: exit 0, the visibility is read and printed, every
        # action says what it would do, and no ref on either origin or clone moved and
        # GitHub got no write.
        before = self.all_refs()
        result = self.release("publish", "--dry-run")
        self.assertEqual(result[0], 0, result)
        self.assert_untouched(before)
        self.assertIn(f"the repo {SLUG} is public\n", result[1])
        self.assertIn("would read the release v0.7.0 and its files back with no login",
                      result[1])
        self.assertEqual(result[1].count("would make and push the annotated tag"), 2)
        for text in (f"would make the release {TAG}", "would upload SHA256SUMS",
                     "would read the release", "would push"):
            self.assertIn(text, result[1])
        self.assertEqual(result[1].count("would push "), 2)

    def test_a_rerun_after_a_stop_between_the_tag_and_the_upload_acts_only_on_what_is_missing(self):
        # The first run tags both repos, makes the release and uploads one file, then an
        # upload fails. The rerun keeps both tags as they are, makes no second release,
        # uploads the two missing files only, and merges.
        self.hub.uploads_left = 1
        first = self.release("publish")
        self.assert_publish_failed_at(first, "the GitHub release", "HTTP 500")
        self.assertEqual(len(self.hub.releases[TAG]["assets"]), 1)
        self.assertNotEqual(self.master(self.origin), self.release_commit)
        tags = [self.git("--git-dir", bare, "rev-parse", TAG) for bare in
                (self.origin, self.dev_origin)]
        self.hub.uploads_left = None
        seen = len(self.hub.requests)
        second = self.release("publish")
        self.assertEqual(second[0], 0, second)
        self.assertEqual([self.git("--git-dir", bare, "rev-parse", TAG) for bare in
                          (self.origin, self.dev_origin)], tags)
        self.assertEqual(second[1].count(f"{TAG} exists already on the origin"), 2)
        posts = [path for method, path, _ in self.hub.requests[seen:] if method == "POST"]
        self.assertEqual(len(posts), 2, posts)
        self.assertTrue(all("/uploads/" in path for path in posts), posts)
        self.assertEqual(self.hub_files(), self.staged_bytes())
        self.assertEqual(self.master(self.origin), self.release_commit)

    def test_a_rerun_after_a_complete_publish_changes_nothing(self):
        # A second publish of the same release finds every result in place: exit 0, no
        # write to GitHub, and no ref moved.
        self.assertEqual(self.release("publish")[0], 0)
        before = self.all_refs()
        posts = len(self.hub.posts())
        result = self.release("publish")
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.all_refs(), before)
        self.assertEqual(len(self.hub.posts()), posts)
        self.assertIn(f"master of the product is at {self.release_commit} already", result[1])

    def test_a_dev_tag_at_an_earlier_head_of_the_line_still_matches(self):
        # A run that stopped after the tags; then a report commit lands on the -dev line.
        # The rerun keeps the -dev tag at the older head and moves master to the new one.
        self.hub.uploads_left = 0
        self.assert_publish_failed_at(self.release("publish"), "the GitHub release", "HTTP 500")
        self.git("-C", self.dev, "checkout", "-q", LINE)
        self.write(self.dev, {"impl/log/new4-report.md": report("new4", "patch")})
        self.commit(self.dev, "log: new4")
        self.git("-C", self.dev, "push", "-q", "origin", LINE)
        self.hub.uploads_left = None
        result = self.release("publish")
        self.assertEqual(result[0], 0, result)
        self.assertEqual(self.tag_target(self.dev_origin), self.dev_line)
        self.assertEqual(self.master(self.dev_origin), self.dev_head())


class PublishFailTest(PublishCase):
    def test_no_staged_release_stops_publish_at_the_first_check(self):
        # No staging directory for 0.7.0: publish fails at its first check and changes
        # nothing.
        shutil.rmtree(self.staging)
        before = self.all_refs()
        self.assert_publish_failed_at(self.release("publish", "--dry-run"),
                                      "the staged release", "no staged release of 0.7.0")
        self.assert_publish_failed_at(self.release("publish"), "the staged release",
                                      "no staged release of 0.7.0")
        self.assert_untouched(before)

    def test_a_staged_file_that_differs_from_its_sum_stops_publish(self):
        # The package changed after SHA256SUMS was written: publish fails at the sums.
        (self.staging / "demo-0.7.0.tar.gz").write_bytes(b"another package\n")
        before = self.all_refs()
        self.assert_publish_failed_at(self.release("publish"), "SHA256SUMS of the staged files",
                                      "demo-0.7.0.tar.gz does not match its hash")
        self.assert_untouched(before)

    def test_a_staged_file_that_sha256sums_does_not_list_stops_publish(self):
        # A third file in the staging directory that the sums do not name: publish fails.
        (self.staging / "stray.txt").write_text("stray\n", encoding="utf-8")
        self.assert_publish_failed_at(self.release("publish"), "SHA256SUMS of the staged files",
                                      "not listed ['stray.txt']")

    def push_release_commit(self, files):
        """Push another "Release 0.7.0" commit with these files to the product's line."""
        self.write(self.product, files)
        self.commit(self.product, "Release 0.7.0")
        self.git("-C", self.product, "push", "-q", "origin", LINE)

    def test_a_line_head_whose_version_file_names_another_version_stops_publish(self):
        # The head of the line is a release commit whose VERSION reads 0.7.9: publish
        # fails at the release commit.
        self.push_release_commit({"VERSION": "0.7.9\n"})
        before = self.all_refs()
        self.assert_publish_failed_at(self.release("publish"), "the release commit",
                                      "VERSION at")
        self.assert_untouched(before)

    def test_a_line_head_whose_top_change_log_entry_is_another_version_stops_publish(self):
        # The head's change log starts with the entry of 0.6.0: publish fails.
        self.push_release_commit({"CHANGELOG.md": CHANGELOG})
        self.assert_publish_failed_at(self.release("publish"), "the release commit",
                                      "the top entry of CHANGELOG.md")

    def test_a_commit_after_the_release_commit_stops_publish(self):
        # A unit pushed another commit after the release commit: the head is not the
        # commit that prepare proved, so publish fails at the release commit.
        self.change_product({"later.txt": "later\n"})
        self.assert_publish_failed_at(self.release("publish"), "the release commit",
                                      "not 'Release 0.7.0'")

    def test_a_product_clone_without_a_push_url_stops_publish(self):
        # The product clone has no push URL of its own, so a tag push would go to the
        # fetch URL: publish fails at the release commit.
        self.git("-C", self.product, "config", "--unset", "remote.origin.pushurl")
        before = self.all_refs()
        self.assert_publish_failed_at(self.release("publish"), "the release commit",
                                      "remote.origin.pushurl")
        self.assert_untouched(before)

    def diverge_master(self, clone):
        """Push to master a commit off the line, so that master is no longer behind it."""
        side = self.git("-C", clone, "commit-tree", "-p", "refs/remotes/origin/master",
                        "-m", "side", "refs/remotes/origin/master^{tree}").strip()
        self.git("-C", clone, "push", "-q", "origin", f"{side}:refs/heads/master")

    def test_a_product_master_off_the_line_stops_publish_before_any_action(self):
        # Master of the product holds a commit the line does not: publish fails at the
        # merge check before it tags or releases anything.
        self.diverge_master(self.product)
        before = self.all_refs()
        self.assert_publish_failed_at(self.release("publish"),
                                      "the merge to master can fast-forward",
                                      "master of the product")
        self.assert_untouched(before)

    def test_a_dev_master_off_the_line_stops_publish_before_any_action(self):
        # The same for master of -dev.
        self.git("-C", self.dev, "fetch", "-q", "origin")
        self.diverge_master(self.dev)
        before = self.all_refs()
        self.assert_publish_failed_at(self.release("publish"),
                                      "the merge to master can fast-forward", "master of -dev")
        self.assert_untouched(before)

    def test_a_visibility_that_cannot_be_read_stops_publish_before_the_tag(self):
        # The repo is private, and its visibility cannot be read: the API answers with
        # an HTTP error, with a body that is not JSON, or with no field private. Each
        # time publish exits 1 at the visibility with one line that names the repo and
        # the reason, and neither origin got a tag, GitHub got no write and nothing was
        # pushed.
        self.hub.private = True
        before = self.all_refs()
        for answer, reason in (("error", "HTTP 500"), ("text", "did not answer with JSON"),
                               ("no field", "is not true or false")):
            with self.subTest(answer=answer):
                self.hub.repo_answer = answer
                result = self.release("publish")
                self.assert_publish_failed_at(result, "the visibility", reason)
                self.assertIn(f"the visibility of the repo {SLUG}: ", result[2])
                self.assert_untouched(before)

    def test_a_tag_that_exists_at_another_commit_is_refused(self):
        # v0.7.0 is on the product's origin already, at a commit off the line: publish
        # refuses it before the step of the GitHub release, and tags nothing in -dev,
        # makes no release and merges nothing.
        side = self.git("-C", self.product, "commit-tree", "-p", self.release_commit,
                        "-m", "side", f"{self.release_commit}^{{tree}}").strip()
        self.git("-C", self.product, "tag", "-a", TAG, "-m", "elsewhere", side)
        self.git("-C", self.product, "push", "-q", "origin", f"refs/tags/{TAG}")
        self.git("-C", self.product, "tag", "-d", TAG)
        result = self.release("publish")
        self.assert_publish_failed_at(result, "the tag in the product", f"points at {side}")
        self.assertNotIn("the GitHub release", result[1])
        self.assertEqual(self.git("--git-dir", self.dev_origin, "tag", "--list"), "")
        self.assertEqual(self.hub.posts(), [])
        self.assertNotEqual(self.master(self.origin), self.release_commit)

    def test_a_release_file_with_another_hash_is_refused(self):
        # GitHub holds the release already, with a package of other bytes: publish fails
        # at the release, uploads nothing and merges nothing.
        self.hub.make_release(TAG)
        self.hub.add_file(TAG, "demo-0.7.0.tar.gz", b"other bytes\n")
        result = self.release("publish")
        self.assert_publish_failed_at(result, "the GitHub release",
                                      "demo-0.7.0.tar.gz with another hash")
        self.assertEqual(self.hub.posts(), [])
        self.assertNotEqual(self.master(self.origin), self.release_commit)

    def test_a_file_read_back_with_no_login_that_differs_stops_publish(self):
        # Every upload lands, but the download URL serves other bytes: the readback
        # fails and master is not moved.
        original = FakeGitHub.add_file

        def add_changed(hub, tag, name, data):
            original(hub, tag, name, data + b"changed")

        self.hub.add_file = add_changed.__get__(self.hub)
        result = self.release("publish")
        self.assert_publish_failed_at(result, "the readback",
                                      "read with no login, differs from the staged file")
        self.assertNotEqual(self.master(self.origin), self.release_commit)

    def test_a_master_whose_tree_differs_after_the_push_fails_the_merge_check(self):
        # The product's origin moves master back after each push, as a broken server
        # might: the merge check after the fetch sees another tree and fails.
        hook = self.origin / "hooks" / "post-receive"
        hook.write_text("#!/bin/sh\ngit update-ref refs/heads/master "
                        f"{self.release_commit}^\n", encoding="utf-8")
        hook.chmod(0o755)
        result = self.release("publish")
        self.assert_publish_failed_at(result, "the merge to master in the product",
                                      "the merge check")
        self.assertNotEqual(self.master(self.dev_origin), self.dev_line)


class UploadLimitTest(PublishCase):
    def test_an_upload_that_stalls_or_only_crawls_fails_at_the_limit_of_its_file(self):
        # The package is 16 MiB, more than the buffers of the loopback network take in,
        # and the limit is made 1 s for each file. First the server takes no byte of the
        # package's body; then, in a rerun, it reads the body all the time at about
        # 800 KiB/s, below the 16 MiB in 1 s that the limit asks for, so that no single
        # wait comes near the limit. Each time publish exits 1 with the line of a failed
        # upload that names the package, its size and the limit, and ends soon after the
        # limit, because the limit holds for the send of the whole body.
        self.start_hub(window=8192)
        size = 16 << 20
        self.stage(PACKAGE, b"p" * size)
        launcher, _ = self.launcher(base="0", block=str(size))
        for how in ("stall", "slow"):
            with self.subTest(how=how):
                self.hub.held_upload = (PACKAGE, how)
                started = time.monotonic()
                result = self.release("publish", launcher=launcher)
                self.assertLess(time.monotonic() - started, 10)
                self.assert_publish_failed_at(
                    result, "the GitHub release",
                    f"the upload of {PACKAGE} ({size} bytes, limit 1 s) failed: the send "
                    f"of the file took longer than the limit")
                self.assertEqual(len(self.hub.releases[TAG]["assets"]), 2)


class UploadLimitValueTest(unittest.TestCase):
    def test_the_limit_is_60_s_plus_1_s_for_each_started_128_kib(self):
        # The sizes at each edge of a block of 128 KiB, and the source archive of 129404073
        # bytes that once failed at a limit of 60 s.
        for size, limit in ((0, 60), (1, 61), (131072, 61), (131073, 62), (129404073, 1048)):
            with self.subTest(size=size):
                self.assertEqual(release.upload_limit(size), limit)


class RerunTest(PublishCase):
    """A rerun of publish on a release that an earlier run left behind."""

    def failed_upload(self, private):
        """Leave the state of an upload that failed partway, as GitHub shows it.

        A first run tags both repos, makes the release and uploads SHA256SUMS; then the
        upload server answers the upload of the source archive with HTTP 500, which fails
        the run with one line that names the file, its size and its limit of 61 s. GitHub
        then lists that file in the state starter, with some of its bytes. Returns the
        asset ID of that file.
        """
        self.hub.private = private
        self.hub.uploads_left = 1
        result = self.release("publish")
        self.assert_publish_failed_at(
            result, "the GitHub release",
            f"the upload of {SOURCE} ({len(STAGED[SOURCE])} bytes, limit 61 s) failed: POST ")
        self.assertTrue(result[2].rstrip().endswith(": HTTP 500"), result)
        self.hub.uploads_left = None
        return self.hub.add_file(TAG, SOURCE, STAGED[SOURCE][:4], state="starter",
                                 size=len(STAGED[SOURCE]))

    def assert_rerun_removes_the_starter_file_and_uploads_it_again(self, private):
        # A dry run first prints that it would remove the starter file and upload it,
        # and sends no DELETE and no POST. The rerun then keeps both tags, the release and
        # SHA256SUMS, each with its line, removes the starter file with one DELETE with
        # the token and its line, uploads it and the package once each, never asks for
        # the bytes of the starter file, and exits 0 after the readback, with the release
        # equal to the staged files.
        starter = self.failed_upload(private)
        seen = len(self.hub.requests)
        dry = self.release("publish", "--dry-run")
        self.assertEqual(dry[0], 0, dry)
        self.assertIn(f"would remove {SOURCE}\njig release:   would upload {SOURCE}\n", dry[1])
        self.assertEqual([method for method, _, _ in self.hub.requests[seen:]
                          if method not in ("GET", "HEAD")], [])
        seen = len(self.hub.requests)
        result = self.release("publish")
        self.assertEqual(result[0], 0, result)
        out = result[1]
        lines = [f"{TAG} exists already on the origin of the product, at {self.release_commit}",
                 f"{TAG} exists already on the origin of -dev, at {self.dev_line}",
                 f"the release {TAG} exists already",
                 "SHA256SUMS is on the release already, with the staged size and hash",
                 f"removed {SOURCE}, an upload that did not complete (state starter)",
                 f"uploaded {SOURCE}", f"uploaded {PACKAGE}"]
        places = [out.index(f"jig release:   {line}\n") for line in lines]
        self.assertEqual(places, sorted(places))
        self.assertEqual(self.hub.deletes(seen),
                         [(f"/repos/{SLUG}/releases/assets/{starter}", f"Bearer {TOKEN}")])
        uploads = [urllib.parse.parse_qs(urllib.parse.urlparse(path).query)["name"][0]
                   for method, path, _ in self.hub.requests[seen:] if method == "POST"]
        self.assertEqual(uploads, [SOURCE, PACKAGE])
        self.assertEqual(self.hub.unready_reads, [])
        self.assertEqual(self.hub_files(), self.staged_bytes())
        self.assertEqual(self.master(self.origin), self.release_commit)

    def test_a_rerun_on_a_public_repo_replaces_the_file_that_did_not_upload(self):
        self.assert_rerun_removes_the_starter_file_and_uploads_it_again(private=False)

    def test_a_rerun_on_a_private_repo_replaces_the_file_that_did_not_upload(self):
        self.assert_rerun_removes_the_starter_file_and_uploads_it_again(private=True)

    def test_a_kept_file_that_differs_or_a_file_in_another_state_stops_before_any_removal(self):
        # The release holds the package in the state uploaded and the source archive in
        # the state starter. The package has another size, then the staged size and other
        # bytes, then the state open. Each time publish exits 1 at the GitHub release with
        # one line, sends no DELETE and uploads nothing, the package stays on the release,
        # and the bytes of a file in the state open are never asked for.
        self.hub.make_release(TAG)
        package = self.hub.add_file(TAG, PACKAGE, b"x" * (len(STAGED[PACKAGE]) + 1))
        self.hub.add_file(TAG, SOURCE, b"", state="starter", size=len(STAGED[SOURCE]))
        listed = self.hub.releases[TAG]["assets"][0]
        path = self.hub.assets[package]
        for case, data, state, reason in (
                ("size", b"x" * (len(STAGED[PACKAGE]) + 1), "uploaded",
                 f"holds {PACKAGE} with another size than the staged file"),
                ("hash", b"x" * len(STAGED[PACKAGE]), "uploaded",
                 f"holds {PACKAGE} with another hash than the staged file"),
                ("state", STAGED[PACKAGE], "open",
                 f"the release {TAG} holds {PACKAGE} in the state open, not uploaded and "
                 f"not starter")):
            with self.subTest(case=case):
                self.hub.files[path] = data
                self.hub.states[path] = listed["state"] = state
                listed["size"] = len(data)
                result = self.release("publish")
                self.assert_publish_failed_at(result, "the GitHub release", reason)
                self.assertEqual(self.hub.deletes(), [])
                self.assertEqual(self.hub.posts(), [])
                self.assertEqual(self.hub_files()[PACKAGE], data)
        self.assertEqual(self.hub.unready_reads, [])

    def test_a_removal_that_fails_stops_the_step_and_one_that_finds_no_file_goes_on(self):
        # The API answers the removal of the starter file with HTTP 500: publish exits 1
        # with one line that names the file, and uploads nothing. Then it answers 404
        # after the file went away: the rerun says the file was gone already, uploads it
        # and exits 0.
        self.failed_upload(private=False)
        posts = len(self.hub.posts())
        self.hub.delete_answer = 500
        self.assert_publish_failed_at(
            self.release("publish"), "the GitHub release",
            f"the removal of {SOURCE}, an upload that did not complete, failed: DELETE ")
        self.assertEqual(len(self.hub.posts()), posts)
        self.hub.delete_answer = "gone"
        result = self.release("publish")
        self.assertEqual(result[0], 0, result)
        self.assertIn(f"{SOURCE} was gone already, an upload that did not complete "
                      f"(state starter)\n", result[1])
        self.assertNotIn("removed ", result[1])
        self.assertEqual(self.hub_files(), self.staged_bytes())


class ReleaseFilesTest(unittest.TestCase):
    def test_a_file_list_without_a_state_or_a_size_or_with_a_name_twice_is_refused(self):
        # One entry lacks its state, one its size, and two entries share a name: each
        # fails the step, so that no check of a rerun runs on a file it cannot judge.
        whole = {"name": PACKAGE, "id": 1, "state": "uploaded", "size": 3,
                 "browser_download_url": "http://127.0.0.1/file"}
        for case, assets, reason in (
                ("no state", [{**whole, "state": None}], "no name, asset ID, state"),
                ("no size", [{key: value for key, value in whole.items() if key != "size"}],
                 "no name, asset ID, state"),
                ("twice", [whole, {**whole, "id": 2}], f"names {PACKAGE} more than once")):
            for private in (False, True):
                with self.subTest(case=case, private=private):
                    with self.assertRaisesRegex(release.StepFailed, reason):
                        release.release_files({"assets": assets}, private)

    def test_the_files_come_in_the_order_of_their_names(self):
        # The list names the package before SHA256SUMS; the files come back by name.
        listed = [{"name": name, "id": number, "state": "uploaded", "size": 1,
                   "browser_download_url": f"http://127.0.0.1/{name}"}
                  for number, name in enumerate((PACKAGE, "SHA256SUMS"), 1)]
        self.assertEqual([item.name for item in release.release_files({"assets": listed},
                                                                      False)],
                         ["SHA256SUMS", PACKAGE])


class VerifyTest(PublishCase):
    def setUp(self):
        super().setUp()
        published = self.release("publish")
        self.assertEqual(published[0], 0, published)
        self.seen = len(self.hub.requests)

    def test_verify_of_a_public_repo_reads_only_the_visibility_with_the_token(self):
        # Right after a publish on a public repo every step passes, in the order the
        # visibility, the files, the tag and the CI state of master, and the read of the
        # visibility is the only request of verify with a login. Then, with the token
        # file gone, verify of the same release fails at the visibility with one line,
        # because it needs the token on a public repo too.
        result = self.release("verify", "0.7.0")
        self.assertEqual(result[0], 0, result)
        for number, name in enumerate(("the visibility", "the published files", "the tag",
                                       "the CI state of master"), 1):
            self.assertIn(f"verify {number}/4: {name}\n", result[1])
        self.assertIn(f"the repo {SLUG} is public\n", result[1])
        with_login = [path for _, path, login in self.hub.requests[self.seen:] if login]
        self.assertEqual(with_login, [f"/repos/{SLUG}"])
        (self.home / ".config" / "jig" / f"{PROJECT}-token").unlink()
        self.assert_verify_failed_at("the visibility", "cannot read the token file")

    def test_a_published_file_that_differs_from_the_staged_file_fails_verify(self):
        # The download of SHA256SUMS serves other bytes than the staged file.
        path = f"/{SLUG}/releases/download/{TAG}/SHA256SUMS"
        self.hub.files[path] = b"other sums\n"
        self.assert_verify_failed_at("the published files", "SHA256SUMS")

    def test_a_tag_at_a_commit_that_is_not_the_release_commit_fails_verify(self):
        # The tag on the product's origin is moved to the commit before the release,
        # which names no version: verify fails at the tag.
        self.git("--git-dir", self.origin, "tag", "-f", "-a", TAG, "-m", "moved",
                 f"{self.release_commit}^")
        self.assert_verify_failed_at("the tag", "does not point at the release commit")

    def test_a_badge_that_does_not_read_passing_fails_verify(self):
        # On a public repo the badge of ci.yml on master reads failing.
        self.hub.badge = BADGE_FAILING
        self.assert_verify_failed_at("the CI state of master", "does not read passing")


@unittest.skipUnless(loopback_works(), "no loopback network for the fake API")
class PrivateReadTest(unittest.TestCase):
    """The reads of a private release, run in this process against the fake GitHub.

    The release v0.7.0 holds the two staged files, and the repo is private, so each
    file can be read only through the release asset API with the token.
    """

    def setUp(self):
        self.hub = FakeGitHub()
        threading.Thread(target=self.hub.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(self.hub.server_close)
        self.addCleanup(self.hub.shutdown)
        self.hub.private = True
        self.hub.make_release(TAG)
        for name, data in sorted(STAGED.items()):
            self.hub.add_file(TAG, name, data)
        self.staged = {name: hashlib.sha256(data).hexdigest() for name, data in STAGED.items()}

    def read_back(self):
        return release.read_back(self.hub.url, SLUG, TAG, self.staged, TOKEN)

    def test_a_file_served_directly_and_one_served_by_a_redirect_are_both_read(self):
        # The asset API answers once with the file itself and once with a redirect to
        # the storage: both give the file's bytes, and the storage got no login.
        for answer in ("direct", "redirect"):
            with self.subTest(answer=answer):
                self.hub.asset_answer = answer
                self.assertEqual(release.read_release_file(self.hub.url, SLUG, 1, TOKEN),
                                 self.hub.files[self.hub.assets[1]])
        self.assertEqual(self.hub.asked("/storage/"), [None])

    def test_a_readback_with_the_staged_files_passes_with_the_token_alone(self):
        # The staged files match the release: the readback passes and names the token,
        # every request to the API carried it, and no download URL was asked.
        self.assertEqual(self.read_back(),
                         "the 2 files of the release match the staged files, read with "
                         "the token")
        self.assertEqual(set(self.hub.asked(f"/repos/{SLUG}/")), {f"Bearer {TOKEN}"})
        self.assertEqual(self.hub.asked("/download/"), [])

    def test_a_file_with_another_hash_fails_the_readback(self):
        # The release holds other bytes for the package than the staged file.
        self.hub.files[self.hub.assets[1]] = b"other bytes\n"
        with self.assertRaisesRegex(release.StepFailed, "read with the token, differs"):
            self.read_back()

    def test_a_file_missing_from_the_release_fails_the_readback(self):
        # The release lacks one of the staged files.
        self.hub.releases[TAG]["assets"].pop()
        with self.assertRaisesRegex(release.StepFailed, "holds .*, the staging directory"):
            self.read_back()

    def test_a_redirect_of_a_request_with_the_token_is_not_followed(self):
        # The API answers the read of the visibility with a redirect elsewhere: the read
        # fails and names the redirect, and its target got no request at all.
        self.hub.repo_answer = "moved"
        with self.assertRaisesRegex(release.StepFailed, "does not follow"):
            release.read_visibility(self.hub.url, SLUG, TOKEN)
        self.assertEqual(self.hub.asked("/moved/"), [])

    def test_the_visibility_reads_true_and_false_with_the_token(self):
        # A private repo reads True, which needs the token, and a public one False.
        self.assertIs(release.read_visibility(self.hub.url, SLUG, TOKEN), True)
        self.hub.private = False
        self.assertIs(release.read_visibility(self.hub.url, SLUG, TOKEN), False)
        self.assertEqual(set(self.hub.asked(f"/repos/{SLUG}")), {f"Bearer {TOKEN}"})

    def test_an_asset_id_that_is_not_a_number_is_refused_before_any_request(self):
        # A file list whose ID would change the path that the token is sent to.
        listed = {"assets": [{"name": "demo-0.7.0.tar.gz", "id": "1/../../../elsewhere"}]}
        with self.assertRaisesRegex(release.StepFailed, "asset ID"):
            release.asset_names(listed, private=True)


class UsageTest(TempTestCase):
    def test_usage_errors_exit_2(self):
        # Each command line is malformed: a missing or unknown part, an option a command
        # does not take, or a version that is not <X>.<Y>.<Z>.
        for args in (["demo"], ["demo", "prepare"], ["demo", "release"],
                     ["demo", "prepare", "--entry"], ["demo", "prepare", "--entry", "e"],
                     ["demo", "prepare", "--entry", "e", "--trailer", ""],
                     ["Bad Name", "prepare", "--entry", "e"], ["demo", "publish", "--wet"],
                     ["demo", "publish", "--dry-run", "--dry-run"], ["demo", "verify"],
                     ["demo", "verify", "1.0"], ["demo", "verify", "1.0.0", "1.0.1"]):
            with self.subTest(args=args):
                result = run([RELEASE, *args], env=self.env())
                self.assertEqual(result.returncode, 2, result)
                self.assertIn("usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
