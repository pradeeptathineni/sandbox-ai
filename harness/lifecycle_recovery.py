"""Analyze supplied lifecycle fault observations without executing or repairing work."""
import json
import hashlib

FORMAT = 'sandbox-lifecycle-observations-1'
FAULTS = {'lost-reply', 'late-write', 'process-crash', 'timeout', 'duplicate-submit', 'cold-reopen'}
STATES = {'prepared', 'running', 'completed', 'partial', 'failed', 'interrupted',
          'blocked', 'cancelled', 'not-run', 'unknown'}
OWNER_FORMAT = 'sandbox-lifecycle-owner-snapshot-1'
RECEIPT_OWNERS = ('MA03', 'MC03')


class RecoveryError(ValueError):
    pass


def analyze_owner_snapshots(store, evidence):
    """Cold-read exact Sandbox plan/attempt records and bind supplied observations to them.

    MA03/MC03 receipt references remain opaque; Sandbox has no receipt schema or authenticator for them.
    Attempt status is reported as recorded and never used to infer external effects.
    """
    from . import trials

    if not isinstance(evidence, dict) or set(evidence) != {
            'format', 'plan_sha256', 'observations', 'attempt_bindings', 'owner_receipts'}:
        raise RecoveryError('owner snapshot must have the exact supported fields')
    if evidence['format'] != OWNER_FORMAT:
        raise RecoveryError(f'format must be {OWNER_FORMAT}')
    plan_sha = evidence['plan_sha256']
    if not trials._is_sha256(plan_sha):
        raise RecoveryError('plan_sha256 must be a lowercase SHA-256 digest')
    try:
        plan = trials.load_record(store, 'plans', plan_sha)
        trials.validate_plan(plan)
    except (trials.TrialError, OSError, ValueError, TypeError, KeyError) as exc:
        raise RecoveryError(f'frozen plan could not be cold-validated: {exc}') from exc

    base = analyze(evidence['observations'])
    operation_ids = {row['operation_id'] for row in evidence['observations']['observations']}
    bindings = evidence['attempt_bindings']
    if not isinstance(bindings, list):
        raise RecoveryError('attempt_bindings must be a list')
    binding_by_operation = {}
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {'operation_id', 'attempts'}:
            raise RecoveryError('each attempt binding must contain only operation_id and attempts')
        operation_id = binding['operation_id']
        if not isinstance(operation_id, str) or not operation_id or operation_id in binding_by_operation:
            raise RecoveryError('attempt binding operation_id must be unique non-empty text')
        if operation_id not in operation_ids:
            raise RecoveryError(f'attempt binding has no observation for operation {operation_id}')
        refs = binding['attempts']
        if not isinstance(refs, list) or not refs:
            raise RecoveryError(f'operation {operation_id} needs one or more exact attempt references')
        attempt_rows = []
        seen_attempts = set()
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {'attempt_id', 'attempt_sha256'}:
                raise RecoveryError('attempt reference must contain only attempt_id and attempt_sha256')
            attempt_id, digest = ref['attempt_id'], ref['attempt_sha256']
            if not isinstance(attempt_id, str) or not attempt_id or not trials._is_sha256(digest):
                raise RecoveryError('attempt reference requires non-empty attempt_id and lowercase SHA-256')
            if digest in seen_attempts:
                raise RecoveryError(f'duplicate attempt digest for operation {operation_id}')
            seen_attempts.add(digest)
            try:
                attempt = trials.load_record(store, 'attempts', digest)
                trials.validate_sealed(attempt, plan)
            except (trials.TrialError, OSError, ValueError, TypeError, KeyError) as exc:
                raise RecoveryError(f'attempt {digest} could not be cold-validated: {exc}') from exc
            if attempt['plan_sha256'] != plan_sha or attempt['attempt_id'] != attempt_id:
                raise RecoveryError(f'attempt {digest} identity or plan binding does not match the exact reference')
            attempt_rows.append({
                'attempt_id': attempt_id, 'attempt_sha256': digest, 'plan_sha256': plan_sha,
                'arm': attempt['arm'], 'record_kind': attempt['record_kind'],
                'execution_status_as_recorded': attempt['execution']['status'],
                'external_effect_inferred': False,
            })
        binding_by_operation[operation_id] = attempt_rows
    if set(binding_by_operation) != operation_ids:
        raise RecoveryError(f'every observation operation needs an exact attempt binding; missing {sorted(operation_ids - set(binding_by_operation))}')

    receipts = evidence['owner_receipts']
    if not isinstance(receipts, dict) or set(receipts) != set(RECEIPT_OWNERS):
        raise RecoveryError('owner_receipts must explicitly contain MA03 and MC03')
    normalized_receipts = {}
    for owner in RECEIPT_OWNERS:
        item = receipts[owner]
        if not isinstance(item, dict) or set(item) != {'status', 'reference', 'sha256', 'reason'}:
            raise RecoveryError(f'{owner} receipt must use the exact opaque-reference shape')
        if item['status'] == 'unknown':
            if item['reference'] is not None or item['sha256'] is not None or not isinstance(item['reason'], str) or not item['reason'].strip():
                raise RecoveryError(f'{owner} unknown receipt requires null reference/digest and a reason')
            normalized_receipts[owner] = {**item, 'verification': 'unknown; no owner receipt supplied'}
        elif item['status'] == 'supplied-unverified':
            if not isinstance(item['reference'], str) or not item['reference'].strip() or not trials._is_sha256(item['sha256']) or item['reason'] is not None:
                raise RecoveryError(f'{owner} supplied receipt needs an opaque reference and SHA-256, with null reason')
            normalized_receipts[owner] = {**item, 'verification': 'unverified; Sandbox has no MA03/MC03 receipt schema'}
        else:
            raise RecoveryError(f'{owner} receipt status must be unknown or supplied-unverified')

    observations = []
    for finding in base['observations']:
        row = dict(finding)
        row['sandbox_attempts'] = binding_by_operation[row['operation_id']]
        row['observation_status_basis'] = 'caller-supplied lifecycle state/count; not authenticated by Sandbox attempts'
        observations.append(row)
    body = {
        'format': 'sandbox-lifecycle-owner-recovery-report-1',
        'plan_sha256': plan_sha,
        'observations': observations,
        'summary': base['summary'],
        'owner_receipts': normalized_receipts,
        'qualification': 'Sandbox plan and attempts were cold-validated by exact digest and schema. Lifecycle states and effect counts remain caller-supplied and unverified; attempt status does not establish external effects. MA03/MC03 references are opaque and unauthenticated.',
    }
    body['report_sha256'] = hashlib.sha256((json.dumps(body, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
    return body


def analyze(observations):
    if not isinstance(observations, dict) or observations.get('format') != FORMAT:
        raise RecoveryError(f'format must be {FORMAT}')
    rows = observations.get('observations')
    if not isinstance(rows, list) or not rows:
        raise RecoveryError('observations must be a non-empty list')
    findings = []
    ids = set()
    for row in rows:
        if set(row) != {'id', 'operation_id', 'fault', 'effect_key', 'effect_count', 'state', 'reopen_state'}:
            raise RecoveryError('observation fields are not the supported closed shape')
        if row['id'] in ids:
            raise RecoveryError(f'duplicate observation id {row["id"]}')
        ids.add(row['id'])
        if row['fault'] not in FAULTS:
            raise RecoveryError(f'unsupported fault point {row["fault"]}')
        if type(row['effect_count']) is not int or row['effect_count'] < 0:
            raise RecoveryError('effect_count must be a non-negative integer observation')
        if not all(isinstance(row[key], str) and row[key].strip() for key in ('id', 'operation_id', 'effect_key')):
            raise RecoveryError('observation, operation and effect identities must be non-empty text')
        if row['state'] not in STATES or row['reopen_state'] not in STATES:
            raise RecoveryError(f'states must be one of {sorted(STATES)}')
        duplicate = row['effect_count'] > 1
        unknown = row['state'] == 'unknown' or row['reopen_state'] == 'unknown'
        if duplicate:
            status = 'invariant-violation-duplicate-effect'
        elif unknown:
            status = 'unknown-preserved'
        elif row['state'] != row['reopen_state']:
            status = 'recovery-state-diverged'
        else:
            status = 'reopened-consistently'
        findings.append({'observation_id': row['id'], 'operation_id': row['operation_id'],
                         'fault': row['fault'], 'status': status,
                         'effect_key': row['effect_key'], 'effect_count': row['effect_count'],
                         'state_before_reopen': row['state'], 'state_after_reopen': row['reopen_state']})
    body = {'format': 'sandbox-lifecycle-recovery-report-1', 'observations': findings,
            'summary': {key: sum(row['status'] == key for row in findings) for key in
                        ('invariant-violation-duplicate-effect', 'unknown-preserved',
                         'recovery-state-diverged', 'reopened-consistently')},
            'qualification': 'Caller-supplied observations only; status consistency is not independently verified and no recovery code was invoked.'}
    body['report_sha256'] = hashlib.sha256((json.dumps(body, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
    return body
