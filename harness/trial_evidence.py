"""Portable paired-trial evidence and caller-authored advisory handoff.

This composes the frozen plan, sealed attempts and existing structural comparator. It runs no experiment,
adds no metric engine, and never adopts or recommends an arm.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import trials

FORMAT = 'sandbox-trial-pair-evidence-1'
ADVISORY_FORMAT = 'sandbox-trial-advisory-handoff-1'
DISPOSITIONS = ('retain-baseline', 'investigate-candidate', 'no-preference', 'insufficient-evidence')
MAX_PACKET_BYTES = 8_000_000
MAX_PLAN_BYTES = 1_000_000
MAX_ATTEMPT_BYTES = 3_000_000
MAX_ATTEMPT_FILES_SCANNED = 5_000
MAX_SCAN_BYTES = 64_000_000
MAX_ATTEMPTS_PER_PLAN = 256
LIMITATIONS = [
    'Attempt records and their stated observations are preserved; Sandbox does not authenticate who sealed them.',
    'Pair verdict is structural identity comparison, not proof of usefulness, quality, causation or general superiority.',
    'Unknown and incomparable measurements remain unknown; no statistical uncertainty or missing value is filled in.',
    'The provisional disposition is caller-authored advisory input, not an automatically inferred recommendation.',
    'This advisory handoff grants no adoption authority; any attempt-level adoption fields remain in the exact embedded records.',
]


def _require(ok, message):
    if not ok:
        raise trials.TrialError(message)


def _digest(value):
    return trials.sha256_bytes(trials.canonical(value))


def _canonical_within(value, limit, label):
    try:
        data = trials.canonical(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise trials.TrialError(f'{label} cannot be canonicalized: {exc}') from exc
    _require(len(data) <= limit, f'{label} exceeds the {limit}-byte limit')
    return data


def _validate_advisory(advisory, plan):
    keys = {'format', 'author', 'disposition', 'rationale', 'dissent', 'revisit_trigger'}
    _require(isinstance(advisory, dict) and set(advisory) == keys,
             'advisory fields must be exactly ' + ', '.join(sorted(keys)))
    _require(advisory['format'] == ADVISORY_FORMAT, f'advisory format must be {ADVISORY_FORMAT}')
    _require(advisory['disposition'] in DISPOSITIONS,
             f'disposition must be one of {DISPOSITIONS}')
    for field in ('author', 'rationale', 'revisit_trigger'):
        _require(isinstance(advisory[field], str) and advisory[field].strip() and len(advisory[field]) <= 2000,
                 f'advisory {field} must be non-empty text of at most 2000 characters')
    _require(advisory['dissent'] is None or
             (isinstance(advisory['dissent'], str) and len(advisory['dissent']) <= 2000),
             'advisory dissent must be null or text of at most 2000 characters')


def _unresolved(plan, baseline, candidate, pair):
    missing_results = {}
    for name, attempt in (('baseline', baseline), ('candidate', candidate)):
        absent = [criterion['id'] for criterion in plan['criteria']
                  if attempt['outcome'].get('results', {}).get(criterion['id'], {}).get('result', 'unknown')
                  == 'unknown']
        missing_results[name] = absent
    measurement_status = {
        name: row['status'] for name, row in pair['measurements'].items()
        if row['status'].startswith(('unknown:', 'incomparable:'))
    }
    expected_cases = {name: entry['sha256'] for name, entry in plan['cases'].items()}
    case_alignment = {}
    for name, attempt in (('baseline', baseline), ('candidate', candidate)):
        observed = attempt['identity']['cases']
        if not isinstance(observed, dict):
            case_alignment[name] = {
                'status': 'unknown' if observed is None else 'mismatched',
                'missing_case_ids': [],
                'unexpected_case_ids': [],
                'changed_case_ids': [],
                'reason': attempt['unknown'].get('cases') if observed is None else
                          'identity.cases is not a case-to-digest mapping',
            }
            continue
        missing = sorted(set(expected_cases) - set(observed))
        unexpected = sorted(set(observed) - set(expected_cases))
        changed = sorted(case_id for case_id in set(expected_cases) & set(observed)
                         if expected_cases[case_id] != observed[case_id])
        case_alignment[name] = {
            'status': 'matched' if not (missing or unexpected or changed) else 'mismatched',
            'missing_case_ids': missing,
            'unexpected_case_ids': unexpected,
            'changed_case_ids': changed,
            'reason': None,
        }
    return {
        'incomplete_attempts': [attempt['attempt_id'] for attempt in (baseline, candidate)
                                if attempt['execution']['status'] != 'completed'],
        'case_alignment': case_alignment,
        'unknown_criterion_results': missing_results,
        'unknown_or_incomparable_measurements': measurement_status,
        'qualification': 'These are explicit gaps in the supplied records, not estimated values.',
    }


def _build_packet(plan_sha256, plan, baseline_row, candidate_row, advisory):
    baseline = baseline_row['record']
    candidate = candidate_row['record']
    _validate_advisory(advisory, plan)
    _require(baseline['arm'] == plan['treatment']['baseline_arm'],
             'selected baseline attempt must belong to the frozen plan baseline arm')
    _require(candidate['arm'] != baseline['arm'], 'selected candidate must be from a non-baseline arm')
    pair = trials.compare_pair(plan, baseline, candidate)
    body = {
        'format': FORMAT,
        'plan': {'sha256': plan_sha256, 'record': plan},
        'selected_attempts': {
            'baseline': baseline_row,
            'candidate': candidate_row,
        },
        'paired_outcome': pair,
        'unresolved_evidence': _unresolved(plan, baseline, candidate, pair),
        'advisory_handoff': {
            'format': ADVISORY_FORMAT,
            'status': 'provisional-advisory',
            'decision_owner': plan['decision']['owner'],
            'intended_consumer': plan['decision']['consumer'],
            'decision_question': plan['decision']['question'],
            'identity_qualification': 'Owner, consumer and author are caller-supplied labels from the plan and handoff; they are not verified identities or authorization.',
            'author': advisory['author'],
            'disposition': advisory['disposition'],
            'rationale': advisory['rationale'],
            'dissent': advisory['dissent'],
            'revisit_trigger': advisory['revisit_trigger'],
        },
        'adoption_handoff': {
            'status': 'no-authority-granted',
            'qualification': 'This advisory handoff does not change or record adoption; attempt-level adoption fields remain in the embedded records.',
        },
        'limitations': list(LIMITATIONS),
    }
    return {**body, 'packet_sha256': _digest(body)}


def _select_attempt(store, plan_sha256, plan, attempt_id):
    folder = Path(store) / 'attempts'
    paths = sorted(folder.glob('*.json')) if folder.is_dir() else []
    _require(len(paths) <= MAX_ATTEMPT_FILES_SCANNED,
             f'attempt store exceeds the {MAX_ATTEMPT_FILES_SCANNED}-record scan limit')
    matches = []
    plan_attempts = 0
    scanned_bytes = 0
    for path in paths:
        size = path.stat().st_size
        _require(size <= MAX_ATTEMPT_BYTES,
                 f'attempt record {path.name} exceeds the {MAX_ATTEMPT_BYTES}-byte limit')
        scanned_bytes += size
        _require(scanned_bytes <= MAX_SCAN_BYTES,
                 f'attempt store scan exceeds the {MAX_SCAN_BYTES}-byte total limit')
        digest = path.stem
        record = trials.load_record(store, 'attempts', digest)
        if record.get('plan_sha256') == plan_sha256:
            plan_attempts += 1
            _require(plan_attempts <= MAX_ATTEMPTS_PER_PLAN,
                     f'plan exceeds the {MAX_ATTEMPTS_PER_PLAN}-attempt evidence bound')
            if record.get('attempt_id') == attempt_id:
                matches.append((digest, record))
    _require(len(matches) == 1, f'attempt ID {attempt_id!r} must identify exactly one sealed attempt')
    digest, record = matches[0]
    trials.validate_sealed(record, plan)
    _require(_digest(record) == digest, f'attempt {attempt_id!r} digest mismatch')
    return {'sha256': digest, 'record': record}


def create_packet(store, plan_sha256, baseline_attempt_id, candidate_attempt_id, advisory):
    """Bind one exact baseline/candidate pair to caller-authored provisional advice."""
    _require(trials._is_sha256(plan_sha256), 'plan SHA-256 must be lowercase hexadecimal')
    plan_path = Path(store) / 'plans' / f'{plan_sha256}.json'
    _require(plan_path.is_file() and os.path.getsize(plan_path) <= MAX_PLAN_BYTES,
             f'frozen plan is missing or exceeds the {MAX_PLAN_BYTES}-byte limit')
    plan = trials.load_record(store, 'plans', plan_sha256)
    _canonical_within(plan, MAX_PLAN_BYTES, 'frozen plan')
    trials.validate_plan(plan)
    _require(_digest(plan) == plan_sha256, 'frozen plan hash mismatch')
    _require(baseline_attempt_id != candidate_attempt_id, 'attempt IDs must differ')
    baseline = _select_attempt(store, plan_sha256, plan, baseline_attempt_id)
    candidate = _select_attempt(store, plan_sha256, plan, candidate_attempt_id)
    packet = _build_packet(plan_sha256, plan, baseline, candidate, advisory)
    _canonical_within(packet, MAX_PACKET_BYTES, 'portable pair packet')
    return packet


def validate_packet(packet):
    """Cold-validate a portable packet without access to the original store or executing a trial."""
    keys = {'format', 'plan', 'selected_attempts', 'paired_outcome', 'unresolved_evidence',
            'advisory_handoff', 'adoption_handoff', 'limitations', 'packet_sha256'}
    _require(isinstance(packet, dict) and set(packet) == keys, 'packet fields are closed')
    _canonical_within(packet, MAX_PACKET_BYTES, 'portable pair packet')
    _require(packet['format'] == FORMAT, f'packet format must be {FORMAT}')
    plan_row = packet['plan']
    _require(isinstance(plan_row, dict) and set(plan_row) == {'sha256', 'record'}, 'invalid plan closure')
    plan = plan_row['record']
    _canonical_within(plan, MAX_PLAN_BYTES, 'frozen plan')
    trials.validate_plan(plan)
    _require(plan_row['sha256'] == _digest(plan), 'frozen plan SHA-256 mismatch')
    selected = packet['selected_attempts']
    _require(isinstance(selected, dict) and set(selected) == {'baseline', 'candidate'},
             'one selected baseline and candidate are required')
    rows = []
    for side in ('baseline', 'candidate'):
        row = selected[side]
        _require(isinstance(row, dict) and set(row) == {'sha256', 'record'}, f'invalid {side} attempt closure')
        _canonical_within(row['record'], MAX_ATTEMPT_BYTES, f'{side} attempt')
        trials.validate_sealed(row['record'], plan)
        _require(row['sha256'] == _digest(row['record']), f'{side} attempt SHA-256 mismatch')
        rows.append(row)
    _require(rows[0]['record']['attempt_id'] != rows[1]['record']['attempt_id'],
             'selected attempt IDs must differ')
    handoff = packet['advisory_handoff']
    advisory = {
        'format': handoff.get('format'),
        'author': handoff.get('author'),
        'disposition': handoff.get('disposition'),
        'rationale': handoff.get('rationale'),
        'dissent': handoff.get('dissent'),
        'revisit_trigger': handoff.get('revisit_trigger'),
    } if isinstance(handoff, dict) else None
    rebuilt = _build_packet(plan_row['sha256'], plan, *rows, advisory)
    _require({key: value for key, value in rebuilt.items() if key != 'packet_sha256'}
             == {key: value for key, value in packet.items() if key != 'packet_sha256'},
             'packet evidence does not match its retained records')
    _require(packet['packet_sha256'] == _digest({key: value for key, value in packet.items()
                                                  if key != 'packet_sha256'}), 'packet digest mismatch')
    return packet


def revise_advisory_packet(packet, advisory):
    """Return a new exact packet revision with caller-authored advisory text.

    The frozen plan, attempt records, structural pair and unresolved evidence
    are revalidated and reused. This does not revise or authorize adoption.
    """
    value = validate_packet(packet)
    rows = value['selected_attempts']
    revised = _build_packet(value['plan']['sha256'], value['plan']['record'],
                            rows['baseline'], rows['candidate'], advisory)
    _canonical_within(revised, MAX_PACKET_BYTES, 'portable pair packet')
    return validate_packet(revised)


def render_packet(packet, format='json'):
    value = validate_packet(packet)
    if format == 'json':
        text = trials.canonical(value).decode('utf-8')
    elif format != 'markdown':
        raise trials.TrialError('format must be json or markdown')
    else:
        text = _render_markdown(value)
    _require(len(text.encode('utf-8')) <= MAX_PACKET_BYTES,
             f'rendered packet exceeds the {MAX_PACKET_BYTES}-byte limit')
    return text


def _render_markdown(value):
    pair = value['paired_outcome']
    handoff = value['advisory_handoff']
    lines = [f'# Trial evidence: {value["plan"]["record"]["plan_id"]}', '',
             f'Packet SHA-256: `{value["packet_sha256"]}`.',
             f'Plan SHA-256: `{value["plan"]["sha256"]}`.',
             f'Attempt pair: `{pair["a"]}` vs `{pair["b"]}`; structural verdict: **{pair["verdict"]}**; '
             f'outcome comparable: **{str(pair["outcome_comparable"]).lower()}**.', '']
    lines += ['', '## Pair qualifications', '']
    lines.extend(f'- {flag}' for flag in pair['flags'])
    lines.extend(f'- Declared treatment: `{row["field"]}`' for row in pair['declared_treatment_differences'])
    lines.extend(f'- Unexpected control difference: `{row["field"]}`'
                 for row in pair['unexpected_control_differences'])
    lines.extend(f'- Unidentified control: `{row["field"]}` ({row["a"]} / {row["b"]})'
                 for row in pair['unidentified_controls'])
    lines.extend(f'- Plan deviation: `{row["field"]}`' for row in pair['plan_deviations'])
    if not any((pair['flags'], pair['declared_treatment_differences'],
                pair['unexpected_control_differences'], pair['unidentified_controls'], pair['plan_deviations'])):
        lines.append('- No comparison qualifications recorded.')
    lines += ['', '| Criterion | Kind | Owner | Baseline | Candidate |', '| --- | --- | --- | --- | --- |']
    for criterion_id, row in pair['results'].items():
        lines.append(f'| {criterion_id} | {row["kind"]} | {row["owner"]} | {row["a"]} | {row["b"]} |')
    lines += ['', '## Measurements', '',
              '| Name | Baseline | Candidate | Candidate − baseline | Status |',
              '| --- | --- | --- | --- | --- |']
    for name, row in pair['measurements'].items():
        def display(measurement):
            if measurement is None or measurement['value'] is None:
                return 'unknown'
            return f'{measurement["value"]} {measurement["unit"]} ({measurement["kind"]})'
        delta = row.get('b_minus_a', 'unknown')
        lines.append(f'| {name} | {display(row["a"])} | {display(row["b"])} | {delta} | {row["status"]} |')
    gaps = value['unresolved_evidence']
    lines += ['', '## Unresolved evidence', '',
              f'Incomplete attempts: {", ".join(gaps["incomplete_attempts"]) or "none recorded"}.',
              'Case alignment:']
    for side, row in gaps['case_alignment'].items():
        reason = f'; reason: {row["reason"]}' if row['reason'] else ''
        lines.append(f'- {side}: {row["status"]}; missing {row["missing_case_ids"]}; '
                     f'unexpected {row["unexpected_case_ids"]}; changed {row["changed_case_ids"]}'
                     f'{reason}.')
    lines += [
              f'Unknown criterion results: {json.dumps(gaps["unknown_criterion_results"], sort_keys=True)}.',
              f'Unknown or incomparable measurements: {json.dumps(gaps["unknown_or_incomparable_measurements"], sort_keys=True)}.', '',
              '## Advisory handoff', '',
              f'Decision owner: {handoff["decision_owner"]}; consumer: {handoff["intended_consumer"]}.',
              f'Identity qualification: {handoff["identity_qualification"]}',
              f'Question: {handoff["decision_question"]}',
              f'Caller-authored provisional disposition: **{handoff["disposition"]}** by {handoff["author"]}.',
              f'Rationale: {handoff["rationale"]}',
              f'Dissent: {handoff["dissent"] or "none supplied"}',
              f'Revisit trigger: {handoff["revisit_trigger"]}', '',
              f'**Adoption authority:** {value["adoption_handoff"]["status"]}. '
              'Any attempt-level adoption fields remain in the embedded attempt records.', '',
              '## Limitations', '']
    lines.extend(f'- {item}' for item in value['limitations'])
    return '\n'.join(lines) + '\n'


def _read(path):
    data = Path(path).read_bytes()
    _require(len(data) <= MAX_PACKET_BYTES, f'{Path(path).name} exceeds the {MAX_PACKET_BYTES}-byte limit')
    try:
        return json.loads(data)
    except (ValueError, RecursionError) as exc:
        raise trials.TrialError(f'{Path(path).name} is not valid bounded JSON: {exc}') from exc


def _write_new(path, text):
    with open(path, 'x', encoding='utf-8') as handle:
        handle.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create', help='compose an exact pair and caller-authored advisory handoff')
    create.add_argument('--store', type=Path, required=True)
    create.add_argument('--plan-sha256', required=True)
    create.add_argument('--baseline-attempt', required=True)
    create.add_argument('--candidate-attempt', required=True)
    create.add_argument('--advisory', type=Path, required=True)
    create.add_argument('--output', type=Path, required=True)
    render = sub.add_parser('render', help='cold-validate and render one portable packet')
    render.add_argument('--input', type=Path, required=True)
    render.add_argument('--format', choices=('json', 'markdown'), default='markdown')
    render.add_argument('--output', type=Path)
    revise = sub.add_parser('revise-advisory', help='create a new packet digest with an edited caller-authored advisory')
    revise.add_argument('--input', type=Path, required=True)
    revise.add_argument('--advisory', type=Path, required=True)
    revise.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'create':
            packet = create_packet(args.store, args.plan_sha256, args.baseline_attempt,
                                   args.candidate_attempt, _read(args.advisory))
            text = render_packet(packet, 'json')
            _write_new(args.output, text)
        elif args.command == 'revise-advisory':
            packet = revise_advisory_packet(_read(args.input), _read(args.advisory))
            text = render_packet(packet, 'json')
            _write_new(args.output, text)
        else:
            packet = validate_packet(_read(args.input))
            text = render_packet(packet, args.format)
            if args.output:
                _write_new(args.output, text)
            else:
                sys.stdout.write(text)
    except (OSError, json.JSONDecodeError, trials.TrialError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    sys.exit(main())
