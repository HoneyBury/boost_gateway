import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scripts.gates.production.check_production_evidence_manifest import check_evidence
from scripts.gates.production.run_long_soak_capacity import attach_provenance


class LongSoakEvidenceTest(unittest.TestCase):
    def test_attach_provenance_updates_completed_long_soak_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            summary_path = Path(temp) / "long-soak-2h-summary.json"
            summary_path.write_text(
                json.dumps({"summary_version": 2, "overall_pass": True}),
                encoding="utf-8",
            )
            provenance = {"candidate_revision": "a" * 40, "workflow": "contract-test"}

            attach_provenance(summary_path, provenance)

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertIs(summary["overall_pass"], True)
            self.assertEqual(summary["provenance"], provenance)

    def test_manifest_accepts_two_hour_summary_independently_of_failed_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            validation = root / "runtime" / "validation"
            validation.mkdir(parents=True)
            generated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            (validation / "long-soak-2h-summary.json").write_text(
                json.dumps(
                    {
                        "summary_version": 2,
                        "generated_at": generated_at,
                        "overall_pass": True,
                        "passed": True,
                        "soak_profile": "long",
                        "artifacts": {},
                    }
                ),
                encoding="utf-8",
            )
            (validation / "long-soak-capacity-summary.json").write_text(
                json.dumps({"summary_version": 2, "overall_pass": False, "passed": False}),
                encoding="utf-8",
            )
            item = {
                "id": "long_soak_2h",
                "category": "long_soak_capacity",
                "path": "runtime/validation/long-soak-2h-summary.json",
                "fixed_runner_required": True,
                "freshness_hours": 336,
                "required_json_values": {"soak_profile": "long"},
            }

            result = check_evidence(
                item,
                root,
                datetime.now(UTC),
                True,
                {},
                "",
            )

            self.assertIs(result["passed"], True)
            self.assertEqual(result["status"], "passed")


if __name__ == "__main__":
    unittest.main()
