# Frozen multi-system experiment manifests

`harness.experiment_manifests` groups exact arms from existing frozen `sandbox-trial-plan-1` records. A manifest is stored as a canonical content-addressed record under `manifests/` in the same trial store as `plans/` and `attempts/`; it does not copy or rewrite plans, create attempts, or add another store. The selected plans must name the same decision question, owner and consumer. A manifest names one baseline arm and one or more candidate arms by their existing plan SHA-256 and arm names.

The preview and cold-read projections show case IDs, rubric IDs, criterion provenance and caller-stated authority completeness from each retained plan. Shared IDs are only a navigation hint: identical labels do not prove equivalent case or rubric content. Exact plan digests remain the source identity. Missing referenced plans, invalid records, unknown arms, request-ID reuse with changed input and tampered manifest bytes are refused. Recovery reads the manifest embedded request ID plus exact input digest; an absent record is not proof that a late write cannot arrive and does not authorize replay.

Every manifest reports execution as `blocked`. A plan's authority fields are caller-stated records, not a grant. This module has no executor, permission broker, scheduler, model call or adoption path. Failed, partial, interrupted and unknown outcomes remain in their existing attempt records; SB01 creates none.

```sh
python3 -m harness.experiment_manifests list-plans --store <trial-store>
python3 -m harness.experiment_manifests prepare --store <trial-store> --manifest manifest-input.json
python3 -m harness.experiment_manifests freeze --store <trial-store> --request-id <uuid> --manifest manifest-input.json
python3 -m harness.experiment_manifests show --store <trial-store> --manifest-sha256 <sha256>
python3 -m harness.experiment_manifests recover --store <trial-store> --request-id <uuid> --input-sha256 <sha256>
```

The CLI is a deterministic local record operation. It does not run or compare attempts and does not establish human identity, permission, utility or adoption.
