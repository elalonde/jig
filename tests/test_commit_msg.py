import shutil
import unittest

from tests.support import CO_AUTHOR_ADDRESS, PUBLIC_ADDRESS, ROOT, TempTestCase, run

HOOK = ROOT / "hooks" / "commit-msg"
CO_AUTHOR = f"Co-Authored-By: Claude Opus 5.5 <{CO_AUTHOR_ADDRESS}>"
SESSION = "Claude-Session: https://claude.example/session/abc123"
TERM = "zanthorpe"


class CommitMsgTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.terms = self.tmp / "planted-terms"
        self.terms.write_text(f"{TERM}\n")

    def check(self, message, **extra):
        path = self.tmp / "message"
        path.write_text(message)
        extra.setdefault("LINT_TERMS", str(self.terms))
        return run([HOOK, path], env=self.env(**extra))

    def assert_refused(self, message, needle):
        result = self.check(message)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(needle, result.stderr)

    def assert_allowed(self, message):
        result = self.check(message)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_commit_msg_allows_a_plain_subject(self):
        self.assert_allowed("Add the dispatcher\n")

    def test_commit_msg_allows_both_trailers(self):
        self.assert_allowed(f"Add the dispatcher\n\nBecause.\n\n{CO_AUTHOR}\n{SESSION}\n")

    def test_commit_msg_refuses_a_third_trailer(self):
        message = f"Add the dispatcher\n\n{CO_AUTHOR}\n{SESSION}\nReviewed-By: Some One\n"
        self.assert_refused(message, "trailer not allowed: Reviewed-By")

    def test_commit_msg_refuses_a_lone_foreign_trailer(self):
        self.assert_refused("Add the dispatcher\n\nSigned-off-by: Some One\n",
                            "trailer not allowed: Signed-off-by")

    def test_commit_msg_refuses_a_trailer_directly_under_the_subject(self):
        self.assert_refused("Add the dispatcher\nReviewed-By: Some One\n",
                            "trailer not allowed")

    def test_commit_msg_allows_a_colon_in_the_subject_and_body(self):
        self.assert_allowed(f"fix: the dispatcher\n\nNote: this is prose.\n\n{CO_AUTHOR}\n")

    def test_commit_msg_allows_a_url_in_the_last_paragraph(self):
        self.assert_allowed("Add the dispatcher\n\nhttps://example.invalid/doc\n")

    def test_commit_msg_refuses_a_unit_id_in_the_subject(self):
        self.assert_refused("zq7ab: add the dispatcher\n", "unit ID: zq7ab")

    def test_commit_msg_refuses_a_record_id_in_the_body(self):
        self.assert_refused("Add the dispatcher\n\nThis closes R-12.\n", "record ID: R-12")

    def test_commit_msg_refuses_an_ipv4_address(self):
        self.assert_refused("Add the dispatcher\n\nTried on 172.16.4.9.\n", "172.16.4.9")

    def test_commit_msg_refuses_another_email_address(self):
        self.assert_refused("Add the dispatcher\n\nAsk someone@example.org.\n",
                            "someone@example.org")

    def test_commit_msg_refuses_a_home_path(self):
        self.assert_refused("Add the dispatcher\n\nFrom /home/someone/ws.\n", "/home/someone")

    def test_commit_msg_refuses_a_planted_term(self):
        self.assert_refused(f"Add the {TERM.title()} support\n", TERM.title())

    def test_commit_msg_refuses_a_planted_term_in_a_trailer(self):
        self.assert_refused(f"Add it\n\nCo-Authored-By: {TERM} <{CO_AUTHOR_ADDRESS}>\n", TERM)

    def test_commit_msg_refuses_another_address_in_the_co_author_trailer(self):
        self.assert_refused("Add it\n\nCo-Authored-By: Some One <someone@example.org>\n",
                            "someone@example.org")

    def test_commit_msg_allows_the_anthropic_address_only_in_the_co_author_trailer(self):
        self.assert_refused(f"Add it\n\nAsk {CO_AUTHOR_ADDRESS} about it.\n",
                            CO_AUTHOR_ADDRESS)
        self.assert_refused(f"Add it\n\nClaude-Session: {CO_AUTHOR_ADDRESS}\n",
                            CO_AUTHOR_ADDRESS)

    def test_commit_msg_allows_the_public_address(self):
        self.assert_allowed(f"Add it\n\nAsk {PUBLIC_ADDRESS} about it.\n")

    def test_commit_msg_allows_the_wording_that_comments_and_docs_refuse(self):
        self.assert_allowed("Reword section 4 as required\n\n"
                            "Step 3: the rule above and the rule below.\n"
                            "Case 2. See § 6.\n\n" + CO_AUTHOR + "\n")

    def test_commit_msg_reads_comment_lines_git_may_keep(self):
        self.assert_refused(f"Add it\n\n# built on {TERM}\n", TERM)

    def test_commit_msg_stops_at_the_scissors_line(self):
        scissors = "# ------------------------ >8 ------------------------"
        self.assert_allowed(f"Add it\n\n{scissors}\n+ someone@example.org\n")

    def test_commit_msg_missing_terms_file_fails(self):
        result = self.check("Add it\n", LINT_TERMS=str(self.tmp / "absent"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing", result.stderr)

    def test_commit_msg_ci_without_terms_runs_generic_only(self):
        result = self.check(f"Add {TERM}\n", LINT_TERMS=str(self.tmp / "absent"), CI="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("generic patterns only", result.stdout)

    def test_commit_msg_make_hook_installs_it_in_the_clone(self):
        clone = self.git_init(self.tmp / "clone")
        for name in ("hooks", "lib"):
            shutil.copytree(ROOT / name, clone / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "Makefile", clone)
        env = self.env(LINT_TERMS=str(self.terms))
        result = run(["make", "-C", clone, "hook"], env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        run(["git", "-C", clone, "add", "-A"], env=env)

        refused = run(["git", "-C", clone, "commit", "-q", "-m", "Add it",
                       "-m", "Reviewed-By: Some One"], env=env)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("trailer not allowed", refused.stderr)

        accepted = run(["git", "-C", clone, "commit", "-q", "-m", "Add it", "-m", CO_AUTHOR],
                       env=env)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)


if __name__ == "__main__":
    unittest.main()
