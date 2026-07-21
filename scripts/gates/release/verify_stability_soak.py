#!/usr/bin/env python3
"""Run bounded stability/soak checks for P2-P5 without long-lived terminals."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts.lib.cancellable_process import (
    CancellationState,
    atomic_write_json,
    installed_signal_handlers,
    run_cancellable_process,
)


IO_FILTER = (
    "V2IoEngineTest.AcceptPolicyRoundRobinDistributesAcrossCores:"
    "V2IoEngineTest.AcceptPolicyLeastLoadedPicksIdleCore:"
    "V2IoEngineTest.AcceptPolicyLeastLoadedBalancesAcrossCores:"
    "V2IoEngineTest.MultiAcceptorCreatedWhenReusePortSet:"
    "V2IoEngineTest.MultiAcceptorHasPortOnAllCores:"
    "V2IoEngineTest.SingleAcceptorWhenReusePortFalse"
)

RECOVERY_FILTER = (
    "ServiceBusIntegrity.GatewayBridgeTimeoutClosesStaleConnectionAndRecovers:"
    "ServiceBusIntegrity.GatewayBridgeCircuitBreakerHalfOpenProbeRecovers:"
    "ServiceBusIntegrity.GatewayBridgeRecoversAfterBackendConfigUpdate"
)

DATA_FILTER = (
    "V2WriteBehindStoreTest.WriteBehindMultipleWritesAllFlushed:"
    "V2WriteBehindStoreTest.WriteBehindDestructorFlushesRemaining:"
    "V2WriteBehindStoreTest.WriteBehindFlushReportsDelegateFailures:"
    "V2WriteBehindStoreTest.WriteBehindDestructorDrainsLargePendingQueue"
)

SOAK_PROFILES = {
    "smoke": {
        "iterations": "2000",
        "actors": "2000",
        "actor_limit": "10000",
        "battles": "100",
        "expected_window": "bounded-smoke",
        "minimum_duration_seconds": 0,
    },
    "short": {
        "iterations": "5000",
        "actors": "5000",
        "actor_limit": "50000",
        "battles": "250",
        "expected_window": "15m-30m",
        "minimum_duration_seconds": 0,
    },
    "medium": {
        "iterations": "10000",
        "actors": "10000",
        "actor_limit": "100000",
        "battles": "500",
        "expected_window": "30m-60m",
        "minimum_duration_seconds": 0,
    },
    "long": {
        "iterations": "20000",
        "actors": "20000",
        "actor_limit": "200000",
        "battles": "1000",
        "expected_window": "2h",
        "minimum_duration_seconds": 7200,
    },
    "overnight": {
        "iterations": "40000",
        "actors": "40000",
        "actor_limit": "400000",
        "battles": "2000",
        "expected_window": "8h",
        "minimum_duration_seconds": 28800,
    },
}

PROFILE_TIMEOUTS = {
    "smoke": {"build": 300, "test": 120, "baseline": 120},
    "short": {"build": 1800, "test": 300, "baseline": 1800},
    "medium": {"build": 1800, "test": 300, "baseline": 3600},
    "long": {"build": 1800, "test": 300, "baseline": 10800},
    "overnight": {"build": 1800, "test": 300, "baseline": 32400},
}

SUSTAINED_GATE_MAX_FAILURE_RATE = 0.01
SUSTAINED_GATE_MAX_DEVIATION_RATIO = 0.20
SUSTAINED_GATE_RARE_MAX_FAILURE_RATE = 0.001
SUSTAINED_GATE_RARE_MAX_DEVIATION_RATIO = 0.25
SUSTAINED_GATE_CONFIRMATION_RUNS = 2
DEFAULT_RESOURCE_SAMPLE_INTERVAL_SECONDS = 30.0


def exe_name(base: str) -> str:
    return f"{base}.exe" if os.name == "nt" else base


def find_executable(build_dir: Path, base_name: str) -> Path:
    names = {exe_name(base_name), base_name}
    matches = sorted(p for p in build_dir.rglob("*") if p.is_file() and p.name in names)
    if os.name == "nt":
        preferred = [
            p for p in matches
            if any(part.lower() in {"debug", "release", "relwithdebinfo", "minsizerel"} for part in p.parts)
        ]
        if preferred:
            matches = preferred
    if not matches:
        raise FileNotFoundError(f"{exe_name(base_name)} not found under {build_dir}")
    return matches[0]


def normalize_output(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    return text


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    text = normalize_output(text)
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(
    name: str,
    category: str,
    cmd: list[str],
    cwd: Path,
    timeout_seconds: int,
    *,
    emit_output: bool = True,
    cancellation: CancellationState | None = None,
) -> dict[str, object]:
    if emit_output:
        print(f"==> {name}", flush=True)
    result = run_cancellable_process(
        cmd,
        cwd,
        timeout_seconds,
        cancellation or CancellationState(),
        termination_grace_seconds=3.0,
    )
    stdout = normalize_output(result.get("stdout"))
    stderr = normalize_output(result.get("stderr"))
    if stdout and emit_output:
        print(stdout, end="")
    if stderr and emit_output:
        print(stderr, end="", file=sys.stderr)
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


def cmake_build_args(args: argparse.Namespace, targets: list[str]) -> list[str]:
    cmd = ["cmake", "--build", str(args.build_dir)]
    if args.configuration:
        cmd.extend(["--config", args.configuration])
    cmd.extend(["--target", *targets])
    return cmd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/windows-msvc-debug"))
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--build-timeout-seconds", type=int)
    parser.add_argument("--test-timeout-seconds", type=int)
    parser.add_argument("--baseline-timeout-seconds", type=int)
    parser.add_argument(
        "--minimum-duration-seconds",
        type=int,
        default=None,
        help="Override the profile's required sustained architecture-baseline duration (0 keeps a bounded run).",
    )
    parser.add_argument("--baseline-profile", choices=["debug", "release"], default="debug")
    parser.add_argument("--soak-profile", choices=sorted(SOAK_PROFILES), default="smoke")
    parser.add_argument(
        "--resource-sample-interval-seconds",
        type=float,
        default=DEFAULT_RESOURCE_SAMPLE_INTERVAL_SECONDS,
        help="Host and verifier process-tree resource sampling interval.",
    )
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/stability-soak-summary.json"))
    args = parser.parse_args()
    if args.resource_sample_interval_seconds <= 0:
        parser.error("--resource-sample-interval-seconds must be greater than zero")
    return args


def arch_baseline_command(
    root: Path,
    build_dir: Path,
    output_root: Path,
    soak_profile: dict[str, str | int],
    baseline_timeout_seconds: int,
    baseline_profile: str,
) -> list[str]:
    return [
        sys.executable,
        str(root / "scripts" / "collect_v2_arch_baseline.py"),
        "--build-dir", str(build_dir),
        "--output-root", str(output_root),
        "--iterations", str(soak_profile["iterations"]),
        "--actors", str(soak_profile["actors"]),
        "--actor-limit", str(soak_profile["actor_limit"]),
        "--battles", str(soak_profile["battles"]),
        "--timeout-seconds", str(baseline_timeout_seconds),
        "--gate-profile", baseline_profile,
    ]


def failed_arch_checks(summary_path: Path) -> list[dict[str, object]]:
    try:
        parsed = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    gates = parsed.get("release_gates")
    if not isinstance(gates, dict):
        return []
    checks = gates.get("checks")
    if not isinstance(checks, list):
        return []
    return [check for check in checks if isinstance(check, dict) and check.get("passed") is False]


def host_resource_snapshot() -> dict[str, object]:
    snapshot: dict[str, object] = {
        "captured_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
    }
    try:
        snapshot["load_average"] = list(os.getloadavg())
    except (AttributeError, OSError):
        pass

    try:
        snapshot["proc_loadavg"] = Path("/proc/loadavg").read_text(encoding="utf-8").strip()
    except OSError:
        pass

    try:
        cpu_fields = [int(value) for value in Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]]
        snapshot["cpu_ticks"] = {
            "total": sum(cpu_fields),
            "idle": sum(cpu_fields[3:5]),
        }
    except (OSError, ValueError, IndexError):
        pass

    meminfo = Path("/proc/meminfo")
    try:
        wanted = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
        snapshot["memory_kib"] = {
            key: int(value.split()[0])
            for line in meminfo.read_text(encoding="utf-8").splitlines()
            for key, value in [line.split(":", 1)]
            if key in wanted
        }
    except (OSError, ValueError):
        pass

    frequencies: list[int] = []
    for path in Path("/sys/devices/system/cpu").glob("cpu*/cpufreq/scaling_cur_freq"):
        try:
            frequencies.append(int(path.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            continue
    if frequencies:
        snapshot["cpu_frequency_khz"] = {
            "minimum": min(frequencies),
            "maximum": max(frequencies),
            "average": round(sum(frequencies) / len(frequencies), 3),
            "sampled_cpus": len(frequencies),
        }

    thermal_millicelsius: dict[str, int] = {}
    for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            thermal_millicelsius[path.parent.name] = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
    if thermal_millicelsius:
        snapshot["thermal_millicelsius"] = thermal_millicelsius
    return snapshot


def process_tree_resource_snapshot(root_pid: int) -> dict[str, object]:
    """Aggregate RSS/fd/thread counts for the soak verifier and its descendants."""
    processes: dict[int, dict[str, object]] = {}
    for status_path in Path("/proc").glob("[0-9]*/status"):
        try:
            fields: dict[str, str] = {}
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    fields[key] = value.strip()
            pid = int(status_path.parent.name)
            processes[pid] = {
                "pid": pid,
                "ppid": int(fields.get("PPid", "0")),
                "name": fields.get("Name", "unknown"),
                "rss_kib": int(fields.get("VmRSS", "0 kB").split()[0]),
                "threads": int(fields.get("Threads", "0")),
            }
        except (OSError, ValueError, IndexError):
            continue

    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, process in processes.items():
            if pid not in selected and int(process["ppid"]) in selected:
                selected.add(pid)
                changed = True

    samples: list[dict[str, object]] = []
    for pid in sorted(selected):
        process = processes.get(pid)
        if process is None:
            continue
        try:
            fd_count = sum(1 for _ in (Path("/proc") / str(pid) / "fd").iterdir())
        except OSError:
            fd_count = 0
        samples.append({**process, "fd_count": fd_count})
    return {
        "root_pid": root_pid,
        "process_count": len(samples),
        "rss_kib": sum(int(item["rss_kib"]) for item in samples),
        "fd_count": sum(int(item["fd_count"]) for item in samples),
        "thread_count": sum(int(item["threads"]) for item in samples),
        "processes": samples,
    }


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

    def values(section: str, key: str) -> list[float]:
        result: list[float] = []
        for sample in samples:
            value = sample.get(section, {}).get(key)
            if isinstance(value, (int, float)):
                result.append(float(value))
        return result

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
        "host": {
            "cpu_percent": trend(host_cpu_percent),
            "memory_available_kib": trend(memory_available),
        },
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
        summary["error"] = self.error
        summary["samples_path"] = str(self.samples_path)
        summary["summary_path"] = str(self.summary_path)
        if self.error:
            summary["passed"] = False
        summary["summary_version"] = 1
        summary["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
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
    for name in ("summary.json", "v2_arch_benchmark.json", "stdout.log", "stderr.log"):
        source = output_root / name
        if source.is_file():
            destination = archive_dir / name
            shutil.copy2(source, destination)
            archived_files.append(str(destination))
    diagnostics_path = archive_dir / "host-resources.json"
    diagnostics_path.write_text(
        json.dumps({"before": before, "after": after}, indent=2),
        encoding="utf-8",
    )
    archived_files.append(str(diagnostics_path))
    return {
        "pass_number": pass_number,
        "attempt": attempt,
        "status": status,
        "failed_checks": [
            {
                "name": check.get("name"),
                "metric": check.get("metric"),
                "value": check.get("value"),
                "threshold": check.get("threshold"),
                "direction": check.get("direction"),
            }
            for check in (failed_checks or [])
        ],
        "archive_dir": str(archive_dir),
        "files": archived_files,
    }


def record_failure_episode(
    failures: dict[str, dict[str, object]],
    checks_by_run: list[list[dict[str, object]]],
) -> None:
    observations: dict[str, list[dict[str, object]]] = {}
    for checks in checks_by_run:
        for check in checks:
            observations.setdefault(str(check.get("name", "unknown")), []).append(check)

    for name, checks in observations.items():
        first = checks[0]
        entry = failures.setdefault(name, {
            "name": name,
            "metric": first.get("metric"),
            "threshold": first.get("threshold"),
            "direction": first.get("direction"),
            "failed_runs": 0,
            "confirmed_failed_runs": 0,
            "unconfirmed_failed_runs": 0,
            "confirmed_episodes": 0,
            "recovered_episodes": 0,
            "last_observed": None,
        })
        confirmed = len(checks) >= 2
        entry["failed_runs"] = int(entry["failed_runs"]) + len(checks)
        entry["last_observed"] = checks[-1].get("value")
        if confirmed:
            entry["confirmed_failed_runs"] = int(entry["confirmed_failed_runs"]) + len(checks)
            entry["confirmed_episodes"] = int(entry["confirmed_episodes"]) + 1
        else:
            entry["unconfirmed_failed_runs"] = int(entry["unconfirmed_failed_runs"]) + len(checks)
            entry["recovered_episodes"] = int(entry["recovered_episodes"]) + 1

        for check in checks:
            observed = float(check["value"])
            worst_observed = entry.get("worst_observed")
            if worst_observed is None or (
                str(entry["direction"]) == "max" and observed > float(worst_observed)
            ) or (
                str(entry["direction"]) == "min" and observed < float(worst_observed)
            ):
                entry["worst_observed"] = observed
            if confirmed:
                worst_confirmed = entry.get("worst_confirmed_observed")
                if worst_confirmed is None or (
                    str(entry["direction"]) == "max" and observed > float(worst_confirmed)
                ) or (
                    str(entry["direction"]) == "min" and observed < float(worst_confirmed)
                ):
                    entry["worst_confirmed_observed"] = observed


def sustained_failure_violations(
    failures: dict[str, dict[str, object]], completed_runs: int
) -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    for entry in failures.values():
        threshold = float(entry["threshold"])
        failed_runs = int(entry["failed_runs"])
        confirmed_failed_runs = int(entry.get("confirmed_failed_runs", failed_runs))
        worst_observed = float(entry["worst_observed"])
        worst_confirmed_observed = float(entry.get("worst_confirmed_observed", worst_observed))
        confirmed_failure_rate = confirmed_failed_runs / max(1, completed_runs)
        raw_failure_rate = failed_runs / max(1, completed_runs)
        failure_rate = confirmed_failure_rate if confirmed_failed_runs else raw_failure_rate
        direction = str(entry["direction"])
        deviation_ratio = (
            (worst_confirmed_observed - threshold) / threshold
            if direction == "max"
            else (threshold - worst_confirmed_observed) / threshold
        )
        entry["raw_failure_rate"] = round(raw_failure_rate, 6)
        entry["confirmed_failure_rate"] = round(confirmed_failure_rate, 6)
        entry["failure_rate"] = round(failure_rate, 6)
        entry["worst_deviation_ratio"] = round(deviation_ratio, 6)
        confirmation_recovered = (
            confirmed_failed_runs == 0
            and raw_failure_rate <= SUSTAINED_GATE_RARE_MAX_FAILURE_RATE
        )
        accepted_standard = (
            failure_rate <= SUSTAINED_GATE_MAX_FAILURE_RATE
            and deviation_ratio <= SUSTAINED_GATE_MAX_DEVIATION_RATIO
        )
        accepted_rare_tail = (
            failure_rate <= SUSTAINED_GATE_RARE_MAX_FAILURE_RATE
            and deviation_ratio <= SUSTAINED_GATE_RARE_MAX_DEVIATION_RATIO
        )
        entry["accepted_as_transient"] = (
            confirmation_recovered or accepted_standard or accepted_rare_tail
        )
        entry["acceptance_tier"] = (
            "confirmation_recovered"
            if confirmation_recovered
            else "standard"
            if accepted_standard
            else "rare_tail"
            if accepted_rare_tail
            else "rejected"
        )
        if not entry["accepted_as_transient"]:
            violations.append(entry)
    return sorted(violations, key=lambda check: str(check["name"]))


def run_sustained_arch_baseline(
    root: Path,
    build_dir: Path,
    output_root: Path,
    soak_profile: dict[str, str | int],
    baseline_timeout_seconds: int,
    baseline_profile: str,
    minimum_duration_seconds: int,
    resource_sample_interval_seconds: float = DEFAULT_RESOURCE_SAMPLE_INTERVAL_SECONDS,
    cancellation: CancellationState | None = None,
) -> dict[str, object]:
    cancellation = cancellation or CancellationState()
    name = "sustained arch baseline with external gates"
    started = time.monotonic()
    completed_runs = 0
    last_run: dict[str, object] = {}
    failures: dict[str, dict[str, object]] = {}
    failure_events: list[dict[str, object]] = []
    failure_archive_root = output_root / "failures"
    if failure_archive_root.exists():
        shutil.rmtree(failure_archive_root)
    summary_path = output_root / "summary.json"
    sampler = ResourceSampler(output_root, resource_sample_interval_seconds, minimum_duration_seconds)
    sampler.start()
    started = time.monotonic()
    resource_evidence: dict[str, object] | None = None
    sampler_stopped = False

    def stop_sampler() -> dict[str, object]:
        nonlocal resource_evidence, sampler_stopped
        if not sampler_stopped:
            sampler_stopped = True
            resource_evidence = sampler.stop()
        if resource_evidence is None:
            raise RuntimeError("resource sampler stopped without producing evidence")
        return resource_evidence

    def finish(result: dict[str, object]) -> dict[str, object]:
        evidence = stop_sampler()
        result["resource_evidence"] = evidence
        if (
            result.get("status") != "cancelled"
            and minimum_duration_seconds > 0
            and evidence.get("passed") is not True
        ):
            result["status"] = "failed"
            result["resource_evidence_failure"] = True
        return result

    def cancelled_result(run: dict[str, object] | None = None) -> dict[str, object]:
        return finish({
            "name": name,
            "category": "interrupted",
            "status": "cancelled",
            "signal": cancellation.signal_name,
            "duration_seconds": round(time.monotonic() - started, 3),
            "minimum_duration_seconds": minimum_duration_seconds,
            "completed_runs": completed_runs,
            "last_run": run or last_run,
            "failed_checks": sorted(failures.values(), key=lambda check: str(check["name"])),
            "failure_events": failure_events,
        })

    try:
        while completed_runs == 0 or time.monotonic() - started < minimum_duration_seconds:
            if cancellation.cancelled:
                return cancelled_result()
            completed_runs += 1
            resources_before = host_resource_snapshot()
            last_run = run_step(
                f"{name} (pass {completed_runs})",
                "baseline",
                arch_baseline_command(
                    root, build_dir, output_root, soak_profile,
                    baseline_timeout_seconds, baseline_profile
                ),
                root,
                baseline_timeout_seconds + 10,
                emit_output=completed_runs == 1,
                cancellation=cancellation,
            )
            resources_after = host_resource_snapshot()
            if last_run["status"] == "cancelled" or cancellation.cancelled:
                return cancelled_result(last_run)
            if last_run["status"] != "passed":
                sample_failures = failed_arch_checks(summary_path)
                failure_events.append(archive_failed_arch_run(
                    output_root,
                    completed_runs,
                    "initial",
                    resources_before,
                    resources_after,
                    failed_checks=sample_failures,
                ))
                # A benchmark gate failure is evidence to aggregate across the full soak,
                # while a missing/invalid summary or process failure must stop immediately.
                if last_run.get("returncode") == 2 and sample_failures:
                    checks_by_run = [sample_failures]
                    confirmation_runs = (
                        SUSTAINED_GATE_CONFIRMATION_RUNS if minimum_duration_seconds > 0 else 0
                    )
                    for confirmation in range(1, confirmation_runs + 1):
                        if cancellation.cancelled:
                            return cancelled_result(last_run)
                        completed_runs += 1
                        resources_before = host_resource_snapshot()
                        confirmation_run = run_step(
                            f"{name} (pass {completed_runs}, confirmation {confirmation})",
                            "baseline",
                            arch_baseline_command(
                                root, build_dir, output_root, soak_profile,
                                baseline_timeout_seconds, baseline_profile
                            ),
                            root,
                            baseline_timeout_seconds + 10,
                            emit_output=False,
                            cancellation=cancellation,
                        )
                        resources_after = host_resource_snapshot()
                        if confirmation_run["status"] == "cancelled" or cancellation.cancelled:
                            return cancelled_result(confirmation_run)
                        confirmation_failures = (
                            failed_arch_checks(summary_path)
                            if confirmation_run.get("returncode") == 2
                            else []
                        )
                        checks_by_run.append(confirmation_failures)
                        failure_events.append(archive_failed_arch_run(
                            output_root,
                            completed_runs,
                            f"confirmation-{confirmation}",
                            resources_before,
                            resources_after,
                            status=str(confirmation_run["status"]),
                            failed_checks=confirmation_failures,
                        ))
                        if confirmation_run["status"] != "passed" and not confirmation_failures:
                            return finish({
                                "name": name,
                                "category": "baseline",
                                "status": confirmation_run["status"],
                                "duration_seconds": round(time.monotonic() - started, 3),
                                "minimum_duration_seconds": minimum_duration_seconds,
                                "completed_runs": completed_runs,
                                "last_run": confirmation_run,
                                "failed_checks": sorted(
                                    failures.values(), key=lambda check: str(check["name"])
                                ),
                                "failure_events": failure_events,
                            })
                        last_run = confirmation_run
                    record_failure_episode(failures, checks_by_run)
                    continue
                if completed_runs > 1:
                    if last_run.get("stdout_tail"):
                        print(last_run["stdout_tail"], end="")
                    if last_run.get("stderr_tail"):
                        print(last_run["stderr_tail"], end="", file=sys.stderr)
                return finish({
                    "name": name,
                    "category": "baseline",
                    "status": last_run["status"],
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "minimum_duration_seconds": minimum_duration_seconds,
                    "completed_runs": completed_runs,
                    "last_run": last_run,
                    "failed_checks": sorted(
                        failures.values(), key=lambda check: str(check["name"])
                    ),
                    "failure_events": failure_events,
                })
        violating_checks = sustained_failure_violations(failures, completed_runs)
        status = "failed" if violating_checks else "passed"
        return finish({
            "name": name,
            "category": "baseline",
            "status": status,
            "duration_seconds": round(time.monotonic() - started, 3),
            "minimum_duration_seconds": minimum_duration_seconds,
            "completed_runs": completed_runs,
            "last_run": last_run,
            "failed_checks": sorted(failures.values(), key=lambda check: str(check["name"])),
            "violating_checks": violating_checks,
            "failure_events": failure_events,
            "transient_failure_policy": {
                "max_failure_rate": SUSTAINED_GATE_MAX_FAILURE_RATE,
                "max_deviation_ratio": SUSTAINED_GATE_MAX_DEVIATION_RATIO,
                "rare_max_failure_rate": SUSTAINED_GATE_RARE_MAX_FAILURE_RATE,
                "rare_max_deviation_ratio": SUSTAINED_GATE_RARE_MAX_DEVIATION_RATIO,
                "confirmation_runs": SUSTAINED_GATE_CONFIRMATION_RUNS,
                "confirmation_required_failures": 2,
            },
        })
    finally:
        stop_sampler()


def main() -> int:
    args = parse_args()
    profile_timeouts = PROFILE_TIMEOUTS[args.soak_profile]
    if args.build_timeout_seconds is None:
        args.build_timeout_seconds = profile_timeouts["build"]
    if args.test_timeout_seconds is None:
        args.test_timeout_seconds = profile_timeouts["test"]
    if args.baseline_timeout_seconds is None:
        args.baseline_timeout_seconds = profile_timeouts["baseline"]
    root = Path(__file__).resolve().parents[3]
    build_dir = args.build_dir.resolve()
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    soak_profile = SOAK_PROFILES[args.soak_profile]
    minimum_duration_seconds = (
        int(soak_profile["minimum_duration_seconds"])
        if args.minimum_duration_seconds is None
        else max(0, args.minimum_duration_seconds)
    )
    steps: list[dict[str, object]] = []
    summary: dict[str, object] = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(build_dir),
        "configuration": args.configuration,
        "baseline_profile": args.baseline_profile,
        "soak_profile": args.soak_profile,
        "expected_window": soak_profile.get("expected_window"),
        "minimum_duration_seconds": minimum_duration_seconds,
        "sustained_duration_seconds": 0.0,
        "sustained_completed_runs": 0,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "overall_pass": False,
        "passed": False,
        "interrupted": False,
        "interruption_signal": "",
        "current_step": "initializing",
        "completed_steps": [],
        "failed_category": "",
        "failed_step": "",
        "steps": steps,
        "artifacts": {
            "summary_path": str(summary_path),
            "arch_baseline_output_root": str(root / "runtime" / "perf" / "v2-stability-soak"),
        },
    }
    if minimum_duration_seconds > 0:
        summary["artifacts"]["notes"] = (
            "This profile repeats the architecture baseline until the required wall-clock duration is reached; "
            "it is intended for fixed runners with dedicated machine access."
        )
    atomic_write_json(summary_path, summary)

    cancellation = CancellationState()
    completed_steps: list[str] = []
    current_step = ""
    unexpected_error = ""
    unexpected_exception: Exception | None = None

    def record_step(step: dict[str, object]) -> dict[str, object]:
        nonlocal current_step
        steps.append(step)
        if step.get("status") != "cancelled":
            completed_steps.append(str(step.get("name", current_step)))
            current_step = ""
        return step

    def execute_process_step(
        name: str,
        category: str,
        command: list[str],
        cwd: Path,
        timeout_seconds: int,
    ) -> dict[str, object]:
        nonlocal current_step
        current_step = name
        return record_step(run_step(
            name,
            category,
            command,
            cwd,
            timeout_seconds,
            cancellation=cancellation,
        ))

    def finalize_summary() -> None:
        nonlocal current_step
        interrupted = cancellation.cancelled or any(
            step.get("status") == "cancelled" for step in steps
        )
        if interrupted and not current_step:
            current_step = "between_steps"
        failed = next((step for step in steps if step.get("status") != "passed"), None)
        summary.update({
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "interrupted": interrupted,
            "interruption_signal": cancellation.signal_name if interrupted else "",
            "current_step": current_step,
            "completed_steps": completed_steps,
            "overall_pass": not interrupted and not unexpected_error and failed is None,
            "passed": not interrupted and not unexpected_error and failed is None,
            "failed_category": (
                "interrupted" if interrupted else str(failed.get("category", "unknown"))
                if failed is not None else "discovery" if unexpected_error else ""
            ),
            "failed_step": (
                current_step if interrupted else str(failed.get("name", "unknown"))
                if failed is not None else unexpected_error
            ),
            "duration_seconds": round(
                sum(float(step.get("duration_seconds", 0.0)) for step in steps),
                3,
            ),
            "steps": steps,
        })
        atomic_write_json(summary_path, summary)

    with installed_signal_handlers(cancellation):
        try:
            if not args.skip_build and not cancellation.cancelled:
                step = execute_process_step(
                    "build stability focused targets",
                    "build",
                    cmake_build_args(
                        args,
                        ["project_v2_unit_tests", "project_v2_integration_tests", "v2_arch_benchmark"],
                    ),
                    root,
                    args.build_timeout_seconds,
                )
                if step["status"] != "passed" and step["status"] != "cancelled":
                    raise RuntimeError(str(step["name"]))

            if not cancellation.cancelled:
                unit_tests = find_executable(build_dir, "project_v2_unit_tests")
                integration_tests = find_executable(build_dir, "project_v2_integration_tests")
                process_steps = (
                    (
                        "I/O policy and bounded accept checks",
                        "io",
                        [str(unit_tests), f"--gtest_filter={IO_FILTER}"],
                        unit_tests.parent,
                    ),
                    (
                        "WriteBehind drain/failure checks",
                        "data",
                        [str(unit_tests), f"--gtest_filter={DATA_FILTER}"],
                        unit_tests.parent,
                    ),
                    (
                        "backend timeout/recovery checks",
                        "recovery",
                        [str(integration_tests), f"--gtest_filter={RECOVERY_FILTER}"],
                        integration_tests.parent,
                    ),
                )
                for name, category, command, cwd in process_steps:
                    if cancellation.cancelled:
                        break
                    execute_process_step(
                        name,
                        category,
                        command,
                        cwd,
                        args.test_timeout_seconds,
                    )

            if not cancellation.cancelled:
                current_step = "sustained arch baseline with external gates"
                sustained_step = record_step(run_sustained_arch_baseline(
                    root,
                    build_dir,
                    root / "runtime" / "perf" / "v2-stability-soak",
                    soak_profile,
                    args.baseline_timeout_seconds,
                    args.baseline_profile,
                    minimum_duration_seconds,
                    args.resource_sample_interval_seconds,
                    cancellation,
                ))
                summary["sustained_duration_seconds"] = sustained_step["duration_seconds"]
                summary["sustained_completed_runs"] = sustained_step["completed_runs"]
                summary["resource_evidence"] = sustained_step["resource_evidence"]
                summary["artifacts"]["resource_samples_path"] = sustained_step[
                    "resource_evidence"
                ]["samples_path"]
                summary["artifacts"]["resource_summary_path"] = sustained_step[
                    "resource_evidence"
                ]["summary_path"]
        except (FileNotFoundError, RuntimeError) as exc:
            unexpected_error = str(exc)
        except Exception as exc:
            unexpected_error = f"{type(exc).__name__}: {exc}"
            unexpected_exception = exc
        finally:
            finalize_summary()
            if cancellation.cancelled and summary.get("interrupted") is not True:
                finalize_summary()

    if unexpected_exception is not None:
        raise unexpected_exception
    if summary["interrupted"]:
        signal_number = cancellation.signal_number or signal.SIGTERM
        print(f"stability soak interrupted by {summary['interruption_signal']}", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 128 + int(signal_number)
    if not summary["passed"]:
        print(f"stability soak failed: {summary['failed_step']}", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 1
    print("stability soak completed.")
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
