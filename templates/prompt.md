# <ID>  paste into: <project>-<role>-<ID> (<account>, ~/ws/src/<project>-dev)
UNATTENDED: Eric is not watching. Ask no questions; take the most
  reasonable choice and state it in the report.
MODEL CHECK: this unit needs <model>. If not: report first line
  "NEEDS ERIC: model is <actual>, not <required>", commit, push, stop.
EFFORT: <level>, set by jig start. Risk at a lower level: <one line>.
ROLE: <role>. Rules: CLAUDE.md, the sections <names>; when the
  first line names the implementer account, also Comment standard and
  Commit message standard. Spec tag: <the approved tag>.
  Prohibited sources: <from the kickoff record>. Never force-push,
  rewrite history, or delete tags or releases.
  <In a project with reference yes: a known security defect of the
  reference is named only by its OI-n, in CLAUDE.md and in every
  record, and no record says more about it. Work on such a defect is
  defensive only: the project's own rule, written as an approved
  difference from the reference, with a test. Write no tool that is
  aimed at systems outside the project. The disclosure of the defect
  is Eric's decision. When the model refuses a request on it, do not
  send the same request again in other words: make the scope of the
  work smaller, and report NEEDS ERIC with the reason, so that the
  orchestrator writes a brief to Eric. The record of such a defect is
  never deleted.>
STATE: <the expected state when this unit starts: the previous unit, the
  path of its report, what should be on origin>; the premise check
  verifies it.
READ: <files and sections, each path from its repo name:
  <project>-dev/... in the -dev clone, <project>/... in the product
  clone beside it; inbox files beside this prompt>.
  <In a review prompt: no plain git status in a reference checkout,
  because it rewrites .git/index; git --no-optional-locks status is
  allowed.>
  DO NOT READ: impl/log/ except the previous report; transcripts;
  full logs; <project items>.
AUTHORITY: <edits outside the normal rules that this prompt allows;
  everything else stays closed>.
PREMISE CHECK: <a measurement and its expected output>. If the problem
  does not exist: report "OK: no change needed", change no product file.
REQUIREMENTS: <numbered; what, not how; what must not change>.
  Change class: none | patch | minor | major.
PROOF: P1 <positive check>; P2 <evidence that it really ran>; P3 <seeded
  break and where it must fail>; P4 <public effect>; Pn <local checks
  exit 0, trees clean>. A pass that cannot fail proves nothing.
SESSION ECONOMY: grep first; read by section; cut -c1-200; long output
  to ~/ws/reviews/<project>/<ID>-*.txt; pass or fail from exit codes;
  the full suite once per product commit, timed. After each step, run
  jig ctx; at exit 3 start no new step; at exit 4 stop after the last
  complete pushed commit, with a clean tree; a stop with steps left
  reports "STOPPED: <reason>"; at exit 2 go on, and run jig ctx again
  after the next step. At exit 3 or 4 still write, commit and push the
  report: the report is no new step, and its status is OK when every
  step before the report is done, else not OK, with the steps left.
  Wait on CI with a command that exits
  when CI ends (for jig, tools/ci-green --wait), not with a watcher
  that you must start again. A test or probe that needs tmux uses its
  own server, tmux -L <name>, never the default server. Edit files
  with the edit tool, never with a shell heredoc or with a script
  that writes files.
STEPS: 1 premise; 2 ...; n-1 commit, push; n report, committed alone
  as "log: <ID>", pushed. A push to -dev that is refused: fetch, rebase
  the unpushed commits, and push again, up to 3 rounds while git
  reports no conflict; a conflict in a rebase, or a refused push in
  the third round, is an escalation. A unit that cannot push its
  report commit also writes its report to
  ~/ws/reviews/<project>/<ID>-report.md, and stops with the text of
  its report as its last message.
ESCALATION: report first line "NEEDS ERIC: <reason>" or
  "NEEDS SPEC: IQ-n", commit "log: <ID>", push, stop, for: <the
  escalation cases>.
REPORT: <project>-dev/impl/log/<ID>-report.md, in the form below.
# end <ID>
