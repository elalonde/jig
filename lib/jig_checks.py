"""Read a row of checks.tsv, and hold a heavy slot of the host while a heavy row runs.

checks.tsv sits at the root of a project's -dev clone and names every check of the
project, one row per line after the line of column names. A row has 4 or 5 fields,
separated by tabs: name, command, account, time cap in seconds, and an optional
weight, heavy or light. A row with 4 fields, or with an empty fifth field, is light.

Several projects share one host, and a heavy row, such as a full build with every
test, uses all of its cores. When too many run at once, each runs slower and can pass
its time cap with correct code. So the host config /srv/jig/host.conf may set
HEAVY_SLOTS, the number of heavy rows that may run at once on the host; JIG_SRV
replaces /srv/jig in tests. With no such key there is no limit.

A slot is a file <dir>/<k> for k from 1 to HEAVY_SLOTS, where <dir> is /run/jig/heavy,
or JIG_HEAVY_DIR in tests. A run holds a slot by an exclusive flock on that file, and
the kernel frees the lock when the process ends, however it ends, so a slot is never
left held by a process that is gone. The holder writes one line into the file once it
has the lock, so that a waiter can say whom it waits for. The text of a free slot is
that of its last holder and means nothing.

A waiter tries every slot again every few seconds (5, or JIG_HEAVY_POLL in seconds in
tests). The polls keep no order: a run that comes later can take a freed slot before
one that has waited longer. With a few projects on a host this is rare, and a queue
would cost more than it saves.

A process that already holds a slot passes JIG_HEAVY_SLOT to the command it runs. A
heavy row inside that command then runs at once, because its process tree already
holds a slot, and waiting for it would wait for ever.

jig's pre-commit hook keeps its own copy of parse_row and of the slot code, because it
cannot import from jig's tree; tests/test_jig_checks.py runs the same rows on both.
"""

import datetime
import errno
import fcntl
import os
import pathlib
import pwd
import re
import time

HEAVY = "heavy"
LIGHT = "light"
SRV_ROOT = "/srv/jig"
HEAVY_DIR = "/run/jig/heavy"
POLL_SECONDS = 5.0
SLOTS_KEY = "HEAVY_SLOTS"
# Set in the environment of a command that runs while its process tree holds a slot.
HELD = "JIG_HEAVY_SLOT"
# A slot file of another account that this account cannot open is tried this many
# more times before the run gives up: just after its creator made it, the umask may
# have kept its group from writing it until the creator's chmod.
DENIED_POLLS = 3
SHAPE = ("is not name, command, account, cap in seconds, and an optional weight, "
         "heavy or light")
WHOLE_NUMBER = re.compile(r"[0-9]+")
# The fields of the line that a holder writes into its slot file.
HOLDER_FIELDS = ("project", "unit", "row", "account", "start", "pid")
# The start of the lines that a waiter prints, so that a caller that captures the
# output of jig run-check can pass them on while it waits.
WAITING = "every heavy slot is held"
TOOK = "took heavy slot"


class BadRow(Exception):
    """A row of checks.tsv is malformed; the message says how, after the row's name."""


class SlotError(Exception):
    """A heavy slot cannot be taken; the message names the file or the key."""


def parse_row(line):
    """Return name, command, account, cap and weight of one row of checks.tsv.

    Raise BadRow when the row has the wrong number of fields, a cap that is not a whole
    number of 1 or more, or a weight other than heavy, light or empty.
    """
    fields = line.split("\t")
    if len(fields) not in (4, 5) or not WHOLE_NUMBER.fullmatch(fields[3]) \
            or int(fields[3]) == 0:
        raise BadRow(SHAPE)
    weight = fields[4] if len(fields) == 5 else ""
    if weight not in ("", HEAVY, LIGHT):
        raise BadRow(f"has the weight {weight!r}; the weight is heavy or light, "
                     f"or empty for light")
    return fields[0], fields[1], fields[2], int(fields[3]), weight or LIGHT


def srv_root():
    return pathlib.Path(os.environ.get("JIG_SRV") or SRV_ROOT)


def heavy_slots():
    """Return HEAVY_SLOTS of host.conf, or None when there is no limit.

    host.conf holds one KEY=value per line, with spaces allowed around the =, and a
    line that starts with # is a comment, as jig new reads it. A missing file means
    no limit. A file that cannot be read, or a value that is not a whole number of 1
    or more, raises SlotError: running a heavy row without the limit the host asked
    for would defeat it.
    """
    path = srv_root() / "host.conf"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise SlotError(f"cannot read {path}: {getattr(error, 'strerror', None) or error}") \
            from None
    value = None
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, found = line.split("=", 1)
            if key.strip() == SLOTS_KEY:
                value = found.strip()
    if value is None:
        return None
    if not WHOLE_NUMBER.fullmatch(value) or int(value) == 0:
        raise SlotError(f"{path}: {SLOTS_KEY}={value!r} is not a whole number of 1 or more")
    return int(value)


def poll_seconds():
    text = os.environ.get("JIG_HEAVY_POLL")
    if not text:
        return POLL_SECONDS
    try:
        seconds = float(text)
    except ValueError:
        seconds = 0
    if not seconds > 0:
        raise SlotError(f"JIG_HEAVY_POLL={text!r} is not a number of seconds above 0")
    return seconds


def make_dir(path):
    """Make the slot directory when it is missing, so that every account can use it.

    The mode is set again only when this process made it, since the umask may have
    taken bits away; a directory that another account made is taken as it is, as only
    its owner may change it. The group comes from the setgid bit of the parent on the
    host, so no group is set here.
    """
    try:
        os.mkdir(path, 0o2775)
    except FileExistsError:
        return
    except OSError as error:
        raise SlotError(f"cannot make {path}: {error.strerror}") from None
    try:
        os.chmod(path, 0o2775)
    except OSError as error:
        raise SlotError(f"cannot set the mode of {path}: {error.strerror}") from None


def owner(path):
    try:
        uid = os.stat(path).st_uid
    except OSError:
        return "?"
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def try_slot(path):
    """Return a descriptor that holds the lock of the slot file, or None when it is held.

    Raise PermissionError when this account cannot open the file.
    """
    # No O_TRUNC: a waiter that opens a held slot must not erase its holder's line.
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o664)
    try:
        if os.fstat(descriptor).st_uid == os.getuid():
            # The umask may have taken the group's write bit away at creation, and the
            # other accounts of the host share the file. Only the owner may chmod.
            os.fchmod(descriptor, 0o664)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def holder_line(project, unit, row):
    """Return the line that a holder writes: who holds the slot, since when, as what."""
    account = pwd.getpwuid(os.getuid()).pw_name
    start = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    fields = (project, unit, row, account, start, str(os.getpid()))
    return "\t".join(field or "-" for field in fields) + "\n"


def describe(path):
    """Return project, unit and row of the slot's holder from its text, or "?"."""
    try:
        with open(path, encoding="utf-8") as stream:
            first = stream.readline()
    except (OSError, UnicodeDecodeError):
        return "?"
    fields = first.rstrip("\n").split("\t")
    if len(fields) != len(HOLDER_FIELDS) or not all(fields[:3]):
        return "?"
    return f"project {fields[0]}, unit {fields[1]}, row {fields[2]}"


class Slot:
    """A held slot: its number, and the descriptor whose lock holds it."""

    def __init__(self, number, descriptor):
        self.number = number
        self.descriptor = descriptor

    def release(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


def take(project, unit, row, say):
    """Take a free heavy slot, waiting as long as every slot is held.

    Return the Slot, or None when there is no limit or this process tree already holds
    a slot, and the seconds waited, or None when there was no wait. say prints one line
    on stderr. Raise SlotError when no slot can be taken at all.
    """
    if os.environ.get(HELD):
        return None, None
    count = heavy_slots()
    if count is None:
        return None, None
    poll = poll_seconds()
    directory = pathlib.Path(os.environ.get("JIG_HEAVY_DIR") or HEAVY_DIR)
    make_dir(directory)
    line = holder_line(project, unit, row)
    denied = {}
    started = time.monotonic()
    waited = False
    while True:
        for number in range(1, count + 1):
            path = directory / str(number)
            try:
                descriptor = try_slot(path)
            except PermissionError:
                denied[number] = denied.get(number, 0) + 1
                if denied[number] > DENIED_POLLS:
                    raise SlotError(f"cannot open {path}, a file of {owner(path)}, after "
                                    f"{DENIED_POLLS} more tries") from None
                continue
            except OSError as error:
                raise SlotError(f"cannot take {path}: {error.strerror}") from None
            if descriptor is None:
                denied.pop(number, None)
                continue
            # The old text belongs to an earlier holder; it is cut only now, under the lock.
            os.ftruncate(descriptor, 0)
            os.write(descriptor, line.encode("utf-8"))
            seconds = time.monotonic() - started
            if waited:
                say(f"{TOOK} {number} after {round(seconds)} s")
            return Slot(number, descriptor), (seconds if waited else None)
        if not waited:
            held = "; ".join(f"slot {number}: {describe(directory / str(number))}"
                             for number in range(1, count + 1))
            say(f"{WAITING}, waiting: {held}")
            waited = True
        time.sleep(poll)


def is_wait_line(line):
    """Say whether a line of jig run-check's output is one of a waiter's lines."""
    return WAITING in line or TOOK in line
