#!/usr/bin/env python3
"""Validate v3 proto schema files without requiring protoc."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PROTO_FILES = {
    "common.proto": ["ServiceEnvelope", "LoginPayload", "RoomPayload", "BattlePayload"],
    "login.proto": ["LoginRequest", "LoginResponse", "TokenValidateRequest"],
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


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proto-dir", type=Path, default=Path("proto/v3"))
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

    if errors:
        return fail("\n".join(errors))

    print(f"validated {len(PROTO_FILES)} v3 proto schema files under {proto_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
