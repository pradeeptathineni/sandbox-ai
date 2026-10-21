# Sandbox workflow

Updated: 2026-10-21.

Sandbox owns portable trial methods, reusable evaluation harnesses, independent consumer and adversarial cases, versioned manifests, comparisons, evidence navigation and scoped recommendations. Each project owns its claims, intended users, operating conditions, acceptance criteria, domain cases and readable proof, and stays testable without Sandbox. Sandbox consumes pinned project criteria and records them as project-supplied, separate from criteria it authors. It can question Context or Signals using the same baseline and acceptance criteria applied to another candidate. A directory named Sandbox does not create an operating-system security boundary.

1. Name the decision, owner, current baseline, observable acceptance criteria and comparable inputs.
2. Establish permitted data, operations, runtime, isolation, budget, stopping rule and rollback owner before execution. Missing authority or isolation leaves the trial blocked.
3. Use the native host or a reviewed existing recipe. Preserve original failed, partial and unknown results at their owner.
4. Record exact source and input identities, direct observations, claims, metric semantics and limitations. Mark a supplied metric as unknown when its source does not expose it.
5. Attach a separate review with its actual reviewer and method. Record a provisional recommendation independently of the trial outcome. Adoption belongs to the destination's authorized decision owner.

`templates/` owns fill-in methods. `docs/` owns evidence meanings and reuse boundaries. `harness/` owns frozen plans, sealed attempts and identity comparison ([guide](../../docs/harness.md)); `recipes/` holds per-client cases with evaluator-only answers; `tests/` checks both. `examples/` holds synthetic fixtures only. `evidence/index.json` links approved portable records with exact digests. `.meta/context/` describes this workflow. Private source locations and migration maps live outside this repository.

Current implementation is documentation, fixtures, a standard-library record/compare harness with one Context consumer recipe, and public records of bounded trials and source assessments. The harness runs no model; the native host executes. Original private receipts remain with their owner. Public records grant no execution or adoption authority. Cross-project navigation benefit and human usefulness remain unmeasured. Revisit the structure after one real shared decision demonstrates a missing capability. Reconcile this file when the owning contracts or workflow change.
