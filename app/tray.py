"""시스템 트레이 아이콘 + 전역 단축키 (Windows 전용 · ctypes 만 사용).

추가 패키지 없이 Win32 API 만으로 다음을 한다.

  · `Shell_NotifyIconW` 로 트레이 아이콘 등록 (assets/app.ico)
  · 왼쪽 클릭 → 창 보이기/숨기기 토글
  · 오른쪽 클릭 → 메뉴(`열기` / `설정` / `종료`)
  · `RegisterHotKey(Ctrl+Shift+A)` → 창 보이기/숨기기

메시지 루프는 별도 데몬 스레드에서 돌고, 콜백은 그 스레드에서 불린다.
pywebview 의 `window.show()/hide()` 는 WinForms `Invoke` 로 메인 스레드에
넘어가므로 스레드 경계를 넘어도 안전하다.  실패하면 `start()` 가 False 를
돌려주고 앱은 트레이 없이 그대로 동작한다(절대 앱을 막지 않는다).
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from typing import Any, Callable

log = logging.getLogger("tray")

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_HOTKEY = 0x0312

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
IDI_APPLICATION = 32512

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800

WS_POPUP = 0x80000000
CW_USEDEFAULT = 0x80000000

HOTKEY_ID = 0xA1A1
TRAY_CALLBACK = 0x0400 + 20  # WM_APP + 20 — 트레이 아이콘 콜백 메시지

_VK = {"A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45, "F": 0x46, "G": 0x47,
       "H": 0x48, "I": 0x49, "J": 0x4A, "K": 0x4B, "L": 0x4C, "M": 0x4D, "N": 0x4E,
       "O": 0x4F, "P": 0x50, "Q": 0x51, "R": 0x52, "S": 0x53, "T": 0x54, "U": 0x55,
       "V": 0x56, "W": 0x57, "X": 0x58, "Y": 0x59, "Z": 0x5A,
       "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
       "SPACE": 0x20}


def available() -> bool:
    return os.name == "nt"


def _configure(user32, kernel32, shell32) -> None:
    """64비트 핸들 손상 방지 — ctypes 기본 restype(c_int)은 HMENU/HWND 를 잘라 먹는다.

    v1.5.0 (#18) · 우클릭 메뉴가 안 뜨던 원인: `CreatePopupMenu()` 가 돌려주는
    HMENU 는 64비트인데 ctypes 는 기본으로 32비트 int 로 받아 핸들이 잘렸고,
    그래서 `TrackPopupMenu(menu, …, hwnd, …)` 가 실패해 메뉴가 뜨지 않았다.
    """
    from ctypes import wintypes

    try:
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.CreatePopupMenu.argtypes = []
        user32.AppendMenuW.restype = wintypes.BOOL
        user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        user32.TrackPopupMenu.restype = wintypes.BOOL
        user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
        user32.DestroyMenu.restype = wintypes.BOOL
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.BringWindowToTop.restype = wintypes.BOOL
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        # v1.0.0 — 64비트 프로토타입 누락 보강 (없으면 핸들이 잘리거나 기본 int 로 반환돼
        # 우클릭 메뉴·메시지 루프가 조용히 실패했다)
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.RegisterClassW.argtypes = [ctypes.c_void_p]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.LoadIconW.restype = wintypes.HICON
        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.argtypes = []
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.AttachThreadInput.restype = wintypes.BOOL
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        user32.AllowSetForegroundWindow.restype = wintypes.BOOL
        user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        user32.SetFocus.restype = wintypes.HWND
        user32.SetFocus.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        kernel32.GetCurrentThreadId.argtypes = []
    except Exception:  # noqa: BLE001 - 구형 파이썬/윈도우에서도 트레이는 시도한다
        log.debug("tray prototypes not configured", exc_info=True)


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """`Ctrl+Shift+A` → (MOD_*, VK).  해석할 수 없으면 None."""
    if not spec:
        return None
    mods = 0
    key = None
    for part in str(spec).replace(" ", "").split("+"):
        token = part.strip().upper()
        if not token:
            continue
        if token in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif token == "SHIFT":
            mods |= MOD_SHIFT
        elif token == "ALT":
            mods |= MOD_ALT
        elif token in ("WIN", "SUPER", "META"):
            mods |= MOD_WIN
        elif token in _VK:
            key = _VK[token]
        elif len(token) == 1 and token.isalnum():
            key = ord(token)
        else:
            return None
    if key is None or not mods:
        return None
    return mods | MOD_NOREPEAT, key


class TrayIcon:
    """트레이 아이콘 + 단축키.  `start()` 실패는 조용히 무시된다."""

    def __init__(self, title: str = "SA AIM LABS", icon_path: str | None = None,
                 on_toggle: Callable[[], None] | None = None,
                 on_quit: Callable[[], None] | None = None,
                 on_settings: Callable[[], None] | None = None,
                 on_open: Callable[[], None] | None = None,
                 hotkey: str = "Ctrl+Shift+A",
                 tooltip: str = "SA AIM LABS · 클릭하면 창이 나타납니다") -> None:
        self.title = title
        self.icon_path = icon_path
        self.on_toggle = on_toggle
        self.on_quit = on_quit
        self.on_settings = on_settings or on_toggle
        self.on_open = on_open or on_toggle
        self.hotkey = hotkey
        self.tooltip = tooltip[:120]

        self._hwnd: int | None = None
        self._hicon: int | None = None
        self._thread: threading.Thread | None = None
        self._wndproc_ref: Any = None
        self._error: str | None = None
        self._hotkey_on = False
        self._icon_added = False
        self._ready = threading.Event()

    # ---------------------------------------------------------------- public
    @property
    def active(self) -> bool:
        return bool(self._hwnd) and self._thread is not None and self._thread.is_alive()

    @property
    def icon_added(self) -> bool:
        return self._icon_added

    @property
    def error(self) -> str | None:
        return self._error

    def start(self) -> bool:
        if not available():
            self._error = "Windows 전용 기능입니다"
            return False
        if self.active:
            return True
        self._thread = threading.Thread(target=self._run, name="sa-tray", daemon=True)
        self._thread.start()
        if self._ready.wait(3.0):
            return self.active
        return self.active

    def stop(self) -> None:
        hwnd = self._hwnd
        try:
            if hwnd:
                ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), WM_CLOSE, 0, 0)
        except Exception:  # noqa: BLE001
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._hwnd = None

    def set_hotkey(self, enabled: bool) -> bool:
        """단축키 ON/OFF (실행 중에도 즉시 반영)."""
        if not self._hwnd or not available():
            return False
        try:
            user32 = ctypes.windll.user32
            if enabled and not self._hotkey_on:
                parsed = parse_hotkey(self.hotkey)
                if not parsed:
                    return False
                if not user32.RegisterHotKey(ctypes.c_void_p(self._hwnd), HOTKEY_ID, parsed[0], parsed[1]):
                    self._error = f"{self.hotkey} 단축키를 다른 프로그램이 쓰고 있습니다"
                    return False
                self._hotkey_on = True
            elif not enabled and self._hotkey_on:
                user32.UnregisterHotKey(ctypes.c_void_p(self._hwnd), HOTKEY_ID)
                self._hotkey_on = False
            return True
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            return False

    def notify(self, title: str, message: str, level: str = "info") -> None:
        """풍선 알림(NIF_INFO).  level: info / warning / error.  실패해도 무시."""
        if not self._hwnd or not available():
            return
        flags = {"warning": 0x2, "error": 0x3}.get(str(level or "info"), 0x1)
        try:
            data = self._nid()
            data.uFlags = NIF_INFO
            data.szInfo = str(message)[:250]
            data.szInfoTitle = str(title)[:60]
            data.dwInfoFlags = flags  # NIIF_INFO / NIIF_WARNING / NIIF_ERROR
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- internals
    def _nid(self):
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", GUID),
                ("hBalloonIcon", wintypes.HICON),
            ]

        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = ctypes.c_void_p(self._hwnd or 6)
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = TRAY_CALLBACK
        data.hIcon = ctypes.c_void_p(self._hicon or 0)
        data.szTip = self.tooltip
        return data

    def _toggle(self) -> None:
        try:
            if self.on_toggle:
                self.on_toggle()
        except Exception:  # noqa: BLE001 - 콜백 오류가 메시지 루프를 죽이면 안 된다
            log.exception("tray toggle callback failed")

    def _quit(self) -> None:
        try:
            if self.on_quit:
                self.on_quit()
        except Exception:  # noqa: BLE001
            log.exception("tray quit callback failed")

    def _menu(self) -> None:
        """우클릭 컨텍스트 메뉴 — 열기 / 설정 / 종료.

        v1.0.0 — 0×0 비표시 창은 SetForegroundWindow 가 거의 항상 실패해서
        TrackPopupMenu 가 즉시 닫혔다.  포그라운드 스레드와 입력 큐를 잠시
        AttachThreadInput 으로 연결하고 AllowSetForegroundWindow 로 허용한 뒤
        메뉴를 띄우고, 끝나면 반드시 분리한다.
        """
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = int(self._hwnd or 0)
        menu = 0
        attached: tuple[int, int] | None = None
        try:
            _configure(user32, kernel32, ctypes.windll.shell32)
            menu = user32.CreatePopupMenu()
            if not menu:
                self._error = "팝업 메뉴를 만들지 못했습니다"
                return
            user32.AppendMenuW(menu, MF_STRING, 1, "열기")
            user32.AppendMenuW(menu, MF_STRING, 2, "설정")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, 3, "종료")
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            attached = self._attach_foreground(user32, kernel32, hwnd)
            flags = TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY
            cmd = user32.TrackPopupMenu(menu, flags, int(point.x), int(point.y), 0,
                                        ctypes.c_void_p(hwnd), None)
            if hwnd:
                user32.PostMessageW(ctypes.c_void_p(hwnd), WM_NULL, 0, 0)
            log.info("tray menu · x=%s y=%s cmd=%s attached=%s", point.x, point.y, cmd, bool(attached))
            if cmd == 1:
                self._open()
            elif cmd == 2:
                self._settings()
            elif cmd == 3:
                self._quit()
        except Exception:  # noqa: BLE001
            log.exception("tray menu failed")
        finally:
            if attached:
                try:
                    user32.AttachThreadInput(ctypes.c_uint(attached[0]), ctypes.c_uint(attached[1]), False)
                except Exception:  # noqa: BLE001
                    pass
            if menu:
                try:
                    user32.DestroyMenu(ctypes.c_void_p(menu))
                except Exception:  # noqa: BLE001
                    pass

    def _attach_foreground(self, user32: Any, kernel32: Any, hwnd: int) -> tuple[int, int] | None:
        """메뉴를 띄우기 전에 우리 스레드를 포그라운드 입력 큐에 붙인다."""
        if not hwnd:
            return None
        attached: tuple[int, int] | None = None
        try:
            foreground = user32.GetForegroundWindow()
            target_thread = int(user32.GetWindowThreadProcessId(foreground, None) or 0) if foreground else 0
            self_thread = int(kernel32.GetCurrentThreadId())
            try:
                user32.AllowSetForegroundWindow(ctypes.c_uint(0xFFFFFFFF))  # ASFW_ANY
            except Exception:  # noqa: BLE001
                pass
            if target_thread and target_thread != self_thread:
                if user32.AttachThreadInput(ctypes.c_uint(target_thread), ctypes.c_uint(self_thread), True):
                    attached = (target_thread, self_thread)
            ok = bool(user32.SetForegroundWindow(ctypes.c_void_p(hwnd)))
            user32.BringWindowToTop(ctypes.c_void_p(hwnd))
            user32.SetFocus(ctypes.c_void_p(hwnd))
            if not ok:
                self._error = "메뉴 창을 포그라운드로 올리지 못했습니다 (메뉴는 계속 시도)"
                log.debug("SetForegroundWindow failed for tray menu")
        except Exception:  # noqa: BLE001 - 포커스 실패가 메뉴를 막지는 않는다
            log.debug("tray foreground attach failed", exc_info=True)
        return attached

    def _open(self) -> None:
        try:
            if self.on_open:
                self.on_open()
        except Exception:  # noqa: BLE001
            log.exception("tray open callback failed")

    def _settings(self) -> None:
        try:
            if self.on_settings:
                self.on_settings()
        except Exception:  # noqa: BLE001
            log.exception("tray settings callback failed")

    def _wndproc(self, hwnd, msg, wparam, lparam):
        from ctypes import wintypes

        try:
            if msg == TRAY_CALLBACK:
                event = int(lparam) & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._toggle()
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._menu()
                return 0
            if msg == WM_HOTKEY and int(wparam) == HOTKEY_ID:
                self._toggle()
                return 0
            if msg == WM_CLOSE:
                ctypes.windll.user32.DestroyWindow(ctypes.c_void_p(hwnd))
                return 0
            if msg == WM_DESTROY:
                self._remove_icon()
                ctypes.windll.user32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001
            log.exception("tray wndproc error")
        return ctypes.windll.user32.DefWindowProcW(ctypes.c_void_p(hwnd), msg, wparam, lparam)

    def _remove_icon(self) -> None:
        if not self._icon_added:
            return
        try:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid()))
        except Exception:  # noqa: BLE001
            pass
        self._icon_added = False

    def _load_icon(self) -> int | None:
        try:
            user32 = ctypes.windll.user32
            if self.icon_path and os.path.exists(self.icon_path):
                handle = user32.LoadImageW(None, str(self.icon_path), IMAGE_ICON, 0, 0,
                                           LR_LOADFROMFILE | LR_DEFAULTSIZE)
                if handle:
                    return int(handle)
        except Exception:  # noqa: BLE001
            pass
        try:
            return int(ctypes.windll.user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION)))
        except Exception:  # noqa: BLE001
            return None

    def _run(self) -> None:
        from ctypes import wintypes

        try:
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            _configure(user32, kernel32, shell32)

            WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            self._wndproc_ref = WNDPROC(self._wndproc)   # 참조 유지 필수
            hinst = kernel32.GetModuleHandleW(None)
            class_name = f"SA_AIM_LABS_TRAY_{os.getpid()}"
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = hinst
            wc.lpszClassName = class_name
            atom = user32.RegisterClassW(ctypes.byref(wc))
            if not atom:
                # 이미 등록된 클래스(재시작) — 그대로 진행
                log.debug("RegisterClassW returned 0 (already registered?)")

            hwnd = user32.CreateWindowExW(0, class_name, self.title, WS_POPUP,
                                          CW_USEDEFAULT, CW_USEDEFAULT, 0, 0,
                                          None, None, hinst, None)
            if not hwnd:
                self._error = "트레이 창을 만들지 못했습니다"
                self._ready.set()
                return
            self._hwnd = int(hwnd)

            self._hicon = self._load_icon()
            try:
                if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid())):
                    self._icon_added = True
                else:
                    self._error = "트레이 아이콘을 등록하지 못했습니다"
            except Exception as exc:  # noqa: BLE001
                self._error = f"트레이 아이콘 등록 실패 · {exc}"

            parsed = parse_hotkey(self.hotkey)
            if parsed:
                try:
                    if user32.RegisterHotKey(ctypes.c_void_p(self._hwnd), HOTKEY_ID, parsed[0], parsed[1]):
                        self._hotkey_on = True
                    else:
                        self._error = f"{self.hotkey} 단축키를 다른 프로그램이 쓰고 있습니다"
                except Exception as exc:  # noqa: BLE001
                    self._error = f"단축키 등록 실패 · {exc}"

            self._ready.set()
            log.info("tray started · icon=%s hotkey=%s", self._icon_added, self._hotkey_on)

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:  # noqa: BLE001 - 트레이는 앱을 막지 않는다
            self._error = str(exc)
            log.warning("tray unavailable · %s", exc)
        finally:
            self._hwnd = None
            self._hotkey_on = False
            self._ready.set()


def status(tray: TrayIcon | None) -> dict[str, Any]:
    if tray is None:
        return {"supported": available(), "active": False, "icon": False, "hotkey": False,
                "hotkey_spec": "Ctrl+Shift+A", "error": None}
    return {"supported": available(), "active": tray.active, "icon": tray.icon_added,
            "hotkey": tray._hotkey_on, "hotkey_spec": tray.hotkey, "error": tray.error}
