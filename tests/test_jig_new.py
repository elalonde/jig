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


# The first line of the block's trust step, which runs to the end of its step 5.
TRUST_STEP_START = "# Claude Code asks whether a folder can be trusted"


class TrustStepMixin:
    """Run the trust step of a written block on the fixture accounts, and read marks."""

    def run_trust_step(self):
        """Run the block's trust step; ssh runs the command in the account's fixture home.

        An account whose fixture home is missing cannot be reached, as ssh to an
        account that does not answer.
        """
        text = (self.srv / "blocks/demo/new.sh").read_text(encoding="utf-8")
        step = text[text.index(TRUST_STEP_START):text.index('echo "step 5 ok"')]
        fake_ssh = ('ssh() {\n  [ "$1" = -o ] && shift 2\n  local home="$FAKE/accounts/${1%@*}"\n'
                    '  shift\n  [ -d "$home" ] || return 255\n'
                    '  (cd "$home" && HOME="$home" bash -c "$*")\n}\n')
        prelude = (f"set -eu -o pipefail\nSPEC=specwriter\nIMPL=implementer\n"
                    f"d=ws/src/{PROJECT}-dev\n")
        return run(["bash", "-c", prelude + fake_ssh + step], env=self.env(FAKE=str(self.fake)))

    def trust_entry(self, home):
        """Return the entry of the home's -dev clone in its Claude Code state."""
        state = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
        return state["projects"][str(home / "ws" / "src" / f"{PROJECT}-dev")]

    def trust_lines(self, output):
        return [line for line in output.splitlines() if line.startswith("trust ")]


# The account names of the spec's table of accounts and roles, and the placeholders that
# the CLAUDE.md template of jig new holds in their place.
ACCOUNT_PLACEHOLDERS = {"orchestrator": "@ORCH@", "specwriter": "@SPEC@",
                        "implementer": "@IMPL@"}
# A copy of the orchestrator row of that table in the approved spec, for a tree that
# has no approved worktree beside it. The copy leaves out the one clause that the
# template leaves out too, on the steps of another project's move, because that clause
# names a project that has no place in this repo; the comparison removes it anyway.
SPEC_ORCHESTRATOR_ROW = (
    "| Orchestrator | orchestrator | the `-dev` repo, public sources, the jig change log, "
    "its inbox, the screen that `jig wait` prints, which, with the clean room on, is never "
    "a screen of a session in the specwriter account (section 7); at a stall line, read "
    "only, the screen of the other project's orchestrator (`tmux capture-pane` of "
    "`<other>-orch`) and `impl/orch/state.md` in the other project's `-dev` clone "
    "(section 7, \"Waiting on another project\") | prompts, blocks, `impl/orch/`, the "
    "kickoff record, answers in `QUESTIONS.md`, briefs, the spec-phase summary and timing "
    "rows of `specs/00-status.md`, `CLAUDE.md`, `terms.txt`, "
    "`impl/log/<batch ID>-summary.md`, `requests/<ID>.md` in `jig-dev` "
    "| product code, spec text, the reference, a tag |")


def spec_orchestrator_row():
    """Return the orchestrator row of the approved spec, and where it was read.

    The approved worktree sits beside the product clone in a worker account; a tree
    without one, as in CI, uses the copy above.
    """
    spec = ROOT.parent / f"{ROOT.name}-dev-approved" / "specs" / "jig-spec.md"
    try:
        lines = spec.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if line.startswith("| Orchestrator | "):
            return line, str(spec)
    return SPEC_ORCHESTRATOR_ROW, "the copy in tests/test_jig_new.py"


def as_template_row(row):
    """Return a row of the spec's table in the form that the CLAUDE.md template holds.

    The account names become placeholders. The citations of the spec's parts are left
    out, because lint A refuses a numbered pointer in the product, and so is the clause
    on the steps of another project's move, because it names that project.
    """
    cells = row.split(" | ")
    cells[1] = ACCOUNT_PLACEHOLDERS.get(cells[1], cells[1])
    row = " | ".join(cells)
    row = re.sub(r"\b(orchestrator|specwriter|implementer) account\b",
                 lambda match: ACCOUNT_PLACEHOLDERS[match[1]] + " account", row)
    row = re.sub(r" \(section [0-9]+[^)]*\)", "", row)
    return re.sub(r"; in steps [0-9]+ to [0-9]+ of [^|]*?(?= \|)", "", row)

class JigNewCase(TempTestCase):
    """The fixture layout of jig new: a srv root, an ssh double and fixture accounts."""

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


class JigNewTest(JigNewCase):

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

    def test_made_claude_md_holds_the_security_defect_rule_and_every_command_of_rule_1(self):
        # A new project gets the rule on a known security defect of the reference as a
        # section of its own, which says that it holds only with a reference, so jig new
        # need not know the profile yet; and the first clean-room rule names every jig
        # command that reaches the worker accounts, jig upgrade and jig branch too.
        text = self.made_text("CLAUDE.md")
        section = text.split("## A known security defect of the reference", 1)[1]
        section = section.split(" ## ", 1)[0]
        for rule in ("This section holds in a project with `reference` yes in the project "
                     "profile.",
                     "is named only by its open-item ID, `OI-n`, in `CLAUDE.md` and in every "
                     "record. No record says more about the defect than that ID.",
                     "Work on such a defect is defensive only: the project's own rule, "
                     "written as an approved difference from the reference, with a test.",
                     "No unit writes a tool that is aimed at systems outside the project.",
                     "The disclosure of such a defect is Eric's decision.",
                     "the session does not send the same request again in other words. It "
                     "makes the scope of the work smaller, and then the orchestrator writes "
                     "a brief to Eric.",
                     "A record of such a defect is never deleted."):
            self.assertIn(rule, section)
        self.assertIn("1. The orchestrator never reads `~/ws/ref`, and uses its ssh keys "
                      "into the worker accounts only through jig (`jig start`, `jig wait`, "
                      "`jig send`, `jig close`, `jig cat`, `jig upgrade`, `jig branch`).",
                      text)

    def test_standing_prompt_gives_the_watcher_of_several_units_and_its_restart(self):
        # The watcher takes several unit IDs, each with optional minutes, and ends with
        # the whole output of a wait. The standing prompt must say so, say how to give
        # it new arguments, and say why minutes go into one start only: a restart with
        # the minutes of a unit that has ended makes the watcher exit at once. It must
        # also say when jig wait ends at a unit that sits at its input prompt with a
        # commit that is not pushed, and that the orchestrator then reads the report and
        # escalates instead of waiting on that unit again.
        text = self.made_text("impl/orch/ORCHESTRATOR.md")
        for rule in ("`jig orch demo events --next [<unit ID>[:<minutes>] ...]`",
                     "While units run, pass the ID of each running unit as a `<unit ID>`: "
                     "the watcher then also runs `jig wait` on each of them.",
                     "the watcher prints the whole output of that wait, then the line "
                     "`jig wait <unit ID> exit <code>: <last line>`, and ends.",
                     "each other end gives the whole output of the wait, then the line "
                     "`jig wait <ID> exit <code>: <last line>`.",
                     "To give the watcher a new unit ID or new minutes while it runs, end "
                     "the running watcher and start it again with the new arguments.",
                     "Give the minutes of a unit in one start of the watcher only, not in "
                     "each restart after it: `jig wait` refuses `--expected` for a unit "
                     "with no lock, also when the report of that unit is on origin, so a "
                     "watcher that starts again with `<unit ID>:<minutes>` after the end of "
                     "that unit exits at once with that refusal.",
                     "- A unit that waits at its input prompt with a commit that is not "
                     "pushed: `jig wait` ends with its own code and a line that says so, "
                     "at a check with no screen change, the input bar, no running turn, "
                     "and a commit in the worker's `-dev` clone or product clone that its "
                     "upstream does not have. Read its report with "
                     "`jig cat demo <account> <ID>-report.md`, and escalate; do not start "
                     "`jig wait` on that unit again."):
            self.assertIn(rule, text)
        # The form of the older watcher, one unit ID and one line at the end of a wait,
        # is gone.
        self.assertNotIn("--next [<unit ID>]`", text)
        self.assertNotIn("one line `jig wait", text)
        self.assertNotIn("each other end gives the line", text)

    def test_made_claude_md_has_the_context_limits_and_the_run_of_jig_ctx_after_each_step(self):
        # A unit of a new project learns its limits, 200k and 230k, what each exit code of
        # jig ctx asks of it, and that the report is still written at a limit; the old
        # limits of 250k and 280k are gone.
        text = self.made_text("CLAUDE.md")
        for rule in ("Start no new step above about 200k tokens of context. At about 230k, "
                     "stop after the last complete pushed commit, with a clean tree.",
                     "After each step, run `jig ctx`",
                     "At exit 3 (200k), start no new step.",
                     "At exit 4 (230k), stop after the last complete pushed commit, with a "
                     "clean tree.",
                     "At exit 2, no size, go on, and run `jig ctx` again after the next step",
                     "At exit 3 or 4 the unit still writes, commits and pushes its report: "
                     "the report is no new step, and its status is `OK` when every step "
                     "before the report is done, else not `OK`, with the steps left."):
            self.assertIn(rule, text)
        self.assertNotIn("250k", text)
        self.assertNotIn("280k", text)

    def test_made_orchestrator_md_gives_no_old_context_limit(self):
        text = self.made_text("impl/orch/ORCHESTRATOR.md")
        self.assertNotIn("250k", text)
        self.assertNotIn("280k", text)

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
        # names the watcher that ends at the first event, how the harness runs it (one
        # turn when it ends, no deadline, not the tool that gives a turn for each line),
        # its start again after each wake, the unit ID in place of a separate jig wait,
        # and the once-read of the old session's events; a fresh session reads those
        # events too, and no line says that the supervisor sends a line into the session.
        text = self.made_text("impl/orch/ORCHESTRATOR.md")
        rules = text.split("## Session rules", 1)[1]
        first = rules.split(" - ", 2)[1]
        for part in ("The first thing after each start of this session: start one "
                     "background watcher, `jig orch demo events --next "
                     "[<unit ID>[:<minutes>] ...]`, as a background command of the harness "
                     "that gives you one turn when the command ends (in Claude Code, a Bash "
                     "command run in the background).",
                     "Set no deadline on it,",
                     "(in Claude Code, not with the Monitor tool).",
                     "After each wake, act on what it printed, then start it again.",
                     "While units run, pass the ID of each running unit as a `<unit ID>`:",
                     "Run no separate `jig wait` in the background.",
                     "Never run two watchers at once,",
                     "`~/ws/reviews/demo/orch/events.log`",
                     "`~/ws/reviews/demo/orch/events.mark`",
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
        # Minutes occur in the first rule only as the expected duration that the
        # watcher passes on to a wait, never as a time limit on the watcher itself.
        rest = first
        for allowed in ("[:<minutes>]", "<unit ID>:<minutes>", "new minutes",
                        "the minutes of a unit"):
            rest = rest.replace(allowed, "")
        self.assertNotIn("minutes", rest)
        # The tool that gives a turn for each line is named only to rule it out, never
        # as the way to run the watcher.
        self.assertEqual(text.count("Monitor"), 1)
        self.assertNotIn("(in Claude Code, the Monitor tool)", text)
        self.assertNotIn("deadline that the tool allows", text)
        # A batch and a fresh session pass the unit ID to the watcher instead of a
        # jig wait of their own.
        self.assertIn("In a batch, pass the running unit's ID to the watcher, which runs "
                      "`jig wait` on it;", text)
        self.assertIn("passes the running unit's ID to its watcher", text)
        self.assertNotIn("run `jig wait` on the running unit", text)
        self.assertNotIn("runs `jig wait` on the running unit", text)

    def test_standing_prompt_holds_one_review_a_fix_unit_and_the_backlog(self):
        # The spec loop has one Fable review of a spec document or a plan and at most
        # one fix unit after it; the findings that are not must-fix go to the backlog.
        # The rule of review rounds is gone, so an orchestrator does not order a second
        # review after the fix.
        text = self.made_text("impl/orch/ORCHESTRATOR.md")
        loop = text[text.index("- Spec loop:"):text.index("- Batches:")]
        for rule in ("A spec document or a plan gets at most one Fable review, and only "
                     "where jig's spec says that one is needed",
                     "one fix unit applies every must-fix finding, and no second review "
                     "follows the fix unit.",
                     "Every finding that is not must-fix goes to the backlog: add a line for "
                     "it to `impl/orch/backlog.md`.",
                     "A line leaves the backlog when a unit or a spec revision takes it.",
                     "when a fix unit ran, the row of each of these documents has four "
                     "cells, with the same review ID and fix ID, also when the fix unit did "
                     "not change that document."):
            self.assertIn(rule, loop)
        # Only the rule of a refused push counts rounds, those of fetch and rebase.
        push = ("up to 3 rounds while git reports no conflict. A conflict in a rebase, or a "
                "refused push in the third round,")
        self.assertIn(push, text)
        self.assertIsNone(re.search(r"\brounds?\b", text.replace(push, "")))
        self.assertIn("`backlog.md` (the findings of a spec or plan review that are not "
                      "must-fix,", text)

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

    def test_standing_prompt_lists_the_events_without_an_hourly_update_line(self):
        # The supervisor appends no "hourly update" event, so the standing prompt lists
        # the events it does append and names no such line, which the orchestrator would
        # otherwise wait for.
        text = " ".join(self.made_text("impl/orch/ORCHESTRATOR.md").split())
        self.assertIn("(the `inbox:` and `readback:` lines, the lines `report: <ID> <status>` "
                      "and the reminders)", text)
        self.assertNotIn("`hourly update`", text)

    def test_standing_prompt_holds_the_end_of_a_turn_and_the_form_of_the_waiting_file(self):
        # An orchestrator that ends its turn with no unit running and no declared wait
        # sits idle in silence; the standing prompt names the three states that may end
        # a turn, the waiting file and its form, and what to do at an idle event and at
        # each line of jig batch.
        text = self.made_text("impl/orch/ORCHESTRATOR.md")
        for line in ("Before you end a turn, check that one of these holds: - a unit or a "
                     "batch runs, and the watcher runs with the ID of each running unit; - "
                     "you wait on purpose, because no work is ready, or the next step waits "
                     "on Eric or on another project; you told Eric so once, in one plain "
                     "line; and you wrote the waiting file for this wait; - you ran "
                     "`jig orch demo rollover`. When none holds, do the next step.",
                     "The waiting file is `~/ws/reviews/demo/orch/waiting`: one line, the "
                     "output of `date -Iseconds`, one space, and the reason of the wait in "
                     "plain words,",
                     "When a wait follows a unit, write the file after the worker of that "
                     "unit is closed.",
                     "`idle: no unit since <HH:MM>; start the next step, or write why you "
                     "wait in ~/ws/reviews/demo/orch/waiting`.",
                     "- An idle event: do the next step, or write the waiting file and tell "
                     "Eric once.",
                     "- A line `batch: <batch file> refused: <reason>`: fix the batch file "
                     "and start the batch again, or tell Eric.",
                     "- A line `batch: <batch file> ended: <exit code>`: read the tail of the "
                     "output file of the batch. When you still wait, write the waiting file "
                     "again,"):
            self.assertIn(line, text)

    def test_templates_give_the_refused_push_in_three_rounds_and_no_single_rebase(self):
        # The push of a report can lose a race with origin more than once, so every
        # template gives up to 3 rounds of fetch, rebase and push, and a unit that still
        # cannot push leaves its report where the orchestrator can read it.
        rounds = ("push again, up to 3 rounds while git reports no conflict. A conflict in "
                  "a rebase, or a refused push in the third round, is an escalation.")
        claude = self.made_text("CLAUDE.md")
        standing = self.made_text("impl/orch/ORCHESTRATOR.md")
        self.assertIn("A push to `-dev` that is refused: fetch, rebase the unpushed commits, "
                      "and " + rounds, claude)
        self.assertIn("A unit that cannot push its report commit also writes its report to "
                      "`~/ws/reviews/demo/<ID>-report.md`, and stops with the text of its "
                      "report as its last message", claude)
        self.assertIn("A push to `-dev` that is refused: fetch, rebase your unpushed commits, "
                      "and " + rounds, standing)
        for text in (claude, standing):
            self.assertNotIn("rebase the unpushed commits once", text)
            self.assertNotIn("a second refusal", text)

    def test_standing_prompt_moves_the_line_with_jig_branch_after_a_minor_or_major(self):
        # After a minor or major release the orchestrator runs jig branch before its next
        # batch, with its own changes pushed first, who pushes the new line in each
        # repository, no batch after an exit 1 and a word to Eric, and the owner block
        # for an older jig.
        text = " ".join(self.made_text("impl/orch/ORCHESTRATOR.md").split())
        for rule in ("After the release of a minor or major, move the project to the line "
                     "of the next minor or major before your next batch.",
                     "No block makes a new line. The release unit of the minor or major, or "
                     "the first unit that needs the next line, pushes the branch of that "
                     "line in the product at the release commit; you push it in `-dev` at "
                     "the head of the line.",
                     "commit and push your own changes, the idle lines of `updates.log` "
                     "among them, so that your `-dev` clone is clean, and run "
                     "`jig branch demo <line>`.",
                     "When it exits 1, start no batch on the line, and tell Eric which clone "
                     "or conf file failed and why.",
                     "With a jig older than 0.10.0, which has no `jig branch`, each move of "
                     "`BRANCH` stays an owner block that Eric runs."):
            self.assertIn(rule, text)

    def test_orchestrator_templates_hold_no_hourly_update(self):
        # Nothing starts an update each hour any more, so neither the standing prompt
        # nor the handoff template may give the orchestrator a rule for one; the
        # standing prompt says instead when an update is written.
        self.made()
        texts = {}
        for name in ("ORCHESTRATOR.md", "handoff.md"):
            path = self.home / "ws/src/demo-dev/impl/orch" / name
            texts[name] = " ".join(path.read_text(encoding="utf-8").split())
            self.assertNotIn("hourly update", texts[name].lower(), name)
        self.assertIn("- Update: write one as the first message to the console in each "
                      "session, and when a result changes what Eric expects or something "
                      "needs him; never by the clock.", texts["ORCHESTRATOR.md"])

    def test_standing_prompt_shows_each_celebration_at_eric_next_message(self):
        # A celebration shown before Eric writes can scroll past unseen. The common part
        # of the standing prompt, apart from the preferences included above it, holds
        # the rule of when to show one, the item in state.md that keeps it until then,
        # and the order at the top of a reply.
        text = " ".join(self.made_text("impl/orch/ORCHESTRATOR.md").split())
        part = text.split("Updates and celebrations:", 1)[1].split("## Blocks", 1)[0]
        for line in ("write each celebration to `updates.log` when it happens. Show it in "
                     "the console at the top of your reply to Eric's next message, and not "
                     "before,",
                     "Keep each celebration that you have not shown as one item in the Open "
                     "part of `state.md`, and remove the item when you have shown the "
                     "celebration.",
                     "The order at the top of a reply: first the celebrations not yet shown, "
                     "then the question that Eric did not answer, then the summary after a "
                     "gap of about an hour."):
            self.assertIn(line, part)

    def test_preferences_hold_the_reply_rule_of_celebrations(self):
        # The preferences that every standing prompt includes hold the same rule: the
        # celebration at the top of the reply to Eric's next message, the item in
        # state.md, and the order at the top of a reply.
        text = " ".join((ROOT / "prefs.md").read_text(encoding="utf-8").split())
        for line in ("Show each celebration at the top of the reply to Eric's next "
                     "message, and not before,",
                     "Keep each celebration not yet shown as one item in the Open part of "
                     "`state.md`, and remove the item when you have shown it.",
                     "The order at the top of a reply: first the celebrations not yet "
                     "shown, then the question that Eric did not answer, then the summary "
                     "after a gap of about an hour."):
            self.assertIn(line, text)

    def test_standing_prompt_gives_the_tag_block_and_the_row_of_a_closed_loop(self):
        # An orchestrator of a new project must learn the tag block line and the exact
        # form of the table that jig tag reads, or the first revision tag is refused.
        # That holds also for the row of four cells, which closes a loop with a fix
        # unit in place of a second review.
        text = self.made_text("impl/orch/ORCHESTRATOR.md")
        for line in ("`BRANCH=<line> jig tag demo <tag> <commit>`, with `<line>` the `BRANCH` "
                     "of your conf file, `~/.config/jig/demo.conf`.",
                     "a row in the table under a Markdown heading with exactly the text "
                     "`Closed review loops` in `specs/00-status.md`.",
                     "The row is `| <document path> | <commit> | <ID> |`, with the document as "
                     "its path from the repo root",
                     "the commit that the review read,",
                     "`| <document path> | <commit> | <ID> | <fix ID> |`, with the fix unit's "
                     "commit;",
                     "the first line of `impl/log/<fix ID>-report.md` must be exactly `OK` "
                     "or start with `OK:`, and its `commits:` line must not be `none`.",
                     "at least one of the documents of the rows with that review ID and fix "
                     "ID must differ between the oldest commit that adds the findings file "
                     "and the commit to tag:",
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

    def test_standing_prompt_rolls_over_only_at_exit_3_or_4_and_never_at_a_batch_boundary(self):
        # The rules for jig ctx exit 3 and exit 4 stay as they are, and the only
        # sentence that names a rollover beside a batch boundary is the one that rules
        # the rollover there out.
        flat = self.made_text("impl/orch/ORCHESTRATOR.md")
        only = "Roll over only when `jig ctx` exits 3 or 4, never at a batch boundary."
        for rule in ("3, at about 200k tokens: roll over. Start no new step. Finish the step "
                     "in hand, write `state.md` and `handoff.md`, commit them, and end the "
                     "session",
                     "4, at about 230k tokens: exit after the current tool call. Stop every "
                     "background shell and the watcher, but not a detached `jig batch`, then "
                     "run `jig orch demo rollover`.", only):
            self.assertIn(rule, flat)
        both = [sentence for sentence in re.split(r"(?<=\.)\s+(?:- )?", flat)
                if "batch boundary" in sentence and "roll" in sentence.lower()]
        self.assertEqual(both, [only])

    def test_standing_prompt_upgrades_jig_at_the_batch_boundary_before_the_next_batch(self):
        # At a batch boundary the orchestrator reads the change log and upgrades there,
        # before the next batch, and leaves no upgrade to the next session.
        flat = self.made_text("impl/orch/ORCHESTRATOR.md")
        for rule in ("At every batch boundary, before you start the next batch, run "
                     "`jig changelog --since <pinned>`",
                     "When it prints a newer version, run `jig upgrade demo <version>` with "
                     "the newest version at that batch boundary, before you start the next "
                     "batch.",
                     "Do not keep the upgrade in `handoff.md` for the next session."):
            self.assertIn(rule, flat)

    def test_handoff_file_names_no_batch_boundary_and_no_upgrade_for_the_next_session(self):
        # A session ends only at a context limit, and the one sentence on an upgrade
        # keeps it out of the items for the next session.
        flat = self.made_text("impl/orch/handoff.md")
        self.assertNotIn("batch boundary", flat.lower())
        self.assertIn("and why: the soft context limit (`jig ctx` exit 3) or the hard "
                      "context limit (exit 4). No rollover yet.", flat)
        upgrades = [sentence for sentence in re.split(r"(?<=\.)\s+", flat)
                    if "upgrade" in sentence.lower()]
        self.assertEqual(upgrades, ["A jig upgrade is never an item here: run it when a batch "
                                    "ends, before the next batch starts."])

    def test_standing_prompt_asks_at_the_kickoff_for_the_section_directories(self):
        # The kickoff part of the standing prompt names the section "Directories" and
        # its line form, so that the orchestrator of a new project asks for the extra
        # folders of each account.
        flat = self.made_text("impl/orch/ORCHESTRATOR.md")
        kickoff = flat[flat.index("- Kickoff:"):flat.index("- Project profile:")]
        for text in ("the extra folders of each account, when there are any.",
                     'Record them in the section "Directories" of the kickoff record: the '
                     "line `## Directories`, then one line `<account>: <folder>` for each "
                     "folder.",
                     "The account is `orchestrator`, `specwriter` or `implementer`; the "
                     "folder is an absolute path, or a path that starts with `~/` for the "
                     "home of that account."):
            self.assertIn(text, kickoff)

    def test_claude_md_template_has_the_orchestrator_row_of_the_spec(self):
        # The orchestrator row of the template is the row of the spec's table, with the
        # account names as placeholders, without the spec's citations and without the
        # clause on another project's move. On failure the message names where the row
        # of the spec was read.
        row, source = spec_orchestrator_row()
        template = (ROOT / "templates/new/dev/CLAUDE.md").read_text(encoding="utf-8")
        rows = [line for line in template.splitlines() if line.startswith("| Orchestrator | ")]
        self.assertEqual(rows, [as_template_row(row)], source)

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
        for rule in ("After each step, run `jig ctx`.", "3, at about 200k tokens: roll over.",
                     "4, at about 230k tokens: exit after the current tool call.",
                     "a rollover does not affect it",
                     "Its first message to the console is its first update: a summary of "
                     "the last session and the next steps, which cites the commit of "
                     "`handoff.md`."):
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

    def test_made_claude_md_has_the_commit_message_standard_after_the_comment_standard(self):
        # A new project's CLAUDE.md holds the commit message rules a to e in full, in
        # the section right after the comment rules, because the implementer's prompt
        # names that section and jig validate prompt refuses one that does not. Its
        # opening says that the history is written for a public reader, which holds
        # for a private product too, and no longer that the history is public.
        self.made()
        text = (self.home / "ws/src/demo-dev/CLAUDE.md").read_text(encoding="utf-8")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertIn("## Commit message standard", headings)
        place = headings.index("## Comment standard")
        self.assertEqual(headings[place + 1], "## Commit message standard", headings)
        section = text.split("\n## Commit message standard\n", 1)[1].split("\n## ", 1)[0]
        flat = " ".join(section.split())
        self.assertTrue(flat.startswith(
            "Every commit message in the product is written for the reader of the comment "
            "standard: a competent maintainer who has never seen the specs, the process or "
            "the `-dev` repo. The history of the product is written for a public reader, "
            "because a private product can become public later."), flat)
        self.assertNotIn("The history of the product is public.", flat)
        for rule in (
                "- a. The subject says what the commit changes, in the imperative (\"Add\", "
                "\"Fix\", \"Read\"), in at most 72 characters, with no period at the end.",
                "- b. When the reason is not plain from the subject, a blank line and then a "
                "body: why the change was needed and which failure it prevents, in full "
                "sentences, with lines of at most 72 characters.",
                "- c. No unit IDs, rule IDs, finding numbers, spec sections or process words. "
                "The link from a rule to the commit lives in the `-dev` repo.",
                "- d. No padding: no praise of the change, no list of the tests that pass, and "
                "no retelling of the diff line by line.",
                "- e. The trailers are the ones of the product repo rules, and nothing hides "
                "that an AI wrote the code."):
            self.assertIn(rule, flat)
        self.assertTrue(flat.endswith(
            "Every prompt whose first line names the implementer account names this section "
            "on its ROLE line, beside \"Comment standard\", and `jig validate prompt` refuses "
            "such a prompt that does not."), flat)

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
        # The product tracks lint A's commit-msg hook at the path of jig's commit-msg
        # hook: that file stays, and no exclude line names it.
        self.assertEqual((product / "hooks/commit-msg").read_bytes(),
                         (ROOT / "hooks" / "commit-msg").read_bytes())
        self.assertNotIn("/hooks/commit-msg", exclude)

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

    def test_a_directory_at_the_place_of_the_product_clone_is_not_overwritten(self):
        # ~/ws/src/demo exists on its own, with no -dev clone beside it: jig new refuses
        # with one line that names it, and changes nothing.
        kept = self.home / "ws" / "src" / PROJECT / "notes.txt"
        kept.parent.mkdir(parents=True)
        kept.write_text("keep\n", encoding="utf-8")
        before = self.snapshot()
        result = self.new(PROJECT)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.splitlines(),
                         [f"jig new: project {PROJECT} exists ({kept.parent}) and jig new "
                          "did not make it; nothing is overwritten"])
        self.assertEqual(self.snapshot(), before)

    def test_a_product_clone_left_by_a_stopped_run_is_refused_with_a_way_out(self):
        # A run that stopped between its two moves left ~/ws/src/demo with no -dev
        # clone beside it, and its marker tells the rerun that jig new started the
        # project. The rerun gets as far as making the clones, refuses there with one
        # line that names the directory and says it may be removed, and changes nothing:
        # it never moves its new clone onto the old one.
        self.made()
        shutil.rmtree(self.home / "ws" / "src" / f"{PROJECT}-dev")
        product = self.home / "ws" / "src" / PROJECT
        self.assertTrue((self.srv / "blocks" / PROJECT / ".jig-new").is_file())
        before = self.snapshot()
        result = self.new(PROJECT)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr.splitlines(),
                         [f"jig new: {product} exists with no {PROJECT}-dev beside it; "
                          "nothing is overwritten (if a stopped jig new left it, remove it "
                          "and run jig new again)"])
        self.assertNotIn("Traceback", result.stdout + result.stderr)
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


class JigNewTrustTest(TrustStepMixin, JigNewCase):
    """The trust mark of the -dev clone: jig new marks its own account, the block the rest."""

    def test_new_marks_the_orchestrators_dev_clone_and_says_so_in_every_run(self):
        # The first run makes the mark; a second run, which writes no block, finds it
        # there, writes nothing and still prints its line.
        first = self.made()
        self.assertEqual(self.trust_lines(first.stdout), [f"trust {ORCH}: trusted"])
        self.assertEqual(self.trust_entry(self.home), {"hasTrustDialogAccepted": True})
        state = self.home / ".claude.json"
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        before = state.stat()
        second = self.new(PROJECT)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("kept ", second.stdout)
        self.assertEqual(self.trust_lines(second.stdout), [f"trust {ORCH}: kept"])
        self.assertEqual((state.stat().st_ino, state.stat().st_mtime_ns),
                         (before.st_ino, before.st_mtime_ns))

    def test_new_sets_a_mark_that_a_stopped_first_run_did_not_set(self):
        # A first run that stopped after it made the clone left no mark.
        self.made()
        (self.home / ".claude.json").unlink()
        second = self.new(PROJECT)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.trust_lines(second.stdout), [f"trust {ORCH}: trusted"])
        self.assertEqual(self.trust_entry(self.home), {"hasTrustDialogAccepted": True})

    def test_new_names_a_file_it_cannot_use_and_still_exits_0(self):
        state = self.home / ".claude.json"
        state.write_text("{not json", encoding="utf-8")
        result = self.made()
        lines = result.stdout.splitlines()
        at = lines.index(f"trust {ORCH}: not marked")
        self.assertIn(f"{state} of {ORCH}: it is not JSON", lines[at - 1])
        self.assertEqual(state.read_text(encoding="utf-8"), "{not json")

    def test_block_marks_the_dev_clone_of_the_spec_writer_and_the_implementer(self):
        self.made()
        for value in ("trusted", "kept"):
            result = self.run_trust_step()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.splitlines(),
                             [f"trust specwriter: {value}", f"trust implementer: {value}"])
        for account in ("specwriter", "implementer"):
            home = self.account(account)
            self.assertEqual(self.trust_entry(home), {"hasTrustDialogAccepted": True})
            self.assertEqual((home / ".claude.json").stat().st_mode & 0o777, 0o600)

    def test_block_goes_on_past_an_account_it_cannot_mark(self):
        # The implementer cannot be reached and the spec writer's file is not JSON:
        # each gets its line and "not marked", and the step ends with exit 0.
        self.made()
        shutil.rmtree(self.account("implementer"))
        state = self.account("specwriter") / ".claude.json"
        state.write_text("[]", encoding="utf-8")
        result = self.run_trust_step()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn(f"{state} of specwriter: its top level is not an object", lines[0])
        self.assertEqual(lines[1:], ["trust specwriter: not marked", "new: no ssh to implementer",
                                     "trust implementer: not marked"])
        self.assertEqual(state.read_text(encoding="utf-8"), "[]")


def prompt_section(text, name, following):
    """The words of one section of a prompt, from its label up to the next label."""
    start = text.index(f"\n{name}:")
    return " ".join(text[start:text.index(f"\n{following}:", start)].split())


class PromptTemplateTest(unittest.TestCase):
    """The prompt template that the orchestrator fills for each unit of a new project."""

    def setUp(self):
        self.text = (ROOT / "templates" / "prompt.md").read_text(encoding="utf-8")

    def test_session_economy_says_to_edit_files_with_the_edit_tool(self):
        # A long shell command that writes files can bring up a safety dialog, and a
        # dialog stops an unattended session; every prompt says so in its economy rules.
        economy = prompt_section(self.text, "SESSION ECONOMY", "STEPS")
        self.assertIn("Edit files with the edit tool, never with a shell heredoc or with a "
                      "script that writes files.", economy)

    def test_steps_give_the_refused_push_in_three_rounds_and_the_report_in_reviews(self):
        # Every unit learns from its prompt how many rounds a refused push gets, and
        # where its report goes when the report commit cannot be pushed.
        steps = prompt_section(self.text, "STEPS", "ESCALATION")
        self.assertIn("A push to -dev that is refused: fetch, rebase the unpushed commits, "
                      "and push again, up to 3 rounds while git reports no conflict; a "
                      "conflict in a rebase, or a refused push in the third round, is an "
                      "escalation.", steps)
        self.assertIn("A unit that cannot push its report commit also writes its report to "
                      "~/ws/reviews/<project>/<ID>-report.md, and stops with the text of its "
                      "report as its last message.", steps)
        self.assertNotIn("once", self.text.split("\nSTEPS:", 1)[1])

    def test_role_holds_the_security_defect_rule_for_a_project_with_a_reference(self):
        # The prompt of a project with a reference carries each part of the rule on a
        # known security defect of that reference, in words that fit any project.
        role = prompt_section(self.text, "ROLE", "STATE")
        rule = role.split("<In a project with reference yes: ", 1)[1].split(">", 1)[0]
        for part in ("a known security defect of the reference is named only by its OI-n, "
                     "in CLAUDE.md and in every record, and no record says more about it.",
                     "Work on such a defect is defensive only: the project's own rule, "
                     "written as an approved difference from the reference, with a test.",
                     "Write no tool that is aimed at systems outside the project.",
                     "The disclosure of the defect is Eric's decision.",
                     "When the model refuses a request on it, do not send the same request "
                     "again in other words: make the scope of the work smaller, and report "
                     "NEEDS ERIC with the reason, so that the orchestrator writes a brief "
                     "to Eric.",
                     "The record of such a defect is never deleted."):
            self.assertIn(part, rule)
        # The rule is generic: it names no path of a reference and no record number.
        self.assertNotIn("~/ws/ref", rule)
        self.assertIsNone(re.search(r"OI-[0-9]", rule))


if __name__ == "__main__":
    unittest.main()
