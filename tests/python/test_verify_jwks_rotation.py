from __future__ import annotations

import unittest
from pathlib import Path

from scripts.gates.security.verify_jwks_rotation import (
    finalize_summary,
    validate_probe_summary,
)


class VerifyJwksRotationTest(unittest.TestCase):
    def test_probe_summary_contract_accepts_redacted_evidence(self) -> None:
        payload = {
            "probe_summary_version": 1,
            "overall_pass": True,
            "passed": True,
            "checks": [
                {"name": "overlap:new-token-accepted", "passed": True, "detail": "accepted"}
            ],
            "phase_metrics": {
                "overlap": {
                    "key_count": 2,
                    "snapshot_available": True,
                    "snapshot_stale": False,
                }
            },
        }

        self.assertEqual(validate_probe_summary(payload), [])

    def test_probe_summary_contract_rejects_sensitive_material(self) -> None:
        payload = {
            "probe_summary_version": 1,
            "overall_pass": True,
            "passed": True,
            "checks": [{"name": "unsafe", "passed": True, "detail": "accepted"}],
            "token": "header.payload.signature",
            "nested": {"value": "-----BEGIN PRIVATE KEY-----"},
        }

        errors = validate_probe_summary(payload)

        self.assertTrue(any("forbidden field: token" in error for error in errors))
        self.assertTrue(any("private key material" in error for error in errors))

    def test_probe_summary_contract_rejects_mismatched_pass_fields(self) -> None:
        payload = {
            "probe_summary_version": 1,
            "overall_pass": True,
            "passed": False,
            "checks": [{"name": "phase", "passed": False, "detail": "failed"}],
        }

        self.assertIn("probe pass fields differ", validate_probe_summary(payload))

    def test_outer_summary_fails_closed_and_keeps_provenance(self) -> None:
        provenance = {
            "candidate_revision": "a" * 40,
            "git_commit": "a" * 40,
            "revision_matches_checkout": True,
        }
        summary = finalize_summary(
            Path("runtime/validation/jwks-rotation-summary.json"),
            [{"name": "phase", "passed": False, "detail": "expected failure"}],
            {},
            {"request_count": 0, "status_counts": {}},
            provenance,
        )

        self.assertFalse(summary["overall_pass"])
        self.assertFalse(summary["passed"])
        self.assertEqual(summary["failed_step"], "phase")
        self.assertIs(summary["provenance"], provenance)
        self.assertFalse(summary["sensitive_material"]["persisted_in_summary_or_artifact"])


if __name__ == "__main__":
    unittest.main()
