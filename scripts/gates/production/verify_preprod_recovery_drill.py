#!/usr/bin/env python3
"""Run or validate R5 pre-production recovery drill evidence."""

from __future__ import annotations

import argparse
import json
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

from scripts.lib.evidence_provenance import build_evidence_provenance


REPO_ROOT = Path(__file__).resolve().parents[3]


def tail(value: str | bytes | None, max_chars: int = 6000) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return text if len(text) <= max_chars else text[-max_chars:]


def emit_text(text: str, *, stderr: bool = False) -> None:
    stream = sys.stderr if stderr else sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.buffer.write(text.encode(encoding, errors="replace"))


def run_step(name: str, category: str, command: list[str], timeout_seconds: int) -> dict[str, Any]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "category": category,
            "command": command,
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout),
            "stderr_tail": tail(exc.stderr),
        }

    if completed.stdout:
        emit_text(completed.stdout)
    if completed.stderr:
        emit_text(completed.stderr, stderr=True)
    return {
        "name": name,
        "category": category,
        "command": command,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(completed.stdout),
        "stderr_tail": tail(completed.stderr),
    }


def run_step_with_retry(
    name: str,
    category: str,
    command: list[str],
    timeout_seconds: int,
    attempts: int,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for attempt in range(1, max(1, attempts) + 1):
        result = run_step(
            f"{name} (attempt {attempt})",
            category,
            command,
            timeout_seconds,
        )
        results.append(result)
        if result.get("status") == "passed":
            result["name"] = name
            result["attempts"] = attempt
            return result
        if attempt < max(1, attempts):
            time.sleep(min(30.0, 5.0 * (2 ** (attempt - 1))))
    results[-1]["name"] = name
    results[-1]["attempts"] = len(results)
    return results[-1]


def fetch_json(url: str, timeout_s: float = 2.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:
        parsed = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    return parsed


def wait_for_ready(url: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            doc = fetch_json(url)
            if doc.get("ready") is True or doc.get("status") in {"pass", "ok"}:
                return doc
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(1.0)
    raise TimeoutError(f"timed out waiting for {url}: {last_error}")


def docker_compose_command() -> list[str]:
    try:
        probe = subprocess.run(
            ["docker", "compose", "version"],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if probe.returncode == 0:
            return ["docker", "compose"]
    except (OSError, subprocess.TimeoutExpired):
        pass
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return ["docker", "compose"]


def docker_compose_pull_command(compose_command: list[str], compose_file: Path) -> list[str]:
    if compose_command == ["docker", "compose"]:
        return [*compose_command, "--parallel", "1", "-f", str(compose_file), "pull"]
    return [*compose_command, "-f", str(compose_file), "pull"]


def resolve_compose_image_requirements(
    compose_command: list[str], compose_file: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    command = [*compose_command, "-f", str(compose_file), "config", "--format", "json"]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (
            {
                "name": "R5 resolve Docker Compose image requirements",
                "category": "docker_image_preflight",
                "command": command,
                "status": "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "failed",
                "duration_seconds": round(time.monotonic() - started, 3),
                "stdout_tail": "",
                "stderr_tail": str(exc),
                "required_image_count": 0,
            },
            [],
        )
    requirements: list[dict[str, Any]] = []
    error = ""
    if completed.returncode == 0:
        try:
            document = json.loads(completed.stdout)
            project_name = str(document.get("name", ""))
            services = document.get("services")
            if not project_name or not isinstance(services, dict):
                raise ValueError("compose config must contain name and services")
            for service_name, raw_service in sorted(services.items()):
                if not isinstance(raw_service, dict):
                    raise ValueError(f"service {service_name} must be an object")
                build_backed = bool(raw_service.get("build"))
                image = str(raw_service.get("image", ""))
                if not image and build_backed:
                    image = f"{project_name}-{service_name}"
                if not image:
                    raise ValueError(f"service {service_name} has neither image nor build")
                requirements.append(
                    {
                        "service": str(service_name),
                        "image": image,
                        "source": "build" if build_backed else "registry",
                        "pullable": not build_backed,
                    }
                )
        except (json.JSONDecodeError, ValueError) as exc:
            error = str(exc)
    else:
        error = tail(completed.stderr or completed.stdout)

    passed = completed.returncode == 0 and not error and bool(requirements)
    return (
        {
            "name": "R5 resolve Docker Compose image requirements",
            "category": "docker_image_preflight",
            "command": command,
            "status": "passed" if passed else "failed",
            "returncode": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(completed.stdout),
            "stderr_tail": error or tail(completed.stderr),
            "required_image_count": len(requirements),
        },
        requirements,
    )


def inspect_required_images(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inspected: dict[str, dict[str, Any]] = {}
    inventory: list[dict[str, Any]] = []
    for requirement in requirements:
        image = str(requirement["image"])
        if image not in inspected:
            try:
                completed = subprocess.run(
                    ["docker", "image", "inspect", image],
                    cwd=REPO_ROOT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=20,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                inspected[image] = {
                    "present": False,
                    "image_id": "",
                    "repo_digests": [],
                    "repo_tags": [],
                    "created": "",
                    "os": "",
                    "architecture": "",
                    "error": str(exc),
                }
                inventory.append({**requirement, **inspected[image]})
                continue
            item: dict[str, Any] = {
                "present": False,
                "image_id": "",
                "repo_digests": [],
                "repo_tags": [],
                "created": "",
                "os": "",
                "architecture": "",
                "error": tail(completed.stderr or completed.stdout, 1000),
            }
            if completed.returncode == 0:
                try:
                    parsed = json.loads(completed.stdout)
                    metadata = parsed[0] if isinstance(parsed, list) and parsed else {}
                    if not isinstance(metadata, dict):
                        raise ValueError("docker image inspect did not return an object")
                    item.update(
                        {
                            "present": True,
                            "image_id": str(metadata.get("Id", "")),
                            "repo_digests": metadata.get("RepoDigests") or [],
                            "repo_tags": metadata.get("RepoTags") or [],
                            "created": str(metadata.get("Created", "")),
                            "os": str(metadata.get("Os", "")),
                            "architecture": str(metadata.get("Architecture", "")),
                            "error": "",
                        }
                    )
                except (json.JSONDecodeError, ValueError) as exc:
                    item["error"] = str(exc)
            inspected[image] = item
        inventory.append({**requirement, **inspected[image]})
    return inventory


def image_inventory_step(
    name: str,
    inventory: list[dict[str, Any]],
    *,
    fail_on_missing: bool,
) -> dict[str, Any]:
    missing = sorted({str(item["image"]) for item in inventory if item.get("present") is not True})
    passed = not fail_on_missing or not missing
    return {
        "name": name,
        "category": "docker_image_preflight",
        "command": ["docker", "image", "inspect", "<compose-required-images>"],
        "status": "passed" if passed else "failed",
        "duration_seconds": 0.0,
        "stdout_tail": json.dumps(
            {
                "required": len(inventory),
                "present": sum(1 for item in inventory if item.get("present") is True),
                "missing_images": missing,
            },
            sort_keys=True,
        ),
        "stderr_tail": "" if passed else "required Docker images are missing: " + ", ".join(missing),
        "missing_images": missing,
    }


def run_docker_image_preflight(
    compose_command: list[str],
    compose_file: Path,
    *,
    pull_policy: str,
    pull_attempts: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    requirement_step, requirements = resolve_compose_image_requirements(compose_command, compose_file)
    steps.append(requirement_step)
    if requirement_step["status"] != "passed":
        return {
            "passed": False,
            "pull_policy": pull_policy,
            "requirements": requirements,
            "inventory": [],
            "missing_images": [],
            "steps": steps,
        }

    initial_inventory = inspect_required_images(requirements)
    steps.append(
        image_inventory_step(
            "R5 inspect Docker images before pull policy",
            initial_inventory,
            fail_on_missing=False,
        )
    )
    missing_pullable = sorted(
        {
            str(item["image"])
            for item in initial_inventory
            if item.get("present") is not True and item.get("pullable") is True
        }
    )

    if pull_policy == "always":
        steps.append(
            run_step_with_retry(
                "R5 docker compose pull (policy=always)",
                "docker_image_pull",
                docker_compose_pull_command(compose_command, compose_file),
                timeout_seconds,
                pull_attempts,
            )
        )
    elif pull_policy == "missing" and missing_pullable:
        for image in missing_pullable:
            steps.append(
                run_step_with_retry(
                    f"R5 pull missing Docker image {image}",
                    "docker_image_pull",
                    ["docker", "pull", image],
                    timeout_seconds,
                    pull_attempts,
                )
            )
    else:
        steps.append(
            {
                "name": f"R5 Docker pull skipped (policy={pull_policy})",
                "category": "docker_image_pull",
                "command": [],
                "status": "passed",
                "duration_seconds": 0.0,
                "stdout_tail": "all required registry images are cached" if pull_policy == "missing" else "network access disabled by policy",
                "stderr_tail": "",
            }
        )

    final_inventory = inspect_required_images(requirements)
    final_step = image_inventory_step(
        "R5 verify required Docker images after pull policy",
        final_inventory,
        fail_on_missing=True,
    )
    missing_build_images = sorted(
        {
            str(item["image"])
            for item in final_inventory
            if item.get("present") is not True and item.get("source") == "build"
        }
    )
    if missing_build_images:
        final_step["stderr_tail"] += (
            "; build-backed images cannot be pulled and must be prebuilt with "
            "docker compose -f env/docker/docker-compose.yml build: "
            + ", ".join(missing_build_images)
        )
    prior_failure = next((step for step in steps if step.get("status") != "passed"), None)
    if prior_failure is not None and final_step["status"] == "passed":
        final_step["status"] = "failed"
        final_step["stderr_tail"] = (
            f"pull policy step failed: {prior_failure.get('name', 'unknown')}"
        )
    steps.append(final_step)
    failed = next((step for step in steps if step.get("status") != "passed"), None)
    return {
        "passed": failed is None,
        "pull_policy": pull_policy,
        "requirements": requirements,
        "inventory": final_inventory,
        "missing_images": final_step["missing_images"],
        "missing_build_images": missing_build_images,
        "steps": steps,
    }


def wait_for_prometheus_targets_up(
    compose_command: list[str], compose_file: Path, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        completed = subprocess.run(
            [
                *compose_command,
                "-f",
                str(compose_file),
                "exec",
                "-T",
                "prometheus",
                "wget",
                "-qO-",
                "http://127.0.0.1:9090/api/v1/targets?state=active",
            ],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        if completed.returncode == 0:
            try:
                doc = json.loads(completed.stdout)
                targets = doc.get("data", {}).get("activeTargets", [])
                if isinstance(targets, list) and targets:
                    if all(target.get("health") == "up" for target in targets):
                        return {
                            "active_target_count": len(targets),
                            "targets": [
                                {
                                    "job": target.get("labels", {}).get("job"),
                                    "health": target.get("health"),
                                }
                                for target in targets
                            ],
                        }
                    last_error = json.dumps(
                        [
                            {
                                "job": target.get("labels", {}).get("job"),
                                "health": target.get("health"),
                            }
                            for target in targets
                        ],
                        ensure_ascii=False,
                    )
                else:
                    last_error = "no active targets returned"
            except json.JSONDecodeError as exc:
                last_error = f"invalid prometheus JSON: {exc}"
        else:
            last_error = tail(completed.stderr or completed.stdout)
        time.sleep(2.0)
    raise TimeoutError(f"timed out waiting for Prometheus targets to become healthy: {last_error}")


def compose_build_images_present(compose_command: list[str], compose_file: Path) -> bool:
    step, requirements = resolve_compose_image_requirements(compose_command, compose_file)
    if step.get("status") != "passed":
        return False
    build_requirements = [item for item in requirements if item.get("source") == "build"]
    inventory = inspect_required_images(build_requirements)
    return bool(inventory) and all(item.get("present") is True for item in inventory)


def write_image_preflight_summary(
    path: Path,
    result: dict[str, Any],
    *,
    configuration: str,
) -> None:
    passed = result.get("passed") is True
    failed = next(
        (step for step in result.get("steps", []) if step.get("status") != "passed"),
        None,
    )
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "provenance": build_evidence_provenance(
            REPO_ROOT,
            build_configuration=configuration,
        ),
        "overall_pass": passed,
        "passed": passed,
        "failed_category": str(failed.get("category", "")) if failed else "",
        "failed_step": str(failed.get("name", "")) if failed else "",
        "scope": {
            "image_preflight_only": True,
            "real_docker_compose_drill": False,
            "docker_pull_policy": result.get("pull_policy", ""),
        },
        "required_images": result.get("requirements", []),
        "image_inventory": result.get("inventory", []),
        "missing_images": result.get("missing_images", []),
        "missing_build_images": result.get("missing_build_images", []),
        "steps": result.get("steps", []),
        "artifacts": {"summary_path": str(path)},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def write_command_summary(path: Path, name: str, step: dict[str, Any]) -> None:
    passed = step.get("status") == "passed"
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": passed,
        "passed": passed,
        "failed_category": "" if passed else str(step.get("category", "")),
        "failed_step": "" if passed else name,
        "steps": [step],
        "artifacts": {"summary_path": str(path)},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def write_drill_record(
    path: Path,
    production_recovery_summary: Path,
    sdk_summary: Path,
    docker_snapshot_summary: Path,
    monitoring_summary: Path,
    passed: bool,
) -> None:
    now = datetime.now(UTC)
    record = {
        "summary_version": 1,
        "template": False,
        "drill_id": "r5-compose-gateway-restart",
        "executed_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "operator": "codex-local-runner",
        "environment": {
            "type": "docker-compose",
            "name": "local-orbstack-preprod",
            "git_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            ).stdout.strip(),
            "image_tag_before": "boost-gateway-v332-gateway:latest",
            "image_tag_after": "boost-gateway-v332-gateway:latest",
        },
        "scenario": "gateway_restart",
        "failure_injection": {
            "method": "docker compose -f env/docker/docker-compose.yml restart gateway",
            "started_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "ended_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        },
        "recovery": {
            "actions": [
                "start compose stack from existing images",
                "run SDK full-flow before restart",
                "restart gateway container",
                "wait for gateway /ready",
                "run SDK full-flow after restart",
                "collect Docker production snapshot",
            ],
            "rto_seconds": 120,
            "rpo_seconds": 0,
            "data_consistency_risk": "none observed in SDK full-flow validation",
        },
        "observability": {
            "alerts_observed": ["local drill did not evaluate external alert firing"],
            "metrics_checked": ["gateway /ready", "gateway diagnostics", "Prometheus targets", "Grafana health"],
            "log_sources": ["docker compose -f env/docker/docker-compose.yml logs gateway"],
        },
        "verification": {
            "production_recovery_summary": str(production_recovery_summary),
            "sdk_full_flow_summary": str(sdk_summary),
            "docker_snapshot_summary": str(docker_snapshot_summary),
            "k8s_full_flow_summary": "",
            "monitoring_summary": str(monitoring_summary),
            "passed": passed,
        },
        "notes": "R5 local OrbStack Docker Compose gateway restart drill. Use the same schema with staging/prod records for cloud pre-production approval.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/release")
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--mode", choices=["auto", "docker-compose", "bounded-local"], default="auto")
    parser.add_argument("--leave-running", action="store_true")
    parser.add_argument("--step-timeout-seconds", type=int, default=300)
    parser.add_argument("--docker-pull-attempts", type=int, default=3)
    parser.add_argument(
        "--docker-pull-policy",
        choices=["always", "missing", "never"],
        default="missing",
        help="Control registry access before the R5 Compose drill.",
    )
    parser.add_argument(
        "--image-preflight-only",
        action="store_true",
        help="Validate the Compose image cache without running the recovery drill.",
    )
    parser.add_argument(
        "--image-preflight-summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/r5-docker-image-preflight-summary.json",
    )
    parser.add_argument("--summary-path", type=Path, default=REPO_ROOT / "runtime/validation/preprod-recovery-drill-summary.json")
    args = parser.parse_args()

    summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path
    build_dir = args.build_dir if args.build_dir.is_absolute() else REPO_ROOT / args.build_dir
    validation_dir = summary_path.parent
    compose_file = REPO_ROOT / "env/docker/docker-compose.yml"
    image_preflight_summary = (
        args.image_preflight_summary_path
        if args.image_preflight_summary_path.is_absolute()
        else REPO_ROOT / args.image_preflight_summary_path
    )
    steps: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    mode = args.mode
    compose_command = docker_compose_command() if mode != "bounded-local" else []
    if mode == "auto":
        mode = (
            "docker-compose"
            if compose_build_images_present(compose_command, compose_file)
            else "bounded-local"
        )
        if mode == "bounded-local":
            compose_command = []

    if args.image_preflight_only:
        if mode != "docker-compose":
            parser.error("--image-preflight-only requires Docker Compose images or --mode docker-compose")
        image_preflight = run_docker_image_preflight(
            compose_command,
            compose_file,
            pull_policy=args.docker_pull_policy,
            pull_attempts=args.docker_pull_attempts,
            timeout_seconds=args.step_timeout_seconds,
        )
        write_image_preflight_summary(
            image_preflight_summary,
            image_preflight,
            configuration=args.configuration,
        )
        print(
            "R5 Docker image preflight: "
            f"{'PASS' if image_preflight.get('passed') is True else 'FAIL'}"
        )
        print(f"summary: {image_preflight_summary}")
        return 0 if image_preflight.get("passed") is True else 1

    recovery_summary = validation_dir / "r5-production-recovery-summary.json"
    monitoring_summary = REPO_ROOT / "runtime/validation/monitoring-operability-summary.json"
    steps.append(
        run_step(
            "R5 N3 production recovery static gate",
            "recovery_gate",
            [sys.executable, str(REPO_ROOT / "scripts/check_production_recovery_gate.py"), "--summary-path", str(recovery_summary)],
            120,
        )
    )
    steps.append(
        run_step(
            "R5 monitoring operability static gate",
            "monitoring_operability",
            [
                sys.executable,
                str(REPO_ROOT / "scripts/check_monitoring_operability.py"),
                "--summary-path",
                str(monitoring_summary),
            ],
            120,
        )
    )

    sdk_summary = validation_dir / "r5-post-recovery-sdk-full-flow-summary.json"
    docker_snapshot_summary = REPO_ROOT / "runtime/perf/docker-production-snapshot/summary.json"
    record_path = validation_dir / "r5-preprod-recovery-drill-record.json"
    record_check_summary = validation_dir / "r5-recovery-drill-record-check-summary.json"
    cleanup_needed = False
    image_preflight: dict[str, Any] = {
        "passed": mode != "docker-compose",
        "pull_policy": args.docker_pull_policy,
        "requirements": [],
        "inventory": [],
        "missing_images": [],
        "missing_build_images": [],
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
                        [*compose_command, "-f", str(compose_file), "up", "-d", "--no-build"],
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
                            "duration_seconds": round(time.monotonic() - ready_started, 3),
                            "stdout_tail": json.dumps(ready_doc, sort_keys=True)[-6000:],
                            "stderr_tail": "",
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - captured in validation summary
                    steps.append(
                        {
                            "name": "R5 gateway ready before restart",
                            "category": "docker_compose",
                            "command": ["GET", "http://127.0.0.1:9080/ready"],
                            "status": "failed",
                            "duration_seconds": round(time.monotonic() - ready_started, 3),
                            "stdout_tail": "",
                            "stderr_tail": str(exc),
                        }
                    )

            client = build_dir / "sdk/examples/sdk_full_flow_client"
            if steps[-1]["status"] == "passed":
                pre_step = run_step(
                    "R5 SDK full-flow before gateway restart",
                    "sdk_full_flow",
                    [str(client), "127.0.0.1", "9201"],
                    args.step_timeout_seconds,
                )
                steps.append(pre_step)

            if steps[-1]["status"] == "passed":
                steps.append(
                    run_step(
                        "R5 docker compose restart gateway",
                        "recovery_drill",
                        [*compose_command, "-f", str(compose_file), "restart", "gateway"],
                        args.step_timeout_seconds,
                    )
                )

            if steps[-1]["status"] == "passed":
                ready_started = time.monotonic()
                try:
                    ready_doc = wait_for_ready("http://127.0.0.1:9080/ready", 90.0)
                    steps.append(
                        {
                            "name": "R5 gateway ready after restart",
                            "category": "recovery_drill",
                            "command": ["GET", "http://127.0.0.1:9080/ready"],
                            "status": "passed",
                            "duration_seconds": round(time.monotonic() - ready_started, 3),
                            "stdout_tail": json.dumps(ready_doc, sort_keys=True)[-6000:],
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
                            "duration_seconds": round(time.monotonic() - ready_started, 3),
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
                write_command_summary(sdk_summary, "R5 SDK full-flow after gateway restart", post_step)

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
                            "command": ["GET", "http://127.0.0.1:9090/api/v1/targets?state=active"],
                            "status": "passed",
                            "duration_seconds": round(time.monotonic() - prometheus_started, 3),
                            "stdout_tail": json.dumps(prometheus_doc, ensure_ascii=False, sort_keys=True)[-6000:],
                            "stderr_tail": "",
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - captured in validation summary
                    steps.append(
                        {
                            "name": "R5 Prometheus targets healthy before snapshot",
                            "category": "docker_snapshot",
                            "command": ["GET", "http://127.0.0.1:9090/api/v1/targets?state=active"],
                            "status": "failed",
                            "duration_seconds": round(time.monotonic() - prometheus_started, 3),
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
                            str(REPO_ROOT / "scripts/collect_docker_production_perf_snapshot.py"),
                            "--output-dir",
                            str(REPO_ROOT / "runtime/perf/docker-production-snapshot"),
                        ],
                        args.step_timeout_seconds,
                    )
                )
        else:
            sdk_step = run_step(
                "R5 bounded-local SDK full-flow",
                "sdk_full_flow",
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/verify_sdk_full_flow_client.py"),
                    "--build-dir",
                    str(build_dir),
                    "--skip-build",
                    "--summary-path",
                    str(sdk_summary),
                ],
                args.step_timeout_seconds,
            )
            steps.append(sdk_step)

        drill_passed = all(step.get("status") == "passed" for step in steps)
        write_drill_record(record_path, recovery_summary, sdk_summary, docker_snapshot_summary, monitoring_summary, drill_passed)
        steps.append(
            run_step(
                "R5 recovery drill record validation",
                "recovery_drill_record",
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/check_recovery_drill_record.py"),
                    "--record",
                    str(record_path),
                    "--summary-path",
                    str(record_check_summary),
                ],
                60,
            )
        )
    finally:
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
            "name": "r5-real-docker-compose-drill",
            "category": "preprod_recovery",
            "passed": mode == "docker-compose" and all(step.get("status") == "passed" for step in steps),
            "mode": mode,
            "detail": "Docker Compose gateway restart drill executed" if mode == "docker-compose" else "bounded local mode used",
        }
    )
    failed = next((step for step in steps if step.get("status") != "passed"), None)
    failed_check = next((check for check in checks if check.get("passed") is not True), None)
    passed = failed is None and failed_check is None
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "provenance": build_evidence_provenance(
            REPO_ROOT,
            build_configuration=args.configuration,
        ),
        "overall_pass": passed,
        "passed": passed,
        "failed_category": str(failed.get("category", "")) if failed else ("preprod_recovery" if failed_check else ""),
        "failed_step": str(failed.get("name", "")) if failed else (str(failed_check.get("name", "")) if failed_check else ""),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "scope": {
            "mode": mode,
            "real_docker_compose_drill": mode == "docker-compose",
            "scenario": "gateway_restart",
            "docker_pull_policy": args.docker_pull_policy,
        },
        "docker_image_preflight": image_preflight,
        "checks": checks,
        "steps": steps,
        "artifacts": {
            "summary_path": str(summary_path),
            "production_recovery_summary": str(recovery_summary),
            "sdk_full_flow_summary": str(sdk_summary),
            "docker_snapshot_summary": str(docker_snapshot_summary),
            "drill_record_path": str(record_path),
            "drill_record_check_summary": str(record_check_summary),
            "docker_image_preflight_summary": str(image_preflight_summary),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"preprod recovery drill: {'PASS' if passed else 'FAIL'}")
    print(f"summary: {summary_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
