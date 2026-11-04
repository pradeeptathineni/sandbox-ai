# Authorized adversarial boundary trials (SB06)

This workflow adapts the existing `harness.authoring`, `harness.trials` and `harness.paired_comparison` contracts. It adds no executor, permission broker, endpoint probe, provider call or security-setting change. The checked-in plan/case/rubric are synthetic fixtures only; they assert no real authorization or target result.

## Scope and authority

Start with the [synthetic case fixture](../examples/sb06-authorized-boundary-cases.fixture.json), [evaluator-only rubric](../examples/sb06-independent-boundary-rubric.fixture.json) and [trial plan](../examples/sb06-trial-plan.fixture.json). They cover a mocked denial, a missing receipt, a fixture error and an interrupted fixture run. No network, provider, private data, filesystem probing or host security change is permitted by this example. Its `record_kind` is `synthetic`; changing that label does not turn fixture values into real evidence.

For any future owned target, name the owner, exact target, permitted operations, isolation, stopping rule and rollback owner before execution. Verify the corresponding MA02 authorization outside Sandbox. Caller-stated plan fields do not grant authority. Keep target data and receipts at their source and bind their exact references/hashes in attempts. Use the native host only after it independently enforces the approved scope. Do not probe public or third-party endpoints.

## Journey

Create a caller-owned draft from the plan fixture, preview it and freeze only the exact reviewed revision:

```sh
python3 -m harness.authoring create --workbench <workbench> --draft sb06 --from-plan-file examples/sb06-trial-plan.fixture.json
python3 -m harness.authoring preview --workbench <workbench> --draft sb06
python3 -m harness.authoring freeze --workbench <workbench> --draft sb06 --revision <revision-sha256> --preview-sha256 <preview-sha256> --store <trial-store>
```

The frozen plan hash is the identity for every sealed attempt. Seal one record for each actual outcome through `harness.trials`; preserve `blocked`, `failed`, `interrupted` and `unknown` exactly, with the failure layer and receipt when available. A missing receipt or metric is unknown, not zero or success. Never overwrite a failed or unknown attempt; a later run gets its own attempt ID and digest.

```sh
python3 -m harness.trials seal --store <trial-store> --plan-sha256 <plan-sha256> --attempt <draft.json> --receipt-root <saved-receipts>
python3 -m harness.trials verify --store <trial-store>
python3 -m harness.trials compare --store <trial-store> --plan-sha256 <plan-sha256> --format json
```

The existing attempt `review` field keeps `independent`, reviewer and method separate from outcome and adoption. Before sealing, an actual independent reviewer can inspect the exact finalized attempt draft and receipts; the caller records the reviewer and method in that attempt and then seals the reviewed bytes. These labels remain unauthenticated. If review has not happened, record `not-performed`; the synthetic example does so. `harness.paired_comparison` can attach a separate caller review to exact selected attempt IDs and emits a decision-owner handoff. That review is also caller-supplied and does not establish independence by itself. Adoption remains `not-recorded-by-Sandbox` and `no-authority-granted`.

For a paired handoff, select exact completed denial attempt IDs and provide the existing `sandbox-paired-comparison-review-1` input. In a real workflow, the review author should be a separate reviewer and discuss the exact selected attempts, failures, unknowns and scope; the packet preserves the supplied review text but cannot authenticate independence.

```sh
python3 -m harness.paired_comparison create --store <trial-store> --plan-sha256 <plan-sha256> \
  --selection selected-pairs.json --review comparison-review.json --output comparison.json
python3 -m harness.paired_comparison preflight --input comparison.json
python3 -m harness.paired_comparison render --input comparison.json --format markdown --publication
```

`python3 -m unittest discover -s tests -p 'test_sb06_boundary_workflow.py'` runs the offline synthetic journey in a fresh temporary store, cold-validates its paired packet, and checks the preserved completed-denial, blocked, failed and interrupted outcomes. It invokes no executor. The result tests record handling, not a real system's boundary behavior, security, or usefulness.
