"""NVIDIA Drs database reader / writer for the 계단현상 끄기 (stair-fix) setting.

The driver keeps per-application 3D settings in
``C:\\ProgramData\\NVIDIA Corporation\\Drs\\nvdrsdb0.bin`` (plus the mirror
``nvdrsdb1.bin``).  The binary is a flat image:

    [app table]
      name : UTF-16LE, zero padded
      meta : 16 bytes   u16 0x016e | u16 kind | u32 count | u32 0 | u32 profile_offset
    [profile]
      head : 16 bytes   u16 0x0053 | u16 kind | u32 bytes | u32 count | u32 next
      settings ...
    [setting]
      u32  (size << 16) | type      size = whole record length (multiple of 4)
      u32  setting_id
      u32  flags
      u8   value[size - 12]

``Antialiasing compatibility`` is setting ``0x00D55F7D``.  The Sudden Attack
community guide (타격효과 · 중세철창 · 계단현상 제거) uses ``0x00401241`` for it,
which this module can read and write **in place** — the record length never
changes, so no offset in the file has to be fixed up.

Nothing here touches the disk; :mod:`app.nvidia` owns backups and elevation, and
these helpers are what makes 계단현상 끄기 work without shipping a whole
third-party Drs pack.
"""
from __future__ import annotations

import struct
from typing import Any, Iterable

APP_MAGIC = 0x016E
PROFILE_MAGIC = 0x0053
APP_META_SIZE = 16
PROFILE_HEAD_SIZE = 16
MAX_RECORD = 0x1000

GAME_EXE = "suddenattack.exe"

# NVIDIA Profile Inspector setting ids used by the Sudden Attack guide.
SETTING_AA_COMPAT = 0x00D55F7D      # Antialiasing compatibility (DX9) / 계단현상 · 타격효과 · 중세철창
SETTING_AA_COMPAT2 = 0x1095DEF8     # Antialiasing compatibility (second dword, left untouched)
SETTING_AA_MODE = 0x10D773D2        # Antialiasing - Mode (0=앱 설정, 1=Override)
SETTING_AA_SETTING = 0x101E61A9     # Antialiasing - Setting (1 = 2x MSAA)
STAIRFIX_VALUE = 0x00401241         # 0x00401241 — guide value
DEFAULT_VALUE = 0x00020041          # driver default seen on a stock database

# 계단현상 끄기 = 이 세트가 함께 있어야 드라이버가 0x00401241 을 실제로 쓴다.
# (AA Mode/Setting 이 꺼져 있으면 compatibility 값은 무시된다 — v1.0.0 수정)
STAIRFIX_SET: tuple[tuple[int, int], ...] = (
    (SETTING_AA_COMPAT, STAIRFIX_VALUE),
    (SETTING_AA_MODE, 1),
    (SETTING_AA_SETTING, 1),
)

# Values we know how to describe (used by the UI/telemetry only).
KNOWN_AA_COMPAT = {
    0x00401241: "계단현상·타격효과 제거",
    0x00020041: "기본값",
    0x00000000: "해제",
}

# 적용 후 '무엇이 바뀌었는지' 보여줄 설정 이름.
# v1.3.1 — NVIDIA Profile Inspector 카탈로그(nvprofileinspector.com 설정 목록 · NVAPI 이름)로
# 확인한 ID 를 쓴다.  예전 표는 0x1095DEF8(실제로는 SLI Compatibility Bits DX9)을
# '안티앨리어싱 호환성 보조값'으로 표시하는 등 이름이 틀렸다.
SETTING_NAMES: dict[int, tuple[str, str]] = {
    SETTING_AA_COMPAT: ("계단현상 끄기", "Antialiasing compatibility (DX9)"),
    SETTING_AA_MODE: ("안티앨리어싱 모드", "Antialiasing - Mode"),
    SETTING_AA_SETTING: ("안티앨리어싱 설정", "Antialiasing - Setting"),
    0x00A879CF: ("수직 동기", "Vertical Sync"),
    0x1057EB71: ("전원 관리 모드", "Power Management - Mode"),
    0x0005F543: ("저지연 모드 (제어판 상태)", "Ultra Low Latency - CPL State"),
    0x10835000: ("저지연 모드 (사용)", "Ultra Low Latency - Enabled"),
    0x007BA09E: ("최대 사전 렌더 프레임", "Maximum Pre-Rendered Frames"),
    0x00CE2691: ("텍스처 필터링 품질", "Texture filtering - Quality"),
    0x20C1221E: ("스레드 최적화", "Threaded optimization"),
    0x10835002: ("프레임 제한 (V3)", "Frame Rate Limiter V3"),
    0x00AC8497: ("셰이더 캐시 크기", "Shader Cache Size"),
    0x0064B541: ("선호 주사율", "Preferred refresh rate"),
    0x00A06946: ("SLI 호환 비트 (DX10·DX11)", "SLI Compatibility Bits Dx10 + Dx11"),
    SETTING_AA_COMPAT2: ("SLI 호환 비트 (DX9)", "SLI Compatibility Bits DX9"),
    0x1033CEC1: ("SLI 모드 (드라이버 정의)", "Nvidia Predefined SLI Mode"),
    0x1033CEC2: ("SLI 모드 (DX10, 드라이버 정의)", "Nvidia Predefined SLI Mode On DirectX 10"),
    0x1033DCD2: ("SLI GPU 수 (드라이버 정의)", "Nvidia Predefined Number Of Gpus To Use On SLI Rendering Mode"),
    0x1033DCD3: ("SLI GPU 수 (DX10, 드라이버 정의)",
                 "Nvidia Predefined Number Of Gpus To Use On SLI Rendering Mode On Directx 10"),
    0x0095DEF9: ("SLI 브로드캐스트 (DX9)", "SLI Broadcast DX9"),
}

# 값 → 사람이 읽는 이름 (아는 것만 · 모르면 0x… 그대로 보여 준다)
VALUE_LABELS: dict[int, dict[int, str]] = {
    0x00A879CF: {0x00000000: "앱 설정 따름", 0x08416747: "강제 끔", 0x18888888: "강제 켬",
                 0x60925292: "적응형", 0x034B2C88: "빠른 동기화"},
    0x1057EB71: {0: "드라이버 제어", 1: "최대 성능 선호", 2: "최적 전력", 5: "일관 성능 선호"},
    0x0005F543: {0: "끔", 1: "켬", 2: "울트라"},
    0x10835000: {0: "끔", 1: "켬"},
    0x007BA09E: {0: "0 (즉시)", 1: "1 프레임", 2: "2 프레임", 3: "3 프레임"},
    0x00CE2691: {0: "고품질", 1: "품질", 2: "성능", 3: "고성능"},
    0x20C1221E: {0: "끔", 1: "켬", 2: "자동"},
    0x10835002: {0: "해제"},
    SETTING_AA_COMPAT: KNOWN_AA_COMPAT,
    SETTING_AA_MODE: {0: "앱 설정 사용", 1: "앱 설정 무시(Override)"},
    SETTING_AA_SETTING: {0: "앱 설정", 1: "2x", 2: "4x", 4: "8x"},
}

# '고성능 게이밍' 체크리스트 — 적용 결과 팝업에서 항목별 상태를 보여 준다.
CHECKLIST: tuple[tuple[int, str, str], ...] = (
    (SETTING_AA_COMPAT, "계단현상 끄기", "0x00401241 로 패치"),
    (0x1057EB71, "전원 관리 모드", "최대 성능 선호"),
    (0x0005F543, "저지연 모드", "켬(1)"),
    (0x007BA09E, "최대 사전 렌더 프레임", "1 프레임"),
    (0x00CE2691, "텍스처 필터링 품질", "성능"),
    (0x00A879CF, "수직 동기", "강제 끔"),
)


def value_label(setting_id: int, value: int | None) -> str:
    if value is None:
        return "없음"
    names = VALUE_LABELS.get(setting_id) or {}
    if value in names:
        return names[value]
    return f"0x{value:08X}"


def setting_name(setting_id: int) -> tuple[str, str]:
    """(한글 이름, 영문 이름) — 모르는 ID 는 '0x…' 로 표시한다."""
    hit = SETTING_NAMES.get(setting_id)
    if hit:
        return hit
    return (f"드라이버 설정 0x{setting_id:08X}", f"setting 0x{setting_id:08X}")


def values_of(blob: bytes | bytearray, exe: str = GAME_EXE) -> dict[int, bytes]:
    """서든어택 프로필의 설정 값 {setting_id: raw value bytes}."""
    off = profile_offset(blob, exe)
    if off is None:
        return {}
    return {rec["setting_id"]: rec["value"] for rec in iter_settings(blob, off)}


def as_text(raw: bytes | None) -> str:
    """설정 값 표시 — 4바이트면 0x…, 아니면 앞 8바이트 hex."""
    if raw is None:
        return "없음"
    if len(raw) >= 4:
        return f"0x{int.from_bytes(raw[:4], 'little'):08X}"
    return raw.hex() or "없음"


def _dword(raw: bytes | None) -> int | None:
    if not raw or len(raw) < 4:
        return None
    return int.from_bytes(raw[:4], "little")


def change_list(before: bytes | bytearray | None, after: bytes | bytearray | None,
                exe: str = GAME_EXE) -> list[dict[str, Any]]:
    """두 Drs DB 사이에서 값이 달라진 서든어택 프로필 설정들 — 적용 내역 모달용.

    값은 ``before`` / ``after`` (0x…) 와 ``label_before`` / ``label_after`` (전원 관리
    모드 → 최대 성능 선호 같은 읽는 이름) 를 함께 돌려준다.  계단현상 끄기
    (0x00D55F7D) 는 항상 맨 앞에 온다.
    """
    old = values_of(before, exe) if before else {}
    new = values_of(after, exe) if after else {}
    rows: list[dict[str, Any]] = []
    for setting_id in sorted(set(old) | set(new)):
        was, now = old.get(setting_id), new.get(setting_id)
        if (was or b"") == (now or b""):
            continue
        ko, en = setting_name(setting_id)
        was_v, now_v = _dword(was), _dword(now)
        rows.append({"id": f"0x{setting_id:08X}", "name": ko, "en": en,
                     "before": as_text(was), "after": as_text(now),
                     "label_before": value_label(setting_id, was_v),
                     "label_after": value_label(setting_id, now_v),
                     "removed": now is None, "added": was is None,
                     "key": setting_id == SETTING_AA_COMPAT})
    rows.sort(key=lambda r: (not r["key"], r["id"]))
    return rows


def profile_items(blob: bytes | bytearray | None, exe: str = GAME_EXE) -> list[dict[str, Any]]:
    """서든어택 프로필에 실제로 들어 있는 설정 목록 (값 + 읽는 이름).

    '값이 바뀐 항목이 없다'는 안내 대신, 이 프로필이 무엇을 설정하고 있는지
    그대로 보여 주기 위한 자료다.
    """
    if not blob:
        return []
    off = profile_offset(blob, exe)
    if off is None:
        return []
    out: list[dict[str, Any]] = []
    for rec in iter_settings(blob, off):
        sid = int(rec["setting_id"])
        ko, en = setting_name(sid)
        value = _dword(rec["value"])
        out.append({"id": f"0x{sid:08X}", "name": ko, "en": en,
                    "value": as_text(rec["value"]), "label": value_label(sid, value)})
    out.sort(key=lambda r: (not (r["name"] != f"드라이버 설정 {r['id'].upper()}"), r["id"]))
    return out


def named_profile_offset(blob: bytes | bytearray | None, name: str) -> int | None:
    """'Base Profile' 처럼 이름으로 저장된 프로필의 오프셋.

    앱 프로필은 이름 뒤에 16바이트 앱 메타(0x016E)가 오지만, 기본 프로필은
    이름 뒤에 곧바로 프로필 헤더(0x0053)가 온다 — 둘 다 찾아본다.
    """
    if not blob:
        return None
    needle = str(name).lower().encode("utf-16-le")
    text = bytes(blob).lower()
    idx = text.find(needle)
    if idx < 0:
        return None
    end = idx + len(needle)
    for pad in range(0, 17, 2):
        pos = end + pad
        if pos + PROFILE_HEAD_SIZE > len(blob):
            return None
        magic = struct.unpack_from("<H", blob, pos)[0]
        if magic == APP_MAGIC:
            meta = app_meta_offset(blob, idx, needle)
            return None if meta is None else struct.unpack_from("<I", blob, meta + 12)[0]
        if magic != PROFILE_MAGIC:
            continue
        _magic, _kind, nbytes, count, _next = struct.unpack_from("<HHIII", blob, pos)
        if nbytes == 0 or count == 0 or pos + nbytes > len(blob):
            continue
        return pos
    return None


def base_change_list(before: bytes | bytearray | None, after: bytes | bytearray | None) -> list[dict[str, Any]]:
    """전역('Base Profile') 설정 중 바뀐 항목 — 저지연·전원 관리·수직 동기 등이 여기 산다."""
    if not before or not after:
        return []
    off_a = named_profile_offset(before, "Base Profile")
    off_b = named_profile_offset(after, "Base Profile")
    if off_a is None or off_b is None:
        return []
    old = {rec["setting_id"]: rec["value"] for rec in iter_settings(before, off_a)}
    new = {rec["setting_id"]: rec["value"] for rec in iter_settings(after, off_b)}
    rows: list[dict[str, Any]] = []
    for sid in sorted(set(old) | set(new)):
        was, now = old.get(sid), new.get(sid)
        if (was or b"") == (now or b""):
            continue
        ko, en = setting_name(sid)
        was_v, now_v = _dword(was), _dword(now)
        rows.append({"id": f"0x{sid:08X}", "name": ko, "en": en, "scope": "전역 프로필",
                     "before": as_text(was), "after": as_text(now),
                     "label_before": value_label(sid, was_v),
                     "label_after": value_label(sid, now_v),
                     "removed": now is None, "added": was is None})
    return rows


def checklist_state(blob: bytes | bytearray | None) -> list[dict[str, Any]]:
    """표준 '고성능 게이밍' 항목이 이 프로필에서 어떻게 되어 있는지."""
    if not blob:
        return []
    off = profile_offset(blob, GAME_EXE)
    values = {rec["setting_id"]: _dword(rec["value"]) for rec in iter_settings(blob, off)} if off is not None else {}
    base_off = named_profile_offset(blob, "Base Profile")
    base = ({rec["setting_id"]: _dword(rec["value"]) for rec in iter_settings(blob, base_off)}
            if base_off is not None else {})
    rows: list[dict[str, Any]] = []
    for sid, label, want in CHECKLIST:
        mine = values.get(sid)
        inherited = base.get(sid)
        if mine is None:
            rows.append({"id": f"0x{sid:08X}", "name": label, "want": want, "value": None,
                         "label_value": "전역 설정 상속" if inherited is None else value_label(sid, inherited),
                         "status": "global"})
            continue
        status = "ok" if (sid == SETTING_AA_COMPAT and mine == STAIRFIX_VALUE) else "profile"
        rows.append({"id": f"0x{sid:08X}", "name": label, "want": want, "value": mine,
                     "label_value": value_label(sid, mine), "status": status})
    return rows


def file_signature(paths: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Drs 파일별 크기·해시 — 실제로 폴더가 바뀌었는지 보여 주는 증거."""
    import hashlib
    from pathlib import Path as _Path

    out: dict[str, dict[str, Any]] = {}
    for item in paths or []:
        path = _Path(item)
        try:
            data = path.read_bytes()
        except OSError:
            out[path.name] = {"exists": False, "size": 0, "sha": ""}
            continue
        out[path.name] = {"exists": True, "size": len(data),
                          "sha": hashlib.sha256(data).hexdigest()}
    return out


class DrsFormatError(RuntimeError):
    pass


# --------------------------------------------------------------------- app table
def app_meta_offset(blob: bytes | bytearray, name_start: int, name_bytes: bytes) -> int | None:
    """Return the offset of the 16-byte app metadata that follows an exe name."""
    end = name_start + len(name_bytes)
    for pad in range(0, 17, 2):
        pos = end + pad
        if pos + APP_META_SIZE > len(blob):
            return None
        magic, _kind, _count, _zero, profile_off = struct.unpack_from("<HHIII", blob, pos)
        if magic != APP_MAGIC:
            continue
        if profile_off == 0 or profile_off + PROFILE_HEAD_SIZE > len(blob):
            continue
        if struct.unpack_from("<H", blob, profile_off)[0] != PROFILE_MAGIC:
            continue
        return pos
    return None


def find_apps(blob: bytes | bytearray, exe: str = GAME_EXE) -> list[dict[str, Any]]:
    """Every app-table entry whose exe name matches exactly (case-insensitive)."""
    needle = exe.lower().encode("utf-16-le")
    text = bytes(blob).lower()
    out: list[dict[str, Any]] = []
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx < 0:
            break
        start = idx + 2
        # exact match: the name must end with a NUL right after the needle
        if text[idx + len(needle): idx + len(needle) + 2] != b"\x00\x00":
            continue
        meta = app_meta_offset(blob, idx, needle)
        if meta is None:
            continue
        magic, kind, count, _zero, profile_off = struct.unpack_from("<HHIII", blob, meta)
        out.append({
            "name_offset": idx,
            "meta_offset": meta,
            "profile_offset": profile_off,
            "kind": kind,
            "count": count,
            "profile_header": struct.unpack_from("<HHIII", blob, profile_off),
        })
    return out


def profile_offset(blob: bytes | bytearray, exe: str = GAME_EXE) -> int | None:
    apps = find_apps(blob, exe)
    return apps[0]["profile_offset"] if apps else None


# --------------------------------------------------------------------- settings
def iter_settings(blob: bytes | bytearray, profile_off: int) -> Iterable[dict[str, Any]]:
    """Yield every setting record of one profile (stops at the first bad record)."""
    pos = profile_off + PROFILE_HEAD_SIZE
    limit = len(blob)
    while pos + 12 <= limit:
        tag = struct.unpack_from("<I", blob, pos)[0]
        size = tag >> 16
        kind = tag & 0xFFFF
        if size < 12 or size > MAX_RECORD or size % 4:
            return
        if pos + size > limit:
            return
        setting_id, flags = struct.unpack_from("<II", blob, pos + 4)
        if kind == 0 or setting_id == 0:
            return
        yield {
            "record_offset": pos,
            "size": size,
            "kind": kind,
            "setting_id": setting_id,
            "flags": flags,
            "value_offset": pos + 12,
            "value_size": size - 12,
            "value": bytes(blob[pos + 12: pos + size]),
        }
        pos += size


def settings(blob: bytes | bytearray, profile_off: int) -> list[dict[str, Any]]:
    return list(iter_settings(blob, profile_off))


def get_dword(blob: bytes | bytearray, profile_off: int, setting_id: int) -> int | None:
    for rec in iter_settings(blob, profile_off):
        if rec["setting_id"] == setting_id and rec["value_size"] >= 4:
            return struct.unpack_from("<I", blob, rec["value_offset"])[0]
    return None


def aa_compat(blob: bytes | bytearray, exe: str = GAME_EXE) -> int | None:
    """Current Antialiasing compatibility value of the game profile, or None."""
    off = profile_offset(blob, exe)
    if off is None:
        return None
    return get_dword(blob, off, SETTING_AA_COMPAT)


def has_profile(blob: bytes | bytearray, exe: str = GAME_EXE) -> bool:
    off = profile_offset(blob, exe)
    return off is not None and any(True for _ in iter_settings(blob, off))


def describe(value: int | None) -> str:
    if value is None:
        return "프로필 없음"
    return KNOWN_AA_COMPAT.get(value, f"0x{value:08X}")


# --------------------------------------------------------------------- write
def stairfix_values(blob: bytes | bytearray, exe: str = GAME_EXE) -> dict[int, int | None]:
    """계단현상 세트(호환성·AA 모드·AA 설정)의 현재 값."""
    off = profile_offset(blob, exe)
    if off is None:
        return {sid: None for sid, _ in STAIRFIX_SET}
    return {sid: get_dword(blob, off, sid) for sid, _ in STAIRFIX_SET}


def stairfix_ready(blob: bytes | bytearray, exe: str = GAME_EXE) -> bool:
    """세트 3개가 모두 의도한 값인가."""
    values = stairfix_values(blob, exe)
    return all(values.get(sid) == value for sid, value in STAIRFIX_SET)


def profile_dwords(blob: bytes | bytearray, exe: str = GAME_EXE) -> dict[int, int]:
    """서든어택 프로필의 DWORD 설정만 {id: value} 로 꺼낸다 (팩 → 내 프로필 이식용)."""
    off = profile_offset(blob, exe)
    if off is None:
        return {}
    out: dict[int, int] = {}
    for rec in iter_settings(blob, off):
        if rec["value_size"] >= 4:
            out[rec["setting_id"]] = struct.unpack_from("<I", blob, rec["value_offset"])[0]
    return out


def apply_profile_values(blob: bytes | bytearray, values: dict[int, int],
                         exe: str = GAME_EXE) -> tuple[bytearray, dict[str, Any]]:
    """서든 프로필의 **기존 레코드만** values 로 바꾼다.

    다른 게임 프로필·Base Profile 은 건드리지 않는다 (오프셋만 바꿔 쓴다).
    없는 레코드는 ``missing`` 으로 돌려주고, 그건 NVAPI 가 새로 만든다.
    """
    apps = find_apps(blob, exe)
    if not apps:
        raise DrsFormatError(f"{exe} 프로필을 Drs DB에서 찾지 못했습니다")
    out = bytearray(blob)
    changed: list[dict[str, Any]] = []
    missing: list[int] = []
    for app in apps:
        off = app["profile_offset"]
        present = {rec["setting_id"]: rec for rec in iter_settings(blob, off)}
        for setting_id, value in values.items():
            rec = present.get(setting_id)
            if rec is None or rec["value_size"] < 4:
                if setting_id not in missing:
                    missing.append(setting_id)
                continue
            before = struct.unpack_from("<I", blob, rec["value_offset"])[0]
            if before != value:
                struct.pack_into("<I", out, rec["value_offset"], value)
                changed.append({"id": setting_id, "before": before, "after": value})
    return out, {"changed": changed, "missing": missing,
                 "changed_count": len(changed), "total": len(values)}


def set_stairfix(blob: bytes | bytearray, exe: str = GAME_EXE) -> tuple[bytearray, dict[str, Any]]:
    """계단현상 끄기 세트를 적용한 복사본을 돌려준다.

    기존에는 ``Antialiasing compatibility`` 레코드가 있을 때만 값을 바꿔서,
    ① 레코드가 없으면 예외로 실패하고 ② AA Mode/Setting 이 꺼져 있으면
    드라이버가 그 값을 무시했다.  이제는 **있으면 전부 패치**하고, 없는
    레코드는 ``missing`` 으로 보고한다 (NVAPI 경로가 새로 만들 수 있다).
    """
    apps = find_apps(blob, exe)
    if not apps:
        raise DrsFormatError(f"{exe} 프로필을 Drs DB에서 찾지 못했습니다")
    out = bytearray(blob)
    report: dict[str, Any] = {"profiles": len(apps), "changed": False, "before": None,
                              "after": None, "values": [], "missing": []}
    for app in apps:
        off = app["profile_offset"]
        present = {rec["setting_id"]: rec for rec in iter_settings(blob, off)}
        for setting_id, value in STAIRFIX_SET:
            rec = present.get(setting_id)
            if rec is None or rec["value_size"] < 4:
                if setting_id not in report["missing"]:
                    report["missing"].append(setting_id)
                continue
            before = struct.unpack_from("<I", blob, rec["value_offset"])[0]
            row = {"id": f"0x{setting_id:08X}", "before": before, "after": value,
                   "changed": before != value}
            report["values"].append(row)
            if setting_id == SETTING_AA_COMPAT:
                report["before"] = before
                report["after"] = value
            if before != value:
                struct.pack_into("<I", out, rec["value_offset"], value)
                report["changed"] = True
    if report["after"] is None:
        raise DrsFormatError(
            "Drs DB의 서든어택 프로필에 Antialiasing compatibility 항목이 없습니다"
        )
    return out, report


def restore_value(blob: bytes | bytearray, value: int = DEFAULT_VALUE, exe: str = GAME_EXE) -> tuple[bytearray, bool]:
    """Put the AA compatibility value back (used when a backup is not available)."""
    apps = find_apps(blob, exe)
    if not apps:
        raise DrsFormatError(f"{exe} 프로필을 Drs DB에서 찾지 못했습니다")
    out = bytearray(blob)
    changed = False
    for app in apps:
        for rec in iter_settings(blob, app["profile_offset"]):
            if rec["setting_id"] == SETTING_AA_COMPAT and rec["value_size"] >= 4:
                if struct.unpack_from("<I", out, rec["value_offset"])[0] != value:
                    struct.pack_into("<I", out, rec["value_offset"], value)
                    changed = True
                break
    return out, changed


def dump(blob: bytes | bytearray, exe: str = GAME_EXE) -> dict[str, Any]:
    """Small diagnostic used by the elevated helper and the tests."""
    apps = find_apps(blob, exe)
    rows = []
    for app in apps:
        rows.append({
            "profile_offset": app["profile_offset"],
            "settings": [
                {"id": f"0x{rec['setting_id']:08X}", "value": rec["value"].hex()}
                for rec in iter_settings(blob, app["profile_offset"])
            ],
        })
    return {"profiles": len(apps), "aa_compat": aa_compat(blob, exe), "rows": rows}
