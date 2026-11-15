# jig change log

One entry for each version of jig, newest first. Each entry says what changed,
which files or formats changed, the migration steps for a project or a host that
moves to the version, and who requested the change. `jig changelog --since
<X.Y.Z>` prints the entries after the version a project is pinned to.

## 0.7.0

What changed: `jig start` now sets `core.hooksPath` of a product clone to
`hooks` when the product tracks files in `hooks/` and no git config scope sets
the value yet. Such a product's own `make check` sets the same value at its
first run, and from then on git no longer runs a hook in `.git/hooks`, so a hook
that `jig start` had put there would stop the term check before a commit
without a word. The value is set in the local config of the clone before the
hook path is read; a value from any scope stays as it is, and a refused start
keeps the value it set. A pre-commit hook at the hook path that the product
tracks belongs to the product: `jig start` leaves it alone, writes no hook there
and adds nothing to the exclude file. An untracked hook there that is not jig's
is refused, as before.

`jig wait <project> <ID> --expected <minutes>` gives a running unit a new
expected time. It puts the minutes, a whole number above 0, into the file
beside the unit's lock in the place of the old value, then waits as without the
option. Every wait that already runs reads the file again at each poll, so the
timeout, three times the expected time since the session started, moves for
them too, and a running `jig batch` follows the new value with no change of its
own. The file is written whole under another name and then renamed, with mode
664, so a reader never sees half a value; a file that holds no valid value keeps
the last one. With `--expected` and no lock for the unit, `jig wait` refuses and
writes nothing, even when the report is already on origin.

The product clone inside the `-dev` clone, the place of the releases before
0.6, is no longer found by any tool, by jig's pre-commit hook or by the
dispatcher `jig`. `other_clone` in `lib/jig_clones.py` looks only beside the
clone, the hook looks for the `-dev` clone only beside the product clone, and the
dispatcher takes the project from a product clone only when its `-dev` clone is
beside it. A product clone left inside a `-dev` directory still runs the
version that project pins, because the dispatcher takes the project from the
`-dev` directory that holds the working directory first.

Heavy checks: a row of `checks.tsv` may have an optional fifth column, its
weight, `heavy` or `light`; a row with 4 fields, or with an empty fifth field,
is light. Several projects share one host, and a heavy row, such as a full
build with every test, uses all of its cores; when too many run at once, each
runs slower and can pass its time cap with correct code. So the host config
`/srv/jig/host.conf` may set `HEAVY_SLOTS`, the number of heavy rows that may run
at once on the host; with no such key there is no limit. A heavy row in
`jig run-check` first takes a free slot, one of the files `1` to `<n>` in the slot
directory `/run/jig/heavy`, by an exclusive lock that the kernel frees when the
process ends, however it ends. While every slot is held it prints one line that
names the project, unit and row of each holder, tries again every 5 seconds, and
prints the slot it took after the wait. The wait does not count toward the
row's time cap: the cap starts when the command starts, and the output gains a
line `wait: <n>s` before the wall line. A command that runs while its process
tree holds a slot gets `JIG_HEAVY_SLOT` in its environment, and a heavy row
inside it runs at once, since waiting for a slot that its own tree holds would
wait for ever. A host config that exists but cannot be read, and a value of
`HEAVY_SLOTS` that is not a whole number of 1 or more, refuse a heavy row with one
line; a light row does not read the host config.

`jig run-check` now handles SIGTERM, SIGHUP and SIGINT for every row, light or
heavy: it ends the whole process group of the command, frees its slot and exits
128 plus the signal number. A stopped run writes no exit file, because it has no
result. jig's pre-commit hook does the same for its term check: a heavy `terms`
row waits for a slot there too, and a signal while it waits stops the hook
without a slot. The hook keeps its own copy of the row reader and the slot
code, because it cannot import from jig's tree; one set of tests runs on both.

`jig release prepare` now reads every row of `checks.tsv` with the same reader as
`jig run-check`, cap and weight included, also a row of another account: a bad
cap on a row of any account now refuses, where 0.6.0 checked only the rows of
the implementer account. A heavy row may wait for a slot during prepare, and the
lines about the wait are printed as they come.

`jig new` refuses a lone product clone, one with no `-dev` clone beside it, with
one line that now also says how to recover: when a stopped `jig new` left it,
remove it and run `jig new` again.

Files and formats: new: `lib/jig_checks.py`, the row reader of `checks.tsv` and
the slot code. New formats: the optional fifth column of `checks.tsv`; the key
`HEAVY_SLOTS` in `/srv/jig/host.conf`; the slot directory `/run/jig/heavy`, mode
2775 with the group `jig` and no sticky bit, which the first heavy run makes when
it is missing, and its slot files, mode 664, each with one tab-separated line of
its last holder: project, unit, row, account, start and process ID; the
environment variable `JIG_HEAVY_SLOT`. Changed: the output of `jig run-check`
(the wait lines and the line `wait: <n>s`) and its exit codes on a signal; the
file of the expected minutes beside a unit's lock, which `jig wait` now
rewrites, mode 664. Changed files: `bin/jig`, `hooks/pre-commit`,
`lib/jig_clones.py`, `lib/jig_units.py`, `jig-new`, `jig-privacy`,
`jig-recompute`, `jig-release`, `jig-report`, `jig-run-check`,
`jig-start-worker`, `jig-terms`, `jig-trace`, `jig-upgrade` and `jig-wait`,
`README.md`, the `Makefile`, and the template `CLAUDE.md` of the `-dev` clone of
`jig new`, whose section "Checks" has one new sentence on the weight column and
the heavy slots. The formats of the conf files, reports and the trace are
unchanged.

Migration: a host and its projects move from 0.6.0 to 0.7.0 in this order, and
no unit of a project runs before its first `jig start` on 0.7.0. A project still
on 0.5.1 or earlier first takes the steps of the 0.6.0 entry.

1. Before the upgrade, check in every account that the clones of each project
   are siblings, the product clone `<project>` beside `<project>-dev` and not
   inside it. 0.7.0 no longer finds a product clone inside the `-dev` clone, so
   a project whose clones are still nested stops working with it. Move such
   clones first, with the move and the `-dev` commit of the 0.6.0 Migration.
2. As the orchestrator account, run `jig upgrade 0.7.0`. It installs 0.7.0
   under `/opt/jig/0.7.0/` and points `/opt/jig/current` to it.
3. For each project, run `jig upgrade <project> 0.7.0` as the orchestrator
   account. It sets `JIG_VERSION=0.7.0` in the conf file of the project in each
   role account, and refuses while a unit holds a lock. Check in each account
   that the conf file of the project reads `JIG_VERSION=0.7.0`.
4. Before the first `jig start` of a project on 0.7.0, run
   `git ls-files -- hooks/` and `git config core.hooksPath` in the product
   clone of each worker account. When the first prints a path and the second
   prints nothing, that start sets `core.hooksPath` to `hooks`, and git then runs
   the pre-commit hook in `hooks/` of the product clone, not the one in
   `.git/hooks`. If the product tracks `hooks/pre-commit`, that file is the
   product's, `jig start` writes no hook, and jig's term check before a commit
   runs only if the product's own hook runs it; if an untracked pre-commit hook
   that is not jig's sits there, the start is refused. Decide what each clone
   needs from that result before the start.
5. Take the new sentence of the section "Checks" of the template `CLAUDE.md`,
   `/opt/jig/0.7.0/templates/new/dev/CLAUDE.md`, into the common part of the
   project's `CLAUDE.md`, in one `-dev` commit, pushed. A project made before
   0.7.0 does not get it by itself, and a unit that meets a waiting row should
   find the rule in its own `CLAUDE.md`.
6. A product that holds a copy of `lib/jig_clones.py`, which `jig new` or the
   0.6.0 Migration put there, takes the file
   `/opt/jig/0.7.0/lib/jig_clones.py` in its next product commit, with its
   ledger entry, so that no copy still looks at the nested place. Nothing breaks
   before that: the copy only finds `terms.txt` for lint A, and the place beside
   wins there.
7. Add the fifth column to a row of `checks.tsv` only after every account of
   the host runs 0.7.0 for that project, each worker account has had a
   `jig start` on 0.7.0, which writes the 0.7.0 hook into its product clone, and
   the project's own tools that read `checks.tsv` accept a fifth field. 0.6.0
   refuses a row with 5 fields in `jig run-check`, `jig release` and the hook, so
   a weight column added too early makes every check refuse.
8. Optional, for a host: set `HEAVY_SLOTS=<n>` in `/srv/jig/host.conf`, a whole
   number of 1 or more, and add the line `d /run/jig/heavy 2775 root jig -` to
   the tmpfiles config of jig, `/etc/tmpfiles.d/jig.conf`, so that the slot
   directory exists from boot with the group `jig`; it has no sticky bit, so
   that an account can open a slot file that another account made. Check that
   `/srv/jig/host.conf` can be read by every role account, because a host config
   that cannot be read refuses every heavy row.
9. Run a heavy row as a background command in a unit, not as a foreground tool
   call, because its wait for a slot has no limit and a foreground call can time
   out. A heavy `terms` row makes every commit wait behind the full builds of the
   host, so keep that row light.

Requested by: Eric; the heavy checks: a feature request of another project.

Units: 2 minor, 2 patch, 4 none

## 0.6.0

What changed: the two clones of a project in an account, the product clone and
the `-dev` clone, are now siblings: they sit side by side in one directory, the
product clone named after the project and the `-dev` clone with the same name
and `-dev` added. No clone is inside the other. Releases before 0.6 kept the
product clone inside the `-dev` clone; that place still works, so a project
whose clones have not moved yet keeps running, but only when no product clone
sits beside the `-dev` clone. Once the clone beside it exists, a leftover copy
inside the `-dev` clone is never used.

One function, `other_clone` in the new module `lib/jig_clones.py`, finds the
other clone of a project: the product clone of a `-dev` clone, and the `-dev`
clone of a product clone. It looks beside the clone first and at the old
nested place only when nothing is beside it. `jig trace`, `jig terms`,
`jig privacy`, `jig recompute`, `jig report`, `jig release`, `jig upgrade`,
the hook install of `jig start` and lint A all use it, and a missing clone is
one line on stderr. `jig start` passes the text of the module to the worker
side of the start as an option, because that side runs from standard input and
imports only the standard library. Lint A finds
`terms.txt` in the `-dev` clone that `other_clone` names, unless `LINT_TERMS`
names another file.

jig's pre-commit hook and the dispatcher `jig` have their own copy of the same
rule, because the hook is copied into clones of accounts that cannot read jig's
tree, and the dispatcher must find the project before it knows which installed
tree to load. The hook finds the `-dev` clone beside the product clone, and its
`checks.tsv`; the dispatcher takes the project from a product clone beside its
`-dev` clone, as it already did from a `-dev` clone. One table of layouts in
the tests runs on the function, the hook and the dispatcher, so the three
copies stay equal.

The `--add-dir` list that `jig start` gives a session holds the product clone
for the spec writer, the implementer and the reviewer, because the product clone
now sits outside the repo the session works in; an account without a product
clone gets no entry for it. The orchestrator's list does not hold it.

`jig new` makes the two seed clones as siblings, both built in one scratch
directory and moved into place with the product clone first, so that an
existing `-dev` clone always means that both are there and a rerun keeps them.
When a directory already sits at the place of the product clone with no `-dev`
clone beside it, `jig new` refuses with one line that names the directory and
writes nothing, rather than overwrite or adopt a tree it did not make. Its
templates follow the new layout: the `-dev` clone ignores no product
directory, the rows of `checks.tsv` and the `Makefile` name the product clone
beside the `-dev` clone, the `CLAUDE.md` of a new project states the path rule
of the two clones, and the block, the prompt template and the README do the
same. `jig new` copies `lib/jig_clones.py` into the product with the other
files of lint A.

Files and formats: new: `lib/jig_clones.py`, which the product of a new
project also gets with lint A. Changed: the layout of the clones in every
account, as above; `bin/jig`, `hooks/pre-commit`, `lib/lint_a.py`,
`lib/jig_dirs.py` (the `--add-dir` list), `lib/jig_trace.py`, `jig-new`,
`jig-privacy`, `jig-recompute`, `jig-release`, `jig-report`, `jig-start`,
`jig-start-worker`, `jig-terms`, `jig-trace` and `jig-upgrade`; the templates
of `jig new` (`README.md`, `block.sh.in`, and in the `-dev` clone `CLAUDE.md`,
`Makefile`, `checks.tsv` and `gitignore`) and the prompt template
`templates/prompt.md`. The formats of the conf files, `checks.tsv`, reports
and the trace are unchanged.

Migration: a host and its projects move from 0.5.1 to 0.6.0 in this order, with
no unit running other than the one of step 3. A project still on 0.5.0 first takes the steps of the 0.5.1
entry, among them the rename of the session file under
`~/ws/reviews/<project>/orch/` to `session-ids`.

1. As the orchestrator account, run `jig upgrade 0.6.0`. It installs 0.6.0
   under `/opt/jig/0.6.0/` and points `/opt/jig/current` to it.
2. For each project, run `jig upgrade <project> 0.6.0` as the orchestrator
   account. It sets `JIG_VERSION=0.6.0` in the conf file of the project in each
   role account, and refuses while a unit holds a lock. Check in each account
   that the conf file of the project reads `JIG_VERSION=0.6.0`. 0.6.0 still
   finds the nested product clone, so the projects run as before until the
   move below.
3. For each project whose product holds a copy of jig's lint A, which `jig new`
   put there, copy `lib/lint_a.py` and the new `lib/jig_clones.py` from
   `/opt/jig/0.6.0/` into the product's `lib/`, in one product commit with its
   ledger entry, made in a unit of the implementer account and pushed.
   The lint A of 0.5.1 reads `terms.txt` only from the directory that holds the
   product clone, which is no longer the `-dev` clone after the move. jig's own
   product has these files from 0.6.0 on.
4. Move each project's clones to siblings, one account at a time, in this
   order: the orchestrator account, then the spec writer account when it holds
   a product clone, then the implementer account last. In each account, pull
   both clones, then, in the directory that holds the `-dev` clone, move the
   product clone `<project>` out of the `-dev` clone into that directory under
   the same name, so that it sits beside `<project>-dev`. Check that git
   status is clean in both clones. A moved clone keeps its git config, with
   `core.hooksPath` where it is set; in a worker account the next `jig start`
   writes the 0.6.0 hook into the product clone.
5. In each project's `-dev` clone, make one commit, pushed, that changes every
   path that names the product clone inside the `-dev` clone to the clone
   beside it: the line of the product directory in `.gitignore` goes; the
   commands of the rows of `checks.tsv` and the `Makefile` that run in the
   product clone name it with `../` before its name; the comments and the
   `CLAUDE.md` rules that state the old place follow. For jig these are the
   `.gitignore` line of the product, the rows `make-check`, `mutants`,
   `ci-green`, `real-start` and `privacy` of `checks.tsv`, and the
   `jig trace check` line of the `Makefile` with its comment. Then pull that
   commit in each account. Between the move and this commit, a row that runs
   in the product clone fails, so no unit runs between them.
6. Take into each project's `CLAUDE.md` the path rule of the two clones from
   the template of `jig new`, when the project wants it, and name each path in
   a prompt from its repo name: `<project>-dev/...` in the `-dev` clone and
   `<project>/...` in the product clone beside it.

Requested by: Eric.

Units: 1 minor, 0 patch, 4 none

## 0.5.1

What changed: the supervisor `jig orch` now keeps the orchestrator's tmux
session across a rollover and across `jig orch <project> stop`, so that a tmux
client that is attached to the session stays attached.

The supervisor sets the tmux option `remain-on-exit` on the session, so the
pane stays, dead, when Claude Code exits. The session counts as live only when
its pane is not dead. When the session is gone, the supervisor starts the next
Claude Code session in a new tmux session, as before; when the session is
there with a dead pane, it starts the next Claude Code session in that same
pane with `tmux respawn-pane`, and the tee of the session goes on into the same
file. `jig orch <project> stop` ends Claude Code in the pane, with SIGTERM and
SIGKILL after 10 seconds, and leaves the tmux session with its dead pane; it
never kills the session. The next `jig orch <project> start` respawns the
pane. When the loop stops after three quick exits, it also leaves the session
with its dead pane.

Before it touches the session, the supervisor tests that the session is its
own, on two IDs: the tmux session ID, which stays the same across a respawn,
and the Claude Code session ID, which it reads from the start command of the
pane. Both must equal the IDs that it wrote at its last start or respawn.
`jig orch <project> start` refuses a session that is not its own and names it;
`jig orch <project> stop` ends the loop and leaves such a session as it is;
the loop respawns nothing in such a session, writes a line to
`supervisor.log`, and stops. `jig orch <project> status` prints the state of
the pane (`live`, `dead` or `none`) and whether the session is its own.

`jig ctx` takes the later of the creation of the tmux session and the start of
the pane's process as the start of the running session, so a Claude Code
session that is respawned in the same pane does not read the context figure of
the session before it and roll over at once.

`jig ctx` compares the start of the pane's process and the time of the context
figure to a fraction of a second, so a session started again in the same pane
no longer takes the figure of the session before it as its own and exits, even
within the same second.

The `ORCHESTRATOR.md` template of `jig new` asks for a celebration of every
release of every project, a patch release too, in the console and in
`updates.log`, sized by the version change: at most 5 lines for a patch, 12
for a minor release, 30 for a major release.

Files and formats: the file `session-ids` takes the place of the file
`session` under `~/ws/reviews/<project>/orch/`. It holds, on one line, the
tmux session ID and the Claude Code session ID of the session that the loop
started or respawned last, the same two words as the old file. Changed:
`jig-orch`, `jig-ctx` and the `ORCHESTRATOR.md` template of `jig new`.

Migration: a project moves from 0.5.0 to 0.5.1 in this order, as the
orchestrator account, with the session of the 0.5.0 supervisor still running.

1. Run `jig upgrade <project> 0.5.1`. It installs 0.5.1 under
   `/opt/jig/0.5.1/` when it is missing, and pins the project to 0.5.1, so that
   the stop and the start below run 0.5.1. A stop that runs 0.5.0 ends the
   session, and with it an attached client.
2. Before the restart, rename the session file and set `remain-on-exit` on the
   running session:
   `mv ~/ws/reviews/<project>/orch/session ~/ws/reviews/<project>/orch/session-ids`
   and `tmux set-option -w -t =<project>-orch: remain-on-exit on`. Without the
   rename, 0.5.1 finds no IDs and treats the session as not its own, so its
   start refuses; the rename also keeps the 0.5.0 loop, when it ends, from
   killing the session. Without `remain-on-exit`, the pane closes when Claude
   Code exits, and the attached client with it.
3. Restart the supervisor: `jig orch <project> stop`, and then `jig orch
   <project> start`. The stop leaves the session with a dead pane, and the
   start respawns the pane. `jig orch <project> status` then prints `pane
   live, its own`.
4. In each project's `ORCHESTRATOR.md`, take the paragraph on release
   celebrations from the template of `jig new`, when the project wants it.

Requested by: Eric.

Units: 0 minor, 2 patch, 7 none

## 0.5.0

What changed: this is the first release of the line 0.5. It moves the term
check in front of every commit, replaces the lines that the supervisor typed
into the orchestrator's session with an event file, and brings the status line
of Claude Code into jig.

jig has a pre-commit hook, `hooks/pre-commit`, that runs the term check before
each commit in a product clone. It runs the command of the row `terms` of the
project's `checks.tsv` from the root of the `-dev` clone around the product
clone, within the row's time cap, and refuses the commit when that command
fails, runs out of time, or when the row is missing. Its second line is the
marker `# jig pre-commit hook`. `jig start` installs the hook in the worker's
product clone before it starts the session, when `checks.tsv` holds the row
`terms`, and removes a hook with the marker when the row is gone; `jig new`
installs it in each product clone it makes. A hook that lies inside the work
tree is added to the clone's `.git/info/exclude`, so it never becomes a product
file; a hook file that the product tracks is left alone. `jig start` refuses, and writes nothing, when another pre-commit
hook is in the way or when the hook path lies outside the product clone. Before
it looks for the hook, `jig start` sets `core.hooksPath` to `hooks` in a product
clone that tracks a directory `hooks/` and has no hooks path yet. `jig
run-check` runs the row `terms` in the implementer account and in the spec
writer account alike, because both commit to the product.

The supervisor `jig orch` no longer types anything into the orchestrator's
session. It appends each event, in the form of the line it used to type, to
the event file `events.log` in the supervisor's directory `orch/` of the
project, once and in order, also while a dialog is open. Before it starts a
session it appends the line `start <session ID>`; when that append fails, the
session does not start. An append that fails is logged once and tried again at
the next poll, in order. The session reads the file with the new watcher `jig
orch <project> events`, which prints each line after the last `start` line and
then each new line as it comes, and ends with the session. `jig orch <project>
events --old` prints once the lines between the last two `start` lines, the
events of the session before, and exits. The waiting for an empty input box
that 0.4.1 added is gone with the typing.

`jig-statusline` is the status line of Claude Code, now part of jig. It shows
two lines: the user, role, branch, model and effort; then the run time, the
5-hour and weekly use with the pace, the host's health note and the model
policy warning. It also keeps the usage file for the host's tools, names the
tmux window, and writes the context figure that `jig ctx` reads. It reads its
two host paths from the host config file `/srv/jig/statusline.conf`; the
product holds no host value. A missing or unreadable config file only leaves
out the parts it would give, and the tool always exits 0, also on input of an
unexpected shape. It runs from `/opt/jig/current/`, so one install serves every
account and project.

`jig new` sets the `statusLine` command of each account's Claude Code settings
to `/opt/jig/current/libexec/jig-statusline` and adds no hook of another tool;
a `statusLine` with another command is replaced, and the settings file from
before the first change is kept as `settings.json.bak-new`. Its readback lists
each account's `statusLine` command. `MODEL_POLICY` now comes only from the
host config `/srv/jig/host.conf`: it has no default, and `jig new` refuses a
host config without it. The host config keys `STATUSLINE` and `SESSION_LOG` are
no longer read. jig's tools no longer ship message-of-the-day lines.

`jig tag` takes the approval tags of the product stage, `spec-X.Y` and
`spec-X.YrM`, besides the four phase forms; it refuses such a tag whose `X.Y`
is not the version line that units start from. `jig start` reads both forms on
the ROLE line of a prompt and checks them on origin. A spec tag word in no
known form is refused, and `spec-1.2.3` or `spec-1.2r1.4` is no longer read as
`spec-1.2`.

`jig trace check` no longer asks for the ledger entry of the product's head
commit when that commit is a release commit, with the subject exactly `Release
X.Y.Z`. So the check passes inside the one run of `jig release prepare`; the
next unit that commits to the product writes that entry first.

Fixes found when the line was reviewed: the standing prompt of the
orchestrator says to run one watcher only, to start it again at each notice
that it ended, and to act on a replayed event only when nothing shows it as
handled; the block of `jig new` sets `core.hooksPath` to `hooks` in each
product clone it makes when the product tracks `hooks/`; an inbox file is
recorded as announced only after its line is appended, so a stop between the
two announces it once at the next start; the status line counts a model or
workspace that is not an object as absent, and treats a reset time that is not
a valid clock time as unknown; `jig start` refuses a hook path outside the
product clone.

Files and formats: new `hooks/pre-commit`, `libexec/jig-statusline`, the
event file `events.log` (one event per line, as the supervisor used to type
it, and a line `start <session ID>` before each session), and the host config
file `/srv/jig/statusline.conf` (one `KEY=value` per line, with the keys
`HEALTH_FILE`, the result file of the host's health check, and `MODEL_POLICY`,
the host's model policy file; environment variables of the same names, and
`JIG_STATUSLINE_CONF` for another file, override them). Changed
`libexec/jig-orch` (the command `events`; the settings `JIG_ORCH_QUIET` and
`JIG_ORCH_BOX_CHECK` are gone), `lib/jig_tmux.py` (the input box reader and the
styled capture are gone), `libexec/jig-start`, `libexec/jig-start-worker`,
`libexec/jig-new`, `libexec/jig-tag`, `libexec/jig-trace`,
`libexec/jig-run-check`, `templates/new/block.sh.in`,
`templates/new/README.md`, `templates/new/dev/checks.tsv` (the row `terms`),
`templates/new/dev/impl/orch/ORCHESTRATOR.md` (the watcher as the first step
after each start), `README.md` and `Makefile`. In `/srv/jig/host.conf` the key
`MODEL_POLICY` is now required, and `STATUSLINE` and `SESSION_LOG` are no
longer read. Removed `share/motd.tsv`.

Migration: a host and each project move to 0.5.0 in this order.

1. Run `jig upgrade 0.5.0`.
2. Put the key `MODEL_POLICY` in `/srv/jig/host.conf`, with the path of the
   host's model policy file, before the next `jig new`; the keys `STATUSLINE`
   and `SESSION_LOG` may be removed.
3. Run the switch block: it writes `/srv/jig/statusline.conf`, mode 644, with
   `HEALTH_FILE` and `MODEL_POLICY`, and in each account whose Claude Code
   settings name the old host status line it keeps a copy of the settings file
   with the suffix `.pre-jig` and sets the `statusLine` command to
   `/opt/jig/current/libexec/jig-statusline`. Its readback lists each settings
   file with its `statusLine` command.
4. Restart each project's supervisor, `jig orch <project> stop` and then `jig
   orch <project> start`, so that it writes the event file instead of typing.
5. In each project's `impl/orch/ORCHESTRATOR.md`, take the watcher step from
   the template of `jig new`: the first thing after each start of the session
   is one background watcher, `jig orch <project> events`, through the harness
   tool that gives a turn for each new line (in Claude Code, the Monitor tool),
   and a single read of `jig orch <project> events --old` for the events of the
   session before.
6. For the pre-commit hook, give each project's `checks.tsv` the row `terms`
   when it lacks one. A product that tracks `hooks/` needs `core.hooksPath` set
   to `hooks` in its clones; `jig start` sets it in the worker's product clone
   at the next start, and `jig new` sets it in the clones it makes.

Requested by: Eric.

Units: 5 minor, 1 patch, 16 none

## 0.4.1

What changed: the supervisor `jig orch` no longer types a line into a session
while someone is writing in it. Each line that it delivers from its queue now waits until the input box of the session
is empty and has not changed for 10 seconds; the box is looked at once a second
while the supervisor rests between polls. Waiting lines go in the order they
came, and a line held by typed text is logged once, not at every look. Faint or
grey text in the box, such as a placeholder or a suggested prompt, counts as
empty; text in the normal colour, a pasted-text or image mark, or a "!" shell
line counts as typed. A line the supervisor types itself counts as a change, so
the next queued line waits the quiet time again. Dialog handling, the rules for a
line that fails, `deliver` and `jig send` are unchanged.

Files and formats: changed `libexec/jig-orch` (new settings `JIG_ORCH_QUIET`,
the seconds the box must stay empty and unchanged, default 10, and
`JIG_ORCH_BOX_CHECK`, the seconds between looks, default 1) and
`lib/jig_tmux.py` (a styled screen capture and the reading of the input box). No
file format changes.

Migration: none for a project. A host moves to this version with
`jig upgrade 0.4.1` and restarts the supervisor so that the new loop runs.

Requested by: Eric.

Units: 0 minor, 1 patch, 2 none

## 0.4.0

What changed: jig releases itself and other projects. `jig release <project>
prepare` makes the release commit of the line (change log entry with its unit
counts, version bump, push), waits for green CI on that commit, proves it from a
clean clone with an offline build of the source archive, checks the release
files, runs `jig terms check`, `jig privacy scan` and every row of `checks.tsv`,
and stages the release files with `SHA256SUMS`. A rerun after a failed step
works on the same release commit. `jig release <project> publish` tags the
release in both repos, makes the GitHub release, reads it back without a login
and moves master to the line; `publish --dry-run` prints each action and changes
nothing; `verify <version>` compares the published files with the staged ones.
`jig terms check` looks for the private terms in the product, its commit
messages and built files; `jig privacy scan` looks for serials, host names,
addresses, UUIDs and MAC addresses in fixtures and captures. `jig upgrade`
downloads a release, checks it against its `SHA256SUMS` and installs it under
`/opt/jig`; `jig changelog --since <version>` prints the entries after a
version. The product gets the make targets `version-bump`, `package`,
`package-check` and `source-archive`, and `install.sh` also installs from a
package file. `jig new` gives a new product a CI workflow that runs
`make check` on every push and makes the shared checks directory; `jig run-check`
copies the exit code of a row of another account there, where prepare reads it;
`jig orch` sends the reminders of the Open items of `state.md` that come due.
`tools/ci-green --wait <seconds>` waits for the CI jobs of a commit. This is the
first version published from the new public repository, whose history starts
with one import commit.

Files and formats: new `libexec/jig-release`, `libexec/jig-terms`,
`libexec/jig-privacy`, `libexec/jig-upgrade`, `libexec/jig-changelog`,
`lib/jig_package.py`, `lib/jig_push.py`, `tools/push-on-token.sh`, `VERSION`,
`CHANGELOG.md`, `README.md`, `THIRD-PARTY-NOTICES` and `SOURCE-OFFER`. Changed:
`Makefile`, `bin/jig` (changelog and upgrade always run from current),
`tools/install.sh`, `tools/ci-green`, `lib/lint_a.py`, `libexec/jig-new`,
`libexec/jig-run-check`, `libexec/jig-orch` and the templates of `jig new`, among
them `.github/workflows/ci.yml` and the `Open` lines of `state.md` for the token
and the logins. A release is staged in `~/ws/reviews/<project>/release/<X.Y.Z>/`.

Migration: a host makes `/srv/jig/checks/<project>/` with the group `jig`, mode
2775, before the first unit that runs a row of another account. A project that
releases with jig gives its product the four make targets and a CI workflow, and
the implementer account gets the project's token file and the push URL that
`tools/push-on-token.sh` sets. A host moves to this version with
`jig upgrade 0.4.0`.

Requested by: Eric.

Units: 6 minor, 2 patch, 26 none

## 0.3.0

What changed: records and their trace. `jig trace` ties the record IDs of a
project's specs to the lines of its product: `gen` writes the trace entries,
`check` checks them against the product and the specs, `form` writes the ledger
entry of a commit, and `coverage` runs the coverage checks. `jig tag` makes an
approval tag after its checks. Lint A reads a pattern file of the product, so
that a test name carries no control ID and a test string no process ID.
`jig report` also refuses a report when a product commit made during the unit
and not named in the report touches the product's vector directory.

Files and formats: new `libexec/jig-trace`, `libexec/jig-tag`,
`lib/jig_trace.py`, `share/trace-diff.gitattributes` and
`tools/lint-a-patterns.txt`, the pattern file, with its template in `jig new`.
The `-dev` Makefile of a new project runs `jig trace check` as its check.
Changed: `lib/lint_a.py`, `lib/jig_ids.py`, `libexec/jig-report`,
`libexec/jig-orch` and the templates `CLAUDE.md`, `ORCHESTRATOR.md` and
`README.md` of `jig new`.

Migration: in the product, add `tools/lint-a-patterns.txt` from the template of
`jig new` to turn on the checks of test names and test strings; a product
without it gets neither check. In the `-dev` repo, make `make check` run
`jig trace check`, as the template does.

Requested by: Eric.

## 0.2.0

What changed: the orchestrator runs batches of units on its own. New commands
`jig batch`, `jig close`, `jig report`, `jig run-check`, `jig hold` and
`jig unhold`, `jig send`, `jig ctx` and `jig recompute`. `jig wait`, `jig cat`
and `jig validate prompt` read the clean room switch from the project profile of
the kickoff record. The client functions `jigin`, `jigblock` and `jigshot`
take the host from `JIG_HOST`. Each installed version carries the owner's
preferences, `prefs.md`.

Files and formats: new `libexec/jig-batch`, `jig-close`, `jig-close-worker`,
`jig-ctx`, `jig-hold`, `jig-unhold`, `jig-recompute`, `jig-report`,
`jig-report-worker`, `jig-run-check`, `jig-send` and `jig-send-worker`; new
`lib/jig_context.py`, `lib/jig_hosts.py`, `lib/jig_profile.py` and
`lib/jig_push.py`; new `prefs.md` and the template `impl/orch/handoff.md`.
New host file `/srv/jig/hosts.toml`, the host list. The kickoff record has a
section "Project profile". Changed: `jig new`, `jig orch`, `jig start`,
`jig validate`, `jig wait`, `jig cat`, `share/jigfn.sh`, the templates of the
prompt, the report, `CLAUDE.md`, `ORCHESTRATOR.md` and `state.md`.

Migration: write `/srv/jig/hosts.toml` with one `[[host]]` table for each
host. Add the section "Project profile" with the `clean room` switch to the
kickoff record of each project; without a value the tools act as with the clean
room on. Add `impl/orch/handoff.md` to each `-dev` repo from the template. On
the laptop, set `JIG_HOST` before the client functions are sourced. Set the
motd lines of the new tools.

Requested by: Eric.

## 0.1.0

What changed: the first version. The dispatcher `jig` and the commands
`jig new`, `jig start`, `jig status`, `jig wait`, `jig cat`, `jig orch`,
`jig run`, `jig validate` and `jig which`; lint A and the commit-msg hook;
`make check`; the CI check `ci-green`; the seeded-break runner `run-mutants`;
the install script; `push-on-token`; the client functions `jigin`, `jigblock`
and `jigshot`.

Files and formats: the tree `bin/`, `libexec/`, `lib/`, `hooks/`, `tools/`,
`share/` and `templates/`; the forms of the unit prompt and the unit report;
the project conf file `~/.config/jig/<project>.conf`; the motd lines in
`share/motd.tsv`.

Migration: none; this is the first version. Install it with
`tools/install.sh`, then link `/usr/local/bin/jig` to `/opt/jig/current/bin/jig`
and set the motd lines.

Requested by: Eric.
