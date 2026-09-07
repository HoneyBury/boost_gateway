"""Runtime checks and the narrow immutable v3.6.7 network bridge."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

LEGACY_DEPLOYMENT_ID = "v3.6.7-fb5f6bfb2626-fa8b69b36dec"
LEGACY_TAG = "v3.6.7"
LEGACY_COMMIT = "db0f905d0421b2052b9de7f49d9bf71787915e23"
LEGACY_CONFIGURATION_SHA256 = (
    "0692efc4119bac78469672b6fee061fe0dfc7ad68265765da8b36b6e10399777"
)
LEGACY_RUNTIME_ASSET_SHA256 = (
    "fb5f6bfb2626c15a5cd31c7bdd8d06a963192b09132d55e0a387250bdf92fbd0"
)
LEGACY_IMAGE_ENVIRONMENT_SHA256 = (
    "928e29430e0e51d6933d12537c849a8893f7e4823668f111fe1a7fafc941af3f"
)
LEGACY_MANIFEST_SHA256 = (
    "a5df1fc90d0347a9e4deabcbbdaf84b72e5fc08a038b72a1309b7cec4ad75374"
)
LEGACY_COMPOSE_SHA256 = (
    "8d6f3d0e81a083c0a7107596d8a7d5ac5135c8a0a0b9818a8404263cdac13d2e"
)
LEGACY_NETWORK_CONFIG_HASH = (
    "8de43c2c9c4d7828e98d26eda44ca2f11d8e41f5af2c326bfdb3c54a351663ae"
)
LEGACY_COMPOSE_VERSION = "2.40.3"
CURRENT_DEPLOYMENT_LINK = Path("/opt/boost-gateway/current")
LEGACY_DEPLOYMENT_PATH = Path("/opt/boost-gateway/deployments") / LEGACY_DEPLOYMENT_ID
LEGACY_RELEASE_PATH = Path("/opt/boost-gateway/releases") / LEGACY_DEPLOYMENT_ID
NETWORK_NAME = "boost-gateway-production_boost-net"
NETWORK_SUBNET = "172.18.0.0/16"
NETWORK_GATEWAY = "172.18.0.1"
NETWORK_CONTRACT_FAILURE = "boost-net: exactly one fixed IPAM config is required"
REQUIRED_CONTAINER_SERVICES = {
    "boost-gateway": "gateway",
    "boost-login-backend": "login-backend",
    "boost-room-backend": "room-backend",
    "boost-battle-backend": "battle-backend",
    "boost-matchmaking-backend": "matchmaking-backend",
    "boost-leaderboard-backend": "leaderboard-backend",
    "boost-redis": "redis",
    "boost-redis-exporter": "redis-exporter",
    "boost-node-exporter": "node-exporter",
    "boost-cadvisor": "cadvisor",
    "boost-prometheus": "prometheus",
    "boost-alertmanager": "alertmanager",
    "boost-grafana": "grafana",
}
REQUIRED_CONTAINER_NAMES = set(REQUIRED_CONTAINER_SERVICES)
LEGACY_RECORD_IDENTITY = {
    "deployment_id": LEGACY_DEPLOYMENT_ID,
    "deployment_path": str(LEGACY_DEPLOYMENT_PATH),
    "release_path": str(LEGACY_RELEASE_PATH),
    "tag": LEGACY_TAG,
    "commit": LEGACY_COMMIT,
    "configuration_sha256": LEGACY_CONFIGURATION_SHA256,
    "runtime_asset_sha256": LEGACY_RUNTIME_ASSET_SHA256,
    "image_environment_sha256": LEGACY_IMAGE_ENVIRONMENT_SHA256,
    "manifest_sha256": LEGACY_MANIFEST_SHA256,
}
LEGACY_RESOLVED_NETWORK = {"name": NETWORK_NAME, "driver": "bridge", "ipam": {}}
LEGACY_RUNTIME_NETWORK_IDENTITY = {
    "network_name": NETWORK_NAME,
    "driver": "bridge",
    "subnet": NETWORK_SUBNET,
    "gateway": NETWORK_GATEWAY,
    "container_count": len(REQUIRED_CONTAINER_NAMES),
    "compose_config_hash": LEGACY_NETWORK_CONFIG_HASH,
    "compose_version": LEGACY_COMPOSE_VERSION,
}
Run = Callable[[list[str], int], subprocess.CompletedProcess[str]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(
    path: Path, description: str, *, expected_symlink_target: Path | None = None
) -> dict[str, Any]:
    if path.is_symlink():
        if expected_symlink_target is None or path.resolve() != expected_symlink_target:
            raise RuntimeError(
                f"legacy network bridge {description} symlink target drift"
            )
    elif expected_symlink_target is not None or not path.is_file():
        raise RuntimeError(f"legacy network bridge {description} is not a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"legacy network bridge cannot read {description}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"legacy network bridge {description} is not an object")
    return value


def _validate_identity(staging: Path, compose: Path) -> dict[str, str]:
    expected_compose = (
        LEGACY_RELEASE_PATH / "deploy/operations/docker-compose.production.yml"
    )
    release_link = staging / "release"
    if staging != LEGACY_DEPLOYMENT_PATH:
        raise RuntimeError("legacy network bridge deployment path is not allowlisted")
    if (
        not release_link.is_symlink()
        or release_link.resolve(strict=True) != LEGACY_RELEASE_PATH
    ):
        raise RuntimeError("legacy network bridge release path is not allowlisted")
    if compose != expected_compose:
        raise RuntimeError("legacy network bridge compose path is not deployment-bound")
    if staging.name != LEGACY_DEPLOYMENT_ID:
        raise RuntimeError("legacy network bridge deployment ID is not allowlisted")

    record_path = staging / "record.json"
    manifest_path = staging / "manifest.json"
    record = _load_json(record_path, "deployment record")
    manifest = _load_json(
        manifest_path,
        "manifest",
        expected_symlink_target=LEGACY_RELEASE_PATH / "manifest.json",
    )
    expected_record = {**LEGACY_RECORD_IDENTITY, "status": "verified"}
    drift = {
        key: {"expected": expected, "observed": record.get(key)}
        for key, expected in expected_record.items()
        if record.get(key) != expected
    }
    if drift:
        raise RuntimeError(
            "legacy network bridge deployment identity drift: "
            + json.dumps(drift, sort_keys=True, separators=(",", ":"))
        )
    if _sha256(manifest_path) != LEGACY_MANIFEST_SHA256:
        raise RuntimeError("legacy network bridge manifest digest drift")
    if _sha256(compose) != LEGACY_COMPOSE_SHA256:
        raise RuntimeError("legacy network bridge Compose digest drift")
    image_environment_path = staging / "compose-images.env"
    if (
        not image_environment_path.is_file()
        or image_environment_path.is_symlink()
        or _sha256(image_environment_path) != LEGACY_IMAGE_ENVIRONMENT_SHA256
    ):
        raise RuntimeError("legacy network bridge image environment digest drift")
    controller = manifest.get("deployment_controller")
    configuration = manifest.get("configuration")
    if (
        manifest.get("repository") != "HoneyBury/boost_gateway"
        or manifest.get("platform") != "linux-x64"
        or manifest.get("tag") != LEGACY_TAG
        or manifest.get("commit") != LEGACY_COMMIT
        or manifest.get("source_build_performed") is not False
        or manifest.get("dependency_resolution_performed") is not False
        or not isinstance(controller, dict)
        or controller.get("compose_sha256") != LEGACY_COMPOSE_SHA256
        or not isinstance(configuration, dict)
        or configuration.get("sha256") != LEGACY_CONFIGURATION_SHA256
    ):
        raise RuntimeError("legacy network bridge manifest identity drift")
    return {**LEGACY_RECORD_IDENTITY, "compose_sha256": LEGACY_COMPOSE_SHA256}


def _validate_resolved_network(document: object) -> dict[str, Any]:
    networks = document.get("networks") if isinstance(document, dict) else None
    network = networks.get("boost-net") if isinstance(networks, dict) else None
    if not isinstance(network, dict):
        raise RuntimeError("legacy network bridge resolved boost-net is missing")
    if set(network) != {"name", "driver", "ipam"}:
        raise RuntimeError("legacy network bridge resolved boost-net shape drift")
    if (
        network.get("name") != NETWORK_NAME
        or network.get("driver") != "bridge"
        or network.get("ipam") != {}
    ):
        raise RuntimeError("legacy network bridge resolved boost-net drift")
    return dict(LEGACY_RESOLVED_NETWORK)


def _load_runtime_containers(
    run: Run, compose: Path
) -> tuple[str, set[str], dict[str, dict[str, str]]]:
    listed = run(["docker", "compose", "-f", str(compose), "ps", "-q"], 30)
    if listed.returncode:
        detail = (listed.stderr or listed.stdout).strip()[-1000:]
        raise RuntimeError(f"legacy network bridge Compose inventory failed: {detail}")
    container_ids = listed.stdout.splitlines()
    if (
        len(container_ids) != len(REQUIRED_CONTAINER_NAMES)
        or len(set(container_ids)) != len(container_ids)
        or any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in container_ids)
    ):
        raise RuntimeError("legacy network bridge Compose container inventory drift")
    inspected = run(["docker", "inspect", *container_ids], 30)
    if inspected.returncode:
        detail = (inspected.stderr or inspected.stdout).strip()[-1000:]
        raise RuntimeError(f"legacy network bridge container inspect failed: {detail}")
    try:
        containers = json.loads(inspected.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "legacy network bridge container inspect returned invalid JSON"
        ) from exc
    if not isinstance(containers, list) or len(containers) != len(container_ids):
        raise RuntimeError("legacy network bridge container inspect inventory drift")

    observed_ids: set[str] = set()
    names: set[str] = set()
    addresses: set[str] = set()
    network_ids: set[str] = set()
    expected_attachments: dict[str, dict[str, str]] = {}
    subnet = ipaddress.ip_network(NETWORK_SUBNET)
    reserved = {
        subnet.network_address,
        subnet.broadcast_address,
        ipaddress.ip_address(NETWORK_GATEWAY),
    }
    for container in containers:
        if not isinstance(container, dict):
            raise RuntimeError("legacy network bridge container entry is invalid")
        container_id = str(container.get("Id", ""))
        container_name = str(container.get("Name", "")).removeprefix("/")
        observed_ids.add(container_id)
        names.add(container_name)
        config = container.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        expected_service = REQUIRED_CONTAINER_SERVICES.get(container_name)
        if (
            not isinstance(labels, dict)
            or expected_service is None
            or labels.get("com.docker.compose.project") != "boost-gateway-production"
            or labels.get("com.docker.compose.service") != expected_service
            or labels.get("com.docker.compose.version") != LEGACY_COMPOSE_VERSION
        ):
            raise RuntimeError("legacy network bridge container labels drift")
        settings = container.get("NetworkSettings")
        networks = settings.get("Networks") if isinstance(settings, dict) else None
        if not isinstance(networks, dict) or set(networks) != {NETWORK_NAME}:
            raise RuntimeError("legacy network bridge container attachment drift")
        attachment = networks[NETWORK_NAME]
        if not isinstance(attachment, dict):
            raise RuntimeError("legacy network bridge container attachment is invalid")
        network_ids.add(str(attachment.get("NetworkID", "")))
        raw_address = (
            f"{attachment.get('IPAddress', '')}/{attachment.get('IPPrefixLen', '')}"
        )
        try:
            address = ipaddress.ip_interface(raw_address)
        except ValueError as exc:
            raise RuntimeError(
                "legacy network bridge container address is invalid"
            ) from exc
        if (
            address.network != subnet
            or address.ip in reserved
            or str(address.ip) in addresses
            or attachment.get("Gateway") != NETWORK_GATEWAY
        ):
            raise RuntimeError("legacy network bridge container address drift")
        addresses.add(str(address.ip))
        expected_attachments[container_id] = {
            "Name": container_name,
            "IPv4Address": str(address),
            "IPv6Address": "",
        }
    if observed_ids != set(container_ids) or names != REQUIRED_CONTAINER_NAMES:
        raise RuntimeError("legacy network bridge governed container identity drift")
    if (
        len(network_ids) != 1
        or re.fullmatch(r"[0-9a-f]{64}", next(iter(network_ids))) is None
    ):
        raise RuntimeError("legacy network bridge container network ID drift")
    return next(iter(network_ids)), set(container_ids), expected_attachments


def _load_runtime_network(run: Run, network_id: str) -> dict[str, Any]:
    completed = run(["docker", "network", "inspect", network_id], 30)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-1000:]
        raise RuntimeError(f"legacy network bridge inspect failed: {detail}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "legacy network bridge inspect returned invalid JSON"
        ) from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RuntimeError(
            "legacy network bridge inspect returned an invalid inventory"
        )
    return value[0]


def _validate_runtime_network(
    network: dict[str, Any],
    network_id: str,
    container_ids: set[str],
    expected_attachments: dict[str, dict[str, str]],
) -> dict[str, Any]:
    expected_labels = {
        "com.docker.compose.config-hash": LEGACY_NETWORK_CONFIG_HASH,
        "com.docker.compose.network": "boost-net",
        "com.docker.compose.project": "boost-gateway-production",
        "com.docker.compose.version": LEGACY_COMPOSE_VERSION,
    }
    scalar_expectations = {
        "Name": NETWORK_NAME,
        "Driver": "bridge",
        "Scope": "local",
        "EnableIPv6": False,
        "Internal": False,
        "Attachable": False,
        "Ingress": False,
        "ConfigOnly": False,
        "Options": {},
        "Labels": expected_labels,
    }
    drift = {
        key: {"expected": expected, "observed": network.get(key)}
        for key, expected in scalar_expectations.items()
        if network.get(key) != expected
    }
    ipam = network.get("IPAM")
    expected_ipam = {
        "Driver": "default",
        "Options": None,
        "Config": [
            {"Subnet": NETWORK_SUBNET, "IPRange": "", "Gateway": NETWORK_GATEWAY}
        ],
    }
    if ipam != expected_ipam:
        drift["IPAM"] = {"expected": expected_ipam, "observed": ipam}
    observed_network_id = network.get("Id")
    if observed_network_id != network_id:
        drift["Id"] = {"expected": network_id, "observed": observed_network_id}

    containers = network.get("Containers")
    observed_names: set[str] = set()
    if isinstance(containers, dict):
        for container_id, item in containers.items():
            if re.fullmatch(
                r"[0-9a-f]{64}", str(container_id)
            ) is None or not isinstance(item, dict):
                drift["Containers"] = {"expected": "governed container objects"}
                break
            observed_names.add(str(item.get("Name", "")))
            expected_attachment = expected_attachments.get(str(container_id))
            if expected_attachment is None or any(
                item.get(key) != expected
                for key, expected in expected_attachment.items()
            ):
                drift["container_attachments"] = {
                    "expected": expected_attachments,
                    "observed": containers,
                }
                break
    else:
        drift["Containers"] = {"expected": "container inventory object"}
    if not isinstance(containers, dict) or set(containers) != container_ids:
        drift["container_ids"] = {
            "expected": sorted(container_ids),
            "observed": sorted(containers) if isinstance(containers, dict) else None,
        }
    if observed_names != REQUIRED_CONTAINER_NAMES:
        drift["container_names"] = {
            "expected": sorted(REQUIRED_CONTAINER_NAMES),
            "observed": sorted(observed_names),
        }
    if drift:
        raise RuntimeError(
            "legacy network bridge runtime drift: "
            + json.dumps(drift, sort_keys=True, separators=(",", ":"))
        )
    container_evidence = [
        {
            "container_id": container_id,
            "name": attachment["Name"],
            "ipv4_address": attachment["IPv4Address"],
        }
        for container_id, attachment in sorted(expected_attachments.items())
    ]
    return {
        **LEGACY_RUNTIME_NETWORK_IDENTITY,
        "network_id": network_id,
        "containers": container_evidence,
    }


def validate_legacy_production_network_bridge(
    staging: Path,
    compose: Path,
    document: object,
    contract_failures: list[str],
    run: Run,
) -> dict[str, Any]:
    """Validate the sole historical deployment admitted by this bridge."""
    if (
        not CURRENT_DEPLOYMENT_LINK.is_symlink()
        or CURRENT_DEPLOYMENT_LINK.resolve(strict=True) != staging
    ):
        raise RuntimeError(
            "legacy network bridge staging is not the current deployment"
        )
    if contract_failures != [NETWORK_CONTRACT_FAILURE]:
        raise RuntimeError(
            "legacy network bridge contract failures are not allowlisted"
        )
    identity = _validate_identity(staging, compose)
    resolved_network = _validate_resolved_network(document)
    network_id, container_ids, expected_attachments = _load_runtime_containers(
        run, compose
    )
    runtime = _validate_runtime_network(
        _load_runtime_network(run, network_id),
        network_id,
        container_ids,
        expected_attachments,
    )
    return {
        "accepted_contract_failures": [NETWORK_CONTRACT_FAILURE],
        "identity": identity,
        "resolved_network": resolved_network,
        "runtime_network": runtime,
    }


def legacy_production_network_evidence_is_complete(value: object) -> bool:
    """Return whether a verifier summary contains the complete bridge evidence."""
    if not isinstance(value, dict) or set(value) != {
        "accepted_contract_failures",
        "identity",
        "resolved_network",
        "runtime_network",
    }:
        return False
    expected_identity = {
        **LEGACY_RECORD_IDENTITY,
        "compose_sha256": LEGACY_COMPOSE_SHA256,
    }
    runtime = value.get("runtime_network")
    containers = runtime.get("containers") if isinstance(runtime, dict) else None
    if (
        not isinstance(containers, list)
        or len(containers) != len(REQUIRED_CONTAINER_NAMES)
        or any(
            not isinstance(item, dict)
            or set(item) != {"container_id", "name", "ipv4_address"}
            for item in containers
        )
    ):
        return False
    container_ids = {str(item.get("container_id", "")) for item in containers}
    container_names = {str(item.get("name", "")) for item in containers}
    try:
        addresses = [
            ipaddress.ip_interface(str(item["ipv4_address"])) for item in containers
        ]
    except ValueError:
        return False
    subnet = ipaddress.ip_network(NETWORK_SUBNET)
    reserved = {
        subnet.network_address,
        subnet.broadcast_address,
        ipaddress.ip_address(NETWORK_GATEWAY),
    }
    return (
        value.get("accepted_contract_failures") == [NETWORK_CONTRACT_FAILURE]
        and value.get("identity") == expected_identity
        and value.get("resolved_network") == LEGACY_RESOLVED_NETWORK
        and isinstance(runtime, dict)
        and set(runtime)
        == {*LEGACY_RUNTIME_NETWORK_IDENTITY, "network_id", "containers"}
        and all(
            runtime.get(key) == expected
            for key, expected in LEGACY_RUNTIME_NETWORK_IDENTITY.items()
        )
        and re.fullmatch(r"[0-9a-f]{64}", str(runtime.get("network_id", "")))
        is not None
        and len(container_ids) == len(containers)
        and all(re.fullmatch(r"[0-9a-f]{64}", item) for item in container_ids)
        and container_names == REQUIRED_CONTAINER_NAMES
        and len({address.ip for address in addresses}) == len(containers)
        and all(
            address.network == subnet and address.ip not in reserved
            for address in addresses
        )
    )
