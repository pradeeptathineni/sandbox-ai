"""The five-task protocol keeps human observations separate from a valid JSON receipt."""

import copy
import json
from pathlib import Path
import unittest

from harness import usability


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = json.loads((ROOT / 'recipes/deep-usability/protocol.json').read_text())
SYNTHETIC = json.loads((ROOT / 'recipes/deep-usability/synthetic-capture.json').read_text())


class UsabilityTests(unittest.TestCase):
    def test_synthetic_capture_and_changed_protocol_or_unsupported_human_claim(self):
        report = usability.evaluate(PROTOCOL, SYNTHETIC)
        self.assertEqual(report['counts'], {'completed': 1, 'failed': 0, 'not-run': 3, 'partial': 1})
        self.assertEqual(report['wrong_turns'], 1)
        self.assertEqual(report['source'], 'synthetic')
        self.assertEqual(len(report['observations']), 5)

        changed = copy.deepcopy(PROTOCOL)
        changed['tasks'][0]['instruction'] += ' Extra instruction.'
        with self.assertRaisesRegex(usability.UsabilityError, 'frozen protocol'):
            usability.evaluate(changed, SYNTHETIC)

        claimed = copy.deepcopy(SYNTHETIC)
        claimed['source'] = 'human-observation'
        claimed['app_revision'] = 'a' * 40
        with self.assertRaisesRegex(usability.UsabilityError, 'what the user believed'):
            usability.evaluate(PROTOCOL, claimed)
        claimed['observations'][0]['belief_about_sent'] = 'Nothing was sent.'
        claimed['observations'][0]['artifacts'] = [{'kind': 'notes', 'sha256': 'b' * 64}]
        claimed['observations'][1]['belief_about_sent'] = 'I do not know.'
        claimed['observations'][1]['artifacts'] = [{'kind': 'notes', 'sha256': 'c' * 64}]
        self.assertEqual(usability.evaluate(PROTOCOL, claimed)['source'], 'human-observation')


if __name__ == '__main__':
    unittest.main()
