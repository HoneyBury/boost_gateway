from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.tools import schedule_observability_evidence as scheduler


class FakePrometheus:
    base_url = scheduler.DEFAULT_PROMETHEUS_URL

    def __init__(self, *, empty_signal: int | None = None) -> None:
        self.calls = 0
        self.empty_signal = empty_signal

    def query_range(
        self, expression: str, window: scheduler.EvidenceWindow
    ) -> list[dict[str, Any]]:
        index = self.calls
        self.calls += 1
        if index == self.empty_signal:
            return []
        values = []
        moment = window.start
        while moment < window.end:
            values.append([moment.timestamp(), "1"])
            moment += timedelta(seconds=window.step_seconds)
        if name := next(
            (item[0] for item in scheduler.QUERY_CATALOG if item[1] == expression), None
        ):
            if name == "target_availability":
                return [
                    {"metric": {"job": job}, "values": values}
                    for job in scheduler.EXPECTED_JOBS
                ]
            if name in {"container_cpu", "container_memory", "container_restarts"}:
                return [
                    {"metric": {"container": container}, "values": values}
                    for container in scheduler.EXPECTED_CONTAINERS
                ]
        return [{"metric": {"test": str(index)}, "values": values}]


class ObservabilityEvidenceSchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = self.root / "ledger"
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
            "operator": {"name": "systemd", "uid": 0},
        }

    def test_previous_daily_and_iso_week_windows_are_closed_utc_periods(self) -> None:
        observed = datetime(2027, 1, 4, 12, 30, tzinfo=UTC)

        daily = scheduler.previous_window("daily", observed)
        weekly = scheduler.previous_window("weekly", observed)

        self.assertEqual(daily.record_id, "daily-2027-01-03")
        self.assertEqual(scheduler.isoformat(daily.end), "2027-01-04T00:00:00Z")
        self.assertEqual(weekly.record_id, "weekly-2026-W53")
        self.assertEqual(scheduler.isoformat(weekly.start), "2026-12-28T00:00:00Z")
        self.assertEqual(scheduler.isoformat(weekly.end), "2027-01-04T00:00:00Z")

    def test_prometheus_origin_is_numeric_loopback_only(self) -> None:
        self.assertEqual(
            scheduler.validate_prometheus_url("http://127.0.0.1:9090/"),
            "http://127.0.0.1:9090",
        )
        self.assertEqual(
            scheduler.validate_prometheus_url("http://[::1]:9090"),
            "http://[::1]:9090",
        )
        for value in (
            "https://127.0.0.1:9090",
            "http://localhost:9090",
            "http://10.0.0.1:9090",
            "http://user:password@127.0.0.1:9090",
            "http://127.0.0.1:9090/api",
        ):
            with self.subTest(value=value), self.assertRaises(scheduler.SchedulerError):
                scheduler.validate_prometheus_url(value)

    def test_daily_record_is_create_only_and_retry_does_not_query(self) -> None:
        observed = datetime(2026, 7, 26, 13, 0, tzinfo=UTC)
        prometheus = FakePrometheus()

        first = scheduler.run_scheduler(
            "daily",
            self.ledger,
            self.deployment,
            prometheus,  # type: ignore[arg-type]
            observed_at=observed,
            identity=self.identity,
        )
        calls = prometheus.calls
        second = scheduler.run_scheduler(
            "daily",
            self.ledger,
            self.deployment,
            prometheus,  # type: ignore[arg-type]
            observed_at=observed,
            identity=self.identity,
        )

        self.assertEqual(first["status"], "recorded")
        self.assertEqual(second["status"], "already_recorded")
        self.assertEqual(prometheus.calls, calls)
        report = json.loads(Path(first["report"]).read_text(encoding="utf-8"))
        record = json.loads(Path(first["record"]).read_text(encoding="utf-8"))
        self.assertFalse(report["formal_30_day_claim"])
        self.assertFalse(report["claims"]["availability_slo_proven"])
        self.assertEqual(record["attributes"]["checkpoint_date"], "2026-07-25")
        self.assertFalse(record["attributes"]["formal_claim"])
        self.assertEqual(first["gap_count"], 0)

    def test_missing_signal_is_an_explicit_gap_but_record_is_created(self) -> None:
        result = scheduler.run_scheduler(
            "weekly",
            self.ledger,
            self.deployment,
            FakePrometheus(empty_signal=2),  # type: ignore[arg-type]
            observed_at=datetime(2026, 7, 27, 1, 0, tzinfo=UTC),
            identity=self.identity,
        )

        report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        record = json.loads(Path(result["record"]).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "recorded")
        self.assertFalse(report["coverage_complete"])
        self.assertIn("no_series", {gap["reason"] for gap in report["gaps"]})
        self.assertEqual(record["attributes"]["period_start"], "2026-07-20")
        self.assertEqual(record["attributes"]["period_end"], "2026-07-26")
        self.assertEqual(report["expected_daily_record_count"], 7)
        self.assertEqual(
            sum(
                gap["reason"] == "missing_or_invalid_daily_record"
                for gap in report["gaps"]
            ),
            7,
        )

    def test_query_failure_is_recorded_as_a_gap(self) -> None:
        prometheus = FakePrometheus()
        prometheus.query_range = lambda *_args: (_ for _ in ()).throw(
            scheduler.SchedulerError("connection refused")
        )

        result = scheduler.run_scheduler(
            "daily",
            self.ledger,
            self.deployment,
            prometheus,  # type: ignore[arg-type]
            observed_at=datetime(2026, 7, 26, 13, 0, tzinfo=UTC),
            identity=self.identity,
        )

        report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        self.assertFalse(report["overall_pass"])
        self.assertIn("query_error", {gap["reason"] for gap in report["gaps"]})

    def test_deployment_change_during_collection_is_rejected(self) -> None:
        replacement = self.root / "replacement.json"
        replacement.write_text(self.deployment.read_text(encoding="utf-8"), encoding="utf-8")
        active = self.root / "active.json"
        active.symlink_to(self.deployment)
        prometheus = FakePrometheus()
        original = prometheus.query_range

        def switch_deployment(
            expression: str, window: scheduler.EvidenceWindow
        ) -> list[dict[str, Any]]:
            if prometheus.calls == 0:
                active.unlink()
                active.symlink_to(replacement)
            return original(expression, window)

        prometheus.query_range = switch_deployment  # type: ignore[method-assign]
        with self.assertRaisesRegex(scheduler.SchedulerError, "changed during collection"):
            scheduler.run_scheduler(
                "daily",
                self.ledger,
                active,
                prometheus,  # type: ignore[arg-type]
                observed_at=datetime(2026, 7, 26, 13, 0, tzinfo=UTC),
                identity=self.identity,
            )
        self.assertFalse((self.ledger / "reports").exists())

    def test_unit_is_loopback_only_and_has_no_secret_or_docker_access(self) -> None:
        unit = (
            Path(__file__).resolve().parents[2]
            / "deploy/systemd/boost-gateway-observability-evidence@.service"
        ).read_text(encoding="utf-8")

        self.assertIn("IPAddressDeny=any", unit)
        self.assertIn("IPAddressAllow=localhost", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", unit)
        self.assertIn("InaccessiblePaths=/etc/boost-gateway /run/docker.sock", unit)
        self.assertNotIn("EnvironmentFile=", unit)
        self.assertNotIn("/usr/bin/docker", unit)


if __name__ == "__main__":
    unittest.main()
