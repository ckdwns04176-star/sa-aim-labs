"""전역 단축키 (Windows · ctypes 만) — 오버레이 호출용.

``RegisterHotKey(hWnd=NULL, ...)`` 는 **호출한 스레드의 메시지 큐**로
``WM_HOTKEY`` 를 보낸다.  그래서 별도 스레드에서 등록하고 같은 스레드에서
``GetMessageW`` 로 받는다.  설정이 바뀌면 그 스레드를 내리고 새로 올린다
(등록/해제를 스레드 경계 너머로 넘기지 않아 안전하다).

실패(다른 프로그램이 선점)해도 앱은 그대로 동작하고, ``state()`` 가
어떤 키가 실패했는지 알려 준다.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
from typing import Any, Callable

log = logging.getLogger("hotkeys")

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

_VK = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23,
    "PGUP": 0x21, "PGDN": 0x22,
    "SPACE": 0x20, "TAB": 0x09,
}

MOD_NAMES = {"CTRL": MOD_CONTROL, "CONTROL": MOD_CONTROL,
             "ALT": MOD_ALT, "SHIFT": MOD_SHIFT}


def available() -> bool:
    return os.name == "nt"


def parse(spec: str) -> tuple[int, int] | None:
    """'F8' / 'Ctrl+F9' / 'Alt+F8' → (modifiers, virtual-key).  None 은 해제."""
    text = str(spec or "").strip().upper().replace(" ", "")
    if not text or text == "NONE":
        return None
    parts = [p for p in text.split("+") if p]
    if not parts:
        return None
    key = parts[-1]
    modifiers = 0
    for part in parts[:-1]:
        if part in MOD_NAMES:
            modifiers |= MOD_NAMES[part]
        else:
            return None
    if key in _VK:
        vk = _VK[key]
    elif len(key) == 1 and key.isalnum():
        vk = ord(key)
    else:
        return None
    return modifiers | MOD_NOREPEAT, vk


class HotkeyListener:
    """이름이 붙은 전역 단축키 여러 개를 한 스레드에서 감시한다."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._mapping: dict[int, str] = {}
        self._callback: Callable[[str], None] | None = None
        self.failed: dict[str, str] = {}
        self.registered: list[str] = []
        self._thread_id: int | None = None

    # ------------------------------------------------------------ 공개
    def start(self, mapping: dict[str, str], callback: Callable[[str], None]) -> bool:
        self.stop()
        if not available():
            return False
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._mapping = {i + 1: name for i, name in enumerate(mapping.keys())}
        self._specs = dict(mapping)
        self._callback = callback
        self.failed = {}
        self.registered = []
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sa-hotkeys")
        self._thread.start()
        self._ready.wait(2.0)
        return bool(self.registered)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and self._thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(int(self._thread_id), WM_QUIT, 0, 0)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        self._thread = None
        self._thread_id = None

    def state(self) -> dict[str, Any]:
        return {"supported": available(), "registered": list(self.registered),
                "failed": dict(self.failed), "specs": dict(getattr(self, "_specs", {}))}

    # ------------------------------------------------------------ 내부
    def _loop(self) -> None:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self._thread_id = int(ctypes.windll.kernel32.GetCurrentThreadId())  # type: ignore[attr-defined]
        ids: list[int] = []
        for hotkey_id, name in self._mapping.items():
            parsed = parse(self._specs.get(name, ""))
            if not parsed:
                self.failed[name] = "사용 안 함"
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(None, hotkey_id, mods, vk):
                ids.append(hotkey_id)
                self.registered.append(name)
            else:
                self.failed[name] = "다른 프로그램이 이 키를 쓰고 있습니다"
        self._ready.set()
        try:
            from ctypes import wintypes

            msg = wintypes.MSG()
            while not self._stop.is_set():
                got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got == 0 or got == -1:
                    break
                if msg.message == WM_HOTKEY:
                    name = self._mapping.get(int(msg.wParam))
                    if name and self._callback:
                        try:
                            self._callback(name)
                        except Exception:  # noqa: BLE001 - 콜백 오류가 감시를 죽이면 안 된다
                            log.exception("hotkey %s callback failed", name)
        finally:
            for hotkey_id in ids:
                try:
                    user32.UnregisterHotKey(None, hotkey_id)
                except Exception:  # noqa: BLE001
                    pass
            self._ready.set()
