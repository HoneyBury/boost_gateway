#!/usr/bin/env python3
"""Combined v3 proto/gRPC governance gate.

Runs both the proto schema validation and the gRPC PoC decision check
in sequence. Use --schema-only or --grpc-only to run just one.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="v3 proto governance gate")
    parser.add_argument("--schema-only", action="store_true",
                        help="Only run proto schema validation")
    parser.add_argument("--grpc-only", action="store_true",
                        help="Only run gRPC PoC decision check")
    parser.add_argument("--build-dir", default="build/default")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    gates_dir = script_dir

    checks = []
    if not args.grpc_only:
        checks.append(("Proto schema validation",
                       gates_dir / "check_v3_proto_schema.py"))
    if not args.schema_only:
        checks.append(("gRPC PoC decision",
                       gates_dir / "check_v3_grpc_poc_decision.py"))

    overall_pass = True
    for label, script in checks:
        print(f"\n{'=' * 60}")
        print(f"  {label}")
        print(f"{'=' * 60}\n")
        cmd = [sys.executable, str(script)]
        if args.allow_missing:
            cmd.append("--allow-missing")
        if args.build_dir:
            cmd.extend(["--build-dir", args.build_dir])
        result = subprocess.run(cmd)
        if result.returncode != 0:
            overall_pass = False
            print(f"\n❌ {label}: FAILED")
        else:
            print(f"\n✅ {label}: PASSED")

    print(f"\n{'=' * 60}")
    if overall_pass:
        print("  v3 proto governance: PASS")
    else:
        print("  v3 proto governance: FAIL")
    print(f"{'=' * 60}\n")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
