#!/usr/bin/env python3
"""Keep private facts out of a public repository.

The generic patterns read comments, docs and commit messages. Private terms are
read everywhere, code and file names included, because a host name in a string
leaks as surely as one in a comment. Comments and docs are also kept readable
without the specs: wording that sends the reader to another document is refused
there, though not in commit messages.

Tests are held to two more rules, by the patterns of the product's pattern file,
tools/lint-a-patterns.txt: a test name carries no control ID, the ID that ties a
test to the rule it proves, and a test string carries no process ID, the ID of a
record or an approval tag. read_patterns describes the file. A product without
the file gets neither check.
"""

import ast
import fnmatch
import io
import os
import pathlib
import re
import subprocess
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jig_ids  # noqa: E402

DEFAULT_ROOT = pathlib.Path(__file__).resolve().parent.parent

IPV4 = re.compile(
    r"(?<![0-9.])(?:(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])(?![0-9]|\.[0-9])"
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
HOME_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:home|Users|root)(?:/[A-Za-z0-9._-]+)?(?![A-Za-z0-9_-])")

# Technical words that happen to have the shape of a unit ID.
COMMON_WORDS = {
    "amd64", "arm64", "base32", "base64", "i386", "ipv4", "ipv6", "md5", "py3",
    "sha1", "sha224", "sha256", "sha384", "sha512", "utf16", "utf32", "utf8",
    "x509", "x86",
}
VERSION_TAG = re.compile(r"v[0-9]+")

# The owner's address is public; any other address is someone's private fact.
ALLOWED_EMAILS = {"eric@lalonde.com"}
ALLOWED_TRAILERS = {"co-authored-by", "claude-session"}
# Claude Code signs its co-author line with this address, and only there.
CO_AUTHOR_EMAILS = {"noreply@anthropic.com"}

TRAILER = re.compile(r"([A-Za-z0-9][A-Za-z0-9-]*)[ \t]*:(?!//)")
SCISSORS = re.compile(r"^# -+ >8 -+$")

# Wording that sends the reader to another document instead of explaining. A
# comment or a doc must make sense to someone who has never read the specs, so
# these are refused there. A commit message is not held to this: it may well
# name the part of a document it changes.
CROSS_REFERENCE = re.compile(
    r"\bas\s+required\b|\bthe\s+rule\s+(?:above|below)\b|\bsection\s+[0-9]+|§",
    re.IGNORECASE,
)
# A numbered step or case at the start of a line reads as a pointer into a
# procedure written elsewhere. Inside a sentence the same words are ordinary
# English, so only the start of a line counts, and only with the capital letter.
STEP_LABEL = re.compile(r"(?:Case|Step)[ \t]+[0-9]+(?=[:.)]|\s*$)")
# Where the text of a line starts, for each kind of line. A comment starts after
# its comment sign; a docstring line after an opening triple quote; a doc line
# after a list sign (-, *, + or a number with . or )) or a heading sign.
COMMENT_START = re.compile(r"\s*#*\s*")
DOCSTRING_START = re.compile(r"\s*(?:[rRuUbBfF]{0,2}(?:\"\"\"|'''))?\s*")
DOCSTRING_END = re.compile(r"\s*(?:\"\"\"|''')\s*\Z")
DOC_START = re.compile(r"\s*(?:(?:[-*+]|[0-9]+[.)]|#+)\s*)?")

HASH_COMMENT_SUFFIXES = {".sh", ".bash", ".yml", ".yaml", ".mk", ".toml", ".conf"}
HASH_COMMENT_NAMES = {"Makefile"}


class TermsError(Exception):
    """The private terms could not be loaded, so the check cannot be trusted."""


def read_terms(path):
    """Read one term per line; '#' starts a comment."""
    terms = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            term = line.split("#", 1)[0].strip()
            if term:
                terms.append(term)
    if not terms:
        raise TermsError(f"{path} holds no terms")
    return terms


def load_terms(root, tree=False):
    """Return the term pattern, or None when CI runs without the private file.

    tree is true when a whole tree is linted, which may be an unpacked source
    archive rather than a clone; a commit message is always checked in a clone.
    """
    path = os.environ.get("LINT_TERMS") or str(pathlib.Path(root) / ".." / "terms.txt")
    try:
        terms = read_terms(path)
    except FileNotFoundError:
        if os.environ.get("CI") == "true":
            print(f"lint-a: CI=true and no terms file at {path}: "
                  "generic patterns only, private terms not checked")
            return None
        # An unpacked source archive carries no private file, and its text was
        # checked against the terms before the archive was published.
        if tree and not is_clone(root):
            print(f"lint-a: {root} is not a git clone and has no terms file at {path}: "
                  "generic patterns only, private terms not checked")
            return None
        raise TermsError(f"terms file {path} is missing") from None
    except OSError as error:
        raise TermsError(f"terms file {path}: {error}") from None
    # Longest first, so a term that contains another is reported whole.
    words = sorted(terms, key=len, reverse=True)
    alternatives = "|".join(re.escape(word) for word in words)
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{alternatives})(?![A-Za-z0-9_])", re.IGNORECASE)


def generic_problems(text, extra_emails=frozenset(), skip_unit_ids=False):
    """Yield (kind, match) for each generic pattern found in text."""
    if not skip_unit_ids:
        for match in jig_ids.UNIT_ID_MENTION.finditer(text):
            word = match.group()
            if word not in COMMON_WORDS and not VERSION_TAG.fullmatch(word):
                yield "unit ID", word
    for match in jig_ids.RECORD_ID.finditer(text):
        yield "record ID", match.group()
    for match in IPV4.finditer(text):
        yield "IPv4 address", match.group()
    for match in EMAIL.finditer(text):
        address = match.group().lower()
        if address not in ALLOWED_EMAILS and address not in extra_emails:
            yield "e-mail address", match.group()
    for match in HOME_PATH.finditer(text):
        yield "home path", match.group()


def wording_problems(text, start):
    """Yield (kind, match) for wording that comments and docs must not use.

    text is the whole comment or doc line and start is the same line from where
    its words begin, after any comment, list or heading sign.
    """
    for match in CROSS_REFERENCE.finditer(text):
        yield "cross-reference", match.group()
    match = STEP_LABEL.match(start)
    if match:
        yield "step or case label", match.group()


def comment_start(text):
    return text[COMMENT_START.match(text).end():]


def docstring_start(text):
    text = text[DOCSTRING_START.match(text).end():]
    return DOCSTRING_END.sub("", text)


def doc_start(text):
    return text[DOC_START.match(text).end():]


def python_comment_lines(text):
    """Return (line number, text, start) for the comments and docstrings of Python source.

    The start is the text from where the words of the line begin; doc_lines
    explains it.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        # Source that does not parse gets no benefit of the doubt: read all of it.
        return [(number, line, comment_start(line))
                for number, line in enumerate(text.splitlines(), 1)]
    source = text.splitlines()
    found = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            found.append((token.start[0], token.string, comment_start(token.string)))
    documented = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if isinstance(node, documented) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                for number in range(first.lineno, first.end_lineno + 1):
                    line = source[number - 1]
                    found.append((number, line, docstring_start(line)))
    return found


def hash_comment(line):
    """Return the '#' comment of a shell, YAML or make line, or None.

    Quotes are tracked within the line only. A string that spans lines may then
    be read as a comment, which errs toward checking too much, not too little.
    """
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[index:]
    return None


def has_interpreter(text, *names):
    first = text.split("\n", 1)[0]
    return first.startswith("#!") and any(name in first for name in names)


def is_python(path, text):
    return path.suffix == ".py" or has_interpreter(text, "python")


def is_shell(path, text):
    return path.suffix in (".sh", ".bash") or has_interpreter(text, "bash", "/sh", " sh")


def doc_lines(path, text):
    """Return (line number, text, start) for the part of a file the generic patterns read.

    A Python file gives its comments and docstrings, a shell, YAML or make file
    its '#' comments, and any other file every line. The start is the line from
    where its words begin, which the step and case labels are matched against:
    after the '#' signs and spaces of a comment, after the opening triple quote
    of a docstring, and after a list or heading sign of any other line.
    """
    if is_python(path, text):
        return python_comment_lines(text)
    if (path.suffix in HASH_COMMENT_SUFFIXES or path.name in HASH_COMMENT_NAMES
            or is_shell(path, text)):
        found = []
        for number, line in enumerate(text.splitlines(), 1):
            comment = hash_comment(line)
            if comment is not None:
                found.append((number, comment, comment_start(comment)))
        return found
    return [(number, line, doc_start(line)) for number, line in enumerate(text.splitlines(), 1)]


PATTERN_FILE = pathlib.Path("tools") / "lint-a-patterns.txt"
PROCESS_PART = "[process IDs]"
CONTROL_PART = "[control IDs]"
LANGUAGE_PART = re.compile(r"\[language ([^\]\s]+)\]")
LANGUAGE_KEYS = ("files", "names", "strings")
ALLOWANCE = "allow process IDs in test strings"


class PatternError(Exception):
    """The pattern file is not in its form, so the test checks cannot be trusted."""


class Patterns:
    """What the pattern file says: the IDs to look for and where tests are."""

    def __init__(self):
        self.process = []
        self.controls = []
        self.languages = {}
        self.allow_process_ids = False

    def process_regex(self):
        """Return one regex for every process ID pattern, with its bounds, or None.

        A letter, digit or hyphen just before, or a letter or digit just after,
        means the text is part of a longer word, such as the end of a version
        name, and not an ID. A hyphen after is allowed so that an ID still
        counts when a word is joined to it.
        """
        if not self.process:
            return None
        alternatives = "|".join(f"(?:{pattern.pattern})" for pattern in self.process)
        return re.compile(rf"(?<![A-Za-z0-9-])(?:{alternatives})(?![A-Za-z0-9])")


def _compile(pattern, where):
    try:
        return re.compile(pattern, re.MULTILINE)
    except re.error as error:
        raise PatternError(f"{where}: the pattern does not compile ({error})") from None


def read_patterns(path):
    """Read the pattern file, or return None when there is none.

    The file is line based. A line that starts with '#' is a note, and blank
    lines are skipped. Each part starts with a heading line: "[process IDs]",
    under which each line is one process ID pattern; "[control IDs]", under
    which each line is a class name, a space and the class pattern; and
    "[language <name>]", once per product language, with exactly the three
    lines "files <pattern>", "names <pattern>" and "strings <pattern>". The
    first two headings appear once each; there may be no language. The line
    "allow process IDs in test strings" may stand anywhere and turns off the
    test string check. Every pattern is a Python regular expression.

    Raise PatternError, naming the line, on anything else, because a typing
    error in this file would otherwise switch a check off without a word.
    """
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise PatternError(f"{path}: {error}") from None
    patterns = Patterns()
    part = None
    seen_parts = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        where = f"{path}:{number}"
        if not line or line.startswith("#"):
            continue
        if line == ALLOWANCE:
            patterns.allow_process_ids = True
            continue
        language = LANGUAGE_PART.fullmatch(line)
        if line in (PROCESS_PART, CONTROL_PART) or language:
            if line in seen_parts:
                raise PatternError(f"{where}: the part {line} appears twice")
            seen_parts.add(line)
            part = line
            if language:
                patterns.languages[language.group(1)] = {}
            continue
        if line.startswith("["):
            # Read as a mistyped heading rather than a pattern, so that a typing
            # error in a heading is not taken as a pattern that never matches.
            raise PatternError(f"{where}: {line} is not a heading, and a pattern cannot "
                               "start with [; write such a pattern in a group, as "
                               "(?:[A-Z]+)-[0-9]+")
        if part is None:
            raise PatternError(f"{where}: a pattern before the first part")
        if part == PROCESS_PART:
            patterns.process.append(_compile(line, where))
        elif part == CONTROL_PART:
            name, _, pattern = line.partition(" ")
            pattern = pattern.strip()
            if not pattern:
                raise PatternError(f"{where}: a control ID line needs a class and a pattern")
            if any(name == known for known, _ in patterns.controls):
                raise PatternError(f"{where}: the class {name} appears twice")
            patterns.controls.append((name, _compile(pattern, where)))
        else:
            keys = patterns.languages[LANGUAGE_PART.fullmatch(part).group(1)]
            key, _, pattern = line.partition(" ")
            pattern = pattern.strip()
            if key not in LANGUAGE_KEYS or not pattern:
                raise PatternError(f"{where}: a language line is files, names or strings, "
                                   "a space and a pattern")
            if key in keys:
                raise PatternError(f"{where}: {key} appears twice in {part}")
            keys[key] = _compile(pattern, where)
    for heading in (PROCESS_PART, CONTROL_PART):
        if heading not in seen_parts:
            raise PatternError(f"{path}: the part {heading} is missing")
    for name, keys in patterns.languages.items():
        missing = [key for key in LANGUAGE_KEYS if key not in keys]
        if missing:
            raise PatternError(f"{path}: [language {name}] has no {', '.join(missing)} line")
    return patterns


def _line_of(text, offset):
    return text.count("\n", 0, offset) + 1


def problems_in_tests(name, text, patterns):
    """Return the problems of one file against the test patterns of each language it is in.

    Names and strings are matched across the whole file, so a pattern may
    span lines; each problem names the line where the ID itself stands.
    """
    problems = []
    process = None if patterns.allow_process_ids else patterns.process_regex()
    for keys in patterns.languages.values():
        if not keys["files"].fullmatch(name):
            continue
        for test in keys["names"].finditer(text):
            for _, regex in patterns.controls:
                for found in regex.finditer(test.group()):
                    if found.group():
                        line = _line_of(text, test.start() + found.start())
                        problems.append(f"{name}:{line}: control ID in a test name: "
                                        f"{found.group()}")
        if process is None:
            continue
        for string in keys["strings"].finditer(text):
            for found in process.finditer(string.group()):
                line = _line_of(text, string.start() + found.start())
                problems.append(f"{name}:{line}: process ID in a test string: {found.group()}")
    return problems


def is_clone(root):
    """Tell whether root is the top of a git clone, where git can list the files."""
    return (pathlib.Path(root) / ".git").exists()


def ignore_rules(root):
    """Return the patterns of root's .gitignore, for a tree that git cannot read.

    Only the simple forms are read: a pattern without a slash is matched against
    each file and directory name, one that ends in a slash only against
    directory names, and one with a slash inside against the path from root.
    Negated patterns are left out, so they hide nothing.
    """
    try:
        lines = (pathlib.Path(root) / ".gitignore").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rules = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith(("#", "!")):
            rules.append(line)
    return rules


def ignored(rules, relative, is_dir):
    for rule in rules:
        directory_only = rule.endswith("/")
        pattern = rule.rstrip("/")
        if directory_only and not is_dir:
            continue
        if "/" in pattern:
            if fnmatch.fnmatchcase(relative, pattern.lstrip("/")):
                return True
        elif fnmatch.fnmatchcase(relative.rsplit("/", 1)[-1], pattern):
            return True
    return False


def walked_files(root):
    """List the files of a tree that is not a git clone, as git would list them.

    An unpacked source archive has no git data, yet its offline build runs lint
    A and shellcheck on the same files as a clone of the release commit.
    """
    root = pathlib.Path(root)
    rules = ignore_rules(root)
    names = []
    for directory, subdirs, files in os.walk(root):
        base = pathlib.Path(directory).relative_to(root).as_posix()
        prefix = "" if base == "." else base + "/"
        subdirs[:] = [name for name in subdirs
                      if name != ".git" and not ignored(rules, prefix + name, True)]
        names.extend(prefix + name for name in files
                     if not ignored(rules, prefix + name, False))
    return sorted(name for name in names if (root / name).is_file())


def listed_files(root):
    """List tracked files and new ones git does not ignore, skipping deleted ones."""
    if not is_clone(root):
        return walked_files(root)
    output = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True, capture_output=True, text=True,
    ).stdout
    names = sorted(set(name for name in output.split("\0") if name))
    return [name for name in names if (pathlib.Path(root) / name).is_file()]


def lint_tree(root, term_pattern, patterns=None):
    """Return (problems, number of files read) for a whole tree.

    patterns, from read_patterns, adds the checks of test names and strings.
    """
    problems = []
    names = listed_files(root)
    for name in names:
        path = pathlib.Path(name)
        text = (pathlib.Path(root) / path).read_text(encoding="utf-8", errors="replace")
        if term_pattern:
            for match in term_pattern.finditer(name):
                problems.append(f"{name}: private term in the path: {match.group()}")
            for number, line in enumerate(text.splitlines(), 1):
                for match in term_pattern.finditer(line):
                    problems.append(f"{name}:{number}: private term: {match.group()}")
        for number, line, start in doc_lines(path, text):
            for kind, found in generic_problems(line):
                problems.append(f"{name}:{number}: {kind}: {found}")
            for kind, found in wording_problems(line, start):
                problems.append(f"{name}:{number}: {kind}: {found}")
        if patterns:
            problems.extend(problems_in_tests(name, text, patterns))
    return problems, len(names)


def message_problems(text, term_pattern):
    """Return the problems of a commit message: its patterns and its trailers."""
    lines = []
    for line in text.splitlines():
        if SCISSORS.match(line):
            break
        lines.append(line.rstrip())

    # Comment lines are left out of the structure, because git may strip them,
    # but not out of the patterns, because with -m or -F git keeps them.
    numbered = [(index, line) for index, line in enumerate(lines) if not line.startswith("#")]
    while numbered and not numbered[-1][1]:
        numbered.pop()
    while numbered and not numbered[0][1]:
        numbered.pop(0)
    # Trailers sit in the last paragraph; the subject line is never one.
    start = len(numbered)
    while start > 1 and numbered[start - 1][1]:
        start -= 1

    problems = []
    trailer_lines = {}
    for index, line in numbered[start:]:
        match = TRAILER.match(line)
        if match:
            name = match.group(1)
            if name.lower() in ALLOWED_TRAILERS:
                trailer_lines[index] = name.lower()
            else:
                problems.append(f"line {index + 1}: trailer not allowed: {name}")

    for index, line in enumerate(lines):
        if term_pattern:
            for match in term_pattern.finditer(line):
                problems.append(f"line {index + 1}: private term: {match.group()}")
        trailer = trailer_lines.get(index)
        # A session name is free-form, so only its shape can look like a unit ID.
        found = generic_problems(
            line,
            extra_emails=CO_AUTHOR_EMAILS if trailer == "co-authored-by" else frozenset(),
            skip_unit_ids=trailer is not None,
        )
        for kind, value in found:
            problems.append(f"line {index + 1}: {kind}: {value}")
    return problems


def report(tool, problems):
    for problem in problems:
        print(f"{tool}: {problem}", file=sys.stderr)
    print(f"{tool}: {len(problems)} problem(s)", file=sys.stderr)
    return 1


def commit_msg_main(argv, root=DEFAULT_ROOT):
    """Check the message file git passes to a commit-msg hook."""
    if len(argv) != 1:
        print("usage: commit-msg <message-file>", file=sys.stderr)
        return 2
    try:
        term_pattern = load_terms(root)
    except TermsError as error:
        print(f"commit-msg: {error}", file=sys.stderr)
        return 1
    with open(argv[0], encoding="utf-8", errors="replace") as handle:
        problems = message_problems(handle.read(), term_pattern)
    return report("commit-msg", problems) if problems else 0


def main(argv):
    """Lint a tree, or list its shell files for shellcheck."""
    if argv[:1] == ["--shell-files"]:
        root = argv[1] if len(argv) > 1 else DEFAULT_ROOT
        for name in listed_files(root):
            path = pathlib.Path(root) / name
            if is_shell(path, path.read_text(encoding="utf-8", errors="replace")):
                print(path)
        return 0

    if len(argv) > 1:
        print("usage: lint-a.sh [<root>]", file=sys.stderr)
        return 2
    root = argv[0] if argv else DEFAULT_ROOT
    try:
        term_pattern = load_terms(root, tree=True)
    except TermsError as error:
        print(f"lint-a: {error}", file=sys.stderr)
        return 1
    try:
        patterns = read_patterns(pathlib.Path(root) / PATTERN_FILE)
    except PatternError as error:
        print(f"lint-a: {error}", file=sys.stderr)
        return 1
    problems, count = lint_tree(root, term_pattern, patterns)
    if count == 0:
        print(f"lint-a: no files found under {root}", file=sys.stderr)
        return 1
    if problems:
        return report("lint-a", problems)
    print(f"lint-a: clean, {count} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
