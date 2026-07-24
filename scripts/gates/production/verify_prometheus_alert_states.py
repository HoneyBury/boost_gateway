#!/usr/bin/env python3
"""Verify that Prometheus alerts traverse an expected runtime state sequence."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lib.evidence_provenance import build_evidence_provenance


REPO_ROOT = Path(__file__).resolve().parents[3]
VALID_STATES = {"inactive", "pending", "firing", "resolved"}


def parse_alert_sequence(value: str) -> tuple[str, list[str]]:
    name, separator, raw_states = value.partition("=")
    name = name.strip()
    states = [state.strip().lower() for state in raw_states.split(",") if state.strip()]
    if not separator or not name or not states:
        raise argparse.ArgumentTypeError("expected ALERT=state[,state...] syntax")
    invalid = [state for state in states if state not in VALID_STATES]
    if invalid:
        raise argparse.ArgumentTypeError(
            f"unsupported state(s) {', '.join(invalid)}; expected one of {', '.join(sorted(VALID_STATES))}"
        )
    return name, states


def fetch_json(url: str, timeout_seconds: float, bearer_token: str = "") -> dict[str, Any]:
    request = urllib.request.Request(url)
    if bearer_token:
        request.add_header("Authorization", f"Bearer {bearer_token}")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        document = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(document, dict) or document.get("status") != "success":
        raise ValueError("Prometheus API did not return a successful JSON object")
    return document


def alert_states_from_alerts(document: dict[str, Any]) -> dict[str, str]:
    data = document.get("data")
    if not isinstance(data, dict):
        raise ValueError("Prometheus alerts response must contain an object in data")
    alerts = data.get("alerts", [])
    if not isinstance(alerts, list):
        raise ValueError("Prometheus alerts response must contain data.alerts")
    found: dict[str, list[str]] = {}
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        labels = alert.get("labels", {})
        name = labels.get("alertname", "") if isinstance(labels, dict) else ""
        state = str(alert.get("state", "")).lower()
        if name and state in {"pending", "firing"}:
            found.setdefault(str(name), []).append(state)
    return {
        name: "firing" if "firing" in states else "pending"
        for name, states in found.items()
    }


def alert_states_from_rules(document: dict[str, Any]) -> dict[str, str]:
    data = document.get("data")
    if not isinstance(data, dict):
        raise ValueError("Prometheus rules response must contain an object in data")
    groups = data.get("groups", [])
    if not isinstance(groups, list):
        raise ValueError("Prometheus rules response must contain data.groups")
    found: dict[str, list[str]] = {}
    for group in groups:
        rules = group.get("rules", []) if isinstance(group, dict) else []
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("type") not in {None, "alerting"}:
                continue
            name = str(rule.get("name", ""))
            state = str(rule.get("state", "inactive")).lower()
            if name and state in {"inactive", "pending", "firing"}:
                found.setdefault(name, []).append(state)
    precedence = {"inactive": 0, "pending": 1, "firing": 2}
    return {name: max(states, key=precedence.__getitem__) for name, states in found.items()}


def read_states(
    prometheus_url: str,
    api: str,
    timeout_seconds: float,
    bearer_token: str,
) -> tuple[dict[str, str], str]:
    base_url = prometheus_url.rstrip("/")
    endpoints = [api] if api != "auto" else ["alerts", "rules"]
    errors: list[str] = []
    for endpoint in endpoints:
        try:
            document = fetch_json(
                f"{base_url}/api/v1/{endpoint}", timeout_seconds, bearer_token
            )
            parser = alert_states_from_alerts if endpoint == "alerts" else alert_states_from_rules
            return parser(document), endpoint
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("; ".join(errors))


def verify_sequences(
    alert_sequences: list[tuple[str, list[str]]],
    *,
    prometheus_url: str,
    api: str = "auto",
    poll_interval_seconds: float = 1.0,
    state_timeout_seconds: float = 120.0,
    overall_timeout_seconds: float | None = None,
    request_timeout_seconds: float = 5.0,
    bearer_token: str = "",
) -> dict[str, Any]:
    started = time.monotonic()
    overall_timeout = overall_timeout_seconds or state_timeout_seconds * max(
        len(states) for _, states in alert_sequences
    )
    trackers: dict[str, dict[str, Any]] = {
        name: {
            "name": name,
            "expected_states": states,
            "matched_states": [],
            "observations": [],
            "status": "running",
            "failure": "",
            "index": 0,
            "state_started": started,
            "seen_active": False,
            "last_raw_state": "inactive",
        }
        for name, states in alert_sequences
    }
    api_endpoints_used: set[str] = set()
    fetch_errors: list[dict[str, Any]] = []

    while any(tracker["status"] == "running" for tracker in trackers.values()):
        now = time.monotonic()
        if now - started > overall_timeout:
            for tracker in trackers.values():
                if tracker["status"] == "running":
                    tracker["status"] = "failed"
                    tracker["failure"] = "overall timeout exceeded"
            break
        try:
            states, endpoint = read_states(
                prometheus_url, api, request_timeout_seconds, bearer_token
            )
            api_endpoints_used.add(endpoint)
        except RuntimeError as exc:
            fetch_errors.append({"elapsed_seconds": round(now - started, 3), "error": str(exc)})
            states = {}
            endpoint = "unavailable"

        for name, tracker in trackers.items():
            if tracker["status"] != "running":
                continue
            raw_state = states.get(name, "inactive") if endpoint != "unavailable" else "unknown"
            state = raw_state
            if (
                raw_state == "inactive"
                and tracker["seen_active"]
                and tracker["last_raw_state"] in {"pending", "firing"}
            ):
                state = "resolved"
            if raw_state in {"pending", "firing"}:
                tracker["seen_active"] = True
            if raw_state != "unknown":
                tracker["last_raw_state"] = raw_state

            expected = tracker["expected_states"][tracker["index"]]
            observation = {
                "elapsed_seconds": round(now - started, 3),
                "state": state,
                "raw_state": raw_state,
                "expected_state": expected,
                "api": endpoint,
            }
            if not tracker["observations"] or any(
                tracker["observations"][-1].get(key) != observation[key]
                for key in ("state", "raw_state", "expected_state", "api")
            ):
                tracker["observations"].append(observation)

            if state == expected:
                tracker["matched_states"].append(
                    {
                        "state": expected,
                        "elapsed_seconds": round(now - started, 3),
                        "wait_seconds": round(now - tracker["state_started"], 3),
                    }
                )
                tracker["index"] += 1
                tracker["state_started"] = now
                if tracker["index"] == len(tracker["expected_states"]):
                    tracker["status"] = "passed"
                continue

            future_states = tracker["expected_states"][tracker["index"] + 1 :]
            if state in future_states and state not in {"inactive", "unknown"}:
                tracker["status"] = "failed"
                tracker["failure"] = f"observed {state} before required {expected}"
            elif now - tracker["state_started"] > state_timeout_seconds:
                tracker["status"] = "failed"
                tracker["failure"] = f"timed out waiting for {expected}"

        if any(tracker["status"] == "running" for tracker in trackers.values()):
            time.sleep(poll_interval_seconds)

    completed = time.monotonic()
    checks = []
    for tracker in trackers.values():
        tracker.pop("index", None)
        tracker.pop("state_started", None)
        tracker.pop("seen_active", None)
        tracker.pop("last_raw_state", None)
        checks.append(tracker)
    passed = all(check["status"] == "passed" for check in checks)
    return {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": passed,
        "passed": passed,
        "failed_category": "" if passed else "prometheus_alert_runtime",
        "failed_step": next((check["name"] for check in checks if check["status"] != "passed"), ""),
        "duration_seconds": round(completed - started, 3),
        "configuration": {
            "prometheus_url": prometheus_url,
            "api": api,
            "poll_interval_seconds": poll_interval_seconds,
            "state_timeout_seconds": state_timeout_seconds,
            "overall_timeout_seconds": overall_timeout,
            "request_timeout_seconds": request_timeout_seconds,
        },
        "api_endpoints_used": sorted(api_endpoints_used),
        "fetch_errors": fetch_errors,
        "total_checks": len(checks),
        "failed_checks": sum(check["status"] != "passed" for check in checks),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--api", choices=("auto", "alerts", "rules"), default="auto")
    parser.add_argument(
        "--alert-sequence",
        action="append",
        required=True,
        type=parse_alert_sequence,
        metavar="ALERT=STATE[,STATE...]",
    )
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--state-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--overall-timeout-seconds", type=float)
    parser.add_argument("--request-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--bearer-token", default="")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/prometheus-alert-runtime-summary.json",
    )
    args = parser.parse_args()
    for argument in (
        "poll_interval_seconds",
        "state_timeout_seconds",
        "request_timeout_seconds",
    ):
        if getattr(args, argument) <= 0:
            parser.error(f"--{argument.replace('_', '-')} must be positive")
    if args.overall_timeout_seconds is not None and args.overall_timeout_seconds <= 0:
        parser.error("--overall-timeout-seconds must be positive")

    summary = verify_sequences(
        args.alert_sequence,
        prometheus_url=args.prometheus_url,
        api=args.api,
        poll_interval_seconds=args.poll_interval_seconds,
        state_timeout_seconds=args.state_timeout_seconds,
        overall_timeout_seconds=args.overall_timeout_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        bearer_token=args.bearer_token,
    )
    summary["provenance"] = build_evidence_provenance(
        REPO_ROOT,
        build_configuration=args.configuration,
    )
    summary["artifacts"] = {"summary_path": str(args.summary_path)}
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"prometheus alert runtime: {'PASS' if summary['passed'] else 'FAIL'} "
        f"({summary['total_checks'] - summary['failed_checks']}/{summary['total_checks']} checks)"
    )
    print(f"summary: {args.summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
