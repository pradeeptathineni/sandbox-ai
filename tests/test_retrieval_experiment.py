import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness import retrieval, retrieval_experiment, trials


FIXTURE = Path(__file__).parents[1] / 'examples' / 'retrieval-comparison.fixture.json'


def build_plan():
    case_ids = ['q:policy', 'q:retention', 'q:exception']
    digest = lambda value: trials.sha256_bytes(value.encode())
    cases = {case_id: {'version': '1', 'sha256': digest(f'case:{case_id}'),
                       'provenance': {'owner': 'sandbox-test'}} for case_id in case_ids}
    rubric = {'version': '1', 'sha256': digest('retrieval-rubric'),
              'provenance': {'owner': 'sandbox-test'}}
    return {
        'format': trials.PLAN_FORMAT, 'plan_id': 'retrieval-pair-smoke', 'record_kind': 'synthetic',
        'frozen_at_utc': '2026-10-27T00:00:00+00:00', 'frozen_before_execution': True,
        'decision': {'question': 'Which frozen retrieval arm warrants further human review?',
                     'owner': 'synthetic owner', 'consumer': 'synthetic smoke'},
        'cases': cases, 'rubrics': {'retrieval': rubric},
        'criteria': [{'id': 'known-evidence', 'case': 'q:policy', 'kind': 'consumer',
                      'statement': 'Expose source identity and judgment completeness.',
                      'provenance': {'owner': 'sandbox-test', 'version': '1', 'reference': 'test fixture',
                                     'sha256': rubric['sha256']}, 'independent_of_subject': True}],
        'treatment': {'baseline_arm': 'baseline',
                      'arms': {'baseline': 'frozen baseline', 'candidate': 'frozen candidate'},
                      'variables': [{'path': 'identity.equipment', 'reason': 'declared candidate arm'}]},
        'authority': {'authorized_by': 'no execution authorized', 'operations': ['offline comparison'],
                      'model_execution': 'not-authorized', 'isolation_owner': 'not-applicable', 'permissions': {}},
        'budget': {'max_attempts_per_arm': 2, 'stopping_rule': 'one frozen synthetic pair only'},
        'evaluator_scope': {'agent_readable_roots': ['synthetic'], 'evaluator_only_roots': ['judgments'],
                            'grader': 'existing retrieval evaluator'},
        'limitations': ['Synthetic fixture only.'],
    }


def build_attempt(plan, attempt_id, arm, ranking, status='completed'):
    case_ids = list(plan['cases'])
    baseline = arm == 'baseline'
    receipt_hash = retrieval.digest(ranking)
    identity = {
        'milestone_label': 'synthetic retrieval pair 1',
        'subject': {'repository': 'synthetic', 'commit': 'a' * 40, 'worktree_status': 'clean',
                    'dirty': {}, 'untracked': {}},
        'release_artifact': {'reference': 'synthetic-run', 'sha256': 'b' * 64},
        'lockfile': {'path': 'synthetic-lock', 'sha256': 'c' * 64},
        'equipment': {} if baseline else {'candidate': {'version': 'synthetic', 'sha256': 'd' * 64}},
        'cases': {qid: plan['cases'][qid]['sha256'] for qid in case_ids},
        'rubrics': {'retrieval': plan['rubrics']['retrieval']['sha256']},
        'prompt_sha256': 'e' * 64, 'inputs': {'corpus': 'f' * 64},
        'model': {'provider': 'synthetic', 'requested': 'none', 'served': 'none', 'settings': {},
                  'weights_snapshot': 'not-applicable'},
        'harness': {'name': 'sandbox-retrieval-fixture', 'version': '1'},
        'runtime': {'python': '3.12'}, 'toolchain': {'git': 'synthetic'}, 'original_artifacts': [],
    }
    return {
        'attempt_id': attempt_id, 'arm': arm, 'record_kind': 'synthetic',
        'adapter': {'kind': 'synthetic-fixture', 'description': 'authored rankings; no retriever run'},
        'execution': {'status': status, 'failure_layer': None if status == 'completed' else 'unknown',
                      'exit_status': 0 if status == 'completed' else None},
        'identity': identity, 'unknown': {}, 'measurements': {}, 'outcome': {'results': {}},
        'review': {'independent': 'not-performed', 'reviewer': None, 'method': None},
        'adoption': {'status': 'not-adopted', 'authority': None, 'record': None},
        'receipts': [{'reference': f'{attempt_id}-ranking-map.json', 'sha256': receipt_hash}],
        'notes': 'Synthetic sealed attempt record for offline workflow qualification.',
    }


def build_grounding_judgments(comparison_input):
    observations = []
    for arm in comparison_input['arms']:
        strategy = arm['strategy']
        for question in comparison_input['questions']:
            qid = question['id']
            if qid == 'q:policy':
                source_id = 'doc:beta' if strategy == 'baseline' else 'doc:outside-corpus'
                observation = {
                    'answer_id': f'answer:{strategy}:{qid}',
                    'answer_sha256': (strategy[0] * 64),
                    'answer_status': 'answered',
                    'evidence_expectation': 'positive',
                    'citation_checks': [{
                        'citation_id': f'citation:{strategy}:{qid}', 'source_id': source_id,
                        'occurrence': 'present',
                        'support': 'supported' if strategy == 'baseline' else 'contradicted',
                        'support_evidence_source_ids': ['doc:beta'] if strategy == 'baseline' else ['doc:gamma'],
                        'uncertainty': 'Synthetic caller judgment; source truth not verified.',
                    }],
                }
            elif qid == 'q:retention' and strategy == 'baseline':
                observation = {
                    'answer_id': None, 'answer_sha256': None, 'answer_status': 'missing',
                    'evidence_expectation': 'unavailable',
                    'citation_checks': [{
                        'citation_id': f'expected-citation:{strategy}:{qid}', 'source_id': 'doc:delta',
                        'occurrence': 'missing', 'support': 'not-assessed',
                        'support_evidence_source_ids': [], 'uncertainty': 'No captured answer; support not assessed.',
                    }],
                }
            elif qid == 'q:retention':
                observation = {
                    'answer_id': f'answer:{strategy}:{qid}', 'answer_sha256': '2' * 64,
                    'answer_status': 'answered', 'evidence_expectation': 'unknown',
                    'citation_checks': [{
                        'citation_id': f'citation:{strategy}:{qid}', 'source_id': 'doc:delta',
                        'occurrence': 'present', 'support': 'unknown',
                        'support_evidence_source_ids': ['doc:delta'], 'uncertainty': 'Judgment incomplete.',
                    }],
                }
            elif strategy == 'baseline':
                observation = {
                    'answer_id': None, 'answer_sha256': None, 'answer_status': 'abstained',
                    'evidence_expectation': 'negative', 'citation_checks': [],
                }
            else:
                observation = {
                    'answer_id': None, 'answer_sha256': None, 'answer_status': 'unknown',
                    'evidence_expectation': 'unknown',
                    'citation_checks': [{
                        'citation_id': f'unknown-citation:{strategy}:{qid}', 'source_id': None,
                        'occurrence': 'unknown', 'support': 'unknown',
                        'support_evidence_source_ids': [], 'uncertainty': 'Citation and support not verified.',
                    }],
                }
            observations.append({'strategy': strategy, 'question_id': qid, **observation})
    return {
        'format': 'sandbox-retrieval-grounding-judgments-1',
        'scope': {'rubric_id': 'synthetic-grounding', 'rubric_revision': '1', 'rubric_sha256': 'a' * 64},
        'evaluator': {'id': 'fixture-reviewer', 'method': 'caller-authored-source-mapping',
                      'provenance': 'synthetic test fixture', 'uncertainty': 'No source truth was independently verified.'},
        'observations': observations,
    }
class RetrievalExperimentPacket(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / 'store'
        self.plan = build_plan()
        self.plan_sha = trials.freeze_plan(self.store, self.plan)
        self.input = json.loads(FIXTURE.read_text())
        self.input['arms'][0]['strategy'] = 'baseline'
        self.input['arms'][1]['strategy'] = 'candidate'
        self.input['arms'][0]['provenance']['producer'] = 'synthetic baseline attempt'
        self.input['arms'][1]['provenance']['producer'] = 'synthetic candidate attempt'

    def tearDown(self):
        self.tmp.cleanup()

    def seal_pair(self, candidate_status='completed', omit_candidate_question=False):
        base_rankings = copy.deepcopy(self.input['arms'][0]['rankings'])
        candidate_rankings = copy.deepcopy(self.input['arms'][1]['rankings'])
        if omit_candidate_question:
            candidate_rankings.pop('q:policy')
            self.input['arms'][1]['rankings'].pop('q:policy')
        trials.seal_attempt(self.store, self.plan_sha,
                            build_attempt(self.plan, 'baseline-1', 'baseline', base_rankings), sealed_at='t')
        trials.seal_attempt(self.store, self.plan_sha,
                            build_attempt(self.plan, 'candidate-1', 'candidate', candidate_rankings,
                                          status=candidate_status), sealed_at='t')

    def test_create_cold_reopen_and_export_retains_case_judgments_and_source_ids(self):
        self.seal_pair()
        packet = retrieval_experiment.create_packet(self.store, self.plan_sha, 'baseline-1', 'candidate-1', self.input)
        self.assertEqual(packet['format'], 'sandbox-retrieval-experiment-packet-1')
        self.assertEqual(packet['paired_outcome']['status'], 'comparable')
        self.assertEqual(packet['paired_outcome']['missing_rankings_by_arm'], {'baseline': [], 'candidate': []})
        self.assertEqual(packet['retrieval_report']['corpus']['id'], self.input['corpus']['id'])
        rows = packet['retrieval_report']['arms'][0]['per_question']
        self.assertEqual(rows['q:retention']['ground_truth_status'], 'partial')
        self.assertEqual(rows['q:retention']['unjudged_candidate_ids'], ['doc:alpha'])
        self.assertEqual(rows['q:exception']['ground_truth_status'], 'unknown')
        self.assertIsNone(rows['q:exception']['metrics']['1']['recall'])
        self.assertEqual(packet['adoption_evidence']['status'], 'evidence-only')
        self.assertEqual(packet['adoption_evidence']['adoption'], 'not-recorded-by-this-packet')
        self.assertNotIn('grounding_evaluation', packet)
        self.assertEqual(retrieval_experiment.grounding_assessment(packet)['status'], 'not_assessed')

        encoded = retrieval_experiment.render_packet(packet, 'json')
        reopened = retrieval_experiment.validate_packet(json.loads(encoded))
        self.assertEqual(reopened, packet)
        markdown = retrieval_experiment.render_packet(reopened, 'markdown')
        self.assertIn('Ground truth | Baseline ranked IDs | Candidate ranked IDs', markdown)
        self.assertIn('q:exception | unknown', markdown)
        self.assertIn('advisory evidence only', markdown)
        self.assertIn('citation occurrence and support judgments were not supplied', markdown)

        input_path = Path(self.tmp.name) / 'retrieval-input.json'
        packet_path = Path(self.tmp.name) / 'packet.json'
        input_path.write_text(json.dumps(self.input), encoding='utf-8')
        command = [sys.executable, '-m', 'harness.retrieval_experiment', 'compare', '--store', str(self.store),
                   '--plan-sha256', self.plan_sha, '--baseline-attempt', 'baseline-1',
                   '--candidate-attempt', 'candidate-1', '--input', str(input_path), '--output', str(packet_path)]
        run = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(packet_path.read_text()), packet)
        exported = subprocess.run([sys.executable, '-m', 'harness.retrieval_experiment', 'render',
                                   '--input', str(packet_path), '--format', 'markdown'],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertIn('q:retention | partial', exported.stdout)

        tampered = copy.deepcopy(packet)
        tampered['retrieval_report']['arms'][0]['per_question']['q:policy']['relevant_ranked_ids'] = []
        tampered['packet_sha256'] = retrieval_experiment._canonical_hash(
            {key: value for key, value in tampered.items() if key != 'packet_sha256'})
        with self.assertRaisesRegex(retrieval.RetrievalError, 'does not match its retained inputs'):
            retrieval_experiment.validate_packet(tampered)

    def test_missing_rankings_and_partial_execution_remain_uncomparable_and_visible(self):
        self.seal_pair(candidate_status='partial', omit_candidate_question=True)
        packet = retrieval_experiment.create_packet(self.store, self.plan_sha, 'baseline-1', 'candidate-1', self.input)
        self.assertEqual(packet['paired_outcome']['status'], 'not-comparable')
        self.assertFalse(packet['paired_outcome']['trial_outcome_comparable'])
        self.assertEqual(packet['paired_outcome']['missing_rankings_by_arm']['candidate'], ['q:policy'])
        candidate = packet['retrieval_report']['arms'][1]
        self.assertEqual(candidate['missing_question_ids'], ['q:policy'])
        self.assertIn({'question_id': 'q:policy', 'type': 'missing_question_ranking'}, candidate['failure_taxonomy'])
        self.assertEqual(candidate['per_question']['q:exception']['ground_truth_status'], 'unknown')
        self.assertIsNone(candidate['per_question']['q:exception']['metrics']['1']['hit_rate'])

    def test_selected_attempt_and_input_must_match_frozen_pair(self):
        self.seal_pair()
        with self.assertRaisesRegex(retrieval.RetrievalError, 'strategy names must exactly match'):
            altered = copy.deepcopy(self.input)
            altered['arms'][1]['strategy'] = 'unselected-arm'
            retrieval_experiment.create_packet(self.store, self.plan_sha, 'baseline-1', 'candidate-1', altered)

    def test_grounding_observations_keep_occurrence_support_and_missing_states_separate(self):
        self.seal_pair()
        judgments = build_grounding_judgments(self.input)
        packet = retrieval_experiment.create_packet(
            self.store, self.plan_sha, 'baseline-1', 'candidate-1', self.input, judgments)
        report = packet['grounding_evaluation']['report']
        self.assertEqual(report['comparison_scope']['status'], 'descriptively-comparable')
        self.assertEqual(report['evaluator']['identity_status'], 'caller-asserted-unverified')
        baseline = next(row for row in report['per_arm'] if row['strategy'] == 'baseline')
        self.assertEqual(baseline['answer_status_counts']['missing'], 1)
        self.assertEqual(baseline['answer_status_counts']['abstained'], 1)
        self.assertEqual(baseline['citation_occurrence_counts']['missing'], 1)
        self.assertEqual(baseline['citation_support_counts']['not-assessed'], 1)
        candidate = next(row for row in report['per_arm'] if row['strategy'] == 'candidate')
        self.assertEqual(candidate['citation_support_counts']['contradicted'], 1)
        self.assertEqual(candidate['citation_support_counts']['unknown'], 2)
        self.assertEqual(candidate['citation_occurrence_counts']['unknown'], 1)
        self.assertEqual(candidate['foreign_source_ids'], ['doc:outside-corpus'])
        self.assertEqual(retrieval_experiment.grounding_assessment(packet)['status'], 'assessed')

        cold = json.loads(retrieval_experiment.render_packet(packet, 'json'))
        self.assertEqual(retrieval_experiment.validate_packet(cold), packet)
        markdown = retrieval_experiment.render_packet(packet, 'markdown')
        self.assertIn('Occurrence and support are separate caller judgments', markdown)
        self.assertIn('caller asserted, unverified', markdown)

        input_path = Path(self.tmp.name) / 'grounding-retrieval-input.json'
        judgments_path = Path(self.tmp.name) / 'grounding-judgments.json'
        packet_path = Path(self.tmp.name) / 'grounding-packet.json'
        input_path.write_text(json.dumps(self.input), encoding='utf-8')
        judgments_path.write_text(json.dumps(judgments), encoding='utf-8')
        command = [sys.executable, '-m', 'harness.retrieval_experiment', 'compare', '--store', str(self.store),
                   '--plan-sha256', self.plan_sha, '--baseline-attempt', 'baseline-1',
                   '--candidate-attempt', 'candidate-1', '--input', str(input_path),
                   '--grounding-judgments', str(judgments_path), '--output', str(packet_path)]
        run = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        cli_packet = json.loads(packet_path.read_text())
        self.assertEqual(retrieval_experiment.validate_packet(cli_packet), packet)
        exported = subprocess.run([sys.executable, '-m', 'harness.retrieval_experiment', 'render',
                                   '--input', str(packet_path), '--format', 'markdown'],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertIn('Occurrence and support are separate caller judgments', exported.stdout)

        tampered = copy.deepcopy(packet)
        tampered['grounding_evaluation']['judgments']['observations'][0]['evidence_expectation'] = 'unknown'
        tampered['packet_sha256'] = retrieval_experiment._canonical_hash(
            {key: value for key, value in tampered.items() if key != 'packet_sha256'})
        with self.assertRaisesRegex(retrieval.RetrievalError, 'does not match its retained inputs'):
            retrieval_experiment.validate_packet(tampered)

    def test_grounding_judgments_require_full_frozen_question_and_arm_scope(self):
        self.seal_pair()
        judgments = build_grounding_judgments(self.input)
        judgments['observations'].pop()
        with self.assertRaisesRegex(retrieval.RetrievalError, 'cover every frozen strategy/question pair'):
            retrieval_experiment.create_packet(
                self.store, self.plan_sha, 'baseline-1', 'candidate-1', self.input, judgments)


if __name__ == '__main__':
    unittest.main()
