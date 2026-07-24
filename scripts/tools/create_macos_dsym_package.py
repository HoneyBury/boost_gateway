#!/usr/bin/env python3
"""Create a stripped macOS ARM64 runtime and its exact dSYM archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path


MACHO64_MAGIC = b"\xcf\xfa\xed\xfe"


def run(command: list[str]) -> str:
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_macho64(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as stream:
            return stream.read(4) == MACHO64_MAGIC
    except OSError:
        return False


def uuid(path: Path) -> str:
    matches = re.findall(r"UUID: ([0-9A-F-]+) \(arm64\)", run(["dwarfdump", "--uuid", str(path)]))
    if len(matches) != 1:
        raise RuntimeError(f"expected one ARM64 UUID for {path}, found {matches}")
    return matches[0]


def dwarf_file(dsym: Path) -> Path:
    candidates = sorted((dsym / "Contents" / "Resources" / "DWARF").glob("*"))
    if len(candidates) != 1 or not candidates[0].is_file():
        raise RuntimeError(f"dSYM must contain exactly one DWARF file: {dsym}")
    return candidates[0]


def symbol_probe(dsym: Path, binary: Path) -> tuple[str, str, str]:
    for line in run(["nm", "-an", str(binary)]).splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) != 3 or parts[1] not in {"T", "t"}:
            continue
        try:
            if int(parts[0], 16) == 0:
                continue
        except ValueError:
            continue
        try:
            resolved = run(["dwarfdump", "--lookup", f"0x{parts[0]}", str(dsym)])
        except subprocess.CalledProcessError:
            continue
        source = re.search(r"Line info: file '([^']+)', line ([0-9]+)", resolved)
        if "DW_TAG_subprogram" in resolved and source:
            return f"0x{parts[0]}", parts[2], f"{source.group(1)}:{source.group(2)}"
    raise RuntimeError(f"no source-resolvable text symbol found in {dsym}")


def archive_tree(root: Path, archive: Path) -> None:
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as output:
        output.add(root, arcname=root.name, recursive=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument(
        "--standard-runtime-name",
        action="store_true",
        help="Name the stripped runtime like the primary release archive instead of a candidate symbol runtime.",
    )
    parser.add_argument("--project-flags", default="-O2 -g -DNDEBUG")
    args = parser.parse_args()

    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("dSYM packaging requires native macOS ARM64")
    for tool in ("dsymutil", "dwarfdump", "nm", "strip", "file", "codesign"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"required Apple symbol tool not found: {tool}")

    install_root = args.install_root.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime_suffix = "" if args.standard_runtime_name else "-symbol-runtime"
    runtime_root = output / f"boost-gateway-{args.version}-macos-arm64{runtime_suffix}"
    symbols_root = output / f"boost-gateway-{args.version}-macos-arm64-dsym"
    for path in (runtime_root, symbols_root):
        if path.exists():
            shutil.rmtree(path)
    shutil.copytree(install_root, runtime_root, symlinks=False)
    symbols_root.mkdir(parents=True)

    records: list[dict[str, object]] = []
    for runtime in sorted(path for path in runtime_root.rglob("*") if is_macho64(path)):
        relative = runtime.relative_to(runtime_root)
        identity = run(["file", str(runtime)]).strip()
        if "Mach-O 64-bit" not in identity or "arm64" not in identity or "x86_64" in identity:
            raise RuntimeError(f"non-native Mach-O artifact: {identity}")
        dsym_relative = Path(str(relative) + ".dSYM")
        dsym = symbols_root / dsym_relative
        dsym.parent.mkdir(parents=True, exist_ok=True)
        run(["dsymutil", str(runtime), "-o", str(dsym)])
        dsym_dwarf = dwarf_file(dsym)
        original_uuid = uuid(runtime)
        if uuid(dsym) != original_uuid:
            raise RuntimeError(f"dSYM UUID does not match runtime: {relative}")
        debug_info = run(["dwarfdump", "--debug-info", str(dsym)])
        if "DW_TAG_compile_unit" not in debug_info:
            raise RuntimeError(f"dSYM contains no compile unit: {relative}")
        address, symbol, source = symbol_probe(dsym, runtime)
        run(["strip", "-S", str(runtime)])
        run(["codesign", "--force", "--sign", "-", str(runtime)])
        if uuid(runtime) != original_uuid:
            raise RuntimeError(f"Mach-O UUID changed while stripping {relative}")
        run(["codesign", "--verify", "--verbose=2", str(runtime)])
        records.append({
            "runtime_path": relative.as_posix(),
            "dsym_path": dsym_relative.as_posix(),
            "dsym_dwarf_path": dsym_dwarf.relative_to(symbols_root).as_posix(),
            "uuid": original_uuid,
            "architecture": "arm64",
            "runtime_sha256": sha256(runtime),
            "dsym_dwarf_sha256": sha256(dsym_dwarf),
            "probe_address": address,
            "probe_symbol": symbol,
            "probe_source": source,
            "code_signature": "ad-hoc-verified",
        })
    if not records:
        raise RuntimeError(f"install tree contains no Mach-O files: {install_root}")

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "candidate_revision": args.candidate_revision,
        "version": args.version,
        "platform": "macos-arm64",
        "build_contract": {
            "cmake_configuration": "Release",
            "project_flags": args.project_flags,
            "conan_dependency_configuration": "Release",
            "notarized": False,
        },
        "files": records,
    }
    manifest_path = symbols_root / "dsym-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    runtime_archive = output / f"{runtime_root.name}.tar.gz"
    symbols_archive = output / f"{symbols_root.name}.tar.gz"
    archive_tree(runtime_root, runtime_archive)
    archive_tree(symbols_root, symbols_archive)
    summary = {
        "summary_version": 1,
        "passed": True,
        "overall_pass": True,
        "candidate_revision": args.candidate_revision,
        "macho_count": len(records),
        "runtime_archive": runtime_archive.name,
        "runtime_archive_sha256": sha256(runtime_archive),
        "symbols_archive": symbols_archive.name,
        "symbols_archive_sha256": sha256(symbols_archive),
        "manifest_sha256": sha256(manifest_path),
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not args.standard_runtime_name:
        shutil.rmtree(runtime_root)
        shutil.rmtree(symbols_root)
    print(f"macOS dSYM package: PASS ({len(records)} Mach-O files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
