#!/usr/bin/env python3
"""Verify SDK wheel/NuGet contents and clean consumer installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

SDK_VERSION = "4.2.0"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def add(checks: list[dict[str, object]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def safe_members(names: list[str]) -> bool:
    return all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts for name in names)


def validate_native_manifest(
    archive: zipfile.ZipFile, manifest_name: str, native_name: str, rid: str, checks: list[dict[str, object]], prefix: str
) -> None:
    try:
        manifest = json.loads(archive.read(manifest_name))
        native_data = archive.read(native_name)
    except (KeyError, json.JSONDecodeError) as exc:
        add(checks, f"{prefix}:manifest-readable", False, str(exc))
        return
    add(checks, f"{prefix}:manifest-version", manifest.get("sdk_version") == SDK_VERSION, str(manifest))
    add(checks, f"{prefix}:manifest-rid", manifest.get("rid") == rid, str(manifest))
    add(checks, f"{prefix}:manifest-native-name", manifest.get("native_file") == PurePosixPath(native_name).name, str(manifest))
    add(checks, f"{prefix}:manifest-native-hash", manifest.get("native_sha256") == sha256_bytes(native_data), str(manifest))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--nuget", type=Path)
    parser.add_argument("--rid", required=True)
    parser.add_argument("--require-nuget", action="store_true")
    parser.add_argument("--require-auditwheel", action="store_true")
    parser.add_argument("--summary-path", type=Path, required=True)
    args = parser.parse_args()
    checks: list[dict[str, object]] = []

    wheel = args.wheel.resolve()
    add(checks, "wheel:exists", wheel.is_file(), str(wheel))
    if wheel.is_file():
        add(checks, "wheel:platform-tag", not wheel.name.endswith("-any.whl"), wheel.name)
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            native_names = [name for name in names if "/libboost_gateway_sdk." in name or name.endswith("/boost_gateway_sdk.dll")]
            manifest_names = [name for name in names if name.endswith("/_native_manifest.json")]
            add(checks, "wheel:safe-paths", safe_members(names), f"members={len(names)}")
            add(checks, "wheel:one-native", len(native_names) == 1, str(native_names))
            add(checks, "wheel:one-manifest", len(manifest_names) == 1, str(manifest_names))
            add(
                checks,
                "wheel:license",
                any("dist-info/" in name and PurePosixPath(name).name == "LICENSE" for name in names),
                "license included",
            )
            if len(native_names) == 1 and len(manifest_names) == 1:
                validate_native_manifest(archive, manifest_names[0], native_names[0], args.rid, checks, "wheel")
        if args.require_auditwheel:
            auditwheel = shutil.which("auditwheel")
            add(checks, "wheel:auditwheel-available", auditwheel is not None, str(auditwheel or "missing"))
            if auditwheel:
                audit = subprocess.run([auditwheel, "show", str(wheel)], text=True, capture_output=True)
                policy_versions = [int(value) for value in re.findall(r"manylinux_2_(\d+)_x86_64", audit.stdout)]
                compatible = audit.returncode == 0 and bool(policy_versions) and min(policy_versions) <= 35
                add(checks, "wheel:manylinux-policy", compatible, audit.stdout + audit.stderr)

        with tempfile.TemporaryDirectory(prefix="boost-sdk-wheel-consumer-") as temp_text:
            venv = Path(temp_text) / "venv"
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], check=True)
            env = os.environ.copy()
            env.pop("BOOST_GATEWAY_SDK_LIBRARY", None)
            probe = subprocess.run(
                [str(python), "-c", "import boost_gateway_sdk as s; print(s.version()); print(s.native_library_path())"],
                env=env,
                text=True,
                capture_output=True,
            )
            add(checks, "wheel:clean-import", probe.returncode == 0 and SDK_VERSION in probe.stdout, probe.stdout + probe.stderr)
            add(checks, "wheel:package-local-native", str(venv) in probe.stdout, probe.stdout)
            uninstall = subprocess.run(
                [str(python), "-m", "pip", "uninstall", "-y", "boost-gateway-sdk"], text=True, capture_output=True
            )
            absent = subprocess.run(
                [str(python), "-c", "import importlib.util; raise SystemExit(importlib.util.find_spec('boost_gateway_sdk') is not None)"],
                text=True,
                capture_output=True,
            )
            add(checks, "wheel:clean-uninstall", uninstall.returncode == 0 and absent.returncode == 0, uninstall.stdout + absent.stderr)

    nuget = args.nuget.resolve() if args.nuget else None
    if args.require_nuget:
        add(checks, "nuget:required", bool(nuget and nuget.is_file()), str(nuget or "missing"))
    if nuget and nuget.is_file():
        with zipfile.ZipFile(nuget) as archive:
            names = archive.namelist()
            native_prefix = f"runtimes/{args.rid}/native/"
            native_names = [name for name in names if name.startswith(native_prefix) and not name.endswith("_native_manifest.json")]
            manifest_name = native_prefix + "_native_manifest.json"
            add(checks, "nuget:safe-paths", safe_members(names), f"members={len(names)}")
            add(checks, "nuget:managed-dll", "lib/net8.0/BoostGateway.Sdk.dll" in names, str(names))
            add(checks, "nuget:one-native", len(native_names) == 1, str(native_names))
            add(checks, "nuget:license", any(PurePosixPath(name).name == "LICENSE" for name in names), "license included")
            if len(native_names) == 1 and manifest_name in names:
                validate_native_manifest(archive, manifest_name, native_names[0], args.rid, checks, "nuget")
            else:
                add(checks, "nuget:manifest-readable", False, f"manifest={manifest_name} native={native_names}")
        dotnet = shutil.which("dotnet")
        if dotnet:
            with tempfile.TemporaryDirectory(prefix="boost-sdk-nuget-consumer-") as temp_text:
                project = Path(temp_text)
                (project / "NuGet.Config").write_text(
                    """<?xml version="1.0" encoding="utf-8"?>
<configuration><packageSources><clear/><add key="candidate" value="SOURCE"/></packageSources></configuration>
""".replace("SOURCE", str(nuget.parent)),
                    encoding="utf-8",
                )
                (project / "Consumer.csproj").write_text(
                    f"""<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework><RuntimeIdentifier>{args.rid}</RuntimeIdentifier></PropertyGroup><ItemGroup><PackageReference Include="BoostGateway.Sdk" Version="{SDK_VERSION}" /></ItemGroup></Project>""",
                    encoding="utf-8",
                )
                (project / "Program.cs").write_text(
                    "using System; using BoostGateway.Sdk; Console.WriteLine(SdkClient.Version); if (SdkClient.Version != SdkClient.ExpectedVersion) return 1; return 0;\n",
                    encoding="utf-8",
                )
                restore = subprocess.run(
                    [dotnet, "restore", "--configfile", str(project / "NuGet.Config")],
                    cwd=project,
                    text=True,
                    capture_output=True,
                )
                consume = subprocess.run(
                    [dotnet, "run", "--configuration", "Release", "--no-restore"],
                    cwd=project,
                    text=True,
                    capture_output=True,
                ) if restore.returncode == 0 else restore
                add(checks, "nuget:clean-consumer", consume.returncode == 0 and SDK_VERSION in consume.stdout, restore.stdout + restore.stderr + consume.stdout + consume.stderr)
        elif args.require_nuget:
            add(checks, "nuget:dotnet-available", False, "dotnet executable not found")

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "sdk_distribution_packages" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"SDK package verification: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
