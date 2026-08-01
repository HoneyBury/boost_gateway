import ctypes
import importlib.util
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_MODULE_PATH = REPO_ROOT / "sdk" / "python" / "__init__.py"


class FakeNativeFunction:
    def __init__(self, name: str) -> None:
        self.name = name
        self.restype = None
        self.argtypes = None

    def __call__(self, *_args):
        if self.name == "gsdk_version":
            return b"4.2.0"
        if self.name == "gsdk_create":
            return 1
        return None


class FakeNativeLibrary:
    def __init__(self) -> None:
        self.functions = {}

    def __getattr__(self, name: str):
        return self.functions.setdefault(name, FakeNativeFunction(name))


def load_sdk_module():
    spec = importlib.util.spec_from_file_location(
        "boost_gateway_sdk_error_telemetry_test", SDK_MODULE_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(ctypes, "CDLL", return_value=FakeNativeLibrary()):
        spec.loader.exec_module(module)
    return module


SDK = load_sdk_module()


def room_result(error_code: int, room_id: bytes = b""):
    result = SDK.GsdkRoomResult()
    result.ok = 0
    result.error_code = error_code
    result.room_id = room_id
    return result


def test_room_operations_return_native_error_codes() -> None:
    client = SDK.SdkClient()

    cases = (
        ("_crm", "create_room", ("room-a",), room_result(-2002, b"room-a")),
        ("_jrm", "join_room", ("room-a",), room_result(-2003)),
        ("_lrm", "leave_room", ("room-a",), room_result(-2004)),
        ("_sr", "set_ready", (True,), room_result(-2005)),
    )
    for native_name, method_name, arguments, native_result in cases:
        with mock.patch.object(SDK, native_name, return_value=native_result):
            result = getattr(client, method_name)(*arguments)

        assert result["ok"] is False
        assert result["error_code"] == native_result.error_code


def test_battle_operations_return_native_error_codes() -> None:
    client = SDK.SdkClient()

    start_result = SDK.GsdkBattleStartResult()
    start_result.ok = 0
    start_result.error_code = -3001
    start_result.battle_id = b"battle-a"
    with mock.patch.object(SDK, "_sb", return_value=start_result):
        result = client.start_battle("room-a")
    assert result == {
        "ok": False,
        "battle_id": "battle-a",
        "error_code": -3001,
    }

    input_result = SDK.GsdkBattleInputResult()
    input_result.ok = 0
    input_result.error_code = -3002
    with mock.patch.object(SDK, "_si", return_value=input_result):
        result = client.send_battle_input("move:1,2")
    assert result == {"ok": False, "error_code": -3002}
