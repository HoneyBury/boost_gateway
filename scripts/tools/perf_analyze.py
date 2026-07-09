#!/usr/bin/env python3
"""
PerfLog Analyzer 鈥?reads PerfCounter CSV logs from build/perf_logs/ and
generates performance reports.

Usage:
    python scripts/perf_analyze.py --input build/perf_logs/ --output-format text --top 10
    python scripts/perf_analyze.py --input build/perf_logs/perf_20260523.csv --output-format json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


# 鈹€鈹€鈹€ Data model 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

class PerfSample:
    """A single row from a PerfCounter CSV log."""

    def __init__(self, row: dict[str, str]) -> None:
        self.timestamp: str = row.get("timestamp", "")
        self.counter_name: str = row.get("counter_name", row.get("name", "unknown"))
        self.count: int = int(row.get("count", 0))
        self.min_us: float = float(row.get("min_us", row.get("min", 0)))
        self.max_us: float = float(row.get("max_us", row.get("max", 0)))
        self.avg_us: float = float(row.get("avg_us", row.get("avg", 0)))
        self.p50_us: float = float(row.get("p50_us", row.get("p50", 0)))
        self.p99_us: float = float(row.get("p99_us", row.get("p99", 0)))


# 鈹€鈹€鈹€ CSV loading 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def load_csv(path: Path) -> list[PerfSample]:
    """Load a single CSV file and return a list of PerfSamples."""
    samples: list[PerfSample] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            samples.append(PerfSample(row))
    return samples


def load_all(path: Path) -> list[PerfSample]:
    """Load all CSV files from a directory (or a single file)."""
    if path.is_file():
        return load_csv(path)

    samples: list[PerfSample] = []
    if path.is_dir():
        for fpath in sorted(path.glob("*.csv")):
            samples.extend(load_csv(fpath))

    return samples


def infer_filename(path: Path) -> str:
    """Return a display-friendly name for the input."""
    if path.is_file():
        return path.name
    nfiles = len(list(path.glob("*.csv")))
    return f"{path.name}/ ({nfiles} files)"


# 鈹€鈹€鈹€ Analysis 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

class PerfReport:
    """Aggregated report over a set of PerfSamples."""

    def __init__(self, samples: list[PerfSample]) -> None:
        self.samples = samples
        self._by_counter: dict[str, list[PerfSample]] = defaultdict(list)
        for s in samples:
            self._by_counter[s.counter_name].append(s)

    def top_n(self, n: int, metric: str = "p99_us") -> list[dict[str, Any]]:
        """Return the top-N counters sorted by the given metric (descending)."""
        aggregated: dict[str, dict[str, float]] = {}
        for name, group in self._by_counter.items():
            vals = [getattr(s, metric) for s in group]
            aggregated[name] = {
                "min": min(vals),
                "max": max(vals),
                "avg": sum(vals) / len(vals),
                "latest": vals[-1] if vals else 0,
                "sample_count": len(group),
            }

        sorted_items = sorted(aggregated.items(),
                              key=lambda kv: kv[1]["avg"],
                              reverse=True)
        result = []
        for name, stats in sorted_items[:n]:
            entry: dict[str, Any] = {
                "counter": name,
                "min_us": round(stats["min"], 1),
                "max_us": round(stats["max"], 1),
                "avg_us": round(stats["avg"], 1),
                f"latest_{metric}": round(stats["latest"], 1),
                "samples": stats["sample_count"],
            }
            result.append(entry)

        return result

    def latency_distribution(self, counter_name: str) -> dict[str, Any]:
        """Return latency histogram buckets for a specific counter."""
        group = self._by_counter.get(counter_name, [])
        if not group:
            return {"counter": counter_name, "buckets": []}

        vals = [s.p99_us for s in group]
        buckets = {
            "under_10us": sum(1 for v in vals if v < 10),
            "10_50us": sum(1 for v in vals if 10 <= v < 50),
            "50_100us": sum(1 for v in vals if 50 <= v < 100),
            "100_500us": sum(1 for v in vals if 100 <= v < 500),
            "500us_1ms": sum(1 for v in vals if 500 <= v < 1000),
            "over_1ms": sum(1 for v in vals if v >= 1000),
        }

        return {
            "counter": counter_name,
            "samples": len(vals),
            "buckets": buckets,
        }

    def trend(self, counter_name: str) -> list[dict[str, Any]]:
        """Return time-ordered trend data for a specific counter."""
        group = sorted(self._by_counter.get(counter_name, []),
                       key=lambda s: s.timestamp)
        return [
            {
                "timestamp": s.timestamp,
                "avg_us": s.avg_us,
                "p50_us": s.p50_us,
                "p99_us": s.p99_us,
                "count": s.count,
            }
            for s in group
        ]


# 鈹€鈹€鈹€ Formatters 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def format_text(report: PerfReport, top_n: int) -> str:
    """Pretty-print the report as plain text."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("  PerfLog Analysis Report")
    lines.append("=" * 72)
    lines.append("")

    top = report.top_n(top_n)
    if not top:
        lines.append("  (no data)")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  Top {len(top)} hot functions by avg latency:")
    lines.append("")
    header = f"  {'Counter':<30} {'Min(us)':>10} {'Avg(us)':>10} {'Max(us)':>10} {'P99(us)':>10} {'Samples':>8}"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for entry in top:
        lines.append(
            f"  {entry['counter']:<30}"
            f" {entry['min_us']:>10.1f}"
            f" {entry['avg_us']:>10.1f}"
            f" {entry['max_us']:>10.1f}"
            f" {entry['latest_p99_us']:>10.1f}"
            f" {entry['samples']:>8}"
        )

    lines.append("")
    lines.append("-" * 72)
    lines.append("")

    # Latency distribution for top functions.
    lines.append("  Latency distribution (p99):")
    lines.append("")
    for entry in top[:5]:
        dist = report.latency_distribution(entry["counter"])
        b = dist["buckets"]
        lines.append(
            f"  {entry['counter']:<30}"
            f" <10us:{b['under_10us']} "
            f" 10-50us:{b['10_50us']} "
            f" 50-100us:{b['50_100us']} "
            f" 100-500us:{b['100_500us']} "
            f" 500us-1ms:{b['500us_1ms']} "
            f" >1ms:{b['over_1ms']}"
        )

    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)


def format_json(report: PerfReport, top_n: int) -> str:
    """Return the report as a JSON string."""
    top = report.top_n(top_n)
    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_samples": len(report.samples),
        "unique_counters": len(report._by_counter),  # noqa: SLF001
        "top_hotspots": top,
    }
    return json.dumps(output, indent=2)


# 鈹€鈹€鈹€ CLI 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PerfLog analyzer 鈥?profile hotspot latency from PerfCounter CSV logs",
    )
    parser.add_argument(
        "--input", "-i",
        type=str,
        required=True,
        help="Path to a CSV file or a directory containing CSV files",
    )
    parser.add_argument(
        "--output-format", "-f",
        type=str,
        default="text",
        choices=["text", "json"],
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--top", "-t",
        type=int,
        default=10,
        help="Number of top hotspots to show (default: 10)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input path not found: {input_path}", file=sys.stderr)
        return 1

    samples = load_all(input_path)
    if not samples:
        print(f"No samples found in: {input_path}", file=sys.stderr)
        return 1

    report = PerfReport(samples)
    source_desc = infer_filename(input_path)

    print(f"Loaded {len(samples)} samples from {source_desc}")

    if args.output_format == "json":
        print(format_json(report, args.top))
    else:
        print(format_text(report, args.top))

    return 0


if __name__ == "__main__":
    sys.exit(main())

