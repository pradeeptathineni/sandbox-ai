# Five-task usability capture

The [protocol](../recipes/deep-usability/protocol.json) gives first-time and experienced users the same five Deep AI tasks: find a story, inspect its source, send one selected source deliberately, save and reopen a Notebook, and recover a pending review. Each task has an observable goal and asks what the person believes was sent. The protocol contains no selectors or assumed product result. Use the current supported build and an authorized session; do not run a live provider or change user data merely to fill a worksheet.

Before a session, keep the protocol unchanged and run `python3 -m harness.usability --protocol recipes/deep-usability/protocol.json --digest` to get its canonical SHA-256 for the capture. Record a pseudonymous `session_id`, exact app source revision and participant group. Keep any screen recording or notes in the authorized private owner store; the capture holds only their SHA-256 digests and kinds. Record each task as `completed`, `partial`, `failed` or `not-run`, with observed wrong turns and the person's own short answer about what was sent. For an observed human task, retain at least one artifact reference. Do not include a name, contact detail, private source text or raw recording in the portable capture.

Validate and summarize the capture without writing to the app or store:

```sh
python3 -m harness.usability \
  --protocol recipes/deep-usability/protocol.json \
  --capture /private/path/to/capture.json
```

The report preserves all five observations, reports simple outcome and wrong-turn counts, and binds the supplied protocol and capture bytes. It refuses a changed protocol, missing task, invalid group or outcome, unsupported fields and incomplete human evidence. The digest does not authenticate a participant or prove the actual send scope. Compare a person's belief with separately retained host receipts before claiming understanding. A successful validation is not a design acceptance decision; actual sessions with first-time and experienced participants and authorized review are still required.

The committed [synthetic capture](../recipes/deep-usability/synthetic-capture.json) exercises the validator with one completed, one partial and three not-run tasks. It is explicitly not a human session or visual acceptance result.
