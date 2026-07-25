#!/usr/bin/env python3
"""Run R6 TLS pre-production multi-run evidence aggregation."""

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
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lib.evidence_provenance import build_evidence_provenance


REPO_ROOT = Path(__file__).resolve().parents[3]


def tail(value: str | bytes | None, max_chars: int = 5000) -> str:
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


def load_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_run_metrics(path: Path) -> dict[str, Any]:
    summary = load_json(path)
    perf = summary.get("performance_comparison") if isinstance(summary.get("performance_comparison"), dict) else {}
    steps = summary.get("steps")
    step_status = {}
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                step_status[str(step.get("category", step.get("name", "unknown")))] = step.get("status")
    return {
        "summary_path": str(path),
        "passed": summary.get("overall_pass", summary.get("passed")) is True,
        "failed_category": summary.get("failed_category", ""),
        "failed_step": summary.get("failed_step", ""),
        "plain_full_flow_seconds": perf.get("plain_full_flow_seconds"),
        "tls_full_flow_seconds": perf.get("tls_full_flow_seconds"),
        "overhead_ratio": perf.get("overhead_ratio"),
        "step_status": step_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/release")
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--step-timeout-seconds", type=int, default=240)
    parser.add_argument("--max-overhead-ratio", type=float, default=5.0)
    parser.add_argument("--summary-path", type=Path, default=REPO_ROOT / "runtime/validation/tls-preprod-multi-run-summary.json")
    args = parser.parse_args()

    summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path
    build_dir = args.build_dir if args.build_dir.is_absolute() else REPO_ROOT / args.build_dir
    validation_dir = summary_path.parent
    validation_dir.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, Any]] = []
    run_metrics: list[dict[str, Any]] = []

    for index in range(max(1, args.runs)):
        run_summary = validation_dir / f"r6-tls-production-readiness-run{index + 1}.json"
        work_dir = REPO_ROOT / "runtime" / "tls-preprod" / f"run{index + 1}"
        command = [
            sys.executable,
            str(REPO_ROOT / "scripts/gates/transport/verify_tls_production_readiness.py"),
            "--build-dir",
            str(build_dir),
            "--step-timeout-seconds",
            str(args.step_timeout_seconds),
            "--max-full-flow-overhead-ratio",
            str(args.max_overhead_ratio),
            "--work-dir",
            str(work_dir),
            "--summary-path",
            str(run_summary),
        ]
        if args.skip_build:
            command.append("--skip-build")
        step = run_step(f"R6 TLS readiness multi-run {index + 1}", "tls_preprod_run", command, args.step_timeout_seconds * 8)
        steps.append(step)
        run_metrics.append(extract_run_metrics(run_summary))

    passed_runs = [metric for metric in run_metrics if metric.get("passed") is True]
    ratios = [float(metric["overhead_ratio"]) for metric in run_metrics if isinstance(metric.get("overhead_ratio"), (int, float))]
    max_observed_ratio = max(ratios) if ratios else None
    failed = next((step for step in steps if step.get("status") != "passed"), None)
    metrics_failed = next((metric for metric in run_metrics if metric.get("passed") is not True), None)
    ratio_failed = max_observed_ratio is None or max_observed_ratio > args.max_overhead_ratio
    passed = failed is None and metrics_failed is None and not ratio_failed
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "provenance": build_evidence_provenance(
            REPO_ROOT,
            build_configuration=args.configuration,
        ),
        "overall_pass": passed,
        "passed": passed,
        "failed_category": str(failed.get("category", "")) if failed else ("tls_preprod_metrics" if metrics_failed or ratio_failed else ""),
        "failed_step": str(failed.get("name", "")) if failed else (str(metrics_failed.get("summary_path", "")) if metrics_failed else ("overhead_ratio" if ratio_failed else "")),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "scope": {
            "runs": max(1, args.runs),
            "max_overhead_ratio": args.max_overhead_ratio,
            "passed_runs": len(passed_runs),
            "mode": "local-preprod-multi-run",
        },
        "performance_comparison": {
            "max_overhead_ratio": max_observed_ratio,
            "ratios": ratios,
        },
        "steps": steps,
        "runs": run_metrics,
        "artifacts": {
            "summary_path": str(summary_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"TLS preprod multi-run: {'PASS' if passed else 'FAIL'} ({len(passed_runs)}/{len(run_metrics)} runs)")
    print(f"summary: {summary_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
