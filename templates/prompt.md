# <ID>  paste into: <project>-<role>-<ID> (<account>, ~/ws/src/<project>-dev)
UNATTENDED: Eric is not watching. Ask no questions; take the most
  reasonable choice and state it in the report.
MODEL CHECK: this unit needs <model>. If not: report first line
  "NEEDS ERIC: model is <actual>, not <required>", commit, push, stop.
EFFORT: <level>, set by jig start. Risk at a lower level: <one line>.
ROLE: <role>. Rules: CLAUDE.md, the sections <names>; when the
  first line names the implementer account, also Comment standard. Spec tag: <the approved tag>.
  Prohibited sources: <from the kickoff record>. Never force-push,
  rewrite history, or delete tags or releases.
STATE: <the expected state when this unit starts: the previous unit, the
  path of its report, what should be on origin>; the premise check
  verifies it.
READ: <files and sections, each path from its repo name:
  <project>-dev/... in the -dev clone, <project>/... in the product
  clone beside it; inbox files beside this prompt>.
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
  the full suite once per product commit, timed. Start no new step
  above 250k tokens; stop at 280k after the last complete pushed
  commit, with a clean tree.
STEPS: 1 premise; 2 ...; n-1 commit, push; n report, committed alone
  as "log: <ID>", pushed.
ESCALATION: report first line "NEEDS ERIC: <reason>" or
  "NEEDS SPEC: IQ-n", commit "log: <ID>", push, stop, for: <the
  escalation cases>.
REPORT: <project>-dev/impl/log/<ID>-report.md, in the form below.
# end <ID>
