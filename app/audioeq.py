"""사플(발소리) 강조 EQ — Equalizer APO 프리셋 + 윈도우 음량 이퀄라이제이션.

두 갈래
------
1. **Equalizer APO (권장 · 실제 이퀄라이저)** — 설치돼 있으면 서든 발소리 대역
   ``2~6kHz`` 를 올리는 프리셋을 ``config.txt`` 에 넣는다.  원본은 백업하고,
   프리셋은 시작·끝 표식 사이에만 넣어 두어 **해제하면 원래 파일로 되돌린다**.
2. **윈도우 음량 이퀄라이제이션(Loudness Equalization)** — 기본 출력 장치의
   ``FxProperties`` 에 개선 기능(사운드 효과) 항목을 넣어 준다.  이 값들은
   장치별로 다르고 윈도우 업데이트가 지울 수 있어, 넣기 전 **장치의 FxProperties
   전체를 백업**하고 복구 시 그대로 되돌린다.

윈도우 음량 이퀄라이제이션은 효과 목록을 켜는 값(``d04e05a6…``)만 넣고,
실제 on/off 플래그(``fc52a749…``)는 건드리지 않는다 — 그 플래그는 윈도우
버전마다 구조가 달라 잘못 쓰면 소리가 나지 않을 수 있다.  대신 값 적용 뒤
사운드 속성 창을 열어 '음량 균등화' 확인을 안내한다.

레지스트리는 :class:`netopt.Registry` 인터페이스로만 만진다 (테스트는 FakeRegistry).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import netopt, paths

HKLM = netopt.HKLM
RENDER_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"

# 효과 목록(개선 기능) 노출 값 — 마이크로소프트 기본 APO GUID.
#   ,1 = PreMixEffectClsid, ,2 = PostMixEffectClsid, ,3 = 개선 기능 탭 UI
FX_EFFECT_VALUES: tuple[tuple[str, str], ...] = (
    ("{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},1", "{62dc1a93-ae24-464c-a43e-452f824c4250}"),
    ("{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},2", "{637c490d-eee3-4c0a-973f-371958802da2}"),
    ("{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},3", "{5860E1C5-F95C-4a7a-8EC8-8AEF24F379A1}"),
)
FX_PREFIX = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d}"
NAME_KEYS = ("{a45c254e-df1c-4efd-8020-67d146a850e0},2",
             "{b3f8fa53-0004-438e-9003-51a46e139bfc},6")

MARK_BEGIN = "# >>> SA AIM LABS 사플 프리셋 (2~6kHz) >>>"
MARK_END = "# <<< SA AIM LABS 사플 프리셋 <<<"

# 발소리(스텝·장전·폭발 잔향) 대역 강조 — 과하지 않게 +3~4.5dB
PRESET_LINES: tuple[str, ...] = (
    "Preamp: -4 dB",
    "Filter: ON PK Fc 2000 Hz Gain 2.0 dB Q 1.2",
    "Filter: ON PK Fc 3000 Hz Gain 4.0 dB Q 1.4",
    "Filter: ON PK Fc 4500 Hz Gain 4.5 dB Q 1.4",
    "Filter: ON PK Fc 6000 Hz Gain 3.0 dB Q 1.6",
)

# 테스트에서 윈도우 동작을 흉내 낼 수 있게 모듈 상수로 둔다.
IS_WINDOWS = os.name == "nt"

BACKUP_FILE = "audioeq_backup.json"
RESULT_FILE = "audioeq_result.json"
APO_DIRS = ("EqualizerAPO",)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- Equalizer APO
def apo_candidates() -> list[Path]:
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"),
             os.environ.get("ProgramFiles(x86)"), r"C:\Program Files", r"C:\Program Files (x86)"]
    out: list[Path] = []
    for root in roots:
        if not root:
            continue
        for name in APO_DIRS:
            path = Path(root) / name / "config" / "config.txt"
            if path not in out:
                out.append(path)
    return out


def find_apo(paths_to_check: list[Path] | None = None) -> Path | None:
    for path in (paths_to_check if paths_to_check is not None else apo_candidates()):
        try:
            if path.exists():
                return path
        except OSError:
            continue
    return None


def preset_block() -> str:
    return "\n".join([MARK_BEGIN, *PRESET_LINES, MARK_END])


def has_preset(text: str) -> bool:
    return MARK_BEGIN in str(text or "")


def strip_block(text: str) -> str:
    """프리셋 블록만 정확히 걷어 낸다 (사용자가 쓴 다른 줄은 그대로)."""
    lines = str(text or "").splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == MARK_BEGIN:
            inside = True
            continue
        if line.strip() == MARK_END:
            inside = False
            continue
        if not inside:
            out.append(line)
    # 블록을 걷어 낸 뒤 생긴 끝쪽 빈 줄 정리
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + ("\n" if out else "")


def with_preset(text: str) -> str:
    base = strip_block(text)
    block = preset_block()
    return (base.rstrip("\n") + "\n\n" if base.strip() else "") + block + "\n"


def apply_apo(config: Path, backup_dir: Path) -> dict[str, Any]:
    """프리셋 적용 — 원본 config.txt 는 백업 폴더에 보관한다."""
    try:
        original = config.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "message": f"Equalizer APO 설정을 읽지 못했습니다 · {exc}"}
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / "apo_config_backup.txt"
    try:
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
        config.write_text(with_preset(original), encoding="utf-8")
    except PermissionError:
        return {"ok": False, "needs_admin": True,
                "message": "Equalizer APO 설정은 관리자 권한이 필요합니다 · UAC 를 승인해 주세요"}
    except OSError as exc:
        return {"ok": False, "message": f"설정을 쓰지 못했습니다 · {exc}"}
    return {"ok": True, "changed": not has_preset(original), "config": str(config),
            "backup": str(backup),
            "message": "🎧 사플 EQ 프리셋을 Equalizer APO 에 넣었습니다 · 사운드 장치를 다시 선택하면 적용됩니다"}


def remove_apo(config: Path, backup_dir: Path) -> dict[str, Any]:
    """프리셋 해제 — 백업본이 있으면 그대로 되돌리고, 없으면 블록만 걷어 낸다."""
    backup = backup_dir / "apo_config_backup.txt"
    try:
        if backup.exists():
            original = backup.read_text(encoding="utf-8", errors="replace")
            config.write_text(original, encoding="utf-8")
            backup.unlink()
            return {"ok": True, "restored": "backup", "config": str(config),
                    "message": "↩ Equalizer APO 설정을 원래대로 되돌렸습니다"}
        if not config.exists():
            return {"ok": True, "restored": "none", "message": "되돌릴 Equalizer APO 설정이 없습니다"}
        current = config.read_text(encoding="utf-8", errors="replace")
        config.write_text(strip_block(current), encoding="utf-8")
        return {"ok": True, "restored": "block", "config": str(config),
                "message": "↩ 사플 EQ 프리셋을 걷어 냈습니다"}
    except PermissionError:
        return {"ok": False, "needs_admin": True,
                "message": "Equalizer APO 설정은 관리자 권한이 필요합니다 · UAC 를 승인해 주세요"}
    except OSError as exc:
        return {"ok": False, "message": f"설정을 되돌리지 못했습니다 · {exc}"}


# ---------------------------------------------------------------- 음량 이퀄라이제이션
def _device_name(reg: Any, path: str) -> str:
    for name in NAME_KEYS:
        exists, value, kind = reg.get(HKLM, f"{path}\\Properties", name)
        if exists and str(value or "").strip():
            return str(value).strip()[:60]
    return ""


def render_devices(reg: Any = None) -> list[dict[str, Any]]:
    """활성 출력 장치 목록 — 이름 · 상태 · 개선 기능 값 유무."""
    reg = netopt.registry(reg)
    if not getattr(reg, "supported", False) or not IS_WINDOWS:
        return []
    out: list[dict[str, Any]] = []
    for guid in reg.subkeys(HKLM, RENDER_KEY):
        base = f"{RENDER_KEY}\\{guid}"
        exists, device_state, _kind = reg.get(HKLM, base, "DeviceState")
        state_value = int(device_state) if exists else 0
        fx = reg.values(HKLM, f"{base}\\FxProperties")
        out.append({
            "guid": guid,
            "name": _device_name(reg, base) or guid[:18],
            "state": state_value,
            "active": state_value == 1,
            "fx": fx,
            "loudness": all(name in fx for name, _v in FX_EFFECT_VALUES),
        })
    return out


def loudness_state(reg: Any = None) -> dict[str, Any]:
    if not IS_WINDOWS:
        return {"supported": False, "devices": [], "applied": False,
                "message": "윈도우에서만 동작합니다"}
    devices = render_devices(reg)
    active = [d for d in devices if d["active"]]
    applied = bool(active) and all(d["loudness"] for d in active)
    return {"supported": bool(devices), "devices": active, "applied": applied,
            "message": ("음량 이퀄라이제이션 값 적용됨" if applied
                        else ("출력 장치 없음" if not devices else "적용 전"))}


def _backup_file(path: str | os.PathLike[str] | None = None) -> Path:
    return Path(path) if path else paths.data_dir() / BACKUP_FILE


def apply_loudness(reg: Any = None, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """활성 출력 장치의 FxProperties 에 개선 기능 값을 넣는다 (전체 백업 먼저)."""
    reg = netopt.registry(reg)
    if not IS_WINDOWS or not getattr(reg, "supported", False):
        return {"ok": False, "unsupported": True, "simulated": True,
                "message": "윈도우 음량 이퀄라이제이션은 윈도우에서만 적용됩니다 (지금은 시뮬레이션)"}
    devices = [d for d in render_devices(reg) if d["active"]]
    if not devices:
        return {"ok": False, "message": "활성 출력 장치를 찾지 못했습니다 · 스피커·헤드셋을 연결해 주세요"}
    target = _backup_file(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"at": _now(), "devices": [
            {"guid": d["guid"], "name": d["name"], "fx": d["fx"]} for d in devices]},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "message": f"백업 파일을 쓰지 못했습니다 · {exc}"}
    changed: list[str] = []
    errors: list[str] = []
    for device in devices:
        base = f"{RENDER_KEY}\\{device['guid']}\\FxProperties"
        for name, value in FX_EFFECT_VALUES:
            try:
                reg.set(HKLM, base, name, value, 1)
            except PermissionError:
                errors.append(f"{device['name']} · 관리자 권한 필요")
                break
            except OSError as exc:
                errors.append(f"{device['name']} · {exc}")
                break
        else:
            changed.append(device["name"])
    return {"ok": bool(changed), "changed": changed, "errors": errors,
            "backup": str(target),
            "needs_reboot": bool(changed),
            "state": loudness_state(reg),
            "message": ("🎧 윈도우 음량 이퀄라이제이션 값을 넣었습니다 · 사운드 속성 → 개선 기능에서 '음량 균등화'를 켜 주세요"
                        if changed else "값을 넣지 못했습니다 · 관리자 권한을 확인해 주세요")}


def restore_loudness(reg: Any = None, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """백업한 FxProperties 값으로 되돌린다 (추가한 값은 지운다)."""
    reg = netopt.registry(reg)
    if not IS_WINDOWS:
        return {"ok": False, "unsupported": True, "simulated": True,
                "message": "윈도우에서만 동작합니다 (지금은 시뮬레이션)"}
    target = _backup_file(path)
    if not target.exists():
        return {"ok": False, "message": "복구 지점이 없습니다 · 먼저 음량 이퀄라이제이션을 적용해 주세요"}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": f"백업 파일을 읽지 못했습니다 · {exc}"}
    restored = 0
    for device in payload.get("devices") or []:
        base = f"{RENDER_KEY}\\{device.get('guid')}\\FxProperties"
        original = device.get("fx") or {}
        current = reg.values(HKLM, base)
        for name in current:
            if name in original:
                continue
            if name.startswith(FX_PREFIX):
                try:
                    reg.delete(HKLM, base, name)
                except OSError:
                    pass
        for name, value in original.items():
            try:
                reg.set(HKLM, base, name, value, 1 if isinstance(value, str) else 4)
                restored += 1
            except OSError:
                pass
    try:
        target.unlink()
    except OSError:
        pass
    return {"ok": True, "restored": restored, "state": loudness_state(reg),
            "message": "↩ 음량 이퀄라이제이션 값을 원래대로 되돌렸습니다"}


# ---------------------------------------------------------------- 종합 상태·적용
def state(config_paths: list[Path] | None = None, reg: Any = None) -> dict[str, Any]:
    apo = find_apo(config_paths)
    preset = False
    if apo:
        try:
            preset = has_preset(apo.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            preset = False
    loud = loudness_state(reg)
    return {
        "supported": IS_WINDOWS,
        "apo": {"installed": bool(apo), "path": str(apo) if apo else None, "preset": preset},
        "loudness": loud,
        "active": "apo" if preset else ("loudness" if loud.get("applied") else None),
        "label": ("Equalizer APO 프리셋 적용됨" if preset
                  else ("음량 이퀄라이제이션 적용됨" if loud.get("applied")
                        else ("Equalizer APO 감지됨" if apo else "적용 전"))),
    }


def apply(config_paths: list[Path] | None = None, reg: Any = None,
          backup_dir: Path | None = None) -> dict[str, Any]:
    """사플 EQ 원클릭 — Equalizer APO 가 있으면 프리셋, 없으면 윈도우 음량 균등화."""
    apo = find_apo(config_paths)
    if apo:
        result = apply_apo(apo, backup_dir or (paths.data_dir() / "audioeq"))
        result["route"] = "apo"
        result["state"] = state(config_paths, reg)
        return result
    result = apply_loudness(reg)
    result["route"] = "loudness"
    result["state"] = state(config_paths, reg)
    if result.get("ok"):
        result["guide"] = "사운드 속성 → 재생 장치 → 속성 → 개선 기능 → 음량 균등화 체크"
    return result


def remove(config_paths: list[Path] | None = None, reg: Any = None,
           backup_dir: Path | None = None) -> dict[str, Any]:
    apo = find_apo(config_paths)
    parts: list[dict[str, Any]] = []
    if apo:
        try:
            parts.append(remove_apo(apo, backup_dir or (paths.data_dir() / "audioeq")))
        except Exception as exc:  # noqa: BLE001
            parts.append({"ok": False, "message": str(exc)})
    loud = restore_loudness(reg)
    if not loud.get("unsupported"):
        parts.append(loud)
    ok = any(p.get("ok") for p in parts)
    return {"ok": ok, "parts": parts, "state": state(config_paths, reg),
            "message": ("↩ 사플 EQ 설정을 되돌렸습니다" if ok else "되돌릴 설정이 없습니다")}


# ---------------------------------------------------------------- 관리자 권한
def open_sound_panel() -> dict[str, Any]:
    """윈도우 사운드 속성(재생) 창 열기 — 사용자가 직접 체크할 때."""
    if not IS_WINDOWS:
        return {"ok": False, "unsupported": True, "message": "윈도우에서만 열립니다"}
    try:
        subprocess.Popen(["control", "mmsys.cpl,,1"], shell=False)  # noqa: S607
        return {"ok": True, "message": "사운드 속성 창을 열었습니다 · 재생 장치 → 속성 → 개선 기능"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"사운드 속성 창을 열지 못했습니다 · {exc}"}


def elevate_help(action: str = "apply") -> dict[str, Any]:
    if not IS_WINDOWS:
        raise RuntimeError("관리자 권한 상승은 윈도우에서만 지원합니다")
    import ctypes

    result_file = paths.data_dir() / RESULT_FILE
    flag = "--audioeq-restore" if action == "restore" else "--audioeq-apply"
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, f'{flag} --result "{result_file}"'
    else:
        exe = sys.executable
        script = Path(__file__).resolve().parent.parent / "main.py"
        args = f'"{script}" {flag} --result "{result_file}"'
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 1)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"관리자 권한 요청을 보내지 못했습니다 · {exc}") from exc
    if int(rc) <= 32:
        raise RuntimeError("관리자 권한 요청이 취소되었습니다 (UAC 거부)")
    return {"elevated": True, "result_file": str(result_file),
            "message": "관리자 권한으로 적용 중입니다 · UAC 창을 승인해 주세요"}


def read_result() -> dict[str, Any] | None:
    p = paths.data_dir() / RESULT_FILE
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        p.unlink()
    except OSError:
        pass
    return data


def run_elevated(action: str, result_path: str | os.PathLike[str]) -> int:
    try:
        result = remove() if action == "restore" else apply()
        result["elevated"] = True
        netopt.write_result(result_path, result)
        return 0 if result.get("ok") else 1
    except Exception as exc:  # noqa: BLE001
        netopt.write_result(result_path, {"ok": False, "elevated": True, "message": str(exc)})
        return 1
