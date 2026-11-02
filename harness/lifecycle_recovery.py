"""Analyze supplied lifecycle fault observations without executing or repairing work."""
import json
import hashlib

FORMAT = 'sandbox-lifecycle-observations-1'
FAULTS = {'lost-reply', 'late-write', 'process-crash', 'timeout', 'duplicate-submit', 'cold-reopen'}
STATES = {'prepared', 'running', 'completed', 'partial', 'failed', 'interrupted',
          'blocked', 'cancelled', 'not-run', 'unknown'}


class RecoveryError(ValueError):
    pass


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
