"""jig-statusline: the two lines, the host config, the usage file and the tmux parts.

The pure parts are tested in this process on the loaded module; the rest runs the
tool as Claude Code does, with the status JSON on stdin. Every run points the tool
at files in the test's own directory: JIG_STATUSLINE_CONF at a fixture config,
USAGE_FILE at a fixture usage file, and, inside a pretend tmux, TMUX_BIN at a fake
tmux and CTX_DIR at a fixture context directory. So no run reads host config or
touches the account's own files, window or context file.
"""

import ast
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import time
import unittest.mock

from tests.support import ROOT, TempTestCase, run

TOOL = ROOT / "libexec" / "jig-statusline"
CTX_TOOL = ROOT / "libexec" / "jig-ctx"
INSTALL = ROOT / "tools" / "install.sh"

_loader = importlib.machinery.SourceFileLoader("jig_statusline", str(TOOL))
_spec = importlib.util.spec_from_loader("jig_statusline", _loader)
statusline = importlib.util.module_from_spec(_spec)
_loader.exec_module(statusline)

# A zone given by its rules rather than by a zone file, so the tests read the same
# clock on a host without zone data, as the CI container is. It has a summer time,
# which changes the abbreviation.
ZONE = "PST8PDT,M3.2.0,M11.1.0"
DAY = 24 * 3600
NOW = 1_800_000_000
# Shows as 2h24m, with slack for the tests that run the tool on the real clock.
FIVE_LEFT = 2 * 3600 + 24 * 60 + 30
USER = pwd.getpwuid(os.getuid()).pw_name


def in_zone(test):
    """Run the rest of a test in ZONE and put the process's zone back afterwards."""
    patch = unittest.mock.patch.dict(os.environ, {"TZ": ZONE})
    patch.start()
    time.tzset()
    test.addCleanup(time.tzset)
    test.addCleanup(patch.stop)


def clock(ts):
    """The expected local reset time of a sample on the real clock."""
    return time.strftime("%I:%M%p %Z", time.localtime(ts + FIVE_LEFT))


def sample(five=23.5, week=41.2, week_left_days=3.5, rate_limits=True, now=NOW):
    data = {"model": {"id": "model-id", "display_name": "Opus"}, "session_id": "abc"}
    if rate_limits:
        data["rate_limits"] = {
            "five_hour": {"used_percentage": five, "resets_at": now + FIVE_LEFT},
            "seven_day": {"used_percentage": week, "resets_at": now + week_left_days * DAY},
        }
    return data


def no_session(data):
    """Return the input without a session_id, as some callers of the tool give it."""
    data.pop("session_id")
    return data


class PaceTest(unittest.TestCase):
    def test_elapsed_fraction(self):
        self.assertAlmostEqual(statusline.elapsed_fraction(NOW + 3.5 * DAY, NOW), 0.5)
        self.assertEqual(statusline.elapsed_fraction(NOW + 8 * DAY, NOW), 0.0)
        self.assertEqual(statusline.elapsed_fraction(NOW - 1, NOW), 1.0)

    def test_pace_thresholds(self):
        # Half the week gone: even from 45 to 55 percent, and beyond 5 points either way
        # ahead or behind.
        for used, want in [(44.9, "behind"), (45, "even"), (50, "even"), (55, "even"),
                           (55.1, "ahead")]:
            self.assertEqual(statusline.pace(used, 0.5), want, used)


class RenderTest(unittest.TestCase):
    def setUp(self):
        in_zone(self)

    def test_full_line_and_usage(self):
        line, usage = statusline.render(sample(), NOW)
        self.assertEqual(line, "5h 24% (2h24m · 02:24AM PST) · wk 41% · pace behind")
        self.assertEqual(usage["five_hour"]["used_percentage"], 23.5)
        self.assertEqual(usage["seven_day"]["used_percentage"], 41.2)
        self.assertEqual(usage["seven_day"]["resets_at"], NOW + 3.5 * DAY)
        self.assertEqual(usage["elapsed_fraction"], 0.5)
        self.assertEqual(usage["pace"], "behind")
        self.assertEqual(usage["ts"], "2027-01-15T08:00:00+00:00")

    def test_even_and_ahead(self):
        self.assertTrue(statusline.render(sample(week=52), NOW)[0].endswith("pace even"))
        self.assertTrue(statusline.render(sample(week=70), NOW)[0].endswith("pace ahead"))

    def test_time_left(self):
        for secs, want in [(FIVE_LEFT + 29, "2h24m"), (3600, "1h00m"), (3599, "59m"),
                           (7 * 60, "7m"), (59, "0m"), (0, "0m"), (-5, "0m")]:
            self.assertEqual(statusline.time_left(NOW + secs, NOW), want, secs)
        for bad in [None, "x"]:
            self.assertEqual(statusline.time_left(bad, NOW), "")

    def test_five_hour_reset_shown_or_left_out(self):
        data = sample()
        data["rate_limits"]["five_hour"]["resets_at"] = NOW + 7 * 60
        self.assertTrue(statusline.render(data, NOW)[0].startswith("5h 24% (7m · 12:07AM PST) · wk 41%"))
        del data["rate_limits"]["five_hour"]["resets_at"]
        self.assertEqual(statusline.render(data, NOW)[0], "5h 24% · wk 41% · pace behind")

    def test_reset_clock(self):
        # A 12-hour clock with a zero-padded hour and the zone; summer time changes
        # the zone's abbreviation.
        for ts, want in [(NOW + FIVE_LEFT, "02:24AM PST"), (NOW + 21 * 3600, "09:00PM PST"),
                         (NOW, "12:00AM PST"), (NOW + 12 * 3600, "12:00PM PST"),
                         (1_784_000_000, "08:33PM PDT")]:
            self.assertEqual(statusline.reset_clock(ts), want, ts)
        for bad in [None, "x"]:
            self.assertEqual(statusline.reset_clock(bad), "")

    def test_no_rate_limits(self):
        self.assertEqual(statusline.render(sample(rate_limits=False), NOW), ("", None))

    def test_only_five_hour(self):
        data = sample()
        del data["rate_limits"]["seven_day"]
        self.assertEqual(statusline.render(data, NOW), ("5h 24% (2h24m · 02:24AM PST)", None))

    def test_five_hour_missing_empty_or_without_percentage(self):
        for case in ("missing", {}, {"resets_at": NOW + 60}, {"used_percentage": None}, "x"):
            data = sample()
            if case == "missing":
                del data["rate_limits"]["five_hour"]
            else:
                data["rate_limits"]["five_hour"] = case
            line, usage = statusline.render(data, NOW)
            self.assertEqual(line, "5h 0% · wk 41% · pace behind", case)
            # A made-up 0 percent must not reach the usage file.
            self.assertIsNone(usage, case)

    def test_only_seven_day_still_shows_five_hour(self):
        data = sample()
        data["rate_limits"] = {"seven_day": data["rate_limits"]["seven_day"]}
        self.assertEqual(statusline.render(data, NOW), ("5h 0% · wk 41% · pace behind", None))


class MergeTest(unittest.TestCase):
    """Each window is merged with the one in the usage file by its reset time."""

    def setUp(self):
        in_zone(self)

    def previous(self, five=30.0, week=60.0, five_reset=NOW + FIVE_LEFT,
                 week_reset=NOW + 3.5 * DAY):
        return {"five_hour": {"used_percentage": five, "resets_at": five_reset},
                "seven_day": {"used_percentage": week, "resets_at": week_reset}}

    def test_stale_input_keeps_the_files_windows(self):
        # Another process reports last week's windows: both reset earlier than the
        # file's, so the file's values stay and the pace comes from them.
        prev = self.previous()
        stale = sample(five=90, week=95)
        stale["rate_limits"]["five_hour"]["resets_at"] = NOW - 3600
        stale["rate_limits"]["seven_day"]["resets_at"] = NOW - 3.5 * DAY
        line, usage = statusline.render(stale, NOW, prev)
        self.assertEqual(usage["five_hour"], prev["five_hour"])
        self.assertEqual(usage["seven_day"], prev["seven_day"])
        self.assertEqual(usage["pace"], "ahead")
        self.assertEqual(line, "5h 30% (2h24m · 02:24AM PST) · wk 60% · pace ahead")

    def test_equal_reset_keeps_the_larger_use_for_an_input_with_no_session(self):
        # With no session_id there is no time to compare, so the larger use wins either
        # way: the file's for the five-hour window, the input's for the weekly one.
        prev = self.previous(five=30.0, week=40.0)
        line, usage = statusline.render(no_session(sample(five=20, week=50)), NOW, prev)
        self.assertEqual(usage["five_hour"]["used_percentage"], 30.0)
        self.assertEqual(usage["seven_day"]["used_percentage"], 50)
        self.assertEqual(line, "5h 30% (2h24m · 02:24AM PST) · wk 50% · pace even")

    def test_later_reset_starts_a_new_window(self):
        # Both windows reset since the file was written, so the lower use is right now.
        prev = self.previous(five=80.0, week=90.0, five_reset=NOW - 60, week_reset=NOW - 60)
        _, usage = statusline.render(sample(five=5, week=1, week_left_days=6.5), NOW, prev)
        self.assertEqual(usage["five_hour"], {"used_percentage": 5, "resets_at": NOW + FIVE_LEFT})
        self.assertEqual(usage["seven_day"], {"used_percentage": 1, "resets_at": NOW + 6.5 * DAY})
        self.assertEqual(usage["elapsed_fraction"], round(0.5 / 7, 4))

    def test_windows_merge_separately(self):
        # The five-hour window rolled over; the weekly one is the same and the file's
        # use is larger, which an input with no session_id cannot lower.
        prev = self.previous(five=70.0, week=45.0, five_reset=NOW - 60)
        _, usage = statusline.render(no_session(sample(five=2, week=41.2)), NOW, prev)
        self.assertEqual(usage["five_hour"]["used_percentage"], 2)
        self.assertEqual(usage["seven_day"]["used_percentage"], 45.0)

    def test_unusable_previous_is_ignored(self):
        for prev in [None, {}, {"five_hour": "x", "seven_day": {"used_percentage": 99}}]:
            _, usage = statusline.render(sample(), NOW, prev)
            self.assertEqual(usage["five_hour"]["used_percentage"], 23.5, prev)
            self.assertEqual(usage["seven_day"]["used_percentage"], 41.2, prev)


# One reset time for each window through a whole sequence of inputs.
FIVE_RESET = NOW + FIVE_LEFT
WEEK_RESET = NOW + 3.5 * DAY


def session_input(use, session):
    """Return an input of the session with the same use in both windows."""
    return {"session_id": session, "rate_limits": {
        "five_hour": {"used_percentage": use, "resets_at": FIVE_RESET},
        "seven_day": {"used_percentage": use, "resets_at": WEEK_RESET}}}


class SessionMergeTest(unittest.TestCase):
    """Inputs of several sessions with the same reset times, merged by the time of each value.

    Each step renders one input at a fixture time on the file that the step before
    left, as the processes of the sessions do in turn.
    """

    def setUp(self):
        in_zone(self)
        self.usage = None

    def step(self, use, session, at):
        """Merge one input at NOW + at seconds; return the use that both windows show."""
        _, self.usage = statusline.render(session_input(use, session), NOW + at, self.usage)
        shown = (self.usage["five_hour"]["used_percentage"],
                 self.usage["seven_day"]["used_percentage"])
        self.assertEqual(shown[0], shown[1])
        return shown[0]

    def test_a_lower_value_more_than_a_refresh_later_shows_and_stale_values_do_not(self):
        self.assertEqual(self.step(96, "first", 0), 96)
        # A real reset of the use: a lower value 61 s after the 96 was set.
        self.assertEqual(self.step(22, "second", 61), 22)
        # The first session is idle and repeats its 96, which it gave before the 22.
        self.assertEqual(self.step(96, "first", 120), 22)
        # A new session with a higher value raises the shown one at once.
        self.assertEqual(self.step(30, "third", 125), 30)

    def test_a_repeated_lower_value_keeps_its_first_time_and_a_new_one_shows(self):
        self.assertEqual(self.step(96, "first", 0), 96)
        # Within 60 s the 22 is the lag between two sessions, not a reset.
        self.assertEqual(self.step(22, "second", 30), 96)
        # The same 22 again is still the value that came 30 s after the 96.
        self.assertEqual(self.step(22, "second", 90), 96)
        # The session's next new value is later than the 96 by more than 60 s.
        self.assertEqual(self.step(23, "second", 120), 23)

    def test_an_input_with_no_session_keeps_the_larger_value(self):
        self.step(96, "first", 0)
        self.assertEqual(self.step(22, "second", 61), 22)
        _, usage = statusline.render(no_session(session_input(96, "x")), NOW + 62, self.usage)
        self.assertEqual(usage["seven_day"]["used_percentage"], 96)
        _, usage = statusline.render(no_session(session_input(22, "x")), NOW + 200, usage)
        self.assertEqual(usage["seven_day"]["used_percentage"], 96)

    def test_a_file_with_no_times_lets_a_session_lower_the_value(self):
        # A file of an older version has the windows and no times, which read as 0.
        self.usage = {"five_hour": {"used_percentage": 96, "resets_at": FIVE_RESET},
                      "seven_day": {"used_percentage": 96, "resets_at": WEEK_RESET}}
        self.assertEqual(self.step(22, "second", 0), 22)

    def test_a_session_goes_once_each_of_its_windows_has_passed(self):
        self.usage = {"sessions": {
            "ended": {"five_hour": {"used_percentage": 5, "resets_at": NOW - 10, "at": 1},
                      "seven_day": {"used_percentage": 5, "resets_at": NOW - 10, "at": 1}},
            "weekly": {"five_hour": {"used_percentage": 5, "resets_at": NOW - 10, "at": 1},
                       "seven_day": {"used_percentage": 5, "resets_at": WEEK_RESET, "at": 1}},
            # A window with no reset time is not known to have passed.
            "unknown": {"five_hour": {"used_percentage": 5, "resets_at": None, "at": 1},
                        "seven_day": {"used_percentage": 5, "resets_at": NOW - 10, "at": 1}},
            "damaged": "x"}}
        self.step(40, "now", 0)
        self.assertEqual(sorted(self.usage["sessions"]), ["now", "unknown", "weekly"])

    def test_the_keys_that_other_tools_read_keep_their_form(self):
        self.step(96, "first", 0)
        self.step(22, "second", 61)
        self.assertLessEqual({"five_hour", "seven_day", "elapsed_fraction", "pace", "ts"},
                             set(self.usage))
        self.assertEqual(self.usage["five_hour"],
                         {"used_percentage": 22, "resets_at": FIVE_RESET})
        self.assertEqual(self.usage["seven_day"],
                         {"used_percentage": 22, "resets_at": WEEK_RESET})
        self.assertEqual(self.usage["pace"], "behind")


class HealthTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.tmp / "last"

    def write(self, *lines, age=0):
        self.path.write_text("first line\n" + "".join(line + "\n" for line in lines))
        os.utime(self.path, (NOW - age, NOW - age))

    def test_missing_file_gives_no_note(self):
        self.assertEqual(statusline.health(str(self.path), NOW), "")

    def test_all_pass_gives_no_note(self):
        self.write("PASS time ok", "WARN usage old")
        self.assertEqual(statusline.health(str(self.path), NOW), "")

    def test_fail_lines_are_counted(self):
        self.write("FAIL one down", "PASS time ok", "FAIL two down")
        self.assertEqual(statusline.health(str(self.path), NOW), "health: 2 FAIL")

    def test_stale_wins_over_fail_lines(self):
        self.write("PASS time ok", age=36 * 3600 + 1)
        self.assertEqual(statusline.health(str(self.path), NOW), "health: stale")
        self.write("FAIL one down", age=36 * 3600 + 1)
        self.assertEqual(statusline.health(str(self.path), NOW), "health: stale")

    def test_not_yet_stale(self):
        self.write("PASS time ok", age=36 * 3600 - 60)
        self.assertEqual(statusline.health(str(self.path), NOW), "")


class ModelPolicyTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        (self.repo / "src" / "deep").mkdir(parents=True)
        self.policy = self.tmp / "policy.toml"
        self.policy.write_text(f"""
[[rule]]
user = "someone-else"
cwd = "{self.repo}"
expect = ["Haiku"]

[[rule]]
user = "{USER}"
cwd = "{self.repo}"
phase = "2"
expect = ["Opus", "Fable"]

[[rule]]
user = "{USER}"
cwd = "{self.repo}"
expect = ["Sonnet"]
""")

    def check(self, model, cwd=None, path=None):
        data = {"model": {"display_name": model}, "cwd": str(cwd or self.repo / "src" / "deep")}
        return statusline.model_policy(data, str(path or self.policy), USER)[1]

    def test_matching_model_gives_no_warning(self):
        self.assertEqual(self.check("Sonnet 5.5"), "")

    def test_other_model_warns(self):
        self.assertEqual(self.check("Opus 5.5"), "model: Opus 5.5 ≠ Sonnet")

    def test_phase_selects_the_rule(self):
        (self.repo / "PHASE").write_text(" 2\n")
        self.assertEqual(self.check("Opus 5.5"), "")
        self.assertEqual(self.check("Sonnet 5.5"), "model: Sonnet 5.5 ≠ Opus")
        # The nearest PHASE file wins.
        (self.repo / "src" / "PHASE").write_text("3\n")
        self.assertEqual(self.check("Sonnet 5.5"), "")
        self.assertEqual(self.check("Opus 5.5"), "model: Opus 5.5 ≠ Sonnet")

    def test_no_matching_rule(self):
        self.assertEqual(self.check("Opus 5.5", cwd=self.tmp), "")
        # A directory whose name only starts with the rule's path is not under it.
        sibling = self.tmp / "repo-old"
        sibling.mkdir()
        self.assertEqual(self.check("Opus 5.5", cwd=sibling), "")

    def test_cwd_is_resolved(self):
        link = self.tmp / "link"
        link.symlink_to(self.repo)
        self.assertEqual(self.check("Opus 5.5", cwd=link), "model: Opus 5.5 ≠ Sonnet")

    def test_workspace_cwd_is_used_without_cwd(self):
        data = {"model": {"display_name": "Opus"}, "workspace": {"current_dir": str(self.repo)}}
        self.assertEqual(statusline.model_policy(data, str(self.policy), USER)[1],
                         "model: Opus ≠ Sonnet")

    def test_missing_file_gives_nothing(self):
        self.assertEqual(self.check("Opus 5.5", path=self.tmp / "missing.toml"), "")

    def test_invalid_file_gives_nothing(self):
        for text in ["[[rule]\nuser = ", "\xff\xfe", "rule = 3",
                     f'[[rule]]\nuser = "{USER}"\ncwd = "/"\nexpect = "Sonnet"',
                     f'[[rule]]\nuser = "{USER}"\ncwd = "/"\nexpect = []',
                     f'[[rule]]\nuser = "{USER}"\ncwd = "/"\nphase = 2\nexpect = ["Sonnet"]']:
            self.policy.write_bytes(text.encode("latin-1"))
            self.assertEqual(self.check("Opus 5.5"), "", text)


class StatuslineTestCase(TempTestCase):
    """Runs the tool on fixture files in the test's directory."""

    def setUp(self):
        super().setUp()
        # The tool runs in ZONE; so does this process, for the expected clock times.
        in_zone(self)
        self.now = time.time()
        self.dir = self.tmp / "work"
        self.dir.mkdir()
        self.usage = self.dir / "usage.json"
        # The health file and the policy are absent unless a test writes them; the
        # config names both.
        self.health = self.tmp / "health-last"
        self.policy = self.tmp / "policy.toml"
        self.conf = self.tmp / "statusline.conf"
        self.write_conf(f"HEALTH_FILE={self.health}", f"MODEL_POLICY={self.policy}")

    def write_conf(self, *lines):
        self.conf.write_text("".join(line + "\n" for line in lines))

    def tool_env(self, **extra):
        values = dict(TZ=ZONE, USAGE_FILE=str(self.usage), JIG_STATUSLINE_CONF=str(self.conf))
        values.update(extra)
        return self.env(**{key: value for key, value in values.items() if value is not None})

    def statusline(self, stdin, **extra):
        if not isinstance(stdin, str):
            stdin = json.dumps(stdin)
        result = run([TOOL], env=self.tool_env(**extra), stdin=stdin)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def line2(self, data, **extra):
        lines = self.statusline(data, **extra).splitlines()
        return lines[1] if len(lines) > 1 else None

    def write_policy(self, expect, role=None, cwd=None):
        role_line = f'role = "{role}"\n' if role else ""
        self.policy.write_text(f'[[rule]]\nuser = "{USER}"\ncwd = "{cwd or self.dir}"\n'
                               f'{role_line}expect = ["{expect}"]\n')

    def write_failing_health(self):
        self.health.write_text("first line\nFAIL one down\nPASS time ok\n")

    def git_repo(self, head):
        repo = self.dir / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text(head)
        (repo / "sub").mkdir()
        return repo


class LinesTest(StatuslineTestCase):
    def test_two_lines_from_a_full_input(self):
        out = self.statusline(sample(now=self.now))
        self.assertEqual(out, f"{USER} · no git · Opus\n"
                              f"5h 24% (2h24m · {clock(self.now)}) · wk 41% · pace behind\n")

    def test_effort_ends_line_one_when_present(self):
        for effort in ("high", {"level": "high"}):
            data = dict(sample(now=self.now), effort=effort)
            self.assertEqual(self.statusline(data).split("\n")[0], f"{USER} · no git · Opus (high)")
        for effort in (None, "", {}, 3):
            data = dict(sample(now=self.now), effort=effort)
            self.assertEqual(self.statusline(data).split("\n")[0], f"{USER} · no git · Opus")

    def test_second_line_left_out_when_empty(self):
        # The key set of a real refresh that carried no rate limits: nothing for line
        # two, so the tool prints one line and leaves the usage file as it was.
        data = {"context_window": {}, "cost": {}, "cwd": str(self.dir), "effort": {"level": "medium"},
                "exceeds_200k_tokens": False, "fast_mode": False,
                "model": {"id": "model-id", "display_name": "Sonnet"},
                "output_style": {"name": "default"}, "scratchpad_dir": str(self.dir),
                "session_id": "abc", "thinking": {"enabled": True},
                "transcript_path": str(self.dir / "t.jsonl"), "version": "2.0.0",
                "workspace": {"current_dir": str(self.dir)}}
        self.usage.write_text("previous")
        self.assertEqual(self.statusline(data), f"{USER} · no git · Sonnet (medium)\n")
        self.assertEqual(self.usage.read_text(), "previous")

    def test_run_time_left_out_when_missing_or_not_a_number(self):
        data = sample(now=self.now)
        for cost in ({}, {"total_duration_ms": None}, {"total_duration_ms": "60000"},
                     {"total_duration_ms": True}, {"total_duration_ms": -1}, None):
            data["cost"] = cost
            self.assertRegex(self.line2(data), r"^5h ")

    def test_run_time_values(self):
        data = sample(now=self.now)
        for ms, text in ((0, "run 0m"), (7 * 60000, "run 7m"), (59 * 60000 + 59999, "run 59m"),
                         ((60 + 18) * 60000, "run 1h18m"), (3600000, "run 1h00m")):
            data["cost"] = {"total_duration_ms": ms}
            self.assertRegex(self.line2(data), rf"^{text} · 5h 24% \(")

    def test_run_time_alone_without_rate_limits(self):
        data = dict(sample(rate_limits=False), cost={"total_duration_ms": 78 * 60000})
        self.assertEqual(self.line2(data), "run 1h18m")

    def test_long_second_line_is_cut_at_99_columns_at_its_end(self):
        # The longest realistic line two, over 99 columns uncut: the cut falls on the
        # model warning at the end, never on the use.
        self.health.write_text("")
        os.utime(self.health, (0, 0))
        self.write_policy("Sonnet 5.5")
        data = dict(sample(five=100, week=100, now=self.now), cwd=str(self.dir), effort="medium")
        data["rate_limits"]["five_hour"]["resets_at"] = self.now + 4 * 3600 + 59 * 60 + 30
        data["model"]["display_name"] = "Opus 5.5"
        line = self.line2(data)
        self.assertEqual(len(line), 99)
        self.assertTrue(line.startswith("5h 100% (4h59m · "))
        self.assertTrue(line.endswith(" · health: stale · model: Opus 5.5 (medium) ≠ …"))
        data["cost"] = {"total_duration_ms": 78 * 60000}
        line = self.line2(data)
        self.assertEqual(len(line), 99)
        self.assertTrue(line.startswith("run 1h18m · 5h 100% ("))

    def test_long_branch_is_shortened_so_the_model_stays(self):
        repo = self.git_repo("ref: refs/heads/" + "b" * 200 + "\n")
        out = self.statusline(dict(sample(now=self.now), cwd=str(repo)))
        for line in out.splitlines():
            self.assertLessEqual(len(line), 99)
        self.assertTrue(out.splitlines()[0].endswith("… · Opus"))

    def test_reset_time_follows_the_process_zone(self):
        data = sample(now=self.now)
        self.assertRegex(self.line2(data), r"^5h 24% \(2h24m · \d\d:\d\d[AP]M P[SD]T\) · ")
        self.assertRegex(self.line2(data, TZ="UTC"), r"^5h 24% \(2h24m · \d\d:\d\d[AP]M UTC\) · ")

    def test_bad_input_still_prints_and_exits_zero(self):
        for stdin in ("not json", "", "[1, 2]", "\"text\""):
            self.assertEqual(self.statusline(stdin), f"{USER} · no git · ?\n", stdin)

    def test_model_or_workspace_that_is_not_an_object_counts_as_absent(self):
        # A model or a workspace given as a string, as a later form of the input
        # might, must not cost the status line: line 1 shows "?" for the model name
        # and the run still exits 0.
        for data in ({"model": "Opus"}, {"model": "Opus", "workspace": "/tmp"},
                     {"model": ["Opus"], "workspace": 3}):
            self.assertEqual(self.statusline(data), f"{USER} · no git · ?\n", data)

    def test_reset_time_that_is_not_a_finite_clock_time_still_gives_both_lines(self):
        # A five-hour reset time that is infinite, not a number, or beyond the
        # clock's range is an unknown reset time: both lines still print, and the
        # five-hour part keeps its percentage without the time left and the clock.
        for resets_at in (float("inf"), float("-inf"), float("nan"), 10 ** 30):
            data = sample(now=self.now)
            data["rate_limits"]["five_hour"]["resets_at"] = resets_at
            self.assertEqual(self.statusline(data),
                             f"{USER} · no git · Opus\n5h 24% · wk 41% · pace behind\n",
                             resets_at)

    def test_branch_and_role_from_the_policy(self):
        repo = self.git_repo("ref: refs/heads/feature/x\n")
        self.write_policy("Opus", role="reviewer", cwd=repo)
        data = dict(sample(rate_limits=False), cwd=str(repo / "sub"))
        self.assertEqual(self.statusline(data), f"reviewer · {USER} · feature/x · Opus\n")

    def test_wrong_model_warns_on_line_two_and_detached_head_shows_the_commit(self):
        repo = self.git_repo("0123456789abcdef\n")
        self.write_policy("Sonnet", role="spec writer", cwd=repo)
        data = dict(sample(rate_limits=False), cwd=str(repo))
        self.assertEqual(self.statusline(data),
                         f"spec writer · {USER} · 0123456 · Opus\nmodel: Opus ≠ Sonnet\n")

    def test_rule_without_role_shows_no_role(self):
        repo = self.git_repo("ref: refs/heads/main\n")
        self.write_policy("Opus", cwd=repo)
        data = dict(sample(rate_limits=False), cwd=str(repo))
        self.assertEqual(self.statusline(data), f"{USER} · main · Opus\n")

    def test_health_note_ends_line_two(self):
        self.write_failing_health()
        self.assertEqual(self.line2(sample(now=self.now)),
                         f"5h 24% (2h24m · {clock(self.now)}) · wk 41% · pace behind · health: 1 FAIL")
        old = time.time() - 37 * 3600
        os.utime(self.health, (old, old))
        self.assertEqual(self.statusline("not json"), f"{USER} · no git · ?\nhealth: stale\n")

    def test_fast_enough_for_a_refresh(self):
        # Claude Code waits for the status line on each refresh, so a run with every
        # file present uses under a tenth of a second of CPU at its best of five. The
        # CPU time of the finished child and its own children is measured, not the
        # time on the clock, which a loaded host stretches.
        self.write_failing_health()
        self.write_policy("Sonnet")
        (self.dir / "PHASE").write_text("2\n")
        stdin = json.dumps(dict(sample(now=self.now), cwd=str(self.dir)))
        env = self.tool_env()
        best = None
        for _ in range(5):
            before = os.times()
            run([TOOL], env=env, stdin=stdin)
            after = os.times()
            took = (after.children_user - before.children_user
                    + after.children_system - before.children_system)
            best = took if best is None else min(best, took)
        self.assertLess(best, 0.1, f"{best * 1000:.0f} ms of CPU")


class HostConfigTest(StatuslineTestCase):
    """The two host paths come from the config file or the environment, each alone."""

    def setUp(self):
        super().setUp()
        self.write_failing_health()
        # A role that is no account's user name, so that line 1 shows whether the
        # role is there wherever the tests run.
        self.write_policy("Sonnet", role="reviewer")
        self.data = dict(sample(rate_limits=False), cwd=str(self.dir))

    def parts(self, **extra):
        """Return (role shown, health note shown, model warning shown) for one run."""
        out = self.statusline(self.data, **extra)
        return (out.startswith("reviewer · "), "health: 1 FAIL" in out,
                "model: Opus ≠ Sonnet" in out)

    def test_both_keys_in_the_file_give_every_part(self):
        self.assertEqual(self.statusline(self.data),
                         f"reviewer · {USER} · no git · Opus\n"
                         "health: 1 FAIL · model: Opus ≠ Sonnet\n")

    def test_missing_health_key_leaves_out_only_the_health_note(self):
        self.write_conf(f"MODEL_POLICY={self.policy}")
        self.assertEqual(self.parts(), (True, False, True))

    def test_empty_health_key_leaves_out_only_the_health_note(self):
        self.write_conf("HEALTH_FILE=", f"MODEL_POLICY={self.policy}")
        self.assertEqual(self.parts(), (True, False, True))

    def test_missing_policy_key_leaves_out_only_the_role_and_the_warning(self):
        self.write_conf(f"HEALTH_FILE={self.health}")
        self.assertEqual(self.parts(), (False, True, False))

    def test_empty_policy_key_leaves_out_only_the_role_and_the_warning(self):
        self.write_conf(f"HEALTH_FILE={self.health}", "MODEL_POLICY=")
        self.assertEqual(self.parts(), (False, True, False))

    def test_environment_sets_a_key_over_the_file(self):
        # The file names files that do not exist; each variable names the real one
        # and wins over the file, key by key.
        self.write_conf(f"HEALTH_FILE={self.tmp / 'no-health'}",
                        f"MODEL_POLICY={self.tmp / 'no-policy'}")
        self.assertEqual(self.parts(), (False, False, False))
        self.assertEqual(self.parts(HEALTH_FILE=str(self.health)), (False, True, False))
        self.assertEqual(self.parts(MODEL_POLICY=str(self.policy)), (True, False, True))

    def test_environment_gives_a_key_the_file_leaves_out(self):
        self.write_conf()
        self.assertEqual(self.parts(HEALTH_FILE=str(self.health),
                                    MODEL_POLICY=str(self.policy)), (True, True, True))

    def test_config_named_by_its_variable_is_read(self):
        # The default config is empty here; the variable names another file that
        # holds both keys.
        other = self.tmp / "other.conf"
        other.write_text(f"MODEL_POLICY = {self.policy}\nHEALTH_FILE={self.health}\n")
        self.write_conf()
        self.assertEqual(self.parts(), (False, False, False))
        self.assertEqual(self.parts(JIG_STATUSLINE_CONF=str(other)), (True, True, True))

    def test_missing_config_gives_no_value(self):
        self.conf.unlink()
        self.assertEqual(self.statusline(self.data), f"{USER} · no git · Opus\n")

    def test_unreadable_config_gives_no_value_and_exits_zero(self):
        # A directory in the place of the file cannot be read by any account, so the
        # test holds also where file modes do not stop the reader.
        self.conf.unlink()
        self.conf.mkdir()
        self.assertEqual(self.statusline(self.data), f"{USER} · no git · Opus\n")

    def test_bad_config_lines_are_ignored_and_never_fail(self):
        # Bytes that are not text give no value; lines without "=" and unknown keys
        # are skipped and the good lines still count.
        self.conf.write_bytes(b"\xff\xfe\x00HEALTH_FILE=x\n")
        self.assertEqual(self.statusline(self.data), f"{USER} · no git · Opus\n")
        self.write_conf("not a pair", "OTHER=1", f"HEALTH_FILE={self.health}")
        self.assertEqual(self.parts(), (False, True, False))

    def test_default_config_is_jigs_host_config(self):
        self.assertEqual(statusline.CONFIG_FILE, "/srv/jig/statusline.conf")


class UsageFileTest(StatuslineTestCase):
    def test_usage_file_written_with_both_windows_and_no_temporary_file(self):
        self.statusline(sample(now=self.now))
        data = json.loads(self.usage.read_text())
        self.assertEqual(data["five_hour"]["used_percentage"], 23.5)
        self.assertEqual(data["seven_day"]["used_percentage"], 41.2)
        self.assertEqual(data["pace"], "behind")
        # The times that the next run merges by are in the file too.
        self.assertEqual(data["sessions"]["abc"]["seven_day"]["used_percentage"], 41.2)
        self.assertEqual(set(data["shown_at"]), {"five_hour", "seven_day"})
        self.assertEqual(sorted(os.listdir(self.dir)), ["usage.json", "usage.json.lock"])

    def test_stale_writer_does_not_overwrite(self):
        # A second process passes last week's windows: the file and the line keep the
        # current week's.
        self.statusline(sample(five=30, week=60, now=self.now))
        stale = sample(five=90, week=95, now=self.now - 7 * DAY)
        self.assertEqual(self.line2(stale),
                         f"5h 30% (2h24m · {clock(self.now)}) · wk 60% · pace ahead")
        data = json.loads(self.usage.read_text())
        self.assertEqual(data["five_hour"]["used_percentage"], 30)
        self.assertEqual(data["seven_day"]["used_percentage"], 60)

    def test_missing_rate_limits_leave_the_file_alone(self):
        self.usage.write_text("previous")
        self.assertEqual(self.statusline(sample(rate_limits=False)), f"{USER} · no git · Opus\n")
        self.assertEqual(self.usage.read_text(), "previous")

    def test_held_lock_makes_the_writer_wait(self):
        # While another holder keeps the lock, the tool waits and writes nothing;
        # once the lock is free, it writes.
        lock = open(str(self.usage) + ".lock", "a")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX)
        proc = subprocess.Popen([str(TOOL)], env=self.tool_env(), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True)
        proc.stdin.write(json.dumps(sample(now=self.now)))
        proc.stdin.close()
        time.sleep(0.3)
        self.assertIsNone(proc.poll())
        self.assertFalse(self.usage.exists())
        fcntl.flock(lock, fcntl.LOCK_UN)
        self.assertEqual(proc.wait(timeout=10), 0)
        proc.stdout.close()
        self.assertTrue(self.usage.exists())

    def test_unwritable_usage_directory_still_prints(self):
        out = self.statusline(sample(now=self.now), USAGE_FILE=str(self.dir / "missing" / "usage.json"))
        self.assertEqual(out, f"{USER} · no git · Opus\n"
                              f"5h 24% (2h24m · {clock(self.now)}) · wk 41% · pace behind\n")

    def test_default_usage_file_is_in_the_accounts_claude_directory(self):
        (self.home / ".claude").mkdir()
        self.statusline(sample(now=self.now), USAGE_FILE=None)
        self.assertTrue((self.home / ".claude" / "usage.json").is_file())
        self.assertTrue((self.home / ".claude" / "usage.json.lock").is_file())


CTX_SESSION = "ctx-demo"
FIGURES = {"total_input_tokens": 84213, "context_window_size": 200000, "used_percentage": 42.1}
# A tmux limit short enough that a test with a stuck fake tmux takes a fraction of a
# second, and far enough below the real limit that a call still on the real limit
# shows in the time.
SHORT_LIMIT = 0.25


class TmuxTest(StatuslineTestCase):
    """Inside a pretend tmux: a fake tmux logs its calls and names one window and session."""

    def setUp(self):
        super().setUp()
        self.ctx = self.tmp / "ctx"
        self.ctx.mkdir()

    def fake_tmux(self, name):
        log = self.tmp / "tmux.log"
        log.unlink(missing_ok=True)
        fake = self.tmp / "tmux"
        fake.write_text(f"#!/bin/sh\necho \"$@\" >> '{log}'\n"
                        f"[ \"$1\" = display-message ] && echo '{name}'\nexit 0\n")
        fake.chmod(0o755)
        return fake, log

    def in_tmux(self, data, name="zsh"):
        tmux, log = self.fake_tmux(name)
        self.statusline(data, TMUX="/tmp/x,1,0", TMUX_PANE="%7", TMUX_BIN=str(tmux),
                        CTX_DIR=str(self.ctx))
        return log.read_text().splitlines()

    def test_window_renamed_to_role_and_branch_only_when_it_differs(self):
        repo = self.git_repo("ref: refs/heads/dev\n")
        self.write_policy("Opus", role="spec writer", cwd=repo)
        data = dict(sample(rate_limits=False), cwd=str(repo))
        self.assertEqual(self.in_tmux(data, "zsh")[:2],
                         ["display-message -p -t %7 #{window_name}", "rename-window -t %7 spec:dev"])
        self.assertNotIn("rename-window -t %7 spec:dev", self.in_tmux(data, "spec:dev"))

    def test_window_name_without_role_is_the_user(self):
        data = dict(sample(rate_limits=False), cwd=str(self.dir))
        self.assertIn(f"rename-window -t %7 {USER}:no git", self.in_tmux(data))

    def test_no_window_name_outside_tmux(self):
        tmux, log = self.fake_tmux("zsh")
        self.statusline(sample(rate_limits=False), TMUX_BIN=str(tmux), CTX_DIR=str(self.ctx))
        self.assertFalse(log.exists())

    def test_context_file_holds_the_figures_with_mode_664(self):
        data = dict(sample(rate_limits=False), cwd=str(self.dir), context_window=FIGURES)
        self.in_tmux(data, CTX_SESSION)
        path = self.ctx / f"{CTX_SESSION}.json"
        got = json.loads(path.read_text())
        self.assertEqual(got["session"], CTX_SESSION)
        self.assertEqual(got["total_input_tokens"], 84213)
        self.assertEqual(got["context_window_size"], 200000)
        self.assertEqual(got["used_percentage"], 42.1)
        self.assertRegex(got["written"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o664)
        self.assertEqual(sorted(os.listdir(self.ctx)), [f"{CTX_SESSION}.json"])

    def test_context_file_written_only_with_all_three_figures(self):
        # A refresh that lacks any one figure writes no file, and keeps the last one
        # as it was.
        path = self.ctx / f"{CTX_SESSION}.json"
        for key in FIGURES:
            for value in (None, "1", True):
                figures = dict(FIGURES, **{key: value})
                data = dict(sample(rate_limits=False), context_window=figures)
                self.in_tmux(data, CTX_SESSION)
                self.assertFalse(path.exists(), (key, value))
        self.in_tmux(dict(sample(rate_limits=False), context_window=FIGURES), CTX_SESSION)
        before = path.read_text()
        self.in_tmux(dict(sample(rate_limits=False), context_window={}), CTX_SESSION)
        self.assertEqual(path.read_text(), before)

    def test_jig_ctx_reads_the_file_the_status_line_wrote(self):
        # The status line writes into a directory that both tools are pointed at,
        # each by its own variable; jig ctx then reads the session's figures from it.
        self.in_tmux(dict(sample(rate_limits=False), context_window=FIGURES), CTX_SESSION)
        tmux_dir = self.tmp / "tmux-server"
        tmux_dir.mkdir()
        result = run([CTX_TOOL, CTX_SESSION],
                     env=self.env(JIG_CTX_DIR=str(self.ctx), TMUX_TMPDIR=str(tmux_dir)))
        self.assertEqual((result.returncode, result.stdout), (0, "84213/200000\n"), result.stderr)


class SessionNameTest(StatuslineTestCase):
    """Line 1 names the tmux session, else the policy role, and then the user."""

    def setUp(self):
        super().setUp()
        self.ctx = self.tmp / "ctx"
        self.ctx.mkdir()
        self.data = dict(sample(rate_limits=False), cwd=str(self.dir))

    fake_tmux = TmuxTest.fake_tmux

    def fake_tmux_that(self, action):
        """Write a fake tmux that logs each call and then runs the shell line action."""
        log = self.tmp / "tmux.log"
        log.unlink(missing_ok=True)
        fake = self.tmp / "tmux"
        fake.write_text(f"#!/bin/sh\necho \"$@\" >> '{log}'\n{action}\n")
        fake.chmod(0o755)
        return fake, log

    def first_line(self, tmux, data=None):
        out = self.statusline(data or self.data, TMUX="/tmp/x,1,0", TMUX_PANE="%7",
                              TMUX_BIN=str(tmux), CTX_DIR=str(self.ctx))
        return out.split("\n")[0]

    def name_calls(self, log):
        return [call for call in log.read_text().splitlines() if call.endswith("#S")]

    def test_session_name_comes_first_and_the_role_is_left_out(self):
        # In the pane of an orchestrator whose policy rule gives a role, line 1 starts
        # with the session name and the user; the role only names the window.
        self.write_policy("Opus", role="reviewer")
        tmux, log = self.fake_tmux("jig-orch")
        line = self.first_line(tmux)
        self.assertEqual(line, f"jig-orch · {USER} · no git · Opus")
        self.assertNotIn("reviewer", line)
        self.assertEqual(self.name_calls(log), ["display-message -p -t %7 #S"])
        self.assertIn("rename-window -t %7 rev:no git", log.read_text().splitlines())

    def test_a_units_session_name_names_line_one_and_the_context_file_from_one_call(self):
        # A unit's session, with the context figures in the input: the one call for the
        # name gives both line 1 and the name of the context file.
        session = "-".join(["demo", "impl", "u" + "4k"])
        self.write_policy("Opus", role="implementer")
        tmux, log = self.fake_tmux(session)
        line = self.first_line(tmux, dict(self.data, context_window=FIGURES))
        self.assertEqual(line, f"{session} · {USER} · no git · Opus")
        self.assertEqual(json.loads((self.ctx / f"{session}.json").read_text())["session"],
                         session)
        self.assertEqual(len(self.name_calls(log)), 1)

    def test_no_name_from_tmux_gives_the_role_then_the_user(self):
        # A call that fails even though it printed a name, one that prints nothing or
        # only white space, and a tmux command that does not exist each leave out the
        # session name; the tool still exits 0, which the run checks.
        self.write_policy("Opus", role="reviewer")
        for action in ("echo jig-orch; exit 1", "exit 0", "echo '  '"):
            tmux, _ = self.fake_tmux_that(action)
            self.assertEqual(self.first_line(tmux), f"reviewer · {USER} · no git · Opus", action)
        missing = self.tmp / "no-such-tmux"
        self.assertEqual(self.first_line(missing), f"reviewer · {USER} · no git · Opus")

    def test_no_name_and_no_role_starts_with_the_user(self):
        # No policy rule matches and tmux fails: line 1 starts with the user name.
        tmux, _ = self.fake_tmux_that("echo jig-orch; exit 1")
        self.assertEqual(self.first_line(tmux), f"{USER} · no git · Opus")

    def test_outside_tmux_the_role_comes_first_and_tmux_is_never_called(self):
        # With TMUX not set, a fake tmux that would name a session is never run, and
        # line 1 starts with the role and then the user.
        self.write_policy("Opus", role="reviewer")
        tmux, log = self.fake_tmux("jig-orch")
        out = self.statusline(self.data, TMUX_BIN=str(tmux), CTX_DIR=str(self.ctx))
        self.assertEqual(out.split("\n")[0], f"reviewer · {USER} · no git · Opus")
        self.assertFalse(log.exists())

    def test_a_stuck_tmux_holds_the_status_line_no_longer_than_its_limit_per_call(self):
        # Two tmux fakes that stop answering, run with the limit lowered in the loaded
        # tool: one never answers, so the window name and the session name each wait
        # out the limit; the other gives the window name and then hangs on the rename
        # and the session name. Each run makes two calls that wait out the limit, line
        # 1 falls back to the role, and the tool returns normally, so its script exits
        # 0. A call still on the real limit of 2 s would take far longer than the bound.
        self.write_policy("Opus", role="reviewer")
        stuck = "exec sleep 30"
        stuck_after_the_window_name = (
            f'case "$*" in *window_name*) echo zsh ;; *) {stuck} ;; esac')
        for action in (stuck, stuck_after_the_window_name):
            tmux, _ = self.fake_tmux_that(action)
            env = self.tool_env(TMUX="/tmp/x,1,0", TMUX_PANE="%7", TMUX_BIN=str(tmux),
                                CTX_DIR=str(self.ctx))
            out = io.StringIO()
            stdin = io.StringIO(json.dumps(self.data))
            with unittest.mock.patch.dict(os.environ, env, clear=True), \
                    unittest.mock.patch.object(statusline, "TMUX_LIMIT_S", SHORT_LIMIT), \
                    unittest.mock.patch.object(sys, "stdin", stdin), \
                    contextlib.redirect_stdout(out):
                start = time.monotonic()
                statusline.main()
                took = time.monotonic() - start
            self.assertEqual(out.getvalue().split("\n")[0],
                             f"reviewer · {USER} · no git · Opus", action)
            waits = 2
            self.assertLess(took, waits * SHORT_LIMIT + 1.0, f"{action}: {took:.2f} s")


class UsageTextTest(unittest.TestCase):
    """The usage text shows placeholders where a sample line would show values."""

    def usage_text(self):
        return ast.get_docstring(ast.parse(TOOL.read_text(encoding="utf-8")))

    def test_usage_text_shows_placeholders(self):
        text = self.usage_text()
        for placeholder in ("$username", "$role", "$branch", "$model", "$effort"):
            self.assertIn(placeholder, text)

    def test_usage_text_gives_line_one_with_the_name_first(self):
        # The sample of line 1 starts with the name and then the user, and the text
        # says where the name comes from.
        text = self.usage_text()
        self.assertIn("\n    $name · $username · $branch · $model ($effort)\n", text)
        self.assertIn("display-message -p '#S'", text)

    def test_usage_text_shows_no_real_or_invented_value(self):
        # No sample percentage, clock time or model family stands in for a
        # placeholder, and the only absolute paths are jig's own.
        # "5h 0%" is what the tool prints for a missing window, not a sample.
        text = self.usage_text().replace('"5h 0%"', "")
        self.assertNotRegex(text, r"\d%|\d\d:\d\d|\d+h\d\dm")
        for word in ("Opus", "Sonnet", "Haiku", "Fable"):
            self.assertNotIn(word, text)
        paths = set(re.findall(r"(?<![\w~.])/[\w./-]+", text))
        self.assertEqual(paths, {"/srv/jig/statusline.conf", "/run/jig/ctx/"})


class PackageTest(TempTestCase):
    def test_package_holds_the_tool_and_the_install_runs_it_from_current(self):
        # A product clone with the tool, packaged as make package does and installed
        # as a release is: the tool runs from current/libexec and prints its lines.
        source = self.git_init(self.tmp / "source")
        (source / "libexec").mkdir()
        shutil.copy(TOOL, source / "libexec")
        (source / "VERSION").write_text("0.5.0\n")
        self.git_commit_all(source)
        made = run(["python3", "-c",
                    "import pathlib, sys; sys.path.insert(0, sys.argv[1]); import jig_package; "
                    "print(jig_package.package(pathlib.Path(sys.argv[2])))",
                    ROOT / "lib", source], env=self.env())
        self.assertEqual(made.returncode, 0, made.stderr)
        root = self.tmp / "opt"
        result = run([INSTALL, source / "dist" / "jig-0.5.0.tar.gz", root, "0.5.0"], env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = root / "current" / "libexec" / "jig-statusline"
        self.assertTrue(installed.stat().st_mode & stat.S_IXOTH)
        result = run([installed], stdin="not json", env=self.env(
            USAGE_FILE=str(self.tmp / "usage.json"), JIG_STATUSLINE_CONF=str(self.tmp / "none")))
        self.assertEqual((result.returncode, result.stdout), (0, f"{USER} · no git · ?\n"))


if __name__ == "__main__":
    unittest.main()
