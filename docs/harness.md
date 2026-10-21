# Versioned trial harness

`harness/` is a small standard-library implementation of frozen plans, sealed attempts and identity comparison. It runs no model, provider SDK, agent or arbitrary command. The authorized native host executes and isolates a trial; the harness records what it produced and compares it. Run the tests with `python3 -m unittest discover -s tests`.

## Records

- A **plan** (`sandbox-trial-plan-1`) is frozen before execution as canonical JSON named by its SHA-256. It names the decision, case and rubric identities, criteria with provenance, arms and declared treatment variables, authority and permissions, attempt budget and stopping rule, and evaluator scope. Only a `historical-retrospective` plan may be written after execution, and it must say so.
- An **attempt** (`sandbox-trial-attempt-1`) is sealed separately and bound to one plan. Records are written read-only and never rewritten. Altered bytes, a reused attempt id with different content and attempts beyond the budget are refused. Failed, partial, interrupted, blocked, cancelled and not-run attempts are sealed like completed ones.
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

## Reuse

The dataset/task/solver/scorer separation follows Inspect AI's `Task` API (`src/inspect_ai/_eval/task/task.py` at `9868e105fa6eb371395a909b9709423c92a88512`, MIT): cases are agent-readable inputs, the native host or synthetic adapter is the solver, and evaluator-only rubrics are the scorer. Only that concept is adopted. No Inspect code is copied or installed, and its runtime and scoring path were not verified.
