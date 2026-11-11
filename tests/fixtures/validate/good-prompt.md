# demo  paste into: jig-impl-demo (implementer, ~/ws/src/jig-dev)
UNATTENDED: Eric is not watching. Ask no questions; take the most
  reasonable choice and state it in the report.
MODEL CHECK: this unit needs Opus. If not: report first line
  "NEEDS ERIC: model is <actual>, not Opus", commit, push, stop.
EFFORT: high, set by jig start. Risk at a lower level: a missed case.
ROLE: implementer. Rules: CLAUDE.md, the section Comment standard.
  Spec tag: spec-p0. Prohibited sources: none. Never force-push,
  rewrite history, or delete tags or releases.
STATE: the previous unit is closed; its report is on origin; the premise
  check verifies it.
READ: the spec, the part on the prompt form.
  DO NOT READ: impl/log/ except the previous report; transcripts; full logs.
AUTHORITY: none beyond the normal rules.
PREMISE CHECK: make -C jig check exits 0. If the problem does not exist:
  report "OK: no change needed", change no product file.
REQUIREMENTS:
  1. Add the demonstration.
  Change class: minor.
PROOF: P1 the demonstration passes; P2 its output shows it ran; P3 a seeded
  break fails its test; P4 local checks exit 0, trees clean.
SESSION ECONOMY: grep first; read by section; cut -c1-200; long output to
  ~/ws/reviews/jig/demo-*.txt; pass or fail from exit codes; the full suite
  once per product commit, timed. Start no new step above 250k tokens; stop
  at 280k after the last complete pushed commit, with a clean tree.
STEPS: 1 premise; 2 the demonstration; 3 commit, push; 4 report, committed
  alone as "log: demo", pushed.
ESCALATION: report first line "NEEDS ERIC: <reason>" or "NEEDS SPEC: IQ-n",
  commit "log: demo", push, stop, for: a failed premise check.
REPORT: jig-dev/impl/log/demo-report.md, in the form of templates/report.md.
# end demo
