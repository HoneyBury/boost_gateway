#!/usr/bin/env python3
"""Install, admit, drill and attest the independent external heartbeat."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
try:
    from scripts.lib import deadman_evidence as evidence
    from scripts.lib.external_deadman import DeadmanError, load_ping_uuid
except ModuleNotFoundError:  # installed flat layout
    import deadman_evidence as evidence
    from external_deadman import DeadmanError, load_ping_uuid

CONFIG = Path("/etc/boost-gateway-external-deadman")
STATE = Path("/var/lib/boost-gateway-external-deadman")
LIBEXEC = Path("/usr/local/libexec/boost-gateway-deadman")
SYSTEMD = Path("/etc/systemd/system")
DROPIN = SYSTEMD / "boost-gateway-external-canary@watchdog.service.d/50-deadman.conf"
TIMER = "boost-gateway-external-canary-watchdog.timer"
UNIT = "boost-gateway-external-deadman@.service"
CANDIDATE = Path("/etc/boost-gateway-canary/deployment-record.json")
FILES = ("scripts/lib/external_deadman.py", "scripts/lib/deadman_evidence.py",
         "scripts/tools/external_deadman_reporter.py", "scripts/tools/manage_external_deadman.py")


def command(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    evidence.require(result.returncode == 0, "host command failed; inspect governed unit status")
    return result.stdout.strip()


def root() -> None:
    evidence.require(os.geteuid() == 0 and sys.platform == "linux", "requires root on Linux observer")


def host_digest() -> str:
    return hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()


def subject(snapshot: dict) -> dict:
    return {"canary_host_id_sha256": host_digest(),
            "check_identity_sha256": snapshot["check_identity_sha256"],
            "reporter_sha256": evidence.digest(LIBEXEC / "external_deadman_reporter.py"),
            "service_unit_sha256": evidence.digest(SYSTEMD / UNIT),
            "watchdog_dropin_sha256": evidence.digest(DROPIN),
            "provider_contract_sha256": hashlib.sha256(json.dumps(evidence.POLICY, sort_keys=True).encode()).hexdigest(),
            "candidate_record_sha256": evidence.digest(CANDIDATE)}


def preflight(snapshot_path: Path, *, active: bool) -> dict:
    root()
    snap = evidence.read_json(snapshot_path)
    evidence.validate_snapshot(snap, statuses={"up"} if active else {"new", "up"})
    credential = CONFIG / "ping_uuid"
    info = credential.lstat()
    evidence.require(info.st_uid == 0 and info.st_mode & 0o777 == 0o600, "unsafe source credential")
    identity = hashlib.sha256(load_ping_uuid(credential, allowed_owner_uids={0}).encode()).hexdigest()
    evidence.require(identity == snap["check_identity_sha256"], "provider credential identity mismatch")
    manifest = evidence.read_json(CONFIG / "installation.json")
    for filename, expected in manifest["files"].items():
        target = Path(filename)
        evidence.require(target.is_absolute() and target.is_relative_to(LIBEXEC)
                         or target == SYSTEMD / UNIT, "invalid installation target")
        evidence.secure_read(target)
        evidence.require(target.stat().st_uid == 0 and evidence.digest(target) == expected, "installed artifact drift")
    for timer in (TIMER, "boost-gateway-external-canary.timer"):
        command("systemctl", "is-active", "--quiet", timer)
        command("systemctl", "is-enabled", "--quiet", timer)
    if active:
        evidence.require(evidence.digest(DROPIN) == manifest["dropin_sha256"], "watchdog binding drift")
        properties = command("systemctl", "show", "boost-gateway-external-canary@watchdog.service",
                             "--property=OnSuccess,OnFailure,ExecStart")
        evidence.require("boost-gateway-external-deadman@success.service" in properties
                         and "boost-gateway-external-deadman@failure.service" in properties
                         and "--require-successful-sample" in properties, "watchdog binding not loaded")
        reporter_result = command("systemctl", "show", "boost-gateway-external-deadman@success.service",
                                  "--property=Result", "--value")
        evidence.require(reporter_result == "success", "last success reporter invocation failed")
        events = sorted((STATE / "events").glob("*-success-*.json"))
        evidence.require(bool(events), "no natural success heartbeat receipt")
        event = evidence.read_json(events[-1])
        evidence.require(event.get("signal_status") == "success" and event.get("delivery_accepted") is True
                         and event.get("overall_pass") is True and event.get("provider_http_status") == 200
                         and event.get("check_identity_sha256") == identity, "invalid last heartbeat receipt")
        age = (evidence.utcnow() - evidence.instant(event.get("observed_at"))).total_seconds()
        evidence.require(0 <= age <= 90, "last heartbeat receipt is stale or future-dated")
    return {"schema_version": 1, "overall_pass": True, "created_at": evidence.stamp(),
            "active": active, "check_identity_sha256": identity,
            "host_id_sha256": host_digest(), "secret_material_recorded": False}


def install(checkout: Path) -> None:
    root()
    evidence.require(not DROPIN.exists(), "already active; use reviewed upgrade procedure")
    evidence.require(not (CONFIG / "installation.json").exists(), "installation exists; preserve it for review")
    revision = command("git", "-C", str(checkout), "rev-parse", "HEAD")
    evidence.require(len(revision) == 40, "invalid checkout revision")
    evidence.require(not command("git", "-C", str(checkout), "status", "--porcelain"), "checkout must be clean")
    command("git", "-C", str(checkout), "merge-base", "--is-ancestor", revision, "origin/main")
    sources = {checkout / item: LIBEXEC / Path(item).name for item in FILES}
    sources[checkout / "deploy/systemd" / UNIT] = SYSTEMD / UNIT
    sources[checkout / "deploy/systemd/boost-gateway-external-canary-watchdog-deadman.conf"] = LIBEXEC / "watchdog.conf"
    for directory in (CONFIG, STATE, LIBEXEC, STATE / "events", STATE / "drills", STATE / "attestations"):
        evidence.require(not directory.is_symlink(), "unsafe installation directory")
        directory.mkdir(mode=0o750, parents=True, exist_ok=True)
        os.chown(directory, 0, 0)
        os.chmod(directory, 0o750)
    for source, target in sources.items():
        evidence.require(source.is_file() and not source.is_symlink() and not target.exists(), "installation target exists or source missing")
    for source, target in sources.items():
        with target.open("xb") as output, source.open("rb") as stream:
            shutil.copyfileobj(stream, output, 65536)
        os.chmod(target, 0o644)
    # Upgrade the observer script only at activation; production runtime stays frozen.
    source = checkout / "scripts/tools/external_business_canary.py"
    with (LIBEXEC / "external_business_canary.py").open("xb") as output, source.open("rb") as stream:
        shutil.copyfileobj(stream, output, 65536)
    os.chmod(LIBEXEC / "external_business_canary.py", 0o644)
    evidence.create(CONFIG / "installation.json", {
        "schema_version": 1, "revision": revision, "created_at": evidence.stamp(),
        "files": {str(target): evidence.digest(target) for target in sources.values()},
        "canary_sha256": evidence.digest(LIBEXEC / "external_business_canary.py"),
        "dropin_sha256": evidence.digest(LIBEXEC / "watchdog.conf")})
    command("systemd-analyze", "verify", str(SYSTEMD / UNIT))
    command("systemctl", "daemon-reload")


def activate(snapshot_path: Path) -> None:
    preflight(snapshot_path, active=False)
    evidence.require(not DROPIN.exists(), "watchdog already bound")
    manifest = evidence.read_json(CONFIG / "installation.json")
    staged = LIBEXEC / "external_business_canary.py"
    evidence.require(evidence.digest(staged) == manifest["canary_sha256"], "staged canary drift")
    target = Path("/usr/local/libexec/boost-gateway-canary/external_business_canary.py")
    rollback = LIBEXEC / "external_business_canary.previous.py"
    evidence.require(not rollback.exists(), "activation rollback already exists")
    shutil.copy2(target, rollback)
    # The timer is not restarted; the next natural invocation uses the new binding.
    staging = target.with_suffix(".deadman-stage")
    evidence.require(not staging.exists(), "activation stage already exists")
    try:
        shutil.copy2(staged, staging)
        os.chown(staging, target.stat().st_uid, target.stat().st_gid)
        os.chmod(staging, target.stat().st_mode & 0o777)
        os.replace(staging, target)
        DROPIN.parent.mkdir(mode=0o755, exist_ok=True)
        with DROPIN.open("xb") as output:
            output.write((LIBEXEC / "watchdog.conf").read_bytes())
        command("systemctl", "daemon-reload")
    except Exception:
        shutil.copy2(rollback, target)
        if DROPIN.exists():
            DROPIN.unlink()
        command("systemctl", "daemon-reload")
        raise


def arm_drill(drill_id: str, snapshot_path: Path) -> None:
    preflight(snapshot_path, active=True)
    evidence.require(bool(evidence.NAME.fullmatch(drill_id)), "invalid drill ID")
    directory = STATE / "drills" / drill_id
    directory.mkdir(mode=0o750)
    snap = evidence.read_json(snapshot_path)
    evidence.create(directory / "before.json", snap)
    due = evidence.utcnow() + timedelta(minutes=8)
    unit = f"boost-gateway-deadman-rearm-{drill_id}"
    command("systemd-run", f"--unit={unit}", f"--on-calendar={evidence.calendar_timestamp(due)}",
            "--timer-property=AccuracySec=1s", "--timer-property=Persistent=true",
            "--property=Type=oneshot", "--property=TimeoutStartSec=60",
            "/usr/bin/python3", str(LIBEXEC / "manage_external_deadman.py"), "rearm", "--drill-id", drill_id)
    command("systemctl", "is-active", "--quiet", unit + ".timer")
    evidence.create(directory / "armed.json", {"created_at": evidence.stamp(), "rearm_at": evidence.stamp(due),
                    "subject": subject(snap), "overall_pass": True, "timer": unit + ".timer"})
    # The recovery timer exists and is active before heartbeat suppression.
    try:
        command("systemctl", "stop", TIMER)
        # Never terminate a running watchdog: that could dispatch an explicit /fail.
        # Let the finite in-flight watchdog and its reporter drain before starting
        # the missing-heartbeat clock. The independent rearm is already scheduled.
        deadline = time.monotonic() + 75
        units = ("boost-gateway-external-canary@watchdog.service",
                 "boost-gateway-external-deadman@success.service",
                 "boost-gateway-external-deadman@failure.service")
        while True:
            states = [command("systemctl", "show", unit, "--property=ActiveState", "--value") for unit in units]
            if all(state in {"inactive", "failed"} for state in states):
                break
            evidence.require(time.monotonic() < deadline, "in-flight heartbeat did not drain")
            time.sleep(1)
        evidence.require(command("systemctl", "show", units[0], "--property=Result", "--value") == "success",
                         "watchdog failed while arming the drill")
        evidence.create(directory / "stopped.json", {"created_at": evidence.stamp(), "overall_pass": True})
    except Exception:
        command("systemctl", "start", TIMER)
        raise


def rearm(drill_id: str) -> None:
    root()
    evidence.require(bool(evidence.NAME.fullmatch(drill_id)), "invalid drill ID")
    directory = STATE / "drills" / drill_id
    armed = evidence.read_json(directory / "armed.json")
    unit = f"boost-gateway-deadman-rearm-{drill_id}.service"
    invocation = os.environ.get("INVOCATION_ID", "")
    evidence.require(bool(invocation) and invocation == command("systemctl", "show", unit,
                     "--property=InvocationID", "--value"), "rearm must execute in its scheduled systemd service")
    triggered = command("systemctl", "show", armed["timer"], "--property=LastTriggerUSec", "--value")
    evidence.require(triggered not in {"", "n/a", "0"}, "automatic recovery timer has not triggered")
    evidence.require(evidence.utcnow() >= evidence.instant(armed["rearm_at"]), "rearm cannot run early")
    command("systemctl", "start", TIMER)
    command("systemctl", "is-active", "--quiet", TIMER)
    evidence.create(directory / "rearmed.json", {"created_at": evidence.stamp(), "overall_pass": True,
                    "automatic": True, "arm_sha256": evidence.digest(directory / "armed.json")})


def attest(drill_id: str, down: Path, up: Path, success: Path, deliveries: Path) -> Path:
    root()
    evidence.require(bool(evidence.NAME.fullmatch(drill_id)), "invalid drill ID")
    directory = STATE / "drills" / drill_id
    paths = {"before": directory / "before.json", "armed": directory / "armed.json",
             "stopped": directory / "stopped.json", "rearmed": directory / "rearmed.json",
             "down": down, "up": up, "success": success}
    items = {role: evidence.read_json(path) for role, path in paths.items()}
    before, final = items["before"], items["up"]
    for role, statuses in (("before", {"up"}), ("down", {"down"}), ("up", {"up"})):
        snap = items[role]
        evidence.validate_snapshot(snap, statuses=statuses, now=evidence.instant(snap["observed_at"]))
        evidence.require(snap["check_identity_sha256"] == before["check_identity_sha256"]
                         and snap["provider_unique_key"] == before["provider_unique_key"], "drill check changed")
    evidence.validate_snapshot(final, statuses={"up"})
    expected = subject(final)
    evidence.require(items["armed"]["subject"] == expected, "drill subject drift")
    restored = items["rearmed"]
    evidence.require(restored["overall_pass"] is True and restored["automatic"] is True
                     and restored["arm_sha256"] == evidence.digest(paths["armed"]), "automatic rearm not proven")
    evidence.require(evidence.instant(restored["created_at"]) >= evidence.instant(items["armed"]["rearm_at"]),
                     "rearm executed before its scheduled time")
    stopped_at = evidence.instant(items["stopped"]["created_at"])
    rearmed_at = evidence.instant(restored["created_at"])
    for path in (STATE / "events").glob("*.json"):
        event = evidence.read_json(path)
        event_at = evidence.instant(event["observed_at"])
        evidence.require(not (stopped_at < event_at < rearmed_at and event.get("delivery_accepted") is True),
                         "heartbeat was delivered during the missing-heartbeat drill")
    event = items["success"]
    evidence.require(event["signal_status"] == "success" and event["delivery_accepted"] is True
                     and event["overall_pass"] is True and event["provider_http_status"] == 200
                     and event["check_identity_sha256"] == final["check_identity_sha256"]
                     and evidence.instant(restored["created_at"]) <= evidence.instant(event["observed_at"])
                     <= evidence.instant(final["observed_at"]), "recovery heartbeat not proven")
    result = {"schema_version": 1, "attestation_id": drill_id, "created_at": evidence.stamp(),
              "create_only": True, "overall_pass": True, "provider": "healthchecks.io", "subject": expected,
              "drill": {"failure_mode": "missing-heartbeat", "armed_at": items["armed"]["created_at"],
                        "heartbeat_stopped_at": items["stopped"]["created_at"],
                        "provider_down_at": items["down"]["observed_at"], "rearmed_at": restored["created_at"],
                        "provider_up_at": final["observed_at"], "provider_unique_key": final["provider_unique_key"],
                        "final_status": "up"}, "deliveries": evidence.read_json(deliveries),
              "artifacts": [{"role": role, "basename": path.name, "size": path.stat().st_size,
                             "sha256": evidence.digest(path)} for role, path in paths.items()],
              "secret_material_recorded": False}
    evidence.validate_attestation(result)
    payload_hash = hashlib.sha256((json.dumps(result, sort_keys=True, indent=2) + "\n").encode()).hexdigest()
    destination = STATE / "attestations" / f"{payload_hash}-{drill_id}.json"
    evidence.create(destination, result)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    install_parser = sub.add_parser("install-shadow")
    install_parser.add_argument("--checkout", type=Path, required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--credential-file", type=Path, required=True)
    snap.add_argument("--api-key-file", type=Path, required=True)
    snap.add_argument("--output", type=Path, required=True)
    for action in ("preflight", "activate", "arm-drill"):
        entry = sub.add_parser(action)
        entry.add_argument("--snapshot", type=Path, required=True)
        if action == "preflight":
            entry.add_argument("--active", action="store_true")
            entry.add_argument("--output", type=Path, required=True)
        if action == "arm-drill":
            entry.add_argument("--drill-id", required=True)
    sub.add_parser("rearm").add_argument("--drill-id", required=True)
    attester = sub.add_parser("attest")
    attester.add_argument("--drill-id", required=True)
    for field in ("down", "up", "success", "deliveries"):
        attester.add_argument("--" + field, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            evidence.create(args.output, evidence.snapshot(args.credential_file, args.api_key_file))
        elif args.command == "install-shadow":
            install(args.checkout.resolve())
        elif args.command == "preflight":
            evidence.create(args.output, preflight(args.snapshot, active=args.active))
        elif args.command == "activate":
            activate(args.snapshot)
        elif args.command == "arm-drill":
            arm_drill(args.drill_id, args.snapshot)
        elif args.command == "rearm":
            rearm(args.drill_id)
        else:
            print(attest(args.drill_id, args.down, args.up, args.success, args.deliveries))
    except (DeadmanError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("external dead-man: FAIL; no admission granted", file=sys.stderr)
        return 1
    print("external dead-man: PASS " + args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
