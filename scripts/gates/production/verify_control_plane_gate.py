#!/usr/bin/env python3
"""Run the P5 Kubernetes control-plane and Operator gate."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(
    name: str,
    category: str,
    cmd: list[str],
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "category": category,
            "command": cmd,
            "cwd": str(cwd),
            "timeout_seconds": timeout_seconds,
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout),
            "stderr_tail": tail(exc.stderr),
        }

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "cwd": str(cwd),
        "timeout_seconds": timeout_seconds,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def require_command(name: str) -> None:
    if not shutil.which(name):
        raise FileNotFoundError(f"missing required command: {name}")


def default_go_state_root() -> Path:
    configured = os.environ.get("BOOST_GATEWAY_GO_STATE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()

    runner_temp = os.environ.get("RUNNER_TEMP", "").strip()
    if runner_temp:
        run_id = os.environ.get("GITHUB_RUN_ID", "local").strip() or "local"
        run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1").strip() or "1"
        return Path(runner_temp) / f"boost-gateway-go-{run_id}-{run_attempt}"

    uid = str(os.getuid()) if hasattr(os, "getuid") else os.environ.get("USERNAME", "user")
    return Path(tempfile.gettempdir()) / f"boost-gateway-go-{uid}"


def go_cache_paths(state_root: Path) -> tuple[Path, Path]:
    return state_root / "cache" / "build", state_root / "cache" / "mod"


def detect_local_proxy() -> str:
    configured = os.environ.get("BOOST_GATEWAY_HTTP_PROXY", "").strip()
    if configured:
        return configured
    for port in (10808, 7890, 7897, 7899, 1080, 10809, 8080, 8118):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return ""


def go_environment(state_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    build_cache, mod_cache = go_cache_paths(state_root)
    go_home = state_root / "home"
    env["GOCACHE"] = str(build_cache)
    env["GOMODCACHE"] = str(mod_cache)
    env["GOTELEMETRY"] = "off"
    env["GOTELEMETRYDIR"] = str(state_root / "telemetry")
    env["APPDATA"] = str(go_home / "AppData" / "Roaming")
    env["LOCALAPPDATA"] = str(go_home / "AppData" / "Local")
    env["USERPROFILE"] = str(go_home)
    proxy = detect_local_proxy()
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env["ALL_PROXY"] = proxy
    else:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY"):
            env.pop(key, None)
    Path(env["GOCACHE"]).mkdir(parents=True, exist_ok=True)
    Path(env["GOMODCACHE"]).mkdir(parents=True, exist_ok=True)
    Path(env["GOTELEMETRYDIR"]).mkdir(parents=True, exist_ok=True)
    Path(env["APPDATA"]).mkdir(parents=True, exist_ok=True)
    Path(env["LOCALAPPDATA"]).mkdir(parents=True, exist_ok=True)
    return env


def preflight_control_plane(root: Path, include_envtest: bool, include_kind: bool) -> dict[str, object]:
    checks: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    if include_envtest or include_kind:
        require_command("go")
        checks.append("go")

    if include_envtest:
        if not os.environ.get("KUBEBUILDER_ASSETS"):
            errors.append("KUBEBUILDER_ASSETS is required for --include-envtest")
        else:
            checks.append("KUBEBUILDER_ASSETS")
        require_command("make")
        checks.append("make")

    if include_kind:
        for command in ["kind", "kubectl", "make"]:
            require_command(command)
            checks.append(command)
        completed = subprocess.run(
            ["kind", "get", "clusters"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            errors.append("kind is installed but `kind get clusters` failed")
            if completed.stderr:
                warnings.append(tail(completed.stderr, 1000))
        else:
            checks.append("kind-cluster-access")

    return {
        "name": "control-plane fixed-runner preflight",
        "category": "preflight",
        "command": ["internal-preflight"],
        "cwd": str(root),
        "timeout_seconds": 0,
        "status": "passed" if not errors else "failed",
        "returncode": 0 if not errors else 1,
        "duration_seconds": 0,
        "stdout_tail": "\n".join(f"ok: {check}" for check in checks),
        "stderr_tail": "\n".join([*errors, *warnings]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-dir", type=Path, default=Path("operator/boostgateway-operator"))
    parser.add_argument("--include-go-tests", action="store_true")
    parser.add_argument("--include-envtest", action="store_true")
    parser.add_argument("--include-kind", action="store_true")
    parser.add_argument("--go-test-timeout-seconds", type=int, default=180)
    parser.add_argument("--envtest-timeout-seconds", type=int, default=240)
    parser.add_argument("--kind-timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--go-state-root",
        type=Path,
        default=None,
        help="Go cache/home/telemetry root (default: BOOST_GATEWAY_GO_STATE_ROOT, RUNNER_TEMP, or system temp)",
    )
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/control-plane-gate-summary.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = REPO_ROOT
    operator_dir = args.operator_dir if args.operator_dir.is_absolute() else root / args.operator_dir
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    go_state_root = (args.go_state_root or default_go_state_root()).resolve()
    go_env = go_environment(go_state_root)
    summary: dict[str, object] = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "operator_dir": str(operator_dir),
        "include_go_tests": args.include_go_tests,
        "include_envtest": args.include_envtest,
        "include_kind": args.include_kind,
        "go_state_root": str(go_state_root),
        "go_cache": {
            "build": str(go_cache_paths(go_state_root)[0]),
            "module": str(go_cache_paths(go_state_root)[1]),
        },
        "overall_pass": False,
        "passed": False,
        "failed_category": "",
        "failed_step": "",
        "steps": [],
        "artifacts": {"summary_path": str(summary_path)},
    }

    try:
        if not operator_dir.is_dir():
            raise FileNotFoundError(f"missing operator dir: {operator_dir}")
        preflight = preflight_control_plane(root, args.include_envtest, args.include_kind)
        summary["steps"].append(preflight)
        if preflight["status"] != "passed":
            raise RuntimeError(preflight["name"])

        summary["steps"].append(run_step(
            "Operator static manifest contract",
            "manifests",
            [
                sys.executable,
                str(root / "scripts/gates/k8s/check_operator_manifests.py"),
                "--operator-dir",
                str(operator_dir),
                "--summary-path",
                str(summary_path.parent / "operator-manifests-summary.json"),
            ],
            root,
            30,
        ))
        if args.include_go_tests or args.include_envtest or args.include_kind:
            summary["steps"].append(run_step(
                "Operator fake-client and unit tests",
                "operator",
                ["go", "test", "./..."],
                operator_dir,
                args.go_test_timeout_seconds,
                env=go_env,
            ))
        if args.include_envtest:
            summary["steps"].append(run_step(
                "Operator envtest reconcile tests",
                "envtest",
                ["make", "test-envtest"],
                operator_dir,
                args.envtest_timeout_seconds,
                env=go_env,
            ))
        if args.include_kind:
            summary["steps"].append(run_step(
                "Operator kind status smoke",
                "kind",
                [sys.executable, str(root / "scripts/tools/operator_kind_smoke.py")],
                root,
                args.kind_timeout_seconds,
                env=go_env,
            ))
    except (FileNotFoundError, RuntimeError) as exc:
        failed = next((step for step in summary["steps"] if step.get("status") != "passed"), None)
        if failed:
            summary["failed_category"] = str(failed.get("category", "unknown"))
            summary["failed_step"] = str(failed.get("name", "unknown"))
        else:
            summary["failed_category"] = "discovery"
            summary["failed_step"] = str(exc)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"control-plane gate failed: {exc}", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 1

    failed = next((step for step in summary["steps"] if step.get("status") != "passed"), None)
    if failed:
        summary["failed_category"] = str(failed.get("category", "unknown"))
        summary["failed_step"] = str(failed.get("name", "unknown"))
    else:
        summary["overall_pass"] = True
        summary["passed"] = True

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
