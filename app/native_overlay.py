"""인게임 오버레이용 **Win32 순정 레이어드 창** (ctypes · GDI+).

pywebview 창은 Edge WebView2 프로세스 + 브라우저 창이라, 서든어택이 독점
전체화면으로 떠 있을 때 ① 포커스를 가져가 게임 창이 내려가고 ② 창이 뒤로
밀리며 ③ 첫 표시가 느리다.  이 모듈은 브라우저 없이

    WS_POPUP | WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE
             | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW

창을 만들고, 32bpp DIB 에 GDI+ 로 그린 뒤 ``UpdateLayeredWindow`` 로 한 번에
올린다.  클릭은 ``WS_EX_TRANSPARENT`` + ``WM_NCHITTEST → HTTRANSPARENT`` 로
전부 게임에 통과하고, ``SWP_NOACTIVATE`` 로만 보이므로 포커스를 뺏지 않는다.

구조
----
* :func:`toast_spec` · :func:`party_spec` · :func:`match_spec` — 페이로드 →
  그리기 명령 목록(순수 Python).  리눅스에서 좌표·문구를 그대로 단위 테스트한다.
* :class:`NativeOverlayWindow` — 실제 창(Windows 전용).  그리기 실패는 로그만
  남기고 앱을 막지 않는다.  ``window_factory`` 로 가짜 창을 넣어 테스트한다.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from typing import Any, Callable, Sequence

log = logging.getLogger("native-overlay")

# ── Win32 상수 ───────────────────────────────────────────────────────────
WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
HTTRANSPARENT = -1
# v2.0.7 — 그리기는 **창 소유 스레드**에서만 한다.  다른 스레드가 부르면 이 메시지를
# 창 스레드 큐에 넣고, 창 스레드가 그 자리에서 bitmap·GDI+·UpdateLayeredWindow 를
# 실행한다.  (스레드가 다른 HDC/Graphics 로 첫 렌더를 하면 실패하던 문제)
WM_APP_RENDER = 0x8000 + 1
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_NCHITTEST = 0x0084
WM_ERASEBKGND = 0x0014
WM_DISPLAYCHANGE = 0x007E
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001
SM_CXSCREEN = 0
SM_CYSCREEN = 1

# ── 색 · 글꼴 (앱 UI 와 같은 딥 차콜 + 3색 포인트) ──────────────────────
COLORS = {
    "panel": "#0B0D12",
    "panel2": "#141922",
    "line": "#2A323F",
    "text": "#F4F7FB",
    "muted": "#9AA8BC",
    "coral": "#FF6B6B",
    "ice": "#7FD8F7",
    "gold": "#E4C580",
    "win": "#59D8A4",
    "lose": "#FF7B84",
}
FONT = "Malgun Gothic"
FONT_FALLBACK = "Segoe UI"

# 진단 표식 — 실기 로그에서 어느 오버레이 빌드가 도는지 바로 확인한다.
OVERLAY_DIAG_BUILD = "class-atom-v4-20261128"

# 첫 렌더 시도 순서 — ① GDI+ 로 한 번 ② 실패하면 GDI 로 한 번 더.
# 둘 다 실패하면 오류 원인(Win32 코드 포함)을 self.error 에 남긴다.
RENDER_ATTEMPTS: tuple[dict[str, Any], ...] = (
    {"label": "GDI+", "force_gdi": False},
    {"label": "GDI", "force_gdi": True},
)
RENDER_WAIT_SECONDS = 2.5
BLIT_RETRY_SLEEP = 0.06

# GDI+ TextRenderingHint
#   3 = ClearTypeGridFit → 32bpp 레이어드 창에서 알파가 깨져 글자가 검게 뭉친다
#   4 = AntiAlias      → 투명 창에서도 획 가장자리 알파가 정상
TEXT_RENDERING_HINT = 4


# ---------------------------------------------------------------- Win32 (64비트 안전)
class _Win32:
    """user32·gdi32·kernel32 를 **64비트 포인터 서명**으로 묶어 둔 것.

    ctypes 는 argtypes/restype 를 지정하지 않으면 반환값을 32비트 ``int`` 로
    해석한다.  Win64 에서 HWND·HDC·HMODULE 은 64비트 포인터라 그대로두면
    핸들이 잘려 ``CreateWindowExW`` 가 실패하거나 엉뚱한 주소를 가리킨다.
    그래서 이 앱이 쓰는 모든 함수에 서명을 못박는다.
    """

    def __init__(self) -> None:
        from ctypes import wintypes

        HANDLE = ctypes.c_void_p
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)  # type: ignore[attr-defined]
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        u, g, k = self.user32, self.gdi32, self.kernel32

        def sig(fn: Any, restype: Any, *argtypes: Any) -> None:
            fn.restype = restype
            fn.argtypes = list(argtypes)

        # kernel32
        sig(k.GetModuleHandleW, HANDLE, wintypes.LPCWSTR)
        sig(k.GetCurrentThreadId, wintypes.DWORD)
        sig(k.GetLastError, wintypes.DWORD)
        # user32 — 창
        sig(u.RegisterClassExW, wintypes.ATOM, ctypes.c_void_p)
        sig(u.CreateWindowExW, HANDLE, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            HANDLE, HANDLE, HANDLE, ctypes.c_void_p)
        sig(u.DefWindowProcW, ctypes.c_ssize_t, HANDLE, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM)
        sig(u.SetWindowPos, wintypes.BOOL, HANDLE, HANDLE, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT)
        sig(u.ShowWindow, wintypes.BOOL, HANDLE, ctypes.c_int)
        sig(u.UpdateLayeredWindow, wintypes.BOOL, HANDLE, HANDLE, ctypes.c_void_p,
            ctypes.c_void_p, HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD)
        sig(u.GetWindowRect, wintypes.BOOL, HANDLE, ctypes.c_void_p)
        sig(u.PostMessageW, wintypes.BOOL, HANDLE, wintypes.UINT, wintypes.WPARAM,
            wintypes.LPARAM)
        sig(u.PostQuitMessage, None, ctypes.c_int)
        sig(u.PostThreadMessageW, wintypes.BOOL, wintypes.DWORD, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM)
        sig(u.GetMessageW, wintypes.BOOL, ctypes.c_void_p, HANDLE, wintypes.UINT, wintypes.UINT)
        sig(u.TranslateMessage, wintypes.BOOL, ctypes.c_void_p)
        sig(u.DispatchMessageW, ctypes.c_ssize_t, ctypes.c_void_p)
        sig(u.DrawTextW, ctypes.c_int, HANDLE, wintypes.LPCWSTR, ctypes.c_int,
            ctypes.c_void_p, wintypes.UINT)
        sig(u.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
        # gdi32 — DIB · 글자
        sig(g.CreateCompatibleDC, HANDLE, HANDLE)
        sig(g.CreateDIBSection, HANDLE, HANDLE, ctypes.c_void_p, wintypes.UINT,
            ctypes.c_void_p, HANDLE, wintypes.DWORD)
        sig(g.SelectObject, HANDLE, HANDLE, HANDLE)
        sig(g.DeleteObject, wintypes.BOOL, HANDLE)
        sig(g.DeleteDC, wintypes.BOOL, HANDLE)
        sig(g.SetBkMode, ctypes.c_int, HANDLE, ctypes.c_int)
        sig(g.SetTextColor, wintypes.DWORD, HANDLE, wintypes.DWORD)
        sig(g.CreateSolidBrush, HANDLE, wintypes.DWORD)
        # FillRect 는 GDI 함수가 아니라 User32 함수다.
        sig(u.FillRect, ctypes.c_int, HANDLE, ctypes.c_void_p, HANDLE)
        sig(g.CreateFontW, HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.LPCWSTR)

    def last_error(self) -> int:
        """직전 Win32 호출의 오류 코드 — ctypes 가 잡아 둔 값 우선."""
        try:
            code = int(ctypes.get_last_error() or 0)
        except Exception:  # noqa: BLE001
            code = 0
        if code:
            return code
        try:
            return int(self.kernel32.GetLastError() or 0)
        except Exception:  # noqa: BLE001
            return 0

    def error_text(self, code: int) -> str:
        """오류 코드 → 'Win32 1400 (잘못된 창 핸들)' 같은 문장."""
        text = ""
        try:
            text = ctypes.FormatError(int(code or 0)).strip()
        except Exception:  # noqa: BLE001
            text = ""
        return f"Win32 {int(code or 0)}" + (f" ({text})" if text else "")


_WIN32: _Win32 | None = None
_win32_lock = threading.Lock()


def win32() -> _Win32:
    """서명이 설정된 Win32 묶음 (Windows 에서만)."""
    global _WIN32
    with _win32_lock:
        if _WIN32 is None:
            _WIN32 = _Win32()
        return _WIN32



# ---------------------------------------------------------------- 그리기 명령
def hex_rgb(value: str) -> tuple[int, int, int]:
    text = str(value or "").lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except (ValueError, IndexError):
        return 255, 255, 255


def _prim(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, **fields}


def rect(x: float, y: float, w: float, h: float, fill: str, *,
         alpha: float = 1.0, radius: float = 0.0, stroke: str | None = None,
         stroke_alpha: float = 1.0, stroke_width: float = 1.0) -> dict[str, Any]:
    return _prim("rect", x=round(x, 1), y=round(y, 1), w=round(max(0.0, w), 1),
                 h=round(max(0.0, h), 1), fill=fill, alpha=alpha, radius=radius,
                 stroke=stroke, stroke_alpha=stroke_alpha, stroke_width=stroke_width)


def text(x: float, y: float, w: float, h: float, value: str, *, size: float = 12.0,
         color: str = "text", alpha: float = 1.0, bold: bool = False,
         align: str = "left", valign: str = "center") -> dict[str, Any]:
    return _prim("text", x=round(x, 1), y=round(y, 1), w=round(max(1.0, w), 1),
                 h=round(max(1.0, h), 1), text=str(value or ""), size=size,
                 color=color, alpha=alpha, bold=bold, align=align, valign=valign)


def line(x1: float, y1: float, x2: float, y2: float, color: str = "line",
         alpha: float = 1.0, width: float = 1.0) -> dict[str, Any]:
    return _prim("line", x1=round(x1, 1), y1=round(y1, 1), x2=round(x2, 1),
                 y2=round(y2, 1), color=color, alpha=alpha, width=width)


def _spec(width: int, height: int, prims: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {"w": int(width), "h": int(height), "prims": list(prims)}


def _pct(value: Any, digits: int = 0) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.{digits}f}%"


# ---------------------------------------------------------------- 토스트
def toast_spec(payload: dict[str, Any], width: int = 372, height: int = 96) -> dict[str, Any]:
    """경기 종료 미니 결과 — 승패 · 맵 · K/D · 딜량 · 연승."""
    win = bool(payload.get("win"))
    accent = COLORS["win"] if win else COLORS["lose"]
    prims = [
        rect(0, 0, width, height, COLORS["panel"], alpha=0.93, radius=10,
             stroke=COLORS["line"], stroke_alpha=0.9),
        rect(0, 10, 4, height - 20, accent, alpha=1.0, radius=2),
        text(16, 10, 90, 22, "WIN" if win else "LOSE", size=11, color=accent if win else "lose", bold=True),
        text(16, 30, width - 32, 26, str(payload.get("map") or "경기 종료"), size=15,
             color="text", bold=True, valign="top"),
        text(16, 56, width - 32, 18, str(payload.get("sub") or ""), size=11, color="muted"),
        text(16, height - 20, width - 32, 14, str(payload.get("foot") or "SA Aim Labs"),
             size=9, color="muted", alpha=0.75, valign="bottom"),
    ]
    return _spec(width, height, prims)


# ---------------------------------------------------------------- 파티 대기실
def party_spec(payload: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """5칸 배지 + 카드 밑 맵 띠 (픽 3 · 밴 3)."""
    rows = list(payload.get("rows") or [])[:5]
    layout = payload.get("layout") or {}
    chips = list(layout.get("chips") or [])
    prims: list[dict[str, Any]] = []
    chip_top = float(layout.get("chip_top") or 0) / 100.0 * height
    chip_h = max(16.0, float(layout.get("chip_height") or 3.0) / 100.0 * height)
    for index, row in enumerate(rows):
        chip = chips[index] if index < len(chips) else None
        if not chip:
            continue
        left = float(chip.get("left") or 0) / 100.0 * width
        chip_w = max(40.0, float(chip.get("width") or 10) / 100.0 * width)
        name = str(row.get("name") or "").strip() or "—"
        status = str(row.get("status") or "")
        needs = status in ("unknown", "needs_check", "empty")
        tone = COLORS["gold"] if needs else COLORS["text"]
        badge = "확인 필요" if status == "needs_check" else ("미검증" if needs else
                ("OCR 보정" if status == "corrected" else ""))
        prims.append(rect(left, chip_top, chip_w, chip_h, COLORS["panel"], alpha=0.86,
                          radius=max(4.0, chip_h / 3), stroke=COLORS["line"], stroke_alpha=0.8))
        prims.append(text(left + 8, chip_top, chip_w - (58 if badge else 16), chip_h, name,
                          size=max(10.0, chip_h * 0.52), color=tone, bold=True))
        if badge:
            prims.append(text(left + chip_w - 56, chip_top, 48, chip_h, badge, size=9.0,
                              color="gold", align="right"))
    maps_top = float(layout.get("maps_top") or 0) / 100.0 * height
    maps_h = max(18.0, float(layout.get("maps_height") or 3.0) / 100.0 * height)
    plan = payload.get("plan") or {}
    picks = [str((p or {}).get("map") or "") for p in (plan.get("picks") or [])][:3]
    bans = [str((b or {}).get("map") or "") for b in (plan.get("bans") or [])][:3]
    prims.append(rect(0, maps_top, width, maps_h, COLORS["panel"], alpha=0.82,
                      radius=max(4.0, maps_h / 3)))
    if picks or bans:
        head = "픽 " + " · ".join(picks) if picks else ""
        tail = "  밴 " + " · ".join(bans) if bans else ""
        prims.append(text(10, maps_top, width * 0.55, maps_h, head, size=max(9.5, maps_h * 0.5),
                          color="ice", bold=True))
        if tail:
            prims.append(text(width * 0.55, maps_top, width * 0.45 - 10, maps_h, tail.strip(),
                              size=max(9.5, maps_h * 0.5), color="coral", align="right"))
    else:
        scan_message = str(payload.get("scan_message") or "").strip()
        display_message = scan_message or "표본이 쌓이면 픽·밴을 표시합니다"
        status_color = "gold" if any(token in display_message for token in ("실패", "0/5", "오류", "확인 필요")) else "muted"
        prims.append(text(10, maps_top, width - 20, maps_h, display_message,
                          size=max(9.0, min(12.0, maps_h * 0.42)), color=status_color))
    return _spec(width, height, prims)


# ---------------------------------------------------------------- 맵 밴 · 스카우팅
def match_spec(payload: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    tab = str(payload.get("tab") or "mapban")
    if tab == "scout":
        return _scout_spec(payload, width, height)
    return _mapban_spec(payload, width, height)


def _mapban_spec(payload: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """5개 맵 카드 위 얇은 글래스 헤더 + 승률 게이지."""
    cards = list(payload.get("cards") or [])[:5]
    prims: list[dict[str, Any]] = []
    if not cards:
        prims.append(text(8, 0, width - 16, height, "맵 밴 화면을 찾지 못했습니다",
                          size=12, color="muted", align="center"))
        return _spec(width, height, prims)
    gap = max(4.0, width * 0.006)
    card_w = (width - gap * (len(cards) + 1)) / max(1, len(cards))
    head_h = max(22.0, min(34.0, height * 0.42))
    for index, card in enumerate(cards):
        left = gap + index * (card_w + gap)
        tone = COLORS["ice"] if card.get("tone") == "pick" else (
            COLORS["coral"] if card.get("tone") == "ban" else COLORS["muted"])
        prims.append(rect(left, 0, card_w, head_h, COLORS["panel"], alpha=0.88, radius=6,
                          stroke=tone, stroke_alpha=0.85 if card.get("tone") != "neutral" else 0.35))
        label = str(card.get("label") or "")
        pct = card.get("pct")
        prims.append(text(left + 8, 0, card_w - 16, head_h * 0.58, str(card.get("map") or ""),
                          size=max(10.0, min(13.0, card_w * 0.075)), color="text", bold=True))
        head = f"{label} {_pct(pct)}" if label else _pct(pct)
        prims.append(text(left + 8, head_h * 0.52, card_w - 16, head_h * 0.42, head,
                          size=max(9.0, min(12.0, card_w * 0.065)), color=tone, bold=True))
        gauge = float(card.get("gauge") or 0) / 100.0
        prims.append(rect(left + 8, head_h - 5, card_w - 16, 3, COLORS["line"], alpha=0.8, radius=2))
        prims.append(rect(left + 8, head_h - 5, max(2.0, (card_w - 16) * gauge), 3, tone, radius=2))
    return _spec(width, height, prims)


def _scout_stat_line(stats: dict[str, Any] | None) -> str:
    """`그마 II · 52% · K/D 58%` — 한 모드의 스탯 한 줄 (접두어 없음).

    표본이 없으면 `언랭크 · -` / `그마 II · -` 로 짧게 남긴다.  맵별 승률은
    카운트다운에서 알 수 없고 조회도 느려서 쓰지 않는다.
    """
    stats = stats or {}
    tier = str(stats.get("short") or "").strip()
    if tier in ("", "언랭", "언랭크"):
        tier = "언랭크"
    games = int(stats.get("games") or 0)
    if not games:
        return f"{tier} · -"
    bits = [tier]
    win, kd = stats.get("win"), stats.get("kd")
    if win is not None:
        bits.append(f"{round(float(win))}%")
    if kd is not None:
        bits.append(f"K/D {round(float(kd))}%")
    return " · ".join(bits)


def scout_groups(payload: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    """스카우팅 페이로드 → [(그룹 제목, 행 목록)] — 상단 5명 · 하단 5명.

    10인 카운트다운 화면은 **윗줄 5명 / 아랫줄 5명**이다.  아군·적군 추정은 하지
    않고 화면 그대로 위/아래로 나눈다(사용자 요청).  예전 5명 페이로드(`rows`)도
    그대로 받아 한 그룹으로 그린다.
    """
    top = [r for r in (payload.get("top_team") or []) if isinstance(r, dict)]
    bottom = [r for r in (payload.get("bottom_team") or []) if isinstance(r, dict)]
    if top or bottom:
        return [("상단 5명", top[:5]), ("하단 5명", bottom[:5])]
    rows = [r for r in (payload.get("rows") or []) if isinstance(r, dict)]
    if len(rows) > 5:
        return [("상단 5명", rows[:5]), ("하단 5명", rows[5:10])]
    # 분석 중 — 화면에서 읽은 인원수만큼 빈 칸을 미리 보여 준다.
    try:
        top_n = int(payload.get("top") or 0)
        bottom_n = int(payload.get("bottom") or 0)
    except (TypeError, ValueError):
        top_n = bottom_n = 0
    if top_n or bottom_n:
        return [("상단 5명", [{}] * max(0, min(5, top_n))),
                ("하단 5명", [{}] * max(0, min(5, bottom_n)))]
    return [("상대 팀", rows[:5])]


def _scout_spec(payload: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """우하단 — **1행 3열**: 닉네임 | 솔로 | 파티 (헤더 줄 없음).

    - 맵 이름·타이틀·서브타이틀은 넣지 않는다(사용자 요청).
    - 그룹 줄(`상단 5명` / `하단 5명`) 오른쪽에 작은 `솔로` `파티` 열 라벨만 둔다.
    - 행마다 "솔로/파티" 접두어를 붙이지 않아 10명이 10줄에 들어간다.
    """
    groups = scout_groups(payload)
    prims = [rect(0, 0, width, height, COLORS["panel"], alpha=0.9, radius=10,
                  stroke=COLORS["line"], stroke_alpha=0.9)]
    pad = 6.0
    group_head = 17.0
    slots = sum(max(1, len(rows)) for _, rows in groups)
    usable = max(40.0, height - pad * 2 - group_head * len(groups))
    row_h = max(16.0, usable / max(1, slots))

    name_x = 14.0
    name_w = width * 0.32
    solo_x = width * 0.335
    solo_w = width * 0.325
    party_x = width * 0.665
    party_w = width - party_x - 10

    top = pad
    for group_index, (label, rows) in enumerate(groups):
        tone = "ice" if group_index == 0 else "coral"
        head = label
        if payload.get("loading") and group_index == 0:
            head = f"{label} · 분석 중…"
        prims.append(text(name_x - 2, top, name_w, group_head, head, size=11.0, color=tone, bold=True))
        prims.append(text(solo_x, top, solo_w, group_head, "솔로", size=10.0, color="muted", bold=True))
        prims.append(text(party_x, top, party_w, group_head, "파티", size=10.0, color="muted", bold=True))
        top += group_head
        for index in range(max(1, len(rows))):
            row = rows[index] if index < len(rows) else None
            prims.append(rect(6, top + 0.5, width - 12, row_h - 2.0, COLORS["panel2"],
                              alpha=0.65 if row else 0.22, radius=5))
            if row:
                name = str(row.get("name") or "")[:12]
                error = str(row.get("error") or "")
                prims.append(text(name_x, top, name_w - 4, row_h - 2, name, size=12.5,
                                  color="text", bold=True))
                if error:
                    prims.append(text(solo_x, top, party_w, row_h - 2, error, size=10.5, color="gold"))
                else:
                    prims.append(text(solo_x, top, solo_w, row_h - 2,
                                      _scout_stat_line(row.get("solo_stats")), size=11.5, color="ice"))
                    prims.append(text(party_x, top, party_w, row_h - 2,
                                      _scout_stat_line(row.get("party_stats")), size=11.5, color="gold"))
            else:
                prims.append(text(name_x, top, width - 28, row_h - 2, "—", size=11.5, color="muted"))
            top += row_h
    return _spec(width, height, prims)


SPEC_BUILDERS: dict[str, Callable[..., dict[str, Any]]] = {
    "toast": toast_spec,
    "party": party_spec,
    "match": match_spec,
}


def build_spec(kind: str, payload: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    builder = SPEC_BUILDERS.get(kind)
    if builder is None:
        return _spec(width, height, [])
    if kind == "toast":
        return builder(payload, width=width, height=height)
    return builder(payload, width, height)


# ---------------------------------------------------------------- GDI+ 바인딩
class _GdiPlus:
    """gdiplus.dll 최소 바인딩 — 필요한 함수만."""

    def __init__(self) -> None:
        self.ok = False
        self.error: str | None = None
        if os.name != "nt":
            self.error = "Windows 전용"
            return
        try:
            self.dll = ctypes.WinDLL("gdiplus.dll")  # type: ignore[attr-defined]
            self.token = ctypes.c_void_p()

            class StartupInput(ctypes.Structure):
                _fields_ = [("version", ctypes.c_uint32), ("callback", ctypes.c_void_p),
                            ("suppress_bg", ctypes.c_int), ("suppress_codecs", ctypes.c_int)]

            startup = self.dll.GdiplusStartup
            startup.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(StartupInput),
                                ctypes.c_void_p]
            startup.restype = ctypes.c_int
            data = StartupInput(1, None, 0, 0)
            if startup(ctypes.byref(self.token), ctypes.byref(data), None) != 0:
                raise RuntimeError("GdiplusStartup 실패")
            self._bind()
            self.ok = True
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)

    def _fn(self, name: str, restype: Any, *argtypes: Any) -> Any:
        fn = getattr(self.dll, name)
        fn.restype = restype
        fn.argtypes = list(argtypes)
        return fn

    def _bind(self) -> None:
        p = ctypes.c_void_p
        self.create_from_hdc = self._fn("GdipCreateFromHDC", ctypes.c_int, p, ctypes.POINTER(p))
        self.delete_graphics = self._fn("GdipDeleteGraphics", ctypes.c_int, p)
        self.set_smoothing = self._fn("GdipSetSmoothingMode", ctypes.c_int, p, ctypes.c_int)
        self.set_text_aa = self._fn("GdipSetTextRenderingHint", ctypes.c_int, p, ctypes.c_int)
        self.create_solid = self._fn("GdipCreateSolidFill", ctypes.c_int, ctypes.c_uint32,
                                     ctypes.POINTER(p))
        self.delete_brush = self._fn("GdipDeleteBrush", ctypes.c_int, p)
        self.create_pen = self._fn("GdipCreatePen1", ctypes.c_int, ctypes.c_uint32,
                                   ctypes.c_float, ctypes.c_int, ctypes.POINTER(p))
        self.delete_pen = self._fn("GdipDeletePen", ctypes.c_int, p)
        self.fill_rect = self._fn("GdipFillRectangle", ctypes.c_int, p, p,
                                  ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float)
        self.draw_rect = self._fn("GdipDrawRectangle", ctypes.c_int, p, p,
                                  ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float)
        self.draw_line = self._fn("GdipDrawLine", ctypes.c_int, p, p,
                                  ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float)
        self.create_path = self._fn("GdipCreatePath", ctypes.c_int, ctypes.c_int,
                                    ctypes.POINTER(p))
        self.delete_path = self._fn("GdipDeletePath", ctypes.c_int, p)
        self.add_arc = self._fn("GdipAddPathArc", ctypes.c_int, p,
                                ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float,
                                ctypes.c_float, ctypes.c_float)
        self.add_line = self._fn("GdipAddPathLine", ctypes.c_int, p,
                                 ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float)
        self.close_figure = self._fn("GdipClosePathFigure", ctypes.c_int, p)
        self.fill_path = self._fn("GdipFillPath", ctypes.c_int, p, p)
        self.draw_path = self._fn("GdipDrawPath", ctypes.c_int, p, p, p)
        self.create_family = self._fn("GdipCreateFontFamilyFromName", ctypes.c_int,
                                      ctypes.c_wchar_p, p, ctypes.POINTER(p))
        self.delete_family = self._fn("GdipDeleteFontFamily", ctypes.c_int, p)
        self.create_font = self._fn("GdipCreateFont", ctypes.c_int, p, ctypes.c_float,
                                    ctypes.c_int, ctypes.c_int, ctypes.POINTER(p))
        self.delete_font = self._fn("GdipDeleteFont", ctypes.c_int, p)
        self.create_format = self._fn("GdipCreateStringFormat", ctypes.c_int, ctypes.c_int,
                                      ctypes.c_uint16, ctypes.POINTER(p))
        self.delete_format = self._fn("GdipDeleteStringFormat", ctypes.c_int, p)
        self.set_align = self._fn("GdipSetStringFormatAlign", ctypes.c_int, p, ctypes.c_int)
        self.set_line_align = self._fn("GdipSetStringFormatLineAlign", ctypes.c_int, p,
                                       ctypes.c_int)
        self.draw_string = self._fn("GdipDrawString", ctypes.c_int, p, ctypes.c_wchar_p,
                                    ctypes.c_int, p, ctypes.c_void_p, p, p)
        self.create_fill_rect_brush = self._fn("GdipCreateSolidFill", ctypes.c_int,
                                               ctypes.c_uint32, ctypes.POINTER(p))

    def color(self, hex_value: str, alpha: float = 1.0) -> int:
        r, g, b = hex_rgb(COLORS.get(hex_value, hex_value))
        a = max(0, min(255, int(round(255 * max(0.0, min(1.0, float(alpha)))))))
        # GDI+ ARGB (0xAARRGGBB)
        return (a << 24) | (r << 16) | (g << 8) | b


_gdiplus: _GdiPlus | None = None
_gdiplus_lock = threading.Lock()


def gdiplus() -> _GdiPlus:
    global _gdiplus
    with _gdiplus_lock:
        if _gdiplus is None:
            _gdiplus = _GdiPlus()
        return _gdiplus


class _Bitmap:
    """32bpp DIB + 메모리 DC — UpdateLayeredWindow 입력."""

    def __init__(self, width: int, height: int):
        from ctypes import wintypes

        self.width, self.height = max(1, int(width)), max(1, int(height))
        libs = win32()
        self.user32 = libs.user32
        self.gdi32 = libs.gdi32

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

        self._info = BITMAPINFO()
        header = self._info.bmiHeader
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = self.width
        header.biHeight = -self.height       # top-down
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0             # BI_RGB
        self.hdc = self.gdi32.CreateCompatibleDC(None)
        if not self.hdc:
            libs = win32()
            raise RuntimeError(f"CreateCompatibleDC 실패 · {libs.error_text(libs.last_error())}")
        self._bits = ctypes.c_void_p()
        self.hbitmap = self.gdi32.CreateDIBSection(
            self.hdc, ctypes.byref(self._info), 0, ctypes.byref(self._bits), None, 0)
        if not self.hbitmap:
            code = libs.last_error()
            self.gdi32.DeleteDC(self.hdc)
            raise RuntimeError(f"CreateDIBSection 실패 · {libs.error_text(code)}")
        self.old = self.gdi32.SelectObject(self.hdc, self.hbitmap)
        self.premultiplied = True

    def clear(self) -> None:
        # DIB 비트(픽셀 메모리)를 0 으로 — HBITMAP 핸들이 아니라 비트 주소를 써야 한다
        try:
            if self._bits:
                ctypes.memset(self._bits, 0, self.width * self.height * 4)
        except Exception:  # noqa: BLE001
            pass

    def dispose(self) -> None:
        try:
            self.gdi32.SelectObject(self.hdc, self.old)
            self.gdi32.DeleteObject(self.hbitmap)
            self.gdi32.DeleteDC(self.hdc)
        except Exception:  # noqa: BLE001
            pass


def render_prims(bitmap: _Bitmap, prims: Sequence[dict[str, Any]], *, force_gdi: bool = False) -> bool:
    """그리기 명령 → DIB. GDI+ 가 없거나 force_gdi 면 GDI 사각형/글자로 그린다."""
    if not force_gdi:
        gp = gdiplus()
        if gp.ok:
            return _render_gdiplus(gp, bitmap, prims)
    return _render_gdi(bitmap, prims)


def _render_gdiplus(gp: _GdiPlus, bitmap: _Bitmap, prims: Sequence[dict[str, Any]]) -> bool:
    graphics = ctypes.c_void_p()
    # GdipCreateFromHDC(HDC hdc, GpGraphics** graphics) — HDC 가 먼저, 결과 포인터가 뒤.
    # (예전 코드는 순서가 거꾸로라 스택 주소를 HDC 로 넘겨 늘 GDI 폴백으로 떨어졌다.)
    if gp.create_from_hdc(bitmap.hdc, ctypes.byref(graphics)) != 0 or not graphics:
        log.debug("GdipCreateFromHDC 실패 · GDI 폴백")
        return _render_gdi(bitmap, prims)
    try:
        gp.set_smoothing(graphics, 4)                    # SmoothingModeAntiAlias
        gp.set_text_aa(graphics, TEXT_RENDERING_HINT)    # AntiAlias (ClearType 는 알파 깨짐)
        family = ctypes.c_void_p()
        # GdipCreateFontFamilyFromName(WCHAR* name, GpFontCollection*, GpFontFamily**)
        if gp.create_family(FONT, None, ctypes.byref(family)) != 0 or not family:
            gp.create_family(FONT_FALLBACK, None, ctypes.byref(family))

        def brush(color: int) -> Any:
            out = ctypes.c_void_p()
            if gp.create_solid(color, ctypes.byref(out)) != 0:
                return None
            return out

        def pen(color: int, width: float) -> Any:
            out = ctypes.c_void_p()
            if gp.create_pen(color, ctypes.c_float(max(0.5, width)), 2, ctypes.byref(out)) != 0:
                return None
            return out

        for prim in prims:
            kind = prim.get("type")
            if kind == "rect":
                color = gp.color(prim.get("fill") or "panel", prim.get("alpha", 1.0))
                fill = brush(color)
                if fill is None:
                    continue
                x, y, w, h = (float(prim["x"]), float(prim["y"]), float(prim["w"]), float(prim["h"]))
                radius = float(prim.get("radius") or 0)
                if radius > 0.5:
                    path = _rounded_path(gp, x, y, w, h, radius)
                    if path:
                        gp.fill_path(graphics, fill, path)
                    if prim.get("stroke"):
                        stroke = pen(gp.color(prim["stroke"], prim.get("stroke_alpha", 1.0)),
                                     float(prim.get("stroke_width") or 1.0))
                        if path and stroke is not None:
                            gp.draw_path(graphics, stroke, path)
                        if stroke is not None:
                            gp.delete_pen(stroke)
                    if path:
                        gp.delete_path(path)
                else:
                    gp.fill_rect(graphics, fill, ctypes.c_float(x), ctypes.c_float(y),
                                 ctypes.c_float(w), ctypes.c_float(h))
                    if prim.get("stroke"):
                        stroke = pen(gp.color(prim["stroke"], prim.get("stroke_alpha", 1.0)),
                                     float(prim.get("stroke_width") or 1.0))
                        if stroke is not None:
                            gp.draw_rect(graphics, stroke, ctypes.c_float(x), ctypes.c_float(y),
                                         ctypes.c_float(w), ctypes.c_float(h))
                            gp.delete_pen(stroke)
                gp.delete_brush(fill)
            elif kind == "line":
                stroke = pen(gp.color(prim.get("color") or "line", prim.get("alpha", 1.0)),
                             float(prim.get("width") or 1.0))
                if stroke is None:
                    continue
                gp.draw_line(graphics, stroke, ctypes.c_float(prim["x1"]), ctypes.c_float(prim["y1"]),
                             ctypes.c_float(prim["x2"]), ctypes.c_float(prim["y2"]))
                gp.delete_pen(stroke)
            elif kind == "text" and family:
                font = ctypes.c_void_p()
                style = 1 if prim.get("bold") else 0   # FontStyleBold
                if gp.create_font(family, ctypes.c_float(float(prim.get("size") or 12)),
                                  style, 3, ctypes.byref(font)) != 0 or not font:
                    continue
                fmt = ctypes.c_void_p()
                gp.create_format(0, 0, ctypes.byref(fmt))
                if fmt:
                    align = {"left": 0, "center": 1, "right": 2}.get(str(prim.get("align")), 0)
                    gp.set_align(fmt, align)
                    gp.set_line_align(fmt, 0)
                color = gp.color(prim.get("color") or "text", prim.get("alpha", 1.0))
                fill = brush(color)
                if fill is not None:
                    rectf = (ctypes.c_float * 4)(float(prim["x"]), float(prim["y"]),
                                                 float(prim["w"]), float(prim["h"]))
                    gp.draw_string(graphics, str(prim.get("text") or ""), -1, font,
                                   ctypes.byref(rectf), fmt, fill)
                    gp.delete_brush(fill)
                if fmt:
                    gp.delete_format(fmt)
                gp.delete_font(font)
        if family:
            gp.delete_family(family)
        return True
    finally:
        gp.delete_graphics(graphics)


def _rounded_path(gp: _GdiPlus, x: float, y: float, w: float, h: float, radius: float) -> Any:
    r = max(1.0, min(radius, min(w, h) / 2))
    path = ctypes.c_void_p()
    # GdipCreatePath(FillMode brushMode, GpPath** path)
    gp.create_path(0, ctypes.byref(path))
    gp.add_arc(path, x, y, r * 2, r * 2, 180, 90)
    gp.add_arc(path, x + w - r * 2, y, r * 2, r * 2, 270, 90)
    gp.add_arc(path, x + w - r * 2, y + h - r * 2, r * 2, r * 2, 0, 90)
    gp.add_arc(path, x, y + h - r * 2, r * 2, r * 2, 90, 90)
    gp.close_figure(path)
    return path


def _render_gdi(bitmap: _Bitmap, prims: Sequence[dict[str, Any]]) -> bool:
    """GDI 폴백 — 둥근 모서리·안티에일리어싱은 없지만 내용은 보인다."""
    if os.name != "nt":
        return False
    user32, gdi32 = bitmap.user32, bitmap.gdi32
    try:
        gdi32.SetBkMode(bitmap.hdc, 1)   # TRANSPARENT
        for prim in prims:
            kind = prim.get("type")
            if kind == "rect":
                r, g, b = hex_rgb(COLORS.get(prim.get("fill") or "panel", prim.get("fill")))
                brush = gdi32.CreateSolidBrush((int(round(b)) << 16) | (int(round(g)) << 8) | int(round(r)))
                user32.FillRect(bitmap.hdc, ctypes.byref((ctypes.c_int * 4)(
                    int(prim["x"]), int(prim["y"]), int(prim["x"] + prim["w"]), int(prim["y"] + prim["h"]))), brush)
                gdi32.DeleteObject(brush)
            elif kind == "text":
                r, g, b = hex_rgb(COLORS.get(prim.get("color") or "text", prim.get("color")))
                gdi32.SetTextColor(bitmap.hdc, (int(round(b)) << 16) | (int(round(g)) << 8) | int(round(r)))
                weight = 700 if prim.get("bold") else 400
                font = gdi32.CreateFontW(-int(float(prim.get("size") or 12)), 0, 0, 0, weight,
                                         0, 0, 0, 1, 0, 0, 5, 0, "Malgun Gothic")
                gdi32.SelectObject(bitmap.hdc, font)
                rect = (ctypes.c_int * 4)(int(prim["x"]), int(prim["y"]),
                                          int(prim["x"] + prim["w"]), int(prim["y"] + prim["h"]))
                align = {"left": 0x0, "center": 0x1, "right": 0x2}.get(str(prim.get("align")), 0)
                user32.DrawTextW(bitmap.hdc, ctypes.c_wchar_p(str(prim.get("text") or "")), -1,
                                 ctypes.byref(rect), 0x10 | 0x20 | align)  # SINGLELINE|VCENTER
                gdi32.DeleteObject(font)
        return True
    except Exception:  # noqa: BLE001
        log.debug("gdi fallback failed", exc_info=True)
        return False


# ---------------------------------------------------------------- 창
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)   # 리눅스 테스트용 폴백
# LRESULT(HWND, UINT, WPARAM, LPARAM) — Win64 에서 WPARAM 은 포인터 크기다.
WNDPROC = _WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint32,
                       ctypes.c_size_t, ctypes.c_ssize_t)


class NativeOverlayWindow:
    """레이어드 창 하나 — show/hide/update/destroy."""

    _class_registered: set[str] = set()
    # Win32 는 등록된 클래스/콜백을 우리 프로세스 메모리에서 참조한다.
    # 콜백 객체가 GC 되면 창 메시지가 들어올 때 프로세스가 죽으므로 모듈 수준에 보관한다.
    _class_wndprocs: dict[str, Any] = {}
    _class_atoms: dict[str, int] = {}

    def __init__(self, kind: str, title: str):
        self.kind = kind
        self.title = title
        self.hwnd: int | None = None
        self.bitmap: _Bitmap | None = None
        self.error: str | None = None
        self._wndproc = None
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._size = (10, 10)
        # 창 스레드에 그리기를 넘길 때 쓰는 슬롯 (v2.0.7)
        self._spec_lock = threading.Lock()
        self._pending_spec: dict[str, Any] | None = None
        self._render_done = threading.Event()
        self._render_ok = False
        self._force_gdi = False

    def diag(self) -> str:
        """실기 로그용 한 줄 — 어느 오버레이 빌드가 창을 만들었는지."""
        return (f"overlay={self.kind} hwnd={self.hwnd} size={self._size} "
                f"build={OVERLAY_DIAG_BUILD}")

    # -------------------------------------------------- 공개 API
    def create(self, rect: dict[str, int], spec: dict[str, Any]) -> bool:
        if os.name != "nt":
            self.error = "Windows 전용"
            return False
        try:
            self._size = (int(rect["w"]), int(rect["h"]))
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name=f"overlay-native-{self.kind}")
            self._thread.start()
            if not self._ready.wait(4.0):
                self.error = "창 생성 시간 초과"
                return False
            if not self.hwnd:
                self.error = self.error or "오버레이 창을 만들지 못했습니다"
                return False
            # 첫 그리기 — 창 스레드에서 실행된다.  (다른 스레드의 HDC·GDI+ 로
            # 그리던 예전 구조가 첫 렌더 실패의 원인이었다)
            if not self.update(spec):
                # 한 번 더: 큐에 넣은 그리기가 아직 안 돌았을 수 있으니 짧게 기다렸다 재시도
                time.sleep(BLIT_RETRY_SLEEP)
                if not self.update(spec):
                    self.error = self.error or "오버레이 첫 렌더에 실패했습니다"
                    log.error("native overlay first render failed · %s · %s", self.diag(), self.error)
                    return False
            log.info("native overlay created · %s", self.diag())
            return bool(self.hwnd)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            return False

    def update(self, spec: dict[str, Any]) -> bool:
        """내용 갱신 — 어느 스레드에서 불러도 **창 소유 스레드**가 그린다."""
        if os.name != "nt" or not self.hwnd:
            return False
        if self._thread_id and threading.get_ident() != self._thread_id:
            return self._post_render(spec)
        return self._render_now(spec)

    # -------------------------------------------------- 그리기 (창 스레드)
    def _post_render(self, spec: dict[str, Any]) -> bool:
        """창 스레드 큐에 그리기를 넣고 결과를 기다린다."""
        with self._spec_lock:
            self._pending_spec = spec
        self._render_done.clear()
        try:
            posted = win32().user32.PostMessageW(ctypes.c_void_p(self.hwnd), WM_APP_RENDER, 0, 0)
        except Exception:  # noqa: BLE001
            posted = 0
        if not posted:
            code = win32().last_error()
            self.error = f"그리기 요청을 전달하지 못했습니다 · {win32().error_text(code)}"
            log.warning(self.error)
            return False
        if not self._render_done.wait(RENDER_WAIT_SECONDS):
            # 메시지 루프가 바쁘거나 창이 사라졌다 — 다음 갱신에서 다시 그린다.
            log.warning("overlay render timeout · %s", self.diag())
            return False
        return bool(self._render_ok)

    def _take_pending(self) -> dict[str, Any] | None:
        with self._spec_lock:
            spec = self._pending_spec
            self._pending_spec = None
        return spec

    def _render_now(self, spec: dict[str, Any]) -> bool:
        """실제 그리기 — 반드시 창 소유 스레드에서 불린다."""
        self.error = None
        try:
            width, height = int(spec.get("w") or 0), int(spec.get("h") or 0)
            if width <= 0 or height <= 0:
                self.error = f"오버레이 크기가 올바르지 않습니다 · {width}×{height}"
                return False
            ok = False
            last_error: str | None = None
            for attempt in RENDER_ATTEMPTS:
                if self.bitmap is None or self._size != (width, height):
                    if self.bitmap is not None:
                        self.bitmap.dispose()
                    self._size = (width, height)
                    self.bitmap = _Bitmap(width, height)
                else:
                    self.bitmap.clear()
                if not render_prims(self.bitmap, spec.get("prims") or [],
                                    force_gdi=bool(attempt.get("force_gdi"))):
                    last_error = f"{attempt.get('label')} 그리기 실패"
                    continue
                if self._blit():
                    ok = True
                    break
                last_error = self.error or "UpdateLayeredWindow 실패"
                time.sleep(BLIT_RETRY_SLEEP)
                if self._blit():
                    ok = True
                    break
            if not ok:
                self.error = f"오버레이 첫 렌더에 실패했습니다 · {last_error or '알 수 없는 오류'}"
                log.error(self.error)
            return ok
        except Exception as exc:  # noqa: BLE001
            self.error = f"오버레이 렌더 오류 · {exc}"
            log.debug("overlay update failed", exc_info=True)
            return False

    def show(self, rect: dict[str, int] | None = None) -> bool:
        if os.name != "nt" or not self.hwnd:
            return False
        try:
            user32 = win32().user32
            if rect:
                user32.SetWindowPos(ctypes.c_void_p(self.hwnd), ctypes.c_void_p(HWND_TOPMOST),
                                    int(rect["x"]), int(rect["y"]), int(rect["w"]), int(rect["h"]),
                                    SWP_NOACTIVATE | SWP_SHOWWINDOW)
            else:
                user32.SetWindowPos(ctypes.c_void_p(self.hwnd), ctypes.c_void_p(HWND_TOPMOST),
                                    0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            return True
        except Exception:  # noqa: BLE001
            return False

    def hide(self) -> bool:
        if os.name != "nt" or not self.hwnd:
            return False
        try:
            win32().user32.ShowWindow(ctypes.c_void_p(self.hwnd), 0)  # SW_HIDE
            return True
        except Exception:  # noqa: BLE001
            return False

    def destroy(self) -> None:
        if os.name != "nt":
            return
        try:
            if self.hwnd:
                # WM_DESTROY 를 직접 post 하면 창 파괴 절차가 돌지 않는다.
                # WM_CLOSE → DefWindowProc → DestroyWindow 경로를 쓴다.
                win32().user32.PostMessageW(ctypes.c_void_p(self.hwnd), WM_CLOSE, 0, 0)
        except Exception:  # noqa: BLE001
            pass
        if self.bitmap is not None:
            self.bitmap.dispose()
            self.bitmap = None
        self.hwnd = None

    # -------------------------------------------------- 내부
    def _blit(self) -> bool:
        from ctypes import wintypes

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        libs = win32()
        user32 = libs.user32
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        size = SIZE(self._size[0], self._size[1])
        src = POINT(0, 0)
        rect = wintypes.RECT()
        user32.GetWindowRect(ctypes.c_void_p(self.hwnd), ctypes.byref(rect))
        dst = POINT(rect.left, rect.top)
        ok = user32.UpdateLayeredWindow(
            ctypes.c_void_p(self.hwnd), None, ctypes.byref(dst), ctypes.byref(size),
            self.bitmap.hdc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
        if not ok:
            code = libs.last_error()
            self.error = f"UpdateLayeredWindow 실패 · {libs.error_text(code)}"
            log.warning("overlay blit failed · hwnd=%s size=%s · %s",
                        self.hwnd, self._size, libs.error_text(code))
        return bool(ok)

    def _run(self) -> None:
        try:
            from ctypes import wintypes

            libs = win32()
            user32 = libs.user32
            kernel32 = libs.kernel32
            hinstance = kernel32.GetModuleHandleW(None)
            if not hinstance:
                code = libs.last_error()
                self.error = f"GetModuleHandleW 실패 · {libs.error_text(code)}"
                log.error(self.error)
                self._ready.set()
                return
            class_name = f"SAAimOverlay_{self.kind}"

            @WNDPROC
            def wndproc(hwnd, msg, wparam, lparam):
                if msg == WM_NCHITTEST:
                    return HTTRANSPARENT
                if msg == WM_ERASEBKGND:
                    return 1
                if msg == WM_APP_RENDER:
                    # v2.0.7 — 다른 스레드가 요청한 그리기를 창 스레드에서 실행한다.
                    try:
                        spec = self._take_pending()
                        self._render_ok = bool(spec) and self._render_now(spec)
                    except Exception:  # noqa: BLE001 - 그리기 실패가 메시지 루프를 죽이면 안 된다
                        self._render_ok = False
                        log.debug("queued overlay render failed", exc_info=True)
                    finally:
                        self._render_done.set()
                    return 0
                if msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._wndproc = wndproc
            if class_name not in NativeOverlayWindow._class_registered:
                class WNDCLASSEXW(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                                ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                                ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
                                ("hbrBackground", ctypes.c_void_p), ("lpszMenuName", ctypes.c_wchar_p),
                                ("lpszClassName", ctypes.c_wchar_p), ("hIconSm", ctypes.c_void_p)]

                wc = WNDCLASSEXW()
                wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
                wc.style = CS_HREDRAW | CS_VREDRAW
                wc.lpfnWndProc = wndproc
                wc.hInstance = hinstance
                wc.lpszClassName = class_name
                atom = user32.RegisterClassExW(ctypes.byref(wc))
                if not atom:
                    code = libs.last_error()
                    self.error = f"RegisterClassExW 실패 · {libs.error_text(code)}"
                    log.error(self.error)
                    self._ready.set()
                    return
                # ATOM 을 캐시해 두고 재등록 없이 CreateWindowExW 에 바로 넘긴다.
                atom = int(atom)
                NativeOverlayWindow._class_atoms[class_name] = atom
                NativeOverlayWindow._class_wndprocs[class_name] = wndproc
                NativeOverlayWindow._class_registered.add(class_name)
            class_atom = NativeOverlayWindow._class_atoms.get(class_name) or 0
            class_atom_arg = ctypes.cast(ctypes.c_void_p(class_atom), wintypes.LPCWSTR)
            ex_style = (WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE
                        | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
            hwnd = user32.CreateWindowExW(
                ex_style, class_atom_arg, self.title, WS_POPUP,
                0, 0, self._size[0], self._size[1], None, None, hinstance, None)
            if not hwnd:
                code = libs.last_error()
                self.error = f"CreateWindowExW 실패 · {libs.error_text(code)}"
                log.error(self.error)
                self._ready.set()
                return
            self.hwnd = int(hwnd)
            self._thread_id = threading.get_ident()
            try:
                # 작업 표시줄·Alt+Tab 에도 남지 않게
                user32.SetWindowPos(ctypes.c_void_p(self.hwnd), ctypes.c_void_p(HWND_TOPMOST),
                                    0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            except Exception:  # noqa: BLE001
                pass
            self._ready.set()
            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:  # noqa: BLE001
            self.error = f"오버레이 창 생성 오류 · {exc}"
            log.debug("native overlay thread failed", exc_info=True)
            self._ready.set()
        finally:
            self._thread_id = None
            with self._spec_lock:
                self._pending_spec = None
            if self.bitmap is not None:
                self.bitmap.dispose()
                self.bitmap = None


class FakeWindow:
    """테스트용 — 실제 Win32 없이 show/update 호출만 기록한다."""

    def __init__(self, kind: str, title: str):
        self.kind = kind
        self.title = title
        self.specs: list[dict[str, Any]] = []
        self.created = False
        self.visible = False
        self.destroyed = False

    def create(self, rect: dict[str, int], spec: dict[str, Any]) -> bool:
        self.created = True
        self.specs.append(spec)
        return True

    def update(self, spec: dict[str, Any]) -> bool:
        self.specs.append(spec)
        return True

    def show(self, rect: dict[str, int] | None = None) -> bool:
        self.visible = True
        return True

    def hide(self) -> bool:
        self.visible = False
        return True

    def destroy(self) -> None:
        self.destroyed = True
        self.visible = False


def make_window(kind: str, title: str, factory: Callable[[str, str], Any] | None = None) -> Any:
    if factory is not None:
        return factory(kind, title)
    return NativeOverlayWindow(kind, title)


def smoke_spec(kind: str) -> dict[str, Any]:
    """리눅스에서도 만들 수 있는 최소 스펙 (창 없이 좌표만 확인)."""
    payload = {"win": True, "map": "제3보급창고", "sub": "딜량 1,840 · K/D 2.33 · 3연승"}
    return build_spec(kind, payload, 372, 96)
