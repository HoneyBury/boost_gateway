#!/usr/bin/env python3
"""Build one immutable NuGet package containing every supported native RID."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SDK_VERSION = "4.2.0"
RID_PROPERTIES = {
    "linux-x64": ("NativeLibraryLinuxX64", "NativeManifestLinuxX64", b"\x7fELF"),
    "linux-arm64": ("NativeLibraryLinuxArm64", "NativeManifestLinuxArm64", b"\x7fELF"),
    "osx-arm64": ("NativeLibraryOsxArm64", "NativeManifestOsxArm64", b"\xcf\xfa\xed\xfe"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_native(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        rid, separator, path_text = value.partition("=")
        if not separator or rid not in RID_PROPERTIES:
            raise ValueError(f"invalid --native value: {value!r}")
        if rid in result:
            raise ValueError(f"duplicate native RID: {rid}")
        path = Path(path_text).resolve()
        if not path.is_file():
            raise ValueError(f"native library does not exist: {path}")
        expected_magic = RID_PROPERTIES[rid][2]
        with path.open("rb") as stream:
            actual_magic = stream.read(4)
        if actual_magic != expected_magic:
            raise ValueError(f"native library format does not match {rid}: {path}")
        result[rid] = path
    missing = sorted(set(RID_PROPERTIES) - set(result))
    if missing:
        raise ValueError(f"missing native RIDs: {', '.join(missing)}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="append", default=[], metavar="RID=PATH")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-revision", required=True)
    args = parser.parse_args()

    try:
        native = parse_native(args.native)
    except ValueError as exc:
        parser.error(str(exc))
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        raise RuntimeError("dotnet is required to build the multi-RID NuGet package")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for old in output.glob(f"BoostGateway.Sdk.{SDK_VERSION}*.nupkg"):
        old.unlink()

    records: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="boost-sdk-multi-rid-") as temp_text:
        temp = Path(temp_text)
        properties: list[str] = []
        for rid, path in sorted(native.items()):
            library_property, manifest_property, _ = RID_PROPERTIES[rid]
            manifest = temp / f"native-manifest-{rid}.json"
            record = {
                "rid": rid,
                "native_file": path.name,
                "native_sha256": sha256(path),
                "sdk_version": SDK_VERSION,
            }
            manifest.write_text(
                json.dumps({"schema_version": 1, **record}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            records.append(record)
            properties.extend([f"-p:{library_property}={path}", f"-p:{manifest_property}={manifest}"])

        subprocess.run(
            [
                dotnet,
                "pack",
                str(ROOT / "sdk/csharp/SdkClient.csproj"),
                "--configuration",
                "Release",
                "--output",
                str(output),
                "-p:GeneratePackageOnBuild=false",
                "-p:ContinuousIntegrationBuild=true",
                *properties,
            ],
            cwd=ROOT,
            check=True,
        )

    package = output / f"BoostGateway.Sdk.{SDK_VERSION}.nupkg"
    if not package.is_file():
        raise RuntimeError(f"NuGet output is missing: {package}")
    provenance = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_revision": args.candidate_revision,
        "sdk_version": SDK_VERSION,
        "rids": records,
        "artifact": {
            "name": package.name,
            "sha256": sha256(package),
            "size": package.stat().st_size,
        },
    }
    provenance_path = output / "sdk-package-provenance-multi-rid.json"
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "SHA256SUMS-sdk-multi-rid.txt").write_text(
        f"{sha256(package)}  {package.name}\n{sha256(provenance_path)}  {provenance_path.name}\n",
        encoding="utf-8",
    )
    print(json.dumps(provenance, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
