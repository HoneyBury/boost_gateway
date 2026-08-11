#!/usr/bin/env python3
"""Small developer task facade; business logic remains in canonical scripts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD_DIR = Path("build/contributor-debug")
MINIMUM_PYTHON = (3, 12)

CHECK_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("scripts/gates/governance/check_current_docs_install.py",),
    ("scripts/gates/governance/check_script_inventory.py",),
    ("scripts/gates/governance/check_workflow_python_cli_contracts.py",),
    ("scripts/gates/governance/check_workflow_catalog.py",),
    ("scripts/gates/governance/check_repository_governance.py",),
    ("scripts/check_mainline_readiness.py",),
    ("scripts/manage_todos.py", "check"),
)

PYTHON_TEST_COMMAND: tuple[str, ...] = (
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests/python",
    "-p",
    "test_*.py",
)


@dataclass(frozen=True)
class Diagnostic:
    name: str
    passed: bool
    detail: str


def cache_value(build_dir: Path, key: str) -> str | None:
    cache = build_dir / "CMakeCache.txt"
    try:
        lines = cache.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    prefix = f"{key}:INTERNAL="
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


def command_version(command: str) -> str:
    try:
        result = subprocess.run(
            [command, "--version"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return str(exc)
    output = result.stdout.strip() or result.stderr.strip()
    return output.splitlines()[0] if output else f"exit={result.returncode}"


def doctor_diagnostics(build_dir: Path) -> list[Diagnostic]:
    diagnostics = [
        Diagnostic(
            "python",
            sys.version_info >= MINIMUM_PYTHON,
            f"{platform.python_version()} ({sys.executable}); required >= 3.12",
        )
    ]
    required_commands = ["git", "cmake", "ninja"]
    if sys.platform.startswith("linux"):
        required_commands.extend(["gcc-13", "g++-13"])
    for command in required_commands:
        resolved = shutil.which(command)
        diagnostics.append(
            Diagnostic(
                command,
                resolved is not None,
                command_version(resolved) if resolved else "not found on PATH",
            )
        )

    if not build_dir.is_absolute():
        build_dir = ROOT / build_dir
    if not build_dir.exists():
        diagnostics.append(
            Diagnostic(
                "build-tree",
                True,
                f"{build_dir} not configured yet (expected on a fresh clone)",
            )
        )
        return diagnostics

    cmake = cache_value(build_dir, "CMAKE_COMMAND")
    ctest = cache_value(build_dir, "CMAKE_CTEST_COMMAND")
    diagnostics.append(
        Diagnostic(
            "build-tree",
            bool(cmake and ctest and Path(cmake).is_file() and Path(ctest).is_file()),
            f"cmake={cmake or 'unknown'}; ctest={ctest or 'unknown'}",
        )
    )
    return diagnostics


def run_doctor(build_dir: Path) -> int:
    diagnostics = doctor_diagnostics(build_dir)
    for item in diagnostics:
        print(f"[{'PASS' if item.passed else 'FAIL'}] {item.name}: {item.detail}")
    return 0 if all(item.passed for item in diagnostics) else 1


def run_commands(commands: Sequence[Sequence[str]]) -> int:
    failures = 0
    for arguments in commands:
        command = [sys.executable, *arguments]
        print(f"+ {' '.join(command)}", flush=True)
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            failures += 1
    return 0 if failures == 0 else 1


def run_check() -> int:
    if sys.version_info < MINIMUM_PYTHON:
        print(
            f"Python 3.12+ is required; current interpreter is {platform.python_version()} "
            f"({sys.executable}). Try: python3.12 scripts/dev.py check",
            file=sys.stderr,
        )
        return 2
    commands: list[tuple[str, ...]] = list(CHECK_COMMANDS)
    commands.append(PYTHON_TEST_COMMAND)
    return run_commands(commands)


def configured_cmake(build_dir: Path) -> str:
    if not build_dir.is_absolute():
        build_dir = ROOT / build_dir
    configured = cache_value(build_dir, "CMAKE_COMMAND")
    return configured if configured and Path(configured).is_file() else "cmake"


def run_smoke(build_dir: Path, parallel: int | None) -> int:
    absolute_build_dir = build_dir if build_dir.is_absolute() else ROOT / build_dir
    if not (absolute_build_dir / "CMakeCache.txt").is_file():
        print(f"Build tree is not configured: {absolute_build_dir}", file=sys.stderr)
        return 2

    build_command = [
        configured_cmake(build_dir),
        "--build",
        str(absolute_build_dir),
        "--target",
        "project_v2_unit_tests",
        "v2_gateway_demo",
        "--parallel",
    ]
    if parallel is not None:
        build_command.append(str(parallel))
    print(f"+ {' '.join(build_command)}", flush=True)
    if subprocess.run(build_command, cwd=ROOT, check=False).returncode:
        return 1

    test_command = [
        sys.executable,
        "scripts/run_tests.py",
        "unit",
        "--build-dir",
        str(absolute_build_dir),
        "--verbose",
    ]
    if parallel is not None:
        test_command.extend(["--parallel", str(parallel)])
    print(f"+ {' '.join(test_command)}", flush=True)
    if subprocess.run(test_command, cwd=ROOT, check=False).returncode:
        return 1

    demo = absolute_build_dir / "examples/v2_gateway_demo/v2_gateway_demo"
    print(f"+ {demo} --script", flush=True)
    return subprocess.run([str(demo), "--script"], cwd=ROOT, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check the local contributor toolchain")
    doctor.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)

    subparsers.add_parser("check", help="run bounded repository governance checks")

    test = subparsers.add_parser("test", help="delegate to the unified CTest runner")
    test.add_argument("test_args", nargs=argparse.REMAINDER)

    smoke = subparsers.add_parser("smoke", help="build, run unit tests, and execute the demo smoke")
    smoke.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    smoke.add_argument("--parallel", type=int, default=4)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return run_doctor(args.build_dir)
    if args.command == "check":
        return run_check()
    if args.command == "test":
        return subprocess.run(
            [sys.executable, "scripts/run_tests.py", *args.test_args],
            cwd=ROOT,
            check=False,
        ).returncode
    if args.command == "smoke":
        return run_smoke(args.build_dir, args.parallel)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
