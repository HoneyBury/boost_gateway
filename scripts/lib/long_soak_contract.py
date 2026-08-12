#!/usr/bin/env python3
"""Run long-soak and capacity evidence on a fixed production-validation host."""

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
import os
import platform
import signal
import shutil
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts.lib.cancellable_process import (
    CancellationState,
    arm_parent_death_signal,
    atomic_write_json,
    installed_signal_handlers,
    run_cancellable_process,
)
from scripts.lib.evidence_provenance import build_evidence_provenance
from scripts.lib.operations_host import host_resource_snapshot, process_tree_resource_snapshot



"""Shared implementation extracted from run_long_soak_capacity.py."""

ROOT = Path(__file__).resolve().parents[2]

LONG_SOAK_PRESETS = {
    "2h": {
        "soak_profile": "long",
        "step_timeout_seconds": 16200,
        "summary_path": "runtime/validation/long-soak-2h-summary.json",
    },
    "8h": {
        "soak_profile": "overnight",
        "step_timeout_seconds": 37800,
        "summary_path": "runtime/validation/long-soak-8h-summary.json",
    },
}


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(
    name: str,
    category: str,
    cmd: list[str],
    timeout_seconds: int,
    cancellation: CancellationState | None = None,
) -> dict[str, object]:
    print(f"==> {name}", flush=True)
    result = run_cancellable_process(
        cmd,
        ROOT,
        timeout_seconds,
        cancellation or CancellationState(),
        cancellation_grace_seconds=10.0,
        timeout_grace_seconds=0.5,
    )
    stdout = str(result.get("stdout", ""))
    stderr = str(result.get("stderr", ""))

    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "status": result["status"],
        "returncode": result.get("returncode"),
        "signal": result.get("signal", ""),
        "duration_seconds": result["duration_seconds"],
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


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


def attach_provenance(summary_path: Path, provenance: dict[str, object]) -> None:
    if not summary_path.exists():
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(summary, dict):
        return
    summary["provenance"] = provenance
    atomic_write_json(summary_path, summary)


def validate_child_summary(summary_path: Path) -> str:
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"child summary is unavailable or invalid: {summary_path}: {exc}"
    if not isinstance(summary, dict):
        return f"child summary must be a JSON object: {summary_path}"
    if summary.get("overall_pass") is not True:
        return f"child summary did not pass: {summary_path}"
    return ""



def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.run_business_operation_perf and not (args.run_capacity or args.run_business_capacity):
        parser.error("--run-business-operation-perf requires --run-capacity or --run-business-capacity")
    if args.run_resource_stability_gate and not args.run_business_capacity:
        parser.error("--run-resource-stability-gate requires --run-business-capacity")
    if args.run_resource_stability_gate and (
        args.resource_stability_windows < 5
        or args.resource_stability_warmup_windows < 1
        or args.resource_stability_windows - args.resource_stability_warmup_windows < 3
        or args.resource_stability_clients <= 0
        or args.resource_stability_clients % 2 != 0
        or args.resource_stability_iterations <= 0
    ):
        parser.error(
            "resource stability requires at least five windows, one warmup, three measurement windows, "
            "positive iterations, and a positive even client count"
        )
    if args.leaderboard_redis_comparison and not (
        args.run_business_operation_perf and args.run_business_capacity
    ):
        parser.error(
            "--leaderboard-redis-comparison requires --run-business-operation-perf "
            "and --run-business-capacity"
        )
    if args.leaderboard_redis_comparison and args.perf_repetitions < 3:
        parser.error("--leaderboard-redis-comparison requires --perf-repetitions >= 3")
    if args.run_otel_comparison and not args.run_business_capacity:
        parser.error("--run-otel-comparison requires --run-business-capacity")
    if args.run_otel_comparison and args.perf_repetitions < 3:
        parser.error("--run-otel-comparison requires --perf-repetitions >= 3")
    if args.loadgen_io_threads <= 0:
        parser.error("--loadgen-io-threads must be positive")
    if args.io_cores <= 0:
        parser.error("--io-cores must be positive")
    if not 0.0 < args.saturation_cpu_threshold_percent <= 100.0:
        parser.error("--saturation-cpu-threshold-percent must be in (0, 100]")
    if not 0.0 < args.saturation_loadgen_headroom_percent <= 100.0:
        parser.error("--saturation-loadgen-headroom-percent must be in (0, 100]")
    if (
        args.cpu_set
        and (args.run_capacity or args.run_business_capacity or args.run_saturation)
        and not args.loadgen_cpu_set
    ):
        parser.error(
            "capacity evidence with --cpu-set requires an explicit, reusable --loadgen-cpu-set"
        )
    if args.loadgen_cpu_set and not args.cpu_set:
        parser.error("--loadgen-cpu-set requires --cpu-set")
    if args.run_saturation and (not args.cpu_set or not args.loadgen_cpu_set):
        parser.error(
            "--run-saturation requires explicit disjoint --cpu-set and --loadgen-cpu-set"
        )


def summarize_resource_samples(
    samples: list[dict[str, object]],
    interval_seconds: float,
    minimum_duration_seconds: float,
) -> dict[str, object]:
    elapsed = [float(sample["elapsed_seconds"]) for sample in samples]
    coverage = max(0.0, elapsed[-1] - elapsed[0]) if len(elapsed) >= 2 else 0.0
    gaps = [current - previous for previous, current in zip(elapsed, elapsed[1:], strict=False)]
    host_cpu_percent: list[float] = []
    for previous, current in zip(samples, samples[1:], strict=False):
        previous_ticks = previous.get("host", {}).get("cpu_ticks", {})
        current_ticks = current.get("host", {}).get("cpu_ticks", {})
        try:
            total_delta = int(current_ticks["total"]) - int(previous_ticks["total"])
            idle_delta = int(current_ticks["idle"]) - int(previous_ticks["idle"])
        except (KeyError, TypeError, ValueError):
            continue
        if total_delta > 0:
            host_cpu_percent.append(round(100.0 * (total_delta - idle_delta) / total_delta, 3))
    if not host_cpu_percent:
        host_cpu_percent = [
            float(sample["host"]["cpu_percent"])
            for sample in samples
            if isinstance(sample.get("host", {}).get("cpu_percent"), (int, float))
        ]

    def values(section: str, key: str) -> list[float]:
        return [
            float(value)
            for sample in samples
            for value in [sample.get(section, {}).get(key)]
            if isinstance(value, (int, float))
        ]

    def trend(series: list[float]) -> dict[str, float | None]:
        return {
            "first": series[0] if series else None,
            "last": series[-1] if series else None,
            "delta": round(series[-1] - series[0], 3) if series else None,
            "minimum": min(series) if series else None,
            "maximum": max(series) if series else None,
        }

    memory_available = [
        float(sample["host"]["memory_kib"]["MemAvailable"])
        for sample in samples
        if isinstance(sample.get("host", {}).get("memory_kib", {}).get("MemAvailable"), (int, float))
    ]
    required = minimum_duration_seconds > 0
    minimum_samples = max(2, int(minimum_duration_seconds / interval_seconds * 0.9)) if required else 1
    checks = {
        "sample_count": len(samples) >= minimum_samples,
        "duration_coverage": not required or coverage >= minimum_duration_seconds,
        "sampling_continuity": not required or (bool(gaps) and max(gaps) <= interval_seconds * 2.5),
        "host_cpu": not required or bool(host_cpu_percent),
        "host_memory": not required or bool(memory_available),
        "process_tree": not required or (
            any(value > 0 for value in values("process_tree", "rss_kib"))
            and any(value > 0 for value in values("process_tree", "process_count"))
        ),
    }
    return {
        "required": required,
        "passed": all(checks.values()),
        "sample_interval_seconds": interval_seconds,
        "sample_count": len(samples),
        "minimum_required_samples": minimum_samples,
        "coverage_seconds": round(coverage, 3),
        "maximum_sample_gap_seconds": round(max(gaps), 3) if gaps else None,
        "checks": checks,
        "host": {"cpu_percent": trend(host_cpu_percent), "memory_available_kib": trend(memory_available)},
        "process_tree": {
            "rss_kib": trend(values("process_tree", "rss_kib")),
            "fd_count": trend(values("process_tree", "fd_count")),
            "thread_count": trend(values("process_tree", "thread_count")),
            "process_count": trend(values("process_tree", "process_count")),
        },
    }


class ResourceSampler:
    def __init__(self, output_root: Path, interval_seconds: float, minimum_duration_seconds: float) -> None:
        self.output_root = output_root
        self.interval_seconds = max(0.1, interval_seconds)
        self.minimum_duration_seconds = minimum_duration_seconds
        self.samples_path = output_root / "resource-samples.jsonl"
        self.summary_path = output_root / "resource-summary.json"
        self.samples: list[dict[str, object]] = []
        self.error = ""
        self._started = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="soak-resource-sampler", daemon=True)

    def start(self) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.samples_path.unlink(missing_ok=True)
        self.summary_path.unlink(missing_ok=True)
        self._capture()
        self._thread.start()

    def _capture(self) -> None:
        sample = {
            "captured_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "elapsed_seconds": round(time.monotonic() - self._started, 6),
            "host": host_resource_snapshot(),
            "process_tree": process_tree_resource_snapshot(os.getpid()),
        }
        self.samples.append(sample)
        with self.samples_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(sample, separators=(",", ":")) + "\n")
            stream.flush()

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.interval_seconds):
                self._capture()
        except Exception as exc:  # pragma: no cover - preserved in the evidence summary
            self.error = f"{type(exc).__name__}: {exc}"

    def stop(self) -> dict[str, object]:
        self._stop.set()
        self._thread.join(timeout=max(5.0, self.interval_seconds + 1.0))
        if not self.error:
            try:
                self._capture()
            except Exception as exc:  # pragma: no cover - preserved in the evidence summary
                self.error = f"{type(exc).__name__}: {exc}"
        summary = summarize_resource_samples(
            self.samples, self.interval_seconds, self.minimum_duration_seconds
        )
        summary.update({
            "error": self.error,
            "samples_path": str(self.samples_path),
            "summary_path": str(self.summary_path),
            "summary_version": 1,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        })
        if self.error:
            summary["passed"] = False
        summary["overall_pass"] = summary["passed"]
        self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary


def archive_failed_arch_run(
    output_root: Path,
    pass_number: int,
    attempt: str,
    before: dict[str, object],
    after: dict[str, object],
    *,
    status: str = "failed",
    failed_checks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    archive_dir = output_root / "failures" / f"pass-{pass_number:06d}-{attempt}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_files: list[str] = []
    for name in (
        "summary.json", "v2_arch_benchmark.json", "v2_mailbox_benchmark.json",
        "stdout.log", "stderr.log", "mailbox-stdout.log", "mailbox-stderr.log",
    ):
        source = output_root / name
        if source.is_file():
            destination = archive_dir / name
            shutil.copy2(source, destination)
            archived_files.append(str(destination))
    diagnostics_path = archive_dir / "host-resources.json"
    diagnostics_path.write_text(json.dumps({"before": before, "after": after}, indent=2), encoding="utf-8")
    archived_files.append(str(diagnostics_path))
    return {
        "pass_number": pass_number,
        "attempt": attempt,
        "status": status,
        "failed_checks": [
            {key: check.get(key) for key in ("name", "metric", "value", "threshold", "direction")}
            for check in (failed_checks or [])
        ],
        "archive_dir": str(archive_dir),
        "files": archived_files,
    }


def record_failure_episode(
    failures: dict[str, dict[str, object]], checks_by_run: list[list[dict[str, object]]]
) -> None:
    observations: dict[str, list[dict[str, object]]] = {}
    for checks in checks_by_run:
        for check in checks:
            observations.setdefault(str(check.get("name", "unknown")), []).append(check)
    for name, checks in observations.items():
        first = checks[0]
        entry = failures.setdefault(name, {
            "name": name, "metric": first.get("metric"), "threshold": first.get("threshold"),
            "direction": first.get("direction"), "failed_runs": 0, "confirmed_failed_runs": 0,
            "unconfirmed_failed_runs": 0, "confirmed_episodes": 0, "recovered_episodes": 0,
            "last_observed": None,
        })
        confirmed = len(checks) >= 2
        entry["failed_runs"] = int(entry["failed_runs"]) + len(checks)
        entry["last_observed"] = checks[-1].get("value")
        key = "confirmed_failed_runs" if confirmed else "unconfirmed_failed_runs"
        entry[key] = int(entry[key]) + len(checks)
        episode_key = "confirmed_episodes" if confirmed else "recovered_episodes"
        entry[episode_key] = int(entry[episode_key]) + 1
        for check in checks:
            observed = float(check["value"])
            for worst_key in (["worst_observed", "worst_confirmed_observed"] if confirmed else ["worst_observed"]):
                worst = entry.get(worst_key)
                if worst is None or (
                    str(entry["direction"]) == "max" and observed > float(worst)
                ) or (str(entry["direction"]) == "min" and observed < float(worst)):
                    entry[worst_key] = observed


def evaluate_sustained_failure_violations(
    failures: dict[str, dict[str, object]],
    completed_runs: int,
    *,
    maximum_failure_rate: float,
    maximum_deviation_ratio: float,
    rare_maximum_failure_rate: float,
    rare_maximum_deviation_ratio: float,
) -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    for entry in failures.values():
        threshold = float(entry["threshold"])
        failed_runs = int(entry["failed_runs"])
        confirmed_failed_runs = int(entry.get("confirmed_failed_runs", failed_runs))
        worst_observed = float(entry["worst_observed"])
        worst_confirmed = float(entry.get("worst_confirmed_observed", worst_observed))
        confirmed_rate = confirmed_failed_runs / max(1, completed_runs)
        raw_rate = failed_runs / max(1, completed_runs)
        failure_rate = confirmed_rate if confirmed_failed_runs else raw_rate
        direction = str(entry["direction"])
        deviation = (
            (worst_confirmed - threshold) / threshold
            if direction == "max" else (threshold - worst_confirmed) / threshold
        )
        entry.update({
            "raw_failure_rate": round(raw_rate, 6),
            "confirmed_failure_rate": round(confirmed_rate, 6),
            "failure_rate": round(failure_rate, 6),
            "worst_deviation_ratio": round(deviation, 6),
        })
        confirmation_recovered = confirmed_failed_runs == 0 and raw_rate <= rare_maximum_failure_rate
        standard = failure_rate <= maximum_failure_rate and deviation <= maximum_deviation_ratio
        rare_tail = failure_rate <= rare_maximum_failure_rate and deviation <= rare_maximum_deviation_ratio
        entry["accepted_as_transient"] = confirmation_recovered or standard or rare_tail
        entry["acceptance_tier"] = (
            "confirmation_recovered" if confirmation_recovered else
            "standard" if standard else "rare_tail" if rare_tail else "rejected"
        )
        if not entry["accepted_as_transient"]:
            violations.append(entry)
    return sorted(violations, key=lambda check: str(check["name"]))
