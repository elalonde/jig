"""Find the words "Spec tag:" split over two lines of a prompt's ROLE section.

The ROLE section of a unit prompt names the approved tag after the words "Spec tag:".
jig start reads that tag only where the two words stand on one line, so a prompt that
wraps them, with "Spec" at the end of one line and "tag:" at the start of the next,
names no tag at all: an implementer unit would then be refused late, and any other unit
would start without the tag its author meant. jig validate refuses such a prompt with
this test, and jig start, which runs jig validate on the prompt before it does anything
else, refuses it through the same test.
"""

import re

# "Spec" as the last word of a line, then "tag:" as the first word of the next line,
# after any indent that a continuation line of the section carries.
SPLIT = re.compile(r"(?<![\w-])Spec[ \t]*\n[ \t]*tag:")


def split_line(text):
    """Return the index, from 0, of the line of text that ends with "Spec" when the next
    line starts with "tag:", or None when no line does."""
    match = SPLIT.search(text)
    return None if match is None else text.count("\n", 0, match.start())
