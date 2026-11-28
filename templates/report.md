OK[: <note>] | STOPPED: <reason> | NEEDS ERIC: <reason> | NEEDS SPEC: IQ-n
jig-report: 1
id: <ID>
class: none | patch | minor | major
commits: none | <hash> [<hash> ...]
checks: none | <name>=<exit code> [...]
proofs: P1=<exit> P2=<exit> P3=<exit; the seeded break failed> ...
start: <ISO time>  end: <ISO time>  context: none | <the readout>
next: <one line: what the next unit needs>
---
<at most 25 lines: what was done; choices taken and why; what was not
done; questions raised>
<OK means every step of the prompt is done. A unit that stops with steps
left, after its last complete pushed commit, writes STOPPED: <reason>, and the
body names the first step that is not done.>
<A unit writes none in checks: and context:; jig report fills them from the
exit files of jig run-check and from the context readout.>
