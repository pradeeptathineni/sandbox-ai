"""Context consumer recipe: prepare bounded native trials, run a no-model demo and import saved receipts.

Context is the first evaluated client. Context owns its claims and acceptance criteria; this recipe references
them by pinned path and hash (recipes/context-consumer/contract.json) and adds Sandbox-authored consumer and
adversarial criteria in an evaluator-only directory. No model, agent or provider is invoked here. The synthetic
adapter applies authored outputs that are labeled synthetic and never counted as model results.
"""
import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from harness import trials

RECIPE = Path(__file__).resolve().parents[1] / 'recipes' / 'context-consumer'
SANDBOX = RECIPE.parents[1]
CASES = ('receipt-review', 'task-requirements')
ARMS = ('baseline', 'equipped')
EVALUATOR_ROOTS = (RECIPE / 'evaluator', RECIPE / 'synthetic')
GIT = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false', '-c', 'core.autocrlf=false']
GIT_ENV = {'GIT_AUTHOR_NAME': 'Fixture Author', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
           'GIT_COMMITTER_NAME': 'Fixture Author', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
GRADER = 'harness.context_consumer deterministic checks'


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_recipe(recipe=RECIPE):
    recipe = Path(recipe)
    loaded = {'contract': _read_json(recipe / 'contract.json'), 'cases': {}, 'rubrics': {},
              'synthetic': _read_json(recipe / 'synthetic' / 'outputs.json')}
    for case_id in CASES:
        for table, path in (('cases', recipe / 'agent' / f'{case_id}.json'),
                            ('rubrics', recipe / 'evaluator' / f'{case_id}.rubric.json')):
            loaded[table][case_id] = {'data': _read_json(path), 'sha256': trials.sha256_file(path),
                                      'reference': path.relative_to(SANDBOX).as_posix()}
    return loaded


# --- Context identity ------------------------------------------------------------------------------------

def observe_context(source, contract):
    """Hash the pinned Context files in a caller-supplied source or artifact directory. Nothing is copied."""
    source = Path(source)
    files = {rel: (trials.sha256_file(source / rel) if (source / rel).is_file() else None)
             for rel in sorted(contract['files'])}
    try:
        snap = trials.snapshot(source, sorted(contract['files']))
    except (subprocess.CalledProcessError, FileNotFoundError):
        snap = None
    return {'files': files, 'snapshot': snap,
            'mismatched': sorted(rel for rel, digest in files.items() if digest != contract['files'][rel])}


def equipment_identity(contract, observed):
    snap = observed['snapshot']
    identity, unknown = {}, {}
    for eid, item in sorted(contract['equipment'].items()):
        files = {rel: observed['files'][rel] for rel in item['files']}
        identity[eid] = {'pinned_version': f'{contract["project"]}@{contract["commit"]}',
                         'source_commit': snap['commit'] if snap else None,
                         'source_worktree': (f'{snap["worktree_status"]}; {snap["excluded_changes"]} change(s) '
                                             'outside pinned files' if snap else None),
                         'files': files, 'sha256': trials.file_set_digest(files)}
        if not snap:
            for key in ('source_commit', 'source_worktree'):
                unknown[f'equipment.{eid}.{key}'] = 'caller-supplied Context path is not a Git checkout'
        for rel, digest in files.items():
            if digest is None:
                unknown[f'equipment.{eid}.files.{rel}'] = 'pinned file absent from caller-supplied Context path'
    return identity, unknown


def classify(case, contract, observed, permissions):
    equipment = {eid: {'concern': item['concern'], 'requires': item['requires'],
                       'files': {rel: contract['files'][rel] for rel in item['files']}}
                 for eid, item in contract['equipment'].items()}
    seen = {eid: {rel: observed['files'][rel] for rel in item['files']} for eid, item in equipment.items()}
    return trials.classify_equipment(case, equipment, permissions, seen)


# --- disposable workspaces -------------------------------------------------------------------------------

def _git(root, *args, date=None):
    env = {**os.environ, **GIT_ENV}
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run([*GIT, '-C', str(root), *args], check=True, capture_output=True, text=True, env=env).stdout


def _write(root, files):
    for name, text in files.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def materialize(case, arm, root, context_source, contract):
    """Build one fixture repository. The equipped arm commits pinned Context bytes before the task layer."""
    root = Path(root)
    root.mkdir(parents=True)
    _git(root, 'init', '--template=', '--initial-branch=main', '--quiet')
    info = {}
    for commit in case['commits']:
        if commit['layer'] == 'equipment':
            if arm != 'equipped':
                continue
            for item in contract['equipment'].values():
                for rel in item['files']:
                    src = Path(context_source) / rel
                    if src.is_file():
                        dest = root / '.context-ai' / 'resources' / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(src, dest)
        else:
            _write(root, case['layers'][commit['layer']])
        _git(root, 'add', '-A')
        _git(root, 'commit', '--quiet', '-m', commit['message'], date=commit['date'])
        if commit['layer'] == 'base':
            info['base_commit'] = _git(root, 'rev-parse', 'HEAD').strip()
            info['base_tree'] = _git(root, 'rev-parse', 'HEAD^{tree}').strip()
        if commit['layer'] == 'change':
            info['reviewed_diff'] = trials.sha256_bytes(_git(root, 'diff', 'HEAD~1', 'HEAD').encode())
    info['head'] = _git(root, 'rev-parse', 'HEAD').strip()
    info['snapshot'] = trials.snapshot(root)
    return info


# --- deterministic evaluation ----------------------------------------------------------------------------

def _result(value, detail):
    return {'result': value, 'method': 'deterministic', 'grader': GRADER, 'detail': detail}


def keyword_check(text, rule):
    lowered = text.lower()
    missing = [k for k in rule.get('all_of', []) if k.lower() not in lowered]
    any_ok = not rule.get('any_of') or any(k.lower() in lowered for k in rule['any_of'])
    forbidden = [k for k in rule.get('none_of', []) if k.lower() in lowered]
    passed = not missing and any_ok and not forbidden
    return _result('pass' if passed else 'fail',
                   {'missing': missing, 'any_of_matched': any_ok, 'forbidden_present': forbidden})


def evaluate_review(text, before, after, rubric, project_criteria, arm):
    results = {}
    for cid, rule in rubric['criteria'].items():
        if rule.get('check') == 'workspace-unchanged':
            same = before == after
            results[cid] = _result('pass' if same else 'fail', {'unchanged': same})
        else:
            results[cid] = keyword_check(text, rule)
    for criterion in project_criteria:
        results[criterion['id']] = _project_probe(criterion, arm, text)
    return results


def _project_probe(criterion, arm, text):
    if arm not in criterion['applies_to_arms']:
        return _result('not-applicable', 'project claim concerns delivered equipment only')
    if criterion['probe'].get('check') == 'transcript-required':
        return _result('unknown', 'needs an execution transcript; none exists for this attempt')
    return keyword_check(text or '', criterion['probe'])


def _functions(path):
    tree = ast.parse(Path(path).read_text())
    return {node.name: [a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
            for node in tree.body if isinstance(node, ast.FunctionDef)}


def _imports(root):
    names = {}
    for path in sorted(Path(root).rglob('*.py')):
        if '.git' in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                found = [a.name.split('.')[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                found = [node.module.split('.')[0]]
            else:
                continue
            for name in found:
                names.setdefault(name, []).append(path.relative_to(root).as_posix())
    return names


def evaluate_task(root, base_files, rubric, project_criteria, arm):
    root = Path(root)
    results = {}
    imports = _imports(root)
    for cid, rule in rubric['criteria'].items():
        check = rule['check']
        if check == 'signatures':
            found = _functions(root / rule['module'])
            wrong = {name: found.get(name) for name, params in rule['functions'].items() if found.get(name) != params}
            results[cid] = _result('fail' if wrong else 'pass', {'mismatched': wrong})
        elif check == 'no-network-imports':
            bad = sorted(name for name in imports if name in rule['forbidden'])
            results[cid] = _result('fail' if bad else 'pass', {'network_imports': bad})
        elif check == 'local-or-stdlib-imports':
            outside = sorted(n for n in imports if n not in sys.stdlib_module_names and n not in rule['local'])
            manifests = sorted(m for m in rule['manifests'] if (root / m).exists() and m not in base_files)
            results[cid] = _result('fail' if outside or manifests else 'pass',
                                   {'non_stdlib_imports': outside, 'new_manifests': manifests})
        elif check == 'tests':
            changed = sorted(p.relative_to(root).as_posix() for p in (root / 'tests').glob('test_*.py')
                             if base_files.get(p.relative_to(root).as_posix()) != p.read_text()
                             and rule['new_test_must_reference'] in p.read_text())
            env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
            run = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests'], cwd=root,
                                 capture_output=True, text=True, timeout=60, env=env)
            summary = (run.stderr.strip().splitlines() or [''])[-1]
            results[cid] = _result('pass' if changed and run.returncode == 0 else 'fail',
                                   {'new_or_changed_tests': changed, 'exit_status': run.returncode,
                                    'summary': summary})
        else:
            results[cid] = _result('unknown', f'unsupported check {check}')
    for criterion in project_criteria:
        results[criterion['id']] = _project_probe(criterion, arm, None)
    return results


# --- plans and drafts ------------------------------------------------------------------------------------

def build_plan(recipe, case_id, out_rel, frozen_at):
    contract, case, rubric = recipe['contract'], recipe['cases'][case_id], recipe['rubrics'][case_id]
    criteria = [{
        'id': c['id'], 'case': case_id, 'kind': 'project-supplied', 'statement': c['statement'],
        'independent_of_subject': False,
        'provenance': {'owner': contract['owner'], 'version': f'{contract["project"]}@{contract["commit"]}',
                       'reference': f'{contract["project"]}:{c["reference"]}', 'sha256': contract['files'][c['reference']]},
    } for c in contract['project_criteria'] if c['case'] == case_id]
    criteria += [{
        'id': cid, 'case': case_id, 'kind': rule['kind'], 'statement': rule['statement'],
        'independent_of_subject': True,
        'provenance': {'owner': rubric['data']['provenance']['owner'], 'version': rubric['data']['version'],
                       'reference': rubric['reference'], 'sha256': rubric['sha256']},
    } for cid, rule in sorted(rubric['data']['criteria'].items())]
    return {
        'format': trials.PLAN_FORMAT, 'plan_id': f'context-consumer-{case_id}', 'record_kind': 'synthetic',
        'frozen_at_utc': frozen_at, 'frozen_before_execution': True,
        'decision': {'question': f'Does pinned Context equipment change observable consumer outcomes on {case_id}?',
                     'owner': 'Sandbox lead (advisory only); Context keeps its own acceptance decision',
                     'consumer': contract['project']},
        'cases': {case_id: {'version': case['data']['version'], 'sha256': case['sha256'],
                            'provenance': {**case['data']['provenance'], 'reference': case['reference']}}},
        'rubrics': {case_id: {'version': rubric['data']['version'], 'sha256': rubric['sha256'],
                              'provenance': {**rubric['data']['provenance'], 'reference': rubric['reference']}}},
        'criteria': criteria,
        'treatment': {'baseline_arm': 'baseline',
                      'arms': {'baseline': 'project instructions and native tools only',
                               'equipped': 'pinned Context equipment bytes committed before the task layer'},
                      'variables': [
                          {'path': 'identity.equipment', 'reason': 'intended treatment'},
                          {'path': 'identity.subject.commit',
                           'reason': 'the equipment commit changes HEAD; base tree and reviewed diff stay pinned in '
                                     'identity.inputs'}]},
        'authority': {'authorized_by': 'caller of this recipe', 'operations': [
            'materialize disposable fixture repositories', 'apply authored synthetic outputs or import saved native '
            'receipts', 'run the fixture project test command', 'static checks'],
            'model_execution': 'not authorized by this recipe; a native host runs any model separately',
            'isolation_owner': 'authorized native host; this recipe adds no isolation',
            'permissions': {'network': False, 'tool_preapproval': False}},
        'budget': {'max_attempts_per_arm': 2,
                   'stopping_rule': 'one attempt per arm; at most one retry after a failed or interrupted attempt'},
        'evaluator_scope': {'agent_readable_roots': [f'{out_rel}/workspaces/{case_id}-{arm}' for arm in ARMS] +
                            [f'{out_rel}/prompts/{case_id}.md'],
                            'evaluator_only_roots': [p.relative_to(SANDBOX).as_posix() for p in EVALUATOR_ROOTS],
                            'grader': GRADER + '; no independent review'},
        'limitations': ['Synthetic recipe plan: a synthetic adapter result is never a model result.',
                        'Keyword and static checks are observable proxies, not judgments of quality or usefulness.',
                        'Equipment is pinned Context bytes, not a full Context lifecycle delivery with routers.'],
    }


def base_identity(recipe, case_id, arm, ws, observed, harness_id, runtime, toolchain):
    contract = recipe['contract']
    equipment, unknown = equipment_identity(contract, observed) if arm == 'equipped' else ({}, {})
    identity = {
        'milestone_label': f'{contract["project"]}@{contract["commit"][:12]} consumer recipe',
        'subject': {'repository': f'fixture:{case_id}', 'commit': ws['head'],
                    'worktree_status': ws['snapshot']['worktree_status'], 'dirty': ws['snapshot']['dirty'],
                    'untracked': ws['snapshot']['untracked']},
        'release_artifact': {'reference': f'{contract["project"]}@{contract["commit"]} pinned file set',
                             'sha256': trials.file_set_digest(observed['files'])},
        'lockfile': {'path': f'{contract["project"]}:{contract["lockfile"]}',
                     'sha256': observed['files'][contract['lockfile']]},
        'equipment': equipment,
        'cases': {case_id: recipe['cases'][case_id]['sha256']},
        'rubrics': {case_id: recipe['rubrics'][case_id]['sha256']},
        'prompt_sha256': trials.sha256_bytes(recipe['cases'][case_id]['data']['prompt'].encode()),
        'inputs': {k: ws[k] for k in ('base_tree', 'reviewed_diff') if k in ws},
        'model': {'provider': 'none', 'requested': 'none', 'served': 'none', 'settings': {},
                  'weights_snapshot': 'not applicable: synthetic adapter'},
        'harness': harness_id, 'runtime': runtime, 'toolchain': toolchain, 'original_artifacts': [],
    }
    if identity['lockfile']['sha256'] is None:
        unknown['lockfile.sha256'] = 'lockfile absent from caller-supplied Context path'
    return identity, unknown


def harness_identity():
    snap = trials.snapshot(SANDBOX, ['harness', 'recipes'])
    files = {p.relative_to(SANDBOX).as_posix(): trials.sha256_file(p)
             for root in ('harness', 'recipes') for p in sorted((SANDBOX / root).rglob('*'))
             if p.is_file() and '__pycache__' not in p.parts}
    return {'name': 'sandbox harness.context_consumer', 'version': f'sandbox-ai@{snap["commit"]}',
            'worktree_status': snap['worktree_status'], 'content_sha256': trials.file_set_digest(files)}


def unknown_measure(unit, reason, kind='observed'):
    return {'value': None, 'unit': unit, 'source': 'none', 'kind': kind, 'reason': reason}


def synthetic_draft(attempt_id, arm, identity, unknown, results, seconds, observations, status='completed'):
    no_model = 'synthetic adapter; no model ran'
    measurements = {
        'evaluator_check_seconds': (
            {'value': round(seconds, 6), 'unit': 'seconds', 'denominator': 'one evaluator pass',
             'source': 'time.monotonic around the deterministic checks', 'kind': 'observed',
             'semantics': 'evaluator wall time only; not model or task time'} if seconds is not None
            else unknown_measure('seconds', 'evaluator did not run: attempt did not complete')),
        'model_elapsed_seconds': unknown_measure('seconds', no_model),
        'input_tokens': unknown_measure('tokens', no_model),
        'cost': unknown_measure('USD', no_model, kind='billed'),
    }
    return {
        'attempt_id': attempt_id, 'arm': arm, 'record_kind': 'synthetic',
        'adapter': {'kind': 'synthetic-fixture',
                    'description': 'Applies authored outputs from recipes/context-consumer/synthetic; never a model result.'},
        'execution': ({'status': 'completed', 'failure_layer': None, 'exit_status': 0} if status == 'completed' else
                      {'status': status, 'failure_layer': 'harness', 'exit_status': None,
                       'detail': 'simulated stop before the adapter produced output; retained, not graded'}),
        'identity': identity, 'unknown': unknown, 'measurements': measurements,
        'outcome': {'results': results, 'observations': observations},
        'review': {'independent': 'not-performed', 'reviewer': None, 'method': None},
        'adoption': {'status': 'not-adopted', 'authority': None, 'record': None},
        'receipts': [], 'notes': 'Synthetic recipe attempt for exercising plan, seal, import and compare.',
    }


def native_template(identity, unknown, arm, attempt_id):
    """A not-run draft the native host completes from its saved receipts before sealing."""
    identity = json.loads(json.dumps(identity))
    identity['model'] = {'provider': None, 'requested': None, 'served': None, 'settings': None,
                         'weights_snapshot': None}
    identity['harness'] = {'name': None, 'version': None}
    reasons = {f'model.{k}': 'fill from the native receipt, or keep null with the reason it is unavailable'
               for k in identity['model']}
    reasons.update({f'harness.{k}': 'fill from the native host before sealing' for k in identity['harness']})
    return {'attempt_id': attempt_id, 'arm': arm, 'record_kind': 'real',
            'adapter': {'kind': 'native-host', 'description': 'saved native receipts imported after the host run'},
            'execution': {'status': 'not-run', 'failure_layer': None, 'exit_status': None},
            'identity': identity, 'unknown': {**unknown, **reasons}, 'measurements': {},
            'outcome': {'results': {}}, 'review': {'independent': 'not-performed', 'reviewer': None, 'method': None},
            'adoption': {'status': 'not-adopted', 'authority': None, 'record': None}, 'receipts': [],
            'notes': 'Template only. Nothing was executed.'}


# --- prepare and demo ------------------------------------------------------------------------------------

def _fresh_dir(out):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise trials.TrialError(f'{out} is not empty; choose a new output directory')
    out.mkdir(parents=True, exist_ok=True)
    return out


def prepare(context_source, out, frozen_at=None):
    """Materialize both arms per case, check the evaluator closure and freeze one plan per case."""
    out = _fresh_dir(out)
    recipe = load_recipe()
    contract = recipe['contract']
    observed = observe_context(context_source, contract)
    runtime, toolchain = trials.runtime_identity()
    harness_id = harness_identity()
    frozen_at = frozen_at or trials.utc_now()
    state = {'out': out, 'store': out / 'store', 'recipe': recipe, 'observed': observed, 'cases': {}}
    (out / 'prompts').mkdir()
    for case_id in CASES:
        case = recipe['cases'][case_id]['data']
        (out / 'prompts' / f'{case_id}.md').write_text(case['prompt'])
        ws = {arm: materialize(case, arm, out / 'workspaces' / f'{case_id}-{arm}', context_source, contract)
              for arm in ARMS}
        closure = trials.check_closure([out / 'workspaces' / f'{case_id}-{arm}' for arm in ARMS] +
                                       [out / 'prompts'], EVALUATOR_ROOTS)
        if not closure['closed']:
            raise trials.TrialError(f'evaluator closure failed for {case_id}: {closure["errors"]}')
        plan = build_plan(recipe, case_id, '<out>', frozen_at)
        plan_sha = trials.freeze_plan(state['store'], plan)
        identities = {arm: base_identity(recipe, case_id, arm, ws[arm], observed, harness_id, runtime, toolchain)
                      for arm in ARMS}
        templates = out / 'native-drafts'
        templates.mkdir(exist_ok=True)
        for arm in ARMS:
            draft = native_template(*identities[arm], arm, f'native-{arm}-1')
            (templates / f'{case_id}-{arm}.json').write_bytes(trials.canonical(draft))
        state['cases'][case_id] = {
            'plan_sha256': plan_sha, 'workspaces': ws, 'identities': identities, 'closure': closure,
            'equipment_fit': classify(case, contract, observed, plan['authority']['permissions'])}
    _write_native_recipe(out, state)
    return state


def _write_native_recipe(out, state):
    lines = ['# Prepared native consumer runs', '',
             'Prepared, not executed. This recipe ran no model, agent or host CLI. The authorized native host '
             'owns execution and isolation, and decides whether to run anything below.', '']
    for case_id, info in state['cases'].items():
        lines.append(f'- `{case_id}`: plan `{info["plan_sha256"]}`; workspaces '
                     f'`workspaces/{case_id}-baseline` and `workspaces/{case_id}-equipped`; prompt '
                     f'`prompts/{case_id}.md`; drafts `native-drafts/{case_id}-*.json`.')
    lines += ['', 'For each arm, a future authorized host would run its own reviewed command in that workspace with '
              'the prompt, for example `<HOST_CLI> <reviewed read-only flags> < prompts/<case>.md`, saving the '
              'response, events and usage files. Fill the matching draft from those saved receipts (status, exit, '
              'requested/served model and settings, harness version, measurements with units and sources, '
              'receipt references and SHA-256), keep unavailable fields null with a reason, then seal:', '',
              '```sh', 'python3 -m harness.trials seal --store store --plan-sha256 <plan> '
              '--attempt native-drafts/<case>-<arm>.json --receipt-root <saved-receipts-dir>', '```', '',
              'Grade saved outputs with the evaluator-only rubric afterwards; never copy evaluator files into a '
              'workspace.', '']
    (out / 'NATIVE-RECIPE.md').write_text('\n'.join(lines))


def run_demo(context_source, out, legacy_inputs=None, frozen_at=None):
    state = prepare(context_source, out, frozen_at)
    out, store, recipe = state['out'], state['store'], state['recipe']
    synthetic = recipe['synthetic']
    summary = {'scope': 'No-model demo. Synthetic adapter outputs are never model results.',
               'context_pin': {'commit': recipe['contract']['commit'], 'mismatched_files': state['observed']['mismatched'],
                               'source_snapshot': state['observed']['snapshot']},
               'cases': {}}
    reports = out / 'reports'
    reports.mkdir()
    for case_id in CASES:
        info = state['cases'][case_id]
        rubric = recipe['rubrics'][case_id]['data']
        project = [c for c in recipe['contract']['project_criteria'] if c['case'] == case_id]
        plan_sha = info['plan_sha256']
        plan_attempts = [('a1', 'baseline', 'completed'), ('b1', 'equipped', 'completed')]
        if case_id == 'task-requirements':
            plan_attempts = [('a1', 'baseline', 'completed'), ('b1', 'equipped', 'interrupted'),
                             ('b2', 'equipped', 'completed')]
        for attempt_id, arm, status in plan_attempts:
            identity, unknown = info['identities'][arm]
            root = out / 'workspaces' / f'{case_id}-{arm}'
            results, seconds, observations = {}, None, {}
            if status == 'completed':
                started = time.monotonic()
                if case_id == 'receipt-review':
                    text = synthetic[case_id][arm]
                    (out / 'outputs').mkdir(exist_ok=True)
                    (out / 'outputs' / f'{case_id}-{arm}.md').write_text(text)
                    before = info['workspaces'][arm]['snapshot']
                    after = trials.snapshot(root)
                    results = evaluate_review(text, before, after, rubric, project, arm)
                    observations = {'output_sha256': trials.sha256_bytes(text.encode())}
                else:
                    _write(root, synthetic[case_id]['compliant'])
                    base = recipe['cases'][case_id]['data']['layers']['base']
                    results = evaluate_task(root, base, rubric, project, arm)
                    observations = {'workspace_after': trials.snapshot(root)}
                seconds = time.monotonic() - started
            draft = synthetic_draft(attempt_id, arm, identity, unknown, results, seconds, observations, status)
            trials.seal_attempt(store, plan_sha, draft)
        report = trials.compare(store, plan_sha)
        (reports / f'{case_id}.json').write_bytes(trials.canonical(report))
        (reports / f'{case_id}.md').write_text(trials.render_markdown(report))
        summary['cases'][case_id] = {
            'plan_sha256': plan_sha, 'closure': info['closure'], 'equipment_fit': info['equipment_fit'],
            'verdicts': {f'{p["a"]}~{p["b"]}': p['verdict'] for p in report['pairs']},
            'statuses': {aid: a['execution']['status'] for aid, a in report['attempts'].items()}}
    summary['evaluator_discrimination'] = _discrimination(out, recipe)
    summary['integration_control'] = {'reviewed_diff_sha256': state['cases']['receipt-review']['workspaces']['baseline']['reviewed_diff'],
                                      'baseline_head': state['cases']['receipt-review']['workspaces']['baseline']['head']}
    if legacy_inputs:
        plan_sha, attempts = import_legacy(legacy_inputs, store, frozen_at)
        report = trials.compare(store, plan_sha)
        (reports / 'legacy-context-pair.json').write_bytes(trials.canonical(report))
        (reports / 'legacy-context-pair.md').write_text(trials.render_markdown(report))
        pre = _read_json(Path(legacy_inputs) / 'receipts/native-results/preflight.json')
        control = summary['integration_control']
        control['legacy_reviewed_diff_sha256'] = pre['arms']['shop-a']['review_diff_sha256']
        control['legacy_baseline_head'] = pre['arms']['shop-a']['before_head']
        control['diff_reproduced'] = control['reviewed_diff_sha256'] == control['legacy_reviewed_diff_sha256']
        control['baseline_head_reproduced'] = control['baseline_head'] == control['legacy_baseline_head']
        summary['legacy'] = {'plan_sha256': plan_sha, 'attempts': attempts,
                             'verdicts': {f'{p["a"]}~{p["b"]}': p['verdict'] for p in report['pairs']}}
    summary['store'] = trials.verify_store(store)
    (out / 'summary.json').write_bytes(trials.canonical(summary))
    return summary


def _discrimination(out, recipe):
    """Grade the authored violating output so the rubric is shown to fail what it should. Not an attempt."""
    case = recipe['cases']['task-requirements']['data']
    root = out / 'discrimination' / 'task-requirements-violating'
    root.mkdir(parents=True)
    _write(root, case['layers']['base'])
    _write(root, recipe['synthetic']['task-requirements']['violating'])
    results = evaluate_task(root, case['layers']['base'], recipe['rubrics']['task-requirements']['data'], [], 'baseline')
    return {cid: r['result'] for cid, r in results.items()}


# --- historical import -----------------------------------------------------------------------------------

LEGACY_REQUIRED = ('receipts/native-results/COMPARISON.json', 'receipts/native-results/preflight.json',
                   'receipts/native-results/matched-prompt.md', 'receipts/evaluator/fixture-build.json',
                   'receipts/evaluator/ground-truth.json', 'receipts/scripts/build_native_fixtures.py')
LEGACY_ARMS = {'shop-a': 'baseline', 'shop-b': 'equipped'}
USAGE_SEMANTICS = {
    'input_tokens': 'native CLI input tokens aggregated over turns; includes cached_input_tokens',
    'cached_input_tokens': 'subset of input_tokens; never add to it',
    'cache_write_input_tokens': 'reported by the native CLI; relation to input_tokens not stated',
    'output_tokens': 'native CLI output tokens aggregated over turns',
    'reasoning_output_tokens': 'reported separately; relation to output_tokens not stated; never add',
}


def import_legacy(inputs, store, imported_at=None):
    """Import the saved Context native pair as a historical-retrospective record. Refuses malformed input."""
    inputs = Path(inputs)
    errors = []
    try:
        manifest = _read_json(inputs / 'original-artifacts.json')
    except (OSError, ValueError) as exc:
        raise trials.TrialError(f'original-artifacts.json unreadable: {exc}')
    entries = {}
    for entry in manifest:
        if not all(entry.get(k) for k in ('logical_path', 'original_reference', 'sha256')):
            errors.append(f'artifact entry lacks logical_path, original_reference or sha256: {entry}')
            continue
        path = inputs / entry['logical_path']
        if not path.is_file():
            errors.append(f'{entry["logical_path"]} missing')
        elif trials.sha256_file(path) != entry['sha256']:
            errors.append(f'{entry["logical_path"]} does not match its original sha256')
        entries[entry['logical_path']] = entry
    errors += [f'{rel} not listed in original-artifacts.json' for rel in LEGACY_REQUIRED if rel not in entries]
    trials.refuse(errors)
    comparison = _read_json(inputs / 'receipts/native-results/COMPARISON.json')
    preflight = _read_json(inputs / 'receipts/native-results/preflight.json')
    build = _read_json(inputs / 'receipts/evaluator/fixture-build.json')
    errors += _legacy_shape_errors(comparison, preflight, build, entries)
    trials.refuse(errors)
    harness = re.fullmatch(r'(.+?) (\d+(?:\.\d+)+) (.+)', comparison['harness'].split(';')[0].strip())
    if not harness:
        raise trials.TrialError('COMPARISON.json harness does not name a tool and version')
    sha = {rel: entries[rel]['sha256'] for rel in entries}
    plan = _legacy_plan(comparison, sha, imported_at or trials.utc_now())
    plan_sha = trials.freeze_plan(store, plan)
    sealed = {}
    for arm_name, arm in LEGACY_ARMS.items():
        draft = _legacy_attempt(arm_name, arm, comparison, preflight, build, entries, sha, harness)
        sealed[arm_name] = trials.seal_attempt(store, plan_sha, draft, sealed_at=plan['frozen_at_utc'],
                                               receipt_root=inputs)
    return plan_sha, sealed


def _legacy_shape_errors(comparison, preflight, build, entries):
    errors = []
    need = ('model', 'effort', 'attempts', 'exit_status', 'completed', 'usage', 'found_defect', 'actionable_fix',
            'preserved_valid_behavior', 'false_positives', 'equipment_reads', 'structured_two_axis_report',
            'response_sha256', 'events_sha256', 'unchanged')
    for key in ('harness', 'prompt_sha256', 'authorization_scope', 'limitations', 'arms', 'judgment'):
        if key not in comparison:
            errors.append(f'COMPARISON.json missing {key}')
    if errors:
        return errors
    for arm_name in LEGACY_ARMS:
        arm = comparison['arms'].get(arm_name, {})
        errors += [f'COMPARISON.json arms.{arm_name} missing {k}' for k in need if k not in arm]
        pre = preflight.get('arms', {}).get(arm_name, {})
        errors += [f'preflight.json arms.{arm_name} missing {k}'
                   for k in ('before_head', 'before_status', 'review_diff_sha256') if k not in pre]
        if not isinstance(arm.get('usage'), dict) or not all(
                isinstance(arm['usage'].get(k), int) for k in USAGE_SEMANTICS):
            errors.append(f'arms.{arm_name}.usage lacks an integer counter; a missing counter is not zero')
        for kind in ('response', 'events'):
            rel = f'receipts/native-results/{arm_name}.{kind}.{"md" if kind == "response" else "jsonl"}'
            if rel not in entries or entries[rel]['sha256'] != arm.get(f'{kind}_sha256'):
                errors.append(f'{rel} is not the {kind} recorded for {arm_name}')
    if 'original_prompt_sha256' not in preflight:
        errors.append('preflight.json missing original_prompt_sha256')
    if preflight.get('prompt_sha256') != comparison['prompt_sha256']:
        errors.append('prompt hash differs between COMPARISON.json and preflight.json')
    if 'installed_skill_hashes' not in build:
        errors.append('fixture-build.json missing installed_skill_hashes')
    return errors


def _legacy_plan(comparison, sha, imported_at):
    builder, truth = 'receipts/scripts/build_native_fixtures.py', 'receipts/evaluator/ground-truth.json'
    coordinator = {'owner': 'context-equipment trial coordinator', 'version': 'ground-truth schema 1',
                   'reference': truth, 'sha256': sha[truth]}
    criteria = [{'id': cid, 'case': 'receipt-review', 'kind': 'consumer', 'statement': text,
                 'independent_of_subject': False, 'provenance': coordinator} for cid, text in (
        ('found-defect', 'Names shop/receipt.py and the stale price lookup or its $0.00 output.'),
        ('actionable-fix', 'Recommends price_cents with integer-cents formatting.'),
        ('preserved-valid-behavior', 'Does not recommend reverting the integer-cents migration.'),
        ('no-false-positives', 'Coordinator identified no false-positive finding.'))]
    criteria.append({'id': 'context-two-axis-report', 'case': 'receipt-review', 'kind': 'project-supplied',
                     'statement': 'The equipped review reports the Standards and Spec axes separately.',
                     'independent_of_subject': False,
                     'provenance': {'owner': 'context-ai', 'version': 'installed procedure bytes',
                                    'reference': 'context-ai:procedures/code-review.md',
                                    'sha256': 'f92246643f716226cbbe72f109be94485f6422af9f9a8a649effbce6e9820c93'}})
    return {
        'format': trials.PLAN_FORMAT, 'plan_id': 'context-native-review-pair-2026-10-21',
        'record_kind': 'historical-retrospective', 'frozen_at_utc': imported_at, 'frozen_before_execution': False,
        'decision': {'question': 'Did installed Context review equipment change a native review of one fixture?',
                     'owner': 'trial coordinator (historical); independent acceptance pending',
                     'consumer': 'context-ai'},
        'cases': {'receipt-review': {'version': 'historical fixture builder', 'sha256': sha[builder],
                                     'provenance': {'owner': 'context-equipment trial', 'reference': builder}}},
        'rubrics': {'receipt-review': {'version': 'ground-truth schema 1', 'sha256': sha[truth],
                                       'provenance': {'owner': 'context-equipment trial', 'reference': truth}}},
        'criteria': criteria,
        'treatment': {'baseline_arm': 'baseline',
                      'arms': {'baseline': 'shop-a: project instructions and native tools only',
                               'equipped': 'shop-b: review-equipment profile applied through the Context lifecycle'},
                      'variables': [
                          {'path': 'identity.equipment', 'reason': 'intended treatment'},
                          {'path': 'identity.subject.commit',
                           'reason': 'equipment commit precedes the reviewed change; the reviewed diff is pinned in '
                                     'identity.inputs.reviewed_diff'}]},
        'authority': {'authorized_by': 'trial coordinator (historical)', 'operations': [comparison['authorization_scope']],
                      'model_execution': 'performed before this import by the coordinator; this import runs none',
                      'isolation_owner': 'native CLI read-only sandbox (historical)',
                      'permissions': {'network': False, 'writes': False}},
        'budget': {'max_attempts_per_arm': 1, 'stopping_rule': 'one invocation per arm (historical preflight limit)'},
        'evaluator_scope': {'agent_readable_roots': ['fixture repository per arm (historical)'],
                            'evaluator_only_roots': ['receipts/evaluator'],
                            'grader': 'coordinator judgment recorded in COMPARISON.json; not independent'},
        'limitations': ['Reconstructed after execution; no manifest was frozen before the runs.',
                        'Not a newly qualified comparable trial; incomplete controls are not promoted.',
                        *comparison['limitations']],
    }


def _legacy_attempt(arm_name, arm, comparison, preflight, build, entries, sha, harness):
    rec, pre = comparison['arms'][arm_name], preflight['arms'][arm_name]
    unknown = {
        'release_artifact.reference': 'no Context release or package artifact recorded for this fixture',
        'release_artifact.sha256': 'no Context release or package artifact recorded for this fixture',
        'lockfile.path': 'consumer lockfile not recorded in staged receipts',
        'lockfile.sha256': 'consumer lockfile hash not recorded in staged receipts',
        'model.requested': 'no model override was passed; the CLI default was not recorded before execution',
        'model.weights_snapshot': 'hosted model identifier; no weights snapshot is exposed or implied',
        'runtime': 'host runtime not recorded in staged receipts',
        'toolchain': 'Python/Git versions used by the fixture build not recorded in staged receipts',
    }
    equipment = {}
    if arm == 'equipped':
        groups = {'context-code-review': 'code-review', 'context-tdd': 'tdd'}
        for eid, needle in groups.items():
            files = {p: h for p, h in sorted(build['installed_skill_hashes'].items())
                     if f'/{eid}/' in p or f'/{needle}' in p.removeprefix('.context-ai/resources')}
            equipment[eid] = {'pinned_version': None, 'files': files, 'sha256': trials.file_set_digest(files)}
            unknown[f'equipment.{eid}.pinned_version'] = ('Context commit used to apply the profile is not '
                                                          'recorded in the staged fixture build')
    arm_files = [rel for rel in entries if f'/{arm_name}.' in rel] + [
        'receipts/native-results/COMPARISON.json', 'receipts/native-results/preflight.json',
        'receipts/native-results/matched-prompt.md']
    usage = rec['usage'] or {}
    measurements = {name: {'value': usage[name], 'unit': 'tokens', 'kind': 'observed',
                           'denominator': 'one CLI invocation, aggregated over turns',
                           'source': 'COMPARISON.json usage from native CLI JSON events', 'semantics': text}
                    for name, text in USAGE_SEMANTICS.items()}
    measurements['elapsed_seconds'] = unknown_measure(
        'seconds', 'per-arm start recorded without a per-arm end; not derived retrospectively')
    measurements['cost'] = unknown_measure('USD', 'no billing data; token counts are not spend', kind='billed')
    grade = lambda ok: 'pass' if ok is True else 'fail' if ok is False else 'unknown'
    method, grader = 'coordinator judgment of the saved response (historical)', 'trial coordinator'
    results = {cid: {'result': grade(value), 'method': method, 'grader': grader} for cid, value in (
        ('found-defect', rec['found_defect']), ('actionable-fix', rec['actionable_fix']),
        ('preserved-valid-behavior', rec['preserved_valid_behavior']),
        ('no-false-positives', rec['false_positives'] == []))}
    results['context-two-axis-report'] = (
        {'result': grade(rec['structured_two_axis_report']), 'method': method, 'grader': grader} if arm == 'equipped'
        else {'result': 'not-applicable', 'method': method, 'grader': grader})
    observations = {'equipment_reads': rec['equipment_reads'], 'commands_recorded': len(rec.get('commands', [])),
                    'workspace_unchanged': rec['unchanged'], 'coordinator_judgment': comparison['judgment']}
    if arm == 'equipped':
        observations['context-tdd'] = 'installed but not read or invoked in this review-only task'
    return {
        'attempt_id': arm_name, 'arm': arm, 'record_kind': 'historical-retrospective',
        'adapter': {'kind': 'imported-receipts', 'description': 'saved native CLI receipts imported after execution'},
        'execution': {'status': 'completed' if rec['completed'] else 'failed',
                      'failure_layer': None if rec['completed'] else 'unknown', 'exit_status': rec['exit_status']},
        'identity': {
            'milestone_label': 'Context review equipment native pair 2026-10-21 (historical)',
            'subject': {'repository': 'fixture:receipt-review', 'commit': pre['before_head'],
                        'worktree_status': pre['before_status'], 'dirty': {}, 'untracked': {}},
            'release_artifact': {'reference': None, 'sha256': None},
            'lockfile': {'path': None, 'sha256': None},
            'equipment': equipment,
            'cases': {'receipt-review': sha['receipts/scripts/build_native_fixtures.py']},
            'rubrics': {'receipt-review': sha['receipts/evaluator/ground-truth.json']},
            'prompt_sha256': comparison['prompt_sha256'],
            'inputs': {'reviewed_diff': pre['review_diff_sha256'],
                       'unscoped_prompt': preflight['original_prompt_sha256']},
            'model': {'provider': 'native CLI default provider', 'requested': None, 'served': rec['model'],
                      'settings': {'effort': rec['effort']}, 'weights_snapshot': None},
            'harness': {'name': harness.group(1), 'version': harness.group(2), 'flags': harness.group(3)},
            'runtime': None, 'toolchain': None,
            'original_artifacts': [{'logical_path': rel, 'reference': entries[rel]['original_reference'],
                                    'sha256': entries[rel]['sha256']} for rel in sorted(arm_files)],
        },
        'unknown': unknown, 'measurements': measurements,
        'outcome': {'results': results, 'observations': observations},
        'review': {'independent': 'not-performed', 'reviewer': None, 'method': None},
        'adoption': {'status': 'not-adopted', 'authority': None, 'record': None},
        'receipts': [{'reference': rel, 'sha256': entries[rel]['sha256']} for rel in sorted(arm_files)],
        'notes': 'Historical import. Not a frozen pre-execution trial and not independently reviewed.',
    }


# --- CLI -------------------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m harness.context_consumer', description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='materialize both arms, freeze plans and write not-run native drafts')
    p.add_argument('--context-source', required=True, help='caller-supplied Context checkout or artifact directory')
    p.add_argument('--out', required=True)
    p = sub.add_parser('demo', help='prepare, then exercise seal/import/compare with the synthetic adapter')
    p.add_argument('--context-source', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--legacy-inputs', help='staged directory holding original-artifacts.json and saved receipts')
    p = sub.add_parser('import-legacy', help='import the saved Context native pair as historical evidence')
    p.add_argument('--inputs', required=True)
    p.add_argument('--store', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            state = prepare(args.context_source, args.out)
            out = {name: {'plan_sha256': info['plan_sha256'], 'closure': info['closure'],
                          'equipment_fit': info['equipment_fit']} for name, info in state['cases'].items()}
        elif args.command == 'demo':
            out = run_demo(args.context_source, args.out, args.legacy_inputs)
        else:
            plan_sha, attempts = import_legacy(args.inputs, args.store)
            out = {'plan_sha256': plan_sha, 'attempts': attempts}
    except (trials.TrialError, ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'refused': str(exc)}), file=sys.stderr)
        return 2
    sys.stdout.write(trials.canonical(out).decode())
    return 0


if __name__ == '__main__':
    sys.exit(main())
