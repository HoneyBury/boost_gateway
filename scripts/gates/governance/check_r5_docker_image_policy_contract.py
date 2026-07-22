#!/usr/bin/env python3
"""Exercise R5 Docker image pull policies without a real Docker daemon."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
APP_IMAGE = "test-stack-gateway"
REDIS_IMAGE = "redis:test"
PROM_IMAGE = "prom/test:v1"


FAKE_DOCKER = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
present_path = Path(os.environ["FAKE_DOCKER_PRESENT_FILE"])
log_path = Path(os.environ["FAKE_DOCKER_LOG_FILE"])
fixture_path = Path(os.environ["FAKE_DOCKER_COMPOSE_FIXTURE"])

def load_present():
    if not present_path.exists():
        return set()
    return {line for line in present_path.read_text(encoding="utf-8").splitlines() if line}

def save_present(images):
    present_path.write_text("\n".join(sorted(images)) + "\n", encoding="utf-8")

with log_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")

if args[:2] == ["compose", "version"]:
    print("Docker Compose version v2.fake")
    raise SystemExit(0)

if args and args[0] == "compose" and "config" in args:
    print(fixture_path.read_text(encoding="utf-8"))
    raise SystemExit(0)

if args and args[0] == "compose" and "pull" in args:
    if os.environ.get("FAKE_DOCKER_FAIL_COMPOSE_PULL") == "1":
        print("simulated compose pull failure", file=sys.stderr)
        raise SystemExit(1)
    document = json.loads(fixture_path.read_text(encoding="utf-8"))
    present = load_present()
    for service in document["services"].values():
        if service.get("image") and not service.get("build"):
            present.add(service["image"])
    save_present(present)
    raise SystemExit(0)

if args[:2] == ["image", "inspect"] and len(args) == 3:
    image = args[2]
    if image not in load_present():
        print(f"No such image: {image}", file=sys.stderr)
        raise SystemExit(1)
    safe_id = image.replace("/", "_").replace(":", "_")
    print(json.dumps([{
        "Id": "sha256:" + safe_id,
        "RepoDigests": [image + "@sha256:" + ("d" * 64)],
        "RepoTags": [image],
        "Created": "2026-07-13T00:00:00Z",
        "Os": "linux",
        "Architecture": os.environ.get("FAKE_DOCKER_ARCHITECTURE", "amd64")
    }]))
    raise SystemExit(0)

if args and args[0] == "run" and "/app/build-manifest.json" in args:
    print(os.environ["FAKE_DOCKER_BUILD_MANIFEST"])
    raise SystemExit(0)

if args and args[0] == "run" and "/app/bin/v2_gateway_demo" in args:
    print(os.environ["FAKE_DOCKER_BINARY_SHA256"] + "  /app/bin/v2_gateway_demo")
    raise SystemExit(0)

if args and args[0] == "pull":
    image = args[-1]
    if os.environ.get("FAKE_DOCKER_FAIL_PULL") == image:
        print(f"simulated pull failure: {image}", file=sys.stderr)
        raise SystemExit(1)
    present = load_present()
    present.add(image)
    save_present(present)
    raise SystemExit(0)

print("unsupported fake docker command: " + " ".join(args), file=sys.stderr)
raise SystemExit(2)
'''


def write_fixture(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "name": "test-stack",
                "services": {
                    "gateway": {"build": {"context": "."}},
                    "prometheus": {"image": PROM_IMAGE},
                    "redis": {"image": REDIS_IMAGE},
                },
            }
        ),
        encoding="utf-8",
    )


def run_case(
    root: Path,
    name: str,
    *,
    policy: str,
    present: set[str],
    fail_pull: str = "",
    fail_compose_pull: bool = False,
    target_platform: str = "linux/amd64",
    image_architecture: str = "amd64",
) -> tuple[int, dict[str, Any], list[list[str]], str]:
    case_root = root / name
    bin_dir = case_root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_path = bin_dir / "docker"
    docker_path.write_text(FAKE_DOCKER, encoding="utf-8")
    docker_path.chmod(docker_path.stat().st_mode | stat.S_IXUSR)

    fixture = case_root / "compose.json"
    present_path = case_root / "present.txt"
    log_path = case_root / "docker.log"
    summary_path = case_root / "summary.json"
    write_fixture(fixture)
    present_path.write_text("\n".join(sorted(present)) + ("\n" if present else ""), encoding="utf-8")

    environment = os.environ.copy()
    candidate_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    lockfile = ROOT / "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock"
    binary_sha256 = "a" * 64
    build_manifest = {
        "schema_version": 1,
        "git_revision": candidate_revision,
        "dependency_provider": "conan",
        "worktree_clean": True,
        "conan_lockfile": str(lockfile.relative_to(ROOT)),
        "conan_lockfile_sha256": hashlib.sha256(lockfile.read_bytes()).hexdigest(),
        "binaries": [{"name": "v2_gateway_demo", "sha256": binary_sha256}],
    }
    environment.update(
        {
            "PATH": str(bin_dir) + os.pathsep + environment.get("PATH", ""),
            "FAKE_DOCKER_PRESENT_FILE": str(present_path),
            "FAKE_DOCKER_LOG_FILE": str(log_path),
            "FAKE_DOCKER_COMPOSE_FIXTURE": str(fixture),
            "FAKE_DOCKER_FAIL_PULL": fail_pull,
            "FAKE_DOCKER_FAIL_COMPOSE_PULL": "1" if fail_compose_pull else "0",
            "FAKE_DOCKER_BUILD_MANIFEST": json.dumps(build_manifest),
            "FAKE_DOCKER_BINARY_SHA256": binary_sha256,
            "FAKE_DOCKER_ARCHITECTURE": image_architecture,
            "BOOST_GATEWAY_CANDIDATE_REVISION": candidate_revision,
            "BOOST_GATEWAY_CONAN_LOCKFILE": str(lockfile.relative_to(ROOT)),
        }
    )
    command = [
        sys.executable,
        str(ROOT / "scripts/verify_preprod_recovery_drill.py"),
        "--mode",
        "docker-compose",
        "--image-preflight-only",
        "--docker-pull-policy",
        policy,
        "--docker-target-platform",
        target_platform,
        "--docker-pull-attempts",
        "1",
        "--step-timeout-seconds",
        "10",
        "--image-preflight-summary-path",
        str(summary_path),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    log = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()] if log_path.exists() else []
    return completed.returncode, summary, log, completed.stdout


def has_pull(log: list[list[str]], image: str = "") -> bool:
    for command in log:
        if command and command[0] == "pull" and (not image or command[-1] == image):
            return True
        if command and command[0] == "compose" and "pull" in command and not image:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/r5-docker-image-policy-contract-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    checks: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="boost-gateway-r5-images-") as temp:
        temp_root = Path(temp)
        all_images = {APP_IMAGE, REDIS_IMAGE, PROM_IMAGE}

        returncode, summary, log, output = run_case(
            temp_root, "never-cached", policy="never", present=all_images
        )
        checks.append(
            {
                "name": "never-uses-complete-cache-without-network",
                "passed": returncode == 0
                and summary.get("overall_pass") is True
                and not has_pull(log)
                and all(item.get("image_id") for item in summary.get("image_inventory", [])),
                "detail": output[-2000:],
            }
        )

        returncode, summary, log, output = run_case(
            temp_root,
            "arm64-cached",
            policy="never",
            present=all_images,
            target_platform="linux/arm64",
            image_architecture="arm64",
        )
        checks.append(
            {
                "name": "arm64-uses-complete-native-cache",
                "passed": returncode == 0
                and summary.get("overall_pass") is True
                and summary.get("target_platform") == "linux/arm64"
                and not has_pull(log),
                "detail": output[-2000:],
            }
        )

        returncode, summary, log, output = run_case(
            temp_root,
            "arm64-rejects-amd64",
            policy="never",
            present=all_images,
            target_platform="linux/arm64",
            image_architecture="amd64",
        )
        checks.append(
            {
                "name": "arm64-rejects-amd64-cache",
                "passed": returncode != 0
                and sorted(summary.get("wrong_platform_images", [])) == sorted(all_images)
                and not has_pull(log),
                "detail": output[-2000:],
            }
        )

        returncode, summary, log, output = run_case(
            temp_root,
            "never-missing",
            policy="never",
            present={APP_IMAGE, REDIS_IMAGE},
        )
        checks.append(
            {
                "name": "never-rejects-missing-registry-image",
                "passed": returncode != 0
                and PROM_IMAGE in summary.get("missing_images", [])
                and not has_pull(log),
                "detail": output[-2000:],
            }
        )

        returncode, summary, log, output = run_case(
            temp_root,
            "missing-pulls-one",
            policy="missing",
            present={APP_IMAGE, REDIS_IMAGE},
        )
        checks.append(
            {
                "name": "missing-pulls-only-absent-registry-images",
                "passed": returncode == 0
                and summary.get("overall_pass") is True
                and has_pull(log, PROM_IMAGE)
                and not has_pull(log, REDIS_IMAGE),
                "detail": output[-2000:],
            }
        )

        returncode, summary, log, output = run_case(
            temp_root,
            "missing-build-image",
            policy="missing",
            present={REDIS_IMAGE, PROM_IMAGE},
        )
        checks.append(
            {
                "name": "missing-rejects-unbuilt-application-image",
                "passed": returncode != 0
                and APP_IMAGE in summary.get("missing_build_images", [])
                and not has_pull(log, APP_IMAGE),
                "detail": output[-2000:],
            }
        )

        returncode, summary, log, output = run_case(
            temp_root,
            "missing-pull-fails",
            policy="missing",
            present={APP_IMAGE, REDIS_IMAGE},
            fail_pull=PROM_IMAGE,
        )
        checks.append(
            {
                "name": "missing-surfaces-registry-pull-failure",
                "passed": returncode != 0
                and summary.get("failed_category") == "docker_image_pull"
                and PROM_IMAGE in summary.get("missing_images", []),
                "detail": output[-2000:],
            }
        )

        returncode, summary, log, output = run_case(
            temp_root,
            "always-pull-fails",
            policy="always",
            present=all_images,
            fail_compose_pull=True,
        )
        checks.append(
            {
                "name": "always-surfaces-refresh-failure-even-with-cache",
                "passed": returncode != 0
                and summary.get("failed_category") == "docker_image_pull"
                and has_pull(log),
                "detail": output[-2000:],
            }
        )

    failed = [check for check in checks if check.get("passed") is not True]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "r5_docker_image_policy_contract" if failed else "",
        "failed_step": str(failed[0]["name"]) if failed else "",
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        "R5 Docker image policy contract: "
        f"{'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
