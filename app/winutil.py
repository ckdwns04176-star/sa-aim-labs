"""Small Windows helpers: mouse acceleration, deep links, admin check, window size lock."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import urlparse

MOUSE_KEY = r"Control Panel\Mouse"
GAMEBAR_KEY = r"Software\Microsoft\GameBar"
GAME_MODE_VALUE = "AutoGameModeEnabled"
POWER_ULTIMATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"   # 최고 성능(Ultimate Performance)
POWER_HIGH = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"       # 고성능(High performance)
POWER_BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"  # 균형 조정
_GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
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


def mouse_params(enabled: bool) -> tuple[int, int, int]:
    """SPI_SETMOUSE 의 int[3] *(threshold1, threshold2, acceleration)*.

    계단현상(가속) 끄기 = (0,0,0), 켜기 = (6,10,1) — 윈도우 기본값.
    """
    return (6, 10, 1) if enabled else (0, 0, 0)


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

    # SPI_SETMOUSE 는 pvParam 이 **int[3] 배열 포인터**여야 한다. None 을 넘기면
    # Windows 가 즉시 거부해서 레지스트리만 바뀌고 재부팅 전까지 반영되지 않았다 (v1.0.0 수정).
    applied = False
    try:
        params = mouse_params(bool(enabled))
        gain = (ctypes.c_int * 3)(*params)
        applied = bool(ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
            SPI_SETMOUSE, 0, ctypes.byref(gain), SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
        ))
    except Exception:  # noqa: BLE001 - 레지스트리 값은 이미 저장됐다
        applied = False

    return {
        "ok": True,
        "enabled": bool(enabled),
        "live": applied,
        "params": mouse_params(bool(enabled)),
        "values": values,
        "message": (("마우스 가속 끄기 완료 · 포인터 정확도 향상 해제 (재부팅 불필요)"
                     if not enabled else
                     "마우스 가속을 다시 켰습니다 · 포인터 정확도 향상 복원")
                    + ("" if applied else " · 일부만 적용됨 · 로그아웃 후 다시 확인해 주세요")),
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


def copy_text(text: str) -> dict[str, Any]:
    """클립보드 복사 (v1.5.0 · 내전 팀 결과).  Windows API 만 쓴다."""
    value = str(text or "")
    if not value:
        return {"ok": False, "message": "복사할 내용이 없습니다"}
    if os.name != "nt":
        return {"ok": False, "message": "클립보드는 Windows 전용 기능입니다"}
    try:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        if not user32.OpenClipboard(None):
            return {"ok": False, "message": "클립보드를 열지 못했습니다"}
        try:
            user32.EmptyClipboard()
            size = (len(value) + 1) * ctypes.sizeof(ctypes.c_wchar)
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not handle:
                return {"ok": False, "message": "클립보드 메모리를 잡지 못했습니다"}
            locked = kernel32.GlobalLock(handle)
            ctypes.memmove(locked, ctypes.create_unicode_buffer(value), size)
            kernel32.GlobalUnlock(handle)
            user32.SetClipboardData(CF_UNICODETEXT, handle)
            return {"ok": True, "length": len(value)}
        finally:
            user32.CloseClipboard()
    except Exception as exc:  # noqa: BLE001 - 복사 실패는 치명적이지 않다
        return {"ok": False, "message": f"복사 실패 · {exc}"}


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


# ------------------------------------------------------------------ window style · drag (v1.3.1)
WS_MAXIMIZEBOX = 0x00010000
WS_THICKFRAME = 0x00040000
WS_MAXIMIZE = 0x01000000
GWL_STYLE = -16
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
VK_LBUTTON = 0x01

_DRAG_STATE: dict[str, Any] = {"thread": None, "stop": threading.Event(), "origin": None}


def fixed_style(style: int) -> int:
    """최대화·크기조절 비트를 지운 창 스타일 (순수 함수 · 테스트용).

    1200×750 고정 창에서 최대화 후 복귀가 어긋나던 문제를 원천 차단한다.
    """
    return int(style) & ~(WS_MAXIMIZEBOX | WS_THICKFRAME | WS_MAXIMIZE)


def fixed_window_style(hwnd: int) -> dict[str, Any]:
    """실제 창에 고정 스타일을 적용한다 (Windows 전용 · 실패는 조용히)."""
    if os.name != "nt" or not hwnd:
        return {"ok": False, "supported": False}
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_long(ctypes.c_void_p(hwnd), GWL_STYLE))
        target = fixed_style(style)
        if target != style:
            set_long(ctypes.c_void_p(hwnd), GWL_STYLE, target)
        return {"ok": True, "supported": True, "before": f"0x{style:08X}", "after": f"0x{target:08X}",
                "maximize": False, "resizable": bool(target & WS_THICKFRAME)}
    except Exception as exc:  # noqa: BLE001 - 스타일 고정은 최선 노력
        return {"ok": False, "supported": True, "message": str(exc)}


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):  # type: ignore[attr-defined]
            return None
        return int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ overlay windows (v1.0.0)
# 인게임 오버레이는 DLL 인젝션·후킹 없이 "독립 투명 창"으로만 만든다.
#   WS_EX_TOPMOST     항상 위
#   WS_EX_NOACTIVATE  게임 포커스를 뺏지 않음
#   WS_EX_TRANSPARENT 클릭 통과 (투표·조준에 영향 없음)
#   WS_EX_TOOLWINDOW  Alt+Tab·작업표시줄에 노출되지 않음
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000
HWND_TOPMOST = -1
SWP_SHOWWINDOW = 0x0040
SWP_NOACTIVATE = 0x0010
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

OVERLAY_EX_STYLE = (WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT |
                    WS_EX_TOOLWINDOW | WS_EX_LAYERED)


def overlay_style(style: int) -> int:
    """클릭 통과 오버레이 확장 스타일 (순수 함수 · 테스트용)."""
    return int(style) | OVERLAY_EX_STYLE


def overlay_style_keeps_focus(style: int) -> bool:
    """NOACTIVATE 가 실제로 붙었는지 (포커스 비탈취 확인 · 순수 함수)."""
    return bool(int(style) & WS_EX_NOACTIVATE)


def foreground_window() -> int:
    """현재 포커스를 가진 창 (게임).  실패하면 0."""
    if os.name != "nt":
        return 0
    try:
        import ctypes

        return int(ctypes.windll.user32.GetForegroundWindow() or 0)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return 0


def restore_foreground(hwnd: int) -> bool:
    """포커스가 오버레이로 넘어갔을 때 원래 창(게임)을 다시 앞으로 — 게임이 내려가지 않게."""
    if os.name != "nt" or not hwnd:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        if int(user32.GetForegroundWindow() or 0) == int(hwnd):
            return True
        user32.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOWNOACTIVATE)
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        return True
    except Exception:  # noqa: BLE001
        return False


def show_no_activate(hwnd: int) -> dict[str, Any]:
    """포커스를 뺏지 않고 창을 보여 준다 (SW_SHOWNOACTIVATE + TOPMOST·NOACTIVATE).

    pywebview 는 ``hidden=False`` 로 창을 만들 때 곧바로 ShowWindow 를 부르고
    포커스를 가져간다 — 그 순간 전체화면 게임이 최소화된다(사용자 제보 #1).
    그래서 창은 hidden 로 만든 뒤 이 함수로만 보이게 한다.
    """
    if os.name != "nt" or not hwnd:
        return {"ok": False, "supported": False}
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOWNOACTIVATE)
        placed = user32.SetWindowPos(
            ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        return {"ok": bool(user32.IsWindowVisible(ctypes.c_void_p(hwnd))),
                "supported": True, "topmost": bool(placed)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "supported": True, "message": str(exc)}


def hide_no_activate(hwnd: int) -> None:
    if os.name != "nt" or not hwnd:
        return
    try:
        import ctypes

        ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), SW_HIDE)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def apply_overlay_style(hwnd: int) -> dict[str, Any]:
    """투명·클릭 통과·항상 위·포커스 비탈취 창으로 만든다 (Windows 전용)."""
    if os.name != "nt" or not hwnd:
        return {"ok": False, "supported": False}
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        before = int(get_long(ctypes.c_void_p(hwnd), GWL_EXSTYLE))
        target = overlay_style(before)
        set_long(ctypes.c_void_p(hwnd), GWL_EXSTYLE, target)
        after = int(get_long(ctypes.c_void_p(hwnd), GWL_EXSTYLE))
        user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        return {"ok": True, "supported": True, "before": f"0x{before:08X}",
                "after": f"0x{after:08X}", "noactivate": overlay_style_keeps_focus(after)}
    except Exception as exc:  # noqa: BLE001 - 스타일 적용 실패는 앱을 막지 않는다
        return {"ok": False, "supported": True, "message": str(exc)}


GAME_TITLE_HINTS = ("서든어택", "sudden attack", "suddenattack")


def _client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """창의 클라이언트 영역을 화면 좌표로 (테두리 제외 — 오버레이 정렬 기준)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        rect = wintypes.RECT()
        if not user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
            return None
        point = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(ctypes.c_void_p(hwnd), ctypes.byref(point)):
            return None
        return int(point.x), int(point.y), int(rect.right - rect.left), int(rect.bottom - rect.top)
    except Exception:  # noqa: BLE001
        return None


def game_window() -> dict[str, Any] | None:
    """실행 중인 서든어택 창(클라이언트 영역)을 찾는다.  없으면 None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        found: list[dict[str, Any]] = []
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        buf = ctypes.create_unicode_buffer(256)

        def _each(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
                    return True
                if not user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 256):
                    return True
                title = str(buf.value or "")
                if not any(hint.lower() in title.lower() for hint in GAME_TITLE_HINTS):
                    return True
                area = _client_rect(int(hwnd))
                if not area or area[2] < 640 or area[3] < 360:
                    return True
                found.append({"hwnd": int(hwnd), "title": title,
                              "x": area[0], "y": area[1], "width": area[2], "height": area[3]})
            except Exception:  # noqa: BLE001 - 한 창의 오류는 건너뛴다
                return True
            return True

        user32.EnumWindows(EnumProc(_each), 0)
        return found[0] if found else None
    except Exception:  # noqa: BLE001
        return None


def screen_area() -> tuple[int, int, int, int]:
    """기본 모니터 작업 영역 (없으면 0,0,0,0)."""
    if os.name != "nt":
        return 0, 0, 0, 0
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # type: ignore[attr-defined]
            return int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top)
    except Exception:  # noqa: BLE001
        pass
    return 0, 0, 0, 0


def _cursor_pos() -> tuple[int, int] | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):  # type: ignore[attr-defined]
            return None
        return int(point.x), int(point.y)
    except Exception:  # noqa: BLE001
        return None


def _place(hwnd: int, x: int, y: int) -> bool:
    """크기는 그대로, 위치만 옮긴다 (SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.SetWindowPos(  # type: ignore[attr-defined]
            ctypes.c_void_p(hwnd), 0, int(x), int(y), 0, 0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE))
    except Exception:  # noqa: BLE001
        return False


def start_drag_loop(hwnd: int, *, max_seconds: float = 120.0) -> dict[str, Any]:
    """마우스 왼쪽 버튼을 누르고 있는 동안 창을 커서에 붙여 움직인다.

    pywebview 의 JS 브리지는 백그라운드 스레드에서 돌기 때문에 모달 이동 루프
    (``WM_NCLBUTTONDOWN``)를 쓰면 실패할 수 있다.  여기서는 커서 위치를 직접
    읽어 ``SetWindowPos`` 로만 옮기므로 스레드·DOM 이벤트와 무관하게 동작한다.
    """
    if os.name != "nt" or not hwnd:
        return {"ok": False, "supported": False}
    rect = _window_rect(hwnd)
    cursor = _cursor_pos()
    if rect is None or cursor is None:
        return {"ok": False, "supported": False, "message": "창/커서 위치를 읽지 못했습니다"}
    thread = _DRAG_STATE.get("thread")
    if thread is not None and thread.is_alive():
        _DRAG_STATE["stop"].set()
        thread.join(timeout=0.2)
    stop = threading.Event()
    _DRAG_STATE.update({"stop": stop, "origin": {"x": rect[0], "y": rect[1], "cx": cursor[0], "cy": cursor[1]},
                        "hwnd": hwnd})

    def _run() -> None:
        import time as _time

        ox, oy = rect[0], rect[1]
        cx0, cy0 = cursor[0], cursor[1]
        deadline = _time.monotonic() + max(1.0, float(max_seconds))
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return
        moved = False
        while not stop.is_set() and _time.monotonic() < deadline:
            try:
                if not (int(user32.GetAsyncKeyState(VK_LBUTTON)) & 0x8000):
                    break
            except Exception:  # noqa: BLE001
                break
            point = _cursor_pos()
            if point is None:
                break
            x = ox + (point[0] - cx0)
            y = oy + (point[1] - cy0)
            if _place(hwnd, x, y):
                moved = True
            _time.sleep(0.008)
        _DRAG_STATE["origin"] = None
        _DRAG_STATE["moved"] = moved

    thread = threading.Thread(target=_run, name="sa-window-drag", daemon=True)
    _DRAG_STATE["thread"] = thread
    thread.start()
    return {"ok": True, "supported": True, "start": [rect[0], rect[1]]}


def end_drag_loop() -> None:
    stop = _DRAG_STATE.get("stop")
    if isinstance(stop, threading.Event):
        stop.set()


def move_window_by(hwnd: int, dx: int, dy: int) -> dict[str, Any]:
    """드래그 시작 지점 기준 (dx, dy) 만큼 이동 — JS mousemove 폴백.

    CSS px 로 들어오므로 DPI 배율(96 기준)을 곱해 실제 픽셀만큼 옮긴다.
    """
    if os.name != "nt" or not hwnd:
        return {"ok": False, "supported": False}
    origin = _DRAG_STATE.get("origin")
    rect = _window_rect(hwnd)
    if rect is None:
        return {"ok": False, "supported": False}
    if not origin:
        origin = {"x": rect[0], "y": rect[1]}
        _DRAG_STATE["origin"] = origin
    try:
        import ctypes

        dpi = int(ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(hwnd))) or 96  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        dpi = 96
    scale = (dpi / 96.0) if dpi else 1.0
    x = int(origin["x"]) + int(round(float(dx) * scale))
    y = int(origin["y"]) + int(round(float(dy) * scale))
    ok = _place(hwnd, x, y)
    return {"ok": ok, "supported": True, "x": x, "y": y}


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
    크기에서 벗어나면 그 크기로 되돌린다.

    v1.3.0 — **위치는 절대 건드리지 않는다.**  예전에는 작업 영역 안으로 좌표를
    보정했는데, 드래그 중에 감시자가 마지막으로 읽은 좌표를 다시 써 넣어 창이
    커서를 따라오지 못하고 제자리에 붙어 있었다(사용자 제보).  드래그·멀티모니터
    이동은 전적으로 사용자 몫이고, 감시자는 크기만 1200×750 으로 유지한다.
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
    return {
        "resize": resize,
        "width": target_w,
        "height": target_h,
        "x": x,
        "y": y,
        "moved": False,
    }


# ------------------------------------------------------------------ perf mode (전원 · 게임 모드)
def _run(cmd: list[str], timeout: float = 12.0) -> tuple[int, str]:
    """외부 명령 실행 (shell 없이 · 짧은 타임아웃 · 실패는 조용히)."""
    try:
        proc = subprocess.run(  # noqa: S603 - 인자 고정, shell 미사용
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return int(proc.returncode), out.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)


def read_power_plan() -> dict[str, Any] | None:
    """현재 활성 전원 옵션 {guid, name}."""
    if os.name != "nt":
        return None
    code, out = _run(["powercfg", "/getactivescheme"])
    if code != 0:
        return None
    for line in out.splitlines():
        match = _GUID_RE.search(line)
        if not match:
            continue
        name = ""
        paren = re.search(r"\(([^)]*)\)", line)
        if paren:
            name = paren.group(1).strip()
        if not name:
            name = line.split(match.group(0), 1)[-1].strip(" :\t") or "전원 옵션"
        return {"guid": match.group(0).lower(), "name": name}
    return None


def list_power_plans() -> list[dict[str, str]]:
    if os.name != "nt":
        return []
    code, out = _run(["powercfg", "/list"])
    if code != 0:
        return []
    plans: list[dict[str, str]] = []
    for line in out.splitlines():
        match = _GUID_RE.search(line)
        if not match:
            continue
        paren = re.search(r"\(([^)]*)\)", line)
        plans.append({"guid": match.group(0).lower(), "name": (paren.group(1).strip() if paren else "")})
    return plans


def read_game_mode() -> dict[str, Any]:
    """윈도우 '게임 모드' 레지스트리 값 (AutoGameModeEnabled)."""
    if os.name != "nt":
        return {"supported": False, "exists": False, "enabled": None}
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, GAMEBAR_KEY) as key:
            try:
                value, _ = winreg.QueryValueEx(key, GAME_MODE_VALUE)
                return {"supported": True, "exists": True, "enabled": bool(int(value))}
            except OSError:
                return {"supported": True, "exists": False, "enabled": None}
    except OSError:
        return {"supported": True, "exists": False, "enabled": None}


def write_game_mode(enabled: bool) -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "unsupported": True}
    import winreg

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, GAMEBAR_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, GAME_MODE_VALUE, 0, winreg.REG_DWORD, 1 if enabled else 0)
        return {"ok": True, "enabled": bool(enabled)}
    except OSError as exc:
        return {"ok": False, "message": f"게임 모드 레지스트리를 쓰지 못했습니다 · {exc}"}


def restore_game_mode(backup: dict[str, Any] | None) -> dict[str, Any]:
    """적용 전 상태로 되돌린다 — 원래 값이 없었으면 키를 지운다."""
    if os.name != "nt":
        return {"ok": False, "unsupported": True}
    import winreg

    backup = backup or {}
    if not backup.get("exists"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, GAMEBAR_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, GAME_MODE_VALUE)
        except OSError:
            return {"ok": True, "restored": "absent"}   # 이미 없음 = 원래 상태
        return {"ok": True, "restored": "absent"}
    value = 1 if backup.get("enabled") else 0
    result = write_game_mode(bool(value))
    return {**result, "restored": "value"}


def perf_state() -> dict[str, Any]:
    """유틸 탭 표시용 — 현재 전원 옵션 + 게임 모드."""
    plan = read_power_plan()
    game = read_game_mode()
    return {
        "supported": os.name == "nt",
        "power": plan,
        "game_mode": game,
        "optimal": bool(plan and plan.get("guid") in (POWER_ULTIMATE, POWER_HIGH))
                   and bool(game.get("enabled")),
        "label": (plan or {}).get("name") or ("윈도우에서만 확인됩니다" if os.name != "nt" else "확인하지 못했습니다"),
    }


def apply_perf_mode() -> dict[str, Any]:
    """고성능/최고 성능 전원 옵션 + 게임 모드 원클릭 적용.

    적용 전 상태(전원 GUID·게임 모드 값)를 그대로 돌려주므로 호출한 쪽이 보관하면
    `restore_perf_mode` 로 원래대로 되돌릴 수 있다.
    """
    if os.name != "nt":
        return {"ok": False, "unsupported": True, "simulated": True,
                "message": "전원 옵션·게임 모드는 윈도우에서만 바뀝니다 (지금은 시뮬레이션)"}
    before = read_power_plan()
    game_before = read_game_mode()
    backup = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "power": before, "game_mode": game_before}

    applied_guid, applied_name, attempts = None, None, []
    plans = list_power_plans()
    guids = {p["guid"] for p in plans}
    if POWER_ULTIMATE in guids:
        code, out = _run(["powercfg", "/setactive", POWER_ULTIMATE])
        attempts.append(("ultimate", code))
        if code == 0:
            applied_guid, applied_name = POWER_ULTIMATE, "최고 성능"
    if applied_guid is None:
        # 최고 성능이 없으면 한 번 만들어 본다 (실패해도 무해)
        code, out = _run(["powercfg", "/duplicatescheme", POWER_ULTIMATE])
        attempts.append(("duplicate", code))
        match = _GUID_RE.search(out)
        if code == 0 and match:
            new_guid = match.group(0).lower()
            code2, _ = _run(["powercfg", "/setactive", new_guid])
            attempts.append(("setactive-new", code2))
            if code2 == 0:
                applied_guid, applied_name = new_guid, "최고 성능"
    if applied_guid is None:
        for target_guid, target_name in ((POWER_HIGH, "고성능"),):
            code, _ = _run(["powercfg", "/setactive", target_guid])
            attempts.append(("high", code))
            if code == 0:
                applied_guid, applied_name = target_guid, target_name
        if applied_guid is None:
            code, _ = _run(["powercfg", "/setactive", "SCHEME_MIN"])
            attempts.append(("scheme_min", code))
            if code == 0:
                applied_guid, applied_name = "SCHEME_MIN", "고성능"

    game = write_game_mode(True)
    power_after = read_power_plan()
    ok = applied_guid is not None or bool(game.get("ok"))
    if not ok:
        return {"ok": False, "backup": backup, "attempts": attempts,
                "message": "전원 옵션을 바꾸지 못했습니다 · powercfg 권한을 확인해 주세요"}
    parts = [f"전원 옵션 → {applied_name or (power_after or {}).get('name') or '고성능'}"]
    if game.get("ok"):
        parts.append("게임 모드 켜기")
    return {
        "ok": True,
        "backup": backup,
        "power_before": before,
        "power_now": power_after,
        "game_mode": game,
        "attempts": attempts,
        "message": "⚡ 적용 완료 · " + " · ".join(parts) + " (복구 지점 저장됨)",
    }


def restore_perf_mode(backup: dict[str, Any] | None) -> dict[str, Any]:
    """적용 전 전원 옵션·게임 모드로 되돌린다."""
    if os.name != "nt":
        return {"ok": False, "unsupported": True, "simulated": True,
                "message": "전원 옵션 복구는 윈도우에서만 동작합니다 (지금은 시뮬레이션)"}
    backup = backup or {}
    power = backup.get("power") or {}
    guid = str(power.get("guid") or "")
    steps, ok = [], False
    if guid and _GUID_RE.fullmatch(guid):
        code, _ = _run(["powercfg", "/setactive", guid])
        steps.append(("power", code))
        ok = code == 0
    game = restore_game_mode(backup.get("game_mode") or {})
    if game.get("ok"):
        ok = True
    now = read_power_plan()
    return {
        "ok": ok,
        "steps": steps,
        "power_now": now,
        "game_mode": read_game_mode(),
        "message": ("↩ 원래 전원 옵션·게임 모드로 되돌렸습니다" if ok
                    else "복구 지점이 없습니다 · 적용을 먼저 실행해 주세요"),
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
        self.moves = 0
        self.last: dict[str, Any] | None = None
        self._last_rect: tuple[int, int, int, int] | None = None
        self._pause_until = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- public
    def pause(self, seconds: float = 1.2) -> None:
        """드래그가 시작될 때 감시를 잠깐 멈춘다 (제목표시줄 mousedown → drag_window)."""
        self._pause_until = max(self._pause_until, time.monotonic() + max(0.0, float(seconds)))

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
            # 드래그 중(좌표가 방금 바뀐 순간)에는 크기 보정도 하지 않는다 —
            # SetWindowPos 가 이동 루프와 부딪히면 창이 커서를 놓친다.
            moving = self._last_rect is not None and (box[0], box[1]) != (self._last_rect[0], self._last_rect[1])
            self._last_rect = box
            if moving or time.monotonic() < self._pause_until:
                self.moves += 1
                self.last = {"skipped": "moving"}
                return self.last
            plan = window_fix_plan(box, (self.width, self.height), dpi, work_area_of_point(box[0] + 40, box[1] + 20))
            self.last = plan
            if plan["resize"]:
                # SWP_NOMOVE — 크기만 되돌리고 위치는 사용자가 둔 그대로 둔다.
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                SWP_NOMOVE = 0x0002
                user32.SetWindowPos(hwnd, 0, 0, 0,
                                    int(plan["width"]), int(plan["height"]),
                                    SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOMOVE)
                self.fixes += 1
            return plan
        except Exception:  # noqa: BLE001 - 감시는 최선 노력
            return None

    # -- thread
    def _loop(self) -> None:
        while not self._stop.is_set():
            self.fix_once()
            self._stop.wait(self.interval)

