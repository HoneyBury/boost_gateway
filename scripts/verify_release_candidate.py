#!/usr/bin/env python3
"""Run bounded release-candidate gates with structured summaries."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def normalize_output(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    return text


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    text = normalize_output(text)
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(name: str, category: str, cmd: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
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

    stdout = normalize_output(completed.stdout)
    stderr = normalize_output(completed.stderr)
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/windows-msvc-debug"))
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--baseline-profile", choices=["debug", "release"], default="debug")
    parser.add_argument("--soak-profile", choices=["smoke", "short", "medium"], default="smoke")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-release-baseline", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/release-candidate-summary.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    summary: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(args.build_dir.resolve()),
        "configuration": args.configuration,
        "baseline_profile": args.baseline_profile,
        "soak_profile": args.soak_profile,
        "passed": False,
        "failed_category": "",
        "failed_step": "",
        "steps": [],
    }

    steps: list[dict[str, object]] = []
    steps.append(run_step(
        "reliability matrix evidence",
        "docs",
        [sys.executable, str(root / "scripts" / "check_reliability_matrix.py")],
        root,
        30,
    ))
    steps.append(run_step(
        "R4 contract gate",
        "contract",
        [
            sys.executable,
            str(root / "scripts" / "verify_r4_contract.py"),
            "--build-dir",
            str(args.build_dir),
            "--configuration",
            args.configuration,
            "--skip-arch-baseline",
            "--baseline-profile",
            args.baseline_profile,
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-r4-contract-summary.json"),
            *(["--skip-build"] if args.skip_build else []),
        ],
        root,
        args.timeout_seconds,
    ))
    steps.append(run_step(
        "security release gate",
        "security",
        [sys.executable, str(root / "scripts" / "check_security_release_gate.py")],
        root,
        30,
    ))
    steps.append(run_step(
        "observability release gate",
        "observability",
        [
            sys.executable,
            str(root / "scripts" / "verify_observability_gate.py"),
            "--build-dir",
            str(args.build_dir),
            "--configuration",
            args.configuration,
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-observability-gate-summary.json"),
            *(["--skip-build"] if args.skip_build else []),
        ],
        root,
        args.timeout_seconds,
    ))
    steps.append(run_step(
        "control-plane operator gate",
        "control_plane",
        [
            sys.executable,
            str(root / "scripts" / "verify_control_plane_gate.py"),
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-control-plane-gate-summary.json"),
        ],
        root,
        args.timeout_seconds,
    ))
    steps.append(run_step(
        "stability soak gate",
        "soak",
        [
            sys.executable,
            str(root / "scripts" / "verify_stability_soak.py"),
            "--build-dir",
            str(args.build_dir),
            "--configuration",
            args.configuration,
            "--baseline-profile",
            args.baseline_profile,
            "--soak-profile",
            args.soak_profile,
            "--summary-path",
            str(root / "runtime" / "validation" / "rc-stability-soak-summary.json"),
            *(["--skip-build"] if args.skip_build else []),
        ],
        root,
        args.timeout_seconds,
    ))
    if not args.skip_release_baseline:
        steps.append(run_step(
            "release baseline entry",
            "baseline",
            [
                sys.executable,
                str(root / "scripts" / "collect_release_baseline.py"),
                "--build-dir",
                str(args.build_dir),
                "--configuration",
                args.configuration,
                "--perf-timeout-seconds",
                str(args.timeout_seconds + 300),
                *(["--skip-build"] if args.skip_build else []),
            ],
            root,
            args.timeout_seconds + 420,
        ))

    summary["steps"] = steps
    failed = next((step for step in steps if step["status"] != "passed"), None)
    if failed:
        summary["failed_category"] = str(failed["category"])
        summary["failed_step"] = str(failed["name"])
    else:
        summary["passed"] = True

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
