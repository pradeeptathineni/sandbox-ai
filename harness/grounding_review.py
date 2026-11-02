"""Stateless, packet-bound helpers for caller-authored blinded grounding review.

The module validates every packet with the existing retrieval experiment owner.
It persists nothing and does not run a model, retriever, or participant study.
"""
from __future__ import annotations

import json
import re
import sys

from . import retrieval_experiment

RATINGS = {"supported", "unsupported", "uncertain", "not-assessed"}
ALIASES = {"A", "B"}
MAX_INPUT = 8_500_000
RUBRIC = {
    "schema": "deep-grounded-answer-review-rubric-1",
    "id": "grounded-answer-support",
    "revision": "1",
    "criterion": "Using the exact question, answer, and retained evidence shown, does the evidence support the answer's relevant claims? This is a caller judgment, not a finding of truth or entailment.",
    "ratings": [
        {"value": "supported", "meaning": "The shown evidence supports the relevant answer claims."},
        {"value": "unsupported", "meaning": "The shown evidence does not support the relevant answer claims."},
        {"value": "uncertain", "meaning": "The evidence or answer is too ambiguous to judge."},
        {"value": "not-assessed", "meaning": "The reviewer did not assess this row."},
    ],
}


def _closed(value, keys, required=None):
    required = set(keys if required is None else required)
    if not isinstance(value, dict) or set(value) - set(keys) or required - set(value):
        raise ValueError("unknown or missing grounding-review fields")
    return value


def _string(value, name, maximum=500):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded nonempty string")
    return value


def _blind_text(text, aliases):
    if not isinstance(text, str):
        return text
    for row in sorted(aliases, key=lambda item: len(item["strategy"]), reverse=True):
        escaped = re.escape(row["strategy"])
        text = re.sub(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])",
                      f"arm {row['alias']}", text, flags=re.IGNORECASE)
    return text


def _packet(value):
    if not isinstance(value, dict):
        raise ValueError("packet must be an object")
    try:
        return retrieval_experiment.validate_packet(value)
    except Exception as exc:
        raise ValueError(f"Sandbox rejected the frozen retrieval packet: {exc}") from exc


def _judgments(view_keys, value):
    if not isinstance(view_keys, list) or not 1 <= len(view_keys) <= 96:
        raise ValueError("viewKeys must contain 1 to 96 rows")
    expected = set()
    for item in view_keys:
        row = _closed(item, {"armAlias", "questionId"})
        if row["armAlias"] not in ALIASES:
            raise ValueError("invalid blind alias")
        key = (row["armAlias"], _string(row["questionId"], "questionId"))
        if key in expected:
            raise ValueError("duplicate blind view row")
        expected.add(key)
    if not isinstance(value, list) or len(value) != len(expected):
        raise ValueError("provide one rating for each blind row")
    seen, result = set(), []
    for item in value:
        row = _closed(item, {"armAlias", "questionId", "rating", "rationale"})
        key = (row["armAlias"], _string(row["questionId"], "questionId"))
        if key not in expected or key in seen or row["rating"] not in RATINGS:
            raise ValueError("rating rows must exactly match the blind view")
        rationale = _string(row["rationale"], "rationale", 1000).strip()
        seen.add(key)
        result.append({"armAlias": key[0], "questionId": key[1],
                       "rating": row["rating"], "rationale": rationale})
    if seen != expected:
        raise ValueError("rating rows must exactly match the blind view")
    return result


def session_view(value):
    value = _closed(value, {"packetJson", "aliases", "studyRows"})
    packet_json = value["packetJson"]
    if not isinstance(packet_json, str) or len(packet_json.encode("utf-8")) > MAX_INPUT:
        raise ValueError("packetJson must be bounded exact frozen packet bytes")
    try:
        packet_value = json.loads(packet_json)
    except json.JSONDecodeError as exc:
        raise ValueError("packetJson is invalid JSON") from exc
    packet = _packet(packet_value)
    aliases = value["aliases"]
    arms = packet["comparison_input"]["arms"]
    strategies = [arm["strategy"] for arm in arms]
    if (not isinstance(aliases, list) or len(aliases) != 2 or
            {row.get("alias") for row in aliases if isinstance(row, dict)} != ALIASES or
            {row.get("strategy") for row in aliases if isinstance(row, dict)} != set(strategies)):
        raise ValueError("aliases must bind both exact packet arms once")
    aliases = [_closed(row, {"alias", "strategy"}) for row in aliases]
    for row in aliases:
        _string(row["strategy"], "strategy")
    observations = packet.get("grounding_evaluation", {}).get("judgments", {}).get("observations")
    if not isinstance(observations, list):
        raise ValueError("packet contains no caller-authored grounding observations")
    questions = packet["comparison_input"]["questions"]
    study_rows = value["studyRows"]
    if not isinstance(study_rows, list) or len(study_rows) > 96:
        raise ValueError("studyRows must be bounded")
    indexed = {}
    for item in study_rows:
        row = _closed(item, {"strategy", "questionId", "answerText", "evidenceExcerpts"})
        key = (_string(row["strategy"], "strategy"), _string(row["questionId"], "questionId"))
        if key in indexed:
            raise ValueError("duplicate Study row")
        if row["answerText"] is not None and (not isinstance(row["answerText"], str) or len(row["answerText"]) > 200_000):
            raise ValueError("answerText exceeds the bounded projection")
        excerpts = row["evidenceExcerpts"]
        if not isinstance(excerpts, list) or len(excerpts) > 24 or any(not isinstance(text, str) or len(text) > 20_000 for text in excerpts):
            raise ValueError("evidenceExcerpts exceed the bounded projection")
        indexed[key] = row
    rows = []
    expected_study = set()
    for alias in aliases:
        for question in questions:
            observation = next((row for row in observations if row.get("strategy") == alias["strategy"] and row.get("question_id") == question["id"]), None)
            if observation is None:
                raise ValueError("packet arm/question coverage is incomplete")
            key = (alias["strategy"], question["id"])
            answer, excerpts = None, []
            availability = "not-provided"
            if observation.get("answer_status") == "answered":
                expected_study.add(key)
                study = indexed.get(key)
                if study is None:
                    raise ValueError("answered rows require exact retained Study reads")
                answer = study["answerText"]
                excerpts = study["evidenceExcerpts"]
                availability = "available" if answer is not None else "unavailable"
            rows.append({
                "armAlias": alias["alias"], "questionId": question["id"],
                "question": _blind_text(question["text"], aliases),
                "answer": _blind_text(answer, aliases) if answer is not None else None,
                "evidenceExcerpts": [_blind_text(text, aliases) for text in excerpts],
                "answerStatus": observation["answer_status"], "availability": availability,
            })
    if set(indexed) != expected_study:
        raise ValueError("Study rows must match exactly the answered packet rows")
    return {"view": rows}


def review_check(value):
    value = _closed(value, {"viewKeys", "judgments"})
    return {"judgments": _judgments(value["viewKeys"], value["judgments"])}


def paired_compare(value):
    value = _closed(value, {"rubric", "aliases", "reviews"})
    if value["rubric"] != RUBRIC:
        raise ValueError("paired comparison requires the predeclared frozen grounding rubric")
    aliases = value["aliases"]
    if not isinstance(aliases, list) or len(aliases) != 2:
        raise ValueError("paired comparison requires exact two-arm aliases")
    aliases = [_closed(row, {"alias", "strategy"}) for row in aliases]
    if {row["alias"] for row in aliases} != ALIASES or len({row["strategy"] for row in aliases}) != 2:
        raise ValueError("paired comparison aliases are invalid")
    reviews = value["reviews"]
    if not isinstance(reviews, list) or len(reviews) != 2:
        raise ValueError("paired comparison requires exactly two reviews")
    normalized = []
    for item in reviews:
        review = _closed(item, {"reviewer", "judgments"})
        normalized.append({"reviewer": _string(review["reviewer"], "reviewer", 200).strip(),
                           "judgments": review["judgments"]})
    if normalized[0]["reviewer"].casefold() == normalized[1]["reviewer"].casefold():
        raise ValueError("paired reveal requires distinct caller-declared reviewers")
    first = normalized[0]["judgments"]
    if not isinstance(first, list):
        raise ValueError("review judgments are invalid")
    keys = [{"armAlias": row.get("armAlias"), "questionId": row.get("questionId")} for row in first]
    left = _judgments(keys, first)
    right = _judgments(keys, normalized[1]["judgments"])
    lm = {(row["armAlias"], row["questionId"]): row for row in left}
    rm = {(row["armAlias"], row["questionId"]): row for row in right}
    comparisons = []
    for key in sorted(lm):
        a, b = lm[key], rm[key]
        unknown = a["rating"] in {"uncertain", "not-assessed"} or b["rating"] in {"uncertain", "not-assessed"}
        comparisons.append({"armAlias": key[0], "questionId": key[1],
                            "reviewerA": a["rating"], "rationaleA": a["rationale"],
                            "reviewerB": b["rating"], "rationaleB": b["rationale"],
                            "status": "unknown" if unknown else "agreement" if a["rating"] == b["rating"] else "disagreement"})
    return {"comparisons": comparisons,
            "counts": {status: sum(row["status"] == status for row in comparisons) for status in ("agreement", "disagreement", "unknown")} | {"denominator": len(comparisons)},
            "reviewerNames": [row["reviewer"] for row in normalized],
            "aliasReveal": aliases,
            "rubric": value["rubric"]}


def operate(value):
    value = _closed(value, {"schema", "operation", "input"})
    if value["schema"] != "sandbox.grounding-review-request.v1":
        raise ValueError("unsupported grounding-review schema")
    operations = {"session-view": session_view, "review-check": review_check, "paired-compare": paired_compare}
    operation = value["operation"]
    if operation not in operations:
        raise ValueError("unsupported grounding-review operation")
    return {"schema": "sandbox.grounding-review-result.v1", "operation": operation,
            "result": operations[operation](value["input"])}


def main():
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise SystemExit("input exceeds limit")
    try:
        value = json.loads(raw)
        result = operate(value)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
        sys.stdout.write("\n")
    except Exception as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
