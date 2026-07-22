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
ENVELOPE_ADAPTER = ROOT / "src/v2/service/envelope_adapter.cpp"
ENVELOPE_CODEC = ROOT / "include/v3/proto/envelope_codec.h"
SERVICE_BOUNDARY_TEST = ROOT / "tests/v2/unit/service_boundary_test.cpp"

REQUIRED_DOC_TOKENS = (
    "legacy raw JSON",
    "compatibility-only",
    "generated proto",
    "generated protobuf / gRPC stub",
    "login/room/battle/match/leaderboard",
    "服务级 handler coverage matrix",
    "typed response wrap",
    "room governance / control-plane",
    "内部 Raft legacy JSON writer",
    "不得新增新的 raw JSON-only 业务消息类型",
    "29 个业务 handler",
    "29 个 handler 已具备 `EnvelopeMessageKind` / schema-backed typed contract",
    "已进入 `EnvelopeMessageKind` / `proto/v3/login.proto`",
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
    "src/v2/service/envelope_adapter.cpp",
    "include/v3/proto/envelope_codec.h",
    "proto/v3/login.proto",
    "tests/v2/unit/service_boundary_test.cpp",
    "proto/README.md",
    "conan/README.md",
    "conan/profiles/linux-gcc-x64",
    "src/v2/login/login_backend_service.cpp",
    "src/v2/room/room_backend_service.cpp",
    "src/v2/leaderboard/leaderboard_service.cpp",
)

SCHEMA_TYPED_HANDLERS = {
    "login": (
        "register_account",
        "login_request",
        "guest_login",
        "token_validate",
        "session_bind",
        "session_close",
        "token_refresh",
    ),
    "room": (
        "room_create",
        "room_join",
        "room_ready",
        "room_leave",
        "room_start_battle",
        "room_list",
        "room_detail",
        "room_kick",
        "room_transfer_owner",
        "room_state_push",
        "room_battle_finished",
    ),
    "battle": (
        "battle_create",
        "battle_input",
        "battle_state",
        "battle_finish",
        "replay_load",
    ),
    "matchmaking": (
        "match_join",
        "match_leave",
        "match_status",
    ),
    "leaderboard": (
        "leaderboard_submit",
        "leaderboard_top",
        "leaderboard_rank",
    ),
}

PUSH_ONLY_HANDLERS = {
    "room_state_push",
}

ADAPTER_RESPONSE_NAME_OVERRIDES = {
    "login_request": "login_response",
}

CODEC_REQUEST_NAME_OVERRIDES = {
    "leaderboard_submit": "submit",
    "leaderboard_top": "top",
    "leaderboard_rank": "rank",
}

CODEC_RESPONSE_NAME_OVERRIDES = {
    "login_request": "login_response",
    "leaderboard_submit": "submit_response",
    "leaderboard_top": "top_response",
    "leaderboard_rank": "rank_response",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def response_mapping_name(handler: str) -> str:
    if handler in PUSH_ONLY_HANDLERS:
        return handler
    if handler in ADAPTER_RESPONSE_NAME_OVERRIDES:
        return ADAPTER_RESPONSE_NAME_OVERRIDES[handler]
    return f"{handler}_response"


def codec_request_name(handler: str) -> str:
    return CODEC_REQUEST_NAME_OVERRIDES.get(handler, handler)


def codec_response_name(handler: str) -> str:
    if handler in PUSH_ONLY_HANDLERS:
        return codec_request_name(handler)
    return CODEC_RESPONSE_NAME_OVERRIDES.get(handler, f"{handler}_response")


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
    envelope_adapter_impl = read(ENVELOPE_ADAPTER)
    envelope_codec = read(ENVELOPE_CODEC)
    service_boundary_test = read(SERVICE_BOUNDARY_TEST)
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
    add(
        checks,
        "test:deprecation-notice-asserted",
        "legacy_raw_json_deprecation_notice()" in service_boundary_test
        and "legacy_raw_json_policy_notice()" in service_boundary_test
        and "DecodeHandlerPayloadMarksLegacyRawJsonDeprecated" in service_boundary_test,
        "service boundary test asserts deprecation and compatibility-only notices",
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

    for service_name, handlers in SCHEMA_TYPED_HANDLERS.items():
        source = read(service_sources[service_name])
        for handler in handlers:
            add(
                checks,
                f"inventory:{service_name}:schema-handler-documented:{handler}",
                f"`{handler}`" in content,
                f"inventory documents schema-backed handler {handler}",
            )
            add(
                checks,
                f"service:{service_name}:handler-registered:{handler}",
                f'"{handler}"' in source,
                f"{service_name} service registers handler {handler}",
            )
            add(
                checks,
                f"adapter:mapping-request:{handler}",
                f'"{handler}"' in envelope_adapter_impl,
                f"envelope adapter maps request kind for {handler}",
            )
            response_name = response_mapping_name(handler)
            add(
                checks,
                f"adapter:mapping-response:{response_name}",
                f'"{response_name}"' in envelope_adapter_impl,
                f"envelope adapter maps response/push kind for {handler}",
            )
            add(
                checks,
                f"codec:kind-string:{codec_request_name(handler)}",
                f'return "{codec_request_name(handler)}";' in envelope_codec
                or f'if (kind == "{codec_request_name(handler)}")' in envelope_codec,
                f"envelope codec exposes string form for {handler}",
            )
            if handler not in PUSH_ONLY_HANDLERS:
                codec_response = codec_response_name(handler)
                add(
                    checks,
                    f"codec:kind-string:{codec_response}",
                    f'return "{codec_response}";' in envelope_codec
                    or f'if (kind == "{codec_response}")' in envelope_codec,
                    f"envelope codec exposes string form for {response_name}",
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
