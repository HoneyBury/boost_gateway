"""Pre-production recovery responsibility module: recovery_drill_preflight."""

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

from scripts.lib.evidence_provenance import build_evidence_provenance
from scripts.lib.recovery_evidence import (
    write_command_summary,
    write_drill_record as _write_drill_record,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_IMAGE_BINARIES = {
    "gateway": ("v2_gateway_demo", "/app/bin/v2_gateway_demo"),
    "login-backend": ("v2_login_backend", "/app/bin/backend"),
    "room-backend": ("v2_room_backend", "/app/bin/backend"),
    "battle-backend": ("v2_battle_backend", "/app/bin/backend"),
    "matchmaking-backend": ("v2_match_backend", "/app/bin/backend"),
    "leaderboard-backend": ("v2_leaderboard_backend", "/app/bin/backend"),
}



from scripts.lib.recovery_drill_runtime import *  # noqa: F401,F403
from scripts.lib.recovery_drill_contract import *  # noqa: F401,F403
from scripts.lib.recovery_drill_images import *  # noqa: F401,F403

RECOVERY_DRILL_ARGUMENTS = (
    ("--build-dir", {"type": Path, "default": REPO_ROOT / "build/release"}),
    ("--configuration", {"default": "Release"}),
    ("--mode", {"choices": ["auto", "docker-compose", "native-process", "bounded-local"], "default": "auto"}),
    ("--leave-running", {"action": "store_true"}),
    ("--include-redis-recovery", {"action": "store_true", "help": "Stop and recover Compose Redis while validating SDK degradation and persisted data."}),
    ("--verify-redis-alert-transition", {"action": "store_true", "help": "Verify the leaderboard dependency alert transition during Redis recovery."}),
    ("--redis-alert-firing-timeout-seconds", {"type": float, "default": 240.0}),
    ("--step-timeout-seconds", {"type": int, "default": 300}),
    ("--docker-pull-attempts", {"type": int, "default": 3}),
    ("--docker-target-platform", {"choices": ["linux/amd64", "linux/arm64"], "default": "linux/amd64"}),
    ("--docker-pull-policy", {"choices": ["always", "missing", "never"], "default": "missing"}),
    ("--image-preflight-only", {"action": "store_true"}),
    ("--image-preflight-summary-path", {"type": Path, "default": REPO_ROOT / "runtime/validation/r5-docker-image-preflight-summary.json"}),
    ("--candidate-revision", {"default": os.environ.get("BOOST_GATEWAY_CANDIDATE_REVISION") or repository_revision()}),
    ("--sdk-leaderboard-probe", {"action": "store_true", "hidden": True}),
    ("--sdk-library", {"type": Path, "hidden": True}),
    ("--gateway-host", {"default": "127.0.0.1", "hidden": True}),
    ("--gateway-port", {"type": int, "default": 9201, "hidden": True}),
    ("--summary-path", {"type": Path, "default": REPO_ROOT / "runtime/validation/preprod-recovery-drill-summary.json"}),
)


def prepare_drill_context(args: argparse.Namespace) -> dict[str, Any]:
    summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path
    build_dir = args.build_dir if args.build_dir.is_absolute() else REPO_ROOT / args.build_dir
    compose_file = REPO_ROOT / "env/docker/docker-compose.yml"
    image_summary = (
        args.image_preflight_summary_path
        if args.image_preflight_summary_path.is_absolute()
        else REPO_ROOT / args.image_preflight_summary_path
    )
    mode = args.mode
    compose_command = docker_compose_command() if mode in {"auto", "docker-compose"} else []
    if mode == "auto":
        mode = "docker-compose" if compose_build_images_present(compose_command, compose_file) else "bounded-local"
        if mode == "bounded-local":
            compose_command = []
    if args.include_redis_recovery and mode != "docker-compose":
        raise ValueError("--include-redis-recovery requires --mode docker-compose or cached Compose images")
    if mode == "docker-compose" and not args.candidate_revision:
        raise ValueError("docker-compose mode requires --candidate-revision or a resolvable Git HEAD")
    if args.verify_redis_alert_transition and not args.include_redis_recovery:
        raise ValueError("--verify-redis-alert-transition requires --include-redis-recovery")
    if args.verify_redis_alert_transition and args.redis_alert_firing_timeout_seconds <= 0:
        raise ValueError("--redis-alert-firing-timeout-seconds must be positive")
    if mode == "docker-compose":
        os.environ["DOCKER_DEFAULT_PLATFORM"] = args.docker_target_platform
    return {
        "summary_path": summary_path,
        "build_dir": build_dir,
        "validation_dir": summary_path.parent,
        "compose_file": compose_file,
        "image_preflight_summary": image_summary,
        "mode": mode,
        "compose_command": compose_command,
    }


def run_preflight_only(args: argparse.Namespace, context: dict[str, Any]) -> int:
    if context["mode"] != "docker-compose":
        raise ValueError("--image-preflight-only requires Docker Compose images or --mode docker-compose")
    result = run_docker_image_preflight(
        context["compose_command"], context["compose_file"],
        pull_policy=args.docker_pull_policy, pull_attempts=args.docker_pull_attempts,
        timeout_seconds=args.step_timeout_seconds,
        candidate_revision=args.candidate_revision, target_platform=args.docker_target_platform,
    )
    write_image_preflight_summary(
        context["image_preflight_summary"], result, configuration=args.configuration
    )
    print(f"R5 Docker image preflight: {'PASS' if result.get('passed') is True else 'FAIL'}")
    print(f"summary: {context['image_preflight_summary']}")
    return 0 if result.get("passed") is True else 1
def run_docker_image_preflight(
    compose_command: list[str],
    compose_file: Path,
    *,
    pull_policy: str,
    pull_attempts: int,
    timeout_seconds: int,
    candidate_revision: str = "",
    target_platform: str = "linux/amd64",
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    requirement_step, requirements = resolve_compose_image_requirements(
        compose_command, compose_file
    )
    steps.append(requirement_step)
    if requirement_step["status"] != "passed":
        return {
            "passed": False,
            "pull_policy": pull_policy,
            "target_platform": target_platform,
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
            target_platform=target_platform,
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
                    ["docker", "pull", "--platform", target_platform, image],
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
                "stdout_tail": (
                    "all required registry images are cached"
                    if pull_policy == "missing"
                    else "network access disabled by policy"
                ),
                "stderr_tail": "",
            }
        )

    final_inventory = inspect_required_images(requirements)
    if candidate_revision:
        final_inventory = inspect_build_image_manifests(
            final_inventory, candidate_revision
        )
    final_step = image_inventory_step(
        "R5 verify required Docker images after pull policy",
        final_inventory,
        fail_on_missing=True,
        target_platform=target_platform,
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
    prior_failure = next(
        (step for step in steps if step.get("status") != "passed"), None
    )
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
        "target_platform": target_platform,
        "requirements": requirements,
        "inventory": final_inventory,
        "missing_images": final_step["missing_images"],
        "wrong_platform_images": final_step["wrong_platform_images"],
        "missing_build_images": missing_build_images,
        "stale_build_images": final_step["stale_build_images"],
        "candidate_revision": candidate_revision,
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
    raise TimeoutError(
        f"timed out waiting for Prometheus targets to become healthy: {last_error}"
    )


def wait_for_compose_redis(
    compose_command: list[str], compose_file: Path, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        completed = subprocess.run(
            [
                *compose_command,
                "-f",
                str(compose_file),
                "exec",
                "-T",
                "redis",
                "redis-cli",
                "ping",
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
        if completed.returncode == 0 and completed.stdout.strip() == "PONG":
            return {"attempts": attempts, "response": "PONG"}
        last_error = tail(completed.stderr or completed.stdout, 1000)
        time.sleep(1.0)
    raise TimeoutError(f"timed out waiting for Redis recovery: {last_error}")


def compose_build_images_present(
    compose_command: list[str], compose_file: Path
) -> bool:
    step, requirements = resolve_compose_image_requirements(
        compose_command, compose_file
    )
    if step.get("status") != "passed":
        return False
    build_requirements = [
        item for item in requirements if item.get("source") == "build"
    ]
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
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
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
            "docker_target_platform": result.get("target_platform", ""),
        },
        "target_platform": result.get("target_platform", ""),
        "required_images": result.get("requirements", []),
        "image_inventory": result.get("inventory", []),
        "missing_images": result.get("missing_images", []),
        "missing_build_images": result.get("missing_build_images", []),
        "wrong_platform_images": result.get("wrong_platform_images", []),
        "stale_build_images": result.get("stale_build_images", []),
        "candidate_revision": result.get("candidate_revision", ""),
        "steps": result.get("steps", []),
        "artifacts": {"summary_path": str(path)},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
