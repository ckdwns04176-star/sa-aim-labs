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
STAIRFIX_VALUE = 0x00401241         # 0x00401241 — guide value
DEFAULT_VALUE = 0x00020041          # driver default seen on a stock database

# Values we know how to describe (used by the UI/telemetry only).
KNOWN_AA_COMPAT = {
    0x00401241: "계단현상·타격효과 제거",
    0x00020041: "기본값",
    0x00000000: "해제",
}


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
def set_stairfix(blob: bytes | bytearray, exe: str = GAME_EXE) -> tuple[bytearray, dict[str, Any]]:
    """Return a patched copy with AA compatibility = 0x00401241.

    ``changed`` is False when the value was already right (or when the profile
    exists but the driver did not store the setting, which we report instead of
    silently pretending).
    """
    apps = find_apps(blob, exe)
    if not apps:
        raise DrsFormatError(f"{exe} 프로필을 Drs DB에서 찾지 못했습니다")
    out = bytearray(blob)
    report: dict[str, Any] = {"profiles": len(apps), "changed": False, "before": None, "after": None}
    for app in apps:
        off = app["profile_offset"]
        target = None
        for rec in iter_settings(blob, off):
            if rec["setting_id"] == SETTING_AA_COMPAT:
                target = rec
                break
        if target is None or target["value_size"] < 4:
            continue
        before = struct.unpack_from("<I", blob, target["value_offset"])[0]
        report["before"] = before if report["before"] is None else report["before"]
        if before == STAIRFIX_VALUE:
            report["after"] = before
            continue
        struct.pack_into("<I", out, target["value_offset"], STAIRFIX_VALUE)
        report["changed"] = True
        report["after"] = STAIRFIX_VALUE
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
