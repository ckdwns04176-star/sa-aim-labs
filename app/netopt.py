"""네트워크 핑·반응 최적화 — 원클릭 적용/복구 (레지스트리 · 백업 포함).

적용 항목
--------
1. ``TcpAckFrequency`` · ``TCPNoDelay`` = 1 — 인터페이스마다 TCP ACK 지연을 끈다
   (핑이 튀는 '네이글' 계열 지연 제거).
2. ``NetworkThrottlingIndex`` = 0xFFFFFFFF — 윈도우 멀티미디어 네트워크 스로틀링 해제.
3. ``suddenattack.exe`` → ``PerfOptions\\CpuPriorityClass`` = 3 — 게임 CPU 우선순위 '높음'.
4. 랜카드 ``PnPCapabilities`` = 24 — '전원 절약을 위해 이 장치를 끌 수 있음' 해제.

모든 값은 적용 전에 JSON 으로 백업하고, 복구는 **원래 값으로 되돌리거나 원래
없던 값이면 지운다**.  HKLM 쓰기는 관리자 권한이 필요하므로, 권한이 없으면
``elevate_help()`` 가 UAC 로 같은 실행 파일을 다시 띄운다.

테스트를 위해 레지스트리는 :class:`Registry` 인터페이스로만 만진다 — 리눅스
테스트는 ``FakeRegistry`` 를 넣어 값 계산·백업·복구를 그대로 검증한다.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths

HKLM = "HKLM"

TCP_IFACE_KEY = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
THROTTLE_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
IFEO_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\suddenattack.exe\PerfOptions"
NETCLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"

THROTTLE_OFF = 0xFFFFFFFF
GAME_PRIORITY_HIGH = 3
NIC_NO_POWER_SAVE = 24

# 테스트에서 윈도우 동작을 흉내 낼 수 있게 모듈 상수로 둔다.
IS_WINDOWS = os.name == "nt"

RESULT_FILE = "netopt_result.json"
BACKUP_FILE = "netopt_backup.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 레지스트리
class Registry:
    """winreg 최소 래퍼 (DWORD·SZ · 하위 키 열거)."""

    supported = IS_WINDOWS

    def __init__(self) -> None:
        import winreg  # noqa: PLC0415 - 윈도우에서만 존재

        self._r = winreg

    def _root(self, root: str) -> Any:
        return self._r.HKEY_LOCAL_MACHINE if root == HKLM else self._r.HKEY_CURRENT_USER

    def subkeys(self, root: str, path: str) -> list[str]:
        out: list[str] = []
        try:
            with self._r.OpenKey(self._root(root), path, 0, self._r.KEY_READ) as key:
                index = 0
                while True:
                    try:
                        out.append(self._r.EnumKey(key, index))
                    except OSError:
                        break
                    index += 1
        except OSError:
            return []
        return out

    def exists(self, root: str, path: str) -> bool:
        try:
            with self._r.OpenKey(self._root(root), path, 0, self._r.KEY_READ):
                return True
        except OSError:
            return False

    def get(self, root: str, path: str, name: str) -> tuple[bool, Any, int]:
        try:
            with self._r.OpenKey(self._root(root), path, 0, self._r.KEY_READ) as key:
                value, kind = self._r.QueryValueEx(key, name)
        except OSError:
            return False, None, 0
        if kind == self._r.REG_DWORD:
            return True, int(value or 0), 4
        if kind == self._r.REG_SZ:
            return True, str(value or ""), 1
        return True, value, int(kind)

    def set(self, root: str, path: str, name: str, value: Any, kind: int = 4) -> None:
        reg_kind = self._r.REG_SZ if int(kind) == 1 else self._r.REG_DWORD
        with self._r.CreateKeyEx(self._root(root), path, 0,
                                 self._r.KEY_SET_VALUE | self._r.KEY_WOW64_64KEY) as key:
            self._r.SetValueEx(key, name, 0, reg_kind, value)

    def delete(self, root: str, path: str, name: str) -> bool:
        try:
            with self._r.OpenKey(self._root(root), path, 0, self._r.KEY_SET_VALUE) as key:
                self._r.DeleteValue(key, name)
            return True
        except OSError:
            return False

    def values(self, root: str, path: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            with self._r.OpenKey(self._root(root), path, 0, self._r.KEY_READ) as key:
                index = 0
                while True:
                    try:
                        name, value, _kind = self._r.EnumValue(key, index)
                    except OSError:
                        break
                    out[name] = value
                    index += 1
        except OSError:
            return {}
        return out


class FakeRegistry:
    """테스트용 — {root: {path: {name: (value, kind)}}} 를 그대로 흉내낸다."""

    supported = True

    def __init__(self, tree: dict[str, dict[str, dict[str, tuple[Any, int]]]] | None = None):
        self.tree: dict[str, dict[str, dict[str, tuple[Any, int]]]] = tree or {}

    def _bucket(self, root: str, path: str) -> dict[str, tuple[Any, int]]:
        return self.tree.setdefault(root, {}).setdefault(path, {})

    def subkeys(self, root: str, path: str) -> list[str]:
        prefix = path.rstrip("\\") + "\\"
        found = [p[len(prefix):] for p in self.tree.get(root, {}) if p.startswith(prefix)
                 and "\\" not in p[len(prefix):]]
        return sorted(found)

    def exists(self, root: str, path: str) -> bool:
        return path in self.tree.get(root, {})

    def get(self, root: str, path: str, name: str) -> tuple[bool, Any, int]:
        row = self.tree.get(root, {}).get(path, {}).get(name)
        if row is None:
            return False, None, 0
        return True, row[0], row[1]

    def set(self, root: str, path: str, name: str, value: Any, kind: int = 4) -> None:
        self._bucket(root, path)[name] = (value, int(kind))

    def delete(self, root: str, path: str, name: str) -> bool:
        bucket = self.tree.get(root, {}).get(path)
        if bucket is None or name not in bucket:
            return False
        bucket.pop(name, None)
        return True

    def values(self, root: str, path: str) -> dict[str, Any]:
        return {name: row[0] for name, row in self.tree.get(root, {}).get(path, {}).items()}


class NullRegistry:
    """윈도우가 아닐 때 — 모든 조회가 '없음'을 돌려준다 (예외 없이 안전)."""

    supported = False

    def subkeys(self, root: str, path: str) -> list[str]:
        return []

    def exists(self, root: str, path: str) -> bool:
        return False

    def get(self, root: str, path: str, name: str) -> tuple[bool, Any, int]:
        return False, None, 0

    def set(self, root: str, path: str, name: str, value: Any, kind: int = 4) -> None:
        raise OSError("윈도우 레지스트리를 사용할 수 없습니다")

    def delete(self, root: str, path: str, name: str) -> bool:
        return False

    def values(self, root: str, path: str) -> dict[str, Any]:
        return {}


def registry(reg: Any = None) -> Any:
    if reg is not None:
        return reg
    if not IS_WINDOWS:
        return NullRegistry()
    return Registry()


# ---------------------------------------------------------------- 설정 계획
def plan(reg: Any = None) -> list[dict[str, Any]]:
    """적용할 값 목록 — 지금 값과 함께 돌려준다 (화면 표시·백업용)."""
    reg = registry(reg)
    rows: list[dict[str, Any]] = []
    if not getattr(reg, "supported", False):
        return rows
    for name, value, label in (("TcpAckFrequency", 1, "TCP ACK 지연 끄기"),
                               ("TCPNoDelay", 1, "Nagle 알고리즘 끄기")):
        for iface in reg.subkeys(HKLM, TCP_IFACE_KEY):
            exists, current, kind = reg.get(HKLM, f"{TCP_IFACE_KEY}\\{iface}", name)
            rows.append({"key": f"{TCP_IFACE_KEY}\\{iface}", "name": name, "value": value,
                         "kind": 4, "exists": exists, "current": current, "label": label,
                         "group": "tcp", "iface": iface})
    exists, current, _kind = reg.get(HKLM, THROTTLE_KEY, "NetworkThrottlingIndex")
    rows.append({"key": THROTTLE_KEY, "name": "NetworkThrottlingIndex", "value": THROTTLE_OFF,
                 "kind": 4, "exists": exists, "current": current,
                 "label": "네트워크 스로틀링 해제", "group": "throttle"})
    exists, current, _kind = reg.get(HKLM, IFEO_KEY, "CpuPriorityClass")
    rows.append({"key": IFEO_KEY, "name": "CpuPriorityClass", "value": GAME_PRIORITY_HIGH,
                 "kind": 4, "exists": exists, "current": current,
                 "label": "서든어택 CPU 우선순위 높음", "group": "priority"})
    for adapter in reg.subkeys(HKLM, NETCLASS_KEY):
        if not adapter.isdigit():
            continue
        path = f"{NETCLASS_KEY}\\{adapter}"
        name, _, _ = reg.get(HKLM, path, "DriverDesc")
        if not reg.exists(HKLM, path):
            continue
        exists, current, _kind = reg.get(HKLM, path, "PnPCapabilities")
        rows.append({"key": path, "name": "PnPCapabilities", "value": NIC_NO_POWER_SAVE,
                     "kind": 4, "exists": exists, "current": current,
                     "label": "랜카드 절전 끄기", "group": "nic", "adapter": str(name or adapter)})
    return rows


def state(reg: Any = None) -> dict[str, Any]:
    """카드 표시용 — 항목별 적용 여부."""
    reg = registry(reg)
    supported = bool(getattr(reg, "supported", False)) and IS_WINDOWS
    if not supported:
        return {"supported": False, "applied": False, "label": "윈도우에서만 동작합니다",
                "rows": [], "counts": {"tcp": [0, 0], "throttle": [0, 0], "priority": [0, 0],
                                       "nic": [0, 0]}}
    rows = plan(reg)
    counts: dict[str, list[int]] = {}
    for row in rows:
        slot = counts.setdefault(row["group"], [0, 0])
        slot[1] += 1
        if row["exists"] and int(row["current"] or 0) == int(row["value"]):
            slot[0] += 1
    total = sum(c[1] for c in counts.values())
    done = sum(c[0] for c in counts.values())
    return {
        "supported": True, "applied": bool(total and done == total), "done": done, "total": total,
        "counts": counts,
        "labels": {"tcp": "TCP ACK·Nagle 끄기", "throttle": "네트워크 스로틀링 해제",
                   "priority": "서든 CPU 우선순위 높음", "nic": "랜카드 절전 끄기"},
        "label": ("적용됨" if total and done == total
                  else (f"{done}/{total} 적용" if done else "적용 전")),
    }


def backup_path(path: str | os.PathLike[str] | None = None) -> Path:
    return Path(path) if path else paths.data_dir() / BACKUP_FILE


def _save_backup(rows: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"at": _now(), "values": [
        {"key": r["key"], "name": r["name"], "exists": bool(r["exists"]),
         "value": r.get("current"), "kind": int(r.get("kind") or 4), "label": r["label"]}
        for r in rows]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def apply(reg: Any = None, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """모든 항목 적용 — 적용 전 값은 백업 파일로 남긴다."""
    reg = registry(reg)
    if not getattr(reg, "supported", False) or not IS_WINDOWS:
        return {"ok": False, "unsupported": True, "simulated": True,
                "message": "네트워크 최적화는 윈도우에서만 적용됩니다 (지금은 시뮬레이션)"}
    rows = plan(reg)
    if not rows:
        return {"ok": False, "message": "적용할 네트워크 항목을 찾지 못했습니다"}
    target = backup_path(path)
    try:
        _save_backup(rows, target)
    except OSError as exc:
        return {"ok": False, "message": f"백업 파일을 쓰지 못했습니다 · {exc}"}
    changes: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in rows:
        if row["exists"] and int(row["current"] or 0) == int(row["value"]):
            continue
        try:
            reg.set(HKLM, row["key"], row["name"], row["value"], row.get("kind") or 4)
            changes.append({**{k: row[k] for k in ("key", "name", "label", "value")},
                            "before": row.get("current"), "existed": row.get("exists")})
        except PermissionError:
            errors.append(f"{row['label']} · 관리자 권한 필요")
        except OSError as exc:
            errors.append(f"{row['label']} · {exc}")
    state_after = state(reg)
    ok = bool(changes) and not errors
    if changes and errors:
        ok = True   # 일부만 적용돼도 화면에는 진행 상황을 보여 준다
    message = "⚡ 네트워크 최적화 적용 완료"
    if changes:
        message += f" · {len(changes)}개 값 변경"
    if errors:
        message += f" · {len(errors)}개 실패"
    return {"ok": ok, "changes": changes, "errors": errors, "backup": str(target),
            "state": state_after, "needs_reboot": bool(changes), "message": message}


def restore(reg: Any = None, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """백업 파일의 값으로 되돌린다 — 원래 없던 값은 지운다."""
    reg = registry(reg)
    if not getattr(reg, "supported", False) or not IS_WINDOWS:
        return {"ok": False, "unsupported": True, "simulated": True,
                "message": "네트워크 복구는 윈도우에서만 동작합니다 (지금은 시뮬레이션)"}
    target = backup_path(path)
    if not target.exists():
        return {"ok": False, "message": "복구 지점이 없습니다 · 먼저 최적화를 적용해 주세요"}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": f"백업 파일을 읽지 못했습니다 · {exc}"}
    restored, errors = 0, []
    for row in payload.get("values") or []:
        try:
            if row.get("exists"):
                reg.set(HKLM, row["key"], row["name"], row.get("value"), int(row.get("kind") or 4))
            else:
                reg.delete(HKLM, row["key"], row["name"])
            restored += 1
        except OSError as exc:
            errors.append(f"{row.get('name')} · {exc}")
    try:
        target.unlink()
    except OSError:
        pass
    return {"ok": restored > 0, "restored": restored, "errors": errors,
            "state": state(reg),
            "message": ("↩ 네트워크 설정을 원래대로 되돌렸습니다" if restored
                        else "되돌릴 값이 없었습니다")}


# ---------------------------------------------------------------- 관리자 권한
def needs_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 리눅스 등
        return False


def elevate_help(action: str = "apply") -> dict[str, Any]:
    """UAC 로 같은 실행 파일을 다시 띄워 레지스트리를 쓴다."""
    if not IS_WINDOWS:
        raise RuntimeError("관리자 권한 상승은 윈도우에서만 지원합니다")
    result_file = paths.data_dir() / RESULT_FILE
    flag = "--netopt-restore" if action == "restore" else "--netopt-apply"
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


def write_result(path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": _now(), **payload}, ensure_ascii=False, indent=2),
                 encoding="utf-8")


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
        result = restore() if action == "restore" else apply()
        result["elevated"] = True
        write_result(result_path, result)
        return 0 if result.get("ok") else 1
    except Exception as exc:  # noqa: BLE001
        write_result(result_path, {"ok": False, "elevated": True, "message": str(exc)})
        return 1
