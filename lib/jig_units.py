"""What jig status, jig wait and jig cat share: the running units and the way to their accounts.

They run in the orchestrator's account. A running unit is a lock that jig start took in
the lock directory: the lock names the session and its start, and the file beside it
holds the expected minutes. The worker accounts are reached only by ssh, through
JIG_SSH, as jig start reaches them.
"""

import datetime
import importlib.machinery
import importlib.util
import os
import pathlib
import re
import shlex
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKER_SCRIPT = ROOT / "libexec" / "jig-start-worker"

# The worker script is the one place for the conf file and the lock, as in jig start.
sys.dont_write_bytecode = True
_loader = importlib.machinery.SourceFileLoader("jig_start_worker", str(WORKER_SCRIPT))
_spec = importlib.util.spec_from_loader("jig_start_worker", _loader)
worker = importlib.util.module_from_spec(_spec)
_loader.exec_module(worker)

Refused = worker.Refused
refuse = worker.refuse
read_conf = worker.read_conf
PROJECT = worker.PROJECT
UNIT_ID = worker.UNIT_ID

# The account whose screen and some files can show reference text, as it alone reads
# the reference. A conf file may name another spec account; this one is refused in
# every project all the same.
SPEC_ACCOUNT = "specwriter"

SSH_TIMEOUT_SECONDS = 60


class Unit:
    """One running unit, as its lock describes it."""

    def __init__(self, project, account, session, role, unit, start, expected):
        self.project = project
        self.account = account
        self.session = session
        self.role = role
        self.unit = unit
        self.start = start
        self.expected = expected


def lock_dir():
    """Return the directory of every lock: the parent of any lock path."""
    return worker.lock_path("project", "account").parent


def accounts(conf):
    """Return every account the conf file names for a role."""
    named = []
    for key in worker.ROLE_KEYS.values():
        named.extend(conf.get(key, "").split())
    return list(dict.fromkeys(named))


def is_spec_account(conf, account):
    return account == SPEC_ACCOUNT or account in worker.role_accounts(conf, "spec")


def _read_lock(path):
    values = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _read_expected(lock):
    try:
        text = worker.expected_path(lock).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() and int(text) > 0 else None


def _parse_start(text):
    try:
        start = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return start if start.tzinfo else start.astimezone()


def running_units(project):
    """Return the units the locks of a project describe, in the order of their names."""
    session_form = re.compile(rf"{re.escape(project)}-({'|'.join(worker.ROLE_KEYS)})-"
                              rf"({UNIT_ID.pattern})")
    units = []
    directory = lock_dir()
    if not directory.is_dir():
        return units
    for lock in sorted(directory.glob(f"{project}-*")):
        if lock.name.endswith(".expected") or not lock.is_file():
            continue
        values = _read_lock(lock)
        matched = session_form.fullmatch(values.get("session", ""))
        if not matched:
            continue
        account = lock.name[len(project) + 1:]
        units.append(Unit(project, account, values["session"], matched[1], matched[2],
                          _parse_start(values.get("start")), _read_expected(lock)))
    return units


def remote_command(account, words):
    ssh = shlex.split(os.environ.get("JIG_SSH", "ssh -o BatchMode=yes"))
    return [*ssh, f"{account}@localhost", shlex.join(words)]


def run_remote(account, words):
    """Run a command in a worker account; return its exit code and output.

    ssh gives 255 when it cannot reach the account, and so does this on a timeout.
    """
    try:
        result = subprocess.run(remote_command(account, words), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, errors="replace",
                                timeout=SSH_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return 255, "", "timed out"
    except OSError as error:
        return 255, "", str(error)
    return result.returncode, result.stdout, result.stderr


def session_state(account, session):
    """Return live, ended or unreachable for a tmux session in a worker account."""
    code, _, _ = run_remote(account, ["tmux", "has-session", "-t", f"={session}"])
    if code == 0:
        return "live"
    return "unreachable" if code == 255 else "ended"
