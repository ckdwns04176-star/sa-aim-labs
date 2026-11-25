"""인게임 오버레이 3종 (파티 대기실 · 맵 밴 · 상대 스카우팅) + 결과 토스트.

원칙
----
* DLL 인젝션·DirectX 후킹 없음.  pywebview 의 **독립 투명 창**을 만들고
  Win32 확장 스타일(WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT |
  WS_EX_TOOLWINDOW)을 입혀 클릭 통과·항상 위·포커스 비탈취를 만족시킨다.
* 좌표는 게임 창의 **클라이언트 영역** 기준 비율(0~1)로 계산한다.  해상도가
  달라져도 같은 자리에 붙고, 설정에서 X/Y/배율 보정값을 더할 수 있다.
* 창이 하나도 없으면(리눅스·미리보기) 조용히 ``supported=False`` 를 돌려준다.

이 모듈의 기하 계산·문구 조합은 순수 함수라 Windows 없이 테스트한다.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Callable, Sequence

from . import capture, paths, winutil

log = logging.getLogger("overlay")

KINDS = ("party", "match", "toast")
TITLES = {
    "party": "SA Aim Labs · Party Scan",
    "match": "SA Aim Labs · Match Overlay",
    "toast": "SA Aim Labs · Toast",
}
PAGES = {
    "party": "overlay_party.html",
    "match": "overlay_match.html",
    "toast": "overlay_toast.html",
}

DEFAULT_HOTKEYS = {"lobby": "F8", "scout": "F9"}
HOTKEY_CHOICES = ("F6", "F7", "F8", "F9", "F10", "F11", "F12",
                  "Ctrl+F8", "Ctrl+F9", "Alt+F8", "Alt+F9", "None")
TOAST_POSITIONS = ("br", "tr")
TOAST_SECONDS = 5.0
MAPBAN_SECONDS = 12.0
PARTY_SECONDS = 15.0

# ---------------------------------------------------------------- 기하 (비율)
# 기준 캡처: 1844×853 (맵 밴) / 2048×945 (파티 대기실) — 게임 클라이언트 전체.
MAPBAN_CARDS = [
    (0.1009, 0.2462, 0.1546, 0.2942),
    (0.2657, 0.2462, 0.1546, 0.2942),
    (0.4306, 0.2462, 0.1546, 0.2942),
    (0.5954, 0.2462, 0.1546, 0.2942),
    (0.7603, 0.2462, 0.1546, 0.2942),
]
MAPBAN_HEADER_H = 0.031
MAPBAN_TAB_H = 0.038   # 카드 위쪽 탭 줄 (투명 창 안에서만 보인다)
MAPBAN_NAME_BAND = (0.0, 0.246, 0.0, 0.032)  # 카드 y 시작 기준 상대값 (아래에서 계산)
MAPBAN_NAME_TOP = 0.492   # 카드 이름 줄 (클라이언트 높이 비율)
MAPBAN_NAME_H = 0.031

PARTY_CARDS = [
    (0.1113, 0.4233, 0.1377, 0.0265),
    (0.2578, 0.4233, 0.1377, 0.0265),
    (0.4028, 0.4233, 0.1377, 0.0265),
    (0.5478, 0.4233, 0.1377, 0.0265),
    (0.6934, 0.4233, 0.1377, 0.0265),
]

SCOUT_RATIO = (0.010, 0.205, 0.235, 0.300)   # 좌측 상단 아래 슬림 패널
TOAST_SIZE = (372, 96)
TOAST_MARGIN = 18


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def rect_scale(game: tuple[int, int, int, int], ratio: Sequence[float],
               calib: dict[str, Any] | None = None) -> dict[str, int]:
    """게임 클라이언트 사각형 + 비율 → 화면 픽셀 사각형."""
    x, y, w, h = (int(v) for v in game[:4])
    cal = calib or {}
    ox = _num(cal.get("offset_x"))
    oy = _num(cal.get("offset_y"))
    scale = _num(cal.get("scale"), 1.0) or 1.0
    rx, ry, rw, rh = (float(v) for v in ratio[:4])
    left = x + int(round((rx * w + ox) * scale))
    top = y + int(round((ry * h + oy) * scale))
    width = max(1, int(round(rw * w * scale)))
    height = max(1, int(round(rh * h * scale)))
    return {"x": left, "y": top, "w": width, "h": height}


def mapban_tab_pad(game: tuple[int, int, int, int]) -> int:
    """맵 밴 창에서 탭 줄이 차지하는 높이(px) — 카드 헤더가 카드 위에 정확히 붙게."""
    h = int(game[3]) if len(game) > 3 else 1080
    return int(round(MAPBAN_TAB_H * max(1, h))) + 10


def mapban_card_rects(game: tuple[int, int, int, int],
                      calib: dict[str, Any] | None = None) -> list[dict[str, int]]:
    return [rect_scale(game, row, calib) for row in MAPBAN_CARDS]


def mapban_name_rects(game: tuple[int, int, int, int],
                      calib: dict[str, Any] | None = None) -> list[dict[str, int]]:
    rows = [(row[0], MAPBAN_NAME_TOP, row[2], MAPBAN_NAME_H) for row in MAPBAN_CARDS]
    return [rect_scale(game, row, calib) for row in rows]


def party_card_rects(game: tuple[int, int, int, int],
                     calib: dict[str, Any] | None = None) -> list[dict[str, int]]:
    return [rect_scale(game, row, calib) for row in PARTY_CARDS]


def mapban_overlay_rect(game: tuple[int, int, int, int]) -> dict[str, int]:
    """5개 카드 줄 + 탭 줄을 덮는 투명 창."""
    x, y, w, h = (int(v) for v in game[:4])
    left = x + int(round(MAPBAN_CARDS[0][0] * w))
    right = x + int(round((MAPBAN_CARDS[-1][0] + MAPBAN_CARDS[-1][2]) * w))
    top = y + int(round(MAPBAN_CARDS[0][1] * h)) - int(round(MAPBAN_TAB_H * h)) - 10
    bottom = y + int(round((MAPBAN_CARDS[0][1] + MAPBAN_CARDS[0][3]) * h)) + 12
    return {"x": left, "y": max(y, top), "w": max(200, right - left + 8),
            "h": max(120, bottom - max(y, top))}


def party_overlay_rect(game: tuple[int, int, int, int]) -> dict[str, int]:
    x, y, w, h = (int(v) for v in game[:4])
    left = x + int(round(PARTY_CARDS[0][0] * w))
    right = x + int(round((PARTY_CARDS[-1][0] + PARTY_CARDS[-1][2]) * w))
    top = y + int(round(PARTY_CARDS[0][1] * h)) - 66
    bottom = y + int(round((PARTY_CARDS[0][1] + PARTY_CARDS[0][3]) * h)) + 10
    return {"x": left, "y": max(y, top), "w": max(260, right - left + 8),
            "h": max(90, bottom - max(y, top))}


def scout_overlay_rect(game: tuple[int, int, int, int]) -> dict[str, int]:
    rx, ry, rw, rh = SCOUT_RATIO
    x, y, w, h = (int(v) for v in game[:4])
    return {"x": x + int(round(rx * w)), "y": y + int(round(ry * h)),
            "w": max(330, int(round(rw * w))), "h": max(210, int(round(rh * h)))}


def toast_rect(screen: tuple[int, int, int, int], position: str = "br",
               margin: int = TOAST_MARGIN) -> dict[str, int]:
    x, y, w, h = (int(v) for v in screen[:4])
    if w <= 0 or h <= 0:
        x, y, w, h = winutil.screen_area()
    width, height = TOAST_SIZE
    pos = position if position in TOAST_POSITIONS else "br"
    left = x + w - width - margin if pos == "br" else x + w - width - margin
    top = y + h - height - margin if pos == "br" else y + margin
    return {"x": left, "y": top, "w": width, "h": height}


def game_area() -> tuple[int, int, int, int]:
    """오버레이 기준이 되는 게임 클라이언트 (없으면 기본 모니터 작업 영역)."""
    window = winutil.game_window()
    if window:
        return window["x"], window["y"], window["width"], window["height"]
    area = winutil.screen_area()
    return area if area[2] and area[3] else (0, 0, 1920, 1080)


# ---------------------------------------------------------------- 맵 밴 문구
def verdict_rows(entries: Sequence[dict[str, Any]], *, pick_cut: float = 52.0,
                 ban_cut: float = 48.0) -> list[dict[str, Any]]:
    """맵 5개 → PICK/BAN 배지 + 게이지.  entries: [{map, win, games}]"""
    rows: list[dict[str, Any]] = []
    values: list[float] = []
    for entry in entries[:5]:
        win = entry.get("win")
        values.append(_num(win, 50.0) if win is not None else 50.0)
    ranked = sorted(range(len(values)), key=lambda i: -values[i])
    picks = set(ranked[:2])
    bans = set(ranked[-2:])
    for index, entry in enumerate(entries[:5]):
        win = entry.get("win")
        pct = int(round(_num(win, 50.0))) if win is not None else None
        tone = "neutral"
        label = ""
        if pct is not None:
            if index in picks and values[index] >= pick_cut:
                tone, label = "pick", "PICK"
            elif index in bans and values[index] <= ban_cut:
                tone, label = "ban", "BAN"
            elif index in picks:
                tone, label = "pick", "PICK"
            elif index in bans:
                tone, label = "ban", "BAN"
        rows.append({
            "map": entry.get("map") or "",
            "pct": pct,
            "tone": tone,
            "label": label,
            "games": int(entry.get("games") or 0),
            "gauge": max(4, min(100, pct if pct is not None else 50)),
        })
    return rows


def toast_lines(match: dict[str, Any]) -> dict[str, Any]:
    """경기 1건 → 토스트 문구 (승패 · 맵 · K/D · 딜량 · 연승)."""
    result = str(match.get("result") or "")
    win = result in ("승", "WIN", "win", "1")
    kills = int(_num(match.get("kills", match.get("kill"))))
    deaths = int(_num(match.get("deaths", match.get("death"))))
    dmg = int(_num(match.get("damage", match.get("dmg"))))
    kd = (kills / deaths) if deaths else float(kills)
    streak = int(_num(match.get("streak")))
    parts = [f"딜량 {dmg:,}"] if dmg else []
    parts.append(f"K/D {kd:.2f}")
    if streak >= 2:
        parts.append(f"{streak}연승 중")
    return {
        "win": win,
        "badge": "WIN" if win else "LOSE",
        "map": str(match.get("map") or "").strip() or "경기 종료",
        "title": f"{str(match.get('map') or '경기').strip()} {'승리' if win else '패배'} · {kills}K {deaths}D",
        "sub": " · ".join(parts),
        "foot": "SA Aim Labs",
    }


# ---------------------------------------------------------------- 창 관리
def _find_hwnd(title: str, timeout: float = 2.0) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        deadline = time.time() + timeout
        buf = ctypes.create_unicode_buffer(256)
        while time.time() < deadline:
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                return int(hwnd)
            time.sleep(0.05)
        return None
    except Exception:  # noqa: BLE001
        return None


class OverlayHub:
    """투명 오버레이 창 3종을 만들고 · 갱신하고 · 닫는다."""

    def __init__(self, payload_provider: Callable[[str], dict[str, Any]] | None = None,
                 evaluator: Callable[[int, str], None] | None = None):
        self._windows: dict[str, Any] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._stops: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._payload: dict[str, dict[str, Any]] = {kind: {} for kind in KINDS}
        self._payload_provider = payload_provider
        self._evaluator = evaluator
        self.last_error: str | None = None
        self.created = 0

    # ------------------------------------------------------------ 상태
    def available(self) -> bool:
        return os.name == "nt"

    def state(self) -> dict[str, Any]:
        with self._lock:
            open_kinds = sorted(self._windows.keys())
        return {"supported": self.available(), "open": open_kinds,
                "created": self.created, "error": self.last_error}

    def payload(self, kind: str) -> dict[str, Any]:
        return dict(self._payload.get(kind) or {})

    def set_payload(self, kind: str, payload: dict[str, Any]) -> None:
        self._payload[kind] = dict(payload or {})

    # ------------------------------------------------------------ 표시/종료
    def show(self, kind: str, payload: dict[str, Any], *, rect: dict[str, int],
             seconds: float | None = None) -> dict[str, Any]:
        if kind not in KINDS:
            return {"ok": False, "message": f"알 수 없는 오버레이 · {kind}"}
        if not self.available():
            return {"ok": False, "supported": False,
                    "message": "인게임 오버레이는 Windows 에서만 동작합니다"}
        self.set_payload(kind, payload)
        try:
            window = self._ensure(kind, rect)
        except Exception as exc:  # noqa: BLE001 - 오버레이 실패는 앱을 막지 않는다
            log.exception("overlay %s could not open", kind)
            self.last_error = str(exc)
            return {"ok": False, "supported": True, "message": f"오버레이 창을 열지 못했습니다 · {exc}"}
        self._place(window, rect)
        self._push(kind)
        self._cancel(kind)
        if seconds:
            timer = threading.Timer(max(1.0, float(seconds)), lambda: self.hide(kind))
            timer.daemon = True
            timer.start()
            self._timers[kind] = timer
        return {"ok": True, "supported": True, "kind": kind, "rect": rect}

    def hide(self, kind: str) -> dict[str, Any]:
        self._cancel(kind)
        with self._lock:
            window = self._windows.pop(kind, None)
        if not window:
            return {"ok": True, "kind": kind, "closed": False}
        try:
            window.destroy()
        except Exception:  # noqa: BLE001 - 이미 닫혔을 수 있다
            log.debug("overlay %s destroy failed", kind, exc_info=True)
        return {"ok": True, "kind": kind, "closed": True}

    def hide_all(self) -> None:
        for kind in list(self._windows.keys()):
            self.hide(kind)

    def toggle(self, kind: str, payload: dict[str, Any], *, rect: dict[str, int],
               seconds: float | None = None) -> dict[str, Any]:
        """단축키 재입력 = 즉시 종료 (그 외에는 새로 표시)."""
        with self._lock:
            is_open = kind in self._windows
        if is_open:
            return self.hide(kind)
        return self.show(kind, payload, rect=rect, seconds=seconds)

    def refresh(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        if payload is not None:
            self.set_payload(kind, payload)
        self._push(kind)

    def arm_disappear(self, kind: str, rect: dict[str, int], *,
                      threshold: float = 16.0, interval: float = 0.5,
                      hits: int = 2) -> None:
        """화면 지문이 바뀌면(맵 밴 창이 사라지면) 1초 안에 창을 닫는다."""
        if not self.available():
            return
        base = capture.signature((rect["x"], rect["y"], rect["w"], rect["h"]))
        if not base:
            return
        stop = threading.Event()
        self._stops[kind] = stop

        def _watch() -> None:
            misses = 0
            while not stop.wait(interval):
                with self._lock:
                    if kind not in self._windows:
                        return
                now = capture.signature((rect["x"], rect["y"], rect["w"], rect["h"]))
                if not now:
                    continue
                if capture.signature_delta(base, now) >= threshold:
                    misses += 1
                    if misses >= hits:
                        self.hide(kind)
                        return
                else:
                    misses = 0

        thread = threading.Thread(target=_watch, daemon=True, name=f"overlay-watch-{kind}")
        thread.start()

    # ------------------------------------------------------------ 내부
    def _cancel(self, kind: str) -> None:
        timer = self._timers.pop(kind, None)
        if timer:
            timer.cancel()
        stop = self._stops.pop(kind, None)
        if stop:
            stop.set()

    def _ensure(self, kind: str, rect: dict[str, int]) -> Any:
        import webview

        with self._lock:
            window = self._windows.get(kind)
        if window is not None:
            return window
        page = paths.ui_dir() / PAGES[kind]
        window = webview.create_window(
            TITLES[kind], url=page.as_uri(),
            js_api=self._api(),
            width=int(rect["w"]), height=int(rect["h"]), x=int(rect["x"]), y=int(rect["y"]),
            frameless=True, easy_drag=False, shadow=False, focus=False,
            on_top=True, transparent=True, background_color="#000000",
            resizable=False, text_select=False, zoomable=False, hidden=False,
        )
        with self._lock:
            self._windows[kind] = window
        self.created += 1
        self._style_later(kind)
        return window

    def _api(self) -> Any | None:
        return self._api_ref if hasattr(self, "_api_ref") else None

    def bind_api(self, api: Any) -> None:
        """오버레이 페이지가 window.pywebview.api 로 현재 페이로드를 읽는다."""
        self._api_ref = api

    def _style_later(self, kind: str) -> None:
        def _apply() -> None:
            hwnd = _find_hwnd(TITLES[kind], timeout=4.0)
            if hwnd:
                result = winutil.apply_overlay_style(hwnd)
                log.info("overlay %s style · %s", kind, result)

        threading.Thread(target=_apply, daemon=True, name=f"overlay-style-{kind}").start()

    def _place(self, window: Any, rect: dict[str, int]) -> None:
        try:
            window.move(int(rect["x"]), int(rect["y"]))
            window.resize(int(rect["w"]), int(rect["h"]))
        except Exception:  # noqa: BLE001 - 위치 조정 실패는 치명적이지 않다
            log.debug("overlay move/resize failed", exc_info=True)

    def _push(self, kind: str) -> None:
        with self._lock:
            window = self._windows.get(kind)
            payload = dict(self._payload.get(kind) or {})
        if window is None:
            return

        def _send() -> None:
            for _ in range(8):
                try:
                    window.evaluate_js("window.OV && window.OV.render(%s)" %
                                       _json(payload))
                    return
                except Exception:  # noqa: BLE001 - 페이지 로드 전이면 잠시 뒤 재시도
                    time.sleep(0.35)

        threading.Thread(target=_send, daemon=True, name=f"overlay-push-{kind}").start()


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def hotkey_label(spec: Any, default: str = "F8") -> str:
    text = str(spec or "").strip()
    if not text:
        return default
    if text.lower() == "none":
        return "None"
    return text.upper().replace(" ", "")


_HOTKEY_RE = re.compile(r"^(?:(?:CTRL|ALT|SHIFT)\+)?F(?:[1-9]|1[0-2])$", re.IGNORECASE)


def clean_hotkey(spec: Any, default: str = "F8") -> str:
    """허용된 단축키만 통과 (F1~F12 · Ctrl/Alt/Shift 조합 · None).  그 외는 기본값."""
    value = hotkey_label(spec, default)
    if value == "None":
        return value
    if _HOTKEY_RE.match(value):
        parts = value.split("+")
        if len(parts) == 1:
            return parts[0].upper()
        mods = [{"CTRL": "Ctrl", "ALT": "Alt", "SHIFT": "Shift"}.get(p.upper(), p) for p in parts[:-1]]
        return "+".join(mods + [parts[-1].upper()])
    return default


def parse_seconds(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(60.0, number))
