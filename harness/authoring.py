"""Trial authoring workbench: draft, revise, preview and explicitly freeze a trial plan.

A draft starts from a fully valid existing plan: a frozen plan in a trials store, a plan file, or the pinned
Context consumer recipe. Each revision is an immutable canonical JSON record linked to its parent and named by
its SHA-256 inside a host-chosen workbench directory. A revision is admitted only as the child of the current
head, so a stale or concurrent edit is refused instead of lost. Preview applies harness.trials.validate_plan and
reports criteria provenance, treatment, authority, budget and unknown values. Freeze requires the exact head
revision and the digest of its reviewed preview, passes the plan to harness.trials.freeze_plan and returns an
unexecuted host handoff.

Authoring and freezing grant no execution or adoption authority. Nothing here runs a model, agent, provider or
command. The authorized native host executes; harness.trials seal and compare remain the source of truth.
"""
import argparse
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path

from harness import context_consumer, trials

DRAFT_FORMAT = 'sandbox-trial-draft-1'
PREVIEW_FORMAT = 'sandbox-trial-preview-1'
HANDOFF_FORMAT = 'sandbox-trial-handoff-1'
REVISION_KEYS = {'format', 'draft_id', 'sequence', 'parent_sha256', 'created_at_utc', 'origin', 'plan', 'note'}
ORIGIN_KINDS = ('frozen-plan', 'plan-file', 'context-recipe')
MAX_BYTES = 1 << 20
MAX_REVISIONS = 256
MAX_NOTE = 500
DRAFT_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
FREEZE_PLACEHOLDER = '<set by freeze>'
IDENTITY_FIELDS = sorted({path.split('.')[0] for path in trials.REQUIRED_IDENTITY})
BROAD_VARIABLES = ('model', 'subject', 'harness', 'runtime', 'toolchain')
PLACEHOLDER_WORDS = ('tbd', 'todo', 'fixme', 'unknown', '?')
GRANTS = {'execution': False, 'adoption': False,
          'note': 'Authoring, previewing and freezing grant no execution or adoption authority.'}


class AuthoringError(trials.TrialError):
    """An authoring step was refused. The message says what to do next."""


def refuse(message):
    raise AuthoringError(message)


# --- bounded file access ---------------------------------------------------------------------------------

def read_bounded(path, limit=MAX_BYTES):
    """Read one regular file without following a final symlink or blocking on a FIFO."""
    name = Path(path).name
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        refuse(f'cannot read {name}: {exc.strerror} (symlinks are refused)')
    with os.fdopen(fd, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            refuse(f'{name} is not a regular file')
        data = handle.read(limit + 1)
    if len(data) > limit:
        refuse(f'{name} exceeds {limit} bytes')
    return data


def _parse(data, what):
    try:
        obj = json.loads(data)
        trials.canonical(obj)
    except (ValueError, RecursionError) as exc:
        refuse(f'{what} is not valid JSON: {exc}')
    return obj


def _create_exclusive(path, mode=0o400):
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)


def _real_dir(path, missing):
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        refuse(missing)
    if not stat.S_ISDIR(mode):
        refuse(f'{Path(path).name} is not a plain directory (symlinks are refused)')
    return Path(path)


def _draft_dir(workbench, draft_id, create=False):
    if not isinstance(draft_id, str) or not DRAFT_ID.fullmatch(draft_id):
        refuse('draft id must be 1-64 characters of a-z, 0-9 and -, starting with a letter or digit')
    drafts = Path(workbench) / 'drafts'
    if create:
        drafts.mkdir(mode=0o700, parents=True, exist_ok=True)
    _real_dir(drafts, 'no drafts in this workbench; create one first')
    folder = drafts / draft_id
    if create:
        try:
            folder.mkdir(mode=0o700)
        except FileExistsError:
            refuse(f'draft {draft_id} already exists; read it and revise from its head')
        (folder / 'revisions').mkdir(mode=0o700)
        (folder / 'freezes').mkdir(mode=0o700)
    for path in (folder, folder / 'revisions', folder / 'freezes'):
        _real_dir(path, f'no draft {draft_id} in this workbench; create it first')
    return folder


def _is_sha256(value):
    return trials._is_sha256(value)


# --- starting points -------------------------------------------------------------------------------------

def start_from_store(store, plan_sha256):
    """A frozen plan already in a trials store, reopened by its hash through harness.trials."""
    if not _is_sha256(plan_sha256):
        refuse('plan sha256 must be 64 lowercase hex digits')
    read_bounded(Path(store) / 'plans' / f'{plan_sha256}.json')
    plan = trials.load_record(store, 'plans', plan_sha256)
    return plan, {'kind': 'frozen-plan', 'plan_sha256': plan_sha256, 'plan_id': plan.get('plan_id'),
                  'frozen_at_utc': plan.get('frozen_at_utc')}


def start_from_file(path):
    data = read_bounded(path)
    return _parse(data, 'plan file'), {'kind': 'plan-file', 'reference': Path(path).name,
                                       'sha256': trials.sha256_bytes(data)}


def start_from_recipe(recipe, case):
    """The pinned Context consumer recipe plan for one case. No Context checkout is read."""
    if recipe != 'context-consumer':
        refuse('the only recipe is context-consumer')
    if case not in context_consumer.CASES:
        refuse(f'context-consumer case must be one of {context_consumer.CASES}')
    loaded = context_consumer.load_recipe()
    plan = context_consumer.build_plan(loaded, case, '<out>', FREEZE_PLACEHOLDER)
    return plan, {'kind': 'context-recipe', 'recipe': recipe, 'case': case,
                  'context_commit': loaded['contract']['commit'],
                  'contract_sha256': trials.sha256_file(context_consumer.RECIPE / 'contract.json'),
                  'case_sha256': loaded['cases'][case]['sha256'], 'rubric_sha256': loaded['rubrics'][case]['sha256']}


# --- revisions -------------------------------------------------------------------------------------------

def load_history(workbench, draft_id):
    """Return [(revision_sha256, record)] oldest first, verifying bytes, order, origin and parent links."""
    folder = _draft_dir(workbench, draft_id) / 'revisions'
    names = sorted(os.listdir(folder))
    if len(names) > MAX_REVISIONS:
        refuse(f'draft {draft_id} holds more than {MAX_REVISIONS} revisions')
    chain, parent = [], None
    for index, name in enumerate(names, 1):
        if name != f'{index:06d}.json':
            refuse(f'unexpected revision entry {name!r}; revisions run 000001.json onward without gaps')
        data = read_bounded(folder / name)
        record = _parse(data, name)
        if (not isinstance(record, dict) or set(record) != REVISION_KEYS or trials.canonical(record) != data
                or (record['format'], record['draft_id'], record['sequence'], record['parent_sha256'])
                != (DRAFT_FORMAT, draft_id, index, parent)
                or (chain and record['origin'] != chain[0][1]['origin'])):
            refuse(f'revision {name} of draft {draft_id} was altered or is out of order')
        parent = trials.sha256_bytes(data)
        chain.append((parent, record))
    if not chain:
        refuse(f'draft {draft_id} has no revisions')
    return chain


def _pick(chain, revision):
    if revision is None:
        return len(chain) - 1
    if not _is_sha256(revision):
        refuse('revision must be a 64-hex revision sha256')
    for index, (sha, _) in enumerate(chain):
        if sha == revision:
            return index
    refuse(f'revision {revision} is not part of this draft')


def _lineage_errors(plan, origin):
    was = origin.get('record_kind') == 'historical-retrospective'
    now = plan.get('record_kind') == 'historical-retrospective'
    if was and not now:
        return ['a historical-retrospective starting point stays historical; it cannot be relabeled real or synthetic']
    if now and not was:
        return ['a new draft cannot become historical-retrospective; historical plans come only from imported records']
    return []


def _check_note(note):
    if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE):
        refuse(f'note must be text of at most {MAX_NOTE} characters')


def _record(draft_id, chain, plan, origin, note, now):
    sequence = len(chain) + 1
    if sequence > MAX_REVISIONS:
        refuse(f'draft {draft_id} reached {MAX_REVISIONS} revisions; start a new draft from a frozen plan')
    _check_note(note)
    record = {'format': DRAFT_FORMAT, 'draft_id': draft_id, 'sequence': sequence,
              'parent_sha256': chain[-1][0] if chain else None, 'created_at_utc': now or trials.utc_now(),
              'origin': origin, 'plan': plan, 'note': note}
    data = trials.canonical(record)
    if len(data) > MAX_BYTES:
        refuse(f'revision exceeds {MAX_BYTES} bytes')
    return record, data


def _admit(folder, record, data):
    """Publish a complete revision under the next sequence name, or refuse if another writer took it."""
    pending = folder / f'.pending-{os.getpid()}-{secrets.token_hex(8)}'
    with os.fdopen(_create_exclusive(pending), 'wb') as handle:
        handle.write(data)
    try:
        os.link(pending, folder / 'revisions' / f'{record["sequence"]:06d}.json')
    except FileExistsError:
        refuse(f'draft {record["draft_id"]} changed while this revision was admitted; read the new head and '
               'revise from it')
    finally:
        pending.unlink()
    sha = trials.sha256_bytes(data)
    return {'draft_id': record['draft_id'], 'revision_sha256': sha, 'sequence': record['sequence'],
            'parent_sha256': record['parent_sha256'],
            'next': f'preview --draft {record["draft_id"]} --revision {sha}'}


def create(workbench, draft_id, plan, origin, note=None, now=None):
    """Start a private draft from a fully valid plan. frozen_at_utc is dropped; freeze sets it again."""
    if not isinstance(plan, dict):
        refuse('a starting plan must be a JSON object')
    if not isinstance(origin, dict) or origin.get('kind') not in ORIGIN_KINDS:
        refuse(f'origin.kind must be one of {ORIGIN_KINDS}')
    plan = {key: value for key, value in plan.items() if key != 'frozen_at_utc'}
    errors = validation_errors(plan)
    if errors:
        refuse('a draft starts from a fully valid plan: ' + '; '.join(errors))
    record, data = _record(draft_id, [], plan, {**origin, 'record_kind': plan['record_kind']}, note, now)
    return _admit(_draft_dir(workbench, draft_id, create=True), record, data)


def revise(workbench, draft_id, parent_sha256, plan, note=None, now=None):
    """Admit a complete edited plan as a new revision of the current head. It may still fail preview."""
    chain = load_history(workbench, draft_id)
    head_sha, head = chain[-1]
    if parent_sha256 != head_sha:
        refuse(f'stale parent: draft {draft_id} head is {head_sha} (revision {len(chain)}); read it, reapply '
               f'your edit and revise with --parent {head_sha}')
    if not isinstance(plan, dict):
        refuse('an edited plan must be a complete JSON object')
    if 'frozen_at_utc' in plan:
        refuse('remove frozen_at_utc from the edited plan; freeze sets it')
    errors = _lineage_errors(plan, head['origin'])
    if errors:
        refuse(errors[0])
    if plan == head['plan']:
        refuse('the edited plan equals the head revision; nothing to record')
    record, data = _record(draft_id, chain, plan, head['origin'], note, now)
    return _admit(_draft_dir(workbench, draft_id), record, data)


def read(workbench, draft_id, revision=None):
    chain = load_history(workbench, draft_id)
    index = _pick(chain, revision)
    sha, record = chain[index]
    return {'draft_id': draft_id, 'revision_sha256': sha, 'sequence': record['sequence'],
            'parent_sha256': record['parent_sha256'], 'head_sha256': chain[-1][0],
            'is_head': index == len(chain) - 1, 'origin': record['origin'], 'note': record['note'],
            'created_at_utc': record['created_at_utc'], 'plan': record['plan'],
            'history': [{'sequence': r['sequence'], 'revision_sha256': s, 'parent_sha256': r['parent_sha256'],
                         'created_at_utc': r['created_at_utc'], 'note': r['note']} for s, r in chain],
            'freezes': _freezes(workbench, draft_id)}


def _freezes(workbench, draft_id):
    folder = _draft_dir(workbench, draft_id) / 'freezes'
    out = []
    for name in sorted(os.listdir(folder))[:MAX_REVISIONS]:
        revision = name.removesuffix('.json')
        if not _is_sha256(revision) or not name.endswith('.json'):
            refuse(f'unexpected freeze entry {name!r}')
        data = read_bounded(folder / name)
        if not data:
            out.append({'revision_sha256': revision, 'plan_sha256': None,
                        'status': 'interrupted or in progress: claim written but no plan recorded; check the store'})
        else:
            out.append({'revision_sha256': revision, 'plan_sha256': _parse(data, name).get('plan_sha256'),
                        'status': 'frozen'})
    return out


# --- preview ---------------------------------------------------------------------------------------------

def validation_errors(plan):
    """Errors from the authoritative trials validator; freeze supplies frozen_at_utc itself."""
    try:
        trials.validate_plan({**plan, 'frozen_at_utc': FREEZE_PLACEHOLDER})
    except trials.TrialError as exc:
        return [str(exc)]
    except (AttributeError, KeyError, TypeError) as exc:
        return [f'plan structure is not readable by harness.trials ({type(exc).__name__}: {exc}); fix that field']
    return []


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _criteria(plan):
    return [c for c in _list(plan.get('criteria')) if isinstance(c, dict)]


def _summary(plan):
    treatment, criteria = _dict(plan.get('treatment')), _criteria(plan)
    counts = {}
    for c in criteria:
        counts[str(c.get('kind'))] = counts.get(str(c.get('kind')), 0) + 1
    return {
        'plan_id': plan.get('plan_id'), 'record_kind': plan.get('record_kind'),
        'frozen_before_execution': plan.get('frozen_before_execution'), 'decision': plan.get('decision'),
        'cases': {n: _dict(e).get('sha256') for n, e in _dict(plan.get('cases')).items()},
        'rubrics': {n: _dict(e).get('sha256') for n, e in _dict(plan.get('rubrics')).items()},
        'criteria': [{'id': c.get('id'), 'case': c.get('case'), 'kind': c.get('kind'),
                      'independent_of_subject': c.get('independent_of_subject'),
                      'owner': _dict(c.get('provenance')).get('owner'),
                      'reference': _dict(c.get('provenance')).get('reference'),
                      'statement': c.get('statement')} for c in criteria],
        'criteria_by_kind': counts,
        'treatment': {'baseline_arm': treatment.get('baseline_arm'), 'arms': treatment.get('arms'),
                      'variables': [_dict(v).get('path') for v in _list(treatment.get('variables'))]},
        'authority': plan.get('authority'), 'budget': plan.get('budget'),
        'evaluator_scope': plan.get('evaluator_scope'),
    }


def _unknown_paths(value, path=''):
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _unknown_paths(v, f'{path}.{k}' if path else str(k))]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _unknown_paths(v, f'{path}[{i}]')]
    if value is None or (isinstance(value, str) and (not value.strip() or value.strip().lower() in PLACEHOLDER_WORDS
                                                     or re.search(r'<[^<>]+>', value))):
        return [path]
    return []


def _warnings(plan, origin):
    warnings = []
    add = lambda code, detail: warnings.append({'code': code, 'detail': detail})
    kind = plan.get('record_kind')
    if kind == 'historical-retrospective':
        add('historical-record', 'Reconstructed after execution; it stays labeled historical and is not a '
                                 'pre-execution trial. Attempts can only be imported saved receipts.')
    elif kind == 'synthetic':
        add('synthetic-record', 'Attempts under this plan must be synthetic and are never model results.')
    if origin.get('record_kind') != kind:
        add('record-kind-changed', f'starting point was {origin.get("record_kind")}; this draft is {kind}')
    criteria = _criteria(plan)
    project = [str(c.get('id')) for c in criteria if c.get('kind') == 'project-supplied']
    if project:
        add('project-supplied-not-independent', f'{", ".join(project)} come from the subject project and are '
                                                'never independent evidence about it')
    if not any(c.get('kind') in ('consumer', 'adversarial') for c in criteria):
        add('no-independent-criteria', 'no consumer or adversarial criterion; nothing independent will be graded')
    elif not any(c.get('kind') == 'adversarial' for c in criteria):
        add('no-adversarial-criteria', 'no adversarial criterion probes what should not happen')
    for case in _dict(plan.get('cases')):
        if not any(c.get('case') == case for c in criteria):
            add('case-without-criteria', f'case {case} has no criterion')
    scope = _dict(plan.get('evaluator_scope'))
    agent = [str(r).rstrip('/') for r in _list(scope.get('agent_readable_roots'))]
    inside = lambda ref: any(ref == r or ref.startswith(r + '/') for r in agent)
    for root in (str(r).rstrip('/') for r in _list(scope.get('evaluator_only_roots'))):
        if inside(root) or any(r.startswith(root + '/') for r in agent):
            add('evaluator-scope-overlap', f'evaluator-only root {root} overlaps a declared agent-readable root')
    for c in criteria:
        if c.get('independent_of_subject') is True and inside(str(_dict(c.get('provenance')).get('reference'))):
            add('evaluator-answer-agent-readable', f'criterion {c.get("id")} reference lies inside a declared '
                                                   'agent-readable root; the agent could read its answer')
    treatment = _dict(plan.get('treatment'))
    if len(_dict(treatment.get('arms'))) < 2:
        add('single-arm', 'one arm only; compare can report replicates but no treatment effect')
    variables = [str(_dict(v).get('path', '')) for v in _list(treatment.get('variables'))]
    if not variables:
        add('no-treatment-variables', 'every identity difference between arms will be reported as confounded')
    for path in variables:
        field = path.removeprefix('identity.')
        if field.split('.')[0] not in IDENTITY_FIELDS:
            add('treatment-unknown-field', f'{path} names no required identity field ({", ".join(IDENTITY_FIELDS)})')
        elif field in BROAD_VARIABLES:
            add('treatment-broad', f'{path} declares every {field} field as treatment; unintended drift inside it '
                                   'will not be reported as a confound')
    granted = sorted(k for k, v in _dict(_dict(plan.get('authority')).get('permissions')).items() if v)
    if granted:
        add('permission-granted', f'plan permits {", ".join(granted)}; the executor must enforce exactly this')
    unknown = _unknown_paths(plan)
    if unknown:
        add('unknown-value', f'null or placeholder values at {", ".join(unknown[:20])}'
                             + (f' and {len(unknown) - 20} more' if len(unknown) > 20 else ''))
    return warnings


def _changes(old, new):
    old_c = {str(c.get('id')): c for c in _criteria(old)}
    new_c = {str(c.get('id')): c for c in _criteria(new)}
    rest = lambda plan: trials.flatten({k: v for k, v in plan.items() if k != 'criteria'})
    before, after = rest(old), rest(new)
    return {'criteria': {'added': sorted(new_c.keys() - old_c.keys()), 'removed': sorted(old_c.keys() - new_c.keys()),
                         'changed': sorted(k for k in old_c.keys() & new_c.keys() if old_c[k] != new_c[k])},
            'fields': [{'path': p, 'from': before.get(p, '<absent>'), 'to': after.get(p, '<absent>')}
                       for p in sorted(before.keys() | after.keys()) if before.get(p, '<absent>') != after.get(p, '<absent>')]}


def preview(workbench, draft_id, revision=None):
    """Validate one revision and report what freezing it would record.

    preview_sha256 covers every field except itself and head; freeze recomputes it, so any change to the
    revision, the validator or the warnings refuses the freeze.
    """
    chain = load_history(workbench, draft_id)
    index = _pick(chain, revision)
    sha, record = chain[index]
    plan = record['plan']
    errors = validation_errors(plan) + _lineage_errors(plan, record['origin'])
    body = {'format': PREVIEW_FORMAT, 'draft_id': draft_id, 'revision_sha256': sha, 'sequence': record['sequence'],
            'candidate_sha256': trials.sha256_bytes(trials.canonical(plan)),
            'status': 'refused' if errors else 'ready', 'errors': errors,
            'warnings': _warnings(plan, record['origin']), 'summary': _summary(plan), 'origin': record['origin'],
            'changes_from_parent': _changes(chain[index - 1][1]['plan'], plan) if index else None, 'grants': GRANTS}
    body['preview_sha256'] = trials.sha256_bytes(trials.canonical(body))
    body['head'] = {'revision_sha256': chain[-1][0], 'is_head': index == len(chain) - 1}
    return body


# --- freeze and handoff ----------------------------------------------------------------------------------

def _require_head(workbench, draft_id, revision):
    chain = load_history(workbench, draft_id)
    _pick(chain, revision)
    if revision != chain[-1][0]:
        refuse(f'stale revision: draft {draft_id} head is now {chain[-1][0]}; preview the head, review it and '
               'freeze that')
    return chain[-1][1]


def freeze(workbench, draft_id, revision, preview_sha256, store, frozen_at=None):
    """Freeze the exact reviewed head revision through harness.trials.freeze_plan and return its handoff."""
    if not _is_sha256(revision) or not _is_sha256(preview_sha256):
        refuse('freeze needs the exact 64-hex revision sha256 and preview sha256')
    head = _require_head(workbench, draft_id, revision)
    current = preview(workbench, draft_id, revision)
    if current['preview_sha256'] != preview_sha256:
        refuse(f'preview {preview_sha256} is not the current preview {current["preview_sha256"]} of revision '
               f'{revision}; re-run preview and review it before freezing')
    if current['status'] != 'ready':
        refuse('the preview is refused: ' + '; '.join(current['errors']))
    claim = _draft_dir(workbench, draft_id) / 'freezes' / f'{revision}.json'
    try:
        handle = os.fdopen(_create_exclusive(claim, 0o600), 'wb')
    except FileExistsError:
        refuse(f'revision {revision} was already frozen; read the draft for its plan, or revise to freeze a new one')
    try:
        _require_head(workbench, draft_id, revision)
        plan = {**head['plan'], 'frozen_at_utc': frozen_at or trials.utc_now()}
        plan_sha = trials.freeze_plan(store, plan)
        handoff = build_handoff(plan_sha, plan, draft_id, revision, preview_sha256, head['origin'])
        handle.write(trials.canonical(handoff))
    except BaseException:
        handle.close()
        claim.unlink(missing_ok=True)
        raise
    handle.close()
    os.chmod(claim, 0o400)
    return handoff


def build_handoff(plan_sha, plan, draft_id, revision, preview_sha, origin):
    """What a native host needs to set up an executor. It names no executor, command or approval."""
    authority, scope, treatment = plan['authority'], plan['evaluator_scope'], plan['treatment']
    historical = plan['record_kind'] == 'historical-retrospective'
    return {
        'format': HANDOFF_FORMAT, 'plan_sha256': plan_sha, 'plan_id': plan['plan_id'],
        'record_kind': plan['record_kind'], 'frozen_at_utc': plan['frozen_at_utc'],
        'frozen_before_execution': plan['frozen_before_execution'],
        'authored_from': {'draft_id': draft_id, 'revision_sha256': revision, 'preview_sha256': preview_sha,
                          'origin': origin},
        'arms': {'baseline': treatment['baseline_arm'], 'declared': sorted(treatment['arms']),
                 'treatment_variables': [v['path'] for v in treatment['variables']]},
        'cases': {name: entry['sha256'] for name, entry in plan['cases'].items()},
        'rubrics': {name: entry['sha256'] for name, entry in plan['rubrics'].items()},
        'criteria': {c['id']: {'kind': c['kind'], 'independent_of_subject': c['independent_of_subject'],
                               'owner': c['provenance']['owner']} for c in plan['criteria']},
        'execution': {
            'status': ('not-applicable: historical-retrospective; seal only imported saved receipts' if historical
                       else 'not-run'),
            'executor': None,
            'executor_setup_required': {
                'authorized_by': authority['authorized_by'], 'operations': authority['operations'],
                'model_execution': authority['model_execution'], 'isolation_owner': authority['isolation_owner'],
                'permissions': authority['permissions'], 'agent_readable_roots': scope['agent_readable_roots'],
                'evaluator_only_roots': scope['evaluator_only_roots'], 'grader': scope['grader'],
                'budget': plan['budget']},
            'note': 'Declared by the frozen plan, not granted by this handoff. The authorized native host names its '
                    'executor and reviewed command, enforces isolation and permissions, keeps evaluator-only roots '
                    'unreadable to the agent and seals every attempt, including failed and interrupted ones.'},
        'grants': GRANTS,
        'record_with': [f'python3 -m harness.trials seal --store <store> --plan-sha256 {plan_sha} '
                        '--attempt <attempt.json> --receipt-root <saved-receipts>',
                        f'python3 -m harness.trials compare --store <store> --plan-sha256 {plan_sha}'],
    }


# --- CLI -------------------------------------------------------------------------------------------------

def _write_new_file(path, data):
    try:
        fd = _create_exclusive(path, 0o600)
    except FileExistsError:
        refuse(f'{Path(path).name} exists; choose a new file')
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m harness.authoring', description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('create', help='start a draft from a valid stored plan, plan file or pinned recipe')
    p.add_argument('--workbench', required=True)
    p.add_argument('--draft', required=True)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--from-plan-sha256', help='frozen plan in --store')
    source.add_argument('--from-plan-file')
    source.add_argument('--from-recipe', choices=('context-consumer',))
    p.add_argument('--store', help='trials store holding --from-plan-sha256')
    p.add_argument('--case', help='recipe case for --from-recipe')
    p.add_argument('--note')
    p = sub.add_parser('read', help='reopen a draft revision with its history')
    p.add_argument('--workbench', required=True)
    p.add_argument('--draft', required=True)
    p.add_argument('--revision')
    p.add_argument('--plan-out', help='new file to receive the editable plan')
    p = sub.add_parser('revise', help='record a complete edited plan as a child of the head revision')
    p.add_argument('--workbench', required=True)
    p.add_argument('--draft', required=True)
    p.add_argument('--parent', required=True, help='revision sha256 the edit was made from')
    p.add_argument('--plan', required=True)
    p.add_argument('--note')
    p = sub.add_parser('preview', help='validate a revision and show what freezing it would record')
    p.add_argument('--workbench', required=True)
    p.add_argument('--draft', required=True)
    p.add_argument('--revision')
    p = sub.add_parser('freeze', help='freeze the exact reviewed head revision into a trials store')
    p.add_argument('--workbench', required=True)
    p.add_argument('--draft', required=True)
    p.add_argument('--revision', required=True)
    p.add_argument('--preview-sha256', required=True)
    p.add_argument('--store', required=True)
    p.add_argument('--handoff-out', help='new file to receive the host handoff')
    args = parser.parse_args(argv)
    try:
        if args.command == 'create':
            if args.from_plan_sha256:
                if not args.store:
                    refuse('--from-plan-sha256 needs --store')
                plan, origin = start_from_store(args.store, args.from_plan_sha256)
            elif args.from_plan_file:
                plan, origin = start_from_file(args.from_plan_file)
            else:
                plan, origin = start_from_recipe(args.from_recipe, args.case)
            out = create(args.workbench, args.draft, plan, origin, args.note)
        elif args.command == 'read':
            out = read(args.workbench, args.draft, args.revision)
            if args.plan_out:
                _write_new_file(args.plan_out, trials.canonical(out.pop('plan')))
                out['plan_written_to'] = Path(args.plan_out).name
        elif args.command == 'revise':
            plan, _ = start_from_file(args.plan)
            out = revise(args.workbench, args.draft, args.parent, plan, args.note)
        elif args.command == 'preview':
            out = preview(args.workbench, args.draft, args.revision)
        else:
            if args.handoff_out and os.path.lexists(args.handoff_out):
                refuse(f'{Path(args.handoff_out).name} exists; choose a new handoff file')
            out = freeze(args.workbench, args.draft, args.revision, args.preview_sha256, args.store)
            if args.handoff_out:
                _write_new_file(args.handoff_out, trials.canonical(out))
    except (trials.TrialError, ValueError, OSError) as exc:
        print(json.dumps({'refused': str(exc)}), file=sys.stderr)
        return 2
    sys.stdout.write(trials.canonical(out).decode())
    return 1 if out.get('status') == 'refused' else 0


if __name__ == '__main__':
    sys.exit(main())
