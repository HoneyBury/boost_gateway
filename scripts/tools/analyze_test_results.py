#!/usr/bin/env python3
"""Analyze CTest / GTest results and produce a Markdown summary report.

Parses either:
- CTest XML output (build/Testing/*/Test.xml)
- GTest JSON output (from --gtest_output=json)
- LastTest.log (build/Testing/Temporary/LastTest.log)

Produces a Markdown report with:
- Pass/fail/skip counts per test suite
- Duration breakdown
- Platform-grouped statistics
- Delta from previous run (new failures, fixed tests)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_ctest_xml(path: Path) -> dict[str, Any]:
    """Parse CTest XML output (Testing/*/Test.xml)."""
    tree = ET.parse(path)
    root = tree.getroot()

    results: list[dict[str, Any]] = []
    for test in root.findall(".//Test"):
        name = test.get("Name", "unknown")
        status = test.find("Results").get("status", "unknown") if test.find("Results") is not None else "unknown"
        duration_s = 0.0
        if test.find("Results/NamedMeasurement") is not None:
            for measurement in test.findall("Results/NamedMeasurement"):
                if measurement.get("name") == "Execution Time":
                    duration_s = float(measurement.find("Value").text or "0")
        results.append({
            "name": name,
            "status": "passed" if status == "passed" else "failed",
            "duration_s": duration_s,
        })

    return {
        "source": "CTest XML",
        "path": str(path),
        "tests": results,
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "passed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "skipped": 0,
    }


def parse_gtest_json(path: Path) -> dict[str, Any]:
    """Parse GTest JSON output (--gtest_output=json)."""
    data = json.loads(path.read_text(encoding="utf-8"))

    results: list[dict[str, Any]] = []
    for suite in data.get("testsuites", []):
        suite_name = suite.get("name", "unknown")
        for test in suite.get("tests", []):
            test_name = test.get("name", "unknown")
            status = test.get("status", "RUN")
            duration_s = test.get("time", 0)
            if isinstance(duration_s, str):
                duration_s = float(duration_s.rstrip("s"))
            results.append({
                "name": f"{suite_name}.{test_name}",
                "suite": suite_name,
                "status": status.lower(),
                "duration_s": duration_s,
            })

    return {
        "source": "GTest JSON",
        "path": str(path),
        "tests": results,
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "passed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
    }


def parse_last_test_log(path: Path) -> dict[str, Any]:
    """Parse LastTest.log format.

    Lines look like:
        test 1
            Start 1: test_name
        1/1 Test #1: test_name ...............   Passed    0.12 sec
    """
    text = path.read_text(encoding="utf-8", errors="replace")

    results: list[dict[str, Any]] = []
    pattern = re.compile(
        r"Test\s+#(\d+):\s+(.+?)\s+\.+\s+(Passed|Failed|SKIPPED|NotRun)\s+(\d+\.\d+)\s*sec"
    )

    for match in pattern.finditer(text):
        test_num = int(match.group(1))
        name = match.group(2).strip()
        status_str = match.group(3).strip().lower()
        duration_s = float(match.group(4))

        if status_str == "skipped" or status_str == "notrun":
            status = "skipped"
        elif status_str == "passed":
            status = "passed"
        else:
            status = "failed"

        results.append({
            "name": name,
            "test_num": test_num,
            "status": status,
            "duration_s": duration_s,
        })

    return {
        "source": "LastTest.log",
        "path": str(path),
        "tests": results,
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "passed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
    }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def group_by_suite(tests: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group tests by suite name (first segment before '.') or 'unknown'."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for test in tests:
        name = test.get("name", "")
        suite = test.get("suite", "")
        if not suite and "." in name:
            suite = name.split(".")[0]
        elif not suite:
            suite = "unknown"
        groups[suite].append(test)
    return dict(groups)


def compute_suite_stats(suite_name: str, tests: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute pass/fail/skip/duration stats for a test suite."""
    durations = [t["duration_s"] for t in tests if t["status"] == "passed"]
    total_duration = sum(t["duration_s"] for t in tests)
    return {
        "suite": suite_name,
        "total": len(tests),
        "passed": sum(1 for t in tests if t["status"] == "passed"),
        "failed": sum(1 for t in tests if t["status"] == "failed"),
        "skipped": sum(1 for t in tests if t["status"] == "skipped"),
        "total_duration_s": round(total_duration, 3),
        "avg_passed_duration_s": round(sum(durations) / len(durations), 3) if durations else 0.0,
        "max_duration_s": round(max(t["duration_s"] for t in tests), 3) if tests else 0.0,
    }


def list_failures(tests: list[dict[str, Any]]) -> list[str]:
    """Return names of failed tests."""
    return [t["name"] for t in tests if t["status"] == "failed"]


def list_skipped(tests: list[dict[str, Any]]) -> list[str]:
    """Return names of skipped tests."""
    return [t["name"] for t in tests if t["status"] == "skipped"]


# ---------------------------------------------------------------------------
# Delta comparison
# ---------------------------------------------------------------------------

def load_previous_results(cache_path: Path) -> dict[str, Any] | None:
    """Load previous run results from a JSON cache file."""
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_current_results(cache_path: Path, results: dict[str, Any]) -> None:
    """Save current run results to a JSON cache file."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "total": results["total"],
        "passed": results["passed"],
        "failed": results["failed"],
        "skipped": results["skipped"],
        "failures": list_failures(results["tests"]),
        "skipped_tests": list_skipped(results["tests"]),
    }
    cache_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def compute_delta(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> dict[str, Any]:
    """Compare current results against previous run."""
    if previous is None:
        return {
            "new_failures": [],
            "fixed_tests": [],
            "total_delta": 0,
            "passed_delta": 0,
            "failed_delta": 0,
            "skipped_delta": 0,
        }

    current_failures = set(list_failures(current["tests"]))
    previous_failures = set(previous.get("failures", []))

    return {
        "new_failures": sorted(current_failures - previous_failures),
        "fixed_tests": sorted(previous_failures - current_failures),
        "total_delta": current["total"] - previous.get("total", 0),
        "passed_delta": current["passed"] - previous.get("passed", 0),
        "failed_delta": current["failed"] - previous.get("failed", 0),
        "skipped_delta": current["skipped"] - previous.get("skipped", 0),
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_markdown(
    results: dict[str, Any],
    delta: dict[str, Any] | None = None,
    platform: str = "unknown",
    title: str = "Test Results",
) -> str:
    """Render test results as a Markdown report."""
    lines: list[str] = [
        f"# {title}",
        "",
        f"- **Platform**: {platform}",
        f"- **Source**: {results.get('source', 'unknown')}",
        f"- **File**: `{results.get('path', 'unknown')}`",
        f"- **Total tests**: {results['total']}",
        f"- **Passed**: {results['passed']}",
        f"- **Failed**: {results['failed']}",
        f"- **Skipped**: {results['skipped']}",
        "",
    ]

    if delta:
        lines.append("### Delta from Previous Run")
        lines.append("")
        if delta["new_failures"]:
            lines.append(f"- **New failures ({len(delta['new_failures'])}):**")
            for name in delta["new_failures"]:
                lines.append(f"  - `{name}`")
        if delta["fixed_tests"]:
            lines.append(f"- **Fixed tests ({len(delta['fixed_tests'])}):**")
            for name in delta["fixed_tests"]:
                lines.append(f"  - `{name}`")
        lines.append(f"- Total: {delta['total_delta']:+d}")
        lines.append(f"- Passed: {delta['passed_delta']:+d}")
        lines.append(f"- Failed: {delta['failed_delta']:+d}")
        lines.append(f"- Skipped: {delta['skipped_delta']:+d}")
        lines.append("")

    # Summary table
    lines.append("### Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | ---:|")
    lines.append(f"| Total | {results['total']} |")
    lines.append(f"| Passed | {results['passed']} |")
    lines.append(f"| Failed | {results['failed']} |")
    lines.append(f"| Skipped | {results['skipped']} |")
    pass_rate = (
        round(results["passed"] / (results["total"] - results["skipped"]) * 100, 1)
        if results["total"] > results["skipped"]
        else 0.0
    )
    lines.append(f"| Pass rate | {pass_rate}% |")
    lines.append("")

    # Suite breakdown
    suites = group_by_suite(results["tests"])
    lines.append("### Suite Breakdown")
    lines.append("")
    lines.append("| Suite | Total | Passed | Failed | Skipped | Duration (s) | Avg time (s) |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for suite_name in sorted(suites.keys()):
        stats = compute_suite_stats(suite_name, suites[suite_name])
        lines.append(
            f"| `{suite_name}` | {stats['total']} | {stats['passed']} | "
            f"{stats['failed']} | {stats['skipped']} | "
            f"{stats['total_duration_s']} | {stats['avg_passed_duration_s']} |"
        )
    lines.append("")

    # Slowest tests
    sorted_by_duration = sorted(
        (t for t in results["tests"] if t["status"] == "passed"),
        key=lambda t: t["duration_s"],
        reverse=True,
    )
    if sorted_by_duration:
        lines.append("### Slowest Tests (Top 10)")
        lines.append("")
        lines.append("| Test | Duration (s) |")
        lines.append("| --- | ---:|")
        for test in sorted_by_duration[:10]:
            lines.append(f"| `{test['name']}` | {test['duration_s']} |")
        lines.append("")

    # Failures
    failures = list_failures(results["tests"])
    if failures:
        lines.append("### Failures")
        lines.append("")
        lines.append(f"**{len(failures)} failed test(s):**")
        lines.append("")
        for name in failures:
            lines.append(f"- `{name}`")
        lines.append("")

    # Skipped
    skipped = list_skipped(results["tests"])
    if skipped:
        lines.append("### Skipped Tests")
        lines.append("")
        lines.append(f"**{len(skipped)} skipped test(s):**")
        lines.append("")
        for name in skipped:
            lines.append(f"- `{name}`")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_test_results(build_dir: Path) -> list[Path]:
    """Find test result files in the build directory.

    Searches for:
    - Testing/*/Test.xml (CTest XML output)
    - test_detail.xml (GTest XML output)
    - Testing/Temporary/LastTest.log
    """
    results: list[Path] = []
    testing_dir = build_dir / "Testing"

    # CTest XML
    if testing_dir.exists():
        for item in testing_dir.iterdir():
            test_xml = item / "Test.xml"
            if test_xml.exists():
                results.append(test_xml)

        last_log = testing_dir / "Temporary" / "LastTest.log"
        if last_log.exists():
            results.append(last_log)

    # GTest JSON (search broadly)
    for pattern in ("**/test_detail.xml", "**/*_test_results.json"):
        for match in sorted(build_dir.rglob(pattern)):
            results.append(match)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze CTest/GTest results and produce Markdown summary."
    )
    parser.add_argument(
        "--build-dir",
        default="build",
        help="Build directory containing test results (default: build)",
    )
    parser.add_argument(
        "--input",
        help="Direct path to a test result file (overrides --build-dir search)",
    )
    parser.add_argument(
        "--cache-dir",
        default="runtime/validation",
        help="Directory for caching previous results for delta comparison",
    )
    parser.add_argument(
        "--platform",
        default="",
        help="Platform label for the report (e.g., ubuntu-latest, windows-2022)",
    )
    parser.add_argument(
        "--title",
        default="Test Results Analysis",
        help="Report title (default: Test Results Analysis)",
    )
    parser.add_argument(
        "--output",
        help="Write Markdown report to this file (default: stdout)",
    )
    parser.add_argument(
        "--no-delta",
        action="store_true",
        help="Skip delta comparison with previous run",
    )
    args = parser.parse_args()

    build_dir = Path(args.build_dir).resolve()

    # Locate input file
    if args.input:
        input_paths = [Path(args.input)]
    else:
        input_paths = find_test_results(build_dir)

    if not input_paths:
        print(
            f"No test result files found in {build_dir}. "
            "Use --input to specify a file.",
            file=sys.stderr,
        )
        return 1

    # Parse all found results
    parsed: dict[str, Any] | None = None
    for path in input_paths:
        if path.name == "Test.xml":
            parsed = parse_ctest_xml(path)
        elif path.name == "LastTest.log" and parsed is None:
            parsed = parse_last_test_log(path)
        elif path.suffix == ".json":
            parsed = parse_gtest_json(path)
        if parsed is not None:
            break

    if parsed is None:
        print(f"Could not parse any test result files from: {input_paths}", file=sys.stderr)
        return 1

    # Delta comparison
    platform = args.platform or build_dir.name
    cache_file = Path(args.cache_dir) / f"test-results-{platform}.json"
    previous = None if args.no_delta else load_previous_results(cache_file)
    delta = compute_delta(parsed, previous) if not args.no_delta else None

    if not args.no_delta:
        save_current_results(cache_file, parsed)

    # Render report
    report = render_markdown(parsed, delta, platform=platform, title=args.title)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"Report written to {output_path}", file=sys.stderr)
    else:
        print(report)

    # Exit code: non-zero if there are failures
    if parsed["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

