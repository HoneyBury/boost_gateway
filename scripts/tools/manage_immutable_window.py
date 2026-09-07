#!/usr/bin/env python3
"""Declare, bind, audit and finalize one immutable TODO-0017 interval."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
try:
    from scripts.lib import deadman_evidence as io
    from scripts.lib import immutable_window as contract
except ModuleNotFoundError:
    import deadman_evidence as io
    import immutable_window as contract

ROOT = Path("/var/lib/boost-gateway-immutable-windows")
CANARY = Path("/var/lib/boost-gateway-canary")
RECORD = Path("/etc/boost-gateway-canary/deployment-record.json")
PYTHON = "/opt/boost-gateway-canary/venv/bin/python"
CANARY_CLI = "/usr/local/libexec/boost-gateway-canary/external_business_canary.py"
SYSTEMD = Path("/etc/systemd/system")
INSTALLED = Path("/usr/local/libexec/boost-gateway-window")


def install(checkout: Path) -> None:
    revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    clean = subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain"], text=True)
    io.require(not clean, "install requires a clean reviewed checkout")
    run("git", "-C", str(checkout), "merge-base", "--is-ancestor", revision, "origin/main")
    io.require(not INSTALLED.exists(), "window installation already exists; preserve it for review")
    sources = [checkout / "scripts/tools/manage_immutable_window.py",
               checkout / "scripts/lib/immutable_window.py", checkout / "scripts/lib/deadman_evidence.py",
               checkout / "scripts/lib/external_deadman.py"]
    io.require(all(path.is_file() and not path.is_symlink() for path in sources), "missing installation source")
    INSTALLED.mkdir(mode=0o755)
    for source in sources:
        with source.open("rb") as stream, (INSTALLED / source.name).open("xb") as output:
            shutil.copyfileobj(stream, output, 65536)
        os.chmod(INSTALLED / source.name, 0o644)


def tool_digests() -> dict:
    return {"finalizer": io.digest(Path(__file__)), "contract": io.digest(Path(contract.__file__)),
            "evidence": io.digest(Path(io.__file__)), "canary": io.digest(Path(CANARY_CLI))}


def run(*args: str, timeout: int = 30) -> None:
    result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    io.require(result.returncode == 0, "governed host command failed")


def identity() -> str:
    return hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()


def report_paths(path: Path) -> dict[str, Path]:
    values = io.read_json(path)
    io.require(all(isinstance(value, str) and Path(value).is_absolute() for value in values.values()),
               "report map must contain absolute local paths")
    return {key: Path(value) for key, value in values.items()}


def load(directory: Path) -> dict:
    value = io.read_json(directory / "declaration.json")
    contract.validate_declaration(value)
    io.require(not (directory / "superseded.json").exists(), "window is permanently superseded")
    io.require(value["host_boundary"]["canary_host_id_sha256"] == identity()
               and value["candidate_record_sha256"] == io.digest(RECORD), "live identity drift")
    return value


def declaration(args: argparse.Namespace) -> Path:
    now = io.utcnow()
    reports = contract.admission(report_paths(args.reports), now=now)
    attestation = io.read_json(args.attestation)
    value = contract.declare(start=io.instant(args.start), now=now, record=io.read_json(RECORD),
                             record_sha=io.digest(RECORD), host_id=identity(), endpoint=args.endpoint,
                             admission_reports=reports, attestation=attestation,
                             attestation_sha=io.digest(args.attestation), sdk_version="4.2.0")
    # A new declaration cannot silently replace another admitted active interval.
    for existing in ROOT.glob("*/declaration.json"):
        io.require((existing.parent / "superseded.json").exists()
                   or (existing.parent / "final.json").exists(), "an unfinished declaration already exists")
    directory = ROOT / value["window_id"]
    directory.mkdir(mode=0o750)
    io.create(directory / "declaration.json", value)
    io.create(directory / "maintenance.json", {"windows": []})
    return directory


def bind(directory: Path, issue_comment: str) -> None:
    value = load(directory)
    io.require(io.utcnow() < io.instant(value["start"]), "cannot bind a window retroactively")
    io.require(issue_comment.isdigit(), "record the Issue 31 declaration comment ID")
    # Read back the public declaration independently; a local assertion is insufficient.
    result = subprocess.run(["gh", "api", f"repos/HoneyBury/boost_gateway/issues/comments/{issue_comment}"],
                            capture_output=True, text=True, timeout=30, check=False)
    io.require(result.returncode == 0, "cannot verify public declaration")
    comment = json.loads(result.stdout)
    declaration_sha = io.digest(directory / "declaration.json")
    io.require(comment.get("issue_url", "").endswith("/issues/31")
               and all(text in comment.get("body", "") for text in
                       (value["start"], value["end"], declaration_sha, value["candidate"]["deployment_id"])),
               "public declaration does not match the immutable record")
    timer_name = value["window_id"] + "-finalizer"
    executable = Path(__file__).resolve()
    io.require(executable.parent == INSTALLED, "bind must use the governed installed finalizer")
    service = SYSTEMD / f"{timer_name}.service"
    timer = SYSTEMD / f"{timer_name}.timer"
    io.require(not service.exists() and not timer.exists(), "finalizer units already exist")
    # Paths are validated before inclusion in systemd command syntax.
    io.require(all(char not in str(directory) + str(executable) for char in "\n\r\t %\""), "unsafe systemd path")
    service_text = ("[Unit]\nDescription=Fixed-end TODO-0017 aggregate\n[Service]\nType=oneshot\n"
                    f"ExecStart=/usr/bin/python3 {executable} aggregate --directory {directory}\n"
                    "TimeoutStartSec=900\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nUMask=0027\n"
                    "NoNewPrivileges=yes\nProtectHome=yes\nProtectSystem=strict\n"
                    f"ReadWritePaths={ROOT}\n")
    due = io.instant(value["end"]) + timedelta(minutes=1)
    timer_text = ("[Unit]\nDescription=Immutable 30-day fixed-end timer\n[Timer]\n"
                  f"OnCalendar={io.stamp(due)}\nPersistent=true\nAccuracySec=1s\n"
                  f"Unit={timer_name}.service\n[Install]\nWantedBy=timers.target\n")
    for path, text in ((service, service_text), (timer, timer_text)):
        with path.open("x") as stream:
            stream.write(text)
        os.chmod(path, 0o644)
    run("systemd-analyze", "verify", str(service), str(timer))
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", timer.name)
    run("systemctl", "is-active", "--quiet", timer.name)
    io.require(io.utcnow() < io.instant(value["start"]), "binding missed the declared start; supersede it")
    io.create(directory / "binding.json", {"created_at": io.stamp(), "declaration_sha256": declaration_sha,
              "issue_comment_id": issue_comment, "service_sha256": io.digest(service),
              "timer_sha256": io.digest(timer), "tools": tool_digests(),
              "overall_pass": True})


def startup(directory: Path) -> None:
    value = load(directory)
    binding = io.read_json(directory / "binding.json")
    now = io.utcnow()
    io.require(io.instant(value["start"]) <= now <= io.instant(value["start"]) + timedelta(minutes=2),
               "startup audit must run in the first two minutes")
    io.require(binding["declaration_sha256"] == io.digest(directory / "declaration.json"), "declaration binding changed")
    run("systemctl", "is-active", "--quiet", value["window_id"] + "-finalizer.timer")
    matches = []
    for path in (CANARY / "samples").glob("**/*.json"):
        sample = io.read_json(path, owner_uid=CANARY.stat().st_uid)
        if sample.get("scheduled_minute") == value["start"]:
            matches.append(sample)
    io.require(len(matches) == 1 and matches[0].get("overall_pass") is True
               and matches[0].get("candidate") == value["candidate"]
               and matches[0].get("host_boundary") == value["host_boundary"], "Day 0 first sample not proven")
    io.create(directory / "startup.json", {"created_at": io.stamp(now), "overall_pass": True,
              "declaration_sha256": binding["declaration_sha256"]})


def aggregate(directory: Path) -> None:
    value = load(directory)
    io.require(io.utcnow() >= io.instant(value["end"]), "fixed end has not elapsed")
    binding = io.read_json(directory / "binding.json")
    startup_record = io.read_json(directory / "startup.json")
    sha = io.digest(directory / "declaration.json")
    io.require(binding["declaration_sha256"] == sha == startup_record["declaration_sha256"]
               and startup_record["overall_pass"] is True, "window was not admitted at startup")
    io.require(binding["tools"] == tool_digests(), "finalizer artifact drift")
    timer_name = value["window_id"] + "-finalizer"
    io.require(binding["service_sha256"] == io.digest(SYSTEMD / (timer_name + ".service"))
               and binding["timer_sha256"] == io.digest(SYSTEMD / (timer_name + ".timer")), "fixed-end unit drift")
    inputs = contract.sample_manifest(CANARY, value)
    aggregate_path = directory / "aggregate.json"
    if not aggregate_path.exists():
        if (directory / "aggregate-inputs.json").exists():
            io.require(io.read_json(directory / "aggregate-inputs.json") == inputs, "retry input drift")
        else:
            io.create(directory / "aggregate-inputs.json", inputs)
        run(PYTHON, CANARY_CLI, "--evidence-root", str(CANARY), "aggregate", "--window", "30d",
            "--end", value["end"], "--maintenance-windows", str(directory / "maintenance.json"),
            "--output", str(aggregate_path), timeout=840)
    io.require(io.read_json(directory / "aggregate-inputs.json") == inputs, "aggregate samples changed")
    io.require(contract.sample_manifest(CANARY, value) == inputs, "samples changed during aggregation")
    contract.validate_aggregate(io.read_json(aggregate_path), value)
    if (directory / "aggregate-receipt.json").exists():
        receipt = io.read_json(directory / "aggregate-receipt.json")
        io.require(receipt["declaration_sha256"] == sha
                   and receipt["aggregate_sha256"] == io.digest(aggregate_path)
                   and receipt["sample_manifest_sha256"] == inputs["sample_manifest_sha256"],
                   "existing aggregate receipt no longer matches")
    else:
        io.create(directory / "aggregate-receipt.json", {"created_at": io.stamp(), "overall_pass": True,
                  "declaration_sha256": sha, "aggregate_sha256": io.digest(aggregate_path), **inputs})


def finalize(directory: Path, reports: Path) -> None:
    aggregate(directory)
    value = load(directory)
    paths = report_paths(reports)
    admitted = contract.admission(paths, now=io.utcnow(), final=True)
    contract.validate_host_window(io.read_json(paths["host_window"]), value)
    # Final closure requires an off-host receipt for this exact fixed-end aggregate.
    offhost = io.read_json(paths["offhost"])
    io.require(offhost.get("aggregate_sha256") == io.digest(directory / "aggregate.json")
               and offhost.get("declaration_sha256") == io.digest(directory / "declaration.json")
               and offhost.get("source_host_id_sha256") != offhost.get("destination_host_id_sha256")
               and offhost.get("readback_verified") is True, "off-host final verification not proven")
    io.create(directory / "final.json", {"created_at": io.stamp(), "overall_pass": True,
              "task": "TODO-0017", "declaration_sha256": io.digest(directory / "declaration.json"),
              "aggregate_sha256": io.digest(directory / "aggregate.json"), "reports": admitted,
              "secret_material_recorded": False, "create_only": True})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install").add_argument("--checkout", type=Path, required=True)
    declare_parser = sub.add_parser("declare")
    declare_parser.add_argument("--start", required=True)
    declare_parser.add_argument("--reports", type=Path, required=True)
    declare_parser.add_argument("--attestation", type=Path, required=True)
    declare_parser.add_argument("--endpoint", default="tcp://100.65.71.117:9201")
    for action in ("bind", "startup-audit", "aggregate", "finalize", "supersede"):
        entry = sub.add_parser(action)
        entry.add_argument("--directory", type=Path, required=True)
        if action == "bind":
            entry.add_argument("--issue-comment-id", required=True)
        if action == "finalize":
            entry.add_argument("--reports", type=Path, required=True)
        if action == "supersede":
            entry.add_argument("--incident-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        io.require(os.geteuid() == 0 and sys.platform == "linux", "requires root on Linux observer")
        io.require(not ROOT.is_symlink(), "unsafe window root")
        ROOT.mkdir(mode=0o750, exist_ok=True)
        lock = os.open(ROOT / ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock, "w"):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == "install":
                install(args.checkout.resolve())
            elif args.command == "declare":
                print(declaration(args))
            else:
                directory = args.directory
                io.require(directory.is_absolute() and directory.parent == ROOT and not directory.is_symlink(),
                           "window directory must be a direct governed child")
                if args.command == "bind":
                    bind(directory, args.issue_comment_id)
                elif args.command == "startup-audit":
                    startup(directory)
                elif args.command == "aggregate":
                    aggregate(directory)
                elif args.command == "finalize":
                    finalize(directory, args.reports)
                else:
                    io.require(bool(io.HEX.fullmatch(args.incident_sha256)), "invalid incident digest")
                    io.create(directory / "superseded.json", {"created_at": io.stamp(), "overall_pass": False,
                              "incident_sha256": args.incident_sha256})
    except (io.DeadmanError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("immutable window: FAIL; interval is not admitted or complete", file=sys.stderr)
        return 1
    print("immutable window: PASS " + args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
