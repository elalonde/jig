import http.server
import json
import socket
import threading
import time
import unittest
import urllib.parse

from tests.support import ROOT, TempTestCase, run

CI_GREEN = ROOT / "tools" / "ci-green"
TOKEN = "planted-token-value"
SUCCESS = {"name": "build", "conclusion": "success"}


def loopback_works():
    """Tell whether a connection to the loopback address gets through.

    The offline build of an unpacked source archive may run with no network at
    all, not even loopback, and these tests talk to a fake API server over it.
    Anywhere else loopback works and the tests run.
    """
    with socket.socket() as server:
        try:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            with socket.create_connection(server.getsockname(), timeout=5):
                return True
        except OSError:
            return False


class FakeApi(http.server.ThreadingHTTPServer):
    """Answer the two endpoints the tool reads, one page at a time."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FakeApiHandler)
        self.runs = []
        self.jobs = {}
        self.status = 200
        self.seen_auth = set()
        self.seen_shas = set()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class FakeApiHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        api = self.server
        api.seen_auth.add(self.headers.get("Authorization"))
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        size = int(query["per_page"][0])
        start = (int(query["page"][0]) - 1) * size
        parts = parsed.path.split("/")
        if parsed.path == "/repos/someowner/somerepo/actions/runs":
            api.seen_shas.update(query["head_sha"])
            body = {"workflow_runs": api.runs[start:start + size]}
        elif parts[:5] == ["", "repos", "someowner", "somerepo", "actions"] and parts[-1] == "jobs":
            body = {"jobs": api.jobs[int(parts[6])][start:start + size]}
        else:
            self.send_error(404)
            return
        if api.status != 200:
            self.send_error(api.status)
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@unittest.skipUnless(loopback_works(), "no loopback network for the fake API")
class CiGreenTest(TempTestCase):
    def setUp(self):
        super().setUp()
        self.clone = self.git_init(self.tmp / "clone")
        (self.clone / "file").write_text("content\n")
        self.git_commit_all(self.clone)
        run(["git", "-C", self.clone, "remote", "add", "origin",
             "deploy-alias:someowner/somerepo.git"], env=self.env())
        self.token = self.tmp / "token"
        self.token.write_text(f"{TOKEN}\n")

        self.api = FakeApi()
        thread = threading.Thread(target=self.api.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        self.addCleanup(self.api.server_close)
        self.addCleanup(self.api.shutdown)

    def tool(self, *extra, wait=None):
        options = [] if wait is None else ["--wait", str(wait)]
        return run([CI_GREEN, *options, self.clone, self.token, *extra],
                   env=self.env(JIG_GITHUB_API=self.api.url, JIG_CI_POLL="0.1"))

    def later(self, seconds, *jobs):
        """Replace the jobs of the one run after a delay, as CI does when a job ends."""
        timer = threading.Timer(seconds, lambda: self.api.jobs.update({11: list(jobs)}))
        timer.start()
        self.addCleanup(timer.cancel)

    def one_run(self, *jobs):
        self.api.runs = [{"id": 11, "status": "completed"}]
        self.api.jobs = {11: list(jobs)}

    def test_ci_green_exits_0_when_every_job_succeeded(self):
        self.one_run(SUCCESS, dict(SUCCESS, name="other"))
        result = self.tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ci_green_sends_the_token_from_the_file(self):
        self.one_run(SUCCESS)
        self.tool()
        self.assertEqual(self.api.seen_auth, {f"Bearer {TOKEN}"})

    def test_ci_green_asks_about_head_by_default(self):
        self.one_run(SUCCESS)
        self.tool()
        head = run(["git", "-C", self.clone, "rev-parse", "HEAD"], env=self.env()).stdout.strip()
        self.assertEqual(self.api.seen_shas, {head})

    def test_ci_green_exits_1_on_a_failed_job(self):
        self.one_run(SUCCESS, {"name": "bad", "conclusion": "failure"})
        self.assertEqual(self.tool().returncode, 1)

    def test_ci_green_exits_1_on_a_running_job(self):
        self.one_run(SUCCESS, {"name": "running", "conclusion": None})
        self.assertEqual(self.tool().returncode, 1)

    def test_ci_green_exits_1_on_a_skipped_job(self):
        self.one_run({"name": "skipped", "conclusion": "skipped"})
        self.assertEqual(self.tool().returncode, 1)

    def test_ci_green_exits_1_with_no_run(self):
        self.assertEqual(self.tool().returncode, 1)

    def test_ci_green_exits_1_on_a_run_with_no_job(self):
        self.one_run()
        self.assertEqual(self.tool().returncode, 1)

    def test_ci_green_reads_every_run(self):
        self.api.runs = [{"id": 11, "status": "completed"}, {"id": 12, "status": "completed"}]
        self.api.jobs = {11: [SUCCESS], 12: [{"name": "bad", "conclusion": "failure"}]}
        self.assertEqual(self.tool().returncode, 1)

    def test_ci_green_reads_later_pages(self):
        self.one_run(*([SUCCESS] * 100), {"name": "bad", "conclusion": "failure"})
        self.assertEqual(self.tool().returncode, 1)

    def test_ci_green_api_error_is_not_green_and_hides_the_token(self):
        self.one_run(SUCCESS)
        self.api.status = 403
        result = self.tool()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_ci_green_empty_token_file_fails_before_any_request(self):
        self.one_run(SUCCESS)
        self.token.write_text("")
        result = self.tool()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.api.seen_auth, set())

    def test_ci_green_unknown_commit_fails(self):
        self.one_run(SUCCESS)
        self.assertEqual(self.tool("no-such-commit").returncode, 2)

    def test_wait_ends_green_when_a_running_job_succeeds_later(self):
        # The job still runs at the first read and succeeds a second later, well
        # inside the 30 s allowed: the wait sees the success and exits 0.
        self.one_run(SUCCESS, {"name": "running", "conclusion": None})
        self.later(1, SUCCESS, dict(SUCCESS, name="running"))
        result = self.tool(wait=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wait_ends_at_once_on_a_completed_red_job(self):
        # One job has failed while another still runs: nothing can make the commit
        # green, so the tool exits 1 long before its 30 s are up.
        self.one_run({"name": "bad", "conclusion": "failure"},
                     {"name": "running", "conclusion": None})
        started = time.monotonic()
        result = self.tool(wait=30)
        self.assertEqual(result.returncode, 1)
        self.assertLess(time.monotonic() - started, 10)
        self.assertNotIn("not finished", result.stdout)

    def test_wait_goes_on_while_a_run_is_in_progress_with_only_green_jobs_listed(self):
        # The run is still in progress though its one listed job has succeeded, as when
        # a second job waits for the first and is not listed yet. A second later that
        # job appears, failed, and the run completes: the wait saw it, and exits 1.
        self.api.runs = [{"id": 11, "status": "in_progress"}]
        self.api.jobs = {11: [SUCCESS]}

        def complete():
            self.api.jobs[11] = [SUCCESS, {"name": "later", "conclusion": "failure"}]
            self.api.runs = [{"id": 11, "status": "completed"}]

        timer = threading.Timer(1, complete)
        timer.start()
        self.addCleanup(timer.cancel)
        result = self.tool(wait=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ci-green: later: failure", result.stdout)

    def test_wait_gives_up_with_ci_not_finished_when_a_job_outlasts_it(self):
        # The job never completes: after the one second allowed, the last line
        # says CI did not finish and the exit is 1.
        self.one_run({"name": "running", "conclusion": None})
        result = self.tool(wait=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("CI not finished", result.stdout.splitlines()[-1])

    def test_wait_keeps_reading_until_the_first_run_appears(self):
        # CI has not yet made a run for the commit when the wait starts; the run
        # appears and succeeds a second later, so the result is green.
        timer = threading.Timer(1, self.one_run, args=(SUCCESS,))
        timer.start()
        self.addCleanup(timer.cancel)
        result = self.tool(wait=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wait_without_a_number_of_seconds_is_a_usage_error(self):
        self.assertEqual(run([CI_GREEN, "--wait", "soon", self.clone, self.token],
                             env=self.env()).returncode, 2)


if __name__ == "__main__":
    unittest.main()
