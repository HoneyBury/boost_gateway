#!/usr/bin/env python3
"""Compare repeated v2 architecture baselines and fail on regressions."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ComparisonError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read JSON summary {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ComparisonError(f"summary must be a JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def results_by_name(summary: dict[str, Any], path: Path) -> dict[str, dict[str, Any]]:
    results = summary.get("results")
    if not isinstance(results, list):
        raise ComparisonError(f"summary is missing results: {path}")
    indexed: dict[str, dict[str, Any]] = {}
    for item in results:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            indexed[item["name"]] = item
    return indexed


def metric_values(paths: list[Path], case: str, metric: str) -> list[float]:
    values: list[float] = []
    for path in paths:
        result = results_by_name(load_json(path), path).get(case)
        if result is None:
            raise ComparisonError(f"summary {path} is missing case {case}")
        samples = result.get("samples")
        value = result.get(metric)
        if not isinstance(samples, int) or samples <= 0:
            raise ComparisonError(f"summary {path} has no samples for {case}")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ComparisonError(f"summary {path} has invalid {case}.{metric}")
        values.append(float(value))
    return values


def evaluate_comparison(
    baseline_paths: list[Path],
    candidate_paths: list[Path],
    config: dict[str, Any],
    *,
    baseline_ref: str,
    candidate_ref: str,
) -> dict[str, Any]:
    policy = config.get("architecture_regression_gates")
    if not isinstance(policy, dict):
        raise ComparisonError("gate config is missing architecture_regression_gates")
    minimum_repetitions = policy.get("minimum_repetitions")
    checks_config = policy.get("checks")
    if not isinstance(minimum_repetitions, int) or minimum_repetitions < 2:
        raise ComparisonError("minimum_repetitions must be at least two")
    if not isinstance(checks_config, list) or not checks_config:
        raise ComparisonError("architecture regression checks must be non-empty")
    if len(baseline_paths) != len(candidate_paths):
        raise ComparisonError("baseline and candidate repetition counts differ")
    if len(baseline_paths) < minimum_repetitions:
        raise ComparisonError(
            f"at least {minimum_repetitions} repetitions are required"
        )

    checks: list[dict[str, Any]] = []
    for raw_check in checks_config:
        if not isinstance(raw_check, dict):
            raise ComparisonError("architecture regression check must be an object")
        name = str(raw_check.get("name", ""))
        case = str(raw_check.get("case", ""))
        metric = str(raw_check.get("metric", ""))
        direction = str(raw_check.get("direction", ""))
        try:
            max_regression_pct = float(raw_check["max_regression_pct"])
            min_absolute_delta = float(raw_check.get("min_absolute_delta", 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ComparisonError(f"invalid threshold for check {name}") from exc
        if not name or not case or not metric or direction not in {"min", "max"}:
            raise ComparisonError(f"invalid architecture regression check: {raw_check}")
        if max_regression_pct < 0 or min_absolute_delta < 0:
            raise ComparisonError(f"negative threshold for check {name}")

        baseline_values = metric_values(baseline_paths, case, metric)
        candidate_values = metric_values(candidate_paths, case, metric)
        baseline_median = float(statistics.median(baseline_values))
        candidate_median = float(statistics.median(candidate_values))
        if baseline_median <= 0:
            raise ComparisonError(f"baseline median must be positive for check {name}")

        if direction == "max":
            absolute_regression = candidate_median - baseline_median
        else:
            absolute_regression = baseline_median - candidate_median
        regression_pct = absolute_regression / baseline_median * 100.0
        meaningful_delta = absolute_regression > min_absolute_delta
        passed = not (regression_pct > max_regression_pct and meaningful_delta)
        checks.append(
            {
                "name": name,
                "case": case,
                "metric": metric,
                "direction": direction,
                "baseline_values": baseline_values,
                "candidate_values": candidate_values,
                "baseline_median": baseline_median,
                "candidate_median": candidate_median,
                "absolute_regression": absolute_regression,
                "regression_pct": regression_pct,
                "max_regression_pct": max_regression_pct,
                "min_absolute_delta": min_absolute_delta,
                "passed": passed,
            }
        )

    return {
        "summary_version": 1,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "overall_pass": all(check["passed"] for check in checks),
        "baseline_ref": baseline_ref,
        "candidate_ref": candidate_ref,
        "repetitions": len(baseline_paths),
        "baseline_inputs": [
            {"path": str(path), "sha256": sha256_file(path)} for path in baseline_paths
        ],
        "candidate_inputs": [
            {"path": str(path), "sha256": sha256_file(path)} for path in candidate_paths
        ],
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, action="append", required=True)
    parser.add_argument("--candidate", type=Path, action="append", required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/perf/v2_arch_baseline_gates.json"),
    )
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = evaluate_comparison(
            args.baseline,
            args.candidate,
            load_json(args.config),
            baseline_ref=args.baseline_ref,
            candidate_ref=args.candidate_ref,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except ComparisonError as exc:
        print(f"v2 architecture regression comparison: FAIL: {exc}", file=sys.stderr)
        return 2
    print(
        f"v2 architecture regression comparison: "
        f"{'PASS' if summary['overall_pass'] else 'FAIL'}"
    )
    print(f"summary: {args.output}")
    return 0 if summary["overall_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
