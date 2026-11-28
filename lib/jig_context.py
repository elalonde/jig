"""Read how full a session's context is, from the file its status line writes.

The status line of a Claude Code session running in tmux writes one JSON object to
<directory>/<tmux session>.json about once a minute, with the session name, the
harness's input token count, the size of the model's context window and the time of
the write. jig only reads the file. The directory is /run/jig/ctx unless JIG_CTX_DIR
names another, which the tests use.

jig ctx prints the reading of a session and compares it with the limits. jig wait needs
the same reading for a unit in another account, so it sends this file to that account
over ssh and runs it there, with the tmux session as its one argument: run so, it
prints what jig ctx prints on its first line and exits 0 for a reading, 2 for none.
That is why the file uses only the standard library.
"""

import datetime
import json
import os
import pathlib
import subprocess
import sys
import time

DEFAULT_DIR = "/run/jig/ctx"

# The limits in tokens, for every session: at the soft one a session starts no new
# step, at the hard one it stops. JIG_CTX_SOFT and JIG_CTX_HARD set others.
SOFT_LIMIT = 200000
HARD_LIMIT = 230000

MAX_AGE = datetime.timedelta(minutes=3)
ONE_SECOND = datetime.timedelta(seconds=1)
TMUX_TIMEOUT = 10


class Context:
    """One reading of a session's context: tokens used, the window, and the reading's age.

    written is the time the file gives for the reading, in whole seconds as the status line
    writes it; modified is the file's modification time, which is finer. Both are aware
    datetimes, or None when the Context was not read from a file.
    """

    def __init__(self, tokens, window, age, written=None, modified=None):
        self.tokens = tokens
        self.window = window
        self.age = age
        self.written = written
        self.modified = modified

    def readout(self):
        """Return the form a report and jig ctx print, tokens over window: 84213/200000."""
        return f"{self.tokens}/{self.window}"


def context_dir():
    return pathlib.Path(os.environ.get("JIG_CTX_DIR") or DEFAULT_DIR)


def limits():
    """Return the soft and the hard limit, from the environment or the defaults.

    A value that is not a whole number raises ValueError with a message that names it,
    so that a typing error does not quietly fall back to the default.
    """
    found = []
    for name, default in (("JIG_CTX_SOFT", SOFT_LIMIT), ("JIG_CTX_HARD", HARD_LIMIT)):
        value = os.environ.get(name)
        if value and not value.isdigit():
            raise ValueError(f"{name} must be a whole number of tokens, not {value!r}")
        found.append(int(value) if value else default)
    return tuple(found)


def _count(value, least):
    # JSON true is an int in Python, and a count of True tokens means a broken writer.
    return isinstance(value, int) and not isinstance(value, bool) and value >= least


def read_context(session, directory=None, now=None):
    """Return the Context of a tmux session, or None when its file is missing or does not parse.

    The age is a timedelta from the file's "written" time to now. A file whose session key
    names another session does not parse, so a copied or misnamed file is not taken.
    """
    path = pathlib.Path(directory or context_dir()) / f"{session}.json"
    try:
        # The time of the change comes from the open file, not from the name, so that it
        # belongs to the text read even when the writer renames a new file into place.
        with open(path, encoding="utf-8") as stream:
            modified = os.fstat(stream.fileno()).st_mtime
            data = json.loads(stream.read())
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("session") != session:
        return None
    tokens, window = data.get("total_input_tokens"), data.get("context_window_size")
    if not _count(tokens, 0) or not _count(window, 1):
        return None
    try:
        written = datetime.datetime.fromisoformat(data.get("written"))
    except (TypeError, ValueError):
        return None
    # A time without an offset could be read in either of two zones; the writer always
    # gives one, so a file without it is not trusted.
    if written.tzinfo is None:
        return None
    now = now or datetime.datetime.now().astimezone()
    modified = datetime.datetime.fromtimestamp(modified).astimezone()
    return Context(tokens, window, now - written, written, modified)


def tmux(*args):
    """Return tmux's output, or None when tmux fails, is missing or does not answer."""
    try:
        result = subprocess.run(["tmux", *args], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=TMUX_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def process_started(pid):
    """Return when a process started, to one tick of the kernel's clock, or None when /proc
    cannot tell."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stream:
            # The command name in parentheses can hold blanks, so the fields after it
            # are counted from its closing parenthesis; the start time is the 20th.
            ticks = int(stream.read().rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None
    # /proc counts the start in clock ticks since boot. The boot time in /proc/stat is cut
    # to whole seconds; added to whole seconds of ticks, it can put the start up to two
    # seconds early, and the figure that the session before wrote just before its pane
    # was started again would then pass as the fresh session's own, so the fresh session
    # would stop at once. The boot moment comes from the two clocks instead, the boot clock
    # read first so that any delay between the reads makes the start later, not earlier.
    # The start is also rounded up to the next tick, so that it is never before the true
    # one; a figure written in that last tick reads as none until the next write.
    since_boot = time.clock_gettime(time.CLOCK_BOOTTIME)
    now = time.time()
    seconds = now - since_boot + (ticks + 1) / os.sysconf("SC_CLK_TCK")
    return datetime.datetime.fromtimestamp(seconds).astimezone()


def figure_time(context):
    """Return the moment the figure in the file was written, as finely as the file tells.

    The status line gives that moment in whole seconds, cut down, so the true moment lies
    in the second that starts at "written". It writes a new file and renames it into
    place, so the file's modification time is the same moment to a fraction of a second,
    and is taken when it falls in that second. A file copied or touched later keeps only
    "written", which is never after the true moment.
    """
    written, modified = context.written, context.modified
    if modified is not None and written <= modified < written + ONE_SECOND:
        return modified
    return written


def session_created(session):
    """Return when the running session started, or None when it does not run.

    That is the later of the tmux session's creation and the start of the process in its
    pane. A pane started again in the same session (tmux respawn-pane) runs a fresh
    Claude Code whose figure starts anew, so a reading from before it is not its own.
    """
    # The "=" asks for this exact name, so a session whose name only starts with it
    # is not taken instead.
    value = tmux("display-message", "-p", "-t", f"={session}:",
                 "#{session_created} #{pane_pid}")
    parts = (value or "").split()
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    created = datetime.datetime.fromtimestamp(int(parts[0])).astimezone()
    # A dead pane has no process; the creation of the session is then all there is.
    started = process_started(int(parts[1]))
    return max(created, started) if started else created


def reading(session, now=None):
    """Return the text to print for a session, the readout or none with its reason, and
    the Context, which is None when the text is none.

    A session that ends and is started again keeps its tmux name, so a reading written
    before the running session started belongs to the one before it, and is none.
    """
    now = now or datetime.datetime.now().astimezone()
    if not (context_dir() / f"{session}.json").exists():
        return "none (no file)", None
    context = read_context(session, now=now)
    if context is None:
        return "none (bad file)", None
    created = session_created(session)
    if created is not None and figure_time(context) < created:
        return "none (before session start)", None
    if context.age > MAX_AGE:
        return f"none ({int(context.age.total_seconds())} s old)", None
    return context.readout(), context


def main(argv):
    # The name becomes a file name in the context directory, so it may not climb out.
    if len(argv) != 1 or not argv[0] or "/" in argv[0] or argv[0] in (".", ".."):
        print("usage: python3 - <tmux session>", file=sys.stderr)
        return 2
    text, context = reading(argv[0])
    print(text)
    return 0 if context is not None else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
