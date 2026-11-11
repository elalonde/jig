# @PROJECT@ orchestrator state

Always current, at most about 200 lines. Change a part when its facts change,
and commit this file after every unit and at every rollover. A fresh session
reads it after `ORCHESTRATOR.md`, so it holds all a session must know that is
not in the repos. Move a fact that is finished and no longer needed out of this
file; git keeps it.

## Current

- Heads: none pushed yet. `jig new @PROJECT@` made the seed repos; the block
  `new` makes the GitHub repos. Write each repo as its branch and short hash:
  `@PROJECT@-dev 0.1 1a2b3c4`, `@PROJECT@ 0.1 5d6e7f8`.
- Running unit: none. Write it as `k/n <ID>`, what it does, its start time and
  its expected minutes; `n` is the number of units in the batch.
- Last results: none. Write the last units, newest first, each as its ID, its
  status, and one line on what it changed.
- Fable sessions used this week: 0, in the week that starts on the Monday of
  the first kickoff session.

## Decisions by date

None yet. Write each decision as one line under its date: the decision, then
the rule that it makes. Form:
`2026-01-05: unit tests run on every push. Rule: a unit that adds a tool adds its test.`

## Open

Each item is one list entry and ends with the date to act on it, as
`act by 2026-01-05`, or with `no date`. The supervisor reads this part of the
file in the clone when its loop starts and every hour after, and sends the
session one line `reminder: <the item>` when the date of an item is today or
past. It sends each item once for each date, also across restarts; a new date
sends it again.

A credential item names its expiry as `expires 2026-01-12` and acts 7 days
before it: `expires 2026-01-12, act by 2026-01-05`. Until the expiry is known,
it ends with `no date`. When a credential is renewed, write the new dates.

- Eric runs the block `new` with the `jig run` line that jig new printed,
  as @OWNER@ on @HOST@. Its readback is `@SRV@/out/@PROJECT@/new.txt`.
  Act at once.
- The product push token (fine-grained, held by @IMPL@): not made yet,
  expires not known, no date.
- The claude.ai login of @ORCH@: expires not known, no date.
- The claude.ai login of @SPEC@: expires not known, no date.
- The claude.ai login of @IMPL@: expires not known, no date.

## Next units

In order. Write each as its ID, what it does, and what it waits for.

1. The kickoff with Eric, which writes `specs/00-kickoff.md`.

## Pinned jig version

Pinned: the `JIG_VERSION` line of `~/.config/jig/@PROJECT@.conf`. Write the
version here when the pin changes, with the date. A fresh session reads the
jig change log from this version on.
