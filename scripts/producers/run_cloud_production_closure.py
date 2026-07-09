#!/usr/bin/env python3
"""Run cloud-production closure for deployment, rollback, and evidence collection."""

from __future__ import annotations

import argparse
import json
import platform
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(name: str, category: str, cmd: list[str], timeout_seconds: int) -> dict[str, object]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
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
            "command": cmd,
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout),
            "stderr_tail": tail(exc.stderr),
        }

    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(completed.stdout),
        "stderr_tail": tail(completed.stderr),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/release"))
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--include-compose", action="store_true")
    parser.add_argument("--include-kind", action="store_true")
    parser.add_argument("--include-production-evidence", action="store_true")
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/cloud-production-closure-summary.json"))
    return parser.parse_args()


def environment_snapshot() -> dict[str, object]:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "host": socket.gethostname(),
        "cwd": str(ROOT),
    }


def main() -> int:
    args = parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    steps: list[dict[str, object]] = []

    steps.append(run_step(
        "cloud production preflight",
        "preflight",
        [
            sys.executable,
            str(ROOT / "scripts" / "check_fixed_runner_environment.py"),
            "--profile",
            "cloud-production",
            "--build-dir",
            str(args.build_dir),
            "--summary-path",
            str(ROOT / "runtime/validation/cloud-production-preflight-summary.json"),
        ],
        120,
    ))

    common = ["--build-dir", str(args.build_dir), "--configuration", args.configuration]
    if args.skip_build:
        common.append("--skip-build")

    steps.append(run_step(
        "deploy operability gate",
        "deploy_operability",
        [
            sys.executable,
            str(ROOT / "scripts" / "check_deploy_operability.py"),
            *common,
            "--summary-path",
            str(ROOT / "runtime/validation/cloud-deploy-operability-summary.json"),
        ],
        180,
    ))

    if args.include_compose:
        steps.append(run_step(
            "docker compose config validation",
            "compose",
            ["docker", "compose", "-f", "env/docker/docker-compose.yml", "config", "--quiet"],
            180,
        ))
        steps.append(run_step(
            "docker compose deployment",
            "compose",
            ["docker", "compose", "-f", "env/docker/docker-compose.yml", "up", "-d", "--build"],
            3600,
        ))
        steps.append(run_step(
            "docker production snapshot",
            "compose",
            [sys.executable, str(ROOT / "scripts" / "collect_docker_production_perf_snapshot.py")],
            600,
        ))

    if args.include_kind:
        steps.append(run_step(
            "kubernetes deploy dry-run",
            "k8s",
            [sys.executable, str(ROOT / "scripts" / "deploy_k8s.py"), "--dry-run"],
            300,
        ))
        steps.append(run_step(
            "control-plane kind gate",
            "k8s",
            [
                sys.executable,
                str(ROOT / "scripts" / "verify_control_plane_gate.py"),
                "--include-kind",
                "--summary-path",
                str(ROOT / "runtime/validation/cloud-kind-control-plane-summary.json"),
            ],
            1800,
        ))

    if args.include_production_evidence:
        steps.append(run_step(
            "production evidence aggregation",
            "production_evidence",
            [
                sys.executable,
                str(ROOT / "scripts" / "verify_production_evidence_gate.py"),
                *common,
                "--include-release-baseline",
                "--summary-path",
                str(ROOT / "runtime/validation/cloud-production-evidence-summary.json"),
            ],
            3600,
        ))

    failed = next((step for step in steps if step.get("status") != "passed"), None)
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(args.build_dir.resolve()),
        "configuration": args.configuration,
        "include_compose": args.include_compose,
        "include_kind": args.include_kind,
        "include_production_evidence": args.include_production_evidence,
        "environment": environment_snapshot(),
        "overall_pass": failed is None,
        "passed": failed is None,
        "failed_category": "" if failed is None else str(failed.get("category")),
        "failed_step": "" if failed is None else str(failed.get("name")),
        "artifacts": {
            "summary_path": str(summary_path),
            "preflight_summary_path": str(ROOT / "runtime/validation/cloud-production-preflight-summary.json"),
            "deploy_operability_summary_path": str(ROOT / "runtime/validation/cloud-deploy-operability-summary.json"),
            "docker_snapshot_summary_path": str(ROOT / "runtime/perf/docker-production-snapshot/summary.json") if args.include_compose else "",
            "control_plane_summary_path": str(ROOT / "runtime/validation/cloud-kind-control-plane-summary.json") if args.include_kind else "",
            "production_evidence_summary_path": str(ROOT / "runtime/validation/cloud-production-evidence-summary.json") if args.include_production_evidence else "",
        },
        "steps": steps,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

