from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.tools import manage_observability_evidence as evidence


class ObservabilityEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = self.root / "ledger"
        self.summary = self.root / "raw-summary.json"
        self.summary.write_text('{"overall_pass": true}\n', encoding="utf-8")
        self.deployment = self.root / "deployment.json"
        self.deployment.write_text(
            json.dumps(
                {
                    "deployment_id": "v3.6.2-test",
                    "tag": "v3.6.2",
                    "commit": "a" * 40,
                    "runtime_asset_sha256": "b" * 64,
                    "image_ids": {"GATEWAY_IMAGE_ID": "sha256:" + "c" * 64},
                    "configuration_sha256": "d" * 64,
                    "host": {"host_id_sha256": "e" * 64},
                    "operator": {"name": "installer", "uid": 1000},
                    "result": {"overall_pass": True, "status": "installed"},
                }
            ),
            encoding="utf-8",
        )
        self.identity = {
            "host": {"host_id_sha256": "e" * 64},
            "operator": {"name": "operator", "uid": 1000},
        }

    def _record(
        self, kind: str, record_id: str, attributes: dict[str, object]
    ) -> tuple[Path, dict[str, object]]:
        return evidence.create_record(
            self.ledger,
            kind,
            record_id,
            [self.summary],
            self.deployment,
            attributes=attributes,
            identity=self.identity,
        )

    def test_record_is_immutable_and_binds_raw_summary_and_deployment(self) -> None:
        path, record = self._record(
            "daily", "2026-07-26", {"checkpoint_date": "2026-07-26"}
        )

        self.assertTrue(path.is_file())
        self.assertEqual(record["deployment"]["tag"], "v3.6.2")
        self.assertEqual(
            record["raw_summaries"][0]["sha256"],
            evidence.sha256_file(self.summary),
        )
        snapshot = Path(record["raw_summaries"][0]["path"])
        self.assertEqual(snapshot.parent, (self.ledger / "raw").resolve())
        self.assertEqual(
            record["raw_summaries"][0]["source_path"],
            str(self.summary.resolve()),
        )
        self.assertFalse(record["formal_30_day_claim"])
        with self.assertRaisesRegex(evidence.EvidenceError, "cannot be overwritten"):
            self._record(
                "daily", "2026-07-26", {"checkpoint_date": "2026-07-26"}
            )

    def test_rejects_secret_like_attributes(self) -> None:
        with self.assertRaisesRegex(evidence.EvidenceError, "secret-like"):
            self._record(
                "incident",
                "incident-1",
                {
                    "title": "test",
                    "severity": "warning",
                    "started_at": "2026-07-26T00:00:00Z",
                    "status": "resolved",
                    "webhook_url": "do-not-record",
                },
            )

    def test_manifest_uses_snapshot_after_source_changes(self) -> None:
        self._record("daily", "day-1", {"checkpoint_date": "2026-07-26"})
        self.summary.write_text('{"overall_pass": false}\n', encoding="utf-8")

        _, manifest = evidence.build_manifest(
            self.ledger, "manifest-1", identity=self.identity
        )

        self.assertEqual(manifest["entry_count"], 2)

    def test_seal_legacy_record_preserves_summary_before_source_changes(self) -> None:
        record_path = self.ledger / "records" / "daily" / "day-1.json"
        record_path.parent.mkdir(parents=True)
        reference = evidence.file_reference(self.summary, "raw-summary-1")
        record_path.write_text(
            json.dumps({"raw_summaries": [reference]}), encoding="utf-8"
        )

        result = evidence.seal_legacy_records(self.ledger)
        self.summary.write_text('{"overall_pass": false}\n', encoding="utf-8")
        _, manifest = evidence.build_manifest(
            self.ledger, "manifest-1", identity=self.identity
        )

        self.assertEqual(result["sealed_count"], 1)
        self.assertEqual(manifest["entry_count"], 2)

    def test_manifest_rejects_snapshot_drift(self) -> None:
        _, record = self._record(
            "daily", "day-1", {"checkpoint_date": "2026-07-26"}
        )
        Path(record["raw_summaries"][0]["path"]).write_text(
            '{"overall_pass": false}\n', encoding="utf-8"
        )

        with self.assertRaisesRegex(evidence.EvidenceError, "drifted"):
            evidence.build_manifest(
                self.ledger, "manifest-1", identity=self.identity
            )

    def test_package_contains_manifest_sums_records_and_raw_summaries(self) -> None:
        self._record("daily", "day-1", {"checkpoint_date": "2026-07-26"})
        self._record(
            "weekly",
            "week-1",
            {"period_start": "2026-07-20", "period_end": "2026-07-26"},
        )
        manifest_path, manifest = evidence.build_manifest(
            self.ledger, "manifest-1", identity=self.identity
        )
        package = self.root / "off-host-evidence.tar.gz"

        result = evidence.package_manifest(manifest_path, package)

        self.assertEqual(result["entry_count"], manifest["entry_count"])
        self.assertFalse(result["off_host_copy_verified"])
        extraction = self.root / "extracted"
        extraction.mkdir()
        with tarfile.open(package, "r:gz") as archive:
            archive.extractall(extraction, filter="data")
        sums = (extraction / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(line.endswith("  manifest.json") for line in sums))
        for line in sums:
            expected, relative = line.split("  ", 1)
            self.assertEqual(evidence.sha256_file(extraction / relative), expected)


if __name__ == "__main__":
    unittest.main()
