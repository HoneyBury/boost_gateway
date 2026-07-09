#!/usr/bin/env python3
"""Collect a bounded performance/operability snapshot from the Docker production stack."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_CONTAINERS = [
    "boost-gateway",
    "boost-login-backend",
    "boost-room-backend",
    "boost-battle-backend",
    "boost-matchmaking-backend",
    "boost-leaderboard-backend",
    "boost-redis",
    "boost-prometheus",
    "boost-grafana",
    "boost-alertmanager",
    "boost-redis-exporter",
]


def run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)


def require(cmd: list[str], timeout: int = 20) -> str:
    result = run(cmd, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def parse_json_output(label: str, raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} did not return valid JSON: {raw[:500]}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} returned non-object JSON")
    return value


def compose_cmd(compose_file: Path, *args: str) -> list[str]:
    return ["docker", "compose", "-f", str(compose_file), *args]


def compose_exec(compose_file: Path, service: str, *args: str, timeout: int = 20) -> str:
    return require(compose_cmd(compose_file, "exec", "-T", service, *args), timeout=timeout)


def git_commit(root: Path) -> str:
    result = run(["git", "rev-parse", "HEAD"], timeout=10)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def parse_stats(raw: str) -> list[dict[str, Any]]:
    stats: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        stats.append(json.loads(line))
    return stats


def summarize_backend_metrics(diagnostics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics = diagnostics.get("backend_metrics")
    if not isinstance(metrics, dict):
        return {}
    summary: dict[str, dict[str, Any]] = {}
    for service in ("login", "room", "battle", "matchmaking", "leaderboard"):
        snap = metrics.get(service)
        if not isinstance(snap, dict):
            summary[service] = {"present": False}
            continue
        requests = int(snap.get("total_requests", 0))
        errors = int(snap.get("total_errors", 0))
        timeouts = int(snap.get("total_timeouts", 0))
        summary[service] = {
            "present": True,
            "total_requests": requests,
            "total_successes": int(snap.get("total_successes", 0)),
            "total_errors": errors,
            "total_timeouts": timeouts,
            "avg_latency_us": snap.get("avg_latency_us"),
            "latency_sample_count": snap.get("latency_sample_count"),
            "has_traffic": requests > 0,
            "healthy_counters": errors == 0 and timeouts == 0,
        }
    return summary


def collect(compose_file: Path, output_dir: Path, containers: list[str]) -> dict[str, Any]:
    gateway_ready = parse_json_output(
        "gateway /ready",
        compose_exec(compose_file, "gateway", "curl", "-fsS", "http://127.0.0.1:9080/ready"),
    )
    gateway_diagnostics = parse_json_output(
        "gateway diagnostics",
        compose_exec(compose_file, "gateway", "curl", "-fsS", "http://127.0.0.1:9080/metrics/diagnostics/json"),
    )
    prometheus_ready = compose_exec(compose_file, "prometheus", "wget", "-qO-", "http://127.0.0.1:9090/-/ready")
    prometheus_targets = parse_json_output(
        "prometheus targets",
        compose_exec(
            compose_file,
            "prometheus",
            "wget",
            "-qO-",
            "http://127.0.0.1:9090/api/v1/targets?state=active",
        ),
    )
    grafana_health = parse_json_output(
        "grafana health",
        compose_exec(compose_file, "grafana", "wget", "-qO-", "http://127.0.0.1:3000/api/health"),
    )

    stats_raw = require(["docker", "stats", "--no-stream", "--format", "{{json .}}", *containers], timeout=30)
    docker_stats = parse_stats(stats_raw)

    active_targets = prometheus_targets.get("data", {}).get("activeTargets", [])
    prometheus_up = all(target.get("health") == "up" for target in active_targets)
    backend_metric_summary = summarize_backend_metrics(gateway_diagnostics)
    summary = {
        "collected_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "git_commit": git_commit(Path.cwd()),
        "compose_file": str(compose_file),
        "overall_pass": bool(
            gateway_ready.get("ready") is True
            and gateway_ready.get("status") == "pass"
            and prometheus_ready.strip().lower() == "prometheus server is ready."
            and prometheus_up
            and grafana_health.get("database") == "ok"
        ),
        "gateway_ready": gateway_ready,
        "gateway_diagnostics": gateway_diagnostics,
        "business_backend_metrics": backend_metric_summary,
        "prometheus": {
            "ready": prometheus_ready,
            "active_target_count": len(active_targets),
            "all_targets_up": prometheus_up,
            "targets": [
                {
                    "job": target.get("labels", {}).get("job"),
                    "scrape_url": target.get("scrapeUrl"),
                    "health": target.get("health"),
                    "last_error": target.get("lastError"),
                }
                for target in active_targets
            ],
        },
        "grafana_health": grafana_health,
        "docker_stats": docker_stats,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "report.md").write_text(render_report(summary), encoding="utf-8")
    return summary


def render_report(summary: dict[str, Any]) -> str:
    ready = summary["gateway_ready"]
    diagnostics = summary["gateway_diagnostics"]
    lines = [
        "# Docker Production Performance Snapshot",
        "",
        f"- Collected at: `{summary['collected_at']}`",
        f"- Git commit: `{summary['git_commit']}`",
        f"- Overall pass: `{summary['overall_pass']}`",
        f"- Gateway ready: `{ready.get('ready')}` / `{ready.get('status')}`",
        f"- Prometheus targets up: `{summary['prometheus']['all_targets_up']}` ({summary['prometheus']['active_target_count']} active)",
        f"- Grafana database: `{summary['grafana_health'].get('database')}`",
        "",
        "## Gateway Diagnostics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| io cores | {diagnostics.get('io_core_count')} |",
        f"| active sessions | {diagnostics.get('total_active_sessions')} |",
        f"| accepted sessions | {diagnostics.get('total_accepted_sessions')} |",
        f"| outbound dispatches | {diagnostics.get('total_outbound_dispatches')} |",
        f"| backend instances | {len(diagnostics.get('backend_instances', []))} |",
        "",
        "## Business Backend Metrics",
        "",
        "| Service | Present | Has traffic | Healthy counters | Requests | Successes | Errors | Timeouts | Avg latency us | Samples |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for service, metric in summary.get("business_backend_metrics", {}).items():
        lines.append(
            f"| `{service}` | {metric.get('present')} | {metric.get('has_traffic')} | "
            f"{metric.get('healthy_counters')} | {metric.get('total_requests', '')} | "
            f"{metric.get('total_successes', '')} | {metric.get('total_errors', '')} | "
            f"{metric.get('total_timeouts', '')} | {metric.get('avg_latency_us', '')} | "
            f"{metric.get('latency_sample_count', '')} |"
        )
    lines.extend([
        "",
        "## Container Stats",
        "",
        "| Container | CPU | Memory | Mem % | PIDs | Net I/O | Block I/O |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for stat in summary["docker_stats"]:
        lines.append(
            "| {name} | {cpu} | {mem} | {memp} | {pids} | {net} | {block} |".format(
                name=stat.get("Name") or stat.get("Container"),
                cpu=stat.get("CPUPerc"),
                mem=stat.get("MemUsage"),
                memp=stat.get("MemPerc"),
                pids=stat.get("PIDs"),
                net=stat.get("NetIO"),
                block=stat.get("BlockIO"),
            )
        )

    lines.extend(
        [
            "",
            "## Prometheus Targets",
            "",
            "| Job | Health | Last error |",
            "| --- | --- | --- |",
        ]
    )
    for target in summary["prometheus"]["targets"]:
        lines.append(f"| {target.get('job')} | {target.get('health')} | {target.get('last_error') or ''} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", default=str(root / "env/docker/docker-compose.yml"))
    parser.add_argument("--output-dir", default=str(root / "runtime/perf/docker-production-snapshot"))
    parser.add_argument("--container", action="append", dest="containers", help="Container name to include in docker stats.")
    args = parser.parse_args()

    try:
        summary = collect(Path(args.compose_file), Path(args.output_dir), args.containers or DEFAULT_CONTAINERS)
    except Exception as exc:
        print(f"[docker-production-snapshot] failed: {exc}", file=sys.stderr)
        return 2

    print(f"[docker-production-snapshot] summary: {Path(args.output_dir) / 'summary.json'}")
    print(f"[docker-production-snapshot] report: {Path(args.output_dir) / 'report.md'}")
    print(f"[docker-production-snapshot] overall_pass={summary['overall_pass']}")
    return 0 if summary["overall_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())

