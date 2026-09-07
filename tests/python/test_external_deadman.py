from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from scripts.lib import deadman_evidence as evidence
from scripts.lib import external_deadman as reporter
from scripts.tools import manage_external_deadman as manager

NOW = datetime(2026, 9, 7, 14, 0, tzinfo=UTC)
SECRET = "12345678-1234-1234-1234-123456789abc"


@pytest.fixture
def credential(tmp_path):
    path = tmp_path / "credential"
    path.write_text(SECRET + "\n")
    path.chmod(0o600)
    return path


def provider_snapshot(status="up", at=NOW):
    return {"schema_version": 1, "provider": "healthchecks.io", "provider_unique_key": "b" * 40,
            "check_identity_sha256": hashlib.sha256(SECRET.encode()).hexdigest(),
            "observed_at": evidence.stamp(at), "status": status, "source": "readonly-management-api-v3",
            "secret_material_recorded": False, **evidence.POLICY}


@pytest.mark.parametrize("body", [b"OK", b"OK\n"])
def test_report_accepts_only_success_and_creates_once(credential, tmp_path, body):
    calls = []
    def transport(path, timeout):
        calls.append(path)
        return reporter.TransportResult(200, body)
    output = tmp_path / "event.json"
    result = reporter.report_deadman_signal(status="failure", credential_path=credential,
        receipt_path=output, transport=transport, now=lambda: NOW, environ={})
    assert result["delivery_accepted"] is True
    assert calls == [f"/{SECRET}/fail"]
    assert SECRET not in output.read_text()
    with pytest.raises(reporter.DeadmanError):
        reporter.report_deadman_signal(status="success", credential_path=credential,
                                      receipt_path=output, transport=transport)
    assert len(calls) == 1


@pytest.mark.parametrize("status,body", [(200, b"OK (not found)"), (200, b"OK (rate limited)"),
                                         (200, b"x" * 65), (302, b"OK"), (500, b"OK")])
def test_rejected_provider_response_leaves_failed_sanitized_receipt(credential, tmp_path, status, body):
    output = tmp_path / "failed.json"
    with pytest.raises(reporter.DeadmanError):
        reporter.report_deadman_signal(status="success", credential_path=credential, receipt_path=output,
            transport=lambda *_: reporter.TransportResult(status, body))
    assert json.loads(output.read_text())["overall_pass"] is False
    assert SECRET not in output.read_text()


def test_transport_exception_never_discloses_request(credential, tmp_path):
    def transport(*_):
        raise TimeoutError("https://hc-ping.com/" + SECRET)
    with pytest.raises(reporter.DeadmanError) as error:
        reporter.report_deadman_signal(status="success", credential_path=credential,
                                      receipt_path=tmp_path / "timeout.json", transport=transport)
    assert SECRET not in str(error.value)
    assert "timeout" == json.loads((tmp_path / "timeout.json").read_text())["transport_error"]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "mode", "oversize", "fifo"])
def test_unsafe_credentials_fail_before_network(credential, tmp_path, kind):
    if kind == "symlink":
        link = tmp_path / "link"
        link.symlink_to(credential)
        credential = link
    elif kind == "hardlink":
        os.link(credential, tmp_path / "link")
    elif kind == "mode":
        credential.chmod(0o644)
    elif kind == "oversize":
        credential.write_text("x" * 100)
    else:
        credential.unlink()
        os.mkfifo(credential)
    with pytest.raises(reporter.DeadmanError):
        reporter.load_ping_uuid(credential)


def test_snapshot_policy_and_freshness():
    evidence.validate_snapshot(provider_snapshot(), statuses={"up"}, now=NOW)
    for changes in ({"manual_resume": True}, {"filter_http_body": True}, {"methods": ""},
                    {"status": "paused"}, {"grace": 600}, {"uuid": SECRET}):
        with pytest.raises(reporter.DeadmanError):
            evidence.validate_snapshot(provider_snapshot() | changes, statuses={"up"}, now=NOW)
    with pytest.raises(reporter.DeadmanError):
        evidence.validate_snapshot(provider_snapshot(), statuses={"up"}, now=NOW + timedelta(minutes=6))


def test_create_rejects_secrets_and_overwrite(tmp_path):
    path = tmp_path / "record.json"
    for value in ({"uuid": SECRET}, {"value": SECRET}, {"value": "recipient@example.com"}):
        with pytest.raises(reporter.DeadmanError):
            evidence.create(path, value)
    evidence.create(path, {"ok": True})
    with pytest.raises(FileExistsError):
        evidence.create(path, {"ok": False})
    assert evidence.read_json(path) == {"ok": True}


def test_help_is_nonmutating():
    for filename in ("scripts/tools/manage_external_deadman.py", "scripts/tools/external_deadman_reporter.py"):
        result = subprocess.run([sys.executable, filename, "--help"], capture_output=True)
        assert result.returncode == 0


def test_rearm_must_be_scheduled_before_suppression(tmp_path):
    state = tmp_path / "state"
    (state / "drills").mkdir(parents=True)
    snap = tmp_path / "snapshot.json"
    evidence.create(snap, provider_snapshot())
    calls = []
    with mock.patch.object(manager, "STATE", state), mock.patch.object(manager, "preflight"), \
         mock.patch.object(manager, "subject", return_value={}), \
         mock.patch.object(manager, "command", side_effect=lambda *args: calls.append(args) or ""):
        manager.arm_drill("test", snap)
    scheduled = next(i for i, c in enumerate(calls) if c[0] == "systemd-run")
    verified = next(i for i, c in enumerate(calls) if c[:2] == ("systemctl", "is-active"))
    stopped = next(i for i, c in enumerate(calls) if c[:2] == ("systemctl", "stop"))
    assert scheduled < verified < stopped


def test_failed_rearm_admission_never_stops_watchdog(tmp_path):
    state = tmp_path / "state"
    (state / "drills").mkdir(parents=True)
    snap = tmp_path / "snapshot.json"
    evidence.create(snap, provider_snapshot())
    calls = []
    def fail(*args):
        calls.append(args)
        raise reporter.DeadmanError("timer could not be scheduled")
    with mock.patch.object(manager, "STATE", state), mock.patch.object(manager, "preflight"), \
         mock.patch.object(manager, "command", side_effect=fail):
        with pytest.raises(reporter.DeadmanError):
            manager.arm_drill("test", snap)
    assert not any(c[:2] == ("systemctl", "stop") for c in calls)
