import json
import tempfile
import unittest
from pathlib import Path

from harness import authoring, paired_comparison, trials
from test_trials import make_attempt

ROOT = Path(__file__).resolve().parents[1]
PLAN_FILE = ROOT / 'examples/sb06-trial-plan.fixture.json'
CASE_FILE = ROOT / 'examples/sb06-authorized-boundary-cases.fixture.json'
RUBRIC_FILE = ROOT / 'examples/sb06-independent-boundary-rubric.fixture.json'


def _result(value, method, grader='synthetic-fixture'):
    return {'result': value, 'method': method, 'grader': grader}


class AuthorizedBoundaryWorkflow(unittest.TestCase):
    def test_freeze_seal_preserve_cold_pair_and_keep_review_unperformed(self):
        case = json.loads(CASE_FILE.read_bytes())
        rubric = json.loads(RUBRIC_FILE.read_bytes())
        self.assertEqual(case['record_kind'], 'synthetic')
        self.assertEqual(case['operations_exercised'], [])
        self.assertFalse(case['network'])
        self.assertTrue(all(row['expected_observation'] in {'denied', 'unknown', 'failed'} for row in case['cases']))
        self.assertTrue(rubric['evaluator_only'])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, workbench, receipts = root / 'store', root / 'workbench', root / 'receipts'
            receipts.mkdir()
            plan, origin = authoring.start_from_file(PLAN_FILE)
            made = authoring.create(workbench, 'sb06', plan, origin, note='synthetic-only boundary workflow')
            preview = authoring.preview(workbench, 'sb06', made['revision_sha256'])
            self.assertEqual(preview['status'], 'ready', preview['errors'])
            self.assertFalse(preview['grants']['execution'])
            self.assertFalse(preview['grants']['adoption'])
            handoff = authoring.freeze(workbench, 'sb06', made['revision_sha256'],
                                       preview['preview_sha256'], store,
                                       frozen_at='2026-11-02T00:00:01+00:00')
            plan_sha = handoff['plan_sha256']
            frozen = trials.load_record(store, 'plans', plan_sha)
            self.assertEqual(frozen['record_kind'], 'synthetic')
            self.assertEqual(frozen['authority']['model_execution'], 'not-authorized')
            self.assertTrue(all(value is False for value in frozen['authority']['permissions'].values()))
            self.assertEqual(frozen['cases']['review']['sha256'], trials.sha256_file(CASE_FILE))
            self.assertEqual(frozen['rubrics']['review']['sha256'], trials.sha256_file(RUBRIC_FILE))

            def draft(attempt_id, arm, status, failure_layer, exit_status, outcomes, receipt=None):
                record = make_attempt(attempt_id, arm)
                record['identity']['cases'] = {'review': frozen['cases']['review']['sha256']}
                record['identity']['rubrics'] = {'review': frozen['rubrics']['review']['sha256']}
                record['execution'] = {'status': status, 'failure_layer': failure_layer, 'exit_status': exit_status}
                record['outcome'] = {'results': outcomes}
                record['review'] = {'independent': 'not-performed', 'reviewer': None, 'method': None}
                record['measurements'] = {'output_tokens': {
                    'value': None, 'reason': 'synthetic boundary fixture records no token measurement',
                    'unit': 'tokens', 'denominator': 'attempt', 'source': 'none',
                    'kind': 'observed', 'semantics': 'unknown',
                }}
                record['notes'] = 'Synthetic fixture record only; no target, endpoint, provider, or private data was used.'
                if receipt:
                    path = receipts / f'{attempt_id}.json'
                    payload = {'format': 'synthetic-boundary-receipt-1', 'attempt_id': attempt_id, **receipt}
                    path.write_bytes(trials.canonical(payload))
                    record['receipts'] = [{'reference': path.name, 'sha256': trials.sha256_file(path)}]
                return record

            unknowns = {criterion['id']: _result('unknown', 'no settled fixture observation')
                        for criterion in frozen['criteria']}
            denial_results = dict(unknowns)
            denial_results['mock-denial'] = _result('pass', 'exact synthetic denial fixture receipt')
            sealed = {}
            for record in (
                draft('sb06-baseline-denial', 'baseline', 'completed', None, 0, denial_results,
                      {'observation': 'denied', 'synthetic': True}),
                draft('sb06-candidate-denial', 'equipped', 'completed', None, 0, denial_results,
                      {'observation': 'denied', 'synthetic': True}),
                draft('sb06-blocked-no-authorization', 'equipped', 'blocked', 'unknown', None, unknowns),
                draft('sb06-failed-fixture-runner', 'equipped', 'failed', 'tool', 1, unknowns,
                      {'observation': 'runner-failed', 'synthetic': True}),
                draft('sb06-interrupted-runner', 'equipped', 'interrupted', 'infrastructure', None, unknowns,
                      {'observation': 'runner-interrupted', 'synthetic': True}),
            ):
                sealed[record['attempt_id']] = trials.seal_attempt(
                    store, plan_sha, record, sealed_at='2026-11-02T00:00:02+00:00', receipt_root=receipts)

            self.assertTrue(trials.verify_store(store)['verified'])
            comparison = trials.compare(store, plan_sha)
            for attempt_id, status in (
                ('sb06-blocked-no-authorization', 'blocked'),
                ('sb06-failed-fixture-runner', 'failed'),
                ('sb06-interrupted-runner', 'interrupted'),
            ):
                row = trials.load_record(store, 'attempts', sealed[attempt_id])
                self.assertEqual(row['execution']['status'], status)
                self.assertTrue(all(r['result'] == 'unknown' for r in row['outcome']['results'].values()))
                self.assertEqual(comparison['attempts'][attempt_id]['execution']['status'], status)
                self.assertEqual(row['review']['independent'], 'not-performed')
            self.assertEqual(comparison['attempts']['sb06-candidate-denial']['execution']['status'], 'completed')
            candidate_denial = trials.load_record(store, 'attempts', sealed['sb06-candidate-denial'])
            self.assertEqual(candidate_denial['outcome']['results']['mock-denial']['result'], 'pass')

            review = {
                'format': paired_comparison.REVIEW_FORMAT,
                'author': 'synthetic fixture; no human reviewer participated',
                'pair_selection_rationale': 'Select only the two exact synthetic completed-denial attempts.',
                'scope_review': 'Fixture-only; no real target result or permission was evaluated.',
                'uncertainty_review': 'Independent human review was not performed; all reviewer fields remain not-performed.',
                'resource_metrics': [],
                'decision_owner_questions': ['No adoption decision is supplied by this fixture.'],
            }
            packet = paired_comparison.create_packet(store, plan_sha, [{
                'pair_id': 'sb06-synthetic-denial',
                'baseline_attempt_id': 'sb06-baseline-denial',
                'candidate_attempt_id': 'sb06-candidate-denial',
            }], review)
            cold = paired_comparison.validate_packet(json.loads(trials.canonical(packet)))
            self.assertEqual(cold['adoption_handoff']['adoption_decision'], 'not-recorded-by-Sandbox')
            self.assertEqual(cold['adoption_handoff']['authority'], 'no-authority-granted')
            self.assertEqual(cold['review']['author'], review['author'])
            unpaired = {row['attempt_id']: row for row in cold['unpaired_attempts']}
            self.assertEqual({key: unpaired[key]['execution_status'] for key in unpaired}, {
                'sb06-blocked-no-authorization': 'blocked',
                'sb06-failed-fixture-runner': 'failed',
                'sb06-interrupted-runner': 'interrupted',
            })
            self.assertEqual(trials.load_record(store, 'attempts', sealed['sb06-interrupted-runner'])['execution']['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
