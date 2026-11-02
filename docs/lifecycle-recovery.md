# Lifecycle recovery matrix

`harness.lifecycle_recovery.analyze` classifies supplied lifecycle observations without running recovery code. Run the synthetic matrix with:

```sh
python3 -m harness.lifecycle_recovery_cli --input examples/sb07-fault-fixtures.fixture.json
```

The closed `sandbox-lifecycle-observations-1` input records each operation ID, fault point, effect key/count, and state before and after reopen. The report retains those identities and classifies consistent reopen, preserved unknown, divergent state, or duplicate-effect invariant violation. A timeout or crash remains unknown when the supplied read does not settle it; a later supplied completed read does not erase the earlier unknown. Duplicate submit and late-write divergence stay visible for a human owner to assess.

The legacy fixture path is synthetic. The CLI does not inject faults, execute a retry, prove an external receipt, or authenticate supplied effect counts and states. A reported `effect_count` of zero means the fixture says zero; it does not prove no external effect occurred. Actual evidence must retain the exact owner receipt and separately identify any MA03/MC03 input snapshots. The report is descriptive and grants no recovery, retry, or adoption authority.

## Owner-backed snapshots

For a retained trial store, `sandbox-lifecycle-owner-snapshot-1` adds an exact `plan_sha256`, the legacy observations, one `attempt_bindings` entry per observation `operation_id`, and both `MA03` and `MC03` receipt entries. Each binding lists ordered `{attempt_id, attempt_sha256}` references. The CLI loads the plan and each attempt through `harness.trials.load_record`, then applies `validate_plan` and `validate_sealed`; the report carries the verified plan/attempt digests, IDs, arms and recorded execution statuses. Each observation operation must bind to at least one exact attempt, and the attempt ID, digest and plan binding must agree.

Run owner-backed evidence with `python3 -m harness.lifecycle_recovery_cli --input <owner-snapshot.json> --store <trial-store>`. Receipt entries are either explicitly `unknown` with a reason and null reference/digest or `supplied-unverified` with an opaque owner reference and digest. There is no MA03/MC03 receipt schema in Sandbox, so supplied references are not interpreted or authenticated. The lifecycle row's effect count and observed states remain caller assertions even when tied to valid Sandbox record bytes; attempt statuses are shown separately and never establish an external effect. A later completed attempt has its own ID/digest and cannot overwrite an earlier interrupted/unknown attempt. The registry is read-only; it does not create or revise trial records.
