import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from harness import authoring, trials
from test_trials import make_attempt, make_plan


class Workbench(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bench, self.store = self.root / 'bench', self.root / 'store'

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, draft='d1', **changes):
        path = self.root / f'{draft}-start.json'
        path.write_bytes(trials.canonical(make_plan(**changes)))
        return authoring.create(self.bench, draft, *authoring.start_from_file(path))

    def edited(self, draft='d1', **changes):
        plan = copy.deepcopy(authoring.read(self.bench, draft)['plan'])
        plan.update(changes)
        return plan

    def plans(self):
        folder = self.store / 'plans'
        return sorted(folder.iterdir()) if folder.is_dir() else []


class Revisions(Workbench):
    def test_revise_keeps_parent_linked_history_and_reports_changes(self):
        r1 = self.start()
        plan = self.edited()
        plan['treatment']['arms']['equipped'] = 'with pinned equipment v2'
        plan['criteria'].append({**plan['criteria'][1], 'id': 'no-rewrite', 'kind': 'adversarial'})
        r2 = authoring.revise(self.bench, 'd1', r1['revision_sha256'], plan, note='edit arm and criteria')
        history = authoring.read(self.bench, 'd1')['history']
        self.assertEqual([h['parent_sha256'] for h in history], [None, r1['revision_sha256']])
        old = authoring.read(self.bench, 'd1', r1['revision_sha256'])
        self.assertFalse(old['is_head'])
        self.assertEqual(old['plan']['treatment']['arms']['equipped'], 'with equipment')
        changes = authoring.preview(self.bench, 'd1', r2['revision_sha256'])['changes_from_parent']
        self.assertEqual(changes['criteria']['added'], ['no-rewrite'])
        self.assertEqual([f['path'] for f in changes['fields']], ['treatment.arms.equipped'])

    def test_stale_parent_is_refused_without_losing_the_head(self):
        r1 = self.start()
        r2 = authoring.revise(self.bench, 'd1', r1['revision_sha256'], self.edited(plan_id='first'))
        with self.assertRaisesRegex(authoring.AuthoringError, 'stale parent'):
            authoring.revise(self.bench, 'd1', r1['revision_sha256'], self.edited(plan_id='second'))
        head = authoring.read(self.bench, 'd1')
        self.assertEqual((head['revision_sha256'], head['plan']['plan_id']), (r2['revision_sha256'], 'first'))

    def test_invalid_start_is_refused_before_a_draft_exists(self):
        with self.assertRaisesRegex(authoring.AuthoringError, 'fully valid plan'):
            self.start(budget={'max_attempts_per_arm': 0, 'stopping_rule': 'x'})
        self.assertFalse((self.bench / 'drafts' / 'd1').exists())
        self.start()
        with self.assertRaisesRegex(authoring.AuthoringError, 'already exists'):
            self.start()

    def test_historical_plans_stay_historical(self):
        r1 = self.start('old', record_kind='historical-retrospective', frozen_before_execution=False)
        with self.assertRaisesRegex(authoring.AuthoringError, 'stays historical'):
            authoring.revise(self.bench, 'old', r1['revision_sha256'],
                             self.edited('old', record_kind='real', frozen_before_execution=True))
        r2 = self.start('new')
        with self.assertRaisesRegex(authoring.AuthoringError, 'cannot become historical'):
            authoring.revise(self.bench, 'new', r2['revision_sha256'],
                             self.edited('new', record_kind='historical-retrospective', frozen_before_execution=False))
        codes = [w['code'] for w in authoring.preview(self.bench, 'old')['warnings']]
        self.assertIn('historical-record', codes)

    def test_ids_links_fifos_and_tampering_are_refused(self):
        for bad in ('../x', 'A', '', 'x/y', 'a' * 65):
            with self.assertRaises(authoring.AuthoringError):
                authoring.read(self.bench, bad)
        target = self.root / 'plan.json'
        target.write_bytes(trials.canonical(make_plan()))
        (self.root / 'link.json').symlink_to(target)
        with self.assertRaisesRegex(authoring.AuthoringError, 'symlinks are refused'):
            authoring.start_from_file(self.root / 'link.json')
        os.mkfifo(self.root / 'pipe.json')
        with self.assertRaisesRegex(authoring.AuthoringError, 'not a regular file'):
            authoring.start_from_file(self.root / 'pipe.json')
        r1 = self.start()
        r2 = authoring.revise(self.bench, 'd1', r1['revision_sha256'], self.edited(plan_id='second'))
        reviewed = authoring.preview(self.bench, 'd1')['preview_sha256']
        revisions = self.bench / 'drafts' / 'd1' / 'revisions'
        for path in revisions.iterdir():
            os.chmod(path, 0o600)
        # An altered head breaks no later link, but the reviewed revision identity no longer exists.
        head = revisions / '000002.json'
        head.write_bytes(head.read_bytes().replace(b'"second"', b'"swapped"'))
        with self.assertRaisesRegex(authoring.AuthoringError, 'not part of this draft'):
            authoring.freeze(self.bench, 'd1', r2['revision_sha256'], reviewed, self.store)
        first = revisions / '000001.json'
        first.write_bytes(first.read_bytes().replace(b'synthetic-plan', b'altered-plan'))
        with self.assertRaisesRegex(authoring.AuthoringError, 'altered'):
            authoring.preview(self.bench, 'd1')
        self.assertEqual(self.plans(), [])


class Preview(Workbench):
    def test_recipe_start_previews_provenance_and_placeholders(self):
        plan, origin = authoring.start_from_recipe('context-consumer', 'receipt-review')
        authoring.create(self.bench, 'recipe', plan, origin)
        view = authoring.preview(self.bench, 'recipe')
        self.assertEqual(view['status'], 'ready')
        self.assertEqual(view['summary']['criteria_by_kind'], {'adversarial': 2, 'consumer': 2, 'project-supplied': 1})
        warnings = {w['code']: w['detail'] for w in view['warnings']}
        self.assertIn('context-two-axis-report', warnings['project-supplied-not-independent'])
        self.assertIn('evaluator_scope.agent_readable_roots[0]', warnings['unknown-value'])
        self.assertEqual(view['grants']['execution'], False)

    def test_refused_preview_and_useful_warnings(self):
        r1 = self.start()
        plan = self.edited(authority={**make_plan()['authority'], 'permissions': {'network': True}})
        plan['treatment']['variables'].append({'path': 'identity.modle', 'reason': 'typo'})
        plan['criteria'][1]['provenance']['reference'] = 'workspace/answers.json'
        plan['criteria'][0]['independent_of_subject'] = True
        r2 = authoring.revise(self.bench, 'd1', r1['revision_sha256'], plan)
        view = authoring.preview(self.bench, 'd1', r2['revision_sha256'])
        self.assertEqual(view['status'], 'refused')
        self.assertIn('never independent evidence', view['errors'][0])
        codes = {w['code'] for w in view['warnings']}
        self.assertLessEqual({'treatment-unknown-field', 'permission-granted', 'evaluator-answer-agent-readable'}, codes)
        with self.assertRaisesRegex(authoring.AuthoringError, 'preview is refused'):
            authoring.freeze(self.bench, 'd1', r2['revision_sha256'], view['preview_sha256'], self.store)
        self.assertEqual(self.plans(), [])


class Freeze(Workbench):
    def test_stale_revision_or_preview_refuses_before_writing_a_plan(self):
        r1 = self.start()
        p1 = authoring.preview(self.bench, 'd1')['preview_sha256']
        r2 = authoring.revise(self.bench, 'd1', r1['revision_sha256'], self.edited(plan_id='revised'))
        with self.assertRaisesRegex(authoring.AuthoringError, 'stale revision'):
            authoring.freeze(self.bench, 'd1', r1['revision_sha256'], p1, self.store)
        with self.assertRaisesRegex(authoring.AuthoringError, 're-run preview'):
            authoring.freeze(self.bench, 'd1', r2['revision_sha256'], p1, self.store)
        self.assertEqual(self.plans(), [])

    def test_freeze_writes_exact_revision_once_and_compare_stays_authoritative(self):
        self.start()
        view = authoring.preview(self.bench, 'd1')
        handoff = authoring.freeze(self.bench, 'd1', view['revision_sha256'], view['preview_sha256'], self.store,
                                   frozen_at='2026-10-24T00:00:00+00:00')
        plan = trials.load_record(self.store, 'plans', handoff['plan_sha256'])
        self.assertEqual(plan, {**authoring.read(self.bench, 'd1')['plan'], 'frozen_at_utc': '2026-10-24T00:00:00+00:00'})
        self.assertEqual(plan, make_plan(frozen_at_utc='2026-10-24T00:00:00+00:00'))
        self.assertEqual((handoff['execution']['status'], handoff['execution']['executor']), ('not-run', None))
        self.assertEqual((handoff['grants']['execution'], handoff['grants']['adoption']), (False, False))
        with self.assertRaisesRegex(authoring.AuthoringError, 'already frozen'):
            authoring.freeze(self.bench, 'd1', view['revision_sha256'], view['preview_sha256'], self.store)
        self.assertEqual(authoring.read(self.bench, 'd1')['freezes'][0]['plan_sha256'], handoff['plan_sha256'])
        for attempt in (make_attempt('a1'), make_attempt('b1', 'equipped')):
            trials.seal_attempt(self.store, handoff['plan_sha256'], attempt, sealed_at='t')
        self.assertEqual(trials.compare(self.store, handoff['plan_sha256'])['pairs'][0]['verdict'], 'comparable')
        reopened = authoring.create(self.bench, 'reopened', *authoring.start_from_store(self.store, handoff['plan_sha256']))
        self.assertEqual(authoring.read(self.bench, 'reopened')['origin']['plan_sha256'], handoff['plan_sha256'])
        self.assertTrue(reopened['revision_sha256'])
        self.assertTrue(trials.verify_store(self.store)['verified'])


class Cli(Workbench):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = authoring.main([str(a) for a in argv])
        return code, json.loads(out.getvalue() or err.getvalue())

    def test_create_preview_freeze_and_actionable_refusals(self):
        bench = ('--workbench', self.bench, '--draft', 'cli')
        code, out = self.run_cli('create', *bench, '--from-recipe', 'context-consumer', '--case', 'nope')
        self.assertEqual(code, 2)
        self.assertIn('case must be one of', out['refused'])
        code, made = self.run_cli('create', *bench, '--from-recipe', 'context-consumer', '--case', 'receipt-review')
        self.assertEqual(code, 0)
        code, view = self.run_cli('preview', *bench)
        self.assertEqual((code, view['status']), (0, 'ready'))
        code, out = self.run_cli('freeze', *bench, '--revision', made['revision_sha256'],
                                 '--preview-sha256', '0' * 64, '--store', self.store)
        self.assertEqual(code, 2)
        self.assertIn('re-run preview', out['refused'])
        code, handoff = self.run_cli('freeze', *bench, '--revision', made['revision_sha256'], '--preview-sha256',
                                     view['preview_sha256'], '--store', self.store,
                                     '--handoff-out', self.root / 'handoff.json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.root / 'handoff.json').read_text()), handoff)
        self.assertEqual(handoff['authored_from']['origin']['kind'], 'context-recipe')


if __name__ == '__main__':
    unittest.main()
