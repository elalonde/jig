# @PROJECT@ orchestrator handoff

Written at each rollover, by the session that ends, for the session that the
supervisor starts next. Write it with `state.md`, commit both together, then
exit. The fresh session cites the commit of this file in its first hourly
update.

## Session

The time this session ended, and why: the soft context limit, the hard
context limit, or a batch boundary. No rollover yet.

## In hand

What this session was doing when it stopped: the step, the unit or brief it
concerned, and how far it got. Name each file that holds the work. A step that
is not complete is named here as not complete. Nothing yet.

## First for the next session

The actions the next session does first, in order, before any new work. Name
a file only when the next session must read it. Nothing yet: start with the
"Next units" of `state.md`.
