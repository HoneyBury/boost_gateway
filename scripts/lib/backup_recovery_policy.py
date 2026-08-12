#!/usr/bin/env python3
"""Validate the repository-only TODO-0012 backup and Redis candidate contract."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "deploy/operations/backup-recovery-policy.example.json"
DEFAULT_REDIS_PROFILE = ROOT / "env/redis/redis.production-validation.conf"
DEFAULT_SUMMARY = ROOT / "runtime/validation/backup-recovery-policy-summary.json"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

REQUIRED_SOURCE_CONTRACTS = {
    "redis_snapshot": (
        "generated_redis_snapshot",
        "/var/backups/boost-gateway/staging/redis",
    ),
    "host_configuration": ("directory", "/etc/boost-gateway"),
    "deployment_state": ("directory", "/opt/boost-gateway/deployments"),
    "release_state": ("directory", "/opt/boost-gateway/releases"),
    "deployment_transactions": (
        "directory",
        "/var/lib/boost-gateway/deployment-transactions",
    ),
    "operations_evidence": ("directory", "/var/lib/boost-gateway-evidence"),
}
REQUIRED_PERFORMANCE_METRICS = {
    "leaderboard_operation_throughput",
    "leaderboard_operation_p50_latency",
    "leaderboard_operation_p99_latency",
    "redis_cpu",
    "redis_rss",
    "redis_disk_write_bytes",
    "redis_aof_delayed_fsync",
}
REQUIRED_BUSINESS_CHECKS = {
    "redis_ping",
    "leaderboard_seed_exact",
    "leaderboard_submit",
    "leaderboard_top",
    "leaderboard_rank",
    "sdk_full_flow",
}
REQUIRED_ACTIVATION_GATES = {
    "measured_aof_performance_evidence",
    "verified_encryption_recipient",
    "verified_distinct_remote_storage_identity",
    "governed_change_record",
    "rollback_plan",
}
REQUIRED_MANIFEST_LINK_FIELDS = {
    "archive_path",
    "original_link_text",
    "target_source_id",
    "target_relative_path",
    "target_type",
}


class PolicyError(RuntimeError):
    """Raised when a policy input cannot be parsed safely."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PolicyError(f"{label} must be a regular non-symlink file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise PolicyError(f"{label} must be a JSON object")
    return value


def load_redis_directives(path: Path) -> dict[str, list[tuple[str, ...]]]:
    if path.is_symlink() or not path.is_file():
        raise PolicyError(f"Redis profile must be a regular non-symlink file: {path}")
    directives: dict[str, list[tuple[str, ...]]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise PolicyError(f"cannot load Redis profile: {exc}") from exc
    for line_number, raw_line in enumerate(lines, 1):
        try:
            parts = shlex.split(raw_line, comments=True, posix=True)
        except ValueError as exc:
            raise PolicyError(
                f"invalid Redis syntax at line {line_number}: {exc}"
            ) from exc
        if not parts:
            continue
        name = parts[0].lower()
        if len(parts) == 1:
            raise PolicyError(
                f"Redis directive has no value at line {line_number}: {name}"
            )
        directives.setdefault(name, []).append(tuple(parts[1:]))
    return directives


def nested(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def string_set(value: object) -> set[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return set()
    return set(value)


def positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def validate_redis_profile(
    checks: list[dict[str, Any]],
    policy: dict[str, Any],
    profile_path: Path,
) -> str:
    try:
        directives = load_redis_directives(profile_path)
        digest = sha256_file(profile_path)
    except (OSError, PolicyError) as exc:
        add(checks, "redis:profile-load", False, str(exc))
        return ""

    add(checks, "redis:profile-load", True, "Redis candidate profile is readable")
    expected_digest = nested(policy, "redis", "profile_sha256")
    add(
        checks,
        "redis:profile-sha256",
        isinstance(expected_digest, str)
        and SHA256_RE.fullmatch(expected_digest) is not None
        and expected_digest == digest,
        "Redis candidate profile matches its policy digest",
    )

    expected_single = {
        "appendonly": ("yes",),
        "appendfsync": ("everysec",),
        "no-appendfsync-on-rewrite": ("no",),
        "aof-use-rdb-preamble": ("yes",),
        "aof-load-truncated": ("no",),
        "aof-timestamp-enabled": ("yes",),
        "auto-aof-rewrite-percentage": ("100",),
        "auto-aof-rewrite-min-size": ("64mb",),
        "appendfilename": ("appendonly.aof",),
        "appenddirname": ("appendonlydir",),
        "maxmemory-policy": ("noeviction",),
        "stop-writes-on-bgsave-error": ("yes",),
        "rdbcompression": ("yes",),
        "rdbchecksum": ("yes",),
        "dbfilename": ("dump.rdb",),
        "dir": ("/data",),
    }
    for name, expected in expected_single.items():
        add(
            checks,
            f"redis:directive:{name}",
            directives.get(name) == [expected],
            f"Redis {name} is explicitly and uniquely set to {' '.join(expected)}",
        )

    policy_save_rules = nested(policy, "redis", "expected_rdb_save_rules")
    expected_saves = (
        {tuple(rule.split()) for rule in policy_save_rules}
        if isinstance(policy_save_rules, list)
        and policy_save_rules
        and all(
            isinstance(rule, str) and len(rule.split()) == 2
            for rule in policy_save_rules
        )
        else set()
    )
    actual_saves = directives.get("save", [])
    add(
        checks,
        "redis:rdb-save-rules",
        bool(expected_saves)
        and len(actual_saves) == len(expected_saves)
        and set(actual_saves) == expected_saves,
        "Redis RDB save rules exactly match the policy",
    )
    add(
        checks,
        "redis:no-dynamic-includes",
        "include" not in directives and "loadmodule" not in directives,
        "Redis candidate has no dynamic include or module escape",
    )
    add(
        checks,
        "redis:policy-data-directory",
        nested(policy, "redis", "expected_data_directory") == "/data",
        "Policy binds the Redis data directory to /data",
    )
    add(
        checks,
        "redis:policy-eviction",
        nested(policy, "redis", "expected_maxmemory_policy") == "noeviction",
        "Policy rejects silent eviction of recoverable leaderboard state",
    )
    return digest


def validate_performance_contract(
    checks: list[dict[str, Any]], policy: dict[str, Any]
) -> None:
    performance = nested(policy, "redis", "performance_impact")
    activation_state = nested(policy, "activation", "state")
    performance_valid = isinstance(performance, dict) and (
        (
            activation_state == "candidate_only"
            and performance.get("status") == "pending_measurement"
            and performance.get("required_before_activation") is True
        )
        or (
            activation_state == "approved_candidate_pending_host_activation"
            and performance.get("status") == "measured_and_accepted"
            and performance.get("required_before_activation") is False
        )
    )
    add(
        checks,
        "performance:governed-state",
        performance_valid,
        "Performance state matches the candidate or approved-candidate activation phase",
    )
    add(
        checks,
        "performance:modes",
        isinstance(performance, dict)
        and performance.get("baseline_mode") == "rdb_only"
        and performance.get("candidate_mode") == "aof_everysec_plus_rdb",
        "Performance evidence compares RDB-only with AOF everysec plus RDB",
    )
    repetitions = (
        performance.get("minimum_repetitions_per_mode")
        if isinstance(performance, dict)
        else None
    )
    add(
        checks,
        "performance:repetitions",
        positive_int(repetitions) and repetitions >= 3,
        "Each persistence mode requires at least three repetitions",
    )
    metrics = (
        string_set(performance.get("required_metrics"))
        if isinstance(performance, dict)
        else set()
    )
    add(
        checks,
        "performance:metrics",
        metrics == REQUIRED_PERFORMANCE_METRICS,
        "AOF comparison requires business, resource, disk and delayed-fsync metrics",
    )


def validate_backup_contract(
    checks: list[dict[str, Any]], policy: dict[str, Any]
) -> None:
    backup = policy.get("backup")
    add(checks, "backup:object", isinstance(backup, dict), "backup is an object")
    if not isinstance(backup, dict):
        return
    add(
        checks,
        "backup:daily-consistent-create-only",
        backup.get("schedule") == "daily"
        and backup.get("consistent_redis_snapshot_required") is True
        and backup.get("create_only_artifacts") is True,
        "Daily backups require a consistent Redis snapshot and create-only artifacts",
    )
    add(
        checks,
        "backup:checksum",
        backup.get("checksum_algorithm") == "sha256",
        "Backup manifests use SHA-256",
    )
    staging_root = backup.get("plaintext_staging_root")
    add(
        checks,
        "backup:plaintext-lifecycle",
        staging_root == "/var/backups/boost-gateway/staging"
        and backup.get("plaintext_removed_after_encryption") is True,
        "Plaintext staging is bounded and removed after encryption",
    )

    sources = backup.get("source_contracts")
    source_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(sources, list):
        for source in sources:
            if isinstance(source, dict) and isinstance(source.get("id"), str):
                source_by_id.setdefault(source["id"], source)
    add(
        checks,
        "backup:sources-unique-complete",
        isinstance(sources, list)
        and len(source_by_id) == len(sources) == len(REQUIRED_SOURCE_CONTRACTS)
        and set(source_by_id) == set(REQUIRED_SOURCE_CONTRACTS),
        "Backup source inventory is complete and has unique IDs",
    )
    for source_id, (kind, path) in REQUIRED_SOURCE_CONTRACTS.items():
        source = source_by_id.get(source_id, {})
        add(
            checks,
            f"backup:source:{source_id}",
            source.get("kind") == kind
            and source.get("path") == path
            and source.get("required") is True
            and isinstance(source.get("contains_secrets"), bool),
            f"{source_id} has the governed type, absolute path and required flag",
        )

    archive_contract = backup.get("archive_contract")
    add(
        checks,
        "backup:link-free-archive",
        isinstance(archive_contract, dict)
        and archive_contract.get("format") == "tar"
        and archive_contract.get("symbolic_link_entries_allowed") is False
        and archive_contract.get("hard_link_entries_allowed") is False
        and archive_contract.get("follow_symbolic_links") is False
        and archive_contract.get("reject_broken_symbolic_links") is True
        and archive_contract.get("reject_symbolic_link_target_escape") is True,
        "Backup tar contains no symbolic/hard links and rejects broken or escaping links",
    )
    add(
        checks,
        "backup:validated-link-metadata",
        isinstance(archive_contract, dict)
        and string_set(archive_contract.get("manifest_link_fields"))
        == REQUIRED_MANIFEST_LINK_FIELDS,
        "Manifest records original link evidence plus a validated source-relative target",
    )

    encryption = backup.get("encryption")
    add(
        checks,
        "backup:encryption",
        isinstance(encryption, dict)
        and encryption.get("tool") == "age"
        and encryption.get("recipient_file")
        == "/etc/boost-gateway/backup.age-recipient"
        and encryption.get("private_key_present_on_source_host") is False
        and encryption.get("encrypt_before_transfer") is True
        and encryption.get("manifest_contains_secret_values") is False,
        "Backups use recipient-only age encryption before transfer",
    )
    forbidden_secret_fields = {
        "recipient",
        "private_key",
        "passphrase",
        "password",
        "token",
        "credential",
    }
    add(
        checks,
        "backup:no-inline-secret-fields",
        isinstance(encryption, dict)
        and not (forbidden_secret_fields & set(encryption)),
        "Encryption policy contains paths and booleans, not inline credentials",
    )

    off_host = backup.get("off_host")
    destination = off_host.get("destination", "") if isinstance(off_host, dict) else ""
    try:
        parsed = urlparse(destination) if isinstance(destination, str) else urlparse("")
        remote_host = parsed.hostname or ""
        destination_has_password = parsed.password is not None
    except ValueError:
        parsed = urlparse("")
        remote_host = ""
        destination_has_password = True
    remote_is_loopback = remote_host.lower() in {"localhost", "localhost.localdomain"}
    try:
        remote_is_loopback = (
            remote_is_loopback or ipaddress.ip_address(remote_host).is_loopback
        )
    except ValueError:
        pass
    add(
        checks,
        "backup:off-host-destination",
        isinstance(off_host, dict)
        and off_host.get("transport") == "ssh"
        and parsed.scheme == "ssh"
        and bool(remote_host)
        and not remote_is_loopback
        and not destination_has_password
        and bool(parsed.path and parsed.path != "/")
        and off_host.get("allow_local_filesystem_destination") is False,
        "Off-host destination is a non-loopback credential-free SSH URI",
    )
    add(
        checks,
        "backup:off-host-proof",
        isinstance(off_host, dict)
        and off_host.get("remote_identity_attestation")
        == "/etc/boost-gateway/backup-remote-host-id.sha256"
        and off_host.get("require_distinct_host_identity") is True
        and off_host.get("require_remote_readback_sha256") is True
        and off_host.get("receipt_required") is True,
        "Remote host identity, readback checksum and receipt are mandatory",
    )

    retention = backup.get("retention")
    add(
        checks,
        "backup:retention-counts",
        isinstance(retention, dict)
        and positive_int(retention.get("daily_copies"))
        and retention.get("daily_copies") >= 2
        and positive_int(retention.get("weekly_copies"))
        and positive_int(retention.get("minimum_known_good_copies"))
        and retention.get("minimum_known_good_copies") >= 2,
        "Retention keeps daily, weekly and at least two known-good copies",
    )
    add(
        checks,
        "backup:retention-deletion-guards",
        isinstance(retention, dict)
        and retention.get("delete_only_after_verified_remote_copy") is True
        and retention.get("deletion_record_required") is True,
        "Retention cannot delete before remote verification and records every deletion",
    )


def validate_restore_contract(
    checks: list[dict[str, Any]], policy: dict[str, Any]
) -> None:
    restore = policy.get("restore")
    add(checks, "restore:object", isinstance(restore, dict), "restore is an object")
    if not isinstance(restore, dict):
        return
    add(
        checks,
        "restore:fresh-volume",
        restore.get("stage_into_new_volume") is True
        and restore.get("preserve_original_volume") is True
        and restore.get("reject_active_volume_destination") is True
        and restore.get("rollback_to_original_volume_on_failure") is True,
        "Restore stages into a fresh volume and preserves the original rollback path",
    )
    add(
        checks,
        "restore:offline-validation",
        string_set(restore.get("offline_validation"))
        == {"redis-check-rdb", "redis-check-aof"},
        "Restore validates RDB and AOF before activation",
    )
    add(
        checks,
        "restore:business-verification",
        string_set(restore.get("required_business_checks")) == REQUIRED_BUSINESS_CHECKS,
        "Restore verifies exact seed state, submit/top/rank and SDK full-flow",
    )
    add(
        checks,
        "restore:independent-drills",
        (
            restore.get("minimum_independent_drills") >= 2
            if positive_int(restore.get("minimum_independent_drills"))
            else False
        ),
        "At least two independent restore drills are required",
    )
    add(
        checks,
        "restore:distinct-drill-inputs",
        restore.get("distinct_backup_id_required") is True
        and restore.get("distinct_restore_target_required") is True,
        "Independent drills use distinct backups and restore targets",
    )
    link_reconstruction = restore.get("link_reconstruction")
    add(
        checks,
        "restore:validated-link-reconstruction",
        isinstance(link_reconstruction, dict)
        and link_reconstruction.get("source_mapping_required") is True
        and link_reconstruction.get("trust_original_link_text") is False
        and link_reconstruction.get("target_source_id_required") is True
        and link_reconstruction.get("target_relative_path_required") is True,
        "Restore rebuilds links from validated source mappings, never raw link text",
    )


def validate_objectives_and_activation(
    checks: list[dict[str, Any]], policy: dict[str, Any]
) -> None:
    add(
        checks,
        "policy:schema",
        policy.get("schema_version") == 1,
        "schema_version is 1",
    )
    add(
        checks,
        "policy:todo",
        policy.get("todo") == "TODO-0012",
        "policy is bound to TODO-0012",
    )
    activation = policy.get("activation")
    state = activation.get("state") if isinstance(activation, dict) else None
    candidate_only = (
        state == "candidate_only"
        and activation.get("production_compose_mount_enabled") is False
        and activation.get("host_units_install_enabled") is False
        and activation.get("live_policy_changed") is False
    )
    approved_candidate = (
        state == "approved_candidate_pending_host_activation"
        and activation.get("production_compose_mount_enabled") is True
        and activation.get("host_units_install_enabled") is True
        and activation.get("live_policy_changed") is False
        and activation.get("decision")
        == "docs/decisions/todo0012-redis-aof-activation.json"
        and re.fullmatch(r"[0-9a-f]{64}", str(activation.get("benchmark_sha256", "")))
        is not None
    )
    add(
        checks,
        "activation:governed-state",
        candidate_only or approved_candidate,
        "Activation is either isolated or an approved immutable candidate pending host activation",
    )
    gates = (
        string_set(activation.get("required_before_activation"))
        if isinstance(activation, dict)
        else set()
    )
    add(
        checks,
        "activation:gates",
        gates == REQUIRED_ACTIVATION_GATES,
        "Activation remains gated on measurements, identities, change record and rollback",
    )

    objectives = policy.get("objectives")
    redis_rpo = (
        objectives.get("redis_rpo_seconds") if isinstance(objectives, dict) else None
    )
    add(
        checks,
        "objectives:redis-rpo",
        positive_int(redis_rpo) and redis_rpo <= 60,
        "Redis RPO target is at most 60 seconds",
    )
    rto = objectives.get("rto_seconds") if isinstance(objectives, dict) else None
    expected_maximums = {
        "gateway": 300,
        "backend": 300,
        "redis_restore": 600,
        "host_reboot": 600,
        "release_rollback": 600,
    }
    add(
        checks,
        "objectives:rto",
        isinstance(rto, dict)
        and set(rto) == set(expected_maximums)
        and all(
            positive_int(rto.get(name)) and rto[name] <= maximum
            for name, maximum in expected_maximums.items()
        ),
        "Gateway/backend RTO is at most 5 minutes and Redis/reboot/rollback at most 10 minutes",
    )


def validate_evidence_contract(
    checks: list[dict[str, Any]], policy: dict[str, Any]
) -> None:
    evidence = policy.get("evidence")
    required_true = {
        "bind_source_host_identity",
        "bind_deployment_identity",
        "bind_redis_profile_sha256",
        "bind_backup_policy_sha256",
        "bind_backup_manifest_sha256",
        "bind_remote_receipt_sha256",
    }
    add(
        checks,
        "evidence:bindings",
        isinstance(evidence, dict)
        and all(evidence.get(name) is True for name in required_true)
        and evidence.get("secret_material_recorded") is False,
        "Evidence binds host, deployment, profile, manifest and receipt without secrets",
    )


def validate_policy(
    policy_path: Path = DEFAULT_POLICY,
    redis_profile_path: Path | None = None,
    *,
    repository_root: Path = ROOT,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    policy_digest = ""
    redis_digest = ""
    resolved_profile = redis_profile_path
    try:
        policy = load_json_object(policy_path, "backup/recovery policy")
        policy_digest = sha256_file(policy_path)
        add(checks, "policy:load", True, "Backup/recovery policy is readable")
    except (OSError, PolicyError) as exc:
        add(checks, "policy:load", False, str(exc))
        policy = {}

    if policy:
        profile_value = nested(policy, "redis", "profile")
        add(
            checks,
            "redis:profile-path",
            profile_value == "env/redis/redis.production-validation.conf",
            "Policy references the dedicated production-validation Redis candidate",
        )
        if resolved_profile is None and isinstance(profile_value, str):
            resolved_profile = repository_root / profile_value
        validate_objectives_and_activation(checks, policy)
        if resolved_profile is None:
            add(
                checks, "redis:profile-load", False, "Redis profile path is unavailable"
            )
        else:
            redis_digest = validate_redis_profile(checks, policy, resolved_profile)
        validate_performance_contract(checks, policy)
        validate_backup_contract(checks, policy)
        validate_restore_contract(checks, policy)
        validate_evidence_contract(checks, policy)

    failed = [check for check in checks if not check["passed"]]
    activation_state = nested(policy, "activation", "state") if policy else None
    return {
        "summary_version": 1,
        "generated_at": now(),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "backup_recovery_policy" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "candidate_contract_valid": not failed,
        "governed_candidate_ready": not failed
        and activation_state == "approved_candidate_pending_host_activation",
        "activation_ready": False,
        "formal_todo0012_claim": False,
        "live_policy_changed": False,
        "secret_material_recorded": False,
        "policy": {
            "path": str(policy_path.resolve()),
            "sha256": policy_digest,
        },
        "redis_profile": {
            "path": (
                str(resolved_profile.resolve()) if resolved_profile is not None else ""
            ),
            "sha256": redis_digest,
        },
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
