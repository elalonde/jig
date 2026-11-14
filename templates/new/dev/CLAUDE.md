# @PROJECT@: rules for every session

This file has two parts. The common part comes from jig and changes only
with jig. The project part, at the end, is written by the orchestrator.

## Accounts and roles

The same split applies to every project, also to a project without a reference:
`~/ws/ref/@PROJECT@` is then empty. The split holds under every project profile:
a project never moves the spec writer into the implementer account.

| Role | Account | Reads | Writes | Never |
| --- | --- | --- | --- | --- |
| Orchestrator | @ORCH@ | the `-dev` repo, public sources, the jig change log, its inbox, the screen that `jig wait` prints, which, with the clean room on, is never a screen of a session in the @SPEC@ account | prompts, blocks, `impl/orch/`, the kickoff record, answers in `QUESTIONS.md`, briefs, the spec-phase summary and timing rows of `specs/00-status.md`, `CLAUDE.md`, `terms.txt`, `impl/log/<batch ID>-summary.md`, `requests/<ID>.md` in `jig-dev` | product code, spec text, the reference, a tag |
| Spec writer | @SPEC@ | the reference (`~/ws/ref/@PROJECT@`, mode 700), `-dev`, the product's vector directory, public sources | `specs/`, vectors and captures, Q-n entries; in the product, the product's vector directory and no other file, with the ledger entry of each such product commit | product code |
| Implementer | @IMPL@ | approved specs, `CLAUDE.md`, the product repo; public docs only when the clean room is off | product code, tests, tools, IQ-n entries, reports | the reference, an edit in the product's vector directory |

A reviewer is a fresh session in an existing account, at the model one
family above the writer. The spec review runs as @SPEC@; the phase-close
code review runs as @IMPL@. A reviewer writes a findings file and edits
nothing else.

## Clean-room rules

The kickoff record has a section "Project profile" that holds the project's
switches, among them `clean room` (`on` or `off`). Rules 2 and 3, and the first
part of rule 1 (the orchestrator never reads `~/ws/ref`), apply when the project
profile turns the clean room on. Rules 4 and 5, and the second part of rule 1
(ssh keys only through jig), apply in every project. The permission wall is
`~/ws/ref` in @SPEC@ at mode 700; the rest is rules in `CLAUDE.md` and the prompt
template. The Never column of the table and the wall hold in every project; the
switch changes only the rules for text that comes from the reference. When jig
can read no value for `clean room`, `jig wait`, `jig cat` and
`jig validate prompt` act as with the clean room on.

1. The orchestrator never reads `~/ws/ref`, and uses its ssh keys into the
   worker accounts only through jig (`jig start`, `jig wait`, `jig send`,
   `jig close`, `jig cat`).
2. A finding cites the reference by `file:line` and never quotes it.
3. A prompt names no path under `~/ws/ref` and no reference-derived text; the
   orchestrator never copies reference-derived text into a prompt.
4. The implementer reads a spec document at its approved tag, which the
   prompt names, in `~/ws/src/@PROJECT@-dev-approved/`.
5. Excluded sources named in the kickoff record stay excluded after the
   reference is archived; `terms.txt` and lint A carry them.

## Unit rules

- A unit is one fresh session with one prompt, one ID and one committed
  report. Never `--resume`, never compact. A stopped unit restarts as a fresh
  session with the same prompt and ID.
- Work from the repo directory, the -dev clone `~/ws/src/@PROJECT@-dev`. The
  product clone is its sibling, `~/ws/src/@PROJECT@`; no clone is inside
  another.
- A path in a prompt, a report or this file starts at the repo name, because
  several repos are open at once: `@PROJECT@-dev/specs/...` is a file of the
  -dev clone, and `@PROJECT@/...` is a file of the product clone.
- A command run in the root of the -dev clone names the product clone as
  `../@PROJECT@/`, for example `make -C ../@PROJECT@ check`.
- Read by section: grep first, then read around the match. Never read a full
  log, build output or test output: write it to
  `~/ws/reviews/@PROJECT@/<ID>-*.txt`, decide from the exit code, and read
  only the lines you need.
- After each step, write `~/ws/reviews/@PROJECT@/<ID>-progress.md`. A fresh
  session with the same prompt continues from the first step not marked done.
- Start no new step above about 250k tokens of context. At about 280k, stop
  after the last complete pushed commit, with a clean tree.
- The report is `impl/log/<ID>-report.md` in the form of jig's report
  template. Commit it alone with `<ID>-prompt.md` and, when they exist, the
  progress and findings files, with the subject exactly `log: <ID>`, and
  push.
- An implementer never decides a difference in behavior. It writes IQ-n in
  `QUESTIONS.md` (spec reference, problem, options, recommendation); when the
  gap blocks the unit, it commits what is complete and reports
  `NEEDS SPEC: IQ-n`.
- A push to `-dev` that is refused: fetch, rebase the unpushed commits once,
  push again. A conflict, or a second refusal, is an escalation.
- Never force-push, rewrite history, or delete tags or releases.
- Every commit in every repo carries the Co-Authored-By trailer for Claude.

## Product repo rules

- Comments read like a human maintainer's: the useful why, in plain words.
  No IDs, hosts, accounts, process words or reference citations. Every
  comment follows the comment standard (below).
- Test names are plain words and carry no control ID.
- Lint A (`tools/lint-a.sh`) runs in `make check` and reads comments and
  docs for the patterns of the first bullet. In comments and docs only, it
  also refuses wording that sends the reader to another document instead of
  explaining: the section sign, a section number, a pointer to a rule above
  or below, a bare appeal to what is required, and a step or case number
  that labels a line. `lib/lint_a.py` holds the exact patterns. The commit-msg
  hook checks messages with the patterns of the first bullet only, not with
  these, and allows only the trailers `Co-Authored-By` and `Claude-Session`.
  `make check` installs the hook.
- Product commit subjects carry no unit ID.
- The private terms are in `terms.txt` in this repo and never go into the
  product.

## Comment standard

Every comment in the product is written for this reader: a competent
maintainer who has never seen the specs, the process or the `-dev` repo.

- a. Full sentences. The purpose first, then the detail.
- b. Explain a project term at its first use in a file, or name the item
  that defines it.
- c. Give the reason for anything a reader would not expect: which failure
  it prevents.
- d. A test comment says the situation it sets up and the result it
  expects.
- e. No comment that only repeats the item's name. No comment is better
  than a weak one.
- f. Use code identifiers or plain words, never the spec's names for
  operations or states.
- g. No rule IDs, spec sections or process words. The link from a rule to
  the code lives in the `-dev` repo.

Every prompt whose first line names the implementer account names this
section on its ROLE line, and `jig validate prompt` refuses such a prompt
that does not. The product source holds no traceability: the link from a
rule to the code is the trace in the `-dev` repo.

## Branches and tags

- One branch per version line (`0.1`, `0.2`, `1.0`), with the same names in
  both repos. `BRANCH` in `~/.config/jig/@PROJECT@.conf` names the line that
  units start from, and it changes only between batches. A patch goes on the
  current line; a minor or major on a new line.
- A spec document is approved only by a tag on origin that Eric makes
  (`spec-pN`, `spec-pNrM`, `impl-pN`, `kickoff-rN`). Drafts between tags do
  not bind, except an IQ answer, from its commit until the next tag.

## Checks

`checks.tsv` names every check: name, command, account, time cap in seconds.
A unit runs every check its prompt names through `jig run-check @PROJECT@
<name>`, which runs the row within its time cap and writes the exit code to
`~/ws/reviews/@PROJECT@/<ID>-<name>.exit`. The unit writes the placeholders
`checks: none` and `context: none` in its report; `jig report` fills them from
those files and from the context readout. The model writes no measured value of
`checks:` or `context:`.

A project with a vector set has the row `recompute`, in the @SPEC@ account, with
the command `jig recompute @PROJECT@ <product vector directory>`. The product
vector directory is the directory of the product where CI reads the vectors, as a
path from the root of the product clone. Only the spec writer writes it, in a
product commit that changes no other product file; the implementer never edits
it, and `jig report` refuses an implementer report whose commits touch it. A unit
whose commits change a vector, a recompute script or an input of a script makes
the copy of the vectors it changed, then runs the row `recompute` after its last
such commit, and the row must exit 0. The phase gate runs every row except
`recompute`.

## Recompute list

The scripts that recompute test vectors, and what each one recomputes. The
scripts are the files in `vectors/recompute/` of the `-dev` repo; no tool reads
this list. None yet: when a vector set arrives, the orchestrator adds a line from the report
of the spec unit.

## Tool list

- `make check` in the product: dependencies, hook install, tests, lint A.
- `make check` in `-dev`: the sidecar tests, which run `jig trace check`.
- jig, run by the orchestrator: `jig start`, `jig batch`, `jig status`,
  `jig wait`, `jig cat`, `jig send`, `jig report`, `jig close`, `jig hold`,
  `jig unhold`, `jig orch`, `jig ctx`, `jig validate prompt | report`,
  `jig which`.
- jig, run by a unit: `jig run-check` for each check its prompt names,
  `jig validate report` on its report, and `jig trace gen` and
  `jig trace form` for the trace entries and the ledger entry of its commits.
- `jig recompute`, the command of the row `recompute`, run by `jig run-check` in
  the @SPEC@ account.
- `jig trace coverage`, the command of the row `coverage` of a project with
  `specs/ids.tsv`, run by `jig run-check` in the @SPEC@ account. The spec unit
  that writes the first `specs/ids.tsv` adds the row.
- `jig run`, run by Eric as the owner, with the line that the orchestrator
  prints.
- `jig tag`, run by Eric in the tag block.

## Project part

No project rules yet. The orchestrator writes them here after the kickoff:
the kickoff rules that bind every session, the project profile, the reference
(or that the project has none), and the excluded sources.
