import datetime
import json
import os
import time

from tests.support import CO_AUTHOR_ADDRESS, ROOT, run
from tests.test_start import JIG, StartCase

REPORT_WORKER = ROOT / "libexec" / "jig-report-worker"
SESSION = "jig-impl-demo"
REPORT = "impl/log/demo-report.md"
TIMES = "start: 2026-11-09T10:00:00-08:00  end: 2026-11-09T11:00:00-08:00  context: "
CHECKS_TSV = ("name\tcommand\taccount\tcap_seconds\n"
              "make-check\tmake check\timplementer\t300\n"
              "mutants\trun-mutants\timplementer\t600\n"
              "ci-green\tci-green\timplementer\t60\n")


def report_text(commits="none", checks="none", context="none", status="OK: done"):
    return "\n".join([status, "jig-report: 1", "id: demo", "class: minor",
                      f"commits: {commits}", f"checks: {checks}", "proofs: P1=0 P2=none",
                      TIMES + context, "next: nothing", "---", "Body line."]) + "\n"


class ReportCase(StartCase):
    """The fixtures of jig start, with a product clone beside the orchestrator's -dev clone.

    A unit is begun by its start commit alone: jig report needs no live session.
    """

    def setUp(self):
        super().setUp()
        self.product_origin = self.tmp / "product.git"
        self.ok(["git", "init", "-q", "--bare", "-b", "master", self.product_origin])
        self.product_seed = self.git_init(self.tmp / "product-seed")
        self.ok(["git", "-C", self.product_seed, "remote", "add", "origin",
                 self.product_origin])
        (self.product_seed / "tool.py").write_text("print('a tool')\n", encoding="utf-8")
        self.git_commit_all(self.product_seed)
        self.ok(["git", "-C", self.product_seed, "push", "-q", "origin", "master"])
        self.product_head = self.ok(["git", "-C", self.product_seed, "rev-parse", "HEAD"])
        self.product = self.orch_clone.parent / "jig"
        self.ok(["git", "clone", "-q", "-b", "master", self.product_origin, self.product])
        self.ctx_dir = self.tmp / "ctx"
        self.ctx_dir.mkdir()
        self.reviews = self.worker_home / "ws" / "reviews" / "jig"
        self.reviews.mkdir(parents=True)
        self.push_files({"checks.tsv": CHECKS_TSV}, "checks")

    def push_files(self, files, subject):
        """Commit files from the seed clone and push them, as another account would."""
        self.ok(["git", "-C", self.seed, "fetch", "-q", "origin"])
        self.ok(["git", "-C", self.seed, "reset", "-q", "--hard", "origin/master"])
        for name, text in files.items():
            path = self.seed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a" if name.endswith("status.log") else "w", encoding="utf-8") \
                    as stream:
                stream.write(text)
        self.ok(["git", "-C", self.seed, "add", "-A"])
        self.ok(["git", "-C", self.seed, "commit", "-q", "--no-verify", "-m", subject])
        self.ok(["git", "-C", self.seed, "push", "-q", "origin", "master"])
        return self.origin_head()

    def begin(self, unit="demo", at="2026-11-09T10:00:00-08:00"):
        """Push the start commit of a run; return its commit time in seconds.

        The start line in status.log carries the time at, as jig start writes its own.
        """
        commit = self.push_files({"impl/log/status.log": f"{at} {unit} start started abc\n"},
                                 f"status: {unit} start")
        return int(self.ok(["git", "-C", self.origin, "log", "-1", "--format=%ct", commit]))

    def push_report(self, text=None):
        return self.push_files({REPORT: text or report_text()}, "log: demo")

    def move_product_to_trunk(self):
        """Leave the product's origin with trunk and no master, in every clone."""
        self.ok(["git", "-C", self.product_origin, "branch", "-q", "trunk", "master"])
        self.ok(["git", "-C", self.product_origin, "symbolic-ref", "HEAD", "refs/heads/trunk"])
        self.ok(["git", "-C", self.product_origin, "branch", "-q", "-D", "master"])
        self.ok(["git", "-C", self.product, "fetch", "-q", "--prune", "origin"])
        self.ok(["git", "-C", self.product_seed, "fetch", "-q", "--prune", "origin"])
        self.ok(["git", "-C", self.product_seed, "checkout", "-q", "-B", "trunk",
                 "origin/trunk"])

    def exit_file(self, name, code, mtime=None):
        path = self.reviews / name
        path.write_text(f"{code}\n", encoding="utf-8")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def context_file(self, tokens=84213, window=200000):
        data = {"session": SESSION, "total_input_tokens": tokens,
                "context_window_size": window, "used_percentage": 42.1,
                "written": "2026-11-09T10:30:00-08:00"}
        (self.ctx_dir / f"{SESSION}.json").write_text(json.dumps(data), encoding="utf-8")

    def report(self, unit="demo"):
        return run([JIG, "report", "jig", unit],
                   env=self.base_env(JIG_CTX_DIR=str(self.ctx_dir)))

    def report_on_origin(self):
        return self.ok(["git", "-C", self.origin, "show", f"master:{REPORT}"]) + "\n"

    def header(self, number):
        return self.report_on_origin().splitlines()[number - 1]

    def assert_refused_unchanged(self, result, name, before):
        """A refusal names its reason and leaves origin and the clone as they were."""
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"refused: {name}:", result.stderr)
        self.assertEqual(self.origin_head(), before)
        self.assertNotIn("report: demo", self.origin_subjects())


class ReportFillTest(ReportCase):
    def test_report_fills_checks_and_context_from_the_files(self):
        # The run recorded two checks and the status line wrote a readout: both go in.
        self.begin()
        self.exit_file("demo-make-check.exit", 0)
        self.exit_file("demo-ci-green.exit", 0)
        self.context_file()
        self.push_report()
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.header(6), "checks: make-check=0 ci-green=0")
        self.assertEqual(self.header(8), TIMES + "84213/200000")
        self.assertEqual(self.report_on_origin(),
                         report_text(checks="make-check=0 ci-green=0", context="84213/200000"))

    def test_report_commits_the_report_alone_with_its_subject_and_trailer(self):
        self.begin()
        self.exit_file("demo-make-check.exit", 0)
        self.push_report()
        self.assertEqual(self.report().returncode, 0)
        self.assertEqual(self.origin_subjects()[0], "report: demo")
        body = self.ok(["git", "-C", self.origin, "log", "-1", "--format=%b", "master"])
        self.assertEqual(body, f"Co-Authored-By: Claude <{CO_AUTHOR_ADDRESS}>")
        self.assertEqual(self.ok(["git", "-C", self.origin, "show", "--name-only", "--format=",
                                  "master"]), REPORT)
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "status", "--porcelain"]), "")

    def test_report_keeps_a_failed_check_with_its_code(self):
        self.begin()
        self.exit_file("demo-make-check.exit", 2)
        self.exit_file("demo-mutants.exit", 124)
        self.push_report()
        self.assertEqual(self.report().returncode, 0)
        self.assertEqual(self.header(6), "checks: make-check=2 mutants=124")

    def test_report_keeps_a_measured_checks_value_as_written(self):
        # The unit wrote its own values; the files do not replace them.
        self.begin()
        self.exit_file("demo-make-check.exit", 1)
        self.context_file()
        self.push_report(report_text(checks="make-check=0", context="142k"))
        self.assertEqual(self.report().returncode, 0)
        self.assertEqual(self.header(6), "checks: make-check=0")
        self.assertEqual(self.header(8), TIMES + "142k")

    def test_report_takes_no_exit_file_of_another_id_or_another_row(self):
        self.begin()
        self.exit_file("demo2-make-check.exit", 0)
        self.exit_file("demo-unknown-row.exit", 0)
        self.exit_file("demo-make-check.txt", 0)
        self.exit_file("demo-ci-green.exit", 5)
        self.push_report()
        self.assertEqual(self.report().returncode, 0)
        self.assertEqual(self.header(6), "checks: ci-green=5")

    def test_report_takes_no_exit_file_older_than_the_current_runs_start(self):
        # The earlier run left an exit file; the restart's start commit comes after it.
        started = self.begin()
        self.exit_file("demo-make-check.exit", 0, mtime=started - 600)
        self.exit_file("demo-mutants.exit", 0)
        self.push_report()
        self.assertEqual(self.report().returncode, 0)
        self.assertEqual(self.header(6), "checks: mutants=0")

    def test_report_leaves_both_placeholders_with_nothing_to_fill(self):
        # No exit file and no context file: the report is unchanged, and still marked done.
        self.begin()
        self.push_report()
        self.assertEqual(self.report().returncode, 0)
        self.assertEqual(self.report_on_origin(), report_text())
        self.assertEqual(self.origin_subjects()[0], "report: demo")

    def test_a_missing_context_file_leaves_context_none(self):
        self.begin()
        self.exit_file("demo-make-check.exit", 0)
        self.push_report()
        self.assertEqual(self.report().returncode, 0)
        self.assertEqual(self.header(8), TIMES + "none")
        self.assertEqual(self.header(6), "checks: make-check=0")

    def test_a_context_file_that_does_not_parse_leaves_context_none(self):
        self.begin()
        (self.ctx_dir / f"{SESSION}.json").write_text("{not json", encoding="utf-8")
        self.push_report()
        self.assertEqual(self.report().returncode, 0)
        self.assertEqual(self.header(8), TIMES + "none")

    def test_report_refuses_an_exit_file_that_holds_no_code(self):
        self.begin()
        self.exit_file("demo-make-check.exit", "passed")
        before = self.push_report()
        self.assert_refused_unchanged(self.report(), "checks", before)


class ReportCommitsTest(ReportCase):
    def test_report_knows_a_product_hash_and_a_dev_hash(self):
        self.begin()
        dev_hash = self.ok(["git", "-C", self.origin, "rev-parse", "master"])
        self.push_report(report_text(commits=f"{self.product_head[:7]} {dev_hash}"))
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[0], "report: demo")

    def test_report_fetches_the_product_clone_for_a_new_product_commit(self):
        # The unit pushed its product commit after the orchestrator's last fetch.
        (self.product_seed / "more.py").write_text("more\n", encoding="utf-8")
        self.git_commit_all(self.product_seed)
        self.ok(["git", "-C", self.product_seed, "push", "-q", "origin", "master"])
        new = self.ok(["git", "-C", self.product_seed, "rev-parse", "HEAD"])
        self.begin()
        self.push_report(report_text(commits=new[:10]))
        self.assertEqual(self.report().returncode, 0)

    def test_report_refuses_an_unknown_hash_and_commits_nothing(self):
        self.begin()
        before = self.push_report(report_text(commits=f"{self.product_head[:7]} deadbee"))
        result = self.report()
        self.assert_refused_unchanged(result, "commits", before)
        self.assertIn("deadbee", result.stderr)
        self.assertNotIn(self.product_head[:7], result.stderr)

    def test_report_refuses_a_product_commit_that_origin_does_not_hold(self):
        # A commit only in the orchestrator's product clone was never pushed.
        (self.product / "local.py").write_text("local\n", encoding="utf-8")
        self.git_commit_all(self.product)
        local = self.ok(["git", "-C", self.product, "rev-parse", "HEAD"])
        self.begin()
        before = self.push_report(report_text(commits=local[:12]))
        self.assert_refused_unchanged(self.report(), "commits", before)


class ReportProductLineTest(ReportCase):
    """Report commits checked against a product that may not have the conf's branch.

    The conf's branch here is master. A product before its version lines keeps its work
    on another branch, trunk in these tests, and has no master at all.
    """

    def push_product(self, branch):
        """Push a new product commit made in another clone to branch; return its hash."""
        (self.product_seed / f"{branch}.py").write_text(f"{branch}\n", encoding="utf-8")
        self.git_commit_all(self.product_seed)
        self.ok(["git", "-C", self.product_seed, "push", "-q", "origin", f"HEAD:{branch}"])
        return self.ok(["git", "-C", self.product_seed, "rev-parse", "HEAD"])

    def test_a_product_without_the_branch_accepts_a_commit_on_another_origin_branch(self):
        self.move_product_to_trunk()
        commit = self.push_product("trunk")
        self.begin()
        self.push_report(report_text(commits=commit[:10]))
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[0], "report: demo")

    def test_a_product_without_the_branch_refuses_a_commit_on_no_origin_branch(self):
        # The commit is only in the orchestrator's product clone; no origin branch has it.
        self.move_product_to_trunk()
        (self.product / "local.py").write_text("local\n", encoding="utf-8")
        self.git_commit_all(self.product)
        local = self.ok(["git", "-C", self.product, "rev-parse", "HEAD"])
        self.begin()
        before = self.push_report(report_text(commits=local[:12]))
        result = self.report()
        self.assert_refused_unchanged(result, "commits", before)
        self.assertIn("origin/master of the -dev clone or on origin/trunk of the product",
                      result.stderr)
        self.assertIn(local[:12], result.stderr)

    def test_a_product_with_the_branch_refuses_a_commit_only_on_another_branch(self):
        # The product has master, so a commit left on a side branch does not count.
        side = self.push_product("side")
        self.begin()
        before = self.push_report(report_text(commits=side[:10]))
        result = self.report()
        self.assert_refused_unchanged(result, "commits", before)
        self.assertIn("origin/master of the -dev clone or on origin/master of the product",
                      result.stderr)

    def test_a_dev_commit_only_on_another_dev_branch_is_refused(self):
        # The product has no master, yet -dev does: there only origin/master counts.
        self.move_product_to_trunk()
        self.ok(["git", "-C", self.seed, "checkout", "-q", "-b", "side"])
        (self.seed / "side.md").write_text("side\n", encoding="utf-8")
        self.ok(["git", "-C", self.seed, "add", "side.md"])
        self.ok(["git", "-C", self.seed, "commit", "-q", "--no-verify", "-m", "side"])
        self.ok(["git", "-C", self.seed, "push", "-q", "origin", "side"])
        side = self.ok(["git", "-C", self.seed, "rev-parse", "HEAD"])
        self.ok(["git", "-C", self.seed, "checkout", "-q", "master"])
        self.begin()
        before = self.push_report(report_text(commits=side[:10]))
        self.assert_refused_unchanged(self.report(), "commits", before)


class ReportOnceTest(ReportCase):
    def test_a_second_report_commits_nothing(self):
        self.begin()
        self.exit_file("demo-make-check.exit", 0)
        self.push_report()
        self.assertEqual(self.report().returncode, 0)
        before = self.origin_head()
        calls = self.ssh_calls()
        self.exit_file("demo-mutants.exit", 0)
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout)
        self.assertIn("filled already", result.stdout)
        self.assertEqual(self.origin_head(), before)
        self.assertEqual(self.ssh_calls(), calls)

    def test_a_new_log_commit_after_the_report_commit_is_filled_again(self):
        # The unit amended its report with a second log commit: that one gets its values.
        self.begin()
        self.push_report()
        self.assertEqual(self.report().returncode, 0)
        self.exit_file("demo-make-check.exit", 0)
        self.push_report(report_text(status="OK: done again"))
        self.assertEqual(self.report().returncode, 0)
        self.assertEqual(self.header(6), "checks: make-check=0")
        self.assertEqual(self.origin_subjects()[:2], ["report: demo", "log: demo"])

    def ssh_calls(self):
        targets = self.worker_file("ssh-targets")
        return targets.read_text(encoding="utf-8").count("\n") if targets.exists() else 0


class ReportRefusalTest(ReportCase):
    def test_report_refuses_a_report_that_fails_validation(self):
        self.begin()
        before = self.push_report(report_text(status="DONE"))
        result = self.report()
        self.assert_refused_unchanged(result, "report", before)
        self.assertIn("line 1:", result.stderr)

    def test_report_refuses_a_report_that_names_another_id(self):
        # The report file of unit demo says on its third line that it is the
        # report of unit other; that is a failed validation, and nothing is
        # committed.
        self.begin()
        before = self.push_report(report_text().replace("id: demo", "id: other"))
        result = self.report()
        self.assert_refused_unchanged(result, "report", before)
        self.assertIn("line 3:", result.stderr)

    def test_report_refuses_without_a_log_commit_after_the_newest_start(self):
        # The first run reported; the restart has not yet.
        self.begin()
        self.push_report()
        before = self.push_files({"impl/log/status.log": "x demo start started abc\n"},
                                 "status: demo start")
        self.assert_refused_unchanged(self.report(), "report", before)

    def test_report_refuses_a_unit_that_never_started(self):
        before = self.push_report()
        self.assert_refused_unchanged(self.report(), "start", before)

    def test_report_refuses_when_the_report_file_has_uncommitted_changes(self):
        self.begin()
        before = self.push_report()
        self.ok(["git", "-C", self.orch_clone, "pull", "-q", "--ff-only"])
        (self.orch_clone / REPORT).write_text("an edit of my own\n", encoding="utf-8")
        self.assert_refused_unchanged(self.report(), "clone", before)
        self.assertEqual((self.orch_clone / REPORT).read_text(encoding="utf-8"),
                         "an edit of my own\n")

    def test_report_refuses_a_clone_with_unpushed_commits(self):
        self.begin()
        before = self.push_report()
        (self.orch_clone / "mine.md").write_text("mine\n", encoding="utf-8")
        self.git_commit_all(self.orch_clone)
        self.assert_refused_unchanged(self.report(), "clone", before)

    def test_report_refuses_when_the_worker_part_fails(self):
        self.begin()
        before = self.push_report()
        self.worker_file("ssh-fails").write_text("", encoding="utf-8")
        self.assert_refused_unchanged(self.report(), "worker", before)

    def test_report_refuses_a_role_the_conf_gives_to_another_account(self):
        self.begin()
        before = self.push_report()
        self.write_conf(self.home, IMPL_ACCOUNT="someone")
        self.assert_refused_unchanged(self.report(), "account", before)

    def test_report_keeps_an_uncommitted_update_log_line_through_a_refused_push(self):
        # The supervisor's idle line waits uncommitted in the orchestrator's clone when
        # the report's push loses a race; the one rebase carries the line along.
        line = self.track_update_log()
        self.begin()
        self.push_report()
        self.race_next_push()
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[:2], ["report: demo", "raced"])
        self.assert_line_kept(line)

    def test_report_takes_back_its_commit_when_origin_refuses_it(self):
        self.begin()
        before = self.push_report()
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        self.assert_refused_unchanged(self.report(), "push", before)
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "rev-parse", "HEAD"]), before)
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "status", "--porcelain"]), "")

    def test_report_takes_back_its_commit_when_origin_cannot_be_reached(self):
        # Origin goes away as the report commit is pushed, so the fetch after the
        # refused push fails too. The report refuses with the fetch named, and the
        # clone keeps no commit that origin lacks, so jig report can run again.
        self.begin()
        before = self.push_report()
        self.lose_origin_at_next_push()
        result = self.report()
        self.assert_refused_unchanged(result, "push", before)
        self.assertIn("the fetch after it failed", result.stderr)
        self.assertIn("jig report can run again", result.stderr)
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "rev-parse", "HEAD"]), before)
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "status", "--porcelain"]), "")

    def test_report_rejects_a_bad_id_as_a_usage_error(self):
        result = self.report(unit="TooLong7")
        self.assertEqual(result.returncode, 2, result.stderr)


class ReportWorkerAloneTest(ReportCase):
    def test_the_worker_lists_only_the_units_plain_exit_files(self):
        self.exit_file("demo-make-check.exit", 0)
        self.exit_file("demo2-make-check.exit", 0)
        (self.reviews / "demo-link.exit").symlink_to(self.reviews / "demo-make-check.exit")
        result = run(["python3", "-I", "-", "jig", "demo"],
                     env=self.base_env(HOME=str(self.worker_home)),
                     stdin=REPORT_WORKER.read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        files = json.loads(result.stdout)["files"]
        self.assertEqual([entry["name"] for entry in files], ["demo-make-check.exit"])
        self.assertEqual(files[0]["text"], "0\n")

    def test_the_worker_uses_only_the_standard_library(self):
        # It runs from standard input in another account, where jig's lib/ is not on the path.
        imports = [line.split()[1] for line in
                   REPORT_WORKER.read_text(encoding="utf-8").splitlines()
                   if line.startswith("import ")]
        self.assertEqual(sorted(imports), ["json", "os", "pathlib", "re", "sys"])


# The checks of a project with test vectors: the last row names the product's vector
# directory, tests/vectors, as the last word of its command.
VECTOR_CHECKS_TSV = CHECKS_TSV + "recompute\tjig recompute jig tests/vectors\tspecwriter\t600\n"
SPEC_PROMPT_LINE = "# demo  paste into: jig-spec-demo (specwriter, ~/ws/src/jig-dev)"


class ReportVectorTest(ReportCase):
    """Product commits listed in a report, set against the product's vector directory."""

    def product_commit(self, files, removed=()):
        """Push a product commit made in another clone; return its hash."""
        for name, text in files.items():
            path = self.product_seed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for name in removed:
            (self.product_seed / name).unlink()
        self.git_commit_all(self.product_seed)
        self.ok(["git", "-C", self.product_seed, "push", "-q", "origin", "master"])
        return self.ok(["git", "-C", self.product_seed, "rev-parse", "HEAD"])

    def report_of(self, commit):
        """Begin the unit and push its report, which lists that product commit."""
        self.begin()
        return self.push_report(report_text(commits=commit[:10]))

    def test_an_implementer_report_whose_product_commit_adds_a_vector_is_refused(self):
        self.push_files({"checks.tsv": VECTOR_CHECKS_TSV}, "checks")
        commit = self.product_commit({"tool.py": "print('changed')\n",
                                      "tests/vectors/a.bin": "vector\n"})
        before = self.report_of(commit)
        result = self.report()
        self.assert_refused_unchanged(result, "vectors", before)
        self.assertIn(f"{commit[:10]} changes tests/vectors/a.bin", result.stderr)

    def test_an_implementer_report_whose_product_commit_removes_a_vector_is_refused(self):
        # The spec writer's copy came first; the listed commit deletes one of its files.
        self.push_files({"checks.tsv": VECTOR_CHECKS_TSV}, "checks")
        self.product_commit({"tests/vectors/a.bin": "vector\n"})
        commit = self.product_commit({}, removed=["tests/vectors/a.bin"])
        before = self.report_of(commit)
        self.assert_refused_unchanged(self.report(), "vectors", before)

    def test_the_same_report_is_accepted_when_checks_have_no_recompute_row(self):
        # Without the row the project has no vector directory, so nothing is refused.
        commit = self.product_commit({"tests/vectors/a.bin": "vector\n"})
        self.report_of(commit)
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[0], "report: demo")

    def test_a_spec_writer_report_whose_product_commit_adds_a_vector_is_accepted(self):
        self.push_files({"checks.tsv": VECTOR_CHECKS_TSV}, "checks")
        rest = self.prompt.read_text(encoding="utf-8").split("\n", 1)[1]
        self.prompt.write_text(f"{SPEC_PROMPT_LINE}\n{rest}", encoding="utf-8")
        commit = self.product_commit({"tests/vectors/a.bin": "vector\n"})
        self.report_of(commit)
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[0], "report: demo")

    def test_an_implementer_commit_beside_the_vector_directory_is_accepted(self):
        # tests/vectors-old shares the directory's name as a prefix but is another one.
        self.push_files({"checks.tsv": VECTOR_CHECKS_TSV}, "checks")
        commit = self.product_commit({"tests/vectors-old/a.bin": "old\n",
                                      "tool.py": "print('changed')\n"})
        self.report_of(commit)
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)


def local_time(seconds):
    """Return a time in the form of a status.log line."""
    return datetime.datetime.fromtimestamp(seconds).astimezone().isoformat(timespec="seconds")


class UnitVectorCase(ReportCase):
    """A project with the row recompute, whose unit began some minutes before now.

    The unit's first start line is some minutes before now and its log commit is made
    now, so a product commit's committer time places it before, during or after the unit.
    """

    def setUp(self):
        super().setUp()
        self.now = int(time.time())
        self.push_files({"checks.tsv": VECTOR_CHECKS_TSV}, "checks")

    def product_commit_at(self, seconds, files, branch="master", clone=None):
        """Make a product commit with the given committer time; return its hash.

        By default the commit is made in the seed clone and pushed to the branch of
        origin. Made in another clone, such as the orchestrator's product clone, it is
        not pushed, so no origin branch holds it.
        """
        clone = clone or self.product_seed
        for name, text in files.items():
            path = clone / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        env = self.env(GIT_COMMITTER_DATE=f"@{seconds} +0000")
        commands = [["add", "-A"], ["commit", "-q", "--no-verify", "-m", "fixture"]]
        if clone == self.product_seed:
            commands.append(["push", "-q", "origin", f"HEAD:{branch}"])
        for command in commands:
            result = run(["git", "-C", clone, *command], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
        return self.ok(["git", "-C", clone, "rev-parse", "HEAD"])

    def unit_ran(self, *starts, commits="none"):
        """Push a start commit for each start time, then the unit's log commit."""
        for seconds in starts:
            self.begin(at=local_time(seconds))
        return self.push_report(report_text(commits=commits))


class ReportUnitVectorTest(UnitVectorCase):
    """Product commits made during a unit and left out of its report."""

    def test_an_unnamed_vector_commit_made_during_the_unit_is_refused(self):
        commit = self.product_commit_at(self.now - 1000, {"tests/vectors/a.bin": "vector\n"})
        before = self.unit_ran(self.now - 2000)
        result = self.report()
        self.assert_refused_unchanged(result, "vectors", before)
        self.assertIn(f"{commit[:12]} changes tests/vectors/a.bin", result.stderr)
        self.assertIn("not named in commits:", result.stderr)

    def test_a_restarted_unit_counts_from_its_first_start_line(self):
        # The commit falls after the first run's start and before the restart's; the
        # newest start would leave it out.
        commit = self.product_commit_at(self.now - 1000, {"tests/vectors/a.bin": "vector\n"})
        before = self.unit_ran(self.now - 2000, self.now - 500)
        result = self.report()
        self.assert_refused_unchanged(result, "vectors", before)
        self.assertIn(f"{commit[:12]} changes tests/vectors/a.bin", result.stderr)

    def test_the_same_commit_named_in_commits_is_refused_as_the_units_own(self):
        commit = self.product_commit_at(self.now - 1000, {"tests/vectors/a.bin": "vector\n"})
        before = self.unit_ran(self.now - 2000, commits=commit[:10])
        result = self.report()
        self.assert_refused_unchanged(result, "vectors", before)
        self.assertIn(f"{commit[:10]} changes tests/vectors/a.bin", result.stderr)
        self.assertNotIn("not named", result.stderr)

    def test_a_vector_commit_before_the_first_start_line_is_accepted(self):
        # The spec writer's copy came before the unit began.
        self.product_commit_at(self.now - 3000, {"tests/vectors/a.bin": "vector\n"})
        self.unit_ran(self.now - 2000)
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[0], "report: demo")

    def test_a_vector_commit_after_the_log_commit_is_accepted(self):
        self.product_commit_at(self.now + 1000, {"tests/vectors/a.bin": "vector\n"})
        self.unit_ran(self.now - 2000)
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[0], "report: demo")

    def test_an_unnamed_commit_during_the_unit_that_touches_no_vector_is_accepted(self):
        self.product_commit_at(self.now - 1000, {"tool.py": "print('changed')\n"})
        self.unit_ran(self.now - 2000)
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[0], "report: demo")

    def test_a_project_without_the_row_recompute_is_accepted(self):
        # Without the row there is no vector directory, so nothing is refused.
        self.push_files({"checks.tsv": CHECKS_TSV}, "checks")
        self.product_commit_at(self.now - 1000, {"tests/vectors/a.bin": "vector\n"})
        self.unit_ran(self.now - 2000)
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[0], "report: demo")

    def test_a_unit_with_no_start_line_in_status_log_is_refused(self):
        # The start commit is there, but its line in status.log is not.
        self.push_files({"impl/log/status.log": ""}, "status: demo start")
        before = self.push_report()
        result = self.report()
        self.assert_refused_unchanged(result, "vectors", before)
        self.assertIn("no start line of demo", result.stderr)


class ReportProductLineVectorTest(UnitVectorCase):
    """The vector directory read in a product that has no branch of the conf's name.

    The conf's branch is master; the product keeps its work on trunk and has no master,
    as a product does before its version lines begin.
    """

    def setUp(self):
        super().setUp()
        self.move_product_to_trunk()

    def test_a_listed_vector_commit_on_another_origin_branch_is_refused(self):
        commit = self.product_commit_at(self.now - 1000, {"tests/vectors/a.bin": "vector\n"},
                                        branch="trunk")
        before = self.unit_ran(self.now - 2000, commits=commit[:10])
        result = self.report()
        self.assert_refused_unchanged(result, "vectors", before)
        self.assertIn(f"{commit[:10]} changes tests/vectors/a.bin", result.stderr)
        self.assertNotIn("not named", result.stderr)

    def test_an_unnamed_vector_commit_on_another_origin_branch_is_refused(self):
        commit = self.product_commit_at(self.now - 1000, {"tests/vectors/a.bin": "vector\n"},
                                        branch="trunk")
        before = self.unit_ran(self.now - 2000)
        result = self.report()
        self.assert_refused_unchanged(result, "vectors", before)
        self.assertIn(f"{commit[:12]} changes tests/vectors/a.bin", result.stderr)
        self.assertIn("not named in commits:", result.stderr)

    def test_a_listed_commit_that_touches_no_vector_is_accepted_without_a_listing_error(self):
        # Reading only origin/master, which this product lacks, would fail to list.
        commit = self.product_commit_at(self.now - 1000, {"tool.py": "print('changed')\n"},
                                        branch="trunk")
        self.unit_ran(self.now - 2000, commits=commit[:10])
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("cannot list", result.stderr)
        self.assertEqual(self.origin_subjects()[0], "report: demo")

    def test_a_vector_commit_on_no_origin_branch_is_not_read(self):
        # The commit is only in the orchestrator's product clone, made during the unit;
        # no origin branch holds it, so it is not the unit's pushed work.
        self.product_commit_at(self.now - 1000, {"tests/vectors/a.bin": "vector\n"},
                               clone=self.product)
        self.unit_ran(self.now - 2000)
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[0], "report: demo")


# A spec writer unit that copies the vectors while the unit under report runs.
COPIER = "copy"
COPIER_PROMPT_LINE = f"# {COPIER}  paste into: jig-spec-{COPIER} (specwriter, ~/ws/src/jig-dev)"


class ReportSpecWriterCopyTest(UnitVectorCase):
    """A vector copy of another unit, pushed during the unit and left out of its report.

    The unit under report begins first; the spec writer unit starts later and pushes a
    product commit with its copy of the vectors before the report is made.
    """

    def copy_made(self, files):
        """Push the copy, 1000 s before now, and begin the unit under report before it."""
        commit = self.product_commit_at(self.now - 1000, files)
        self.unit_ran(self.now - 2000)
        return commit

    def records(self, files):
        """Push records of the copier to -dev, as its own unit would; return the head."""
        return self.push_files(files, f"records of {COPIER}")

    def refused_with(self, before, *texts):
        """Run the report, which must be refused for the vectors with each text in its line."""
        result = self.report()
        self.assert_refused_unchanged(result, "vectors", before)
        self.assertIn("changes tests/vectors/a.bin", result.stderr)
        for text in texts:
            self.assertIn(text, result.stderr)

    def test_a_copy_is_skipped_only_once_each_record_of_its_unit_is_on_origin(self):
        # The records come one at a time; each report before the last is refused for the
        # record that is still missing or wrong, and the last one passes.
        commit = self.copy_made({"tests/vectors/a.bin": "vector\n"})
        line = f"{COPIER} start started abc\n"
        before = self.records({"impl/log/status.log": f"{local_time(self.now - 900)} {line}"})
        self.refused_with(before, "no ledger entry")
        ledger = f"trace/ledger/{commit}.md"
        before = self.records({ledger: f"commit: {commit}\nunit: demo\n"})
        self.refused_with(before, "names demo, the unit under report")
        # The only start line of the copier is after the copy, so it cannot have made it.
        before = self.records({ledger: f"commit: {commit}\nunit: {COPIER}\n"})
        self.refused_with(before, f"no start line of {COPIER} before the commit")
        before = self.records({"impl/log/status.log": f"{local_time(self.now - 1500)} {line}"})
        self.refused_with(before, f"names {COPIER}, whose prompt copy",
                          f"run jig report again after 'log: {COPIER}'")
        prompt = f"impl/log/{COPIER}-prompt.md"
        implementer = COPIER_PROMPT_LINE.replace("specwriter", "implementer")
        before = self.records({prompt: f"{implementer}\nThe copy.\n"})
        self.refused_with(before, "names no spec writer account")
        # With the right prompt copy, a conf that gives the role to another account still
        # refuses; a conf with no such key counts the default account, and the copy passes.
        before = self.records({prompt: f"{COPIER_PROMPT_LINE}\nThe copy.\n"})
        self.write_conf(self.home, SPEC_ACCOUNT="writer")
        self.refused_with(before, "names no spec writer account")
        conf = self.home / ".config" / "jig" / "jig.conf"
        conf.write_text("".join(entry for entry in conf.read_text(encoding="utf-8")
                                .splitlines(keepends=True)
                                if not entry.startswith("SPEC_ACCOUNT=")), encoding="utf-8")
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[0], "report: demo")

    def test_a_copy_that_also_changes_a_file_outside_the_directory_is_refused(self):
        # Every record of the copier is there; the extra file alone refuses the commit.
        commit = self.copy_made({"tests/vectors/a.bin": "vector\n", "tool.py": "print(2)\n"})
        before = self.records({
            "impl/log/status.log": f"{local_time(self.now - 1500)} {COPIER} start started abc\n",
            f"trace/ledger/{commit}.md": f"commit: {commit}\nunit: {COPIER}\n",
            f"impl/log/{COPIER}-prompt.md": f"{COPIER_PROMPT_LINE}\nThe copy.\n"})
        self.refused_with(before, "also changes files outside tests/vectors")
