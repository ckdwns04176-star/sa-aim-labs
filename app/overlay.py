"""인게임 오버레이 3종 (파티 대기실 · 맵 밴 · 상대 스카우팅) + 결과 토스트.

v2.0.0 — **Win32 순정 레이어드 창**으로 교체
--------------------------------------------
pywebview(WebView2) 창은 브라우저 프로세스라 독점 전체화면 서든어택 위에서
포커스를 가져가거나 뒤로 밀렸다.  이제 실제 그리기는
:mod:`app.native_overlay` 가 ``WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE |
WS_EX_TRANSPARENT`` 창에 GDI+ 로 직접 하고, 이 모듈은

1. 게임 클라이언트 사각형(비율) 계산,
2. 화면 판별(맵 밴 / 대기실),
3. 표시 시간·재입력 종료·'화면이 바뀌면 닫기' 감시

를 맡는다.  좌표·문구 함수는 순수 함수라 Windows 없이 테스트한다.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Callable, Sequence

from . import capture, native_overlay, paths, winutil

log = logging.getLogger("overlay")

KINDS = ("party", "match", "toast")
TITLES = {
    "party": "SA Aim Labs · Party Scan",
    "match": "SA Aim Labs · Match Overlay",
    "toast": "SA Aim Labs · Toast",
}
BACKEND = "win32-layered"

DEFAULT_HOTKEYS = {"lobby": "Insert", "scout": "Home"}
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
MAPBAN_CARDS = [
    (0.1009, 0.2462, 0.1546, 0.2942),
    (0.2657, 0.2462, 0.1546, 0.2942),
    (0.4306, 0.2462, 0.1546, 0.2942),
    (0.5954, 0.2462, 0.1546, 0.2942),
    (0.7603, 0.2462, 0.1546, 0.2942),
]
MAPBAN_HEADER_H = 0.031
MAPBAN_TAB_H = 0.038
MAPBAN_NAME_TOP = 0.492
MAPBAN_NAME_H = 0.031

# 카드의 가로 위치·폭 기준값. OCR 세로 탐색띠는 party_card_rects()에서 별도 계산한다.
PARTY_CARDS = [
    (0.039833, 0.471958, 0.166468, 0.028571),
    (0.230083, 0.471958, 0.166468, 0.028571),
    (0.420333, 0.471958, 0.166468, 0.028571),
    (0.610583, 0.471958, 0.166468, 0.028571),
    (0.800832, 0.471958, 0.166468, 0.028571),
]
PARTY_BADGE_TOP = 0.500529
PARTY_BADGE_H = 0.030688
PARTY_MAPS_TOP = 0.538624
PARTY_MAPS_H = 0.033862

SCOUT_SIZE = (0.300, 0.360)
SCOUT_MARGIN = (0.012, 0.020)
TOAST_SIZE = (372, 96)
TOAST_MARGIN = 18

# 16:9 · 4:3 공통 — 창은 게임 **클라이언트 비율**로만 계산하므로 해상도가
# 바뀌어도 같은 자리에 붙는다.  아래 RATIO_TOLERANCE 는 테스트·진단용.
RATIO_TOLERANCE = 0.02


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


def aspect_of(game: tuple[int, int, int, int]) -> float:
    w = max(1, int(game[2]))
    h = max(1, int(game[3]))
    return w / h


def aspect_label(game: tuple[int, int, int, int]) -> str:
    ratio = aspect_of(game)
    if abs(ratio - 16 / 9) <= RATIO_TOLERANCE * 2:
        return "16:9"
    if abs(ratio - 4 / 3) <= RATIO_TOLERANCE * 2:
        return "4:3"
    if abs(ratio - 16 / 10) <= RATIO_TOLERANCE * 2:
        return "16:10"
    return f"{ratio:.2f}:1"


def mapban_tab_pad(game: tuple[int, int, int, int]) -> int:
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
    """5개 카드의 닉네임 탐색 띠.

    구형 대기실(참고 영상)과 현재 인게임 대기실은 닉네임 줄의 높이가
    서로 다르다. 두 UI를 모두 포함하도록 세로 띠를 넓히고,
    capture.ocr_regions(cyan_only=True)가 청록색 닉네임만 분리해 읽는다.
    """
    name_top, name_height = 0.440, 0.105
    # 닉네임은 카드 중앙에 정렬되므로 중앙 60%만 읽어 측면 배지/아이콘을 배제한다.
    rows = [(row[0] + row[2] * 0.20, name_top, row[2] * 0.60, name_height)
            for row in PARTY_CARDS]
    return [rect_scale(game, row, calib) for row in rows]


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
    """파티 배지 창 — 5칸 닉네임 줄 바로 아래 + 카드 밑 맵 띠."""
    x, y, w, h = (int(v) for v in game[:4])
    left = x + int(round(PARTY_CARDS[0][0] * w))
    right = x + int(round((PARTY_CARDS[-1][0] + PARTY_CARDS[-1][2]) * w))
    top = y + int(round(PARTY_BADGE_TOP * h))
    bottom = y + int(round((PARTY_MAPS_TOP + PARTY_MAPS_H) * h))
    return {"x": left, "y": max(y, top), "w": max(260, right - left),
            "h": max(60, bottom - max(y, top))}


def party_badge_rects(game: tuple[int, int, int, int]) -> list[dict[str, int]]:
    rows = [(col[0], PARTY_BADGE_TOP, col[2], PARTY_BADGE_H) for col in PARTY_CARDS]
    return [rect_scale(game, row) for row in rows]


def party_maps_rect(game: tuple[int, int, int, int]) -> dict[str, int]:
    x, y, w, h = (int(v) for v in game[:4])
    left = x + int(round(PARTY_CARDS[0][0] * w))
    right = x + int(round((PARTY_CARDS[-1][0] + PARTY_CARDS[-1][2]) * w))
    return {"x": left, "y": y + int(round(PARTY_MAPS_TOP * h)),
            "w": max(260, right - left), "h": max(28, int(round(PARTY_MAPS_H * h)))}


def party_layout(rect: dict[str, int], game: tuple[int, int, int, int]) -> dict[str, Any]:
    """창 안에서 배지·맵 띠가 놓일 위치를 %로 — 해상도가 달라도 같은 자리."""
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
    """상대 스카우팅 창 — 게임 창 오른쪽 아래."""
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


def spec_for(kind: str, payload: dict[str, Any], rect: dict[str, int]) -> dict[str, Any]:
    """페이로드 → native_overlay 그리기 스펙 (창 크기 기준)."""
    return native_overlay.build_spec(kind, payload, int(rect["w"]), int(rect["h"]))


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
class OverlayHub:
    """레이어드 창 3종을 만들고 · 갱신하고 · 닫는다."""

    def __init__(self, payload_provider: Callable[[str], dict[str, Any]] | None = None,
                 evaluator: Callable[[int, str], None] | None = None,
                 window_factory: Callable[[str, str], Any] | None = None):
        self._windows: dict[str, Any] = {}
        self._specs: dict[str, dict[str, Any]] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._stops: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._payload: dict[str, dict[str, Any]] = {kind: {} for kind in KINDS}
        self._payload_provider = payload_provider
        self._evaluator = evaluator
        self._window_factory = window_factory
        self.last_error: str | None = None
        self.created = 0
        self.last_rect: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------ 상태
    def available(self) -> bool:
        if self._window_factory is not None:
            return True
        return os.name == "nt"

    def state(self) -> dict[str, Any]:
        with self._lock:
            open_kinds = sorted(self._windows.keys())
        return {"supported": self.available(), "open": open_kinds, "created": self.created,
                "error": self.last_error, "backend": BACKEND,
                "title": "Win32 순정 레이어드 창 · 클릭 통과 · 포커스 비탈취"}

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
        spec = spec_for(kind, payload, rect)
        try:
            # 창 생성/갱신/표시를 한 임계 구역으로 묶어 같은 kind 의 단축키·토스트
            # 호출이 겹쳐도 중복 HWND 가 생기거나 한 창 참조를 덮어쓰지 않게 한다.
            with self._lock:
                window = self._windows.get(kind)
                is_new = window is None
                if window is None:
                    window = native_overlay.make_window(kind, TITLES[kind], self._window_factory)
                    if not window.create(rect, spec):
                        message = getattr(window, "error", None) or "오버레이 창을 만들지 못했습니다"
                        self.last_error = message
                        try:
                            window.destroy()
                        except Exception:  # noqa: BLE001
                            pass
                        return {"ok": False, "supported": True, "message": message}
                elif not window.update(spec):
                    message = getattr(window, "error", None) or "오버레이 내용을 갱신하지 못했습니다"
                    self.last_error = message
                    self._windows.pop(kind, None)
                    self._cancel(kind)
                    try:
                        window.destroy()
                    except Exception:  # noqa: BLE001
                        pass
                    return {"ok": False, "supported": True, "message": message}

                if not window.show(rect):
                    message = getattr(window, "error", None) or "오버레이 창을 표시하지 못했습니다"
                    self.last_error = message
                    if not is_new:
                        self._windows.pop(kind, None)
                        self._cancel(kind)
                    try:
                        window.destroy()
                    except Exception:  # noqa: BLE001
                        pass
                    return {"ok": False, "supported": True, "message": message}
                if is_new:
                    self._windows[kind] = window
                    self.created += 1
        except Exception as exc:  # noqa: BLE001 - 오버레이 실패는 앱을 막지 않는다
            log.exception("overlay %s could not open", kind)
            self.last_error = str(exc)
            return {"ok": False, "supported": True, "message": f"오버레이 창을 열지 못했습니다 · {exc}"}
        self._specs[kind] = spec
        self.last_rect[kind] = dict(rect)
        self._cancel(kind)
        if seconds:
            timer = threading.Timer(max(1.0, float(seconds)), lambda: self.hide(kind))
            timer.daemon = True
            timer.start()
            self._timers[kind] = timer
        return {"ok": True, "supported": True, "kind": kind, "rect": rect,
                "backend": BACKEND, "prims": len(spec.get("prims") or [])}

    def hide(self, kind: str) -> dict[str, Any]:
        self._cancel(kind)
        with self._lock:
            window = self._windows.pop(kind, None)
        self._specs.pop(kind, None)
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
        with self._lock:
            window = self._windows.get(kind)
            payload_now = dict(self._payload.get(kind) or {})
        if window is None:
            return
        rect = self.last_rect.get(kind) or {"x": 0, "y": 0, "w": 400, "h": 300}
        spec = spec_for(kind, payload_now, rect)
        try:
            window.update(spec)
            self._specs[kind] = spec
        except Exception:  # noqa: BLE001
            log.debug("overlay %s refresh failed", kind, exc_info=True)

    def spec(self, kind: str) -> dict[str, Any]:
        return dict(self._specs.get(kind) or {})

    def arm_disappear(self, kind: str, rect: dict[str, int], *,
                      threshold: float = 16.0, interval: float = 0.5,
                      hits: int = 2) -> None:
        """화면 지문이 바뀌면(맵 밴 창이 사라지면) 1초 안에 창을 닫는다."""
        if not self.available() or self._window_factory is not None:
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


def hotkey_label(spec: Any, default: str = "Insert") -> str:
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


def clean_hotkey(spec: Any, default: str = "Insert") -> str:
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
