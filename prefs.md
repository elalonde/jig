# Eric's standing preferences

These rules hold for every message that an orchestrator writes to Eric: in the
console, in a brief, and in a file that Eric reads. Eric pastes nothing.

Language:

- Write in ASD-STE100 Simplified Technical English.
- Use no metaphor and no padding.
- After a gap of about an hour, start the reply with a summary of the last
  session and the next steps.
- When Eric did not answer a question, repeat the question at the top of the
  next message, with the reason it matters.

Briefs, and every other message that gives Eric options:

- A brief asks Eric for a decision. Put one batch of questions in one message.
- Give each item its question, its context (the IDs it concerns and what each
  of them says), and its cost.
- Every question carries a recommendation.
- Put the recommendation, with its reason and sources, and the rejected option,
  with the reason it was rejected, last in the message, just before the
  `orch <ID>` line. When the message has several items, put all the
  recommendations together at the end, numbered to match the items.
- Do not ask about a choice with an obvious answer: make the choice and record
  it.
- Write each brief to `impl/orch/briefs/<ID>.md` and show it in the console.
