"""Compose bounded descriptive analysis from existing frozen trial records.

This module never executes a trial or makes an adoption decision. Pair selection and
resource scope are caller supplied and remain visibly qualified.
"""
from collections import defaultdict
from pathlib import Path
from . import trials, trial_evidence

FORMAT = 'sandbox-paired-comparison-evidence-1'
REVIEW_FORMAT = 'sandbox-paired-comparison-review-1'
MAX_PAIRS = 32


def _require(ok, message):
    if not ok:
        raise trials.TrialError(message)


def _digest(value):
    return trials.sha256_bytes(trials.canonical(value))


def _review(review, plan):
    keys = {'format', 'author', 'pair_selection_rationale', 'scope_review',
            'uncertainty_review', 'resource_metrics', 'decision_owner_questions'}
    _require(isinstance(review, dict) and set(review) == keys, 'review fields are closed')
    _require(review['format'] == REVIEW_FORMAT, f'review format must be {REVIEW_FORMAT}')
    for key in ('author', 'pair_selection_rationale', 'scope_review', 'uncertainty_review'):
        _require(isinstance(review[key], str) and review[key].strip() and len(review[key]) <= 4000,
                 f'{key} must be non-empty text of at most 4000 characters')
    _require(isinstance(review['decision_owner_questions'], list) and
             all(isinstance(x, str) and x.strip() and len(x) <= 2000 for x in review['decision_owner_questions']) and
             len(review['decision_owner_questions']) <= 32, 'decision_owner_questions must be bounded non-empty text')
    _require(isinstance(review['resource_metrics'], list) and len(review['resource_metrics']) <= 64,
             'resource_metrics must be a bounded list')
    known = set()
    for metric in review['resource_metrics']:
        _require(isinstance(metric, dict) and set(metric) == {'name', 'scope', 'window_reference', 'rationale'},
                 'resource metric fields are closed')
        _require(isinstance(metric['name'], str) and metric['name'] and metric['name'] not in known,
                 'resource metric names must be unique non-empty text')
        known.add(metric['name'])
        _require(metric['scope'] in ('per-run', 'accounting-window', 'unknown'), 'invalid resource scope')
        _require((metric['scope'] == 'accounting-window' and isinstance(metric['window_reference'], str)
                  and metric['window_reference'].strip()) or
                 (metric['scope'] != 'accounting-window' and metric['window_reference'] is None),
                 'window reference must be supplied only for accounting-window metrics')
        _require(isinstance(metric['rationale'], str) and metric['rationale'].strip() and
                 len(metric['rationale']) <= 2000, 'resource rationale is required')
    return review


def _classify(a, b):
    if a == b and a in ('pass', 'fail'):
        return 'tie'
    if a == 'pass' and b == 'fail':
        return 'baseline-better'
    if a == 'fail' and b == 'pass':
        return 'candidate-better'
    return 'unknown-or-not-applicable'


def _metric_row(name, scope, window, a, b, context, pair_comparable):
    if scope == 'unknown': state, delta = 'unknown-scope', None
    elif scope == 'accounting-window': state, delta = 'reported-window-values-only', None
    elif not pair_comparable: state, delta = 'incomparable-pair', None
    elif a is None or b is None or a.get('value') is None or b.get('value') is None:
        state, delta = 'unavailable', None
    elif any(a.get(k) != b.get(k) for k in ('unit', 'denominator', 'semantics', 'kind', 'source')):
        state, delta = 'incomparable', None
    else:
        state, delta = 'paired-per-run-observation', b['value'] - a['value']
    return {'name': name, 'scope': scope, 'window_reference': window,
            'baseline_measurement': a, 'candidate_measurement': b,
            'state': state, 'reported_delta': delta, 'context_sha256': context}


def _build(plan_sha, plan, rows, unpaired, review):
    candidate_arm = rows[0]['candidate']['record']['arm']
    _review(review, plan)
    strata = defaultdict(list)
    pairs = []
    resource = []
    outcomes = defaultdict(lambda: defaultdict(list))
    for row in rows:
        a, b = row['baseline']['record'], row['candidate']['record']
        comparison = trials.compare_pair(plan, a, b)
        context = {'baseline': trials.control_identity(a['identity']),
                   'candidate': trials.control_identity(b['identity']),
                   'declared_differences': comparison['declared_treatment_differences']}
        context_sha = _digest(context)
        context_key = context_sha if not comparison['unidentified_controls'] else 'unknown:' + context_sha
        strata[context_key].append(row['pair_id'])
        criterion_results = comparison['results']
        for cid, result in criterion_results.items():
            outcome = (_classify(result['a'], result['b']) if comparison['outcome_comparable']
                       else 'unknown-or-not-applicable')
            outcomes[(context_key, cid)][outcome].append(row['pair_id'])
        measurement_map = {x['name']: x for x in review['resource_metrics']}
        raw = comparison['measurements']
        for name, scope in measurement_map.items():
            values = raw.get(name, {})
            metric = _metric_row(name, scope['scope'], scope['window_reference'],
                                 values.get('a'), values.get('b'), context_key,
                                 comparison['outcome_comparable'])
            resource.append({'pair_id': row['pair_id'], **metric})
        pairs.append({'pair_id': row['pair_id'], 'baseline': row['baseline'],
                      'candidate': row['candidate'], 'structural_comparison': comparison,
                      'criterion_results': criterion_results, 'context_sha256': context_sha})
    context_rows = [{'context_sha256': key, 'pair_ids': ids,
                     'scope_known': not key.startswith('unknown:')} for key, ids in sorted(strata.items())]
    outcome_rows = []
    for (context, criterion), counts in sorted(outcomes.items()):
        outcome_rows.append({'context_sha256': context, 'criterion_id': criterion,
                             'denominator': len(set(sum((list(v) for v in counts.values()), []))),
                             'pair_ids_by_outcome': {k: v for k, v in sorted(counts.items())}})
    # Totals are only safe for a complete exact-context/measurement metadata group.
    groups = []
    by_group = defaultdict(list)
    for row in resource:
        values = [row.get('baseline_measurement'), row.get('candidate_measurement')]
        meta = tuple(values[0].get(k) if values[0] else None for k in ('unit','denominator','kind','semantics','source'))
        by_group[(row['name'], row['context_sha256'], row['scope'], meta)].append(row)
    for (name, context, scope, meta), group in by_group.items():
        usable = [x for x in group if x['state'] == 'paired-per-run-observation']
        complete = len(usable) == len(group) and bool(group) and scope == 'per-run'
        groups.append({'name': name, 'context_sha256': context, 'scope': scope,
                       'kind': meta[2], 'unit': meta[0], 'denominator': meta[1],
                       'semantics': meta[3], 'source': meta[4],
                       'included_pair_ids': [x['pair_id'] for x in usable],
                       'unavailable_pair_ids': [x['pair_id'] for x in group if x['state']=='unavailable'],
                       'incomparable_pair_ids': [x['pair_id'] for x in group if x['state']=='incomparable'],
                       'complete_total': sum(x['reported_delta'] for x in usable) if complete else None,
                       'range': ([min(x['reported_delta'] for x in usable), max(x['reported_delta'] for x in usable)]
                                 if usable and scope == 'per-run' else None)})
    body = {'format': FORMAT, 'plan': {'sha256': plan_sha, 'record': plan},
            'candidate_arm': candidate_arm, 'selected_pairs': pairs,
            'unpaired_attempts': unpaired, 'context_strata': context_rows,
            'outcome_summary': outcome_rows,
            'resource_accounting': {'pairs': resource, 'groups': groups},
            'review': review,
            'adoption_handoff': {'status': 'prepared-for-decision-owner-review',
              'decision_owner': plan['decision']['owner'], 'intended_consumer': plan['decision']['consumer'],
              'decision_question': plan['decision']['question'],
              'adoption_decision': 'not-recorded-by-Sandbox', 'authority': 'no-authority-granted'},
            'limitations': ['Counts describe only the explicitly selected pairs; a small denominator does not establish general performance.',
              'Pair selection, scope and resource meanings are caller supplied; identity labels are not authenticated.',
              'No significance, causality, generalized quality, cost or adoption claim is inferred.',
              'A cold read validates this packet only and does not regenerate missing source records.']}
    return {**body, 'packet_sha256': _digest(body)}


def create_packet(store, plan_sha256, pairs, review):
    _require(trials._is_sha256(plan_sha256), 'plan SHA-256 must be lowercase hexadecimal')
    plan_path = Path(store) / 'plans' / f'{plan_sha256}.json'
    _require(plan_path.is_file() and plan_path.stat().st_size <= trial_evidence.MAX_PLAN_BYTES,
             'frozen plan is missing or exceeds the plan evidence bound')
    plan = trials.load_record(store, 'plans', plan_sha256)
    trials.validate_plan(plan)
    _require(_digest(plan) == plan_sha256, 'frozen plan hash mismatch')
    _require(isinstance(pairs, list) and 1 <= len(pairs) <= MAX_PAIRS, 'select 1 to 32 explicit pairs')
    attempt_folder = Path(store) / 'attempts'
    paths = sorted(attempt_folder.glob('*.json')) if attempt_folder.is_dir() else []
    _require(len(paths) <= trial_evidence.MAX_ATTEMPT_FILES_SCANNED,
             f'attempt store exceeds {trial_evidence.MAX_ATTEMPT_FILES_SCANNED}-record scan limit')
    attempt_rows, scanned_bytes = [], 0
    for path in paths:
        size = path.stat().st_size
        _require(size <= trial_evidence.MAX_ATTEMPT_BYTES,
                 f'attempt record {path.name} exceeds the size limit')
        scanned_bytes += size
        _require(scanned_bytes <= trial_evidence.MAX_SCAN_BYTES, 'attempt store exceeds total scan byte limit')
        record = trials.load_record(store, 'attempts', path.stem)
        if record.get('plan_sha256') == plan_sha256:
            attempt_rows.append((path.stem, record))
    _require(len(attempt_rows) <= trial_evidence.MAX_ATTEMPTS_PER_PLAN,
             f'plan exceeds {trial_evidence.MAX_ATTEMPTS_PER_PLAN}-attempt evidence bound')
    by_id = {}
    for digest, record in attempt_rows:
        trials.validate_sealed(record, plan)
        _require(_digest(record) == digest, 'sealed attempt digest mismatch')
        _require(record['attempt_id'] not in by_id, 'attempt IDs must be unique within the frozen plan')
        by_id[record['attempt_id']] = {'sha256': digest, 'record': record}
    seen_pairs, seen_attempts, rows = set(), set(), []
    candidate_arm = None
    for item in pairs:
        _require(isinstance(item, dict) and set(item) == {'pair_id','baseline_attempt_id','candidate_attempt_id'},
                 'pair selection fields are closed')
        pid, aid, bid = item['pair_id'], item['baseline_attempt_id'], item['candidate_attempt_id']
        _require(isinstance(pid, str) and pid and len(pid) <= 120 and pid not in seen_pairs, 'pair IDs must be unique bounded text')
        _require(aid != bid and aid not in seen_attempts and bid not in seen_attempts, 'attempts may be selected only once')
        _require(aid in by_id and bid in by_id, 'selected attempt ID must exist in the exact frozen plan')
        a, b = by_id[aid], by_id[bid]
        _require(a['record']['arm'] == plan['treatment']['baseline_arm'], 'baseline attempt must use the plan baseline arm')
        _require(b['record']['arm'] != plan['treatment']['baseline_arm'], 'candidate must use a non-baseline arm')
        _require(candidate_arm is None or candidate_arm == b['record']['arm'], 'all selected pairs must use the same candidate arm')
        candidate_arm = b['record']['arm']
        seen_pairs.add(pid); seen_attempts.update((aid,bid))
        rows.append({'pair_id': pid, 'baseline': a, 'candidate': b})
    unpaired = [{'attempt_id': rec['attempt_id'], 'sha256': digest, 'arm': rec['arm'],
                 'execution_status': rec['execution']['status'], 'record_kind': rec['record_kind']}
                for digest, rec in attempt_rows if rec['attempt_id'] not in seen_attempts]
    packet = _build(plan_sha256, plan, rows, unpaired, review)
    trial_evidence._canonical_within(packet, trial_evidence.MAX_PACKET_BYTES, 'paired comparison packet')
    return packet


def validate_packet(packet):
    _require(isinstance(packet, dict) and packet.get('format') == FORMAT, 'unsupported paired comparison packet')
    expected = {'format', 'plan', 'candidate_arm', 'selected_pairs', 'unpaired_attempts',
                'context_strata', 'outcome_summary', 'resource_accounting', 'review',
                'adoption_handoff', 'limitations', 'packet_sha256'}
    _require(set(packet) == expected, 'paired comparison packet fields are closed')
    trial_evidence._canonical_within(packet, trial_evidence.MAX_PACKET_BYTES, 'paired comparison packet')
    _require(packet.get('packet_sha256') == _digest({k:v for k,v in packet.items() if k!='packet_sha256'}), 'packet digest mismatch')
    planrow = packet.get('plan', {})
    plan = planrow.get('record')
    trials.validate_plan(plan)
    _require(planrow.get('sha256') == _digest(plan), 'plan digest mismatch')
    rows = packet.get('selected_pairs')
    _require(isinstance(rows, list) and 1 <= len(rows) <= MAX_PAIRS, 'invalid selected pair count')
    unpaired = packet.get('unpaired_attempts')
    _require(isinstance(unpaired, list) and len(unpaired) <= trial_evidence.MAX_ATTEMPTS_PER_PLAN,
             'invalid unpaired attempt list')
    unpaired_ids = set()
    for row in unpaired:
        _require(isinstance(row, dict) and set(row) ==
                 {'attempt_id', 'sha256', 'arm', 'execution_status', 'record_kind'},
                 'unpaired attempt fields are closed')
        _require(isinstance(row['attempt_id'], str) and row['attempt_id'] not in unpaired_ids and
                 trials._is_sha256(row['sha256']) and row['arm'] in plan['treatment']['arms'] and
                 row['execution_status'] in trials.EXECUTION_STATUSES and
                 row['record_kind'] in trials.RECORD_KINDS, 'invalid unpaired attempt receipt facts')
        unpaired_ids.add(row['attempt_id'])
    # Recompute all derived evidence solely from embedded exact records.
    normalized = []
    pair_ids, selected_ids = set(), set()
    candidate_arm = None
    for row in rows:
        _require(isinstance(row, dict) and set(row) ==
                 {'pair_id', 'baseline', 'candidate', 'structural_comparison',
                  'criterion_results', 'context_sha256'}, 'selected pair fields are closed')
        for side in ('baseline','candidate'):
            recrow = row.get(side, {}); rec = recrow.get('record')
            trials.validate_sealed(rec, plan)
            _require(recrow.get('sha256') == _digest(rec), f'{side} attempt digest mismatch')
        pid = row.get('pair_id')
        aid = row['baseline']['record']['attempt_id']
        bid = row['candidate']['record']['attempt_id']
        _require(isinstance(pid, str) and pid and pid not in pair_ids, 'pair IDs must be unique')
        _require(aid != bid and aid not in selected_ids and bid not in selected_ids,
                 'each selected attempt may appear only once')
        _require(row['baseline']['record']['arm'] == plan['treatment']['baseline_arm'],
                 'baseline receipt does not use the plan baseline arm')
        arm = row['candidate']['record']['arm']
        _require(arm != plan['treatment']['baseline_arm'] and
                 (candidate_arm is None or candidate_arm == arm),
                 'candidate receipts must use one non-baseline arm')
        pair_ids.add(pid); selected_ids.update((aid, bid)); candidate_arm = arm
        normalized.append({'pair_id': row['pair_id'], 'baseline': row['baseline'], 'candidate': row['candidate']})
    _require(packet.get('candidate_arm') == candidate_arm, 'candidate arm label does not match selected receipts')
    _require(not (selected_ids & unpaired_ids), 'an attempt cannot be both selected and unpaired')
    rebuilt = _build(planrow['sha256'], plan, normalized, packet.get('unpaired_attempts'), packet.get('review'))
    _require(rebuilt == packet, 'paired comparison derived evidence does not match exact receipts')
    return packet


def publication_preflight(packet):
    """Check every derived measurement before publishing an unchanged packet.

    Historical packet validation deliberately keeps its original arithmetic.
    This read-only gate also checks the embedded structural comparison, where
    a delta can exist even when resource accounting withholds it.
    """
    value = validate_packet(packet)
    scopes = {row['name']: row['scope'] for row in value['review']['resource_metrics']}
    fields = ('unit', 'denominator', 'semantics', 'kind', 'source')
    unknown = {'unknown', 'unspecified', 'unavailable', 'not recorded', 'none', 'n/a'}
    issues = []
    for pair in value['selected_pairs']:
        comparison = pair['structural_comparison']
        for name, row in comparison['measurements'].items():
            if 'b_minus_a' not in row:
                continue
            reasons = []
            for side in ('a', 'b'):
                measurement = row[side]
                for field in fields:
                    text = measurement.get(field)
                    if (not isinstance(text, str) or not text.strip()
                            or text.strip().casefold() in unknown):
                        reasons.append(f'{side}.{field} is missing or unknown')
            if any(row['a'].get(field) != row['b'].get(field) for field in fields):
                reasons.append('measurement metadata differs')
            if name in scopes and scopes[name] != 'per-run':
                reasons.append('per-run resource scope is not established')
            if not comparison['outcome_comparable']:
                reasons.append('the selected attempts are not outcome-comparable')
            if reasons:
                issues.append({'pair_id': pair['pair_id'], 'metric': name, 'reasons': reasons})
    return {'format': 'sandbox-paired-publication-preflight-1',
            'packet_sha256': value['packet_sha256'],
            'measurement_publication_ready': not issues, 'issues': issues,
            'qualification': 'Metadata and declared scope only; no authentication, usefulness, causality or adoption established.'}


def _require_publication(packet):
    _require(publication_preflight(packet)['measurement_publication_ready'],
             'Measurement publication preflight failed; inspect with the preflight command. Historical records are unchanged.')


def render_packet(packet, *, publication=False):
    """Return original packet bytes; publication=True first checks measurement meanings."""
    value = validate_packet(packet)
    if publication:
        _require_publication(value)
    return trials.canonical(value).decode('utf-8')


def _display(value):
    """Keep caller-authored labels as text in the optional Markdown view."""
    return (str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('\\', '\\\\').replace('`', '\\`').replace('*', '\\*')
            .replace('_', '\\_').replace('[', '\\[').replace(']', '\\]')
            .replace('\r', ' ').replace('\n', ' '))


def _measurement(value):
    if value is None or value.get('value') is None:
        return 'not recorded'
    return f"{value['value']:.6g} {_display(value['unit'])} ({_display(value['source'])})"


def render_markdown(packet, *, publication=False):
    """Explain a validated packet for review without changing its exact evidence."""
    value = validate_packet(packet)
    if publication:
        _require_publication(value)
    pairs = value['selected_pairs']
    lines = [
        '# Paired comparison evidence', '',
        ('Measurement publication preflight passed; caller meanings and other evidence qualifications remain unverified.'
         if publication else
         'Historical rendering: original arithmetic is retained without measurement publication preflight.'), '',
        f"{len(pairs)} selected pair{'s' if len(pairs) != 1 else ''}; "
        f"{len(value['unpaired_attempts'])} unselected attempt{'s' if len(value['unpaired_attempts']) != 1 else ''}; "
        f"candidate arm: {_display(value['candidate_arm'])}.",
        'Results describe these selected attempts only. They do not establish general performance, causality or adoption.',
        '', '## Observed criteria', ''
    ]
    for pair in pairs:
        lines.append(f"### {_display(pair['pair_id'])}")
        lines.append('')
        for criterion, result in sorted(pair['criterion_results'].items()):
            lines.append(f"- {_display(criterion)}: baseline {_display(result['a'])}; "
                         f"candidate {_display(result['b'])}.")
        lines.append('')
    lines += ['## Counts by recorded context', '']
    for row in value['outcome_summary']:
        counts = ', '.join(f"{_display(outcome.replace('-', ' '))} {len(ids)}"
                           for outcome, ids in sorted(row['pair_ids_by_outcome'].items()))
        lines.append(f"- {_display(row['criterion_id'])}: {counts}; "
                     f"denominator {row['denominator']}; context {row['context_sha256'][:8]}…")
    lines.append('')
    lines += ['## Resource accounting (observed measurements)', '']
    measurements = value['resource_accounting']['pairs']
    if not measurements:
        lines.append('No resource measurements were selected.')
    for row in measurements:
        baseline, candidate = row['baseline_measurement'], row['candidate_measurement']
        lines.append(f"- {_display(row['pair_id'])} · {_display(row['name'])}: "
                     f"baseline {_measurement(baseline)}; candidate {_measurement(candidate)}.")
        if row['reported_delta'] is not None:
            lines.append(f"  - Descriptive candidate minus baseline: {row['reported_delta']:.6g} "
                         f"{_display(candidate['unit'])}.")
        elif row['state'] == 'incomparable':
            fields = ('unit', 'denominator', 'semantics', 'kind', 'source')
            differing = [field for field in fields if baseline.get(field) != candidate.get(field)]
            lines.append('  - No delta: measurement metadata differs' +
                         (f" ({', '.join(differing)})." if differing else '.'))
        else:
            lines.append(f"  - No delta: {_display(row['state'].replace('-', ' '))}.")
    review = value['review']
    lines += ['', '## Caller review', '',
              f"Recorded by: {_display(review['author'])}. Reviewer identity is caller supplied.", '',
              f"Selection: {_display(review['pair_selection_rationale'])}", '',
              f"Scope: {_display(review['scope_review'])}", '',
              f"Uncertainty: {_display(review['uncertainty_review'])}", '']
    if review['decision_owner_questions']:
        lines += ['Questions for the decision owner:', '']
        lines.extend(f"- {_display(question)}" for question in review['decision_owner_questions'])
        lines.append('')
    lines += [
              '## Decision-owner handoff', '',
              f"Owner: {_display(value['adoption_handoff']['decision_owner'])}; "
              f"status: {_display(value['adoption_handoff']['status'])}.",
              'Decision remains unrecorded; authority is not granted.', '',
              '## Exact references', '',
              f"- Frozen plan SHA-256: {value['plan']['sha256']}",
              f"- Packet SHA-256: {value['packet_sha256']}"]
    for pair in pairs:
        lines.append(f"- {_display(pair['pair_id'])}: baseline receipt {pair['baseline']['sha256']}; "
                     f"candidate receipt {pair['candidate']['sha256']}; "
                     f"context SHA-256 {pair['context_sha256']}.")
    lines += ['', '## Limits', '']
    lines.extend(f"- {_display(item)}" for item in value['limitations'])
    return '\n'.join(lines) + '\n'


def main(argv=None):
    import argparse, json, sys
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create')
    create.add_argument('--store', required=True, type=Path)
    create.add_argument('--plan-sha256', required=True)
    create.add_argument('--selection', required=True, type=Path)
    create.add_argument('--review', required=True, type=Path)
    create.add_argument('--output', required=True, type=Path)
    render = sub.add_parser('render')
    render.add_argument('--input', required=True, type=Path)
    render.add_argument('--format', choices=('json','markdown'), default='markdown')
    render.add_argument('--publication', action='store_true',
                        help='Refuse all output when any derived measurement lacks comparable explicit meanings and per-run scope')
    preflight = sub.add_parser('preflight')
    preflight.add_argument('--input', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'create':
            selection = json.loads(args.selection.read_bytes())
            review = json.loads(args.review.read_bytes())
            packet = create_packet(args.store, args.plan_sha256, selection, review)
            data = trials.canonical(packet).decode('utf-8')
            with open(args.output, 'x', encoding='utf-8') as handle:
                handle.write(data)
            return 0
        packet = validate_packet(json.loads(args.input.read_bytes()))
        if args.command == 'preflight':
            report = publication_preflight(packet)
            sys.stdout.write(trials.canonical(report).decode())
            return 0 if report['measurement_publication_ready'] else 1
        if not args.publication:
            print('Historical rendering: measurement publication preflight was not applied. Use --publication for a report to share.',
                  file=sys.stderr)
        if args.format == 'json':
            sys.stdout.write(render_packet(packet, publication=args.publication))
        else:
            sys.stdout.write(render_markdown(packet, publication=args.publication))
    except (OSError, ValueError, TypeError, KeyError, trials.TrialError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
