# Trial authoring workbench

`harness/authoring.py` lets you start from an existing valid plan, edit it as a private draft with history, preview what freezing it would record, and then explicitly freeze one exact reviewed revision through `harness.trials.freeze_plan`. It is standard-library only. It runs no model, agent, provider or command and adds no scheduler, hook or service. Authoring, previewing and freezing grant **no execution or adoption authority**. The authorized native host executes; `harness.trials` seal, verify and compare remain the source of truth ([harness](harness.md), [evidence meanings](evidence.md)).

## Journey

```sh
python3 -m harness.authoring create  --workbench <wb> --draft <id> --from-recipe context-consumer --case receipt-review
python3 -m harness.authoring create  --workbench <wb> --draft <id> --from-plan-sha256 <sha> --store <store>
python3 -m harness.authoring create  --workbench <wb> --draft <id> --from-plan-file plan.json
python3 -m harness.authoring preview --workbench <wb> --draft <id> [--revision <rev>]
python3 -m harness.authoring read    --workbench <wb> --draft <id> [--revision <rev>] --plan-out edit.json
python3 -m harness.authoring revise  --workbench <wb> --draft <id> --parent <head rev> --plan edit.json --note "..."
python3 -m harness.authoring freeze  --workbench <wb> --draft <id> --revision <head rev> --preview-sha256 <digest> --store <store> [--handoff-out handoff.json]
```

1. **Create** from a fully valid starting point: a frozen plan reopened by hash from a trials store, a plan file, or the pinned [Context consumer recipe](../recipes/context-consumer/README.md) plan. Invalid starting plans are refused before a draft exists. `frozen_at_utc` is dropped; freeze sets it again.
2. **Read** reopens any revision with its full parent-linked history and recorded freezes. `--plan-out` writes the complete editable plan to a new file.
3. **Revise** records a complete edited plan, not a patch, as a child of the head. A stale `--parent` is refused with the current head, so concurrent edits are never lost or overwritten. An edit may still be invalid; preview reports that.
4. **Preview** applies `trials.validate_plan` and reports `ready` or `refused`, the errors, and a summary: cases, rubrics, every criterion with kind, owner, reference and independence, arms and treatment variables, authority, budget and evaluator scope. It also lists changes from the parent and these warnings:
   - synthetic or historical labeling, and any change of record kind;
   - project-supplied criteria, which are never independent evidence;
   - a missing independent or adversarial criterion, or a case with no criterion;
   - declared paths that would let the agent read an evaluator answer;
   - single-arm plans, treatment variables naming no identity field or a whole broad field;
   - granted permissions;
   - null or placeholder values such as `<out>`.

   `preview_sha256` digests the review.
5. **Freeze** needs the exact head revision and its current `preview_sha256`. A stale revision, a changed preview or a refused preview is refused before any plan is written. Each revision freezes at most once. The output is the host handoff described below.

## Records

The workbench is an explicit, host-chosen directory and is never discovered by scanning. Its layout is `drafts/<id>/revisions/000001.json…` plus `drafts/<id>/freezes/<revision>.json`.

- **Revision** (`sandbox-trial-draft-1`): `format, draft_id, sequence, parent_sha256, created_at_utc, origin, plan, note`. It is canonical JSON, read-only, and identified by its SHA-256. It is published by a hard link that fails if another writer took the sequence first, so that writer's revision is never overwritten.
- **Origin** records the starting point: `frozen-plan` (`plan_sha256`, `plan_id`, `frozen_at_utc`), `plan-file` (file name and `sha256`) or `context-recipe` (case, Context commit, contract/case/rubric hashes). It also records the starting `record_kind`. A historical-retrospective start stays historical; a new draft cannot become historical.
- **Preview** (`sandbox-trial-preview-1`): `revision_sha256, candidate_sha256, status, errors, warnings, summary, origin, changes_from_parent, grants, preview_sha256, head`.
- **Handoff** (`sandbox-trial-handoff-1`): `plan_sha256`, `plan_id`, `record_kind`, `frozen_before_execution`, `authored_from` (draft, revision, preview, origin), arms and treatment variables, case/rubric hashes, criteria provenance, and `execution`. `execution` holds `status: not-run`, `executor: null` and `executor_setup_required` (the declared authority, permissions, evaluator scope and budget). It also includes `grants` (false) and the existing `harness.trials` seal/compare commands. It names no executor, host command or approval; the native host supplies and authorizes those.

## Bounds

- Draft ids are `[a-z0-9][a-z0-9-]{0,63}`; revisions and plans are 64-hex digests, so ids cannot traverse paths.
- Files are read without following symlinks, FIFOs are refused, and inputs are limited to 1 MiB.
- A draft holds at most 256 revisions; notes are limited to 500 characters.
- Altered or reordered history is refused. A canonical rewrite of the head is not detectable from links alone, but it changes the revision identity, so freezing the reviewed digest fails. As with trials records, a hash proves integrity, not who wrote the record.
- If a freeze is interrupted after the claim, `read` shows it as interrupted; check the store and revise to freeze again.

## Demonstration

`python3 examples/trial_authoring.py --out <new dir>` runs every step as a fresh CLI process:

1. Start from the recipe plan and preview it.
2. Edit the treatment, a criterion and the scope; a concurrent edit from the old parent is refused.
3. Save a refused budget edit.
4. Show that freezing a stale revision, a refused preview or a stale preview digest each writes no plan.
5. Freeze the reviewed head and reopen the plan by hash.
6. Seal and compare two authored synthetic attempts through `harness.trials`.

The attempts apply `recipes/context-consumer/synthetic` outputs and are never model output. The equipped attempt leaves its toolchain unknown, so compare reports the pair as `insufficiently-identified`. The authored adversarial rubric stays in an evaluator-only directory, and `trials.check_closure` confirms it is outside the agent-readable workspace.

## Reuse

The workbench is original code over this project's own `harness.trials` contracts and the Context consumer recipe. No external code was copied or installed. The Inspect AI concept pinned in [harness.md](harness.md) is unchanged.
