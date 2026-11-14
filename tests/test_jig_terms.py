import os

from tests.support import ROOT, TempTestCase, run

TERMS = ROOT / "libexec" / "jig-terms"

# The private words of the fixture project. They stand on lines 3 and 5 of its
# terms file, after a note, a blank line and a line of spaces, which hold no term.
FIRST, SECOND = "Quuxwidget", "Zorbleck"
TERMS_TEXT = f"# words that never go public\n\n{FIRST}\n   \n  {SECOND}  \n"
FIRST_LINE, SECOND_LINE = "terms.txt line 3", "terms.txt line 5"


class TermsCase(TempTestCase):
    """A -dev clone with a terms file and a clean product clone beside it.

    The conf file names the line 0.4, so the tags v0.4.<n> mark its releases.
    """

    def setUp(self):
        super().setUp()
        self.dev = self.git_init(self.tmp / "demo-dev")
        self.write(self.dev, {"terms.txt": TERMS_TEXT})
        self.git_commit_all(self.dev)
        conf = self.home / ".config" / "jig" / "demo.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f"REPO={self.dev}\nBRANCH=0.4\n", encoding="utf-8")
        self.product = self.git_init(self.tmp / "demo")
        self.write(self.product, {"src/tool.py": "print('a public tool')\n",
                                  "README.md": "A tool.\n", ".gitignore": "build/\n"})
        self.commit("Add the tool")

    def git(self, *args):
        result = run(["git", "-C", self.product, *args], env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def write(self, root, files):
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(text, bytes):
                path.write_bytes(text)
            else:
                path.write_text(text, encoding="utf-8")

    def commit(self, message, files=None):
        """Commit the product's tree with the message; return the new commit's hash.

        With no files, a counter file changes, so that the commit is not empty and its
        message is the only place a word of it stands.
        """
        self.count = getattr(self, "count", 0) + 1
        self.write(self.product, files or {"count.txt": f"{self.count}\n"})
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", message)
        return self.git("rev-parse", "HEAD")

    def terms(self, *args):
        return run([TERMS, *(args or ("check", "demo"))], env=self.env())

    def assert_found(self, result, lines):
        """The run exits 1, prints exactly these lines and neither private word."""
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout.splitlines(), lines)
        printed = (result.stdout + result.stderr).casefold()
        self.assertNotIn(FIRST.casefold(), printed)
        self.assertNotIn(SECOND.casefold(), printed)

    def assert_failed(self, result, words):
        """The run exits 1 with one line on stderr that holds the words, and no match."""
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn(words, result.stderr)


class TermsCleanTest(TermsCase):
    def test_a_clean_tree_exits_zero_and_prints_nothing(self):
        result = self.terms()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_an_ignored_file_is_not_read(self):
        # A build output that git ignores holds a term; git does not list it, so the
        # run stays clean.
        self.write(self.product, {"build/out.txt": f"{FIRST}\n"})
        self.assertEqual(self.terms().returncode, 0)

    def test_a_directory_with_clean_files_exits_zero(self):
        staged = self.tmp / "staged"
        self.write(staged, {"pkg.tar": b"\x00\x01public bytes\xff\n", "SHA256SUMS": "00 pkg\n"})
        result = self.terms("check", "demo", str(staged))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_gives_the_usage_and_exits_zero(self):
        result = self.terms("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: jig terms check <project> [<directory>]", result.stdout)


class TermsMatchTest(TermsCase):
    def test_a_term_in_a_tracked_file_gives_its_place_and_terms_line(self):
        # The term stands in other letters' case and inside a longer word; it still
        # matches, and the line names the terms line instead of the word.
        self.commit("Add notes", {"docs/notes.md": "intro\nsee the "
                                  f"{FIRST.upper()}s here\n"})
        self.assert_found(self.terms(), [f"demo/docs/notes.md:2: {FIRST_LINE}"])

    def test_a_term_in_a_new_file_that_git_does_not_ignore_is_found(self):
        self.write(self.product, {"draft.txt": f"by {SECOND.lower()}\n"})
        self.assert_found(self.terms(), [f"demo/draft.txt:1: {SECOND_LINE}"])

    def test_one_term_twice_on_a_line_gives_one_line_and_two_terms_give_two(self):
        self.commit("Add a page", {"page.txt": f"{FIRST} and {FIRST}\n{SECOND} {FIRST}\n"})
        self.assert_found(self.terms(), [f"demo/page.txt:1: {FIRST_LINE}",
                                         f"demo/page.txt:2: {FIRST_LINE}",
                                         f"demo/page.txt:2: {SECOND_LINE}"])

    def test_a_term_in_a_path_is_masked_in_every_line(self):
        # The file's name holds one term and its text the other: both lines print the
        # path with the term in it replaced, so neither word is printed.
        self.commit("Add a file", {f"docs/{SECOND.lower()}-notes.md": f"{FIRST}\n"})
        self.assert_found(self.terms(), [f"demo/docs/***-notes.md: {SECOND_LINE} in the path",
                                         f"demo/docs/***-notes.md:1: {FIRST_LINE}"])

    def test_a_term_in_a_staged_file_is_found_in_its_bytes_at_any_depth(self):
        staged = self.tmp / "staged"
        self.write(staged, {"deep/er/pkg.bin": b"\x00\xfe\xff" + FIRST.encode() + b"\x00\n",
                            "SHA256SUMS": "00 pkg\n"})
        result = self.terms("check", "demo", str(staged))
        self.assert_found(result, [f"{staged}/deep/er/pkg.bin:1: {FIRST_LINE}"])

    def test_a_term_in_the_name_of_the_directory_itself_is_no_match(self):
        # The directory that holds the staged files has a term in its name, and its
        # files hold none: the place of the directory is not published, so the run
        # exits 0 and prints nothing.
        staged = self.tmp / f"release-{SECOND.lower()}"
        self.write(staged, {"pkg.tar": b"public bytes\n", "SHA256SUMS": "00 pkg\n"})
        result = self.terms("check", "demo", str(staged))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_term_in_a_file_under_a_directory_with_a_term_gives_one_line_masked(self):
        # The directory's name holds one term and a file under it the other, in its
        # bytes: one line, for the bytes, whose path has the directory's term masked.
        staged = self.tmp / f"release-{SECOND.lower()}"
        self.write(staged, {"pkg.bin": b"\x00" + FIRST.encode() + b"\n"})
        result = self.terms("check", "demo", str(staged))
        self.assert_found(result, [f"{self.tmp}/release-***/pkg.bin:1: {FIRST_LINE}"])


class TermsMessageTest(TermsCase):
    def test_a_term_in_a_message_after_the_last_release_gives_one_line(self):
        self.commit(f"Mention {FIRST} before the release")
        self.git("tag", "v0.4.0")
        self.commit("A public change")
        self.assertEqual(self.terms().returncode, 0)
        after = self.commit(f"Mention {SECOND.upper()} after the release")
        self.assert_found(self.terms(), [f"commit {after}: {SECOND_LINE}"])

    def test_the_newest_release_by_version_bounds_the_messages(self):
        # Tags 9 and 10 of the line: by version 10 is newer, though it sorts first by
        # text. A tag of another line is not a release of this one.
        self.commit(f"First {FIRST}")
        self.git("tag", "v0.4.9")
        self.commit(f"Second {FIRST}")
        self.git("tag", "v0.4.10")
        last = self.commit(f"Third {FIRST}")
        self.git("tag", "v0.5.0")
        self.assert_found(self.terms(), [f"commit {last}: {FIRST_LINE}"])

    def test_a_tag_that_head_does_not_reach_is_not_the_last_release(self):
        # The tag sits on a side branch, so every commit of HEAD counts.
        early = self.commit(f"Early {FIRST}")
        self.git("checkout", "-q", "-b", "side")
        self.commit("Side work")
        self.git("tag", "v0.4.3")
        self.git("checkout", "-q", "-")
        self.assert_found(self.terms(), [f"commit {early}: {FIRST_LINE}"])

    def test_with_no_release_every_commit_counts(self):
        first = self.commit(f"Start with {FIRST}")
        second = self.commit(f"Then {SECOND}")
        self.assert_found(self.terms(), [f"commit {second}: {SECOND_LINE}",
                                         f"commit {first}: {FIRST_LINE}"])


class TermsFailureTest(TermsCase):
    def test_a_missing_terms_file_fails_with_one_line(self):
        (self.dev / "terms.txt").unlink()
        self.assert_failed(self.terms(), f"terms file {self.dev / 'terms.txt'} does not exist")

    def test_an_unreadable_terms_file_fails_with_one_line(self):
        path = self.dev / "terms.txt"
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        if os.access(path, os.R_OK):
            self.skipTest("this account reads a file of mode 0")
        self.assert_failed(self.terms(), f"cannot read terms file {path}")

    def test_a_terms_file_with_only_notes_and_blank_lines_fails(self):
        self.write(self.dev, {"terms.txt": "# nothing yet\n\n  \n"})
        self.assert_failed(self.terms(), f"terms file {self.dev / 'terms.txt'} holds no term")

    def test_an_empty_terms_file_fails(self):
        self.write(self.dev, {"terms.txt": ""})
        self.assert_failed(self.terms(), "holds no term")

    def test_a_missing_product_clone_fails(self):
        # The -dev clone of the project other has its terms but no product clone
        # beside it or inside it: the check fails and names the place beside it.
        other = self.git_init(self.tmp / "other-dev")
        self.write(other, {"terms.txt": TERMS_TEXT})
        conf = self.home / ".config" / "jig" / "other.conf"
        conf.write_text(f"REPO={other}\nBRANCH=0.4\n", encoding="utf-8")
        self.assert_failed(self.terms("check", "other"),
                           f"no product clone at {self.tmp / 'other'}")

    def test_a_missing_directory_fails(self):
        missing = self.tmp / "nowhere"
        self.assert_failed(self.terms("check", "demo", str(missing)),
                           f"{missing} is not a directory")

    def test_a_project_without_a_conf_file_fails(self):
        self.assert_failed(self.terms("check", "absent"), "absent.conf")


class TermsUsageTest(TermsCase):
    def test_usage_errors_exit_two(self):
        for args in ([], ["check"], ["scan", "demo"], ["check", "Bad/Name"],
                     ["check", "demo", "a", "b"]):
            with self.subTest(args=args):
                result = run([TERMS, *args], env=self.env())
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("usage: jig terms check", result.stderr)

