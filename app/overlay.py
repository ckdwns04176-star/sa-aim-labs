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
# v1.0.1 — Insert · Delete 추가 (사용자 요청) + 기존 F1~F12 · Ctrl/Alt/Shift 조합
HOTKEY_KEYS = ("F6", "F7", "F8", "F9", "F10", "F11", "F12", "Insert", "Delete",
               "Home", "End", "PgUp", "PgDn")
HOTKEY_CHOICES = tuple(HOTKEY_KEYS) + (
    "Ctrl+F8", "Ctrl+F9", "Alt+F8", "Alt+F9", "Ctrl+Insert", "Ctrl+Delete",
    "Shift+Insert", "Shift+Delete", "Alt+Insert", "Alt+Delete", "None")
TOAST_POSITIONS = ("br", "tr")
TOAST_SECONDS = 5.0
MAPBAN_SECONDS = 12.0
PARTY_SECONDS = 15.0

# ---------------------------------------------------------------- 기하 (비율)
# 기준 캡처: 1844×853 (맵 밴) / 2048×945 (파티 대기실) — 게임 클라이언트 전체.
#
# v1.0.1 — 파티 대기실 좌표 재보정.
#  실제 스크린샷(2048×945)은 좌우에 검은 여백이 있어 게임 클라이언트는
#  x 183~1864 (폭 1682) 이다.  비율은 반드시 **클라이언트 영역** 기준으로 계산해야
#  하므로 아래 값은 모두 (x-183)/1682, y/945 로 환산한 값이다.
#   · 5인 카드 왼쪽 x = 250 + 320*i  · 카드 폭 280  · 닉네임(남색) 줄 y 446~473
#  예전 값(0.1113 + 0.1465*i, y 0.4233)은 2048 전체 폭·아이콘 줄 기준이라
#  오버레이가 오른쪽 120px · 위 46px 어긋나고 OCR 이 남색 닉네임이 아닌
#  그 위 아이콘 줄을 읽고 있었다.
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

# 5칸 닉네임 줄 — OCR 크롭 영역 (카드 1~5, 클랜명 줄은 제외)
PARTY_CARDS = [
    (0.039833, 0.471958, 0.166468, 0.028571),
    (0.230083, 0.471958, 0.166468, 0.028571),
    (0.420333, 0.471958, 0.166468, 0.028571),
    (0.610583, 0.471958, 0.166468, 0.028571),
    (0.800832, 0.471958, 0.166468, 0.028571),
]
# 파티 배지(칩)는 닉네임 줄 바로 아래(클랜명 줄)에 카드 폭 그대로 붙는다.
PARTY_BADGE_TOP = 0.500529   # 473/945
PARTY_BADGE_H = 0.030688     # 29/945
# 그 아래, 카드가 끝난 뒤의 빈 띠 — 좋은 맵 3 · 안 좋은 맵 3 (맵 사진 포함).
#   카드 아래(505) ~ "파티 이탈" 버튼 위(542) 사이 33px 에 맞춘다.
PARTY_MAPS_TOP = 0.538624    # 509/945
PARTY_MAPS_H = 0.033862      # 32/945

SCOUT_RATIO = (0.010, 0.205, 0.235, 0.300)   # v1.0.0 값 — 이제 우하단 계산에 쓴다
SCOUT_SIZE = (0.300, 0.360)   # 게임 클라이언트 대비 폭·높이 비율 (우하단)
SCOUT_MARGIN = (0.012, 0.020)
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
    """파티 배지 창 — 5칸 닉네임 줄 바로 아래 + 카드 밑 맵 띠.

    v1.0.1 — 예전에는 창 위쪽에 헤더(44px)를 두고 ``y - 66`` 에 띄워
    카드 중간을 덮었다.  이제 창 자체를 배지 줄(클랜명 줄) + 맵 띠에만
    맞추고, 가운데 투명 영역은 클릭 통과(WS_EX_TRANSPARENT)라 게임에 영향이 없다.
    """
    x, y, w, h = (int(v) for v in game[:4])
    left = x + int(round(PARTY_CARDS[0][0] * w))
    right = x + int(round((PARTY_CARDS[-1][0] + PARTY_CARDS[-1][2]) * w))
    top = y + int(round(PARTY_BADGE_TOP * h))
    bottom = y + int(round((PARTY_MAPS_TOP + PARTY_MAPS_H) * h))
    return {"x": left, "y": max(y, top), "w": max(260, right - left),
            "h": max(60, bottom - max(y, top))}


def party_badge_rects(game: tuple[int, int, int, int]) -> list[dict[str, int]]:
    """5개 배지 사각형 — 각 카드 닉네임 줄 바로 아래에 폭을 맞춰 붙인다."""
    rows = [(col[0], PARTY_BADGE_TOP, col[2], PARTY_BADGE_H) for col in PARTY_CARDS]
    return [rect_scale(game, row) for row in rows]


def party_maps_rect(game: tuple[int, int, int, int]) -> dict[str, int]:
    """카드 아래 맵 띠 (좋은 맵 3 · 안 좋은 맵 3) — 카드 줄 폭 전체."""
    x, y, w, h = (int(v) for v in game[:4])
    left = x + int(round(PARTY_CARDS[0][0] * w))
    right = x + int(round((PARTY_CARDS[-1][0] + PARTY_CARDS[-1][2]) * w))
    return {"x": left, "y": y + int(round(PARTY_MAPS_TOP * h)),
            "w": max(260, right - left), "h": max(28, int(round(PARTY_MAPS_H * h)))}


def party_layout(rect: dict[str, int], game: tuple[int, int, int, int]) -> dict[str, Any]:
    """창 안에서 배지·맵 띠가 놓일 위치를 %로 — 해상도가 달라도 같은 자리.

    overlay_party.html 이 이 값을 그대로 절대 위치로 쓴다(픽셀 계산 없음).
    """
    gw, gh = max(1, int(game[2])), max(1, int(game[3]))

    def pct_x(ratio: float) -> float:
        return round((game[0] + ratio * gw - rect["x"]) / max(1, rect["w"]) * 100, 3)

    def pct_y(ratio: float) -> float:
        return round((game[1] + ratio * gh - rect["y"]) / max(1, rect["h"]) * 100, 3)

    chips = [{"left": pct_x(col[0]), "width": round(col[2] * gw / max(1, rect["w"]) * 100, 3)}
             for col in PARTY_CARDS]
    return {
        "chips": chips,
        "chip_top": pct_y(PARTY_BADGE_TOP),
        "chip_height": round(PARTY_BADGE_H * gh / max(1, rect["h"]) * 100, 3),
        "maps_top": pct_y(PARTY_MAPS_TOP),
        "maps_height": round(PARTY_MAPS_H * gh / max(1, rect["h"]) * 100, 3),
    }


def scout_overlay_rect(game: tuple[int, int, int, int]) -> dict[str, int]:
    """상대 스카우팅 창 — 게임 창 **오른쪽 아래** (v1.0.1 · 예전엔 좌측 상단)."""
    x, y, w, h = (int(v) for v in game[:4])
    rw, rh = SCOUT_SIZE
    mx, my = SCOUT_MARGIN
    win_w = max(330, int(round(rw * w)))
    win_h = max(210, int(round(rh * h)))
    left = x + w - win_w - int(round(mx * w))
    top = y + h - win_h - int(round(my * h))
    return {"x": max(x, left), "y": max(y, top), "w": win_w, "h": win_h}


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
        # v1.0.1 (#1) — hidden=True 로 만들고, 스타일(NOACTIVATE)을 붙인 뒤
        # "활성화 없이" 보여 준다.  hidden=False 로 만들면 pywebview 가 곧바로
        # ShowWindow 로 포커스를 가져가 전체화면 서든어택 창이 내려간다.
        window = webview.create_window(
            TITLES[kind], url=page.as_uri(),
            js_api=self._api(),
            width=int(rect["w"]), height=int(rect["h"]), x=int(rect["x"]), y=int(rect["y"]),
            frameless=True, easy_drag=False, shadow=False, focus=False,
            on_top=True, transparent=True, background_color="#000000",
            resizable=False, text_select=False, zoomable=False, hidden=True,
        )
        with self._lock:
            self._windows[kind] = window
        self.created += 1
        self._style_and_show(kind, rect)
        return window

    def _api(self) -> Any | None:
        return self._api_ref if hasattr(self, "_api_ref") else None

    def bind_api(self, api: Any) -> None:
        """오버레이 페이지가 window.pywebview.api 로 현재 페이로드를 읽는다."""
        self._api_ref = api

    def _style_and_show(self, kind: str, rect: dict[str, int]) -> None:
        """HWND 를 찾아 NOACTIVATE 스타일 → 활성화 없는 표시 → 게임 포커스 복구.

        순서가 핵심이다.  스타일이 붙기 전에 창이 보이면 포커스를 가져가고,
        스타일이 붙은 뒤에 보이면 게임이 그대로 앞에 남는다.
        """
        game_hwnd = winutil.game_window() or {}
        previous = winutil.foreground_window()

        def _apply() -> None:
            hwnd = _find_hwnd(TITLES[kind], timeout=5.0)
            if not hwnd:
                # 스타일을 못 붙였으면 보이기라도 한다 — 안 뜨는 오버레이보다 낫다
                log.warning("overlay %s hwnd not found · falling back to window.show()", kind)
                with self._lock:
                    window = self._windows.get(kind)
                try:
                    if window is not None:
                        window.show()
                except Exception:  # noqa: BLE001
                    log.debug("overlay %s show fallback failed", kind, exc_info=True)
                return
            result = winutil.apply_overlay_style(hwnd)
            if os.name == "nt":
                try:
                    from ctypes import wintypes
                    import ctypes

                    # 크기는 그대로 두고 위치만 맞춘 뒤(포커스 없이) 보여 준다
                    ctypes.windll.user32.SetWindowPos(  # type: ignore[attr-defined]
                        ctypes.c_void_p(hwnd), ctypes.c_void_p(-1),
                        int(rect["x"]), int(rect["y"]), int(rect["w"]), int(rect["h"]),
                        winutil.SWP_NOACTIVATE | winutil.SWP_SHOWWINDOW)
                except Exception:  # noqa: BLE001
                    log.debug("overlay %s place failed", kind, exc_info=True)
            shown = winutil.show_no_activate(hwnd)
            log.info("overlay %s style · %s · show · %s", kind, result, shown)
            # 게임 창이 포커스를 잃었으면 그대로 되돌린다 (최소화 방지)
            target = (game_hwnd or {}).get("hwnd") or previous
            if target and target != hwnd:
                winutil.restore_foreground(int(target))
            # hidden → 표시 순서라 페이지 첫 렌더가 늦을 수 있다 — 한 번 더 밀어 준다
            time.sleep(0.4)
            self._push(kind)

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
            for _ in range(20):
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


_HOTKEY_RE = re.compile(
    r"^(?:(?:CTRL|ALT|SHIFT)\+)*(?:F(?:[1-9]|1[0-2])|INSERT|DELETE|HOME|END|PGUP|PGDN)$",
    re.IGNORECASE)
_HOTKEY_NAME = {"INSERT": "Insert", "DELETE": "Delete", "HOME": "Home", "END": "End",
                "PGUP": "PgUp", "PGDN": "PgDn"}


def clean_hotkey(spec: Any, default: str = "F8") -> str:
    """허용된 단축키만 통과 (F1~F12 · Insert/Delete/Home/End/PgUp/PgDn · 수식키 조합)."""
    value = hotkey_label(spec, default)
    if value == "None":
        return value
    if _HOTKEY_RE.match(value):
        parts = value.split("+")
        if len(parts) == 1:
            return _HOTKEY_NAME.get(parts[0].upper(), parts[0].upper())
        mods = [{"CTRL": "Ctrl", "ALT": "Alt", "SHIFT": "Shift"}.get(p.upper(), p) for p in parts[:-1]]
        key = _HOTKEY_NAME.get(parts[-1].upper(), parts[-1].upper())
        return "+".join(mods + [key])
    return default


def parse_seconds(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(60.0, number))
