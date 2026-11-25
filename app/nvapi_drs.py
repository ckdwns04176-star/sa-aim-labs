"""NVIDIA NVAPI(DRS) 로 계단현상 끄기 세트를 **프로필에 직접** 쓴다.

왜 필요한가
-----------
``nvdrsdb0.bin`` 을 바이너리로 패치하면 **이미 있는 레코드만** 바꿀 수 있다.
NVIDIA 프로필에 ``Antialiasing - Compatibility`` 나 ``Antialiasing - Mode``
레코드가 없으면 패치는 아무 것도 못 하고, AA Mode/Setting 이 꺼져 있으면
호환성 값(0x00401241)은 드라이버가 무시한다.

그래서 Windows 에 기본 설치된 ``nvapi64.dll`` 의 DRS API
(``DRS_CreateSession`` → ``DRS_LoadSettings`` → ``DRS_FindProfileByName`` →
``DRS_GetSetting``/``DRS_CreateSetting``/``DRS_SetSetting`` →
``DRS_SaveSettings``)를 ctypes 로 먼저 호출해 **레코드가 없으면 새로 만들고**
세트 3개를 한 번에 저장한다.  실패하면 호출한 쪽(nvidia.py)이 기존 Drs 패치로
폴백하고, 필요하면 UAC 관리자 권한을 요청한다.

안전장치
--------
* 이 모듈은 Windows + nvapi64.dll 이 있을 때만 동작한다 (리눅스 테스트는 skip).
* 구조체 크기·버전은 NVAPI 규칙(``sizeof | version << 16``)을 그대로 쓴다.
* 앱 본체가 NVAPI 오류로 죽지 않도록 **별도 프로세스**에서 실행한다
  (``python main.py --nvapi-stairfix`` / ``SA_Aim_Labs.exe --nvapi-stairfix``).
* 반환은 항상 JSON(dict) 이고, rc 코드를 그대로 남겨 현장 진단이 가능하다.
"""
from __future__ import annotations

import ctypes
import json
import os
from typing import Any

# NVAPI function offsets (nvapi.h · Profile Inspector 가 쓰는 값)
NVAPI_INITIALIZE = 0x0150E828
NVAPI_DRS_CREATE_SESSION = 0x0694D52E
NVAPI_DRS_DESTROY_SESSION = 0xDAFB9D2F
NVAPI_DRS_LOAD_SETTINGS = 0x375DBD6B
NVAPI_DRS_SAVE_SETTINGS = 0xFCBC7E14
NVAPI_DRS_FIND_PROFILE_BY_NAME = 0x7E4A9BF9
NVAPI_DRS_GET_SETTING = 0x73BF8338
NVAPI_DRS_SET_SETTING = 0x577DD202
NVAPI_DRS_CREATE_SETTING = 0x0CC8748A

NVAPI_OK = 0
NVAPI_SETTING_NOT_FOUND = -171
NVAPI_ACCESS_DENIED = -9
NVAPI_INCOMPATIBLE_STRUCT_VERSION = -12

PROFILE_NAMES = ("Sudden Attack", "SuddenAttack", "서든어택", "Sudden Attack (Korea)")

STATUS = {
    0: "OK",
    -1: "ERROR",
    -5: "INVALID_ARGUMENT",
    -6: "NVIDIA_DEVICE_NOT_FOUND",
    -9: "ACCESS_DENIED (관리자 권한 필요)",
    -12: "INCOMPATIBLE_STRUCT_VERSION",
    -171: "SETTING_NOT_FOUND",
}


def available() -> bool:
    if os.name != "nt":
        return False
    try:
        ctypes.WinDLL("nvapi64.dll")  # type: ignore[attr-defined]
        return True
    except Exception:  # noqa: BLE001
        return False


def status_text(code: int) -> str:
    return STATUS.get(int(code), f"NVAPI_{int(code)}")


def _structures() -> dict[str, Any]:
    from ctypes import wintypes

    class NVDRS_UNICODE_STRING(ctypes.Structure):
        _fields_ = [("length", ctypes.c_uint32), ("string", ctypes.c_wchar * 2048)]

    class NVDRS_BINARY_SETTING(ctypes.Structure):
        _fields_ = [("valueLength", ctypes.c_uint32), ("valueData", ctypes.c_ubyte * 4096)]

    class NVDRS_SETTING_VALUE(ctypes.Union):
        _fields_ = [("u32Value", ctypes.c_uint32), ("binaryValue", NVDRS_BINARY_SETTING)]

    class NVDRS_SETTING_V1(ctypes.Structure):
        _fields_ = [
            ("version", ctypes.c_uint32),
            ("settingId", ctypes.c_uint32),
            ("settingType", ctypes.c_int),
            ("settingLocation", ctypes.c_int),
            ("isCurrentPredefined", ctypes.c_uint32),
            ("isPredefinedValid", ctypes.c_uint32),
            ("predefinedValue", NVDRS_SETTING_VALUE),
            ("currentValue", NVDRS_SETTING_VALUE),
        ]

    return {"unicode": NVDRS_UNICODE_STRING, "setting": NVDRS_SETTING_V1}


def _query(dll: Any, offset: int, restype: Any, *argtypes: Any) -> Any:
    query = dll.nvapi_QueryInterface
    query.restype = ctypes.c_void_p
    query.argtypes = [ctypes.c_uint]
    address = query(offset)
    if not address:
        raise RuntimeError(f"nvapi_QueryInterface(0x{offset:08X}) 반환 없음")
    return ctypes.CFUNCTYPE(restype, *argtypes)(address)


def apply_profile_settings(settings: list[tuple[int, int]]) -> dict[str, Any]:
    """NVAPI DRS 로 서든 프로필에 settingId=value 를 저장한다.

    반환: {"ok", "rc", "status", "profile", "settings":[{id, value, rc}], "message"}
    """
    if not available():
        return {"ok": False, "supported": False, "rc": None,
                "message": "nvapi64.dll 을 찾지 못했습니다 (Windows · NVIDIA 드라이버 필요)"}
    try:
        from ctypes import wintypes

        structs = _structures()
        dll = ctypes.WinDLL("nvapi64.dll")  # type: ignore[attr-defined]
        init = _query(dll, NVAPI_INITIALIZE, ctypes.c_int)
        create_session = _query(dll, NVAPI_DRS_CREATE_SESSION, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_void_p))
        destroy_session = _query(dll, NVAPI_DRS_DESTROY_SESSION, ctypes.c_int, ctypes.c_void_p)
        load_settings = _query(dll, NVAPI_DRS_LOAD_SETTINGS, ctypes.c_int, ctypes.c_void_p)
        save_settings = _query(dll, NVAPI_DRS_SAVE_SETTINGS, ctypes.c_int, ctypes.c_void_p)
        find_profile = _query(dll, NVAPI_DRS_FIND_PROFILE_BY_NAME, ctypes.c_int,
                              ctypes.c_void_p, ctypes.POINTER(structs["unicode"]),
                              ctypes.POINTER(ctypes.c_void_p))
        get_setting = _query(dll, NVAPI_DRS_GET_SETTING, ctypes.c_int,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(structs["setting"]))
        set_setting = _query(dll, NVAPI_DRS_SET_SETTING, ctypes.c_int,
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(structs["setting"]))
        # v1.0.0 — CreateSetting 은 오프셋이 드라이버 버전마다 달라 호출하지 않는다.
        # NvAPI_DRS_SetSetting 이 프로필에 없는 설정을 새로 만드는 것까지 처리한다.
        create_setting = None

        rc = int(init())
        if rc != NVAPI_OK:
            return {"ok": False, "supported": True, "rc": rc, "status": status_text(rc),
                    "message": f"NVAPI 초기화 실패 · {status_text(rc)}"}

        session = ctypes.c_void_p()
        rc = int(create_session(ctypes.byref(session)))
        if rc != NVAPI_OK or not session:
            return {"ok": False, "supported": True, "rc": rc, "status": status_text(rc),
                    "message": f"DRS 세션 생성 실패 · {status_text(rc)}"}
        try:
            rc = int(load_settings(session))
            if rc != NVAPI_OK:
                return {"ok": False, "supported": True, "rc": rc, "status": status_text(rc),
                        "message": f"DRS 설정 로드 실패 · {status_text(rc)}"}

            profile = ctypes.c_void_p()
            profile_name = ""
            last_rc = None
            for name in PROFILE_NAMES:
                unicode_name = structs["unicode"]()
                encoded = name.encode("utf-16-le")
                unicode_name.length = len(encoded)
                for index, char in enumerate(name):
                    unicode_name.string[index] = char
                handle = ctypes.c_void_p()
                rc = int(find_profile(session, ctypes.byref(unicode_name), ctypes.byref(handle)))
                last_rc = rc
                if rc == NVAPI_OK and handle:
                    profile, profile_name = handle, name
                    break
            if not profile:
                return {"ok": False, "supported": True, "rc": last_rc,
                        "status": status_text(last_rc or 0),
                        "message": "NVAPI DRS 에서 서든어택 프로필을 찾지 못했습니다 (드라이버 DB에 프로필 없음)"}

            size = ctypes.sizeof(structs["setting"])
            version = (1 << 16) | size
            rows: list[dict[str, Any]] = []
            for setting_id, value in settings:
                setting = structs["setting"]()
                setting.version = version
                rc = int(get_setting(session, profile, setting_id, ctypes.byref(setting)))
                created = False
                if rc == NVAPI_SETTING_NOT_FOUND:
                    setting = structs["setting"]()
                    setting.version = version
                    setting.settingId = setting_id
                    setting.settingType = 0          # NVDRS_DWORD_TYPE
                    setting.settingLocation = 0      # NVDRS_CURRENT_PROFILE_LOCATION
                    created = True
                if rc not in (NVAPI_OK, NVAPI_SETTING_NOT_FOUND):
                    rows.append({"id": f"0x{setting_id:08X}", "value": value, "rc": rc,
                                 "status": status_text(rc)})
                    continue
                setting.settingId = setting_id
                setting.settingType = 0
                setting.isCurrentPredefined = 0
                setting.currentValue.u32Value = int(value)
                rc = int(set_setting(session, profile, ctypes.byref(setting)))
                rows.append({"id": f"0x{setting_id:08X}", "value": value, "rc": rc,
                             "status": status_text(rc), "created": created})
            save_rc = int(save_settings(session))
            ok = save_rc == NVAPI_OK and all(row["rc"] == NVAPI_OK for row in rows)
            return {"ok": ok, "supported": True, "rc": save_rc, "status": status_text(save_rc),
                    "profile": profile_name, "settings": rows,
                    "message": ("NVAPI 저장 완료 · " + ", ".join(
                        f"{row['id']}={row['value']}" for row in rows)) if ok else
                        f"NVAPI 저장 실패 · {status_text(save_rc)}"}
        finally:
            try:
                destroy_session(session)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 - NVAPI 실패는 폴백으로 넘긴다
        return {"ok": False, "supported": True, "rc": None, "message": f"NVAPI 오류 · {exc}"}


def cli(settings: list[tuple[int, int]] | None = None, result_path: str | None = None) -> int:
    """``--nvapi-stairfix`` 진입점 — 결과를 파일/표준출력에 남긴다."""
    from . import drs

    pairs = settings or list(drs.STAIRFIX_SET)
    result = apply_profile_settings(pairs)
    text = json.dumps(result, ensure_ascii=False)
    if result_path:
        try:
            with open(result_path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass
    print(text)
    return 0 if result.get("ok") else 1
