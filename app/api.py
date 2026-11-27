"""The JavaScript ↔ Python bridge exposed to the UI as ``window.pywebview.api``.

Every public method returns plain JSON-serialisable data.  Errors are returned as
``{"ok": False, "message": ...}`` instead of raising, so the UI can show a toast
without losing the page.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import platform
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from . import auth as auth_mod, barracks as barracks_mod, capture as capture_mod, insights, nexon, nvidia, overlay, party_lobby, paths, scouting, supabase_sync, tilt, toast, tray, updates, v140, v150, winutil
from . import hotkeys as hotkeys_mod
from .store import Store, now_iso, scrub_secrets as store_scrub, today_str

# v1.0.1 (#12) — 내 전적 자동 최신화 주기 = 1분 5초
MATCH_REFRESH_SECONDS = 65

log = logging.getLogger("api")

# pywebview builds ``window.pywebview.api`` by walking *every public attribute*
# of the js_api object (``webview.util.inject_pywebview`` → ``get_functions``)
# and recursing into anything that is not callable.  A ``pywebview.Window``
# stored on the instance made that walk read ``window.x`` / ``window.width``
# properties, which block up to 15 s each on ``events.shown`` and marshal into
# the WinForms thread — that alone delayed ``pywebviewready`` past the UI's boot
# timer and left the app on its sample (preview) data.  The window reference now
# lives in this module-level slot: no public attribute is walked.
_WINDOW_REF: Any = None
_TRAY_REF: Any = None


def window_ref() -> Any:
    return _WINDOW_REF


def attach_tray(tray_icon: Any) -> None:
    """main.py 가 만든 트레이 아이콘을 API 가 쓸 수 있게 넘긴다."""
    global _TRAY_REF
    _TRAY_REF = tray_icon


def tray_ref() -> Any:
    return _TRAY_REF


_GUARD_REF: Any = None
_CLOSE_ALLOWED = False


def attach_guard(guard: Any) -> None:
    """창 크기 감시자 — 드래그가 시작될 때 잠깐 멈추는 데 쓴다 (v1.3.0)."""
    global _GUARD_REF
    _GUARD_REF = guard


class Api:
    def __init__(self, store: Store | None = None, *, overlay_factory: Any = None):
        self.store = Store() if store is None else store
        # 회원 로그인·등급 — 테스트는 client_factory 로 가짜 Supabase 를 넣는다
        self.account = auth_mod.AccountService(self.store)
        self._drs_state_at = 0.0
        self._drs_state: dict[str, Any] | None = None
        self._perf_state_at = 0.0
        self._perf_state: dict[str, Any] | None = None
        self._busy: str | None = None
        self._last_progress = 0.0
        # v1.4.0 — 연패 브레이크 감시 (설정 ON + 서든 실행 중일 때만 동작)
        self._tilt: tilt.TiltWatcher | None = None
        self._tilt_lock = __import__("threading").Lock()
        # v2.0.0 — 클라우드 자동 백업 · 회원 등급
        self._cloud_stop = __import__("threading").Event()
        self._cloud_thread: Any = None
        # v1.5.0 — 모니터 해상도·OS 배율 (main.py 가 창을 만들기 전에 채운다)
        self._display: dict[str, Any] = {"width": 0, "height": 0, "scale": 1.0, "auto_scale": 100}
        # v1.0.0 — 인게임 오버레이 · 단축키 · 경기 종료 토스트 · 캐시 정리
        # v2.0.0 — pywebview 대신 Win32 순정 레이어드 창 (테스트는 window_factory 주입)
        self._overlay = overlay.OverlayHub(window_factory=overlay_factory)
        self._hotkeys = hotkeys_mod.HotkeyListener()
        self._match_watch: toast.MatchWatcher | None = None
        self._overlay_lock = __import__("threading").Lock()
        self._last_scan: list[dict[str, Any]] = []
        self._cache_cleanup: dict[str, Any] | None = None
        # v1.0.1 — 내 전적 65초 자동 최신화 상태
        self._match_refresh: dict[str, Any] = {"checked": False, "at": None, "newest": None,
                                               "updated": None, "error": None}
        self._match_interval = MATCH_REFRESH_SECONDS
        self._match_stop = __import__("threading").Event()
        self._match_thread: Any = None
        self._cleanup_cache_once()

    def set_display(self, width: int = 0, height: int = 0, scale: float = 1.0) -> dict[str, Any]:
        """모니터 작업 영역·OS 배율을 받아 QHD 자동 보정값을 정한다 (#1)."""
        auto = v150.auto_ui_scale(width, height, scale)
        self._display = {"width": int(width or 0), "height": int(height or 0),
                         "scale": float(scale or 1.0), "auto_scale": auto}
        return dict(self._display)

    set_display._serializable = False  # type: ignore[attr-defined]

    def effective_ui_scale(self) -> int:
        """화면에 적용할 배율 — 사용자가 직접 고른 값이 우선, 아니면 QHD 자동값."""
        raw = self.store.settings.get("ui_scale")
        auto = bool(self.store.settings.get("ui_scale_auto", True))
        if auto:
            return int(self._display.get("auto_scale") or v150.DEFAULT_UI_SCALE)
        return v150.clean_ui_scale(raw)

    effective_ui_scale._serializable = False  # type: ignore[attr-defined]

    def start_update_check(self) -> None:
        """앱 시작 시 백그라운드로 새 버전을 조용히 확인한다 (실패해도 무시)."""
        try:
            updates.start_background(paths.APP_VERSION)
        except Exception:  # noqa: BLE001 - 확인 실패는 앱 시작을 막지 않는다
            log.debug("update check could not start", exc_info=True)

    start_update_check._serializable = False  # type: ignore[attr-defined]

    # ------------------------------------------------------------ helpers
    def attach(self, window) -> None:
        """Keep the native window out of the instance dict (see _WINDOW_REF)."""
        global _WINDOW_REF
        _WINDOW_REF = window

    # JS 에서 호출될 이유가 없다 — pywebview 노출 목록에서도 뺀다.
    attach._serializable = False  # type: ignore[attr-defined]

    def attach_guard(self, guard) -> None:
        """창 크기 감시자를 연결한다 — drag_window 가 드래그 중 감시를 잠깐 멈춘다."""
        attach_guard(guard)

    attach_guard._serializable = False  # type: ignore[attr-defined]

    @property
    def _win(self):
        """The pywebview window.  The leading underscore keeps it out of the
        JS API export walk."""
        return _WINDOW_REF

    def _progress(self, done: int, total: int, label: str = "") -> None:
        now = time.monotonic()
        if done < total and now - self._last_progress < 0.25:
            return
        self._last_progress = now
        if not self._win:
            return
        try:
            self._win.evaluate_js(
                f"window.__alProgress && window.__alProgress({int(done)},{int(total)},{json.dumps(label, ensure_ascii=False)})"
            )
        except Exception:  # noqa: BLE001 - UI may be closing
            pass

    def _ok(self, **payload: Any) -> dict[str, Any]:
        return {"ok": True, **payload}

    def _err(self, exc: Exception | str, **payload: Any) -> dict[str, Any]:
        message = str(exc)
        return {"ok": False, "message": message, "quota": bool(getattr(exc, "quota", False)), **payload}

    # ------------------------------------------------------------ window controls
    def minimize_window(self) -> dict[str, Any]:
        if self._win:
            self._win.minimize()
        return self._ok()

    def toggle_maximize(self) -> dict[str, Any]:
        """v1.3.1 — 최대화는 없앴다 (1200×750 고정). UI 에서도 버튼을 제거했고,
        혹시 남아 있는 호출이 와도 창을 건드리지 않고 현재 상태만 돌려준다."""
        return self._ok(maximized=False, fixed=True,
                        message="이 앱은 1200×750 고정 크기로만 동작합니다")

    def close_window(self) -> dict[str, Any]:
        """완전 종료 — OS 닫기 이벤트가 이 선택을 막지 않게 허용 플래그를 먼저 세운다."""
        global _CLOSE_ALLOWED
        _CLOSE_ALLOWED = True
        try:
            self.shutdown("exit")
        except Exception:  # noqa: BLE001 - 종료를 막지 않는다
            log.debug("shutdown before close failed", exc_info=True)
        if self._win:
            self._win.destroy()
        return self._ok()

    def allow_close(self) -> bool:
        """main.py 의 WM_CLOSE / clossing 핸들러가 확인한다 (v1.5.0 · #23)."""
        return bool(_CLOSE_ALLOWED)

    allow_close._serializable = False  # type: ignore[attr-defined]

    def request_close(self) -> dict[str, Any]:
        """작업표시줄 ✕·Alt+F4 로 닫힐 때 화면에 같은 확인 팝업을 띄운다."""
        window = self._win
        if window is None:
            return self._err("창이 없습니다")
        try:
            window.evaluate_js("window.AL && AL.confirmClose ? AL.confirmClose() : null")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)
        return self._ok()

    request_close._serializable = False  # type: ignore[attr-defined]

    def lock_window_style(self) -> dict[str, Any]:
        """Win32 스타일에서 최대화·크기조절 비트를 지운다 (main.py 가 시작 시 1회)."""
        handle = self._hwnd()
        if not handle:
            return self._ok(supported=False)
        result = winutil.fixed_window_style(handle)
        log.info("window style locked · %s", result)
        return self._ok(**result)

    def start_window_drag(self) -> dict[str, Any]:
        """타이틀바 드래그 — 커서를 따라가는 이동 루프를 별도 스레드에서 돌린다.

        pywebview 의 JS API 호출은 백그라운드 스레드에서 실행되므로
        ``ReleaseCapture()+WM_NCLBUTTONDOWN`` 같은 모달 루프 방식은 실패할 수 있다.
        여기서는 GetCursorPos + GetAsyncKeyState(VK_LBUTTON) 로 마우스를 직접 읽어
        SetWindowPos(SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE) 로 창만 옮긴다 —
        DOM 이벤트가 끊겨도, IPC 가 느려도 창이 커서를 놓치지 않는다.
        """
        if os.name != "nt" or not self._win:
            return self._ok(supported=False)
        handle = self._hwnd()
        if not handle:
            return self._ok(supported=False)
        guard = _GUARD_REF
        if guard is not None and hasattr(guard, "pause"):
            guard.pause(2.5)
        started = winutil.start_drag_loop(handle)
        if not started.get("ok"):
            return {**started, "supported": False}
        return self._ok(supported=True, mode="win32-cursor")

    def move_window(self, dx: int = 0, dy: int = 0) -> dict[str, Any]:
        """JS mousemove 폴백 — 시작 지점 기준 (dx, dy) CSS px 만큼 창을 옮긴다."""
        if os.name != "nt" or not self._win:
            return self._ok(supported=False)
        handle = self._hwnd()
        if not handle:
            return self._ok(supported=False)
        return self._ok(**winutil.move_window_by(handle, int(dx or 0), int(dy or 0)))

    def end_window_drag(self) -> dict[str, Any]:
        winutil.end_drag_loop()
        return self._ok()

    def drag_window(self) -> dict[str, Any]:
        """(예전 경로) WM_NCLBUTTONDOWN 방식 — 네이티브 이동을 쓸 수 있는 환경용 폴백."""
        if os.name != "nt" or not self._win:
            return self._ok(supported=False)
        try:
            import ctypes

            handle = self._hwnd()
            if not handle:
                return self._ok(supported=False)
            guard = _GUARD_REF
            if guard is not None and hasattr(guard, "pause"):
                guard.pause(1.5)
            ctypes.windll.user32.ReleaseCapture()  # type: ignore[attr-defined]
            ctypes.windll.user32.SendMessageW(  # type: ignore[attr-defined]
                ctypes.c_void_p(handle), 0x00A1, 2, 0   # WM_NCLBUTTONDOWN, HTCAPTION
            )
            return self._ok(supported=True, mode="wm-nclbuttondown")
        except Exception as exc:  # noqa: BLE001 - drag is best effort
            return self._err(exc, supported=False)

    def _hwnd(self) -> int | None:
        native = getattr(self._win, "native", None)
        if native is not None:
            try:
                handle = getattr(native, "Handle", None)
                if handle is not None:
                    return int(handle.ToInt64() if hasattr(handle, "ToInt64") else handle)
                handle = getattr(native, "_hwnd", None)
                if handle:
                    return int(handle)
            except Exception:  # noqa: BLE001
                pass
        # 백엔드가 달라도 우리 창을 제목으로 찾아낸다 (pywebview 내부 구조 의존 제거)
        if os.name == "nt":
            try:
                import ctypes

                found = ctypes.windll.user32.FindWindowW(None, paths.APP_TITLE)  # type: ignore[attr-defined]
                return int(found) or None
            except Exception:  # noqa: BLE001
                return None
        return None

    def window_bounds(self) -> dict[str, Any]:
        """Position/size helper (DPR-safe centering check)."""
        if not self._win:
            return self._ok()
        try:
            return self._ok(x=self._win.x, y=self._win.y, width=self._win.width, height=self._win.height)
        except Exception:  # noqa: BLE001
            return self._ok()

    # ------------------------------------------------------------ state
    def bootstrap(self) -> dict[str, Any]:
        """First paint payload: everything the UI needs, minus the slow probes.

        The DRS state (tasklist + sha256 over two ~2 MB databases) and the mouse
        registry read are skipped here and fetched right after the window is
        painted through :meth:`get_nvidia_state`, which is what used to make the
        app stutter for a second on launch.

        Every step is logged with its duration: if the UI ever reports a failed
        boot, ``app.log`` shows exactly which stage broke.
        """
        started = time.perf_counter()
        log.info("bootstrap · start")
        t = time.perf_counter()
        try:
            store_ok = bool(self.store)
            log.info("bootstrap · store ready=%s data=%s (%.0f ms)",
                     store_ok, getattr(self.store, "path", "?"), (time.perf_counter() - t) * 1000)
        except Exception:  # noqa: BLE001 - logging must never break boot
            pass
        try:
            state = self._build_state(light=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("bootstrap · state failed after %.0f ms", (time.perf_counter() - started) * 1000)
            traceback.print_exc()
            return self._err(exc, stage="state", log_file=str(paths.log_dir() / "app.log"))
        took = (time.perf_counter() - started) * 1000
        log.info("bootstrap · done in %.0f ms · records=%s", took, state.get("record_count"))
        return self._ok(state=state, ms=round(took), log_file=str(paths.log_dir() / "app.log"))

    def bridge_ping(self) -> dict[str, Any]:
        """Tiny call the UI can use to prove the JS ↔ Python bridge is alive."""
        return {"ok": True, "pong": True, "version": paths.APP_VERSION,
                "log_file": str(paths.log_dir() / "app.log"),
                "data_file": str(getattr(self.store, "path", ""))}

    def get_state(self) -> dict[str, Any]:
        try:
            return self._ok(state=self._build_state())
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._err(exc)

    def _build_state(self, light: bool = False) -> dict[str, Any]:
        """화면 첫 페인트용 상태 — **서든어택 전적**만 담는다.

        v2.0.0 — 에임 연습(코박스·에임랩) 기록/추천/티어/잔디를 모두 걷어내고,
        홈에 필요한 랭크 · 컨디션 · 플레이 잔디 · 최근 5판 · 약점 맵 TOP3 ·
        오버레이/엔비디아 상태를 한 번에 계산한다.  ``light=True``(bootstrap)는
        DRS·마우스 레지스트리 같은 느린 확인을 건너뛴다.
        """
        store = self.store
        today = _dt.date.today()
        settings = store.public_settings()
        notes: list[str] = []

        # ── 병영수첩 · 순위표 ────────────────────────────────────────
        try:
            cache = store.barracks_cache()
            barracks = cache.get(settings.get("nickname") or "") or None
            if barracks is None and cache:
                barracks = list(cache.values())[-1]
            members = self._ensure_me(barracks)
        except Exception:  # noqa: BLE001 - 전적 캐시가 깨져도 화면은 그린다
            log.exception("state · barracks/leaderboard failed")
            barracks, members = None, []

        matches = list((barracks or {}).get("matches") or [])
        maps = list((barracks or {}).get("maps") or [])
        if isinstance(barracks, dict):
            # 첫 설치 직후에도 '직전 최신화일' 비교가 가능하도록 캐시 시각을 스냅샷으로 남긴다
            try:
                if not store.barracks_history():
                    stamp = str(barracks.get("updated_at") or "")[:10]
                    if stamp:
                        store.barracks_history().append({
                            "date": stamp, "at": str(barracks.get("updated_at") or ""),
                            "nickname": barracks.get("nickname"),
                            "stats": barracks.get("stats") or {},
                            "tier": barracks.get("tier") or {},
                            "basic": {"clan": (barracks.get("basic") or {}).get("clan")},
                        })
                        store.save()
            except Exception:  # noqa: BLE001
                log.debug("state · barracks snapshot seed failed", exc_info=True)
            try:
                barracks["play_coaching"] = insights.play_coaching(barracks)
                barracks["weak_maps"] = insights.weak_maps(maps, 3)
                barracks["form"] = insights.recent_form(matches, 8)
            except Exception:  # noqa: BLE001
                log.exception("state · play insights failed")
                barracks["play_coaching"] = []
                barracks["weak_maps"] = []
                barracks["form"] = insights.recent_form([], 8)
            try:
                store.merge_play_days(matches)
            except Exception:  # noqa: BLE001
                log.debug("state · play days merge failed", exc_info=True)

        # ── 랭크 · 컨디션 ───────────────────────────────────────────
        tier = (barracks or {}).get("tier") or {}
        solo_rp = int(((tier.get("solo") or {}).get("rp") or 0))
        party_rp = int(((tier.get("party") or {}).get("rp") or 0))
        solo_band = insights.band_of(solo_rp)
        party_band = insights.party_band_of(party_rp)
        # v2.0.1 — 아이콘은 RP 가 아니라 **API 티어 이름**을 먼저 따른다.
        #   (별레전드인데 RP 가 낮다고 그마 아이콘이 뜨던 문제)
        solo_name = (tier.get("solo") or {}).get("name") or solo_band["n"]
        party_name = (tier.get("party") or {}).get("name") or party_band["n"]
        rank = {
            "solo": {"rp": solo_rp, "name": solo_name,
                     "band": solo_band["n"], "img": insights.tier_img(solo_name, solo_rp),
                     "progress": insights.band_progress(solo_rp)},
            "party": {"rp": party_rp, "name": party_name,
                      "band": party_band["n"],
                      "img": insights.tier_img(party_name, party_rp, party=True),
                      "progress": insights.band_progress(party_rp, party=True)},
        }
        try:
            condition = v140.rank_condition(matches)
            stored = store.data.get("condition_history") or []
            if condition.get("available"):
                history = v140.condition_history(stored, condition, today=today.isoformat())
                if history != stored:
                    store.data["condition_history"] = history
                    store.save()
            else:
                history = stored
            condition["history"] = history[-14:]
        except Exception:  # noqa: BLE001
            log.exception("state · rank condition failed")
            condition = {"available": False, "verdict": None, "label": "전적 없음", "history": []}
        try:
            grass = insights.play_grass(matches, store.play_days(), weeks=12, today=today)
        except Exception:  # noqa: BLE001
            log.exception("state · play grass failed")
            grass = {"weeks": 12, "cells": [], "total_games": 0, "wins": 0, "active_days": 0}
        try:
            form = insights.recent_form(matches, 8)
        except Exception:  # noqa: BLE001
            form = insights.recent_form([], 8)
        weak_maps = insights.weak_maps(maps, 3)

        # ── 유틸(엔비디아 · 마우스) ──────────────────────────────────
        if light:
            utils_state = {
                "pending": True, "supported": os.name == "nt", "mode": None, "alias": False,
                "backups": [], "points": [], "modes": {}, "missing": [], "last_backup": None,
                "game_running": None, "writable": False, "drs_exists": False, "drs_dir": "",
                "error": None, "needs_admin": False, "accel": None,
                "stairfix": {"ready": False, "applied": False, "value": None, "label": "확인 중",
                             "target": "0x00401241", "verified": False},
                "last_backup_at": store.utils().get("last_backup_at"),
                "last_restore_at": store.utils().get("last_restore_at"),
            }
        else:
            utils_state = self._drs_summary()
            try:
                utils_state["accel"] = winutil.get_mouse_accel() if os.name == "nt" else store.utils().get("accel")
            except Exception:  # noqa: BLE001
                log.exception("state · mouse accel failed")
                utils_state["accel"] = None
            utils_state["last_backup_at"] = store.utils().get("last_backup_at")
            utils_state["last_restore_at"] = store.utils().get("last_restore_at")
        utils_state["last_changes"] = (store.utils().get("nvidia") or {}).get("last_changes")

        try:
            cloud_state = self._cloud_state()
        except Exception:  # noqa: BLE001
            log.exception("state · cloud failed")
            cloud_state = {"configured": False, "url": "", "email": None, "user_id": None,
                           "signed_in": False, "last_sync": None}
        try:
            account_state = self.account.state()
        except Exception:  # noqa: BLE001
            log.exception("state · account failed")
            account_state = {"signed_in": False, "email": "", "plan": {"plan": "free"},
                             "quota": {"enforce": False, "allowed": True}, "enforce": False}
        try:
            goals = self._goals(barracks)
        except Exception:  # noqa: BLE001
            log.exception("state · goals failed")
            goals = []
        try:
            digest = self._digest()
        except Exception:  # noqa: BLE001
            log.exception("state · digest failed")
            digest = {"available": False, "reason": "병영수첩 변화를 계산하지 못했습니다"}
        try:
            guides = insights.map_guides(maps)
        except Exception:  # noqa: BLE001
            log.exception("state · map guides failed")
            guides = []
        try:
            gate_state = updates.gate(updates.cached(), paths.APP_VERSION)
        except Exception:  # noqa: BLE001
            gate_state = {"blocked": False, "reason": "unknown"}

        # ── 화면 설정 ───────────────────────────────────────────────
        theme_key = v150.clean_theme(settings.get("theme"))
        ui_scale = self.effective_ui_scale()
        try:
            deltas = v150.delta_badges(store.barracks_history())
        except Exception:  # noqa: BLE001
            deltas = []
        links = self._barracks_links(barracks)
        overlay_settings = self._overlay_settings()
        overlay_state = {**self._overlay.state(), **{
            "enabled": overlay_settings["enabled"],
            "hotkey_lobby": overlay_settings["hotkey_lobby"],
            "hotkey_scout": overlay_settings["hotkey_scout"],
            "toast_enabled": overlay_settings["toast_enabled"],
            "toast_position": overlay_settings["toast_position"],
        }}

        return {
            "meta": {
                "version": paths.APP_VERSION,
                "platform": platform.system(),
                "platform_raw": os.name,
                "frozen": paths.is_frozen(),
                "portable": paths.portable_mode(),
                "data_dir": str(paths.data_dir()),
                "data_file": str(store.path),
                "is_admin": winutil.is_admin(),
                "steam": winutil.steam_installed(),
                "load_error": store.load_error,
                "now": now_iso(),
                "today": today_str(),
                "log_file": str(paths.log_dir() / "app.log"),
                "bridge": "pywebview",
                "mode": "suddenattack",
            },
            "settings": settings,
            "nexon": {
                "connected": bool((store.settings.get("api_key") or "").strip()),
                "nickname": store.settings.get("nickname") or "",
                "last_barracks_at": (barracks or {}).get("updated_at"),
                "proxy": nexon.PROXY_URL,
                "endpoint": "proxy" if not (store.settings.get("api_key") or "").strip() else "direct",
                "ready": True,
                "key_required": False,
            },
            "barracks": barracks,
            "leaderboard": nexon.leaderboard_payload(members),
            "home": {
                "rank": rank,
                "condition": condition,
                "grass": grass,
                "recent": matches[:5],
                "weak_maps": weak_maps,
                "form": form,
                "overlay": overlay_state,
                "stairfix": utils_state.get("stairfix"),
                "accel": utils_state.get("accel"),
                "account": account_state,
                "nickname": (barracks or {}).get("nickname") or (store.settings.get("nickname") or ""),
                "updated_at": (barracks or {}).get("updated_at"),
            },
            "play_grass": grass,
            "weak_maps": weak_maps,
            "map_index": v150.map_index_payload(),
            "memos": dict(store.memos()),
            "utils": utils_state,
            "cloud": cloud_state,
            "account": account_state,
            "auth": {"signed_in": account_state.get("signed_in"),
                     "email": account_state.get("email"), "plan": account_state.get("plan")},
            "goals": goals,
            "update": updates.cached(),
            "tray": self._tray_state(),
            "digest": digest,
            "map_guides": guides,
            "condition": condition,
            "condition_history": condition.get("history") or [],
            "onboarded": bool(settings.get("onboarded")),
            "onboarding": {
                "needed": (not bool(settings.get("onboarded"))
                           and not settings.get("nickname")
                           and not bool((cloud_state or {}).get("signed_in"))),
                "has_local": bool(settings.get("nickname")),
                "cloud_signed_in": bool((cloud_state or {}).get("signed_in")),
            },
            "cloud_config": {"url": (settings.get("supabase") or {}).get("url", ""),
                             "email": (cloud_state or {}).get("email")},
            "update_gate": gate_state,
            "tilt": {"enabled": bool(settings.get("tilt_watch", True)),
                     "interval": int(settings.get("tilt_interval") or tilt.DEFAULT_INTERVAL),
                     "supported": os.name == "nt"},
            "v2": True,
            "theme": theme_key,
            "themes": [dict(t) for t in v150.THEMES.values()],
            "ui_scale": ui_scale,
            "ui_scale_setting": v150.clean_ui_scale(settings.get("ui_scale")),
            "ui_scale_auto": bool(settings.get("ui_scale_auto", True)),
            "display": dict(self._display),
            "ui_scales": list(v150.UI_SCALES),
            "delta": deltas,
            "barracks_links": links,
            "cloud_auto": {**store.cloud_auto(), "enabled": bool(settings.get("auto_cloud", True))},
            "match_refresh": {"interval": self._match_interval, **self._match_refresh},
            "recency": {"newest_match": (barracks or {}).get("newest_match") or ""},
            "overlay": {**overlay_state, "settings": overlay_settings,
                        "hotkeys": self._hotkeys.state(),
                        "last_scan": list(store.overlay_cache().get("last_scan") or [])[:5],
                        "last_scan_at": store.overlay_cache().get("last_scan_at")},
            "party_presets": party_lobby.preset_payload(store.party_presets()),
            "attribution": "Data based on NEXON Open API",
            "cache_cleanup": self._cache_cleanup or {"ok": True, "skipped": True},
            "guest": not bool((store.settings.get("nickname") or "").strip()) and not bool(barracks),
            "notes": notes,
        }

    # ------------------------------------------------------------ v1.3.0 helpers
    def _digest(self) -> dict[str, Any]:
        """오늘 첫 실행용 직전 연습일 대비 병영수첩 변화 + 한마디 총고."""
        digest = insights.barracks_digest(self.store.barracks_history())
        ack = (self.store.ui() or {}).get("digest_ack")
        digest["ack"] = ack == today_str()
        digest["show"] = bool(digest.get("available")) and digest["ack"] is False
        digest["today"] = today_str()
        return digest

    def ack_digest(self) -> dict[str, Any]:
        """오늘의 병영수첩 변화 팝업을 닫았다고 기록 (하루 한 번만 자동 표시)."""
        ui = self.store.ui()
        ui["digest_ack"] = today_str()
        self.store.save()
        return self._ok(state=self._build_state())

    def _goals(self, barracks: dict[str, Any] | None) -> list[dict[str, Any]]:
        g = self.store.settings.get("goals") or {}
        stats = (barracks or {}).get("stats") or {}
        tier = (barracks or {}).get("tier") or {}
        solo_rp = ((tier.get("solo") or {}).get("rp") or 0)
        rows = [
            ("현재 승률", float(stats.get("win") or 0), float(g.get("win") or 55), "%"),
            ("현재 K/D", float(stats.get("kd") or 0), float(g.get("kd") or 60), "%"),
            ("현재 평균 딜량", float(stats.get("dmg") or 0), float(g.get("dmg") or 1400), ""),
            ("현재 솔로 RP", float(solo_rp), float(g.get("rp") or 4000), ""),
        ]
        out = []
        for label, cur, tgt, unit in rows:
            gap = cur - tgt
            target_txt = f"{tgt:,.1f}".rstrip("0").rstrip(".") if unit == "%" else f"{tgt:,.0f}"
            out.append({"label": label, "target": f"(목표 {target_txt}{unit})",
                        "cur": round(cur, 1), "tgt": tgt, "unit": unit,
                        "gap": round(gap, 1), "gaptxt": f"{abs(gap):.1f}".rstrip("0").rstrip(".")})
        return out

    def _cloud_state(self) -> dict[str, Any]:
        cfg = supabase_sync.load_config()
        cloud = self.store.cloud()
        s = self.store.settings.get("supabase") or {}
        return {
            "configured": bool((s.get("url") or cfg["url"]) and (s.get("anon_key") or cfg["anon_key"])),
            "url": s.get("url") or cfg["url"],
            "email": cloud.get("email"),
            "user_id": cloud.get("user_id"),
            "signed_in": bool(cloud.get("access_token") or cloud.get("refresh_token")),
            "last_sync": cloud.get("last_sync"),
        }

    def _ensure_me(self, barracks: dict[str, Any] | None) -> list[dict[str, Any]]:
        members = [dict(m) for m in self.store.leaderboard()]
        name = ((barracks or {}).get("nickname") or self.store.settings.get("nickname") or "").strip()
        me = next((m for m in members if m.get("me")), None)
        if name:
            if me is None:
                me = {"name": name, "me": True, "ouid": (barracks or {}).get("ouid"),
                      "clan": ((barracks or {}).get("basic") or {}).get("clan") or "리드",
                      "weapon": "sniper", "solo": 0, "party": None, "kd": None, "dmg": None, "win": None}
                members.insert(0, me)
            else:
                me["name"] = name
                if (barracks or {}).get("ouid"):
                    me["ouid"] = barracks["ouid"]
            if barracks:
                st = barracks.get("stats") or {}
                tier = barracks.get("tier") or {}
                me.update({
                    "clan": (barracks.get("basic") or {}).get("clan") or me.get("clan"),
                    "weapon": "sniper" if float(((barracks.get("recent") or {}).get("sniper") or 0)) >=
                    float(((barracks.get("recent") or {}).get("assault") or 0)) else "rifle",
                    "solo": int((tier.get("solo") or {}).get("rp") or 0),
                    "party": int((tier.get("party") or {}).get("rp") or 0) or None,
                    "kd": st.get("kd"), "dmg": st.get("dmg"), "win": st.get("win"),
                    "solo_tier": (tier.get("solo") or {}).get("name"),
                    "party_tier": (tier.get("party") or {}).get("name"),
                    "updated_at": barracks.get("updated_at"),
                })
        # de-duplicate by nickname, ME first
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for m in members:
            key = str(m.get("name", "")).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(m)
        return out[:20]

    # ------------------------------------------------------------ settings
    _MASK_CHARS = ("\u2022", "*", "…")

    @classmethod
    def _looks_masked(cls, value: str) -> bool:
        """Never let the masked preview string overwrite the real secret."""
        text = (value or "").strip()
        return bool(text) and any(ch in text for ch in cls._MASK_CHARS)

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {"nickname", "auto_sync", "notify", "hotkey_enabled", "goals", "cloud_email",
                   "tilt_watch", "tilt_interval", "onboarded", "auto_cloud",
                   "theme", "ui_scale", "ui_scale_auto",
                   "overlay_enabled", "overlay_hotkey_lobby", "overlay_hotkey_scout",
                   "overlay_mapban_seconds", "overlay_party_seconds", "overlay_scan_x",
                   "overlay_scan_y", "overlay_scan_scale", "toast_enabled", "toast_position", "toast_auto"}
        clean: dict[str, Any] = {}
        for key, value in (patch or {}).items():
            if key not in allowed:
                continue
            if key == "nickname":
                value = str(value).strip()[:12]
            elif key == "api_key":
                value = str(value).strip()
                if not value or self._looks_masked(value):
                    continue      # 화면에 보이는 마스킹 문자열은 저장하지 않는다
            elif key == "goals" and isinstance(value, dict):
                value = {k: float(v) for k, v in value.items() if k in ("win", "kd", "dmg", "rp") and str(v).strip() != ""}
            elif key == "theme":
                value = v150.clean_theme(value)
            elif key == "ui_scale":
                value = v150.clean_ui_scale(value)
                clean["ui_scale_auto"] = False
            elif key == "ui_scale_auto":
                value = bool(value)
            elif key in ("overlay_hotkey_lobby", "overlay_hotkey_scout"):
                value = overlay.clean_hotkey(value, "Insert" if key.endswith("lobby") else "Home")
            elif key in ("overlay_mapban_seconds", "overlay_party_seconds"):
                value = overlay.parse_seconds(value, 12.0 if "mapban" in key else 15.0)
            elif key in ("overlay_scan_x", "overlay_scan_y"):
                try:
                    value = max(-600, min(600, int(float(value))))
                except (TypeError, ValueError):
                    continue
            elif key == "overlay_scan_scale":
                try:
                    value = max(0.7, min(1.4, round(float(value), 2)))
                except (TypeError, ValueError):
                    continue
            elif key == "toast_position":
                value = value if value in overlay.TOAST_POSITIONS else "br"
            elif isinstance(value, bool):
                value = bool(value)
            clean[key] = value
        if "tilt_watch" in clean:
            try:
                watcher = self._tilt_watcher()
                if clean.get("tilt_watch"):
                    watcher.start()
                else:
                    watcher.stop()
            except Exception:  # noqa: BLE001 - 감시 토글이 저장을 막지 않는다
                log.debug("tilt watcher toggle failed", exc_info=True)
        self.store.update_settings(clean)
        if "hotkey_enabled" in clean:
            self._apply_hotkey(bool(clean["hotkey_enabled"]))
        if any(key.startswith("overlay_") or key.startswith("toast_") for key in clean):
            try:
                self.apply_overlay_hotkeys()
            except Exception:  # noqa: BLE001
                log.debug("overlay hotkeys could not apply", exc_info=True)
            try:
                set_ = self.store.settings
                if set_.get("toast_enabled", True) and set_.get("toast_auto", True):
                    self._watcher().start()
                else:
                    self._watcher().stop()
            except Exception:  # noqa: BLE001
                log.debug("match watcher toggle failed", exc_info=True)
        return self.get_state()

    def save_cloud_config(self, url: str, anon_key: str) -> dict[str, Any]:
        self.store.update_settings({"supabase": {"url": (url or "").strip(), "anon_key": (anon_key or "").strip()}})
        if (url or "").strip() and (anon_key or "").strip():
            try:
                supabase_sync.save_config(url, anon_key)
            except OSError:
                pass
        probe = supabase_sync.probe((url or "").strip(), (anon_key or "").strip()) if url else \
            {"ok": False, "configured": False, "message": "Supabase URL 이 비어 있습니다"}
        return {"ok": True, "probe": probe, "state": self._build_state()}

    def _queue_cloud_backup(self, reason: str = "training") -> dict[str, Any]:
        """저장 직후 백업 — 3초 디바운스로 백그라운드에서 올린다 (화면을 막지 않는다)."""
        if not self.store.settings.get("auto_cloud", True):
            return {"ok": False, "skipped": "자동 백업이 꺼져 있습니다"}
        state = self._cloud_state()
        if not state.get("signed_in") and not (self.store.cloud().get("refresh_token")):
            return {"ok": False, "skipped": "클라우드 로그인이 필요합니다"}
        import threading as _threading

        lock = getattr(self, "_cloud_push_lock", None)
        if lock is None:
            lock = self._cloud_push_lock = _threading.Lock()

        def _run() -> None:
            time.sleep(3.0)
            with lock:
                try:
                    self.cloud_auto_sync(reason)
                except Exception:  # noqa: BLE001
                    log.debug("cloud backup after save failed", exc_info=True)

        _threading.Thread(target=_run, daemon=True, name="sa-cloud-save").start()
        return {"ok": True, "queued": True, "reason": reason}

    _queue_cloud_backup._serializable = False  # type: ignore[attr-defined]

    def open_external(self, url: str) -> dict[str, Any]:
        return winutil.open_target(url)

    def open_data_folder(self) -> dict[str, Any]:
        return winutil.open_folder(str(paths.data_dir()))

    # ------------------------------------------------------------ NEXON
    def _client(self, api_key: str | None = None, *, force: bool = False) -> nexon.NexonClient:
        """개인 키가 있으면 직접, 없으면 공용 프록시로.  앱 코드에는 키가 없다."""
        key = (api_key if api_key is not None else self.store.settings.get("api_key") or "").strip()
        if key and self._looks_masked(key):
            key = ""
        if api_key:
            self.store.update_settings({"api_key": key})
        return nexon.NexonClient(key, progress=self._progress, force=force)

    def _resolve_inputs(self, nickname: str | None, api_key: str | None) -> tuple[str, str | None]:
        """화면 입력값을 우선 쓰고, 저장된 값으로 채운 뒤 함께 저장한다."""
        nick = (nickname or "").strip()[:12]
        key = (api_key or "").strip()
        if key and self._looks_masked(key):
            key = ""
        saved_nick = (self.store.settings.get("nickname") or "").strip()
        patch: dict[str, Any] = {}
        if nick and nick != saved_nick:
            patch["nickname"] = nick
        if key:
            patch["api_key"] = key
            self.store.update_settings({"api_key": key})
        elif patch:
            self.store.update_settings(patch)
        if not nick:
            nick = saved_nick
        if not nick:
            raise ValueError("서든어택 닉네임을 입력해 주세요")
        return nick, key or None

    def fetch_barracks(self, nickname: str | None = None, api_key: str | None = None) -> dict[str, Any]:
        """내 병영수첩 최신화 — 화면에 입력된 닉네임/키도 그대로 받아 쓴다."""
        try:
            nick, key = self._resolve_inputs(nickname, api_key)
            client = self._client(key, force=True)
            barracks = nexon.build_barracks(client, nick, progress=self._progress)
            self.store.update_settings({"nickname": barracks["nickname"]})
            self.store.barracks_cache()[barracks["nickname"]] = barracks
            self.store.add_barracks_snapshot(barracks)
            try:
                self.store.merge_play_days(barracks.get("matches") or [])
            except Exception:  # noqa: BLE001
                log.debug("play days merge failed after fetch", exc_info=True)
            self.store.save()
            member = self._member_from_barracks(barracks)
            self._upsert_member(member)
            # v1.5.0 — 병영수첩 실제 주소(user_nexon_sn·클랜 ID·프로필 사진)도 함께 연결.
            # 사이트가 막혀 있으면 조용히 넘어간다 (최신화 자체는 성공).
            try:
                if not (self.store.barracks_ids().get(str(barracks["nickname"]).lower()) or {}).get("sn"):
                    self._barracks_resolver().lookup(barracks["nickname"])
            except Exception:  # noqa: BLE001
                log.debug("barracks link resolve skipped", exc_info=True)
            # v1.0.1 — 닉네임을 늦게 연결해도 65초 자동 최신화가 돌게 한다 (이미 돌고 있으면 무시)
            try:
                self.start_match_auto_refresh()
            except Exception:  # noqa: BLE001
                log.debug("match auto refresh not started", exc_info=True)
            return {"ok": True, "barracks": barracks, "state": self._build_state(),
                    "calls": client.calls, "cache": client.cache_stats(),
                    "message": f"{barracks['nickname']} 최신화 완료"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def search_user(self, nickname: str, api_key: str | None = None) -> dict[str, Any]:
        """닉네임 검색 1단계 — 가볍게 존재 여부와 기본 정보만 확인한다."""
        try:
            # v1.4.0 — 닉네임 · ouid · 병영수첩 URL 모두 허용
            nick = v140.normalize_target(nickname)
            if not nick:
                raise ValueError("검색할 서든어택 닉네임을 입력해 주세요")
            key = (api_key or "").strip()
            if key and self._looks_masked(key):
                key = ""
            client = self._client(key or None, force=True)
            try:
                ouid = nick if v140.is_ouid(nick) else client.resolve_ouid(nick)
            except nexon.NexonError as exc:
                if getattr(exc, "quota", False) or "한도" in str(exc):
                    return self._err(exc)
                return {"ok": True, "found": False, "query": nick,
                        "message": f"'{nick}' 조회 결과 없음"}
            basic = client.user_basic(ouid)
            rank = client.user_rank(ouid)
            tier = client.user_tier(ouid)
            solo = int(tier.get("solo_rank_match_score") or 0)
            party = int(tier.get("party_rank_match_score") or 0)
            return {
                "ok": True, "found": True, "query": nick,
                "user": {
                    "name": basic.get("user_name") or nick,
                    "ouid": ouid,
                    "clan": basic.get("clan_name") or "-",
                    "created": str(basic.get("user_date_create") or "")[:10].replace("-", "."),
                    "manner": basic.get("manner_grade") or "-",
                    "grade": rank.get("grade") or "-",
                    "grade_ranking": rank.get("grade_ranking"),
                    "solo": solo,
                    "party": party,
                    "solo_tier": nexon.kr_tier(tier.get("solo_rank_match_tier")),
                    "party_tier": nexon.kr_tier(tier.get("party_rank_match_tier")),
                },
            }
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def lookup_barracks(self, nickname: str | None = None, ouid: str | None = None,
                        api_key: str | None = None) -> dict[str, Any]:
        """다른 유저 병영수첩 — 내 data.json 은 건드리지 않는 임시 조회."""
        try:
            nick = (nickname or "").strip()[:12]
            key = (api_key or "").strip()
            if key and self._looks_masked(key):
                key = ""
            if not ouid and not nick:
                raise ValueError("조회할 닉네임이 없습니다")
            client = self._client(key or None, force=True)
            if ouid:
                basic = client.user_basic(str(ouid).strip())
                nick = str(basic.get("user_name") or "").strip() or nick
            if not nick:
                raise ValueError("해당 ouid 의 닉네임을 찾지 못했습니다")
            barracks = nexon.build_barracks(client, nick, progress=self._progress)
            return {"ok": True, "barracks": barracks, "peek": True, "calls": client.calls}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def refresh_leaderboard_names(self, force: bool = False) -> dict[str, Any]:
        """순위표 멤버의 **현재 닉네임**만 ouid 로 다시 확인해 목록·메모를 최신화한다.

        v1.0.1 (#9) — 닉네임을 바꾼 친구가 예전 이름으로 남아 있던 문제.
        ouid 가 저장된 멤버는 ``user/basic`` 만 읽어(30분 캐시) 이름·클랜을 갱신하고,
        메모는 바뀐 이름으로 옮긴다.  ouid 가 없으면 닉네임으로 다시 찾는다.
        """
        try:
            members = self._ensure_me(None)
            if not members:
                return {"ok": True, "renamed": [], "message": "순위표가 비어 있습니다"}
            client = self._client(force=force)
            renamed: list[dict[str, Any]] = []
            memos = self.store.memos()
            changed = False
            for i, member in enumerate(members):
                ouid = str(member.get("ouid") or "").strip()
                old = str(member.get("name") or "").strip()
                if not ouid and old:
                    try:
                        ouid = client.resolve_ouid(old)
                        member["ouid"] = ouid
                    except nexon.NexonError:
                        continue
                if not ouid:
                    continue
                try:
                    basic = client.user_basic(ouid)
                except nexon.NexonError:
                    continue
                now_name = str(basic.get("user_name") or "").strip()
                if not now_name or now_name == old:
                    continue
                member["name"] = now_name
                member["renamed_from"] = old
                if basic.get("clan_name"):
                    member["clan"] = basic.get("clan_name")
                members[i] = member
                changed = True
                renamed.append({"from": old, "to": now_name, "ouid": ouid})
                if old in memos:
                    memos[now_name] = memos.pop(old)
                barr = self.store.barracks_cache().pop(old, None)
                if isinstance(barr, dict):
                    barr["nickname"] = now_name
                    self.store.barracks_cache()[now_name] = barr
            if changed:
                self.store.set_leaderboard(members[:20])
                self.store.save()
                self._push_state()
            return {"ok": True, "renamed": renamed, "state": self._build_state(),
                    "message": ("닉네임 변경 " + ", ".join(f"{r['from']} → {r['to']}" for r in renamed[:3]))
                               if renamed else "순위표 닉네임이 최신입니다"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, renamed=[])

    def fetch_leaderboard(self, nicknames: list[str] | None = None, api_key: str | None = None,
                          depth: int | None = None) -> dict[str, Any]:
        try:
            client = self._client(api_key, force=True)
            members = self._ensure_me(None)
            if nicknames:
                wanted = {str(n).strip().lower() for n in nicknames if str(n).strip()}
                members = [m for m in members if str(m.get("name", "")).strip().lower() in wanted] or members
            if not members:
                raise ValueError("순위표에 등록된 닉네임이 없습니다")
            rows = nexon.build_leaderboard(client, members[:20], progress=self._progress,
                                           depth=nexon.DETAIL_DEPTH if depth is None else int(depth))
            renamed = [r for r in rows if r.get("renamed_from")]
            self.store.set_leaderboard([{**r} for r in rows[:20]])
            message = f"{len(rows)}명 전적 일괄 갱신 완료"
            if renamed:
                message += " · 닉변 " + ", ".join(f"{r['renamed_from']} → {r['name']}" for r in renamed[:3])
            return {"ok": True, "members": rows, "renamed": renamed, "message": message,
                    "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def add_leaderboard_member(self, nickname: str, depth: int = 0, ouid: str | None = None) -> dict[str, Any]:
        try:
            # v1.4.0 — 병영수첩 URL/ouid 를 붙여넣어도 그대로 등록
            name = v140.normalize_target(nickname)
            ouid = str(ouid or "").strip() or None
            if v140.is_ouid(name):
                ouid = ouid or name
                name = ""
            if not name and not ouid:
                raise ValueError("닉네임을 입력해 주세요")
            members = self._ensure_me(None)
            if len(members) >= 20:
                raise ValueError("20명이 가득 찼습니다 · 한 명을 삭제한 뒤 등록해 주세요")
            if any((m.get("ouid") and m.get("ouid") == ouid) or str(m.get("name", "")).lower() == name.lower()
                   for m in members):
                raise ValueError("이미 등록된 닉네임입니다")
            client = self._client(force=True)
            row = nexon.fetch_member(client, name, ouid=ouid, depth=int(depth), progress=self._progress)
            if not row.get("ok"):
                raise ValueError(row.get("error") or "전적을 불러오지 못했습니다")
            members.append(row)
            self.store.set_leaderboard(members)
            return {"ok": True, "member": row, "state": self._build_state(),
                    "message": f"'{row['name']}' 순위표에 추가했습니다"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def remove_leaderboard_member(self, nickname: str) -> dict[str, Any]:
        members = self._ensure_me(None)
        removed = [m for m in members if str(m.get("name", "")).lower() == str(nickname).lower() and not m.get("me")]
        if not removed:
            return self._err("삭제할 수 없는 항목입니다 (본인 행은 고정)")
        self.store.set_leaderboard([m for m in members if m not in removed])
        return {"ok": True, "state": self._build_state(), "message": f"'{nickname}' 삭제됨"}

    def set_leaderboard_order(self, names: list[str]) -> dict[str, Any]:
        members = self._ensure_me(None)
        index = {str(n).lower(): i for i, n in enumerate(names)}
        members.sort(key=lambda m: index.get(str(m.get("name", "")).lower(), 99))
        self.store.set_leaderboard(members)
        return {"ok": True, "state": self._build_state()}

    def _member_from_barracks(self, barracks: dict[str, Any]) -> dict[str, Any]:
        st = barracks.get("stats") or {}
        tier = barracks.get("tier") or {}
        rec = barracks.get("recent") or {}
        return {
            "name": barracks["nickname"], "ouid": barracks.get("ouid"), "me": True,
            "clan": (barracks.get("basic") or {}).get("clan") or "리드",
            "weapon": "sniper" if float(rec.get("sniper") or 0) >= float(rec.get("assault") or 0) else "rifle",
            "solo": int((tier.get("solo") or {}).get("rp") or 0),
            "party": int((tier.get("party") or {}).get("rp") or 0) or None,
            "kd": st.get("kd"), "dmg": st.get("dmg"), "win": st.get("win"),
            "solo_tier": (tier.get("solo") or {}).get("name"),
            "party_tier": (tier.get("party") or {}).get("name"),
            "updated_at": barracks.get("updated_at"),
        }

    def _upsert_member(self, member: dict[str, Any]) -> None:
        """내 행은 ouid/닉네임이 같으면 갱신, 아니면 맨 앞에 넣는다."""
        members = self._ensure_me(None)
        for i, m in enumerate(members):
            same_ouid = bool(member.get("ouid")) and m.get("ouid") == member.get("ouid")
            same_name = str(m.get("name", "")).lower() == str(member.get("name", "")).lower()
            if same_ouid or same_name or m.get("me"):
                members[i] = {**m, **member, "me": True}
                break
        else:
            members.insert(0, {**member, "me": True})
        self.store.set_leaderboard(members[:20])

    # ------------------------------------------------------------ v1.3.1 · 메모 · 폴더
    def save_member_memo(self, nickname: str, text: str) -> dict[str, Any]:
        """친구 순위표 우클릭 메모 저장 — 닉네임 옆 아이콘으로 다시 본다."""
        try:
            self.store.set_memo(nickname, text)
            body = str(text or "").strip()
            return self._ok(state=self._build_state(), memos=dict(self.store.memos()),
                            message=(f"메모를 저장했습니다 · {nickname}" if body else f"메모를 지웠습니다 · {nickname}"))
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def open_barracks_page(self, ouid: str | None = None, nickname: str | None = None) -> dict[str, Any]:
        """서든 병영수첩 홈페이지를 기본 브라우저로 연다 (v1.5.0 · user_nexon_sn 사용)."""
        nick = "" if str(nickname or "").strip() in ("내", "me", "ME") else str(nickname or "").strip()
        nick = nick or (self.store.settings.get("nickname") or "").strip()
        target = str(ouid or "").strip()
        sn = barracks_mod.parse_sn(target) or barracks_mod.parse_sn(nick)
        if not sn and nick:
            sn = str((self.store.barracks_ids().get(nick.lower()) or {}).get("sn") or "")
        if not sn and target and not barracks_mod.parse_sn(target):
            # ouid(32자 hex)로는 병영수첩 주소를 만들 수 없다 — 저장된 링크만 쓴다
            cached = self.store.barracks_cache().get(nick, {})
            real = str((cached or {}).get("nickname") or nick)
            sn = str((self.store.barracks_ids().get(real.lower()) or {}).get("sn") or "")
        if not sn:
            lookup = self._barracks_resolver().lookup(nick) if nick else {}
            sn = str((lookup or {}).get("sn") or "")
        if not sn:
            # 마지막 수단 — 병영수첩 검색 페이지 (엉뚱한 주소보다 낫다)
            url = barracks_mod.search_page()
            result = winutil.open_target(url)
            result.setdefault("url", url)
            result.setdefault("message", "병영수첩 주소를 찾지 못해 검색 페이지를 열었습니다")
            return result
        url = barracks_mod.profile_url(sn)
        result = winutil.open_target(url)
        result.setdefault("url", url)
        return result

    def open_clan_page(self, ouid: str | None = None, nickname: str | None = None) -> dict[str, Any]:
        """클랜명 클릭 → 클랜 홈페이지 (v1.5.0 · 실제 clan_id 사용)."""
        nick = str(nickname or "").strip() or (self.store.settings.get("nickname") or "").strip()
        row = self.store.barracks_ids().get(nick.lower()) or {}
        clan_id = str(row.get("clan_id") or "")
        if not clan_id and nick:
            lookup = self._barracks_resolver().lookup(nick)
            clan_id = str((lookup or {}).get("clan_id") or "")
        if not clan_id:
            return {"ok": False, "message": "클랜 주소를 찾지 못했습니다 · 병영수첩을 한 번 최신화해 주세요"}
        url = barracks_mod.clan_url(clan_id)
        result = winutil.open_target(url)
        result.setdefault("url", url)
        return result

    # ============================================================ v1.5.0
    def _barracks_resolver(self) -> barracks_mod.Resolver:
        return barracks_mod.Resolver(self.store.barracks_ids(), save=self.store.save)

    def _barracks_links(self, barracks: dict[str, Any] | None = None) -> dict[str, Any]:
        nick = ((barracks or {}).get("nickname") or self.store.settings.get("nickname") or "").strip()
        row = self.store.barracks_ids().get(nick.lower()) or {}
        sn = str(row.get("sn") or "")
        return {
            "nickname": nick, "sn": sn,
            "profile": barracks_mod.profile_url(sn) if sn else "",
            "match": barracks_mod.profile_url(sn, "match") if sn else "",
            "clan_id": str(row.get("clan_id") or ""),
            "clan_url": barracks_mod.clan_url(row.get("clan_id") or ""),
            "photo": str(row.get("photo") or ""),
            "known": bool(sn), "source": str(row.get("source") or ""),
        }

    def resolve_barracks(self, nickname: str = "", refresh: bool = False) -> dict[str, Any]:
        """닉네임 → user_nexon_sn·클랜 ID·프로필 사진 (#8 · #10)."""
        try:
            nick = str(nickname or "").strip() or (self.store.settings.get("nickname") or "").strip()
            if not nick:
                raise ValueError("닉네임을 먼저 저장해 주세요")
            row = self._barracks_resolver().lookup(nick, refresh=bool(refresh))
            return {"ok": bool(row.get("ok")), **row, "links": self._barracks_links(),
                    "state": self._build_state(),
                    "message": (f"병영수첩 연결 · {row.get('sn')}" if row.get("ok")
                                else row.get("reason") or "병영수첩 주소를 찾지 못했서요")}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def link_barracks_url(self, text: str = "") -> dict[str, Any]:
        """사용자가 붙여넣은 `/{id}/detail` URL → 닉네임 역조회 후 연결 (#13)."""
        try:
            sn = barracks_mod.parse_sn(text)
            if not sn:
                raise ValueError("병영수첩 주소에서 ID(…SA)를 찾지 못했습니다")
            row = self._barracks_resolver().lookup_sn(sn)
            if row.get("ok"):
                return {"ok": True, **row, "links": self._barracks_links(), "state": self._build_state(),
                        "message": f"병영수첩 연결 · {row.get('nickname')} ({row.get('sn')})"}
            # 역조회가 막혔어도 ID 는 저장해 둔다 — 버튼은 실제 주소를 연다
            self._barracks_resolver().remember_url(text)
            return {"ok": False, "sn": sn, "profile": barracks_mod.profile_url(sn),
                    "message": row.get("reason") or "ID 는 저장했지만 닉네임을 읽지 못했서요",
                    "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def connect_nickname(self, text: str = "") -> dict[str, Any]:
        """홈에서 한 번에 내 서든 닉네임 연결 — 닉네임 · 병영수첩 주소 · ouid 모두 (#10).

        v1.0.1 — 설정에 들어가지 않고도 홈 팝업에서 연결할 수 있게 한 경로다.
        병영수첩 주소(…SA)는 역조회로 닉네임을 얻고, 닉네임을 바꾼 직후 역조회가
        막히면 ID 만 저장해 두고 닉네임 입력을 다시 받는다.
        """
        try:
            raw = str(text or "").strip().strip("\"'")
            if not raw:
                raise ValueError("서든 닉네임 또는 병영수첩 주소를 입력해 주세요")
            sn = barracks_mod.parse_sn(raw)
            ouid = ""
            nick = ""
            note = ""
            if sn:
                row = self._barracks_resolver().lookup_sn(sn)
                nick = str((row or {}).get("nickname") or "").strip()
                if not nick:
                    # 역조회가 막혔어도 ID 는 저장 — 다음 버튼이 실제 주소를 연다
                    self._barracks_resolver().remember_url(raw)
                    return {"ok": False, "need_nick": True, "sn": sn,
                            "message": (row or {}).get("reason") or "주소는 저장했어요 · 닉네임을 직접 입력해 주세요"}
                note = f"병영수첩 주소로 연결 · {sn}"
            else:
                target = v140.normalize_target(raw)
                if v140.is_ouid(target):
                    ouid = target
                    nick = self._client(force=True).user_name_of(ouid) or ""
                    if not nick:
                        raise ValueError("이 ID 의 닉네임을 읽지 못했습니다 · 닉네임을 직접 입력해 주세요")
                else:
                    nick = target
            nick = nick[:12]
            check = self.search_user(nick)
            if not check.get("ok"):
                return {**check, "need_nick": True}
            if not check.get("found"):
                return {"ok": False, "need_nick": True, "not_found": True,
                        "message": f"'{nick}' 닉네임을 찾지 못했습니다 · 철자를 확인해 주세요"}
            real = str(((check.get("user") or {}).get("name")) or nick)
            result = self.fetch_barracks(real)
            if not result.get("ok"):
                return {**result, "need_nick": True}
            message = f"{real} 연결 완료" + (f" · {note}" if note else "")
            return {**result, "nickname": real, "message": message, "source": (note or "닉네임")}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def party_map_plan(self, names: Any = None, refresh: bool = False) -> dict[str, Any]:
        """파티랭크 5인 맵 밴·픽 추천 — 파티원 맵별 전적 합산 (v1.5.0)."""
        try:
            guard = self._account_guard("mapban")
            if guard:
                return guard
            cache = self.store.barracks_cache()
            my_nick = (self.store.settings.get("nickname") or "").strip()
            wanted = v140.split_targets(names, limit=5) if names else []
            if wanted:
                selected = wanted
            else:
                selected = [my_nick] + [str(m.get("name") or "") for m in self.store.leaderboard()
                                       if not m.get("me")]
                selected = [n for n in dict.fromkeys(n.strip() for n in selected if n.strip())][:5]
            if not selected:
                raise ValueError("파티원 닉네임을 입력하거나 순위표에 멤버를 먼저 등록해 주세요")
            stored = self.store.data.setdefault("party_maps", {})
            entries: list[dict[str, Any]] = []
            fetched: list[str] = []
            client = None
            for name in selected[:5]:
                key = name.lower()
                barracks = cache.get(name) or next(
                    (b for b in cache.values() if str((b or {}).get("nickname") or "").lower() == key), None)
                maps = (barracks or {}).get("maps") or []
                source = "cache"
                if not maps:
                    hit = stored.get(key) or {}
                    maps = hit.get("maps") or []
                    source = "saved" if maps else "none"
                if (not maps or refresh) and v140.normalize_target(name):
                    try:
                        client = client or self._client()
                        nick = name if not (v140.is_ouid(name) or v140.is_sn(name)) else client.user_name_of(name)
                        ouid = name if v140.is_ouid(name) else None
                        target = nick or name
                        if not ouid:
                            ouid = client.resolve_ouid(target)
                        rows = client.recent_matches(ouid, 20)
                        details = [client.match_detail(m.get("match_id")) for m in rows[:20]]
                        agg = nexon.aggregate(rows, details, target)
                        maps = agg.get("maps") or []
                        if maps:
                            source = "live"
                            stored[key] = {"at": now_iso(), "name": target, "maps": maps}
                            fetched.append(target if target not in fetched else target)
                    except Exception as exc:  # noqa: BLE001 - 한 명 실패가 나머지를 막지 않는다
                        log.debug("party maps · %s failed: %s", name, exc)
                        source = source if maps else "error"
                entries.append({"name": name, "maps": maps, "source": source})
            if fetched:
                self.store.save()
            plan = v150.party_map_plan(entries)
            plan["members_detail"] = [{"name": e["name"], "maps": len(e["maps"]), "source": e["source"]}
                                       for e in entries]
            plan["calls"] = int(getattr(client, "calls", 0)) if client else 0
            plan["refreshed"] = fetched
            return {"ok": bool(plan.get("available")), **plan,
                    "message": plan.get("headline") or plan.get("reason") or ""}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, picks=[], bans=[], maps=[], members=[])

    def copy_text(self, text: str = "") -> dict[str, Any]:
        """클립보드 복사 — 내전 팀 결과 등."""
        return winutil.copy_text(text)

    # ---------------------------------------------------------- 클라우드 자동 백업
    def cloud_auto_sync(self, reason: str = "periodic") -> dict[str, Any]:
        """30분 주기 · 앱 시작/종료 자동 백업 (#17)."""
        try:
            if not self.store.settings.get("auto_cloud", True):
                return {"ok": False, "skipped": "자동 백업이 꺼져 있습니다"}
            if not self._cloud_state().get("signed_in"):
                return {"ok": False, "skipped": "클라우드 로그인이 필요합니다"}
            result = self.cloud_push()
            state = self.store.cloud_auto()
            state["last_at"] = now_iso()
            state["last_reason"] = reason
            self.store.save()
            return {"ok": bool(result.get("ok")), "reason": reason,
                    "message": result.get("message") or result.get("message", ""),
                    "at": state["last_at"]}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def start_cloud_auto(self) -> None:
        """앱 시작 시 1회 + 30분 주기 자동 동기화 스레드."""
        import threading

        if self._cloud_thread and self._cloud_thread.is_alive():
            return
        self._cloud_stop.clear()

        def loop() -> None:
            self.cloud_auto_sync("start")
            while not self._cloud_stop.wait(30 * 60):
                self.cloud_auto_sync("periodic")

        self._cloud_thread = threading.Thread(target=loop, name="sa-cloud-auto", daemon=True)
        self._cloud_thread.start()

    start_cloud_auto._serializable = False  # type: ignore[attr-defined]

    def shutdown(self, reason: str = "exit") -> dict[str, Any]:
        """앱 종료 직전 — 자동 백업 1회 + 감시 정지."""
        out: dict[str, Any] = {}
        try:
            self._cloud_stop.set()
            out["cloud"] = self.cloud_auto_sync(reason)
        except Exception:  # noqa: BLE001
            out["cloud"] = {"ok": False}
        try:
            self._match_stop.set()
        except Exception:  # noqa: BLE001
            pass
        return out

    shutdown._serializable = False  # type: ignore[attr-defined]


    # ------------------------------------------------------------ NVIDIA / Windows utils
    def _drs(self) -> nvidia.DrsManager:
        return nvidia.DrsManager()

    def _drs_summary(self) -> dict[str, Any]:
        import time

        if self._drs_state and time.monotonic() - self._drs_state_at < 3:
            return dict(self._drs_state)
        try:
            st = self._drs().state()
        except Exception as exc:  # noqa: BLE001
            st = {"supported": False, "error": str(exc), "backups": [], "modes": {},
                  "mode": None, "game_running": None, "writable": False, "last_backup": None}
        st["points"] = self.store.utils().get("nvidia", {}).get("points", [])[:3]
        st["needs_admin"] = bool(st.get("supported") and st.get("drs_exists") and not st.get("writable"))
        pending = nvidia.read_result()
        if pending:
            st["elevated_result"] = pending
        self._drs_state, self._drs_state_at = st, time.monotonic()
        return dict(st)

    def get_nvidia_state(self) -> dict[str, Any]:
        self._drs_state = None
        return {"ok": True, "utils": self._drs_summary(), "state": self._build_state()}

    def apply_nvidia_profile(self, mode: str, elevate: bool = False) -> dict[str, Any]:
        mode = str(mode or "").strip()
        if mode == "alias":
            mode = "stairfix"
        try:
            mgr = self._drs()
            if mode not in nvidia.MODES:
                raise nvidia.DrsError(f"알 수 없는 프로필: {mode}")
            if mode == "stairfix":
                # 계단현상 끄기 = 내장된 Drs 패치 로직 (프로필 팩 불필요)
                if not mgr.supported:
                    raise nvidia.DrsError("계단현상 끄기는 윈도우 NVIDIA 환경에서만 적용됩니다")
                if not mgr.drs_dir.is_dir():
                    raise nvidia.DrsError(
                        "NVIDIA Drs 폴더를 찾지 못했습니다 · NVIDIA 드라이버 설치 후 한 번 실행해 주세요"
                    )
                if not mgr.stairfix_ready():
                    raise nvidia.DrsError(
                        "Drs DB에서 서든어택(suddenattack.exe) 프로필을 찾지 못했습니다 · "
                        "서든어택을 한 번 실행하면 프로필이 생성됩니다"
                    )
                if elevate:
                    return {"ok": True, **(mgr.elevate_help(mode)), "state": self._build_state()}
                result = mgr.apply_stairfix(force=True)
                self._remember_drs("stairfix", result, label="계단현상 끄기 적용 전")
                self._drs_state = None
                return {"ok": True, **result, "state": self._build_state()}
            if not mgr.available_modes().get(mode):
                raise nvidia.DrsError(
                    f"{nvidia.MODES[mode]} 데이터를 읽지 못했습니다 · 앱을 다시 설치해 주세요"
                )
            if elevate:
                return {"ok": True, **(mgr.elevate_help(mode)), "state": self._build_state()}
            result = mgr.apply(mode, force=True)
            self._remember_drs(mode, result, label=f"{nvidia.MODES[mode]} 적용 전")
            self._drs_state = None
            return {"ok": True, **result, "state": self._build_state()}
        except nvidia.DrsError as exc:
            message = str(exc)
            needs_admin = "관리자 권한" in message
            return {"ok": False, "message": message, "needs_admin": needs_admin,
                    "can_elevate": needs_admin, "mode": mode, "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def wait_elevated_result(self, timeout: float = 30.0) -> dict[str, Any]:
        """관리자 권한으로 다시 적용한 결과 파일을 기다린다.

        v1.3.0 은 UAC 창만 띄우고 결과를 읽지 않아, 권한 상승 경로에서는
        변경 내역이 화면에 전혀 나오지 않았다.  여기서 결과 JSON 을 폴링해
        적용 내역·프로필·체크리스트를 그대로 돌려준다.
        """
        deadline = time.monotonic() + max(5.0, float(timeout or 30.0))
        while time.monotonic() < deadline:
            result = nvidia.read_result()
            if result:
                self._drs_state = None
                if result.get("ok"):
                    self._remember_drs(result.get("mode"), result, label="관리자 권한 적용")
                payload = {**result, "state": self._build_state()}
                return payload
            time.sleep(0.4)
        return {"ok": False, "timeout": True,
                "message": "관리자 권한 적용 결과를 기다리다 시간이 지났습니다 · UAC 창을 승인했는지 확인해 주세요"}

    def toggle_nvidia_alias(self, enabled: bool) -> dict[str, Any]:
        if enabled:
            return self.apply_nvidia_profile("stairfix")
        try:
            latest = self._drs().list_backups()
            if not latest:
                raise nvidia.DrsError("복구 지점이 없습니다 · 먼저 프로필을 적용해 주세요")
            result = self._drs().restore(latest[0]["id"])
            self._remember_drs(None, result, label="계단현상 설정 복구")
            self._drs_state = None
            return {"ok": True, **result, "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, needs_admin="관리자 권한" in str(exc))

    def restore_nvidia_profile(self, backup_id: str | None = None, elevate: bool = False) -> dict[str, Any]:
        try:
            mgr = self._drs()
            if elevate:
                # UAC 로 다시 실행해 백업을 되돌린다 (결과는 wait_elevated_result 로 받는다)
                return {"ok": True, **(mgr.elevate_help("restore", restore=True)), "state": self._build_state()}
            if backup_id:
                result = mgr.restore(str(backup_id), force=True)
            else:
                result = mgr.restore_latest(force=True)
            self._remember_drs(None, result, label="원래 설정 복구")
            self._drs_state = None
            return {"ok": True, **result, "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            needs_admin = "관리자 권한" in message
            return self._err(exc, needs_admin=needs_admin, can_elevate=needs_admin)

    def _remember_drs(self, mode: str | None, result: dict[str, Any], label: str) -> None:
        utils = self.store.utils()
        nv = utils.setdefault("nvidia", {"mode": None, "alias": False, "last_backup": None, "points": []})
        if result.get("ok") and not result.get("unchanged"):
            nv["mode"] = mode
            nv["last_backup"] = result.get("applied_at") or now_iso()
            point = {"at": now_iso(), "label": label, "backup": result.get("backup")}
            points = [p for p in (nv.get("points") or []) if p.get("backup") != point["backup"]]
            nv["points"] = [point, *points][:3]
            changes = result.get("changes") or []
            base_changes = result.get("base_changes") or []
            if changes or base_changes or result.get("profile_items"):
                nv["last_changes"] = {"at": now_iso(), "mode": mode, "label": label,
                                      "rows": changes[:40], "base_rows": base_changes[:40],
                                      "count": len(changes) + len(base_changes),
                                      "files": (result.get("files") or [])[:6],
                                      "profile_items": (result.get("profile_items") or [])[:20],
                                      "checklist": (result.get("checklist") or [])[:8],
                                      "reason": result.get("unchanged_reason") or "",
                                      "message": result.get("message") or ""}
        if mode is None:
            nv["mode"] = None
        nv["alias"] = bool(mode == "stairfix")
        self.store.save()

    def set_mouse_accel(self, enabled: bool) -> dict[str, Any]:
        result = winutil.set_mouse_accel(bool(enabled))
        if result.get("ok"):
            self.store.utils()["accel"] = bool(enabled)
            self.store.save()
        return {**result, "state": self._build_state()}

    def get_mouse_accel(self) -> dict[str, Any]:
        return {"ok": True, "enabled": winutil.get_mouse_accel(), "state": self._build_state()}

    # ------------------------------------------------------------ backup / restore
    def export_backup(self) -> dict[str, Any]:
        try:
            path = self.store.export_backup()
            size = path.stat().st_size
            return {"ok": True, "path": str(path), "size": size,
                    "message": f"💾 내 기록 백업 완료 · {path.name} ({size / 1024:.1f}KB)"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def import_backup(self, path: str | None = None) -> dict[str, Any]:
        try:
            target = path
            if not target:
                target = self._ask_open_file()
                if not target:
                    return {"ok": False, "cancelled": True, "message": "파일 선택이 취소되었습니다"}
            data = self.store.import_backup(target)
            return {"ok": True, "path": str(target), "state": self._build_state(),
                    "message": f"📂 기록 불러오기 완료 · 훈련 기록 {len(data.get('records', []))}회 복원"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def _ask_open_file(self) -> str | None:
        if not self._win:
            return None
        try:
            import webview

            result = self._win.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False, file_types=("SA AIM LABS 백업 (*.json)", "모든 파일 (*.*)")
            )
        except Exception:  # noqa: BLE001
            return None
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else str(result)

    # ------------------------------------------------------------ cloud sync
    def cloud_signup(self, email: str, password: str) -> dict[str, Any]:
        return self._cloud_auth("signup", email, password)

    def cloud_signin(self, email: str, password: str) -> dict[str, Any]:
        return self._cloud_auth("signin", email, password)

    def _cloud_auth(self, action: str, email: str, password: str) -> dict[str, Any]:
        try:
            url, key = self._cloud_credentials()
            client = supabase_sync.SupabaseClient(url, key)
            if action == "signup":
                session = client.sign_up(email, password)
                if session.get("needs_confirmation"):
                    return {"ok": True, "needs_confirmation": True, "message": session["message"],
                            "state": self._build_state()}
            else:
                session = client.sign_in(email, password)
            cloud = self.store.cloud()
            cloud.update({
                "email": session.get("email") or email,
                "user_id": session.get("user_id"),
                "access_token": session.get("access_token"),
                "refresh_token": session.get("refresh_token"),
            })
            self.store.update_settings({"cloud_email": email})
            self.store.save()
            # 로그인 직후 백업 1회 — 첫 백업이 30분 뒤로 밀리지 않게 (v1.0.1)
            try:
                self._queue_cloud_backup("signin")
            except Exception:  # noqa: BLE001
                log.debug("first cloud backup after signin failed", exc_info=True)
            return {"ok": True, "session": {k: session.get(k) for k in ("email", "user_id", "expires_in")},
                    "message": "클라우드 로그인 완료" if action == "signin" else "회원가입 완료 · 로그인되었습니다",
                    "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def cloud_signout(self) -> dict[str, Any]:
        cloud = self.store.cloud()
        try:
            url, key = self._cloud_credentials()
            if cloud.get("access_token"):
                supabase_sync.SupabaseClient(url, key).sign_out(cloud["access_token"])
        except Exception:  # noqa: BLE001
            pass
        cloud.update({"access_token": None, "refresh_token": None, "user_id": None})
        self.store.save()
        return {"ok": True, "message": "클라우드 로그아웃 완료", "state": self._build_state()}

    def cloud_push(self) -> dict[str, Any]:
        try:
            url, key = self._cloud_credentials()
            cloud = self.store.cloud()
            token, user_id = self._cloud_token(cloud, url, key)
            if not token or not user_id:
                raise ValueError("클라우드 로그인이 필요합니다")
            # v2.0.0 — 백업과 **같은 범위**만 올린다 (비밀 제외 · 에임 기록 없음)
            clean = store_scrub(self.store.data)
            payload = {
                "settings": {k: v for k, v in (clean.get("settings") or {}).items()},
                "leaderboard": nexon.leaderboard_payload(self.store.leaderboard()),
                "barracks": clean.get("barracks") or {},
                "barracks_ids": clean.get("barracks_ids") or {},
                "barracks_history": clean.get("barracks_history") or [],
                "play_days": clean.get("play_days") or {},
                "party_presets": clean.get("party_presets") or [],
                "memos": clean.get("memos") or {},
                "exported_at": now_iso(),
                "app_version": paths.APP_VERSION,
            }
            client = supabase_sync.SupabaseClient(url, key)
            try:
                client.upsert_profile(token, user_id, payload)
            except supabase_sync.SupabaseError as exc:
                # v1.0.1 — 1시간짜리 액세스 토큰이 만료되면 401 이 나고, 예전에는
                # 재시작 전까지 계속 실패했다.  한 번 토큰을 새로 받아 재시도한다.
                if int(getattr(exc, "status", 0) or 0) not in (401, 403):
                    raise
                refreshed = supabase_sync.SupabaseClient(url, key).refresh(cloud.get("refresh_token") or "")
                token = refreshed.get("access_token")
                if not token:
                    cloud.update({"access_token": None})
                    self.store.save()
                    raise ValueError("클라우드 로그인이 만료되었습니다 · 다시 로그인해 주세요") from exc
                cloud.update({"access_token": token, "refresh_token": refreshed.get("refresh_token")})
                client.upsert_profile(token, user_id, payload)
            cloud["last_sync"] = now_iso()
            self.store.save()
            return {"ok": True, "message": "☁️ 클라우드에 전적·설정을 올렸습니다",
                    "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def cloud_pull(self) -> dict[str, Any]:
        try:
            url, key = self._cloud_credentials()
            cloud = self.store.cloud()
            token, user_id = self._cloud_token(cloud, url, key)
            if not token or not user_id:
                raise ValueError("클라우드 로그인이 필요합니다")
            row = supabase_sync.SupabaseClient(url, key).get_profile(token, user_id)
            if not row:
                raise ValueError("클라우드에 저장된 기록이 없습니다 · 먼저 올리기를 실행해 주세요")
            payload = row.get("payload") or {}
            backup = self.store.export_backup()
            self.store.import_backup_from_payload(payload) if hasattr(self.store, "import_backup_from_payload") else None
            if not hasattr(self.store, "import_backup_from_payload"):
                data = self.store.data
                for section in ("leaderboard", "barracks_history", "party_presets"):
                    if isinstance(payload.get(section), list):
                        data[section] = payload[section]
                if isinstance(payload.get("barracks"), dict):
                    data["barracks"] = payload["barracks"]
                if isinstance(payload.get("play_days"), dict):
                    data["play_days"] = payload["play_days"]
                self.store.save()
            cloud["last_sync"] = now_iso()
            self.store.save()
            return {"ok": True, "message": f"☁️ 클라우드 기록을 내려받았습니다 · 이전 상태는 {backup.name} 에 보관",
                    "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def _cloud_credentials(self) -> tuple[str, str]:
        cfg = supabase_sync.load_config()
        s = self.store.settings.get("supabase") or {}
        url = (s.get("url") or cfg["url"] or "").strip()
        key = (s.get("anon_key") or cfg["anon_key"] or "").strip()
        if not url or not key:
            raise ValueError("Supabase URL 과 anon key 를 먼저 설정해 주세요")
        return url, key

    def _cloud_token(self, cloud: dict[str, Any], url: str, key: str) -> tuple[str | None, str | None]:
        token = cloud.get("access_token")
        refresh = cloud.get("refresh_token")
        if not token and refresh:
            session = supabase_sync.SupabaseClient(url, key).refresh(refresh)
            cloud.update({"access_token": session.get("access_token"), "refresh_token": session.get("refresh_token"),
                          "user_id": session.get("user_id") or cloud.get("user_id")})
            self.store.save()
            token = cloud.get("access_token")
        return token, cloud.get("user_id")

    def cloud_status(self) -> dict[str, Any]:
        return {"ok": True, "cloud": self._cloud_state(), "state": self._build_state()}

    # ------------------------------------------------------------ 회원 · 등급 (v2.0.0)
    def auth_login(self, email: str = "", password: str = "") -> dict[str, Any]:
        """이메일·비밀번호 로그인 → Supabase 세션 + 등급(plan) 조회."""
        try:
            result = self.account.sign_in(email, password)
            return {**result, "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, locked=False)

    def auth_signup(self, email: str = "", password: str = "") -> dict[str, Any]:
        """회원가입 — 이메일 확인이 켜져 있으면 확인 메일 안내를 돌려준다."""
        try:
            result = self.account.sign_up(email, password)
            return {**result, "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def auth_recover(self, email: str = "") -> dict[str, Any]:
        """비밀번호 찾기 — Supabase 가 재설정 링크 메일을 보낸다."""
        try:
            return self.account.recover(email)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def auth_signout(self) -> dict[str, Any]:
        try:
            result = self.account.sign_out()
            return {**result, "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def account_state(self, refresh_plan: bool = False) -> dict[str, Any]:
        """지금 로그인·등급 상태.  ``refresh_plan`` 이면 subscriptions 를 다시 읽는다."""
        try:
            if refresh_plan:
                self.account.refresh_plan()
            state = self.account.state()
            return {"ok": True, "account": state, "quota": state.get("quota")}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def _account_guard(self, feature: str) -> dict[str, Any] | None:
        """PRO 기능 잠금 — ``ENFORCE_PRO_LIMIT = False`` 인 지금은 항상 None."""
        try:
            return self.account.guard(feature)
        except Exception:  # noqa: BLE001 - 잠금 확인 실패가 기능을 막지 않는다
            log.debug("account guard failed", exc_info=True)
            return None

    def _account_use(self, feature: str) -> None:
        try:
            self.account.use(feature)
        except Exception:  # noqa: BLE001
            log.debug("account usage bump failed", exc_info=True)

    # ============================================================ v1.2.0
    # 업데이트 · 전원/게임 모드 · 트레이
    def check_update(self, force: bool = True) -> dict[str, Any]:
        try:
            return updates.check(paths.APP_VERSION, force=bool(force))
        except Exception as exc:  # noqa: BLE001 - 확인 실패는 조용히
            return {"ok": False, "newer": False, "current": paths.APP_VERSION, "message": str(exc)}

    # ================================================================ v1.4.0
    # 1) 클랜전 상대 스카우팅
    def scout_team(self, nicknames: Any = None) -> dict[str, Any]:
        """상대 1~5명 비교 — 이미 받은 전적은 캐시, 처음 보는 상대만 최근 10판 상세."""
        import re

        try:
            guard = self._account_guard("scout")
            if guard:
                return guard
            # 닉네임·ouid·병영수첩 URL 을 섞어 붙여넣어도 된다 (최대 6명 = 6:6 상대 팀)
            names = v140.split_targets(nicknames, limit=6)
            if not names:
                raise ValueError("상대 닉네임을 1명 이상 입력해 주세요 (최대 6명)")
            client = self._client()
            me_rp = 0
            try:
                cached = self.store.barracks_cache().get(self.store.settings.get("nickname") or "", {})
                me_rp = int(((cached or {}).get("tier") or {}).get("solo", {}).get("rp") or 0)
            except (TypeError, ValueError, AttributeError):
                me_rp = 0
            rows: list[dict[str, Any]] = []
            for name in names:
                try:
                    rows.append(v140.scout_player(client, name, me_rp=me_rp))
                except Exception as exc:  # noqa: BLE001 - 한 명 실패가 나머지를 막지 않는다
                    rows.append({"name": name, "error": nexon.friendly_error(str(exc)),
                                 "threat": 0, "threat_label": "정보 없음", "tips": [str(exc)]})
            # v1.4.0 — 서든은 대부분 스나 K/D가 높아 개인별 판별이면 전원이 스나가 된다.
            # 팀 단위로 격차 상위 2명만 추정 스나로 배정한다.
            v140.assign_roles(rows)
            for row in rows:
                if not row.get("error"):
                    row["tips"] = v140.counter_tips(row)
            team = v140.team_summary(rows)
            self._account_use("scout")
            found = len([r for r in rows if not r.get("error")])
            return {"ok": found > 0, "rows": rows, "team": team, "calls": int(getattr(client, "calls", 0)),
                    "message": f"스카우팅 {found}/{len(rows)}명 완료 · {team.get('label')} (위협도 {team.get('threat')})"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, rows=[], team={})

    # 2) 듀오·깐부 시너지
    def duo_synergy(self, nickname: str = "") -> dict[str, Any]:
        try:
            guard = self._account_guard("duo")
            if guard:
                return guard
            cache = self.store.barracks_cache()
            barracks = cache.get(self.store.settings.get("nickname") or "", None)
            if barracks is None and cache:
                barracks = list(cache.values())[-1]
            matches = (barracks or {}).get("matches") or []
            result = v140.duo_synergy(matches, nickname)
            return {"ok": True, **result, "message": result.get("label") or result.get("reason") or ""}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def recent_opponents(self, refresh: bool = True) -> dict[str, Any]:
        """전술 도구 하단 '최근 상대 팀' — **지금 서버의 최신 판**을 다시 읽는다.

        v2.0.0 — 캐시된 병영수첩 목록만 보여 주면 방금 끝난 판이 빠진 채로
        계속 같은 상대가 남았다.  그래서 목록은 ``fresh=True`` 로 새로 받고,
        새 데이터가 없거나 실패하면 **저장된 판을 그대로 쓰되 ``stale=True`` 와
        그 판의 시각**을 함께 돌려준다 (최신처럼 보이지 않게).
        """
        try:
            nick = self._store_nick()
            if not nick:
                raise ValueError("설정에서 서든 닉네임을 먼저 연결해 주세요")
            client = self._client()
            rows: list[dict[str, Any]] = []
            error = None
            if refresh:
                try:
                    rows = client.recent_matches(client.resolve_ouid(nick), 5, fresh=True)
                except Exception as exc:  # noqa: BLE001 - 실패하면 저장된 판으로
                    error = nexon.friendly_error(str(exc))
            latest = rows[0] if rows else None
            detail = None
            if latest:
                detail = client.match_detail_cached(latest.get("match_id")) or client.match_detail(latest.get("match_id"))
            cached = self.store.barracks_cache().get(nick) or {}
            cached_matches = list(cached.get("matches") or [])
            stale = False
            if latest:
                match = latest
            else:
                match = cached_matches[0] if cached_matches else None
                stale = bool(match)
            if not match:
                return {"ok": False, "rows": [], "opponents": [], "stale": False,
                        "message": error or "최근 경기를 찾지 못했습니다"}
            if detail:
                players = detail.get("match_detail") or []
                mine = None
                for player in players:
                    if str(player.get("user_name") or "").strip().lower() == nick.lower():
                        mine = player.get("team_id")
                opponents = [str(p.get("user_name") or "").strip() for p in players
                             if mine is not None and p.get("team_id") != mine and str(p.get("user_name") or "").strip()]
                map_name = detail.get("match_map") or "-"
            else:
                opponents = []
                for chunk in (match.get("players") or []):
                    try:
                        name, side = str(chunk[0]).strip(), str(chunk[1])
                    except (IndexError, TypeError):
                        continue
                    if side == "적" and name:
                        opponents.append(name)
                map_name = match.get("map") or "-"
            opponents = list(dict.fromkeys(opponents))[:6]
            stamp = (str(match.get("date_match") or match.get("date") or "")[:10]
                     + " " + str(match.get("time") or "").strip()).strip()
            fetched_at = now_iso()
            payload = {
                "ok": bool(opponents), "opponents": opponents,
                "rows": [{"name": name, "slot": i} for i, name in enumerate(opponents)],
                "match": {"match_id": match.get("match_id"), "map": map_name, "at": stamp,
                          "result": "승" if str(match.get("match_result", match.get("result"))) in ("1", "승") else "패"},
                "fetched_at": fetched_at if not stale else None,
                "stored_at": (cached.get("updated_at") if stale else None),
                "stale": stale, "error": error,
                "message": (f"{map_name} · 상대 {len(opponents)}명" if opponents else "상대 팀을 찾지 못했습니다"),
            }
            if opponents:
                cache = self.store.overlay_cache()
                cache["last_opponents"] = {"at": fetched_at, "match": payload["match"],
                                           "rows": payload["rows"], "stale": stale}
                self.store.save()
            return payload
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, rows=[], opponents=[], stale=False)

    def scout_last_match(self) -> dict[str, Any]:
        """방금 끝난 판 상대 팀을 그대로 불러와 분석한다 (타이핑 없음)."""
        try:
            guard = self._account_guard("scout_last")
            if guard:
                return guard
            nick = (self.store.settings.get("nickname") or "").strip()
            if not nick:
                raise ValueError("설정에서 서든 닉네임과 API 키를 먼저 저장해 주세요")
            client = self._client()
            ouid = client.resolve_ouid(nick)
            # v1.0.1 (#12·#17) — 10분 목록 캐시를 건너뛰고 지금 바로 최신 판을 본다.
            rows = client.recent_matches(ouid, 1, fresh=True)
            if not rows:
                raise ValueError("최근 경기를 찾지 못했습니다 · 전적을 한 번 최신화해 주세요")
            latest = rows[0]
            detail = client.match_detail_cached(latest.get("match_id")) or client.match_detail(latest.get("match_id"))
            players = (detail or {}).get("match_detail") or []
            my_team = None
            for p in players:
                if str(p.get("user_name") or "").strip().lower() == nick.lower():
                    my_team = p.get("team_id")
            if my_team is None:
                raise ValueError("최근 경기 상세에서 내 팀 정보를 찾지 못했습니다")
            opponents = [str(p.get("user_name") or "").strip() for p in players
                         if p.get("team_id") != my_team and str(p.get("user_name") or "").strip()]
            opponents = list(dict.fromkeys(opponents))[:6]  # 5:5 → 5명, 6:6 → 6명
            if not opponents:
                raise ValueError("상대 팀 닉네임을 찾지 못했습니다")
            me_rp = 0
            try:
                cached = self.store.barracks_cache().get(nick, {})
                me_rp = int(((cached or {}).get("tier") or {}).get("solo", {}).get("rp") or 0)
            except (TypeError, ValueError, AttributeError):
                me_rp = 0
            out: list[dict[str, Any]] = []
            for name in opponents:
                try:
                    out.append(v140.scout_player(client, name, me_rp=me_rp, depth=8))
                except Exception as exc:  # noqa: BLE001
                    out.append({"name": name, "error": nexon.friendly_error(str(exc)),
                                "threat": 0, "threat_label": "정보 없음", "tips": [str(exc)]})
            v140.assign_roles(out)
            for row in out:
                if not row.get("error"):
                    row["tips"] = v140.counter_tips(row)
            team = v140.team_summary(out)
            found = len([r for r in out if not r.get("error")])
            return {"ok": found > 0, "rows": out, "team": team, "opponents": opponents,
                    "map": (detail or {}).get("match_map") or "-",
                    "date": latest.get("date_match") or "",
                    "calls": int(getattr(client, "calls", 0)),
                    "message": f"방금 끝난 판 상대 {found}명 분석 · {team.get('label')} (실전 전력 {((team.get('force') or {}).get('power') or 0)})"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, rows=[], team={})

    def _coplayers(self) -> list[dict[str, Any]]:
        """내 최근 매치 캐시에 함께 등장한 플레이어(팀·상대 모두) 집계."""
        cache = self.store.barracks_cache()
        barracks = cache.get(self.store.settings.get("nickname") or "", None)
        if barracks is None and cache:
            barracks = list(cache.values())[-1]
        matches = (barracks or {}).get("matches") or []
        me = (self.store.settings.get("nickname") or "").strip().lower()
        table: dict[str, dict[str, Any]] = {}
        for m in matches:
            won = str(m.get("result")) == "승"
            for p in m.get("players") or []:
                try:
                    name = str(p[0]).strip()
                    k, d, dmg = int(p[2] or 0), int(p[3] or 0), int(p[4] or 0)
                except (IndexError, TypeError, ValueError):
                    continue
                if not name or name.lower() == me:
                    continue
                slot = table.setdefault(name, {"name": name, "games": 0, "k": 0, "d": 0, "dmg": 0, "wins": 0})
                slot["games"] += 1
                slot["k"] += k
                slot["d"] += d
                slot["dmg"] += dmg
                slot["wins"] += 1 if won else 0
        out = []
        for slot in table.values():
            g = slot["games"]
            if g < 1:
                continue
            out.append({
                "name": slot["name"], "games": g,
                "kd": round(slot["k"] / (slot["k"] + slot["d"]) * 100, 1) if (slot["k"] + slot["d"]) else 0.0,
                "dmg": int(round(slot["dmg"] / g)),
                "win": round(slot["wins"] / g * 100, 1),
                "solo": 0, "party": 0, "weapon": "sniper" if slot["k"] >= slot["d"] else "rifle",
                "source": "match",
            })
        out.sort(key=lambda r: -r["games"])
        return out[:30]

    def balance_candidates(self) -> dict[str, Any]:
        """밸런서 후보 — 순위표 멤버 + 최근 매치 동반 플레이어."""
        try:
            members = [{"name": m.get("name"), "solo": m.get("solo"), "party": m.get("party"),
                        "weapon": m.get("weapon"), "kd": m.get("kd"), "dmg": m.get("dmg"),
                        "win": m.get("win"), "me": bool(m.get("me")), "source": "leaderboard"}
                       for m in self.store.leaderboard()]
            names = {str(m.get("name") or "").strip().lower() for m in members}
            coplayers = [c for c in self._coplayers() if c["name"].strip().lower() not in names]
            return self._ok(members=members, coplayers=coplayers,
                            message=f"순위표 {len(members)}명 · 최근 동반 {len(coplayers)}명")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, members=[], coplayers=[])

    def balance_teams(self, names: Any = None, size: int = 0, roles: Any = None) -> dict[str, Any]:
        """내전 팀 자동 짜기 — 순위표 멤버 + 최근 동반 플레이어로 A/B 균형 분배."""
        try:
            members = [dict(m) for m in self.store.leaderboard()]
            by_name = {str(m.get("name") or "").strip().lower(): m for m in members}
            wanted = v140.split_targets(names, limit=16)
            client = None
            pool = list(members)
            if wanted:
                pool = []
                coplayers = {c["name"].strip().lower(): c for c in self._coplayers()}
                for name in wanted:
                    key = name.lower()
                    if key in by_name:
                        pool.append(by_name[key])
                    elif key in coplayers:
                        pool.append(coplayers[key])
                    elif v140.is_ouid(name):
                        try:
                            client = client or self._client()
                            pool.append(nexon.fetch_member(client, name, ouid=name))
                        except Exception:  # noqa: BLE001 - 한 명 실패는 건너뛴다
                            continue
                    else:
                        # v1.5.0 (#21) — 순위표에 없는 닉네임을 solo:0 으로 넣지 않는다.
                        # 넥슨 API 로 바로 조회해 RP·K/D 를 채운다.
                        try:
                            client = client or self._client()
                            fetched = nexon.fetch_member(client, name)
                            if fetched.get("ok"):
                                pool.append(fetched)
                            else:
                                pool.append({"name": name, "solo": 0, "party": 0, "weapon": "sniper",
                                             "kd": None, "dmg": None, "win": None,
                                             "error": fetched.get("error")})
                        except Exception as exc:  # noqa: BLE001
                            pool.append({"name": name, "solo": 0, "party": 0, "weapon": "sniper",
                                         "kd": None, "dmg": None, "win": None,
                                         "error": str(exc)})
            role_map: dict[str, str] = {}
            if isinstance(roles, dict):
                role_map = {str(k): str(v) for k, v in roles.items() if str(v) in ("sniper", "rifle")}
            result = v140.balance_teams(pool, size=(int(size) or None), roles=role_map)
            return {"ok": bool(result.get("available")), **result,
                    "members": [{"name": m.get("name"), "solo": m.get("solo"), "party": m.get("party"),
                                 "weapon": m.get("weapon"), "me": bool(m.get("me"))} for m in pool]}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def duo_rank(self, extra: Any = None) -> dict[str, Any]:
        """최고 궁합 깐부 순위 — 순위표 멤버 + 직접 입력 닉네임 (추가 API 0건)."""
        try:
            cache = self.store.barracks_cache()
            barracks = cache.get(self.store.settings.get("nickname") or "", None)
            if barracks is None and cache:
                barracks = list(cache.values())[-1]
            matches = (barracks or {}).get("matches") or []
            me = (self.store.settings.get("nickname") or "").strip().lower()
            names = [str(m.get("name") or "") for m in self.store.leaderboard()
                     if str(m.get("name") or "").strip().lower() != me and not m.get("me")]
            if isinstance(extra, str):
                names += [n.strip() for n in extra.replace("\n", ",").split(",") if n.strip()]
            elif isinstance(extra, (list, tuple)):
                names += [str(n).strip() for n in extra if str(n).strip()]
            result = v140.duo_ranking(matches, names)
            return {"ok": True, **result,
                    "message": (f"최고 궁합 1위 {result['rows'][0]['friend']}" if result.get("rows")
                                else "함께 뛴 판이 2판 이상인 친구를 찾지 못했습니다")}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # 3) 맞춤형 .plo + AI 코칭
    # 4) 오늘 랭크 컨디션
    def rank_condition(self, force: bool = False) -> dict[str, Any]:
        try:
            cache = self.store.barracks_cache()
            barracks = cache.get(self.store.settings.get("nickname") or "", None)
            if barracks is None and cache:
                barracks = list(cache.values())[-1]
            condition = v140.rank_condition((barracks or {}).get("matches") or [])
            stored = self.store.data.get("condition_history") or []
            history = v140.condition_history(stored, condition, today=today_str()) if condition.get("available") else stored
            if history != stored:
                self.store.data["condition_history"] = history
                self.store.save()
            condition["history"] = history[-14:]
            return self._ok(condition=condition, history=history[-14:])
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # 5) 연패 브레이크 감시
    def _tilt_watcher(self) -> tilt.TiltWatcher:
        with self._tilt_lock:
            if self._tilt is None:
                try:
                    interval = int(self.store.settings.get("tilt_interval") or tilt.DEFAULT_INTERVAL)
                except (TypeError, ValueError):
                    interval = tilt.DEFAULT_INTERVAL
                def _notify(title: str, message: str, level: str = "info") -> None:
                    icon = tray_ref()
                    if icon is not None:
                        icon.notify(title, message, level)

                self._tilt = tilt.TiltWatcher(
                    self._tilt_matches, interval=interval,
                    enabled=lambda: bool(self.store.settings.get("tilt_watch", True)),
                    notify=_notify,
                )
            return self._tilt

    def _tilt_matches(self) -> list[dict[str, Any]]:
        """가벼운 확인 — 최신 솔로·파티 매치 목록 1회씩(캐시 TTL 2분)."""
        nick = (self.store.settings.get("nickname") or "").strip()
        if not nick:
            return []
        client = self._client()
        ouid = client.resolve_ouid(nick)
        rows: list[dict[str, Any]] = []
        for mtype in nexon.MATCH_TYPES:
            try:
                rows.extend(client.matches(ouid, mtype, ttl=120))
            except nexon.NexonError:
                continue
        rows.sort(key=nexon.match_time, reverse=True)
        return rows[:20]

    def start_tilt_watch(self) -> None:
        """main.py 가 앱 시작 시 한 번 호출 — 설정이 OFF 면 스레드는 놀고 있는다."""
        try:
            self._tilt_watcher().start()
        except Exception:  # noqa: BLE001 - 감시 실패는 앱을 막지 않는다
            log.debug("tilt watcher could not start", exc_info=True)

    start_tilt_watch._serializable = False  # type: ignore[attr-defined]

    def tilt_state(self) -> dict[str, Any]:
        try:
            watcher = self._tilt_watcher()
            if bool(self.store.settings.get("tilt_watch", True)) and not watcher.running:
                watcher.start()
            state = watcher.state()
            state["enabled"] = bool(self.store.settings.get("tilt_watch", True))
            return self._ok(tilt=state)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, tilt={})

    def set_tilt_watch(self, enabled: bool = True, interval: int | None = None) -> dict[str, Any]:
        try:
            patch: dict[str, Any] = {"tilt_watch": bool(enabled)}
            if interval is not None:
                patch["tilt_interval"] = max(tilt.MIN_INTERVAL, min(tilt.MAX_INTERVAL, int(interval)))
            self.store.update_settings(patch)
            watcher = self._tilt_watcher()
            if interval is not None:
                watcher.interval = int(patch["tilt_interval"])
            if enabled:
                watcher.start()
            else:
                watcher.stop()
            return self._ok(tilt={**watcher.state(), "enabled": bool(enabled)}, state=self._build_state(),
                            message="연패 브레이크 감시를 켰습니다" if enabled else "연패 브레이크 감시를 껐습니다")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def ack_tilt(self, alert_id: str = "") -> dict[str, Any]:
        try:
            alerts = self._tilt_watcher().ack(alert_id or "")
            return self._ok(alerts=alerts, message="")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # 6) 원클릭 자동 업데이트 + 구버전 차단
    def update_gate(self, force: bool = False) -> dict[str, Any]:
        try:
            result = updates.check(paths.APP_VERSION, force=bool(force))
            gate_state = updates.gate(result, paths.APP_VERSION)
            return self._ok(gate=gate_state, update=result)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, gate={"blocked": False, "reason": "error"})

    def install_update(self) -> dict[str, Any]:
        try:
            result = updates.cached() or updates.check(paths.APP_VERSION, force=True)
            gate_state = updates.gate(result, paths.APP_VERSION)
            url = str(gate_state.get("download") or "")
            if not url:
                raise ValueError("새 버전 다운로드 주소가 없습니다 · 릴리스 페이지를 열어 주세요")
            prep = updates.prepare_install(url, digest=result.get("digest"))
            if not prep.get("ok"):
                raise ValueError(prep.get("message") or "업데이트를 내려받지 못했습니다")
            run = updates.run_install(str(prep["script"]))
            if not run.get("ok"):
                raise ValueError(run.get("message") or "업데이트를 실행하지 못했습니다")
            return self._ok(path=prep.get("path"), size=prep.get("size"),
                            verified=prep.get("verified"), sha256=prep.get("sha256"),
                            message=run.get("message"))
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # 7) 첫 실행 2단계 온보딩
    def complete_onboarding(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            data = payload or {}
            patch: dict[str, Any] = {"onboarded": True}
            nick = str(data.get("nickname") or "").strip()[:12]
            key = str(data.get("api_key") or "").strip()
            if nick:
                patch["nickname"] = nick
            if key and not self._looks_masked(key):
                patch["api_key"] = key
            self.store.update_settings(patch)
            return self._ok(state=self._build_state(), message="환영합니다 · 준비가 끝났습니다")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def skip_onboarding(self) -> dict[str, Any]:
        try:
            self.store.update_settings({"onboarded": True})
            return self._ok(state=self._build_state(), message="설정에서 언제든 바꿀 수 있습니다")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # ------------------------------------------------------------ 1:1 비교
    def compare_friend(self, nickname: str) -> dict[str, Any]:
        """저장된 순위표 값만으로 나 vs 친구."""
        try:
            members = self._ensure_me(None)
            key = str(nickname or "").strip().lower()
            if not key:
                raise ValueError("비교할 친구를 선택해 주세요")
            target = next((m for m in members if str(m.get("name", "")).strip().lower() == key), None)
            if target is None:
                raise ValueError(f"'{nickname}' 을(를) 순위표에서 찾지 못했습니다")
            me = next((m for m in members if m.get("me")), None)
            if me is None:
                raise ValueError("내 전적이 없습니다 · 병영수첩을 먼저 불러와 주세요")
            compare = insights.friend_compare(me, target)
            return {"ok": True, "compare": compare,
                    "message": f"{compare['me']['name']} vs {compare['friend']['name']} · {compare['verdict']}"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # ------------------------------------------------------------ 타인 A + 타인 B 듀오 시너지
    def _recent_for(self, client: nexon.NexonClient, name: str, limit: int = 30) -> tuple[str, list[dict[str, Any]]]:
        """닉네임 → (실제 닉네임, 최근 경기 목록).  ouid·병영 URL 도 받는다."""
        target = v140.normalize_target(str(name or "").strip())
        if not target:
            raise ValueError("비교할 닉네임을 입력해 주세요")
        if v140.is_ouid(target):
            real = client.user_name_of(target) or target
            ouid = target
        else:
            real = target
            ouid = client.resolve_ouid(target)
        rows = client.recent_matches(ouid, max(2, int(limit)), fresh=False)
        return real, rows

    def duo_between(self, left: str = "", right: str = "") -> dict[str, Any]:
        """타인 A · 타인 B 가 함께 뛴 판의 공동 승률·K/D·시너지.

        자유 1:1 스탯 비교를 대신하는 기능이다.  두 사람의 최근 경기 목록을
        ``match_id`` 로 맞춰 **같은 판**만 계산하고, 표본이 모자라면 숫자를
        지어내지 않고 이유를 돌려준다.  (내 듀오는 :meth:`duo_synergy`)
        """
        try:
            guard = self.account.guard("duo_between")
            if guard:
                return guard
            names = v140.split_targets([left, right], limit=2) if (left or right) else []
            if len(names) < 2:
                raise ValueError("두 사람의 닉네임을 모두 입력해 주세요")
            client = self._client()
            real_a, rows_a = self._recent_for(client, names[0])
            real_b, rows_b = self._recent_for(client, names[1])
            maps = {}
            for row in list(rows_a) + list(rows_b):
                mid = str(row.get("match_id") or "")
                if mid and mid not in maps:
                    detail = client.match_detail_cached(mid) or {}
                    if detail.get("match_map"):
                        maps[mid] = detail["match_map"]
            result = v140.duo_between(rows_a, rows_b, real_a, real_b, map_lookup=maps)
            self.account.use("duo_between")
            calls = int(getattr(client, "calls", 0))
            return {"ok": bool(result.get("available")), **result, "calls": calls,
                    "as_of": now_iso(),
                    "message": result.get("label") if result.get("available") else result.get("reason")}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # ------------------------------------------------------------ 전원 · 게임 모드
    def perf_state(self, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._perf_state and now - self._perf_state_at < 5:
            return {"ok": True, "perf": dict(self._perf_state)}
        try:
            state = winutil.perf_state()
        except Exception as exc:  # noqa: BLE001
            state = {"supported": False, "label": str(exc), "power": None, "game_mode": {}}
        perf_saved = self.store.utils().get("perf") or {}
        backup = perf_saved.get("backup")
        state["backup"] = backup
        state["applied_at"] = perf_saved.get("applied_at")
        # v1.3.1 — 스위치 ON/OFF 는 GUID 일치가 아니라 '적용해 둔 상태'로 판단한다.
        # (최고 성능이 없어 powercfg /duplicatescheme 으로 새 GUID 를 만들면
        #  GUID 비교가 실패해 스위치가 켜지지 않던 버그)
        state["applied"] = bool(perf_saved.get("applied_at"))
        state["active"] = bool(perf_saved.get("applied_at"))
        state["mode_name"] = perf_saved.get("mode")
        self._perf_state, self._perf_state_at = state, now
        return {"ok": True, "perf": dict(state)}

    def apply_perf_mode(self) -> dict[str, Any]:
        try:
            result = winutil.apply_perf_mode()
            if result.get("ok"):
                utils = self.store.utils()
                utils["perf"] = {"applied_at": now_iso(), "backup": result.get("backup"),
                                 "mode": (result.get("power_now") or {}).get("name")}
                self.store.save()
                try:
                    paths.perf_backup_file().write_text(
                        json.dumps(result.get("backup") or {}, ensure_ascii=False, indent=2), encoding="utf-8")
                except OSError:
                    pass
            self._perf_state = None
            return {**result, "perf": self.perf_state(force=True).get("perf")}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def restore_perf_mode(self) -> dict[str, Any]:
        try:
            backup = (self.store.utils().get("perf") or {}).get("backup")
            result = winutil.restore_perf_mode(backup)
            if result.get("ok"):
                utils = self.store.utils()
                utils["perf"] = {"applied_at": None, "backup": None,
                                 "restored_at": now_iso(), "mode": None}
                self.store.save()
            self._perf_state = None
            return {**result, "perf": self.perf_state(force=True).get("perf")}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # ------------------------------------------------------------ 트레이 · 단축키
    def _tray_state(self) -> dict[str, Any]:
        set_ = self.store.settings
        icon = tray_ref()
        state = {"supported": tray.available(), "active": False, "icon": False,
                 "hotkey": False, "hotkey_spec": "Ctrl+Shift+A", "error": None}
        try:
            state.update({k: v for k, v in tray.status(icon).items()})
        except Exception:  # noqa: BLE001
            pass
        state["background"] = bool(set_.get("tray_background"))
        state["hotkey_enabled"] = bool(set_.get("hotkey_enabled", True))
        state["hidden"] = bool(getattr(self._win, "_sa_hidden", False))
        return state

    def _apply_hotkey(self, enabled: bool) -> None:
        icon = tray_ref()
        if icon is None:
            return
        try:
            icon.set_hotkey(bool(enabled))
        except Exception:  # noqa: BLE001
            log.debug("hotkey could not change", exc_info=True)

    def tray_hide(self) -> dict[str, Any]:
        if not self._win:
            return self._ok(hidden=False, message="앱 창을 찾지 못했습니다")
        try:
            self._win.hide()
            try:
                self._win._sa_hidden = True
            except Exception:  # noqa: BLE001
                pass
            return self._ok(hidden=True,
                            message="트레이로 숨겼습니다 · 단축키(Ctrl+Shift+A) 또는 트레이 아이콘으로 다시 엽니다")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def tray_show(self) -> dict[str, Any]:
        if not self._win:
            return self._ok(hidden=False)
        try:
            self._win.show()
            try:
                self._win._sa_hidden = False
            except Exception:  # noqa: BLE001
                pass
            return self._ok(hidden=False)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def tray_state(self) -> dict[str, Any]:
        return {"ok": True, "tray": self._tray_state(), "state": self._build_state()}

    # ------------------------------------------------------------ v1.0.0 · 인게임 오버레이
    def _overlay_settings(self) -> dict[str, Any]:
        set_ = self.store.settings
        return {
            "enabled": bool(set_.get("overlay_enabled", True)),
            "hotkey_lobby": overlay.clean_hotkey(set_.get("overlay_hotkey_lobby"), "Insert"),
            "hotkey_scout": overlay.clean_hotkey(set_.get("overlay_hotkey_scout"), "Home"),
            "mapban_seconds": overlay.parse_seconds(set_.get("overlay_mapban_seconds"), 12.0),
            "party_seconds": overlay.parse_seconds(set_.get("overlay_party_seconds"), 15.0),
            "toast_enabled": bool(set_.get("toast_enabled", True)),
            "toast_position": (set_.get("toast_position") if set_.get("toast_position") in overlay.TOAST_POSITIONS else "br"),
            "toast_auto": bool(set_.get("toast_auto", True)),
            "scan_calib": {"offset_x": set_.get("overlay_scan_x") or 0,
                           "offset_y": set_.get("overlay_scan_y") or 0,
                           "scale": set_.get("overlay_scan_scale") or 1.0},
        }

    _overlay_settings._serializable = False  # type: ignore[attr-defined]

    def _cleanup_cache_once(self) -> None:
        """하루 한 번, 30일 지난 넥슨 매치 캐시만 정리한다 (기록·잔디·프리셋 불변)."""
        try:
            last = str(self.store.data.get("cache_cleanup_at") or "")[:10]
            if last == today_str():
                self._cache_cleanup = {"ok": True, "skipped": True}
                return
            self._cache_cleanup = nexon.purge_expired_cache(30)
            self.store.data["cache_cleanup_at"] = now_iso()
            self.store.save()
            log.info("nexon cache cleanup · %s", self._cache_cleanup)
        except Exception:  # noqa: BLE001 - 정리 실패는 앱을 막지 않는다
            log.exception("cache cleanup failed")
            self._cache_cleanup = {"ok": False}

    _cleanup_cache_once._serializable = False  # type: ignore[attr-defined]

    def _known_names(self) -> list[str]:
        """OCR 보정에 쓸 이름 — 순위표 · 최근 파티원 · 프리셋 · 내 닉네임."""
        names: list[str] = []
        me = (self.store.settings.get("nickname") or "").strip()
        if me:
            names.append(me)
        for row in self.store.leaderboard():
            name = str((row or {}).get("name") or "").strip()
            if name:
                names.append(name)
        try:
            for row in self._coplayers():
                if row.get("name"):
                    names.append(str(row["name"]))
        except Exception:  # noqa: BLE001
            pass
        for preset in self.store.party_presets():
            for member in (preset or {}).get("members") or []:
                names.append(str(member))
        return list(dict.fromkeys(n for n in names if n))[:80]

    _known_names._serializable = False  # type: ignore[attr-defined]

    def _map_entries_fast(self, names: list[str]) -> list[dict[str, Any]]:
        """파티원 맵 전적을 저장된 캐시에서만 모은다 (오버레이용 · API 0건)."""
        cache = self.store.barracks_cache()
        stored = self.store.data.get("party_maps") or {}
        entries: list[dict[str, Any]] = []
        for name in names:
            key = str(name or "").strip().lower()
            if not key:
                continue
            barracks = cache.get(name) or next(
                (b for b in cache.values() if str((b or {}).get("nickname") or "").lower() == key), None)
            maps = (barracks or {}).get("maps") or (stored.get(key) or {}).get("maps") or []
            entries.append({"name": name, "maps": maps,
                            "source": "cache" if maps else "none"})
        return entries

    _map_entries_fast._serializable = False  # type: ignore[attr-defined]

    def start_overlays(self) -> dict[str, Any]:
        """앱 시작 시 오버레이 단축키·경기 토스트 감시를 켠다 (main.py 가 부른다)."""
        result = self.apply_overlay_hotkeys()
        settings = self._overlay_settings()
        if settings.get("toast_enabled") and settings.get("toast_auto"):
            try:
                watcher = self._watcher()
                watcher.start()

                def _prime() -> None:
                    try:
                        match = toast.last_match(self._client(),
                                                (self.store.settings.get("nickname") or ""))
                        if match:
                            watcher.prime(match.get("match_id"))
                    except Exception:  # noqa: BLE001 - 첫 확인 실패는 무시
                        log.debug("toast prime failed", exc_info=True)

                __import__("threading").Thread(target=_prime, daemon=True).start()
            except Exception:  # noqa: BLE001
                log.debug("match watcher could not start", exc_info=True)
        return result

    start_overlays._serializable = False  # type: ignore[attr-defined]

    def apply_overlay_hotkeys(self) -> dict[str, Any]:
        """설정에 적힌 오버레이 단축키(기본 Insert/Home)를 실제 전역 단축키로 등록한다."""
        settings = self._overlay_settings()
        if not settings.get("enabled"):
            self._hotkeys.stop()
            return {"ok": True, "enabled": False, "hotkeys": self._hotkeys.state(),
                    "message": "오버레이가 꺼져 있습니다"}
        mapping = {"lobby": settings["hotkey_lobby"], "scout": settings["hotkey_scout"]}
        mapping = {k: v for k, v in mapping.items() if v and v != "None"}
        self._hotkeys.start(mapping, self._on_hotkey)
        state = self._hotkeys.state()
        return {"ok": bool(state.get("registered")), "enabled": True, "hotkeys": state,
                "message": ("단축키 등록 · " + ", ".join(state.get("registered") or [])) if state.get("registered")
                           else "단축키를 등록하지 못했습니다 (Windows 전용 / 키 선점 확인)"}

    def _on_hotkey(self, name: str) -> None:
        if name == "scout":
            self.overlay_scout_start()
        else:
            self.overlay_lobby_toggle()

    _on_hotkey._serializable = False  # type: ignore[attr-defined]

    def _watcher(self) -> toast.MatchWatcher:
        if self._match_watch is None:
            # v1.0.1 (#13) — 종료 후 5~30초 안에 뜨도록 18초 간격.  다만 사용자가 직접
            # 넣은 test_ 키(하루 1,000건)라면 45초로 늘려 한도를 지킨다.
            key = (self.store.settings.get("api_key") or "").strip()
            interval = 45.0 if key and key.startswith("test_") else toast.POLL_SECONDS
            self._match_watch = toast.MatchWatcher(
                client_factory=lambda: self._client(),
                nick_provider=lambda: (self.store.settings.get("nickname") or ""),
                on_match=self._on_match_finished,
                interval=interval,
            )
        return self._match_watch

    _watcher._serializable = False  # type: ignore[attr-defined]

    def _on_match_finished(self, match: dict[str, Any]) -> None:
        settings = self._overlay_settings()
        if not (settings.get("toast_enabled") and settings.get("toast_auto")):
            return
        self._show_toast(match)
        # v1.0.1 — 새 판이 감지되면 내 전적(병영수첩 캐시)도 바로 새로 받아 화면을 맞춘다
        try:
            __import__("threading").Thread(target=self.refresh_my_matches, kwargs={"force": True},
                                           daemon=True, name="sa-match-refresh").start()
        except Exception:  # noqa: BLE001
            log.debug("post-toast refresh failed", exc_info=True)

    _on_match_finished._serializable = False  # type: ignore[attr-defined]

    # ---------------------------------------------------------- 내 전적 자동 최신화 (65초)
    def refresh_my_matches(self, force: bool = False) -> dict[str, Any]:
        """1분 5초마다 내 경기 목록을 새로 읽고, 새 판이면 병영수첩까지 다시 받는다.

        v1.0.1 (#12·#17) — 목록은 ``fresh=True`` 로 캐시를 건너뛰고(2콜),
        새 match_id 가 생겼을 때만 무거운 병영수첩 재구성을 한다.
        """
        nick = (self.store.settings.get("nickname") or "").strip()
        if not nick:
            return {"ok": False, "skipped": "닉네임이 없습니다"}
        try:
            client = self._client()
            ouid = client.resolve_ouid(nick)
            rows = client.recent_matches(ouid, 8, fresh=True)
        except Exception as exc:  # noqa: BLE001
            self._match_refresh["error"] = str(exc)
            return {"ok": False, "message": nexon.friendly_error(str(exc))}
        newest = str((rows[0] or {}).get("match_id") or "") if rows else ""
        seen = self._match_refresh.get("newest") or self._newest_stored_match(nick)
        self._match_refresh.update({"at": now_iso(), "newest": newest or seen, "checked": True,
                                    "error": None})
        if not newest or (newest == seen and not force):
            return {"ok": True, "changed": False, "newest": newest}
        try:
            barracks = nexon.build_barracks(self._client(force=True), nick, progress=None)
            self.store.barracks_cache()[barracks["nickname"]] = barracks
            self.store.add_barracks_snapshot(barracks)
            try:
                self.store.merge_play_days(barracks.get("matches") or [])
            except Exception:  # noqa: BLE001
                log.debug("play days merge failed after refresh", exc_info=True)
            self.store.save()
            self._upsert_member(self._member_from_barracks(barracks))
            self._match_refresh["newest"] = newest
            self._match_refresh["updated"] = now_iso()
            self._push_state()
            return {"ok": True, "changed": True, "newest": newest,
                    "message": f"{barracks['nickname']} 전적 최신화 · {barracks['stats'].get('games')}판"}
        except Exception as exc:  # noqa: BLE001
            self._match_refresh["error"] = str(exc)
            return {"ok": False, "message": nexon.friendly_error(str(exc))}

    refresh_my_matches._serializable = False  # type: ignore[attr-defined]

    def _newest_stored_match(self, nick: str) -> str:
        try:
            barracks = self.store.barracks_cache().get(nick) or {}
            matches = barracks.get("matches") or []
            return str(barracks.get("newest_match") or (matches[0].get("match_id") if matches else "") or "")
        except Exception:  # noqa: BLE001
            return ""

    _newest_stored_match._serializable = False  # type: ignore[attr-defined]

    def _push_state(self) -> None:
        """백그라운드 갱신 결과를 화면에 밀어 줌 (실패해도 조용히)."""
        window = self._win
        if window is None:
            return
        try:
            window.evaluate_js("window.AL && AL.stateReload ? AL.stateReload() : null")
        except Exception:  # noqa: BLE001
            log.debug("state push failed", exc_info=True)

    _push_state._serializable = False  # type: ignore[attr-defined]

    def start_match_auto_refresh(self) -> None:
        """앱 시작 시 1회 + **65초(1분 5초)** 주기 내 전적 최신화 스레드 (#12)."""
        import threading

        if self._match_thread and self._match_thread.is_alive():
            return
        if not (self.store.settings.get("nickname") or "").strip():
            return
        key = (self.store.settings.get("api_key") or "").strip()
        # 사용자 test_ 키(하루 1,000건)면 5분으로 늘려 한도를 지킨다
        self._match_interval = 300 if key.startswith("test_") else MATCH_REFRESH_SECONDS
        self._match_stop.clear()

        def loop() -> None:
            while not self._match_stop.is_set():
                try:
                    self.refresh_my_matches()
                except Exception:  # noqa: BLE001
                    log.debug("match auto refresh failed", exc_info=True)
                if self._match_stop.wait(self._match_interval):
                    return

        self._match_thread = threading.Thread(target=loop, name="sa-match-auto", daemon=True)
        self._match_thread.start()

    start_match_auto_refresh._serializable = False  # type: ignore[attr-defined]

    def match_refresh_state(self) -> dict[str, Any]:
        return {"ok": True, "interval": getattr(self, "_match_interval", MATCH_REFRESH_SECONDS),
                **self._match_refresh}

    def _show_toast(self, match: dict[str, Any]) -> dict[str, Any]:
        settings = self._overlay_settings()
        lines = overlay.toast_lines(match)
        payload = {**lines, "seconds": overlay.TOAST_SECONDS}
        area = overlay.game_area()
        rect = overlay.toast_rect(area, settings.get("toast_position") or "br")
        return self._overlay.show("toast", payload, rect=rect, seconds=overlay.TOAST_SECONDS + 0.6)

    _show_toast._serializable = False  # type: ignore[attr-defined]

    # ------------------------------------------------------------ 오버레이 공개 API
    def overlay_state(self) -> dict[str, Any]:
        settings = self._overlay_settings()
        return {"ok": True, "overlay": {**self._overlay.state(), **{
                    "enabled": settings["enabled"], "hotkey_lobby": settings["hotkey_lobby"],
                    "hotkey_scout": settings["hotkey_scout"], "toast_position": settings["toast_position"],
                    "mapban_seconds": settings["mapban_seconds"], "scan_calib": settings["scan_calib"]}},
                "hotkeys": self._hotkeys.state(), "toast": self._watcher().state(),
                "cache_cleanup": self._cache_cleanup or {"ok": True, "skipped": True},
                "attribution": "Data based on NEXON Open API"}

    def overlay_payload(self, kind: str = "match") -> dict[str, Any]:
        """오버레이 페이지가 로드되자마자 자기 내용을 그릴 때 쓴다."""
        return {"ok": True, "payload": self._overlay.payload(kind), "kind": kind}

    def overlay_hide(self, kind: str = "") -> dict[str, Any]:
        if kind in overlay.KINDS:
            return self._ok(**self._overlay.hide(kind))
        self._overlay.hide_all()
        return self._ok(hidden=True)

    def overlay_preview(self, kind: str = "toast", position: str = "") -> dict[str, Any]:
        """설정 화면의 미리보기 — 실제 게임 없이 샘플 데이터로 띄운다."""
        if kind == "toast":
            sample = {"map": "제3보급창고", "result": "승", "kills": 14, "deaths": 6,
                      "damage": 1840, "streak": 3}
            settings = self._overlay_settings()
            area = overlay.game_area()
            rect = overlay.toast_rect(area, position or settings.get("toast_position") or "br")
            lines = overlay.toast_lines(sample)
            return self._ok(**self._overlay.show("toast", {**lines, "seconds": 5}, rect=rect,
                                                 seconds=5.6))
        if kind == "party":
            rows = self.store.overlay_cache().get("last_scan") or []
            area = overlay.game_area()
            payload = self._party_overlay_payload(rows or _sample_party_rows(self._store_nick()))
            if not (payload.get("plan") or {}).get("picks"):
                # 미리보기에는 맵 띠 예시를 보여 준다 (실데이터 없음) — 실제 표시에는 쓰지 않는다
                payload["plan"] = _sample_plan()
            return self._ok(**self._overlay.show("party", payload, rect=overlay.party_overlay_rect(area),
                                                 seconds=10))
        if kind == "scout":
            area = overlay.game_area()
            payload = {"tab": "scout", "rows": _sample_scout_rows(), "map": "제3보급창고"}
            return self._ok(**self._overlay.show("match", payload, rect=overlay.scout_overlay_rect(area),
                                                 seconds=12))
        return self._err("알 수 없는 미리보기 종류입니다")

    def _store_nick(self) -> str:
        return (self.store.settings.get("nickname") or "").strip()

    _store_nick._serializable = False  # type: ignore[attr-defined]

    # ------------------------------------------------------------ 파티 대기실 OCR
    def overlay_lobby_toggle(self) -> dict[str, Any]:
        """대기실·맵 밴 단축키(기본 Insert) — 열려 있으면 닫고, 아니면 맵 밴 → 파티 대기실 순서로 판별."""
        settings = self._overlay_settings()
        if not settings.get("enabled"):
            return self._ok(enabled=False, message="설정에서 오버레이를 켜 주세요")
        open_kinds = set(self._overlay.state().get("open") or [])
        if open_kinds & {"party", "match"}:
            self._overlay.hide("party")
            self._overlay.hide("match")
            return self._ok(closed=True)
        return self.overlay_mapban_start()

    def party_scan(self) -> dict[str, Any]:
        """대기실 5칸 OCR → 보정 → API 검증."""
        try:
            if os.name != "nt":
                return self._err("인게임 OCR은 Windows 에서만 동작합니다")
            settings = self._overlay_settings()
            area = overlay.game_area()
            client = self._client()
            known = self._known_names()
            scan = party_lobby.scan_party(client, area, known, calib=settings.get("scan_calib"))
            if scan.get("rows") and any((row or {}).get("name") for row in scan["rows"]):
                cache = self.store.overlay_cache()
                cache["last_scan_at"] = now_iso()
                cache["last_scan"] = scan["rows"]
                self.store.save()
                self._last_scan = scan["rows"]
            return {"ok": bool(scan.get("ok")), "rows": scan.get("rows") or [],
                    "names": scan.get("names") or [], "run": scan,
                    "recognized": int(scan.get("recognized") or 0),
                    "confirmed": int(scan.get("confirmed") or 0),
                    "stage": str(scan.get("stage") or ""),
                    "debug_file": str(scan.get("debug_file") or ""),
                    "presets": self.party_presets()["presets"],
                    "mapban": self._mapban_payload(scan.get("names") or []) if scan.get("rows") else None,
                    "message": scan.get("message") or ""}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, rows=[])

    def _party_overlay_payload(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """파티 오버레이 한 화면분 — 배지 5칸 + 카드 밑 맵 띠(픽 3·밴 3)."""
        area = overlay.game_area()
        rect = overlay.party_overlay_rect(area)
        names = [str(r.get("name") or "") for r in rows if r.get("name")]
        plan: dict[str, Any] = {"picks": [], "bans": []}
        try:
            entries = self._map_entries_fast(names)
            full = v150.party_map_plan(entries)
            if full.get("available"):
                plan = {"picks": full.get("picks") or [], "bans": full.get("bans") or [],
                        "headline": full.get("headline") or ""}
        except Exception:  # noqa: BLE001 - 맵 띠는 보조 정보
            log.debug("party overlay map plan failed", exc_info=True)
        return {"rows": rows, "presets": self.party_presets()["presets"],
                "plan": plan, "me": self._store_nick(),
                "layout": overlay.party_layout(rect, area)}

    def _party_autofill(self, rows: list[dict[str, Any]]) -> None:
        """v1.0.1 (#11) — 대기실 단축키(기본 Insert)로 읽은 5인을 파티랭크 맵 밴·픽에 채운다."""
        names = [str(r.get("name") or "").strip() for r in rows if r.get("name")]
        if not names:
            return
        window = self._win
        if window is None:
            return
        try:
            window.evaluate_js("window.AL && AL.partyAutoFill ? AL.partyAutoFill(%s) : null"
                              % json.dumps(names, ensure_ascii=False))
        except Exception:  # noqa: BLE001 - 화면이 없으면 조용히
            log.debug("party autofill push failed", exc_info=True)

    _party_autofill._serializable = False  # type: ignore[attr-defined]

    def overlay_party_show(self) -> dict[str, Any]:
        settings = self._overlay_settings()
        if not settings.get("enabled"):
            return self._err("오버레이가 꺼져 있습니다")
        scan = self.party_scan()
        rows = scan.get("rows") or []
        if not rows:
            # 스캔 함수 자체가 행을 반환하지 못한 경우에만 직전 판 API 폴백을 사용한다.
            # 5개 실시간 OCR 슬롯이 비어 있는 경우 과거 명단을 현재 대기실 결과처럼 보이지 않는다.
            try:
                me = self._store_nick()
                fallback = party_lobby.last_match_teams(self._client(), me) if me else {"ok": False}
                mine = [r for r in (fallback.get("rows") or []) if r.get("team") in ("mine", "내 팀", "red")][:5]
                if mine:
                    rows = [{"slot": i, "name": r.get("name"), "status": "ok", "score": 1.0}
                            for i, r in enumerate(mine)]
                    scan = {**scan, "ok": True, "rows": rows, "names": [r["name"] for r in rows],
                            "message": "직전 판 API 에서 우리 팀 5명을 불러왔습니다"}
                    self._last_scan = rows
            except Exception:  # noqa: BLE001 - 폴백 실패는 원래 메시지를 보여 준다
                log.debug("party fallback failed", exc_info=True)
        if not rows:
            # OCR 및 직전 경기 API 폴백이 모두 실패해도 Insert 오버레이 창은 표시한다.
            # 빈 슬롯으로 화면을 열어 사용자가 인식 실패 여부를 확인할 수 있게 한다.
            rows = [
                {"slot": i, "name": "", "status": "empty", "score": 0.0}
                for i in range(5)
            ]
            scan = {
                **scan,
                "rows": rows,
                "names": [],
                "message": scan.get("message") or "닉네임을 읽지 못했습니다 · 대기실 화면을 확인해 주세요",
            }
        payload = self._party_overlay_payload(rows)
        payload["scan_message"] = str(scan.get("message") or "")
        payload["debug_file"] = str(scan.get("debug_file") or (scan.get("run") or {}).get("debug_file") or "")
        shown = self._overlay.show("party", payload, rect=overlay.party_overlay_rect(overlay.game_area()),
                                   seconds=settings.get("party_seconds"))
        # 5인을 읽었으면 앱 전술 도구 슬롯에도 채우고 분석까지 돌린다 (#11)
        self._party_autofill(rows)
        return {**scan, **shown, "kind": "party"}

    def party_presets(self) -> dict[str, Any]:
        return {"ok": True, "presets": party_lobby.preset_payload(self.store.party_presets())}

    def save_party_preset(self, name: str = "", members: Any = None, use_scan: bool = True) -> dict[str, Any]:
        try:
            rows = party_lobby.clean_members(members)
            if not rows and use_scan and self._last_scan:
                rows = [str(r.get("name") or "") for r in self._last_scan if r.get("name")]
            if not rows:
                rows = party_lobby.clean_members([self._store_nick()])
            entry = self.store.save_party_preset(name, rows)
            return self._ok(preset=entry, presets=self.party_presets()["presets"],
                            state=self._build_state(), message=f"프리셋 저장 · {entry['name']} ({len(entry['members'])}명)")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def delete_party_preset(self, name: str = "") -> dict[str, Any]:
        try:
            removed = self.store.delete_party_preset(name)
            return self._ok(removed=removed, presets=self.party_presets()["presets"],
                            message="프리셋을 삭제했습니다" if removed else "해당 프리셋이 없습니다")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def load_party_preset(self, name: str = "") -> dict[str, Any]:
        rows = party_lobby.preset_payload(self.store.party_presets())
        hit = next((r for r in rows if r["name"] == str(name or "").strip()), None)
        if not hit:
            return self._err("프리셋을 찾지 못했습니다")
        return self._ok(preset=hit, members=hit["members"], mapban=self._mapban_payload(hit["members"]),
                        message=f"프리셋 · {hit['name']} ({len(hit['members'])}명)")

    # ------------------------------------------------------------ 맵 밴
    def _mapban_payload(self, names: list[str]) -> dict[str, Any]:
        """5개 카드 이름 + 파티/내 맵 전적 → PICK/BAN 배지."""
        area = overlay.game_area()
        rects = overlay.mapban_name_rects(area, self._overlay_settings().get("scan_calib"))
        shot = capture_mod.ocr_regions([(r["x"], r["y"], r["w"], r["h"]) for r in rects])
        card_names: list[str] = []
        if shot.get("ok"):
            for lines in shot.get("lines") or []:
                card_names.append(_map_line(lines))
        # 맵 이름으로 **확인된 카드**가 2개 이상이어야 맵 밴 화면으로 본다.
        # (대기실·채팅 UI 글자를 맵 이름으로 오인하지 않도록)
        known = [(name, v150.map_key(name)) for name in card_names]
        known = [(name, key) for name, key in known if key]
        if len(known) < 2:
            return {"ok": False, "cards": [], "source": "ocr-failed",
                    "message": "맵 이름을 읽지 못했습니다 · 맵 밴 화면에서 다시 눌러 주세요"}
        table: dict[str, dict[str, Any]] = {}
        for entry in self._map_entries_fast(names or [self._store_nick()]):
            for row in entry.get("maps") or []:
                key = v150.map_key(row.get("map"))
                slot = table.setdefault(key, {"map": row.get("map"), "win": 0.0, "games": 0, "_w": 0.0})
                games = float(row.get("games") or 0)
                slot["games"] += int(games)
                slot["_w"] += float(row.get("win") or 0) * games
        entries = []
        for name in card_names:
            key = v150.map_key(name)
            slot = table.get(key) or {}
            games = int(slot.get("games") or 0)
            win = round(slot["_w"] / games, 1) if games else None
            entries.append({"map": name, "win": win, "games": games})
        rows = overlay.verdict_rows(entries)
        return {"ok": True, "cards": rows, "maps": card_names, "source": "nexon-cache",
                "message": f"맵 {len([n for n in card_names if n])}개 인식 · 픽/밴 추천"}

    def overlay_mapban_start(self) -> dict[str, Any]:
        settings = self._overlay_settings()
        if not settings.get("enabled"):
            return self._err("오버레이가 꺼져 있습니다")
        if os.name != "nt":
            return self._err("인게임 오버레이는 Windows 에서만 동작합니다")
        preset = party_lobby.preset_payload(self.store.party_presets())
        names: list[str] = []
        if self._last_scan:
            names = [str(r.get("name") or "") for r in self._last_scan if r.get("name")]
        if not names and preset:
            names = list(preset[0].get("members") or [])
        if not names:
            names = [self._store_nick()]
        plan = self._mapban_payload(names)
        if not plan.get("ok"):
            # 맵 밴 화면이 아니면 대기실 스캔으로 넘어간다
            party = self.overlay_party_show()
            if party.get("ok"):
                return {**party, "screen": "party"}
            return {**plan, "screen": "unknown", "party": party.get("message")}
        area = overlay.game_area()
        payload = {"tab": "mapban", "cards": plan.get("cards") or [],
                   "maps": plan.get("maps") or [], "seconds": settings.get("mapban_seconds"),
                   "tab_pad": overlay.mapban_tab_pad(area)}
        shown = self._overlay.show("match", payload, rect=overlay.mapban_overlay_rect(area),
                                    seconds=settings.get("mapban_seconds"))
        if shown.get("ok"):
            self._overlay.arm_disappear("match", rect=overlay.mapban_overlay_rect(area))
        return {**plan, **shown, "screen": "mapban", "kind": "match"}

    # ------------------------------------------------------------ 10초 상대 스카우팅
    def overlay_scout_start(self, red: Any = None, blue: Any = None, map_name: str = "") -> dict[str, Any]:
        settings = self._overlay_settings()
        if not settings.get("enabled"):
            return self._err("오버레이가 꺼져 있습니다")
        guard = self._account_guard("overlay")
        if guard:
            return guard
        open_kinds = set(self._overlay.state().get("open") or [])
        if open_kinds & {"party", "match"}:
            self._overlay.hide("party")
            self._overlay.hide("match")
            return self._ok(closed=True)
        try:
            client = self._client()
            me = self._store_nick()
            rows: list[dict[str, Any]] = []
            source = "ocr"
            if red or blue:
                rows = [{"team": "red", "name": n, "slot": i} for i, n in enumerate(party_lobby.clean_members(red, 5))]
                rows += [{"team": "blue", "name": n, "slot": i} for i, n in enumerate(party_lobby.clean_members(blue, 5))]
                source = "manual"
            else:
                area = overlay.game_area()
                scan = party_lobby.scan_teams(area, self._known_names(), client)
                rows = scan.get("rows") or []
                if len([r for r in rows if r.get("name")]) < 6:
                    fallback = party_lobby.last_match_teams(client, me)
                    if fallback.get("ok"):
                        rows, source = fallback.get("rows") or [], "last-match"
            if not me:
                return self._err("설정에서 서든 닉네임을 먼저 저장해 주세요")
            split = party_lobby.team_of(rows, me)
            opponents = [str(r.get("name") or "") for r in split.get("opponents") or [] if r.get("name")]
            if not opponents:
                return self._err("상대 팀을 가르지 못했습니다 · 10인 화면에서 다시 눌러 주세요",
                                 rows=rows)
            area = overlay.game_area()
            payload = {"tab": "scout", "map": map_name or "", "rows": [],
                       "loading": True, "mine": [r.get("name") for r in split.get("mine") or []],
                       "opponents": opponents, "source": source, "seconds": 12}
            shown = self._overlay.show("match", payload, rect=overlay.scout_overlay_rect(area),
                                        seconds=14)

            def _update(rows_payload: list[dict[str, Any]]) -> None:
                self._overlay.refresh("match", {**payload, "rows": rows_payload,
                                                "loading": len(rows_payload) == 0})

            result = scouting.quick_scout(client, opponents, map_name, on_update=_update)
            if result.get("busy"):
                return {"ok": False, "busy": True, "shown": shown, "rows": [],
                        "message": result.get("message") or "이미 같은 상대를 분석 중입니다"}
            self._account_use("overlay")
            payload["rows"] = result.get("rows") or []
            payload["loading"] = False
            payload["elapsed"] = result.get("elapsed")
            payload["stale"] = bool(result.get("stale"))
            self._overlay.refresh("match", payload)
            cache = self.store.overlay_cache()
            cache["last_team"] = payload["rows"]
            self.store.save()
            return {"ok": bool(result.get("ok")), "shown": shown, "rows": payload["rows"],
                    "opponents": opponents, "mine": payload["mine"], "source": source,
                    "elapsed": result.get("elapsed"),
                    "message": result.get("message") or f"상대 {len(opponents)}명 분석"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, rows=[])

    def overlay_toast_preview(self, position: str = "") -> dict[str, Any]:
        return self.overlay_preview("toast", position=position)

    def overlay_toast_last(self) -> dict[str, Any]:
        """방금 끝난 판으로 토스트를 띄운다 (수동)."""
        try:
            match = toast.last_match(self._client(), self._store_nick())
            if not match:
                return self._err("최근 경기를 찾지 못했습니다")
            return self._ok(**self._show_toast(match), match=match,
                            message=f"{match.get('map') or '최근 경기'} 결과 토스트")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # ------------------------------------------------------------ misc
    def app_info(self) -> dict[str, Any]:
        return {
            "ok": True,
            "version": paths.APP_VERSION,
            "data_dir": str(paths.data_dir()),
            "data_file": str(self.store.path),
            "platform": platform.platform(),
            "python": platform.python_version(),
        }

    def open_devtools(self) -> dict[str, Any]:
        return self._ok()


def _map_line(lines: Any) -> str:
    """맵 카드 이름 OCR 줄 → 맵 이름 (괄호 접미사는 남겨 두고 매칭에서 뗀다)."""
    best = ""
    for line in (lines or []):
        text = str(line or "").strip().replace("　", " ")
        text = "".join(ch for ch in text if ch.isalnum() or ch in "()-심플폭파클래식[]")
        if len(text) < 2:
            continue
        if len(text) > len(best):
            best = text
    return best[:24]


def _sample_party_rows(me: str) -> list[dict[str, Any]]:
    names = [me or "게스트", "시티캣", "프로방스", "제5보급", "크로스포트"]
    return [{"slot": i, "name": name, "status": "ok", "score": 1.0}
            for i, name in enumerate(names[:5])]


def _sample_plan() -> dict[str, Any]:
    """미리보기용 맵 띠 (실제 파티 전적이 없을 때만).  실제 표시 경로에는 쓰지 않는다."""
    names = ["제3보급창고", "프로방스", "시티캣", "크로스포트", "공항", "빅보급창고"]
    picks = [{"map": n, "expected": e, "games": 9 - i, "image": v150.map_image(n)}
             for i, (n, e) in enumerate(zip(names[:3], (61.0, 57.0, 54.0)))]
    bans = [{"map": n, "expected": e, "games": 8 - i, "image": v150.map_image(n)}
            for i, (n, e) in enumerate(zip(names[3:], (43.0, 41.0, 39.0)))]
    return {"picks": picks, "bans": bans, "headline": "미리보기"}


def _sample_scout_rows() -> list[dict[str, Any]]:
    """오버레이 '상대 분석' 미리보기용 — 실제 응답과 같은 솔로/파티 구조."""
    from . import scouting as _sc

    rows: list[dict[str, Any]] = []
    spec = [("적팀1", "스나", "별레전드", 3610, 57.1, 61.2, "그랜드마스터 III", 3400, 48.3, 55.0),
            ("적팀2", "라플", "그랜드마스터 II", 3240, 44.0, 52.9, "마스터 I", 2500, 51.0, 58.0),
            ("적팀3", "스나", "그랜드마스터 II", 3261, 55.0, 63.0, "언랭크", 0, None, None),
            ("적팀4", "스나", "불레전드", 3720, 52.0, 58.0, "레전드", 3600, 46.0, 54.0),
            ("적팀5", "라플", "마스터 III", 2900, 47.0, 49.0, "골드 I", 1900, 53.0, 56.0)]
    for name, weapon, solo_t, solo_rp, solo_w, solo_kd, party_t, party_rp, party_w, party_kd in spec:
        row = {
            "name": name, "clan": "", "weapon": weapon,
            "solo": solo_rp, "solo_tier": solo_t,
            "party": party_rp or None, "party_tier": party_t if party_rp else "언랭크",
            "solo_stats": {"tier": solo_t, "rp": solo_rp, "win": solo_w, "kd": solo_kd,
                           "games": 12, "short": _sc.short_tier(solo_t)},
            "party_stats": {"tier": party_t if party_rp else "", "rp": party_rp, "win": party_w,
                            "kd": party_kd, "games": 8 if party_rp else 0,
                            "short": _sc.short_tier(party_t) if party_rp else "언랭"},
            "kd": solo_kd, "win": solo_w,
            "badge": "", "error": None,
        }
        row["badge"] = _sc.badge_text({**row, "solo_stats": {"games": 12, "win": solo_w, "kd": solo_kd},
                                       "party_stats": {"games": 8 if party_rp else 0, "win": party_w, "kd": party_kd}})
        rows.append(row)
    return rows
