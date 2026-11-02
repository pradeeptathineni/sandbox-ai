"""Portable paired evidence over frozen trial attempts and frozen retrieval lists.

This module composes the existing sealed-trial and retrieval evaluators. It does
not execute either arm, generate rankings, judge relevance or adopt a result.
"""
import argparse
import copy
import json
from pathlib import Path

from . import retrieval, trials

FORMAT = 'sandbox-retrieval-experiment-packet-1'
GROUNDING_INPUT_FORMAT = 'sandbox-retrieval-grounding-judgments-1'
GROUNDING_REPORT_FORMAT = 'sandbox-retrieval-grounding-report-1'
SHA256_CHARS = frozenset('0123456789abcdef')
ADOPTION_NOTE = 'Evidence packet only. It does not issue a recommendation, change a plan, or authorize adoption.'
LIMITS = [
    'No experiment, retriever, model, or provider was run while creating, validating, or rendering this packet.',
    'Ground-truth labels and rankings are supplied inputs; the evaluator preserves their stated completeness but does not verify relevance.',
    'Source IDs establish corpus membership only, not citation entailment, answer correctness, source independence, or general performance.',
    'A matching sealed receipt hash is a recorded reference, not proof the original sealer checked the referenced bytes.',
    'This packet is advisory evidence only; adoption remains with the authorized decision owner.',
]


def _require(ok, message):
    if not ok:
        raise retrieval.RetrievalError(message)


def _canonical_hash(value):
    return trials.sha256_bytes(trials.canonical(value))


def _attempt_by_id(store, plan_sha256, plan, attempt_id):
    matches = [(digest, record) for digest, record in trials.attempts_for(store, plan_sha256)
               if record.get('attempt_id') == attempt_id]
    _require(len(matches) == 1, f'attempt id {attempt_id!r} must identify exactly one sealed attempt under this plan')
    digest, record = matches[0]
    trials.validate_sealed(record, plan)
    return digest, record


def _receipt_binding(attempt, arm):
    ranking_sha256 = retrieval.digest(arm['rankings'])
    references = sorted(receipt['reference'] for receipt in attempt['receipts']
                        if receipt['sha256'] == ranking_sha256)
    return {
        'ranking_sha256': ranking_sha256,
        'status': 'matching-receipt-reference' if references else 'unbound',
        'receipt_references': references,
        'qualification': ('A sealed attempt records this hash for the exact canonical ranking map; original receipt bytes are not rechecked here.'
                          if references else 'No sealed receipt declares the exact canonical ranking-map hash.'),
    }


def _grounding_report(comparison_input, judgments, frozen_case_ids):
    _require(isinstance(judgments, dict) and judgments.get('format') == GROUNDING_INPUT_FORMAT,
             f'grounding judgments format must be {GROUNDING_INPUT_FORMAT}')
    _require(set(judgments) == {'format', 'scope', 'evaluator', 'observations'},
             'grounding judgment fields are closed')
    scope = judgments['scope']
    _require(isinstance(scope, dict) and set(scope) == {'rubric_id', 'rubric_revision', 'rubric_sha256'},
             'grounding scope fields are closed')
    _require(all(isinstance(scope[key], str) and scope[key].strip() for key in ('rubric_id', 'rubric_revision')),
             'grounding rubric identity is required')
    _require(isinstance(scope['rubric_sha256'], str) and len(scope['rubric_sha256']) == 64 and
             all(char in SHA256_CHARS for char in scope['rubric_sha256']),
             'grounding rubric SHA-256 must be lowercase hexadecimal')
    evaluator = judgments['evaluator']
    _require(isinstance(evaluator, dict) and set(evaluator) == {'id', 'method', 'provenance', 'uncertainty'},
             'grounding evaluator fields are closed')
    _require(all(isinstance(evaluator[key], str) and evaluator[key].strip() and len(evaluator[key]) <= 2000
                 for key in ('id', 'method', 'provenance', 'uncertainty')),
             'grounding evaluator identity, method, provenance and uncertainty are required caller assertions')
    observations = judgments['observations']
    _require(isinstance(observations, list), 'grounding observations must be a list')
    questions = comparison_input['questions']
    strategies = [arm['strategy'] for arm in comparison_input['arms']]
    question_ids = [question['id'] for question in questions]
    frozen_question_alignment = 'matched' if set(frozen_case_ids) == set(question_ids) else 'mismatched'
    expected = {(strategy, question['id']) for strategy in strategies for question in questions}
    observed = set()
    source_ids = set(comparison_input['corpus']['source_ids'])
    per_arm = {strategy: [] for strategy in strategies}
    per_question = {question['id']: {} for question in questions}
    for row in observations:
        _require(isinstance(row, dict) and set(row) == {
            'strategy', 'question_id', 'answer_id', 'answer_sha256', 'answer_status',
            'evidence_expectation', 'citation_checks'},
            'grounding observation fields are closed')
        _require(isinstance(row['strategy'], str) and isinstance(row['question_id'], str),
                 'grounding strategy and question IDs must be exact strings')
        key = (row['strategy'], row['question_id'])
        _require(key in expected, 'grounding observation must identify an exact frozen strategy and question')
        _require(key not in observed, 'grounding observations contain a duplicate strategy/question pair')
        observed.add(key)
        _require(row['answer_status'] in ('answered', 'abstained', 'missing', 'unknown'),
                 f'{row["question_id"]}/{row["strategy"]} has an unsupported answer status')
        _require(row['evidence_expectation'] in ('positive', 'negative', 'unavailable', 'unknown'),
                 f'{row["question_id"]}/{row["strategy"]} has an unsupported evidence expectation')
        answer_id, answer_sha = row['answer_id'], row['answer_sha256']
        _require(answer_id is None or (isinstance(answer_id, str) and answer_id.strip()),
                 'answer_id must be exact non-empty text or null')
        _require(answer_sha is None or (isinstance(answer_sha, str) and len(answer_sha) == 64 and
                                       all(char in SHA256_CHARS for char in answer_sha)),
                 'answer_sha256 must be lowercase hexadecimal or null')
        if row['answer_status'] == 'answered':
            _require(answer_id is not None and answer_sha is not None,
                     'answered observations require exact answer ID and content digest')
        else:
            _require(answer_id is None and answer_sha is None,
                     'abstained, missing and unknown answers must not carry an asserted answer identity')
        checks = row['citation_checks']
        _require(isinstance(checks, list), 'citation_checks must be a list')
        seen_citations = set()
        normalized_checks = []
        for citation in checks:
            _require(isinstance(citation, dict) and set(citation) == {
                'citation_id', 'source_id', 'occurrence', 'support',
                'support_evidence_source_ids', 'uncertainty'},
                'citation check fields are closed')
            citation_id = citation['citation_id']
            _require(isinstance(citation_id, str) and citation_id.strip() and citation_id not in seen_citations,
                     'citation IDs must be exact and unique within an answer')
            seen_citations.add(citation_id)
            source_id = citation['source_id']
            _require(source_id is None or (isinstance(source_id, str) and source_id.strip()),
                     'citation source_id must be exact text or null')
            _require(citation['occurrence'] in ('present', 'missing', 'unknown'),
                     'citation occurrence must be present, missing or unknown')
            _require(citation['support'] in ('supported', 'unsupported', 'contradicted', 'not-assessed', 'unknown'),
                     'citation support must preserve an explicit judgment state')
            _require(source_id is not None or citation['occurrence'] == 'unknown',
                     'a citation without a source ID must remain occurrence-unknown')
            _require(citation['occurrence'] != 'missing' or citation['support'] == 'not-assessed',
                     'a missing citation occurrence cannot claim a support judgment')
            if citation['occurrence'] == 'unknown':
                _require(citation['support'] in ('unknown', 'not-assessed'),
                         'unknown citation occurrence cannot claim a support judgment')
            evidence_ids = citation['support_evidence_source_ids']
            _require(isinstance(evidence_ids, list) and all(isinstance(item, str) and item for item in evidence_ids),
                     'support evidence IDs must be exact strings')
            _require(len(evidence_ids) == len(set(evidence_ids)), 'support evidence IDs contain duplicates')
            _require(isinstance(citation['uncertainty'], str) and len(citation['uncertainty']) <= 2000,
                     'citation uncertainty must be text of at most 2000 characters')
            foreign = [item for item in ([source_id] if source_id is not None else []) + evidence_ids
                       if item not in source_ids]
            normalized_checks.append({**citation, 'foreign_source_ids': foreign})
        _require(row['answer_status'] == 'answered' or
                 all(check['occurrence'] != 'present' for check in normalized_checks),
                 'a citation cannot be recorded present when its answer is absent or unknown')
        normalized = {**row, 'citation_checks': normalized_checks}
        per_arm[row['strategy']].append(normalized)
        per_question[row['question_id']][row['strategy']] = normalized
    _require(observed == expected,
             f'grounding judgments must cover every frozen strategy/question pair; missing={sorted(expected-observed)}')

    answer_states = ('answered', 'abstained', 'missing', 'unknown')
    expectation_states = ('positive', 'negative', 'unavailable', 'unknown')
    occurrence_states = ('present', 'missing', 'unknown')
    support_states = ('supported', 'unsupported', 'contradicted', 'not-assessed', 'unknown')
    arm_summaries = []
    for strategy in strategies:
        rows = per_arm[strategy]
        checks = [check for row in rows for check in row['citation_checks']]
        arm_summaries.append({
            'strategy': strategy,
            'question_count': len(rows),
            'answer_status_counts': {state: sum(row['answer_status'] == state for row in rows)
                                     for state in answer_states},
            'answer_status_denominator': len(rows),
            'evidence_expectation_counts': {state: sum(row['evidence_expectation'] == state for row in rows)
                                            for state in expectation_states},
            'evidence_expectation_denominator': len(rows),
            'citation_occurrence_counts': {state: sum(check['occurrence'] == state for check in checks)
                                           for state in occurrence_states},
            'citation_occurrence_denominator': len(checks),
            'citation_support_counts': {state: sum(check['support'] == state for check in checks)
                                        for state in support_states},
            'citation_support_denominator': len(checks),
            'foreign_source_ids': sorted({source_id for check in checks for source_id in check['foreign_source_ids']}),
        })
    judgments_sha256 = _canonical_hash(judgments)
    return {
        'format': GROUNDING_REPORT_FORMAT,
        'input_sha256': judgments_sha256,
        'rubric_scope': {**scope,
                         'question_ids': [question['id'] for question in questions],
                         'questions_sha256': _canonical_hash(questions)},
        'evaluator': {**evaluator, 'identity_status': 'caller-asserted-unverified'},
        'comparison_scope': {
            'status': 'descriptively-comparable' if frozen_question_alignment == 'matched' else 'not-comparable',
            'judgments_sha256': judgments_sha256,
            'strategies': strategies,
            'question_ids': question_ids,
            'frozen_case_ids': sorted(frozen_case_ids),
            'frozen_question_alignment': frozen_question_alignment,
            'rubric_sha256': scope['rubric_sha256'],
            'method': evaluator['method'],
            'full_arm_question_coverage': True,
            'qualification': 'Descriptive comparability requires exact frozen question alignment, shared rubric/method and full arm/question coverage; per-arm denominators remain separate and no quality delta or conclusion is inferred.',
        },
        'per_arm': arm_summaries,
        'per_question': per_question,
        'limitations': [
            'Citation occurrence, support, expected evidence, answer status, evaluator identity and rationale are caller-supplied observations, not independently verified source truth.',
            'Occurrence and support are reported separately; supported/unsupported/contradicted labels do not establish entailment or answer correctness.',
            'Counts retain unknown, not-assessed, missing, negative and foreign-source cases with explicit denominators; no combined quality score or efficacy conclusion is calculated.',
        ],
    }


def _build_packet(plan_sha256, plan, baseline, candidate, comparison_input, grounding_judgments=None):
    retrieval.validate(comparison_input)
    baseline_arm = plan['treatment']['baseline_arm']
    candidate_arm = candidate['record']['arm']
    arms = comparison_input['arms']
    _require(len(arms) == 2, 'paired retrieval packet requires exactly two retrieval arms')
    _require(baseline['record']['arm'] == baseline_arm,
             'selected baseline attempt must belong to the frozen plan baseline arm')
    _require(candidate_arm != baseline_arm,
             'selected candidate attempt must belong to a non-baseline arm')
    _require({arm['strategy'] for arm in arms} == {baseline_arm, candidate_arm},
             'retrieval arm strategy names must exactly match the selected frozen attempt arms')
    by_strategy = {arm['strategy']: arm for arm in arms}
    ordered_arms = [by_strategy[baseline_arm], by_strategy[candidate_arm]]
    # Preserve caller input order everywhere else; the selected pair is always baseline then candidate.
    normalized_input = copy.deepcopy(comparison_input)
    normalized_input['arms'] = copy.deepcopy(ordered_arms)
    retrieval.validate(normalized_input)

    baseline_binding = _receipt_binding(baseline['record'], by_strategy[baseline_arm])
    candidate_binding = _receipt_binding(candidate['record'], by_strategy[candidate_arm])
    pair = trials.compare_pair(plan, baseline['record'], candidate['record'])
    report = retrieval.evaluate(normalized_input)
    retrieval_pair = next((entry for entry in report['pairwise']
                           if entry['a'] == baseline_arm and entry['b'] == candidate_arm), None)
    _require(retrieval_pair is not None, 'retrieval evaluator did not return the selected pair')

    frozen_case_ids = sorted(plan['cases'])
    observed_question_ids = [question['id'] for question in normalized_input['questions']]
    case_alignment = {
        'status': 'matched' if set(frozen_case_ids) == set(observed_question_ids) else 'mismatched',
        'frozen_case_ids': frozen_case_ids,
        'observed_question_ids': observed_question_ids,
        'missing_case_ids': [case_id for case_id in frozen_case_ids if case_id not in observed_question_ids],
        'unexpected_question_ids': [qid for qid in observed_question_ids if qid not in set(frozen_case_ids)],
        'qualification': 'Case ID alignment only; case file bytes are not re-read or compared with question text here.',
    }
    selected_reports = {arm['strategy']: next(item for item in report['arms'] if item['strategy'] == arm['strategy'])
                        for arm in ordered_arms}
    arm_identity_aligned = all(not item['identity_mismatches'] for item in selected_reports.values())
    source_receipts_bound = baseline_binding['status'] == candidate_binding['status'] == 'matching-receipt-reference'
    reasons = []
    if not pair['outcome_comparable']:
        reasons.append('selected sealed attempts are not structurally outcome-comparable')
    if case_alignment['status'] != 'matched':
        reasons.append('retrieval question IDs do not match the frozen plan case IDs')
    if not arm_identity_aligned:
        reasons.append('retrieval arm corpus or question identity has a recorded mismatch')
    if not source_receipts_bound:
        reasons.append('one or both exact ranking maps lack a matching hash in the selected sealed attempt receipts')
    missing_rankings = {
        arm_name: selected_reports[arm_name]['missing_question_ids'] for arm_name in (baseline_arm, candidate_arm)
    }
    if any(missing_rankings.values()):
        reasons.append('one or both arms omit rankings for declared questions')
    paired_status = 'comparable' if not reasons else 'not-comparable'
    packet_body = {
        'format': FORMAT,
        'plan': {'sha256': plan_sha256, 'record': plan},
        'selected_attempts': {
            'baseline': {'sha256': baseline['sha256'], 'record': baseline['record'],
                         'ranking_receipt_binding': baseline_binding},
            'candidate': {'sha256': candidate['sha256'], 'record': candidate['record'],
                          'ranking_receipt_binding': candidate_binding},
        },
        'comparison_input': normalized_input,
        'retrieval_report': report,
        'trial_pair': pair,
        'case_alignment': case_alignment,
        'paired_outcome': {
            'status': paired_status,
            'trial_outcome_comparable': pair['outcome_comparable'],
            'retrieval_identity_comparable': retrieval_pair['identity_comparable'],
            'ranking_receipt_binding_complete': source_receipts_bound,
            'missing_rankings_by_arm': missing_rankings,
            'reasons': reasons,
            'qualification': 'Per-case rankings and supplied judgments remain inspectable even when the pair is not outcome-comparable.',
        },
        'adoption_evidence': {'status': 'evidence-only', 'adoption': 'not-recorded-by-this-packet',
                              'authority': None, 'record': None, 'note': ADOPTION_NOTE},
        'limitations': list(LIMITS),
    }
    if grounding_judgments is not None:
        packet_body['grounding_evaluation'] = {
            'judgments': grounding_judgments,
            'report': _grounding_report(normalized_input, grounding_judgments, frozen_case_ids),
        }
    return {**packet_body, 'packet_sha256': _canonical_hash(packet_body)}


def create_packet(store, plan_sha256, baseline_attempt_id, candidate_attempt_id, comparison_input,
                  grounding_judgments=None):
    """Builds evidence from exact stored attempts and frozen candidate-list input."""
    _require(isinstance(plan_sha256, str) and len(plan_sha256) == 64
             and all(char in SHA256_CHARS for char in plan_sha256), 'plan SHA-256 must be lowercase hexadecimal')
    plan = trials.load_record(store, 'plans', plan_sha256)
    trials.validate_plan(plan)
    _require(_canonical_hash(plan) == plan_sha256, 'frozen plan hash mismatch')
    baseline_sha, baseline_record = _attempt_by_id(store, plan_sha256, plan, baseline_attempt_id)
    candidate_sha, candidate_record = _attempt_by_id(store, plan_sha256, plan, candidate_attempt_id)
    _require(baseline_attempt_id != candidate_attempt_id, 'baseline and candidate attempt IDs must differ')
    return _build_packet(plan_sha256, plan,
                         {'sha256': baseline_sha, 'record': baseline_record},
                         {'sha256': candidate_sha, 'record': candidate_record},
                         comparison_input, grounding_judgments)


def validate_packet(packet):
    """Cold-validates the exact portable evidence closure without executing either arm."""
    expected = {'format', 'plan', 'selected_attempts', 'comparison_input', 'retrieval_report', 'trial_pair',
                'case_alignment', 'paired_outcome', 'adoption_evidence', 'limitations', 'packet_sha256'}
    _require(isinstance(packet, dict) and set(packet) in (expected, expected | {'grounding_evaluation'}),
             'packet fields are closed')
    _require(packet['format'] == FORMAT, f'format must be {FORMAT}')
    plan_row = packet['plan']
    _require(isinstance(plan_row, dict) and set(plan_row) == {'sha256', 'record'}, 'invalid frozen plan closure')
    plan = plan_row['record']
    trials.validate_plan(plan)
    _require(plan_row['sha256'] == _canonical_hash(plan), 'frozen plan SHA-256 mismatch')
    selected = packet['selected_attempts']
    _require(isinstance(selected, dict) and set(selected) == {'baseline', 'candidate'}, 'exactly one selected pair is required')
    for name, row in selected.items():
        _require(isinstance(row, dict) and set(row) == {'sha256', 'record', 'ranking_receipt_binding'},
                 f'invalid {name} attempt closure')
        trials.validate_sealed(row['record'], plan)
        _require(row['sha256'] == _canonical_hash(row['record']), f'{name} sealed attempt hash mismatch')
    baseline, candidate = selected['baseline'], selected['candidate']
    _require(baseline['record']['attempt_id'] != candidate['record']['attempt_id'], 'selected attempt IDs must differ')
    grounding_judgments = None
    if 'grounding_evaluation' in packet:
        evaluation = packet['grounding_evaluation']
        _require(isinstance(evaluation, dict) and set(evaluation) == {'judgments', 'report'},
                 'grounding evaluation fields are closed')
        grounding_judgments = evaluation['judgments']
    rebuilt = _build_packet(plan_row['sha256'], plan,
                            {'sha256': baseline['sha256'], 'record': baseline['record']},
                            {'sha256': candidate['sha256'], 'record': candidate['record']},
                            packet['comparison_input'], grounding_judgments)
    _require({key: value for key, value in rebuilt.items() if key != 'packet_sha256'}
             == {key: value for key, value in packet.items() if key != 'packet_sha256'},
             'packet evidence does not match its retained inputs')
    _require(packet['packet_sha256'] == _canonical_hash({key: value for key, value in packet.items()
                                                          if key != 'packet_sha256'}),
             'packet digest mismatch')
    return packet


def grounding_assessment(packet):
    """Read optional caller-supplied grounding judgments without changing legacy packet bytes."""
    value = validate_packet(packet)
    evaluation = value.get('grounding_evaluation')
    if evaluation is None:
        return {'status': 'not_assessed',
                'qualification': 'This legacy retrieval packet contains rankings and relevance labels only; citation occurrence and support were not assessed.'}
    return {'status': 'assessed', 'input_sha256': evaluation['report']['input_sha256'],
            'report': evaluation['report']}


def render_packet(packet, format='json'):
    """Exports the cold-open packet as canonical JSON or a per-case Markdown brief."""
    value = validate_packet(packet)
    if format == 'json':
        return trials.canonical(value).decode('utf-8')
    if format != 'markdown':
        raise retrieval.RetrievalError('format must be json or markdown')
    base = value['selected_attempts']['baseline']['record']
    candidate = value['selected_attempts']['candidate']['record']
    lines = [f'# Retrieval experiment evidence: {value["plan"]["record"]["plan_id"]}', '',
             f'Packet SHA-256: `{value["packet_sha256"]}`.',
             f'Plan SHA-256: `{value["plan"]["sha256"]}`.',
             f'Attempt pair: `{base["attempt_id"]}` ({base["execution"]["status"]}) vs '
             f'`{candidate["attempt_id"]}` ({candidate["execution"]["status"]}); '
             f'{value["trial_pair"]["verdict"]}; paired outcome {value["paired_outcome"]["status"]}.',
             f'Case alignment: {value["case_alignment"]["status"]}.', '',
             '| Question | Ground truth | Baseline ranked IDs | Candidate ranked IDs |',
             '| --- | --- | --- | --- |']
    for question in value['comparison_input']['questions']:
        qid = question['id']
        left = value['retrieval_report']['arms'][0]['per_question'].get(qid)
        right = value['retrieval_report']['arms'][1]['per_question'].get(qid)
        left_ids = value['comparison_input']['arms'][0]['rankings'].get(qid)
        right_ids = value['comparison_input']['arms'][1]['rankings'].get(qid)
        status = question['ground_truth']['status']
        lines.append(f'| {qid} | {status} | {json.dumps(left_ids) if left_ids is not None else "missing"} '
                     f'| {json.dumps(right_ids) if right_ids is not None else "missing"} |')
        if left or right:
            lines.append('')
            for side, row in ((value['comparison_input']['arms'][0]['strategy'], left),
                              (value['comparison_input']['arms'][1]['strategy'], right)):
                if row is None:
                    lines.append(f'- {qid} / {side}: ranking missing; metrics unknown.')
                    continue
                metric_text = ', '.join(f'@{k}: recall={metric["recall"]}, precision={metric["precision"]}, '
                                         f'hit_rate={metric["hit_rate"]}'
                                         for k, metric in row['metrics'].items())
                lines.append(f'- {qid} / {side}: relevant ranked IDs `{row["relevant_ranked_ids"]}`; {metric_text}; '
                             f'unjudged `{row["unjudged_candidate_ids"]}`.')
            lines.append('')
    lines += ['', '## Pair and decision limits', '',
              f'Trial controls verdict: {value["trial_pair"]["verdict"]}; outcome comparable: '
              f'{str(value["trial_pair"]["outcome_comparable"]).lower()}.',
              f'Paired status: {value["paired_outcome"]["status"]}; reasons: '
              f'{"; ".join(value["paired_outcome"]["reasons"]) or "none recorded"}.',
              f'Adoption evidence status: {value["adoption_evidence"]["status"]}.',
              f'Adoption: {value["adoption_evidence"]["adoption"]}. {ADOPTION_NOTE}', '']
    assessment = value.get('grounding_evaluation')
    lines += ['## Citation and answer grounding', '']
    if assessment is None:
        lines.append('Not assessed. This packet records retrieval rankings and relevance labels only; citation occurrence and support judgments were not supplied.')
    else:
        report = assessment['report']
        lines.append(f'Rubric `{report["rubric_scope"]["rubric_sha256"]}`; evaluator `{report["evaluator"]["id"]}` by `{report["evaluator"]["method"]}` (caller asserted, unverified).')
        lines.append(f'Scope: {report["comparison_scope"]["status"]}; full question coverage: {report["comparison_scope"]["full_arm_question_coverage"]}.')
        for arm in report['per_arm']:
            lines.append(f'- {arm["strategy"]}: answer states {arm["answer_status_counts"]} / {arm["answer_status_denominator"]}; '
                         f'citation occurrence {arm["citation_occurrence_counts"]} / {arm["citation_occurrence_denominator"]}; '
                         f'support {arm["citation_support_counts"]} / {arm["citation_support_denominator"]}; '
                         f'foreign source IDs {arm["foreign_source_ids"]}.')
        for qid, judgments_by_arm in report['per_question'].items():
            for strategy, row in judgments_by_arm.items():
                lines.append(f'- {qid} / {strategy}: answer `{row["answer_id"] or "none"}` is {row["answer_status"]}; '
                             f'evidence expectation {row["evidence_expectation"]}.')
                for citation in row['citation_checks']:
                    lines.append(f'  - citation `{citation["citation_id"]}` / source `{citation["source_id"] or "unknown"}`: '
                                 f'occurrence {citation["occurrence"]}; support {citation["support"]}; '
                                 f'out-of-corpus IDs {citation["foreign_source_ids"] or "none"}.')
        lines.append('Occurrence and support are separate caller judgments. These counts do not infer entailment, answer correctness or efficacy.')
    lines += ['', '## Limitations', '']
    lines.extend(f'- {limit}' for limit in value['limitations'])
    return '\n'.join(lines) + '\n'


def _read(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def _write_new(path, data):
    with open(path, 'xb') as handle:
        handle.write(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    compare = sub.add_parser('compare', help='assemble a portable packet from stored paired attempts and candidate lists')
    compare.add_argument('--store', type=Path, required=True)
    compare.add_argument('--plan-sha256', required=True)
    compare.add_argument('--baseline-attempt', required=True)
    compare.add_argument('--candidate-attempt', required=True)
    compare.add_argument('--input', type=Path, required=True, help='explicit sandbox-retrieval-comparison-1 JSON')
    compare.add_argument('--grounding-judgments', type=Path,
                         help=f'optional {GROUNDING_INPUT_FORMAT} caller-supplied judgments')
    compare.add_argument('--output', type=Path)
    render = sub.add_parser('render', help='cold-validate and export a saved packet without running experiments')
    render.add_argument('--input', type=Path, required=True, help='saved sandbox-retrieval-experiment-packet-1 JSON')
    render.add_argument('--format', choices=('json', 'markdown'), default='markdown')
    render.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'compare':
            packet = create_packet(args.store, args.plan_sha256, args.baseline_attempt,
                                   args.candidate_attempt, _read(args.input),
                                   _read(args.grounding_judgments) if args.grounding_judgments else None)
            text = render_packet(packet, 'json')
        else:
            packet = validate_packet(_read(args.input))
            text = render_packet(packet, args.format)
        if args.output:
            _write_new(args.output, text.encode('utf-8'))
        else:
            print(text, end='')
    except (OSError, json.JSONDecodeError, retrieval.RetrievalError, trials.TrialError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
