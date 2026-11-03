import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import unittest

from harness import grounding_calibration, review_sheet
import test_grounding_review as fixtures


class SheetData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_data = False
        self.data = ''
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.scripts += 1
            self.in_data = dict(attrs).get('id') == 'blind-session'

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_data = False

    def handle_data(self, data):
        if self.in_data:
            self.data += data


class OfflineReviewSheet(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GroundingReviewOperations()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.store = Path(self.fixture.tmp.name) / 'calibration'
        self.fixture.study_rows[0]['answerText'] = '</script><script>globalThis.injected=true</script> & literal'
        view = grounding_calibration._view(self.fixture.packet_json, self.fixture.aliases, self.fixture.study_rows)
        result = grounding_calibration.freeze_anchor_set(self.store, {
            'anchorSetId': 'sheet-test', 'packetJson': self.fixture.packet_json,
            'aliases': self.fixture.aliases, 'studyRows': self.fixture.study_rows,
            'referenceAuthor': 'WITHHELD_AUTHOR_SENTINEL', 'referenceMethod': 'WITHHELD_METHOD_SENTINEL',
            'referenceBasis': 'WITHHELD_BASIS_SENTINEL',
            'referenceRatings': [{key: row[key] for key in ('armAlias', 'questionId')} |
                                 {'rating': 'supported', 'rationale': 'WITHHELD_RATIONALE_SENTINEL'} for row in view],
        })
        self.digest = result['anchor_set_sha256']
        self.request = {'schema': 'sandbox.grounding-calibration-request.v1', 'operation': 'blind-session',
                        'input': {'anchorSetSha256': self.digest}}

    def cli(self, request, *args):
        return subprocess.run([sys.executable, '-B', '-m', 'harness.grounding_calibration',
                               '--store', str(self.store), *args], input=json.dumps(request),
                              text=True, capture_output=True, timeout=10, cwd=Path(__file__).parents[1])

    def inventory(self):
        return {str(p.relative_to(self.store)): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}

    def test_cli_sheet_withholds_references_and_keeps_source_text_inert_without_store_writes(self):
        before = self.inventory()
        output = self.cli(self.request, '--format', 'html')
        self.assertEqual(output.returncode, 0, output.stderr)
        parser = SheetData()
        parser.feed(output.stdout)
        session = grounding_calibration.blind_session(self.store, self.digest)
        self.assertEqual(json.loads(parser.data), session)
        self.assertEqual(parser.scripts, 2, 'source closing tags must not create executable scripts')
        self.assertNotIn('WITHHELD_', output.stdout)
        self.assertNotIn('</script><script>globalThis.injected', output.stdout)
        self.assertEqual(self.inventory(), before)
        default = self.cli(self.request)
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(json.loads(default.stdout)['result'], session)

    def test_html_mutation_request_refuses_before_dispatch(self):
        before = self.inventory()
        session = grounding_calibration.blind_session(self.store, self.digest)
        review = {'schema': self.request['schema'], 'operation': 'record-review', 'input': {
            'anchorSetSha256': self.digest, 'reviewer': 'Different reviewer',
            'judgments': [{'armAlias': r['armAlias'], 'questionId': r['questionId'],
                           'rating': 'not-assessed', 'rationale': 'Not assessed in this synthetic test.'}
                          for r in session['view']]}}
        result = self.cli(review, '--format', 'html')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('read-only blind-session', result.stderr)
        self.assertEqual(self.inventory(), before, 'format refusal must precede any append')

    def test_renderer_refuses_reference_or_extra_row_fields(self):
        session = grounding_calibration.blind_session(self.store, self.digest)
        with self.assertRaises(ValueError):
            review_sheet.render(session | {'reference_ratings': []})
        altered = copy.deepcopy(session)
        altered['view'][0]['reference_rating'] = 'supported'
        with self.assertRaises(ValueError):
            review_sheet.render(altered)


if __name__ == '__main__':
    unittest.main()
