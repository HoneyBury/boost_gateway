#!/usr/bin/env python3
"""Run the P5 production resilience gate.

This gate keeps the default path bounded while giving fixed runners one
entrypoint for longer soak, Redis live failure paths, runtime HTTP evidence,
and Kubernetes kind control-plane exercises.
"""

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
import platform
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.lib.cancellable_process import (
    CancellationState,
    atomic_write_json,
    installed_signal_handlers,
    run_cancellable_process,
)


def normalize_output(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    return text


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    text = normalize_output(text)
    return text if len(text) <= max_chars else text[-max_chars:]


def emit_text(text: str, *, stderr: bool = False) -> None:
    stream = sys.stderr if stderr else sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.buffer.write(text.encode(encoding, errors="replace"))


def run_step(
    name: str,
    category: str,
    cmd: list[str],
    cwd: Path,
    timeout_seconds: int,
    cancellation: CancellationState | None = None,
) -> dict[str, object]:
    print(f"==> {name}", flush=True)
    result = run_cancellable_process(
        cmd,
        cwd,
        timeout_seconds,
        cancellation or CancellationState(),
        cancellation_grace_seconds=3.0,
        timeout_grace_seconds=0.5,
    )
    stdout = normalize_output(result.get("stdout"))
    stderr = normalize_output(result.get("stderr"))
    if stdout:
        emit_text(stdout)
    if stderr:
        emit_text(stderr, stderr=True)
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "cwd": str(cwd),
        "timeout_seconds": timeout_seconds,
        "status": result["status"],
        "returncode": result.get("returncode"),
        "signal": result.get("signal", ""),
        "duration_seconds": result["duration_seconds"],
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/default"))
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--include-redis-live", action="store_true")
    parser.add_argument("--include-operator-kind", action="store_true")
    parser.add_argument("--include-runtime-http", action="store_true")
    parser.add_argument("--include-release-baseline", action="store_true")
    parser.add_argument("--include-capacity-baseline", action="store_true")
    parser.add_argument("--soak-profile", choices=["smoke", "short", "medium", "long", "overnight"], default="smoke")
    parser.add_argument("--baseline-profile", choices=["debug", "release"], default="debug")
    parser.add_argument("--perf-repetitions", type=int, default=1)
    parser.add_argument("--step-timeout-seconds", type=int, default=900)
    parser.add_argument("--kind-timeout-seconds", type=int, default=900)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/production-resilience-summary.json"))
    return parser.parse_args()


def append_if(args: argparse.Namespace, cmd: list[str], flag_name: str, option: str) -> None:
    if getattr(args, flag_name):
        cmd.append(option)


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    summary_path.unlink(missing_ok=True)
    cancellation = CancellationState()
    steps: list[dict[str, object]] = []
    completed_steps: list[str] = []
    current_step = ""
    interrupted = False
    interruption_signal = ""
    unexpected_error = ""
    summary: dict[str, object] = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(args.build_dir.resolve()),
        "configuration": args.configuration,
        "soak_profile": args.soak_profile,
        "baseline_profile": args.baseline_profile,
        "include_redis_live": args.include_redis_live,
        "include_operator_kind": args.include_operator_kind,
        "include_runtime_http": args.include_runtime_http,
        "include_release_baseline": args.include_release_baseline,
        "include_capacity_baseline": args.include_capacity_baseline,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "overall_pass": False,
        "passed": False,
        "interrupted": False,
        "interruption_signal": "",
        "current_step": "",
        "completed_steps": [],
        "failed_category": "",
        "failed_step": "",
        "steps": steps,
        "artifacts": {
            "summary_path": str(summary_path),
            "preflight_summary_path": str(root / "runtime" / "validation" / "p5-fixed-runner-preflight-summary.json"),
            "recovery_summary_path": str(root / "runtime" / "validation" / "p5-production-recovery-summary.json"),
            "transport_config_summary_path": str(
                root / "runtime" / "validation" / "p5-transport-config-governance-summary.json"
            ),
            "soak_summary_path": str(root / "runtime" / "validation" / "p5-long-soak-summary.json"),
            "fault_recovery_summary_path": str(root / "runtime" / "validation" / "p5-fault-data-recovery-summary.json"),
            "specialized_summary_path": str(root / "runtime" / "validation" / "p5-specialized-failure-summary.json"),
            "runtime_http_summary_path": str(root / "runtime" / "validation" / "p5-runtime-observability-summary.json"),
            "control_plane_summary_path": str(root / "runtime" / "validation" / "p5-control-plane-kind-summary.json"),
            "release_baseline_summary_path": str(root / "runtime" / "validation" / "p5-release-baseline-summary.json"),
        },
    }

    def execute_step(
        name: str,
        category: str,
        command: list[str],
        timeout_seconds: int,
    ) -> dict[str, object]:
        nonlocal current_step, interrupted, interruption_signal
        current_step = name
        result = run_step(name, category, command, root, timeout_seconds, cancellation)
        steps.append(result)
        if result.get("status") == "cancelled":
            interrupted = True
            interruption_signal = str(result.get("signal", ""))
            if not cancellation.cancelled:
                cancellation.request(int(getattr(signal, interruption_signal, signal.SIGTERM)))
        else:
            completed_steps.append(name)
            current_step = ""
        return result

    with installed_signal_handlers(cancellation):
        atomic_write_json(summary_path, summary)
        try:
            preflight_cmd = [
                sys.executable,
                str(root / "scripts/gates/infrastructure/check_fixed_runner_environment.py"),
                "--profile", "production-resilience",
                "--build-dir", str(args.build_dir),
                "--summary-path", str(root / "runtime/validation/p5-fixed-runner-preflight-summary.json"),
            ]
            if args.include_redis_live:
                preflight_cmd.append("--require-redis")
            if args.include_operator_kind:
                preflight_cmd.append("--require-kind")
            if not cancellation.cancelled:
                execute_step("P5 fixed-runner preflight", "preflight", preflight_cmd, 60)

            if not cancellation.cancelled:
                execute_step(
                    "P5/N3 deployment recovery and rollback evidence",
                    "recovery",
                    [
                        sys.executable,
                        str(root / "scripts/gates/production/check_production_recovery_gate.py"),
                        "--summary-path", str(root / "runtime/validation/p5-production-recovery-summary.json"),
                    ],
                    60,
                )

            if not cancellation.cancelled:
                execute_step(
                    "P5/N4 transport security and config drift evidence",
                    "transport_config",
                    [
                        sys.executable,
                        str(root / "scripts/gates/transport/check_transport_config_governance.py"),
                        "--generate-dev-certs",
                        "--summary-path", str(root / "runtime/validation/p5-transport-config-governance-summary.json"),
                    ],
                    90,
                )

            if not cancellation.cancelled:
                stability_cmd = [
                    sys.executable,
                    str(root / "scripts/gates/release/verify_stability_soak.py"),
                    "--build-dir", str(args.build_dir),
                    "--configuration", args.configuration,
                    "--soak-profile", args.soak_profile,
                    "--baseline-profile", args.baseline_profile,
                    "--summary-path", str(root / "runtime/validation/p5-long-soak-summary.json"),
                ]
                if args.skip_build:
                    stability_cmd.append("--skip-build")
                execute_step(
                    "P5 bounded long-soak evidence", "soak", stability_cmd,
                    args.step_timeout_seconds,
                )

            if not cancellation.cancelled:
                data_cmd = [
                    sys.executable,
                    str(root / "scripts/gates/production/verify_data_recovery_gate.py"),
                    "--build-dir", str(args.build_dir),
                    "--configuration", args.configuration,
                    "--summary-path", str(root / "runtime/validation/p5-fault-data-recovery-summary.json"),
                ]
                if args.skip_build:
                    data_cmd.append("--skip-build")
                if args.include_redis_live:
                    data_cmd.append("--include-redis-live")
                execute_step(
                    "P5 fault recovery and data resilience evidence", "fault_recovery",
                    data_cmd, args.step_timeout_seconds,
                )

            if not cancellation.cancelled:
                specialized_cmd = [
                    sys.executable,
                    str(root / "scripts/gates/e2e/verify_specialized_e2e.py"),
                    "--build-dir", str(args.build_dir),
                    "--configuration", args.configuration,
                    "--summary-path", str(root / "runtime/validation/p5-specialized-failure-summary.json"),
                ]
                if args.skip_build:
                    specialized_cmd.append("--skip-build")
                append_if(args, specialized_cmd, "include_redis_live", "--include-redis-live")
                append_if(args, specialized_cmd, "include_operator_kind", "--include-operator-kind")
                execute_step(
                    "P5 Redis/Raft/Operator failure-path evidence", "specialized",
                    specialized_cmd, args.step_timeout_seconds,
                )

            if args.include_runtime_http and not cancellation.cancelled:
                observability_cmd = [
                    sys.executable,
                    str(root / "scripts/gates/production/verify_observability_gate.py"),
                    "--build-dir", str(args.build_dir),
                    "--configuration", args.configuration,
                    "--include-runtime-http",
                    "--summary-path", str(root / "runtime/validation/p5-runtime-observability-summary.json"),
                ]
                if args.skip_build:
                    observability_cmd.append("--skip-build")
                execute_step(
                    "P5 runtime HTTP observability during resilience gate", "observability",
                    observability_cmd, args.step_timeout_seconds,
                )

            if args.include_operator_kind and not cancellation.cancelled:
                execute_step(
                    "P5 Kubernetes rollout/delete smoke evidence",
                    "control_plane",
                    [
                        sys.executable,
                        str(root / "scripts/gates/production/verify_control_plane_gate.py"),
                        "--include-kind",
                        "--kind-timeout-seconds", str(args.kind_timeout_seconds),
                        "--summary-path", str(root / "runtime/validation/p5-control-plane-kind-summary.json"),
                    ],
                    args.kind_timeout_seconds + 120,
                )

            if (
                (args.include_release_baseline or args.include_capacity_baseline)
                and not cancellation.cancelled
            ):
                perf_preset = "capacity" if args.include_capacity_baseline else "baseline"
                release_cmd = [
                    sys.executable,
                    str(root / "scripts/producers/collect_release_baseline.py"),
                    "--build-dir", str(args.build_dir),
                    "--configuration", args.configuration,
                    "--perf-preset", perf_preset,
                    "--perf-repetitions", str(args.perf_repetitions),
                    "--summary-path", str(root / "runtime/validation/p5-release-baseline-summary.json"),
                ]
                if args.skip_build:
                    release_cmd.append("--skip-build")
                execute_step(
                    "P5 release/capacity regression evidence", "release_baseline",
                    release_cmd, args.step_timeout_seconds,
                )
        except Exception as exc:
            unexpected_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            def finalize_summary() -> None:
                nonlocal interrupted, interruption_signal, current_step
                if cancellation.cancelled and not interrupted:
                    interrupted = True
                    interruption_signal = cancellation.signal_name
                    if not current_step:
                        current_step = "between_steps"
                failed = next(
                    (step for step in steps if step.get("status") != "passed"), None
                )
                passed = not interrupted and not unexpected_error and failed is None
                summary.update({
                    "generated_at": datetime.now(UTC)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                    "steps": steps,
                    "duration_seconds": round(
                        sum(float(step.get("duration_seconds", 0.0)) for step in steps),
                        3,
                    ),
                    "interrupted": interrupted,
                    "interruption_signal": interruption_signal,
                    "current_step": current_step,
                    "completed_steps": completed_steps,
                    "overall_pass": passed,
                    "passed": passed,
                    "failed_category": (
                        "interrupted" if interrupted else "orchestrator"
                        if unexpected_error else "" if failed is None
                        else str(failed.get("category", "unknown"))
                    ),
                    "failed_step": (
                        current_step if interrupted else unexpected_error
                        if unexpected_error else "" if failed is None
                        else str(failed.get("name", "unknown"))
                    ),
                })
                atomic_write_json(summary_path, summary)

            finalize_summary()
            if cancellation.cancelled and summary.get("interrupted") is not True:
                finalize_summary()
            print(f"summary: {summary_path}")

    if interrupted:
        signal_number = cancellation.signal_number or getattr(signal, interruption_signal, 1)
        return 128 + int(signal_number)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
