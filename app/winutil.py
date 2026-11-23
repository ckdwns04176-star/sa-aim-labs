"""Small Windows helpers: mouse acceleration, deep links, admin check, window size lock."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import urlparse

MOUSE_KEY = r"Control Panel\Mouse"
SPI_SETMOUSE = 0x0004
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02
ALLOWED_SCHEMES = {"steam", "aimlab", "https", "http"}

# 창 고정 크기 (논리 px · 96 DPI 기준)
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 750


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


# ------------------------------------------------------------------ window size lock
def work_area_of_point(x: int, y: int) -> tuple[int, int, int, int]:
    """Work area (taskbar excluded) of the monitor that contains the point."""
    if os.name != "nt":
        return 0, 0, 0, 0
    try:
        import ctypes
        from ctypes import wintypes

        MONITOR_DEFAULTTONEAREST = 2
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hmon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
        if not hmon:
            return 0, 0, 0, 0
        info = wintypes.MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return 0, 0, 0, 0
        r = info.rcWork
        return int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)
    except Exception:  # noqa: BLE001
        return 0, 0, 0, 0


def window_fix_plan(rect: tuple[int, int, int, int], logical: tuple[int, int] = (WINDOW_WIDTH, WINDOW_HEIGHT),
                    dpi: int = 96, work: tuple[int, int, int, int] | None = None,
                    tolerance: int = 1) -> dict[str, Any]:
    """창 사각형 → '고정 크기로 되돌릴 값' (순수 함수 · 테스트용).

    서든어택을 4:3 저해상도로 실행했다가 바탕화면으로 돌아오면 DPI·해상도가
    바뀌면서 창이 커지는 현상이 있었다.  논리 1200×750 을 실제 DPI 로 환산한
    크기에서 벗어나면 그 크기로 되돌리고, 모니터 작업 영역 밖으로 나가지 않게
    좌표를 보정한 값을 돌려준다.
    """
    x, y, w, h = (int(v) for v in rect)
    scale = (float(dpi) / 96.0) if dpi else 1.0
    target_w = int(round(logical[0] * scale))
    target_h = int(round(logical[1] * scale))
    if work:
        left, top, area_w, area_h = work
        if area_w > 200 and area_h > 200:
            target_w = min(target_w, area_w)
            target_h = min(target_h, area_h)
    resize = abs(w - target_w) > tolerance or abs(h - target_h) > tolerance
    nx, ny = x, y
    if work:
        left, top, area_w, area_h = work
        if area_w > 200 and area_h > 200:
            nx = max(left, min(x, left + area_w - target_w))
            ny = max(top, min(y, top + area_h - target_h))
    return {
        "resize": resize,
        "width": target_w,
        "height": target_h,
        "x": nx,
        "y": ny,
        "moved": (nx, ny) != (x, y),
    }


class WindowGuard:
    """1200×750 고정 · 화면 밖 이탈 방지 감시자.

    해상도·DPI 가 바뀔 때(서든 저해상도 → 바탕화면 복귀) 창이 커지는 문제를
    막기 위해 짧은 간격으로 창 사각형을 읽어, 고정 크기를 벗어나면
    SetWindowPos 로 바로 되돌린다.  최소화·최대화 상태는 건드리지 않는다.
    """

    def __init__(self, title: str, width: int = WINDOW_WIDTH, height: int = WINDOW_HEIGHT,
                 interval: float = 0.3) -> None:
        self.title = title
        self.width = int(width)
        self.height = int(height)
        self.interval = float(interval)
        self.fixes = 0
        self.last: dict[str, Any] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- public
    def start(self) -> bool:
        if os.name != "nt":
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._loop, name="sa-window-guard", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def fix_once(self) -> dict[str, Any] | None:
        """한 번 점검하고, 필요하면 크기를 되돌린다 (테스트·진단용)."""
        if os.name != "nt":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hwnd = user32.FindWindowW(None, self.title)
            if not hwnd:
                return None
            if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
                self.last = {"skipped": "minimized-or-maximized"}
                return self.last
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            box = (int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top))
            dpi = 96
            try:
                dpi = int(user32.GetDpiForWindow(hwnd)) or 96
            except Exception:  # noqa: BLE001 - older Windows
                dpi = 96
            plan = window_fix_plan(box, (self.width, self.height), dpi, work_area_of_point(box[0] + 40, box[1] + 20))
            self.last = plan
            if plan["resize"] or plan["moved"]:
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                user32.SetWindowPos(hwnd, 0, int(plan["x"]), int(plan["y"]),
                                    int(plan["width"]), int(plan["height"]),
                                    SWP_NOZORDER | SWP_NOACTIVATE)
                self.fixes += 1
            return plan
        except Exception:  # noqa: BLE001 - 감시는 최선 노력
            return None

    # -- thread
    def _loop(self) -> None:
        while not self._stop.is_set():
            self.fix_once()
            self._stop.wait(self.interval)

