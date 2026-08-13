#!/usr/bin/env python3
"""Run and aggregate the external BoostGateway SDK business canary."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from scripts.lib.perf_statistics import interpolated_percentile
except ModuleNotFoundError:  # pragma: no cover - direct installed-script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.lib.perf_statistics import interpolated_percentile

DEFAULT_EVIDENCE_ROOT = Path("/var/lib/boost-gateway-canary")
DEFAULT_DEPLOYMENT_RECORD = Path("/etc/boost-gateway-canary/deployment-record.json")
DEFAULT_TIMEOUT_MS = 5000
ENVIRONMENT_KEYS = frozenset(
    {
        "BOOST_GATEWAY_CANARY_HOST",
        "BOOST_GATEWAY_CANARY_PORT",
        "BOOST_GATEWAY_CANARY_USER_A",
        "BOOST_GATEWAY_CANARY_USER_B",
        "BOOST_GATEWAY_CANARY_TOKEN_A",
        "BOOST_GATEWAY_CANARY_TOKEN_B",
        "BOOST_GATEWAY_CANARY_ALERTMANAGER_URL",
        "BOOST_GATEWAY_CANARY_TIMEOUT_MS",
    }
)
REQUIRED_STEPS = ("login", "room", "battle", "settlement", "leaderboard", "reconnect")
ERROR_TYPES = {
    "none",
    "connect_error",
    "sdk_error",
    "protocol_error",
    "timeout",
    "internal_error",
    "dependency_failure",
}


class CanaryError(RuntimeError):
    pass


class StepFailure(CanaryError):
    def __init__(self, error_type: str, message: str, code: int | None = None) -> None:
        super().__init__(message)
        if error_type not in ERROR_TYPES:
            raise ValueError(f"unknown canary error type: {error_type}")
        self.error_type = error_type
        self.code = code


@dataclass(frozen=True)
class CanaryConfig:
    host: str
    port: int
    user_a: str
    user_b: str
    token_a: str
    token_b: str
    alertmanager_url: str
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    @property
    def endpoint(self) -> str:
        if ":" in self.host:
            return f"tcp://[{self.host}]:{self.port}"
        return f"tcp://{self.host}:{self.port}"


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CanaryError(f"invalid UTC timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise CanaryError(f"timestamp must include a UTC offset: {value}")
    return parsed.astimezone(UTC)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise CanaryError(f"required regular JSON file is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CanaryError(f"cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CanaryError(f"JSON root must be an object: {path}")
    return value


def write_create_only(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    except FileExistsError as exc:
        raise CanaryError(f"refusing to replace create-only evidence: {path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def validate_alertmanager_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise CanaryError("Alertmanager URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise CanaryError(
            "Alertmanager URL must be an HTTP(S) origin without credentials"
        )
    path = parsed.path.rstrip("/")
    if path and path != "/api/v2/alerts":
        raise CanaryError("Alertmanager URL path must be empty or /api/v2/alerts")
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise CanaryError("Alertmanager URL has an invalid port") from exc
    port = f":{parsed_port}" if parsed_port is not None else ""
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}{port}/api/v2/alerts"


def validate_config(config: CanaryConfig) -> CanaryConfig:
    try:
        ipaddress.ip_address(config.host)
    except ValueError:
        labels = config.host.split(".")
        if len(config.host) > 253 or any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
            for label in labels
        ):
            raise CanaryError("canary host must be an IP literal or DNS hostname")
    if not 1 <= config.port <= 65535:
        raise CanaryError("canary port must be between 1 and 65535")
    if not 250 <= config.timeout_ms <= 30_000:
        raise CanaryError("timeout must be between 250 and 30000 milliseconds")
    for label, value in (("user A", config.user_a), ("user B", config.user_b)):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,48}", value):
            raise CanaryError(
                f"{label} must be a fixed 3-48 character synthetic identity"
            )
    if config.user_a == config.user_b:
        raise CanaryError("canary users must be distinct")
    if not config.token_a or not config.token_b:
        raise CanaryError("both canary tokens are required")
    validate_alertmanager_url(config.alertmanager_url)
    return config


def config_from_mapping(environment: dict[str, str]) -> CanaryConfig:
    required = (
        "BOOST_GATEWAY_CANARY_HOST",
        "BOOST_GATEWAY_CANARY_USER_A",
        "BOOST_GATEWAY_CANARY_USER_B",
        "BOOST_GATEWAY_CANARY_TOKEN_A",
        "BOOST_GATEWAY_CANARY_TOKEN_B",
        "BOOST_GATEWAY_CANARY_ALERTMANAGER_URL",
    )
    missing = [name for name in required if not environment.get(name)]
    if missing:
        raise CanaryError("missing canary environment variables: " + ", ".join(missing))
    try:
        port = int(environment.get("BOOST_GATEWAY_CANARY_PORT", "9201"))
        timeout_ms = int(
            environment.get("BOOST_GATEWAY_CANARY_TIMEOUT_MS", str(DEFAULT_TIMEOUT_MS))
        )
    except ValueError as exc:
        raise CanaryError("canary port and timeout must be integers") from exc
    return validate_config(
        CanaryConfig(
            host=environment["BOOST_GATEWAY_CANARY_HOST"],
            port=port,
            user_a=environment["BOOST_GATEWAY_CANARY_USER_A"],
            user_b=environment["BOOST_GATEWAY_CANARY_USER_B"],
            token_a=environment["BOOST_GATEWAY_CANARY_TOKEN_A"],
            token_b=environment["BOOST_GATEWAY_CANARY_TOKEN_B"],
            alertmanager_url=environment["BOOST_GATEWAY_CANARY_ALERTMANAGER_URL"],
            timeout_ms=timeout_ms,
        )
    )


def config_from_environment() -> CanaryConfig:
    return config_from_mapping(dict(os.environ))


def load_environment_file(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise CanaryError(f"environment file must be a regular non-symlink: {path}")
    metadata = path.stat()
    if metadata.st_uid not in {0, os.geteuid()} or metadata.st_mode & 0o077:
        raise CanaryError("environment file must be owner-controlled and mode 0600")
    if metadata.st_size > 16_384:
        raise CanaryError("environment file exceeds the 16 KiB limit")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise CanaryError(
            f"cannot read environment file: {type(exc).__name__}"
        ) from exc
    environment: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line or raw_line.lstrip().startswith("#"):
            continue
        if "=" not in raw_line:
            raise CanaryError(f"invalid environment entry on line {line_number}")
        name, value = raw_line.split("=", 1)
        if name not in ENVIRONMENT_KEYS:
            raise CanaryError(f"unknown environment key on line {line_number}")
        if name in environment:
            raise CanaryError(f"duplicate environment key on line {line_number}")
        if "\x00" in value:
            raise CanaryError(f"invalid environment value on line {line_number}")
        environment[name] = value
    return environment


def candidate_from_record(path: Path) -> dict[str, str]:
    record = read_json(path)
    tag = record.get("tag")
    commit = record.get("commit")
    deployment_id = record.get("deployment_id")
    image_ids = record.get("image_ids", {})
    runtime_digest = (
        image_ids.get("GATEWAY_IMAGE_ID") if isinstance(image_ids, dict) else None
    )
    if not runtime_digest:
        runtime_digest = record.get("runtime_asset_sha256")
    if not isinstance(tag, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise CanaryError("deployment record has no valid release tag")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise CanaryError("deployment record has no full lowercase commit SHA")
    if not isinstance(deployment_id, str) or not deployment_id:
        raise CanaryError("deployment record has no deployment_id")
    if not isinstance(runtime_digest, str) or not re.fullmatch(
        r"(?:sha256:)?[0-9a-f]{64}", runtime_digest
    ):
        raise CanaryError("deployment record has no valid gateway runtime digest")
    if not runtime_digest.startswith("sha256:"):
        runtime_digest = "sha256:" + runtime_digest
    return {
        "deployment_id": deployment_id,
        "tag": tag,
        "commit": commit,
        "runtime_digest": runtime_digest,
    }


def validate_external_host(
    deployment_record: Path,
    machine_id_path: Path = Path("/etc/machine-id"),
) -> dict[str, str]:
    record = read_json(deployment_record)
    host = record.get("host")
    source_id = host.get("host_id_sha256") if isinstance(host, dict) else None
    if not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{64}", source_id):
        raise CanaryError("deployment record has no valid production host identity")
    if not machine_id_path.is_file() or machine_id_path.is_symlink():
        raise CanaryError("external canary host has no regular /etc/machine-id")
    machine_id = machine_id_path.read_bytes()
    if not machine_id.strip():
        raise CanaryError("external canary host machine-id is empty")
    canary_id = hashlib.sha256(machine_id).hexdigest()
    if canary_id == source_id:
        raise CanaryError("canary must run outside the production service host")
    return {"production_host_id_sha256": source_id, "canary_host_id_sha256": canary_id}


def _result_ok(result: Any, operation: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise StepFailure("protocol_error", f"{operation} returned a non-object result")
    if not result.get("ok"):
        code = result.get("error_code")
        raise StepFailure(
            "sdk_error",
            f"{operation} was rejected",
            code if isinstance(code, int) else None,
        )
    return result


def execute_business_flow(
    config: CanaryConfig,
    client_factory: Callable[[], Any],
    *,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
    sleep: Callable[[float], None] = time.sleep,
    sample_suffix: str | None = None,
) -> list[dict[str, Any]]:
    clients: list[Any] = []
    state: dict[str, Any] = {}
    suffix = sample_suffix or uuid.uuid4().hex[:12]
    room_id = f"canary_{suffix}"[:63]

    def connect(client: Any, label: str) -> None:
        try:
            connected = client.connect(config.host, config.port, config.timeout_ms)
        except (TimeoutError, socket.timeout) as exc:
            raise StepFailure("timeout", f"{label} connection timed out") from exc
        except Exception as exc:
            raise StepFailure(
                "connect_error", f"{label} connection raised {type(exc).__name__}"
            ) from exc
        if not connected:
            raise StepFailure("connect_error", f"{label} connection was rejected")

    def login() -> None:
        alice = client_factory()
        bob = client_factory()
        clients.extend((alice, bob))
        connect(alice, "primary")
        connect(bob, "secondary")
        _result_ok(
            alice.login(config.user_a, config.token_a, config.timeout_ms),
            "primary login",
        )
        _result_ok(
            bob.login(config.user_b, config.token_b, config.timeout_ms),
            "secondary login",
        )
        state.update(alice=alice, bob=bob)

    def room() -> None:
        alice, bob = state["alice"], state["bob"]
        created = _result_ok(
            alice.create_room(room_id, config.timeout_ms), "create room"
        )
        state["room_created"] = True
        if created.get("room_id") not in {None, "", room_id}:
            raise StepFailure(
                "protocol_error", "create room returned an unexpected room identity"
            )
        _result_ok(bob.join_room(room_id, config.timeout_ms), "join room")
        state["room_joined"] = True
        _result_ok(alice.set_ready(True, config.timeout_ms), "primary ready")
        _result_ok(bob.set_ready(True, config.timeout_ms), "secondary ready")

    def battle() -> None:
        alice, bob = state["alice"], state["bob"]
        _result_ok(alice.start_battle(room_id, config.timeout_ms), "start battle")
        sleep(0.2)
        _result_ok(
            alice.send_battle_input("move:10,20", config.timeout_ms),
            "primary battle input",
        )
        _result_ok(
            bob.send_battle_input("move:30,40", config.timeout_ms),
            "secondary battle input",
        )

    def settlement() -> None:
        _result_ok(
            state["alice"].send_battle_input("finish:surrender", config.timeout_ms),
            "battle settlement",
        )

    def leaderboard() -> None:
        alice, bob = state["alice"], state["bob"]
        top = _result_ok(
            alice.leaderboard_top(20, config.timeout_ms), "leaderboard top"
        )
        if "entries" not in str(top.get("body", "")):
            raise StepFailure(
                "protocol_error", "leaderboard top omitted the entries collection"
            )
        _result_ok(
            alice.leaderboard_submit(
                config.user_a, "Canary A", 8_000_000_001, config.timeout_ms
            ),
            "leaderboard submit primary",
        )
        _result_ok(
            bob.leaderboard_submit(
                config.user_b, "Canary B", 8_000_000_000, config.timeout_ms
            ),
            "leaderboard submit secondary",
        )
        rank = _result_ok(
            alice.leaderboard_rank(config.user_a, config.timeout_ms), "leaderboard rank"
        )
        if config.user_a not in str(rank.get("body", "")):
            raise StepFailure(
                "protocol_error", "leaderboard rank omitted the fixed canary identity"
            )

    def reconnect() -> None:
        alice, bob = state["alice"], state["bob"]
        _result_ok(bob.leave_room(room_id, config.timeout_ms), "secondary leave room")
        state["room_joined"] = False
        _result_ok(alice.leave_room(room_id, config.timeout_ms), "primary leave room")
        state["room_created"] = False
        alice.disconnect()
        connect(alice, "primary reconnect")
        _result_ok(
            alice.login(config.user_a, config.token_a, config.timeout_ms),
            "primary relogin",
        )

    operations: tuple[tuple[str, Callable[[], None]], ...] = (
        ("login", login),
        ("room", room),
        ("battle", battle),
        ("settlement", settlement),
        ("leaderboard", leaderboard),
        ("reconnect", reconnect),
    )
    results: list[dict[str, Any]] = []
    failed = False
    try:
        for name, operation in operations:
            if failed:
                results.append(
                    {
                        "name": name,
                        "ok": False,
                        "latency_ms": None,
                        "error_type": "dependency_failure",
                        "sdk_error_code": None,
                    }
                )
                continue
            started = clock_ns()
            try:
                operation()
            except StepFailure as exc:
                elapsed = max(0.0, (clock_ns() - started) / 1_000_000)
                results.append(
                    {
                        "name": name,
                        "ok": False,
                        "latency_ms": round(elapsed, 3),
                        "error_type": exc.error_type,
                        "sdk_error_code": exc.code,
                    }
                )
                failed = True
            except (TimeoutError, socket.timeout):
                elapsed = max(0.0, (clock_ns() - started) / 1_000_000)
                results.append(
                    {
                        "name": name,
                        "ok": False,
                        "latency_ms": round(elapsed, 3),
                        "error_type": "timeout",
                        "sdk_error_code": None,
                    }
                )
                failed = True
            except Exception:
                elapsed = max(0.0, (clock_ns() - started) / 1_000_000)
                results.append(
                    {
                        "name": name,
                        "ok": False,
                        "latency_ms": round(elapsed, 3),
                        "error_type": "internal_error",
                        "sdk_error_code": None,
                    }
                )
                failed = True
            else:
                elapsed = max(0.0, (clock_ns() - started) / 1_000_000)
                results.append(
                    {
                        "name": name,
                        "ok": True,
                        "latency_ms": round(elapsed, 3),
                        "error_type": "none",
                        "sdk_error_code": None,
                    }
                )
    finally:
        if state.get("room_joined") and state.get("bob") is not None:
            try:
                state["bob"].leave_room(room_id, config.timeout_ms)
            except Exception:
                pass
        if state.get("room_created") and state.get("alice") is not None:
            try:
                state["alice"].leave_room(room_id, config.timeout_ms)
            except Exception:
                pass
        for client in clients:
            try:
                client.disconnect()
            except Exception:
                pass
    return results


def deliver_alert(
    alertmanager_url: str,
    *,
    alertname: str,
    candidate: dict[str, str],
    endpoint: str,
    summary: str,
    observed_at: datetime,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    url = validate_alertmanager_url(alertmanager_url)
    safe_deployment = re.sub(r"[^A-Za-z0-9_.-]", "_", candidate["deployment_id"])[-120:]
    body = [
        {
            "labels": {
                "alertname": alertname,
                "severity": "critical",
                "component": "external-business-canary",
                "deployment_id": safe_deployment,
            },
            "annotations": {
                "summary": summary[:240],
                "endpoint": endpoint,
                "tag": candidate["tag"],
                "commit": candidate["commit"],
                "runtime_digest": candidate["runtime_digest"],
            },
            "startsAt": isoformat(observed_at),
            "endsAt": isoformat(observed_at + timedelta(minutes=5)),
            "generatorURL": "https://github.com/HoneyBury/boost_gateway/issues/27",
        }
    ]
    request = urllib.request.Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=10) as response:
            status = int(getattr(response, "status", response.getcode()))
            response.read(4096)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        return {
            "delivered": False,
            "status_code": None,
            "error_type": type(exc).__name__,
        }
    return {"delivered": 200 <= status < 300, "status_code": status, "error_type": None}


def _sample_path(root: Path, observed_at: datetime, sample_id: str) -> Path:
    return root / "samples" / observed_at.strftime("%Y/%m/%d") / f"{sample_id}.json"


def run_once(
    config: CanaryConfig,
    deployment_record: Path,
    evidence_root: Path,
    *,
    client_factory: Callable[[], Any],
    sdk_version: str,
    observed_at: datetime | None = None,
    alert_opener: Callable[..., Any] = urllib.request.urlopen,
    suffix: str | None = None,
) -> dict[str, Any]:
    config = validate_config(config)
    observed = (observed_at or utc_now()).astimezone(UTC)
    sample_suffix = suffix or uuid.uuid4().hex[:12]
    sample_id = observed.strftime("canary-%Y%m%dT%H%M%S") + "-" + sample_suffix
    candidate = candidate_from_record(deployment_record)
    steps = execute_business_flow(config, client_factory, sample_suffix=sample_suffix)
    overall_pass = all(step["ok"] for step in steps)
    alert_delivery: dict[str, Any] | None = None
    incident_path: Path | None = None
    if not overall_pass:
        failed_step = next(step for step in steps if not step["ok"])
        alert_delivery = deliver_alert(
            config.alertmanager_url,
            alertname="BoostGatewayExternalCanaryFailed",
            candidate=candidate,
            endpoint=config.endpoint,
            summary=f"External business canary failed at {failed_step['name']} ({failed_step['error_type']})",
            observed_at=observed,
            opener=alert_opener,
        )
        incident_path = evidence_root / "incidents" / f"{sample_id}.json"
        write_create_only(
            incident_path,
            {
                "schema_version": 1,
                "incident_id": sample_id,
                "created_at": isoformat(observed),
                "issue_url": "https://github.com/HoneyBury/boost_gateway/issues/27",
                "candidate": candidate,
                "endpoint": config.endpoint,
                "failed_step": failed_step,
                "alertmanager_delivery": alert_delivery,
                "secret_material_recorded": False,
            },
        )
    completed = utc_now()
    sample = {
        "schema_version": 1,
        "sample_id": sample_id,
        "scheduled_minute": observed.replace(second=0, microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "started_at": isoformat(observed),
        "completed_at": isoformat(completed),
        "candidate": candidate,
        "endpoint": config.endpoint,
        "sdk_version": sdk_version,
        "synthetic_identity_sha256": [
            sha256_text(config.user_a),
            sha256_text(config.user_b),
        ],
        "fixed_identity_count": 2,
        "steps": steps,
        "overall_pass": overall_pass,
        "alertmanager_delivery": alert_delivery,
        "incident_record": str(incident_path) if incident_path else None,
        "secret_material_recorded": False,
    }
    path = _sample_path(evidence_root, observed, sample_id)
    write_create_only(path, sample)
    sample["sample_path"] = str(path)
    return sample


def _percentile(values: list[float], percentile: float) -> float | None:
    return interpolated_percentile(values, percentile)


def load_maintenance_windows(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    document = read_json(path)
    raw_windows = document.get("windows")
    if not isinstance(raw_windows, list):
        raise CanaryError("maintenance document must contain a windows array")
    result = []
    for index, item in enumerate(raw_windows):
        if not isinstance(item, dict):
            raise CanaryError(f"maintenance window {index} is not an object")
        start, end = parse_time(str(item.get("start"))), parse_time(
            str(item.get("end"))
        )
        if start >= end or not item.get("approved_by") or not item.get("id"):
            raise CanaryError(
                f"maintenance window {index} is not approved or has invalid bounds"
            )
        result.append(
            {
                "id": str(item["id"]),
                "start": start,
                "end": end,
                "approved_by": str(item["approved_by"]),
            }
        )
    return result


def _in_maintenance(moment: datetime, windows: list[dict[str, Any]]) -> bool:
    return any(window["start"] <= moment < window["end"] for window in windows)


def _iter_minutes(start: datetime, end: datetime) -> Iterable[datetime]:
    moment = start.replace(second=0, microsecond=0)
    while moment < end:
        yield moment
        moment += timedelta(minutes=1)


def _gap_ranges(missing: list[datetime]) -> list[dict[str, Any]]:
    if not missing:
        return []
    ranges: list[dict[str, Any]] = []
    start = previous = missing[0]
    for moment in missing[1:]:
        if moment != previous + timedelta(minutes=1):
            ranges.append(
                {
                    "start": isoformat(start),
                    "end": isoformat(previous + timedelta(minutes=1)),
                    "minutes": int((previous - start).total_seconds() / 60) + 1,
                }
            )
            start = moment
        previous = moment
    ranges.append(
        {
            "start": isoformat(start),
            "end": isoformat(previous + timedelta(minutes=1)),
            "minutes": int((previous - start).total_seconds() / 60) + 1,
        }
    )
    return ranges


def aggregate_samples(
    evidence_root: Path,
    start: datetime,
    end: datetime,
    maintenance_windows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if (
        start.second
        or start.microsecond
        or end.second
        or end.microsecond
        or start >= end
    ):
        raise CanaryError("aggregation bounds must be distinct whole UTC minutes")
    windows = maintenance_windows or []
    expected = list(_iter_minutes(start, end))
    by_minute: dict[datetime, dict[str, Any]] = {}
    invalid_samples: list[dict[str, str]] = []
    for path in sorted((evidence_root / "samples").glob("**/*.json")):
        try:
            sample = read_json(path)
            minute = parse_time(str(sample["scheduled_minute"]))
            if not start <= minute < end:
                continue
            if minute in by_minute:
                invalid_samples.append(
                    {"path": str(path), "reason": "duplicate_scheduled_minute"}
                )
                continue
            names = tuple(step.get("name") for step in sample.get("steps", []))
            if (
                names != REQUIRED_STEPS
                or sample.get("secret_material_recorded") is not False
            ):
                invalid_samples.append(
                    {"path": str(path), "reason": "invalid_sample_contract"}
                )
                continue
            by_minute[minute] = sample
        except (CanaryError, KeyError, TypeError) as exc:
            invalid_samples.append({"path": str(path), "reason": type(exc).__name__})
    missing = [moment for moment in expected if moment not in by_minute]
    nonmaintenance_expected = [
        moment for moment in expected if not _in_maintenance(moment, windows)
    ]
    successful = [
        moment
        for moment, sample in by_minute.items()
        if sample.get("overall_pass") is True
    ]
    successful_nonmaintenance = [
        moment for moment in successful if not _in_maintenance(moment, windows)
    ]
    missing_nonmaintenance = [
        moment for moment in missing if not _in_maintenance(moment, windows)
    ]
    candidates = {
        json.dumps(sample.get("candidate"), sort_keys=True, separators=(",", ":"))
        for sample in by_minute.values()
    }
    endpoints = {sample.get("endpoint") for sample in by_minute.values()}
    latency: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_STEPS:
        values = [
            float(step["latency_ms"])
            for sample in by_minute.values()
            for step in sample["steps"]
            if step["name"] == name
            and step["ok"]
            and isinstance(step.get("latency_ms"), (int, float))
        ]
        latency[name] = {
            "successful_samples": len(values),
            "p50_ms": _percentile(values, 0.50),
            "p99_ms": _percentile(values, 0.99),
        }
    expected_count = len(expected)
    nonmaintenance_count = len(nonmaintenance_expected)
    recorded_success_count = sum(
        sample.get("overall_pass") is True for sample in by_minute.values()
    )
    report = {
        "schema_version": 1,
        "period": {
            "start": isoformat(start),
            "end": isoformat(end),
            "duration_seconds": int((end - start).total_seconds()),
        },
        "candidate": (
            json.loads(next(iter(candidates))) if len(candidates) == 1 else None
        ),
        "endpoint": next(iter(endpoints)) if len(endpoints) == 1 else None,
        "candidate_consistent": len(candidates) == 1 and len(endpoints) == 1,
        "expected_samples": expected_count,
        "recorded_samples": len(by_minute),
        "successful_samples": recorded_success_count,
        "failed_samples": len(by_minute) - recorded_success_count,
        "coverage_rate": len(by_minute) / expected_count if expected_count else 0.0,
        "recorded_success_rate": (
            recorded_success_count / len(by_minute) if by_minute else 0.0
        ),
        "availability_including_approved_maintenance": (
            len(successful) / expected_count if expected_count else 0.0
        ),
        "availability_excluding_approved_maintenance": (
            len(successful_nonmaintenance) / nonmaintenance_count
            if nonmaintenance_count
            else 0.0
        ),
        "maintenance_minutes": expected_count - nonmaintenance_count,
        "maintenance_windows": [
            {
                "id": window["id"],
                "start": isoformat(window["start"]),
                "end": isoformat(window["end"]),
                "approved_by": window["approved_by"],
            }
            for window in windows
        ],
        "latency": latency,
        "gaps": _gap_ranges(missing),
        "nonmaintenance_gaps": _gap_ranges(missing_nonmaintenance),
        "max_gap_minutes": max(
            (item["minutes"] for item in _gap_ranges(missing)), default=0
        ),
        "max_nonmaintenance_gap_minutes": max(
            (item["minutes"] for item in _gap_ranges(missing_nonmaintenance)), default=0
        ),
        "invalid_samples": invalid_samples,
        "secret_material_recorded": False,
    }
    report["overall_pass"] = (
        report["candidate_consistent"]
        and not invalid_samples
        and report["coverage_rate"] >= 0.999
        and report["availability_including_approved_maintenance"] >= 0.999
        and report["max_nonmaintenance_gap_minutes"] <= 2
    )
    return report


def aggregate_window(
    evidence_root: Path,
    window: str,
    end: datetime,
    maintenance_path: Path | None,
    output: Path | None = None,
) -> dict[str, Any]:
    durations = {"72h": timedelta(hours=72), "30d": timedelta(days=30)}
    if window not in durations:
        raise CanaryError("window must be 72h or 30d")
    end = end.astimezone(UTC).replace(second=0, microsecond=0)
    report = aggregate_samples(
        evidence_root,
        end - durations[window],
        end,
        load_maintenance_windows(maintenance_path),
    )
    report["window"] = window
    report["generated_at"] = isoformat(utc_now())
    destination = (
        output
        or evidence_root
        / "aggregates"
        / f"{window}-{end.strftime('%Y%m%dT%H%MZ')}.json"
    )
    write_create_only(destination, report)
    report["report_path"] = str(destination)
    return report


def watchdog(
    config: CanaryConfig,
    deployment_record: Path,
    evidence_root: Path,
    *,
    observed_at: datetime | None = None,
    max_age_seconds: int = 130,
    alert_opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    observed = (observed_at or utc_now()).astimezone(UTC)
    samples: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path in (evidence_root / "samples").glob("**/*.json"):
        try:
            sample = read_json(path)
            samples.append((parse_time(str(sample["started_at"])), path, sample))
        except (CanaryError, KeyError):
            continue
    latest = max(samples, default=None, key=lambda item: item[0])
    age = None if latest is None else max(0.0, (observed - latest[0]).total_seconds())
    if age is not None and age <= max_age_seconds:
        sample = latest[2]
        previous_delivery = sample.get("alertmanager_delivery")
        if sample.get("overall_pass") is False and (
            not isinstance(previous_delivery, dict)
            or previous_delivery.get("delivered") is not True
        ):
            source_id = re.sub(
                r"[^A-Za-z0-9_.-]", "_", str(sample.get("sample_id", "unknown"))
            )[:160]
            for path in (evidence_root / "incidents").glob(f"retry-{source_id}-*.json"):
                try:
                    incident = read_json(path)
                    if (
                        incident.get("alertmanager_delivery", {}).get("delivered")
                        is True
                    ):
                        return {
                            "overall_pass": True,
                            "latest_sample": str(latest[1]),
                            "age_seconds": age,
                            "alertmanager_delivery": {
                                "delivered": True,
                                "status_code": None,
                                "error_type": None,
                                "deduplicated": True,
                            },
                            "incident_record": str(path),
                        }
                except CanaryError:
                    continue
            candidate = candidate_from_record(deployment_record)
            delivery = deliver_alert(
                config.alertmanager_url,
                alertname="BoostGatewayExternalCanaryFailed",
                candidate=candidate,
                endpoint=config.endpoint,
                summary="Retrying Alertmanager delivery for a failed external business canary sample",
                observed_at=observed,
                opener=alert_opener,
            )
            incident_path = (
                evidence_root
                / "incidents"
                / f"retry-{source_id}-{observed.strftime('%Y%m%dT%H%M%S')}.json"
            )
            write_create_only(
                incident_path,
                {
                    "schema_version": 1,
                    "incident_id": incident_path.stem,
                    "created_at": isoformat(observed),
                    "issue_url": "https://github.com/HoneyBury/boost_gateway/issues/27",
                    "candidate": candidate,
                    "endpoint": config.endpoint,
                    "source_sample": str(latest[1]),
                    "alertmanager_delivery": delivery,
                    "secret_material_recorded": False,
                },
            )
            return {
                "overall_pass": delivery["delivered"],
                "latest_sample": str(latest[1]),
                "age_seconds": age,
                "alertmanager_delivery": delivery,
                "incident_record": str(incident_path),
            }
        return {
            "overall_pass": True,
            "latest_sample": str(latest[1]),
            "age_seconds": age,
            "alertmanager_delivery": None,
        }
    candidate = candidate_from_record(deployment_record)
    stale_key = "no-samples" if latest is None else latest[0].strftime("%Y%m%dT%H%M%S")
    delivered_for_stale = False
    for path in (evidence_root / "incidents").glob(f"silent-{stale_key}-*.json"):
        try:
            incident = read_json(path)
            if incident.get("alertmanager_delivery", {}).get("delivered") is True:
                delivered_for_stale = True
                break
        except CanaryError:
            continue
    delivery = {
        "delivered": True,
        "status_code": None,
        "error_type": None,
        "deduplicated": True,
    }
    incident_path = None
    if not delivered_for_stale:
        delivery = deliver_alert(
            config.alertmanager_url,
            alertname="BoostGatewayExternalCanarySilent",
            candidate=candidate,
            endpoint=config.endpoint,
            summary="External business canary has stopped producing per-minute evidence",
            observed_at=observed,
            opener=alert_opener,
        )
        incident_path = (
            evidence_root
            / "incidents"
            / f"silent-{stale_key}-{observed.strftime('%Y%m%dT%H%M%S')}.json"
        )
        write_create_only(
            incident_path,
            {
                "schema_version": 1,
                "incident_id": incident_path.stem,
                "created_at": isoformat(observed),
                "issue_url": "https://github.com/HoneyBury/boost_gateway/issues/27",
                "candidate": candidate,
                "endpoint": config.endpoint,
                "latest_sample": str(latest[1]) if latest else None,
                "age_seconds": age,
                "alertmanager_delivery": delivery,
                "secret_material_recorded": False,
            },
        )
    return {
        "overall_pass": False,
        "latest_sample": str(latest[1]) if latest else None,
        "age_seconds": age,
        "alertmanager_delivery": delivery,
        "incident_record": str(incident_path) if incident_path else None,
    }


def load_sdk() -> tuple[Callable[[], Any], str]:
    try:
        import boost_gateway_sdk as sdk  # type: ignore[import-not-found]
    except Exception as exc:
        raise CanaryError(
            f"cannot load the released Python SDK: {type(exc).__name__}: {exc}"
        ) from exc
    version = str(sdk.assert_compatible_version())
    return sdk.SdkClient, version


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument(
        "--deployment-record", type=Path, default=DEFAULT_DEPLOYMENT_RECORD
    )
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--machine-id-path", type=Path, default=Path("/etc/machine-id"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "validate", help="validate configuration, candidate binding and released SDK"
    )
    subparsers.add_parser("run", help="run one full external SDK canary sample")
    watchdog_parser = subparsers.add_parser(
        "watchdog", help="alert when samples become stale"
    )
    watchdog_parser.add_argument("--max-age-seconds", type=int, default=130)
    watchdog_parser.add_argument("--initial-delay-seconds", type=int, default=0)
    aggregate_parser = subparsers.add_parser(
        "aggregate", help="create a 72-hour or 30-day report"
    )
    aggregate_parser.add_argument("--window", choices=("72h", "30d"), required=True)
    aggregate_parser.add_argument(
        "--end", help="exclusive whole-minute UTC bound; default is current minute"
    )
    aggregate_parser.add_argument("--maintenance-windows", type=Path)
    aggregate_parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "aggregate":
            end = parse_time(args.end) if args.end else utc_now()
            result = aggregate_window(
                args.evidence_root,
                args.window,
                end,
                args.maintenance_windows,
                args.output,
            )
        else:
            environment = (
                load_environment_file(args.environment_file)
                if args.environment_file
                else dict(os.environ)
            )
            config = config_from_mapping(environment)
            if args.command == "validate":
                _, sdk_version = load_sdk()
                result = {
                    "overall_pass": True,
                    "candidate": candidate_from_record(args.deployment_record),
                    "host_boundary": validate_external_host(
                        args.deployment_record, args.machine_id_path
                    ),
                    "endpoint": config.endpoint,
                    "sdk_version": sdk_version,
                    "secret_material_recorded": False,
                }
            elif args.command == "watchdog":
                if not 60 <= args.max_age_seconds <= 600:
                    raise CanaryError(
                        "watchdog max age must be between 60 and 600 seconds"
                    )
                if not 0 <= args.initial_delay_seconds <= 50:
                    raise CanaryError(
                        "watchdog initial delay must be between 0 and 50 seconds"
                    )
                time.sleep(args.initial_delay_seconds)
                result = watchdog(
                    config,
                    args.deployment_record,
                    args.evidence_root,
                    max_age_seconds=args.max_age_seconds,
                )
            else:
                factory, sdk_version = load_sdk()
                result = run_once(
                    config,
                    args.deployment_record,
                    args.evidence_root,
                    client_factory=factory,
                    sdk_version=sdk_version,
                )
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("overall_pass") else 1
    except CanaryError as exc:
        print(f"external business canary: FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
