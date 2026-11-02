# Lifecycle recovery matrix

`harness.lifecycle_recovery.analyze` classifies supplied lifecycle observations without running recovery code. Run the synthetic matrix with:

```sh
python3 -m harness.lifecycle_recovery_cli --input examples/sb07-fault-fixtures.fixture.json
```

The closed `sandbox-lifecycle-observations-1` input records each operation ID, fault point, effect key/count, and state before and after reopen. The report retains those identities and classifies consistent reopen, preserved unknown, divergent state, or duplicate-effect invariant violation. A timeout or crash remains unknown when the supplied read does not settle it; a later supplied completed read does not erase the earlier unknown. Duplicate submit and late-write divergence stay visible for a human owner to assess.

The fixture is synthetic. The CLI does not inject faults, execute a retry, inspect frozen trial plans or sealed attempts, prove an external receipt, or authenticate supplied effect counts and states. A reported `effect_count` of zero means the fixture says zero; it does not prove no external effect occurred. Actual evidence must retain the exact owner receipt and separately identify any MA03/MC03 input snapshots. The report is descriptive and grants no recovery, retry, or adoption authority.
