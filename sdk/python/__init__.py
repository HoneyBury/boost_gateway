# SDK v4.1.0: Thin Python wrapper via C API (ctypes, zero deps).
import ctypes, os
from ctypes import c_int32, c_uint16, c_uint64, c_char, c_char_p, c_int, c_void_p, CFUNCTYPE

_dll = None
for p in ["boost_gateway_sdk.dll","libboost_gateway_sdk.so","libboost_gateway_sdk.dylib"]:
    try: _dll = ctypes.CDLL(p); break
    except OSError: continue

class GsdkLoginResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("error_code", c_int32), ("user_id", c_char*64), ("display_name", c_char*64), ("error_message", c_char*256)]

class GsdkRoomResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("error_code", c_int32), ("room_id", c_char*64), ("member_count", c_int), ("error_message", c_char*256)]

class GsdkBattleStartResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("error_code", c_int32), ("battle_id", c_char*64), ("error_message", c_char*256)]

class GsdkBattleInputResult(ctypes.Structure):
    _fields_ = [("ok", c_int), ("error_code", c_int32), ("input_seq", c_uint64), ("error_message", c_char*256)]

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
_ec = _b("gsdk_echo", GsdkEchoResult, c_void_p, c_char_p, c_int32)
_op = _b("gsdk_on_push", None, c_void_p, PUSH_CB, c_void_p)
_od = _b("gsdk_on_disconnect", None, c_void_p, DC_CB, c_void_p)

class SdkClient:
    def __init__(self): self._h = _cr()
    def connect(self, h="127.0.0.1", p=9201, ms=5000): return bool(_co(self._h, h.encode(), p, ms))
    def disconnect(self): _dc(self._h)
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
    def echo(self, b, ms=5000):
        v = _ec(self._h, b.encode(), ms); return {"ok":bool(v.ok),"body":v.body.decode()}
    def __del__(self):
        if hasattr(self,'_h') and self._h: _de(self._h)
