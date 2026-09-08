from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

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
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self, _: int) -> bytes:
        return b""


class StatusOnlyResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "StatusOnlyResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

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

    def test_cli_imports_from_repo_and_flat_install_layouts(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        script = repository / "scripts/tools/external_business_canary.py"
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        environment.pop("PYTHONPATH", None)
        for working_directory in (repository, self.root):
            completed = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=working_directory,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

        flat = self.root / "flat"
        flat.mkdir()
        flat_script = flat / script.name
        shutil.copy2(script, flat_script)
        shutil.copy2(
            repository / "scripts/lib/perf_statistics.py",
            flat / "perf_statistics.py",
        )
        completed = subprocess.run(
            [sys.executable, str(flat_script), "--help"],
            cwd=self.root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

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

    def test_create_only_atomic_publish_preserves_existing_target(self) -> None:
        target = self.root / "existing.json"
        original = b'{"original":true}\n'
        target.write_bytes(original)
        target.chmod(0o640)

        with self.assertRaisesRegex(canary.CanaryError, "create-only"):
            canary.write_create_only(target, {"replacement": True})

        self.assertEqual(original, target.read_bytes())
        self.assertEqual(
            [], list(target.parent.glob(f"{canary.CREATE_ONLY_TEMP_PREFIX}*"))
        )

        backing = self.root / "backing.json"
        backing.write_bytes(original)
        symlink_target = self.root / "existing-symlink.json"
        symlink_target.symlink_to(backing)
        with self.assertRaisesRegex(canary.CanaryError, "create-only"):
            canary.write_create_only(symlink_target, {"replacement": True})
        self.assertTrue(symlink_target.is_symlink())
        self.assertEqual(original, backing.read_bytes())

    def test_create_only_failure_before_publish_leaves_no_target_or_temp(self) -> None:
        target = self.root / "not-published.json"
        with mock.patch.object(
            canary.os, "fsync", side_effect=OSError("simulated data sync failure")
        ):
            with self.assertRaisesRegex(OSError, "data sync failure"):
                canary.write_create_only(target, {"complete": False})

        self.assertFalse(target.exists())
        self.assertEqual(
            [], list(target.parent.glob(f"{canary.CREATE_ONLY_TEMP_PREFIX}*"))
        )

    def test_create_only_syncs_complete_file_then_parent_directory(self) -> None:
        target = self.root / "published.json"
        sync_events: list[tuple[str, bool]] = []
        real_fsync = os.fsync

        def observe_fsync(descriptor: int) -> None:
            kind = "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
            sync_events.append((kind, target.exists()))
            real_fsync(descriptor)

        previous_umask = os.umask(0o077)
        try:
            with mock.patch.object(canary.os, "fsync", side_effect=observe_fsync):
                canary.write_create_only(target, {"complete": True, "sequence": 7})
        finally:
            os.umask(previous_umask)

        self.assertEqual(
            [("file", False), ("directory", True), ("directory", True)],
            sync_events,
        )
        self.assertEqual(
            {"complete": True, "sequence": 7},
            json.loads(target.read_text(encoding="utf-8")),
        )
        self.assertTrue(target.read_bytes().endswith(b"\n"))
        self.assertEqual(0o640, stat.S_IMODE(target.stat().st_mode))

    def test_create_only_never_rolls_back_a_fully_published_target(self) -> None:
        target = self.root / "published-before-directory-sync-failure.json"
        real_fsync = os.fsync

        def fail_directory_sync(descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("simulated directory sync failure")
            real_fsync(descriptor)

        with mock.patch.object(canary.os, "fsync", side_effect=fail_directory_sync):
            with self.assertRaisesRegex(OSError, "directory sync failure"):
                canary.write_create_only(target, {"fully_written": True})

        self.assertEqual(
            {"fully_written": True}, json.loads(target.read_text(encoding="utf-8"))
        )
        self.assertEqual(
            [], list(target.parent.glob(f"{canary.CREATE_ONLY_TEMP_PREFIX}*"))
        )

    def test_create_only_concurrent_publish_has_exactly_one_winner(self) -> None:
        target = self.root / "concurrent.json"
        barrier = threading.Barrier(2)

        def publish(writer: str) -> str:
            barrier.wait(timeout=5)
            try:
                canary.write_create_only(target, {"writer": writer})
            except canary.CanaryError:
                return "exists"
            return "published"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(publish, ("one", "two")))

        self.assertEqual(["exists", "published"], sorted(outcomes))
        self.assertIn(
            json.loads(target.read_text(encoding="utf-8"))["writer"], {"one", "two"}
        )
        self.assertEqual(
            [], list(target.parent.glob(f"{canary.CREATE_ONLY_TEMP_PREFIX}*"))
        )

    def test_orphan_atomic_publish_temps_are_not_evidence(self) -> None:
        sample_orphan = (
            self.evidence
            / "samples/2026/08/01"
            / f"{canary.CREATE_ONLY_TEMP_PREFIX}sample{canary.CREATE_ONLY_TEMP_SUFFIX}"
        )
        incident_orphan = (
            self.evidence
            / "incidents"
            / f"{canary.CREATE_ONLY_TEMP_PREFIX}incident{canary.CREATE_ONLY_TEMP_SUFFIX}"
        )
        sample_orphan.parent.mkdir(parents=True)
        incident_orphan.parent.mkdir(parents=True)
        sample_orphan.write_text('{"looks":"complete"}\n', encoding="utf-8")
        incident_orphan.write_text('{"looks":"complete"}\n', encoding="utf-8")

        self.assertEqual([], list((self.evidence / "samples").glob("**/*.json")))
        self.assertEqual([], list((self.evidence / "incidents").glob("retry-*.json")))
        self.assertEqual([], list((self.evidence / "incidents").glob("silent-*.json")))

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
        self.assertTrue(first["alertmanager_readiness"]["ready"])
        self.assertTrue(first["alertmanager_delivery"]["delivered"])
        self.assertTrue(second["alertmanager_delivery"]["deduplicated"])
        self.assertEqual(2, sum(request.get_method() == "GET" for request in calls))
        self.assertEqual(1, sum(request.get_method() == "POST" for request in calls))

    def test_watchdog_refreshes_a_delivered_silent_alert_before_it_expires(
        self,
    ) -> None:
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
            observed_at=observed + canary.SILENT_ALERT_REFRESH_AFTER,
            alert_opener=open_alert,
        )

        self.assertFalse(first["overall_pass"])
        self.assertFalse(second["overall_pass"])
        self.assertNotEqual(first["incident_record"], second["incident_record"])
        self.assertEqual(2, sum(request.get_method() == "GET" for request in calls))
        self.assertEqual(2, sum(request.get_method() == "POST" for request in calls))
        self.assertEqual(
            2, len(list((self.evidence / "incidents").glob("silent-*.json")))
        )

    def test_watchdog_ignores_malformed_dedup_delivery_metadata(self) -> None:
        observed = datetime(2026, 8, 1, 0, 5, tzinfo=UTC)
        sample_time = observed - timedelta(minutes=3)
        self.write_sample(sample_time, True)
        malformed = (
            self.evidence
            / "incidents"
            / f"silent-{sample_time.strftime('%Y%m%dT%H%M%S')}-malformed.json"
        )
        canary.write_create_only(
            malformed,
            {
                "created_at": canary.isoformat(observed),
                "alertmanager_delivery": None,
            },
        )
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

        self.assertFalse(result["overall_pass"])
        self.assertTrue(result["alertmanager_delivery"]["delivered"])
        self.assertEqual(["GET", "POST"], [request.get_method() for request in calls])

    def test_strict_watchdog_rejects_failed_current_minute_even_when_alert_delivered(self) -> None:
        observed = datetime(2026, 8, 1, 0, 5, 45, tzinfo=UTC)
        self.write_sample(observed - timedelta(seconds=40), False)
        result = canary.watchdog(self.config, self.deployment, self.evidence,
                                observed_at=observed, require_successful_sample=True,
                                alert_opener=lambda *_args, **_kwargs: FakeResponse())
        self.assertFalse(result["overall_pass"])
        self.assertEqual("natural_minute_sample_not_successful", result["failure_type"])

    def test_strict_watchdog_rejects_success_from_previous_minute(self) -> None:
        observed = datetime(2026, 8, 1, 0, 5, 45, tzinfo=UTC)
        self.write_sample(observed - timedelta(seconds=70), True)
        result = canary.watchdog(self.config, self.deployment, self.evidence,
                                observed_at=observed, require_successful_sample=True,
                                alert_opener=lambda *_args, **_kwargs: FakeResponse())
        self.assertFalse(result["overall_pass"])

    def test_strict_watchdog_accepts_successful_current_minute(self) -> None:
        observed = datetime(2026, 8, 1, 0, 5, 45, tzinfo=UTC)
        self.write_sample(observed - timedelta(seconds=40), True)
        result = canary.watchdog(self.config, self.deployment, self.evidence,
                                observed_at=observed, require_successful_sample=True,
                                alert_opener=lambda *_args, **_kwargs: FakeResponse())
        self.assertTrue(result["overall_pass"])

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
        self.assertTrue(result["alertmanager_readiness"]["ready"])
        self.assertTrue(result["alertmanager_delivery"]["delivered"])
        self.assertEqual(2, sum(request.get_method() == "GET" for request in calls))
        self.assertEqual(1, sum(request.get_method() == "POST" for request in calls))
        self.assertTrue(second["alertmanager_delivery"]["deduplicated"])
        retry = json.loads(Path(result["incident_record"]).read_text(encoding="utf-8"))
        self.assertIn("source_sample", retry)
        self.assertFalse(retry["secret_material_recorded"])

    def test_watchdog_retries_failed_sample_and_stale_stream_alert_together(
        self,
    ) -> None:
        observed = datetime(2026, 8, 1, 0, 5, tzinfo=UTC)
        self.write_sample(observed - timedelta(minutes=3), False)
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

        self.assertFalse(result["overall_pass"])
        self.assertTrue(
            result["failed_sample_retry"]["alertmanager_delivery"]["delivered"]
        )
        self.assertTrue(result["alertmanager_delivery"]["delivered"])
        self.assertEqual(
            ["GET", "POST", "POST"], [request.get_method() for request in calls]
        )
        self.assertEqual(
            1, len(list((self.evidence / "incidents").glob("retry-*.json")))
        )
        self.assertEqual(
            1, len(list((self.evidence / "incidents").glob("silent-*.json")))
        )

    def test_watchdog_records_local_incident_when_forward_is_unready(self) -> None:
        observed = datetime(2026, 8, 1, 0, 5, tzinfo=UTC)
        self.write_sample(observed - timedelta(seconds=30), True)
        calls = []

        def unavailable(request: Any, timeout: int) -> FakeResponse:
            calls.append((request, timeout))
            raise ConnectionRefusedError("governed forward is unavailable")

        result = canary.watchdog(
            self.config,
            self.deployment,
            self.evidence,
            observed_at=observed,
            alert_opener=unavailable,
        )

        self.assertFalse(result["overall_pass"])
        self.assertEqual("alertmanager_forward_unready", result["failure_type"])
        self.assertIsNotNone(result["latest_sample"])
        self.assertEqual(30.0, result["age_seconds"])
        self.assertEqual(
            "http://127.0.0.1:19093/-/ready",
            result["alertmanager_readiness"]["url"],
        )
        self.assertFalse(result["alertmanager_readiness"]["ready"])
        self.assertEqual(
            "ConnectionRefusedError",
            result["alertmanager_readiness"]["error_type"],
        )
        self.assertFalse(result["alertmanager_delivery"]["delivered"])
        self.assertEqual(
            "ConnectionRefusedError", result["alertmanager_delivery"]["error_type"]
        )
        self.assertEqual(["GET", "POST"], [call[0].get_method() for call in calls])
        incident_path = Path(result["incident_record"])
        incident = json.loads(incident_path.read_text(encoding="utf-8"))
        self.assertEqual("alertmanager_forward_unready", incident["incident_type"])
        self.assertEqual(result["latest_sample"], incident["latest_sample"])
        self.assertEqual(30.0, incident["age_seconds"])
        self.assertFalse(incident["overall_pass"])
        self.assertFalse(incident["alertmanager_delivery"]["delivered"])
        self.assertFalse(incident["secret_material_recorded"])
        payload = incident_path.read_text(encoding="utf-8")
        self.assertNotIn(self.config.token_a, payload)
        self.assertNotIn(self.config.token_b, payload)
        with self.assertRaisesRegex(canary.CanaryError, "create-only"):
            canary.write_create_only(incident_path, {"replacement": True})

        first_payload = incident_path.read_text(encoding="utf-8")
        second = canary.watchdog(
            self.config,
            self.deployment,
            self.evidence,
            observed_at=observed,
            alert_opener=unavailable,
        )
        second_path = Path(second["incident_record"])
        self.assertNotEqual(incident_path, second_path)
        self.assertEqual(first_payload, incident_path.read_text(encoding="utf-8"))
        self.assertEqual(
            "alertmanager_forward_unready",
            json.loads(second_path.read_text(encoding="utf-8"))["incident_type"],
        )

    def test_watchdog_fails_on_unready_http_status_even_if_alert_is_delivered(
        self,
    ) -> None:
        calls = []

        def status_aware(request: Any, timeout: int) -> FakeResponse:
            calls.append((request, timeout))
            if request.get_method() == "GET":
                raise urllib.error.HTTPError(
                    request.full_url,
                    503,
                    "not ready",
                    hdrs=None,
                    fp=None,
                )
            return FakeResponse()

        result = canary.watchdog(
            self.config,
            self.deployment,
            self.evidence,
            observed_at=datetime(2026, 8, 1, 0, 5, tzinfo=UTC),
            alert_opener=status_aware,
        )

        self.assertFalse(result["overall_pass"])
        self.assertFalse(result["alertmanager_readiness"]["ready"])
        self.assertEqual(503, result["alertmanager_readiness"]["status_code"])
        self.assertEqual("HTTPError", result["alertmanager_readiness"]["error_type"])
        self.assertTrue(result["alertmanager_delivery"]["delivered"])
        self.assertEqual(["GET", "POST"], [call[0].get_method() for call in calls])

    def test_watchdog_rejects_non_200_readiness_and_delivery_statuses(self) -> None:
        calls = []

        def non_contract_status(request: Any, timeout: int) -> StatusOnlyResponse:
            calls.append((request, timeout))
            return StatusOnlyResponse(204 if request.get_method() == "GET" else 202)

        result = canary.watchdog(
            self.config,
            self.deployment,
            self.evidence,
            observed_at=datetime(2026, 8, 1, 0, 5, tzinfo=UTC),
            alert_opener=non_contract_status,
        )

        self.assertFalse(result["overall_pass"])
        self.assertEqual(204, result["alertmanager_readiness"]["status_code"])
        self.assertEqual(
            "unexpected_http_status",
            result["alertmanager_readiness"]["error_type"],
        )
        self.assertFalse(result["alertmanager_delivery"]["delivered"])
        self.assertEqual(202, result["alertmanager_delivery"]["status_code"])
        self.assertEqual(
            "unexpected_http_status", result["alertmanager_delivery"]["error_type"]
        )

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
        dependency = "boost-gateway-canary-alertmanager-forward.service"
        for unit in (service, timer, watchdog):
            with self.subTest(unit=unit.splitlines()[1]):
                self.assertNotIn(f"Requires={dependency}", unit)
                self.assertRegex(unit, rf"(?m)^Wants=.*{dependency}")
                self.assertRegex(unit, rf"(?m)^After=.*{dependency}")
        self.assertIn("After=boost-gateway-canary-alertmanager-forward.service", timer)
        self.assertIn(
            "After=boost-gateway-canary-alertmanager-forward.service", watchdog
        )
        self.assertIn("User=boost-gateway-canary", service)
        self.assertNotIn("ConditionPathExists=", service)
        self.assertIn("AssertPathExists=/etc/boost-gateway-canary/environment", service)
        self.assertIn(
            "AssertPathExists=/etc/boost-gateway-canary/deployment-record.json",
            service,
        )
        self.assertIn("ProtectSystem=strict", service)
        self.assertNotIn("/var/run/docker.sock", service)
        self.assertIn("assert_compatible_version", installer)
        self.assertIn("scripts/lib/perf_statistics.py", installer)
        self.assertIn(
            "/usr/local/libexec/boost-gateway-canary/perf_statistics.py",
            installer,
        )
        self.assertIn("@validate.service", installer)
        self.assertIn("0:600", installer)
        self.assertIn(
            "BOOST_GATEWAY_CANARY_ALERTMANAGER_URL=http://127.0.0.1:19093",
            example,
        )

    def test_linux_alertmanager_forward_is_fixed_and_fail_closed(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        service = (
            repository
            / "deploy/systemd/boost-gateway-canary-alertmanager-forward.service"
        ).read_text()
        installer = (
            repository
            / "deploy/operations/install_external_canary_alertmanager_forward.sh"
        ).read_text()

        self.assertIn("User=boost-gateway-canary-forward", service)
        self.assertIn("Group=boost-gateway-canary-forward", service)
        self.assertNotIn("ConditionPathExists=", service)
        self.assertIn("Wants=network-online.target tailscaled.service", service)
        self.assertIn("Restart=on-failure", service)
        self.assertIn(
            "EnvironmentFile=/etc/boost-gateway-canary/alertmanager-forward.env",
            service,
        )
        self.assertIn("LoadCredential=ssh_identity:", service)
        self.assertIn("LoadCredential=ssh_known_hosts:", service)
        self.assertIn("-F /dev/null -NT", service)
        for option in (
            "BatchMode=yes",
            "IdentitiesOnly=yes",
            "StrictHostKeyChecking=yes",
            "ExitOnForwardFailure=yes",
            "ServerAliveInterval=30",
            "ServerAliveCountMax=3",
        ):
            self.assertIn(option, service)
        self.assertIn("-L 127.0.0.1:19093:127.0.0.1:9093", service)
        self.assertEqual(service.count(" -L "), 1)
        self.assertNotIn(" -R ", service)
        self.assertNotIn("0.0.0.0:19093", service)
        self.assertNotIn("ClearAllForwardings=yes", service)

        self.assertIn("SSH identity must be root-owned mode 0600", installer)
        self.assertIn("ssh-keygen -y -P ''", installer)
        self.assertIn("SSH identity must use Ed25519", installer)
        self.assertIn("TARGET_FORWARD_USER=boost-gateway-alert-forward", installer)
        self.assertIn("--ssh-target user must be ${TARGET_FORWARD_USER}", installer)
        self.assertIn('ssh-keygen -F "${TARGET_HOST}"', installer)
        self.assertIn("SSH known_hosts must be root-owned mode 0600", installer)
        self.assertIn('install_secret "${IDENTITY_FILE}"', installer)
        self.assertIn('install_secret "${KNOWN_HOSTS_FILE}"', installer)
        self.assertIn("${source} -ef ${destination}", installer)
        self.assertIn("FORWARD_USER=boost-gateway-canary-forward", installer)
        self.assertIn("forward service account and group must not be root", installer)
        self.assertIn(
            "forward service account must not belong to supplementary groups",
            installer,
        )
        self.assertIn("systemctl restart", installer)
        self.assertIn("systemctl is-active --quiet", installer)
        self.assertIn("http://127.0.0.1:19093/-/ready", installer)
        self.assertIn(
            'from="<external-canary-tailscale-address>",command="/bin/false",'
            "restrict,port-forwarding,"
            'permitopen="127.0.0.1:9093"',
            installer,
        )
        self.assertIn(
            "target_authorization_installer="
            "deploy/operations/install_alertmanager_forward_target.sh",
            installer,
        )
        self.assertNotIn("--local-port", installer)
        self.assertNotIn("--remote-port", installer)
        self.assertNotIn("set -x", installer)


if __name__ == "__main__":
    unittest.main()
