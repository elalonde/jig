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
        """Commit files in the work clone and push its branch; return the full hash."""
        for name, text in files.items():
            path = self.work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for name in removed:
            (self.work / name).unlink()
        self.ok(["git", "-C", self.work, "add", "-A"])
        self.ok(["git", "-C", self.work, "commit", "-q", "--no-verify", "-m", subject])
        self.ok(["git", "-C", self.work, "push", "-q", "origin", "HEAD"])
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
    def test_each_of_the_six_forms_is_tagged_pushed_and_read_back(self):
        # Each revision follows its base tag on the same commit, so no document differs.
        # A tag of a version line needs that line in BRANCH, so origin holds the lines.
        for line in ("1.2", "12.30"):
            self.ok(["git", "-C", self.work, "push", "-q", "origin", f"HEAD:refs/heads/{line}"])
        for tag, line in (("spec-p3", LINE), ("spec-p3r1", LINE), ("impl-p3", LINE),
                          ("kickoff-r12", LINE), ("spec-1.2", "1.2"), ("spec-1.2r1", "1.2"),
                          ("spec-12.30", "12.30"), ("spec-12.30r10", "12.30")):
            with self.subTest(tag):
                result = self.tag(tag, self.first, BRANCH=line)
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

    def test_a_tag_of_none_of_the_six_forms_is_refused(self):
        # The form is checked before the line, so BRANCH may hold the line a tag names.
        for tag in ("spec-p", "spec-px", "spec-p3r", "spec-p3r1x", "impl-p3r1", "kickoff-1",
                    "kickoff-rx", "v0.3.0", "release-p3", "spec-p-3", "spec-1", "spec-1.",
                    "spec-.2", "spec-1.2.3", "spec-1.2r", "spec-1.2r1.4", "spec-1.2-r1",
                    "spec-1.2R1", "spec-p1.2", "impl-1.2", "kickoff-1.2", "spec-1,2",
                    "spec-1.2x"):
            with self.subTest(tag):
                self.assert_refused(self.tag(tag, self.first, BRANCH="1.2"), "form", tag)
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


class TagVersionLineTest(TagCase):
    """The tags of a project that ships releases: spec-X.Y and its revisions spec-X.YrM.

    Origin holds the lines 1.2 and 1.3, so a refusal for the line comes from the tag's
    own X.Y and not from a line that origin lacks. The work clone writes on 1.2.
    """

    def setUp(self):
        super().setUp()
        self.ok(["git", "-C", self.work, "checkout", "-q", "-b", "1.2"])
        self.ok(["git", "-C", self.work, "push", "-q", "origin", "HEAD"])
        self.ok(["git", "-C", self.work, "push", "-q", "origin", "HEAD:refs/heads/1.3"])

    def closed_loop(self, document_commit, unit="rv1"):
        """Commit the row and the clean findings file of a loop that read document_commit."""
        return self.commit({STATUS: status_text((DOC, document_commit, unit)),
                            f"impl/log/{unit}-findings.md": findings_text("info")})

    def test_a_line_tag_is_made_when_branch_names_its_line(self):
        self.assert_tagged(self.tag("spec-1.2", self.first, BRANCH="1.2"),
                           "spec-1.2", self.first)

    def test_a_line_tag_for_another_line_than_branch_is_refused(self):
        # 1.3 is on origin and holds the commit, so only the tag's own line can refuse it.
        # 1.20 shows that the line is compared whole, not as a prefix.
        for tag, branch in (("spec-1.2", "1.3"), ("spec-1.2r1", "1.3"), ("spec-1.3", "1.2"),
                            ("spec-1.20", "1.2")):
            with self.subTest(tag=tag, branch=branch):
                self.assert_refused(self.tag(tag, self.first, BRANCH=branch), "line", tag,
                                    f"BRANCH is {branch}")
                self.assertIsNone(self.on_origin(tag))

    def test_a_line_revision_whose_changed_document_has_no_closed_loop_is_refused(self):
        self.approve("spec-1.2", self.first)
        changed = self.commit({DOC: "two\n"})
        self.assert_refused(self.tag("spec-1.2r1", changed, BRANCH="1.2"), "review",
                            "since spec-1.2", DOC, "no row")
        self.assertIsNone(self.on_origin("spec-1.2r1"))

    def test_a_line_revision_whose_changed_document_has_a_closed_loop_is_tagged(self):
        self.approve("spec-1.2", self.first)
        changed = self.commit({DOC: "two\n"})
        recorded = self.closed_loop(changed)
        self.assert_tagged(self.tag("spec-1.2r1", recorded, BRANCH="1.2"),
                           "spec-1.2r1", recorded)

    def test_a_line_revision_compares_with_a_phase_tag_when_that_is_nearest(self):
        # The project switched to version lines without a spec-X.Y tag in between, so the
        # last phase tag is the previous approval: the change since it needs a loop.
        self.approve("spec-p4", self.first)
        changed = self.commit({DOC: "two\n"})
        self.assert_refused(self.tag("spec-1.2r1", changed, BRANCH="1.2"), "review",
                            "since spec-p4", DOC, "no row")
        recorded = self.closed_loop(changed)
        self.assert_tagged(self.tag("spec-1.2r1", recorded, BRANCH="1.2"),
                           "spec-1.2r1", recorded)

    def test_a_line_tag_after_a_phase_tag_is_the_nearest_previous_tag(self):
        # The document changed after the phase tag, and spec-1.2 approved that change, so
        # a revision that changes nothing more needs no row.
        self.approve("spec-p4", self.first)
        changed = self.commit({DOC: "two\n"})
        self.approve("spec-1.2", changed)
        later = self.commit({"README": "b\n"})
        self.assert_tagged(self.tag("spec-1.2r1", later, BRANCH="1.2"), "spec-1.2r1", later)


class TagCleanupTest(TagCase):
    def test_the_temporary_clone_is_gone_after_a_success_and_after_a_refusal(self):
        changed = self.commit({DOC: "two\n"})
        self.approve("spec-p4", self.first)
        self.assert_refused(self.tag("spec-p4r1", changed), "review")
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assert_tagged(self.tag("impl-p4", changed), "impl-p4", changed)
        self.assertEqual(list(self.scratch.iterdir()), [])
