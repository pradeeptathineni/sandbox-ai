"""Stateless claim-to-evidence registry checks over exact retained owner records."""
from __future__ import annotations

import json
import re
import sys

from . import paired_comparison, retrieval_experiment, trial_evidence, trials

SCHEMA = "sandbox.claim-evidence-registry.v1"
FORMAT = "deep-sb09-claim-evidence-registry-1"
MAX_BYTES = 8_500_000
UUID = re.compile(r"^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$")
HEX = re.compile(r"^[a-f0-9]{64}$")
PACKET_VALIDATORS = {
    "trial-pair-evidence": trial_evidence.validate_packet,
    "paired-comparison-evidence": paired_comparison.validate_packet,
    "retrieval-experiment-evidence": retrieval_experiment.validate_packet,
}
PACKET_FORMATS = {
    "trial-pair-evidence": "sandbox-trial-pair-evidence-1",
    "paired-comparison-evidence": "sandbox-paired-comparison-evidence-1",
    "retrieval-experiment-evidence": "sandbox-retrieval-experiment-packet-1",
}


class RegistryError(ValueError):
    pass


def _closed(value, keys, required=None):
    required = set(keys if required is None else required)
    if not isinstance(value, dict) or set(value) - set(keys) or required - set(value):
        raise RegistryError("unknown or missing claim-registry fields")
    return value


def _sha(value, field):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise RegistryError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _id(value, field):
    if not isinstance(value, str) or not UUID.fullmatch(value):
        raise RegistryError(f"{field} must be a retained journal UUID")
    return value


def _text(value, field, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise RegistryError(f"{field} must be bounded nonempty text")
    return value.strip()


def claim_fingerprint(claim):
    claim = _closed(claim, {"claimId", "revision", "text", "subject"})
    subject = _closed(claim["subject"], {"project", "revision"})
    if not isinstance(claim["claimId"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", claim["claimId"]):
        raise RegistryError("claimId is invalid")
    if type(claim["revision"]) is not int or claim["revision"] < 1:
        raise RegistryError("claim revision must be a positive integer")
    normalized = {
        "claimId": claim["claimId"],
        "revision": claim["revision"],
        "text": _text(claim["text"], "claim text"),
        "subject": {
            "project": _text(subject["project"], "subject project", 200),
            "revision": _text(subject["revision"], "subject revision", 200),
        },
    }
    return normalized, trials.sha256_bytes(trials.canonical(normalized))


def _packet(snapshot, ref):
    row = _closed(ref, {"recordId", "packetSha256", "artifactKind", "inputSha256"},
                  required={"recordId", "packetSha256", "artifactKind"})
    rid = _id(row["recordId"], "evidence recordId")
    digest = _sha(row["packetSha256"], "evidence packetSha256")
    kind = row["artifactKind"]
    if kind not in PACKET_VALIDATORS:
        raise RegistryError("unsupported retained Sandbox packet kind")
    if snapshot is None:
        return {"recordId": rid, "packetSha256": digest, "artifactKind": kind,
                "status": "missing", "nativeValid": None}
    if snapshot == {"state": "unavailable"}:
        return {"recordId": rid, "packetSha256": digest, "artifactKind": kind,
                "status": "unknown", "nativeValid": None}
    stored = _closed(snapshot, {"recordId", "packetSha256", "packetJson", "context"},
                      required={"recordId", "packetSha256", "packetJson"})
    if stored["recordId"] != rid or stored["packetSha256"] != digest:
        raise RegistryError("cold-read evidence identity differs from registry locator")
    try:
        packet = json.loads(stored["packetJson"])
        if packet.get("format") != PACKET_FORMATS[kind] or packet.get("packet_sha256") != digest:
            raise RegistryError("retained packet format or digest differs from its locator")
        PACKET_VALIDATORS[kind](packet)
        if row.get("inputSha256") is not None:
            _sha(row["inputSha256"], "evidence inputSha256")
            context = stored.get("context")
            if (not isinstance(context, dict) or
                    context.get("format") != "deep-sb05-grounded-answer-review-1" or
                    context.get("requestInputSha256") != row["inputSha256"]):
                raise RegistryError("retained source-review context differs from evidence locator")
    except Exception as exc:
        return {"recordId": rid, "packetSha256": digest, "artifactKind": kind,
                "status": "invalid", "nativeValid": False, "detail": str(exc)[:300]}
    status = _packet_status(packet, kind)
    return {"recordId": rid, "packetSha256": digest, "artifactKind": kind,
            "status": status, "nativeValid": True}


def _packet_status(packet, kind):
    if kind == "trial-pair-evidence":
        values = [packet.get("selected_attempts", {}).get(key, {}).get("record", {}).get("execution", {}).get("status")
                  for key in ("baseline", "candidate")]
        return "completed" if values == ["completed", "completed"] else "incomplete"
    if kind == "paired-comparison-evidence":
        pairs = packet.get("selected_pairs")
        if not isinstance(pairs, list) or not pairs:
            return "unknown"
        complete = all(
            isinstance(row, dict) and
            isinstance(row.get("structural_comparison"), dict) and
            row["structural_comparison"].get("outcome_comparable") is True
            for row in pairs
        )
        return "completed" if complete else "incomplete"
    state = packet.get("paired_outcome", {}).get("status")
    return "completed" if state == "comparable" else "incomplete" if state is not None else "unknown"


def _review_snapshot(ref, snapshot, session_ref, evidence_refs):
    ref = _closed(ref, {"recordId", "packetSha256", "inputSha256", "sessionId", "reviewer"})
    rid = _id(ref["recordId"], "review recordId")
    packet_sha = _sha(ref["packetSha256"], "review packetSha256")
    input_sha = _sha(ref["inputSha256"], "review inputSha256")
    session_id = _id(ref["sessionId"], "review sessionId")
    reviewer = _text(ref["reviewer"], "reviewer", 200)
    if snapshot is None:
        return {"recordId": rid, "packetSha256": packet_sha, "inputSha256": input_sha,
                "sessionId": session_id, "reviewer": reviewer, "status": "missing"}
    if snapshot == {"state": "unavailable"}:
        return {"recordId": rid, "packetSha256": packet_sha, "inputSha256": input_sha,
                "sessionId": session_id, "reviewer": reviewer, "status": "unknown"}
    stored = _closed(snapshot, {"recordId", "packetSha256", "context"})
    if stored["recordId"] != rid or stored["packetSha256"] != packet_sha:
        raise RegistryError("cold-read reviewer identity differs from registry locator")
    context = stored["context"]
    try:
        required = {"format", "sessionId", "sessionRecordId", "sessionPacketSha256", "sessionInputSha256",
                    "baseReviewId", "basePacketSha256", "baseInputSha256", "reviewInputSha256", "reviewer", "judgments"}
        _closed(context, required)
        if (context["format"] != "deep-sb08-grounded-blind-review-1" or
                context["sessionId"] != session_id or context["reviewInputSha256"] != input_sha or
                context["reviewer"] != reviewer or context["sessionPacketSha256"] != packet_sha or
                context["sessionRecordId"] != session_ref["recordId"] or
                context["sessionPacketSha256"] != session_ref["packetSha256"] or
                context["sessionInputSha256"] != session_ref["inputSha256"] or
                not any(item["recordId"] == context["baseReviewId"] and
                        item["packetSha256"] == context["basePacketSha256"] and
                        item.get("inputSha256") == context["baseInputSha256"] for item in evidence_refs) or
                not isinstance(context["judgments"], list) or not context["judgments"]):
            raise RegistryError("review context differs from exact registry locator")
        return {"recordId": rid, "packetSha256": packet_sha, "inputSha256": input_sha,
                "sessionId": session_id, "reviewer": reviewer, "status": "current"}
    except (RegistryError, TypeError):
        return {"recordId": rid, "packetSha256": packet_sha, "inputSha256": input_sha,
                "sessionId": session_id, "reviewer": reviewer, "status": "mismatch"}


def registry(claim, evidence_refs, review_refs):
    normalized, revision_sha = claim_fingerprint(claim)
    if not isinstance(evidence_refs, list) or not 1 <= len(evidence_refs) <= 32:
        raise RegistryError("provide 1 to 32 exact retained packet references")
    evidence = [_closed(ref, {"recordId", "packetSha256", "artifactKind", "inputSha256"},
                         required={"recordId", "packetSha256", "artifactKind"}) for ref in evidence_refs]
    for ref in evidence:
        if ref.get("inputSha256") is not None:
            _sha(ref["inputSha256"], "evidence inputSha256")
        _id(ref["recordId"], "evidence recordId")
        _sha(ref["packetSha256"], "evidence packetSha256")
        if ref["artifactKind"] not in PACKET_VALIDATORS:
            raise RegistryError("unsupported retained Sandbox packet kind")
    identities = [(ref.get("recordId"), ref.get("packetSha256")) for ref in evidence]
    if len(set(identities)) != len(identities):
        raise RegistryError("duplicate evidence locator")
    review_refs = [] if review_refs is None else review_refs
    if not isinstance(review_refs, dict):
        raise RegistryError("review refs must be a closed session-and-review object")
    review_refs = _closed(review_refs, {"session", "reviews"}, required=set())
    session = review_refs.get("session")
    reviews = review_refs.get("reviews", [])
    if session is None and not reviews:
        review_bundle = None
    else:
        session = _closed(session, {"recordId", "packetSha256", "inputSha256", "sessionId"})
        _id(session["recordId"], "session recordId")
        _sha(session["packetSha256"], "session packetSha256")
        _sha(session["inputSha256"], "session inputSha256")
        _id(session["sessionId"], "sessionId")
        if not isinstance(reviews, list) or len(reviews) not in (0, 2):
            raise RegistryError("link either no blinded reviews or an exact reviewer pair")
        normalized_reviews = []
        for item in reviews:
            row = _closed(item, {"recordId", "packetSha256", "inputSha256", "sessionId", "reviewer"})
            _id(row["recordId"], "review recordId")
            _sha(row["packetSha256"], "review packetSha256")
            _sha(row["inputSha256"], "review inputSha256")
            _id(row["sessionId"], "review sessionId")
            row["reviewer"] = _text(row["reviewer"], "reviewer", 200)
            if row["sessionId"] != session["sessionId"]:
                raise RegistryError("reviewer record references a different blind session")
            normalized_reviews.append(row)
        reviews = normalized_reviews
        review_bundle = {"session": session, "reviews": reviews}
    return {"format": FORMAT, "claim": normalized, "claimRevisionSha256": revision_sha,
            "evidence": evidence, "review": review_bundle}


def register(claim, evidence_refs, packet_snapshots, review_refs=None,
             session_snapshot=None, review_snapshots=None, current_subject_revision=None):
    if not isinstance(packet_snapshots, list) or not packet_snapshots or not any(
            row is not None for row in packet_snapshots):
        raise RegistryError("registry needs one existing retained Sandbox packet anchor")
    value = registry(claim, evidence_refs, review_refs or {})
    result = audit(value, claim, packet_snapshots, session_snapshot,
                   review_snapshots, current_subject_revision)
    return {"registry": value, "audit": result}


def revise(previous_registry, claim, evidence_refs, packet_snapshots, review_refs=None,
           session_snapshot=None, review_snapshots=None, current_subject_revision=None):
    if not isinstance(previous_registry, dict) or previous_registry.get("format") != FORMAT:
        raise RegistryError("previous registry is not a supported claim record")
    previous_claim, previous_sha = claim_fingerprint(previous_registry.get("claim"))
    if previous_sha != previous_registry.get("claimRevisionSha256"):
        raise RegistryError("previous claim fingerprint is invalid")
    current, _current_sha = claim_fingerprint(claim)
    if current["claimId"] != previous_claim["claimId"] or current["revision"] != previous_claim["revision"] + 1:
        raise RegistryError("revision must advance the same claim by exactly one")
    if current["text"] == previous_claim["text"] and current["subject"] == previous_claim["subject"]:
        raise RegistryError("a revised claim must change its text or subject basis")
    previous_reviews = previous_registry.get("review") or {}
    old_ids = {row.get("recordId") for row in previous_reviews.get("reviews", [])}
    old_session_id = (previous_reviews.get("session") or {}).get("recordId")
    next_reviews = review_refs or {}
    next_ids = {row.get("recordId") for row in next_reviews.get("reviews", []) if isinstance(row, dict)}
    next_session_id = (next_reviews.get("session") or {}).get("recordId") if isinstance(next_reviews, dict) else None
    if old_ids & next_ids or (old_session_id and old_session_id == next_session_id):
        raise RegistryError("a claim revision cannot inherit prior reviewer records")
    return register(current, evidence_refs, packet_snapshots, review_refs,
                    session_snapshot, review_snapshots, current_subject_revision)


def audit(registry_value, current_claim, packet_snapshots, session_snapshot=None, review_snapshots=None,
          current_subject_revision=None):
    if not isinstance(registry_value, dict) or registry_value.get("format") != FORMAT:
        raise RegistryError(f"format must be {FORMAT}")
    expected_registry = registry(registry_value.get("claim"), registry_value.get("evidence"),
                                 registry_value.get("review") or {})
    if trials.canonical(expected_registry) != trials.canonical(registry_value):
        raise RegistryError("stored registry is not the exact supported closed form")
    stored_claim, stored_sha = claim_fingerprint(registry_value.get("claim"))
    if stored_sha != registry_value.get("claimRevisionSha256"):
        raise RegistryError("stored claim revision fingerprint is invalid")
    current, current_sha = claim_fingerprint(current_claim)
    stale = current_sha != stored_sha
    if not isinstance(packet_snapshots, list) or len(packet_snapshots) != len(registry_value.get("evidence", [])):
        raise RegistryError("packet snapshot list does not match registry evidence references")
    evidence_checks = [_packet(snapshot, ref) for ref, snapshot in zip(registry_value["evidence"], packet_snapshots)]
    reviews = []
    review_bundle = registry_value.get("review")
    if review_bundle:
        refs = review_bundle.get("reviews", [])
        review_snapshots = review_snapshots or [None] * len(refs)
        if len(review_snapshots) != len(refs):
            raise RegistryError("review snapshot list does not match registry reviewer references")
        if session_snapshot is None:
            session_state = "missing"
        elif session_snapshot == {"state": "unavailable"}:
            session_state = "unknown"
        else:
            session_ref = review_bundle["session"]
            session = _closed(session_snapshot, {"recordId", "packetSha256", "context"})
            context = session["context"]
            session_state = "current" if (
                session["recordId"] == session_ref["recordId"] and
                session["packetSha256"] == session_ref["packetSha256"] and
                isinstance(context, dict) and context.get("format") == "deep-sb08-grounded-blind-session-1" and
                context.get("sessionId") == session_ref["sessionId"] and
                context.get("sessionInputSha256") == session_ref["inputSha256"] and
                any(item["recordId"] == context.get("baseReviewId") and
                    item["packetSha256"] == context.get("basePacketSha256") and
                    item.get("inputSha256") == context.get("baseInputSha256") for item in registry_value["evidence"])
            ) else "mismatch"
        reviews = [_review_snapshot(ref, snapshot, review_bundle["session"], registry_value["evidence"])
                   for ref, snapshot in zip(refs, review_snapshots)]
        if len(reviews) == 2 and reviews[0]["reviewer"].casefold() == reviews[1]["reviewer"].casefold():
            for row in reviews:
                row["status"] = "mismatch"
    else:
        session_state = "not-linked"
    subject_state = "unknown" if current_subject_revision is None else (
        "current" if current_subject_revision == stored_claim["subject"]["revision"] else "changed")
    evidence_state = "missing" if any(row["status"] == "missing" for row in evidence_checks) else (
        "unknown" if any(row["status"] in {"unknown", "invalid"} for row in evidence_checks) else
        "incomplete" if any(row["status"] != "completed" for row in evidence_checks) else "current")
    review_state = "not-linked" if not review_bundle else (
        "missing" if session_state == "missing" or any(row["status"] == "missing" for row in reviews) else
        "mismatch" if session_state == "mismatch" or any(row["status"] == "mismatch" for row in reviews) else
        "current" if reviews and all(row["status"] == "current" for row in reviews) else "unknown")
    if stale:
        status = "stale-claim-revision"
    elif subject_state != "current":
        status = "subject-" + subject_state
    elif evidence_state != "current":
        status = "evidence-" + evidence_state
    elif review_state in {"missing", "mismatch", "unknown"}:
        status = "review-" + review_state
    elif review_state == "not-linked":
        status = "review-not-linked"
    else:
        status = "identity-current"
    return {"format": "sandbox-claim-evidence-audit-1", "claimId": stored_claim["claimId"],
            "storedClaimRevisionSha256": stored_sha, "currentClaimRevisionSha256": current_sha,
            "claimMatches": not stale, "status": status,
            "subjectStatus": subject_state, "subjectRevisionSource": "caller-supplied",
            "evidenceStatus": evidence_state,
            "evidence": evidence_checks, "reviewStatus": review_state, "reviews": reviews,
            "proofInherited": False if stale else None,
            "qualification": "This is an exact identity and provenance audit, not validation of claim truth, reviewer accuracy, entailment, or release approval."}


def operate(value):
    value = _closed(value, {"schema", "operation", "input"})
    if value["schema"] != SCHEMA:
        raise RegistryError("unsupported claim evidence registry request schema")
    operation = value["operation"]
    data = value["input"]
    if operation == "register":
        data = _closed(data, {"claim", "evidence", "packetSnapshots", "review", "sessionSnapshot",
                              "reviewSnapshots", "currentSubjectRevision"},
                       required={"claim", "evidence", "packetSnapshots", "currentSubjectRevision"})
        result = register(data["claim"], data["evidence"], data["packetSnapshots"],
                          data.get("review"), data.get("sessionSnapshot"),
                          data.get("reviewSnapshots"), data["currentSubjectRevision"])
    elif operation == "audit":
        data = _closed(data, {"registry", "currentClaim", "packetSnapshots", "sessionSnapshot", "reviewSnapshots", "currentSubjectRevision"},
                       required={"registry", "currentClaim", "packetSnapshots"})
        result = audit(data["registry"], data["currentClaim"], data["packetSnapshots"],
                       data.get("sessionSnapshot"), data.get("reviewSnapshots"), data.get("currentSubjectRevision"))
    elif operation == "revise":
        data = _closed(data, {"previousRegistry", "claim", "evidence", "packetSnapshots", "review",
                              "sessionSnapshot", "reviewSnapshots", "currentSubjectRevision"},
                       required={"previousRegistry", "claim", "evidence", "packetSnapshots", "currentSubjectRevision"})
        result = revise(data["previousRegistry"], data["claim"], data["evidence"], data["packetSnapshots"],
                        data.get("review"), data.get("sessionSnapshot"), data.get("reviewSnapshots"),
                        data["currentSubjectRevision"])
    else:
        raise RegistryError("operation must be register, revise, or audit")
    if len(trials.canonical(result)) > MAX_BYTES:
        raise RegistryError("claim registry result exceeds the size limit")
    return {"schema": "sandbox.claim-evidence-result.v1", "operation": operation, "result": result}


def main():
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise SystemExit("request exceeds size limit")
    try:
        result = operate(json.loads(raw))
    except (ValueError, TypeError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc
    sys.stdout.write(trials.canonical(result).decode("utf-8") + "\n")


if __name__ == "__main__":
    main()
