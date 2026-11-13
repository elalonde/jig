"""Read how full a session's context is, from the file its status line writes.

The status line of a Claude Code session running in tmux writes one JSON object to
<directory>/<tmux session>.json about once a minute, with the session name, the
harness's input token count, the size of the model's context window and the time of
the write. jig only reads the file. The directory is /run/jig/ctx unless JIG_CTX_DIR
names another, which the tests use.
"""

import datetime
import json
import os
import pathlib

DEFAULT_DIR = "/run/jig/ctx"


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
