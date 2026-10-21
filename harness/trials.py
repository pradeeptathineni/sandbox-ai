"""Versioned trial records: frozen plans, sealed attempts and identity comparison.

A plan is frozen before execution. Each actual attempt is sealed as a separate record bound to that plan.
Both are canonical JSON files named by their SHA-256 and are never rewritten. Comparison reports which
identity fields changed, separating declared treatment from unexpected control differences and unknowns.

Nothing here runs a model, a provider SDK or an arbitrary command. Execution and isolation belong to the
authorized native host; this module records and compares what that host produced.
"""
import argparse
import datetime
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

PLAN_FORMAT = 'sandbox-trial-plan-1'
ATTEMPT_FORMAT = 'sandbox-trial-attempt-1'
RECORD_KINDS = ('real', 'synthetic', 'historical-retrospective')
EXECUTION_STATUSES = ('attempted', 'completed', 'partial', 'failed', 'interrupted', 'blocked', 'cancelled', 'not-run')
FAILURE_LAYERS = (None, 'model', 'harness', 'evaluator', 'tool', 'infrastructure', 'unknown')
ADAPTERS = ('native-host', 'imported-receipts', 'synthetic-fixture')
CRITERION_KINDS = ('project-supplied', 'consumer', 'adversarial')
RESULTS = ('pass', 'fail', 'unknown', 'not-applicable')
MEASUREMENT_KINDS = ('observed', 'estimate', 'billed')

PLAN_KEYS = {'format', 'plan_id', 'record_kind', 'frozen_at_utc', 'frozen_before_execution', 'decision', 'cases',
             'rubrics', 'criteria', 'treatment', 'authority', 'budget', 'evaluator_scope', 'limitations'}
DRAFT_KEYS = {'attempt_id', 'arm', 'record_kind', 'adapter', 'execution', 'identity', 'unknown', 'measurements',
              'outcome', 'review', 'adoption', 'receipts', 'notes'}
SEALED_KEYS = DRAFT_KEYS | {'format', 'plan_sha256', 'sealed_at_utc', 'plan_deviations'}
CRITERION_KEYS = {'id', 'case', 'kind', 'statement', 'provenance', 'independent_of_subject'}
REQUIRED_IDENTITY = (
    'milestone_label',
    'subject.repository', 'subject.commit', 'subject.worktree_status', 'subject.dirty', 'subject.untracked',
    'release_artifact.reference', 'release_artifact.sha256',
    'lockfile.path', 'lockfile.sha256',
    'equipment', 'cases', 'rubrics', 'prompt_sha256', 'inputs',
    'model.provider', 'model.requested', 'model.served', 'model.settings', 'model.weights_snapshot',
    'harness.name', 'harness.version', 'runtime', 'toolchain', 'original_artifacts',
)
PER_ATTEMPT_IDENTITY = ('original_artifacts',)


class TrialError(Exception):
    """A record was refused. The message lists every reason found."""


def canonical(obj):
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 16), b''):
            digest.update(block)
    return digest.hexdigest()


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def lookup(obj, path):
    """Return (found, value) for a dotted path through nested dicts."""
    for part in path.split('.'):
        if not isinstance(obj, dict) or part not in obj:
            return False, None
        obj = obj[part]
    return True, obj


def flatten(obj, prefix=''):
    """Map dotted paths to leaves. Lists are compared as whole canonical values."""
    if isinstance(obj, dict) and obj:
        out = {}
        for key, value in obj.items():
            out.update(flatten(value, f'{prefix}.{key}' if prefix else key))
        return out
    return {prefix: obj}


def _is_sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def refuse(errors):
    if errors:
        raise TrialError('; '.join(errors))


# --- plans -----------------------------------------------------------------------------------------------

def validate_plan(plan):
    errors = []
    if not isinstance(plan, dict):
        raise TrialError('plan must be a JSON object')
    missing, extra = PLAN_KEYS - plan.keys(), plan.keys() - PLAN_KEYS
    if missing:
        errors.append(f'plan missing {sorted(missing)}')
    if extra:
        errors.append(f'plan has unsupported keys {sorted(extra)}')
    refuse(errors)
    if plan['format'] != PLAN_FORMAT:
        errors.append(f'plan format must be {PLAN_FORMAT}')
    if plan['record_kind'] not in RECORD_KINDS:
        errors.append(f'record_kind must be one of {RECORD_KINDS}')
    if plan['frozen_before_execution'] is not (plan['record_kind'] != 'historical-retrospective'):
        errors.append('only a historical-retrospective plan may be frozen after execution, and it must say so')
    for key in ('question', 'owner', 'consumer'):
        if not isinstance(plan['decision'].get(key), str):
            errors.append(f'decision.{key} required')
    for table in ('cases', 'rubrics'):
        if not isinstance(plan[table], dict) or not plan[table]:
            errors.append(f'{table} must name at least one entry')
            continue
        for name, entry in plan[table].items():
            if not _is_sha256(entry.get('sha256')) or not entry.get('version') or not entry.get('provenance'):
                errors.append(f'{table}.{name} needs version, sha256 and provenance')
    seen = set()
    for criterion in plan['criteria']:
        errors.extend(_criterion_errors(criterion, plan['cases']))
        if criterion.get('id') in seen:
            errors.append(f'duplicate criterion {criterion.get("id")}')
        seen.add(criterion.get('id'))
    treatment = plan['treatment']
    if treatment.get('baseline_arm') not in treatment.get('arms', {}):
        errors.append('treatment.baseline_arm must name one of treatment.arms')
    for variable in treatment.get('variables', []):
        if not str(variable.get('path', '')).startswith('identity.') or not variable.get('reason'):
            errors.append('each treatment variable needs an identity.* path and a reason')
    authority = plan['authority']
    for key in ('authorized_by', 'operations', 'model_execution', 'isolation_owner', 'permissions'):
        if key not in authority:
            errors.append(f'authority.{key} required (use an explicit value, not omission)')
    budget = plan['budget']
    if not isinstance(budget.get('max_attempts_per_arm'), int) or budget['max_attempts_per_arm'] < 1:
        errors.append('budget.max_attempts_per_arm must be a positive integer')
    if not budget.get('stopping_rule'):
        errors.append('budget.stopping_rule required')
    scope = plan['evaluator_scope']
    for key in ('agent_readable_roots', 'evaluator_only_roots', 'grader'):
        if key not in scope:
            errors.append(f'evaluator_scope.{key} required')
    refuse(errors)


def _criterion_errors(criterion, cases):
    cid = criterion.get('id')
    errors = []
    if set(criterion) != CRITERION_KEYS:
        errors.append(f'criterion {cid} keys must be exactly {sorted(CRITERION_KEYS)}; '
                      'criteria cannot carry authority or self-grades')
        return errors
    if criterion['case'] not in cases:
        errors.append(f'criterion {cid} names unknown case {criterion["case"]}')
    if criterion['kind'] not in CRITERION_KINDS:
        errors.append(f'criterion {cid} kind must be one of {CRITERION_KINDS}')
    provenance = criterion['provenance']
    for key in ('owner', 'version', 'reference', 'sha256'):
        if not provenance.get(key):
            errors.append(f'criterion {cid} provenance.{key} required')
    if provenance.get('sha256') and not _is_sha256(provenance['sha256']):
        errors.append(f'criterion {cid} provenance.sha256 must be a SHA-256 hex digest')
    if criterion['kind'] == 'project-supplied' and criterion['independent_of_subject'] is not False:
        errors.append(f'criterion {cid}: project-supplied criteria are never independent evidence of that project')
    if not isinstance(criterion['independent_of_subject'], bool):
        errors.append(f'criterion {cid} independent_of_subject must be true or false')
    return errors


# --- store -----------------------------------------------------------------------------------------------

def _write_record(store, kind, record):
    data = canonical(record)
    digest = sha256_bytes(data)
    folder = Path(store) / kind
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{digest}.json'
    try:
        with open(path, 'xb') as handle:
            handle.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise TrialError(f'{path} exists with different content; refusing to overwrite a sealed record')
        return digest
    os.chmod(path, 0o444)
    return digest


def load_record(store, kind, digest):
    path = Path(store) / kind / f'{digest}.json'
    if not path.is_file():
        raise TrialError(f'no {kind} record {digest}')
    data = path.read_bytes()
    if sha256_bytes(data) != digest:
        raise TrialError(f'{path} content does not match its name; record was altered')
    record = json.loads(data)
    if canonical(record) != data:
        raise TrialError(f'{path} is not canonical; record was altered')
    return record


def freeze_plan(store, plan):
    validate_plan(plan)
    return _write_record(store, 'plans', plan)


def iter_records(store, kind):
    folder = Path(store) / kind
    for path in sorted(folder.glob('*.json')) if folder.is_dir() else []:
        yield path.stem, load_record(store, kind, path.stem)


def attempts_for(store, plan_sha):
    return [(digest, rec) for digest, rec in iter_records(store, 'attempts') if rec['plan_sha256'] == plan_sha]


# --- attempts --------------------------------------------------------------------------------------------

def validate_draft(draft, plan):
    errors = []
    if not isinstance(draft, dict):
        raise TrialError('attempt must be a JSON object')
    missing, extra = DRAFT_KEYS - draft.keys(), draft.keys() - DRAFT_KEYS
    if missing:
        errors.append(f'attempt missing {sorted(missing)}')
    if extra:
        errors.append(f'attempt has unsupported keys {sorted(extra)}')
    refuse(errors)
    if draft['arm'] not in plan['treatment']['arms']:
        errors.append(f'arm {draft["arm"]} is not declared by the plan')
    if draft['record_kind'] not in RECORD_KINDS:
        errors.append(f'record_kind must be one of {RECORD_KINDS}')
    if draft['record_kind'] != plan['record_kind']:
        errors.append('attempt record_kind must match its plan; synthetic and real attempts are never pooled')
    adapter = draft['adapter']
    if adapter.get('kind') not in ADAPTERS:
        errors.append(f'adapter.kind must be one of {ADAPTERS}')
    if adapter.get('kind') == 'synthetic-fixture' and draft['record_kind'] != 'synthetic':
        errors.append('a synthetic fixture adapter can never produce a real or historical result')
    execution = draft['execution']
    if execution.get('status') not in EXECUTION_STATUSES:
        errors.append(f'execution.status must be one of {EXECUTION_STATUSES}')
    if execution.get('failure_layer', 'missing') not in FAILURE_LAYERS:
        errors.append(f'execution.failure_layer must be one of {FAILURE_LAYERS}')
    if execution.get('status') == 'completed' and execution.get('failure_layer') is not None:
        errors.append('a completed attempt has no failure layer')
    if 'exit_status' not in execution:
        errors.append('execution.exit_status required (null when unknown)')
    errors.extend(_identity_errors(draft['identity'], draft['unknown']))
    errors.extend(_measurement_errors(draft['measurements']))
    criteria = {c['id'] for c in plan['criteria']}
    for cid, result in draft['outcome'].get('results', {}).items():
        if cid not in criteria:
            errors.append(f'outcome result for undeclared criterion {cid}')
        if result.get('result') not in RESULTS or not result.get('method') or not result.get('grader'):
            errors.append(f'outcome {cid} needs result in {RESULTS}, method and grader')
    review = draft['review']
    if review.get('independent') not in ('not-performed', 'performed'):
        errors.append('review.independent must be not-performed or performed')
    if review.get('independent') == 'performed' and not (review.get('reviewer') and review.get('method')):
        errors.append('a performed independent review names its reviewer and method')
    adoption = draft['adoption']
    if adoption.get('status') not in ('not-adopted', 'adopted'):
        errors.append('adoption.status must be not-adopted or adopted')
    if adoption.get('status') == 'adopted' and not (adoption.get('authority') and adoption.get('record')):
        errors.append('adoption requires the authorized owner and adoption record')
    for receipt in draft['receipts']:
        if not receipt.get('reference') or not _is_sha256(receipt.get('sha256')):
            errors.append('each receipt needs a reference and sha256')
    refuse(errors)


def _identity_errors(identity, unknown):
    errors = []
    if not isinstance(unknown, dict):
        return ['unknown must map identity paths to reasons']
    for path in REQUIRED_IDENTITY:
        found, _ = lookup(identity, path)
        if not found:
            errors.append(f'identity.{path} missing; record null with a reason instead of omitting it')
    leaves = flatten(identity)
    for path, value in leaves.items():
        if value is None and not unknown.get(path):
            errors.append(f'identity.{path} is null without a reason in unknown')
    for path, reason in unknown.items():
        if not reason:
            errors.append(f'unknown.{path} needs a reason')
        if path not in leaves:
            errors.append(f'unknown.{path} names no identity field')
        elif leaves[path] is not None:
            errors.append(f'identity.{path} has a value but is also declared unknown')
    if identity.get('subject', {}).get('worktree_status') not in ('clean', 'dirty', None):
        errors.append('identity.subject.worktree_status must be clean, dirty or null with a reason')
    return errors


def _measurement_errors(measurements):
    errors = []
    for name, m in measurements.items():
        for key in ('value', 'unit', 'source', 'kind'):
            if key not in m:
                errors.append(f'measurement {name} missing {key}')
        if m.get('kind') not in MEASUREMENT_KINDS:
            errors.append(f'measurement {name} kind must be one of {MEASUREMENT_KINDS}')
        if m.get('value') is None and not m.get('reason'):
            errors.append(f'measurement {name} is unknown without a reason')
        if m.get('value') is not None and not isinstance(m['value'], (int, float)):
            errors.append(f'measurement {name} value must be numeric or null')
    return errors


def seal_attempt(store, plan_sha, draft, sealed_at=None, receipt_root=None):
    """Seal one attempt. Failed, interrupted and blocked attempts are sealed exactly like completed ones."""
    plan = load_record(store, 'plans', plan_sha)
    validate_draft(draft, plan)
    if receipt_root is not None:
        errors = []
        for receipt in draft['receipts']:
            path = Path(receipt_root) / receipt['reference']
            if not path.is_file():
                errors.append(f'receipt {receipt["reference"]} not found under the receipt root')
            elif sha256_file(path) != receipt['sha256']:
                errors.append(f'receipt {receipt["reference"]} does not match its recorded sha256')
        refuse(errors)
    deviations = []
    for table in ('cases', 'rubrics'):
        declared = {name: entry['sha256'] for name, entry in plan[table].items()}
        if draft['identity'][table] != declared:
            deviations.append({'field': f'identity.{table}', 'plan': declared, 'attempt': draft['identity'][table]})
    record = {**draft, 'format': ATTEMPT_FORMAT, 'plan_sha256': plan_sha,
              'sealed_at_utc': sealed_at or utc_now(), 'plan_deviations': deviations}
    digest = sha256_bytes(canonical(record))
    existing = attempts_for(store, plan_sha)
    for other_digest, other in existing:
        if other['attempt_id'] == draft['attempt_id'] and other_digest != digest:
            raise TrialError(f'attempt {draft["attempt_id"]} is already sealed as {other_digest}; '
                             'record a new attempt id instead of rewriting')
    if digest not in {d for d, _ in existing}:
        used = sum(1 for _, other in existing if other['arm'] == draft['arm'])
        if used >= plan['budget']['max_attempts_per_arm']:
            raise TrialError(f'arm {draft["arm"]} already used its budget of '
                             f'{plan["budget"]["max_attempts_per_arm"]} attempts; stopping rule: '
                             f'{plan["budget"]["stopping_rule"]}')
    return _write_record(store, 'attempts', record)


def verify_store(store):
    """Re-hash every record, check plan bindings and report conflicting attempt ids."""
    errors, counts = [], {'plans': 0, 'attempts': 0}
    for kind in counts:
        folder = Path(store) / kind
        for path in sorted(folder.glob('*.json')) if folder.is_dir() else []:
            try:
                record = load_record(store, kind, path.stem)
                if kind == 'plans':
                    validate_plan(record)
                else:
                    if set(record) != SEALED_KEYS or record['format'] != ATTEMPT_FORMAT:
                        raise TrialError('attempt fields do not match the sealed format')
                    load_record(store, 'plans', record['plan_sha256'])
                counts[kind] += 1
            except (TrialError, ValueError, KeyError) as exc:
                errors.append(f'{kind}/{path.name}: {exc}')
    seen = {}
    for digest, record in _safe_attempts(store):
        key = (record['plan_sha256'], record['attempt_id'])
        if key in seen and seen[key] != digest:
            errors.append(f'attempt id {record["attempt_id"]} sealed twice with different content')
        seen.setdefault(key, digest)
    return {'verified': not errors, 'counts': counts, 'errors': errors}


def _safe_attempts(store):
    folder = Path(store) / 'attempts'
    for path in sorted(folder.glob('*.json')) if folder.is_dir() else []:
        try:
            yield path.stem, load_record(store, 'attempts', path.stem)
        except (TrialError, ValueError):
            continue


# --- identity helpers ------------------------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True).stdout


def snapshot(repo, relevant=None):
    """Identify a Git working tree by commit plus exact hashes of relevant dirty and untracked files.

    Nothing is copied. Ignored files are not inspected. Changes outside the relevant prefixes are counted, so
    the tree still reports dirty instead of looking clean.
    """
    repo = Path(repo)
    commit = _git(repo, 'rev-parse', 'HEAD').decode().strip()
    entries = _git(repo, 'status', '--porcelain=v1', '-z', '--untracked-files=all').decode().split('\0')
    dirty, untracked, excluded = {}, {}, 0
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in 'RC':
            index += 1
        if relevant and not any(path == p.rstrip('/') or path.startswith(p.rstrip('/') + '/') for p in relevant):
            excluded += 1
            continue
        full = repo / path
        if full.is_symlink():
            digest = sha256_bytes(os.readlink(full).encode())
        elif full.is_file():
            digest = sha256_file(full)
        else:
            digest = 'deleted'
        (untracked if code == '??' else dirty)[path] = digest
    status = 'dirty' if dirty or untracked or excluded else 'clean'
    return {'commit': commit, 'worktree_status': status, 'dirty': dirty, 'untracked': untracked,
            'excluded_changes': excluded, 'relevant_prefixes': sorted(relevant) if relevant else None,
            'ignored_files': 'not inspected'}


def file_set_digest(hashes):
    """One digest for a {path: sha256} mapping, so an artifact set has a single comparable identity."""
    return sha256_bytes(canonical(dict(sorted(hashes.items()))))


def runtime_identity():
    git_version = subprocess.run(['git', '--version'], capture_output=True, text=True).stdout.strip() or None
    return ({'python': platform.python_version(), 'implementation': platform.python_implementation(),
             'system': platform.system(), 'machine': platform.machine()},
            {'git': git_version})


# --- equipment fit ---------------------------------------------------------------------------------------

def classify_equipment(case, equipment, granted, observed=None):
    """Classify installed equipment against one case from declared metadata only.

    case: {'concerns': [...], 'constraints': {name: bool}}; equipment: {id: {'concern', 'requires', 'files'}}
    where files maps paths to pinned sha256; granted: plan permissions; observed: {id: {path: sha256 or None}}.
    This is task-fit, authority and freshness bookkeeping, not a prediction of model behavior.
    """
    by_concern = {}
    for eid, item in equipment.items():
        by_concern.setdefault(item['concern'], []).append(eid)
    report = {}
    for eid, item in sorted(equipment.items()):
        requires = item.get('requires', {})
        conflicts = [f'same-concern:{other}' for other in by_concern[item['concern']] if other != eid]
        conflicts += [f'case-constraint:{key}' for key, needed in sorted(requires.items())
                      if needed and case.get('constraints', {}).get(key) is False]
        exceeds = [key for key, needed in sorted(requires.items()) if needed and not granted.get(key)]
        if observed is None or eid not in observed:
            freshness = 'unknown'
        else:
            stale = sorted(p for p, pinned in item['files'].items() if observed[eid].get(p) != pinned)
            freshness = f'stale:{",".join(stale)}' if stale else 'current'
        report[eid] = {'task_fit': 'relevant' if item['concern'] in case.get('concerns', []) else 'irrelevant',
                       'conflict': conflicts or 'none',
                       'authority': f'exceeds-plan:{",".join(exceeds)}' if exceeds else 'within-plan',
                       'freshness': freshness}
    return report


def check_closure(agent_roots, evaluator_roots):
    """Confirm no evaluator-only file is inside, or byte-identical to a file in, the agent-readable closure."""
    agent_roots = [Path(p).resolve() for p in agent_roots]
    evaluator_roots = [Path(p).resolve() for p in evaluator_roots]
    errors = []
    for e in evaluator_roots:
        for a in agent_roots:
            if e == a or a in e.parents or e in a.parents:
                errors.append(f'evaluator root {e.name} overlaps agent-readable root {a.name}')
    evaluator_hashes = {}
    for root in evaluator_roots:
        for path in sorted(p for p in root.rglob('*') if p.is_file()):
            evaluator_hashes[sha256_file(path)] = path.relative_to(root).as_posix()
    agent_files = 0
    for root in agent_roots:
        for path in sorted(p for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts):
            agent_files += 1
            digest = sha256_file(path)
            if digest in evaluator_hashes:
                errors.append(f'{root.name}/{path.relative_to(root).as_posix()} equals evaluator-only '
                              f'{evaluator_hashes[digest]}')
    return {'closed': not errors, 'agent_files_checked': agent_files,
            'evaluator_files': len(evaluator_hashes), 'errors': errors}


# --- comparison ------------------------------------------------------------------------------------------

def _is_treatment(path, variables):
    return any(path == v or path.startswith(v + '.') for v in variables)


def control_identity(identity):
    """Identity without per-attempt provenance; each attempt necessarily has its own original artifacts."""
    return {k: v for k, v in identity.items() if k not in PER_ATTEMPT_IDENTITY}


def compare_pair(plan, a, b):
    variables = [v['path'].removeprefix('identity.') for v in plan['treatment']['variables']]
    left, right = flatten(control_identity(a['identity'])), flatten(control_identity(b['identity']))
    declared, unexpected = [], []
    unknown_either = a['unknown'].keys() | b['unknown'].keys()
    for path in sorted(left.keys() | right.keys()):
        lv, rv = left.get(path, '<absent>'), right.get(path, '<absent>')
        if lv == rv or (path in unknown_either and not _is_treatment(path, variables)):
            continue
        row = {'field': f'identity.{path}', 'a': lv, 'b': rv}
        (declared if _is_treatment(path, variables) else unexpected).append(row)
    unknown = sorted(p for p in unknown_either if not _is_treatment(p, variables))
    deviations = a['plan_deviations'] + b['plan_deviations']
    flags = []
    for side, rec in (('a', a), ('b', b)):
        if rec['record_kind'] == 'synthetic':
            flags.append(f'{side} is synthetic; never counted as a real model result')
        if rec['record_kind'] == 'historical-retrospective':
            flags.append(f'{side} is historical; no manifest was frozen before execution')
        if rec['execution']['status'] != 'completed':
            flags.append(f'{side} execution {rec["execution"]["status"]}; outcome not comparable')
    if unexpected or deviations:
        verdict = 'confounded'
    elif unknown:
        verdict = 'insufficiently-identified'
    elif not declared:
        verdict = 'replicate' if a['arm'] == b['arm'] else 'no-treatment-difference'
    else:
        verdict = 'comparable'
    return {'a': a['attempt_id'], 'b': b['attempt_id'], 'arms': [a['arm'], b['arm']], 'verdict': verdict,
            'outcome_comparable': verdict in ('comparable', 'replicate') and all(
                r['execution']['status'] == 'completed' for r in (a, b)),
            'declared_treatment_differences': declared, 'unexpected_control_differences': unexpected,
            'unidentified_controls': [{'field': f'identity.{p}', 'a': a['unknown'].get(p),
                                       'b': b['unknown'].get(p)} for p in unknown],
            'plan_deviations': deviations, 'flags': flags,
            'measurements': compare_measurements(a['measurements'], b['measurements']),
            'results': _compare_results(plan, a, b)}


def compare_measurements(a, b):
    rows = {}
    for name in sorted(a.keys() | b.keys()):
        ma, mb = a.get(name), b.get(name)
        row = {'a': ma, 'b': mb}
        if ma is None or mb is None:
            row['status'] = 'unknown: not recorded by one attempt'
        elif ma['value'] is None or mb['value'] is None:
            row['status'] = 'unknown: ' + '; '.join(m['reason'] for m in (ma, mb) if m['value'] is None)
        elif any(ma.get(k) != mb.get(k) for k in ('unit', 'denominator', 'semantics', 'kind')):
            row['status'] = 'incomparable: unit, denominator, semantics or estimate/billed kind differs'
        else:
            row['status'] = 'both measured'
            row['b_minus_a'] = mb['value'] - ma['value']
        rows[name] = row
    return rows


def _compare_results(plan, a, b):
    out = {}
    for criterion in plan['criteria']:
        cid = criterion['id']
        out[cid] = {'kind': criterion['kind'], 'owner': criterion['provenance']['owner'],
                    'independent_of_subject': criterion['independent_of_subject'],
                    'a': a['outcome'].get('results', {}).get(cid, {}).get('result', 'unknown'),
                    'b': b['outcome'].get('results', {}).get(cid, {}).get('result', 'unknown')}
    return out


def compare(store, plan_sha):
    plan = load_record(store, 'plans', plan_sha)
    attempts = sorted(attempts_for(store, plan_sha), key=lambda item: item[1]['attempt_id'])
    by_arm = {}
    for _, rec in attempts:
        by_arm.setdefault(rec['arm'], []).append(rec)
    arms = {}
    for arm in plan['treatment']['arms']:
        recs = by_arm.get(arm, [])
        groups = {sha256_bytes(canonical(control_identity(r['identity']))) for r in recs}
        completed = [r for r in recs if r['execution']['status'] == 'completed']
        arms[arm] = {'attempts': [r['attempt_id'] for r in recs],
                     'statuses': {r['attempt_id']: r['execution']['status'] for r in recs},
                     'identity_groups': len(groups),
                     'pooling': ('not pooled: attempts in this arm have different identities' if len(groups) > 1
                                 else 'single identity' if recs else 'no attempts'),
                     'completed': len(completed)}
    baseline = plan['treatment']['baseline_arm']
    pairs = []
    for base in by_arm.get(baseline, []):
        for arm, recs in sorted(by_arm.items()):
            for other in recs:
                if other is base or (arm == baseline and other['attempt_id'] < base['attempt_id']):
                    continue
                pairs.append(compare_pair(plan, base, other))
    return {'format': 'sandbox-trial-comparison-1', 'plan_sha256': plan_sha, 'plan_id': plan['plan_id'],
            'record_kind': plan['record_kind'], 'frozen_before_execution': plan['frozen_before_execution'],
            'treatment_variables': plan['treatment']['variables'], 'arms': arms, 'pairs': pairs,
            'attempts': {r['attempt_id']: {'arm': r['arm'], 'record_kind': r['record_kind'],
                                           'adapter': r['adapter']['kind'], 'execution': r['execution'],
                                           'independent_review': r['review']['independent'],
                                           'adoption': r['adoption']['status'],
                                           'unknown_identity_fields': sorted(r['unknown'])}
                         for _, r in attempts},
            'limits': 'Structural identity comparison. It does not establish usefulness, quality or adoption.'}


def render_markdown(report):
    lines = [f'# Comparison: {report["plan_id"]}', '',
             f'Plan `{report["plan_sha256"]}`; record kind {report["record_kind"]}; '
             f'frozen before execution: {str(report["frozen_before_execution"]).lower()}.', '',
             '| Attempt | Arm | Kind | Adapter | Execution | Independent review | Adoption |',
             '| --- | --- | --- | --- | --- | --- | --- |']
    for aid, att in report['attempts'].items():
        lines.append(f'| {aid} | {att["arm"]} | {att["record_kind"]} | {att["adapter"]} | '
                     f'{att["execution"]["status"]} | {att["independent_review"]} | {att["adoption"]} |')
    for arm, info in report['arms'].items():
        lines.append(f'\nArm {arm}: {len(info["attempts"])} attempts, {info["completed"]} completed; {info["pooling"]}.')
    for pair in report['pairs']:
        lines += ['', f'## {pair["a"]} vs {pair["b"]}: {pair["verdict"]}', '']
        lines += [f'- Flag: {flag}' for flag in pair['flags']]
        lines += [f'- Declared treatment: `{d["field"]}`' for d in pair['declared_treatment_differences']]
        lines += [f'- Unexpected control difference: `{d["field"]}`' for d in pair['unexpected_control_differences']]
        lines += [f'- Unidentified control: `{d["field"]}`' for d in pair['unidentified_controls']]
        lines += [f'- Plan deviation: `{d["field"]}`' for d in pair['plan_deviations']]
        lines += ['', '| Criterion | Kind | Owner | Independent of subject | A | B |', '| --- | --- | --- | --- | --- | --- |']
        for cid, row in pair['results'].items():
            lines.append(f'| {cid} | {row["kind"]} | {row["owner"]} | {str(row["independent_of_subject"]).lower()} '
                         f'| {row["a"]} | {row["b"]} |')
        lines += ['', '| Measurement | A | B | Status |', '| --- | --- | --- | --- |']
        for name, row in pair['measurements'].items():
            value = lambda m: 'absent' if m is None else ('null' if m['value'] is None else f'{m["value"]} {m["unit"]}')
            lines.append(f'| {name} | {value(row["a"])} | {value(row["b"])} | {row["status"]} |')
    lines += ['', report['limits'], '']
    return '\n'.join(lines)


# --- CLI -------------------------------------------------------------------------------------------------

def _load_json(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m harness.trials', description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('snapshot', help='identify a Git working tree without copying it')
    p.add_argument('--repo', required=True)
    p.add_argument('--relevant', action='append', help='path prefix to hash; repeatable')
    p = sub.add_parser('freeze-plan', help='validate and store a plan before execution')
    p.add_argument('--store', required=True)
    p.add_argument('--plan', required=True)
    p = sub.add_parser('seal', help='seal one attempt manifest bound to a frozen plan')
    p.add_argument('--store', required=True)
    p.add_argument('--plan-sha256', required=True)
    p.add_argument('--attempt', required=True)
    p.add_argument('--receipt-root', help='directory the attempt receipt references are relative to')
    p = sub.add_parser('verify', help='re-hash every stored record')
    p.add_argument('--store', required=True)
    p = sub.add_parser('compare', help='compare sealed attempts under one plan')
    p.add_argument('--store', required=True)
    p.add_argument('--plan-sha256', required=True)
    p.add_argument('--format', choices=('json', 'markdown'), default='json')
    args = parser.parse_args(argv)
    try:
        if args.command == 'snapshot':
            out = snapshot(args.repo, args.relevant)
        elif args.command == 'freeze-plan':
            out = {'plan_sha256': freeze_plan(args.store, _load_json(args.plan))}
        elif args.command == 'seal':
            out = {'attempt_sha256': seal_attempt(args.store, args.plan_sha256, _load_json(args.attempt),
                                                  receipt_root=args.receipt_root)}
        elif args.command == 'verify':
            out = verify_store(args.store)
        else:
            report = compare(args.store, args.plan_sha256)
            if args.format == 'markdown':
                sys.stdout.write(render_markdown(report))
                return 0
            out = report
    except (TrialError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'refused': str(exc)}), file=sys.stderr)
        return 2
    sys.stdout.write(canonical(out).decode())
    return 0 if out.get('verified', True) else 1


if __name__ == '__main__':
    sys.exit(main())
