import os

from tests.support import ROOT, TempTestCase, run

PRIVACY = ROOT / "libexec" / "jig-privacy"

# The serial of the fixture project stands on line 2 of its terms file.
SERIAL = "ZQ9917XK"
TERMS_TEXT = f"# serials of the bench units\n{SERIAL}\n"

# The fixture host list: a local host, a remote host by address, and a host in a
# domain that documentation uses, whose name and address never match.
HOST_NAME, HOST_ADDRESS = "quillbox", "quillbox.local"
FAR_NAME, FAR_ADDRESS = "farquill", "198.51.100.23"
HOSTS_TEXT = f"""[[host]]
name = "{HOST_NAME}"
address = "{HOST_ADDRESS}"
local = true

[[host]]
name = "ci.example.net"
address = "build.test"
local = false

[[host]]
name = "{FAR_NAME}"
address = "{FAR_ADDRESS}"
local = false
"""

# Private values are joined from pieces, so that this file holds none of them whole
# and the scan of the product's own tests stays clean.
UUID = "-".join(["1b4e28ba", "2fa1", "41d2", "883f", "0016d3cca427"])
MAC_COLONS = ":".join(["02", "1a", "c4", "7e", "33", "9b"])
MAC_HYPHENS = "-".join(["02", "1A", "C4", "7E", "33", "9C"])
EMAIL = "@".join(["ann.lee", "quillmail.zz"])
# One step outside a documentation range of MAC addresses.
NEAR_MAC = ":".join(["00", "00", "5E", "00", "54", "01"])
NEAR_EMAIL = "@".join(["ann", "example.community"])

SEEDS = {
    "serial": (SERIAL.lower(), "terms.txt line 2"),
    "host name": (HOST_NAME.upper(), "host name of host 1 in the host list"),
    "host address": (FAR_ADDRESS, "host address of host 3 in the host list"),
    "UUID": (UUID.upper(), "UUID"),
    "MAC address with colons": (MAC_COLONS, "MAC address"),
    "MAC address with hyphens": (MAC_HYPHENS, "MAC address"),
    "MAC address next to a documentation range": (NEAR_MAC, "MAC address"),
    "e-mail address": (EMAIL, "e-mail address"),
    "e-mail address in a domain like a reserved one": (NEAR_EMAIL, "e-mail address"),
}

# Values that documentation uses: none of them is a match.
DOCUMENTATION = [
    "00000000-0000-0000-0000-000000000000",
    "00:00:5E:00:53:00", "00:00:5e:00:53:ff", "01-00-5E-90-10-00", "01:00:5E:90:10:FF",
    "someone@example.com", "a.b@mail.example.org", "x@example.net", "y@example",
    "z@host.test", "w@nowhere.invalid", "v@dev.localhost",
    "ci.example.net", "build.test",
]

PATTERNS_TEXT = """[process IDs]
[control IDs]
[language python3]
files tests/[^/]+\\.py
names (?<=def )test[A-Za-z0-9_]*
strings "[^"]*"
"""
CHECKS_TEXT = ("name\tcommand\taccount\tcap\n"
               "recompute\tjig recompute demo tests/vectors\tspecwriter\t60\n")


class PrivacyCase(TempTestCase):
    """A -dev clone with an origin, a terms file and a checks.tsv, a product nested in it,
    and a host list under a stand-in for /srv/jig.

    The row recompute names tests/vectors as the product's vector directory; the
    pattern file makes each .py file directly under tests/ a test file. Nothing holds a
    private value, and neither captures/ nor vectors/ nor the vector directory exists.
    """

    def setUp(self):
        super().setUp()
        self.srv = self.tmp / "srv"
        self.write(self.srv, {"hosts.toml": HOSTS_TEXT})
        self.dev = self.git_init(self.tmp / "dev")
        self.write(self.dev, {"terms.txt": TERMS_TEXT, "checks.tsv": CHECKS_TEXT,
                              ".gitignore": "demo/\n"})
        self.git_commit_all(self.dev)
        self.push_dev()
        conf = self.home / ".config" / "jig" / "demo.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f"REPO={self.dev}\nBRANCH=master\n", encoding="utf-8")
        self.product = self.git_init(self.dev / "demo")
        self.write(self.product, {"tools/lint-a-patterns.txt": PATTERNS_TEXT,
                                  "tests/test_tool.py": "def test_tool():\n    pass\n",
                                  "src/tool.py": "print('a tool')\n"})
        self.git_commit_all(self.product)

    def git(self, *args):
        result = run(["git", *args], env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)

    def push_dev(self):
        """Push the -dev clone to a bare origin, so that origin/master holds checks.tsv."""
        origin = self.tmp / "dev.git"
        if not origin.exists():
            self.git("init", "-q", "--bare", "-b", "master", origin)
            self.git("-C", self.dev, "remote", "add", "origin", origin)
        self.git("-C", self.dev, "push", "-q", "origin", "master")
        self.git("-C", self.dev, "fetch", "-q", "origin")

    def write(self, root, files):
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def scan(self, *args):
        return run([PRIVACY, *(args or ("scan", "demo"))], env=self.env(JIG_SRV=str(self.srv)))

    def assert_found(self, result, lines, values):
        """The run exits 1, prints exactly these lines and none of the values."""
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout.splitlines(), lines)
        printed = (result.stdout + result.stderr).casefold()
        for value in values:
            self.assertNotIn(value.casefold(), printed)

    def assert_failed(self, result, words):
        """The run exits 1 with one line on stderr that holds the words, and no match."""
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn(words, result.stderr)


class PrivacyCleanTest(PrivacyCase):
    def test_a_clean_tree_without_the_scanned_directories_exits_zero(self):
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_documentation_values_give_no_line(self):
        # Every place the scan reads holds each documentation value, one per line.
        text = "".join(f"value {value} here\n" for value in DOCUMENTATION)
        self.write(self.dev, {"captures/doc.txt": text, "vectors/doc.txt": text})
        self.write(self.product, {"tests/test_doc.py": text, "tests/vectors/doc.txt": text})
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "")

    def test_a_private_value_outside_the_scanned_files_gives_no_line(self):
        # Product code and a file below tests/ that is not a test file hold values,
        # and the -dev clone holds one outside captures/ and vectors/.
        self.write(self.product, {"src/ids.py": f"ID = '{UUID}'\n",
                                  "tests/data/seed.txt": f"{EMAIL}\n"})
        self.write(self.dev, {"notes/mail.txt": f"{EMAIL}\n"})
        self.assertEqual(self.scan().returncode, 0)

    def test_without_the_row_recompute_the_vector_directory_is_not_read(self):
        self.write(self.dev, {"checks.tsv": "name\tcommand\taccount\tcap\n"})
        self.git_commit_all(self.dev)
        self.push_dev()
        self.write(self.product, {"tests/vectors/seed.txt": f"{UUID}\n"})
        self.assertEqual(self.scan().returncode, 0)

    def test_without_a_pattern_file_the_product_has_no_test_file(self):
        (self.product / "tools" / "lint-a-patterns.txt").unlink()
        self.write(self.product, {"tests/test_seed.py": f"MAIL = '{EMAIL}'\n"})
        self.assertEqual(self.scan().returncode, 0)

    def test_help_gives_the_usage_and_exits_zero(self):
        result = self.scan("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: jig privacy scan <project>", result.stdout)


class PrivacyMatchTest(PrivacyCase):
    def test_each_kind_of_seeded_value_gives_one_line(self):
        values = [value for value, _ in SEEDS.values()]
        for kind, (value, label) in SEEDS.items():
            with self.subTest(kind=kind):
                self.write(self.product, {"tests/test_seed.py":
                                          f"def test_seed():\n    x = '{value}'\n"})
                self.assert_found(self.scan(), [f"demo/tests/test_seed.py:2: {label}"],
                                  values)

    def test_each_place_is_read_at_any_depth(self):
        # The same e-mail address stands in each of the four places.
        line = f"mail {EMAIL}\n"
        self.write(self.dev, {"captures/run/one/log.txt": line, "vectors/set/v.txt": line})
        self.write(self.product, {"tests/test_mail.py": line,
                                  "tests/vectors/deep/v.txt": "x\n" + line})
        self.assert_found(self.scan(), ["captures/run/one/log.txt:1: e-mail address",
                                        "vectors/set/v.txt:1: e-mail address",
                                        "demo/tests/test_mail.py:1: e-mail address",
                                        "demo/tests/vectors/deep/v.txt:2: e-mail address"],
                          [EMAIL])

    def test_kinds_on_one_line_each_give_a_line_and_a_repeat_gives_none(self):
        self.write(self.dev, {"captures/mix.txt":
                              f"{EMAIL} {UUID} {EMAIL} {HOST_NAME} {SERIAL}\n"})
        self.assert_found(self.scan(), ["captures/mix.txt:1: terms.txt line 2",
                                        "captures/mix.txt:1: host name of host 1 in the "
                                        "host list",
                                        "captures/mix.txt:1: UUID",
                                        "captures/mix.txt:1: e-mail address"],
                          [EMAIL, UUID, HOST_NAME, SERIAL])

    def test_a_value_in_a_path_is_masked_in_every_line(self):
        self.write(self.dev, {f"captures/{UUID}.json": f"{{\"mac\": \"{MAC_COLONS}\"}}\n"})
        self.assert_found(self.scan(), ["captures/***.json: UUID in the path",
                                        "captures/***.json:1: MAC address"],
                          [UUID, MAC_COLONS])

    def test_a_vector_directory_that_is_not_tracked_yet_is_read(self):
        self.write(self.product, {"tests/vectors/new.txt": f"{SERIAL}\n"})
        self.assert_found(self.scan(), ["demo/tests/vectors/new.txt:1: terms.txt line 2"],
                          [SERIAL])


class PrivacyFailureTest(PrivacyCase):
    def test_a_missing_terms_file_fails_with_one_line(self):
        (self.dev / "terms.txt").unlink()
        self.assert_failed(self.scan(), f"terms file {self.dev / 'terms.txt'} does not exist")

    def test_an_unreadable_terms_file_fails_with_one_line(self):
        path = self.dev / "terms.txt"
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        if os.access(path, os.R_OK):
            self.skipTest("this account reads a file of mode 0")
        self.assert_failed(self.scan(), f"cannot read terms file {path}")

    def test_an_empty_terms_file_fails_with_one_line(self):
        self.write(self.dev, {"terms.txt": "# no serial yet\n\n"})
        self.assert_failed(self.scan(), f"terms file {self.dev / 'terms.txt'} holds no term")

    def test_a_missing_host_list_fails_with_one_line(self):
        (self.srv / "hosts.toml").unlink()
        self.assert_failed(self.scan(), f"host list {self.srv / 'hosts.toml'} does not exist")

    def test_an_invalid_host_list_fails_with_one_line(self):
        self.write(self.srv, {"hosts.toml": '[[host]]\nname = "solo"\naddress = "solo"\n'
                                            'local = true\n'})
        self.assert_failed(self.scan(), f"host list {self.srv / 'hosts.toml'} is not valid")

    def test_a_pattern_file_that_lint_a_refuses_fails_with_its_one_line(self):
        self.write(self.product, {"tools/lint-a-patterns.txt": "[process IDs]\n[process IDs]\n"
                                                               "[control IDs]\n"})
        self.assert_failed(self.scan(),
                           f"{self.product / 'tools' / 'lint-a-patterns.txt'}:2: the part "
                           "[process IDs] appears twice")

    def test_a_row_recompute_that_names_no_directory_fails(self):
        self.write(self.dev, {"checks.tsv": "name\tcommand\taccount\tcap\n"
                                            "recompute\tjig recompute demo /abs\tspecwriter\t60\n"})
        self.git_commit_all(self.dev)
        self.push_dev()
        self.assert_failed(self.scan(), "the row recompute of checks.tsv on origin/master "
                                        "names no product directory")

    def test_a_missing_product_clone_fails(self):
        conf = self.home / ".config" / "jig" / "other.conf"
        conf.write_text(f"REPO={self.dev}\nBRANCH=master\n", encoding="utf-8")
        self.assert_failed(self.scan("scan", "other"), f"no product clone at {self.dev / 'other'}")


class PrivacyUsageTest(PrivacyCase):
    def test_usage_errors_exit_two(self):
        for args in ([], ["scan"], ["check", "demo"], ["scan", "Bad/Name"],
                     ["scan", "demo", "extra"]):
            with self.subTest(args=args):
                result = run([PRIVACY, *args], env=self.env(JIG_SRV=str(self.srv)))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("usage: jig privacy scan", result.stderr)
