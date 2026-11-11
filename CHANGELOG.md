# jig change log

One entry for each version of jig, newest first. Each entry says what changed,
which files or formats changed, the migration steps for a project or a host that
moves to the version, and who requested the change. `jig changelog --since
<X.Y.Z>` prints the entries after the version a project is pinned to.

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

Units: 6 minor, 2 patch, 17 none

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
