"""Fail-closed Healthchecks.io dead-man signal reporting primitives."""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import secrets
import socket
import ssl
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import AbstractSet, Callable, MutableMapping

PROVIDER_NAME = "healthchecks.io"
PROVIDER_HOST = "hc-ping.com"
PROVIDER_PORT = 443
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 15.0
MAX_CREDENTIAL_BYTES = 64
MAX_RESPONSE_BYTES = 64
RECEIPT_MODE = 0o640
SAFE_ROOT_CREDENTIAL_MODES = frozenset({0o400, 0o440, 0o600})
SAFE_USER_CREDENTIAL_MODES = frozenset({0o400, 0o600})
ACCEPTED_RESPONSE_BODIES = frozenset({b"OK", b"OK\n"})
STATUS_SUFFIXES = {"success": "", "failure": "/fail"}
PROXY_ENVIRONMENT_NAMES = frozenset(
    {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY", "NO_PROXY"}
)
RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "provider",
        "signal_status",
        "observed_at",
        "check_identity_sha256",
        "provider_http_status",
        "delivery_accepted",
        "transport_error",
        "overall_pass",
        "create_only",
        "secret_material_recorded",
    }
)
DELIVERY_ERROR_CODES = frozenset(
    {
        "timeout",
        "tls",
        "network",
        "transport",
        "invalid_response",
        "unexpected_http_status",
        "unexpected_response_body",
    }
)
CANONICAL_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
EMBEDDED_UUID_RE = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f])"
)
URL_RE = re.compile(r"(?i)\b(?:https?|smtp)://")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class DeadmanError(RuntimeError):
    """Raised when a dead-man signal cannot be reported and evidenced safely."""


@dataclass(frozen=True)
class TransportResult:
    """The bounded, in-memory result of one direct provider request."""

    status_code: int
    body: bytes


def _safe_owner_uids(
    allowed_owner_uids: AbstractSet[int] | None,
) -> frozenset[int]:
    if allowed_owner_uids is None:
        return frozenset({0, os.geteuid()})
    owners = frozenset(allowed_owner_uids)
    if not owners or any(type(owner) is not int or owner < 0 for owner in owners):
        raise DeadmanError("the secure owner policy is invalid")
    return owners


def _require_absolute_file_path(path: Path, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.name in {"", ".", ".."}:
        raise DeadmanError(f"{label} must be an absolute file path")
    return candidate


def _read_bounded(descriptor: int, maximum: int) -> bytes:
    blocks: list[bytes] = []
    remaining = maximum + 1
    while remaining > 0:
        block = os.read(descriptor, remaining)
        if not block:
            break
        blocks.append(block)
        remaining -= len(block)
    return b"".join(blocks)


def load_ping_uuid(
    credential_path: Path,
    *,
    allowed_owner_uids: AbstractSet[int] | None = None,
) -> str:
    """Read a canonical UUID from a secure systemd credential file."""

    path = _require_absolute_file_path(credential_path, "credential")
    if not hasattr(os, "O_NOFOLLOW"):
        raise DeadmanError("this platform cannot safely open the credential")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise DeadmanError("the credential cannot be opened safely") from None

        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise DeadmanError("the credential must be a regular file")
        if metadata.st_nlink != 1:
            raise DeadmanError("the credential must have exactly one hard link")
        if metadata.st_uid not in _safe_owner_uids(allowed_owner_uids):
            raise DeadmanError("the credential owner is not trusted")

        mode = stat.S_IMODE(metadata.st_mode)
        allowed_modes = (
            SAFE_ROOT_CREDENTIAL_MODES
            if metadata.st_uid == 0
            else SAFE_USER_CREDENTIAL_MODES
        )
        if mode not in allowed_modes:
            raise DeadmanError("the credential mode is not safe")
        if metadata.st_size < 1 or metadata.st_size > MAX_CREDENTIAL_BYTES:
            raise DeadmanError("the credential format is invalid")

        try:
            path_metadata = os.stat(path, follow_symlinks=False)
        except OSError:
            raise DeadmanError("the credential path changed while opening") from None
        if not stat.S_ISREG(path_metadata.st_mode) or (
            path_metadata.st_dev,
            path_metadata.st_ino,
        ) != (metadata.st_dev, metadata.st_ino):
            raise DeadmanError("the credential path changed while opening")

        raw_value = _read_bounded(descriptor, MAX_CREDENTIAL_BYTES)
    finally:
        if descriptor is not None:
            os.close(descriptor)

    if len(raw_value) > MAX_CREDENTIAL_BYTES:
        raise DeadmanError("the credential format is invalid")
    if raw_value.endswith(b"\n"):
        raw_value = raw_value[:-1]
    if not raw_value or b"\n" in raw_value or b"\r" in raw_value:
        raise DeadmanError("the credential format is invalid")
    try:
        value = raw_value.decode("ascii")
    except UnicodeDecodeError:
        raise DeadmanError("the credential format is invalid") from None
    if CANONICAL_UUID_RE.fullmatch(value) is None:
        raise DeadmanError("the credential format is invalid")
    return value


def validate_timeout_seconds(value: float) -> float:
    if isinstance(value, bool):
        raise DeadmanError("timeout must be a number greater than zero")
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise DeadmanError("timeout must be a number greater than zero") from None
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise DeadmanError(
            f"timeout must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g} seconds"
        )
    return timeout


def clear_proxy_environment(environ: MutableMapping[str, str]) -> None:
    for name in tuple(environ):
        if name.upper() in PROXY_ENVIRONMENT_NAMES:
            environ.pop(name, None)


def _https_post(request_path: str, timeout_seconds: float) -> TransportResult:
    context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    connection = http.client.HTTPSConnection(
        PROVIDER_HOST,
        PROVIDER_PORT,
        timeout=timeout_seconds,
        context=context,
    )
    try:
        connection.request(
            "POST",
            request_path,
            body=b"",
            headers={
                "Accept": "text/plain",
                "Connection": "close",
                "Content-Length": "0",
                "User-Agent": "boost-gateway-external-deadman/1",
            },
        )
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        return TransportResult(status_code=response.status, body=body)
    finally:
        connection.close()


def _classify_transport_error(error: Exception) -> str:
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(error, ssl.SSLError):
        return "tls"
    if isinstance(error, OSError):
        return "network"
    return "transport"


def _utc_timestamp(now: Callable[[], datetime]) -> str:
    try:
        value = now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError
        offset = value.utcoffset()
        if offset is None:
            raise ValueError
        return value.astimezone(UTC).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
    except Exception:
        raise DeadmanError("the receipt clock did not return an aware timestamp") from None


def _assert_nonsecret_values(
    value: object,
    *,
    forbidden_values: tuple[str, ...] = (),
) -> None:
    if isinstance(value, dict):
        for nested in value.values():
            _assert_nonsecret_values(nested, forbidden_values=forbidden_values)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _assert_nonsecret_values(nested, forbidden_values=forbidden_values)
        return
    if not isinstance(value, str):
        return

    folded = value.casefold()
    if any(secret and secret.casefold() in folded for secret in forbidden_values):
        raise DeadmanError("receipt values contain forbidden secret material")
    if EMBEDDED_UUID_RE.search(value) is not None or URL_RE.search(value) is not None:
        raise DeadmanError("receipt values contain forbidden secret material")


def _validate_receipt(
    receipt: dict[str, object],
    *,
    forbidden_values: tuple[str, ...],
) -> None:
    if frozenset(receipt) != RECEIPT_KEYS:
        raise DeadmanError("receipt schema is invalid")
    if (type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1
            or receipt["provider"] != PROVIDER_NAME
            or receipt["signal_status"] not in STATUS_SUFFIXES):
        raise DeadmanError("receipt schema is invalid")
    observed_at = receipt["observed_at"]
    identity_hash = receipt["check_identity_sha256"]
    if not isinstance(observed_at, str) or TIMESTAMP_RE.fullmatch(observed_at) is None:
        raise DeadmanError("receipt schema is invalid")
    if not isinstance(identity_hash, str) or SHA256_RE.fullmatch(identity_hash) is None:
        raise DeadmanError("receipt schema is invalid")
    status_code = receipt["provider_http_status"]
    if status_code is not None and (
        type(status_code) is not int or status_code < 100 or status_code > 599
    ):
        raise DeadmanError("receipt schema is invalid")
    accepted = receipt["delivery_accepted"]
    overall_pass = receipt["overall_pass"]
    if type(accepted) is not bool or type(overall_pass) is not bool or accepted is not overall_pass:
        raise DeadmanError("receipt schema is invalid")
    delivery_error = receipt["transport_error"]
    if accepted:
        if status_code != 200 or delivery_error is not None:
            raise DeadmanError("receipt schema is invalid")
    elif delivery_error not in DELIVERY_ERROR_CODES:
        raise DeadmanError("receipt schema is invalid")
    if receipt["create_only"] is not True or receipt["secret_material_recorded"] is not False:
        raise DeadmanError("receipt schema is invalid")
    _assert_nonsecret_values(receipt, forbidden_values=forbidden_values)


def _open_receipt_directory(
    receipt_path: Path,
    *,
    allowed_owner_uids: AbstractSet[int] | None,
) -> tuple[int, str]:
    path = _require_absolute_file_path(receipt_path, "receipt")
    if not hasattr(os, "O_NOFOLLOW"):
        raise DeadmanError("this platform cannot safely create the receipt")
    flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        directory = os.open(path.parent, flags)
    except OSError:
        raise DeadmanError("the receipt directory cannot be opened safely") from None
    try:
        metadata = os.fstat(directory)
        if not stat.S_ISDIR(metadata.st_mode):
            raise DeadmanError("the receipt parent must be a directory")
        if metadata.st_uid not in _safe_owner_uids(allowed_owner_uids):
            raise DeadmanError("the receipt directory owner is not trusted")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise DeadmanError("the receipt directory mode is not safe")
        try:
            os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError:
            raise DeadmanError("the receipt target cannot be inspected safely") from None
        else:
            raise DeadmanError("the create-only receipt already exists")
        return directory, path.name
    except Exception:
        os.close(directory)
        raise


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _write_create_only_receipt(
    directory: int,
    final_name: str,
    receipt: dict[str, object],
    *,
    forbidden_values: tuple[str, ...],
) -> None:
    _validate_receipt(receipt, forbidden_values=forbidden_values)
    payload = (
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("ascii")
    for secret in forbidden_values:
        if secret and secret.encode("ascii") in payload:
            raise DeadmanError("serialized receipt contains forbidden secret material")

    temporary_name = f".external-deadman-{secrets.token_hex(12)}.tmp"
    temporary_descriptor: int | None = None
    temporary_exists = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        temporary_descriptor = os.open(temporary_name, flags, RECEIPT_MODE, dir_fd=directory)
        temporary_exists = True
        os.fchmod(temporary_descriptor, RECEIPT_MODE)
        _write_all(temporary_descriptor, payload)
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        try:
            os.link(
                temporary_name,
                final_name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
        except FileExistsError:
            raise DeadmanError("the create-only receipt already exists") from None
        os.fsync(directory)
        os.unlink(temporary_name, dir_fd=directory)
        temporary_exists = False
        os.fsync(directory)
    except DeadmanError:
        raise
    except OSError:
        raise DeadmanError("the create-only receipt could not be persisted") from None
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if temporary_exists:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except OSError:
                pass


def _evaluate_transport_result(
    result: object,
) -> tuple[int | None, bool, str | None]:
    if not isinstance(result, TransportResult):
        return None, False, "invalid_response"
    status_code = result.status_code
    if type(status_code) is not int or status_code < 100 or status_code > 599:
        return None, False, "invalid_response"
    if not isinstance(result.body, bytes) or len(result.body) > MAX_RESPONSE_BYTES:
        return status_code, False, "invalid_response"
    if status_code != 200:
        return status_code, False, "unexpected_http_status"
    if result.body not in ACCEPTED_RESPONSE_BODIES:
        return status_code, False, "unexpected_response_body"
    return status_code, True, None


def report_deadman_signal(
    *,
    status: str,
    credential_path: Path,
    receipt_path: Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    transport: Callable[[str, float], TransportResult] | None = None,
    now: Callable[[], datetime] | None = None,
    environ: MutableMapping[str, str] | None = None,
    allowed_credential_owner_uids: AbstractSet[int] | None = None,
    allowed_receipt_owner_uids: AbstractSet[int] | None = None,
) -> dict[str, object]:
    if status not in STATUS_SUFFIXES:
        raise DeadmanError("status must be success or failure")
    timeout = validate_timeout_seconds(timeout_seconds)
    observed_at = _utc_timestamp(now or (lambda: datetime.now(UTC)))
    directory, final_name = _open_receipt_directory(
        receipt_path,
        allowed_owner_uids=allowed_receipt_owner_uids,
    )
    try:
        ping_uuid = load_ping_uuid(
            credential_path,
            allowed_owner_uids=allowed_credential_owner_uids,
        )
        identity_hash = hashlib.sha256(ping_uuid.encode("ascii")).hexdigest()
        environment = os.environ if environ is None else environ
        clear_proxy_environment(environment)
        provider_status: int | None = None
        accepted = False
        delivery_error: str | None
        request_path = f"/{ping_uuid}{STATUS_SUFFIXES[status]}"
        try:
            result = (transport or _https_post)(request_path, timeout)
        except Exception as error:
            delivery_error = _classify_transport_error(error)
        else:
            provider_status, accepted, delivery_error = _evaluate_transport_result(result)

        receipt: dict[str, object] = {
            "schema_version": 1,
            "provider": PROVIDER_NAME,
            "signal_status": status,
            "observed_at": observed_at,
            "check_identity_sha256": identity_hash,
            "provider_http_status": provider_status,
            "delivery_accepted": accepted,
            "transport_error": delivery_error,
            "overall_pass": accepted,
            "create_only": True,
            "secret_material_recorded": False,
        }
        _write_create_only_receipt(
            directory,
            final_name,
            receipt,
            forbidden_values=(ping_uuid,),
        )
    finally:
        os.close(directory)

    if not accepted:
        raise DeadmanError("the provider did not accept the dead-man signal")
    return receipt
