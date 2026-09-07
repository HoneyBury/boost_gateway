from __future__ import annotations

import copy
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from scripts.lib import deadman_evidence as io
from scripts.lib import immutable_window as window
from scripts.tools import manage_immutable_window as manager

NOW = datetime(2026, 9, 7, 14, 0, tzinfo=UTC)


def attestation():
    times = [NOW - timedelta(minutes=value) for value in (20, 19, 16, 12, 11)]
    return {"schema_version": 1, "attestation_id": "drill", "created_at": io.stamp(NOW),
            "create_only": True, "overall_pass": True, "provider": "healthchecks.io",
            "subject": {key: "a" * 64 for key in ("canary_host_id_sha256", "check_identity_sha256",
                "reporter_sha256", "service_unit_sha256", "watchdog_dropin_sha256", "provider_contract_sha256",
                "candidate_record_sha256")},
            "drill": {"failure_mode": "missing-heartbeat", "armed_at": io.stamp(times[0]),
                      "heartbeat_stopped_at": io.stamp(times[1]), "provider_down_at": io.stamp(times[2]),
                      "rearmed_at": io.stamp(times[3]), "provider_up_at": io.stamp(times[4]),
                      "provider_unique_key": "b" * 40, "final_status": "up"},
            "deliveries": {"down": {"message_id": "<down@example.org>", "observed_at": io.stamp(times[2])},
                           "up": {"message_id": "<up@example.org>", "observed_at": io.stamp(times[4])}},
            "artifacts": [{"role": role, "basename": role + ".json", "size": 10, "sha256": "c" * 64}
                          for role in ("before", "down", "up", "armed", "stopped", "rearmed", "success")],
            "secret_material_recorded": False}


def declared(**overrides):
    arguments = {"start": NOW + timedelta(minutes=5), "now": NOW,
                 "record": {"tag": "v3.6.7", "commit": "db0f905d0421b2052b9de7f49d9bf71787915e23",
                            "deployment_id": "v3.6.7-fb5f6bfb2626-fa8b69b36dec",
                            "runtime_asset_sha256": "e" * 64, "host": {"host_id_sha256": "f" * 64}},
                 "record_sha": "a" * 64, "host_id": "a" * 64, "endpoint": "tcp://100.65.71.117:9201",
                 "admission_reports": {role: {} for role in window.ADMISSION_ROLES},
                 "attestation": attestation(), "attestation_sha": "b" * 64, "sdk_version": "4.2.0"}
    return window.declare(**(arguments | overrides))


def report(declaration):
    return {"overall_pass": True, "window": "30d", "period": {"start": declaration["start"],
        "end": declaration["end"], "duration_seconds": window.DURATION}, "candidate": declaration["candidate"],
        "endpoint": declaration["endpoint"], "candidate_consistent": True, "expected_samples": 43200,
        "maintenance_windows": [], "invalid_samples": [], "coverage_rate": 1.0, "recorded_success_rate": 1.0,
        "availability_including_approved_maintenance": 1.0, "availability_excluding_approved_maintenance": 1.0,
        "max_nonmaintenance_gap_minutes": 0}


def test_fixed_window_is_exactly_30_days():
    value = declared()
    window.validate_declaration(value)
    assert (io.instant(value["end"]) - io.instant(value["start"])).total_seconds() == 2592000
    assert value["expected_samples"] == 43200


@pytest.mark.parametrize("offset", [-1, 0, 60, 121, 7200])
def test_rejects_past_unaligned_or_unreasonably_future_start(offset):
    with pytest.raises(io.DeadmanError):
        declared(start=NOW + timedelta(seconds=offset))


@pytest.mark.parametrize("mutation", ["missing_delivery", "same_id", "false_success", "secret", "order", "artifacts"])
def test_deadman_attestation_cannot_be_faked_by_single_pass_flag(mutation):
    item = attestation()
    if mutation == "missing_delivery":
        del item["deliveries"]["down"]
    elif mutation == "same_id":
        item["deliveries"]["up"]["message_id"] = item["deliveries"]["down"]["message_id"]
    elif mutation == "false_success":
        item["overall_pass"] = False
    elif mutation == "secret":
        item["subject"]["reporter_sha256"] = "12345678-1234-1234-1234-123456789abc"
    elif mutation == "order":
        item["drill"]["rearmed_at"] = item["drill"]["heartbeat_stopped_at"]
    else:
        item["artifacts"] = []
    with pytest.raises(io.DeadmanError):
        declared(attestation=item)


@pytest.mark.parametrize("field,value", [("coverage_rate", 0.998), ("recorded_success_rate", float("nan")),
    ("max_nonmaintenance_gap_minutes", 3), ("expected_samples", 43199), ("invalid_samples", [{}]),
    ("candidate_consistent", False), ("overall_pass", False)])
def test_aggregate_rejects_false_success(field, value):
    declaration = declared()
    aggregate = report(declaration)
    window.validate_aggregate(aggregate, declaration)
    with pytest.raises(io.DeadmanError):
        window.validate_aggregate(aggregate | {field: value}, declaration)


def test_aggregate_rejects_rolling_end():
    declaration = declared()
    aggregate = report(declaration)
    aggregate["period"]["end"] = io.stamp(io.instant(declaration["end"]) + timedelta(minutes=1))
    with pytest.raises(io.DeadmanError):
        window.validate_aggregate(aggregate, declaration)


def test_aggregate_accepts_existing_canary_millisecond_timestamp_format():
    declaration = declared()
    aggregate = report(declaration)
    for key in ("start", "end"):
        aggregate["period"][key] = declaration[key].replace("Z", ".000Z")
    window.validate_aggregate(aggregate, declaration)


def test_admission_requires_actual_fresh_reports(tmp_path):
    paths = {}
    for role in window.ADMISSION_ROLES:
        paths[role] = tmp_path / (role + ".json")
        io.create(paths[role], {"created_at": io.stamp(NOW), "overall_pass": True, "role": role})
    assert set(window.admission(paths, now=NOW)) == window.ADMISSION_ROLES
    with pytest.raises(io.DeadmanError):
        window.admission(paths, now=NOW + timedelta(hours=1))
    with pytest.raises(io.DeadmanError):
        window.admission({"deadman": paths["deadman"]}, now=NOW)


def test_sample_manifest_binds_external_host_and_exact_interval(tmp_path):
    (tmp_path / "samples").mkdir()
    declaration = declared()
    sample = {"scheduled_minute": declaration["start"], "candidate": declaration["candidate"],
              "host_boundary": declaration["host_boundary"], "endpoint": declaration["endpoint"], "sdk_version": "4.2.0"}
    path = tmp_path / "samples/one.json"
    # The sample endpoint is legitimate business provenance, not a heartbeat secret.
    import json
    path.write_text(json.dumps(sample))
    first = window.sample_manifest(tmp_path, declaration)
    assert first["sample_count"] == 1
    altered = copy.deepcopy(sample)
    altered["host_boundary"]["canary_host_id_sha256"] = "0" * 64
    path.write_text(json.dumps(altered))
    with pytest.raises(io.DeadmanError):
        window.sample_manifest(tmp_path, declaration)


def test_window_cli_help_has_no_host_side_effects():
    result = subprocess.run([sys.executable, "scripts/tools/manage_immutable_window.py", "--help"], capture_output=True)
    assert result.returncode == 0
    assert manager.ROOT.name == "boost-gateway-immutable-windows"


def test_declaration_serializes_create_only(tmp_path):
    item = declared()
    target = tmp_path / "declaration.json"
    io.create(target, item)
    assert io.read_json(target) == item
    with pytest.raises(FileExistsError):
        io.create(target, item)


def test_finalizer_does_not_aggregate_before_fixed_end(tmp_path):
    item = declared(start=io.utcnow().replace(second=0, microsecond=0) + timedelta(minutes=5), now=io.utcnow())
    with mock.patch.object(manager, "load", return_value=item), mock.patch.object(manager, "run") as run:
        with pytest.raises(io.DeadmanError, match="fixed end"):
            manager.aggregate(tmp_path)
        run.assert_not_called()


def test_superseded_window_cannot_be_loaded(tmp_path):
    io.create(tmp_path / "declaration.json", declared())
    io.create(tmp_path / "superseded.json", {"overall_pass": False})
    with pytest.raises(io.DeadmanError, match="superseded"):
        manager.load(tmp_path)
