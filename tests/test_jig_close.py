import importlib.machinery
import importlib.util
import json
import os
import sys
import unittest.mock

from tests.support import CO_AUTHOR_ADDRESS, ROOT, run
from tests.test_start import DIALOG, JIG, SESSION, StartCase

CLOSE_WORKER = ROOT / "libexec" / "jig-close-worker"
START_WORKER = ROOT / "libexec" / "jig-start-worker"


def load(path, name):
    sys.dont_write_bytecode = True
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(module)
    return module


def person(text):
    """A transcript entry for a message that a person typed."""
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_result(text):
    """A transcript entry that carries a tool's output back to the model."""
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": text}]}}


PROMPT_START = "# demo  paste into: jig-impl-demo (implementer, ~/ws/src/jig-dev)\nUNATTENDED"


class CloseCase(StartCase):
    """The fixtures of jig start, with helpers for reports, transcripts and the close."""

    def close(self, *extra, unit="demo"):
        return run([JIG, "close", "jig", unit, *extra], env=self.base_env())

    def started(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)

    def push_report(self, first_line="OK: done", unit="demo"):
        """Push the unit's report commit from another clone, as the worker does."""
        self.ok(["git", "-C", self.seed, "fetch", "-q", "origin"])
        self.ok(["git", "-C", self.seed, "reset", "-q", "--hard", "origin/master"])
        report = self.seed / "impl" / "log" / f"{unit}-report.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(f"{first_line}\njig-report: 1\nid: {unit}\n", encoding="utf-8")
        self.ok(["git", "-C", self.seed, "add", "-A"])
        self.ok(["git", "-C", self.seed, "commit", "-q", "--no-verify", "-m", f"log: {unit}"])
        self.ok(["git", "-C", self.seed, "push", "-q", "origin", "master"])
        return self.origin_head()

    def status_log(self):
        return self.ok(["git", "-C", self.origin, "show", "master:impl/log/status.log"])

    def alive(self, session):
        return self.tmux("has-session", "-t", f"={session}").returncode == 0

    def transcript(self, name, entries, folder="-home-implementer-ws-src-jig-dev"):
        path = self.worker_home / ".claude" / "projects" / folder / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
        return path

    def run_dir(self, number, unit="demo"):
        return self.worker_home / "ws" / "reviews" / "jig" / "transcripts" / unit / str(number)

    def run_files(self, number):
        return sorted(path.name for path in self.run_dir(number).iterdir())

    def ssh_calls(self):
        targets = self.worker_file("ssh-targets")
        return targets.read_text(encoding="utf-8").count("\n") if targets.exists() else 0

    def assert_nothing_closed(self, result, name, before):
        """A refusal names its reason and leaves the session, the lock and origin as they were."""
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"refused: {name}:", result.stderr)
        self.assertTrue(self.alive(SESSION))
        self.assertTrue(self.lock().exists())
        self.assertEqual(self.origin_head(), before)
        self.assertFalse(self.run_dir(1).exists())


class CloseReportTest(CloseCase):
    def test_close_after_a_report_ends_the_unit_and_writes_its_status(self):
        # A started unit has pushed its report; the close ends it and logs the report's status.
        self.started()
        logged = self.push_report("NEEDS ERIC: a reason")
        (self.orch_clone / "not-mine.txt").write_text("the orchestrator's\n", encoding="utf-8")
        result = self.close()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.alive(SESSION))
        self.assertFalse(self.lock().exists())
        self.assertFalse((self.lock_dir / "jig-implementer.expected").exists())
        self.assertRegex(self.status_log().splitlines()[-1],
                         rf"^\S+T\S+ demo end NEEDS ERIC {logged}$")
        self.assertEqual(self.origin_subjects()[0], "status: demo end")
        body = self.ok(["git", "-C", self.origin, "log", "-1", "--format=%b", "master"])
        self.assertEqual(body, f"Co-Authored-By: Claude <{CO_AUTHOR_ADDRESS}>")
        self.assertEqual(self.ok(["git", "-C", self.origin, "show", "--name-only", "--format=",
                                  "master"]), "impl/log/status.log")
        # The orchestrator's own uncommitted file stays where it was, uncommitted.
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "status", "--porcelain"]),
                         "?? not-mine.txt")

    def test_close_reads_the_status_word_of_a_report_without_a_note(self):
        self.started()
        logged = self.push_report("OK")
        self.assertEqual(self.close().returncode, 0)
        self.assertTrue(self.status_log().endswith(f" demo end OK {logged}"))

    def test_close_refuses_with_no_report_after_the_newest_start(self):
        # The unit runs and has not reported: without --dead nothing may change.
        self.started()
        before = self.origin_head()
        self.assert_nothing_closed(self.close(), "report", before)

    def test_close_refuses_a_restart_whose_earlier_run_has_a_report(self):
        # The first run reported and was stopped by hand with no end line; the restart runs on.
        self.started()
        self.push_report()
        self.tmux("kill-session", "-t", f"={SESSION}")
        self.lock().unlink()
        self.started()
        before = self.origin_head()
        self.assert_nothing_closed(self.close(), "report", before)

    def test_close_refuses_dead_when_the_run_has_a_report(self):
        self.started()
        before = self.push_report()
        self.assert_nothing_closed(self.close("--dead"), "report", before)

    def test_close_pushes_after_one_rebase_when_origin_moved(self):
        # Another commit reaches origin while the end line is pushed: one rebase, then the push.
        self.started()
        self.push_report()
        hook = self.orch_clone / ".git" / "hooks" / "pre-push"
        hook.write_text(f"""#!/bin/bash
[ -e "{self.tmp}/raced" ] && exit 0
touch "{self.tmp}/raced"
cd "{self.seed}" && unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE
echo raced > raced.md && git add raced.md && git commit -q --no-verify -m raced
git push -q origin master
""", encoding="utf-8")
        hook.chmod(0o755)
        result = self.close()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[:2], ["status: demo end", "raced"])

    def test_close_keeps_an_uncommitted_update_log_line_through_a_refused_push(self):
        # The supervisor's idle line waits uncommitted in the orchestrator's clone when
        # the end line's push loses a race; the one rebase carries the line along.
        line = self.track_update_log()
        self.started()
        self.push_report()
        self.race_next_push()
        result = self.close()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.origin_subjects()[:2], ["status: demo end", "raced"])
        self.assert_line_kept(line)

    def test_close_takes_back_its_end_commit_when_origin_refuses_it(self):
        self.started()
        before = self.push_report()
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        result = self.close()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("refused: status:", result.stderr)
        self.assertEqual(self.origin_head(), before)
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "rev-parse", "HEAD"]), before)
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "status", "--porcelain"]), "")

    def test_close_takes_back_its_end_commit_when_origin_cannot_be_reached(self):
        # Origin goes away as the end commit is pushed, so the fetch after the refused
        # push fails too. The close refuses with the fetch named, and the clone keeps
        # no commit that origin lacks, so the next close can run.
        self.started()
        before = self.push_report()
        self.lose_origin_at_next_push()
        result = self.close()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("refused: status: push refused, and the fetch after it failed", result.stderr)
        self.assertIn("jig close again writes the end line", result.stderr)
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "rev-parse", "HEAD"]), before)
        self.assertEqual(self.ok(["git", "-C", self.orch_clone, "status", "--porcelain"]), "")


class CloseDeadTest(CloseCase):
    def test_close_dead_ends_a_worker_without_a_report(self):
        # The worker stopped before its report: --dead logs "dead" with origin's head.
        self.started()
        result = self.close("--dead")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.alive(SESSION))
        self.assertFalse(self.lock().exists())
        start_commit = self.ok(["git", "-C", self.origin, "rev-parse", "master~1"])
        self.assertRegex(self.status_log().splitlines()[-1],
                         rf"^\S+T\S+ demo end dead {start_commit}$")

    def test_close_dead_ends_a_session_that_a_refused_start_left_at_the_dialog(self):
        # jig start refused at the trust dialog and kept the session and the lock for this.
        (self.worker_home / "claude-dialog").write_text(DIALOG, encoding="utf-8")
        self.assertEqual(self.start().returncode, 1)
        self.assertTrue(self.alive(SESSION))
        result = self.close("--dead")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.alive(SESSION))
        self.assertFalse(self.lock().exists())
        self.assertIn(" demo end dead ", self.status_log())
        self.assertFalse(self.worker_file("claude-answered").exists())

    def test_close_refuses_a_unit_that_never_started(self):
        before = self.origin_head()
        result = self.close("--dead")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("refused: start:", result.stderr)
        self.assertEqual(self.origin_head(), before)
        self.assertEqual(self.ssh_calls(), 0)


class CloseOnceTest(CloseCase):
    def test_close_of_a_closed_unit_does_nothing(self):
        # The last line of the unit is its end line: a second close reports that and stops.
        self.started()
        self.assertEqual(self.close("--dead").returncode, 0)
        before, calls = self.origin_head(), self.ssh_calls()
        result = self.close("--dead")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("closed already", result.stdout)
        self.assertEqual(self.origin_head(), before)
        self.assertEqual(self.status_log().count(" demo end "), 1)
        self.assertEqual(self.ssh_calls(), calls)
        self.assertFalse(self.run_dir(2).exists())

    def test_close_of_a_restarted_unit_copies_its_new_transcript_to_run_two(self):
        # Run 1 holds the first session's transcript; the restart's close adds run 2 only.
        first = self.transcript("first", [person(PROMPT_START)])
        self.started()
        self.assertEqual(self.close("--dead").returncode, 0)
        self.assertEqual(self.run_files(1), ["first.jsonl"])
        kept = (self.run_dir(1) / "first.jsonl").read_bytes()
        # The first transcript changes after the close, and a new one belongs to the restart.
        with open(first, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(person("later")) + "\n")
        self.transcript("second", [person(PROMPT_START)])
        self.started()
        result = self.close("--dead")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_files(1), ["first.jsonl"])
        self.assertEqual((self.run_dir(1) / "first.jsonl").read_bytes(), kept)
        self.assertEqual(self.run_files(2), ["second.jsonl"])
        self.assertEqual(self.status_log().count(" demo end dead "), 2)


class CloseWorkerSideTest(CloseCase):
    def test_close_leaves_a_lock_that_names_another_session(self):
        # The lock belongs to another unit of the account: it stays, and the close goes on.
        self.started()
        other = "session=jig-spec-other\nstart=2026-01-01T00:00:00+00:00\n"
        self.lock().write_text(other, encoding="utf-8")
        result = self.close("--dead")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("names jig-spec-other, not jig-impl-demo; it stays", result.stdout)
        self.assertEqual(self.lock().read_text(encoding="utf-8"), other)
        self.assertTrue((self.lock_dir / "jig-implementer.expected").exists())
        self.assertFalse(self.alive(SESSION))
        self.assertIn(" demo end dead ", self.status_log())

    def test_close_ends_only_the_units_own_session(self):
        # Two other sessions share the server, one with the unit's name as its prefix.
        self.started()
        for other in ("jig-impl-demo2", "jig-spec-demo"):
            created = self.tmux("new-session", "-d", "-s", other, "sleep 300")
            self.assertEqual(created.returncode, 0, created.stderr)
        self.assertEqual(self.close("--dead").returncode, 0)
        self.assertFalse(self.alive(SESSION))
        self.assertTrue(self.alive("jig-impl-demo2"))
        self.assertTrue(self.alive("jig-spec-demo"))

    def test_close_leaves_the_tee_as_it_is(self):
        self.started()
        tee = self.worker_home / "ws" / "reviews" / "jig" / "out" / "demo.txt"
        self.wait_for(tee, "double claude output")
        before = tee.read_bytes()
        self.assertEqual(self.close("--dead").returncode, 0)
        self.assertEqual(tee.read_bytes(), before)

    def test_close_finishes_when_no_transcript_exists(self):
        self.started()
        result = self.close("--dead")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no transcript of demo", result.stdout)
        self.assertEqual(self.run_files(1), [])
        self.assertIn(" demo end dead ", self.status_log())

    def test_close_copies_only_the_units_transcripts(self):
        # Meta entries come before the prompt; the unit's prompt can also be a text part.
        self.transcript("own", [
            {"type": "summary", "summary": "x"},
            {"type": "user", "isMeta": True, "message": {"content": "# demo as a caveat"}},
            person(PROMPT_START)])
        self.transcript("parts", [{"type": "user", "message": {"content": [
            {"type": "text", "text": PROMPT_START}]}}], folder="-home-implementer")
        # Another unit's newer session quotes this prompt in a tool result.
        other = self.transcript("other", [
            person("# other  paste into: jig-impl-other (implementer, ~/ws/src/jig-dev)"),
            tool_result(PROMPT_START)])
        os.utime(other, (2_000_000_000, 2_000_000_000))
        self.transcript("quoted", [person("look at this"), person(PROMPT_START)])
        self.transcript("result-first", [tool_result(PROMPT_START), person(PROMPT_START)])
        self.transcript("longer-id", [person("# demo2  paste into: jig-impl-demo2 (x)")])
        self.transcript("broken", [])
        (self.worker_home / ".claude" / "projects" / "-home-implementer-ws-src-jig-dev"
         / "broken.jsonl").write_text("{not json\n", encoding="utf-8")
        self.started()
        self.assertEqual(self.close("--dead").returncode, 0)
        self.assertEqual(self.run_files(1), ["own.jsonl", "parts.jsonl"])

    def test_close_takes_only_its_own_projects_transcript_of_a_shared_id(self):
        # Two projects in one worker account each ran a unit with the ID demo. The close
        # of each project copies only the transcript whose prompt names its own session.
        self.transcript("mine", [person(PROMPT_START)])
        self.transcript("beta", [person(
            "# demo  paste into: beta-impl-demo (implementer, ~/ws/src/beta-dev)\nUNATTENDED")],
            folder="-home-implementer-ws-src-beta-dev")
        self.started()
        self.assertEqual(self.close("--dead").returncode, 0)
        self.assertEqual(self.run_files(1), ["mine.jsonl"])
        # The worker part is the step that copies transcripts; beta's close runs it alone.
        closed = run(["python3", CLOSE_WORKER, "beta", "impl", "demo", "--dead"],
                     env=self.base_env(HOME=str(self.worker_home)))
        self.assertEqual(closed.returncode, 0, closed.stderr)
        beta_run = self.worker_home / "ws" / "reviews" / "beta" / "transcripts" / "demo" / "1"
        self.assertEqual(sorted(path.name for path in beta_run.iterdir()), ["beta.jsonl"])

    def test_close_refuses_when_the_worker_part_fails(self):
        self.started()
        before = self.origin_head()
        self.worker_file("ssh-fails").touch()
        result = self.close("--dead")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("refused: worker:", result.stderr)
        self.assertEqual(self.origin_head(), before)
        self.assertNotIn(" end ", self.status_log())


class CloseRefusalTest(CloseCase):
    def test_close_refuses_when_status_log_has_uncommitted_changes(self):
        self.started()
        before = self.origin_head()
        log = self.orch_clone / "impl" / "log" / "status.log"
        with open(log, "a", encoding="utf-8") as stream:
            stream.write("a line of the orchestrator's\n")
        self.assert_nothing_closed(self.close("--dead"), "status", before)
        self.assertIn("a line of the orchestrator's", log.read_text(encoding="utf-8"))

    def test_close_refuses_a_clone_with_unpushed_commits(self):
        self.started()
        before = self.origin_head()
        (self.orch_clone / "mine.md").write_text("mine\n", encoding="utf-8")
        self.git_commit_all(self.orch_clone)
        self.assert_nothing_closed(self.close("--dead"), "status", before)

    def test_close_refuses_another_branch_checked_out(self):
        self.started()
        before = self.origin_head()
        self.ok(["git", "-C", self.orch_clone, "checkout", "-q", "-b", "elsewhere"])
        self.assert_nothing_closed(self.close("--dead"), "status", before)

    def test_close_refuses_without_the_prompt(self):
        self.started()
        before = self.origin_head()
        self.prompt.unlink()
        self.assert_nothing_closed(self.close("--dead"), "prompt", before)

    def test_close_refuses_a_prompt_for_another_unit(self):
        self.started()
        before = self.origin_head()
        self.replace_in_prompt("paste into: jig-impl-demo", "paste into: jig-impl-other")
        self.assert_nothing_closed(self.close("--dead"), "prompt", before)

    def test_close_refuses_a_role_the_conf_gives_to_another_account(self):
        self.started()
        before = self.origin_head()
        self.write_conf(self.home, IMPL_ACCOUNT="someone")
        self.assert_nothing_closed(self.close("--dead"), "account", before)

    def test_close_rejects_a_bad_id_as_a_usage_error(self):
        for unit in ("Demo", "a/b", "abcdefg"):
            self.assertEqual(self.close(unit=unit).returncode, 2, unit)


class CloseWorkerAloneTest(CloseCase):
    def worker(self, *extra):
        return run([CLOSE_WORKER, "jig", "impl", "demo", "--account", "implementer", *extra],
                   env=self.base_env(HOME=str(self.worker_home)))

    def test_close_worker_alone_ends_a_session_and_frees_its_lock(self):
        self.assertEqual(self.tmux("new-session", "-d", "-s", SESSION, "sleep 300").returncode, 0)
        self.lock().write_text(f"session={SESSION}\nstart=x\n", encoding="utf-8")
        (self.lock_dir / "jig-implementer.expected").write_text("45\n", encoding="utf-8")
        result = self.worker("--dead")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.alive(SESSION))
        self.assertFalse(self.lock().exists())
        self.assertFalse((self.lock_dir / "jig-implementer.expected").exists())
        self.assertTrue(self.run_dir(1).is_dir())

    def test_close_worker_alone_finishes_with_no_session_and_no_lock(self):
        result = self.worker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no tmux session jig-impl-demo", result.stdout)
        self.assertIn("no lock", result.stdout)

    def test_close_worker_uses_only_the_standard_library(self):
        for line in CLOSE_WORKER.read_text(encoding="utf-8").splitlines():
            if line.startswith(("import ", "from ")):
                self.assertIn(line.split()[1].split(".")[0], sys.stdlib_module_names, line)

    def test_close_worker_agrees_with_the_start_worker(self):
        # Both parts must name the same lock and accept the same IDs and roles.
        close_part, start_part = load(CLOSE_WORKER, "close_part"), load(START_WORKER, "start_part")
        self.assertEqual(close_part.UNIT_ID.pattern, start_part.UNIT_ID.pattern)
        self.assertEqual(close_part.PROJECT.pattern, start_part.PROJECT.pattern)
        self.assertEqual(close_part.ROLE_KEYS, start_part.ROLE_KEYS)
        for lock_dir in ({}, {"JIG_LOCK_DIR": str(self.lock_dir)}):
            with unittest.mock.patch.dict(os.environ, lock_dir):
                if not lock_dir:
                    os.environ.pop("JIG_LOCK_DIR", None)
                self.assertEqual(close_part.lock_path("jig", "implementer"),
                                 start_part.lock_path("jig", "implementer"))
        self.assertEqual(close_part.expected_path(self.lock()),
                         start_part.expected_path(self.lock()))
