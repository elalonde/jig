"""Push one fresh commit of a jig tool to origin, with the one retry that is allowed.

jig start, jig close and jig report each commit one file alone in the orchestrator's
clone of the -dev repo and push that commit at once. Someone else may have pushed in
between, so a refused push is followed by a fetch, one rebase of the commit onto
origin, and a second push. When any of these fails, the commit is taken back with
git reset --keep HEAD~1: a clone left with a commit that origin lacks makes every later
start, close and report refuse until a person repairs it. The caller words its own
refusal from the reason that push_commit returns.
"""

import subprocess


def _git(clone, *args):
    return subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True,
                          check=False)


def _last_line(result):
    lines = (result.stderr or result.stdout).strip().splitlines()
    return lines[-1] if lines else f"exit {result.returncode}"


def push_commit(clone, branch):
    """Push the branch with its new commit on top; return None, or why it failed.

    On a failure the commit is no longer in the clone when this returns.
    """
    if _git(clone, "push", "-q", "origin", branch).returncode == 0:
        return None
    # A push that fails because origin cannot be reached is the common case, and then
    # the fetch fails too; that must take the commit back like any other failure.
    fetched = _git(clone, "fetch", "-q", "origin")
    if fetched.returncode != 0:
        detail = f"push refused, and the fetch after it failed: {_last_line(fetched)}"
    else:
        # --autostash carries an uncommitted change through the rebase: the idle lines
        # that jig orch appends to impl/orch/updates.log wait there until the
        # orchestrator's next commit, and a plain rebase refuses to run beside a
        # changed file.
        rebased = _git(clone, "rebase", "-q", "--autostash", f"refs/remotes/origin/{branch}")
        if rebased.returncode != 0:
            _git(clone, "rebase", "--abort")
            detail = f"push refused, and its rebase failed: {_last_line(rebased)}"
        else:
            pushed = _git(clone, "push", "-q", "origin", branch)
            if pushed.returncode == 0:
                return None
            detail = f"push refused after a rebase: {_last_line(pushed)}"
    _git(clone, "reset", "-q", "--keep", "HEAD~1")
    return detail
