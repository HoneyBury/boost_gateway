# BoostGateway Python SDK v3.0.0
# Lightweight client for BoostGateway game servers.
# Supports: connect, login, room operations, battle input, matchmaking, leaderboard.

import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, List

# ── Protocol Constants ─────────────────────────────────────────────────

MSG_HEARTBEAT_REQUEST  = 1
MSG_ECHO_REQUEST       = 1001
MSG_LOGIN_REQUEST      = 2001
MSG_ROOM_CREATE_REQUEST = 3001
MSG_ROOM_JOIN_REQUEST  = 3003
MSG_ROOM_LEAVE_REQUEST = 3005
MSG_ROOM_READY_REQUEST = 3007
MSG_BATTLE_START_REQUEST = 4001
MSG_BATTLE_INPUT_REQUEST = 4003

MSG_ECHO_RESPONSE       = 1002
MSG_LOGIN_RESPONSE      = 2002
MSG_ROOM_CREATE_RESPONSE = 3002
MSG_ROOM_JOIN_RESPONSE  = 3004
MSG_ROOM_LEAVE_RESPONSE = 3006
MSG_ROOM_READY_RESPONSE = 3008
MSG_BATTLE_START_RESPONSE = 4002
MSG_BATTLE_INPUT_RESPONSE = 4004
MSG_ERROR_RESPONSE      = 9001
MSG_SESSION_KICKED_PUSH = 1003
MSG_BATTLE_STATE_PUSH   = 4006

# ── Data Classes ───────────────────────────────────────────────────────

@dataclass
class LoginResult:
    ok: bool = False
    user_id: str = ""
    error_code: int = 0

@dataclass
class RoomResult:
    ok: bool = False
    room_id: str = ""
    error_code: int = 0

@dataclass
class PushMessage:
    message_id: int = 0
    body: str = ""

# ── SdkClient ──────────────────────────────────────────────────────────

class SdkClient:
    """BoostGateway game server client."""

    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._req_id = 0
        self._push_cb: Optional[Callable[[PushMessage], None]] = None

    def connect(self, host: str = "127.0.0.1", port: int = 9201,
                timeout: float = 5.0) -> bool:
        """Connect to a gateway server."""
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout)
            self._sock.settimeout(timeout)
            return True
        except OSError:
            return False

    def disconnect(self):
        """Disconnect from the server."""
        if self._sock:
            self._sock.close()
            self._sock = None

    def login(self, user_id: str, token: str) -> LoginResult:
        """Login with user credentials."""
        body = f"{user_id}|token:{token}|{user_id}"
        resp = self._request(MSG_LOGIN_REQUEST, body)
        if resp and resp[0] == MSG_LOGIN_RESPONSE:
            return LoginResult(ok=True, user_id=user_id)
        return LoginResult(ok=False, error_code=-1)

    def create_room(self, room_id: str) -> RoomResult:
        resp = self._request(MSG_ROOM_CREATE_REQUEST, room_id)
        ok = resp is not None and resp[0] == MSG_ROOM_CREATE_RESPONSE
        return RoomResult(ok=ok, room_id=room_id)

    def join_room(self, room_id: str) -> RoomResult:
        resp = self._request(MSG_ROOM_JOIN_REQUEST, room_id)
        ok = resp is not None and resp[0] == MSG_ROOM_JOIN_RESPONSE
        return RoomResult(ok=ok, room_id=room_id)

    def leave_room(self, room_id: str) -> RoomResult:
        resp = self._request(MSG_ROOM_LEAVE_REQUEST, room_id)
        ok = resp is not None and resp[0] == MSG_ROOM_LEAVE_RESPONSE
        return RoomResult(ok=ok, room_id=room_id)

    def set_ready(self, ready: bool = True) -> RoomResult:
        body = "true" if ready else "false"
        resp = self._request(MSG_ROOM_READY_REQUEST, body)
        ok = resp is not None and resp[0] == MSG_ROOM_READY_RESPONSE
        return RoomResult(ok=ok)

    def start_battle(self, room_id: str):
        resp = self._request(MSG_BATTLE_START_REQUEST, room_id)
        return resp is not None

    def send_input(self, input_data: str):
        resp = self._request(MSG_BATTLE_INPUT_REQUEST, input_data)
        return resp is not None

    def on_push(self, callback: Callable[[PushMessage], None]):
        self._push_cb = callback

    def _request(self, msg_id: int, body: str):
        if not self._sock:
            return None
        self._req_id += 1
        req_id = self._req_id
        encoded = self._encode(msg_id, req_id, 0, body)
        try:
            self._sock.sendall(encoded)
            return self._read()
        except OSError:
            return None

    @staticmethod
    def _encode(msg_id: int, req_id: int, err_code: int, body: str) -> bytes:
        body_bytes = body.encode("utf-8")
        total = 11 + len(body_bytes)  # 2+4+4+1 + body
        header = struct.pack("!IHIIB", total, msg_id, req_id, err_code, 0)
        return header + body_bytes

    def _read(self):
        if not self._sock:
            return None
        length_data = self._sock.recv(4)
        if len(length_data) < 4:
            return None
        total = struct.unpack("!I", length_data)[0]
        remaining = total - 4
        data = b""
        while len(data) < remaining:
            chunk = self._sock.recv(remaining - len(data))
            if not chunk:
                break
            data += chunk
        fmt = "!HIIB"
        hdr_size = struct.calcsize(fmt)
        msg_id, req_id, err_code, flags = struct.unpack(fmt, data[:hdr_size])
        body = data[hdr_size:].decode("utf-8", errors="replace")
        return (msg_id, req_id, err_code, flags, body)
