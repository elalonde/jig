import unittest

from tests import clone_cases
from tests.support import ROOT, TempTestCase, run

JIG = ROOT / "bin" / "jig"


class DispatcherTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "opt"
        self.root.mkdir()
        self.neutral = self.tmp / "neutral"
        self.neutral.mkdir()

    def install(self, version, *subcommands):
        libexec = self.root / version / "libexec"
        libexec.mkdir(parents=True)
        for name in subcommands or ("probe",):
            script = libexec / f"jig-{name}"
            script.write_text(f'#!/bin/bash\necho "{version} {name} $*"\n')
            script.chmod(0o755)

    def set_current(self, version):
        (self.root / "current").symlink_to(version)

    def pin(self, project, text):
        config = self.home / ".config" / "jig"
        config.mkdir(parents=True, exist_ok=True)
        (config / f"{project}.conf").write_text(text)

    def jig(self, *args, cwd=None):
        return run([JIG, *args], env=self.env(JIG_ROOT=str(self.root)), cwd=cwd or self.neutral)

    def two_versions(self):
        self.install("0.1.0")
        self.install("0.2.0")
        self.set_current("0.2.0")

    def assert_runs(self, result, version):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(f"{version} "), result.stdout)

    def test_dispatcher_picks_the_pinned_version(self):
        self.two_versions()
        self.pin("demo", "REPO=demo\nJIG_VERSION=0.1.0\nBRANCH=phase-0\n")
        self.assert_runs(self.jig("probe", "demo"), "0.1.0")

    def test_dispatcher_reads_a_quoted_pin(self):
        self.two_versions()
        self.pin("demo", 'JIG_VERSION="0.1.0"\n')
        self.assert_runs(self.jig("probe", "demo"), "0.1.0")

    def test_dispatcher_reads_a_pin_with_a_trailing_comment_or_blanks(self):
        self.two_versions()
        for line in ("JIG_VERSION=0.1.0  # pinned at the batch start\n",
                     'JIG_VERSION="0.1.0" #pinned\n', "JIG_VERSION=0.1.0 \t\n"):
            with self.subTest(line=line):
                self.pin("demo", line)
                self.assert_runs(self.jig("probe", "demo"), "0.1.0")

    def test_dispatcher_falls_back_to_current_with_no_conf(self):
        self.two_versions()
        self.assert_runs(self.jig("probe", "demo"), "0.2.0")

    def test_dispatcher_falls_back_to_current_with_no_pin_in_the_conf(self):
        self.two_versions()
        self.pin("demo", "REPO=demo\n")
        self.assert_runs(self.jig("probe", "demo"), "0.2.0")

    def test_dispatcher_takes_the_project_from_the_dev_directory(self):
        self.two_versions()
        self.pin("demo", "JIG_VERSION=0.1.0\n")
        inside = self.tmp / "ws" / "demo-dev" / "demo" / "deep"
        inside.mkdir(parents=True)
        self.assert_runs(self.jig("probe", cwd=inside), "0.1.0")

    def test_dispatcher_takes_the_project_of_each_shared_layout(self):
        # demo is pinned to the older version. From a product clone, or from deep in
        # one, the dispatcher takes the project of the -dev clone that the rule finds,
        # and runs the pin; with no -dev clone it runs current.
        self.two_versions()
        self.pin(clone_cases.PROJECT, "JIG_VERSION=0.1.0\n")
        cases = (*clone_cases.FROM_PRODUCT, *clone_cases.DEEP_CASES)
        for number, case in enumerate(cases):
            with self.subTest(case=case.name):
                base = clone_cases.build(self.tmp / "cases" / f"case{number}", case.layout)
                inside = base / case.start
                inside.mkdir(parents=True, exist_ok=True)
                expected = "0.2.0" if case.expected is None else "0.1.0"
                self.assert_runs(self.jig("probe", cwd=inside), expected)

    def test_dispatcher_argument_wins_over_the_dev_directory(self):
        self.two_versions()
        self.pin("demo", "JIG_VERSION=0.1.0\n")
        inside = self.tmp / "other-dev"
        inside.mkdir()
        self.pin("other", "JIG_VERSION=0.2.0\n")
        self.assert_runs(self.jig("probe", "demo", cwd=inside), "0.1.0")

    def test_dispatcher_with_neither_uses_current(self):
        self.two_versions()
        self.pin("demo", "JIG_VERSION=0.1.0\n")
        self.assert_runs(self.jig("probe"), "0.2.0")

    def test_dispatcher_takes_an_option_for_no_project(self):
        self.two_versions()
        self.pin("demo", "JIG_VERSION=0.1.0\n")
        inside = self.tmp / "demo-dev"
        inside.mkdir()
        self.assert_runs(self.jig("probe", "--since", "0.1.0", cwd=inside), "0.1.0")

    def test_dispatcher_finds_the_project_after_two_words(self):
        self.install("0.1.0", "terms", "privacy")
        self.install("0.2.0", "terms", "privacy")
        self.set_current("0.2.0")
        self.pin("demo", "JIG_VERSION=0.1.0\n")
        self.assert_runs(self.jig("terms", "check", "demo"), "0.1.0")
        self.assert_runs(self.jig("privacy", "scan", "demo"), "0.1.0")

    def test_dispatcher_does_not_read_a_trace_verb_as_a_project(self):
        self.install("0.1.0", "trace")
        self.install("0.2.0", "trace")
        self.set_current("0.2.0")
        self.pin("demo", "JIG_VERSION=0.1.0\n")
        self.pin("gen", "JIG_VERSION=0.2.0\n")
        inside = self.tmp / "demo-dev"
        inside.mkdir()
        self.assert_runs(self.jig("trace", "gen", cwd=inside), "0.1.0")

    def test_dispatcher_does_not_read_a_validate_kind_as_a_project(self):
        self.install("0.1.0", "validate")
        self.install("0.2.0", "validate")
        self.set_current("0.2.0")
        self.pin("demo", "JIG_VERSION=0.1.0\n")
        self.pin("prompt", "JIG_VERSION=0.2.0\n")
        inside = self.tmp / "demo-dev"
        inside.mkdir()
        self.assert_runs(self.jig("validate", "prompt", "file", cwd=inside), "0.1.0")

    def test_dispatcher_pin_to_a_missing_version_fails(self):
        self.two_versions()
        self.pin("demo", "JIG_VERSION=9.9.9\n")
        result = self.jig("probe", "demo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not installed", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_dispatcher_refuses_a_pin_that_leaves_the_root(self):
        self.two_versions()
        self.pin("demo", "JIG_VERSION=../../elsewhere\n")
        result = self.jig("probe", "demo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("bad JIG_VERSION", result.stderr)

    def test_dispatcher_does_not_run_the_conf_file(self):
        self.two_versions()
        marker = self.tmp / "marker"
        self.pin("demo", f"touch {marker}\nJIG_VERSION=0.1.0\n")
        self.assert_runs(self.jig("probe", "demo"), "0.1.0")
        self.assertFalse(marker.exists())

    def test_dispatcher_finds_subcommands_only_in_the_chosen_tree(self):
        self.install("0.1.0")
        self.install("0.2.0", "probe", "newer")
        self.set_current("0.2.0")
        self.pin("demo", "JIG_VERSION=0.1.0\n")
        result = self.jig("newer", "demo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no subcommand newer", result.stderr)
        self.assert_runs(self.jig("newer"), "0.2.0")

    def test_dispatcher_refuses_a_subcommand_that_leaves_the_tree(self):
        self.two_versions()
        result = self.jig("../probe")
        self.assertEqual(result.returncode, 1)
        self.assertIn("bad subcommand", result.stderr)

    def test_dispatcher_passes_the_arguments_on(self):
        self.two_versions()
        result = self.jig("probe", "demo", "with space", "--flag")
        self.assertEqual(result.stdout.strip(), "0.2.0 probe demo with space --flag")

    def test_dispatcher_passes_the_exit_code_on(self):
        self.install("0.1.0", "probe")
        self.set_current("0.1.0")
        script = self.root / "0.1.0" / "libexec" / "jig-probe"
        script.write_text("#!/bin/bash\nexit 7\n")
        self.assertEqual(self.jig("probe").returncode, 7)

    def test_dispatcher_with_no_arguments_lists_subcommands(self):
        self.two_versions()
        result = self.jig()
        self.assertEqual(result.returncode, 2)
        self.assertIn("probe", result.stderr)

    def test_dispatcher_source_tree_runs_its_own_subcommands(self):
        result = self.jig("which")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(ROOT))

    def test_dispatcher_source_tree_gives_way_to_an_installed_version(self):
        self.install("0.1.0", "which")
        self.set_current("0.1.0")
        self.assert_runs(self.jig("which"), "0.1.0")

    def test_dispatcher_source_tree_does_not_stand_in_for_a_pin(self):
        self.pin("demo", "JIG_VERSION=0.1.0\n")
        result = self.jig("which", "demo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not installed", result.stderr)

    def test_dispatcher_runs_changelog_from_current_under_a_pin_to_a_version_without_it(self):
        # The project in whose -dev directory the command runs is pinned to a version
        # that has no changelog command; it runs from current all the same.
        self.install("0.1.0", "probe")
        self.install("0.2.0", "probe", "changelog")
        self.set_current("0.2.0")
        self.pin("demo", "JIG_VERSION=0.1.0\n")
        dev = self.tmp / "demo-dev"
        dev.mkdir()
        self.assert_runs(self.jig("changelog", "--since", "0.1.0", cwd=dev), "0.2.0")
        self.assert_runs(self.jig("probe", cwd=dev), "0.1.0")

    def test_dispatcher_runs_upgrade_from_current_whatever_the_project_pins(self):
        # The project named on the command line is pinned to the older version, which
        # has its own upgrade command; the one of current runs.
        self.install("0.1.0", "upgrade")
        self.install("0.2.0", "upgrade")
        self.set_current("0.2.0")
        self.pin("demo", "JIG_VERSION=0.1.0\n")
        result = self.jig("upgrade", "demo", "0.2.0")
        self.assert_runs(result, "0.2.0")
        self.assertEqual(result.stdout.strip(), "0.2.0 upgrade demo 0.2.0")


if __name__ == "__main__":
    unittest.main()
