#!/usr/bin/env python3
"""Build platform SDK packages from one already-built C ABI library."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SDK_VERSION = "4.2.0"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], cwd: Path) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def native_manifest(native: Path, rid: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "sdk_version": SDK_VERSION,
        "rid": rid,
        "native_file": native.name,
        "native_sha256": sha256(native),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-library", type=Path, required=True)
    parser.add_argument("--rid", required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runtime/sdk-packages")
    parser.add_argument(
        "--wheel-platform-tag",
        help="Explicit audited wheel platform tag (for example manylinux_2_35_x86_64 or macosx_14_0_arm64)",
    )
    parser.add_argument("--skip-python", action="store_true")
    parser.add_argument("--skip-nuget", action="store_true")
    parser.add_argument("--require-nuget", action="store_true")
    args = parser.parse_args()

    native = args.native_library.resolve()
    if not native.is_file():
        parser.error(f"native library does not exist: {native}")
    if args.skip_nuget and args.require_nuget:
        parser.error("--skip-nuget and --require-nuget are mutually exclusive")
    current_platform = (platform.system(), platform.machine().lower())
    expected_platforms = {
        "linux-x64": {("Linux", "x86_64"), ("Linux", "amd64")},
        "osx-arm64": {("Darwin", "arm64"), ("Darwin", "aarch64")},
    }
    if args.rid in expected_platforms and current_platform not in expected_platforms[args.rid]:
        parser.error(f"RID {args.rid} cannot be packaged on native platform {current_platform}")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for old in output.glob("boost_gateway_sdk-4.2.0-*.whl"):
        old.unlink()
    for old in output.glob("BoostGateway.Sdk.4.2.0*.nupkg"):
        old.unlink()

    native_meta = native_manifest(native, args.rid)
    produced: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="boost-sdk-package-") as temp_text:
        temp = Path(temp_text)
        if not args.skip_python:
            python_stage = temp / "python"
            shutil.copytree(ROOT / "sdk/python", python_stage)
            shutil.copy2(native, python_stage / native.name)
            shutil.copy2(ROOT / "LICENSE", python_stage / "LICENSE")
            (python_stage / "_native_manifest.json").write_text(
                json.dumps(native_meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            wheel_command = [sys.executable, "setup.py", "bdist_wheel", "--dist-dir", str(output)]
            if args.wheel_platform_tag:
                wheel_command.extend(["--plat-name", args.wheel_platform_tag])
            run(wheel_command, python_stage)
            produced.extend(sorted(output.glob("boost_gateway_sdk-4.2.0-*.whl")))

        if not args.skip_nuget:
            dotnet = shutil.which("dotnet")
            if dotnet is None:
                if args.require_nuget:
                    raise RuntimeError("dotnet is required to build the NuGet package")
                print("dotnet not found; NuGet package was not built", file=sys.stderr)
            else:
                nuget_manifest = temp / "nuget-native-manifest.json"
                nuget_manifest.write_text(
                    json.dumps(native_meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                run(
                    [
                        dotnet,
                        "pack",
                        str(ROOT / "sdk/csharp/SdkClient.csproj"),
                        "--configuration",
                        "Release",
                        "--output",
                        str(output),
                        f"-p:NativeLibrary={native}",
                        f"-p:NativeManifest={nuget_manifest}",
                        f"-p:RuntimeIdentifier={args.rid}",
                        "-p:GeneratePackageOnBuild=false",
                        "-p:ContinuousIntegrationBuild=true",
                    ],
                    ROOT,
                )
                produced.extend(sorted(output.glob("BoostGateway.Sdk.4.2.0*.nupkg")))

    if not produced:
        raise RuntimeError("no SDK packages were produced")

    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    artifact_records = [
        {"name": path.name, "sha256": sha256(path), "size": path.stat().st_size}
        for path in sorted(set(produced))
    ]
    provenance = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_revision": revision,
        "sdk_version": SDK_VERSION,
        "rid": args.rid,
        "native": native_meta,
        "artifacts": artifact_records,
    }
    provenance_path = output / f"sdk-package-provenance-{args.rid}.json"
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksum_path = output / f"SHA256SUMS-sdk-{args.rid}.txt"
    checksum_paths = [*sorted(set(produced)), provenance_path]
    checksum_path.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in checksum_paths), encoding="utf-8"
    )
    print(json.dumps(provenance, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
