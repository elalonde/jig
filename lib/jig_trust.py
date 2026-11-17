"""Mark a folder as trusted in the Claude Code state of one account.

Claude Code asks whether a folder can be trusted the first time a session
works in it, and waits at that dialog until a person answers. A session
that jig starts has nobody to answer, so jig sets the answer first. Claude
Code keeps it in ~/.claude.json of the account: in the object "projects",
the entry named by the absolute path of the folder holds the key
hasTrustDialogAccepted with the value true.

The file belongs to Claude Code and holds much else, so the step changes
only that key. It writes a new file beside the old one and renames it into
place, so that no reader ever sees half a file, and reads it again
after the rename: Claude Code takes no lock on the file, and a session
that saves an older copy can drop the mark again. The step runs as the
account whose file it is, so that the file keeps its owner.

jig start and the supervisor run this module in the account itself: jig
start sends its text to a worker that imports nothing from jig's tree, and
jig new puts the same text into the block that the owner runs, which sends
it to each account by ssh. So it uses the standard library only, and no
line is longer than a line of that block may be.
"""

import json
import os
import sys
import tempfile

STATE_FILE = ".claude.json"
PROJECTS = "projects"
# The key that ends the dialog for the folder a session starts in.
TRUST_KEY = "hasTrustDialogAccepted"
# The key for a folder that a session gets with --add-dir. Claude Code
# 2.1.326 asks about such a folder in a dialog of its own. It reads this
# same key in the entry of the added folder itself and then skips that
# dialog, but it never writes the key: the answer in the dialog is kept
# in no file. That was measured with real sessions of that version, so a
# later version that reads another key needs a change here only.
ADDED_FOLDER_KEY = TRUST_KEY

TRUSTED = "trusted"
KEPT = "kept"
NOT_MARKED = "not marked"


class Unusable(Exception):
    """The file cannot take the mark; the message says why."""


def read_state(path, folder, key):
    """Return the state, the entry of the folder and whether it is marked.

    A missing file, key "projects" or entry comes back as an empty object
    that the caller fills in.
    """
    try:
        with open(path, encoding="utf-8") as stream:
            text = stream.read()
    except FileNotFoundError:
        text = None
    except (OSError, UnicodeDecodeError) as error:
        raise Unusable(f"cannot read it: {error}") from None
    if text is None:
        state = {}
    else:
        try:
            state = json.loads(text)
        except ValueError:
            raise Unusable("it is not JSON") from None
    if not isinstance(state, dict):
        raise Unusable("its top level is not an object")
    projects = state.setdefault(PROJECTS, {})
    if not isinstance(projects, dict):
        raise Unusable(f'its value of "{PROJECTS}" is not an object')
    entry = projects.setdefault(folder, {})
    if not isinstance(entry, dict):
        raise Unusable(f"the entry of {folder} is not an object")
    return state, entry, entry.get(key) is True


def write_state(path, state):
    """Put the state in place of the file at path by a rename.

    The new file has the mode of the old one, or 600 for a file that did
    not exist, since the file holds the account's own settings. A file
    that belongs to another account is not replaced: the rename would
    hand it to this one.
    """
    directory = os.path.dirname(path)
    try:
        old = os.stat(path)
    except FileNotFoundError:
        old = None
        owner = os.stat(directory).st_uid
    else:
        owner = old.st_uid
    if owner != os.getuid():
        raise Unusable("it belongs to another account")
    mode = 0o600 if old is None else old.st_mode & 0o7777
    descriptor, temporary = tempfile.mkstemp(
        prefix=STATE_FILE + ".", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(state, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def mark(folder, account, home=None, key=TRUST_KEY):
    """Set the mark of a folder in the file of an account.

    Return the value and a problem. The value is TRUSTED when the
    step set the mark and read it back, KEPT when the mark was there, and
    NOT_MARKED otherwise; then the problem is one line that names the
    account and the file, and else it is None. The step changes nothing
    in a file that it cannot use. home is the home of the account, the
    home of this process when it is None.
    """
    home = os.path.expanduser("~") if home is None else str(home)
    folder = os.path.abspath(os.path.expanduser(folder))
    shown = os.path.join(home, STATE_FILE)
    # A link stays a link: the file it names is the one to replace.
    path = os.path.realpath(shown)

    def problem(reason):
        return (f"jig: cannot set the trust mark of {folder} in {shown} "
                f"of {account}: {reason}")

    try:
        state, entry, marked = read_state(path, folder, key)
        if marked:
            return KEPT, None
        entry[key] = True
        write_state(path, state)
    except Unusable as error:
        return NOT_MARKED, problem(error)
    except OSError as error:
        return NOT_MARKED, problem(f"cannot write it: {error}")
    try:
        marked = read_state(path, folder, key)[2]
    except Unusable:
        marked = False
    if not marked:
        return NOT_MARKED, problem(
            "the mark is not there when the file is read again")
    return TRUSTED, None


def main(argv):
    """Mark one folder in this account and print the result.

    usage: python3 - ACCOUNT FOLDER

    The block of jig new runs this in each account. It prints the problem,
    if any, and then "trust ACCOUNT: VALUE"; it exits 0 whatever the
    value, so that the block goes on.
    """
    if len(argv) != 2:
        print("usage: python3 - ACCOUNT FOLDER", file=sys.stderr)
        return 2
    account, folder = argv
    value, problem = mark(folder, account)
    if problem:
        print(problem, flush=True)
    print(f"trust {account}: {value}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
