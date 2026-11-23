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

