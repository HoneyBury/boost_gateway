#!/usr/bin/env python3
"""Validate strict-Conan and explicit-development OpenSSL governance."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def add(checks: list[dict[str, object]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def main() -> int:
    cmake = read("cmake/Dependencies.cmake")
    third_party_readme = read("third_party/README.md")
    conanfile = read("conanfile.py")
    download_sh = read("third_party/download_deps.sh")
    package_sh = read("third_party/package.sh")

    checks: list[dict[str, object]] = []
    add(checks, "openssl:conan-requirement", 'self.requires("openssl/3.3.2")' in conanfile, "Conan graph provides OpenSSL")
    add(checks, "openssl:central-resolver", "function(project_ensure_openssl)" in cmake, "Dependencies.cmake has a central OpenSSL resolver")
    add(checks, "openssl:config-first", "find_package(OpenSSL CONFIG QUIET)" in cmake, "resolver checks Conan/config package first")
    add(checks, "openssl:system-fallback", "find_package(OpenSSL QUIET)" in cmake, "resolver supports system OpenSSL fallback")
    add(checks, "openssl:local-root-fallback", '"${THIRD_PARTY_DIR}/openssl"' in cmake and '"${THIRD_PARTY_DIR}/openssl-src"' in cmake, "resolver supports local OpenSSL install directories")
    add(checks, "openssl:explicit-root-diagnostic", "OPENSSL_ROOT_DIR" in cmake and "third_party/openssl" in cmake, "resolver failure explains explicit/local OpenSSL options")
    add(checks, "openssl:standard-targets", "OpenSSL::SSL" in cmake and "OpenSSL::Crypto" in cmake, "standard OpenSSL CMake targets remain the contract")
    add(checks, "openssl:third-party-doc", "OpenSSL 是例外" in third_party_readme and "Conan config package" in third_party_readme, "third_party docs explain OpenSSL acquisition paths")
    add(checks, "openssl:download-script-doc", "OpenSSL is not downloaded as source" in download_sh and "BOOST_DEPENDENCY_PROVIDER=conan" in download_sh, "download script documents OpenSSL source policy")
    add(checks, "openssl:package-warning", "no local OpenSSL install found" in package_sh, "package script warns when no local OpenSSL install is bundled")

    failed = [check for check in checks if not check["passed"]]
    summary_path = ROOT / "runtime/validation/openssl-dependency-governance-summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "openssl_dependency_governance" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"openssl dependency governance gate: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
