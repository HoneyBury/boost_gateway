#!/usr/bin/env python3
"""Validate v3 proto schema files without requiring protoc."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PROTO_FILES = {
    "common.proto": ["ServiceEnvelope", "LoginPayload", "RoomPayload", "BattlePayload"],
    "login.proto": [
        "LoginRequest",
        "LoginResponse",
        "RegisterAccountRequest",
        "RegisterAccountResponse",
        "GuestLoginRequest",
        "GuestLoginResponse",
        "TokenValidateRequest",
    ],
    "room.proto": ["RoomCreateRequest", "RoomJoinRequest", "RoomReadyRequest"],
    "battle.proto": ["BattleCreateRequest", "BattleInputRequest", "BattleInputResponse"],
    "match.proto": ["MatchJoinRequest", "MatchStatusRequest", "MatchFoundPush"],
    "leaderboard.proto": [
        "LeaderboardSubmitRequest",
        "LeaderboardTopRequest",
        "LeaderboardRankRequest",
        "LeaderboardEntry",
    ],
}

TRANSPORT_CONTRACT = {
    "ServiceEnvelope": ["login", "room", "battle", "match", "leaderboard"],
    "LoginPayload": [
        "login_request",
        "login_response",
        "register_account",
        "register_account_response",
        "guest_login",
        "guest_login_response",
    ],
    "RoomPayload": ["room_create", "room_create_response", "room_join", "room_join_response", "room_ready", "room_ready_response"],
    "BattlePayload": ["battle_input", "battle_input_response"],
    "MatchPayload": ["match_join", "match_join_response", "match_status", "match_status_response"],
    "LeaderboardPayload": ["submit", "submit_response", "top", "top_response", "rank", "rank_response"],
}


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def message_body(content: str, message: str) -> str:
    start = re.search(rf"\bmessage\s+{re.escape(message)}\s*\{{", content)
    if not start:
        return ""
    depth = 0
    body_start = start.end()
    for idx in range(start.end() - 1, len(content)):
        char = content[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[body_start:idx]
    return ""


def validate_transport_contract(common: str) -> list[str]:
    errors: list[str] = []
    for message, fields in TRANSPORT_CONTRACT.items():
        body = message_body(common, message)
        if not body:
            errors.append(f"common.proto: missing transport message {message}")
            continue
        if "oneof" not in body:
            errors.append(f"common.proto: {message} must declare oneof payload/kind")
        for field in fields:
            if not re.search(rf"\b{re.escape(field)}\s*=", body):
                errors.append(f"common.proto: {message} missing transport field {field}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proto-dir", type=Path, default=Path("proto/v3"))
    parser.add_argument(
        "--require-transport-contract",
        action="store_true",
        help="Validate the experimental generated-transport payload contract in common.proto.",
    )
    args = parser.parse_args()

    proto_dir = args.proto_dir
    errors: list[str] = []

    for filename, messages in PROTO_FILES.items():
        path = proto_dir / filename
        if not path.is_file():
            errors.append(f"missing proto file: {path}")
            continue

        content = path.read_text(encoding="utf-8")
        if 'syntax = "proto3";' not in content:
            errors.append(f"{path}: missing proto3 syntax")
        if "package boost.gateway.v3;" not in content:
            errors.append(f"{path}: missing boost.gateway.v3 package")

        for message in messages:
            if not re.search(rf"\bmessage\s+{re.escape(message)}\b", content):
                errors.append(f"{path}: missing message {message}")

    common = (proto_dir / "common.proto").read_text(encoding="utf-8")
    for field in ["correlation_id", "source_service", "target_service", "trace_id", "span_id"]:
        if not re.search(rf"\b{re.escape(field)}\s*=", common):
            errors.append(f"common.proto: missing ServiceEnvelope field {field}")
    if args.require_transport_contract:
        errors.extend(validate_transport_contract(common))

    if errors:
        return fail("\n".join(errors))

    print(f"validated {len(PROTO_FILES)} v3 proto schema files under {proto_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
