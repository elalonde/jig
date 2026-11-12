# jig change log

One entry for each version of jig, newest first. Each entry says what changed,
which files or formats changed, the migration steps for a project or a host that
moves to the version, and who requested the change. `jig changelog --since
<X.Y.Z>` prints the entries after the version a project is pinned to.

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
