# Stateless blinded grounding review

`python -m harness.grounding_review` accepts one bounded JSON request on stdin and writes one JSON result to stdout. Its closed operations are `session-view`, `review-check`, and `paired-compare`. The module has no store and writes no files. Session views first validate the complete frozen retrieval packet with `harness.retrieval_experiment`, then bind rows to the packet's exact arms and questions and mask arm names in displayed question, answer, and excerpts. The caller supplies exact retained Study text only after its source owner has reread it; the module does not fetch sources.

`review-check` accepts one rating per exact A/B question row from the predeclared rubric. `paired-compare` requires two distinct caller-declared reviewer names, identical row coverage, the exact rubric and aliases, and preserves `uncertain` and `not-assessed` as unknown comparisons. Agreement counts are descriptive only. Reviewer identities are unauthenticated; no result establishes truth, entailment, reviewer accuracy, live model quality, or adoption.

For Deep's integrated session, Deep remains the persistence owner: it retains the frozen session and reviewer records in its existing Experiment Evidence journal, cold-reads exact records, rechecks Research source identity, and delegates deterministic projection/validation/comparison to this native module. The standalone [calibration flow](grounding-calibration.md) is separate: it appends only caller-authored anchor and review records to a caller-selected local store and is not mounted in Deep. No model, retriever, or participant is run.

Focused checks: `python -B -m unittest discover -s tests -p 'test_grounding_review.py' -v`.
