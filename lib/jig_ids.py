"""The ID forms jig uses, defined once so every tool agrees on them."""

import re

# A prompt or block name: at most six lower-case letters and digits.
UNIT_ID = re.compile(r"[a-z0-9]{1,6}")

# A word in running text that looks like a prompt or block name. Plain words
# have no digit and very short ones are mostly version tags, so this asks for
# a leading letter, a digit somewhere, and three to six characters.
UNIT_ID_MENTION = re.compile(
    r"(?<![A-Za-z0-9_])(?=[a-z0-9]*[0-9])[a-z][a-z0-9]{2,5}(?![A-Za-z0-9_])"
)

# The records a project keeps about itself: decisions, questions, requirements,
# findings, open items, controls and fixtures.
RECORD_ID = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?:(?:D|Q|IQ|R|K|F|OI)-[0-9]+"
    r"|P[0-9]+-D[0-9]+"
    r"|C-[a-z0-9_-]+-[0-9]+"
    r"|X-[a-z0-9][a-z0-9-]*)"
    r"(?![A-Za-z0-9_-])"
)

# The one form every record ID of a project fits, whatever its class: a word of
# letters and digits that starts with a letter, then one or more parts that are
# each a hyphen and such a word, the last ending in a digit. The guards on both
# sides keep a search from finding an ID inside a longer one: without them, a
# search in a two-digit ID would also find the shorter ID of its first digit.
ID_BEFORE = r"(?<![A-Za-z0-9-])"
ID_AFTER = r"(?![A-Za-z0-9-])"
GENERAL_ID = re.compile(
    ID_BEFORE + r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*-[A-Za-z0-9]*[0-9]" + ID_AFTER
)


def bounded(pattern):
    """Compile pattern with the guards of GENERAL_ID on both sides.

    A project's own ID patterns are searched this way, so that, like the general
    form, they never find an ID inside a longer word or ID. Raise re.error when
    the pattern does not compile.
    """
    return re.compile(ID_BEFORE + "(?:" + pattern + ")" + ID_AFTER)


def fixed_bounded(text):
    """Compile a search for text as it is, with the guards of GENERAL_ID around it."""
    return re.compile(ID_BEFORE + re.escape(text) + ID_AFTER)


# The IDs of the process records and the approval tags, one regular expression
# each: decisions, phase decisions, questions, implementer questions,
# requirements, open items, findings, then the spec, spec revision,
# implementation and kickoff tags. Lint A keeps these out of test strings. A
# unit name alone is not among them, because its form also fits ordinary short
# words. Each pattern fits the general form above. The template of the lint A
# pattern file holds these lines as they are, and the test of jig new compares
# the two.
PROCESS_ID_PATTERNS = (
    r"D-[0-9]+",
    r"P[0-9]+-D[0-9]+",
    r"Q-[0-9]+",
    r"IQ-[0-9]+",
    r"R-[0-9]+",
    r"OI-[0-9]+",
    r"F-[0-9]+",
    r"spec-p[0-9]+",
    r"spec-p[0-9]+r[0-9]+",
    r"impl-p[0-9]+",
    r"kickoff-r[0-9]+",
)


def is_unit_id(text):
    """Say whether text is a valid name for a prompt or a block."""
    return UNIT_ID.fullmatch(text) is not None
