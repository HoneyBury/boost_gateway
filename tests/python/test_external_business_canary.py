from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.tools import external_business_canary as canary


class FakeClient:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.logged_in_user: str | None = None

    def connect(self, host: str, port: int, timeout: int) -> bool:
        self.state["connects"] = self.state.get("connects", 0) + 1
        return not self.state.get("connect_failure", False)

    def disconnect(self) -> None:
        self.state["disconnects"] = self.state.get("disconnects", 0) + 1

    def login(self, user: str, token: str, timeout: int) -> dict[str, Any]:
        self.state.setdefault("credentials", []).append((user, token))
        if self.state.get("login_failure"):
            return {"ok": False, "error_code": 401}
        self.logged_in_user = user
        return {"ok": True, "user_id": user, "error_code": 0}

    def create_room(self, room: str, timeout: int) -> dict[str, Any]:
        self.state["room"] = room
        return {"ok": True, "room_id": room}

    def join_room(self, room: str, timeout: int) -> dict[str, Any]:
        return {"ok": True}

    def leave_room(self, room: str, timeout: int) -> dict[str, Any]:
        self.state["leaves"] = self.state.get("leaves", 0) + 1
        return {"ok": True}

    def set_ready(self, ready: bool, timeout: int) -> dict[str, Any]:
        return {"ok": True}

    def start_battle(self, room: str, timeout: int) -> dict[str, Any]:
        if self.state.get("battle_failure"):
            return {"ok": False, "error_code": 503}
        return {"ok": True, "battle_id": "battle"}

    def send_battle_input(self, value: str, timeout: int) -> dict[str, Any]:
        self.state.setdefault("inputs", []).append(value)
        return {"ok": True}

    def leaderboard_submit(
        self, user: str, display_name: str, score: int, timeout: int
    ) -> dict[str, Any]:
        if user != self.logged_in_user:
            return {"ok": False, "error_code": 1001, "body": "{}"}
        self.state.setdefault("leaderboard_users", set()).add(user)
        return {"ok": True, "error_code": 0, "body": "{}"}

    def leaderboard_top(self, k: int, timeout: int) -> dict[str, Any]:
        return {"ok": True, "error_code": 0, "body": '{"entries":[]}'}

    def leaderboard_rank(self, user: str, timeout: int) -> dict[str, Any]:
        return {"ok": True, "error_code": 0, "body": json.dumps({"user_id": user})}


class FakeResponse:
    status = 200

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self, _: int) -> bytes:
        return b""


class ExternalBusinessCanaryTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.evidence = self.root / "evidence"
        self.deployment = self.root / "deployment.json"
        self.deployment.write_text(
            json.dumps(
                {
                    "deployment_id": "v3.6.2-candidate",
                    "tag": "v3.6.2",
                    "commit": "a" * 40,
                    "runtime_asset_sha256": "b" * 64,
                    "image_ids": {"GATEWAY_IMAGE_ID": "sha256:" + "c" * 64},
                    "host": {"host_id_sha256": "d" * 64},
                }
            ),
            encoding="utf-8",
        )
        self.config = canary.CanaryConfig(
            host="100.65.71.117",
            port=9201,
            user_a="fixed_canary_a",
            user_b="fixed_canary_b",
            token_a="highly-secret-token-a",
            token_b="highly-secret-token-b",
            alertmanager_url="http://127.0.0.1:19093",
            timeout_ms=5000,
        )

    def factory(self, state: dict[str, Any]):
        return lambda: FakeClient(state)

    def test_full_flow_has_required_typed_steps_and_bounded_identities(self) -> None:
        state: dict[str, Any] = {}
        steps = canary.execute_business_flow(
            self.config,
            self.factory(state),
            sleep=lambda _: None,
            sample_suffix="sample123",
        )

        self.assertEqual(list(canary.REQUIRED_STEPS), [step["name"] for step in steps])
        self.assertTrue(all(step["ok"] for step in steps))
        self.assertTrue(all(step["error_type"] == "none" for step in steps))
        self.assertEqual(
            {"fixed_canary_a", "fixed_canary_b"}, state["leaderboard_users"]
        )
        self.assertEqual("canary_sample123", state["room"])
        self.assertIn("finish:surrender", state["inputs"])
        self.assertEqual(3, state["connects"])
        self.assertEqual(2, state["leaves"])
        self.assertEqual(2, len({user for user, _ in state["credentials"]}))

    def test_external_host_validation_rejects_the_production_machine(self) -> None:
        machine_id = self.root / "machine-id"
        machine_id.write_text("external-machine-id\n", encoding="utf-8")
        boundary = canary.validate_external_host(self.deployment, machine_id)
        self.assertNotEqual(
            boundary["production_host_id_sha256"],
            boundary["canary_host_id_sha256"],
        )

        record = json.loads(self.deployment.read_text(encoding="utf-8"))
        record["host"]["host_id_sha256"] = canary.hashlib.sha256(
            machine_id.read_bytes()
        ).hexdigest()
        self.deployment.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(canary.CanaryError, "outside"):
            canary.validate_external_host(self.deployment, machine_id)

    def test_endpoint_validation_rejects_credentials_and_non_host_input(self) -> None:
        with self.assertRaises(canary.CanaryError):
            canary.validate_alertmanager_url("http://user:secret@127.0.0.1:9093")
        with self.assertRaises(canary.CanaryError):
            canary.validate_alertmanager_url("http://[invalid")
        invalid = canary.CanaryConfig(
            **{**self.config.__dict__, "host": "tcp://100.65.71.117"}
        )
        with self.assertRaises(canary.CanaryError):
            canary.validate_config(invalid)

    def test_environment_file_is_literal_private_and_allowlisted(self) -> None:
        environment_file = self.root / "environment"
        values = {
            "BOOST_GATEWAY_CANARY_HOST": self.config.host,
            "BOOST_GATEWAY_CANARY_USER_A": self.config.user_a,
            "BOOST_GATEWAY_CANARY_USER_B": self.config.user_b,
            "BOOST_GATEWAY_CANARY_TOKEN_A": "$TOKEN_A_IS_LITERAL",
            "BOOST_GATEWAY_CANARY_TOKEN_B": self.config.token_b,
            "BOOST_GATEWAY_CANARY_ALERTMANAGER_URL": self.config.alertmanager_url,
        }
        environment_file.write_text(
            "\n".join(f"{name}={value}" for name, value in values.items()) + "\n",
            encoding="utf-8",
        )
        environment_file.chmod(0o600)

        loaded = canary.load_environment_file(environment_file)
        config = canary.config_from_mapping(loaded)

        self.assertEqual("$TOKEN_A_IS_LITERAL", config.token_a)
        environment_file.chmod(0o644)
        with self.assertRaisesRegex(canary.CanaryError, "0600"):
            canary.load_environment_file(environment_file)

    def test_environment_file_rejects_unknown_duplicate_and_symlink(self) -> None:
        environment_file = self.root / "environment"
        environment_file.write_text("UNEXPECTED=value\n", encoding="utf-8")
        environment_file.chmod(0o600)
        with self.assertRaisesRegex(canary.CanaryError, "unknown"):
            canary.load_environment_file(environment_file)

        environment_file.write_text(
            "BOOST_GATEWAY_CANARY_HOST=one\nBOOST_GATEWAY_CANARY_HOST=two\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(canary.CanaryError, "duplicate"):
            canary.load_environment_file(environment_file)

        link = self.root / "environment-link"
        link.symlink_to(environment_file)
        with self.assertRaisesRegex(canary.CanaryError, "non-symlink"):
            canary.load_environment_file(link)

    def test_success_sample_binds_candidate_without_tokens_and_is_create_only(
        self,
    ) -> None:
        state: dict[str, Any] = {}
        observed = datetime(2026, 8, 1, 12, 0, 3, tzinfo=UTC)
        result = canary.run_once(
            self.config,
            self.deployment,
            self.evidence,
            client_factory=self.factory(state),
            sdk_version="4.2.0",
            observed_at=observed,
            suffix="abcdef123456",
        )

        self.assertTrue(result["overall_pass"])
        self.assertEqual("v3.6.2", result["candidate"]["tag"])
        self.assertEqual("sha256:" + "c" * 64, result["candidate"]["runtime_digest"])
        self.assertEqual("tcp://100.65.71.117:9201", result["endpoint"])
        self.assertEqual(2, result["fixed_identity_count"])
        payload = Path(result["sample_path"]).read_text(encoding="utf-8")
        self.assertNotIn(self.config.token_a, payload)
        self.assertNotIn(self.config.token_b, payload)
        self.assertNotIn(self.config.user_a, payload)
        self.assertFalse(json.loads(payload)["secret_material_recorded"])
        with self.assertRaisesRegex(canary.CanaryError, "create-only"):
            canary.write_create_only(Path(result["sample_path"]), {"replacement": True})

    def test_failure_path_best_effort_leaves_short_lived_room(self) -> None:
        state: dict[str, Any] = {"battle_failure": True}
        steps = canary.execute_business_flow(
            self.config,
            self.factory(state),
            sleep=lambda _: None,
            sample_suffix="cleanup123",
        )

        self.assertFalse(steps[2]["ok"])
        self.assertEqual("sdk_error", steps[2]["error_type"])
        self.assertEqual(2, state["leaves"])

    def test_failure_posts_alert_and_creates_incident_without_secret_material(
        self,
    ) -> None:
        state: dict[str, Any] = {"login_failure": True}
        requests = []

        def open_alert(request: Any, timeout: int) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse()

        result = canary.run_once(
            self.config,
            self.deployment,
            self.evidence,
            client_factory=self.factory(state),
            sdk_version="4.2.0",
            observed_at=datetime(2026, 8, 1, 12, 1, tzinfo=UTC),
            alert_opener=open_alert,
            suffix="failure12345",
        )

        self.assertFalse(result["overall_pass"])
        self.assertTrue(result["alertmanager_delivery"]["delivered"])
        self.assertEqual("sdk_error", result["steps"][0]["error_type"])
        self.assertEqual(401, result["steps"][0]["sdk_error_code"])
        self.assertEqual("dependency_failure", result["steps"][1]["error_type"])
        self.assertEqual(1, len(requests))
        alert_body = requests[0][0].data.decode("utf-8")
        self.assertIn("BoostGatewayExternalCanaryFailed", alert_body)
        self.assertNotIn(self.config.token_a, alert_body)
        incident = json.loads(
            Path(result["incident_record"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            "https://github.com/HoneyBury/boost_gateway/issues/27",
            incident["issue_url"],
        )
        self.assertFalse(incident["secret_material_recorded"])

    def write_sample(
        self, minute: datetime, success: bool, latency: float = 10.0
    ) -> None:
        sample = {
            "schema_version": 1,
            "sample_id": minute.strftime("sample-%H%M"),
            "scheduled_minute": minute.isoformat().replace("+00:00", "Z"),
            "started_at": minute.isoformat().replace("+00:00", "Z"),
            "candidate": canary.candidate_from_record(self.deployment),
            "endpoint": self.config.endpoint,
            "steps": [
                {
                    "name": name,
                    "ok": success,
                    "latency_ms": latency if success else None,
                    "error_type": "none" if success else "sdk_error",
                    "sdk_error_code": None,
                }
                for name in canary.REQUIRED_STEPS
            ],
            "overall_pass": success,
            "secret_material_recorded": False,
        }
        canary.write_create_only(
            self.evidence
            / "samples"
            / minute.strftime("%Y/%m/%d")
            / f"{sample['sample_id']}.json",
            sample,
        )

    def test_aggregator_counts_gaps_failures_latency_and_both_maintenance_views(
        self,
    ) -> None:
        start = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
        end = start + timedelta(minutes=5)
        self.write_sample(start, True, 10.0)
        self.write_sample(start + timedelta(minutes=1), False)
        # Minute two is an approved-maintenance gap; minute three is an unapproved gap.
        self.write_sample(start + timedelta(minutes=4), True, 30.0)
        windows = [
            {
                "id": "CHG-1",
                "start": start + timedelta(minutes=2),
                "end": start + timedelta(minutes=3),
                "approved_by": "reviewer",
            }
        ]

        report = canary.aggregate_samples(self.evidence, start, end, windows)

        self.assertEqual(5, report["expected_samples"])
        self.assertEqual(3, report["recorded_samples"])
        self.assertEqual(2, report["successful_samples"])
        self.assertAlmostEqual(
            0.4, report["availability_including_approved_maintenance"]
        )
        self.assertAlmostEqual(
            0.5, report["availability_excluding_approved_maintenance"]
        )
        self.assertEqual(1, len(report["gaps"]))
        self.assertEqual(2, report["max_gap_minutes"])
        self.assertEqual(1, report["max_nonmaintenance_gap_minutes"])
        self.assertEqual(20.0, report["latency"]["login"]["p50_ms"])
        self.assertEqual(29.8, report["latency"]["login"]["p99_ms"])
        self.assertFalse(report["overall_pass"])

    def test_watchdog_alerts_stale_stream_and_deduplicates_after_delivery(self) -> None:
        observed = datetime(2026, 8, 1, 0, 5, tzinfo=UTC)
        self.write_sample(observed - timedelta(minutes=3), True)
        calls = []

        def open_alert(request: Any, timeout: int) -> FakeResponse:
            calls.append(request)
            return FakeResponse()

        first = canary.watchdog(
            self.config,
            self.deployment,
            self.evidence,
            observed_at=observed,
            alert_opener=open_alert,
        )
        second = canary.watchdog(
            self.config,
            self.deployment,
            self.evidence,
            observed_at=observed + timedelta(seconds=10),
            alert_opener=open_alert,
        )

        self.assertFalse(first["overall_pass"])
        self.assertTrue(first["alertmanager_delivery"]["delivered"])
        self.assertTrue(second["alertmanager_delivery"]["deduplicated"])
        self.assertEqual(1, len(calls))

    def test_watchdog_retries_alert_delivery_for_latest_failed_sample(self) -> None:
        observed = datetime(2026, 8, 1, 0, 5, tzinfo=UTC)
        self.write_sample(observed - timedelta(seconds=30), False)
        calls = []

        def open_alert(request: Any, timeout: int) -> FakeResponse:
            calls.append(request)
            return FakeResponse()

        result = canary.watchdog(
            self.config,
            self.deployment,
            self.evidence,
            observed_at=observed,
            alert_opener=open_alert,
        )
        second = canary.watchdog(
            self.config,
            self.deployment,
            self.evidence,
            observed_at=observed + timedelta(seconds=5),
            alert_opener=open_alert,
        )

        self.assertTrue(result["overall_pass"])
        self.assertTrue(result["alertmanager_delivery"]["delivered"])
        self.assertEqual(1, len(calls))
        self.assertTrue(second["alertmanager_delivery"]["deduplicated"])
        retry = json.loads(Path(result["incident_record"]).read_text(encoding="utf-8"))
        self.assertIn("source_sample", retry)
        self.assertFalse(retry["secret_material_recorded"])

    def test_systemd_schedule_and_installer_preserve_external_host_boundary(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[2]
        service = (
            repository / "deploy/systemd/boost-gateway-external-canary@.service"
        ).read_text()
        timer = (
            repository / "deploy/systemd/boost-gateway-external-canary.timer"
        ).read_text()
        watchdog = (
            repository / "deploy/systemd/boost-gateway-external-canary-watchdog.timer"
        ).read_text()
        installer = (
            repository / "deploy/operations/install_external_canary_host_units.sh"
        ).read_text()
        example = (
            repository / "deploy/operations/external-canary.environment.example"
        ).read_text()

        self.assertIn("OnCalendar=*-*-* *:*:00 UTC", timer)
        self.assertIn("OnCalendar=*-*-* *:*:45 UTC", watchdog)
        self.assertIn("User=boost-gateway-canary", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertNotIn("/var/run/docker.sock", service)
        self.assertIn("assert_compatible_version", installer)
        self.assertIn("@validate.service", installer)
        self.assertIn("0:600", installer)
        self.assertIn(
            "BOOST_GATEWAY_CANARY_ALERTMANAGER_URL=http://127.0.0.1:19093",
            example,
        )


if __name__ == "__main__":
    unittest.main()
