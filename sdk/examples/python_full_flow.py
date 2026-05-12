#!/usr/bin/env python3
"""SDK v4.1.0: Python full business flow test (via C API DLL)."""
import sys, os

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

    # 1. Connect
    print("[1] Connect...")
    check(alice.connect(host, port), "Alice connect")
    check(bob.connect(host, port), "Bob connect")
    print("  Both connected.")

    # 2. Login
    print("[2] Login...")
    r = alice.login("alice", "token:alice")
    check(r["ok"], f"Alice login: {r}")
    print(f"  Alice: {r['user_id']}")
    r = bob.login("bob", "token:bob")
    check(r["ok"], f"Bob login: {r}")
    print(f"  Bob: {r['user_id']}")

    # 3. Echo
    print("[3] Echo...")
    r = alice.echo("Hello from Python!")
    check(r["ok"], "Echo failed")
    print(f"  Echo: {r['body']}")

    # 4. Create room
    print("[4] Create room...")
    r = alice.create_room("py_room")
    check(r["ok"], f"Create room: {r}")
    print(f"  Room: {r['room_id']}")

    # 5. Join room
    print("[5] Join room...")
    check(bob.join_room("py_room")["ok"], "Join room")
    print("  Bob joined.")

    # 6. Ready
    print("[6] Ready...")
    check(alice.set_ready(True)["ok"], "Alice ready")
    check(bob.set_ready(True)["ok"], "Bob ready")
    print("  Both ready.")

    # 7. Battle
    print("[7] Start battle...")
    r = alice.start_battle("py_room")
    check(r["ok"], f"Start battle: {r}")
    print(f"  Battle: {r['battle_id']}")

    # 8. Inputs
    print("[8] Battle inputs...")
    r = alice.send_battle_input("move:50,50")
    print(f"  Alice move: {'OK' if r['ok'] else 'rejected'}")
    r = bob.send_battle_input("move:60,60")
    print(f"  Bob move: {'OK' if r['ok'] else 'rejected'}")
    r = alice.send_battle_input("attack:bob")
    print(f"  Alice attack: {'OK' if r['ok'] else 'rejected'}")

    # 9. Leave
    print("[9] Leave room...")
    check(alice.leave_room("py_room")["ok"], "Alice leave")
    check(bob.leave_room("py_room")["ok"], "Bob leave")
    print("  Both left.")

    # Cleanup
    alice.disconnect()
    bob.disconnect()
    print("\n=== ALL TESTS PASSED ===")

if __name__ == "__main__":
    main()
