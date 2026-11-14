import pathlib

from tests.support import ROOT, TempTestCase, run

RECOMPUTE = ROOT / "libexec" / "jig-recompute"
PRODUCT_DIR = "tests/vectors"

# The inputs of the fixture project and the vectors its script makes from them: each
# vector is its input in capitals, so a vector edited by hand is rebuilt to other bytes.
INPUTS = {"a.txt": "alpha\n", "b.txt": "bravo\n", "sub/c.txt": "charlie\n"}
VECTORS = {f"vectors/{name}": text.upper() for name, text in INPUTS.items()}
# The product holds a copy of two of the three vectors; the one-way compare allows that.
PRODUCT_COPY = {f"{PRODUCT_DIR}/a.txt": "ALPHA\n", f"{PRODUCT_DIR}/sub/c.txt": "CHARLIE\n"}

BUILD_SCRIPT = """#!/bin/sh
set -e
echo build >> "{log}"
mkdir -p vectors/sub
for name in a.txt b.txt sub/c.txt; do
    tr a-z A-Z < "inputs/$name" > "vectors/$name"
done
"""


class RecomputeCase(TempTestCase):
    """A -dev clone with inputs, vectors and a recompute script, and a product clone beside it.

    The product clone has its own origin, which also holds a copy of the vectors. The
    script appends a line to ran.log in the test directory each time it runs.
    """

    def setUp(self):
        super().setUp()
        self.log = self.tmp / "ran.log"
        self.dev = self.git_init(self.tmp / "demo-dev")
        self.write(self.dev, {**VECTORS,
                              **{f"inputs/{name}": text for name, text in INPUTS.items()}})
        self.add_script("10-build", BUILD_SCRIPT.format(log=self.log))
        self.git_commit_all(self.dev)
        conf = self.home / ".config" / "jig" / "demo.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f"REPO={self.dev}\nBRANCH=master\n", encoding="utf-8")

        self.product_origin = self.tmp / "product.git"
        self.ok("init", "-q", "--bare", "-b", "master", self.product_origin)
        self.product_seed = self.git_init(self.tmp / "product-seed")
        self.ok("-C", self.product_seed, "remote", "add", "origin", self.product_origin)
        self.push_product({"src/tool.py": "print('a tool')\n", **PRODUCT_COPY})
        self.product = self.tmp / "demo"
        self.ok("clone", "-q", "-b", "master", self.product_origin, self.product)

    def ok(self, *args):
        result = run(["git", *args], env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def write(self, root, files):
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def add_script(self, name, text, executable=True):
        path = self.dev / "vectors" / "recompute" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755 if executable else 0o644)
        return path

    def commit_dev(self, files=None):
        self.write(self.dev, files or {})
        self.git_commit_all(self.dev)

    def push_product(self, files, removed=()):
        """Commit a change to the product from another clone and push it to origin."""
        self.write(self.product_seed, files)
        for name in removed:
            (self.product_seed / name).unlink()
        self.git_commit_all(self.product_seed)
        self.ok("-C", self.product_seed, "push", "-q", "origin", "master")

    def recompute(self, *args):
        return run([RECOMPUTE, *(args or ("demo", PRODUCT_DIR))], env=self.env())

    def runs(self):
        return self.log.read_text(encoding="utf-8").count("\n") if self.log.exists() else 0

    def dev_status(self):
        return self.ok("-C", self.dev, "status", "--porcelain", "--untracked-files=all")


class RecomputePassTest(RecomputeCase):
    def test_a_script_that_rebuilds_every_vector_exits_zero(self):
        # The script writes the same bytes as the commit and the product copy holds them.
        result = self.recompute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.runs(), 1)
        self.assertEqual(self.dev_status(), "")

    def test_a_product_copy_with_fewer_files_than_vectors_exits_zero(self):
        # The product lacks vectors/b.txt and also drops sub/c.txt: one way, so still 0.
        self.push_product({}, removed=[f"{PRODUCT_DIR}/sub/c.txt"])
        result = self.recompute()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scripts_run_in_name_order_from_the_root_of_the_dev_clone(self):
        # 20-second is written first and 05-first sorts first; the subdirectory is not run.
        self.add_script("20-second", f"#!/bin/sh\npwd >> '{self.log}.order'\n"
                                     f"echo second >> '{self.log}.order'\n")
        self.add_script("05-first", f"#!/bin/sh\necho first >> '{self.log}.order'\n")
        self.add_script("helpers/not-a-script", f"#!/bin/sh\necho helper >> '{self.log}.order'\n")
        self.commit_dev()
        result = self.recompute()
        self.assertEqual(result.returncode, 0, result.stderr)
        order = pathlib.Path(f"{self.log}.order").read_text(encoding="utf-8").splitlines()
        self.assertEqual(order, ["first", str(self.dev), "second"])
        self.assertEqual(self.runs(), 1)

    def test_help_gives_the_usage_and_the_exit_codes(self):
        result = self.recompute("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: jig recompute <project> <product vector directory>",
                      result.stdout)
        for words in ("Exits 0 when", "1 when a file differs", "2 when the product",
                      "git restore", "git clean"):
            self.assertIn(words, result.stdout)


class RecomputeDifferenceTest(RecomputeCase):
    def test_a_vector_edited_by_hand_and_committed_exits_one_and_stays_rebuilt(self):
        # The commit holds bytes that the script does not make, so the script changes it.
        self.commit_dev({"vectors/b.txt": "edited by hand\n"})
        result = self.recompute()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "vectors: differs vectors/b.txt\n")
        # The rebuilt file stays in the tree, so the next run refuses before any script.
        self.assertEqual((self.dev / "vectors/b.txt").read_text(encoding="utf-8"), "BRAVO\n")
        self.assertEqual(self.dev_status(), " M vectors/b.txt\n")
        again = self.recompute()
        self.assertEqual(again.returncode, 2, again.stderr)
        self.assertIn("not committed: differs vectors/b.txt", again.stderr)
        self.assertEqual(self.runs(), 1)

    def test_a_vector_that_the_script_adds_or_no_longer_makes_is_listed(self):
        # The commit holds a vector no script makes, and the script makes one not committed.
        self.add_script("20-more", "#!/bin/sh\necho new > vectors/new.txt\n"
                                   "rm vectors/old.txt\n")
        self.commit_dev({"vectors/old.txt": "old\n"})
        result = self.recompute()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ["vectors: new vectors/new.txt", "vectors: missing vectors/old.txt"])

    def test_a_product_copy_that_differs_exits_one(self):
        self.push_product({f"{PRODUCT_DIR}/a.txt": "ALPHA, CHANGED\n"})
        result = self.recompute()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, f"product: differs {PRODUCT_DIR}/a.txt\n")
        self.assertEqual(self.dev_status(), "")

    def test_a_product_copy_with_a_file_that_vectors_lacks_exits_one(self):
        self.push_product({f"{PRODUCT_DIR}/extra.txt": "EXTRA\n"})
        result = self.recompute()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, f"product: new {PRODUCT_DIR}/extra.txt\n")

    def test_the_product_is_compared_at_origin_after_a_fetch(self):
        # The product clone was made before the push, so only a fetch shows the change;
        # a local commit that origin lacks does not count.
        self.push_product({f"{PRODUCT_DIR}/sub/c.txt": "CHARLIE?\n"})
        self.write(self.product, {f"{PRODUCT_DIR}/local.txt": "LOCAL\n"})
        self.git_commit_all(self.product)
        result = self.recompute()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, f"product: differs {PRODUCT_DIR}/sub/c.txt\n")


class RecomputeRefusalTest(RecomputeCase):
    def assert_failed(self, result, words, runs=0):
        """Exit 2 with these words on stderr, and the scripts ran this many times."""
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(words, result.stderr)
        self.assertEqual(self.runs(), runs)

    def test_a_script_that_fails_exits_two_and_names_it(self):
        # The build runs first and rewrites a vector; the failing script stops the run
        # before the later one, and the build's output stays in the tree.
        self.add_script("20-broken", "#!/bin/sh\necho broken >&2\nexit 3\n")
        self.add_script("30-later", f"#!/bin/sh\necho later >> '{self.log}'\n")
        self.commit_dev({"vectors/a.txt": "hand made\n"})
        result = self.recompute()
        self.assert_failed(result, "vectors/recompute/20-broken exited 3", runs=1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.dev_status(), " M vectors/a.txt\n")

    def test_an_untracked_file_under_vectors_exits_two_before_any_script(self):
        self.write(self.dev, {"vectors/stray.txt": "stray\n"})
        self.assert_failed(self.recompute(), "not committed: new vectors/stray.txt")

    def test_a_changed_uncommitted_vector_exits_two_before_any_script(self):
        self.write(self.dev, {"vectors/a.txt": "ALPHA, EDITED\n"})
        self.assert_failed(self.recompute(), "not committed: differs vectors/a.txt")

    def test_a_staged_change_under_vectors_exits_two(self):
        # The work tree matches the commit again, but the index holds another version.
        self.write(self.dev, {"vectors/a.txt": "STAGED\n"})
        self.ok("-C", self.dev, "add", "vectors/a.txt")
        self.write(self.dev, {"vectors/a.txt": "ALPHA\n"})
        self.assert_failed(self.recompute(), "not committed: differs vectors/a.txt")

    def test_a_missing_recompute_directory_exits_two(self):
        self.ok("-C", self.dev, "rm", "-q", "-r", "vectors/recompute")
        self.commit_dev()
        self.assert_failed(self.recompute(), "vectors/recompute/ is missing")

    def test_a_recompute_directory_with_no_script_exits_two(self):
        # Nothing would be rebuilt, so a pass would prove nothing.
        self.ok("-C", self.dev, "rm", "-q", "vectors/recompute/10-build")
        self.add_script("lib/shared.sh", "true\n")
        self.add_script(".keep", "")
        self.commit_dev()
        self.assert_failed(self.recompute(), "holds no script")

    def test_a_script_that_is_not_executable_exits_two_before_any_script(self):
        self.add_script("20-plain", "#!/bin/sh\ntrue\n", executable=False)
        self.commit_dev()
        self.assert_failed(self.recompute(), "20-plain is not executable")

    def test_a_product_vector_directory_missing_at_origin_exits_two(self):
        # The directory exists in a local product commit that was never pushed.
        self.write(self.product, {"tests/other/a.txt": "ALPHA\n"})
        self.git_commit_all(self.product)
        self.assert_failed(self.recompute("demo", "tests/other"),
                           "origin/master of")

    def test_a_product_that_cannot_be_fetched_exits_two(self):
        self.ok("-C", self.product, "remote", "set-url", "origin", self.tmp / "gone.git")
        self.assert_failed(self.recompute(), "cannot fetch origin")

    def test_an_absolute_or_parent_product_directory_is_a_usage_error(self):
        for directory in ("/tmp/vectors", "tests/../../vectors", "..", "."):
            result = self.recompute("demo", directory)
            self.assertEqual(result.returncode, 2, directory)
            self.assertIn("usage: jig recompute", result.stderr)
        self.assertEqual(self.runs(), 0)

    def test_a_missing_argument_is_a_usage_error(self):
        result = self.recompute("demo")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("usage: jig recompute", result.stderr)
