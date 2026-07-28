#!/usr/bin/env python3
"""Validate a governed Redis persistence performance decision against raw evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ReviewError(RuntimeError):
    """Raised when benchmark review evidence fails closed."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path, description: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ReviewError(f"{description} is missing or unsafe: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot read {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"{description} must be a JSON object")
    return value


def require_equal(actual: Any, expected: Any, description: str) -> None:
    if actual != expected:
        raise ReviewError(f"{description} differs: {actual!r} != {expected!r}")


def require_number(value: Any, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReviewError(f"{description} must be numeric")
    return float(value)


def require_max(value: Any, maximum: Any, description: str) -> None:
    observed = require_number(value, description)
    limit = require_number(maximum, f"{description} limit")
    if observed > limit:
        raise ReviewError(f"{description} exceeds limit: {observed} > {limit}")


def require_min(value: Any, minimum: Any, description: str) -> None:
    observed = require_number(value, description)
    limit = require_number(minimum, f"{description} limit")
    if observed < limit:
        raise ReviewError(f"{description} is below limit: {observed} < {limit}")


def canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ReviewError(f"create-only review summary already exists: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.link(temporary, path)
    except OSError as exc:
        raise ReviewError(f"cannot write review summary: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def validate_review(benchmark_path: Path, decision_path: Path) -> dict[str, Any]:
    benchmark = load_object(benchmark_path, "benchmark summary")
    decision = load_object(decision_path, "change decision")
    binding = decision.get("benchmark")
    review = decision.get("review")
    rollback = decision.get("rollback")
    activation = decision.get("activation")
    if not all(
        isinstance(item, dict) for item in (binding, review, rollback, activation)
    ):
        raise ReviewError("change decision sections must be objects")
    assert isinstance(binding, dict)
    assert isinstance(review, dict)
    assert isinstance(rollback, dict)
    assert isinstance(activation, dict)

    require_equal(decision.get("schema_version"), 1, "decision schema_version")
    require_equal(decision.get("todo"), "TODO-0012", "decision TODO")
    require_equal(
        decision.get("decision"),
        "approved_for_governed_candidate",
        "change decision",
    )
    require_equal(
        decision.get("secret_material_recorded"), False, "decision secret flag"
    )
    require_equal(benchmark.get("overall_pass"), True, "benchmark pass flag")
    require_equal(
        benchmark.get("measurement_complete"), True, "measurement completeness"
    )
    require_equal(
        benchmark.get("activation_ready"), False, "benchmark activation boundary"
    )
    require_equal(
        benchmark.get("production_compose_changed"), False, "benchmark Compose boundary"
    )
    require_equal(
        benchmark.get("secret_material_recorded"), False, "benchmark secret flag"
    )
    require_equal(
        sha256_file(benchmark_path), binding.get("sha256"), "benchmark SHA-256"
    )
    require_equal(
        benchmark.get("benchmark_id"), binding.get("benchmark_id"), "benchmark ID"
    )

    controller = benchmark.get("controller", {})
    policy = benchmark.get("policy", {})
    profile = benchmark.get("candidate_profile", {})
    require_equal(
        controller.get("commit"), binding.get("controller_commit"), "controller commit"
    )
    require_equal(
        controller.get("runner_sha256"), binding.get("runner_sha256"), "runner SHA-256"
    )
    require_equal(controller.get("worktree_clean"), True, "controller worktree state")
    require_equal(policy.get("sha256"), binding.get("policy_sha256"), "policy SHA-256")
    require_equal(
        profile.get("sha256"), binding.get("profile_sha256"), "profile SHA-256"
    )

    workload = benchmark.get("workload", {})
    rounds = benchmark.get("rounds")
    aggregates = benchmark.get("aggregates", {})
    impact = benchmark.get("candidate_impact_percent", {})
    observed = review.get("observed", {})
    limits = review.get("limits", {})
    if (
        not isinstance(rounds, list)
        or not isinstance(observed, dict)
        or not isinstance(limits, dict)
    ):
        raise ReviewError("benchmark rounds or review values are invalid")
    repetitions = workload.get("repetitions_per_mode")
    require_equal(repetitions, review.get("repetitions_per_mode"), "repetition count")
    require_equal(len(rounds), int(repetitions) * 2, "round count")
    require_equal(
        {item.get("mode") for item in rounds},
        {"rdb_only", "aof_everysec_rdb"},
        "mode set",
    )
    if any(item.get("passed") is not True for item in rounds):
        raise ReviewError("one or more benchmark rounds did not pass")
    if any(item.get("redis_bgsave", {}).get("last_status") != "ok" for item in rounds):
        raise ReviewError("one or more benchmark BGSAVE operations failed")

    candidate_rounds = [
        item for item in rounds if item.get("mode") == "aof_everysec_rdb"
    ]
    for item in candidate_rounds:
        effective = item.get("effective_configuration", {})
        require_equal(effective.get("appendonly"), "yes", "candidate appendonly")
        require_equal(effective.get("appendfsync"), "everysec", "candidate appendfsync")
        require_equal(
            effective.get("maxmemory-policy"), "noeviction", "candidate eviction policy"
        )
        require_equal(item.get("redis_aof_delayed_fsync"), 0, "candidate delayed fsync")

    candidate = aggregates.get("aof_everysec_rdb", {})
    expected_observed = {
        "throughput_percent": impact.get("throughput_percent"),
        "p50_latency_percent": impact.get("p50_latency_percent"),
        "p99_latency_percent": impact.get("p99_latency_percent"),
        "redis_cpu_percent": impact.get("redis_cpu_percent"),
        "redis_rss_percent": impact.get("redis_rss_percent"),
        "redis_disk_write_bytes_percent": impact.get("redis_disk_write_bytes_percent"),
        "candidate_throughput_requests_per_second_median": candidate.get(
            "throughput_requests_per_second_median"
        ),
        "candidate_p50_latency_ms_median": candidate.get("p50_latency_ms_median"),
        "candidate_p99_latency_ms_median": candidate.get("p99_latency_ms_median"),
        "candidate_redis_cpu_percent_of_one_core_median": candidate.get(
            "redis_cpu_percent_of_one_core_median"
        ),
        "candidate_redis_rss_sampled_peak_bytes_median": candidate.get(
            "redis_rss_sampled_peak_bytes_median"
        ),
        "candidate_workload_disk_write_bytes_median": candidate.get(
            "redis_workload_disk_write_bytes_median"
        ),
        "candidate_bgsave_disk_write_bytes_median": candidate.get(
            "redis_bgsave_disk_write_bytes_median"
        ),
        "candidate_aof_delayed_fsync_total": candidate.get(
            "redis_aof_delayed_fsync_total"
        ),
        "candidate_worst_round_throughput_requests_per_second": min(
            require_number(
                item.get("workload", {}).get("throughput_requests_per_second"),
                "round throughput",
            )
            for item in candidate_rounds
        ),
        "candidate_worst_round_p99_latency_ms": max(
            require_number(item.get("workload", {}).get("p99_latency_ms"), "round P99")
            for item in candidate_rounds
        ),
    }
    for key, value in expected_observed.items():
        require_equal(observed.get(key), value, f"review observed {key}")

    require_min(
        impact.get("throughput_percent"),
        -float(limits["maximum_throughput_regression_percent"]),
        "throughput impact",
    )
    require_max(
        impact.get("p50_latency_percent"),
        limits.get("maximum_p50_regression_percent"),
        "P50 impact",
    )
    require_max(
        impact.get("p99_latency_percent"),
        limits.get("maximum_p99_regression_percent"),
        "P99 impact",
    )
    require_max(
        impact.get("redis_cpu_percent"),
        limits.get("maximum_redis_cpu_regression_percent"),
        "Redis CPU impact",
    )
    require_max(
        impact.get("redis_rss_percent"),
        limits.get("maximum_redis_rss_regression_percent"),
        "Redis RSS impact",
    )
    require_max(
        candidate.get("p99_latency_ms_median"),
        limits.get("maximum_candidate_p99_latency_ms"),
        "candidate median P99",
    )
    require_min(
        expected_observed["candidate_worst_round_throughput_requests_per_second"],
        limits.get("minimum_candidate_round_throughput_requests_per_second"),
        "candidate worst-round throughput",
    )
    per_request = require_number(
        candidate.get("redis_workload_disk_write_bytes_median"),
        "candidate workload writes",
    ) / require_number(
        workload.get("requests_per_repetition"), "requests per repetition"
    )
    require_equal(
        review.get("disk_write_assessment", {}).get(
            "candidate_workload_write_bytes_per_request"
        ),
        per_request,
        "disk write per request",
    )
    require_max(
        per_request,
        limits.get("maximum_candidate_workload_write_bytes_per_request"),
        "candidate write bytes per request",
    )
    require_max(
        candidate.get("redis_aof_delayed_fsync_total"),
        limits.get("maximum_aof_delayed_fsync"),
        "candidate delayed fsync total",
    )

    required_rollback = {
        "old_deployment_retained": True,
        "active_volume_must_be_preserved": True,
        "aof_to_rdb_is_a_data_format_downgrade": True,
        "blind_old_compose_restore_prohibited": True,
        "write_quiescence_required": True,
        "fresh_bgsave_required": True,
        "rdb_offline_validation_required": True,
        "checkpoint_identity_must_be_recorded": True,
        "automatic_release_restore_requires_data_compatible_transition_hook": True,
    }
    for key, expected in required_rollback.items():
        require_equal(rollback.get(key), expected, f"rollback contract {key}")
    require_equal(
        activation.get("production_activated"), False, "production activation state"
    )
    require_equal(
        activation.get("immutable_release_required"),
        True,
        "immutable release requirement",
    )
    require_equal(
        activation.get("governance_path"),
        "protected_pull_request",
        "activation governance path",
    )
    require_equal(
        activation.get("formal_todo0012_claim"), False, "formal claim boundary"
    )
    require_equal(review.get("accepted"), True, "performance review decision")

    return {
        "schema_version": 1,
        "todo": "TODO-0012",
        "change_id": decision.get("change_id"),
        "generated_at": now(),
        "overall_pass": True,
        "benchmark": {
            "path": str(benchmark_path.resolve()),
            "sha256": sha256_file(benchmark_path),
            "benchmark_id": benchmark.get("benchmark_id"),
        },
        "decision": {
            "path": str(decision_path.resolve()),
            "sha256": sha256_file(decision_path),
            "status": decision.get("decision"),
        },
        "performance_review_pass": True,
        "governed_change_record_valid": True,
        "rollback_contract_valid": True,
        "governed_candidate_ready": True,
        "production_activated": False,
        "effective_config_verified": False,
        "crash_rpo_verified": False,
        "activation_ready": False,
        "formal_todo0012_claim": False,
        "secret_material_recorded": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-summary", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = validate_review(args.benchmark_summary, args.decision)
        write_new(args.summary_path, canonical_json(result))
    except (KeyError, ReviewError, TypeError, ValueError) as exc:
        print(f"Redis persistence review: FAIL: {exc}", file=os.sys.stderr)
        return 1
    print("Redis persistence review: PASS")
    print(f"summary: {args.summary_path}")
    print("activation_ready: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
