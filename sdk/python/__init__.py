# SDK v4.1.0: Thin Python wrapper via C API (ctypes, zero deps).
import ctypes, os
from pathlib import Path
from ctypes import c_int32, c_uint16, c_uint64, c_char, c_char_p, c_int, c_void_p, CFUNCTYPE

EXPECTED_MAJOR = "4"
EXPECTED_VERSION_PREFIX = EXPECTED_MAJOR + "."

_dll = None
_load_errors = []
_candidates = []
if os.environ.get("BOOST_GATEWAY_SDK_LIBRARY"):
    _candidates.append(os.environ["BOOST_GATEWAY_SDK_LIBRARY"])
_here = Path(__file__).resolve().parent
_candidates.extend([
    str(_here / "boost_gateway_sdk.dll"),
    str(_here / "libboost_gateway_sdk.so"),
    str(_here / "libboost_gateway_sdk.dylib"),
    "boost_gateway_sdk.dll",
    "libboost_gateway_sdk.so",
    "libboost_gateway_sdk.dylib",
])
for p in _candidates:
    try:
        _dll = ctypes.CDLL(p)
        _loaded_path = p
        break
    except OSError as exc:
        _load_errors.append(f"{p}: {exc}")
if _dll is None:
    raise RuntimeError(
        "BoostGateway SDK native library not found. Set BOOST_GATEWAY_SDK_LIBRARY "
        "to the full native library path. Tried: " + "; ".join(_load_errors)
    )

class GsdkLoginResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("error_code", c_int32), ("user_id", c_char*64), ("display_name", c_char*64), ("error_message", c_char*256)]

class GsdkRoomResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("error_code", c_int32), ("room_id", c_char*64), ("member_count", c_int), ("error_message", c_char*256)]

class GsdkBattleStartResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("error_code", c_int32), ("battle_id", c_char*64), ("error_message", c_char*256)]

class GsdkBattleInputResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("error_code", c_int32), ("input_seq", c_uint64), ("error_message", c_char*256)]

class GsdkMatchResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("error_code", c_int32), ("response_body", c_char*4096), ("error_message", c_char*256)]

class GsdkLeaderboardSubmitResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("error_code", c_int32), ("response_body", c_char*4096), ("error_message", c_char*256)]

class GsdkLeaderboardQueryResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("error_code", c_int32), ("response_body", c_char*4096), ("error_message", c_char*256)]

class GsdkEchoResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("body", c_char*4096)]

PUSH_CB = CFUNCTYPE(None, c_uint16, c_char_p, c_void_p)
DC_CB = CFUNCTYPE(None, c_void_p)

def _b(name, restype, *argtypes):
    f = getattr(_dll, name); f.restype = restype; f.argtypes = argtypes; return f

_cr = _b("gsdk_create", c_void_p)
_de = _b("gsdk_destroy", None, c_void_p)
_co = _b("gsdk_connect", c_int, c_void_p, c_char_p, c_uint16, c_int32)
_dc = _b("gsdk_disconnect", None, c_void_p)
_lo = _b("gsdk_login", GsdkLoginResult, c_void_p, c_char_p, c_char_p, c_int32)
_crm = _b("gsdk_create_room", GsdkRoomResult, c_void_p, c_char_p, c_int32)
_jrm = _b("gsdk_join_room", GsdkRoomResult, c_void_p, c_char_p, c_int32)
_lrm = _b("gsdk_leave_room", GsdkRoomResult, c_void_p, c_char_p, c_int32)
_sr = _b("gsdk_set_ready", GsdkRoomResult, c_void_p, c_int, c_int32)
_sb = _b("gsdk_start_battle", GsdkBattleStartResult, c_void_p, c_char_p, c_int32)
_si = _b("gsdk_send_battle_input", GsdkBattleInputResult, c_void_p, c_char_p, c_int32)
_mj = _b("gsdk_match_join", GsdkMatchResult, c_void_p, c_char_p, ctypes.c_int64, c_char_p, c_int32)
_ml = _b("gsdk_match_leave", GsdkMatchResult, c_void_p, c_char_p, c_char_p, c_int32)
_ms = _b("gsdk_match_status", GsdkMatchResult, c_void_p, c_char_p, c_char_p, c_int32)
_lbs = _b("gsdk_leaderboard_submit", GsdkLeaderboardSubmitResult, c_void_p, c_char_p, c_char_p, ctypes.c_int64, c_int32)
_lbt = _b("gsdk_leaderboard_top", GsdkLeaderboardQueryResult, c_void_p, ctypes.c_uint32, c_int32)
_lbr = _b("gsdk_leaderboard_rank", GsdkLeaderboardQueryResult, c_void_p, c_char_p, c_int32)
_ec = _b("gsdk_echo", GsdkEchoResult, c_void_p, c_char_p, c_int32)
_op = _b("gsdk_on_push", None, c_void_p, PUSH_CB, c_void_p)
_od = _b("gsdk_on_disconnect", None, c_void_p, DC_CB, c_void_p)
_hb = _b("gsdk_start_heartbeat", None, c_void_p, c_int32)
_shb = _b("gsdk_stop_heartbeat", None, c_void_p)
_ver = _b("gsdk_version", c_char_p)

def version():
    return _ver().decode()

def native_library_path():
    return _loaded_path

def assert_compatible_version():
    actual = version()
    if not actual.startswith(EXPECTED_VERSION_PREFIX):
        raise RuntimeError(f"BoostGateway SDK native version mismatch: expected {EXPECTED_VERSION_PREFIX}x, got {actual}")
    return actual

class SdkClient:
    def __init__(self):
        assert_compatible_version()
        self._h = _cr()
        if not self._h:
            raise RuntimeError("BoostGateway SDK native client allocation failed")
    def connect(self, h="127.0.0.1", p=9201, ms=5000): return bool(_co(self._h, h.encode(), p, ms))
    def disconnect(self): _dc(self._h)
    def start_heartbeat(self, seconds=15): _hb(self._h, seconds)
    def stop_heartbeat(self): _shb(self._h)
    def login(self, u, t, ms=5000):
        r = _lo(self._h, u.encode(), t.encode(), ms)
        return {"ok":bool(r.ok),"user_id":r.user_id.decode(),"error_code":r.error_code}
    def create_room(self, r, ms=5000):
        v = _crm(self._h, r.encode(), ms); return {"ok":bool(v.ok),"room_id":v.room_id.decode()}
    def join_room(self, r, ms=5000):
        v = _jrm(self._h, r.encode(), ms); return {"ok":bool(v.ok)}
    def leave_room(self, r, ms=5000):
        v = _lrm(self._h, r.encode(), ms); return {"ok":bool(v.ok)}
    def set_ready(self, r=True, ms=5000):
        v = _sr(self._h, 1 if r else 0, ms); return {"ok":bool(v.ok)}
    def start_battle(self, r, ms=5000):
        v = _sb(self._h, r.encode(), ms); return {"ok":bool(v.ok),"battle_id":v.battle_id.decode()}
    def send_battle_input(self, d, ms=5000):
        v = _si(self._h, d.encode(), ms); return {"ok":bool(v.ok)}
    def match_join(self, user_id, mmr=1000, mode="1v1", ms=5000):
        v = _mj(self._h, user_id.encode(), mmr, mode.encode(), ms)
        return {"ok":bool(v.ok),"error_code":v.error_code,"body":v.response_body.decode()}
    def match_leave(self, user_id, mode="1v1", ms=5000):
        v = _ml(self._h, user_id.encode(), mode.encode(), ms)
        return {"ok":bool(v.ok),"error_code":v.error_code,"body":v.response_body.decode()}
    def match_status(self, user_id, mode="1v1", ms=5000):
        v = _ms(self._h, user_id.encode(), mode.encode(), ms)
        return {"ok":bool(v.ok),"error_code":v.error_code,"body":v.response_body.decode()}
    def leaderboard_submit(self, user_id, display_name, score, ms=5000):
        v = _lbs(self._h, user_id.encode(), display_name.encode(), score, ms)
        return {"ok":bool(v.ok),"error_code":v.error_code,"body":v.response_body.decode()}
    def leaderboard_top(self, k=10, ms=5000):
        v = _lbt(self._h, k, ms)
        return {"ok":bool(v.ok),"error_code":v.error_code,"body":v.response_body.decode()}
    def leaderboard_rank(self, user_id, ms=5000):
        v = _lbr(self._h, user_id.encode(), ms)
        return {"ok":bool(v.ok),"error_code":v.error_code,"body":v.response_body.decode()}
    def echo(self, b, ms=5000):
        v = _ec(self._h, b.encode(), ms); return {"ok":bool(v.ok),"body":v.body.decode()}
    def __del__(self):
        if hasattr(self,'_h') and self._h: _de(self._h)
