"""A Claude Code session in tmux: the dialog test and the delivery of one line.

jig wait and the supervisor (jig orch) test for a dialog the same way: the session's
normal input bar is missing for DIALOG_POLLS polls in a row. jig send
and the supervisor deliver a line the same way: the text, a pause, Enter, then
a check that the text reached the session and left its input box, because text and
Enter sent together were measured to stay unsubmitted. The supervisor also asks, before
it types, whether a person has typed into the box: box_is_empty() reads the box as
drawn, so that a hint the harness draws in it is not taken for typed text.

tmux is the one on PATH, with its default server; a test puts its own tmux first.
"""

import re
import subprocess
import time

# Measured on Claude Code screens: under the normal input bar, in the last lines of the
# screen, is a line that names the permission mode, and a dialog hides it. jig starts its
# sessions in auto mode; the others are here so that a session a person started in
# another mode, or cycled with shift+tab, still reads as a session at work. The last one
# is the hint of the default mode. A pasted text can hide the line for one poll.
INPUT_BARS = ("auto mode on", "accept edits on", "plan mode on", "bypass permissions on",
              "? for shortcuts")
BAR_LINES = 8
DIALOG_POLLS = 2

# The input box lies between the last two rules of the screen: lines of this character.
RULE = "─"
# Claude Code can show a long text as this marker in the box instead of the text.
PASTED = "[Pasted text"
# A pasted image shows as this marker. Both markers stand for what a person put in the
# box, however they are drawn.
PASTED_IMAGE = "[Image #"
# The mark at the start of an empty box. A mark of another mode, such as "!" for a shell
# command, is something a person typed.
PROMPT_MARKS = (">", "❯")
BOX_SIDE = "│"

# The escape sequences of a screen captured with tmux capture-pane -e: a control
# sequence, an operating system command such as a link, or a two-character escape.
# Only a control sequence that ends in "m" changes how the next characters are drawn.
ESCAPE = re.compile(r"\x1b(?:\[([0-?]*)[ -/]*([@-~])|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])")
# Claude Code draws a placeholder or a suggested prompt in an empty box as faint or grey
# text, with its cursor, a character in inverse video, on the first character; a person's
# typed text is drawn in the terminal's own colour. These are the codes of faint and of
# grey: the faint attribute, bright black, and the grey ramp of the 256 colours.
FAINT, FAINT_OFF, INVERSE, INVERSE_OFF = 2, 22, 7, 27
GREY = 90
GREY_RAMP = range(232, 256)
GREY_SPREAD = 16

PAUSE_SECONDS = 0.5
CHECK_SECONDS = 5.0
POLL_SECONDS = 0.1
ENTER_TRIES = 2
# Enough of the text's end to see that it is still in the box.
TAIL = 20


def non_blank(screen):
    return [line for line in screen.splitlines() if line.strip()]


def has_input_bar(screen):
    return any(bar in line for line in non_blank(screen)[-BAR_LINES:] for bar in INPUT_BARS)


def tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, check=False)


def capture(session, styled=False):
    """Return the session's screen, or None when the session does not exist.

    A styled screen keeps how each character is drawn, as escape sequences.
    """
    styles = ("-e",) if styled else ()
    result = tmux("capture-pane", "-p", *styles, "-t", f"={session}:")
    return result.stdout if result.returncode == 0 else None


def literal(text):
    """Return the argument that makes tmux send-keys -l type exactly text.

    tmux reads an argument that ends in ";" as the end of a command: it drops that
    ";", and turns a "\\;" at the end into ";". A text that ends in ";" would arrive
    short and never match the input box. Ending the argument in "\\;" types the ";".
    """
    return text[:-1] + "\\;" if text.endswith(";") else text


def is_rule(line):
    text = line.strip()
    return len(text) >= 3 and set(text) == {RULE}


def input_box(screen):
    """Return the text of the input box; without two rules, the last lines of the screen."""
    lines = screen.splitlines()
    rules = [index for index, line in enumerate(lines) if is_rule(line)]
    if len(rules) >= 2:
        return "\n".join(lines[rules[-2] + 1:rules[-1]])
    return "\n".join(non_blank(screen)[-BAR_LINES:])


def plain(text):
    """The text of a styled screen without its escape sequences."""
    return ESCAPE.sub("", text)


def drawn_box(screen):
    """Return the lines of the input box of a styled screen, escapes kept, or None.

    The box is read only from a screen with the normal input bar, as the dialog test
    reads it, and only between two rules: without them, the lines that would stand in
    for the box could be anything, and an empty box must not be guessed.
    """
    lines = screen.splitlines()
    shown = [plain(line) for line in lines]
    if not has_input_bar("\n".join(shown)):
        return None
    rules = [index for index, line in enumerate(shown) if is_rule(line)]
    if len(rules) < 2:
        return None
    return lines[rules[-2] + 1:rules[-1]]


def _faint_colour(params):
    """Say whether a foreground colour of 256 colours or of RGB is grey; return the
    answer and the number of parameters it took."""
    if len(params) >= 2 and params[0] == 5:
        return params[1] in GREY_RAMP or params[1] == 8, 2
    if len(params) >= 4 and params[0] == 2:
        rgb = params[1:4]
        return max(rgb) - min(rgb) <= GREY_SPREAD, 4
    return False, len(params)


def _style(state, text):
    """Apply the parameters of one SGR sequence to state, a dict of faint, grey and
    inverse."""
    params = []
    for part in (text or "0").split(";"):
        # A parameter with colons, as in 38:5:245, carries its own sub-parameters.
        params.extend(int(piece) if piece.isdigit() else 0 for piece in part.split(":"))
    index = 0
    while index < len(params):
        code = params[index]
        index += 1
        if code == 0:
            state.update(faint=False, grey=False, inverse=False)
        elif code == FAINT:
            state["faint"] = True
        elif code == FAINT_OFF:
            state["faint"] = False
        elif code == INVERSE:
            state["inverse"] = True
        elif code == INVERSE_OFF:
            state["inverse"] = False
        elif code == GREY:
            state["grey"] = True
        elif code == 38:
            state["grey"], taken = _faint_colour(params[index:])
            index += taken
        elif 30 <= code <= 39 or 91 <= code <= 97:
            state["grey"] = False
        elif code == 48:
            # A background colour of 256 colours or RGB: its parameters are skipped.
            index += _faint_colour(params[index:])[1]


def _cells(lines):
    """Return each line as a list of (character, faint, inverse), with the drawing
    state carried from line to line as a terminal carries it."""
    state = {"faint": False, "grey": False, "inverse": False}
    result = []
    for line in lines:
        cells, position = [], 0
        for match in ESCAPE.finditer(line):
            for char in line[position:match.start()]:
                cells.append((char, state["faint"] or state["grey"], state["inverse"]))
            if match.group(2) == "m":
                _style(state, match.group(1))
            position = match.end()
        for char in line[position:]:
            cells.append((char, state["faint"] or state["grey"], state["inverse"]))
        result.append(cells)
    return result


def box_is_empty(box):
    """Say whether the lines of a drawn input box hold no typed character.

    A character counts as typed unless it is blank, a side of the box, the prompt mark
    at the start, faint or grey text, or the cursor on the first character of such
    text. The cursor on a typed character, or after it, leaves that character typed.
    """
    shown = plain("\n".join(box))
    if PASTED in shown or PASTED_IMAGE in shown:
        return False
    for number, cells in enumerate(_cells(box)):
        visible = [index for index, cell in enumerate(cells) if not cell[0].isspace()]
        if visible and cells[visible[0]][0] == BOX_SIDE:
            visible.pop(0)
        if visible and cells[visible[-1]][0] == BOX_SIDE:
            visible.pop()
        if number == 0 and visible and cells[visible[0]][0] in PROMPT_MARKS:
            visible.pop(0)
        for place, index in enumerate(visible):
            char, faint, inverse = cells[index]
            if faint:
                continue
            following = visible[place + 1] if place + 1 < len(visible) else None
            if inverse and following is not None and cells[following][1]:
                continue
            return False
    return True


def squeeze(text):
    """The text without white space and box sides, so that a wrapped line still matches."""
    return "".join(text.split()).replace("│", "")


def _wait_for(session, test, seconds):
    """Poll until the input box passes test; return False at the time limit or when
    the session ends.

    Only a screen with the normal input bar shows the input box. Claude Code redraws
    its whole screen, and a capture in the middle of a redraw can be blank or half
    drawn; its box would look empty, and an empty box would pass for a submitted text.
    Such a screen proves nothing, so the poll goes on.
    """
    deadline = time.monotonic() + seconds
    while True:
        screen = capture(session)
        if screen is not None and has_input_bar(screen) and test(input_box(screen)):
            return True
        if screen is None or time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def deliver(session, text, pause=PAUSE_SECONDS, check=CHECK_SECONDS):
    """Type one line into the session and submit it; return None, or why it failed.

    Nothing is sent while the input bar is missing, because Enter would answer a dialog.
    The text has to show in the input box before Enter goes, and to leave it after;
    both are read only from screens that show the input bar.
    """
    if "\n" in text or "\r" in text or not text.strip():
        raise ValueError("deliver takes one line of text")
    screen = capture(session)
    if screen is None:
        return f"session {session} does not exist"
    if not has_input_bar(screen):
        return f"session {session} shows no input bar"
    whole, tail = squeeze(text), squeeze(text)[-TAIL:]
    typed = tmux("send-keys", "-t", f"={session}:", "-l", "--", literal(text))
    if typed.returncode != 0:
        return f"tmux send-keys failed: {typed.stderr.strip()}"
    if not _wait_for(session, lambda box: whole in squeeze(box) or PASTED in box, check):
        return "the text did not reach the input box"
    time.sleep(pause)

    def gone(box):
        return tail not in squeeze(box) and PASTED not in box

    for attempt in range(ENTER_TRIES):
        if attempt:
            # Enter goes again only to a screen with the input bar: the first Enter may
            # have been submitted and a dialog come up, and that dialog would take a
            # second Enter as its answer.
            screen = capture(session)
            if screen is None:
                return f"session {session} does not exist"
            if not has_input_bar(screen):
                return f"session {session} shows no input bar after Enter"
            if gone(input_box(screen)):
                return None
        tmux("send-keys", "-t", f"={session}:", "Enter")
        if _wait_for(session, gone, check):
            return None
    return "the text stayed in the input box after Enter"
