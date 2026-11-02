import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness import lifecycle_recovery, trials
from test_trials import make_attempt, make_plan

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'examples/sb07-fault-fixtures.fixture.json'


class SB07RecoveryMatrix(unittest.TestCase):
    def test_interruption_unknown_recovery_decision_and_cold_read(self):
        result = subprocess.run(
            [sys.executable, '-B', '-m', 'harness.lifecycle_recovery_cli',
             '--input', str(FIXTURE)],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        report = json.loads(result.stdout)
        rows = {row['observation_id']: row for row in report['observations']}
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows['sb07-process-crash-after-dispatch-synthetic']['status'], 'unknown-preserved')
        self.assertEqual(rows['sb07-timeout-before-readback-synthetic']['status'], 'unknown-preserved')
        # A later caller-supplied completed read leaves the earlier timeout outcome unknown.
        self.assertEqual(rows['sb07-timeout-readback-completes-synthetic']['status'], 'unknown-preserved')
        self.assertEqual(rows['sb07-race-duplicate-submit-synthetic']['status'], 'invariant-violation-duplicate-effect')
        self.assertEqual(rows['sb07-late-write-diverged-synthetic']['status'], 'recovery-state-diverged')
        self.assertEqual(rows['sb07-lost-reply-once-synthetic']['status'], 'reopened-consistently')
        self.assertEqual(report['summary'], {
            'invariant-violation-duplicate-effect': 1,
            'unknown-preserved': 3,
            'recovery-state-diverged': 1,
            'reopened-consistently': 1,
        })
        self.assertIn('Caller-supplied observations only', report['qualification'])
        self.assertEqual(len({row['operation_id'] for row in report['observations']}), 6)
        self.assertEqual(len({row['effect_key'] for row in report['observations']}), 6)

        # Persist the output in an isolated temp file, cold-read it, and verify its body digest.
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory) / 'recovery-report.json'
            saved.write_text(result.stdout, encoding='utf-8')
            cold = json.loads(saved.read_bytes())
        claimed = cold.pop('report_sha256')
        actual = hashlib.sha256((json.dumps(cold, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
        self.assertEqual(claimed, actual)

    def test_owner_backed_timeout_attempts_cold_reopen_without_inheriting_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / 'trial-store'
            plan_sha = trials.freeze_plan(store, make_plan())
            interrupted = make_attempt('timeout-before-readback')
            interrupted['execution'] = {'status': 'interrupted', 'failure_layer': 'harness', 'exit_status': None}
            interrupted['outcome'] = {'results': {}}
            first_sha = trials.seal_attempt(store, plan_sha, interrupted, sealed_at='synthetic-1')
            later = make_attempt('later-readback-attempt')
            later_sha = trials.seal_attempt(store, plan_sha, later, sealed_at='synthetic-2')

            base_observations = json.loads(FIXTURE.read_bytes())
            timeout = next(row for row in base_observations['observations']
                           if row['id'] == 'sb07-timeout-before-readback-synthetic')
            owner_snapshot = {
                'format': 'sandbox-lifecycle-owner-snapshot-1',
                'plan_sha256': plan_sha,
                'observations': {'format': base_observations['format'], 'observations': [timeout]},
                'attempt_bindings': [{
                    'operation_id': timeout['operation_id'],
                    'attempts': [
                        {'attempt_id': 'timeout-before-readback', 'attempt_sha256': first_sha},
                        {'attempt_id': 'later-readback-attempt', 'attempt_sha256': later_sha},
                    ],
                }],
                'owner_receipts': {
                    owner: {'status': 'unknown', 'reference': None, 'sha256': None,
                            'reason': 'no owner receipt snapshot supplied'}
                    for owner in ('MA03', 'MC03')
                },
            }
            input_path = root / 'owner-snapshot.json'
            input_path.write_bytes(trials.canonical(owner_snapshot))
            first_bytes = (store / 'attempts' / f'{first_sha}.json').read_bytes()
            second_bytes = (store / 'attempts' / f'{later_sha}.json').read_bytes()
            result = subprocess.run(
                [sys.executable, '-B', '-m', 'harness.lifecycle_recovery_cli',
                 '--input', str(input_path), '--store', str(store)],
                cwd=ROOT, capture_output=True, text=True, check=True,
            )
            report = json.loads(result.stdout)

            self.assertEqual(report['plan_sha256'], plan_sha)
            self.assertEqual(report['observations'][0]['status'], 'unknown-preserved')
            attempts = report['observations'][0]['sandbox_attempts']
            self.assertEqual([item['attempt_id'] for item in attempts],
                             ['timeout-before-readback', 'later-readback-attempt'])
            self.assertEqual([item['execution_status_as_recorded'] for item in attempts],
                             ['interrupted', 'completed'])
            self.assertTrue(all(item['external_effect_inferred'] is False for item in attempts))
            self.assertEqual(report['owner_receipts']['MA03']['status'], 'unknown')
            self.assertEqual(report['owner_receipts']['MC03']['status'], 'unknown')
            self.assertIn('effect counts remain caller-supplied and unverified', report['qualification'])
            self.assertEqual((store / 'attempts' / f'{first_sha}.json').read_bytes(), first_bytes)
            self.assertEqual((store / 'attempts' / f'{later_sha}.json').read_bytes(), second_bytes)
            self.assertTrue(trials.verify_store(store)['verified'])

    def test_owner_backed_mode_rejects_unbound_attempt_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / 'store'
            plan_sha = trials.freeze_plan(store, make_plan())
            digest = trials.seal_attempt(store, plan_sha, make_attempt('actual-id'), sealed_at='synthetic')
            observation = json.loads(FIXTURE.read_bytes())['observations'][0]
            evidence = {
                'format': 'sandbox-lifecycle-owner-snapshot-1', 'plan_sha256': plan_sha,
                'observations': {'format': 'sandbox-lifecycle-observations-1', 'observations': [observation]},
                'attempt_bindings': [{'operation_id': observation['operation_id'], 'attempts': [
                    {'attempt_id': 'guessed-id', 'attempt_sha256': digest}]}],
                'owner_receipts': {
                    owner: {'status': 'unknown', 'reference': None, 'sha256': None, 'reason': 'not supplied'}
                    for owner in ('MA03', 'MC03')
                },
            }
            with self.assertRaisesRegex(lifecycle_recovery.RecoveryError, 'identity or plan binding'):
                lifecycle_recovery.analyze_owner_snapshots(store, evidence)


if __name__ == '__main__':
    unittest.main()
