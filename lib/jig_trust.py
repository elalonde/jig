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
account whose file it is, so that the file keeps its owner. A start
sets all of its marks with one read and one rename, and reads the
status of the file again before the rename, in update; the same
step removes the entries of folders that a test run made.

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


def file_status(info):
    """Return what tells two versions of the file apart.

    Claude Code replaces the file or writes it in place, so a write by a
    session changes the inode, the size or the modification time.
    """
    return info.st_ino, info.st_size, info.st_mtime_ns


def status_of(path):
    """Return the status of the file at path, or None if missing."""
    try:
        return file_status(os.stat(path))
    except FileNotFoundError:
        return None


def load(path):
    """Return the state in the file and the status of the file it read.

    A missing file gives an empty state and the status None. The status
    comes from the open file, so that it belongs to the text that was
    read even when the file is replaced during the read.
    """
    try:
        with open(path, encoding="utf-8") as stream:
            status = file_status(os.fstat(stream.fileno()))
            text = stream.read()
    except FileNotFoundError:
        return {}, None
    except (OSError, UnicodeDecodeError) as error:
        raise Unusable(f"cannot read it: {error}") from None
    try:
        state = json.loads(text)
    except ValueError:
        raise Unusable("it is not JSON") from None
    if not isinstance(state, dict):
        raise Unusable("its top level is not an object")
    return state, status


def projects_of(state):
    """Return the object "projects" of the state, made when missing."""
    projects = state.setdefault(PROJECTS, {})
    if not isinstance(projects, dict):
        raise Unusable(f'its value of "{PROJECTS}" is not an object')
    return projects


def entry_of(state, folder):
    """Return the entry of a folder in the state, made when missing."""
    entry = projects_of(state).setdefault(folder, {})
    if not isinstance(entry, dict):
        raise Unusable(f"the entry of {folder} is not an object")
    return entry


def read_state(path, folder, key):
    """Return the state, the entry of the folder and whether it is marked.

    A missing file, key "projects" or entry comes back as an empty object
    that the caller fills in.
    """
    state = load(path)[0]
    entry = entry_of(state, folder)
    return state, entry, entry.get(key) is True


def make_new_file(path, state):
    """Write the state to a new file beside path; return its name.

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
            # The data must be on the disk before the rename. Otherwise a
            # power loss soon after it can leave the file empty or cut off
            # on a file system that writes the name first, and the file
            # holds the whole Claude Code state of the account.
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        discard(temporary)
        raise
    return temporary


def discard(temporary):
    try:
        os.unlink(temporary)
    except OSError:
        pass


def put_in_place(temporary, path):
    """Rename the new file over the old one; remove it when that fails."""
    try:
        os.replace(temporary, path)
    except BaseException:
        discard(temporary)
        raise


def write_state(path, state):
    """Put the state in place of the file at path by a rename."""
    put_in_place(make_new_file(path, state), path)


def update(path, change):
    """Change the file with one read and one rename; say if it wrote.

    change(state) changes the state in place and returns whether the
    file needs a write. Claude Code writes the whole file from its own
    read and takes no lock, so a write of a session between this read and
    the rename is lost. To keep that time short, the status of the file
    is read again just before the rename. When the file changed since
    the read, it is read, changed and made again, once; when it changed
    again by then, the new file goes into place all the same, and the
    read after the rename shows what was lost. So the second pass reads
    no status before its rename: it would rename in either case. That
    pass always ends in a return, so the loop never runs to its end.
    """
    for again in (False, True):
        state, status = load(path)
        if not change(state):
            return False
        temporary = make_new_file(path, state)
        if again or status_of(path) == status:
            put_in_place(temporary, path)
            return True
        discard(temporary)


def locate(home):
    """Return the file of an account as it is shown and as it is written.

    home is the home of the account, the home of this process when it is
    None. A link stays a link: the file it names is the one to replace.
    """
    home = os.path.expanduser("~") if home is None else str(home)
    shown = os.path.join(home, STATE_FILE)
    return shown, os.path.realpath(shown)


def mark(folder, account, home=None, key=TRUST_KEY):
    """Set the mark of a folder in the file of an account.

    Return the value and a problem. The value is TRUSTED when the
    step set the mark and read it back, KEPT when the mark was there, and
    NOT_MARKED otherwise; then the problem is one line that names the
    account and the file, and else it is None. The step changes nothing
    in a file that it cannot use. jig new and its block set one mark this
    way; a start sets all of its marks with mark_all.
    """
    folder = os.path.abspath(os.path.expanduser(folder))
    shown, path = locate(home)

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


def mark_all(marks, account, home=None):
    """Set the marks of several folders with one read and one rename.

    marks holds (folder, key) pairs. Return (folder, value, problem) for
    each different pair, in the order of their first place in marks, with
    the value and the problem of mark(). A file that the step cannot use,
    also for one entry, is left as it is. With every mark there, nothing
    is written.
    """
    # A folder can come twice: the -dev clone or a folder of the role's
    # list can also be a folder that the kickoff record adds, once as
    # "~/..." and once as an absolute path. A pair kept twice would find
    # its own new mark at its second place and count it as there, so
    # nothing would be written and the session would stop at the trust
    # dialog.
    unique = []
    for folder, key in marks:
        pair = (os.path.abspath(os.path.expanduser(folder)), key)
        if pair not in unique:
            unique.append(pair)
    marks = unique
    shown, path = locate(home)
    found = set()

    def change(state):
        found.clear()
        bad = None
        for folder, key in marks:
            try:
                entry = entry_of(state, folder)
            except Unusable as error:
                bad = bad or error
                continue
            if entry.get(key) is True:
                found.add((folder, key))
            else:
                entry[key] = True
        if bad:
            raise bad
        return any(pair not in found for pair in marks)

    reason, after = None, set()
    try:
        if update(path, change):
            state = load(path)[0]
            after = {(folder, key) for folder, key in marks
                     if entry_of(state, folder).get(key) is True}
    except Unusable as error:
        reason = str(error)
    except OSError as error:
        reason = f"cannot write it: {error}"
    results = []
    for folder, key in marks:
        if (folder, key) in found:
            results.append((folder, KEPT, None))
        elif not reason and (folder, key) in after:
            results.append((folder, TRUSTED, None))
        else:
            why = reason or \
                "the mark is not there when the file is read again"
            results.append((folder, NOT_MARKED,
                            f"jig: cannot set the trust mark of {folder} "
                            f"in {shown} of {account}: {why}"))
    return results


def remove_entries(select, account, home=None):
    """Remove the entries of "projects" whose names select accepts.

    The same one read and one rename as for the marks. Return the names
    that were removed and a problem: None, or one line that names the
    account and the file. A file that the step cannot use is left as it
    is.
    """
    shown, path = locate(home)
    removed = []

    def change(state):
        projects = projects_of(state)
        removed[:] = [name for name in projects if select(name)]
        for name in removed:
            del projects[name]
        return bool(removed)

    def problem(reason):
        return (f"jig: cannot remove the entries of folders from {shown} "
                f"of {account}: {reason}")

    try:
        if update(path, change):
            left = [name for name in projects_of(load(path)[0])
                    if select(name)]
            if left:
                return removed, problem(
                    f"{len(left)} of them are there at the read after "
                    "the rename")
    except Unusable as error:
        return [], problem(error)
    except OSError as error:
        return [], problem(f"cannot write it: {error}")
    return removed, None


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
