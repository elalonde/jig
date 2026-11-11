import unittest

from tests.support import ROOT, TempTestCase, run

SCRIPT = ROOT / "tools" / "push-on-token.sh"
TOKEN = "planted-token-value"
ASK = "protocol=https\nhost=github.com\n\n"
GLOBAL_CONFIG = """[credential]
\thelper = "!f() { echo username=someone; echo password=from-global-plain; }; f"
[credential "https://github.com"]
\thelper = "!f() { echo username=someone; echo password=from-global-scoped; }; f"
"""


class PushOnTokenTest(TempTestCase):
    def setUp(self):
        super().setUp()
        (self.home / ".gitconfig").write_text(GLOBAL_CONFIG)
        self.clone = self.git_init(self.tmp / "clone")
        self.origin = "deploy-alias:someowner/somerepo.git"
        run(["git", "-C", self.clone, "remote", "add", "origin", self.origin], env=self.env())
        self.token = self.tmp / "token"
        self.token.write_text(f"{TOKEN}\n")

    def setup_clone(self, token=None):
        return run([SCRIPT, self.clone, token or self.token], env=self.env())

    def config(self, *args):
        return run(["git", "-C", self.clone, "config", *args], env=self.env()).stdout

    def fill(self):
        return run(["git", "-C", self.clone, "credential", "fill"], env=self.env(), stdin=ASK)

    def test_push_on_token_sets_an_https_push_url(self):
        self.assertEqual(self.setup_clone().returncode, 0)
        self.assertEqual(self.config("--local", "remote.origin.pushurl").strip(),
                         "https://github.com/someowner/somerepo.git")

    def test_push_on_token_leaves_fetch_on_the_deploy_key(self):
        self.assertEqual(self.setup_clone().returncode, 0)
        self.assertEqual(self.config("--local", "remote.origin.url").strip(), self.origin)

    def test_push_on_token_helper_reads_the_token_file(self):
        self.assertEqual(self.setup_clone().returncode, 0)
        result = self.fill()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"password={TOKEN}\n", result.stdout)
        self.assertIn("username=x-access-token\n", result.stdout)

    def test_push_on_token_global_helpers_do_not_apply(self):
        before = self.fill()
        self.assertIn("from-global", before.stdout)
        self.assertEqual(self.setup_clone().returncode, 0)
        self.assertNotIn("from-global", self.fill().stdout)

    def test_push_on_token_empty_entry_comes_first(self):
        self.assertEqual(self.setup_clone().returncode, 0)
        helpers = self.config("--local", "--get-all", "credential.helper").split("\n")
        self.assertEqual(helpers[0], "")
        self.assertTrue(helpers[1].startswith("!f()"))

    def test_push_on_token_keeps_the_token_out_of_the_config(self):
        self.assertEqual(self.setup_clone().returncode, 0)
        self.assertNotIn(TOKEN, (self.clone / ".git" / "config").read_text())

    def test_push_on_token_leaves_the_global_config_alone(self):
        self.assertEqual(self.setup_clone().returncode, 0)
        self.assertEqual((self.home / ".gitconfig").read_text(), GLOBAL_CONFIG)

    def test_push_on_token_empty_token_file_stops_the_attempt(self):
        self.token.write_text("")
        self.assertEqual(self.setup_clone().returncode, 0)
        result = self.fill()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("password=", result.stdout)

    def test_push_on_token_missing_token_file_stops_the_attempt(self):
        self.assertEqual(self.setup_clone(self.tmp / "absent").returncode, 0)
        result = self.fill()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("password=", result.stdout)

    def test_push_on_token_is_safe_to_run_twice(self):
        self.assertEqual(self.setup_clone().returncode, 0)
        first = self.config("--local", "--get-all", "credential.helper")
        self.assertEqual(self.setup_clone().returncode, 0)
        self.assertEqual(self.config("--local", "--get-all", "credential.helper"), first)
        self.assertIn(f"password={TOKEN}\n", self.fill().stdout)

    def test_push_on_token_a_second_run_changes_the_token_path(self):
        self.assertEqual(self.setup_clone().returncode, 0)
        other = self.tmp / "other-token"
        other.write_text("other-token-value\n")
        self.assertEqual(self.setup_clone(other).returncode, 0)
        self.assertIn("password=other-token-value\n", self.fill().stdout)

    def test_push_on_token_handles_quotes_and_spaces_in_the_path(self):
        odd = self.tmp / "it's a token"
        odd.write_text(f"{TOKEN}\n")
        self.assertEqual(self.setup_clone(odd).returncode, 0)
        self.assertIn(f"password={TOKEN}\n", self.fill().stdout)

    def test_push_on_token_helper_ignores_store_and_erase(self):
        self.assertEqual(self.setup_clone().returncode, 0)
        approve = run(["git", "-C", self.clone, "credential", "approve"], env=self.env(),
                      stdin="protocol=https\nhost=github.com\nusername=a\npassword=b\n\n")
        self.assertEqual(approve.returncode, 0, approve.stderr)
        self.assertIn(f"password={TOKEN}\n", self.fill().stdout)

    def test_push_on_token_refuses_an_origin_with_no_repo_path(self):
        run(["git", "-C", self.clone, "remote", "set-url", "origin", "nothing"], env=self.env())
        result = self.setup_clone()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.config("--local", "remote.origin.pushurl"), "")

    def test_push_on_token_needs_both_arguments(self):
        result = run([SCRIPT, self.clone], env=self.env())
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
