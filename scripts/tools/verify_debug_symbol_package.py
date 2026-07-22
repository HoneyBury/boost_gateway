#!/usr/bin/env python3
"""Independently verify a stripped runtime/debug-symbol archive pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output(command: list[str]) -> str:
    return subprocess.run(
        command,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    ).stdout


def is_regular_elf(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as stream:
            return stream.read(4) == b"\x7fELF"
    except OSError:
        return False


def safe_extract(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"unsafe archive member: {member.name}")
            if member.issym() or member.islnk():
                target = PurePosixPath(posixpath.normpath(str(path.parent / member.linkname)))
                if PurePosixPath(member.linkname).is_absolute() or ".." in target.parts:
                    raise RuntimeError(f"unsafe archive link: {member.name} -> {member.linkname}")
        source.extractall(destination)
    roots = [path for path in destination.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"archive must contain exactly one root directory: {archive}")
    return roots[0]


def build_id(path: Path) -> str:
    match = re.search(r"Build ID:\s*([0-9a-fA-F]+)", output(["readelf", "-n", str(path)]))
    return match.group(1).lower() if match else ""


def add(checks: list[dict[str, object]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-archive", type=Path, required=True)
    parser.add_argument("--symbols-archive", type=Path, required=True)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    args = parser.parse_args()
    for tool in ("readelf", "addr2line"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"required ELF tool not found: {tool}")

    checks: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="boost-symbol-verify-") as temp_text:
        temp = Path(temp_text)
        runtime_root = safe_extract(args.runtime_archive.resolve(), temp / "runtime")
        symbols_root = safe_extract(args.symbols_archive.resolve(), temp / "symbols")
        manifest_path = symbols_root / "debug-symbol-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        add(checks, "manifest:revision", manifest.get("candidate_revision") == args.candidate_revision, str(manifest.get("candidate_revision")))
        records = manifest.get("files", [])
        add(checks, "manifest:records", isinstance(records, list) and bool(records), f"records={len(records) if isinstance(records, list) else 0}")
        mapped_runtime_paths = {
            str(record.get("runtime_path")) for record in records if isinstance(record, dict)
        } if isinstance(records, list) else set()
        archive_elf_paths = {
            path.relative_to(runtime_root).as_posix()
            for path in runtime_root.rglob("*")
            if is_regular_elf(path)
        }
        add(checks, "manifest:complete-elf-map", archive_elf_paths == mapped_runtime_paths, f"archive={sorted(archive_elf_paths)} mapped={sorted(mapped_runtime_paths)}")
        for index, record in enumerate(records if isinstance(records, list) else []):
            runtime = runtime_root / str(record["runtime_path"])
            debug_file = symbols_root / str(record["debug_path"])
            prefix = f"elf:{index}:{record['runtime_path']}"
            add(checks, f"{prefix}:files", runtime.is_file() and debug_file.is_file(), "runtime/debug files exist")
            if not runtime.is_file() or not debug_file.is_file():
                continue
            add(checks, f"{prefix}:runtime-hash", sha256(runtime) == record.get("runtime_sha256"), sha256(runtime))
            add(checks, f"{prefix}:debug-hash", sha256(debug_file) == record.get("debug_sha256"), sha256(debug_file))
            add(checks, f"{prefix}:build-id", build_id(runtime) == record.get("build_id") == build_id(debug_file), build_id(runtime))
            runtime_sections = output(["readelf", "-S", str(runtime)])
            debug_sections = output(["readelf", "-S", str(debug_file)])
            add(checks, f"{prefix}:runtime-stripped", ".debug_info" not in runtime_sections, "runtime has no .debug_info")
            add(checks, f"{prefix}:debug-has-dwarf", ".debug_info" in debug_sections, "debug file has .debug_info")
            debuglink = output(["readelf", "--string-dump=.gnu_debuglink", str(runtime)])
            add(checks, f"{prefix}:debuglink", str(record.get("debuglink")) in debuglink, debuglink.strip())
            resolved = output([
                "addr2line", "-f", "-C", "-e", str(debug_file), str(record["probe_address"])
            ]).splitlines()
            symbol_ok = len(resolved) >= 2 and resolved[0] != "??" and not resolved[1].startswith("??:")
            add(checks, f"{prefix}:symbolization", symbol_ok, "\n".join(resolved[:2]))

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "passed": not failed,
        "overall_pass": not failed,
        "failed_category": "debug_symbols" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "runtime_archive_sha256": sha256(args.runtime_archive),
        "symbols_archive_sha256": sha256(args.symbols_archive),
        "checks": checks,
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"debug symbol verification: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
