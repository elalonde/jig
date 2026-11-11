# jig change log

One entry for each version of jig, newest first. Each entry says what changed,
which files or formats changed, the migration steps for a project or a host that
moves to the version, and who requested the change. `jig changelog --since
<X.Y.Z>` prints the entries after the version a project is pinned to.

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
