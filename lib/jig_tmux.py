"""A Claude Code session in tmux: the dialog test and the delivery of one line.

jig wait and the supervisor (jig orch) test for a dialog the same way: the session's
normal input bar is missing for DIALOG_POLLS polls in a row. jig send
and the supervisor deliver a line the same way: the text, a pause, Enter, then
a check that the text reached the session and left its input box, because text and
Enter sent together were measured to stay unsubmitted.

tmux is the one on PATH, with its default server; a test puts its own tmux first.
"""

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


def capture(session):
    """Return the session's screen, or None when the session does not exist."""
    result = tmux("capture-pane", "-p", "-t", f"={session}:")
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
