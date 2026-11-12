import pathlib
import re
import shutil
import tempfile
import unittest

from tests.support import NO_CLEAN_ROOM, ROOT, TempTestCase, clean_env, kickoff_record, \
    make_dev_repos, push_kickoff, run

JIG = ROOT / "bin" / "jig"
TOOL = ROOT / "libexec" / "jig-validate"
FIXTURES = ROOT / "tests" / "fixtures" / "validate"
TEMPLATES = ROOT / "templates"

SECTIONS = ("UNATTENDED", "MODEL CHECK", "EFFORT", "ROLE", "STATE", "READ", "AUTHORITY",
            "PREMISE CHECK", "REQUIREMENTS", "PROOF", "SESSION ECONOMY", "STEPS", "ESCALATION",
            "REPORT")

HEADER_LABELS = ("jig-report", "id", "class", "commits", "checks", "proofs", "start", "next")

GOOD_FIRST_LINE = "# demo  paste into: jig-impl-demo (implementer, ~/ws/src/jig-dev)"

# One line number of the report, and wrong values for it. Line 10 is the "---".
WRONG_HEADERS = {
    1: ("", "ok", "OK:", "OK: ", "OK note", "OK[: note]", "OK: <note>", "OK | NEEDS ERIC: x",
        "DONE: x", "NEEDS ERIC", "NEEDS ERIC:", "NEEDS ERIC: ", "NEEDS ERIC: <reason>",
        "NEEDS ERIC x", "needs eric: x", "NEEDS SPEC", "NEEDS SPEC: why", "NEEDS SPEC: IQ-",
        "NEEDS SPEC: IQ-n", "NEEDS SPEC: Q-3", "NEEDS SPEC: IQ-3 why", " OK"),
    2: ("", "jig-report: 2", "jig-report:1", "jig-report: ", "report: 1", "jig-report: 1 "),
    3: ("", "id:", "id: ", "id: Demo", "id: sevench", "id: a b", "ID: demo", "id: <ID>",
        "id: demo ", "id:  demo"),
    4: ("", "class:", "class: ", "class: huge", "class: Minor", "class: minor ",
        "class: minor patch", "class: none | patch | minor | major", "clas: minor"),
    5: ("", "commits:", "commits: ", "commits: xyz1234", "commits: abc", "commits: ABC1234",
        "commits: none abc1234", "commits: abc1234  def5678", "commits: abc1234 ",
        "commits: <hash>", "commits: None", "commit: abc1234", "commits: " + "a" * 41),
    6: ("", "checks:", "checks: ", "checks: make-check", "checks: make-check=",
        "checks: make-check=x", "checks: make-check=256", "checks: make-check=00",
        "checks: make-check=-1", "checks: make-check=0 make-check=1", "checks: =0",
        "checks: none make-check=0", "checks: make-check=none", "checks: None",
        "checks: make-check=0  mutants=0", "check: make-check=0", "checks: Make=0"),
    7: ("", "proofs:", "proofs: ", "proofs: none", "proofs: P1", "proofs: P1=", "proofs: P1=x",
        "proofs: p1=0", "proofs: P0=0", "proofs: P1=256", "proofs: P1=0 P1=0",
        "proofs: P1=0  P2=0", "proofs: P1=<exit>", "proofs: make-check=0", "proof: P1=0"),
    8: ("", "start: x  end: y  context: n/a",
        "start: 2026-11-06T22:51:35-08:00 end: 2026-11-06T23:06:49-08:00 context: n/a",
        "start: 2026-11-06T22:51:35-08:00  end: 2026-11-06T23:06:49-08:00",
        "start: 2026-11-06T22:51:35-08:00  end: 2026-11-06T23:06:49-08:00  context:",
        "start: 2026-11-06T22:51:35-08:00  end: 2026-11-06T23:06:49-08:00  context: ",
        "start: 2026-11-06T22:51:35-08:00  end: 2026-11-06T23:06:49-08:00  "
        "context: <filled by jig report>",
        "start: 2026-13-06T22:51:35-08:00  end: 2026-11-06T23:06:49-08:00  context: n/a",
        "start: 2026-11-06T22:51:35-08:00  end: 2026-11-31T23:06:49-08:00  context: n/a",
        "start: 2026-11-06T22:51:35  end: 2026-11-06T23:06:49-08:00  context: n/a",
        "start: 2026-11-06 22:51:35-08:00  end: 2026-11-06T23:06:49-08:00  context: n/a",
        "end: 2026-11-06T23:06:49-08:00  start: 2026-11-06T22:51:35-08:00  context: n/a",
        "start: <ISO time>  end: <ISO time>  context: n/a"),
    9: ("", "next:", "next: ", "next: <one line: what the next unit needs>", "nxt: x", "Next: x"),
    10: ("", "--", "----", "--- ", " ---", "==="),
}


# The projects the prompts of these tests name in their first line. Each gets a conf
# file whose -dev clone has a profile with the clean room on, so that the tests see
# the rules as they stand today, with nothing extra on stderr.
PROJECTS = ("jig", "my-app")

# The first line of a prompt, as jig validate reads it; a prompt whose first line does
# not have this form gives the tool no project, and so no profile.
FIRST_LINE = re.compile(r"# \S+  paste into: \S+ \([a-z][a-z0-9_-]*, "
                        r"~/ws/src/[a-z0-9][a-z0-9._-]*-dev\)")


def write_conf(home, project, repo):
    conf = home / ".config" / "jig" / f"{project}.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(f"REPO={repo}\nBRANCH=master\n", encoding="utf-8")
    return conf


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def prompt_parts():
    """Split the good prompt into its first line, one block per section, and its last line."""
    return prompt_parts_of(fixture("good-prompt.md"))


def prompt_parts_of(text):
    lines = text.splitlines()
    blocks = {}
    current = None
    for line in lines[1:-1]:
        label = next((name for name in SECTIONS if line.startswith(name + ":")), None)
        if label:
            current = label
            blocks[current] = []
        blocks[current].append(line)
    return lines[0], blocks, lines[-1]


def prompt_text(first, blocks, last, order=SECTIONS):
    body = [line for label in order for line in blocks[label]]
    return "\n".join([first, *body, last]) + "\n"


def report_lines():
    return fixture("good-report.md").splitlines()


def report_with(number, line):
    lines = report_lines()
    lines[number - 1] = line
    return "\n".join(lines) + "\n"


class ValidateCase(TempTestCase):
    @classmethod
    def setUpClass(cls):
        # One origin and -dev clone for the whole class: the tests only read them.
        base = pathlib.Path(tempfile.mkdtemp(prefix="jig-test."))
        cls.addClassCleanup(shutil.rmtree, base, ignore_errors=True)
        (base / "home").mkdir()
        env = clean_env(HOME=str(base / "home"))
        _, work, cls.profile_clone = make_dev_repos(base, env)
        push_kickoff(env, work, 1, kickoff_record("clean room: on"))

    def setUp(self):
        super().setUp()
        for project in PROJECTS:
            write_conf(self.home, project, self.profile_clone)

    def validate(self, kind, text):
        path = self.tmp / "input.md"
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8", newline="")
        return run([TOOL, kind, path], env=self.env())

    def assert_valid(self, kind, text):
        result = self.validate(kind, text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def assert_invalid(self, kind, text, *needles):
        result = self.validate(kind, text)
        self.assertEqual(result.returncode, 1, (text, result.stderr))
        self.assertNotIn("Traceback", result.stderr)
        for needle in needles:
            self.assertIn(needle, result.stderr)
        lines = result.stderr.splitlines()
        # A prompt with no first line to read the project from cannot reach a profile,
        # so the tool says it acts as with the clean room on; any other prompt names a
        # project whose profile these tests set.
        first = text.split(b"\n" if isinstance(text, bytes) else "\n", 1)[0]
        if kind == "prompt" and not FIRST_LINE.fullmatch(
                first.decode("utf-8", "replace") if isinstance(first, bytes) else first):
            self.assertEqual(lines[:1], [NO_CLEAN_ROOM], result.stderr)
            lines = lines[1:]
        for line in lines:
            self.assertTrue(line.startswith("jig validate: "), line)
        return lines


class ValidatePromptTest(ValidateCase):
    def test_validate_prompt_accepts_the_good_fixture(self):
        self.assert_valid("prompt", fixture("good-prompt.md"))

    def test_validate_prompt_names_each_failure_of_the_bad_fixture_on_one_line(self):
        failures = self.assert_invalid("prompt", fixture("bad-prompt.md"))
        self.assertEqual(len(failures), 3, failures)
        self.assertIn("section PROOF is missing", failures[0])
        self.assertIn("section SESSION ECONOMY is out of order", failures[1])
        self.assertIn("'other'", failures[2])

    def test_validate_prompt_fails_with_each_section_missing(self):
        first, blocks, last = prompt_parts()
        for label in SECTIONS:
            with self.subTest(label):
                order = [name for name in SECTIONS if name != label]
                failures = self.assert_invalid("prompt", prompt_text(first, blocks, last, order),
                                               f"section {label} is missing")
                self.assertEqual(len(failures), 1, failures)

    def test_validate_prompt_fails_with_each_section_swapped_with_the_next(self):
        first, blocks, last = prompt_parts()
        for index in range(len(SECTIONS) - 1):
            order = list(SECTIONS)
            order[index], order[index + 1] = order[index + 1], order[index]
            with self.subTest(order[index]):
                failures = self.assert_invalid(
                    "prompt", prompt_text(first, blocks, last, order),
                    f"section {SECTIONS[index]} is out of order",
                    f"it follows {SECTIONS[index + 1]}")
                self.assertEqual(len(failures), 1, failures)

    def test_validate_prompt_fails_with_each_section_moved_to_either_end(self):
        first, blocks, last = prompt_parts()
        for label in SECTIONS:
            rest = [name for name in SECTIONS if name != label]
            for order in ([label, *rest], [*rest, label]):
                if tuple(order) == SECTIONS:
                    continue
                with self.subTest(order[0] + " first " + order[-1] + " last"):
                    self.assert_invalid("prompt", prompt_text(first, blocks, last, order),
                                        "is out of order")

    def test_validate_prompt_fails_with_each_section_twice(self):
        first, blocks, last = prompt_parts()
        for label in SECTIONS:
            with self.subTest(label):
                doubled = dict(blocks)
                doubled[label] = blocks[label] * 2
                self.assert_invalid("prompt", prompt_text(first, doubled, last),
                                    f"section {label} appears twice")

    def test_validate_prompt_fails_with_a_section_not_at_the_start_of_a_line(self):
        first, blocks, last = prompt_parts()
        for label in SECTIONS:
            with self.subTest(label):
                moved = dict(blocks)
                moved[label] = ["  " + blocks[label][0], *blocks[label][1:]]
                self.assert_invalid("prompt", prompt_text(first, moved, last),
                                    f"section {label} is missing")

    def test_validate_prompt_fails_with_a_section_inside_another_line(self):
        first, blocks, last = prompt_parts()
        moved = dict(blocks)
        moved["PROOF"] = ["  see PROOF: below"]
        self.assert_invalid("prompt", prompt_text(first, moved, last),
                            "section PROOF is missing")

    def test_validate_prompt_needs_the_label_whole(self):
        first, blocks, last = prompt_parts()
        for wrong in ("PROOF P1 passes", "PROOF:P1 passes", "PROOFS: P1 passes", "Proof: P1 passes"):
            with self.subTest(wrong):
                moved = dict(blocks)
                moved["PROOF"] = [wrong]
                self.assert_invalid("prompt", prompt_text(first, moved, last),
                                    "section PROOF is missing")

    def test_validate_prompt_does_not_take_do_not_read_for_read(self):
        first, blocks, last = prompt_parts()
        moved = dict(blocks)
        moved["READ"] = ["DO NOT READ: transcripts"]
        self.assert_invalid("prompt", prompt_text(first, moved, last), "section READ is missing")

    def test_validate_prompt_accepts_a_label_alone_on_its_line(self):
        first, blocks, last = prompt_parts()
        moved = dict(blocks)
        moved["REQUIREMENTS"] = ["REQUIREMENTS:", "  1. A thing."]
        self.assert_valid("prompt", prompt_text(first, moved, last))

    def test_validate_prompt_fails_on_a_wrong_first_line(self):
        _, blocks, last = prompt_parts()
        wrong = {
            "": "first line",
            "demo": "first line",
            "# demo": "first line",
            "# demo paste into: jig-impl-demo (implementer, ~/ws/src/jig-dev)": "first line",
            "demo  paste into: jig-impl-demo (implementer, ~/ws/src/jig-dev)": "first line",
            " # demo  paste into: jig-impl-demo (implementer, ~/ws/src/jig-dev)": "first line",
            "# demo  paste into: jig-impl-demo (implementer)": "first line",
            "# demo  paste into: jig-impl-demo (implementer, ~/ws/src/jig)": "first line",
            "# demo  paste into: jig-impl-demo (implementer, ~/ws/src/jig-dev) ": "first line",
            "# <ID>  paste into: jig-impl-demo (implementer, ~/ws/src/jig-dev)": "'<ID>'",
            "# demo  paste into: jig-demo (implementer, ~/ws/src/jig-dev)": "'jig-demo'",
            "# demo  paste into: jig-impl-other (implementer, ~/ws/src/jig-dev)": "'jig-impl-other'",
            "# demo  paste into: other-impl-demo (implementer, ~/ws/src/jig-dev)": "'other-impl-demo'",
        }
        for line, needle in wrong.items():
            with self.subTest(line):
                self.assert_invalid("prompt", prompt_text(line, blocks, last), needle)

    def test_validate_prompt_fails_on_a_wrong_last_line(self):
        first, blocks, _ = prompt_parts()
        for line in ("", "demo", "# end", "# end ", "# end  demo", "# demo", "#end demo",
                     "# end other", "# end DEMO", "# end demo "):
            with self.subTest(line):
                self.assert_invalid("prompt", prompt_text(first, blocks, line), "last line")

    def test_validate_prompt_applies_the_shared_id_form(self):
        _, blocks, _ = prompt_parts()
        accepted = "abc123"
        first = f"# {accepted}  paste into: jig-impl-{accepted} (implementer, ~/ws/src/jig-dev)"
        self.assert_valid("prompt", prompt_text(first, blocks, f"# end {accepted}"))
        for wrong in ("abcd123", "Demo", "de_mo", "de-mo"):
            with self.subTest(wrong):
                first = f"# {wrong}  paste into: jig-impl-{wrong} (implementer, ~/ws/src/jig-dev)"
                self.assert_invalid("prompt", prompt_text(first, blocks, f"# end {wrong}"),
                                    f"{wrong!r} is not a valid ID")

    def test_validate_prompt_accepts_a_project_with_a_hyphen(self):
        _, blocks, last = prompt_parts()
        first = "# demo  paste into: my-app-impl-demo (implementer, ~/ws/src/my-app-dev)"
        self.assert_valid("prompt", prompt_text(first, blocks, last))

    def test_validate_prompt_fails_with_a_line_after_the_last(self):
        self.assert_invalid("prompt", fixture("good-prompt.md") + "\n", "last line")
        self.assert_invalid("prompt", fixture("good-prompt.md") + "Variables:\n", "last line")

    def test_validate_prompt_accepts_no_final_newline(self):
        self.assert_valid("prompt", fixture("good-prompt.md").rstrip("\n"))

    def test_validate_prompt_fails_on_carriage_returns(self):
        self.assert_invalid("prompt", fixture("good-prompt.md").replace("\n", "\r\n"),
                            "carriage return")

    def test_validate_prompt_fails_on_an_empty_file(self):
        self.assert_invalid("prompt", "", "first line", "last line",
                            "section UNATTENDED is missing")

    def test_validate_prompt_template_has_the_sections_in_order(self):
        text = (TEMPLATES / "prompt.md").read_text(encoding="utf-8")
        labels = [next((name for name in SECTIONS if line.startswith(name + ":")), None)
                  for line in text.splitlines()]
        self.assertEqual(tuple(label for label in labels if label), SECTIONS)

    def test_validate_prompt_template_role_line_names_the_comment_standard(self):
        _, blocks, _ = prompt_parts_of((TEMPLATES / "prompt.md").read_text(encoding="utf-8"))
        role = " ".join(" ".join(blocks["ROLE"]).split())
        self.assertTrue(role.startswith(
            "ROLE: <role>. Rules: CLAUDE.md, the sections <names>; when the first line names "
            "the implementer account, also Comment standard. Spec tag: <the approved tag>. "), role)
        escalation = " ".join(" ".join(blocks["ESCALATION"]).split())
        self.assertTrue(escalation.endswith("for: <the escalation cases>."), escalation)

    def test_validate_prompt_refuses_an_implementer_prompt_without_the_comment_standard(self):
        first, blocks, last = prompt_parts()
        blocks["ROLE"] = ["ROLE: implementer. Rules: CLAUDE.md, the section Unit rules.",
                          *blocks["ROLE"][1:]]
        failures = self.assert_invalid("prompt", prompt_text(first, blocks, last),
                                       "line 7: a prompt for the implementer account must name "
                                       "the section Comment standard")
        self.assertEqual(len(failures), 1, failures)

    def test_validate_prompt_does_not_take_the_words_from_another_section(self):
        first, blocks, last = prompt_parts()
        blocks["ROLE"] = ["ROLE: implementer. Rules: CLAUDE.md, the section Unit rules.",
                          *blocks["ROLE"][1:]]
        blocks["STATE"] = ["STATE: the Comment standard section is in CLAUDE.md."]
        self.assert_invalid("prompt", prompt_text(first, blocks, last), "Comment standard")

    def test_validate_prompt_accepts_an_implementer_prompt_with_the_comment_standard(self):
        text = fixture("good-prompt.md")
        self.assertIn("(implementer, ", text.splitlines()[0])
        self.assertIn("Rules: CLAUDE.md, the section Comment standard.", text)
        self.assert_valid("prompt", text)

    def test_validate_prompt_accepts_the_comment_standard_wrapped_across_role_lines(self):
        first, blocks, last = prompt_parts()
        blocks["ROLE"] = ["ROLE: implementer. Rules: CLAUDE.md, the sections Unit rules and Comment",
                          "    standard. Spec tag: spec-p0.", *blocks["ROLE"][1:]]
        self.assert_valid("prompt", prompt_text(first, blocks, last))

    def test_validate_prompt_accepts_each_spec_tag_form_of_a_version_line(self):
        # A project that ships releases names spec-X.Y or spec-X.YrM where a project
        # that runs by phases names spec-pN; the prompt passes with either.
        text = fixture("good-prompt.md")
        self.assertIn("Spec tag: spec-p0.", text)
        for tag in ("spec-1.2", "spec-1.2r1", "spec-12.30r10"):
            with self.subTest(tag):
                self.assert_valid("prompt", text.replace("Spec tag: spec-p0.",
                                                         f"Spec tag: {tag}."))

    def test_validate_prompt_accepts_a_specwriter_prompt_without_the_comment_standard(self):
        first, blocks, last = prompt_parts()
        first = "# demo  paste into: jig-spec-demo (specwriter, ~/ws/src/jig-dev)"
        blocks["ROLE"] = ["ROLE: spec writer. Rules: CLAUDE.md, the section Unit rules.",
                          *blocks["ROLE"][1:]]
        self.assert_valid("prompt", prompt_text(first, blocks, last))

    def test_validate_prompt_accepts_the_template_once_its_names_are_filled(self):
        text = (TEMPLATES / "prompt.md").read_text(encoding="utf-8")
        self.assert_invalid("prompt", text, "'<ID>'")
        for name, value in (("<ID>", "demo"), ("<project>", "jig"), ("<role>", "impl"),
                            ("<account>", "implementer")):
            text = text.replace(name, value)
        self.assert_valid("prompt", text)


class ValidateReportTest(ValidateCase):
    def test_validate_report_accepts_the_good_fixture(self):
        self.assert_valid("report", fixture("good-report.md"))

    def test_validate_report_names_each_failure_of_the_bad_fixture_on_one_line(self):
        failures = self.assert_invalid("report", fixture("bad-report.md"))
        self.assertEqual(len(failures), 4, failures)
        self.assertIn("line 1:", failures[0])
        self.assertIn("line 4: class:", failures[1])
        self.assertIn("line 6: checks:", failures[2])
        self.assertIn("line 10:", failures[3])

    def test_validate_report_fails_on_each_wrong_header_line(self):
        self.assertEqual(sorted(WRONG_HEADERS), list(range(1, 11)))
        for number, wrong_lines in WRONG_HEADERS.items():
            for line in wrong_lines:
                with self.subTest(line=number, text=line):
                    failures = self.assert_invalid("report", report_with(number, line),
                                                   f"line {number}:")
                    self.assertEqual(len(failures), 1, failures)

    def test_validate_report_fails_with_each_header_line_missing(self):
        for number in range(1, 11):
            with self.subTest(number):
                lines = report_lines()
                del lines[number - 1]
                self.assert_invalid("report", "\n".join(lines) + "\n", f"line {number}:")

    def test_validate_report_fails_with_each_header_line_out_of_order(self):
        for number in range(1, 10):
            with self.subTest(number):
                lines = report_lines()
                lines[number - 1], lines[number] = lines[number], lines[number - 1]
                self.assert_invalid("report", "\n".join(lines) + "\n", f"line {number}:")

    def test_validate_report_fails_with_each_header_line_twice(self):
        for number in range(2, 10):
            with self.subTest(number):
                lines = report_lines()
                lines.insert(number, lines[number - 1])
                self.assert_invalid("report", "\n".join(lines) + "\n", f"line {number + 1}:")

    def test_validate_report_accepts_each_status(self):
        for line in ("OK", "OK: a note", "OK: note: with a colon", "NEEDS ERIC: a reason",
                     "NEEDS ERIC: model is Sonnet, not Opus", "NEEDS SPEC: IQ-7",
                     "NEEDS SPEC: IQ-120"):
            with self.subTest(line):
                self.assert_valid("report", report_with(1, line))

    def test_validate_report_accepts_each_class(self):
        for value in ("none", "patch", "minor", "major"):
            with self.subTest(value):
                self.assert_valid("report", report_with(4, f"class: {value}"))

    def test_validate_report_commits_and_checks_accept_none(self):
        self.assert_valid("report", report_with(5, "commits: none"))
        self.assert_valid("report", report_with(6, "checks: none"))

    def test_validate_report_accepts_the_context_placeholder(self):
        # A unit writes "context: none" and jig report fills it later.
        self.assert_valid("report", report_with(
            8, "start: 2026-11-06T22:51:35-08:00  end: 2026-11-06T23:06:49-08:00  context: none"))

    def test_validate_report_accepts_every_none_value_in_one_report(self):
        # A unit with no commit, no check run and a proof that did not apply, before jig report.
        lines = report_lines()
        lines[4] = "commits: none"
        lines[5] = "checks: none"
        lines[6] = "proofs: P1=0 P2=none"
        lines[7] = "start: 2026-11-06T22:51:35-08:00  end: 2026-11-06T23:06:49-08:00  context: none"
        self.assert_valid("report", "\n".join(lines) + "\n")

    def test_validate_report_accepts_hashes_of_seven_to_forty_digits(self):
        self.assert_valid("report", report_with(5, "commits: abc1234"))
        self.assert_valid("report", report_with(5, "commits: " + "a" * 40))
        self.assert_valid("report", report_with(5, "commits: abc1234 def5678 0123456"))

    def test_validate_report_accepts_a_failed_exit_code_as_form(self):
        self.assert_valid("report", report_with(6, "checks: make-check=2 mutants=255"))
        self.assert_valid("report", report_with(7, "proofs: P1=1 P2=none P10=255"))

    def test_validate_report_accepts_any_id_of_the_shared_form(self):
        self.assert_valid("report", report_with(3, "id: abc123"))

    def test_validate_report_accepts_a_utc_time_and_a_filled_context(self):
        self.assert_valid("report", report_with(
            8, "start: 2026-11-06T22:51:35Z  end: 2026-11-07T01:00:00+01:00  context: 142k"))

    def test_validate_report_counts_the_lines_after_the_separator(self):
        header = report_lines()[:10]
        for count, valid in ((0, True), (1, True), (25, True), (26, False), (40, False)):
            with self.subTest(count):
                text = "\n".join(header + ["body"] * count) + "\n"
                if valid:
                    self.assert_valid("report", text)
                else:
                    self.assert_invalid("report", text, f"{count} lines after")

    def test_validate_report_counts_a_blank_line_after_the_separator(self):
        header = report_lines()[:10]
        self.assert_invalid("report", "\n".join(header + [""] * 26) + "\n", "26 lines after")

    def test_validate_report_accepts_another_separator_in_the_body(self):
        lines = report_lines()
        self.assert_valid("report", "\n".join(lines + ["---", "more"]) + "\n")

    def test_validate_report_fails_on_carriage_returns(self):
        self.assert_invalid("report", fixture("good-report.md").replace("\n", "\r\n"),
                            "carriage return")
        self.assert_invalid("report", fixture("good-report.md") + "a line\r\n",
                            "carriage return")

    def test_validate_report_fails_on_an_empty_file(self):
        failures = self.assert_invalid("report", "", "line 1:", "line 9:", "line 10:")
        self.assertEqual(len(failures), 10, failures)

    def test_validate_report_template_has_the_header_lines_in_order(self):
        lines = (TEMPLATES / "report.md").read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].startswith("OK[: <note>] | NEEDS ERIC: <reason>"), lines[0])
        self.assertEqual(tuple(line.split(":", 1)[0] for line in lines[1:9]), HEADER_LABELS)
        self.assertEqual(lines[9], "---")

    def test_validate_report_good_fixture_follows_the_template(self):
        self.assertEqual(tuple(line.split(":", 1)[0] for line in report_lines()[1:9]),
                         HEADER_LABELS)


class ValidateCommandTest(ValidateCase):
    def test_validate_does_not_cross_the_kinds(self):
        self.assert_invalid("prompt", fixture("good-report.md"))
        self.assert_invalid("report", fixture("good-prompt.md"))

    def test_validate_names_a_missing_file(self):
        result = run([TOOL, "prompt", self.tmp / "absent.md"], env=self.env())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn("cannot read", result.stderr)

    def test_validate_names_a_directory_given_as_the_file(self):
        result = run([TOOL, "report", self.tmp], env=self.env())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn("cannot read", result.stderr)

    def test_validate_names_a_file_that_is_not_utf8(self):
        failures = self.assert_invalid("report", b"OK\n\xff\xfe\n", "not UTF-8")
        self.assertEqual(len(failures), 1, failures)

    def test_validate_keeps_a_control_character_on_one_line(self):
        failures = self.assert_invalid("report", report_with(4, "class: minor\x0bmajor"),
                                       "line 4:", "\\x0b")
        self.assertEqual(len(failures), 1, failures)

    def test_validate_usage_errors_exit_2(self):
        for arguments in ((), ("prompt",), ("other", "file"), ("prompt", "a", "b")):
            with self.subTest(arguments):
                self.assertEqual(run([TOOL, *arguments], env=self.env()).returncode, 2)

    def test_validate_runs_through_the_dispatcher(self):
        env = self.env(JIG_ROOT=str(self.tmp / "no-install"))
        for kind in ("prompt", "report"):
            with self.subTest(kind):
                good = run([JIG, "validate", kind, FIXTURES / f"good-{kind}.md"], env=env,
                           cwd=self.tmp)
                self.assertEqual(good.returncode, 0, good.stderr)
                bad = run([JIG, "validate", kind, FIXTURES / f"bad-{kind}.md"], env=env,
                          cwd=self.tmp)
                self.assertEqual(bad.returncode, 1, bad.stderr)


if __name__ == "__main__":
    unittest.main()
