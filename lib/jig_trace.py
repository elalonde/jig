"""Readers of a project's trace records, shared by the jig trace commands.

The trace lives in the -dev clone and describes the product clone nested in it.
trace/<product path>.json holds the entries of one product file: each entry ties
record IDs and comment text to a code line, found again by the line's text and by
which occurrence of that text it is. specs/ids.tsv declares the project's ID
classes, each with a regular expression, the files that define its IDs and the
coverage check that applies to it. trace/commit-map.tsv maps the commits that a
history rewrite replaced to the commits that took their place, so that a record
citing an old hash can still be followed. specs/id-map.tsv does the same for IDs
that a renumbering replaced.

An ID is defined by one line of a file that its class names: a heading that starts
with the ID, a list item that starts with it, or a table row whose first cell is
the ID.

Each reader raises FormError with one sentence when a file does not have its form,
so that a tool can print it as one failure line.
"""

import collections
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jig_ids  # noqa: E402

ENTRY_KEYS = {"line", "text", "nth", "ids", "comments"}
ID_CLASSES_HEADER = ["class", "regex", "defined", "coverage"]
COMMIT_MAP_HEADER = ["old", "new", "date", "reason"]
ID_MAP_HEADER = ["old", "new"]
# The coverage checks a class can ask for; none still checks that no ID is
# defined twice.
COVERAGE_WORDS = ("requirement", "control", "fixture", "reference", "none")
# Git names a commit by 40 hex digits, or by 64 in a repository that uses SHA-256.
FULL_HASH = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# The kickoff record, the status file and the two ID tables sit under specs/
# but are not specs: an ID they mention is not written into a spec by being
# mentioned there.
NOT_SPEC_DOCUMENTS = {"00-kickoff.md", "00-status.md", "ids.tsv", "id-map.tsv"}


class FormError(Exception):
    """A record file that does not have its form; the message names the problem."""


# One row of specs/ids.tsv. regex matches a whole ID; search finds the IDs of the
# class in text, with the guards of the general ID form; defined holds the glob
# patterns of the files that define the IDs.
IdClass = collections.namedtuple("IdClass", "name regex search defined coverage")


def dev_root(start):
    """Return the nearest directory at or above start whose name ends in -dev, or None."""
    path = pathlib.Path(start).resolve()
    for candidate in (path, *path.parents):
        if candidate.name.endswith("-dev") and len(candidate.name) > len("-dev"):
            return candidate
    return None


def product_dir(dev):
    """The product clone nested in a -dev clone: the -dev name without its suffix."""
    return dev / dev.name[:-len("-dev")]


def _is_count(value, least):
    # bool is a subclass of int in Python, but true is not a line number.
    return isinstance(value, int) and not isinstance(value, bool) and value >= least


def _is_strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def check_entries(data):
    """Raise FormError unless data is a list of entries with exactly the five keys."""
    if not isinstance(data, list):
        raise FormError("is not a JSON array of entries")
    for number, entry in enumerate(data, 1):
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise FormError(f"entry {number} does not have exactly the keys "
                            "line, text, nth, ids, comments")
        if not (_is_count(entry["line"], 0) and isinstance(entry["text"], str)
                and _is_count(entry["nth"], 1) and _is_strings(entry["ids"])
                and _is_strings(entry["comments"])):
            raise FormError(f"entry {number} has a value of the wrong type: line is a whole "
                            "number from 0, text a string, nth a whole number from 1, "
                            "ids and comments arrays of strings")


def read_trace_file(path):
    """Return the entries of one trace file, or raise FormError."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise FormError(f"is not valid JSON in UTF-8 ({error})") from None
    check_entries(data)
    return data


def trace_files(dev):
    """Map each product path that has a trace file to that file, from trace/ of dev.

    The ledger directory holds one note per commit, not entries, so it is skipped.
    """
    top = dev / "trace"
    found = {}
    if not top.is_dir():
        return found
    for path in sorted(top.rglob("*.json")):
        relative = path.relative_to(top)
        if relative.parts[0] == "ledger" or not path.is_file():
            continue
        found[str(relative)[:-len(".json")]] = path
    return found


def anchor_resolves(lines, entry):
    """Say whether an entry finds its code line among the stripped lines of its file.

    The line number an entry holds is where the line was when the entry was written;
    code moves, so only the text and its occurrence count decide. Line 0 stands for
    the file as a whole.
    """
    if entry["line"] == 0:
        return True
    return sum(1 for line in lines if line == entry["text"]) >= entry["nth"]


def stripped_lines(path):
    text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    return [line.strip() for line in text.split("\n")]


def read_id_classes(path):
    """Return the ID classes of specs/ids.tsv as a list of IdClass, in file order.

    Raise FormError on a file in another form: a regex that does not compile, a
    coverage word outside COVERAGE_WORDS, or a class that names no defining file.
    """
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise FormError("is not UTF-8 text") from None
    rows = _tsv_rows(text, ID_CLASSES_HEADER)
    classes, names = [], set()
    for number, (name, regex, defined, coverage) in rows:
        if not name or not regex:
            raise FormError(f"line {number} has an empty class or regex")
        if name in names:
            raise FormError(f"line {number} repeats the class {name}")
        names.add(name)
        try:
            whole = re.compile(regex)
        except re.error as error:
            raise FormError(f"line {number}: the regex of class {name} does not compile "
                            f"({error})") from None
        try:
            search = jig_ids.bounded(regex)
        except re.error:
            # The search puts the regex after its guards, where Python refuses a
            # flag that is meant for the whole regex, though the regex alone is
            # valid. A flag scoped to a group works in both places.
            raise FormError(f"line {number}: class {name}: a flag for the whole regex, "
                            "such as (?i), is not allowed in a class regex; a scoped group "
                            "such as (?i:...) is") from None
        if coverage not in COVERAGE_WORDS:
            raise FormError(f"line {number}: the coverage {coverage!r} of class {name} is not "
                            "one of " + ", ".join(COVERAGE_WORDS))
        patterns = [part.strip() for part in defined.split(",") if part.strip()]
        if not patterns:
            raise FormError(f"line {number}: class {name} names no file in defined")
        classes.append(IdClass(name, whole, search, tuple(patterns), coverage))
    return classes


def _tsv_rows(text, header):
    """Return (line number, fields) for each row under the header, or raise FormError."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0].rstrip("\r").split("\t") != header:
        raise FormError("line 1 is not the header line " + ", ".join(header)
                        + ", separated by tabs")
    rows = []
    for number, line in enumerate(lines[1:], 2):
        fields = line.rstrip("\r").split("\t")
        if len(fields) != len(header):
            raise FormError(f"line {number} has {len(fields)} fields, not {len(header)}")
        rows.append((number, fields))
    return rows


def spec_documents(dev):
    """Map the path of each spec document under specs/ of dev to its text."""
    top = dev / "specs"
    documents = {}
    if not top.is_dir():
        return documents
    for path in sorted(top.rglob("*")):
        relative = path.relative_to(top)
        if not path.is_file() or str(relative) in NOT_SPEC_DOCUMENTS:
            continue
        documents[f"specs/{relative}"] = path.read_text(encoding="utf-8", errors="replace")
    return documents


def ids_in(text):
    """The set of IDs a search with the general ID form finds in text."""
    return {match.group(0) for match in jig_ids.GENERAL_ID.finditer(text)}


def class_of(classes, record):
    """The name of the first class whose regex matches the whole ID, or None."""
    for item in classes:
        if item.regex.fullmatch(record):
            return item.name
    return None


def occurrences(text, record):
    """The line numbers where record occurs in text, found as the general ID form finds IDs.

    A part of a longer ID does not count, such as the first two characters of an ID
    with a two-digit number.
    """
    return [number for number, line in enumerate(text.split("\n"), 1)
            if any(match.group(0) == record for match in jig_ids.GENERAL_ID.finditer(line))]


def defined_files(dev, patterns):
    """The files under dev that the glob patterns of a class name, in pattern order.

    In a pattern, * matches any characters except a slash, and every other
    character stands for itself. Each file is listed once. The product clone
    nested in dev is never listed, because records live in the -dev repo only.
    """
    product = product_dir(dev).name
    found = []
    for pattern in patterns:
        # A pattern that climbs out of the -dev repo names no record of it.
        if ".." in pattern.split("/"):
            continue
        places = [dev]
        for part in pattern.split("/"):
            if "*" in part:
                name = re.compile("[^/]*".join(re.escape(piece) for piece in part.split("*")))
                places = [child for place in places if place.is_dir()
                          for child in sorted(place.iterdir()) if name.fullmatch(child.name)]
            else:
                places = [place / part for place in places]
        for place in places:
            relative = place.relative_to(dev).as_posix()
            if (place.is_file() and relative.split("/")[0] != product
                    and relative not in found):
                found.append(relative)
    return found


HEADING = re.compile(r"#+ ")
LIST_ITEM = re.compile(r"- (?:\*\*)?")
# A bar ends a table cell unless a backslash comes right before it.
CELL_BAR = re.compile(r"(?<!\\)\|")


def table_cells(line):
    """The cells of a table row, each without the spaces around it."""
    cells = CELL_BAR.split(line.rstrip())[1:]
    if cells and cells[-1] == "" and line.rstrip().endswith("|"):
        cells.pop()
    return [cell.strip() for cell in cells]


def defined_on(line):
    """The ID a line defines and the form it uses: heading, item or row; else None.

    A heading or a list item defines the ID of the general form that starts right
    after its sign; a table row defines whatever its first cell holds, which the
    caller then holds to the regex of the class.
    """
    for kind, sign in (("heading", HEADING), ("item", LIST_ITEM)):
        start = sign.match(line)
        if start:
            found = jig_ids.GENERAL_ID.match(line, start.end())
            return (found.group(0), kind) if found else None
    if line.startswith("|"):
        cells = table_cells(line)
        return (cells[0], "row") if cells and cells[0] else None
    return None


# One definition: the file from the root of the -dev repo, the line number, the
# ID, its form, and the full text of the file, which the requirement and control
# checks read around the line.
Definition = collections.namedtuple("Definition", "path line record kind lines")


def definitions(dev, item, kinds=("heading", "item", "row")):
    """The definitions of the IDs of class item, in file and line order.

    kinds limits the forms that count: a requirement is defined by a heading only,
    and a control or fixture by a table row only.
    """
    found = []
    for relative in defined_files(dev, item.defined):
        text = (dev / relative).read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        for number, line in enumerate(lines, 1):
            defined = defined_on(line)
            if defined and defined[1] in kinds and item.regex.fullmatch(defined[0]):
                found.append(Definition(relative, number, defined[0], defined[1], lines))
    return found


def table_header(lines, number):
    """The cells of the header of the table that holds line number, which counts from 1.

    A table is a block of consecutive lines that start with a bar, and its first
    line is its header.
    """
    start = number
    while start > 1 and lines[start - 2].startswith("|"):
        start -= 1
    return table_cells(lines[start - 1])


def owner_lines(lines, number):
    """The files that the Owner lines under the heading at line number name, in order.

    They are looked for up to the next heading.
    """
    owners = []
    for line in lines[number:]:
        if HEADING.match(line):
            break
        if line.startswith("Owner:"):
            owners.append(line[len("Owner:"):].strip())
    return owners


def git(product, *args):
    return subprocess.run(["git", "-C", str(product), *args], capture_output=True, text=True,
                          check=False)


def in_product(product, commit):
    """The full hash of commit when it is in the history of the product's HEAD, else None."""
    found = git(product, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    if found.returncode != 0:
        return None
    full = found.stdout.strip()
    if git(product, "merge-base", "--is-ancestor", full, "HEAD").returncode != 0:
        return None
    return full


class CommitMap:
    """The rows of trace/commit-map.tsv: which commit replaced which, and when."""

    def __init__(self, rows):
        # Each row is (old, new, date, reason), with full hashes.
        self.rows = rows

    @classmethod
    def read(cls, path):
        """Read a commit map, or raise FormError when it is in another form."""
        try:
            text = pathlib.Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise FormError("is not UTF-8 text") from None
        rows, first_lines = [], {}
        for number, (old, new, date, reason) in _tsv_rows(text, COMMIT_MAP_HEADER):
            # A rewrite can drop a commit, and then its row has no new hash.
            if not (FULL_HASH.fullmatch(old) and (new == "" or FULL_HASH.fullmatch(new))):
                raise FormError(f"line {number}: old must be a full commit hash, and new a "
                                "full commit hash or empty")
            if not DATE.fullmatch(date):
                raise FormError(f"line {number}: the date is not YYYY-MM-DD")
            try:
                datetime.date.fromisoformat(date)
            except ValueError:
                raise FormError(f"line {number}: {date} is not a date") from None
            if not reason.strip():
                raise FormError(f"line {number} gives no reason")
            # One old hash has one replacement; a second row would leave the
            # read-through with two answers.
            if old in first_lines:
                raise FormError(f"line {number} repeats the old hash {old}, of line "
                                f"{first_lines[old]}")
            first_lines[old] = number
            rows.append((old, new, date, reason))
        return cls(rows)

    def import_commit(self, product):
        """Return the import: the new hash that every other new hash is an ancestor of.

        A rewrite can be followed by later ones, so the import is the newest of them.
        A dropped commit has no new hash and takes no part. Raise FormError when a
        new hash is not in the product or no hash is newest.
        """
        news = sorted({new for _old, new, _date, _reason in self.rows if new})
        for new in news:
            if in_product(product, new) is None:
                raise FormError(f"the new hash {new} is not in the product")
        for candidate in news:
            if all(git(product, "merge-base", "--is-ancestor", other, candidate).returncode == 0
                   for other in news if other != candidate):
                return candidate
        raise FormError("no new hash has every other new hash as its ancestor")

    def resolve(self, product, commit):
        """Read a hash that a record cites through the map.

        A hash in the product reads as itself, in full. A hash that a rewrite replaced
        reads as the new hash of its row; an abbreviation of at least seven digits
        does too when it names one old hash only. A dropped commit, and any other
        hash, gives None.
        """
        commit = commit.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{7,64}", commit):
            return None
        found = in_product(product, commit)
        if found:
            return found
        news = {new for old, new, _date, _reason in self.rows if old.startswith(commit)}
        olds = {old for old, _new, _date, _reason in self.rows if old.startswith(commit)}
        if len(olds) == 1 and len(news) == 1:
            return news.pop() or None
        return None


class IdMap:
    """The rows of specs/id-map.tsv: which new IDs replaced each old ID.

    An old ID can be any text, because the IDs before a renumbering need not have
    the general form; the new IDs must have it. An empty list marks a dropped ID.
    """

    def __init__(self, rows):
        # Maps each old ID to the list of its new IDs, in file order.
        self.rows = rows

    @classmethod
    def read(cls, path):
        """Read an ID map, or raise FormError when it is in another form."""
        try:
            text = pathlib.Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise FormError("is not UTF-8 text") from None
        rows, lines = {}, {}
        for number, (old, new) in _tsv_rows(text, ID_MAP_HEADER):
            if not old:
                raise FormError(f"line {number} has an empty old ID")
            if old in rows:
                raise FormError(f"line {number} repeats the old ID {old}, of line {lines[old]}")
            news = new.split(",") if new else []
            for record in news:
                if jig_ids.GENERAL_ID.fullmatch(record) is None:
                    raise FormError(f"line {number}: the new ID {record!r} does not have the "
                                    "general ID form; new IDs are separated by commas, with "
                                    "no spaces")
            rows[old], lines[old] = news, number
        for old, news in rows.items():
            for record in news:
                if record in rows and record != old:
                    raise FormError(f"line {lines[old]}: the new ID {record} is the old ID of "
                                    f"line {lines[record]}")
        return cls(rows)

    def read_through(self, record):
        """The IDs that record stands for now: its new IDs, or itself when not an old ID."""
        return self.rows.get(record, [record])
