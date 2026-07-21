#!/usr/bin/env python3
"""Run and record a strict offline Conan install for the Raft release graph."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.evidence_provenance import build_evidence_provenance


def load_lock_packages(lockfile: Path) -> set[str]:
    document = json.loads(lockfile.read_text(encoding="utf-8"))
    if document.get("version") != "0.5" or not isinstance(document.get("requires"), list):
        raise ValueError("Conan lockfile must use schema 0.5 with a requires list")
    packages: set[str] = set()
    for reference in document["requires"]:
        if not isinstance(reference, str) or "/" not in reference:
            raise ValueError("Conan lockfile contains an invalid requirement")
        packages.add(reference.split("/", 1)[0])
    return packages


def build_command(args: argparse.Namespace) -> list[str]:
    return [
        args.conan_executable,
        "install",
        ".",
        "--profile:host",
        str(args.profile),
        "--profile:build",
        str(args.profile),
        "--lockfile",
        str(args.lockfile),
        "-o",
        "&:with_grpc=False",
        "-o",
        "&:with_raft_protobuf=True",
        "-o",
        "&:with_sqlite=False",
        f"--output-folder={args.output_folder}",
        "--build=never",
        "--no-remote",
        "-s",
        f"build_type={args.build_type}",
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--lockfile", type=Path, required=True)
    parser.add_argument("--output-folder", type=Path, required=True)
    parser.add_argument("--build-type", choices=("Debug", "Release"), required=True)
    parser.add_argument("--conan-executable", default="conan")
    parser.add_argument("--candidate-revision")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("runtime/validation/raft-conan-offline-summary.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lockfile = args.lockfile if args.lockfile.is_absolute() else ROOT / args.lockfile
    profile = args.profile if args.profile.is_absolute() else ROOT / args.profile
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    command = build_command(args)
    started = time.monotonic()
    failures: list[str] = []
    returncode = -1
    stdout = ""
    stderr = ""
    packages: set[str] = set()

    try:
        packages = load_lock_packages(lockfile)
        missing = sorted({"protobuf", "abseil"} - packages)
        if missing:
            failures.append("lockfile is missing Raft runtime packages: " + ", ".join(missing))
        if "grpc" in packages:
            failures.append("non-gRPC Raft lockfile unexpectedly contains grpc")
        if not profile.is_file():
            failures.append(f"Conan profile does not exist: {profile}")
        if not failures:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=args.timeout_seconds,
                check=False,
            )
            returncode = completed.returncode
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            if returncode != 0:
                failures.append(f"Conan offline install exited with {returncode}")
    except (OSError, json.JSONDecodeError, ValueError, subprocess.TimeoutExpired) as exc:
        failures.append(str(exc))

    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    passed = not failures
    summary: dict[str, Any] = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": passed,
        "passed": passed,
        "failed_category": "conan_offline" if failures else "",
        "failed_step": failures[0] if failures else "",
        "provenance": build_evidence_provenance(
            ROOT,
            build_configuration=args.build_type,
            conan_lockfile=lockfile,
            candidate_revision=args.candidate_revision,
        ),
        "offline_contract": {
            "no_remote": True,
            "build_policy": "never",
            "with_grpc": False,
            "with_raft_protobuf": True,
            "with_sqlite": False,
            "required_packages": ["abseil", "protobuf"],
            "lock_packages": sorted(packages),
        },
        "command": command,
        "returncode": returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "failures": failures,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "artifacts": {
            "summary_path": str(summary_path),
            "output_folder": str(args.output_folder),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Raft Conan offline install: {'PASS' if passed else 'FAIL'}")
    print(f"summary: {summary_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
