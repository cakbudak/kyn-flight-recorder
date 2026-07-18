from __future__ import annotations

import json
import unittest

from scripts.gpt56_review import (
    REVIEW_SCHEMA,
    ReviewError,
    build_evidence,
    build_request,
    build_review_packet,
    canonical_json,
    extract_output_text,
    load_fixture,
    validate_review,
)


VALID_REVIEW = {
    "verdict": "supported",
    "confidence_percent": 94,
    "supported_claims": ["The approval gate blocks the effect."],
    "unsupported_claims": [],
    "risks": ["The transition is a local simulation."],
    "suggested_copy": "The synthetic run is waiting for approval.",
}


class Gpt56EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture()
        cls.packet = build_review_packet(cls.fixture)
        cls.request = build_request(cls.packet)

    def test_packet_is_allow_listed_and_contains_causal_guardrail(self) -> None:
        serialized = canonical_json(self.packet)
        for forbidden in (
            "connector_credential",
            "claim_token",
            "SYNTHETIC_VALUE",
            "REDACTED_BY_FIXTURE",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)
        self.assertIn("lease_remaining_seconds", serialized)
        self.assertIn("approval_gate", serialized)
        self.assertIn("external_effect", serialized)

    def test_request_uses_gpt56_responses_structured_output_without_storage(self) -> None:
        self.assertEqual(self.request["model"], "gpt-5.6")
        self.assertEqual(self.request["reasoning"], {"effort": "low"})
        self.assertFalse(self.request["store"])
        output_format = self.request["text"]["format"]
        self.assertEqual(output_format["type"], "json_schema")
        self.assertTrue(output_format["strict"])
        self.assertEqual(output_format["schema"], REVIEW_SCHEMA)

    def test_non_gpt56_model_fails_closed(self) -> None:
        with self.assertRaisesRegex(ReviewError, "only accepts a GPT-5.6"):
            build_request(self.packet, "gpt-5")

    def test_review_validation_accepts_exact_contract(self) -> None:
        self.assertEqual(validate_review(VALID_REVIEW.copy()), VALID_REVIEW)

    def test_review_validation_rejects_extra_fields_and_bad_confidence(self) -> None:
        with self.assertRaisesRegex(ReviewError, "fields"):
            validate_review({**VALID_REVIEW, "hidden": "value"})
        with self.assertRaisesRegex(ReviewError, "confidence"):
            validate_review({**VALID_REVIEW, "confidence_percent": 101})
        with self.assertRaisesRegex(ReviewError, "confidence"):
            validate_review({**VALID_REVIEW, "confidence_percent": True})

    def test_output_extraction_handles_response_and_refusal(self) -> None:
        response = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(VALID_REVIEW)}],
                }
            ],
        }
        self.assertEqual(json.loads(extract_output_text(response)), VALID_REVIEW)
        with self.assertRaisesRegex(ReviewError, "refused"):
            extract_output_text(
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "refusal", "refusal": "no"}],
                        }
                    ],
                }
            )

    def test_sanitized_evidence_keeps_provenance_not_raw_payloads(self) -> None:
        response = {
            "id": "resp_test",
            "model": "gpt-5.6-sol",
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        }
        evidence = build_evidence(response, VALID_REVIEW, self.fixture, self.request)
        self.assertEqual(evidence["status"], "completed")
        self.assertEqual(evidence["response_id"], "resp_test")
        self.assertEqual(evidence["model_returned"], "gpt-5.6-sol")
        self.assertFalse(evidence["privacy"]["raw_response_persisted"])
        self.assertNotIn("input", evidence)
        self.assertNotIn("output", evidence)


if __name__ == "__main__":
    unittest.main()
