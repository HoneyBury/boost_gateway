#!/usr/bin/env python3
"""Create a stripped Linux runtime and its exact ELF debug-symbol archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path


def run(command: list[str], *, text: bool = True) -> str:
    result = subprocess.run(command, check=True, text=text, capture_output=True)
    return result.stdout if text else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_elf(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as stream:
            return stream.read(4) == b"\x7fELF"
    except OSError:
        return False


def build_id(path: Path) -> str:
    match = re.search(r"Build ID:\s*([0-9a-fA-F]+)", run(["readelf", "-n", str(path)]))
    if not match:
        raise RuntimeError(f"ELF has no GNU build-id: {path}")
    return match.group(1).lower()


def symbol_probe(debug_file: Path) -> tuple[str, str, str]:
    for line in run(["nm", "-an", str(debug_file)]).splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) != 3 or parts[1] not in {"T", "t"} or int(parts[0], 16) == 0:
            continue
        address, _, symbol = parts
        resolved = run(["addr2line", "-f", "-C", "-e", str(debug_file), f"0x{address}"]).splitlines()
        if len(resolved) >= 2 and resolved[0] != "??" and not resolved[1].startswith("??:"):
            return f"0x{address}", symbol, resolved[1]
    raise RuntimeError(f"no source-resolvable text symbol found in {debug_file}")


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
    args = parser.parse_args()

    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise RuntimeError("debug symbol packaging currently requires native Linux x86_64")
    for tool in ("objcopy", "strip", "readelf", "nm", "addr2line"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"required ELF tool not found: {tool}")

    install_root = args.install_root.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime_root = output / f"boost-gateway-{args.version}-linux-x64"
    symbols_root = output / f"boost-gateway-{args.version}-linux-x64-debug-symbols"
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    if symbols_root.exists():
        shutil.rmtree(symbols_root)
    # Materialize install-tree symlinks so every shipped ELF has its own mapping.
    shutil.copytree(install_root, runtime_root, symlinks=False)
    symbols_root.mkdir(parents=True)

    records: list[dict[str, object]] = []
    for runtime in sorted(path for path in runtime_root.rglob("*") if is_elf(path)):
        relative = runtime.relative_to(runtime_root)
        debug_relative = Path(str(relative) + ".debug")
        debug_file = symbols_root / debug_relative
        debug_file.parent.mkdir(parents=True, exist_ok=True)
        original_build_id = build_id(runtime)
        run(["objcopy", "--only-keep-debug", str(runtime), str(debug_file)])
        run(["strip", "--strip-unneeded", str(runtime)])
        run(["objcopy", f"--add-gnu-debuglink={debug_file}", str(runtime)])
        if build_id(runtime) != original_build_id:
            raise RuntimeError(f"build-id changed while stripping {relative}")
        address, symbol, source = symbol_probe(debug_file)
        records.append(
            {
                "runtime_path": relative.as_posix(),
                "debug_path": debug_relative.as_posix(),
                "build_id": original_build_id,
                "runtime_sha256": sha256(runtime),
                "debug_sha256": sha256(debug_file),
                "debuglink": debug_file.name,
                "probe_address": address,
                "probe_symbol": symbol,
                "probe_source": source,
            }
        )
    if not records:
        raise RuntimeError(f"install tree contains no ELF files: {install_root}")

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "candidate_revision": args.candidate_revision,
        "version": args.version,
        "platform": "linux-x64",
        "files": records,
    }
    manifest_path = symbols_root / "debug-symbol-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    runtime_archive = output / f"{runtime_root.name}.tar.gz"
    symbols_archive = output / f"{symbols_root.name}.tar.gz"
    archive_tree(runtime_root, runtime_archive)
    archive_tree(symbols_root, symbols_archive)
    summary = {
        "summary_version": 2,
        "passed": True,
        "candidate_revision": args.candidate_revision,
        "elf_count": len(records),
        "runtime_archive": runtime_archive.name,
        "runtime_archive_sha256": sha256(runtime_archive),
        "symbols_archive": symbols_archive.name,
        "symbols_archive_sha256": sha256(symbols_archive),
        "manifest_sha256": sha256(manifest_path),
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"debug symbol package: PASS ({len(records)} ELF files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
