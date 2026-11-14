"""jig new: a test project in a temporary root, with doubles for ssh and the other accounts."""

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import unittest

from tests.support import ROOT, TempTestCase, run
from tests.units_support import SSH_DOUBLE

sys.path.insert(0, str(ROOT / "lib"))

import jig_ids  # noqa: E402
import lint_a  # noqa: E402

TOOL = ROOT / "libexec" / "jig-new"
ORCH = "orchtest"
OWNER = "ownerx"
HOST = "bench7"
GITHUB = "ghowner"
PROJECT = "demo"
# The statusLine command that every account gets: jig's own status line.
JIG_STATUS_LINE = "/opt/jig/current/libexec/jig-statusline"
PLACEHOLDER = re.compile(r"@[A-Z_]+@|<[a-z][a-z0-9 ]*>")
PATTERN_TEMPLATE = ROOT / "templates" / "new" / "product" / "tools" / "lint-a-patterns.txt"


def pattern_file_differences(path):
    """List how a lint A pattern file differs from what jig new must write.

    That is the process ID patterns of the shared ID module, in its order, no
    control ID class, no language and no allowance line. An empty list means
    the file is as it should be.
    """
    patterns = lint_a.read_patterns(path)
    if patterns is None:
        return [f"{path} is missing"]
    differences = []
    written = [pattern.pattern for pattern in patterns.process]
    if written != list(jig_ids.PROCESS_ID_PATTERNS):
        differences.append(f"process ID patterns {written} are not those of the module "
                           f"{list(jig_ids.PROCESS_ID_PATTERNS)}")
    if patterns.controls:
        differences.append("the control ID part is not empty")
    if patterns.languages:
        differences.append("the language part is not empty")
    if patterns.allow_process_ids:
        differences.append("the file holds the allowance line")
    return differences


class JigNewTest(TempTestCase):

    def setUp(self):
        super().setUp()
        self.srv = self.tmp / "srv"
        for part in ("blocks", "in", "out"):
            (self.srv / part).mkdir(parents=True)
        self.fake = self.tmp / "fake"
        for account in ("specwriter", "implementer"):
            (self.fake / "accounts" / account).mkdir(parents=True)
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self.ssh = self.bin_dir / "ssh-double"
        self.ssh.write_text(SSH_DOUBLE, encoding="utf-8")
        self.ssh.chmod(0o755)
        # Only the policy file is a host fact on disk: no status line file and no
        # tool of another kind exists, and jig new must not need one.
        self.policy = self.tmp / "tools" / "policy.toml"
        self.policy.parent.mkdir()
        self.policy.write_text("", encoding="utf-8")
        self.host_conf(ORCH_ACCOUNT=ORCH, OWNER=OWNER, HOST=HOST, GITHUB_OWNER=GITHUB,
                       MODEL_POLICY=self.policy)

    def host_conf(self, **values):
        text = "# host facts\n" + "".join(f"{key}={value}\n" for key, value in values.items())
        (self.srv / "host.conf").write_text(text, encoding="utf-8")

    def new(self, *args, **extra):
        env = self.env(PATH=f"{self.bin_dir}:{os.environ['PATH']}", JIG_SSH=str(self.ssh),
                       FAKE=str(self.fake), JIG_SRV=str(self.srv), USER=ORCH, LOGNAME=ORCH)
        env.update(extra)
        return run([TOOL, *args], env=env)

    def account(self, name):
        return self.fake / "accounts" / name

    def snapshot(self):
        """Every path under the temporary root, with its mode, time and content."""
        found = {}
        for top, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                path = os.path.join(top, name)
                if name == "ssh-targets":
                    continue
                info = os.lstat(path)
                content = b""
                if stat.S_ISREG(info.st_mode):
                    with open(path, "rb") as stream:
                        content = stream.read()
                found[path] = (info.st_mode, info.st_mtime_ns, content)
        return found

    def made(self):
        result = self.new(PROJECT)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_new_project_makes_every_path(self):
        self.made()
        orch, spec, impl = self.home, self.account("specwriter"), self.account("implementer")
        for home in (orch, spec, impl):
            for path in ("ws/src", "ws/prompts/demo", "ws/reviews/demo", ".config/jig/demo.conf",
                         ".ssh/demo_dev_deploy", ".ssh/demo_dev_deploy.pub"):
                self.assertTrue((home / path).exists(), f"{home}/{path}")
            conf = (home / ".config/jig/demo.conf").read_text(encoding="utf-8")
            self.assertIn("REPO=~/ws/src/demo-dev\n", conf)
            self.assertIn("BRANCH=0.1\n", conf)
            self.assertIn(f"ORCH_ACCOUNT={ORCH}\n", conf)
            values = [line for line in conf.splitlines() if not line.startswith("#")]
            self.assertNotIn("<", "\n".join(values))
            config = (home / ".ssh/config").read_text(encoding="utf-8")
            self.assertIn("Host github-demo-dev\n", config)
        for home in (orch, impl):
            self.assertTrue((home / ".ssh/demo_deploy").exists())
            self.assertIn("Host github-demo\n", (home / ".ssh/config").read_text("utf-8"))
        self.assertFalse((spec / ".ssh/demo_deploy").exists())
        for path in (spec / "ws/ref", spec / "ws/ref/demo"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertFalse((orch / "ws/ref").exists())
        for part in ("blocks", "in", "out"):
            path = self.srv / part / "demo"
            self.assertTrue(path.is_dir())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o2775)
        self.assertTrue((self.srv / "blocks/demo/new.sh").is_file())

        # The two clones are siblings: the product clone is beside the -dev clone and
        # not inside it.
        dev = orch / "ws/src/demo-dev"
        product = orch / "ws/src/demo"
        self.assertFalse((dev / "demo").exists())
        for name in ("demo-dev/CLAUDE.md", "demo-dev/impl/orch/ORCHESTRATOR.md",
                     "demo-dev/impl/orch/state.md", "demo-dev/impl/orch/handoff.md",
                     "demo-dev/checks.tsv", "demo-dev/Makefile", "demo-dev/.gitignore",
                     "demo-dev/terms.txt",
                     "demo/Makefile", "demo/tools/lint-a.sh", "demo/lib/lint_a.py",
                     "demo/lib/jig_ids.py", "demo/lib/jig_clones.py", "demo/hooks/commit-msg",
                     "demo/.gitignore", "demo/tools/lint-a-patterns.txt",
                     "demo/.github/workflows/ci.yml"):
            path = orch / "ws/src" / name
            self.assertTrue(path.is_file(), name)
            self.assertIsNone(re.search(r"@[A-Z_]+@", path.read_text(encoding="utf-8")), name)
        self.assertTrue(os.access(product / "hooks/commit-msg", os.X_OK))
        self.assertEqual(pattern_file_differences(product / "tools/lint-a-patterns.txt"), [])
        terms = (dev / "terms.txt").read_text(encoding="utf-8").split()
        for term in (OWNER, HOST, GITHUB):
            self.assertIn(term, terms)
        orchestrator = (dev / "impl/orch/ORCHESTRATOR.md").read_text(encoding="utf-8")
        self.assertIn(f"# as {OWNER} on {HOST}", orchestrator)
        self.assertIn("jig wait demo <ID>", orchestrator)
        self.assertIn("every 10 minutes", orchestrator)
        self.assertIn("prefs.md", orchestrator)
        self.assertNotIn("demo", (dev / ".gitignore").read_text(encoding="utf-8"))
        for repo, origin in ((dev, f"github-demo-dev:{GITHUB}/demo-dev.git"),
                             (product, f"github-demo:{GITHUB}/demo.git")):
            log = run(["git", "-C", repo, "log", "--format=%B", "0.1"], env=self.env())
            self.assertEqual(log.returncode, 0, log.stderr)
            self.assertIn("Co-Authored-By: Claude", log.stdout)
            url = run(["git", "-C", repo, "remote", "get-url", "origin"], env=self.env())
            self.assertEqual(url.stdout.strip(), origin)
            status = run(["git", "-C", repo, "status", "--porcelain"], env=self.env())
            self.assertEqual(status.stdout, "")
            # jig start wants the branch of BRANCH checked out.
            branch = run(["git", "-C", repo, "branch", "--show-current"], env=self.env())
            self.assertEqual(branch.stdout, "0.1\n")

    def test_standing_prompt_names_the_clean_room_rules(self):
        # The orchestrator's standing prompt must carry the clean-room rules itself: a
        # session started outside the -dev repo does not load CLAUDE.md, and would then
        # have no rule that keeps it out of the reference.
        self.made()
        path = self.home / "ws/src/demo-dev/impl/orch/ORCHESTRATOR.md"
        orchestrator = " ".join(path.read_text(encoding="utf-8").split())
        self.assertIn("never read `~/ws/ref`", orchestrator)
        self.assertIn("ssh keys into the worker accounts only through jig", orchestrator)
        self.assertIn("no path under `~/ws/ref`", orchestrator)
        self.assertIn("never copy reference-derived text into a prompt", orchestrator)

    def made_text(self, name):
        """Make a project and return one of its files with every run of blanks as one space."""
        self.made()
        return " ".join((self.home / "ws/src/demo-dev" / name).read_text(encoding="utf-8").split())

    def test_made_claude_md_states_the_path_rule_of_the_sibling_clones(self):
        # The common part says where the two clones are, that a path starts at the repo
        # name, and that a command in the -dev clone names the product as ../demo/.
        text = self.made_text("CLAUDE.md")
        self.assertIn("The product clone is its sibling, `~/ws/src/demo`; no clone is "
                      "inside another.", text)
        self.assertIn("starts at the repo name", text)
        self.assertIn("`demo-dev/specs/...` is a file of the -dev clone, and `demo/...` is "
                      "a file of the product clone.", text)
        self.assertIn("names the product clone as `../demo/`, for example "
                      "`make -C ../demo check`.", text)
        common = text[:text.index("## Project part")]
        self.assertNotIn("demo-dev/demo", common)

    def test_made_check_rows_reach_the_product_clone_beside_the_dev_clone(self):
        # The make-check row runs in the root of the -dev clone, as jig run-check runs
        # it, and reaches the product's make check through ../demo.
        self.made()
        dev = self.home / "ws/src/demo-dev"
        rows = (dev / "checks.tsv").read_text(encoding="utf-8").splitlines()
        command = next(row.split("\t")[1] for row in rows if row.startswith("make-check\t"))
        self.assertEqual(command, "make -C ../demo check")
        result = run(["sh", "-c", command], env=self.env(), cwd=dev)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("lint-a: clean", result.stdout)

    def test_made_claude_md_ties_the_clean_room_rules_to_the_profile(self):
        # The tools read the clean room from the project profile, and the default is off,
        # so a new project's rules must say which ones hold only with the clean room on.
        text = self.made_text("CLAUDE.md")
        for line in ("which, with the clean room on, is never a screen of a session in the "
                     "specwriter account",
                     "public docs only when the clean room is off",
                     'The kickoff record has a section "Project profile"',
                     "Rules 2 and 3, and the first part of rule 1 (the orchestrator never reads "
                     "`~/ws/ref`), apply when the project profile turns the clean room on.",
                     "Rules 4 and 5, and the second part of rule 1 (ssh keys only through jig), "
                     "apply in every project.",
                     "a project never moves the spec writer into the implementer account"):
            self.assertIn(line, text)
        self.assertNotIn("when the project has no reference", text)

    def test_made_claude_md_has_the_vector_rules_the_checks_and_every_tool(self):
        # An implementer must learn the vector rule before jig report refuses its report,
        # and every session must know how a check is run and which tools exist.
        text = self.made_text("CLAUDE.md")
        for line in ("| the reference, an edit in the product's vector directory |",
                     "in the product, the product's vector directory and no other file, with "
                     "the ledger entry of each such product commit",
                     "`-dev`, the product's vector directory, public sources",
                     "the command `jig recompute demo <product vector directory>`",
                     "The phase gate runs every row except `recompute`.",
                     "A unit runs every check its prompt names through `jig run-check demo "
                     "<name>`",
                     "The unit writes the placeholders `checks: none` and `context: none`"):
            self.assertIn(line, text)
        self.assertNotIn("reports their exit codes", text)
        self.assertIn("- `make check` in `-dev`: the sidecar tests, which run "
                      "`jig trace check`.", text)
        tools = text.split("## Tool list", 1)[1].split("## Project part", 1)[0]
        for tool in ("start", "batch", "status", "wait", "cat", "send", "report", "close",
                     "hold", "unhold", "orch", "ctx", "run-check", "recompute", "which", "run"):
            self.assertIn(f"`jig {tool}", tools)

    def test_standing_prompt_has_the_profile_and_the_batch_rules(self):
        # A new orchestrator learns from its standing prompt alone how a batch runs, what
        # each stop means, and that a worker must be closed before its account is free.
        text = self.made_text("impl/orch/ORCHESTRATOR.md")
        self.assertIn("## Running a batch", text)
        for line in ("one line `<switch>: <value>` for each of the six switches: `reference`, "
                     "`clean room`, `oracle`, `differences review`, `pre-check` and "
                     "`review after a risky unit`.",
                     "With the clean room on, a session in the specwriter account: `jig wait` "
                     "prints no screen",
                     "or, with the clean room on, a session in the specwriter account with no "
                     "screen change at a second wait;",
                     "`jig batch demo <batch file>` runs a batch.",
                     "with 5 columns, the kind: `unit`, `pre-check` or `review`.",
                     "Outside a batch, close a finished worker 10 minutes after its report "
                     "with `jig close demo <ID>`",
                     "`jig close demo <ID> --dead`",
                     "`jig hold demo` writes `/run/jig/hold/demo`",
                     "`jig unhold demo` removes it",
                     "a must-fix or should-fix finding of a `pre-check` row.",
                     "a must-fix finding of a `review` row",
                     "The batch does not close that worker, so its lock stays, and `jig start` "
                     "refuses a rerun until you run `jig close`.",
                     "exits only when every worker that reported is closed, which can be up to "
                     "10 minutes later.",
                     "`NEEDS SPEC` does not stop the batch"):
            self.assertIn(line, text)
        self.assertNotIn("phase 1", text)

    def test_standing_prompt_starts_the_event_watcher_first(self):
        # The supervisor types nothing into the session, so an orchestrator that never
        # starts its watcher would miss every event. The first rule of the session rules
        # names the watcher command, the Monitor tool and the once-read of the old
        # session's events; a fresh session reads those events too, and no line says
        # that the supervisor sends a line into the session. The harness tool ends a
        # watcher at a deadline, so the same rule says to start it with the longest
        # deadline and again after each end, and how to treat the lines that a
        # restarted watcher prints again.
        text = self.made_text("impl/orch/ORCHESTRATOR.md")
        rules = text.split("## Session rules", 1)[1]
        first = rules.split(" - ", 2)[1]
        for part in ("The first thing after each start of this session: start one "
                     "background watcher, `jig orch demo events`,",
                     "(in Claude Code, the Monitor tool)",
                     "Never run two watchers at once.",
                     "When the harness tool ends a watcher at a deadline, start it with the "
                     "longest deadline that the tool allows, and start it again at each "
                     "notice that it ended;",
                     "A restarted watcher prints first the lines that the earlier watcher of "
                     "this session printed, in the same order.",
                     "Act only on the lines after them, and on a line of that replay only "
                     "when you have no record, in this session or in `state.md` and "
                     "`handoff.md`, of having handled it.",
                     "`~/ws/reviews/demo/orch/events.log`",
                     "the lines between the last two start lines: `jig orch demo events --old`.",
                     "Act on each of these events that `state.md` and `handoff.md` do not "
                     "show as handled"):
            self.assertIn(part, first)
        self.assertIn("the events of the old session (`jig orch demo events --old`)", rules)
        self.assertNotIn("into this session", text.replace("types nothing into this session",
                                                           ""))
        self.assertNotIn("supervisor sends", text)
        self.assertNotIn("announces", text)
        self.assertNotIn("Start no second one", text)
        self.assertNotIn("minutes", first)

    def test_standing_prompt_celebrates_every_release_by_its_size(self):
        # An orchestrator learns from the standing prompt alone that a release, a patch
        # too, gets its own celebration. The made prompt must say this in the part on
        # updates and celebrations, with the line limit and the content for a patch, a
        # minor and a major release.
        text = self.made_text("impl/orch/ORCHESTRATOR.md")
        part = text.split("Updates and celebrations:", 1)[1].split("## Blocks", 1)[0]
        for line in ("every release of every project, a patch too, gets a text celebration "
                     "in the console and in `updates.log`.",
                     "A bigger version change gets a bigger celebration.",
                     "A patch release gets at most 5 lines: what it fixes.",
                     "A minor release gets at most 12 lines: what it adds, with the numbers.",
                     "A major release gets the longest, at most 30 lines, with the story of "
                     "the version."):
            self.assertIn(line, part)

    def test_standing_prompt_gives_the_tag_block_and_the_row_of_a_closed_loop(self):
        # An orchestrator of a new project must learn the tag block line and the exact
        # form of the table that jig tag reads, or the first revision tag is refused.
        text = self.made_text("impl/orch/ORCHESTRATOR.md")
        for line in ("`BRANCH=<line> jig tag demo <tag> <commit>`, with `<line>` the `BRANCH` "
                     "of your conf file, `~/.config/jig/demo.conf`.",
                     "a row in the table under a Markdown heading with exactly the text "
                     "`Closed review loops` in `specs/00-status.md`.",
                     "The row is `| <document path> | <commit> | <ID> |`, with the document as "
                     "its path from the repo root",
                     "the commit that the last review round of the loop read",
                     "so the tag goes at or after the commit that adds the row."):
            self.assertIn(line, text)

    def test_made_claude_md_names_the_trace_commands_and_jig_tag(self):
        # A unit of a new project learns from the tool list which trace commands are
        # its own, which account runs the coverage row, and that Eric runs jig tag.
        text = self.made_text("CLAUDE.md")
        tools = text.split("## Tool list", 1)[1].split("## Project part", 1)[0]
        for line in ("and `jig trace gen` and `jig trace form` for the trace entries and the "
                     "ledger entry of its commits.",
                     "`jig trace coverage`, the command of the row `coverage` of a project with "
                     "`specs/ids.tsv`, run by `jig run-check` in the specwriter account.",
                     "The spec unit that writes the first `specs/ids.tsv` adds the row.",
                     "`jig tag`, run by Eric in the tag block."):
            self.assertIn(line, tools)

    def headings(self, name):
        path = self.home / "ws/src/demo-dev/impl/orch" / name
        return [line for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith("## ")]

    def test_state_file_has_the_five_parts_in_order(self):
        # A fresh orchestrator session learns the project's state from this file alone,
        # so a new project starts with every part it must keep.
        self.made()
        self.assertEqual(self.headings("state.md"),
                         ["## Current", "## Decisions by date", "## Open", "## Next units",
                          "## Pinned jig version"])
        text = (self.home / "ws/src/demo-dev/impl/orch/state.md").read_text(encoding="utf-8")
        for item in ("- Heads:", "- Running unit:", "- Last results:",
                     "- Fable sessions used this week:", "act by"):
            self.assertIn(item, text)

    def test_state_file_has_an_open_line_for_the_token_and_each_login(self):
        # A new project does not know when its credentials expire, so the Open part
        # holds one undated line for the push token and one for the claude.ai login of
        # each of the three accounts, and says how a known expiry is written: the
        # expiry, then the date to act, 7 days before it.
        self.made()
        text = (self.home / "ws/src/demo-dev/impl/orch/state.md").read_text(encoding="utf-8")
        open_part = text.split("## Open\n", 1)[1].split("\n## ", 1)[0]
        items = [line for line in open_part.splitlines() if line.startswith("- ")]
        self.assertEqual(items[1:], [
            "- The product push token (fine-grained, held by implementer): not made yet,",
            f"- The claude.ai login of {ORCH}: expires not known, no date.",
            "- The claude.ai login of specwriter: expires not known, no date.",
            "- The claude.ai login of implementer: expires not known, no date."])
        self.assertIn("  expires not known, no date.\n- The claude.ai login", open_part)
        self.assertIn("`expires 2026-01-12, act by 2026-01-05`",
                      " ".join(open_part.split()))

    def test_standing_prompt_runs_another_accounts_check_last_before_prepare(self):
        # A release reads the exit file copy of a check that another account runs, so
        # the standing prompt puts the unit of that check last before the unit that runs
        # prepare, and runs it again when prepare fails on that check.
        text = self.made_text("impl/orch/ORCHESTRATOR.md")
        self.assertIn("A row of `checks.tsv` whose account is not implementer: `prepare` "
                      "does not run it, but reads the copy of its exit file that "
                      f"`jig run-check` writes in `{self.srv}/checks/demo/<name>.exit`", text)
        self.assertIn("Run that row in a unit of its account, whose prompt names the check, "
                      "as the last unit before the unit that runs `prepare`. When `prepare` "
                      "fails on that check, run that unit again, then the unit that runs "
                      "`prepare`.", text)

    def test_product_gets_the_ci_workflow_that_runs_make_check_on_every_push(self):
        # The product's first commit holds a GitHub Actions workflow whose trigger is
        # every push and whose last step runs make check at the root of the checkout.
        self.made()
        product = self.home / "ws/src/demo"
        workflow = product / ".github/workflows/ci.yml"
        lines = workflow.read_text(encoding="utf-8").splitlines()
        self.assertIn("on: push", lines)
        steps = [line.strip() for line in lines if line.strip().startswith(("- name:", "run:",
                                                                            "- uses:"))]
        self.assertEqual(steps[-2:], ["- name: make check", "run: make check"])
        tracked = run(["git", "-C", product, "ls-files", ".github"], env=self.env())
        self.assertEqual(tracked.stdout, ".github/workflows/ci.yml\n")

    def test_checks_directories_take_the_group_of_the_root_with_setgid(self):
        # The srv root has no checks/ yet. jig new makes checks/ and checks/demo/, both
        # mode 2775 and in the group of the root, so that another account's check can
        # leave its exit file copy there for the implementer. A second run keeps them.
        self.made()
        for path in (self.srv / "checks", self.srv / "checks/demo"):
            self.assertTrue(path.is_dir(), path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o2775, path)
            self.assertEqual(path.stat().st_gid, self.srv.stat().st_gid, path)
        again = self.new(PROJECT)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn(f"kept {self.srv / 'checks/demo'}", again.stdout)

    def test_checks_directory_of_the_root_that_exists_is_kept(self):
        # A host where another project made checks/ already: jig new keeps its mode and
        # makes only the project's directory in it.
        (self.srv / "checks").mkdir(mode=0o755)
        (self.srv / "checks").chmod(0o755)
        result = self.made()
        self.assertIn(f"kept {self.srv / 'checks'}", result.stdout)
        self.assertEqual(stat.S_IMODE((self.srv / "checks").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.srv / "checks/demo").stat().st_mode), 0o2775)

    def test_checks_path_that_is_a_file_refuses(self):
        # checks/demo is a plain file: jig new names it and stops, and leaves the file.
        (self.srv / "checks").mkdir()
        (self.srv / "checks/demo").write_text("x\n", encoding="utf-8")
        result = self.new(PROJECT)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"cannot make {self.srv / 'checks/demo'}", result.stderr)
        self.assertEqual((self.srv / "checks/demo").read_text(encoding="utf-8"), "x\n")

    def test_handoff_file_has_what_was_in_hand_and_what_comes_first(self):
        self.made()
        self.assertEqual(self.headings("handoff.md"),
                         ["## Session", "## In hand", "## First for the next session"])

    def test_standing_prompt_holds_the_preferences_text_and_the_rollover_rules(self):
        # The preferences file's text, without its title, stands in the standing prompt
        # in place of its name, so the orchestrator has it without reading another file.
        self.made()
        path = self.home / "ws/src/demo-dev/impl/orch/ORCHESTRATOR.md"
        orchestrator = path.read_text(encoding="utf-8")
        body = (ROOT / "prefs.md").read_text(encoding="utf-8").split("\n", 1)[1].strip()
        self.assertIn("ASD-STE100", body)
        self.assertIn("\n## Writing to Eric\n\n" + body + "\n\n", orchestrator)
        self.assertNotIn("# Eric's standing preferences", orchestrator)
        self.assertEqual(orchestrator.count("ASD-STE100"), 1)
        self.assertNotIn("will be in `prefs.md`", orchestrator)
        flat = " ".join(orchestrator.split())
        for rule in ("After each step, run `jig ctx`.", "3, at about 250k tokens: roll over.",
                     "4, at about 280k tokens: exit after the current tool call.",
                     "Roll over also at every batch boundary",
                     "a rollover does not affect it",
                     "Its first hourly update cites the commit of `handoff.md`."):
            self.assertIn(rule, flat)
        self.assertNotIn("120k", orchestrator)
        self.assertNotIn("150k", orchestrator)

    def test_made_claude_md_has_the_comment_standard_with_rules_a_to_g(self):
        # A new project's CLAUDE.md must hold the comment rules in full, because the
        # implementer's prompt names that section and nothing else carries the rules.
        self.made()
        text = (self.home / "ws/src/demo-dev/CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("\n## Comment standard\n", text)
        section = text.split("\n## Comment standard\n", 1)[1].split("\n## ", 1)[0]
        flat = " ".join(section.split())
        self.assertIn("Every comment in the product is written for this reader: a competent "
                      "maintainer who has never seen the specs, the process or the `-dev` repo.",
                      flat)
        for rule in (
                "- a. Full sentences. The purpose first, then the detail.",
                "- b. Explain a project term at its first use in a file, or name the item "
                "that defines it.",
                "- c. Give the reason for anything a reader would not expect: which failure "
                "it prevents.",
                "- d. A test comment says the situation it sets up and the result it expects.",
                "- e. No comment that only repeats the item's name. No comment is better "
                "than a weak one.",
                "- f. Use code identifiers or plain words, never the spec's names for "
                "operations or states.",
                "- g. No rule IDs, spec sections or process words. The link from a rule to "
                "the code lives in the `-dev` repo."):
            self.assertIn(rule, flat)
        self.assertIn("Every prompt whose first line names the implementer account names this "
                      "section on its ROLE line", flat)
        rules = " ".join(text.split())
        self.assertIn("Test names are plain words and carry no control ID.", rules)
        self.assertIn("The commit-msg hook checks messages with the patterns of the first "
                      "bullet only, not with these", rules)

    def test_make_check_exits_0_in_both_repos_while_empty(self):
        # A new project has no trace and no ID classes yet, and both checks pass. The
        # jig on PATH is this tree's dispatcher, and the empty install root leaves it no
        # installed version to choose, so the sidecar check runs this tree's trace tool.
        self.made()
        dev = self.home / "ws/src/demo-dev"
        no_install = self.tmp / "no-install"
        no_install.mkdir()
        env = self.env(PATH=f"{ROOT / 'bin'}:{os.environ['PATH']}", JIG_ROOT=str(no_install))
        product = self.home / "ws/src/demo"
        for repo in (dev, product):
            result = run(["make", "-C", repo, "check"], env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if repo == dev:
                self.assertIn("jig trace check\n", result.stdout)
            else:
                self.assertIn("lint-a: clean", result.stdout)
        hooks = run(["git", "-C", product, "config", "core.hooksPath"], env=self.env())
        self.assertEqual(hooks.stdout.strip(), "hooks")
        # The hooks are live. The term check of the pre-commit hook finds nothing in the
        # new product, and the commit-msg hook then refuses a message with a private term.
        bad = run(["git", "-C", product, "commit", "--allow-empty", "-q", "-m",
                   f"Talk about {HOST}"], env=env)
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotIn("jig pre-commit hook", bad.stderr)
        self.assertIn(HOST, bad.stderr)

    def test_new_installs_the_pre_commit_hook_after_the_first_commit(self):
        # The seed product clone gets jig's pre-commit hook at the hooks path in its work
        # tree. The first commit does not hold it, the exclude file keeps it out of the
        # product, and the clone stays clean.
        self.made()
        product = self.home / "ws/src/demo"
        hook = product / "hooks/pre-commit"
        self.assertEqual(hook.read_bytes(), (ROOT / "hooks" / "pre-commit").read_bytes())
        self.assertTrue(os.access(hook, os.X_OK))
        named = run(["git", "-C", product, "rev-parse", "--git-path", "hooks/pre-commit"],
                    env=self.env())
        self.assertEqual(named.stdout.strip(), "hooks/pre-commit")
        first = run(["git", "-C", product, "show", "--name-only", "--format=", "HEAD"],
                    env=self.env()).stdout.split()
        self.assertIn("hooks/commit-msg", first)
        self.assertNotIn("hooks/pre-commit", first)
        exclude = (product / ".git/info/exclude").read_text(encoding="utf-8").splitlines()
        self.assertIn("/hooks/pre-commit", exclude)
        status = run(["git", "-C", product, "status", "--porcelain"], env=self.env())
        self.assertEqual(status.stdout, "")

    def test_checks_template_gives_the_new_project_the_row_terms(self):
        # The row runs the term check of the new project in the implementer account.
        self.made()
        text = (self.home / "ws/src/demo-dev/checks.tsv").read_text(encoding="utf-8")
        rows = [line.split("\t") for line in text.splitlines()]
        self.assertIn(["terms", "jig terms check demo", "implementer", "60"], rows)

    def test_pattern_template_holds_the_process_ids_of_the_module(self):
        # The template that jig new copies holds exactly the process ID patterns of the
        # shared module, with the other parts empty.
        self.assertEqual(pattern_file_differences(PATTERN_TEMPLATE), [])

    def test_pattern_template_check_fails_on_a_changed_pattern(self):
        # A copy of the template with one process ID pattern changed, and one with a
        # pattern left out, each differs from the module, and the comparison says so.
        text = PATTERN_TEMPLATE.read_text(encoding="utf-8")
        first = jig_ids.PROCESS_ID_PATTERNS[0]
        for changed in (text.replace(f"\n{first}\n", "\nD-[0-9]\n", 1),
                        text.replace(f"\n{first}\n", "\n", 1)):
            self.assertNotEqual(changed, text)
            copy = self.tmp / "lint-a-patterns.txt"
            copy.write_text(changed, encoding="utf-8")
            self.assertNotEqual(pattern_file_differences(copy), [])

    def test_second_run_changes_nothing(self):
        first = self.made()
        before = self.snapshot()
        second = self.new(PROJECT)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("made ", second.stdout)
        self.assertEqual(first.stdout.splitlines()[-2:], second.stdout.splitlines()[-2:])

    def test_bad_name_refuses(self):
        before = self.snapshot()
        for name in ("Demo", "a b", "-x", "x-", "x--y", "1x", "x-dev", "../x", "x/y", "",
                     "a" * 25, "x;rm"):
            result = self.new(name)
            self.assertEqual(result.returncode, 1, name)
            self.assertIn("not a project name", result.stderr, name)
        self.assertEqual(self.snapshot(), before)

    def test_existing_project_is_not_overwritten(self):
        kept = self.srv / "blocks" / PROJECT / "x.sh"
        kept.parent.mkdir()
        kept.write_text("keep\n", encoding="utf-8")
        before = self.snapshot()
        result = self.new(PROJECT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("jig new did not make it", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_project_name_that_is_a_file_is_not_overwritten(self):
        (self.srv / "in" / PROJECT).write_text("a file\n", encoding="utf-8")
        before = self.snapshot()
        result = self.new(PROJECT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not a directory", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_block_has_the_first_and_last_line_form_and_no_placeholder(self):
        result = self.made()
        block = self.srv / "blocks/demo/new.sh"
        text = block.read_text(encoding="utf-8")
        lines = text.splitlines()
        self.assertEqual(lines[0], f"# new  as {OWNER} on {HOST} — role: owner")
        self.assertEqual(lines[-1], "# end new")
        self.assertEqual([line for line in lines if len(line) >= 75], [])
        self.assertIsNone(PLACEHOLDER.search(text), PLACEHOLDER.search(text))
        self.assertIn("--- readback new", text)
        self.assertEqual(text.count("git clone -q -b 0.1"), 1)
        self.assertIn("-u origin/0.1 0.1", text)
        self.assertIn("# Readback. Expected:", text)
        self.assertIn("Not checked:", text)
        for line in lines:
            command = line.strip()
            if re.match(r"(gh|ssh|git) ", command) and not command.endswith("\\"):
                self.assertRegex(command, r"</dev/(null|tty)|<<|< ", command)
        for home in (self.home, self.account("specwriter"), self.account("implementer")):
            self.assertIn(f"={home}\n", text)
        self.assertEqual(run(["bash", "-n", block]).returncode, 0)
        if shutil.which("shellcheck"):
            checked = run(["shellcheck", "-s", "bash", block])
            self.assertEqual(checked.returncode, 0, checked.stdout)
        digest = hashlib.sha256(block.read_bytes()).hexdigest()[:8]
        self.assertEqual(result.stdout.splitlines()[-2:],
                         [f"# as {OWNER} on {HOST}", f"jig run demo new {digest}"])

    def block_step(self, number):
        """The text of one step of the block, from its first line to the next step's."""
        text = (self.srv / "blocks/demo/new.sh").read_text(encoding="utf-8")
        return text[text.index(f'echo "step {number}:'):text.index(f'echo "step {number + 1}:')]

    def test_block_first_push_reaches_a_bare_remote_with_every_branch(self):
        self.made()
        dev = self.home / "ws/src/demo-dev"
        remote = self.tmp / "remote"
        for repo in ("demo-dev", "demo"):
            made = run(["git", "init", "-q", "--bare", remote / f"{repo}.git"], env=self.env())
            self.assertEqual(made.returncode, 0, made.stderr)
        # The ssh double reaches the orchestrator's account, which is this test's home.
        (self.fake / "accounts" / ORCH).symlink_to(self.home)
        prelude = (f"set -eu -o pipefail\nP={PROJECT}\nORCH={ORCH}\nURL={remote}\n"
                   f"G=(git)\nT={self.tmp}/t\nmkdir -p \"$T\"\n")
        env = self.env(FAKE=str(self.fake), GIT_SSH_COMMAND=str(self.ssh),
                       GIT_SSH_VARIANT="simple")
        for word in ("made", "kept"):
            result = run(["bash", "-c", prelude + self.block_step(4)], env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(f"push demo-dev {word}\npush demo {word}\n", result.stdout)
        for repo, source in (("demo-dev", dev), ("demo", self.home / "ws/src/demo")):
            bare = remote / f"{repo}.git"
            heads = run(["git", "-C", bare, "for-each-ref", "--format=%(refname:short)",
                         "refs/heads"], env=self.env())
            self.assertEqual(sorted(heads.stdout.split()), ["0.1", "master"], repo)
            for branch in ("0.1", "master"):
                pushed = run(["git", "-C", bare, "rev-parse", branch], env=self.env())
                seeded = run(["git", "-C", source, "rev-parse", branch], env=self.env())
                self.assertEqual(pushed.stdout, seeded.stdout, f"{repo} {branch}")

    def run_settings_step(self):
        """Run the block's header, step 7 and the status line readback on fixture accounts.

        ssh is replaced by a shell function that runs the command in the account's
        fixture home, and tmux by a double that does nothing, so no real account,
        session or tmux server is touched.
        """
        text = (self.srv / "blocks/demo/new.sh").read_text(encoding="utf-8")
        header = text[text.index("set -eu"):text.index('echo "step 1:')]
        step = text[text.index('echo "step 7:'):text.index("# Readback.")]
        start = text.index('echo "--- readback new"')
        readback = text[start:text.index('  echo "$a head', start)]
        readback = readback[:readback.rindex("for a in")]
        fake_ssh = ('ssh() {\n  [ "$1" = -o ] && shift 2\n  local home="$FAKE/accounts/${1%@*}"\n'
                    '  shift\n  (cd "$home" && HOME="$home" bash -c "$*")\n}\n')
        tmux = self.bin_dir / "tmux"
        tmux.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tmux.chmod(0o755)
        env = self.env(FAKE=str(self.fake), PATH=f"{self.bin_dir}:{os.environ['PATH']}",
                       TMUX_TMPDIR=str(self.tmp), TMPDIR=str(self.tmp))
        return run(["bash", "-c", header + fake_ssh + step + readback], env=env)

    def settings(self, account):
        path = self.account(account) / ".claude/settings.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_block_sets_the_hooks_path_in_each_product_clone_of_the_clone_step(self):
        # The clone step makes the implementer's product clone; the orchestrator holds
        # the seed clone. Both must read the product's tracked hooks directory, so the
        # step sets core.hooksPath in each of them when that directory exists, and
        # does so before it reports the step as done.
        self.made()
        text = (self.srv / "blocks/demo/new.sh").read_text(encoding="utf-8")
        step = text.split('echo "step 5: clones"', 1)[1].split('echo "step 5 ok"', 1)[0]
        self.assertIn('hp="[ ! -d ~/$p/hooks ] || '
                      'git -C ~/$p config core.hooksPath hooks"', step)
        self.assertIn('on "$ORCH" "$hp"\n', step)
        self.assertIn('on "$IMPL" "$hp"\n', step)
        self.assertLess(step.index("$c github-$P:$GH/$P ~/$p"),
                        step.index('on "$IMPL" "$hp"'))

    def test_block_settings_step_writes_jig_status_line_and_no_other_hook(self):
        # Two accounts have no settings yet; the implementer has a status line of
        # another command and a hook of its own. After the step every account's
        # statusLine runs jig's status line, no hooks entry is added, the existing
        # one is left as it was, and the readback prints each account's command.
        self.made()
        (self.fake / "accounts" / ORCH).mkdir()
        (self.home / ".tmux.conf").write_text("set -g mouse on\n", encoding="utf-8")
        hooks = {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}
        existing = self.account("implementer") / ".claude/settings.json"
        existing.parent.mkdir()
        existing.write_text(json.dumps({"statusLine": {"type": "command",
                                                       "command": "/usr/bin/other-line"},
                                        "hooks": hooks}), encoding="utf-8")
        result = self.run_settings_step()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("settings made"), 3, result.stdout)
        for account in (ORCH, "specwriter", "implementer"):
            settings = self.settings(account)
            self.assertEqual(settings["statusLine"],
                             {"type": "command", "command": JIG_STATUS_LINE,
                              "refreshInterval": 60})
            self.assertIn(f"{account} statusLine {JIG_STATUS_LINE}\n", result.stdout)
        for account in (ORCH, "specwriter"):
            self.assertNotIn("hooks", self.settings(account))
        self.assertEqual(self.settings("implementer")["hooks"], hooks)
        self.assertTrue((existing.parent / "settings.json.bak-new").is_file())
        again = self.run_settings_step()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(again.stdout.count("settings kept"), 3, again.stdout)

    def test_block_sets_jig_status_line_and_names_no_other_tool(self):
        # The block names jig's status line as the command, reads it back per
        # account, and says that jig new did not check it; nothing in it names the
        # session-log hooks or the host tool of the old status line.
        self.made()
        text = (self.srv / "blocks/demo/new.sh").read_text(encoding="utf-8")
        self.assertIn(f"LINE={JIG_STATUS_LINE}\n", text)
        self.assertIn('echo "$a statusLine $c"', text)
        readback_note = text[text.index("# Readback. Expected:"):text.index("--- readback")]
        self.assertIn("statusLine", readback_note)
        header = text[text.index("Checked by jig new:"):text.index("set -eu")]
        checked, not_checked = header.split("Not checked:")
        self.assertNotIn("status line", checked)
        self.assertIn("status line", not_checked)
        for word in ("session-log", "merge-hooks", "hooks.json", "SESSION_LOG",
                     "STATUSLINE", "statusline.py"):
            self.assertNotIn(word, text)

    def test_refuses_when_not_run_as_the_orchestrator(self):
        result = self.new(PROJECT, USER="someone", LOGNAME="someone")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"run as {ORCH}", result.stderr)
        self.assertFalse((self.srv / "blocks" / PROJECT).exists())

    def test_refuses_without_the_host_facts(self):
        self.host_conf(ORCH_ACCOUNT=ORCH, OWNER=OWNER, HOST=HOST)
        result = self.new(PROJECT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("sets no GITHUB_OWNER", result.stderr)
        self.assertIn("GITHUB_OWNER  the GitHub account", result.stderr)
        self.assertFalse((self.srv / "blocks" / PROJECT).exists())

    def test_refuses_without_host_conf_and_names_every_key(self):
        (self.srv / "host.conf").unlink()
        result = self.new(PROJECT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read", result.stderr)
        for key in ("OWNER", "HOST", "GITHUB_OWNER", "MODEL_POLICY  the model policy",
                    "ORCH_ACCOUNT=orchestrator", "SPEC_ACCOUNT=", "IMPL_ACCOUNT="):
            self.assertIn(f"  {key}", result.stderr)
        # The keys of the old status line and the session-log hooks are gone, and no
        # key has a host path as its default.
        for key in ("SESSION_LOG", "STATUSLINE"):
            self.assertNotIn(key, result.stderr)
        optional = result.stderr.split("Optional", 1)[1].splitlines()[1:]
        self.assertEqual(len(optional), 3, result.stderr)
        for line in optional:
            self.assertNotIn("/", line)
        readme = (ROOT / "templates" / "new" / "README.md").read_text(encoding="utf-8")
        for key in ("OWNER", "HOST", "GITHUB_OWNER", "ORCH_ACCOUNT", "SPEC_ACCOUNT",
                    "IMPL_ACCOUNT", "MODEL_POLICY"):
            self.assertIn(f"| `{key}` |", readme)
        for key in ("SESSION_LOG", "STATUSLINE"):
            self.assertNotIn(f"`{key}`", readme)
        self.assertIn("| `MODEL_POLICY` | yes |", readme)
        self.assertFalse((self.srv / "blocks" / PROJECT).exists())

    def test_refuses_without_the_model_policy_and_names_the_key(self):
        # The policy file has no default: a host.conf without it is refused before
        # anything is made.
        self.host_conf(ORCH_ACCOUNT=ORCH, OWNER=OWNER, HOST=HOST, GITHUB_OWNER=GITHUB)
        result = self.new(PROJECT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("sets no MODEL_POLICY", result.stderr)
        self.assertFalse((self.srv / "blocks" / PROJECT).exists())

    def test_refuses_when_the_model_policy_is_not_a_file(self):
        # The key is there but names nothing; the refusal names the key.
        self.policy.unlink()
        result = self.new(PROJECT)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"MODEL_POLICY: {self.policy} is not a file", result.stderr)
        self.assertFalse((self.srv / "blocks" / PROJECT).exists())

    def test_unreachable_account_refuses_and_a_rerun_completes(self):
        (self.fake / "ssh-fails").write_text("", encoding="utf-8")
        result = self.new(PROJECT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("account specwriter", result.stderr)
        self.assertFalse((self.srv / "blocks/demo/new.sh").exists())
        (self.fake / "ssh-fails").unlink()
        self.made()
        self.assertTrue((self.srv / "blocks/demo/new.sh").exists())

    def test_usage_error(self):
        self.assertEqual(self.new().returncode, 2)
        self.assertEqual(self.new("a", "b").returncode, 2)


if __name__ == "__main__":
    unittest.main()
