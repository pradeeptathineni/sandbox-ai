import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
