#!/usr/bin/env python3
"""Independently verify a macOS ARM64 stripped runtime/dSYM archive pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import posixpath
import re
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


MACHO64_MAGIC = b"\xcf\xfa\xed\xfe"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output(command: list[str]) -> str:
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout


def is_macho64(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as stream:
            return stream.read(4) == MACHO64_MAGIC
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
        source.extractall(destination, filter="fully_trusted")
    roots = [path for path in destination.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"archive must contain exactly one root directory: {archive}")
    return roots[0]


def uuid(path: Path) -> str:
    matches = re.findall(
        r"UUID: ([0-9A-F-]+) \(arm64\)",
        output(["dwarfdump", "--uuid", str(path)]),
    )
    return matches[0] if len(matches) == 1 else ""


def safe_relative(value: object) -> PurePosixPath | None:
    if not isinstance(value, str) or not value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        return None
    return path


def add(checks: list[dict[str, object]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-archive", type=Path, required=True)
    parser.add_argument("--symbols-archive", type=Path, required=True)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("dSYM verification requires native macOS ARM64")

    checks: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="boost-macos-dsym-verify-") as raw:
        temp = Path(raw)
        runtime_root = safe_extract(args.runtime_archive.resolve(), temp / "runtime")
        symbols_root = safe_extract(args.symbols_archive.resolve(), temp / "symbols")
        manifest = json.loads((symbols_root / "dsym-manifest.json").read_text(encoding="utf-8"))
        add(checks, "manifest:platform", manifest.get("platform") == "macos-arm64", str(manifest.get("platform")))
        add(
            checks,
            "manifest:revision",
            manifest.get("candidate_revision") == args.candidate_revision,
            str(manifest.get("candidate_revision")),
        )
        records = manifest.get("files", [])
        add(checks, "manifest:records", isinstance(records, list) and bool(records), f"records={len(records) if isinstance(records, list) else 0}")
        mapped = {str(item.get("runtime_path")) for item in records if isinstance(item, dict)} if isinstance(records, list) else set()
        archived = {
            path.relative_to(runtime_root).as_posix()
            for path in runtime_root.rglob("*")
            if is_macho64(path)
        }
        add(checks, "manifest:complete-macho-map", mapped == archived, f"archive={sorted(archived)} mapped={sorted(mapped)}")
        add(
            checks,
            "manifest:unique-runtime-paths",
            isinstance(records, list) and len(mapped) == len(records),
            f"records={len(records) if isinstance(records, list) else 0} unique={len(mapped)}",
        )
        uuids: set[str] = set()
        for index, record in enumerate(records if isinstance(records, list) else []):
            runtime_relative = safe_relative(record.get("runtime_path"))
            dsym_relative = safe_relative(record.get("dsym_path"))
            dwarf_relative = safe_relative(record.get("dsym_dwarf_path"))
            prefix = f"macho:{index}:{record.get('runtime_path', 'unknown')}"
            safe_paths = all(path is not None for path in (runtime_relative, dsym_relative, dwarf_relative))
            add(checks, f"{prefix}:safe-paths", safe_paths, "manifest paths stay within archive roots")
            if not safe_paths:
                continue
            runtime = runtime_root.joinpath(*runtime_relative.parts)
            dsym = symbols_root.joinpath(*dsym_relative.parts)
            dwarf = symbols_root.joinpath(*dwarf_relative.parts)
            add(checks, f"{prefix}:files", runtime.is_file() and dsym.is_dir() and dwarf.is_file(), "runtime/dSYM files exist")
            if not runtime.is_file() or not dwarf.is_file():
                continue
            runtime_uuid = uuid(runtime)
            dsym_uuid = uuid(dsym)
            expected_uuid = str(record.get("uuid"))
            add(checks, f"{prefix}:uuid", runtime_uuid == dsym_uuid == expected_uuid, runtime_uuid)
            add(checks, f"{prefix}:unique-uuid", bool(runtime_uuid) and runtime_uuid not in uuids, runtime_uuid)
            uuids.add(runtime_uuid)
            add(checks, f"{prefix}:runtime-hash", sha256(runtime) == record.get("runtime_sha256"), sha256(runtime))
            add(checks, f"{prefix}:dwarf-hash", sha256(dwarf) == record.get("dsym_dwarf_sha256"), sha256(dwarf))
            identity = output(["file", str(runtime)]).strip()
            add(checks, f"{prefix}:native-arm64", "Mach-O 64-bit" in identity and "arm64" in identity and "x86_64" not in identity, identity)
            runtime_debug = output(["dwarfdump", "--debug-info", str(runtime)])
            dsym_debug = output(["dwarfdump", "--debug-info", str(dsym)])
            add(checks, f"{prefix}:runtime-split", "DW_TAG_compile_unit" not in runtime_debug, "runtime has no embedded compile unit")
            add(checks, f"{prefix}:dsym-dwarf", "DW_TAG_compile_unit" in dsym_debug, "dSYM contains compile units")
            resolved = output(["dwarfdump", "--lookup", str(record["probe_address"]), str(dsym)])
            symbolized = "DW_TAG_subprogram" in resolved and "Line info: file" in resolved
            add(checks, f"{prefix}:symbolization", symbolized, str(record.get("probe_source")))
            signature = subprocess.run(
                ["codesign", "--verify", "--verbose=2", str(runtime)],
                text=True,
                capture_output=True,
            )
            signature_detail = subprocess.run(
                ["codesign", "-dv", "--verbose=4", str(runtime)],
                text=True,
                capture_output=True,
            )
            signature_text = signature_detail.stderr or signature_detail.stdout
            add(
                checks,
                f"{prefix}:adhoc-signature",
                signature.returncode == 0
                and signature_detail.returncode == 0
                and "Signature=adhoc" in signature_text,
                signature_text.strip(),
            )

        hello = runtime_root / "bin" / "example_hello_world"
        if hello.is_file():
            completed = subprocess.run([str(hello)], text=True, capture_output=True)
            add(checks, "runtime:hello-world", completed.returncode == 0 and "Hello, World!" in completed.stdout, completed.stdout[-500:])

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "passed": not failed,
        "overall_pass": not failed,
        "candidate_revision": args.candidate_revision,
        "failed_category": "macos_dsym" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "runtime_archive_sha256": sha256(args.runtime_archive),
        "symbols_archive_sha256": sha256(args.symbols_archive),
        "checks": checks,
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"macOS dSYM verification: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
