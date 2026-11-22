"""Small Windows helpers: mouse acceleration, deep links, admin check."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse

MOUSE_KEY = r"Control Panel\Mouse"
SPI_SETMOUSE = 0x0004
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02
ALLOWED_SCHEMES = {"steam", "aimlab", "https", "http"}


# ------------------------------------------------------------------ mouse accel
def mouse_values(enabled: bool) -> dict[str, str]:
    """Registry values that toggle '포인터 정확도 향상'."""
    if enabled:
        return {"MouseSpeed": "1", "MouseThreshold1": "6", "MouseThreshold2": "10"}
    return {"MouseSpeed": "0", "MouseThreshold1": "0", "MouseThreshold2": "0"}


def read_mouse_values() -> dict[str, str] | None:
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, MOUSE_KEY) as key:
            out = {}
            for name in ("MouseSpeed", "MouseThreshold1", "MouseThreshold2"):
                try:
                    out[name], _ = winreg.QueryValueEx(key, name)
                except OSError:
                    out[name] = "0"
            return {k: str(v) for k, v in out.items()}
    except OSError:
        return None


def accel_enabled_from(values: dict[str, str] | None) -> bool | None:
    if values is None:
        return None
    try:
        return int(values.get("MouseSpeed", "0")) != 0
    except ValueError:
        return values.get("MouseSpeed") not in ("0", "")


def get_mouse_accel() -> bool | None:
    return accel_enabled_from(read_mouse_values())


def set_mouse_accel(enabled: bool) -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "unsupported": True, "enabled": None,
                "message": "마우스 가속 설정은 윈도우에서만 바뀝니다"}
    import ctypes
    import winreg

    values = mouse_values(bool(enabled))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, MOUSE_KEY, 0, winreg.KEY_SET_VALUE) as key:
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    except OSError as exc:
        return {"ok": False, "enabled": get_mouse_accel(), "message": f"레지스트리를 쓰지 못했습니다 · {exc}"}

    applied = True
    try:
        ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
            SPI_SETMOUSE, 0, None, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
        )
    except Exception:  # noqa: BLE001 - values are already in the registry
        applied = False

    return {
        "ok": True,
        "enabled": bool(enabled),
        "live": applied,
        "values": values,
        "message": ("마우스 가속 끄기 완료 · 포인터 정확도 향상 해제 (재부팅 불필요)"
                    if not enabled else
                    "마우스 가속을 다시 켰습니다 · 포인터 정확도 향상 복원"),
    }


# ------------------------------------------------------------------ deep links
def normalize_target(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("실행할 주소가 비어 있습니다")
    scheme = urlparse(url).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"허용되지 않은 실행 주소입니다: {scheme or url[:32]}")
    return url


def kovak_scenario_url(name: str) -> str:
    """Steam one-click jump used by the sheet (no redirect, so Chrome cannot block it)."""
    if not name:
        return "steam://run/824270"
    return f"steam://run/824270/?action=jump-to-scenario;name={name}"


def aimlab_url(name: str = "") -> str:
    """Aim Lab deep link. Falls back to the app's Steam entry when no scenario is given."""
    if not name:
        return "steam://run/714010"
    return f"aimlab://scenario/{name}"


def open_target(url: str) -> dict[str, Any]:
    target = normalize_target(url)
    scheme = urlparse(target).scheme.lower()
    try:
        if os.name == "nt" and scheme in {"steam", "aimlab"}:
            os.startfile(target)  # type: ignore[attr-defined]
            return {"ok": True, "method": "startfile", "url": target}
        if os.name == "nt" and scheme in {"http", "https"}:
            os.startfile(target)  # type: ignore[attr-defined]
            return {"ok": True, "method": "startfile", "url": target}
        import webbrowser

        opened = webbrowser.open(target)
        return {"ok": bool(opened), "method": "browser", "url": target}
    except OSError as exc:
        # No handler registered for steam:// — tell the user instead of failing silently.
        return {"ok": False, "method": None, "url": target,
                "message": f"실행 앱을 찾지 못했습니다 · Steam 설치를 확인해 주세요 ({exc})"}


def steam_installed() -> bool:
    if os.name != "nt":
        return False
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Steam\steam.exe"),
        os.path.expandvars(r"%ProgramFiles%\Steam\steam.exe"),
    ]
    return any(os.path.exists(p) for p in candidates) or bool(shutil.which("steam"))


def is_admin() -> bool:
    if os.name != "nt":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return False


def open_folder(path: str) -> dict[str, Any]:
    """Open a folder in Explorer (used by '기록 폴더 열기')."""
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return {"ok": True, "path": path}
    except OSError as exc:
        return {"ok": False, "path": path, "message": str(exc)}
