import json
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import unittest
from pathlib import Path

from harness import grounding_calibration
from test_grounding_review import GroundingReviewOperations


class GroundingCalibrationLifecycle(unittest.TestCase):
    def test_frozen_blind_anchors_append_review_and_correction_history(self):
        source = GroundingReviewOperations()
        source.setUp()
        try:
            store = Path(source.tmp.name) / 'calibration-store'
            blind_rows = grounding_calibration._view(source.packet_json, source.aliases, source.study_rows)
            reference = [{'armAlias': row['armAlias'], 'questionId': row['questionId'],
                          'rating': 'supported', 'rationale': 'Synthetic predeclared anchor.'}
                         for row in blind_rows]
            frozen = grounding_calibration.freeze_anchor_set(store, {
                'anchorSetId': 'synthetic-anchor-v1', 'packetJson': source.packet_json,
                'aliases': source.aliases, 'studyRows': source.study_rows,
                'referenceAuthor': 'Anchor author', 'referenceMethod': 'Synthetic authored examples',
                'referenceBasis': 'Fixture-only reference labels, not source truth.',
                'referenceRatings': reference,
            })
            anchor_sha = frozen['anchor_set_sha256']
            session = grounding_calibration.blind_session(store, anchor_sha)
            self.assertTrue(session['reference_ratings_withheld'])
            self.assertNotIn('reference_ratings', session)
            keys = [{'armAlias': row['armAlias'], 'questionId': row['questionId']} for row in session['view']]
            first = [{'armAlias': row['armAlias'], 'questionId': row['questionId'],
                      'rating': 'supported', 'rationale': 'Synthetic independent rating.'}
                     for row in session['view']]
            second = [dict(row, rating='unsupported' if index == 0 else 'supported',
                           rationale='Synthetic second reviewer.')
                      for index, row in enumerate(first)]
            left = grounding_calibration.record_review(store, anchor_sha, 'Reviewer one', first)
            right = grounding_calibration.record_review(store, anchor_sha, 'Reviewer two', second)
            self.assertEqual(left['payload']['calibration']['counts']['agreement'], len(keys))
            self.assertEqual(right['payload']['calibration']['counts']['disagreement'], 1)
            def duplicate_submit():
                try:
                    grounding_calibration.record_review(store, anchor_sha, 'Reviewer three', first)
                    return 'accepted'
                except ValueError:
                    return 'refused'
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: duplicate_submit(), range(2)))
            self.assertEqual(outcomes.count('accepted'), 1)
            self.assertEqual(outcomes.count('refused'), 1)
            left_path = store / 'events' / anchor_sha / f"{left['sequence']:06d}-{left['event_sha256']}.json"
            original_bytes = left_path.read_bytes()
            correction = grounding_calibration.correct_rating(
                store, anchor_sha, left['event_sha256'], keys[0]['armAlias'], keys[0]['questionId'],
                'uncertain', 'Synthetic correction after reread.', 'Original rating was too certain.', 'Reviewer one')
            self.assertEqual(correction['payload']['prior_rating'], 'supported')
            correction2 = grounding_calibration.correct_rating(
                store, anchor_sha, left['event_sha256'], keys[0]['armAlias'], keys[0]['questionId'],
                'unsupported', 'Second synthetic correction.', 'Reassessment changed the row again.', 'Reviewer one')
            self.assertEqual(correction2['payload']['prior_rating'], 'uncertain')
            self.assertEqual(correction2['payload']['prior_event_sha256'], correction['event_sha256'])
            correction3 = grounding_calibration.correct_rating(
                store, anchor_sha, left['event_sha256'], keys[1]['armAlias'], keys[1]['questionId'],
                'uncertain', 'Keep this row unresolved.', 'Available evidence remains unclear.', 'Reviewer one')
            self.assertEqual(left_path.read_bytes(), original_bytes, 'corrections append; old ratings stay byte-identical')
            history = grounding_calibration.read_history(store, anchor_sha)
            self.assertEqual(len(history['events']), 6)
            self.assertEqual(history['reviewers']['Reviewer one']['effective_calibration']['counts']['unknown'], 1)
            self.assertEqual(history['reviewers']['Reviewer one']['effective_calibration']['counts']['disagreement'], 1)
            self.assertEqual(correction3['payload']['calibration'],
                             history['reviewers']['Reviewer one']['effective_calibration'])
            self.assertEqual(history['interreviewer']['counts']['unknown'], 1)
            self.assertTrue(all(event['payload'].get('reviewer_identity_status', 'caller-asserted-unverified')
                                == 'caller-asserted-unverified' for event in history['events']
                                if event['event_type'] == 'review'))

            request = {'schema': 'sandbox.grounding-calibration-request.v1', 'operation': 'read-history',
                       'input': {'anchorSetSha256': anchor_sha}}
            completed = subprocess.run([sys.executable, '-B', '-m', 'harness.grounding_calibration',
                                        '--store', str(store)], input=json.dumps(request), text=True,
                                       capture_output=True, check=False, cwd=Path(__file__).parents[1], timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)['result']['events'], history['events'])
        finally:
            source.tearDown()


if __name__ == '__main__':
    unittest.main()
