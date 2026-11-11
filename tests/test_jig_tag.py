"""Tests of jig tag, which makes an approval tag in the -dev repo and pushes it to origin.

Each test has a bare repo as the fixture origin, a work clone that writes its history
the way the spec writer and the orchestrator would, and the account's own clone of
-dev under its HOME, from which jig tag learns the origin URL. The version line is
given in the environment, and the account that runs jig tag has an invented git
identity, so that the tests can tell its tags from the fixture's own.
"""

from tests.support import ROOT, TempTestCase, run

TAG = ROOT / "libexec" / "jig-tag"
LINE = "0.9"
TAGGER_NAME = "Ada Approver"
TAGGER_EMAIL = "ada@example.invalid"
STATUS = "specs/00-status.md"
DOC = "specs/design.md"
OTHER_DOC = "specs/parts/other.md"


def status_text(*rows):
    """Return a status file whose table of closed review loops holds these rows.

    Another table follows in the next section, so a reader that does not stop at the
    end of its own section would take that table's rows as records.
    """
    lines = ["# Status", "", "## Spec phase", "", "Prose about the phase.", "",
             "### Closed review loops", "", "| Path | Commit | ID |", "| --- | --- | --- |"]
    lines += [f"| {path} | {commit} | {unit} |" for path, commit, unit in rows]
    lines += ["", "### Open concessions", "", "| Item | Gap | Risk | State |",
              "| --- | --- | --- | --- |", f"| {DOC} | none | low | open |", ""]
    return "\n".join(lines)


def findings_text(*severities):
    """Return a findings file with one finding of each severity, in order."""
    lines = ["# Findings", ""]
    for number, severity in enumerate(severities, 1):
        lines += [f"### F-{number} {severity}", "file: specs/design.md  line: 3  commit: abc",
                  "rule: the rule", "problem: the problem", "options: the options", ""]
    return "\n".join(lines)


class TagCase(TempTestCase):
    """An origin with the version line, a work clone that writes it, and the account's clone."""

    def setUp(self):
        super().setUp()
        self.origin = self.tmp / "origin.git"
        self.ok(["git", "init", "-q", "--bare", "-b", "master", self.origin])
        self.work = self.git_init(self.tmp / "work")
        self.ok(["git", "-C", self.work, "checkout", "-q", "-b", LINE])
        self.ok(["git", "-C", self.work, "remote", "add", "origin", self.origin])
        self.first = self.commit({DOC: "one\n", STATUS: status_text(), "README": "a\n"})
        self.dev = self.home / "ws" / "src" / "demo-dev"
        self.ok(["git", "clone", "-q", "-b", LINE, self.origin, self.dev])
        # jig tag makes its temporary clone here, so a test can see that it is gone.
        self.scratch = self.tmp / "scratch"
        self.scratch.mkdir()

    def ok(self, command):
        result = run(command, env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, files, removed=(), subject="change"):
        """Commit files in the work clone and push the line; return the full hash."""
        for name, text in files.items():
            path = self.work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for name in removed:
            (self.work / name).unlink()
        self.ok(["git", "-C", self.work, "add", "-A"])
        self.ok(["git", "-C", self.work, "commit", "-q", "--no-verify", "-m", subject])
        self.ok(["git", "-C", self.work, "push", "-q", "origin", LINE])
        return self.ok(["git", "-C", self.work, "rev-parse", "HEAD"])

    def approve(self, tag, commit):
        """Push an annotated tag made outside jig tag, as an earlier approval."""
        self.ok(["git", "-C", self.work, "tag", "-a", "-m", tag, tag, commit])
        self.ok(["git", "-C", self.work, "push", "-q", "origin", f"refs/tags/{tag}"])

    def tag_env(self, **extra):
        values = dict(BRANCH=LINE, TMPDIR=str(self.scratch), GIT_COMMITTER_NAME=TAGGER_NAME,
                      GIT_COMMITTER_EMAIL=TAGGER_EMAIL)
        values.update(extra)
        return self.env(**{key: value for key, value in values.items() if value is not None})

    def tag(self, tag, commit, **extra):
        return run([TAG, "demo", tag, commit], env=self.tag_env(**extra))

    def on_origin(self, tag):
        """Return the commit that origin's tag points at, or None without that tag."""
        result = run(["git", "-C", self.work, "ls-remote", self.origin, f"refs/tags/{tag}^{{}}"],
                     env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split("\t")[0] if result.stdout else None

    def assert_tagged(self, result, tag, commit):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.on_origin(tag), commit)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def assert_refused(self, result, name, *parts):
        """A refusal exits 1 with one line that names its reason, and leaves no clone."""
        self.assertEqual(result.returncode, 1, result.stderr)
        lines = result.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertIn(f"refused: {name}:", lines[0])
        for part in parts:
            self.assertIn(part, lines[0])
        self.assertEqual(list(self.scratch.iterdir()), [])


class TagFormsTest(TagCase):
    def test_each_of_the_four_forms_is_tagged_pushed_and_read_back(self):
        # The revision follows the phase tag on the same commit, so no document differs.
        for tag in ("spec-p3", "spec-p3r1", "impl-p3", "kickoff-r12"):
            with self.subTest(tag):
                result = self.tag(tag, self.first)
                self.assert_tagged(result, tag, self.first)
                self.assertIn(tag, result.stdout)

    def test_the_tag_is_annotated_with_the_identity_of_the_account_that_runs_it(self):
        self.assert_tagged(self.tag("impl-p3", self.first), "impl-p3", self.first)
        self.ok(["git", "-C", self.work, "fetch", "-q", "origin",
                 "refs/tags/impl-p3:refs/tags/impl-p3"])
        shown = self.ok(["git", "-C", self.work, "for-each-ref", "--format",
                         "%(objecttype) %(taggername) %(taggeremail)", "refs/tags/impl-p3"])
        self.assertEqual(shown, f"tag {TAGGER_NAME} <{TAGGER_EMAIL}>")

    def test_an_abbreviated_commit_is_tagged_at_its_full_hash(self):
        self.assert_tagged(self.tag("kickoff-r1", self.first[:10]), "kickoff-r1", self.first)

    def test_the_origin_comes_from_the_repo_that_the_conf_file_names(self):
        # The account keeps its -dev clone somewhere else and says so in its conf file.
        elsewhere = self.tmp / "elsewhere"
        self.dev.rename(elsewhere)
        conf = self.home / ".config" / "jig" / "demo.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f"REPO={elsewhere}\nBRANCH=0.1\n", encoding="utf-8")
        self.assert_tagged(self.tag("impl-p3", self.first), "impl-p3", self.first)

    def test_without_a_conf_file_the_origin_comes_from_the_dev_clone_in_ws_src(self):
        self.assertFalse((self.home / ".config").exists())
        self.assert_tagged(self.tag("impl-p3", self.first), "impl-p3", self.first)


class TagRefusalTest(TagCase):
    def test_no_branch_in_the_environment_is_refused(self):
        for value in (None, ""):
            with self.subTest(repr(value)):
                self.assert_refused(self.tag("impl-p3", self.first, BRANCH=value), "branch",
                                    "BRANCH")
        self.assertIsNone(self.on_origin("impl-p3"))

    def test_a_branch_in_the_conf_file_does_not_count(self):
        # The tag block sets the line in the environment; a conf file can be stale.
        conf = self.home / ".config" / "jig" / "demo.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f"REPO={self.dev}\nBRANCH={LINE}\n", encoding="utf-8")
        self.assert_refused(self.tag("impl-p3", self.first, BRANCH=None), "branch")
        self.assertIsNone(self.on_origin("impl-p3"))

    def test_a_tag_of_none_of_the_four_forms_is_refused(self):
        for tag in ("spec-p", "spec-px", "spec-p3r", "spec-p3r1x", "impl-p3r1", "kickoff-1",
                    "kickoff-rx", "v0.3.0", "release-p3", "spec-p-3"):
            with self.subTest(tag):
                self.assert_refused(self.tag(tag, self.first), "form", tag)
                self.assertIsNone(self.on_origin(tag))

    def test_a_tag_that_exists_on_origin_is_refused(self):
        later = self.commit({"README": "b\n"})
        self.approve("impl-p3", self.first)
        self.assert_refused(self.tag("impl-p3", later), "exists", "impl-p3")
        self.assertEqual(self.on_origin("impl-p3"), self.first)

    def test_a_commit_that_is_not_on_the_line_of_origin_is_refused(self):
        self.ok(["git", "-C", self.work, "checkout", "-q", "-b", "side"])
        side = self.commit({"README": "side\n"}, subject="side")
        self.ok(["git", "-C", self.work, "push", "-q", "origin", "side"])
        self.assert_refused(self.tag("impl-p3", side), "commit", side[:12], f"origin/{LINE}")
        self.assertIsNone(self.on_origin("impl-p3"))

    def test_a_commit_that_origin_does_not_hold_is_refused(self):
        self.assert_refused(self.tag("impl-p3", "0123456789abcdef"), "commit",
                            "0123456789abcdef")

    def test_a_line_that_origin_does_not_have_is_refused(self):
        self.assert_refused(self.tag("impl-p3", self.first, BRANCH="0.8"), "commit",
                            "origin/0.8")

    def test_no_origin_url_is_refused_with_the_rule(self):
        # Neither a conf file nor a clone under ws/src gives the URL.
        self.dev.rename(self.tmp / "moved")
        self.assert_refused(self.tag("impl-p3", self.first), "origin", "REPO", "demo-dev")

    def test_a_conf_file_whose_repo_has_no_origin_is_refused(self):
        # The conf file decides even when ws/src holds a good clone.
        bare = self.git_init(self.tmp / "no-remote")
        conf = self.home / ".config" / "jig" / "demo.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f'REPO="{bare}"\n', encoding="utf-8")
        self.assert_refused(self.tag("impl-p3", self.first), "origin", str(bare))

    def test_a_push_that_origin_refuses_exits_1_with_one_line(self):
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho no tags today >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        self.assert_refused(self.tag("impl-p3", self.first), "push")
        self.assertIsNone(self.on_origin("impl-p3"))

    def test_a_revision_with_no_spec_tag_as_an_ancestor_is_refused(self):
        # Tags of the other forms, and a spec tag on a later commit, do not count.
        self.approve("impl-p4", self.first)
        changed = self.commit({DOC: "two\n"})
        self.approve("spec-p5", self.commit({"README": "later\n"}))
        self.assert_refused(self.tag("spec-p4r1", changed), "previous", changed[:12])

    def test_wrong_arguments_are_a_usage_error(self):
        for args in ([], ["demo", "impl-p3"], ["../demo", "impl-p3", self.first],
                     ["demo", "impl-p3", "HEAD"], ["demo", "impl-p3", "--all"]):
            with self.subTest(args):
                result = run([TAG, *args], env=self.tag_env())
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("usage: jig tag", result.stderr)


class TagRevisionTest(TagCase):
    """A revision tag needs a closed review loop for every spec document it changes."""

    def setUp(self):
        super().setUp()
        self.approve("spec-p4", self.first)

    def test_a_changed_document_with_no_row_is_refused(self):
        changed = self.commit({DOC: "two\n"})
        # The one line also gives the form of the row it looked for.
        self.assert_refused(self.tag("spec-p4r1", changed), "review", DOC, "no row",
                            f"the row it looks for is | {DOC} | <commit> | <ID> |")
        self.assertIsNone(self.on_origin("spec-p4r1"))

    def test_a_row_at_another_commit_whose_document_differs_is_refused(self):
        # The loop read the second text; the commit to tag holds a third.
        read = self.commit({DOC: "two\n"})
        changed = self.commit({DOC: "three\n", STATUS: status_text((DOC, read, "rv1")),
                               "impl/log/rv1-findings.md": findings_text("should-fix")})
        self.assert_refused(self.tag("spec-p4r1", changed), "review", DOC, "differs")

    def test_a_row_whose_findings_file_is_missing_is_refused(self):
        read = self.commit({DOC: "two\n"})
        changed = self.commit({STATUS: status_text((DOC, read, "rv1"))})
        self.assert_refused(self.tag("spec-p4r1", changed), "review", DOC,
                            "impl/log/rv1-findings.md")

    def test_a_row_whose_findings_file_has_a_must_fix_is_refused(self):
        read = self.commit({DOC: "two\n"})
        changed = self.commit({STATUS: status_text((DOC, read, "rv1")),
                               "impl/log/rv1-findings.md": findings_text("info", "must-fix")})
        self.assert_refused(self.tag("spec-p4r1", changed), "review", DOC, "must-fix")

    def test_a_revision_whose_changed_documents_all_have_closed_loops_is_tagged(self):
        # One row in the form of the real table, with backticks and an abbreviated hash,
        # one plain row with a full hash. The status file itself changes too, and needs
        # no row, nor does the kickoff record.
        read = self.commit({DOC: "two\n", OTHER_DOC: "new\n"})
        rows = ((f"`{DOC}`", f"`{read[:7]}`", "`rv1`"), (OTHER_DOC, read, "rv2"))
        changed = self.commit({STATUS: status_text(*rows),
                               "specs/00-kickoff.md": "# Kickoff\n",
                               "impl/log/rv1-findings.md": findings_text("should-fix", "info"),
                               "impl/log/rv2-findings.md": findings_text()})
        self.assert_tagged(self.tag("spec-p4r1", changed), "spec-p4r1", changed)

    def test_a_removed_document_needs_a_closed_loop_too(self):
        changed = self.commit({}, removed=[DOC])
        self.assert_refused(self.tag("spec-p4r1", changed), "review", DOC, "no row")

    def test_the_previous_tag_is_the_nearest_spec_tag(self):
        # The document changed after the phase tag, and a revision approved that change;
        # a second revision that changes no document needs no row.
        revised = self.commit({DOC: "two\n"})
        self.approve("spec-p4r1", revised)
        later = self.commit({"README": "b\n"})
        self.assert_tagged(self.tag("spec-p4r2", later), "spec-p4r2", later)

    def test_the_records_are_read_at_the_commit_to_tag(self):
        # The row and the findings file arrive after the commit to tag, so they do not
        # count for it.
        changed = self.commit({DOC: "two\n"})
        self.commit({STATUS: status_text((DOC, changed, "rv1")),
                     "impl/log/rv1-findings.md": findings_text()})
        self.assert_refused(self.tag("spec-p4r1", changed), "review", DOC, "no row")

    def test_a_document_outside_specs_needs_no_row(self):
        changed = self.commit({"impl/notes.md": "notes\n", "README": "b\n"})
        self.assert_tagged(self.tag("spec-p4r1", changed), "spec-p4r1", changed)


class TagCleanupTest(TagCase):
    def test_the_temporary_clone_is_gone_after_a_success_and_after_a_refusal(self):
        changed = self.commit({DOC: "two\n"})
        self.approve("spec-p4", self.first)
        self.assert_refused(self.tag("spec-p4r1", changed), "review")
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assert_tagged(self.tag("impl-p4", changed), "impl-p4", changed)
        self.assertEqual(list(self.scratch.iterdir()), [])
