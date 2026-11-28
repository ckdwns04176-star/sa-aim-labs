"""NVIDIA NVAPI(DRS) 로 그래픽 호환 세트를 **프로필에 직접** 쓴다.

왜 필요한가
-----------
``nvdrsdb0.bin`` 을 바이너리로 패치하면 **이미 있는 레코드만** 바꿀 수 있다.
NVIDIA 프로필에 ``Antialiasing - Compatibility`` 나 ``Antialiasing - Mode``
레코드가 없으면 패치는 아무 것도 못 하고, AA Mode/Setting 이 꺼져 있으면
호환성 값(0x00401241)은 드라이버가 무시한다.

그래서 Windows 에 기본 설치된 ``nvapi64.dll`` 의 DRS API
(``DRS_CreateSession`` → ``DRS_LoadSettings`` → ``DRS_FindProfileByName`` →
``DRS_GetSetting``/``DRS_SetSetting`` → ``DRS_SaveSettings``)를 ctypes 로 호출해
**레코드가 없으면 새로 만들고** 세트를 한 번에 저장한다.

v2.0.0 구조체 수정 (실기에서 '적용 완료'인데 값이 안 바뀌던 원인)
-------------------------------------------------------------
* ``NVDRS_UNICODE_STRING`` 앞의 4바이트 ``length`` 필드는 잘못된 정의였다.
  실제 구조체는 ``NvWideChar string[2048]`` (4096바이트) 하나뿐이다.  앞에
  length 가 끼면 뒤따르는 모든 필드가 4바이트씩 밀려 프로필 이름·설정 이름이
  깨지고, FindProfileByName 이 엉뚱한 핸들을 돌려준다.
* ``NVDRS_SETTING_V1`` 에 ``settingName``(4096바이트)이 빠져 있어 구조체 크기가
  12320 이 아니라 8224 로 계산됐다.  버전 필드가 ``sizeof | version<<16`` 이므로
  크기가 틀리면 드라이버가 ``NVAPI_INCOMPATIBLE_STRUCT_VERSION`` 으로 거절한다.

적용 판정
---------
파일/API 호출이 성공(rc=0)했다고 '적용 완료'로 보지 않는다.  저장 후 **세션을
다시 열어 프로필을 다시 찾고 GetSetting 으로 값이 실제로 그 값인지** 확인한
뒤에만 ``ok=True`` 를 돌려준다.  확인에 실패하면 ``verified=False`` 와 이유를
남기고, 호출한 쪽(nvidia.py)이 기존 Drs 패치로 폴백한다.

안전장치
--------
* 이 모듈은 Windows + nvapi64.dll 이 있을 때만 동작한다 (리눅스 테스트는 구조체
  크기·오프셋과 결과 판정만 검증한다).
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
# v2.0.4 — 실행 파일(suddenattack.exe)로 프로필을 찾는 쪽이 더 정확하다.
# 드라이버 버전·언어에 따라 프로필 "이름"이 달라져도 앱 이름은 고정이기 때문.
NVAPI_DRS_FIND_APPLICATION_BY_NAME = 0xEEE566B2
NVAPI_DRS_GET_SETTING = 0x73BF8338
NVAPI_DRS_SET_SETTING = 0x577DD202

NVAPI_OK = 0
NVAPI_SETTING_NOT_FOUND = -171
NVAPI_ACCESS_DENIED = -9
NVAPI_INCOMPATIBLE_STRUCT_VERSION = -12

# 설정 구조체 버전 (NVDRS_SETTING_VER1)
SETTING_VER = 1

PROFILE_NAMES = ("Sudden Attack", "SuddenAttack", "서든어택", "Sudden Attack (Korea)")
# FindApplicationByName 1순위 — 서든어택 실행 파일 이름 (대소문자 표기 차이 대비)
APPLICATION_NAMES = ("suddenattack.exe", "SuddenAttack.exe")
# NVDRS_APPLICATION_V3 버전
APPLICATION_VER = 3

STATUS = {
    0: "OK",
    -1: "ERROR",
    -5: "INVALID_ARGUMENT",
    -6: "NVIDIA_DEVICE_NOT_FOUND",
    -9: "ACCESS_DENIED (관리자 권한 필요)",
    -12: "INCOMPATIBLE_STRUCT_VERSION",
    -171: "SETTING_NOT_FOUND",
}

UNICODE_CHARS = 2048
BINARY_BYTES = 4096
# 구조체 검증용 기대값 — 테스트가 이 숫자를 그대로 확인한다.
EXPECTED_SIZES = {
    "unicode": 4096,
    "binary": 4100,
    "value": 4100,
    "setting": 12320,
}
EXPECTED_OFFSETS = {
    "version": 0,
    "settingId": 4,
    "settingType": 8,
    "settingLocation": 12,
    "isCurrentPredefined": 16,
    "isPredefinedValid": 20,
    "predefinedValue": 24,
    "currentValue": 4124,
    "settingName": 8224,
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
    """NVAPI DRS 구조체 — nvapi.h 정의 그대로 (Wintypes 의존 없음)."""

    class NVDRS_UNICODE_STRING(ctypes.Structure):
        # v2.0.0 — 잘못 붙어 있던 length(4바이트) 제거. 4096바이트 하나뿐.
        # c_wchar 는 리눅스에서 4바이트라 구조체 크기가 달라진다 → UTF-16 코드
        # 단위(uint16) 배열로 고정해 Windows·리눅스 모두 4096바이트가 되게 한다.
        _fields_ = [("string", ctypes.c_uint16 * UNICODE_CHARS)]

    class NVDRS_BINARY_SETTING(ctypes.Structure):
        _fields_ = [("valueLength", ctypes.c_uint32),
                    ("valueData", ctypes.c_ubyte * BINARY_BYTES)]

    class NVDRS_SETTING_VALUE(ctypes.Union):
        _fields_ = [("u32Value", ctypes.c_uint32),
                    ("binaryValue", NVDRS_BINARY_SETTING)]

    class NVDRS_SETTING_V1(ctypes.Structure):
        _fields_ = [
            ("version", ctypes.c_uint32),
            ("settingId", ctypes.c_uint32),
            ("settingType", ctypes.c_uint32),
            ("settingLocation", ctypes.c_uint32),
            ("isCurrentPredefined", ctypes.c_uint32),
            ("isPredefinedValid", ctypes.c_uint32),
            ("predefinedValue", NVDRS_SETTING_VALUE),
            ("currentValue", NVDRS_SETTING_VALUE),
            # v2.0.0 — 누락됐던 설정 이름 (4096바이트)
            ("settingName", NVDRS_UNICODE_STRING),
        ]

    class NVDRS_APPLICATION_V3(ctypes.Structure):
        # FindApplicationByName 의 OUT 파라미터 — 프로필이 아닌 **앱** 정보.
        _fields_ = [
            ("version", ctypes.c_uint32),
            ("appName", NVDRS_UNICODE_STRING),
            ("userFriendlyName", NVDRS_UNICODE_STRING),
            ("launcher", NVDRS_UNICODE_STRING),
            ("fileInvisible", ctypes.c_uint32),
            ("reserved", ctypes.c_uint32 * 7),
        ]

    return {"unicode": NVDRS_UNICODE_STRING, "binary": NVDRS_BINARY_SETTING,
            "value": NVDRS_SETTING_VALUE, "setting": NVDRS_SETTING_V1,
            "application": NVDRS_APPLICATION_V3}


def struct_report() -> dict[str, Any]:
    """구조체 크기·오프셋을 그대로 돌려준다 (테스트 · 현장 진단용)."""
    structs = _structures()
    setting = structs["setting"]
    sizes = {name: ctypes.sizeof(cls) for name, cls in structs.items()}
    offsets = {name: int(getattr(setting, name).offset) for name, _ in setting._fields_}
    errors: list[str] = []
    for name, expected in EXPECTED_SIZES.items():
        if sizes.get(name) != expected:
            errors.append(f"sizeof({name})={sizes.get(name)} · 기대 {expected}")
    for name, expected in EXPECTED_OFFSETS.items():
        if offsets.get(name) != expected:
            errors.append(f"offset({name})={offsets.get(name)} · 기대 {expected}")
    return {"sizes": sizes, "offsets": offsets, "ok": not errors, "errors": errors,
            "version": (SETTING_VER << 16) | int(sizes["setting"])}


def _query(dll: Any, offset: int, restype: Any, *argtypes: Any) -> Any:
    query = dll.nvapi_QueryInterface
    query.restype = ctypes.c_void_p
    query.argtypes = [ctypes.c_uint32]
    address = query(offset)
    if not address:
        raise RuntimeError(f"nvapi_QueryInterface(0x{offset:08X}) 반환 없음")
    return ctypes.CFUNCTYPE(restype, *argtypes)(address)


def set_unicode(field: Any, text: str) -> None:
    """UTF-16 코드 단위로 채운다 (플랫폼과 무관하게 2바이트/글자)."""
    units = str(text or "").encode("utf-16-le")[: (UNICODE_CHARS - 1) * 2]
    for index in range(0, len(units), 2):
        field.string[index // 2] = int.from_bytes(units[index:index + 2], "little")


def get_unicode(field: Any) -> str:
    units: list[int] = []
    for index in range(UNICODE_CHARS):
        value = int(field.string[index])
        if value == 0:
            break
        units.append(value)
    raw = b"".join(u.to_bytes(2, "little") for u in units)
    return raw.decode("utf-16-le", "ignore")


def _fill_name(structs: dict[str, Any], name: str) -> Any:
    value = structs["unicode"]()
    set_unicode(value, name)
    return value


def application_struct(structs: dict[str, Any]) -> Any:
    """FindApplicationByName 이 채워 줄 NVDRS_APPLICATION_V3 — version 먼저."""
    application = structs["application"]()
    application.version = (APPLICATION_VER << 16) | ctypes.sizeof(application)
    return application


def _find_profile(structs: dict[str, Any], find_profile: Any, session: Any,
                  find_app: Any | None = None) -> tuple[Any, str, int | None]:
    """프로필 찾기 — **suddenattack.exe 로 먼저**, 없으면 프로필 이름으로 폴백.

    드라이버 버전·언어에 따라 프로필 표시 이름이 달라지면
    FindProfileByName 이 실패한다(사용자 지적).  실행 파일 이름은 게임이
    정해져 있으므로 FindApplicationByName(0xEEE566B2) 쪽이 안정적이다.
    """
    last_rc: int | None = None
    if find_app is not None:
        for app_name in APPLICATION_NAMES:
            encoded = _fill_name(structs, app_name)
            handle = ctypes.c_void_p()
            application = application_struct(structs)
            try:
                rc = int(find_app(session, ctypes.byref(encoded), ctypes.byref(handle),
                                  ctypes.byref(application)))
            except Exception:  # noqa: BLE001 - 바인딩 실패는 이름 조회로 넘어간다
                last_rc = None
                break
            last_rc = rc
            if rc == NVAPI_OK and handle:
                return handle, app_name, rc
    for name in PROFILE_NAMES:
        encoded = _fill_name(structs, name)
        handle = ctypes.c_void_p()
        rc = int(find_profile(session, ctypes.byref(encoded), ctypes.byref(handle)))
        last_rc = rc
        if rc == NVAPI_OK and handle:
            return handle, name, rc
    return None, "", last_rc


def plan_apply(settings: list[tuple[int, int]]) -> list[dict[str, int]]:
    """설정 목록을 정리 (중복 제거 · 32비트 마스크). 순수 함수."""
    out: list[dict[str, int]] = []
    seen: set[int] = set()
    for setting_id, value in settings or []:
        try:
            sid = int(setting_id) & 0xFFFFFFFF
            val = int(value) & 0xFFFFFFFF
        except (TypeError, ValueError):
            continue
        if sid in seen:
            continue
        seen.add(sid)
        out.append({"id": sid, "value": val})
    return out


def verify_rows(rows: list[dict[str, Any]], expected: list[dict[str, int]]) -> dict[str, Any]:
    """재조회 결과와 기대값을 비교 — 이 판정이 '적용 완료'의 유일한 근거다."""
    want = {int(item["id"]): int(item["value"]) for item in expected}
    bad: list[dict[str, Any]] = []
    checked = 0
    for row in rows or []:
        try:
            sid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if sid not in want:
            continue
        checked += 1
        if row.get("error") or int(row.get("value") or 0) != want[sid]:
            bad.append(row)
    missing = [sid for sid in want if sid not in {int(r.get("id")) for r in rows or [] if r.get("id") is not None}]
    return {"verified": checked > 0 and not bad and not missing,
            "checked": checked, "mismatched": bad, "missing": missing}


def apply_profile_settings(settings: list[tuple[int, int]]) -> dict[str, Any]:
    """NVAPI DRS 로 서든 프로필에 settingId=value 를 저장하고 **재조회로 확인**한다.

    반환: {"ok", "verified", "rc", "status", "profile", "settings":[{id, value, rc}],
           "verify": {...}, "message"}
    """
    if not available():
        return {"ok": False, "verified": False, "supported": False, "rc": None,
                "message": "nvapi64.dll 을 찾지 못했습니다 (Windows · NVIDIA 드라이버 필요)"}
    plan = plan_apply(settings)
    if not plan:
        return {"ok": False, "verified": False, "supported": True, "rc": None,
                "message": "적용할 설정이 없습니다"}
    try:
        structs = _structures()
        report = struct_report()
        if not report["ok"]:
            return {"ok": False, "verified": False, "supported": True, "rc": None,
                    "message": "NVAPI 구조체 정의가 잘못됐습니다 · " + "; ".join(report["errors"]),
                    "struct": report}
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
        # 실행 파일 이름으로 프로필 찾기 (1순위) — 구버전 드라이버에 없으면 None
        try:
            find_app = _query(dll, NVAPI_DRS_FIND_APPLICATION_BY_NAME, ctypes.c_int,
                              ctypes.c_void_p, ctypes.POINTER(structs["unicode"]),
                              ctypes.POINTER(ctypes.c_void_p),
                              ctypes.POINTER(structs["application"]))
        except Exception:  # noqa: BLE001
            find_app = None
        get_setting = _query(dll, NVAPI_DRS_GET_SETTING, ctypes.c_int,
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                             ctypes.POINTER(structs["setting"]))
        set_setting = _query(dll, NVAPI_DRS_SET_SETTING, ctypes.c_int,
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(structs["setting"]))

        rc = int(init())
        if rc != NVAPI_OK:
            return {"ok": False, "verified": False, "supported": True, "rc": rc,
                    "status": status_text(rc), "message": f"NVAPI 초기화 실패 · {status_text(rc)}"}

        version = report["version"]
        rows: list[dict[str, Any]] = []

        def _session() -> Any:
            session = ctypes.c_void_p()
            code = int(create_session(ctypes.byref(session)))
            if code != NVAPI_OK or not session:
                raise RuntimeError(f"DRS 세션 생성 실패 · {status_text(code)}")
            code = int(load_settings(session))
            if code != NVAPI_OK:
                try:
                    destroy_session(session)
                except Exception:  # noqa: BLE001
                    pass
                raise RuntimeError(f"DRS 설정 로드 실패 · {status_text(code)}")
            return session

        session = _session()
        try:
            profile, profile_name, _rc = _find_profile(structs, find_profile, session,
                                                       find_app=find_app)
            if not profile:
                return {"ok": False, "verified": False, "supported": True, "rc": _rc,
                        "status": status_text(_rc or 0),
                        "message": "NVAPI DRS 에서 서든어택 프로필을 찾지 못했습니다 (드라이버 DB에 프로필 없음)"}
            for item in plan:
                setting = structs["setting"]()
                setting.version = version
                found_rc = int(get_setting(session, profile, item["id"], ctypes.byref(setting)))
                created = found_rc == NVAPI_SETTING_NOT_FOUND
                if found_rc not in (NVAPI_OK, NVAPI_SETTING_NOT_FOUND):
                    rows.append({"id": item["id"], "value": item["value"], "rc": found_rc,
                                 "status": status_text(found_rc), "created": False,
                                 "error": True})
                    continue
                setting.settingId = item["id"]
                setting.settingType = 0          # NVDRS_DWORD_TYPE
                setting.settingLocation = 0      # NVDRS_CURRENT_PROFILE_LOCATION
                setting.isCurrentPredefined = 0
                setting.isPredefinedValid = 0
                setting.currentValue.u32Value = item["value"]
                write_rc = int(set_setting(session, profile, ctypes.byref(setting)))
                rows.append({"id": item["id"], "value": item["value"], "rc": write_rc,
                             "status": status_text(write_rc), "created": created,
                             "error": write_rc != NVAPI_OK})
            save_rc = int(save_settings(session))
        finally:
            try:
                destroy_session(session)
            except Exception:  # noqa: BLE001
                pass

        # ── 재조회 검증: 세션을 새로 열어 실제 저장된 값을 읽는다 ──
        verified_rows: list[dict[str, Any]] = []
        verify_error = None
        if save_rc == NVAPI_OK:
            try:
                session2 = _session()
                try:
                    profile2, _name2, _rc2 = _find_profile(structs, find_profile, session2,
                                                          find_app=find_app)
                    if not profile2:
                        verify_error = "재조회에서 서든 프로필을 찾지 못했습니다"
                    else:
                        for item in plan:
                            check = structs["setting"]()
                            check.version = version
                            start_rc = int(get_setting(session2, profile2, item["id"],
                                                       ctypes.byref(check)))
                            verified_rows.append({
                                "id": item["id"], "value": int(check.currentValue.u32Value),
                                "name": get_unicode(check.settingName)[:64],
                                "rc": start_rc, "error": start_rc != NVAPI_OK,
                            })
                finally:
                    try:
                        destroy_session(session2)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as exc:  # noqa: BLE001 - 검증 실패는 '적용 미확인'
                verify_error = str(exc)

        verdict = verify_rows(verified_rows, plan) if verified_rows else {
            "verified": False, "checked": 0, "mismatched": [], "missing": [p["id"] for p in plan]}
        ok = save_rc == NVAPI_OK and all(not row["error"] for row in rows) and verdict["verified"]
        if ok:
            message = "NVAPI 저장 · 재조회 확인 완료 · " + ", ".join(
                f"0x{item['id']:08X}={item['value']}" for item in plan)
        elif save_rc != NVAPI_OK:
            message = f"NVAPI 저장 실패 · {status_text(save_rc)}"
        elif verify_error:
            message = f"NVAPI 저장은 됐지만 재조회 확인 실패 · {verify_error}"
        elif verdict["mismatched"]:
            message = ("NVAPI 재조회 값이 다릅니다 · " + ", ".join(
                f"0x{row['id']:08X}={row['value']}" for row in verdict["mismatched"][:3]))
        else:
            message = "NVAPI 재조회에서 설정을 확인하지 못했습니다"
        return {"ok": ok, "verified": bool(verdict["verified"]), "supported": True,
                "rc": save_rc, "status": status_text(save_rc), "profile": profile_name,
                "settings": [{"id": f"0x{row['id']:08X}", "value": row["value"],
                              "rc": row["rc"], "status": row["status"],
                              "created": row["created"]} for row in rows],
                "verify": {**verdict, "rows": verified_rows, "error": verify_error},
                "struct": report, "message": message}
    except Exception as exc:  # noqa: BLE001 - NVAPI 실패는 폴백으로 넘긴다
        return {"ok": False, "verified": False, "supported": True, "rc": None,
                "message": f"NVAPI 오류 · {exc}"}


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
