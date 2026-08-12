#!/usr/bin/env python3
"""Run or validate R5 pre-production recovery drill evidence."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lib.recovery_drill_runtime import *  # noqa: E402,F401,F403
from scripts.lib.recovery_drill_contract import *  # noqa: E402,F401,F403
from scripts.lib.recovery_drill_images import *  # noqa: E402,F401,F403
from scripts.lib.recovery_drill_preflight import *  # noqa: E402,F401,F403
from scripts.lib.recovery_drill_record import *  # noqa: E402,F401,F403

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag, raw_options in RECOVERY_DRILL_ARGUMENTS:
        options = dict(raw_options)
        if options.pop("hidden", False):
            options["help"] = argparse.SUPPRESS
        parser.add_argument(flag, **options)
    args = parser.parse_args()

    if args.sdk_leaderboard_probe:
        if args.sdk_library is None:
            parser.error("--sdk-leaderboard-probe requires --sdk-library")
        return run_sdk_leaderboard_probe(
            args.gateway_host,
            args.gateway_port,
            args.sdk_library,
        )

    try:
        context = prepare_drill_context(args)
    except ValueError as exc:
        parser.error(str(exc))
    summary_path = context["summary_path"]
    build_dir = context["build_dir"]
    validation_dir = context["validation_dir"]
    compose_file = context["compose_file"]
    image_preflight_summary = context["image_preflight_summary"]
    steps: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    mode = context["mode"]
    compose_command = context["compose_command"]

    if args.image_preflight_only:
        try:
            return run_preflight_only(args, context)
        except ValueError as exc:
            parser.error(str(exc))

    recovery_summary = validation_dir / "r5-production-recovery-summary.json"
    monitoring_summary = (
        REPO_ROOT / "runtime/validation/monitoring-operability-summary.json"
    )
    steps.append(
        run_step(
            "R5 N3 production recovery static gate",
            "recovery_gate",
            [
                sys.executable,
                str(REPO_ROOT / "scripts/gates/production/check_production_recovery_gate.py"),
                "--summary-path",
                str(recovery_summary),
            ],
            120,
        )
    )
    steps.append(
        run_step(
            "R5 monitoring operability static gate",
            "monitoring_operability",
            [
                sys.executable,
                str(REPO_ROOT / "scripts/gates/production/check_monitoring_operability.py"),
                "--summary-path",
                str(monitoring_summary),
            ],
            120,
        )
    )

    sdk_summary = validation_dir / "r5-post-recovery-sdk-full-flow-summary.json"
    redis_sdk_summary = validation_dir / "r5-redis-recovery-sdk-full-flow-summary.json"
    redis_alert_summary = validation_dir / "r5-redis-alert-runtime-summary.json"
    docker_snapshot_summary = (
        REPO_ROOT / "runtime/perf/docker-production-snapshot/summary.json"
    )
    record_path = validation_dir / "r5-preprod-recovery-drill-record.json"
    record_check_summary = (
        validation_dir / "r5-recovery-drill-record-check-summary.json"
    )
    cleanup_needed = False
    redis_fault_active = False
    alert_verifier: subprocess.Popen[str] | None = None
    alert_verifier_command: list[str] = []
    failure_started_at: datetime | None = None
    failure_ended_at: datetime | None = None
    measured_rto_seconds: float | None = None
    fault_started_monotonic: float | None = None
    image_preflight: dict[str, Any] = {
        "passed": mode != "docker-compose",
        "pull_policy": args.docker_pull_policy,
        "target_platform": args.docker_target_platform,
        "requirements": [],
        "inventory": [],
        "missing_images": [],
        "missing_build_images": [],
        "stale_build_images": [],
        "candidate_revision": args.candidate_revision,
        "steps": [],
    }

    try:
        if mode == "docker-compose":
            image_preflight = run_docker_image_preflight(
                compose_command,
                compose_file,
                pull_policy=args.docker_pull_policy,
                pull_attempts=args.docker_pull_attempts,
                timeout_seconds=args.step_timeout_seconds,
                candidate_revision=args.candidate_revision,
                target_platform=args.docker_target_platform,
            )
            steps.extend(image_preflight["steps"])
            write_image_preflight_summary(
                image_preflight_summary,
                image_preflight,
                configuration=args.configuration,
            )
            if steps[-1]["status"] == "passed":
                steps.append(
                    run_step(
                        "R5 docker compose up from existing images",
                        "docker_compose",
                        [
                            *compose_command,
                            "-f",
                            str(compose_file),
                            "up",
                            "-d",
                            "--no-build",
                        ],
                        args.step_timeout_seconds,
                    )
                )
                cleanup_needed = True
            if steps[-1]["status"] == "passed":
                ready_started = time.monotonic()
                try:
                    ready_doc = wait_for_ready("http://127.0.0.1:9080/ready", 90.0)
                    steps.append(
                        {
                            "name": "R5 gateway ready before restart",
                            "category": "docker_compose",
                            "command": ["GET", "http://127.0.0.1:9080/ready"],
                            "status": "passed",
                            "duration_seconds": round(
                                time.monotonic() - ready_started, 3
                            ),
                            "stdout_tail": json.dumps(ready_doc, sort_keys=True)[
                                -6000:
                            ],
                            "stderr_tail": "",
                        }
                    )
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - captured in validation summary
                    steps.append(
                        {
                            "name": "R5 gateway ready before restart",
                            "category": "docker_compose",
                            "command": ["GET", "http://127.0.0.1:9080/ready"],
                            "status": "failed",
                            "duration_seconds": round(
                                time.monotonic() - ready_started, 3
                            ),
                            "stdout_tail": "",
                            "stderr_tail": str(exc),
                        }
                    )

            client = build_dir / "sdk/examples/sdk_full_flow_client"
            sdk_library = resolve_sdk_shared_library(build_dir, args.configuration)
            if steps[-1]["status"] == "passed":
                pre_step = run_step(
                    "R5 SDK full-flow before gateway restart",
                    "sdk_full_flow",
                    [str(client), "127.0.0.1", "9201"],
                    args.step_timeout_seconds,
                )
                steps.append(pre_step)

            if steps[-1]["status"] == "passed":
                failure_started_at = datetime.now(UTC)
                gateway_recovery_started = time.monotonic()
                steps.append(
                    run_step(
                        "R5 docker compose restart gateway",
                        "recovery_drill",
                        [
                            *compose_command,
                            "-f",
                            str(compose_file),
                            "restart",
                            "gateway",
                        ],
                        args.step_timeout_seconds,
                    )
                )

            if steps[-1]["status"] == "passed":
                ready_started = time.monotonic()
                try:
                    ready_doc = wait_for_ready("http://127.0.0.1:9080/ready", 90.0)
                    failure_ended_at = datetime.now(UTC)
                    measured_rto_seconds = time.monotonic() - gateway_recovery_started
                    steps.append(
                        {
                            "name": "R5 gateway ready after restart",
                            "category": "recovery_drill",
                            "command": ["GET", "http://127.0.0.1:9080/ready"],
                            "status": "passed",
                            "duration_seconds": round(
                                time.monotonic() - ready_started, 3
                            ),
                            "stdout_tail": json.dumps(ready_doc, sort_keys=True)[
                                -6000:
                            ],
                            "stderr_tail": "",
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    steps.append(
                        {
                            "name": "R5 gateway ready after restart",
                            "category": "recovery_drill",
                            "command": ["GET", "http://127.0.0.1:9080/ready"],
                            "status": "failed",
                            "duration_seconds": round(
                                time.monotonic() - ready_started, 3
                            ),
                            "stdout_tail": "",
                            "stderr_tail": str(exc),
                        }
                    )

            if steps[-1]["status"] == "passed":
                post_step = run_step(
                    "R5 SDK full-flow after gateway restart",
                    "sdk_full_flow",
                    [str(client), "127.0.0.1", "9201"],
                    args.step_timeout_seconds,
                )
                steps.append(post_step)
                write_command_summary(
                    sdk_summary, "R5 SDK full-flow after gateway restart", post_step
                )

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                marker_key = f"boost_gateway:r5:recovery:{int(time.time())}"
                marker_value = build_evidence_provenance(
                    REPO_ROOT,
                    build_configuration=args.configuration,
                ).get("candidate_revision", "unknown")
                redis_cli = [
                    *compose_command,
                    "-f",
                    str(compose_file),
                    "exec",
                    "-T",
                    "redis",
                    "redis-cli",
                ]
                steps.append(
                    run_step_expect_stdout(
                        "R5 seed Redis persistence marker",
                        "redis_recovery",
                        [*redis_cli, "SET", marker_key, str(marker_value)],
                        args.step_timeout_seconds,
                        "OK",
                    )
                )

            if steps[-1]["status"] == "passed" and args.verify_redis_alert_transition:
                alert_verifier_command = [
                    sys.executable,
                    str(REPO_ROOT / "scripts/verify_prometheus_alert_states.py"),
                    "--configuration",
                    args.configuration,
                    "--prometheus-url",
                    "http://127.0.0.1:9090",
                    "--api",
                    "alerts",
                    "--alert-sequence",
                    "BoostGatewayRedisUnavailable=inactive,pending,firing,resolved",
                    "--state-timeout-seconds",
                    str(max(float(args.step_timeout_seconds), 480.0)),
                    "--overall-timeout-seconds",
                    str(max(float(args.step_timeout_seconds) * 2.0, 720.0)),
                    "--summary-path",
                    str(redis_alert_summary),
                ]
                alert_verifier, alert_start_step = start_background_step(
                    "R5 start Prometheus Redis alert verifier",
                    "prometheus_alert_runtime",
                    alert_verifier_command,
                )
                steps.append(alert_start_step)

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                failure_started_at = datetime.now(UTC)
                failure_ended_at = None
                measured_rto_seconds = None
                fault_started_monotonic = time.monotonic()
                redis_fault_active = True
                redis_fault_services = ["redis"]
                redis_stop_step = run_step(
                    "R5 stop Redis during SDK traffic",
                    "redis_recovery",
                    [
                        *compose_command,
                        "-f",
                        str(compose_file),
                        "stop",
                        *redis_fault_services,
                    ],
                    args.step_timeout_seconds,
                )
                steps.append(redis_stop_step)

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                steps.append(
                    run_expected_failure_step(
                        "R5 SDK leaderboard probe degrades while Redis is unavailable",
                        "redis_recovery",
                        [
                            sys.executable,
                            str(REPO_ROOT / "scripts/verify_preprod_recovery_drill.py"),
                            "--sdk-leaderboard-probe",
                            "--sdk-library",
                            str(sdk_library),
                            "--gateway-host",
                            "127.0.0.1",
                            "--gateway-port",
                            "9201",
                        ],
                        args.step_timeout_seconds,
                        ("leaderboard",),
                    )
                )

            if steps[-1]["status"] == "passed" and args.verify_redis_alert_transition:
                assert alert_verifier is not None
                steps.append(
                    wait_for_prometheus_alert_firing(
                        alert_verifier,
                        "BoostGatewayRedisUnavailable",
                        args.redis_alert_firing_timeout_seconds,
                    )
                )

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                recovery_started = time.monotonic()
                redis_recovery_services = ["redis"]
                steps.append(
                    run_step(
                        "R5 start Redis after fault injection",
                        "redis_recovery",
                        [
                            *compose_command,
                            "-f",
                            str(compose_file),
                            "start",
                            *redis_recovery_services,
                        ],
                        args.step_timeout_seconds,
                    )
                )
                if steps[-1]["status"] == "passed":
                    try:
                        redis_doc = wait_for_compose_redis(
                            compose_command,
                            compose_file,
                            min(float(args.step_timeout_seconds), 120.0),
                        )
                        failure_ended_at = datetime.now(UTC)
                        measured_rto_seconds = time.monotonic() - (
                            fault_started_monotonic or recovery_started
                        )
                        redis_fault_active = False
                        steps.append(
                            {
                                "name": "R5 Redis responds after recovery",
                                "category": "redis_recovery",
                                "command": [*redis_cli, "PING"],
                                "status": "passed",
                                "duration_seconds": round(
                                    time.monotonic() - recovery_started, 3
                                ),
                                "stdout_tail": json.dumps(redis_doc, sort_keys=True),
                                "stderr_tail": "",
                                "rto_seconds": round(
                                    time.monotonic() - recovery_started, 3
                                ),
                            }
                        )
                    except (
                        Exception
                    ) as exc:  # noqa: BLE001 - captured in validation summary
                        steps.append(
                            {
                                "name": "R5 Redis responds after recovery",
                                "category": "redis_recovery",
                                "command": [*redis_cli, "PING"],
                                "status": "failed",
                                "duration_seconds": round(
                                    time.monotonic() - recovery_started, 3
                                ),
                                "stdout_tail": "",
                                "stderr_tail": str(exc),
                            }
                        )

            if steps[-1]["status"] == "passed" and args.verify_redis_alert_transition:
                steps.append(
                    run_step(
                        "R5 restart Redis exporter after dependency recovery",
                        "prometheus_alert_runtime",
                        [
                            *compose_command,
                            "-f",
                            str(compose_file),
                            "restart",
                            "redis-exporter",
                        ],
                        args.step_timeout_seconds,
                    )
                )

            if steps[-1]["status"] == "passed" and args.verify_redis_alert_transition:
                assert alert_verifier is not None
                steps.append(
                    wait_background_step(
                        "R5 verify Prometheus Redis alert inactive/pending/firing/resolved",
                        "prometheus_alert_runtime",
                        alert_verifier_command,
                        alert_verifier,
                        max(args.step_timeout_seconds * 2, 720) + 30,
                    )
                )
                alert_verifier = None

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                steps.append(
                    run_step_expect_stdout(
                        "R5 verify Redis persistence marker after recovery",
                        "redis_recovery",
                        [*redis_cli, "--raw", "GET", marker_key],
                        args.step_timeout_seconds,
                        str(marker_value),
                    )
                )

            if steps[-1]["status"] == "passed" and args.include_redis_recovery:
                redis_post_step = run_step(
                    "R5 SDK full-flow after Redis recovery",
                    "redis_recovery",
                    [str(client), "127.0.0.1", "9201"],
                    args.step_timeout_seconds,
                )
                steps.append(redis_post_step)
                write_command_summary(
                    redis_sdk_summary,
                    "R5 SDK full-flow after Redis recovery",
                    redis_post_step,
                )

            if steps[-1]["status"] == "passed":
                prometheus_started = time.monotonic()
                try:
                    prometheus_doc = wait_for_prometheus_targets_up(
                        compose_command,
                        compose_file,
                        min(float(args.step_timeout_seconds), 90.0),
                    )
                    steps.append(
                        {
                            "name": "R5 Prometheus targets healthy before snapshot",
                            "category": "docker_snapshot",
                            "command": [
                                "GET",
                                "http://127.0.0.1:9090/api/v1/targets?state=active",
                            ],
                            "status": "passed",
                            "duration_seconds": round(
                                time.monotonic() - prometheus_started, 3
                            ),
                            "stdout_tail": json.dumps(
                                prometheus_doc, ensure_ascii=False, sort_keys=True
                            )[-6000:],
                            "stderr_tail": "",
                        }
                    )
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - captured in validation summary
                    steps.append(
                        {
                            "name": "R5 Prometheus targets healthy before snapshot",
                            "category": "docker_snapshot",
                            "command": [
                                "GET",
                                "http://127.0.0.1:9090/api/v1/targets?state=active",
                            ],
                            "status": "failed",
                            "duration_seconds": round(
                                time.monotonic() - prometheus_started, 3
                            ),
                            "stdout_tail": "",
                            "stderr_tail": str(exc),
                        }
                    )

            if steps[-1]["status"] == "passed":
                steps.append(
                    run_step(
                        "R5 Docker production snapshot after recovery",
                        "docker_snapshot",
                        [
                            sys.executable,
                            str(
                                REPO_ROOT
                                / "scripts/producers/collect_docker_production_perf_snapshot.py"
                            ),
                            "--output-dir",
                            str(REPO_ROOT / "runtime/perf/docker-production-snapshot"),
                        ],
                        args.step_timeout_seconds,
                    )
                )
        else:
            native_process = mode == "native-process"
            sdk_step = run_step(
                (
                    "R5 native gateway restart and SDK full-flow"
                    if native_process
                    else "R5 bounded-local SDK full-flow"
                ),
                "sdk_full_flow",
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/gates/sdk/verify_sdk_full_flow_client.py"),
                    "--build-dir",
                    str(build_dir),
                    "--skip-build",
                    "--summary-path",
                    str(sdk_summary),
                    *(["--restart-gateway"] if native_process else []),
                ],
                args.step_timeout_seconds,
            )
            steps.append(sdk_step)
            if native_process and sdk_summary.is_file():
                try:
                    sdk_document = json.loads(sdk_summary.read_text(encoding="utf-8"))
                    measured_rto_seconds = sdk_document.get(
                        "gateway_restart_rto_seconds"
                    )
                except (OSError, json.JSONDecodeError):
                    measured_rto_seconds = None

        drill_passed = all(step.get("status") == "passed" for step in steps)
        write_drill_record(
            record_path,
            recovery_summary,
            sdk_summary,
            redis_alert_summary,
            docker_snapshot_summary,
            monitoring_summary,
            drill_passed,
            include_redis_recovery=args.include_redis_recovery,
            verify_redis_alert_transition=args.verify_redis_alert_transition,
            failure_started_at=failure_started_at,
            failure_ended_at=failure_ended_at,
            measured_rto_seconds=measured_rto_seconds,
            mode=mode,
        )
        steps.append(
            run_step(
                "R5 recovery drill record validation",
                "recovery_drill_record",
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/gates/production/check_recovery_drill_record.py"),
                    "--record",
                    str(record_path),
                    "--summary-path",
                    str(record_check_summary),
                ],
                60,
            )
        )
    finally:
        if redis_fault_active:
            redis_recovery_services = ["redis"]
            restore_step = run_step(
                "R5 restore Redis after interrupted fault drill",
                "cleanup",
                [
                    *compose_command,
                    "-f",
                    str(compose_file),
                    "start",
                    *redis_recovery_services,
                ],
                args.step_timeout_seconds,
            )
            steps.append(restore_step)
            if restore_step["status"] == "passed":
                cleanup_started = time.monotonic()
                try:
                    redis_doc = wait_for_compose_redis(
                        compose_command,
                        compose_file,
                        min(float(args.step_timeout_seconds), 120.0),
                    )
                    redis_fault_active = False
                    steps.append(
                        {
                            "name": "R5 verify Redis ready after interrupted fault drill",
                            "category": "cleanup",
                            "command": ["redis-cli", "PING"],
                            "status": "passed",
                            "duration_seconds": round(
                                time.monotonic() - cleanup_started, 3
                            ),
                            "stdout_tail": json.dumps(redis_doc, sort_keys=True),
                            "stderr_tail": "",
                        }
                    )
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - cleanup failure belongs in summary
                    steps.append(
                        {
                            "name": "R5 verify Redis ready after interrupted fault drill",
                            "category": "cleanup",
                            "command": ["redis-cli", "PING"],
                            "status": "failed",
                            "duration_seconds": round(
                                time.monotonic() - cleanup_started, 3
                            ),
                            "stdout_tail": "",
                            "stderr_tail": str(exc),
                        }
                    )
        if alert_verifier is not None:
            steps.append(terminate_background_process(alert_verifier))
        if cleanup_needed and not args.leave_running:
            steps.append(
                run_step(
                    "R5 docker compose cleanup",
                    "cleanup",
                    [*compose_command, "-f", str(compose_file), "down"],
                    args.step_timeout_seconds,
                )
            )

    checks.append(
        {
            "name": "r5-real-platform-recovery-drill",
            "category": "preprod_recovery",
            "passed": mode in {"docker-compose", "native-process"}
            and all(step.get("status") == "passed" for step in steps),
            "mode": mode,
            "detail": (
                "Docker Compose gateway restart drill executed"
                if mode == "docker-compose"
                else (
                    "native gateway process restart drill executed"
                    if mode == "native-process"
                    else "bounded local mode used"
                )
            ),
        }
    )
    failed = next((step for step in steps if step.get("status") != "passed"), None)
    failed_check = next(
        (check for check in checks if check.get("passed") is not True), None
    )
    passed = failed is None and failed_check is None
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "provenance": build_evidence_provenance(
            REPO_ROOT,
            build_configuration=args.configuration,
        ),
        "overall_pass": passed,
        "passed": passed,
        "failed_category": (
            str(failed.get("category", ""))
            if failed
            else ("preprod_recovery" if failed_check else "")
        ),
        "failed_step": (
            str(failed.get("name", ""))
            if failed
            else (str(failed_check.get("name", "")) if failed_check else "")
        ),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "scope": {
            "mode": mode,
            "real_docker_compose_drill": mode == "docker-compose",
            "real_native_process_drill": mode == "native-process",
            "scenario": (
                "redis_recovery" if args.include_redis_recovery else "gateway_restart"
            ),
            "include_redis_recovery": args.include_redis_recovery,
            "verify_redis_alert_transition": args.verify_redis_alert_transition,
            "redis_alert_firing_timeout_seconds": args.redis_alert_firing_timeout_seconds,
            "docker_pull_policy": args.docker_pull_policy,
            "docker_target_platform": (
                args.docker_target_platform if mode == "docker-compose" else ""
            ),
        },
        "docker_image_preflight": image_preflight,
        "checks": checks,
        "steps": steps,
        "artifacts": {
            "summary_path": str(summary_path),
            "production_recovery_summary": str(recovery_summary),
            "sdk_full_flow_summary": str(sdk_summary),
            "redis_recovery_sdk_full_flow_summary": (
                str(redis_sdk_summary) if args.include_redis_recovery else ""
            ),
            "redis_alert_runtime_summary": (
                str(redis_alert_summary) if args.verify_redis_alert_transition else ""
            ),
            "docker_snapshot_summary": str(docker_snapshot_summary),
            "drill_record_path": str(record_path),
            "drill_record_check_summary": str(record_check_summary),
            "docker_image_preflight_summary": str(image_preflight_summary),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"preprod recovery drill: {'PASS' if passed else 'FAIL'}")
    print(f"summary: {summary_path}")
    return 0 if passed else 1

if __name__ == "__main__":
    raise SystemExit(main())
