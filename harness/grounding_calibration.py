"""Append-only synthetic/caller-authored calibration around the blinded reviewer.

The accepted ``grounding_review`` module remains the source of blind views and
ratings validation. This module freezes anchor labels before review, appends
review/correction events, and reports descriptive agreement only.
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from . import grounding_review

ANCHOR_FORMAT = 'sandbox-grounding-anchor-set-1'
EVENT_FORMAT = 'sandbox-grounding-calibration-event-1'
MAX_BYTES = 8_500_000
SHA = re.compile(r'[a-f0-9]{64}\Z')
RATINGS = grounding_review.RATINGS


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _digest(value, label):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError(f'{label} must be a lowercase SHA-256')
    return value


def _text(value, label, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label} must be bounded nonempty text')
    return value.strip()


def _closed(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('record has unknown or missing fields')
    return value


def _write_once(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, 'xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError('content-addressed record already exists with different bytes')


def _read_json(path, limit=MAX_BYTES):
    data = path.read_bytes()
    if len(data) > limit:
        raise ValueError('record exceeds the read limit')
    value = json.loads(data)
    if _canonical(value) + b'\n' != data:
        raise ValueError('record is not canonical or its bytes changed')
    return value


def _view(packet_json, aliases, study_rows):
    view = grounding_review.session_view({
        'packetJson': packet_json,
        'aliases': aliases,
        'studyRows': study_rows,
    })['view']
    return view


def _ratings(view, rows):
    keys = [{'armAlias': row['armAlias'], 'questionId': row['questionId']} for row in view]
    checked = grounding_review.review_check({'viewKeys': keys, 'judgments': rows})['judgments']
    return checked


def freeze_anchor_set(store, request):
    """Freeze the exact packet-derived blind view and caller-authored reference rows."""
    request = _closed(request, ('anchorSetId', 'packetJson', 'aliases', 'studyRows',
                                'referenceAuthor', 'referenceMethod', 'referenceBasis', 'referenceRatings'))
    anchor_id = _text(request['anchorSetId'], 'anchorSetId', 120)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,119}', anchor_id):
        raise ValueError('anchorSetId has invalid characters')
    packet_json = request['packetJson']
    if not isinstance(packet_json, str) or len(packet_json.encode('utf-8')) > MAX_BYTES:
        raise ValueError('packetJson must be bounded exact frozen packet bytes')
    view = _view(packet_json, request['aliases'], request['studyRows'])
    reference = _ratings(view, request['referenceRatings'])
    reference_author = _text(request['referenceAuthor'], 'referenceAuthor', 200)
    record = {
        'format': ANCHOR_FORMAT,
        'anchor_set_id': anchor_id,
        'frozen_before_reviews': True,
        'frozen_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'packet_sha256': _sha(json.loads(packet_json)),
        'rubric': copy.deepcopy(grounding_review.RUBRIC),
        'rubric_sha256': _sha(grounding_review.RUBRIC),
        'reference_provenance': {
            'author': reference_author,
            'method': _text(request['referenceMethod'], 'referenceMethod', 1000),
            'basis': _text(request['referenceBasis'], 'referenceBasis', 2000),
            'identity_status': 'caller-asserted-unverified',
        },
        'blind_view': view,
        'reference_ratings': reference,
        'limitations': [
            'Anchor ratings are caller-authored references, not verified truth, entailment, or reviewer accuracy.',
            'Agreement is descriptive for this frozen set only and is not a quality score or generalization claim.',
            'The packet, answer text, evidence excerpts, rubric and reference basis may contain synthetic material.',
        ],
    }
    record['anchor_set_sha256'] = _sha(record)
    data = _canonical(record) + b'\n'
    if len(data) > MAX_BYTES:
        raise ValueError('anchor set exceeds the record bound')
    digest = record['anchor_set_sha256']
    _write_once(Path(store) / 'anchor-sets' / f'{digest}.json', data)
    return {'anchor_set_sha256': digest, 'record': record}


def _load_anchor(store, digest):
    digest = _digest(digest, 'anchor_set_sha256')
    path = Path(store) / 'anchor-sets' / f'{digest}.json'
    record = _read_json(path)
    _closed(record, ('format', 'anchor_set_id', 'frozen_before_reviews', 'frozen_at_utc', 'packet_sha256',
                     'rubric', 'rubric_sha256', 'reference_provenance', 'blind_view',
                     'reference_ratings', 'limitations', 'anchor_set_sha256'))
    body = {key: value for key, value in record.items() if key != 'anchor_set_sha256'}
    if record['format'] != ANCHOR_FORMAT or record['anchor_set_sha256'] != digest or _sha(body) != digest:
        raise ValueError('anchor set digest or format is invalid')
    if record['rubric'] != grounding_review.RUBRIC or record['rubric_sha256'] != _sha(grounding_review.RUBRIC):
        raise ValueError('anchor set does not use the exact predeclared frozen rubric')
    return record


def blind_session(store, anchor_set_sha256):
    record = _load_anchor(store, anchor_set_sha256)
    return {'anchor_set_sha256': record['anchor_set_sha256'],
            'rubric': copy.deepcopy(record['rubric']),
            'view': copy.deepcopy(record['blind_view']),
            'reference_ratings_withheld': True}


def _events(store, anchor_sha):
    root = Path(store) / 'events' / anchor_sha
    paths = sorted(root.glob('*.json')) if root.is_dir() else []
    if len(paths) > 10000:
        raise ValueError('calibration history exceeds its event limit')
    events, parent = [], None
    for index, path in enumerate(paths, start=1):
        row = _read_json(path)
        _closed(row, ('format', 'anchor_set_sha256', 'sequence', 'parent_sha256', 'event_type', 'payload', 'event_sha256'))
        body = {key: value for key, value in row.items() if key != 'event_sha256'}
        if (row['format'] != EVENT_FORMAT or row['event_type'] not in {'review', 'correction'} or
                row['anchor_set_sha256'] != anchor_sha or
                row['sequence'] != index or row['parent_sha256'] != parent or _sha(body) != row['event_sha256']):
            raise ValueError('calibration event chain is invalid or out of order')
        if path.name != f'{index:06d}-{row["event_sha256"]}.json':
            raise ValueError('calibration event locator does not match its exact digest')
        events.append(row)
        parent = row['event_sha256']
    return events


def _append(store, anchor_sha, event_type, build_payload):
    anchor_sha = _digest(anchor_sha, 'anchor_set_sha256')
    root = Path(store) / 'events' / anchor_sha
    root.mkdir(parents=True, exist_ok=True)
    lock = root / '.append-lock'
    try:
        with open(lock, 'xb'):
            pass
    except FileExistsError as exc:
        raise ValueError('calibration history is locked by another or interrupted append; preserve and inspect it') from exc
    try:
        anchor = _load_anchor(store, anchor_sha)
        events = _events(store, anchor_sha)
        payload = build_payload(anchor, events)
        body = {'format': EVENT_FORMAT, 'anchor_set_sha256': anchor['anchor_set_sha256'],
                'sequence': len(events) + 1, 'parent_sha256': events[-1]['event_sha256'] if events else None,
                'event_type': event_type, 'payload': payload}
        row = {**body, 'event_sha256': _sha(body)}
        data = _canonical(row) + b'\n'
        if len(data) > MAX_BYTES:
            raise ValueError('calibration event exceeds the record bound')
        _write_once(root / f'{row["sequence"]:06d}-{row["event_sha256"]}.json', data)
        return row
    finally:
        lock.unlink(missing_ok=True)


def _agreement(reference, judgments):
    expected = {(row['armAlias'], row['questionId']): row for row in reference}
    rows = []
    for judgment in judgments:
        key = (judgment['armAlias'], judgment['questionId'])
        anchor = expected[key]
        unknown = judgment['rating'] in {'uncertain', 'not-assessed'} or anchor['rating'] in {'uncertain', 'not-assessed'}
        rows.append({'armAlias': key[0], 'questionId': key[1], 'reviewer_rating': judgment['rating'],
                     'anchor_rating': anchor['rating'],
                     'status': 'unknown' if unknown else 'agreement' if judgment['rating'] == anchor['rating'] else 'disagreement'})
    statuses = ('agreement', 'disagreement', 'unknown')
    return {'rows': rows, 'counts': {status: sum(row['status'] == status for row in rows) for status in statuses} |
            {'denominator': len(rows)},
            'qualification': 'Descriptive agreement with caller-authored anchors for this frozen set; not verified accuracy or quality.'}


def record_review(store, anchor_set_sha256, reviewer, judgments):
    reviewer = _text(reviewer, 'reviewer', 200)

    def build(anchor, existing):
        if reviewer.casefold() == anchor['reference_provenance']['author'].casefold():
            raise ValueError('reviewer must differ from caller-declared anchor author')
        if any(row['event_type'] == 'review' and row['payload']['reviewer'].casefold() == reviewer.casefold()
               for row in existing):
            raise ValueError('reviewer already has a retained review; submit a correction event instead')
        checked = _ratings(anchor['blind_view'], judgments)
        return {'reviewer': reviewer, 'reviewer_identity_status': 'caller-asserted-unverified',
                'judgments': checked,
                'calibration': _agreement(anchor['reference_ratings'], checked)}

    return _append(store, anchor_set_sha256, 'review', build)


def correct_rating(store, anchor_set_sha256, review_event_sha256, arm_alias, question_id,
                   rating, rationale, correction_reason, reviewer):
    review_sha = _digest(review_event_sha256, 'review_event_sha256')
    reviewer = _text(reviewer, 'reviewer', 200)
    rationale = _text(rationale, 'rationale', 1000)
    correction_reason = _text(correction_reason, 'correction_reason', 1000)
    if rating not in RATINGS:
        raise ValueError('correction rating is invalid')
    key = (arm_alias, question_id)

    def build(anchor, events):
        source = next((row for row in events if row['event_sha256'] == review_sha and row['event_type'] == 'review'), None)
        if source is None:
            raise ValueError('correction must reference an existing immutable review event')
        if reviewer.casefold() != source['payload']['reviewer'].casefold():
            raise ValueError('correction reviewer must match the original review author')
        prior_events = [row for row in events if row['event_type'] == 'correction' and
                        row['payload']['review_event_sha256'] == review_sha and
                        (row['payload']['armAlias'], row['payload']['questionId']) == key]
        original = next((row for row in source['payload']['judgments']
                         if (row['armAlias'], row['questionId']) == key), None)
        if original is None or arm_alias not in grounding_review.ALIASES:
            raise ValueError('correction row is outside the exact original blind review')
        prior = prior_events[-1] if prior_events else None
        prior_rating = prior['payload']['new_rating'] if prior else original['rating']
        judgments = [dict(row) for row in source['payload']['judgments']]
        by_key = {(row['armAlias'], row['questionId']): row for row in judgments}
        for event in events:
            if event['event_type'] != 'correction' or event['payload']['review_event_sha256'] != review_sha:
                continue
            previous = event['payload']
            corrected = by_key[(previous['armAlias'], previous['questionId'])]
            corrected['rating'] = previous['new_rating']
            corrected['rationale'] = previous['new_rationale']
        current = by_key[key]
        current['rating'] = rating
        current['rationale'] = rationale
        return {'review_event_sha256': review_sha, 'armAlias': arm_alias, 'questionId': question_id,
                'prior_event_sha256': prior['event_sha256'] if prior else review_sha,
                'prior_rating': prior_rating, 'new_rating': rating,
                'new_rationale': rationale, 'correction_reason': correction_reason,
                'reviewer': reviewer,
                'calibration': _agreement(anchor['reference_ratings'], judgments)}

    return _append(store, anchor_set_sha256, 'correction', build)


def read_history(store, anchor_set_sha256):
    anchor = _load_anchor(store, anchor_set_sha256)
    events = _events(store, anchor_set_sha256)
    latest = {}
    for event in events:
        if event['event_type'] == 'correction':
            payload = event['payload']
            latest[(payload['review_event_sha256'], payload['armAlias'], payload['questionId'])] = event
    reviewer_rows = {}
    effective_by_reviewer = {}
    for event in events:
        if event['event_type'] != 'review':
            continue
        reviewer, judgments = event['payload']['reviewer'], event['payload']['judgments']
        effective = []
        for row in judgments:
            correction = latest.get((event['event_sha256'], row['armAlias'], row['questionId']))
            effective.append({**row, **({'rating': correction['payload']['new_rating'],
                                         'rationale': correction['payload']['new_rationale'],
                                         'correction_event_sha256': correction['event_sha256']}
                                        if correction else {})})
        effective_by_reviewer[reviewer] = {(row['armAlias'], row['questionId']): row for row in effective}
        reviewer_rows[reviewer] = {'review_event_sha256': event['event_sha256'],
                                   'effective_calibration': _agreement(anchor['reference_ratings'], effective)}
    reviewer_list = list(reviewer_rows)
    disagreements = []
    if len(reviewer_list) >= 2:
        by_reviewer = {name: effective_by_reviewer[name] for name in reviewer_list}
        for key in sorted(by_reviewer[reviewer_list[0]]):
            left = by_reviewer[reviewer_list[0]][key]['rating']
            right = by_reviewer[reviewer_list[1]][key]['rating']
            status = 'unknown' if left in {'uncertain', 'not-assessed'} or right in {'uncertain', 'not-assessed'} else 'agreement' if left == right else 'disagreement'
            disagreements.append({'armAlias': key[0], 'questionId': key[1], 'reviewerA': left, 'reviewerB': right, 'status': status})
    return {'anchor_set': anchor, 'events': events, 'reviewers': reviewer_rows,
            'interreviewer': {'rows': disagreements,
                              'counts': {status: sum(row['status'] == status for row in disagreements)
                                         for status in ('agreement', 'disagreement', 'unknown')} |
                                         {'denominator': len(disagreements)}},
            'qualification': 'All reviewer identities and anchor reference labels are caller assertions; agreement is descriptive only.'}


def operate(request, store):
    _closed(request, ('schema', 'operation', 'input'))
    if request['schema'] != 'sandbox.grounding-calibration-request.v1':
        raise ValueError('unsupported grounding-calibration schema')
    value = request['input']
    operations = {
        'freeze-anchors': (('anchorSetId', 'packetJson', 'aliases', 'studyRows', 'referenceAuthor',
                            'referenceMethod', 'referenceBasis', 'referenceRatings'),
                           lambda: freeze_anchor_set(store, value)),
        'blind-session': (('anchorSetSha256',), lambda: blind_session(store, value['anchorSetSha256'])),
        'record-review': (('anchorSetSha256', 'reviewer', 'judgments'),
                          lambda: record_review(store, value['anchorSetSha256'], value['reviewer'], value['judgments'])),
        'correct-rating': (('anchorSetSha256', 'reviewEventSha256', 'armAlias', 'questionId',
                            'rating', 'rationale', 'correctionReason', 'reviewer'),
                           lambda: correct_rating(store, value['anchorSetSha256'], value['reviewEventSha256'],
                               value['armAlias'], value['questionId'], value['rating'], value['rationale'],
                               value['correctionReason'], value['reviewer'])),
        'read-history': (('anchorSetSha256',), lambda: read_history(store, value['anchorSetSha256']))}
    if request['operation'] not in operations:
        raise ValueError('unsupported grounding-calibration operation')
    keys, function = operations[request['operation']]
    _closed(value, keys)
    return {'schema': 'sandbox.grounding-calibration-result.v1', 'operation': request['operation'],
            'result': function()}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--format', choices=('json', 'html'), default='json',
                        help='HTML is available only for the read-only blind-session operation.')
    args = parser.parse_args()
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise SystemExit('input exceeds limit')
    try:
        request = json.loads(raw)
        if args.format == 'html' and (not isinstance(request, dict) or request.get('operation') != 'blind-session'):
            raise ValueError('HTML output supports only the read-only blind-session operation')
        result = operate(request, args.store)
        if args.format == 'html':
            from .review_sheet import render
            sys.stdout.write(render(result['result']))
        else:
            sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(',', ':'), sort_keys=True) + '\n')
    except Exception as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == '__main__':
    main()
