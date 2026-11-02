import unittest
import copy
import json
import tempfile
from pathlib import Path
import subprocess
import sys

from harness import grounding_review, retrieval_experiment, trials
from test_retrieval_experiment import (
    FIXTURE, build_plan, build_attempt, build_grounding_judgments,
)


class GroundingReviewOperations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "store"
        self.plan = build_plan()
        self.plan_sha = trials.freeze_plan(self.store, self.plan)
        self.comparison = json.loads(FIXTURE.read_text())
        self.comparison["arms"][0]["strategy"] = "baseline"
        self.comparison["arms"][1]["strategy"] = "candidate"
        self.comparison["arms"][0]["provenance"]["producer"] = "synthetic baseline"
        self.comparison["arms"][1]["provenance"]["producer"] = "synthetic candidate"
        base_rankings = copy.deepcopy(self.comparison["arms"][0]["rankings"])
        candidate_rankings = copy.deepcopy(self.comparison["arms"][1]["rankings"])
        trials.seal_attempt(self.store, self.plan_sha,
            build_attempt(self.plan, "baseline-1", "baseline", base_rankings), sealed_at="t")
        trials.seal_attempt(self.store, self.plan_sha,
            build_attempt(self.plan, "candidate-1", "candidate", candidate_rankings), sealed_at="t")
        self.packet = retrieval_experiment.create_packet(
            self.store, self.plan_sha, "baseline-1", "candidate-1",
            self.comparison, build_grounding_judgments(self.comparison),
        )
        self.packet_json = retrieval_experiment.render_packet(self.packet, "json")
        self.aliases = [
            {"alias": "A", "strategy": "baseline"},
            {"alias": "B", "strategy": "candidate"},
        ]
        observations = self.packet["grounding_evaluation"]["judgments"]["observations"]
        self.study_rows = [
            {"strategy": row["strategy"], "questionId": row["question_id"],
             "answerText": f"Synthetic {row['strategy']} response", "evidenceExcerpts": ["Synthetic quote"]}
            for row in observations if row["answer_status"] == "answered"
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def test_blinded_projection_preserves_statuses_and_masks_arm_names(self):
        view = grounding_review.session_view({
            "packetJson": self.packet_json, "aliases": self.aliases, "studyRows": self.study_rows,
        })["view"]
        self.assertEqual(len(view), 6)
        self.assertEqual({row["answerStatus"] for row in view}, {"answered", "missing", "abstained", "unknown"})
        self.assertIn("arm A", next(row["answer"] for row in view if row["armAlias"] == "A" and row["answer"] is not None))
        self.assertNotIn("baseline", str(view))
        self.assertNotIn("candidate", str(view))
        with self.assertRaises(ValueError):
            altered = json.loads(self.packet_json)
            altered["packet_sha256"] = "0" * 64
            grounding_review.session_view({"packetJson": json.dumps(altered),
                                            "aliases": self.aliases, "studyRows": self.study_rows})

    def test_closed_ratings_and_descriptive_paired_calibration_keep_unknown(self):
        view = grounding_review.session_view({"packetJson": self.packet_json, "aliases": self.aliases,
                                               "studyRows": self.study_rows})["view"]
        keys = [{"armAlias": row["armAlias"], "questionId": row["questionId"]} for row in view]
        left = [{**key, "rating": "supported", "rationale": "Synthetic judgment"} for key in keys]
        right = [{**key, "rating": "uncertain" if index == 0 else "supported",
                  "rationale": "Synthetic independent judgment"} for index, key in enumerate(keys)]
        checked = grounding_review.review_check({"viewKeys": keys, "judgments": left})["judgments"]
        result = grounding_review.paired_compare({"rubric": grounding_review.RUBRIC,
            "aliases": self.aliases,
            "reviews": [{"reviewer": "Reviewer one", "judgments": checked},
                        {"reviewer": "Reviewer two", "judgments": right}]})
        self.assertEqual(result["counts"], {"agreement": 5, "disagreement": 0, "unknown": 1, "denominator": 6})
        self.assertEqual(sum(row["status"] == "unknown" for row in result["comparisons"]), 1)
        with self.assertRaises(ValueError):
            grounding_review.review_check({"viewKeys": keys, "judgments": left[:-1]})
        with self.assertRaises(ValueError):
            grounding_review.paired_compare({"rubric": {}, "aliases": self.aliases,
                "reviews": [{"reviewer": "same", "judgments": left},
                            {"reviewer": "SAME", "judgments": right}]})
        with self.assertRaises(ValueError):
            grounding_review.paired_compare({"rubric": {**grounding_review.RUBRIC, "revision": "2"},
                "aliases": self.aliases,
                "reviews": [{"reviewer": "Reviewer one", "judgments": left},
                            {"reviewer": "Reviewer two", "judgments": right}]})

    def test_stdin_cli_uses_the_same_closed_operations(self):
        request = {"schema": "sandbox.grounding-review-request.v1",
                   "operation": "session-view",
                   "input": {"packetJson": self.packet_json, "aliases": self.aliases,
                             "studyRows": self.study_rows}}
        completed = subprocess.run([sys.executable, "-B", "-m", "harness.grounding_review"],
            input=json.dumps(request), text=True, capture_output=True, check=True,
            cwd=Path(__file__).parents[1], timeout=10,
            env={"PATH": __import__("os").environ.get("PATH", "/usr/bin:/bin"),
                 "PYTHONDONTWRITEBYTECODE": "1"})
        result = json.loads(completed.stdout)
        self.assertEqual(result["schema"], "sandbox.grounding-review-result.v1")
        self.assertEqual(result["operation"], "session-view")
        self.assertEqual(len(result["result"]["view"]), 6)


if __name__ == "__main__":
    unittest.main()
