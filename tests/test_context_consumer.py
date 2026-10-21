import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import context_consumer as cc
from harness import trials


def fake_context_source(root, contract, exact=()):
    """A synthetic stand-in for a caller-supplied Context checkout. Only paths listed in exact are absent."""
    root = Path(root)
    for rel in contract['files']:
        if rel in exact:
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'synthetic stand-in for {rel}\n')
    git = ['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
           '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null']
    subprocess.run([*git, 'init', '--quiet', '--template='], check=True)
    subprocess.run([*git, 'add', '-A'], check=True)
    subprocess.run([*git, 'commit', '--quiet', '-m', 'synthetic context'], check=True)
    return root


def synthetic_legacy(root):
    """Synthetic files shaped like the saved Context native pair. Values are invented."""
    root = Path(root)
    files = {
        'receipts/native-results/matched-prompt.md': 'Review the most recent commit.\n',
        'receipts/native-results/shop-a.response.md': 'synthetic baseline response\n',
        'receipts/native-results/shop-b.response.md': 'synthetic equipped response\n',
        'receipts/native-results/shop-a.events.jsonl': '{"synthetic": "a"}\n',
        'receipts/native-results/shop-b.events.jsonl': '{"synthetic": "b"}\n',
        'receipts/evaluator/ground-truth.json': '{"evaluator_only": true}\n',
        'receipts/scripts/build_native_fixtures.py': '# synthetic builder\n',
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    prompt_sha = trials.sha256_file(root / 'receipts/native-results/matched-prompt.md')
    arm = lambda name, equipped: {
        'model': 'synthetic-model', 'effort': 'low', 'attempts': 1, 'exit_status': 0, 'completed': True,
        'usage': {'input_tokens': 100, 'cached_input_tokens': 60, 'cache_write_input_tokens': 0,
                  'output_tokens': 10, 'reasoning_output_tokens': 2},
        'commands': [], 'found_defect': True, 'actionable_fix': True, 'preserved_valid_behavior': True,
        'false_positives': [], 'equipment_reads': {'router': equipped}, 'structured_two_axis_report': equipped,
        'unchanged': {'head': name}, 'response_sha256': trials.sha256_file(root / f'receipts/native-results/{name}.response.md'),
        'events_sha256': trials.sha256_file(root / f'receipts/native-results/{name}.events.jsonl')}
    comparison = {'harness': 'Synthetic CLI 1.2.3 --read-only', 'prompt_sha256': prompt_sha,
                  'authorization_scope': 'synthetic', 'judgment': 'synthetic tie', 'limitations': ['synthetic'],
                  'arms': {'shop-a': arm('shop-a', False), 'shop-b': arm('shop-b', True)}}
    preflight = {'prompt_sha256': prompt_sha, 'original_prompt_sha256': 'e' * 64,
                 'arms': {name: {'before_head': head * 40, 'before_status': 'clean', 'review_diff_sha256': 'f' * 64}
                          for name, head in (('shop-a', 'a'), ('shop-b', 'b'))}}
    build = {'installed_skill_hashes': {'.agents/skills/context-code-review/SKILL.md': 'c' * 64,
                                        '.agents/skills/context-tdd/SKILL.md': 'd' * 64}}
    for rel, data in (('receipts/native-results/COMPARISON.json', comparison),
                      ('receipts/native-results/preflight.json', preflight),
                      ('receipts/evaluator/fixture-build.json', build)):
        (root / rel).write_text(json.dumps(data, indent=2) + '\n')
    rels = sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file())
    manifest = [{'logical_path': rel, 'original_reference': f'synthetic://{rel}',
                 'sha256': trials.sha256_file(root / rel)} for rel in rels]
    (root / 'original-artifacts.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return root


class Demo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        contract = cc.load_recipe()['contract']
        source = fake_context_source(Path(cls.tmp.name) / 'context', contract)
        cls.out = Path(cls.tmp.name) / 'demo'
        cls.summary = cc.run_demo(source, cls.out, synthetic_legacy(Path(cls.tmp.name) / 'legacy'),
                                  frozen_at='2026-10-21T00:00:00+00:00')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_plan_seal_compare_runs_end_to_end(self):
        self.assertTrue(self.summary['store']['verified'], self.summary['store'])
        self.assertEqual(self.summary['cases']['receipt-review']['verdicts'], {'a1~b1': 'comparable'})
        task = self.summary['cases']['task-requirements']
        self.assertEqual(task['statuses'], {'a1': 'completed', 'b1': 'interrupted', 'b2': 'completed'})
        self.assertEqual(set(task['verdicts'].values()), {'comparable'})

    def test_interrupted_attempt_is_retained_but_not_outcome_comparable(self):
        report = json.loads((self.out / 'reports/task-requirements.json').read_text())
        pairs = {(p['a'], p['b']): p for p in report['pairs']}
        self.assertFalse(pairs[('a1', 'b1')]['outcome_comparable'])
        self.assertTrue(pairs[('a1', 'b2')]['outcome_comparable'])
        self.assertEqual(report['arms']['equipped']['pooling'], 'single identity')

    def test_synthetic_results_are_labeled_and_never_model_results(self):
        report = json.loads((self.out / 'reports/receipt-review.json').read_text())
        pair = report['pairs'][0]
        self.assertIn('a is synthetic; never counted as a real model result', pair['flags'])
        self.assertTrue(pair['measurements']['input_tokens']['status'].startswith('unknown'))
        self.assertEqual(report['attempts']['a1']['adapter'], 'synthetic-fixture')

    def test_project_criteria_keep_owner_and_are_not_independent(self):
        results = json.loads((self.out / 'reports/receipt-review.json').read_text())['pairs'][0]['results']
        self.assertEqual(results['context-two-axis-report'],
                         {'kind': 'project-supplied', 'owner': 'context-ai', 'independent_of_subject': False,
                          'a': 'not-applicable', 'b': 'pass'})
        self.assertEqual(results['found-stale-price-reference']['owner'], 'sandbox-ai')
        self.assertEqual(results['review-left-workspace-unchanged']['b'], 'pass')
        task = json.loads((self.out / 'reports/task-requirements.json').read_text())['pairs'][0]['results']
        self.assertEqual(task['context-red-green']['b'], 'unknown')
        self.assertEqual({task[c]['a'] for c in ('api-preserved', 'feature-present', 'offline',
                                                 'no-new-dependencies', 'tests-added-and-pass')}, {'pass'})

    def test_rubric_discriminates_the_violating_output(self):
        self.assertEqual(self.summary['evaluator_discrimination'],
                         {'api-preserved': 'fail', 'feature-present': 'pass', 'no-new-dependencies': 'fail',
                          'offline': 'fail', 'tests-added-and-pass': 'fail'})

    def test_stale_and_irrelevant_equipment_is_classified(self):
        fit = self.summary['cases']['receipt-review']['equipment_fit']
        self.assertEqual(fit['context-code-review']['task_fit'], 'relevant')
        self.assertEqual(fit['context-tdd']['task_fit'], 'irrelevant')
        self.assertTrue(fit['context-code-review']['freshness'].startswith('stale:'))
        self.assertEqual(fit['context-code-review']['authority'], 'within-plan')

    def test_evaluator_answers_stay_outside_agent_closure(self):
        for case in self.summary['cases'].values():
            self.assertTrue(case['closure']['closed'])
            self.assertGreater(case['closure']['evaluator_files'], 0)
        for path in (self.out / 'workspaces').rglob('*'):
            if path.is_file() and '.git' not in path.parts:
                self.assertNotIn('evaluator_only', path.read_text(errors='ignore'))

    def test_native_drafts_are_not_run_and_cannot_pass_as_complete(self):
        draft = json.loads((self.out / 'native-drafts/receipt-review-equipped.json').read_text())
        self.assertEqual(draft['execution']['status'], 'not-run')
        self.assertIsNone(draft['identity']['model']['served'])
        self.assertIn('model.served', draft['unknown'])
        self.assertIn('Prepared, not executed', (self.out / 'NATIVE-RECIPE.md').read_text())

    def test_legacy_pair_is_historical_and_insufficiently_identified(self):
        self.assertEqual(self.summary['legacy']['verdicts'], {'shop-a~shop-b': 'insufficiently-identified'})
        report = json.loads((self.out / 'reports/legacy-context-pair.json').read_text())
        self.assertFalse(report['frozen_before_execution'])
        self.assertEqual(report['record_kind'], 'historical-retrospective')
        pair = report['pairs'][0]
        self.assertIn('a is historical; no manifest was frozen before execution', pair['flags'])
        unknown = {row['field'] for row in pair['unidentified_controls']}
        self.assertTrue({'identity.lockfile.sha256', 'identity.runtime', 'identity.toolchain',
                         'identity.release_artifact.sha256', 'identity.model.requested'} <= unknown)
        self.assertTrue(pair['measurements']['elapsed_seconds']['status'].startswith('unknown'))
        self.assertEqual(pair['measurements']['cached_input_tokens']['b_minus_a'], 0)


class LegacyRefusals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inputs = synthetic_legacy(Path(self.tmp.name) / 'legacy')
        self.store = Path(self.tmp.name) / 'store'

    def tearDown(self):
        self.tmp.cleanup()

    def rewrite_comparison(self, mutate):
        path = self.inputs / 'receipts/native-results/COMPARISON.json'
        data = json.loads(path.read_text())
        mutate(data)
        path.write_text(json.dumps(data))
        manifest = json.loads((self.inputs / 'original-artifacts.json').read_text())
        for entry in manifest:
            if entry['logical_path'] == 'receipts/native-results/COMPARISON.json':
                entry['sha256'] = trials.sha256_file(path)
        (self.inputs / 'original-artifacts.json').write_text(json.dumps(manifest))

    def test_altered_receipt_is_refused(self):
        (self.inputs / 'receipts/native-results/shop-b.response.md').write_text('edited\n')
        with self.assertRaisesRegex(trials.TrialError, 'does not match its original sha256'):
            cc.import_legacy(self.inputs, self.store)

    def test_missing_counter_is_refused_not_zero(self):
        self.rewrite_comparison(lambda d: d['arms']['shop-a']['usage'].pop('output_tokens'))
        with self.assertRaisesRegex(trials.TrialError, 'missing counter is not zero'):
            cc.import_legacy(self.inputs, self.store)

    def test_missing_outcome_field_is_refused(self):
        self.rewrite_comparison(lambda d: d['arms']['shop-b'].pop('found_defect'))
        with self.assertRaisesRegex(trials.TrialError, 'missing found_defect'):
            cc.import_legacy(self.inputs, self.store)

    def test_unlisted_required_artifact_is_refused(self):
        manifest = json.loads((self.inputs / 'original-artifacts.json').read_text())
        manifest = [e for e in manifest if not e['logical_path'].endswith('ground-truth.json')]
        (self.inputs / 'original-artifacts.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(trials.TrialError, 'not listed'):
            cc.import_legacy(self.inputs, self.store)


class ContractFit(unittest.TestCase):
    def test_current_pin_and_case_constraint_conflict(self):
        contract = cc.load_recipe()['contract']
        observed = {'files': dict(contract['files'])}
        case = {'concerns': ['code-review'], 'constraints': {'network': False}}
        fit = cc.classify(case, contract, observed, {'network': False, 'tool_preapproval': False})
        self.assertEqual(fit['context-code-review']['freshness'], 'current')
        conflicted = json.loads(json.dumps(contract))
        conflicted['equipment']['context-tdd']['requires']['network'] = True
        fit = cc.classify(case, conflicted, observed, {'network': False})
        self.assertEqual(fit['context-tdd']['conflict'], ['case-constraint:network'])
        self.assertEqual(fit['context-tdd']['authority'], 'exceeds-plan:network')


if __name__ == '__main__':
    unittest.main()
