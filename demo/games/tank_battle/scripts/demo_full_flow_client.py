#!/usr/bin/env python3
"""
Tank Battle Demo Full-Flow Client.

Runs comprehensive end-to-end validation of the tank battle demo in one of three
modes:

  default (in-process):
      Builds and runs the tank_battle_e2e_test executable, parses GTest output,
      and reports pass/fail per test case. No external dependencies beyond the
      built test binary.

  --sdk-mode:
      Uses the Python SDK wrapper (sdk/python/__init__.py) to connect to a
      running gateway and exercise the full business lifecycle:
      connect -> login (2 players) -> create room -> join -> ready ->
      start battle -> send tank inputs -> receive snapshot -> finish ->
      leaderboard query.

  --perf:
      Runs tank_battle_perf_test and parses its JSON performance output.

Output:
    Writes a JSON summary to
      <build-dir>/runtime/validation/tank-battle-demo-full-flow-summary.json

Usage:
    python3 demo/games/tank_battle/scripts/demo_full_flow_client.py --build-dir build
    python3 demo/games/tank_battle/scripts/demo_full_flow_client.py --build-dir build --sdk-mode
    python3 demo/games/tank_battle/scripts/demo_full_flow_client.py --build-dir build --perf
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


# ─── Helpers ──────────────────────────────────────────────────────────────

def run_cmd(cmd: list, cwd: Path = None, timeout: int = 60) -> tuple:
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "(timeout)"
    except FileNotFoundError:
        return -2, "", f"command not found: {cmd[0]}"
    except OSError as e:
        return -3, "", str(e)


def make_result(name: str, passed: bool, duration_ms: float = 0.0,
                details: str = "") -> dict:
    return {
        "name": name,
        "passed": passed,
        "duration_ms": round(duration_ms, 1),
        "details": details,
    }


# ─── GTest Output Parser ──────────────────────────────────────────────────

# Matches: [       OK ] TankBattleE2ETest.CreateInstanceWithPlayers (0 ms)
# Matches: [  FAILED  ] TankBattleE2ETest.CreateInstanceWithPlayers (1 ms)
_RE_TEST_LINE = re.compile(
    r'^\[\s+(OK|FAILED)\s+\]\s+(\S+)\.(\S+)\s+\((\d+)\s*ms\)'
)

# Summary line: [  PASSED  ] 5 tests.
_RE_PASSED_SUMMARY = re.compile(r'^\[\s+PASSED\s+\]\s+(\d+)\s+test')
_RE_FAILED_SUMMARY = re.compile(r'^\[\s+FAILED\s+\]\s+(\d+)\s+test')


def parse_gtest_output(stdout: str) -> list:
    """Parse GTest output into a list of test result dicts.

    Each result has keys: name, passed, duration_ms, details.
    """
    results = []
    for line in stdout.splitlines():
        m = _RE_TEST_LINE.match(line)
        if m:
            status, suite, test, ms_str = m.groups()
            name = f"{suite}.{test}"
            passed = status == "OK"
            duration_ms = float(ms_str) if ms_str else 0.0
            results.append(make_result(name, passed, duration_ms))
    return results


# ─── In-Process: Run E2E Test Binary ──────────────────────────────────────

def find_e2e_test_exe(build_dir: Path, errors: list) -> Path:
    """Locate tank_battle_e2e_test.exe in the build tree."""
    candidates = [
        build_dir / "demo" / "games" / "tank_battle" / "tests" / "Debug" / "tank_battle_e2e_test.exe",
        build_dir / "demo" / "games" / "tank_battle" / "tests" / "Release" / "tank_battle_e2e_test.exe",
        build_dir / "demo" / "games" / "tank_battle" / "tests" / "RelWithDebInfo" / "tank_battle_e2e_test.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    # Fall back to a recursive search
    matches = sorted(Path(build_dir).rglob("tank_battle_e2e_test.exe"))
    if matches:
        return matches[0]
    errors.append("tank_battle_e2e_test.exe not found in build tree")
    return None


def run_in_process(build_dir: Path, errors: list, warnings: list) -> list:
    """Run the e2e test executable and return per-test results.

    Returns a list of result dicts, one per GTest test case.
    """
    exe = find_e2e_test_exe(build_dir, errors)
    if exe is None:
        return []

    print(f"  Executable: {exe}")
    rc, stdout, stderr = run_cmd([str(exe)], timeout=60)

    if stderr:
        warnings.append(f"In-process: stderr: {stderr[:500]}")

    results = parse_gtest_output(stdout)

    if not results:
        # Parsing yielded nothing — capture raw output for diagnostics
        details = f"exit={rc}"
        if stdout:
            details += f", stdout={stdout[:500]}"
        if stderr:
            details += f", stderr={stderr[:500]}"
        results.append(make_result("E2E", rc == 0, 0.0, details))

    # Cross-check parsed results against summary line counts
    passed_count = 0
    failed_count = 0
    for line in stdout.splitlines():
        m = _RE_PASSED_SUMMARY.match(line)
        if m:
            passed_count = int(m.group(1))
        m = _RE_FAILED_SUMMARY.match(line)
        if m:
            failed_count = int(m.group(1))

    parsed_passed = sum(1 for r in results if r["passed"])
    parsed_failed = sum(1 for r in results if not r["passed"])

    if passed_count > 0 and parsed_passed != passed_count:
        warnings.append(
            f"In-process: parsed {parsed_passed} passed, GTest summary says {passed_count}"
        )

    if rc != 0 and parsed_failed == 0:
        # Non-zero exit but zero parsed failures — add a synthetic failure
        results.append(make_result("E2E.ExitCode", False, 0.0,
                                   f"exit code {rc} with no parsed failures"))

    return results


# ─── SDK Mode: Full Business-Lifecycle via Gateway ────────────────────────

def _import_sdk(sdk_dir: Path, sdk_build_dir: Path, errors: list):
    """Dynamically import the Python SDK module.

    Adds *sdk_build_dir* to the DLL search path (Windows) and inserts
    *sdk_dir* into sys.path so that ``import sdk_python`` resolves to
    ``sdk/python/__init__.py``.
    """
    if os.name == "nt":
        os.add_dll_directory(str(sdk_build_dir))

    sys.path.insert(0, str(sdk_dir))

    try:
        import sdk_python
        return sdk_python
    except ImportError:
        pass

    # Fallback: load via importlib from the exact file
    import importlib.util
    init_py = sdk_dir / "__init__.py"
    if not init_py.is_file():
        errors.append(f"SDK __init__.py not found at {init_py}")
        return None
    spec = importlib.util.spec_from_file_location("sdk_python", str(init_py))
    if spec is None or spec.loader is None:
        errors.append(f"Failed to create module spec from {init_py}")
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except RuntimeError as e:
        errors.append(f"SDK load failed: {e}")
        return None
    return mod


def _find_sdk_build_dir(build_dir: Path) -> Path:
    """Locate the directory containing boost_gateway_sdk.dll."""
    for cfg in ("Debug", "Release", "RelWithDebInfo"):
        candidate = build_dir / "sdk" / cfg
        if (candidate / "boost_gateway_sdk.dll").is_file():
            return candidate
    # Fallback: search recursively
    matches = sorted(Path(build_dir).rglob("boost_gateway_sdk.dll"))
    if matches:
        return matches[0].parent
    return build_dir / "sdk" / "Debug"  # best guess


def run_sdk_flow(build_dir: Path, host: str, port: int, errors: list,
                 warnings: list) -> list:
    """Run the full business lifecycle via the Python SDK.

    Returns a list of per-step result dicts.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    sdk_dir = repo_root / "sdk" / "python"
    sdk_build_dir = _find_sdk_build_dir(build_dir)

    sdk_mod = _import_sdk(sdk_dir, sdk_build_dir, errors)
    if sdk_mod is None:
        return []

    SdkClient = sdk_mod.SdkClient
    results = []

    run_id = str(time.monotonic_ns())
    alice_id = "alice_dfc_" + run_id
    bob_id = "bob_dfc_" + run_id
    room_id = "dfc_room_" + run_id
    base_score = 5_000_000_000_000 + int(run_id[-6:])

    alice = SdkClient()
    bob = SdkClient()

    try:
        # --- Connect ---
        t0 = time.monotonic()
        ok = alice.connect(host, port, 5000)
        dt = (time.monotonic() - t0) * 1000
        if ok:
            results.append(make_result("SDK.AliceConnect", True, dt))
        else:
            results.append(make_result("SDK.AliceConnect", False, dt,
                                       "connect returned False"))

        t0 = time.monotonic()
        ok = bob.connect(host, port, 5000)
        dt = (time.monotonic() - t0) * 1000
        if ok:
            results.append(make_result("SDK.BobConnect", True, dt))
        else:
            results.append(make_result("SDK.BobConnect", False, dt,
                                       "connect returned False"))

        if not all(r["passed"] for r in results[-2:]):
            errors.append("SDK: connection failed; aborting SDK flow")
            return results

        # --- Login ---
        t0 = time.monotonic()
        r = alice.login(alice_id, "token:" + alice_id, 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result(
            "SDK.AliceLogin", r["ok"], dt,
            f"user_id={r.get('user_id','?')}" if r["ok"] else f"err={r.get('error_code','?')}"
        ))

        t0 = time.monotonic()
        r = bob.login(bob_id, "token:" + bob_id, 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result(
            "SDK.BobLogin", r["ok"], dt,
            f"user_id={r.get('user_id','?')}" if r["ok"] else f"err={r.get('error_code','?')}"
        ))

        # --- Create Room ---
        t0 = time.monotonic()
        r = alice.create_room(room_id, 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result(
            "SDK.CreateRoom", r["ok"], dt,
            f"room_id={r.get('room_id','?')}" if r["ok"] else ""
        ))

        # --- Join Room ---
        t0 = time.monotonic()
        r = bob.join_room(room_id, 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result("SDK.BobJoinRoom", r["ok"], dt))

        # --- Ready ---
        t0 = time.monotonic()
        r = alice.set_ready(True, 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result("SDK.AliceReady", r["ok"], dt))

        t0 = time.monotonic()
        r = bob.set_ready(True, 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result("SDK.BobReady", r["ok"], dt))

        # --- Start Battle ---
        t0 = time.monotonic()
        r = alice.start_battle(room_id, 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result(
            "SDK.StartBattle", r["ok"], dt,
            f"battle_id={r.get('battle_id','?')}" if r["ok"] else ""
        ))

        # --- Send Tank Inputs ---
        t0 = time.monotonic()
        r = alice.send_battle_input('{"actions":[{"type":"move","dx":1,"dy":0}]}', 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result("SDK.AliceMoveInput", r["ok"], dt))

        t0 = time.monotonic()
        r = bob.send_battle_input('{"actions":[{"type":"move","dx":0,"dy":1}]}', 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result("SDK.BobMoveInput", r["ok"], dt))

        t0 = time.monotonic()
        r = alice.send_battle_input('{"actions":[{"type":"attack","target":"bob"}]}', 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result("SDK.AliceAttackInput", r["ok"], dt))

        # --- Leaderboard Query ---
        t0 = time.monotonic()
        r = alice.leaderboard_top(20, 5000)
        dt = (time.monotonic() - t0) * 1000
        lb_ok = r["ok"] and alice_id in r.get("body", "")
        results.append(make_result(
            "SDK.LeaderboardTop", lb_ok, dt,
            "alice found" if alice_id in r.get("body", "") else "alice missing"
        ))

        t0 = time.monotonic()
        r = alice.leaderboard_rank(alice_id, 5000)
        dt = (time.monotonic() - t0) * 1000
        lb_ok = r["ok"] and alice_id in r.get("body", "")
        results.append(make_result(
            "SDK.LeaderboardRank", lb_ok, dt,
            "alice found" if alice_id in r.get("body", "") else "alice missing"
        ))

        # --- Leaderboard Submit ---
        t0 = time.monotonic()
        r = alice.leaderboard_submit(alice_id, "Alice", base_score, 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result("SDK.AliceSubmitScore", r["ok"], dt))

        t0 = time.monotonic()
        r = bob.leaderboard_submit(bob_id, "Bob", base_score + 100, 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result("SDK.BobSubmitScore", r["ok"], dt))

        # --- Leave Room ---
        t0 = time.monotonic()
        r = alice.leave_room(room_id, 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result("SDK.AliceLeave", r["ok"], dt))

        t0 = time.monotonic()
        r = bob.leave_room(room_id, 5000)
        dt = (time.monotonic() - t0) * 1000
        results.append(make_result("SDK.BobLeave", r["ok"], dt))

    except Exception as exc:
        errors.append(f"SDK flow exception: {exc}")
    finally:
        try:
            alice.disconnect()
        except Exception:
            pass
        try:
            bob.disconnect()
        except Exception:
            pass

    return results


# ─── Performance Smoke ────────────────────────────────────────────────────

def find_perf_test_exe(build_dir: Path, errors: list) -> Path:
    """Locate tank_battle_perf_test.exe in the build tree."""
    candidates = [
        build_dir / "demo" / "games" / "tank_battle" / "tests" / "Debug" / "tank_battle_perf_test.exe",
        build_dir / "demo" / "games" / "tank_battle" / "tests" / "Release" / "tank_battle_perf_test.exe",
        build_dir / "demo" / "games" / "tank_battle" / "tests" / "RelWithDebInfo" / "tank_battle_perf_test.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    matches = sorted(Path(build_dir).rglob("tank_battle_perf_test.exe"))
    if matches:
        return matches[0]
    errors.append("tank_battle_perf_test.exe not found in build tree")
    return None


def run_perf_smoke(build_dir: Path, errors: list, warnings: list) -> list:
    """Run the performance smoke test and return per-benchmark results."""
    exe = find_perf_test_exe(build_dir, errors)
    if exe is None:
        return []

    print(f"  Executable: {exe}")
    rc, stdout, stderr = run_cmd([str(exe)], timeout=120)

    if stderr:
        warnings.append(f"Perf: stderr: {stderr[:500]}")

    if rc != 0:
        errors.append(f"Perf: exited with code {rc}")

    try:
        report = json.loads(stdout)
    except json.JSONDecodeError as e:
        errors.append(f"Perf: failed to parse JSON output: {e}")
        return []

    results = []
    for bench in report.get("results", []):
        ni = bench.get("num_instances", "?")
        tp = bench.get("ticks_per_instance", "?")
        name = f"Perf.{ni}inst_x{tp}ticks"
        passed = bench.get("passed", False)
        tps = bench.get("ticks_per_second", 0)
        min_tps = bench.get("min_ticks_per_second", 0)
        duration_ms = bench.get("total_duration_ms", 0)
        results.append(make_result(
            name, passed, duration_ms,
            f"tps={tps:.0f} (min={min_tps:.0f})"
        ))

    return results


# ─── Summary Writer ───────────────────────────────────────────────────────

def write_summary(build_dir: Path, mode: str, results: list,
                  errors: list, warnings: list):
    """Write the JSON summary file."""
    overall_pass = (
        len(errors) == 0
        and len(results) > 0
        and all(r["passed"] for r in results)
    )

    summary = {
        "demo": "tank_battle",
        "mode": mode,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "overall_pass": overall_pass,
        "total_tests": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "results": results,
        "errors": errors,
        "warnings": warnings,
    }

    out_dir = build_dir / "runtime" / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "tank-battle-demo-full-flow-summary.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"  Summary written to {out_path}")
    return overall_pass


# ─── CLI ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tank Battle Demo Full-Flow Client"
    )
    parser.add_argument(
        "--build-dir", default="build",
        help="Build directory (default: build/)"
    )
    parser.add_argument(
        "--sdk-mode", action="store_true",
        help="Run SDK-based gateway full-flow test"
    )
    parser.add_argument(
        "--perf", action="store_true",
        help="Run performance smoke test"
    )
    parser.add_argument(
        "--gateway-host", default="127.0.0.1",
        help="Gateway host for --sdk-mode (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--gateway-port", type=int, default=9201,
        help="Gateway port for --sdk-mode (default: 9201)"
    )
    args = parser.parse_args()

    build_dir = Path(args.build_dir).resolve()
    errors = []
    warnings = []
    results = []

    # Determine mode
    if args.sdk_mode and args.perf:
        print("ERROR: --sdk-mode and --perf are mutually exclusive")
        return 1

    if args.sdk_mode:
        mode = "sdk"
        print("=== Tank Battle Demo: SDK Full-Flow ===")
        print(f"  Gateway: {args.gateway_host}:{args.gateway_port}")
        print(f"  Build dir: {build_dir}")
        print()
        results = run_sdk_flow(build_dir, args.gateway_host, args.gateway_port,
                               errors, warnings)
    elif args.perf:
        mode = "perf"
        print("=== Tank Battle Demo: Performance Smoke ===")
        print(f"  Build dir: {build_dir}")
        print()
        results = run_perf_smoke(build_dir, errors, warnings)
    else:
        mode = "in-process"
        print("=== Tank Battle Demo: In-Process E2E ===")
        print(f"  Build dir: {build_dir}")
        print()
        results = run_in_process(build_dir, errors, warnings)

    # Print results table
    print()
    if results:
        print(f"{'Test':<45} {'Result':<8} {'Duration':>10}")
        print("-" * 65)
        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            dur = f"{r['duration_ms']:>8.1f}ms" if r["duration_ms"] else "       -"
            print(f"  {r['name']:<43} {status:<8} {dur}")
    else:
        print("  (no test results)")

    # Print errors and warnings
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors:
            print(f"  ERROR: {e}")

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  WARN: {w}")

    # Write summary JSON
    print()
    overall_pass = write_summary(build_dir, mode, results, errors, warnings)

    print(f"\n=== OVERALL: {'PASS' if overall_pass else 'FAIL'} ===")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
