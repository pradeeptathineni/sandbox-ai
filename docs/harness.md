# Versioned trial harness

`harness/` is a small standard-library implementation of frozen plans, sealed attempts and identity comparison. It runs no model, provider SDK, agent or arbitrary command. The authorized native host executes and isolates a trial; the harness records what it produced and compares it. Run the tests with `python3 -m unittest discover -s tests`.

## Records

- A **plan** (`sandbox-trial-plan-1`) is frozen before execution as canonical JSON named by its SHA-256. It names the decision, case and rubric identities, criteria with provenance, arms and declared treatment variables, authority and permissions, attempt budget and stopping rule, and evaluator scope. Only a `historical-retrospective` plan may be written after execution, and it must say so.
- An **attempt** (`sandbox-trial-attempt-1`) is sealed separately and bound to one plan. Records are written read-only and never rewritten. Altered bytes, a reused attempt id with different content and attempts beyond the budget are refused. Failed, partial, interrupted, blocked, cancelled and not-run attempts are sealed like completed ones. `verify` and `compare` re-apply the sealing contract to every stored attempt against its bound plan, and `compare` refuses invalid records. A matching content hash shows integrity, not who sealed a record: a valid record written under its recomputed name passes. Authenticating the original sealer requires an independently trusted index of digests.
- **Run identity** requires the milestone label, subject commit and clean/dirty status with exact hashes of relevant dirty and untracked files, release artifact, lockfile, equipment, case/rubric, prompt/input hashes, requested and served model with settings and weights snapshot, harness, runtime, toolchain and original artifact references. A genuinely unavailable value is `null` with a reason in `unknown`; omission is refused. A hosted model identifier is never treated as a weights snapshot.
- **Criteria** carry provenance. `project-supplied` criteria reference the owning project's pinned file and hash and are never independent evidence about that project. `consumer` and `adversarial` criteria are authored separately and live with the evaluator. Criteria cannot carry authority or self-grades.
- **Measurements** carry value or null with a reason, unit, denominator, source, semantics and observed/estimate/billed kind. Duration appears only where it was measured.

Execution status, deterministic outcome, independent review and adoption are separate fields.

## Comparison

`compare` pairs the baseline arm with every other attempt under one plan. A pair is `comparable` when only declared treatment fields differ, `confounded` when another control differs or the attempt deviated from the frozen case/rubric, `insufficiently-identified` when a control is unknown, and `replicate` when nothing differs within an arm. Arms whose attempts have different identities are not pooled. Synthetic and historical records are flagged, and an attempt that did not complete is retained but marked not outcome-comparable. Measurements with different units, semantics or kinds are incomparable; missing values stay unknown. Per-attempt original artifacts are provenance, not a control. A comparable verdict establishes structure only, never usefulness.

## CLI

```sh
python3 -m harness.trials snapshot --repo <checkout> --relevant <prefix>
python3 -m harness.trials freeze-plan --store <store> --plan plan.json
python3 -m harness.trials seal --store <store> --plan-sha256 <sha> --attempt attempt.json --receipt-root <saved-receipts>
python3 -m harness.trials verify --store <store>
python3 -m harness.trials compare --store <store> --plan-sha256 <sha> --format markdown
```

The [Context consumer recipe](../recipes/context-consumer/README.md) is the first client.

## Frozen experiment manifests

`harness.experiment_manifests` groups exact arms from existing frozen plans into a content-addressed SB01 manifest in the same trial store. It creates no plan or attempt and reports execution as blocked. Plan identity, authority completeness and recovery behavior are documented in [experiment manifests](experiment-manifests.md).

## Explicit paired comparison evidence

`harness.paired_comparison` composes caller-selected pairs from one frozen plan and its sealed attempts. It calls `trials.compare_pair`; it does not execute trials, match attempts by order, infer missing partners or modify existing records. Unselected attempts remain listed separately. The `sandbox-paired-comparison-evidence-1` packet embeds exact receipts and digests, the plan, structural comparisons, context strata, criterion outcome counts with denominators, resource accounting, caller review and a decision-owner handoff.

The caller selects 1–32 distinct baseline/candidate pairs from one plan, using the same candidate arm throughout. Reviewer labels, selection rationale, task/dataset/model/fixture scope and uncertainty notes remain caller assertions. Confounded and incompletely identified pairs remain visible and separate from known strata. Counts describe selected pairs and do not establish general performance, causality or superiority.

Resource scope is explicit per metric: `per-run`, `accounting-window` or `unknown`. Missing values remain unavailable. Per-run deltas require matching unit, denominator, semantics, measurement kind and source. Totals appear only for complete per-run groups with matching context and metadata. Window values remain as supplied and are never added or subtracted across windows.

```sh
python3 -m harness.paired_comparison create --store <store> --plan-sha256 <plan-sha> \
  --selection selected-pairs.json --review comparison-review.json --output comparison.json
python3 -m harness.paired_comparison render --input comparison.json --format markdown
```

Packets can be cold-validated and rendered without the trial store. The packet records a handoff for the plan's named decision owner, but no adoption choice or authority. Its digest binds the selected receipts and caller review; a cold read does not regenerate missing source evidence.

## Blinded evaluator calibration

`harness.grounding_review` validates a frozen retrieval packet, masks arm names in a caller-supplied answer/evidence view, checks closed ratings, and reports descriptive two-reviewer agreement, disagreement and unknown counts. [Grounding calibration](grounding-calibration.md) freezes the exact rubric and caller-authored anchor ratings before review, then records reviews and corrections as append-only digest-linked events. Anchor agreement is descriptive only; reviewer identities and anchor labels are unauthenticated, and the workflow makes no real-world quality claim.

## Claim evidence audit

`harness.claim_evidence_registry` returns a canonical claim registry and audit from caller-supplied snapshots of existing packet and review records. `register` creates the first claim revision; `audit` cold-reads and revalidates a retained registry; `revise` requires a new claim revision plus explicitly supplied evidence/review references and rejects inherited SB08 reviewer/session IDs. A stale claim revision reports `proofInherited: false`. The module stores no records and makes no claim-truth or approval decision; see [claim evidence registry](claim-evidence-registry.md).

## Lifecycle recovery matrix

`harness.lifecycle_recovery` classifies caller-supplied interruption, timeout, reopen, duplicate-effect and divergence observations. Unknown remains unknown; the workflow does not execute retries or attest external effects. See the [SB07 recovery matrix](lifecycle-recovery.md) and synthetic fixture.

## Authorized adversarial boundary trials

SB06 uses the existing plan authoring, sealed-attempt and paired-comparison APIs; it adds no executor or permission change. The [SB06 workflow](authorized-boundary-trials.md) ships a synthetic-only case/rubric/plan and a focused journey retaining denial, blocked, failed and interrupted records. Authority fields are caller assertions; independent review and adoption remain separate.

## Reuse

The dataset/task/solver/scorer separation follows Inspect AI's `Task` API (`src/inspect_ai/_eval/task/task.py` at `9868e105fa6eb371395a909b9709423c92a88512`, MIT): cases are agent-readable inputs, the native host or synthetic adapter is the solver, and evaluator-only rubrics are the scorer. Only that concept is adopted. No Inspect code is copied or installed, and its runtime and scoring path were not verified.
