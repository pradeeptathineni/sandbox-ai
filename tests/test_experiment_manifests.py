import copy
import tempfile
import unittest
from pathlib import Path

from harness import experiment_manifests, trials
from test_trials import make_plan


class ExperimentManifestLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "existing-trial-store"
        self.first = trials.freeze_plan(self.store, make_plan())
        other = make_plan(plan_id="synthetic-candidate-plan")
        self.second = trials.freeze_plan(self.store, other)
        self.input = {
            "title": "Two-system fixture",
            "decision": {
                "question": "Does equipment change the review?",
                "owner": "example owner",
                "consumer": "example",
            },
            "systems": [
                {
                    "system_id": "baseline-system",
                    "plan_sha256": self.first,
                    "arm": "baseline",
                    "role": "baseline",
                },
                {
                    "system_id": "candidate-system",
                    "plan_sha256": self.second,
                    "arm": "equipped",
                    "role": "candidate",
                },
            ],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_freeze_cold_reopen_and_exact_request_recovery(self):
        before = {
            digest: (self.store / "plans" / f"{digest}.json").read_bytes()
            for digest in (self.first, self.second)
        }
        request_id = "11111111-1111-4111-8111-111111111111"
        digest = experiment_manifests.freeze_manifest(self.store, request_id, self.input, frozen_at="2026-10-28T00:00:00+00:00")
        reopened = experiment_manifests.show_manifest(self.store, digest)
        manifest = reopened["manifest"]
        self.assertEqual(manifest["format"], experiment_manifests.FORMAT)
        self.assertEqual(manifest["request_id"], request_id)
        self.assertEqual([row["system_id"] for row in reopened["systems"]], ["baseline-system", "candidate-system"])
        self.assertEqual(reopened["scope_overlap"]["common_case_ids"], ["review"])
        self.assertEqual(reopened["execution"]["status"], "blocked")
        self.assertIn("no execution capability", reopened["execution"]["reason"])
        self.assertEqual(
            experiment_manifests.freeze_manifest(self.store, request_id, self.input),
            digest,
            "same request and input resolve to the original immutable manifest",
        )
        recovery = experiment_manifests.recover_request(self.store, request_id, manifest["input_sha256"])
        self.assertEqual(recovery["state"], "confirmed")
        self.assertEqual(recovery["manifest_sha256"], digest)
        self.assertEqual(experiment_manifests.recover_request(self.store, "22222222-2222-4222-8222-222222222222", "0" * 64)["state"], "absent")
        self.assertEqual(experiment_manifests.list_manifests(self.store)["manifests"][0]["manifest_sha256"], digest)
        self.assertEqual(
            {key: (self.store / "plans" / f"{key}.json").read_bytes() for key in before},
            before,
        )
        self.assertFalse((self.store / "attempts").exists(), "manifest planning must never seal or execute attempts")

    def test_missing_authority_stays_blocked_and_missing_plan_refuses(self):
        incomplete = make_plan(plan_id="missing-authority")
        incomplete["authority"]["authorized_by"] = ""
        incomplete_sha = trials.freeze_plan(self.store, incomplete)
        manifest_input = copy.deepcopy(self.input)
        manifest_input["systems"][1]["plan_sha256"] = incomplete_sha
        digest = experiment_manifests.freeze_manifest(
            self.store,
            "33333333-3333-4333-8333-333333333333",
            manifest_input,
        )
        reopened = experiment_manifests.show_manifest(self.store, digest)
        self.assertEqual(reopened["execution"]["status"], "blocked")
        self.assertEqual(reopened["systems"][1]["authority_review"]["status"], "incomplete")
        self.assertIn("authorized_by", reopened["systems"][1]["authority_review"]["missing_fields"])

        missing = copy.deepcopy(self.input)
        missing["systems"][1]["plan_sha256"] = "f" * 64
        with self.assertRaisesRegex(experiment_manifests.ManifestError, "missing or invalid"):
            experiment_manifests.freeze_manifest(
                self.store,
                "44444444-4444-4444-8444-444444444444",
                missing,
            )
        self.assertFalse(experiment_manifests._manifest_by_request(self.store, "44444444-4444-4444-8444-444444444444"))

    def test_request_collision_and_missing_plan_on_reopen_refuse(self):
        request_id = "55555555-5555-4555-8555-555555555555"
        digest = experiment_manifests.freeze_manifest(self.store, request_id, self.input, frozen_at="2026-10-28T00:00:00+00:00")
        changed = copy.deepcopy(self.input)
        changed["title"] = "Different content"
        with self.assertRaisesRegex(experiment_manifests.ManifestError, "different manifest input"):
            experiment_manifests.freeze_manifest(self.store, request_id, changed)

        (self.store / "plans" / f"{self.second}.json").unlink()
        with self.assertRaisesRegex(experiment_manifests.ManifestError, "missing or invalid"):
            experiment_manifests.show_manifest(self.store, digest)


if __name__ == "__main__":
    unittest.main()
