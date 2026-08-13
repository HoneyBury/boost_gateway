"""Pre-production recovery responsibility module: recovery_drill_record."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lib.evidence_provenance import build_evidence_provenance
from scripts.lib.recovery_evidence import (
    write_command_summary,
    write_drill_record as _write_drill_record,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_IMAGE_BINARIES = {
    "gateway": ("v2_gateway_demo", "/app/bin/v2_gateway_demo"),
    "login-backend": ("v2_login_backend", "/app/bin/backend"),
    "room-backend": ("v2_room_backend", "/app/bin/backend"),
    "battle-backend": ("v2_battle_backend", "/app/bin/backend"),
    "matchmaking-backend": ("v2_match_backend", "/app/bin/backend"),
    "leaderboard-backend": ("v2_leaderboard_backend", "/app/bin/backend"),
}



from scripts.lib.recovery_drill_runtime import *  # noqa: F401,F403
from scripts.lib.recovery_drill_contract import *  # noqa: F401,F403
from scripts.lib.recovery_drill_images import *  # noqa: F401,F403
from scripts.lib.recovery_drill_preflight import *  # noqa: F401,F403
def write_drill_record(
    path: Path,
    production_recovery_summary: Path,
    sdk_summary: Path,
    redis_alert_summary: Path,
    docker_snapshot_summary: Path,
    monitoring_summary: Path,
    passed: bool,
    *,
    include_redis_recovery: bool,
    verify_redis_alert_transition: bool,
    failure_started_at: datetime | None,
    failure_ended_at: datetime | None,
    measured_rto_seconds: float | None,
    mode: str = "docker-compose",
) -> None:
    """Preserve the public helper while delegating evidence rendering."""
    _write_drill_record(
        path,
        production_recovery_summary,
        sdk_summary,
        redis_alert_summary,
        docker_snapshot_summary,
        monitoring_summary,
        passed,
        repo_root=REPO_ROOT,
        include_redis_recovery=include_redis_recovery,
        verify_redis_alert_transition=verify_redis_alert_transition,
        failure_started_at=failure_started_at,
        failure_ended_at=failure_ended_at,
        measured_rto_seconds=measured_rto_seconds,
        mode=mode,
    )
