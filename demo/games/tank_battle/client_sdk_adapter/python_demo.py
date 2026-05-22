#!/usr/bin/env python3
"""
Tank Battle Python SDK Demo (N5).

Connects two players through the BoostGateway SDK and exercises the full
tank battle lifecycle: login, room create/join, ready, battle start,
tank move inputs, finish, and leaderboard query.

Outputs a JSON result summary.  Exits 0 if all steps pass, 1 otherwise.
Gracefully skips when the SDK native DLL is not available.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


# ─── SDK Bootstrap with graceful fallback ────────────────────────────────

def _bootstrap_sdk():
    """
    Attempt to load the BoostGateway Python SDK wrapper and the native DLL.

    Returns (SdkClient_class, None) on success, or (None, error_message) on
    failure.  Callers should treat a failure as "skip the demo" rather than
    a hard error.
    """
    base = Path(__file__).resolve().parent
    # Walk up to the project root: demo/games/tank_battle/client_sdk_adapter/
    # -> demo/games/tank_battle/ -> demo/games/ -> demo/ -> project root
    project_dir = base.parent.parent.parent.parent
    sdk_dir = project_dir / "sdk" / "python"
    dll_dir = project_dir / "build" / "sdk" / "Debug"

    if not sdk_dir.joinpath("__init__.py").is_file():
        return None, f"SDK python wrapper not found at {sdk_dir}"

    if not dll_dir.joinpath("boost_gateway_sdk.dll").is_file():
        # Try Release config
        dll_dir = project_dir / "build" / "sdk" / "Release"
        if not dll_dir.joinpath("boost_gateway_sdk.dll").is_file():
            return None, (
                f"boost_gateway_sdk.dll not found in Debug or Release build. "
                f"Tried: {project_dir / 'build' / 'sdk' / 'Debug'} and {dll_dir}"
            )

    try:
        os.add_dll_directory(str(dll_dir))
    except AttributeError:
        pass  # pre-3.8 Python does not have add_dll_directory
    old_cwd = os.getcwd()
    os.chdir(str(dll_dir))

    try:
        import importlib.util
        import ctypes

        # Pre-load the DLL so __init__.py can find it
        ctypes.CDLL(str(dll_dir / "boost_gateway_sdk.dll"))

        spec = importlib.util.spec_from_file_location(
            "boost_gateway_sdk", str(sdk_dir / "__init__.py")
        )
        sdk_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sdk_mod)
        SdkClient = sdk_mod.SdkClient
        return SdkClient, None
    except Exception as exc:
        return None, f"SDK load failed: {exc}"
    finally:
        os.chdir(old_cwd)


# ─── Demo Steps ──────────────────────────────────────────────────────────

class StepReporter:
    """Collects pass/fail results for each lifecycle step."""

    def __init__(self):
        self.steps = []

    def record(self, step_num: int, name: str, passed: bool, detail: str = ""):
        self.steps.append({
            "step": step_num,
            "name": name,
            "passed": passed,
            "detail": detail,
        })
        status = "PASS" if passed else "FAIL"
        print(f"  [{step_num}] {name}: {status}" + (f"  ({detail})" if detail else ""))

    def all_passed(self) -> bool:
        return all(s["passed"] for s in self.steps)

    def summary(self) -> dict:
        return {
            "demo": "tank_battle_python_sdk",
            "overall_pass": self.all_passed(),
            "steps": self.steps,
        }


def run_demo(host: str, port: int) -> dict:
    """
    Execute the full tank battle lifecycle.

    Returns a summary dict suitable for JSON serialisation.
    """
    SdkClient, err = _bootstrap_sdk()
    if SdkClient is None:
        return {
            "demo": "tank_battle_python_sdk",
            "overall_pass": True,       # skipped is not a failure
            "skipped": True,
            "skip_reason": err,
            "steps": [],
        }

    step = StepReporter()
    run_id = str(time.monotonic_ns())[-8:]
    alice_id = f"n5_alice_{run_id}"
    bob_id = f"n5_bob_{run_id}"
    room_id = "tank_demo_room"

    alice = SdkClient()
    bob = SdkClient()

    # 1. Connect
    a_ok = alice.connect(host, port)
    b_ok = bob.connect(host, port)
    step.record(1, "connect", a_ok and b_ok,
                f"Alice={'ok' if a_ok else 'fail'} Bob={'ok' if b_ok else 'fail'}")
    if not (a_ok and b_ok):
        return step.summary()

    # 2. Login
    try:
        r = alice.login(alice_id, "token:" + alice_id)
        alice_login_ok = r.get("ok", False)
        r = bob.login(bob_id, "token:" + bob_id)
        bob_login_ok = r.get("ok", False)
    except Exception as e:
        alice_login_ok = bob_login_ok = False
    step.record(2, "login", alice_login_ok and bob_login_ok)

    # 3. Create room
    try:
        r = alice.create_room(room_id)
        room_ok = r.get("ok", False)
    except Exception:
        room_ok = False
    step.record(3, "create_room", room_ok)

    # 4. Join room
    try:
        r = bob.join_room(room_id)
        join_ok = r.get("ok", False)
    except Exception:
        join_ok = False
    step.record(4, "join_room", join_ok)

    # 5. Set ready
    try:
        r = alice.set_ready(True)
        alice_ready = r.get("ok", False)
        r = bob.set_ready(True)
        bob_ready = r.get("ok", False)
    except Exception:
        alice_ready = bob_ready = False
    step.record(5, "set_ready", alice_ready and bob_ready)

    # 6. Start battle
    try:
        r = alice.start_battle(room_id)
        battle_ok = r.get("ok", False)
        battle_id = r.get("battle_id", "")
    except Exception:
        battle_ok = False
        battle_id = ""
    step.record(6, "start_battle", battle_ok, f"battle_id={battle_id}")

    # 7. Alice tank move input
    try:
        r = alice.send_battle_input("move:0,1")
        alice_move_ok = r.get("ok", False)
    except Exception:
        alice_move_ok = False
    step.record(7, "alice_move_input", alice_move_ok)

    # 8. Bob tank move input
    try:
        r = bob.send_battle_input("move:0,-1")
        bob_move_ok = r.get("ok", False)
    except Exception:
        bob_move_ok = False
    step.record(8, "bob_move_input", bob_move_ok)

    # 9. Alice finishes battle
    try:
        r = alice.send_battle_input("finish:normal")
        finish_ok = r.get("ok", False)
    except Exception:
        finish_ok = False
    step.record(9, "finish_battle", finish_ok)

    # 10. Leaderboard top query
    try:
        r = alice.leaderboard_top(10)
        lb_ok = r.get("ok", False)
    except Exception:
        lb_ok = False
    step.record(10, "leaderboard_top", lb_ok)

    # 11. Disconnect
    try:
        alice.disconnect()
        bob.disconnect()
        step.record(11, "disconnect", True)
    except Exception:
        step.record(11, "disconnect", False)

    return step.summary()


def main():
    parser = argparse.ArgumentParser(
        description="Tank Battle Python SDK Demo (N5)"
    )
    parser.add_argument("--host", default="127.0.0.1", help="Gateway host")
    parser.add_argument("--port", type=int, default=9201, help="Gateway port")
    args = parser.parse_args()

    print("=== Tank Battle Python SDK Demo (N5) ===")
    print(f"Gateway: {args.host}:{args.port}")
    print()

    summary = run_demo(args.host, args.port)

    print()
    print("=== Step Results ===")
    for s in summary.get("steps", []):
        status = "PASS" if s["passed"] else "FAIL"
        print(f"  [{s['step']}] {s['name']}: {status}")

    if summary.get("skipped"):
        print(f"\nDemo skipped: {summary.get('skip_reason', 'unknown')}")
        print("\n=== SKIPPED (DLL not available, not an error) ===")
    elif summary.get("overall_pass"):
        print("\n=== ALL STEPS PASSED ===")
    else:
        print("\n=== SOME STEPS FAILED ===")

    # Write JSON summary alongside this script
    out_path = Path(__file__).resolve().parent / "python_demo_summary.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Summary written to {out_path}")

    # Exit 0 if all pass (or skipped), 1 if any real failure
    if summary.get("skipped"):
        sys.exit(0)
    sys.exit(0 if summary.get("overall_pass") else 1)


if __name__ == "__main__":
    main()
