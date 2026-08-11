"""Small, dependency-free statistics helpers for performance evidence."""

from __future__ import annotations

import math
import statistics


def latency_percentile(values: list[float], percentile: float) -> float | None:
    """Return the nearest-rank percentile used by the performance collectors."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def metric_distribution(values: list[float]) -> dict[str, float | None]:
    """Summarize a metric sample without hiding an empty input."""
    if not values:
        return {"min": None, "median": None, "max": None}
    return {
        "min": round(min(values), 3),
        "median": round(statistics.median(values), 3),
        "max": round(max(values), 3),
    }


def distribution(values: list[float]) -> dict[str, float | None]:
    """Compatibility name used by the OpenTelemetry aggregation path."""
    return metric_distribution(values)


def linear_slope(values: list[float]) -> float:
    """Return the least-squares slope for evenly spaced samples."""
    if len(values) < 2:
        return 0.0
    mean_x = (len(values) - 1) / 2.0
    mean_y = statistics.mean(values)
    denominator = sum((index - mean_x) ** 2 for index in range(len(values)))
    if denominator == 0:
        return 0.0
    numerator = sum(
        (index - mean_x) * (value - mean_y)
        for index, value in enumerate(values)
    )
    return numerator / denominator
