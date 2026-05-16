#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate C++ protobuf sources for v3 proto files.")
    parser.add_argument("--proto-dir", default="")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    proto_dir = Path(args.proto_dir).resolve() if args.proto_dir else root / "proto" / "v3"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else root / "src" / "v3" / "proto"

    protoc = shutil.which("protoc")
    if not protoc:
        raise SystemExit("protoc not found in PATH")
    grpc_plugin = shutil.which("grpc_cpp_plugin")

    out_dir.mkdir(parents=True, exist_ok=True)
    proto_files = [
        proto_dir / "common.proto",
        proto_dir / "login.proto",
        proto_dir / "room.proto",
        proto_dir / "battle.proto",
        proto_dir / "match.proto",
        proto_dir / "leaderboard.proto",
    ]

    cmd = [
        protoc,
        f"--proto_path={proto_dir.parent}",
        f"--cpp_out={out_dir}",
        *map(str, proto_files),
    ]
    subprocess.run(cmd, check=True)
    if grpc_plugin:
        grpc_cmd = [
            protoc,
            f"--proto_path={proto_dir.parent}",
            f"--grpc_out={out_dir}",
            f"--plugin=protoc-gen-grpc={grpc_plugin}",
            *map(str, proto_files),
        ]
        subprocess.run(grpc_cmd, check=True)
        print(f"Generated C++ protobuf and gRPC stubs in {out_dir}")
    else:
        print(f"Generated C++ protobuf stubs in {out_dir}; grpc_cpp_plugin not found, skipped gRPC stubs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
