"""Bounded, secret-free evidence contracts for independent heartbeat admission."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import ssl
import stat
from datetime import UTC, datetime
from pathlib import Path

try:
    from scripts.lib.external_deadman import DeadmanError, load_ping_uuid
except ModuleNotFoundError:  # installed flat layout
    from external_deadman import DeadmanError, load_ping_uuid

MAX_JSON = 262144
HEX = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"(?i)[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,180}$")
MESSAGE = re.compile(r"^<[^\s<>@]+@[^\s<>@]+>$")
POLICY = {"schedule": "*-*-* *:*:45", "tz": "UTC", "grace": 90,
          "methods": "POST", "manual_resume": False, "filter_http_body": False}
SNAPSHOT_KEYS = {"schema_version", "provider", "provider_unique_key",
                 "check_identity_sha256", "observed_at", "status", "source",
                 "secret_material_recorded", *POLICY}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DeadmanError(message)


def utcnow() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime | None = None) -> str:
    return (value or utcnow()).astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def calendar_timestamp(value: datetime) -> str:
    """systemd OnCalendar accepts a spaced date/time and explicit timezone, not ISO T/Z."""
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def instant(value: object) -> datetime:
    require(isinstance(value, str), "invalid UTC timestamp")
    require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value)), "invalid UTC timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise DeadmanError("invalid UTC timestamp") from None


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def secure_read(path: Path, maximum: int = MAX_JSON, *, secret: bool = False, owner_uid: int | None = None) -> bytes:
    require(path.is_absolute(), "input must be an absolute path")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "input must be a regular single-link file")
        require(info.st_uid in {0, os.geteuid(), owner_uid}, "input owner is not trusted")
        require(not info.st_mode & (0o077 if secret else 0o022), "unsafe input permissions")
        require(info.st_size <= maximum, "input exceeds size limit")
        data = os.read(descriptor, maximum + 1)
        require(len(data) <= maximum, "input exceeds size limit")
        return data
    finally:
        os.close(descriptor)


def read_json(path: Path, *, owner_uid: int | None = None) -> dict:
    try:
        result = json.loads(secure_read(path, owner_uid=owner_uid))
    except (ValueError, UnicodeError):
        raise DeadmanError("invalid evidence JSON") from None
    require(isinstance(result, dict), "evidence must be an object")
    return result


def nonsecret(value: object, location: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            require(isinstance(key, str), "invalid evidence key")
            require(key not in {"uuid", "ping_url", "api_key", "channels", "recipient", "authorization"},
                    "secret-bearing evidence key")
            nonsecret(child, (*location, key))
    elif isinstance(value, list):
        for child in value:
            nonsecret(child, location)
    elif isinstance(value, str):
        endpoint = location == ("endpoint",) and value == "tcp://100.65.71.117:9201"
        require(UUID.search(value) is None and ("://" not in value or endpoint), "secret-bearing evidence value")
        if "@" in value:
            require(location in {("deliveries", "down", "message_id"),
                                 ("deliveries", "up", "message_id")}
                    and bool(MESSAGE.fullmatch(value)), "email outside delivery Message-ID")


def create(path: Path, value: dict) -> None:
    """Atomically publish a complete record, refusing existing targets."""
    nonsecret(value)
    require(path.is_absolute() and bool(NAME.fullmatch(path.name)), "invalid evidence destination")
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp = f".record-{os.urandom(12).hex()}"
    created = False
    try:
        metadata = os.fstat(parent)
        require(metadata.st_uid in {0, os.geteuid()} and not metadata.st_mode & 0o022,
                "unsafe evidence directory")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=parent)
        created = True
        with os.fdopen(descriptor, "wb") as output:
            output.write((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
        os.link(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.fsync(parent)
    finally:
        if created:
            os.unlink(temp, dir_fd=parent)
            os.fsync(parent)
        os.close(parent)


def snapshot(credential: Path, api_key: Path) -> dict:
    """Read one check using a local read-only key, never persist the raw body."""
    ping_uuid = load_ping_uuid(credential)
    key = secure_read(api_key, 256, secret=True).decode("ascii").strip()
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{20,200}", key)), "invalid API credential")
    connection = http.client.HTTPSConnection("healthchecks.io", timeout=10, context=ssl.create_default_context())
    try:
        connection.request("GET", f"/api/v3/checks/{ping_uuid}", headers={"X-Api-Key": key})
        response = connection.getresponse()
        raw = response.read(MAX_JSON + 1)
        require(response.status == 200 and len(raw) <= MAX_JSON, "provider snapshot request failed")
        data = json.loads(raw)
    except Exception:
        raise DeadmanError("provider snapshot request failed") from None
    finally:
        connection.close()
    require(isinstance(data, dict) and not {"uuid", "ping_url", "channels"} & data.keys(),
            "provider API key must be read-only")
    result = {"schema_version": 1, "provider": "healthchecks.io",
              "provider_unique_key": data.get("unique_key"),
              "check_identity_sha256": hashlib.sha256(ping_uuid.encode()).hexdigest(),
              "observed_at": stamp(), "status": data.get("status"),
              "source": "readonly-management-api-v3", "secret_material_recorded": False,
              **{field: data.get(field) for field in POLICY}}
    validate_snapshot(result, statuses={"new", "up", "down", "grace"})
    return result


def validate_snapshot(value: dict, *, statuses: set[str], now: datetime | None = None,
                      max_age: int = 300) -> None:
    require(set(value) == SNAPSHOT_KEYS, "provider snapshot schema mismatch")
    require(value["schema_version"] == 1 and value["provider"] == "healthchecks.io"
            and value["source"] == "readonly-management-api-v3"
            and value["secret_material_recorded"] is False, "invalid provider snapshot provenance")
    for key, expected in POLICY.items():
        require(type(value[key]) is type(expected) and value[key] == expected, "provider policy mismatch")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", str(value["provider_unique_key"]))), "invalid provider identity")
    require(bool(HEX.fullmatch(str(value["check_identity_sha256"]))), "invalid check digest")
    require(value["status"] in statuses, "provider status is not admitted")
    age = ((now or utcnow()) - instant(value["observed_at"])).total_seconds()
    require(0 <= age <= max_age, "provider snapshot is stale or future-dated")
    nonsecret(value)


def validate_attestation(value: dict, *, now: datetime | None = None) -> None:
    require(set(value) == {"schema_version", "attestation_id", "created_at", "create_only",
            "overall_pass", "provider", "subject", "drill", "deliveries", "artifacts",
            "secret_material_recorded"}, "attestation schema mismatch")
    nonsecret(value)
    require(value["schema_version"] == 1 and value["create_only"] is True
            and value["overall_pass"] is True and value["secret_material_recorded"] is False
            and value["provider"] == "healthchecks.io", "attestation is not passing")
    require(bool(NAME.fullmatch(str(value["attestation_id"]))), "invalid drill ID")
    subject = value["subject"]
    require(set(subject) == {"canary_host_id_sha256", "check_identity_sha256", "reporter_sha256",
            "service_unit_sha256", "watchdog_dropin_sha256", "provider_contract_sha256",
            "candidate_record_sha256", "installation_sha256"}, "attestation subject schema mismatch")
    require(all(HEX.fullmatch(str(item)) for item in subject.values()), "invalid subject digest")
    drill = value["drill"]
    require(set(drill) == {"failure_mode", "armed_at", "heartbeat_stopped_at", "provider_down_at",
            "rearmed_at", "provider_up_at", "provider_unique_key", "final_status"}, "invalid drill schema")
    require(drill["failure_mode"] == "missing-heartbeat" and drill["final_status"] == "up",
            "missing-heartbeat recovery not proven")
    times = [instant(drill[key]) for key in ("armed_at", "heartbeat_stopped_at", "provider_down_at",
                                            "rearmed_at", "provider_up_at")]
    require(times == sorted(times) and times[1] < times[2] < times[3] < times[4], "invalid drill ordering")
    require((times[2] - times[1]).total_seconds() >= 90, "missing-heartbeat grace was not exercised")
    created = instant(value["created_at"])
    require(times[-1] <= created <= (now or utcnow()), "invalid attestation time")
    require(set(value["deliveries"]) == {"down", "up"}, "both target mailbox deliveries are required")
    ids = []
    for state, transition in (("down", times[2]), ("up", times[4])):
        delivery = value["deliveries"][state]
        require(set(delivery) == {"message_id", "observed_at"}, "invalid delivery schema")
        require(bool(MESSAGE.fullmatch(str(delivery["message_id"]))), "invalid Message-ID")
        require(transition <= instant(delivery["observed_at"]) <= created, "invalid delivery time")
        ids.append(delivery["message_id"])
    require(ids[0] != ids[1], "delivery Message-IDs must be distinct")
    roles = {"before", "down", "up", "armed", "stopped", "rearmed", "success"}
    require(isinstance(value["artifacts"], list) and len(value["artifacts"]) == len(roles), "missing drill artifacts")
    require({entry["role"] for entry in value["artifacts"]} == roles, "invalid drill artifact roles")
    for entry in value["artifacts"]:
        require(set(entry) == {"role", "basename", "size", "sha256"}
                and bool(NAME.fullmatch(str(entry["basename"])))
                and type(entry["size"]) is int and 0 < entry["size"] <= MAX_JSON
                and bool(HEX.fullmatch(str(entry["sha256"]))), "invalid drill artifact")
