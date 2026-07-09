#!/usr/bin/env python3
"""Validate the maintained legacy/helper inventory and deprecation boundaries."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
INVENTORY = ROOT / "docs/legacy/legacy-helper-inventory.md"

REQUIRED_DOC_TOKENS = (
    "legacy raw JSON",
    "compatibility-only",
    "generated proto",
    "generated protobuf / gRPC stub",
    "login/room/battle/match/leaderboard",
    "服务级 handler coverage matrix",
    "typed response wrap",
    "room governance / control-plane",
    "内部 Raft raw JSON RPC",
    "不得新增新的 raw JSON-only 业务消息类型",
)

BUSINESS_SERVICE_SOURCES = (
    "src/v2/login/login_backend_service.cpp",
    "src/v2/room/room_backend_service.cpp",
    "src/v2/battle/battle_backend_service.cpp",
    "src/v2/match/matchmaking_service.cpp",
    "src/v2/leaderboard/leaderboard_service.cpp",
)


REQUIRED_REFERENCES = (
    "include/v2/service/envelope_adapter.h",
    "tests/v2/unit/service_boundary_test.cpp",
    "proto/README.md",
    "conan/README.md",
    "conan/profiles/linux-gcc-x64",
    "src/v2/login/login_backend_service.cpp",
    "src/v2/room/room_backend_service.cpp",
    "src/v2/leaderboard/leaderboard_service.cpp",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/legacy-helper-inventory-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    checks: list[dict[str, Any]] = []
    add(checks, "inventory:exists", INVENTORY.exists(), "legacy/helper inventory doc exists")
    content = read(INVENTORY) if INVENTORY.exists() else ""

    for token in REQUIRED_DOC_TOKENS:
        add(checks, f"inventory:token:{token}", token in content, f"inventory mentions {token}")

    for relative in REQUIRED_REFERENCES:
        add(checks, f"inventory:reference:{relative}", relative in content and (ROOT / relative).exists(), f"{relative} is documented and exists")

    proto_readme = read(ROOT / "proto/README.md")
    add(
        checks,
        "proto:legacy-helper-boundary",
        "legacy raw JSON" in proto_readme and "generated protobuf / gRPC" in proto_readme,
        "proto README keeps helper and legacy boundary explicit",
    )

    envelope_adapter = read(ROOT / "include/v2/service/envelope_adapter.h")
    add(
        checks,
        "code:deprecation-notice",
        "legacy raw JSON backend payload is deprecated; use typed envelope" in envelope_adapter,
        "envelope adapter exports the legacy raw JSON deprecation notice",
    )
    add(
        checks,
        "code:policy-notice",
        "legacy raw JSON is compatibility-only and must not be used for new handlers" in envelope_adapter,
        "envelope adapter exports the legacy raw JSON compatibility-only policy notice",
    )

    root_cmake = read(ROOT / "CMakeLists.txt")
    add(
        checks,
        "build:legacy-v1-options-removed",
        "BOOST_BUILD_V1_LEGACY_EXAMPLES" not in root_cmake
        and "BOOST_BUILD_V1_LEGACY_CORE" not in root_cmake
        and "BOOST_BUILD_V1_LEGACY_TESTS" not in root_cmake,
        "v1 legacy build options have been removed from CMakeLists.txt",
    )
    add(
        checks,
        "build:legacy-game-dir-removed",
        not (ROOT / "include/game").exists() and not (ROOT / "src/game").exists(),
        "include/game and src/game directories no longer exist",
    )

    service_sources = {
        "login": ROOT / "src/v2/login/login_backend_service.cpp",
        "room": ROOT / "src/v2/room/room_backend_service.cpp",
        "battle": ROOT / "src/v2/battle/battle_backend_service.cpp",
        "matchmaking": ROOT / "src/v2/match/matchmaking_service.cpp",
        "leaderboard": ROOT / "src/v2/leaderboard/leaderboard_service.cpp",
    }
    for name, path in service_sources.items():
        source = read(path)
        add(
            checks,
            f"service:{name}:typed-request-path",
            "decode_handler_payload(" in source,
            f"{name} service has at least one typed request decode path",
        )
    for name in ("login", "room", "battle", "matchmaking", "leaderboard"):
        source = read(service_sources[name])
        add(
            checks,
            f"service:{name}:typed-response-path",
            "wrap_typed_response_if_needed(" in source,
            f"{name} service has at least one typed response wrap path",
        )

    for relative in BUSINESS_SERVICE_SOURCES:
        source = read(ROOT / relative)
        service_name = Path(relative).stem
        add(
            checks,
            f"service:{service_name}:raw-json-policy-marker",
            "decode_handler_payload(" in source and "wrap_typed_response_if_needed(" in source,
            f"{relative} must keep typed decode and response wrap markers before accepting new business handlers",
        )

    forbidden_new_raw_markers = (
        "new raw JSON-only",
        "raw_json_only_new_handler",
        "legacy_json_new_business",
    )
    for relative in BUSINESS_SERVICE_SOURCES:
        source = read(ROOT / relative)
        add(
            checks,
            f"service:{Path(relative).stem}:no-new-raw-json-marker",
            not any(marker in source for marker in forbidden_new_raw_markers),
            f"{relative} must not contain markers for new raw JSON-only business handlers",
        )

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "legacy_helper_inventory" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {
            "summary_path": str(summary_path),
            "inventory_path": str(INVENTORY),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(
        f"legacy/helper inventory gate: {'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
