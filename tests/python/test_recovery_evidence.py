from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.lib import recovery_evidence


IMAGE_KEYS = {"gateway": "GATEWAY_IMAGE_ID", "login": "LOGIN_IMAGE_ID"}


def valid_context() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    deployment = {
        "deployment_id": "deployment-one",
        "tag": "v3.6.7",
        "commit": "a" * 40,
        "runtime_asset_sha256": "b" * 64,
    }
    summary: dict[str, object] = {
        "schema_version": 1,
        "restore_id": "restore-one",
        "deployment": deployment,
        "overall_pass": True,
        "status": "passed",
        "target_volume": "boost-gateway-recovery-one",
        "target_volume_retained": True,
        "leaderboard_seed_exact": True,
        "redis_ping": True,
        "production_switched": False,
        "active_volume_mounted_by_drill": False,
        "restore_known_good": False,
        "formal_todo0012_claim": False,
        "active_volume_identity_sha256": "c" * 64,
        "target_volume_identity_sha256": "d" * 64,
        "canonical_seed_restored_sha256": "e" * 64,
        "canonical_seed_key_count": 2,
        "redis_snapshot_sha256": "f" * 64,
        "redis_image": "sha256:" + "1" * 64,
    }
    record: dict[str, object] = {
        **deployment,
        "status": "verified",
        "release_path": "/release/v3.6.7",
        "image_ids": {
            "GATEWAY_IMAGE_ID": "sha256:" + "2" * 64,
            "LOGIN_IMAGE_ID": "sha256:" + "3" * 64,
        },
    }
    manifest: dict[str, object] = {
        "schema_version": 1,
        "tag": deployment["tag"],
        "commit": deployment["commit"],
        "platform": "linux-x64",
        "source_build_performed": False,
        "binaries": [{"name": "sdk_full_flow_client", "sha256": "4" * 64}],
    }
    return summary, record, manifest


def validate(
    summary: dict[str, object],
    record: dict[str, object],
    manifest: dict[str, object],
) -> dict[str, str]:
    return recovery_evidence.validate_retained_release_context(
        summary,
        record,
        manifest,
        retained_volume="boost-gateway-recovery-one",
        resolved_release_path="/release/v3.6.7",
        client_name="sdk_full_flow_client",
        client_sha256="4" * 64,
        image_keys=IMAGE_KEYS,
    )


def test_retained_release_context_returns_bound_immutable_images() -> None:
    summary, record, manifest = valid_context()

    images = validate(summary, record, manifest)

    assert images == {
        "gateway": "sha256:" + "2" * 64,
        "login": "sha256:" + "3" * 64,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("target_volume_retained", False),
        ("canonical_seed_key_count", True),
        ("redis_snapshot_sha256", "invalid"),
        ("formal_todo0012_claim", True),
    ),
)
def test_retained_release_context_rejects_ineligible_seed(
    field: str, value: object
) -> None:
    summary, record, manifest = valid_context()
    summary[field] = value

    with pytest.raises(ValueError, match="eligible retained seed"):
        validate(summary, record, manifest)


def test_retained_release_context_rejects_each_identity_layer() -> None:
    summary, record, manifest = valid_context()
    cases = (
        ("restore and deployment identities differ", summary, "deployment", {}),
        ("release directory binding differs", record, "release_path", "/other"),
        ("release manifest identity differs", manifest, "platform", "linux-arm64"),
        ("client digest differs", manifest["binaries"][0], "sha256", "5" * 64),
        ("deployment image identity is invalid", record["image_ids"], "GATEWAY_IMAGE_ID", "mutable"),
    )
    for message, target, field, value in cases:
        current_summary = deepcopy(summary)
        current_record = deepcopy(record)
        current_manifest = deepcopy(manifest)
        if target is summary:
            current_summary[field] = value
        elif target is record:
            current_record[field] = value
        elif target is manifest:
            current_manifest[field] = value
        elif target is manifest["binaries"][0]:
            current_manifest["binaries"][0][field] = value
        else:
            current_record["image_ids"][field] = value
        with pytest.raises(ValueError, match=message):
            validate(current_summary, current_record, current_manifest)


def test_retained_release_context_rejects_invalid_observed_client_digest() -> None:
    summary, record, manifest = valid_context()

    with pytest.raises(ValueError, match="client digest differs"):
        recovery_evidence.validate_retained_release_context(
            summary,
            record,
            manifest,
            retained_volume="boost-gateway-recovery-one",
            resolved_release_path="/release/v3.6.7",
            client_name="sdk_full_flow_client",
            client_sha256="invalid",
            image_keys=IMAGE_KEYS,
        )
