#!/usr/bin/env python3
"""SDK v4.1.0: Python full business flow test (via C API DLL)."""
import sys, os, time

# Point to DLL and SDK
base = os.path.dirname(os.path.abspath(__file__))
sdk_dir = os.path.join(base, "..")
project_dir = os.path.join(base, "..", "..")
dll_dir = os.path.join(project_dir, "build", "sdk", "Debug")
sys.path.insert(0, os.path.join(sdk_dir, "python"))
os.add_dll_directory(dll_dir)
os.chdir(dll_dir)

import ctypes
dll = ctypes.CDLL(os.path.join(dll_dir, "boost_gateway_sdk.dll"))
import importlib.util
spec = importlib.util.spec_from_file_location("sdk", os.path.join(sdk_dir, "python", "__init__.py"))
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)
SdkClient = sdk.SdkClient

def check(ok, msg):
    if not ok: print(f"FAIL: {msg}"); sys.exit(1)

def main():
    host, port = sys.argv[1] if len(sys.argv)>1 else "127.0.0.1", int(sys.argv[2]) if len(sys.argv)>2 else 9201
    print(f"=== Python SDK v4.1 Full Flow Test ===\nTarget: {host}:{port}\n")

    alice = SdkClient()
    bob = SdkClient()
    run_id = str(time.monotonic_ns())
    alice_id = "alice_py_" + run_id
    bob_id = "bob_py_" + run_id
    room_id = "py_room_" + run_id
    base_score = 9_000_000_000_000 + int(run_id[-6:])

    # 1. Connect
    print("[1] Connect...")
    check(alice.connect(host, port), "Alice connect")
    check(bob.connect(host, port), "Bob connect")
    print("  Both connected.")

    # 2. Login
    print("[2] Login...")
    r = alice.login(alice_id, "token:" + alice_id)
    check(r["ok"], f"Alice login: {r}")
    print(f"  Alice: {r['user_id']}")
    r = bob.login(bob_id, "token:" + bob_id)
    check(r["ok"], f"Bob login: {r}")
    print(f"  Bob: {r['user_id']}")

    # 3. Echo
    print("[3] Echo...")
    r = alice.echo("Hello from Python!")
    check(r["ok"], "Echo failed")
    print(f"  Echo: {r['body']}")

    # 4. Matchmaking
    print("[4] Matchmaking...")
    r = alice.match_join(alice_id, 1200, "1v1")
    check(r["ok"], f"Alice match join: {r}")
    r = alice.match_status(alice_id, "1v1")
    check(r["ok"], f"Alice match status: {r}")
    r = bob.match_join(bob_id, 1210, "1v1")
    check(r["ok"], f"Bob match join: {r}")
    time.sleep(1.2)
    r = bob.match_leave(bob_id, "1v1")
    check(r["ok"], f"Bob match leave: {r}")
    print("  Match join/status/leave OK.")

    # 5. Create room
    print("[5] Create room...")
    r = alice.create_room(room_id)
    check(r["ok"], f"Create room: {r}")
    print(f"  Room: {r['room_id']}")

    # 6. Join room
    print("[6] Join room...")
    check(bob.join_room(room_id)["ok"], "Join room")
    print("  Bob joined.")

    # 7. Ready
    print("[7] Ready...")
    check(alice.set_ready(True)["ok"], "Alice ready")
    check(bob.set_ready(True)["ok"], "Bob ready")
    print("  Both ready.")

    # 8. Battle
    print("[8] Start battle...")
    r = alice.start_battle(room_id)
    check(r["ok"], f"Start battle: {r}")
    print(f"  Battle: {r['battle_id']}")

    # 9. Inputs
    print("[9] Battle inputs...")
    r = alice.send_battle_input("move:50,50")
    print(f"  Alice move: {'OK' if r['ok'] else 'rejected'}")
    r = bob.send_battle_input("move:60,60")
    print(f"  Bob move: {'OK' if r['ok'] else 'rejected'}")
    r = alice.send_battle_input("attack:bob")
    print(f"  Alice attack: {'OK' if r['ok'] else 'rejected'}")

    # 10. Leaderboard
    print("[10] Leaderboard...")
    r = alice.leaderboard_top(20)
    check(r["ok"] and alice_id in r["body"] and bob_id in r["body"], f"Auto settlement leaderboard top: {r}")
    r = alice.leaderboard_rank(alice_id)
    check(r["ok"] and alice_id in r["body"], f"Auto settlement leaderboard rank: {r}")
    r = alice.leaderboard_submit(alice_id, "Alice", base_score)
    check(r["ok"], f"Manual Alice leaderboard submit: {r}")
    r = bob.leaderboard_submit(bob_id, "Bob", base_score + 100)
    check(r["ok"], f"Manual Bob leaderboard submit: {r}")
    print("  Auto settlement leaderboard and manual submit paths OK.")

    # 11. Leave
    print("[11] Leave room...")
    check(alice.leave_room(room_id)["ok"], "Alice leave")
    check(bob.leave_room(room_id)["ok"], "Bob leave")
    print("  Both left.")

    # Cleanup
    alice.disconnect()
    bob.disconnect()
    print("\n=== ALL TESTS PASSED ===")

if __name__ == "__main__":
    main()
