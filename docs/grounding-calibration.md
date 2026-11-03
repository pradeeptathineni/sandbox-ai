# Frozen blinded-review calibration

`harness.grounding_review` remains the owner of packet validation, A/B masking, closed reviewer ratings and descriptive two-reviewer comparison. `harness.grounding_calibration` adds the missing standalone calibration record flow: freeze a caller-authored reference set against the exact predeclared rubric and a packet-derived blinded view, present the view with reference labels withheld, record reviewer ratings, append corrections as new immutable events, and cold-read the complete history.

The frozen `sandbox-grounding-anchor-set-1` record binds the validated retrieval packet digest, the current fixed rubric and its digest, the exact blind view, every reference rating, and caller-asserted anchor author, method and basis. Anchor ratings and reviewer identities are not authenticated. They are not ground truth, reviewer accuracy, or a real-world quality score. Reviewers must differ from the caller-declared anchor author; separate reviews require distinct caller labels.

Every write uses a content-addressed canonical JSON record with exclusive creation. Review events form a sequential parent-digest chain. A correction names the exact original review event and row, retains the previous and replacement rating with a reason, and appends a new event. Old anchor sets, reviews and corrections remain byte-identical and readable. A concurrent append is refused under an exclusive temporary lock; an interrupted stale lock blocks future appends for inspection rather than permitting a replay. The chain protects against accidental change and ordering errors, not a malicious local writer or false reviewer identity.

The calibration projection reports per-reviewer agreement, disagreement and unknown counts against the frozen references, plus descriptive inter-reviewer agreement/disagreement/unknown counts. `uncertain` and `not-assessed` remain unknown. A correction changes only the effective history projection; it does not rewrite the submitted rating. There is no threshold, score, decision, automatic rubric change, or adoption authority.

The API is `freeze_anchor_set(store, request)`, `blind_session(store, anchor_set_sha256)`, `record_review(store, anchor_set_sha256, reviewer, judgments)`, `correct_rating(...)`, and `read_history(store, anchor_set_sha256)`. The same operations are exposed by one-request-per-process JSON CLI:

```sh
python3 -m harness.grounding_calibration --store <new-calibration-store> <<'JSON'
{"schema":"sandbox.grounding-calibration-request.v1","operation":"read-history","input":{"anchorSetSha256":"<exact-sha256>"}}
JSON
```

`freeze-anchors` accepts the exact frozen packet JSON, aliases, reread Study rows, anchor provenance and one reference rating for every blinded A/B question row. `blind-session` returns the pinned rubric and rows with anchor labels withheld. `record-review` accepts one rating per row. `correct-rating` appends a correction linked to the exact review event. `read-history` cold-verifies the anchor digest and event chain.

For a reviewer-facing form, [generate an offline review sheet](offline-review.md)
with `--format html` on `blind-session`. It downloads the existing `record-review`
request for explicit CLI import. No reference ratings or author provenance are
included, and no browser operation writes to the owner store.

This record flow uses only supplied packet bytes and supplied text. It runs no model, retriever, provider or participant study. The synthetic test fixture does not claim real-world quality or reviewer competence. The caller remains responsible for data rights and for choosing a new, separate store for private material.
