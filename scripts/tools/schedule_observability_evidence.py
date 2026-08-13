#!/usr/bin/env python3
"""Create daily or weekly observability evidence from loopback Prometheus."""

from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib import observability_evidence as evidence


DEFAULT_LEDGER = Path("/var/lib/boost-gateway-evidence/observability")
DEFAULT_DEPLOYMENT = Path("/opt/boost-gateway/current/record.json")
DEFAULT_PROMETHEUS_URL = "http://127.0.0.1:9090"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
EXPECTED_JOBS = {"gateway", "prometheus", "redis-exporter", "node-exporter", "cadvisor"}
EXPECTED_CONTAINERS = {
    "boost-gateway",
    "boost-login-backend",
    "boost-room-backend",
    "boost-battle-backend",
    "boost-matchmaking-backend",
    "boost-leaderboard-backend",
    "boost-redis",
    "boost-redis-exporter",
    "boost-node-exporter",
    "boost-cadvisor",
    "boost-prometheus",
    "boost-alertmanager",
    "boost-grafana",
}
BACKEND_FAILURE_QUERY = " + ".join(
    (
        "sum(rate(gateway_backend_login_errors_total[5m]))",
        "sum(rate(gateway_backend_login_timeouts_total[5m]))",
        "sum(rate(gateway_backend_room_errors_total[5m]))",
        "sum(rate(gateway_backend_room_timeouts_total[5m]))",
        "sum(rate(gateway_backend_battle_errors_total[5m]))",
        "sum(rate(gateway_backend_battle_timeouts_total[5m]))",
        "sum(rate(gateway_backend_matchmaking_errors_total[5m]))",
        "sum(rate(gateway_backend_matchmaking_timeouts_total[5m]))",
        "sum(rate(gateway_backend_leaderboard_errors_total[5m]))",
        "sum(rate(gateway_backend_leaderboard_timeouts_total[5m]))",
    )
)

QUERY_CATALOG = (
    ("target_availability", 'min by (job) (up{job=~"gateway|prometheus|redis-exporter|node-exporter|cadvisor"})'),
    ("host_cpu_utilization", '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))'),
    ("host_load", "max(node_load1)"),
    ("host_memory_utilization", "1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes"),
    ("host_filesystem_available", 'min(node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs"} / node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs"})'),
    ("host_disk_read_rate", "sum(rate(node_disk_read_bytes_total[5m]))"),
    ("host_disk_write_rate", "sum(rate(node_disk_written_bytes_total[5m]))"),
    ("host_network_receive_rate", 'sum(rate(node_network_receive_bytes_total{device!="lo"}[5m]))'),
    ("host_network_transmit_rate", 'sum(rate(node_network_transmit_bytes_total{device!="lo"}[5m]))'),
    ("host_temperature", "max(node_hwmon_temp_celsius or node_thermal_zone_temp)"),
    ("container_cpu", "sum by (container) (rate(container_cpu_usage_seconds_total[5m]) * on (id) group_left (container) boost_gateway_container_info)"),
    ("container_memory", "sum by (container) (container_memory_working_set_bytes * on (id) group_left (container) boost_gateway_container_info)"),
    ("container_restarts", "max by (container) (boost_gateway_container_restart_count)"),
    ("gateway_sessions", "max(gateway_active_sessions)"),
    ("gateway_accept_rate", "sum(rate(gateway_accepted_sessions_total[5m]))"),
    ("gateway_backend_failures", BACKEND_FAILURE_QUERY),
    ("gateway_backend_p99_latency", 'max({__name__=~"gateway_backend_.*_p99_latency_us"})'),
    ("prometheus_rule_evaluation_failures", "sum(increase(prometheus_rule_evaluation_failures_total[5m]))"),
    ("redis_availability", 'min(up{job="redis-exporter"})'),
    ("redis_rdb_status", "min(redis_rdb_last_bgsave_status)"),
    ("redis_rdb_age", "max(time() - redis_rdb_last_save_timestamp_seconds)"),
)


class SchedulerError(RuntimeError):
    """Raised when a checkpoint cannot be collected or recorded safely."""


@dataclass(frozen=True)
class EvidenceWindow:
    kind: str
    record_id: str
    start: datetime
    end: datetime
    step_seconds: int


def isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def previous_window(kind: str, observed_at: datetime) -> EvidenceWindow:
    if observed_at.tzinfo is None:
        raise SchedulerError("observed time must be timezone-aware")
    current = observed_at.astimezone(UTC)
    today = datetime.combine(current.date(), time.min, UTC)
    if kind == "daily":
        start = today - timedelta(days=1)
        return EvidenceWindow(kind, f"daily-{start.date().isoformat()}", start, today, 300)
    if kind == "weekly":
        this_monday = today - timedelta(days=today.weekday())
        start = this_monday - timedelta(days=7)
        iso_year, iso_week, _ = start.isocalendar()
        return EvidenceWindow(kind, f"weekly-{iso_year}-W{iso_week:02d}", start, this_monday, 900)
    raise SchedulerError(f"unsupported schedule kind: {kind}")


def validate_prometheus_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.hostname is None
    ):
        raise SchedulerError("Prometheus URL must be an unauthenticated HTTP origin")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise SchedulerError("Prometheus URL must use a numeric loopback address") from exc
    if not address.is_loopback:
        raise SchedulerError("Prometheus URL must be loopback-only")
    try:
        port = parsed.port
    except ValueError as exc:
        raise SchedulerError("Prometheus URL has an invalid port") from exc
    if port is None or not 1 <= port <= 65535:
        raise SchedulerError("Prometheus URL must include a valid port")
    return value.rstrip("/")


class PrometheusClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = validate_prometheus_url(base_url)
        self.timeout_seconds = timeout_seconds

    def query_range(self, expression: str, window: EvidenceWindow) -> list[dict[str, Any]]:
        parameters = urllib.parse.urlencode(
            {
                "query": expression,
                "start": isoformat(window.start),
                "end": isoformat(window.end - timedelta(seconds=1)),
                "step": str(window.step_seconds),
            }
        )
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/query_range?{parameters}",
            headers={"Accept": "application/json", "User-Agent": "boost-gateway-evidence-scheduler/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - URL is validated as numeric loopback
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, urllib.error.URLError) as exc:
            raise SchedulerError(f"Prometheus range query failed: {exc}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SchedulerError("Prometheus range response exceeds the size limit")
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SchedulerError("Prometheus returned invalid JSON") from exc
        data = document.get("data") if isinstance(document, dict) else None
        result = data.get("result") if isinstance(data, dict) else None
        if (
            not isinstance(document, dict)
            or document.get("status") != "success"
            or data.get("resultType") != "matrix"
            or not isinstance(result, list)
        ):
            raise SchedulerError("Prometheus returned an unsuccessful matrix response")
        return result


def _signal_summary(
    name: str, expression: str, series: list[dict[str, Any]], window: EvidenceWindow
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    gaps: list[dict[str, Any]] = []
    sample_count = 0
    valid_series = 0
    largest_gap = 0.0
    statistics: list[dict[str, Any]] = []
    observed_labels: set[str] = set()
    for index, item in enumerate(series):
        values = item.get("values") if isinstance(item, dict) else None
        metric = item.get("metric") if isinstance(item, dict) else None
        if isinstance(metric, dict):
            label_name = "job" if name == "target_availability" else "container"
            label_value = metric.get(label_name)
            if isinstance(label_value, str) and label_value:
                observed_labels.add(label_value)
        if not isinstance(values, list):
            gaps.append({"signal": name, "reason": "invalid_series", "series_index": index})
            continue
        finite_samples: list[tuple[float, float]] = []
        for sample in values:
            if not isinstance(sample, list) or len(sample) != 2:
                continue
            try:
                timestamp = float(sample[0])
                numeric = float(sample[1])
            except (TypeError, ValueError):
                continue
            if math.isfinite(timestamp) and math.isfinite(numeric):
                finite_samples.append((timestamp, numeric))
        if not finite_samples:
            gaps.append({"signal": name, "reason": "series_has_no_finite_samples", "series_index": index})
            continue
        finite_samples.sort()
        timestamps = [sample[0] for sample in finite_samples]
        numerics = [sample[1] for sample in finite_samples]
        valid_series += 1
        sample_count += len(timestamps)
        intervals = [right - left for left, right in zip(timestamps, timestamps[1:])]
        observed_gap = max(intervals, default=0.0)
        largest_gap = max(largest_gap, observed_gap)
        boundary_slack = window.step_seconds * 1.5
        if timestamps[0] - window.start.timestamp() > boundary_slack:
            gaps.append({"signal": name, "reason": "late_first_sample", "series_index": index})
        if window.end.timestamp() - timestamps[-1] > boundary_slack:
            gaps.append({"signal": name, "reason": "early_last_sample", "series_index": index})
        if observed_gap > boundary_slack:
            gaps.append(
                {
                    "signal": name,
                    "reason": "sample_gap",
                    "series_index": index,
                    "gap_seconds": observed_gap,
                }
            )
        statistics.append(
            {
                "metric": metric if isinstance(metric, dict) else {},
                "sample_count": len(numerics),
                "first": numerics[0],
                "last": numerics[-1],
                "minimum": min(numerics),
                "maximum": max(numerics),
                "average": sum(numerics) / len(numerics),
                "delta": numerics[-1] - numerics[0],
            }
        )
    if not series:
        gaps.append({"signal": name, "reason": "no_series"})
    expected_labels: set[str] = set()
    label_name = ""
    if name == "target_availability":
        expected_labels, label_name = EXPECTED_JOBS, "job"
    elif name in {"container_cpu", "container_memory", "container_restarts"}:
        expected_labels, label_name = EXPECTED_CONTAINERS, "container"
    missing_labels = sorted(expected_labels - observed_labels)
    if missing_labels:
        gaps.append(
            {
                "signal": name,
                "reason": "missing_expected_series",
                "label": label_name,
                "missing": missing_labels,
            }
        )
    return (
        {
            "name": name,
            "query": expression,
            "series_count": len(series),
            "valid_series_count": valid_series,
            "finite_sample_count": sample_count,
            "largest_observed_gap_seconds": largest_gap,
            "statistics": statistics,
            "series": series,
        },
        gaps,
    )


def collect_report(
    window: EvidenceWindow,
    client: PrometheusClient,
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for name, expression in QUERY_CATALOG:
        try:
            series = client.query_range(expression, window)
        except SchedulerError as exc:
            series = []
            gaps.append({"signal": name, "reason": "query_error", "detail": str(exc)})
        summary, signal_gaps = _signal_summary(name, expression, series, window)
        signals.append(summary)
        gaps.extend(signal_gaps)
    return {
        "schema_version": 1,
        "kind": window.kind,
        "record_id": window.record_id,
        "generated_at": isoformat(generated_at),
        "window": {
            "timezone": "UTC",
            "start": isoformat(window.start),
            "end_exclusive": isoformat(window.end),
            "selection": "previous full UTC day" if window.kind == "daily" else "previous full ISO week",
            "step_seconds": window.step_seconds,
        },
        "prometheus": {"url": client.base_url, "query_count": len(QUERY_CATALOG)},
        "signals": signals,
        "gaps": gaps,
        "gap_count": len(gaps),
        "coverage_complete": not gaps,
        "overall_pass": not gaps,
        "formal_30_day_claim": False,
        "claims": {
            "availability_slo_proven": False,
            "incident_free_proven": False,
            "formal_validation_window_proven": False,
        },
        "secret_material_recorded": False,
        "conclusion": "Operational checkpoint only; gaps are explicit and no formal readiness claim is made.",
    }


def _weekly_daily_references(
    ledger_root: Path, window: EvidenceWindow
) -> tuple[list[Path], list[dict[str, Any]], list[dict[str, Any]]]:
    paths: list[Path] = []
    references: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for offset in range(7):
        checkpoint_date = (window.start.date() + timedelta(days=offset)).isoformat()
        record_id = f"daily-{checkpoint_date}"
        path = ledger_root / "records" / "daily" / f"{record_id}.json"
        try:
            record = evidence.load_json_object(path, "scheduled daily record")
            attributes = record.get("attributes")
            if (
                record.get("kind") != "daily"
                or record.get("record_id") != record_id
                or not isinstance(attributes, dict)
                or attributes.get("checkpoint_date") != checkpoint_date
            ):
                raise SchedulerError("daily record identity drifted")
            reference = evidence.file_reference(path, "scheduled-daily-record")
        except (evidence.EvidenceError, OSError, SchedulerError) as exc:
            gaps.append(
                {
                    "signal": "scheduled_daily_records",
                    "reason": "missing_or_invalid_daily_record",
                    "checkpoint_date": checkpoint_date,
                    "detail": str(exc),
                }
            )
            continue
        paths.append(path)
        references.append(reference)
    return paths, references, gaps


def _deployment_snapshot(path: Path) -> tuple[Path, str]:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SchedulerError(f"cannot resolve active deployment record: {exc}") from exc
    evidence.load_json_object(resolved, "active deployment record")
    return resolved, evidence.sha256_file(resolved)


def _assert_deployment_unchanged(path: Path, resolved: Path, digest: str) -> None:
    try:
        current = path.resolve(strict=True)
    except OSError as exc:
        raise SchedulerError(f"active deployment changed during collection: {exc}") from exc
    if current != resolved or evidence.sha256_file(current) != digest:
        raise SchedulerError("active deployment changed during collection")


def _write_new_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _validate_existing(
    ledger_root: Path, report_path: Path, record_path: Path, window: EvidenceWindow
) -> dict[str, Any]:
    report = evidence.load_json_object(report_path, "scheduled observability report")
    record = evidence.load_json_object(record_path, "scheduled observability record")
    if report.get("kind") != window.kind or report.get("record_id") != window.record_id:
        raise SchedulerError("existing scheduled report identity drifted")
    if record.get("kind") != window.kind or record.get("record_id") != window.record_id:
        raise SchedulerError("existing scheduled record identity drifted")
    summaries = record.get("raw_summaries")
    expected = evidence.sha256_file(report_path)
    if not isinstance(summaries, list) or not any(
        isinstance(item, dict)
        and item.get("source_path") == str(report_path.resolve())
        and item.get("sha256") == expected
        for item in summaries
    ):
        raise SchedulerError("existing scheduled record is not bound to its report")
    return report


class SchedulerLock:
    def __init__(self, ledger_root: Path) -> None:
        self.path = ledger_root / ".scheduler.lock"
        self.descriptor = -1

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        self.descriptor = os.open(self.path, flags, 0o640)
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.descriptor)
            self.descriptor = -1
            raise SchedulerError("another observability evidence schedule is running") from exc

    def __exit__(self, *_args: object) -> None:
        if self.descriptor >= 0:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)


def run_scheduler(
    kind: str,
    ledger_root: Path,
    deployment_path: Path,
    client: PrometheusClient,
    *,
    observed_at: datetime,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with SchedulerLock(ledger_root):
        return _run_scheduler_locked(
            kind,
            ledger_root,
            deployment_path,
            client,
            observed_at=observed_at,
            identity=identity,
        )


def _run_scheduler_locked(
    kind: str,
    ledger_root: Path,
    deployment_path: Path,
    client: PrometheusClient,
    *,
    observed_at: datetime,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    window = previous_window(kind, observed_at)
    report_path = ledger_root / "reports" / kind / f"{window.record_id}.json"
    record_path = ledger_root / "records" / kind / f"{window.record_id}.json"
    if record_path.exists() or record_path.is_symlink():
        report = _validate_existing(ledger_root, report_path, record_path, window)
        return {"status": "already_recorded", "record": str(record_path), "report": str(report_path), "gap_count": report["gap_count"]}

    if report_path.exists() or report_path.is_symlink():
        report = evidence.load_json_object(report_path, "scheduled observability report")
        if report.get("kind") != kind or report.get("record_id") != window.record_id:
            raise SchedulerError("existing scheduled report identity drifted")
    else:
        resolved_deployment, deployment_digest = _deployment_snapshot(deployment_path)
        report = collect_report(window, client, generated_at=observed_at)
        report["deployment_record"] = evidence.file_reference(
            resolved_deployment, "active-deployment-record"
        )
        daily_paths: list[Path] = []
        if kind == "weekly":
            daily_paths, references, daily_gaps = _weekly_daily_references(
                ledger_root, window
            )
            report["scheduled_daily_records"] = references
            report["expected_daily_record_count"] = 7
            report["gaps"].extend(daily_gaps)
            report["gap_count"] = len(report["gaps"])
            report["coverage_complete"] = not report["gaps"]
            report["overall_pass"] = not report["gaps"]
        _assert_deployment_unchanged(
            deployment_path, resolved_deployment, deployment_digest
        )
        try:
            _write_new_json(report_path, report)
        except FileExistsError:
            report = evidence.load_json_object(report_path, "scheduled observability report")

    deployment_reference = report.get("deployment_record")
    if not isinstance(deployment_reference, dict):
        raise SchedulerError("scheduled report has no deployment binding")
    resolved_deployment, deployment_digest = _deployment_snapshot(deployment_path)
    if (
        deployment_reference.get("path") != str(resolved_deployment)
        or deployment_reference.get("sha256") != deployment_digest
    ):
        raise SchedulerError("active deployment differs from scheduled report binding")

    daily_paths: list[Path] = []
    if kind == "weekly":
        references = report.get("scheduled_daily_records")
        if not isinstance(references, list):
            raise SchedulerError("weekly report has no scheduled daily references")
        for reference in references:
            if not isinstance(reference, dict):
                raise SchedulerError("weekly report has an invalid daily reference")
            path = Path(str(reference.get("path", "")))
            observed = evidence.file_reference(path, "scheduled-daily-record")
            if (
                observed["sha256"] != reference.get("sha256")
                or observed["size_bytes"] != reference.get("size_bytes")
            ):
                raise SchedulerError(f"scheduled daily record drifted: {path}")
            daily_paths.append(path)

    attributes: dict[str, Any] = {
        "window_start": isoformat(window.start),
        "window_end_exclusive": isoformat(window.end),
        "coverage_complete": report.get("coverage_complete") is True,
        "gap_count": report.get("gap_count"),
        "formal_claim": False,
    }
    if kind == "daily":
        attributes["checkpoint_date"] = window.start.date().isoformat()
    else:
        attributes["period_start"] = window.start.date().isoformat()
        attributes["period_end"] = (window.end.date() - timedelta(days=1)).isoformat()
    try:
        created_path, _ = evidence.create_record(
            ledger_root,
            kind,
            window.record_id,
            [report_path, *daily_paths],
            resolved_deployment,
            attributes=attributes,
            identity=identity,
        )
    except evidence.EvidenceError as exc:
        if record_path.exists() and "cannot be overwritten" in str(exc):
            report = _validate_existing(ledger_root, report_path, record_path, window)
            return {"status": "already_recorded", "record": str(record_path), "report": str(report_path), "gap_count": report["gap_count"]}
        raise
    return {"status": "recorded", "record": str(created_path), "report": str(report_path), "gap_count": report["gap_count"], "coverage_complete": report["coverage_complete"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("daily", "weekly"))
    parser.add_argument("--ledger-root", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--deployment-record", type=Path, default=DEFAULT_DEPLOYMENT)
    parser.add_argument("--prometheus-url", default=DEFAULT_PROMETHEUS_URL)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()
    try:
        if args.timeout_seconds <= 0:
            raise SchedulerError("timeout must be positive")
        result = run_scheduler(
            args.kind,
            args.ledger_root,
            args.deployment_record,
            PrometheusClient(args.prometheus_url, args.timeout_seconds),
            observed_at=datetime.now(UTC),
        )
    except (SchedulerError, evidence.EvidenceError, OSError, ValueError) as exc:
        print(f"observability evidence scheduler: FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if int(result.get("gap_count", 0)) > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
