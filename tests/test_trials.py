import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from harness import trials

SHA = {name: trials.sha256_bytes(name.encode()) for name in
       ('case', 'case2', 'rubric', 'rubric2', 'prompt', 'prompt2', 'artifact', 'artifact2', 'lock', 'equip', 'ref')}


def make_plan(**changes):
    plan = {
        'format': trials.PLAN_FORMAT, 'plan_id': 'synthetic-plan', 'record_kind': 'synthetic',
        'frozen_at_utc': '2026-10-21T00:00:00+00:00', 'frozen_before_execution': True,
        'decision': {'question': 'Does equipment change the review?', 'owner': 'example owner', 'consumer': 'example'},
        'cases': {'review': {'version': '1', 'sha256': SHA['case'], 'provenance': {'owner': 'sandbox-ai'}}},
        'rubrics': {'review': {'version': '1', 'sha256': SHA['rubric'], 'provenance': {'owner': 'sandbox-ai'}}},
        'criteria': [
            {'id': 'project-claim', 'case': 'review', 'kind': 'project-supplied', 'independent_of_subject': False,
             'statement': 'Equipped report has two axes',
             'provenance': {'owner': 'example-project', 'version': 'abc', 'reference': 'docs/claim.md', 'sha256': SHA['ref']}},
            {'id': 'found-defect', 'case': 'review', 'kind': 'consumer', 'independent_of_subject': True,
             'statement': 'Names the stale reference',
             'provenance': {'owner': 'sandbox-ai', 'version': '1', 'reference': 'evaluator/rubric.json', 'sha256': SHA['rubric']}},
        ],
        'treatment': {'baseline_arm': 'baseline', 'arms': {'baseline': 'native', 'equipped': 'with equipment'},
                      'variables': [{'path': 'identity.equipment', 'reason': 'intended treatment'}]},
        'authority': {'authorized_by': 'example', 'operations': ['read-only review'], 'model_execution': 'not-authorized',
                      'isolation_owner': 'native host', 'permissions': {'network': False}},
        'budget': {'max_attempts_per_arm': 2, 'stopping_rule': 'stop after two attempts per arm'},
        'evaluator_scope': {'agent_readable_roots': ['workspace'], 'evaluator_only_roots': ['evaluator'], 'grader': 'deterministic'},
        'limitations': [],
    }
    plan.update(changes)
    return plan


def make_attempt(attempt_id='a1', arm='baseline', **identity_changes):
    identity = {
        'milestone_label': 'example 1.0',
        'subject': {'repository': 'fixture', 'commit': 'c' * 40, 'worktree_status': 'clean', 'dirty': {}, 'untracked': {}},
        'release_artifact': {'reference': 'example-1.0.tar', 'sha256': SHA['artifact']},
        'lockfile': {'path': 'lock.json', 'sha256': SHA['lock']},
        'equipment': {} if arm == 'baseline' else {'review-skill': {'version': '1', 'sha256': SHA['equip']}},
        'cases': {'review': SHA['case']}, 'rubrics': {'review': SHA['rubric']},
        'prompt_sha256': SHA['prompt'], 'inputs': {'diff': SHA['ref']},
        'model': {'provider': 'none', 'requested': 'none', 'served': 'none', 'settings': {}, 'weights_snapshot': 'none'},
        'harness': {'name': 'synthetic', 'version': '1'}, 'runtime': {'python': '3.12'}, 'toolchain': {'git': '2'},
        'original_artifacts': [],
    }
    for path, value in identity_changes.items():
        node = identity
        *parents, leaf = path.split('__')
        for part in parents:
            node = node[part]
        node[leaf] = value
    return {
        'attempt_id': attempt_id, 'arm': arm, 'record_kind': 'synthetic',
        'adapter': {'kind': 'synthetic-fixture', 'description': 'test fixture'},
        'execution': {'status': 'completed', 'failure_layer': None, 'exit_status': 0},
        'identity': identity, 'unknown': {},
        'measurements': {'output_tokens': {'value': 10, 'unit': 'tokens', 'denominator': 'attempt',
                                           'source': 'fixture', 'kind': 'observed', 'semantics': 'reported output'}},
        'outcome': {'results': {'found-defect': {'result': 'pass', 'method': 'deterministic', 'grader': 'fixture'}}},
        'review': {'independent': 'not-performed', 'reviewer': None, 'method': None},
        'adoption': {'status': 'not-adopted', 'authority': None, 'record': None},
        'receipts': [], 'notes': 'synthetic test attempt',
    }


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / 'store'

    def tearDown(self):
        self.tmp.cleanup()

    def fresh(self, name):
        self.store = Path(self.tmp.name) / name

    def freeze(self, **changes):
        return trials.freeze_plan(self.store, make_plan(**changes))

    def pair(self, base, other, **plan_changes):
        plan_sha = self.freeze(**plan_changes)
        trials.seal_attempt(self.store, plan_sha, base, sealed_at='t')
        trials.seal_attempt(self.store, plan_sha, other, sealed_at='t')
        report = trials.compare(self.store, plan_sha)
        return report['pairs'][0]


class Comparison(StoreCase):
    def test_equipment_only_difference_is_comparable(self):
        pair = self.pair(make_attempt('a1'), make_attempt('b1', 'equipped'))
        self.assertEqual(pair['verdict'], 'comparable')
        fields = [d['field'] for d in pair['declared_treatment_differences']]
        self.assertTrue(fields and all(f.startswith('identity.equipment') for f in fields), fields)
        self.assertEqual(pair['unexpected_control_differences'], [])
        self.assertTrue(pair['outcome_comparable'])

    def test_unchanged_inputs_are_a_comparable_replicate(self):
        pair = self.pair(make_attempt('a1'), make_attempt('a2'))
        self.assertEqual(pair['verdict'], 'replicate')
        self.assertTrue(pair['outcome_comparable'])

    def test_undeclared_changes_are_confounded(self):
        changes = {
            'model': {'model__served': 'other-model'},
            'model settings': {'model__settings': {'effort': 'high'}},
            'runtime': {'runtime': {'python': '3.13'}},
            'artifact': {'release_artifact__sha256': SHA['artifact2']},
            'prompt': {'prompt_sha256': SHA['prompt2']},
            'subject commit': {'subject__commit': 'd' * 40},
        }
        for label, change in changes.items():
            with self.subTest(label):
                self.fresh(label)
                pair = self.pair(make_attempt('a1'), make_attempt('b1', 'equipped', **change))
                self.assertEqual(pair['verdict'], 'confounded')
                self.assertTrue(pair['unexpected_control_differences'])
                self.assertFalse(pair['outcome_comparable'])

    def test_case_or_rubric_change_is_a_plan_deviation(self):
        for field, value in (('cases', {'review': SHA['case2']}), ('rubrics', {'review': SHA['rubric2']})):
            with self.subTest(field):
                self.fresh(field)
                pair = self.pair(make_attempt('a1'), make_attempt('b1', 'equipped', **{field: value}))
                self.assertEqual(pair['verdict'], 'confounded')
                self.assertEqual(pair['plan_deviations'][0]['field'], f'identity.{field}')

    def test_unknown_controls_are_insufficiently_identified_not_clean(self):
        other = make_attempt('b1', 'equipped', lockfile__sha256=None)
        other['unknown'] = {'lockfile.sha256': 'lock not recorded'}
        pair = self.pair(make_attempt('a1'), other)
        self.assertEqual(pair['verdict'], 'insufficiently-identified')
        self.assertEqual(pair['unidentified_controls'][0]['b'], 'lock not recorded')

    def test_heterogeneous_arm_is_not_pooled(self):
        plan_sha = self.freeze()
        trials.seal_attempt(self.store, plan_sha, make_attempt('b1', 'equipped'), sealed_at='t')
        trials.seal_attempt(self.store, plan_sha, make_attempt('b2', 'equipped', model__served='x'), sealed_at='t')
        self.assertIn('not pooled', trials.compare(self.store, plan_sha)['arms']['equipped']['pooling'])

    def test_measurements_stay_unknown_or_incomparable(self):
        base, other = make_attempt('a1'), make_attempt('b1', 'equipped')
        other['measurements']['output_tokens']['unit'] = 'characters'
        base['measurements']['cost_usd'] = {'value': None, 'reason': 'no billing data', 'unit': 'USD',
                                            'source': 'none', 'kind': 'billed'}
        rows = self.pair(base, other)['measurements']
        self.assertTrue(rows['output_tokens']['status'].startswith('incomparable'))
        self.assertTrue(rows['cost_usd']['status'].startswith('unknown'))
        self.assertNotIn('b_minus_a', rows['cost_usd'])

    def test_project_and_consumer_criteria_keep_provenance(self):
        results = self.pair(make_attempt('a1'), make_attempt('b1', 'equipped'))['results']
        self.assertEqual(results['project-claim']['kind'], 'project-supplied')
        self.assertFalse(results['project-claim']['independent_of_subject'])
        self.assertEqual(results['project-claim']['a'], 'unknown')
        self.assertEqual(results['found-defect']['b'], 'pass')


class Sealing(StoreCase):
    def test_failed_and_interrupted_attempts_are_retained(self):
        plan_sha = self.freeze()
        stopped = make_attempt('b1', 'equipped')
        stopped['execution'] = {'status': 'interrupted', 'failure_layer': 'harness', 'exit_status': None}
        stopped['outcome'] = {'results': {}}
        trials.seal_attempt(self.store, plan_sha, make_attempt('a1'), sealed_at='t')
        trials.seal_attempt(self.store, plan_sha, stopped, sealed_at='t')
        report = trials.compare(self.store, plan_sha)
        self.assertEqual(report['attempts']['b1']['execution']['status'], 'interrupted')
        self.assertFalse(report['pairs'][0]['outcome_comparable'])
        self.assertIn('b execution interrupted; outcome not comparable', report['pairs'][0]['flags'])

    def test_budget_stops_further_attempts(self):
        plan_sha = self.freeze(budget={'max_attempts_per_arm': 1, 'stopping_rule': 'one per arm'})
        trials.seal_attempt(self.store, plan_sha, make_attempt('a1'), sealed_at='t')
        with self.assertRaisesRegex(trials.TrialError, 'budget'):
            trials.seal_attempt(self.store, plan_sha, make_attempt('a2'), sealed_at='t')

    def test_same_attempt_id_with_new_content_is_refused(self):
        plan_sha = self.freeze()
        first = trials.seal_attempt(self.store, plan_sha, make_attempt('a1'), sealed_at='t')
        self.assertEqual(trials.seal_attempt(self.store, plan_sha, make_attempt('a1'), sealed_at='t'), first)
        with self.assertRaisesRegex(trials.TrialError, 'already sealed'):
            trials.seal_attempt(self.store, plan_sha, make_attempt('a1', model__served='x'), sealed_at='t')

    def test_tampered_records_are_detected(self):
        plan_sha = self.freeze()
        attempt_sha = trials.seal_attempt(self.store, plan_sha, make_attempt('a1'), sealed_at='t')
        path = self.store / 'attempts' / f'{attempt_sha}.json'
        self.assertEqual(os.stat(path).st_mode & 0o222, 0, 'sealed records are written read-only')
        os.chmod(path, 0o644)
        path.write_text(path.read_text().replace('"completed"', '"failed"'))
        result = trials.verify_store(self.store)
        self.assertFalse(result['verified'])
        self.assertIn('altered', result['errors'][0])
        plan_path = self.store / 'plans' / f'{plan_sha}.json'
        os.chmod(plan_path, 0o644)
        plan_path.write_bytes(plan_path.read_bytes().replace(b'synthetic-plan', b'synthetic-plaN'))
        with self.assertRaisesRegex(trials.TrialError, 'altered'):
            trials.seal_attempt(self.store, plan_sha, make_attempt('a2'), sealed_at='t')

    def test_existing_different_content_is_never_overwritten(self):
        plan_sha = self.freeze()
        path = self.store / 'plans' / f'{plan_sha}.json'
        os.chmod(path, 0o644)
        path.write_text('{}')
        with self.assertRaisesRegex(trials.TrialError, 'refusing to overwrite'):
            self.freeze()

    def test_missing_or_unexplained_metadata_is_refused(self):
        plan_sha = self.freeze()
        cases = {
            'missing field': lambda a: a['identity'].pop('lockfile'),
            'null without reason': lambda a: a['identity']['model'].update(served=None),
            'unknown with value': lambda a: a['unknown'].update({'model.served': 'not known'}),
            'measurement null without reason': lambda a: a['measurements']['output_tokens'].update(value=None),
            'missing exit status': lambda a: a['execution'].pop('exit_status'),
            'completed with failure layer': lambda a: a['execution'].update(failure_layer='model'),
            'adopted without authority': lambda a: a['adoption'].update(status='adopted'),
            'synthetic counted as real': lambda a: a.update(record_kind='real'),
            'undeclared criterion': lambda a: a['outcome']['results'].update(
                {'invented': {'result': 'pass', 'method': 'x', 'grader': 'y'}}),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                attempt = make_attempt('a1')
                mutate(attempt)
                with self.assertRaises(trials.TrialError):
                    trials.seal_attempt(self.store, plan_sha, attempt, sealed_at='t')

    def test_receipts_must_match_their_hashes(self):
        plan_sha = self.freeze()
        root = Path(self.tmp.name) / 'receipts'
        root.mkdir()
        (root / 'response.md').write_text('saved output\n')
        attempt = make_attempt('a1')
        attempt['receipts'] = [{'reference': 'response.md', 'sha256': SHA['ref']}]
        with self.assertRaisesRegex(trials.TrialError, 'does not match'):
            trials.seal_attempt(self.store, plan_sha, attempt, receipt_root=root)


class Plans(unittest.TestCase):
    def test_invalid_plans_are_refused(self):
        cases = {
            'project criterion claims independence': lambda p: p['criteria'][0].update(independent_of_subject=True),
            'criterion carries authority': lambda p: p['criteria'][1].update(authorizes='execution'),
            'retrospective claims pre-freeze': lambda p: p.update(record_kind='historical-retrospective'),
            'real plan frozen afterwards': lambda p: p.update(record_kind='real', frozen_before_execution=False),
            'missing authority field': lambda p: p['authority'].pop('model_execution'),
            'zero budget': lambda p: p['budget'].update(max_attempts_per_arm=0),
            'unknown baseline arm': lambda p: p['treatment'].update(baseline_arm='other'),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                plan = make_plan()
                mutate(plan)
                with self.assertRaises(trials.TrialError):
                    trials.validate_plan(copy.deepcopy(plan))


class Snapshot(unittest.TestCase):
    def test_dirty_and_untracked_content_is_hashed_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = ['git', '-C', tmp, '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                   '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null']
            subprocess.run([*git, 'init', '--quiet', '--template='], check=True)
            (repo / 'src').mkdir()
            (repo / 'src' / 'a.py').write_text('one\n')
            (repo / 'notes.txt').write_text('n\n')
            subprocess.run([*git, 'add', '-A'], check=True)
            subprocess.run([*git, 'commit', '--quiet', '-m', 'base'], check=True)
            clean = trials.snapshot(repo, ['src'])
            self.assertEqual(clean['worktree_status'], 'clean')
            (repo / 'src' / 'a.py').write_text('two\n')
            first = trials.snapshot(repo, ['src'])
            (repo / 'src' / 'a.py').write_text('three\n')
            second = trials.snapshot(repo, ['src'])
            self.assertEqual(first['worktree_status'], 'dirty')
            self.assertEqual(first['commit'], clean['commit'])
            self.assertNotEqual(first['dirty'], second['dirty'])
            self.assertEqual(second['dirty']['src/a.py'], trials.sha256_bytes(b'three\n'))
            (repo / 'src' / 'new.py').write_text('x\n')
            (repo / 'notes.txt').write_text('changed\n')
            third = trials.snapshot(repo, ['src'])
            self.assertEqual(third['untracked'], {'src/new.py': trials.sha256_bytes(b'x\n')})
            self.assertEqual(third['excluded_changes'], 1)
            self.assertNotIn('notes.txt', third['dirty'])


class Equipment(unittest.TestCase):
    EQUIPMENT = {
        'review': {'concern': 'code-review', 'files': {'review.md': SHA['equip']}, 'requires': {}},
        'review-alt': {'concern': 'code-review', 'files': {'alt.md': SHA['equip']}, 'requires': {}},
        'tdd': {'concern': 'test-first-development', 'files': {'tdd.md': SHA['equip']}, 'requires': {}},
        'fetcher': {'concern': 'research', 'files': {'fetch.md': SHA['equip']}, 'requires': {'network': True}},
    }

    def test_task_fit_conflict_authority_and_freshness(self):
        case = {'concerns': ['code-review'], 'constraints': {'network': False}}
        observed = {'review': {'review.md': SHA['equip']}, 'tdd': {'tdd.md': SHA['lock']}}
        report = trials.classify_equipment(case, self.EQUIPMENT, {'network': False}, observed)
        self.assertEqual(report['review']['task_fit'], 'relevant')
        self.assertEqual(report['review']['conflict'], ['same-concern:review-alt'])
        self.assertEqual(report['review']['freshness'], 'current')
        self.assertEqual(report['tdd']['task_fit'], 'irrelevant')
        self.assertEqual(report['tdd']['freshness'], 'stale:tdd.md')
        self.assertEqual(report['fetcher']['conflict'], ['case-constraint:network'])
        self.assertEqual(report['fetcher']['authority'], 'exceeds-plan:network')
        self.assertEqual(report['review-alt']['freshness'], 'unknown')


class Closure(unittest.TestCase):
    def test_evaluator_answers_outside_agent_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, evaluator = Path(tmp, 'agent'), Path(tmp, 'evaluator')
            agent.mkdir()
            evaluator.mkdir()
            (agent / 'task.md').write_text('do the task\n')
            (evaluator / 'answer.json').write_text('{"answer": 42}\n')
            self.assertTrue(trials.check_closure([agent], [evaluator])['closed'])
            (agent / 'copied.json').write_text('{"answer": 42}\n')
            leaked = trials.check_closure([agent], [evaluator])
            self.assertFalse(leaked['closed'])
            self.assertIn('copied.json', leaked['errors'][0])
            self.assertFalse(trials.check_closure([tmp], [evaluator])['closed'])


class Cli(StoreCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = trials.main(list(argv))
        return code, out.getvalue()

    def test_freeze_seal_verify_compare(self):
        plan_path, attempt_path = Path(self.tmp.name, 'plan.json'), Path(self.tmp.name, 'attempt.json')
        plan_path.write_text(json.dumps(make_plan()))
        attempt_path.write_text(json.dumps(make_attempt('a1')))
        code, out = self.run_cli('freeze-plan', '--store', str(self.store), '--plan', str(plan_path))
        self.assertEqual(code, 0)
        plan_sha = json.loads(out)['plan_sha256']
        code, _ = self.run_cli('seal', '--store', str(self.store), '--plan-sha256', plan_sha, '--attempt', str(attempt_path))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.run_cli('verify', '--store', str(self.store))[1])['counts'],
                         {'plans': 1, 'attempts': 1})
        code, md = self.run_cli('compare', '--store', str(self.store), '--plan-sha256', plan_sha, '--format', 'markdown')
        self.assertEqual(code, 0)
        self.assertIn('| a1 | baseline | synthetic | synthetic-fixture | completed |', md)


if __name__ == '__main__':
    unittest.main()
