import copy
import json
import unittest
from pathlib import Path

from harness import retrieval


FIXTURE = Path(__file__).parents[1] / 'examples' / 'retrieval-comparison.fixture.json'


class RetrievalComparison(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text())

    def test_metrics_unknowns_and_pairwise_are_explicit(self):
        report = retrieval.evaluate(self.data)
        base = report['arms'][0]
        self.assertEqual(base['per_question']['q:policy']['metrics']['1']['recall'], 0)
        self.assertEqual(base['per_question']['q:policy']['metrics']['3']['recall'], 1)
        self.assertEqual(base['per_question']['q:retention']['metrics']['3']['recall'], None)
        self.assertIsNone(base['per_question']['q:retention']['metrics']['3']['precision'])
        self.assertEqual(base['per_question']['q:retention']['metrics']['3']['known_positive_fraction_lower_bound'], 0.5)
        self.assertEqual(base['per_question']['q:exception']['metrics']['1']['hit_rate'], None)
        self.assertEqual(base['aggregate_metrics']['3']['complete_ground_truth_recall_denominator'], 1)
        pair = report['pairwise'][0]
        self.assertEqual(pair['complete_ground_truth_comparison']['3']['denominator'], 1)
        self.assertEqual(pair['complete_ground_truth_comparison']['3']['per_question'][0]['b_minus_a'], 0)
        self.assertEqual(report['input_sha256'], retrieval.digest(self.data))
        report_body = dict(report)
        report_sha = report_body.pop('report_sha256')
        self.assertEqual(report_sha, retrieval.digest(report_body))
        self.assertIn('question_sha256', base['per_question']['q:policy'])
        self.assertEqual(base['per_question']['q:policy']['relevant_ranked_ids'], ['doc:beta', 'doc:gamma'])

    def test_missing_ranking_is_retained_and_reduces_pair_denominator(self):
        self.data['arms'][1]['rankings'].pop('q:policy')
        report = retrieval.evaluate(self.data)
        other = report['arms'][1]
        self.assertEqual(other['missing_question_ids'], ['q:policy'])
        self.assertIn({'question_id': 'q:policy', 'type': 'missing_question_ranking'}, other['failure_taxonomy'])
        pair = report['pairwise'][0]
        self.assertFalse(pair['all_questions_present_in_both'])
        self.assertEqual(pair['comparable_question_count'], 2)
        self.assertEqual(pair['complete_ground_truth_comparison']['3']['denominator'], 0)

    def test_out_of_corpus_source_is_reported_as_citation_identity_failure(self):
        self.data['arms'][0]['rankings']['q:policy'][0] = 'foreign:source'
        report = retrieval.evaluate(self.data)
        self.assertIn({'question_id': 'q:policy', 'type': 'source_id_outside_corpus',
                       'source_ids': ['foreign:source']}, report['arms'][0]['failure_taxonomy'])
        pair = report['pairwise'][0]
        self.assertFalse(pair['identity_comparable'])
        self.assertEqual(pair['comparable_question_count'], 0)

    def test_duplicate_candidates_rejected_but_arm_identity_mismatch_is_reported(self):
        bad = copy.deepcopy(self.data)
        bad['arms'][0]['rankings']['q:policy'].append('doc:beta')
        with self.assertRaisesRegex(retrieval.RetrievalError, 'duplicate candidates'):
            retrieval.evaluate(bad)
        bad = copy.deepcopy(self.data)
        bad['arms'][1]['question_ids'] = ['q:policy']
        bad['arms'][1]['rankings'] = {'q:policy': ['doc:alpha']}
        report = retrieval.evaluate(bad)
        self.assertEqual(report['arms'][1]['identity_mismatches'][0]['type'], 'question_set_mismatch')
        self.assertFalse(report['pairwise'][0]['identity_comparable'])
        self.assertEqual(report['pairwise'][0]['comparable_question_count'], 0)

    def test_recall_requires_complete_nonempty_ground_truth(self):
        bad = copy.deepcopy(self.data)
        bad['questions'][0]['ground_truth']['status'] = 'complete'
        bad['questions'][0]['ground_truth']['relevant_source_ids'] = []
        report = retrieval.evaluate(bad)
        metric = report['arms'][0]['per_question']['q:policy']['metrics']['3']
        self.assertIsNone(metric['recall'])
        self.assertEqual(metric['recall_unknown_reason'], 'no known relevant sources')


if __name__ == '__main__':
    unittest.main()
