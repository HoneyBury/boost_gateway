"""Unit coverage for executable R5 recovery helpers."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
SCRIPT_PATH = REPO_ROOT / "scripts/gates/production/verify_preprod_recovery_drill.py"
SPEC = importlib.util.spec_from_file_location("verify_preprod_recovery_drill", SCRIPT_PATH)
assert SPEC and SPEC.loader
DRILL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRILL)


def test_expected_failure_is_required() -> None:
    with patch.object(DRILL, "run_step", return_value={"status": "failed", "stderr_tail": "down"}):
        step = DRILL.run_expected_failure_step("name", "category", ["command"], 1)
    assert step["status"] == "passed"
    assert step["expected_failure_observed"] is True

    with patch.object(DRILL, "run_step", return_value={"status": "passed", "stderr_tail": ""}):
        step = DRILL.run_expected_failure_step("name", "category", ["command"], 1)
    assert step["status"] == "failed"
    assert step["expected_failure_observed"] is False


def test_stdout_contract_rejects_wrong_value() -> None:
    with patch.object(
        DRILL,
        "run_step",
        return_value={"status": "passed", "stdout_tail": "unexpected\n", "stderr_tail": ""},
    ):
        step = DRILL.run_step_expect_stdout("name", "category", ["command"], 1, "expected")
    assert step["status"] == "failed"
    assert step["observed_stdout"] == "unexpected"


def test_stdout_contract_accepts_exact_trimmed_value() -> None:
    with patch.object(
        DRILL,
        "run_step",
        return_value={"status": "passed", "stdout_tail": "PONG\n", "stderr_tail": ""},
    ):
        step = DRILL.run_step_expect_stdout("name", "category", ["command"], 1, "PONG")
    assert step["status"] == "passed"


def test_expected_failure_requires_dependency_evidence() -> None:
    with patch.object(
        DRILL,
        "run_step",
        return_value={"status": "failed", "stdout_tail": "", "stderr_tail": "binary missing"},
    ):
        step = DRILL.run_expected_failure_step(
            "name", "category", ["command"], 1, ("leaderboard",)
        )
    assert step["status"] == "failed"
    assert step["expected_failure_observed"] is False


def test_resolve_sdk_shared_library_uses_release_build_layout(tmp_path: Path) -> None:
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    library = tmp_path / f"sdk/libboost_gateway_sdk{suffix}"
    library.parent.mkdir(parents=True)
    library.touch()
    assert DRILL.resolve_sdk_shared_library(tmp_path, "Release") == library


def test_preprod_workflow_exposes_opt_in_redis_recovery() -> None:
    workflow = (REPO_ROOT / ".github/workflows/preprod-evidence.yml").read_text(encoding="utf-8")
    assert "include_redis_recovery:" in workflow
    assert "args+=(--include-redis-recovery)" in workflow


def test_preprod_workflow_exposes_opt_in_redis_alert_transition() -> None:
    workflow = (REPO_ROOT / ".github/workflows/preprod-evidence.yml").read_text(encoding="utf-8")
    assert "verify_redis_alert_transition:" in workflow
    assert "redis_alert_firing_timeout_seconds:" in workflow
    assert "args+=(--verify-redis-alert-transition" in workflow
    assert "r5-redis-alert-runtime-summary.json" in workflow


def test_preprod_workflow_rebuilds_and_validates_candidate_images() -> None:
    workflow = (REPO_ROOT / ".github/workflows/preprod-evidence.yml").read_text(encoding="utf-8")
    assert "prepare_docker_runtime_context.py" in workflow
    assert "build --pull=false" in workflow
    assert '--candidate-revision "$BOOST_GATEWAY_CANDIDATE_REVISION"' in workflow


def test_build_image_manifest_must_match_candidate_and_lockfile() -> None:
    lockfile = REPO_ROOT / "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock"
    manifest = {
        "schema_version": 1,
        "git_revision": "candidate-sha",
        "dependency_provider": "conan",
        "worktree_clean": True,
        "conan_lockfile": str(lockfile.relative_to(REPO_ROOT)),
        "conan_lockfile_sha256": DRILL.sha256_file(lockfile),
        "binaries": [{"name": "v2_gateway_demo", "sha256": "a" * 64}],
    }
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=json.dumps(manifest), stderr=""
    )
    inventory = [
        {
            "service": "gateway",
            "image": "candidate-gateway",
            "source": "build",
            "present": True,
        }
    ]
    sha_completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=("a" * 64) + "  /app/bin/v2_gateway_demo\n", stderr=""
    )
    with patch.object(DRILL.subprocess, "run", side_effect=[completed, sha_completed]):
        inspected = DRILL.inspect_build_image_manifests(inventory, "candidate-sha")
    assert inspected[0]["build_manifest_valid"] is True

    with patch.object(DRILL.subprocess, "run", side_effect=[completed, sha_completed]):
        stale = DRILL.inspect_build_image_manifests(inventory, "different-sha")
    assert stale[0]["build_manifest_valid"] is False
    assert stale[0]["build_manifest_checks"]["git_revision"] is False
    step = DRILL.image_inventory_step("verify", stale, fail_on_missing=True)
    assert step["status"] == "failed"
    assert step["stale_build_images"] == ["candidate-gateway"]


def test_alert_wait_fails_when_verifier_exits_early() -> None:
    process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    process.wait(timeout=5)
    step = DRILL.wait_for_prometheus_alert_firing(process, "Alert", 1.0)
    assert step["status"] == "failed"
    assert step["returncode"] == 3


def test_alert_wait_passes_only_after_firing() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        response = {
            "status": "success",
            "data": {"alerts": [{"labels": {"alertname": "Alert"}, "state": "firing"}]},
        }
        with patch.object(DRILL, "fetch_json", return_value=response):
            step = DRILL.wait_for_prometheus_alert_firing(process, "Alert", 1.0)
        assert step["status"] == "passed"
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_terminate_background_process_cleans_up_child() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    step = DRILL.terminate_background_process(process)
    assert step["status"] == "passed"
    assert process.poll() is not None


def test_drill_record_reports_redis_alert_evidence(tmp_path: Path) -> None:
    record_path = tmp_path / "record.json"
    started = datetime.now(UTC) - timedelta(seconds=12)
    ended = datetime.now(UTC)
    DRILL.write_drill_record(
        record_path,
        tmp_path / "recovery.json",
        tmp_path / "sdk.json",
        tmp_path / "alerts.json",
        tmp_path / "snapshot.json",
        tmp_path / "monitoring.json",
        True,
        include_redis_recovery=True,
        verify_redis_alert_transition=True,
        failure_started_at=started,
        failure_ended_at=ended,
        measured_rto_seconds=1.25,
    )

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["scenario"] == "redis_recovery"
    assert record["observability"]["alerts_observed"] == [
        "BoostGatewayRedisUnavailable: inactive -> pending -> firing -> resolved"
    ]
    assert record["verification"]["redis_alert_runtime_summary"].endswith("alerts.json")
    assert record["failure_injection"]["started_at"] == started.isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    assert record["failure_injection"]["ended_at"] == ended.isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    assert record["recovery"]["rto_seconds"] == 1.25
