#!/usr/bin/env python3
"""Run the P5 Kubernetes control-plane and Operator gate."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(name: str, category: str, cmd: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
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
            "cwd": str(cwd),
            "timeout_seconds": timeout_seconds,
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout),
            "stderr_tail": tail(exc.stderr),
        }

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "cwd": str(cwd),
        "timeout_seconds": timeout_seconds,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def require_command(name: str) -> None:
    if not shutil.which(name):
        raise FileNotFoundError(f"missing required command: {name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-dir", type=Path, default=Path("operator/boostgateway-operator"))
    parser.add_argument("--include-envtest", action="store_true")
    parser.add_argument("--include-kind", action="store_true")
    parser.add_argument("--go-test-timeout-seconds", type=int, default=180)
    parser.add_argument("--envtest-timeout-seconds", type=int, default=240)
    parser.add_argument("--kind-timeout-seconds", type=int, default=900)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/control-plane-gate-summary.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    operator_dir = args.operator_dir if args.operator_dir.is_absolute() else root / args.operator_dir
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    summary: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "operator_dir": str(operator_dir),
        "include_envtest": args.include_envtest,
        "include_kind": args.include_kind,
        "passed": False,
        "failed_category": "",
        "failed_step": "",
        "steps": [],
    }

    try:
        if not operator_dir.is_dir():
            raise FileNotFoundError(f"missing operator dir: {operator_dir}")
        require_command("go")
        if args.include_kind:
            for command in ["kind", "kubectl", "make"]:
                require_command(command)

        summary["steps"].append(run_step(
            "Operator fake-client and unit tests",
            "operator",
            ["go", "test", "./..."],
            operator_dir,
            args.go_test_timeout_seconds,
        ))
        if args.include_envtest:
            summary["steps"].append(run_step(
                "Operator envtest reconcile tests",
                "envtest",
                ["make", "test-envtest"],
                operator_dir,
                args.envtest_timeout_seconds,
            ))
        if args.include_kind:
            summary["steps"].append(run_step(
                "Operator kind status smoke",
                "kind",
                [sys.executable, str(root / "scripts" / "operator_kind_smoke.py")],
                root,
                args.kind_timeout_seconds,
            ))
    except (FileNotFoundError, RuntimeError) as exc:
        failed = next((step for step in summary["steps"] if step.get("status") != "passed"), None)
        if failed:
            summary["failed_category"] = str(failed.get("category", "unknown"))
            summary["failed_step"] = str(failed.get("name", "unknown"))
        else:
            summary["failed_category"] = "discovery"
            summary["failed_step"] = str(exc)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"control-plane gate failed: {exc}", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 1

    failed = next((step for step in summary["steps"] if step.get("status") != "passed"), None)
    if failed:
        summary["failed_category"] = str(failed.get("category", "unknown"))
        summary["failed_step"] = str(failed.get("name", "unknown"))
    else:
        summary["passed"] = True

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
