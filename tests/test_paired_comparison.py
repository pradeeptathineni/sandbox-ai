import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness import experiment_manifests, paired_comparison, trials
from test_trials import make_attempt, make_plan


class PairedComparisonWorkflow(unittest.TestCase):
    def test_explicit_pairs_qualified_resources_and_cold_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / 'store'
            plan_sha = trials.freeze_plan(store, make_plan())
            manifest_input = {
                'title': 'Synthetic paired comparison',
                'decision': {'question': 'Does equipment change the review?',
                             'owner': 'example owner', 'consumer': 'example'},
                'systems': [
                    {'system_id': 'baseline-system', 'plan_sha256': plan_sha,
                     'arm': 'baseline', 'role': 'baseline'},
                    {'system_id': 'candidate-system', 'plan_sha256': plan_sha,
                     'arm': 'equipped', 'role': 'candidate'},
                ],
            }
            manifest_sha = experiment_manifests.freeze_manifest(
                store, '77777777-7777-4777-8777-777777777777', manifest_input,
                frozen_at='2026-11-02T00:00:00+00:00')
            records = [make_attempt('b1', 'baseline'), make_attempt('c1', 'equipped'),
                       make_attempt('b2', 'baseline'), make_attempt('c2', 'equipped')]
            records[1]['measurements'] = copy.deepcopy(records[0]['measurements'])
            records[1]['measurements']['output_tokens']['value'] = 12
            records[3]['identity']['harness']['version'] = 'different fixture harness'
            for record in records:
                record['measurements']['window_cost'] = {
                    'value': None, 'unit': 'USD', 'denominator': 'billing window',
                    'source': 'none', 'semantics': 'window cost', 'kind': 'billed',
                    'reason': 'not recorded',
                }
                trials.seal_attempt(store, plan_sha, record, sealed_at='t')
            review = {
                'format': paired_comparison.REVIEW_FORMAT, 'author': 'Synthetic reviewer',
                'pair_selection_rationale': 'Pair by the same fixture and task labels.',
                'scope_review': 'Synthetic fixture only.',
                'uncertainty_review': 'A confounded pair and unknown counters remain visible.',
                'resource_metrics': [
                    {'name': 'output_tokens', 'scope': 'per-run', 'window_reference': None,
                     'rationale': 'Per-attempt counter.'},
                    {'name': 'window_cost', 'scope': 'accounting-window',
                     'window_reference': 'synthetic-window-1', 'rationale': 'Window value only.'},
                    {'name': 'absent', 'scope': 'unknown', 'window_reference': None,
                     'rationale': 'Not established.'},
                    {'name': 'missing-per-run', 'scope': 'per-run', 'window_reference': None,
                     'rationale': 'Not recorded by these attempts.'},
                ],
                'decision_owner_questions': ['Are two synthetic pairs enough for further review?'],
            }
            selection = [
                {'pair_id': 'pair-1', 'baseline_attempt_id': 'b1', 'candidate_attempt_id': 'c1'},
                {'pair_id': 'pair-2', 'baseline_attempt_id': 'b2', 'candidate_attempt_id': 'c2'},
            ]
            selection_path = Path(tmp) / 'selection.json'
            review_path = Path(tmp) / 'review.json'
            packet_path = Path(tmp) / 'paired.json'
            selection_path.write_text(json.dumps(selection), encoding='utf-8')
            review_path.write_text(json.dumps(review), encoding='utf-8')
            created = subprocess.run([
                sys.executable, '-m', 'harness.paired_comparison', 'create',
                '--store', str(store), '--plan-sha256', plan_sha,
                '--selection', str(selection_path), '--review', str(review_path),
                '--output', str(packet_path),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(created.returncode, 0, created.stderr)
            packet = json.loads(packet_path.read_bytes())
            manifest = experiment_manifests.show_manifest(store, manifest_sha)
            self.assertEqual(packet['format'], paired_comparison.FORMAT)
            self.assertEqual(manifest['systems'][0]['plan_sha256'], packet['plan']['sha256'])
            self.assertEqual(manifest['systems'][1]['plan_sha256'], packet['plan']['sha256'])
            self.assertEqual(len(packet['selected_pairs']), 2)
            self.assertTrue(any(row['structural_comparison']['verdict'] == 'confounded'
                                for row in packet['selected_pairs']))
            self.assertTrue(any(row['complete_total'] == 2 for row in packet['resource_accounting']['groups']))
            states = {(row['name'], row['scope'], row['state']) for row in packet['resource_accounting']['pairs']}
            self.assertIn(('window_cost', 'accounting-window', 'reported-window-values-only'), states)
            self.assertIn(('absent', 'unknown', 'unknown-scope'), states)
            self.assertIn(('output_tokens', 'per-run', 'incomparable-pair'), states)
            self.assertIn(('missing-per-run', 'per-run', 'unavailable'), states)
            self.assertEqual(packet['adoption_handoff']['authority'], 'no-authority-granted')
            self.assertEqual(paired_comparison.validate_packet(packet), packet)
            saved_packet = Path(tmp) / 'paired.json'
            saved_packet.write_bytes(trials.canonical(packet))
            cold_packet = json.loads(saved_packet.read_bytes())
            self.assertEqual(paired_comparison.validate_packet(cold_packet), packet)
            self.assertEqual(paired_comparison.render_packet(cold_packet), trials.canonical(packet).decode())
            rendered = subprocess.run([
                sys.executable, '-m', 'harness.paired_comparison', 'render',
                '--input', str(saved_packet), '--format', 'markdown',
            ], capture_output=True, text=True, check=False)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertIn('Resource accounting', rendered.stdout)
            changed = copy.deepcopy(packet)
            changed['resource_accounting']['pairs'][0]['reported_delta'] = 0
            changed['packet_sha256'] = paired_comparison._digest(
                {key: value for key, value in changed.items() if key != 'packet_sha256'})
            with self.assertRaisesRegex(trials.TrialError, 'derived evidence'):
                paired_comparison.validate_packet(changed)


if __name__ == '__main__':
    unittest.main()
