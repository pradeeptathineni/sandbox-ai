"""Deterministic comparison of frozen retrieval candidate lists.

This module evaluates supplied rankings only. It does not retrieve, embed, call a
model, or infer relevance. Inputs and outputs are portable JSON using stdlib only.
"""
import argparse
import hashlib
import json
from pathlib import Path

FORMAT = 'sandbox-retrieval-comparison-1'


class RetrievalError(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _require(condition, message):
    if not condition:
        raise RetrievalError(message)


def validate(data):
    _require(isinstance(data, dict), 'input must be a JSON object')
    _require(data.get('format') == FORMAT, f'format must be {FORMAT}')
    _require(isinstance(data.get('corpus'), dict), 'corpus must be an object')
    _require(isinstance(data.get('questions'), list) and data['questions'],
             'questions must be a non-empty list')
    _require(isinstance(data.get('arms'), list) and len(data['arms']) >= 2,
             'at least two strategy arms are required')
    corpus = data['corpus']
    _require(isinstance(corpus.get('id'), str) and corpus['id'], 'corpus.id required')
    sources = corpus.get('source_ids')
    _require(isinstance(sources, list) and sources and all(isinstance(x, str) and x for x in sources),
             'corpus.source_ids must be a non-empty list of exact IDs')
    _require(len(sources) == len(set(sources)), 'corpus.source_ids contain duplicates')
    qids = [q.get('id') for q in data['questions'] if isinstance(q, dict)]
    _require(len(qids) == len(data['questions']) and all(isinstance(x, str) and x for x in qids),
             'each question needs an exact id')
    _require(len(qids) == len(set(qids)), 'question IDs contain duplicates')
    for q in data['questions']:
        _require(isinstance(q.get('text'), str) and q['text'], f"question {q['id']} needs text")
        gt = q.get('ground_truth')
        _require(isinstance(gt, dict) and gt.get('status') in ('complete', 'partial', 'unknown'),
                 f"question {q['id']} ground_truth.status must be complete, partial or unknown")
        relevant = gt.get('relevant_source_ids', [])
        _require(isinstance(relevant, list) and all(x in sources for x in relevant),
                 f"question {q['id']} relevance IDs must belong to this corpus")
        _require(len(relevant) == len(set(relevant)), f"question {q['id']} has duplicate relevance IDs")
        if gt['status'] == 'unknown':
            _require(not relevant, f"question {q['id']} unknown ground truth must not assert relevance IDs")
    names = [a.get('strategy') for a in data['arms'] if isinstance(a, dict)]
    _require(len(names) == len(data['arms']) and all(isinstance(n, str) and n for n in names),
             'each arm needs a strategy name')
    _require(len(names) == len(set(names)), 'strategy names must be unique')
    for arm in data['arms']:
        _require(isinstance(arm.get('provenance'), dict), f"arm {arm['strategy']} provenance required")
        for field in ('producer', 'version', 'reference'):
            _require(isinstance(arm['provenance'].get(field), str) and arm['provenance'][field],
                     f"arm {arm['strategy']} provenance.{field} required")
        _require(isinstance(arm.get('corpus_id'), str) and arm['corpus_id'],
                 f"arm {arm['strategy']} corpus_id required")
        arm_qids = arm.get('question_ids')
        _require(isinstance(arm_qids, list) and all(isinstance(x, str) and x for x in arm_qids),
                 f"arm {arm['strategy']} question_ids must be an explicit list")
        _require(len(arm_qids) == len(set(arm_qids)), f"arm {arm['strategy']} question_ids contain duplicates")
        rankings = arm.get('rankings')
        _require(isinstance(rankings, dict), f"arm {arm['strategy']} rankings must be an object")
        _require(set(rankings) <= set(arm_qids), f"arm {arm['strategy']} rankings contain undeclared question IDs")
        for qid, ids in rankings.items():
            _require(isinstance(ids, list) and all(isinstance(x, str) for x in ids),
                     f'{arm["strategy"]}/{qid} candidate list must contain source IDs')
            _require(len(ids) == len(set(ids)), f'{arm["strategy"]}/{qid} contains duplicate candidates')
    _require(isinstance(data.get('cutoffs'), list) and data['cutoffs'] and
             all(isinstance(k, int) and k > 0 for k in data['cutoffs']),
             'cutoffs must be positive integers')
    _require(len(data['cutoffs']) == len(set(data['cutoffs'])), 'cutoffs must be unique')
    return data


def _question_metrics(q, ranking, cutoffs):
    gt = q['ground_truth']
    judged = set(gt['relevant_source_ids'])
    status = gt['status']
    result = {
        'ground_truth_status': status,
        'candidate_count': len(ranking),
        'unjudged_candidate_ids': [x for x in ranking if x not in judged] if status != 'complete' else [],
        'relevant_ranked_ids': [x for x in ranking if x in judged],
        'first_relevant_rank': next((i + 1 for i, x in enumerate(ranking) if x in judged), None),
        'metrics': {},
    }
    for k in cutoffs:
        top = ranking[:k]
        hits = sum(x in judged for x in top)
        # Recall and exact precision need complete relevance judgments.
        recall = hits / len(judged) if status == 'complete' and judged else None
        result['metrics'][str(k)] = {
            'hits': hits,
            'hit_rate': (1.0 if hits else 0.0) if status == 'complete' else None,
            'hit_rate_denominator': 1 if status == 'complete' else 0,
            'precision': hits / min(k, len(ranking)) if status == 'complete' and ranking else None,
            'precision_denominator': min(k, len(ranking)) if status == 'complete' else 0,
            'known_positive_fraction_lower_bound': hits / min(k, len(ranking)) if status == 'partial' and ranking else None,
            'known_positive_fraction_lower_bound_denominator': min(k, len(ranking)) if status == 'partial' else 0,
            'recall': recall,
            'recall_denominator_relevant': len(judged) if status == 'complete' else None,
            'recall_unknown_reason': None if recall is not None else
                ('ground truth is unknown' if status == 'unknown' else
                 'no known relevant sources' if status == 'complete' else 'ground truth is partial'),
        }
    return result


def evaluate(data):
    validate(data)
    questions = data['questions']
    cutoffs = data['cutoffs']
    arms = []
    for arm in data['arms']:
        rows = {}
        missing = []
        for q in questions:
            qid = q['id']
            if qid not in arm['rankings']:
                missing.append(qid)
                continue
            rows[qid] = {'question_sha256': digest(q), **_question_metrics(q, arm['rankings'][qid], cutoffs)}
        comparisons = {}
        for k in cutoffs:
            eligible = [(qid, row['metrics'][str(k)]['recall']) for qid, row in rows.items()
                        if row['metrics'][str(k)]['recall'] is not None]
            hit_eligible = [(qid, row['metrics'][str(k)]['hit_rate']) for qid, row in rows.items()
                            if row['metrics'][str(k)]['hit_rate'] is not None]
            comparisons[str(k)] = {
                'complete_ground_truth_recall_mean': sum(v for _, v in eligible) / len(eligible) if eligible else None,
                'complete_ground_truth_recall_questions': [qid for qid, _ in eligible],
                'complete_ground_truth_recall_denominator': len(eligible),
                'known_ground_truth_hit_rate_mean': sum(v for _, v in hit_eligible) / len(hit_eligible) if hit_eligible else None,
                'known_ground_truth_hit_rate_questions': [qid for qid, _ in hit_eligible],
                'known_ground_truth_hit_rate_denominator': len(hit_eligible),
            }
        failures = []
        identity_mismatches = []
        if arm['corpus_id'] != data['corpus']['id']:
            identity_mismatches.append({'type': 'corpus_id_mismatch', 'expected': data['corpus']['id'],
                                        'observed': arm['corpus_id']})
        if arm['question_ids'] != [q['id'] for q in questions]:
            identity_mismatches.append({'type': 'question_set_mismatch',
                                        'expected_question_ids': [q['id'] for q in questions],
                                        'observed_question_ids': arm['question_ids']})
        foreign_by_question = {
            qid: [source_id for source_id in ids if source_id not in data['corpus']['source_ids']]
            for qid, ids in arm['rankings'].items()
        }
        foreign_by_question = {qid: ids for qid, ids in foreign_by_question.items() if ids}
        if foreign_by_question:
            identity_mismatches.append({'type': 'source_id_outside_corpus',
                                        'by_question': foreign_by_question})
        failures.extend({'type': x['type'], **{k: v for k, v in x.items() if k != 'type'}}
                        for x in identity_mismatches)
        for q in questions:
            qid = q['id']
            if qid in missing:
                failures.append({'question_id': qid, 'type': 'missing_question_ranking'})
                continue
            row = rows[qid]
            if row['ground_truth_status'] == 'unknown':
                failures.append({'question_id': qid, 'type': 'ground_truth_unknown'})
            elif row['ground_truth_status'] == 'partial' and row['unjudged_candidate_ids']:
                failures.append({'question_id': qid, 'type': 'unjudged_candidates',
                                 'source_ids': row['unjudged_candidate_ids']})
            if row['ground_truth_status'] == 'complete' and not q['ground_truth']['relevant_source_ids']:
                failures.append({'question_id': qid, 'type': 'no_relevant_ground_truth'})
            elif row['ground_truth_status'] == 'complete' and not row['relevant_ranked_ids']:
                failures.append({'question_id': qid, 'type': 'no_known_relevant_candidate'})
            foreign = foreign_by_question.get(qid, [])
            if foreign:
                failures.append({'question_id': qid, 'type': 'source_id_outside_corpus', 'source_ids': foreign})
        arms.append({
            'strategy': arm['strategy'],
            'provenance': arm['provenance'],
            'corpus_id': arm['corpus_id'],
            'question_ids': arm['question_ids'],
            'identity_mismatches': identity_mismatches,
            'candidate_input_sha256': digest(arm['rankings']),
            'ranked_questions': len(rows),
            'expected_questions': len(questions),
            'missing_question_ids': missing,
            'per_question': rows,
            'aggregate_metrics': comparisons,
            'failure_taxonomy': failures,
        })
    report = {
        'format': 'sandbox-retrieval-report-1',
        'input_sha256': digest(data),
        'corpus': {'id': data['corpus']['id'], 'source_ids_sha256': digest(data['corpus']['source_ids']),
                   'source_count': len(data['corpus']['source_ids'])},
        'questions_sha256': digest(questions),
        'question_count': len(questions),
        'cutoffs': cutoffs,
        'arms': arms,
        'pairwise': _pairwise(arms, questions, cutoffs),
        'limitations': [
            'Evaluates supplied candidate lists only; no retrieval engine or model was run.',
            'Metric aggregates use only questions with the stated ground-truth support; inspect denominators and missing IDs.',
            'A source ID match establishes corpus membership, not citation entailment or answer correctness.',
        ],
    }
    report['report_sha256'] = digest(report)
    return report


def _pairwise(arms, questions, cutoffs):
    pairs = []
    for i, a in enumerate(arms):
        for b in arms[i + 1:]:
            identity_comparable = not a['identity_mismatches'] and not b['identity_mismatches']
            both = set(a['per_question']) & set(b['per_question']) if identity_comparable else set()
            deltas = {}
            for k in cutoffs:
                values = []
                for q in questions:
                    qid = q['id']
                    if qid in both and q['ground_truth']['status'] == 'complete':
                        av = a['per_question'][qid]['metrics'][str(k)]['recall']
                        bv = b['per_question'][qid]['metrics'][str(k)]['recall']
                        if av is not None and bv is not None:
                            values.append({'question_id': qid, 'a': av, 'b': bv, 'b_minus_a': bv - av})
                deltas[str(k)] = {'per_question': values,
                                  'mean_b_minus_a': sum(x['b_minus_a'] for x in values) / len(values) if values else None,
                                  'denominator': len(values)}
            pairs.append({'a': a['strategy'], 'b': b['strategy'],
                          'common_question_ids': [q['id'] for q in questions if q['id'] in both],
                          'complete_ground_truth_comparison': deltas,
                          'comparable_question_count': len(both),
                          'identity_comparable': identity_comparable,
                          'all_questions_present_in_both': len(both) == len(questions)})
    return pairs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        result = evaluate(json.loads(args.input.read_text(encoding='utf-8')))
        encoded = canonical(result)
        if args.output:
            args.output.write_bytes(encoded)
        else:
            print(encoded.decode(), end='')
    except (OSError, json.JSONDecodeError, RetrievalError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
