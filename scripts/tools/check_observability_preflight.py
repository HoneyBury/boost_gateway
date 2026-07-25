#!/usr/bin/env python3
"""Fail closed unless production observability credentials and alert delivery are proven."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.operations_identity import collect_operations_identity


DEFAULT_CONFIG = Path("/etc/boost-gateway/alertmanager.yml")
DEFAULT_ENV = Path("/etc/boost-gateway/compose.env")
DEFAULT_ATTESTATION = Path(
    "/var/lib/boost-gateway-evidence/observability/alert-delivery-attestation.json"
)
DEFAULT_SUMMARY = Path(
    "/var/lib/boost-gateway-evidence/observability/observability-preflight-summary.json"
)
ALERTMANAGER_IMAGE = "prom/alertmanager:v0.28.1"
MAX_ATTESTATION_AGE = timedelta(days=7)
INTEGRATION_KEYS = {
    "discord_configs",
    "email_configs",
    "msteams_configs",
    "msteamsv2_configs",
    "opsgenie_configs",
    "pagerduty_configs",
    "pushover_configs",
    "slack_configs",
    "sns_configs",
    "telegram_configs",
    "victorops_configs",
    "webex_configs",
    "webhook_configs",
    "wechat_configs",
}
PLACEHOLDER_TOKENS = {
    "127.0.0.1",
    "change-me",
    "changeme",
    "example.com",
    "localhost",
    "placeholder",
}


class PreflightError(RuntimeError):
    """Raised when an observability production invariant is not satisfied."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_regular_file(path: Path, label: str, *, enforce_ownership: bool) -> str:
    if path.is_symlink() or not path.is_file():
        raise PreflightError(f"{label} must be a regular non-symlink file: {path}")
    status = path.stat()
    if enforce_ownership:
        mode = stat.S_IMODE(status.st_mode)
        if status.st_uid != 0 or mode not in {0o600, 0o640}:
            raise PreflightError(f"{label} must be root-owned with mode 0600 or 0640")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PreflightError(f"cannot read {label}: {exc}") from exc


def _parse_environment(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PreflightError(f"invalid Compose environment line {line_number}")
        key, value = line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None or key in values:
            raise PreflightError(f"invalid or duplicate Compose variable at line {line_number}")
        values[key] = value
    return values


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise PreflightError(f"{label} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PreflightError(f"{label} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise PreflightError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _route_receiver(config: str) -> str:
    match = re.search(r"(?m)^\s+receiver:\s*['\"]?([^\s#'\"]+)", config)
    if match is None:
        raise PreflightError("Alertmanager route has no receiver")
    receiver = match.group(1)
    if receiver.lower() in {"default", "null", "none"}:
        raise PreflightError("Alertmanager route uses a placeholder receiver")
    return receiver


def _validate_alertmanager_text(config: str) -> str:
    receiver = _route_receiver(config)
    receiver_block: list[str] | None = None
    receiver_indent = -1
    lines = config.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)-\s+name:\s*['\"]?([^\s#'\"]+)", line)
        if match is None or match.group(2) != receiver:
            continue
        receiver_indent = len(match.group(1))
        receiver_block = []
        for following in lines[index + 1 :]:
            next_receiver = re.match(
                r"^(\s*)-\s+name:\s*['\"]?([^\s#'\"]+)", following
            )
            if next_receiver is not None and len(next_receiver.group(1)) == receiver_indent:
                break
            receiver_block.append(following)
        break
    if receiver_block is None:
        raise PreflightError("Alertmanager route receiver is not declared")
    integrations = {
        match.group(1)
        for match in re.finditer(
            r"(?m)^\s+([a-z0-9_]+_configs):\s*$", "\n".join(receiver_block)
        )
        if match.group(1) in INTEGRATION_KEYS
    }
    if not integrations:
        raise PreflightError(
            "Alertmanager route receiver has no supported notification integration"
        )
    lowered = config.lower()
    found_placeholders = sorted(token for token in PLACEHOLDER_TOKENS if token in lowered)
    if found_placeholders:
        raise PreflightError(
            "Alertmanager config contains placeholder endpoint tokens: "
            + ", ".join(found_placeholders)
        )
    return receiver


def run_amtool(config_path: Path) -> None:
    command = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--volume",
        f"{config_path.parent}:{config_path.parent}:ro",
        "--entrypoint",
        "/bin/amtool",
        ALERTMANAGER_IMAGE,
        "check-config",
        str(config_path),
    ]
    completed = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise PreflightError(f"amtool rejected Alertmanager config: {detail}")


def validate_preflight(
    config_path: Path,
    env_path: Path,
    attestation_path: Path,
    *,
    current_time: datetime | None = None,
    enforce_ownership: bool = True,
    config_validator: Callable[[Path], None] = run_amtool,
    identity_provider: Callable[[], dict[str, Any]] = collect_operations_identity,
) -> dict[str, Any]:
    observed_at = (current_time or datetime.now(UTC)).astimezone(UTC)
    config = _load_regular_file(
        config_path, "Alertmanager config", enforce_ownership=enforce_ownership
    )
    receiver = _validate_alertmanager_text(config)
    config_validator(config_path)
    config_sha256 = sha256_file(config_path)

    secret_env = _parse_environment(
        _load_regular_file(
            env_path, "Compose secret environment", enforce_ownership=enforce_ownership
        )
    )
    username = secret_env.get("GRAFANA_ADMIN_USER", "").strip()
    password = secret_env.get("GRAFANA_ADMIN_PASSWORD", "")
    if not username or username.lower() == "admin":
        raise PreflightError("Grafana admin username must be explicit and non-default")
    if len(password) < 20 or password.lower() in {
        "admin",
        "password",
        "boost-gateway-change-me",
        "changeme",
    }:
        raise PreflightError("Grafana admin password is default, empty, or shorter than 20 characters")

    attestation_content = _load_regular_file(
        attestation_path,
        "alert delivery attestation",
        enforce_ownership=enforce_ownership,
    )
    try:
        attestation = json.loads(attestation_content)
    except json.JSONDecodeError as exc:
        raise PreflightError(f"alert delivery attestation is invalid JSON: {exc}") from exc
    if not isinstance(attestation, dict):
        raise PreflightError("alert delivery attestation must be a JSON object")
    forbidden_attestation_keys: set[str] = set()

    def find_secret_keys(value: object, location: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_location = f"{location}.{key}"
                if re.search(
                    r"password|secret|credential|webhook_url|access_token",
                    str(key),
                    re.I,
                ):
                    forbidden_attestation_keys.add(child_location)
                find_secret_keys(child, child_location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                find_secret_keys(child, f"{location}[{index}]")

    find_secret_keys(attestation, "attestation")
    if forbidden_attestation_keys:
        raise PreflightError("alert delivery attestation contains secret-like fields")
    if attestation.get("schema_version") != 1 or attestation.get("overall_pass") is not True:
        raise PreflightError("alert delivery attestation is not passing schema version 1")
    if attestation.get("receiver") != receiver:
        raise PreflightError("alert delivery attestation receiver differs from active route")
    if attestation.get("alertmanager_config_sha256") != config_sha256:
        raise PreflightError("alert delivery attestation does not bind the active config")

    identity = identity_provider()
    host = identity.get("host") if isinstance(identity, dict) else None
    if not isinstance(host, dict) or not host.get("host_id_sha256"):
        raise PreflightError("current host identity is unavailable")
    if attestation.get("host_id_sha256") != host["host_id_sha256"]:
        raise PreflightError("alert delivery attestation belongs to another host")

    tested_at = _parse_timestamp(attestation.get("tested_at"), "tested_at")
    if tested_at > observed_at + timedelta(minutes=5):
        raise PreflightError("alert delivery attestation is from the future")
    if observed_at - tested_at > MAX_ATTESTATION_AGE:
        raise PreflightError("alert delivery attestation is older than 7 days")
    firing = attestation.get("firing_delivery")
    resolved = attestation.get("resolved_delivery")
    if not isinstance(firing, dict) or not str(firing.get("id", "")).strip():
        raise PreflightError("firing notification delivery ID is missing")
    if not isinstance(resolved, dict) or not str(resolved.get("id", "")).strip():
        raise PreflightError("resolved notification delivery ID is missing")
    firing_at = _parse_timestamp(firing.get("observed_at"), "firing_delivery.observed_at")
    resolved_at = _parse_timestamp(resolved.get("observed_at"), "resolved_delivery.observed_at")
    if not (firing_at <= resolved_at <= tested_at + timedelta(minutes=5)):
        raise PreflightError("firing/resolved notification timestamps are inconsistent")
    if firing_at < tested_at - timedelta(days=1):
        raise PreflightError("firing/resolved notification delivery is not from the test run")

    return {
        "schema_version": 1,
        "overall_pass": True,
        "checked_at": observed_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "receiver": receiver,
        "alertmanager_config_sha256": config_sha256,
        "alert_delivery_attestation_sha256": sha256_file(attestation_path),
        "host_id_sha256": host["host_id_sha256"],
        "attestation_tested_at": tested_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "secret_material_recorded": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alertmanager-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--compose-env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--delivery-attestation", type=Path, default=DEFAULT_ATTESTATION)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    try:
        summary = validate_preflight(
            args.alertmanager_config, args.compose_env, args.delivery_attestation
        )
    except (OSError, PreflightError, subprocess.SubprocessError) as exc:
        print(f"observability preflight: FAIL: {exc}", file=sys.stderr)
        return 1
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(args.summary_path, 0o640)
    print("observability preflight: PASS")
    print(f"summary: {args.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
