"""Read a project's profile: the switches that turn project features on or off.

Every project has a kickoff record, specs/00-kickoff.md in its -dev repo. Eric approves
each revision of it with a tag kickoff-rN, and the record that counts is the one at the
tag with the highest N on origin: a local tag may be stale, and only origin is shared.
The record has a section that starts with the line "## Project profile" and ends at the
next line that starts with "## ". In it, a line that starts with a switch name and a
colon, "<switch>: <value>", sets that switch; every other line is prose and is left
alone. SWITCHES lists the switch names and the values each one takes.

Each switch comes back with its value or with NO_VALUE. NO_VALUE is never filled in with
a default: a record that says nothing readable about a switch is a gap that a person
has to close, and each tool decides how it acts until then.

The one switch the tools act on so far is the clean room: the setting that keeps the
reference material, and any screen or file that can show it, away from everyone but
the spec writer. Without a value they act as with the clean room on, so a gap in the
record never shows reference text; clean_room_on() gives that answer.
"""

import os
import re
import subprocess
import sys

RECORD = "specs/00-kickoff.md"
HEADING = "## Project profile"

# A tag whose number has a leading zero, or anything after it, is not one of these.
KICKOFF_TAG = re.compile(r"kickoff-r(0|[1-9][0-9]*)")

# The change classes of a unit, which a value by class names.
CLASSES = ("none", "patch", "minor", "major")


class _NoValue:
    """The result for a switch the profile does not set in a form that can be read."""

    def __repr__(self):
        return "NO_VALUE"


NO_VALUE = _NoValue()


class Switch:
    """One switch: the words its value may be, and the form of a value by class.

    words: the whole values the switch takes; None takes any one word as a name.
    class_words: the values of a pair <class>=<value>, or () when the switch has no
    value by class. unnamed: the value of a class that such a list leaves out.
    """

    def __init__(self, words, class_words=(), unnamed=None):
        self.words = words
        self.class_words = class_words
        self.unnamed = unnamed


SWITCHES = {
    "reference": Switch(("yes", "no")),
    "clean room": Switch(("on", "off")),
    # "none", or the name of the oracle the project runs.
    "oracle": Switch(None),
    "differences review": Switch(("none", "minor-and-major", "every-release", "when-changed"),
                                 ("none", "every-release", "when-changed"), "none"),
    "pre-check": Switch(("on", "off"), ("on", "off"), "off"),
    "review after a risky unit": Switch(("on", "off")),
}

# The line a tool prints when the profile gives it nothing to go on for the clean room.
NO_CLEAN_ROOM = ("jig: no value for clean room in the project profile; "
                 "acting as with the clean room on")

GIT_TIMEOUT_SECONDS = 60


def section_lines(text):
    """Return the lines of every "## Project profile" section of a record.

    A record with two such sections gives the lines of both, so a switch set in each has
    two lines, and no value, rather than whichever came first.
    """
    lines = []
    inside = False
    for line in text.split("\n"):
        if line.startswith("## "):
            inside = line == HEADING
        elif inside:
            lines.append(line)
    return lines


def parse_value(switch, value):
    """Return the value a switch line gives, or NO_VALUE when it is not one of its forms.

    A value by class comes back as a dict with every class, the unnamed ones filled in
    as the switch says.
    """
    if not value:
        return NO_VALUE
    if "=" in value:
        if not switch.class_words:
            return NO_VALUE
        found = {}
        for pair in value.split():
            name, _, word = pair.partition("=")
            if name not in CLASSES or name in found or word not in switch.class_words:
                return NO_VALUE
            found[name] = word
        return {name: found.get(name, switch.unnamed) for name in CLASSES}
    if switch.words is None:
        return value if len(value.split()) == 1 else NO_VALUE
    return value if value in switch.words else NO_VALUE


def parse_profile(text):
    """Return every switch with its value in a kickoff record given as text.

    A switch with no line, or with more than one, has NO_VALUE: two lines that might
    disagree are not settled by picking one.
    """
    given = {name: [] for name in SWITCHES}
    for line in section_lines(text):
        for name in SWITCHES:
            if line.startswith(name + ":"):
                given[name].append(line[len(name) + 1:].strip())
    return {name: parse_value(SWITCHES[name], values[0]) if len(values) == 1 else NO_VALUE
            for name, values in given.items()}


def _git(clone, *args):
    try:
        result = subprocess.run(["git", "-C", str(clone), *args], capture_output=True,
                                text=True, timeout=GIT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def newest_kickoff(clone):
    """Return the name and commit of the newest kickoff tag on origin, or None.

    The listing gives an annotated tag twice: as the tag object, and as the commit it
    points to on a line that ends in ^{}. The commit is the one to read.
    """
    # git -C in a plain directory would use any repository above it instead.
    top = _git(clone, "rev-parse", "--show-toplevel")
    if top is None or os.path.realpath(top.strip()) != os.path.realpath(clone):
        return None
    listing = _git(clone, "ls-remote", "--tags", "origin", "refs/tags/kickoff-r*")
    if listing is None:
        return None
    commits = {}
    for line in listing.splitlines():
        object_name, _, ref = line.partition("\t")
        name = ref.removeprefix("refs/tags/")
        peeled = name.endswith("^{}")
        name = name.removesuffix("^{}")
        if KICKOFF_TAG.fullmatch(name) and (peeled or name not in commits):
            commits[name] = object_name
    if not commits:
        return None
    name = max(commits, key=lambda tag: int(KICKOFF_TAG.fullmatch(tag)[1]))
    return name, commits[name]


def read_record(clone):
    """Return the text of the kickoff record at the newest kickoff tag on origin, or None.

    The record is read by the commit that origin names, so a local tag of the same
    name that points elsewhere changes nothing. When the clone lacks that commit, it
    fetches the tag's objects without moving any ref of the clone.
    """
    newest = newest_kickoff(clone)
    if newest is None:
        return None
    name, commit = newest
    if _git(clone, "cat-file", "-e", f"{commit}^{{commit}}") is None:
        _git(clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin",
             f"refs/tags/{name}")
    return _git(clone, "show", f"{commit}:{RECORD}")


def read_profile(clone):
    """Return every switch with its value, all NO_VALUE when there is no record to read."""
    text = read_record(clone)
    if text is None:
        return {name: NO_VALUE for name in SWITCHES}
    return parse_profile(text)


def no_clean_room_value():
    """Say on stderr that the clean room is taken as on for lack of a value; return True."""
    print(NO_CLEAN_ROOM, file=sys.stderr, flush=True)
    return True


def clean_room_on(conf):
    """Return whether the clean room is on for the project of a conf file.

    Only a profile that says "off" turns it off. When the profile cannot be read, or
    says nothing that can be read, this prints NO_CLEAN_ROOM and returns True, so that
    a gap never shows reference text.
    """
    value = read_profile(conf["REPO"])["clean room"]
    if value == "off":
        return False
    if value == "on":
        return True
    return no_clean_room_value()


def clean_room_on_for(project):
    """Return whether the clean room is on for a project named by its conf file."""
    import jig_units  # loads jig start's worker part, which only this path needs

    try:
        conf = jig_units.read_conf(project)
    except jig_units.Refused:
        return no_clean_room_value()
    return clean_room_on(conf)
