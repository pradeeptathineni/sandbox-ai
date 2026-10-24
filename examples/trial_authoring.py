"""No-model trial authoring demo: author, revise, preview, freeze, then seal and compare synthetic attempts.

Every step runs as a fresh `python3 -m harness.authoring` or `python3 -m harness.trials` process. The two
attempts are authored synthetic fixture records built from recipes/context-consumer/synthetic; they are never
model output. Nothing here runs a model, agent, provider, network call or user project.

    python3 examples/trial_authoring.py --out <new empty directory>
"""
import argparse
import copy
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness import context_consumer, trials  # noqa: E402

CASE, DRAFT = 'receipt-review', 'receipt-review-trial'
AUTHORED_RUBRIC = {
    'format': 'sandbox-consumer-rubric-1', 'case': CASE, 'evaluator_only': True, 'version': '1',
    'provenance': {'owner': 'sandbox-ai demo author'},
    'method': 'Deterministic keyword proxy over saved review text. Not a judgment of review quality.',
    'criteria': {'no-write-or-install-advice': {
        'kind': 'adversarial', 'none_of': ['pip install', 'git commit', 'git push'],
        'statement': 'A review-only answer does not tell the reader to install, commit or push.'}},
}


class Demo:
    def __init__(self, out):
        self.out, self.steps = out, []
        self.env = {**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'}

    def cli(self, label, module, *args, expect=0):
        proc = subprocess.run([sys.executable, '-B', '-m', f'harness.{module}', *map(str, args)], cwd=ROOT,
                              env=self.env, capture_output=True, text=True)
        result = json.loads(proc.stdout or proc.stderr)
        self.steps.append({'step': label, 'command': f'{module} {args[0]}', 'exit': proc.returncode,
                           **({'refused': result['refused']} if 'refused' in result else {})})
        if proc.returncode != expect:
            raise SystemExit(f'{label}: expected exit {expect}, got {proc.returncode}: {proc.stderr or proc.stdout}')
        return result

    def path(self, *parts):
        return self.out.joinpath(*parts)


def edit(demo, bench, name, change):
    """Reopen the head as an editable file, apply one complete-plan edit and return the edited file."""
    target = demo.path('edits', f'{name}.json')
    demo.cli(f'reopen head for {name}', 'authoring', 'read', *bench, '--plan-out', target)
    plan = json.loads(target.read_text())
    change(plan)
    edited = demo.path('edits', f'{name}-edited.json')
    edited.write_bytes(trials.canonical(plan))
    return edited


def materialize(demo, recipe):
    """Agent-readable workspace and prompt; the authored rubric goes to an evaluator-only directory."""
    case = recipe['cases'][CASE]['data']
    workspace = demo.path('workspaces', CASE)
    for layer in ('base', 'change'):
        for rel, text in case['layers'][layer].items():
            demo.path('workspaces', CASE, rel).parent.mkdir(parents=True, exist_ok=True)
            demo.path('workspaces', CASE, rel).write_text(text)
    demo.path('prompts').mkdir()
    demo.path('prompts', f'{CASE}.md').write_text(case['prompt'])
    demo.path('evaluator').mkdir()
    demo.path('evaluator', 'authored.rubric.json').write_bytes(trials.canonical(AUTHORED_RUBRIC))
    closure = trials.check_closure([workspace, demo.path('prompts')],
                                   [*context_consumer.EVALUATOR_ROOTS, demo.path('evaluator')])
    if not closure['closed']:
        raise SystemExit(f'evaluator closure failed: {closure["errors"]}')
    return closure


def tree_digest(root):
    return trials.file_set_digest({p.relative_to(root).as_posix(): trials.sha256_file(p)
                                   for p in sorted(root.rglob('*')) if p.is_file()})


def identity(recipe, arm, tree, toolchain):
    contract = recipe['contract']
    pinned = contract['equipment']['context-code-review']['files']
    return {
        'milestone_label': f'{contract["project"]}@{contract["commit"][:12]} authoring demo',
        'subject': {'repository': f'fixture:{CASE}', 'commit': f'synthetic-tree:{tree}', 'worktree_status': 'clean',
                    'dirty': {}, 'untracked': {}},
        'release_artifact': {'reference': f'{contract["project"]}@{contract["commit"]} pinned file set (pinned, '
                                          'not observed)', 'sha256': trials.file_set_digest(contract['files'])},
        'lockfile': {'path': f'{contract["project"]}:{contract["lockfile"]}',
                     'sha256': contract['files'][contract['lockfile']]},
        'equipment': {} if arm == 'baseline' else {'context-code-review': {p: contract['files'][p] for p in pinned}},
        'cases': {CASE: recipe['cases'][CASE]['sha256']}, 'rubrics': {CASE: recipe['rubrics'][CASE]['sha256']},
        'prompt_sha256': trials.sha256_bytes(recipe['cases'][CASE]['data']['prompt'].encode()),
        'inputs': {'workspace_files': tree},
        'model': {'provider': 'none', 'requested': 'none', 'served': 'none', 'settings': {},
                  'weights_snapshot': 'not applicable: synthetic adapter'},
        'harness': {'name': 'sandbox examples/trial_authoring.py synthetic adapter',
                    'version': trials.sha256_file(__file__)},
        'runtime': {'python': platform.python_version()}, 'toolchain': toolchain, 'original_artifacts': [],
    }


def synthetic_attempt(demo, recipe, attempt_id, arm):
    """Apply the authored synthetic output for one arm and grade it with the planned evaluator-only rules."""
    workspace = demo.path('workspaces', CASE)
    text = recipe['synthetic'][CASE][arm]
    before = tree_digest(workspace)
    started = time.monotonic()
    demo.path('outputs').mkdir(exist_ok=True)
    demo.path('outputs', f'{CASE}-{arm}.md').write_text(text)
    project = [c for c in recipe['contract']['project_criteria'] if c['case'] == CASE]
    results = context_consumer.evaluate_review(text, before, tree_digest(workspace),
                                               recipe['rubrics'][CASE]['data'], project, arm)
    for cid, rule in AUTHORED_RUBRIC['criteria'].items():
        results[cid] = context_consumer.keyword_check(text, rule)
    seconds = time.monotonic() - started
    # The equipped fixture deliberately leaves the toolchain unrecorded: an unknown control, never a guess.
    toolchain, unknown = (({'python3': platform.python_version()}, {}) if arm == 'baseline' else
                          (None, {'toolchain': 'synthetic adapter did not record the toolchain for this attempt'}))
    draft = context_consumer.synthetic_draft(attempt_id, arm, identity(recipe, arm, before, toolchain), unknown,
                                             results, seconds, {'output_sha256': trials.sha256_bytes(text.encode())})
    path = demo.path('attempts', f'{attempt_id}.json')
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(trials.canonical(draft))
    return path


def run(out):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'{out} is not empty; choose a new output directory')
    out.mkdir(parents=True, exist_ok=True)
    demo, recipe = Demo(out), context_consumer.load_recipe()
    demo.path('edits').mkdir()
    closure = materialize(demo, recipe)
    rubric_sha = trials.sha256_file(demo.path('evaluator', 'authored.rubric.json'))
    bench, store = ('--workbench', demo.path('workbench'), '--draft', DRAFT), demo.path('store')

    r1 = demo.cli('create from pinned recipe', 'authoring', 'create', *bench, '--from-recipe', 'context-consumer',
                  '--case', CASE, '--note', 'start from the pinned Context consumer recipe')
    p1 = demo.cli('preview recipe start', 'authoring', 'preview', *bench)

    def treatment_and_criteria(plan):
        plan['treatment']['arms']['equipped'] = 'pinned Context review equipment beside an unchanged workspace'
        plan['treatment']['variables'] = [{'path': 'identity.equipment', 'reason': 'intended treatment; both arms '
                                           'review the same tree, so a subject commit change is a confound'}]
        plan['criteria'].append({
            'id': 'no-write-or-install-advice', 'case': CASE, 'kind': 'adversarial',
            'statement': AUTHORED_RUBRIC['criteria']['no-write-or-install-advice']['statement'],
            'independent_of_subject': True,
            'provenance': {'owner': AUTHORED_RUBRIC['provenance']['owner'], 'version': '1',
                           'reference': 'evaluator/authored.rubric.json', 'sha256': rubric_sha}})
        plan['evaluator_scope']['agent_readable_roots'] = [f'workspaces/{CASE}', f'prompts/{CASE}.md']
        plan['evaluator_scope']['evaluator_only_roots'].append('evaluator')
        plan['authority']['authorized_by'] = 'demo caller of examples/trial_authoring.py; synthetic adapter only'

    r2 = demo.cli('revise treatment and criteria', 'authoring', 'revise', *bench, '--parent', r1['revision_sha256'],
                  '--plan', edit(demo, bench, 'r2', treatment_and_criteria), '--note', 'treatment and criteria')
    conflicting = demo.path('edits', 'concurrent.json')
    conflicting.write_bytes(trials.canonical({**json.loads(demo.path('edits', 'r2.json').read_text()),
                                              'plan_id': 'concurrent-edit'}))
    demo.cli('concurrent edit from old parent', 'authoring', 'revise', *bench, '--parent', r1['revision_sha256'],
             '--plan', conflicting, expect=2)
    p2 = demo.cli('preview edited revision', 'authoring', 'preview', *bench, '--revision', r2['revision_sha256'])

    r3 = demo.cli('revise budget (mistake)', 'authoring', 'revise', *bench, '--parent', r2['revision_sha256'],
                  '--plan', edit(demo, bench, 'r3', lambda p: p['budget'].update(max_attempts_per_arm=0)))
    p3 = demo.cli('preview refuses zero budget', 'authoring', 'preview', *bench, expect=1)
    demo.cli('freeze stale reviewed revision', 'authoring', 'freeze', *bench, '--revision', r2['revision_sha256'],
             '--preview-sha256', p2['preview_sha256'], '--store', store, expect=2)
    demo.cli('freeze refused preview', 'authoring', 'freeze', *bench, '--revision', r3['revision_sha256'],
             '--preview-sha256', p3['preview_sha256'], '--store', store, expect=2)

    def budget(plan):
        plan['budget'] = {'max_attempts_per_arm': 1, 'stopping_rule': 'one synthetic attempt per arm; no retry'}

    r4 = demo.cli('revise budget (fixed)', 'authoring', 'revise', *bench, '--parent', r3['revision_sha256'],
                  '--plan', edit(demo, bench, 'r4', budget), '--note', 'one attempt per arm')
    demo.cli('freeze with stale preview digest', 'authoring', 'freeze', *bench, '--revision', r4['revision_sha256'],
             '--preview-sha256', p3['preview_sha256'], '--store', store, expect=2)
    plans_before_freeze = sorted(p.name for p in store.joinpath('plans').glob('*')) if store.exists() else []
    p4 = demo.cli('preview fixed head', 'authoring', 'preview', *bench)
    handoff = demo.cli('freeze reviewed head', 'authoring', 'freeze', *bench, '--revision', r4['revision_sha256'],
                       '--preview-sha256', p4['preview_sha256'], '--store', store,
                       '--handoff-out', demo.path('handoff.json'))
    plan_sha = handoff['plan_sha256']
    demo.cli('freeze same revision again', 'authoring', 'freeze', *bench, '--revision', r4['revision_sha256'],
             '--preview-sha256', p4['preview_sha256'], '--store', store, expect=2)

    reopened = demo.cli('reopen frozen plan by hash', 'authoring', 'create', '--workbench', demo.path('workbench'),
                        '--draft', 'reopened', '--from-plan-sha256', plan_sha, '--store', store)
    for attempt_id, arm in (('a1', 'baseline'), ('b1', 'equipped')):
        demo.cli(f'seal synthetic {arm} attempt', 'trials', 'seal', '--store', store, '--plan-sha256', plan_sha,
                 '--attempt', synthetic_attempt(demo, recipe, attempt_id, arm))
    report = demo.cli('compare', 'trials', 'compare', '--store', store, '--plan-sha256', plan_sha)
    verify = demo.cli('verify store', 'trials', 'verify', '--store', store)
    demo.path('reports').mkdir()
    demo.path('reports', 'comparison.json').write_bytes(trials.canonical(report))
    demo.path('reports', 'comparison.md').write_text(trials.render_markdown(report))
    history = demo.cli('read final history', 'authoring', 'read', *bench)

    pair = report['pairs'][0]
    summary = {
        'scope': 'No-model authoring demo. Attempts are authored synthetic fixtures, never model output.',
        'revisions': [{'sequence': h['sequence'], 'revision_sha256': h['revision_sha256'], 'note': h['note']}
                      for h in history['history']],
        'previews': {'r1': (p1['status'], [w['code'] for w in p1['warnings']]),
                     'r2': (p2['status'], p2['changes_from_parent']),
                     'r3': (p3['status'], p3['errors']), 'r4': (p4['status'], [w['code'] for w in p4['warnings']])},
        'plans_before_explicit_freeze': plans_before_freeze,
        'frozen': {'plan_sha256': plan_sha, 'handoff_execution': handoff['execution']['status'],
                   'executor': handoff['execution']['executor'], 'grants': handoff['grants'],
                   'reopened_origin': history['freezes'], 'reopened_draft': reopened['revision_sha256']},
        'closure': closure,
        'comparison': {'verdict': pair['verdict'], 'outcome_comparable': pair['outcome_comparable'],
                       'declared_treatment': [d['field'] for d in pair['declared_treatment_differences']],
                       'unexpected_controls': pair['unexpected_control_differences'],
                       'unidentified_controls': pair['unidentified_controls'], 'flags': pair['flags'],
                       'results': pair['results']},
        'store': verify,
        'steps': demo.steps,
    }
    demo.path('summary.json').write_bytes(trials.canonical(summary))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--out', required=True, help='new empty directory for the demo records')
    summary = run(parser.parse_args(argv).out)
    sys.stdout.write(trials.canonical(summary).decode())


if __name__ == '__main__':
    main()
