"""Freeze a multi-system index of existing Sandbox trial plans without executing them.

Manifests are immutable records in the same trial store as plans and attempts. They
reference exact plan digests and arms; they do not copy plans or create attempts.
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

from harness import trials

FORMAT = "sandbox-multi-system-experiment-manifest-1"
INPUT_KEYS = {"title", "decision", "systems"}
SYSTEM_KEYS = {"system_id", "plan_sha256", "arm", "role"}
ROLES = {"baseline", "candidate"}
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
UUID = re.compile(r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}\Z")
MAX_SYSTEMS = 16
MAX_MANIFESTS = 256


class ManifestError(trials.TrialError):
    """A manifest request was refused without changing a frozen plan or attempt."""


def canonical(value):
    return trials.canonical(value)


def _text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ManifestError(f"{label} must be non-empty text of at most {limit} characters.")
    return value.strip()


def _plan(store, digest):
    if not isinstance(digest, str) or not SHA.fullmatch(digest):
        raise ManifestError("Use an exact retained frozen-plan SHA-256.")
    try:
        record = trials.load_record(store, "plans", digest)
        trials.validate_plan(record)
    except (trials.TrialError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ManifestError(f"Referenced plan {digest} is missing or invalid: {exc}") from exc
    return record


def _authority(plan):
    authority = plan.get("authority")
    required = ("authorized_by", "operations", "model_execution", "isolation_owner", "permissions")
    missing = []
    if not isinstance(authority, dict):
        return {"status": "incomplete", "missing_fields": list(required)}
    for key in required:
        value = authority.get(key)
        if value is None or value == "" or value == [] or value == {}:
            missing.append(key)
        elif isinstance(value, str) and value.strip().lower() in {"unknown", "tbd", "todo", "?"}:
            missing.append(key)
    return {
        "status": "incomplete" if missing else "caller-stated-not-verified",
        "missing_fields": missing,
    }


def _normalized_input(validated):
    return {
        "title": validated["title"],
        "decision": validated["decision"],
        "systems": [
            {key: system[key] for key in ("system_id", "plan_sha256", "arm", "role")}
            for system in validated["systems"]
        ],
    }


def _validate_input(store, value):
    if not isinstance(value, dict) or set(value) != INPUT_KEYS:
        raise ManifestError(f"Manifest input keys must be exactly {sorted(INPUT_KEYS)}.")
    title = _text(value["title"], "title", 160)
    decision = value["decision"]
    if not isinstance(decision, dict) or set(decision) != {"question", "owner", "consumer"}:
        raise ManifestError("decision must contain exactly question, owner and consumer.")
    normalized_decision = {
        key: _text(decision[key], f"decision.{key}", 2000 if key == "question" else 300)
        for key in ("question", "owner", "consumer")
    }
    systems = value["systems"]
    if not isinstance(systems, list) or not 2 <= len(systems) <= MAX_SYSTEMS:
        raise ManifestError(f"Select between two and {MAX_SYSTEMS} retained systems.")
    seen_ids, seen_refs, roles = set(), set(), {"baseline": 0, "candidate": 0}
    validated = []
    for index, system in enumerate(systems):
        if not isinstance(system, dict) or set(system) != SYSTEM_KEYS:
            raise ManifestError(f"systems[{index}] must contain exactly {sorted(SYSTEM_KEYS)}.")
        system_id = system["system_id"]
        if not isinstance(system_id, str) or not ID.fullmatch(system_id) or system_id in seen_ids:
            raise ManifestError(f"systems[{index}].system_id must be unique lowercase identifier text.")
        digest = system["plan_sha256"]
        arm = system["arm"]
        role = system["role"]
        if not isinstance(role, str) or role not in ROLES:
            raise ManifestError(f"systems[{index}].role must be baseline or candidate.")
        if not isinstance(arm, str) or not arm:
            raise ManifestError(f"systems[{index}].arm must name an exact frozen-plan arm.")
        plan = _plan(store, digest)
        ref = (digest, arm)
        if ref in seen_refs:
            raise ManifestError("Each selected frozen plan arm may appear only once.")
        if arm not in plan["treatment"]["arms"]:
            raise ManifestError(f"Arm {arm!r} is not present in retained plan {digest}.")
        if plan["decision"] != normalized_decision:
            raise ManifestError("All selected plans must name the exact same decision question, owner and consumer.")
        seen_ids.add(system_id)
        seen_refs.add(ref)
        roles[role] += 1
        validated.append({"system_id": system_id, "plan_sha256": digest, "arm": arm, "role": role, "plan": plan})
    if roles["baseline"] != 1 or roles["candidate"] < 1:
        raise ManifestError("Choose exactly one baseline arm and at least one candidate arm.")
    return {"title": title, "decision": normalized_decision, "systems": validated}


def _manifest_by_request(store, request_id):
    folder = Path(store) / "manifests"
    if not folder.is_dir():
        return None
    paths = sorted(folder.glob("*.json"))
    if len(paths) > MAX_MANIFESTS:
        raise ManifestError("Manifest inventory exceeds its bounded read limit.")
    found = []
    for path in paths:
        digest = path.stem
        record = trials.load_record(store, "manifests", digest)
        if isinstance(record, dict) and record.get("request_id") == request_id:
            found.append((digest, record))
    if len(found) > 1:
        raise ManifestError("Request identity appears in multiple frozen manifests; preserve and inspect the records.")
    return found[0] if found else None


def freeze_manifest(store, request_id, value, frozen_at=None):
    """Validate and write one immutable manifest; same request/input returns its prior digest."""
    if not isinstance(request_id, str) or not UUID.fullmatch(request_id):
        raise ManifestError("A canonical request UUID is required.")
    validated = _validate_input(store, value)
    normalized = _normalized_input(validated)
    input_digest = trials.sha256_bytes(canonical(normalized))
    prior = _manifest_by_request(store, request_id)
    if prior:
        digest, record = prior
        if record.get("input_sha256") != input_digest:
            raise ManifestError("Request identity was already used for a different manifest input.")
        return digest
    record = {
        "format": FORMAT,
        "request_id": request_id,
        "input_sha256": input_digest,
        "frozen_at_utc": frozen_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "title": validated["title"],
        "decision": validated["decision"],
        "systems": [
            {key: system[key] for key in ("system_id", "plan_sha256", "arm", "role")}
            for system in validated["systems"]
        ],
    }
    digest = trials._write_record(store, "manifests", record)
    reopened = trials.load_record(store, "manifests", digest)
    if reopened != record:
        raise ManifestError("Frozen manifest could not be cold-verified after writing; retain the request identity.")
    return digest


def _system_projection(store, record_systems, decision):
    systems = []
    for row in record_systems:
        plan = _plan(store, row["plan_sha256"])
        if row["arm"] not in plan["treatment"]["arms"] or plan["decision"] != decision:
            raise ManifestError("A referenced plan no longer matches the frozen manifest; no replacement was selected.")
        case_ids, rubric_ids = sorted(plan["cases"]), sorted(plan["rubrics"])
        systems.append({
            **row,
            "plan_id": plan["plan_id"],
            "record_kind": plan["record_kind"],
            "frozen_before_execution": plan["frozen_before_execution"],
            "cases": [{"id": key, **plan["cases"][key]} for key in case_ids],
            "rubrics": [{"id": key, **plan["rubrics"][key]} for key in rubric_ids],
            "criteria": [{"id": item["id"], "kind": item["kind"], "provenance": item["provenance"]}
                         for item in plan["criteria"]],
            "authority_review": _authority(plan),
        })
    return systems


def _scope_overlap(record_systems, systems):
    case_sets = [set(item["id"] for item in row["cases"]) for row in systems]
    rubric_sets = [set(item["id"] for item in row["rubrics"]) for row in systems]
    return {
        "common_case_ids": sorted(set.intersection(*case_sets)) if case_sets else [],
        "common_rubric_ids": sorted(set.intersection(*rubric_sets)) if rubric_sets else [],
        "case_ids_by_system": {row["system_id"]: sorted(values) for row, values in zip(record_systems, case_sets)},
        "rubric_ids_by_system": {row["system_id"]: sorted(values) for row, values in zip(record_systems, rubric_sets)},
        "qualification": "Identity overlap only. Similar labels or shared digests do not establish equivalent cases, rubrics or outcomes.",
    }


def _execution():
    return {
        "status": "blocked",
        "reason": "This manifest workflow has no execution capability. Plan authority fields are caller-stated and are not independent permission grants.",
    }


def prepare_manifest(store, value):
    """Read-only preflight used before the UI records its exact freeze request."""
    validated = _validate_input(store, value)
    systems = _system_projection(store, validated["systems"], validated["decision"])
    normalized = _normalized_input(validated)
    return {
        "format": "sandbox-multi-system-experiment-manifest-preview-1",
        "input": normalized,
        "input_sha256": trials.sha256_bytes(canonical(normalized)),
        "systems": systems,
        "scope_overlap": _scope_overlap(normalized["systems"], systems),
        "execution": _execution(),
    }


def _review(store, digest, record):
    systems = _system_projection(store, record["systems"], record["decision"])
    return {
        "manifest_sha256": digest,
        "manifest": record,
        "systems": systems,
        "scope_overlap": _scope_overlap(record["systems"], systems),
        "execution": _execution(),
    }


def show_manifest(store, digest):
    if not isinstance(digest, str) or not SHA.fullmatch(digest):
        raise ManifestError("Use an exact retained manifest SHA-256.")
    try:
        record = trials.load_record(store, "manifests", digest)
    except (trials.TrialError, ValueError, KeyError) as exc:
        raise ManifestError(f"Manifest {digest} is missing or invalid: {exc}") from exc
    if (
        not isinstance(record, dict)
        or set(record) != {"format", "request_id", "input_sha256", "frozen_at_utc", "title", "decision", "systems"}
        or record.get("format") != FORMAT
    ):
        raise ManifestError("Retained record is not a multi-system experiment manifest.")
    input_value = {key: record.get(key) for key in ("title", "decision", "systems")}
    validated = _validate_input(store, input_value)
    if record.get("input_sha256") != trials.sha256_bytes(canonical(_normalized_input(validated))):
        raise ManifestError("Manifest input digest does not match its frozen content.")
    if record.get("request_id") is None or not UUID.fullmatch(str(record["request_id"])):
        raise ManifestError("Manifest request identity is invalid.")
    _text(record.get("frozen_at_utc"), "frozen_at_utc", 80)
    return _review(store, digest, record)


def recover_request(store, request_id, input_sha256):
    """Cold-read the exact request. Missing is reported as absent, never permission to replay."""
    if not isinstance(request_id, str) or not UUID.fullmatch(request_id) or not isinstance(input_sha256, str) or not SHA.fullmatch(input_sha256):
        raise ManifestError("Use the exact saved request UUID and manifest input digest.")
    prior = _manifest_by_request(store, request_id)
    if prior is None:
        return {"state": "absent", "request_id": request_id, "qualification": "Absence does not prove that a late write cannot arrive; keep the original request locked."}
    digest, record = prior
    if record.get("input_sha256") != input_sha256:
        raise ManifestError("Retained request identity binds different input; keep the original request locked.")
    return {"state": "confirmed", **show_manifest(store, digest)}


def list_plans(store):
    rows = []
    for digest, plan in trials.iter_records(store, "plans"):
        trials.validate_plan(plan)
        rows.append({
            "plan_sha256": digest,
            "plan_id": plan["plan_id"],
            "decision": plan["decision"],
            "record_kind": plan["record_kind"],
            "frozen_before_execution": plan["frozen_before_execution"],
            "arms": list(plan["treatment"]["arms"]),
            "authority_review": _authority(plan),
        })
        if len(rows) > 256:
            raise ManifestError("Plan inventory exceeds its bounded read limit.")
    return {"plans": rows, "execution": "blocked: no execution capability is supplied by manifests"}


def list_manifests(store):
    folder = Path(store) / "manifests"
    paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
    if len(paths) > MAX_MANIFESTS:
        raise ManifestError("Manifest inventory exceeds its bounded read limit.")
    rows = []
    for path in paths:
        result = show_manifest(store, path.stem)
        record = result["manifest"]
        rows.append({
            "manifest_sha256": path.stem,
            "request_id": record["request_id"],
            "title": record["title"],
            "frozen_at_utc": record["frozen_at_utc"],
            "system_count": len(record["systems"]),
            "execution": "blocked",
        })
    return {"manifests": rows}


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m harness.experiment_manifests")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("list-plans", "list-manifests"):
        sub.add_parser(name).add_argument("--store", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--store", required=True)
    freeze.add_argument("--request-id", required=True)
    freeze.add_argument("--manifest", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--store", required=True)
    prepare.add_argument("--manifest", required=True)
    show = sub.add_parser("show")
    show.add_argument("--store", required=True)
    show.add_argument("--manifest-sha256", required=True)
    recover = sub.add_parser("recover")
    recover.add_argument("--store", required=True)
    recover.add_argument("--request-id", required=True)
    recover.add_argument("--input-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "list-plans":
            result = list_plans(args.store)
        elif args.command == "list-manifests":
            result = list_manifests(args.store)
        elif args.command == "freeze":
            result = {"manifest_sha256": freeze_manifest(args.store, args.request_id, _load_json(args.manifest))}
        elif args.command == "prepare":
            result = prepare_manifest(args.store, _load_json(args.manifest))
        elif args.command == "show":
            result = show_manifest(args.store, args.manifest_sha256)
        else:
            result = recover_request(args.store, args.request_id, args.input_sha256)
    except (ManifestError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"refused": str(exc)}, ensure_ascii=False))
        return 2
    sys.stdout.write(canonical(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
