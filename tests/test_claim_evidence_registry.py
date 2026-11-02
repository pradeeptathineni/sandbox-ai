import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness import claim_evidence_registry, paired_comparison, retrieval_experiment, trials
from test_retrieval_experiment import (
    FIXTURE, build_attempt, build_plan, build_grounding_judgments,
)


class ClaimEvidenceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "store"
        self.plan = build_plan()
        self.plan_sha = trials.freeze_plan(self.store, self.plan)
        comparison = json.loads(FIXTURE.read_text())
        comparison["arms"][0]["strategy"] = "baseline"
        comparison["arms"][1]["strategy"] = "candidate"
        comparison["arms"][0]["provenance"]["producer"] = "synthetic baseline"
        comparison["arms"][1]["provenance"]["producer"] = "synthetic candidate"
        for arm, name in (("baseline", "baseline-1"), ("candidate", "candidate-1")):
            rankings = comparison["arms"][0 if arm == "baseline" else 1]["rankings"]
            trials.seal_attempt(self.store, self.plan_sha,
                build_attempt(self.plan, name, arm, rankings), sealed_at="synthetic")
        self.packet = retrieval_experiment.create_packet(
            self.store, self.plan_sha, "baseline-1", "candidate-1", comparison,
            build_grounding_judgments(comparison))
        self.packet_json = retrieval_experiment.render_packet(self.packet, "json")
        self.packet_sha = self.packet["packet_sha256"]
        self.claim = {"claimId": "synthetic-result", "revision": 1,
            "text": "The synthetic paired packet is digest-valid.",
            "subject": {"project": "sandbox-ai", "revision": "fixture-revision-1"}}
        self.ref = {"recordId": "11111111-1111-4111-8111-111111111111",
            "packetSha256": self.packet_sha, "artifactKind": "retrieval-experiment-evidence"}
        self.snapshot = {"recordId": self.ref["recordId"], "packetSha256": self.packet_sha,
            "packetJson": self.packet_json}
        self.session_ref = {"recordId": "22222222-2222-4222-8222-222222222222",
            "packetSha256": self.packet_sha, "inputSha256": "a" * 64,
            "sessionId": "33333333-3333-4333-8333-333333333333"}
        self.source_context = {"format": "deep-sb05-grounded-answer-review-1",
            "requestInputSha256": "c" * 64, "bindings": []}
        self.source_ref = {**self.ref, "recordId": "44444444-4444-4444-8444-444444444444",
            "inputSha256": "c" * 64}
        self.session_snapshot = {"recordId": self.session_ref["recordId"],
            "packetSha256": self.packet_sha,
            "context": {"format": "deep-sb08-grounded-blind-session-1",
                "sessionId": self.session_ref["sessionId"],
                "sessionInputSha256": self.session_ref["inputSha256"],
                "baseReviewId": self.source_ref["recordId"],
                "basePacketSha256": self.packet_sha,
                "baseInputSha256": self.source_ref["inputSha256"]}}
        self.review_refs = {"session": self.session_ref, "reviews": [
            {"recordId": "55555555-5555-4555-8555-555555555555", "packetSha256": self.packet_sha,
             "inputSha256": "d" * 64, "sessionId": self.session_ref["sessionId"], "reviewer": "Reviewer 1"},
            {"recordId": "66666666-6666-4666-8666-666666666666", "packetSha256": self.packet_sha,
             "inputSha256": "e" * 64, "sessionId": self.session_ref["sessionId"], "reviewer": "Reviewer 2"},
        ]}
        self.review_snapshots = []
        for ref in self.review_refs["reviews"]:
            context = {"format": "deep-sb08-grounded-blind-review-1",
                "sessionId": self.session_ref["sessionId"],
                "sessionRecordId": self.session_ref["recordId"],
                "sessionPacketSha256": self.packet_sha,
                "sessionInputSha256": self.session_ref["inputSha256"],
                "baseReviewId": self.source_ref["recordId"],
                "basePacketSha256": self.packet_sha,
                "baseInputSha256": self.source_ref["inputSha256"],
                "reviewInputSha256": ref["inputSha256"], "reviewer": ref["reviewer"],
                "judgments": [{"armAlias": "A", "questionId": "q:policy", "rating": "supported", "rationale": "synthetic"}]}
            self.review_snapshots.append({"recordId": ref["recordId"], "packetSha256": self.packet_sha,
                "context": context})

    def tearDown(self):
        self.tmp.cleanup()

    def test_registration_cold_audit_and_claim_revision_invalidate_linkage(self):
        created = claim_evidence_registry.register(self.claim, [self.ref], [self.snapshot], {},
            current_subject_revision="fixture-revision-1")
        registered = created["registry"]
        self.assertEqual(registered["claimRevisionSha256"],
            claim_evidence_registry.claim_fingerprint(self.claim)[1])
        first = claim_evidence_registry.audit(registered, self.claim, [self.snapshot],
            current_subject_revision="fixture-revision-1")
        self.assertEqual(first["status"], "review-not-linked")
        self.assertEqual(first["evidenceStatus"], "current")
        revised = {**self.claim, "revision": 2,
            "text": "The synthetic paired packet proves answer quality."}
        stale = claim_evidence_registry.audit(registered, revised, [self.snapshot],
            current_subject_revision="fixture-revision-1")
        self.assertEqual(stale["status"], "stale-claim-revision")
        self.assertFalse(stale["claimMatches"])
        self.assertFalse(stale["proofInherited"])
        new_record = claim_evidence_registry.revise(registered, revised, [self.ref], [self.snapshot], {},
            current_subject_revision="fixture-revision-1")
        self.assertEqual(new_record["registry"]["claimRevisionSha256"], stale["currentClaimRevisionSha256"])
        self.assertEqual(new_record["audit"]["reviewStatus"], "not-linked")

    def test_missing_or_changed_evidence_stays_explicit_and_unknown(self):
        registered = claim_evidence_registry.registry(self.claim, [self.ref], {})
        missing = claim_evidence_registry.audit(registered, self.claim, [None],
            current_subject_revision="fixture-revision-1")
        self.assertEqual(missing["status"], "evidence-missing")
        unknown = claim_evidence_registry.audit(registered, self.claim, [{"state": "unavailable"}],
            current_subject_revision="fixture-revision-1")
        self.assertEqual(unknown["status"], "evidence-unknown")
        self.assertIsNone(unknown["evidence"][0]["nativeValid"])
        changed = {**self.snapshot, "packetJson": self.packet_json.replace("baseline", "changed")}
        invalid = claim_evidence_registry.audit(registered, self.claim, [changed],
            current_subject_revision="fixture-revision-1")
        self.assertEqual(invalid["evidenceStatus"], "unknown")
        self.assertFalse(invalid["evidence"][0]["nativeValid"])

    def test_exact_blind_review_links_are_cold_checked_and_never_inherited(self):
        source_snapshot = {**self.snapshot, "recordId": self.source_ref["recordId"],
            "context": self.source_context}
        registered = claim_evidence_registry.register(self.claim, [self.source_ref], [source_snapshot],
            self.review_refs, self.session_snapshot, self.review_snapshots, "fixture-revision-1")
        self.assertEqual(registered["audit"]["reviewStatus"], "current")
        revised = {**self.claim, "revision": 2, "text": "The claim now says something else."}
        with self.assertRaises(claim_evidence_registry.RegistryError):
            claim_evidence_registry.revise(registered["registry"], revised, [self.source_ref], [source_snapshot],
                self.review_refs, self.session_snapshot, self.review_snapshots, "fixture-revision-1")
        missing = claim_evidence_registry.audit(registered["registry"], self.claim, [source_snapshot],
            None, [None, None], "fixture-revision-1")
        self.assertEqual(missing["reviewStatus"], "missing")

    def test_native_paired_comparison_packet_status_uses_selected_pair_receipts(self):
        review = {
            "format": paired_comparison.REVIEW_FORMAT,
            "author": "Synthetic registry reviewer",
            "pair_selection_rationale": "Select the exact baseline and candidate attempts.",
            "scope_review": "Synthetic retrieval fixture only.",
            "uncertainty_review": "No real result is implied.",
            "resource_metrics": [],
            "decision_owner_questions": ["Does this packet stay structurally comparable?"],
        }
        selection = [{"pair_id": "registry-pair", "baseline_attempt_id": "baseline-1",
                      "candidate_attempt_id": "candidate-1"}]
        packet = paired_comparison.create_packet(self.store, self.plan_sha, selection, review)
        ref = {"recordId": "77777777-7777-4777-8777-777777777777",
               "packetSha256": packet["packet_sha256"], "artifactKind": "paired-comparison-evidence"}
        snapshot = {"recordId": ref["recordId"], "packetSha256": ref["packetSha256"],
                    "packetJson": trials.canonical(packet).decode("utf-8")}
        result = claim_evidence_registry.register(self.claim, [ref], [snapshot],
            current_subject_revision="fixture-revision-1")
        self.assertEqual(result["audit"]["evidence"][0]["status"], "completed")
        self.assertEqual(result["audit"]["evidenceStatus"], "current")

        partial = build_attempt(self.plan, "candidate-partial", "candidate",
                                self.packet["comparison_input"]["arms"][1]["rankings"], status="partial")
        trials.seal_attempt(self.store, self.plan_sha, partial, sealed_at="synthetic")
        selection[0]["pair_id"] = "registry-partial-pair"
        selection[0]["candidate_attempt_id"] = "candidate-partial"
        partial_packet = paired_comparison.create_packet(self.store, self.plan_sha, selection, review)
        partial_ref = {**ref, "recordId": "88888888-8888-4888-8888-888888888888",
                       "packetSha256": partial_packet["packet_sha256"]}
        partial_snapshot = {"recordId": partial_ref["recordId"],
                            "packetSha256": partial_ref["packetSha256"],
                            "packetJson": trials.canonical(partial_packet).decode("utf-8")}
        partial_registry = claim_evidence_registry.registry(self.claim, [partial_ref], {})
        partial_audit = claim_evidence_registry.audit(partial_registry, self.claim, [partial_snapshot],
            current_subject_revision="fixture-revision-1")
        self.assertEqual(partial_audit["evidence"][0]["status"], "incomplete")
        self.assertEqual(partial_audit["evidenceStatus"], "incomplete")

    def test_stdin_cli_is_closed_and_returns_same_fingerprint(self):
        request = {"schema": claim_evidence_registry.SCHEMA, "operation": "register",
            "input": {"claim": self.claim, "evidence": [self.ref], "packetSnapshots": [self.snapshot],
                      "review": {}, "currentSubjectRevision": "fixture-revision-1"}}
        result = subprocess.run([sys.executable, "-B", "-m", "harness.claim_evidence_registry"],
            cwd=Path(__file__).parents[1], input=json.dumps(request), text=True,
            capture_output=True, check=True, timeout=10)
        response = json.loads(result.stdout)
        self.assertEqual(response["schema"], "sandbox.claim-evidence-result.v1")
        registered = response["result"]["registry"]
        self.assertEqual(registered["claimRevisionSha256"],
            claim_evidence_registry.claim_fingerprint(self.claim)[1])
        audit_request = {"schema": claim_evidence_registry.SCHEMA, "operation": "audit",
            "input": {"registry": registered, "currentClaim": self.claim,
                      "packetSnapshots": [self.snapshot],
                      "currentSubjectRevision": "fixture-revision-1"}}
        audited = subprocess.run([sys.executable, "-B", "-m", "harness.claim_evidence_registry"],
            cwd=Path(__file__).parents[1], input=json.dumps(audit_request), text=True,
            capture_output=True, check=True, timeout=10)
        self.assertEqual(json.loads(audited.stdout)["result"]["status"], "review-not-linked")
        request["surprise"] = True
        refused = subprocess.run([sys.executable, "-B", "-m", "harness.claim_evidence_registry"],
            cwd=Path(__file__).parents[1], input=json.dumps(request), text=True,
            capture_output=True, timeout=10)
        self.assertNotEqual(refused.returncode, 0)


if __name__ == "__main__":
    unittest.main()
