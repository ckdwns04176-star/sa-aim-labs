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

from . import barracks as barracks_mod, kovak, nexon, nvidia, paths, scoring, supabase_sync, tilt, tray, updates, v140, v150, winutil
from .store import Store, now_iso, today_str

GAMES = ("kovak", "aimlab")
WEAPONS = ("sniper", "rifle")

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
    def __init__(self, store: Store | None = None):
        self.store = Store() if store is None else store
        self._drs_state_at = 0.0
        self._drs_state: dict[str, Any] | None = None
        self._perf_state_at = 0.0
        self._perf_state: dict[str, Any] | None = None
        self._busy: str | None = None
        self._last_progress = 0.0
        # v1.4.0 — 연패 브레이크 감시 (설정 ON + 서든 실행 중일 때만 동작)
        self._tilt: tilt.TiltWatcher | None = None
        self._tilt_lock = __import__("threading").Lock()
        # v1.5.0 — 코박스·에임랩 종료 감지 · 클라우드 자동 백업
        self._trainer_watch: v150.TrainerWatch | None = None
        self._trainer_lock = __import__("threading").Lock()
        self._cloud_stop = __import__("threading").Event()
        self._cloud_thread: Any = None
        # v1.5.0 — 모니터 해상도·OS 배율 (main.py 가 창을 만들기 전에 채운다)
        self._display: dict[str, Any] = {"width": 0, "height": 0, "scale": 1.0, "auto_scale": 100}

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
        store = self.store
        records = store.records()
        today = _dt.date.today()
        settings = store.public_settings()
        excluded_names = [str(n) for n in (settings.get("excluded_scenarios") or []) if str(n).strip()]
        style = scoring.user_style(records, today=today)
        attendance = store.attendance()
        streak = scoring.streak(records, today, attendance)

        more_rows = store.more()
        summaries: dict[str, Any] = {}
        for game in GAMES:
            for weapon in WEAPONS:
                key = f"{game}-{weapon}"
                try:
                    summary = scoring.summary(records, game, weapon, today)
                    recs = self._today_recs(summary["values"], records, game, weapon, today)
                    # v1.5.0 — '추천에서 제외' 한 시나리오는 오늘의 추천에서만 뺀다
                    summary["recs"] = v150.apply_exclusions(recs, excluded_names)
                    summary["excluded_count"] = len(recs) - len(summary["recs"])
                    # 오늘 `+ 더 연습하기` 로 연 시나리오 (아직 화면에 남아 있어야 한다)
                    entry = next((m for m in reversed(more_rows)
                                  if m.get("date") == today.isoformat()
                                  and m.get("game") == game and m.get("weapon") == weapon), None)
                    names = [n for n in ((entry or {}).get("names") or [])
                             if n not in {r["name"] for r in recs}]
                    summary["more"] = scoring.recs_for_names(
                        names, values=summary["values"], records=records,
                        game=game, weapon=weapon, today=today,
                    )
                    summary["style"] = style
                    summary["streak"] = streak
                except Exception:  # noqa: BLE001 - one broken summary must not break boot
                    log.exception("state · summary %s failed", key)
                    summary = scoring.summary([], game, weapon, today)
                    summary["recs"] = []
                    summary["more"] = []
                    summary["style"] = style
                    summary["streak"] = streak
                summaries[key] = summary

        # 통합 에임 프로필 — 트레이너/무기와 무관하게 하나의 점수
        profile = scoring.summary(records, "kovak", "sniper", today)
        profile["today_scenarios"] = scoring.today_scenario_count(records, today=today)

        try:
            barracks = store.barracks_cache().get(settings.get("nickname") or "", None)
            if barracks is None and store.barracks_cache():
                barracks = list(store.barracks_cache().values())[-1]
            members = self._ensure_me(barracks)
        except Exception:  # noqa: BLE001
            log.exception("state · barracks/leaderboard failed")
            barracks, members = None, []
        if isinstance(barracks, dict):
            # v1.3.0 — 첫 설치 직후에도 '직전 연습일' 비교가 가능하도록, 저장된 병영수첩
            # 캐시의 최신화 시각을 첫 스냅샷으로 남긴다 (이미 스냅샷이 있으면 건드리지 않음).
            try:
                if not self.store.barracks_history():
                    stamp = str(barracks.get("updated_at") or "")[:10]
                    if stamp:
                        self.store.barracks_history().append({
                            "date": stamp, "at": str(barracks.get("updated_at") or ""),
                            "nickname": barracks.get("nickname"),
                            "stats": barracks.get("stats") or {},
                            "tier": barracks.get("tier") or {},
                            "basic": {"clan": (barracks.get("basic") or {}).get("clan")},
                        })
                        self.store.save()
            except Exception:  # noqa: BLE001 - 스냅샷 시드는 실패해도 화면은 그린다
                log.debug("state · barracks snapshot seed failed", exc_info=True)
            # 🧭 최근 100판 플레이 코칭 — 이미 받아 둔 지표만으로 매번 새로 계산
            try:
                barracks["play_coaching"] = scoring.play_coaching(barracks)
            except Exception:  # noqa: BLE001
                log.exception("state · play coaching failed")
                barracks["play_coaching"] = []

        if light:
            utils_state = {
                "pending": True, "supported": os.name == "nt", "mode": None, "alias": False,
                "backups": [], "points": [], "modes": {}, "missing": [], "last_backup": None,
                "game_running": None, "writable": False, "drs_exists": False, "drs_dir": "",
                "error": None, "needs_admin": False, "accel": None,
                "stairfix": {"ready": False, "applied": False, "value": None, "label": "확인 중",
                             "target": "0x00401241"},
                "last_backup_at": store.utils().get("last_backup_at"),
                "last_restore_at": store.utils().get("last_restore_at"),
            }
        else:
            utils_state = self._drs_summary()
            try:
                utils_state["accel"] = winutil.get_mouse_accel() if os.name == "nt" else self.store.utils().get("accel")
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
            goals = self._goals(barracks)
        except Exception:  # noqa: BLE001
            log.exception("state · goals failed")
            goals = []

        # ── v1.2.0 · 잔디 히트맵 · 주간 목표 · 훈련 전후 비교 (모두 로컬 계산) ──
        try:
            heatmap_state = scoring.heatmap(records, 12, today, attendance)
        except Exception:  # noqa: BLE001
            log.exception("state · heatmap failed")
            heatmap_state = None
        try:
            week_state = scoring.weekly_progress(records, self.store.settings.get("week_goals"), today, attendance)
        except Exception:  # noqa: BLE001
            log.exception("state · weekly goals failed")
            week_state = None
        try:
            impact_state = scoring.training_impact((barracks or {}).get("matches") or [], records, today)
        except Exception:  # noqa: BLE001
            log.exception("state · training impact failed")
            impact_state = None

        # ── v1.3.0 · 18단계 티어 분포 · 오늘 첫 실행 병영수첩 변화 · 맵 가이드 ──
        try:
            distribution = self._tier_distribution(profile)
        except Exception:  # noqa: BLE001
            log.exception("state · tier distribution failed")
            distribution = scoring.tier_distribution()
        try:
            digest = self._digest()
        except Exception:  # noqa: BLE001
            log.exception("state · digest failed")
            digest = {"available": False, "reason": "병영수첩 변화를 계산하지 못했습니다"}
        try:
            guides = scoring.map_guides((barracks or {}).get("maps") or [])
        except Exception:  # noqa: BLE001
            log.exception("state · map guides failed")
            guides = []

        # ── v1.4.0 · 오늘 랭크 컨디션 · 일자별 히스토리 · 온보딩 ──
        try:
            condition = v140.rank_condition((barracks or {}).get("matches") or [])
            stored = self.store.data.get("condition_history") or []
            if condition.get("available"):
                history = v140.condition_history(stored, condition, today=today.isoformat())
                if history != stored:
                    self.store.data["condition_history"] = history
                    self.store.save()
            else:
                history = stored
            condition["history"] = history[-14:]
        except Exception:  # noqa: BLE001
            log.exception("state · rank condition failed")
            condition = {"available": False, "verdict": None, "label": "전적 없음", "history": []}
        try:
            gate_state = updates.gate(updates.cached(), paths.APP_VERSION)
        except Exception:  # noqa: BLE001
            gate_state = {"blocked": False, "reason": "unknown"}

        # ── v1.5.0 · 테마·배율·맵 이미지·등락 뱃지·사용자 시나리오·병영수첩 링크 ──
        theme_key = v150.clean_theme(settings.get("theme"))
        ui_scale = self.effective_ui_scale()
        excluded = [str(n) for n in (settings.get("excluded_scenarios") or []) if str(n).strip()]
        try:
            deltas = v150.delta_badges(self.store.barracks_history())
        except Exception:  # noqa: BLE001
            log.exception("state · delta badges failed")
            deltas = []
        custom_rows = [r for r in self.store.custom_scenarios() if isinstance(r, dict) and r.get("name")]
        library_counts = scoring.scenario_library()
        for row in custom_rows:
            game = row.get("game") if row.get("game") in GAMES else "kovak"
            for weapon in WEAPONS:
                library_counts[f"{game}-{weapon}"] = library_counts.get(f"{game}-{weapon}", 0) + 1
        scenario_rows = {f"{g}-{w}": scoring.scenarios_for(g, w) for g in GAMES for w in WEAPONS}
        for row in custom_rows:
            game = row.get("game") if row.get("game") in GAMES else "kovak"
            area = str(row.get("area") or "precision")
            entry = {"name": row["name"], "area": area, "area_key": area,
                     "area_idx": scoring.AREA_INDEX.get(area, 0), "priority": 99,
                     "custom": True, "diff": row.get("diff"), "play_url": row.get("play_url"),
                     "stats_url": row.get("stats_url"), "memo": row.get("memo"),
                     "excluded": v150.is_excluded(row["name"], excluded)}
            for weapon in WEAPONS:
                scenario_rows[f"{game}-{weapon}"].append(dict(entry))
        links = self._barracks_links(barracks)
        try:
            watch_state = self._trainer_state()
        except Exception:  # noqa: BLE001
            watch_state = {"enabled": bool(settings.get("watch_trainers", True)), "supported": os.name == "nt",
                           "running": [], "last": None}

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
            },
            "settings": settings,
            "nexon": {
                "connected": bool((store.settings.get("api_key") or "").strip()),
                "nickname": store.settings.get("nickname") or "",
                "last_barracks_at": (barracks or {}).get("updated_at"),
            },
            "barracks": barracks,
            "leaderboard": nexon.leaderboard_payload(members),
            "summaries": summaries,
            "profile": {
                "values": profile["values"], "score": profile["score"],
                "tier": profile["tier"], "grade": profile["grade"],
                "days": profile["days"], "record_count": profile["record_count"],
                "today_scenarios": profile["today_scenarios"],
                "trainers": profile["trainers"], "daily": profile["daily"],
                "week_delta": profile["week_delta"], "integrated": True,
            },
            "library": library_counts,
            "scenarios": scenario_rows,
            "excluded_scenarios": excluded,
            "custom_scenarios": custom_rows,
            "map_index": v150.map_index_payload(),
            "pbs": scoring.pb_rows(records),
            "memos": dict(store.memos()),
            "today_scores": scoring.today_scores(records, today),
            "philosophy": scoring.WEAPON_PHILOSOPHY,
            "style": style,
            "streak": streak,
            "records": records[-200:],
            "record_count": len(records),
            "areas": scoring.AREAS,
            "tiers": scoring.TIERS,
            "utils": utils_state,
            "cloud": cloud_state,
            "goals": goals,
            "heatmap": heatmap_state,
            "week": week_state,
            "impact": impact_state,
            "update": updates.cached(),
            "tray": self._tray_state(),
            "digest": digest,
            "tier_distribution": distribution,
            "tier_details": [scoring.tier_detail(i, profile["score"], distribution, profile["values"])
                             for i in range(len(scoring.TIERS))],
            "map_guides": guides,
            "condition": condition,
            "condition_history": condition.get("history") or [],
            "onboarded": bool(settings.get("onboarded")),
            "onboarding": {
                # v1.4.0 — 로컬 닉네임·API 키·클라우드 세션이 모두 없을 때만 첫 화면을 띄운다.
                "needed": (not bool(settings.get("onboarded"))
                           and not settings.get("nickname")
                           and not settings.get("api_key_set")
                           and not bool((cloud_state or {}).get("signed_in"))),
                "has_local": bool(settings.get("nickname") or settings.get("api_key_set")),
                "cloud_signed_in": bool((cloud_state or {}).get("signed_in")),
                "weapon": settings.get("weapon") or "sniper",
                "game": settings.get("game") or "kovak"},
            "cloud_config": {"url": (settings.get("supabase") or {}).get("url", ""),
                             "email": (cloud_state or {}).get("email")},
            "update_gate": gate_state,
            "tilt": {"enabled": bool(settings.get("tilt_watch", True)),
                     "interval": int(settings.get("tilt_interval") or tilt.DEFAULT_INTERVAL),
                     "supported": os.name == "nt"},
            "v120": True,
            "v130": True,
            "v140": True,
            "v150": True,
            "theme": theme_key,
            "themes": [dict(t) for t in v150.THEMES.values()],
            "ui_scale": ui_scale,
            "ui_scale_setting": v150.clean_ui_scale(settings.get("ui_scale")),
            "ui_scale_auto": bool(settings.get("ui_scale_auto", True)),
            "display": dict(self._display),
            "ui_scales": list(v150.UI_SCALES),
            "delta": deltas,
            "barracks_links": links,
            "trainer_watch": watch_state,
            "cloud_auto": {**self.store.cloud_auto(), "enabled": bool(settings.get("auto_cloud", True))},
        }

    # ------------------------------------------------------------ v1.3.0 helpers
    def _digest(self) -> dict[str, Any]:
        """오늘 첫 실행용 직전 연습일 대비 병영수첩 변화 + 한마디 총고."""
        digest = scoring.barracks_digest(self.store.barracks_history())
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

    def _tier_distribution(self, profile: dict[str, Any]) -> dict[str, Any]:
        """18단계 티어 사용자 분포 — Supabase 집계 → 부족하면 기준 분포."""
        counts: list[int] | None = None
        total = 0
        source = "baseline"
        try:
            url, key = self._cloud_credentials()
            rows = supabase_sync.SupabaseClient(url, key).aim_scores(2000)
            if rows:
                buckets = [0] * len(scoring.TIERS)
                for row in rows:
                    try:
                        buckets[scoring.tier_index(int(float(row.get("score") or 0)))] += 1
                    except (TypeError, ValueError):
                        continue
                total = sum(buckets)
                if total >= 20:
                    counts, source = buckets, "live"
        except Exception:  # noqa: BLE001 - 집계 실패는 기준 분포로
            log.debug("tier distribution · live aggregation unavailable", exc_info=True)
        if counts is None:
            # 실제 집계가 없으면 기준 분포 — 내 점수는 티어표에서 따로 강조한다.
            total = 0
        return scoring.tier_distribution(counts, source=source, total_users=total or None)

    def reset_scores(self, confirm: str = "") -> dict[str, Any]:
        """에임 연습 점수·로그 전체 초기화 — UI 가 두 번 확인한 뒤에만 부른다."""
        if str(confirm).strip() != "초기화":
            return self._err("확인 문구가 일치하지 않습니다 · '초기화'를 입력해 주세요", cancelled=True)
        try:
            removed = self.store.reset_scores()
            self._drs_state = None
            return self._ok(removed=removed, state=self._build_state(),
                            message=f"점수 기록 {removed.get('records', 0)}건을 초기화했습니다 · 설정·순위표는 그대로입니다")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

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
            "signed_in": bool(cloud.get("access_token")),
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
        return out[:10]

    # ------------------------------------------------------------ settings
    _MASK_CHARS = ("\u2022", "*", "…")

    @classmethod
    def _looks_masked(cls, value: str) -> bool:
        """Never let the masked preview string overwrite the real secret."""
        text = (value or "").strip()
        return bool(text) and any(ch in text for ch in cls._MASK_CHARS)

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {"nickname", "api_key", "kovak_path", "aimlab_path", "auto_sync", "auto_launch",
                   "tray_background", "notify", "autosave", "goals", "cloud_email",
                   "hotkey_enabled", "kovak_stats_path", "kovak_autofill", "week_goals",
                   "tilt_watch", "tilt_interval", "weapon", "game", "onboarded",
                   "theme", "ui_scale", "ui_scale_auto", "excluded_scenarios", "watch_trainers", "auto_cloud"}
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
            elif key == "week_goals" and isinstance(value, dict):
                clean_goals: dict[str, int] = {}
                for k in ("days", "games"):
                    try:
                        number = int(float(value.get(k)))
                    except (TypeError, ValueError):
                        continue
                    clean_goals[k] = max(1, min(7 if k == "days" else 200, number))
                value = clean_goals
            elif key in ("kovak_stats_path", "kovak_path", "aimlab_path"):
                value = str(value or "").strip()[:400]
            elif key == "theme":
                value = v150.clean_theme(value)
            elif key == "ui_scale":
                value = v150.clean_ui_scale(value)
                clean["ui_scale_auto"] = False
            elif key == "ui_scale_auto":
                value = bool(value)
            elif key == "excluded_scenarios":
                if not isinstance(value, (list, tuple)):
                    continue
                seen: list[str] = []
                for name in value:
                    text = str(name or "").strip()[:80]
                    if text and text.lower() not in {s.lower() for s in seen}:
                        seen.append(text)
                value = seen[:200]
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
        if "watch_trainers" in clean:
            try:
                watcher = self._trainer_watch_obj()
                watcher.enabled = bool(clean["watch_trainers"])
                watcher.start() if clean["watch_trainers"] else watcher.stop()
            except Exception:  # noqa: BLE001
                log.debug("trainer watch toggle failed", exc_info=True)
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

    # ------------------------------------------------------------ training records
    def save_training(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            game = payload.get("game") if payload.get("game") in GAMES else "kovak"
            weapon = payload.get("weapon") if payload.get("weapon") in WEAPONS else "sniper"
            items = payload.get("scenarios") or []
            raw_scores = payload.get("scores") or {}
            scores: dict[str, float] = {}
            scen_rows: list[dict[str, Any]] = []
            for item in items:
                name = str(item.get("name") or "").strip()
                value = item.get("score")
                if not name or value is None or str(value).strip() == "":
                    continue
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
                if value < 0 or value != value or value in (float("inf"), float("-inf")):
                    raise ValueError(f"{name} 점수는 0 이상의 숫자로 입력해 주세요")
                # 시나리오마다 단위가 달라(코박스 152 · 에임랩 92,500) 상한을 두지 않는다.
                area = item.get("area_key") or item.get("area")
                if area not in scoring.AREA_KEYS:
                    area = _area_for(game, weapon, name)
                scen_rows.append({"name": name, "area": area, "score": round(value, 1),
                                  "raw_score": round(value, 1), "game": game, "weapon": weapon})
            for key in scoring.AREA_KEYS:
                if raw_scores.get(key) not in (None, ""):
                    scores[key] = round(float(raw_scores[key]), 1)
            if not scores and scen_rows:
                for key in scoring.AREA_KEYS:
                    vals = [s["score"] for s in scen_rows if s["area"] == key]
                    if vals:
                        scores[key] = round(sum(vals) / len(vals), 1)
            if not scores:
                raise ValueError("저장할 점수가 없습니다")
            date = str(payload.get("date") or today_str())

            record = {
                "date": date,
                "game": game,
                "trainer": game,          # v1.1.0: 통합 프로필에서도 프로그램 구분은 남긴다
                "weapon": weapon,
                "scores": scores,
                "scenarios": scen_rows,
                "note": str(payload.get("note") or "")[:200],
            }
            # 🏆 NEW PB — 저장 전 같은 날짜 기록을 빼고 비교한다(오늘 점수 수정과 구분)
            prior = [r for r in self.store.records()
                     if not (r.get("date") == date and r.get("game") == game and r.get("weapon") == weapon)]
            try:
                pbs = scoring.new_pbs(prior, scen_rows)
            except Exception:  # noqa: BLE001 - PB 계산 실패가 저장을 막지 않는다
                pbs = []
            self._drop_previous(date, game, weapon, [s["name"] for s in scen_rows])
            saved = self.store.add_record(record)
            self.store.note_attendance(self.store.records())
            return {"ok": True, "record": saved, "pbs": pbs, "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def _drop_previous(self, date: str, game: str, weapon: str, names: list[str]) -> None:
        """같은 날 같은 시나리오를 다시 적으면 '수정' — 이전 기록에서 그 항목을 뺀다.

        모달에서 오늘 점수를 고쳐 저장할 때 같은 시나리오가 두 번 쌓여
        5영역 평균을 왜곡하는 것을 막는다.  시나리오만 있던 기록은 통째로 지운다.
        """
        drop = {str(n) for n in names if n}
        if not drop:
            return
        records = self.store.records()
        kept: list[dict[str, Any]] = []
        changed = False
        for rec in records:
            if rec.get("date") != date or rec.get("game") != game or rec.get("weapon") != weapon:
                kept.append(rec)
                continue
            rows = rec.get("scenarios") or []
            remain = [s for s in rows if str(s.get("name")) not in drop]
            if len(remain) == len(rows):
                kept.append(rec)
                continue
            changed = True
            if not remain:
                continue          # 이 시나리오만 있던 기록 → 기록째로 삭제
            rec["scenarios"] = remain
            scores: dict[str, float] = {}
            for key in scoring.AREA_KEYS:
                vals = [float(s["score"]) for s in remain
                        if s.get("area") == key and s.get("score") is not None]
                if vals:
                    scores[key] = round(sum(vals) / len(vals), 1)
            rec["scores"] = scores
            kept.append(rec)
        if changed:
            self.store.data["records"] = kept

    def save_training_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        """UI(ui/bridge.js) 가 부르는 이름 · save_training 과 같은 동작."""
        return self.save_training(payload)

    def delete_record(self, record_id: str) -> dict[str, Any]:
        removed = self.store.delete_record(str(record_id))
        return {"ok": removed, "state": self._build_state(),
                "message": "기록을 삭제했습니다" if removed else "기록을 찾지 못했습니다"}

    def regenerate_recs(self, game: str = "kovak", weapon: str = "sniper", offset: int | None = None) -> dict[str, Any]:
        game = game if game in GAMES else "kovak"
        weapon = weapon if weapon in WEAPONS else "sniper"
        records = self.store.records()
        summary = scoring.summary(records, game, weapon)
        history = self.store.rotation()
        step = int(offset) if offset is not None else 1
        recs = scoring.build_recs(values=summary["values"], records=records, game=game,
                                  weapon=weapon, history=history, offset=step)
        try:
            self.store.set_rotation(scoring.push_rotation(
                history, scoring.rotation_entry(recs, game, weapon, _dt.date.today())))
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "recs": recs, "state": self._build_state()}

    def recommend_more(self, game: str = "kovak", weapon: str = "sniper", count: int = 3) -> dict[str, Any]:
        """`+ 더 연습하기` — 오늘 목록에 없는 시나리오를 가장 약한 영역 위주로.

        연 목록은 data.json(`more`)에 남겨 앱을 다시 켜도 오늘 화면에 그대로 보인다.
        """
        try:
            game = game if game in GAMES else "kovak"
            weapon = weapon if weapon in WEAPONS else "sniper"
            count = max(1, min(6, int(count or 3)))
            records = self.store.records()
            today = _dt.date.today()
            summary = scoring.summary(records, game, weapon, today)
            base = self._today_recs(summary["values"], records, game, weapon, today)
            rows = self.store.more()
            entry = next((m for m in reversed(rows)
                          if m.get("date") == today.isoformat()
                          and m.get("game") == game and m.get("weapon") == weapon), None)
            existing = list((entry or {}).get("names") or [])
            exclude = ([r["name"] for r in base] + existing
                       + list(scoring.today_scores(records, today).keys()))
            extra = scoring.build_more(values=summary["values"], records=records, game=game,
                                       weapon=weapon, exclude=exclude, today=today, count=count)
            if not extra:
                return self._ok(recs=[], state=self._build_state(),
                                message="이 조합에서 더 추천할 시나리오가 없습니다 · 새로고침으로 로테이션을 돌려 보세요")
            names = existing + [r["name"] for r in extra]
            if entry is None:
                rows.append({"date": today.isoformat(), "game": game, "weapon": weapon, "names": names})
            else:
                entry["names"] = names
            self.store.set_more(rows)
            return self._ok(recs=extra, state=self._build_state(),
                            message=f"약점 보완 {len(extra)}종을 추가했습니다 · {scoring.WEAPON_PHILOSOPHY.get(weapon, '')}")
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def _today_recs(self, values: dict[str, float], records: list[dict[str, Any]], game: str,
                    weapon: str, today: _dt.date) -> list[dict[str, Any]]:
        """오늘의 추천 5종 — 하루 동안 고정. 새로고침(regenerate_recs) 때만 바뀝니다.

        오늘 자 로테이션 엔트리가 있으면 그 5종을 그대로 다시 그린다(점수를 적어도
        목록이 흔들리지 않게). 없으면 약점 우선으로 뽑아 오늘 엔트리로 남긴다.
        """
        rows = self.store.rotation()
        entry = next((h for h in reversed(rows)
                      if h.get("date") == today.isoformat() and h.get("game") == game
                      and h.get("weapon") == weapon and h.get("names")), None)
        if entry:
            recs = scoring.recs_for_names(entry["names"], values=values, records=records,
                                          game=game, weapon=weapon, today=today)
            # v1.4.0 — 스나 모드에 '트래킹·무빙'이 1순위로 뜨던 모순: 예전 로테이션에
            # 남은 트래킹 시나리오는 버리고 무기 철학대로 다시 뽑는다.
            if weapon == "sniper":
                track_names = {s["name"] for s in scoring.scenarios_for(game, "sniper")
                               if s.get("area_key") == "track"}
                kept = [r for r in recs if str(r.get("name")) not in track_names]
                if len(kept) != len(recs):
                    entry = None
                    recs = kept
            if entry and len(recs) == len(entry["names"]):
                return recs
        recs = scoring.build_recs(values=values, records=records, game=game, weapon=weapon,
                                  history=rows, today=today)
        try:
            self.store.set_rotation(scoring.push_rotation(
                rows, scoring.rotation_entry(recs, game, weapon, today)))
        except Exception:  # noqa: BLE001 - 목록 고정은 실패해도 화면은 그린다
            log.exception("state · rotation save failed for %s-%s", game, weapon)
        return recs

    # ------------------------------------------------------------ launch
    def launch_trainer(self, url: str) -> dict[str, Any]:
        result = winutil.open_target(url)
        self.store.utils()["last_launch"] = now_iso()
        self.store.save()
        if not result.get("ok"):
            result.setdefault("message", "실행 앱을 찾지 못했습니다 · Steam 설치를 확인해 주세요")
        return result

    def open_external(self, url: str) -> dict[str, Any]:
        return winutil.open_target(url)

    def open_data_folder(self) -> dict[str, Any]:
        return winutil.open_folder(str(paths.data_dir()))

    # ------------------------------------------------------------ NEXON
    def _client(self, api_key: str | None = None, *, force: bool = False) -> nexon.NexonClient:
        key = (api_key if api_key is not None else self.store.settings.get("api_key") or "").strip()
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
        if not (self.store.settings.get("api_key") or "").strip():
            raise ValueError("NEXON Open API 키를 입력해 주세요 · 설정 탭에서 저장할 수 있습니다")
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
            if not (key or (self.store.settings.get("api_key") or "").strip()):
                raise ValueError("NEXON Open API 키를 먼저 저장해 주세요 · 설정 탭")
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
            rows = nexon.build_leaderboard(client, members[:10], progress=self._progress,
                                           depth=nexon.DETAIL_DEPTH if depth is None else int(depth))
            renamed = [r for r in rows if r.get("renamed_from")]
            self.store.set_leaderboard([{**r} for r in rows[:10]])
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
            if len(members) >= 10:
                raise ValueError("10명이 가득 찼습니다 · 한 명을 삭제한 뒤 등록해 주세요")
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
        self.store.set_leaderboard(members[:10])

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

    def open_reports_folder(self) -> dict[str, Any]:
        """리포트 카드 저장 폴더를 탐색기로 연다."""
        folder = paths.reports_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        result = winutil.open_folder(str(folder))
        result.setdefault("folder", str(folder))
        return result

    def open_playlists_folder(self) -> dict[str, Any]:
        """코박스 Playlists 폴더를 연다 (v1.5.0 #6 · 없으면 앱 데이터 폴더)."""
        folder = kovak.find_playlists_dir(None) or paths.playlists_dir()
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        result = winutil.open_folder(str(folder))
        result.setdefault("folder", str(folder))
        result.setdefault("message", f"플레이리스트 폴더를 열었습니다 · {folder}")
        return result

    def open_barracks_page(self, ouid: str | None = None, nickname: str | None = None) -> dict[str, Any]:
        """서든 병영수첩 홈페이지를 기본 브라우저로 연다 (v1.5.0 · user_nexon_sn 사용)."""
        nick = str(nickname or "").strip() or (self.store.settings.get("nickname") or "").strip()
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

    def party_map_plan(self, names: Any = None, refresh: bool = False) -> dict[str, Any]:
        """파티랭크 5인 맵 밴·픽 추천 — 파티원 맵별 전적 합산 (v1.5.0)."""
        try:
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

    # ---------------------------------------------------------- 사용자 시나리오
    def add_custom_scenario(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            row = v150.clean_custom_scenario(payload or {}, scoring.AREA_KEYS)
            rows = self.store.custom_scenarios()
            existing = next((r for r in rows if str(r.get("name")).strip().lower() == row["name"].lower()), None)
            if existing:
                existing.update(row)
                message = f"'{row['name']}' 시나리오를 수정했습니다"
            else:
                row["id"] = f"custom-{len(rows) + 1}-{int(time.time())}"
                rows.append(row)
                message = f"'{row['name']}' 시나리오를 추가했습니다"
            self.store.save()
            return {"ok": True, "message": message, "scenario": row, "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def delete_custom_scenario(self, name: str = "") -> dict[str, Any]:
        try:
            target = str(name or "").strip().lower()
            if not target:
                raise ValueError("삭제할 시나리오 이름이 없습니다")
            rows = self.store.custom_scenarios()
            remain = [r for r in rows if str(r.get("name")).strip().lower() != target]
            if len(remain) == len(rows):
                raise ValueError("'내 시나리오'에서 추가한 것만 삭제할 수 있습니다")
            self.store.data["custom_scenarios"] = remain
            self.store.save()
            return {"ok": True, "message": "시나리오를 삭제했습니다", "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def set_scenario_excluded(self, name: str = "", excluded: bool = True) -> dict[str, Any]:
        """추천 5종에서 제외 / 제외 해제 (라이브러리에는 흐리게 남는다)."""
        try:
            target = str(name or "").strip()
            if not target:
                raise ValueError("시나리오 이름이 없습니다")
            current = [str(n) for n in (self.store.settings.get("excluded_scenarios") or [])]
            low = target.lower()
            if excluded and low not in {n.lower() for n in current}:
                current.append(target)
            elif not excluded:
                current = [n for n in current if n.lower() != low]
            self.store.update_settings({"excluded_scenarios": current})
            # 오늘 추천 로테이션에서도 바로 뺀다
            try:
                self.store.set_rotation([r for r in self.store.rotation()
                                         if not v150.is_excluded(r.get("name"), [target])])
            except Exception:  # noqa: BLE001
                pass
            return {"ok": True, "excluded": current, "state": self._build_state(),
                    "message": ("추천에서 제외했습니다" if excluded else "추천에 다시 넣었습니다")}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # ---------------------------------------------------------- 트레이너 종료 감시
    def _trainer_watch_obj(self) -> v150.TrainerWatch:
        with self._trainer_lock:
            if self._trainer_watch is None:
                self._trainer_watch = v150.TrainerWatch(on_finished=self._trainer_finished)
            return self._trainer_watch

    def _trainer_state(self) -> dict[str, Any]:
        watcher = self._trainer_watch_obj()
        return {"enabled": bool(self.store.settings.get("watch_trainers", True)),
                "supported": watcher.supported, "running": watcher.running,
                "last": watcher.last_event}

    def _trainer_finished(self, event: dict[str, Any]) -> None:
        """감시 스레드 → 화면 토스트 (스레드 경계는 pywebview evaluate_js 가 처리)."""
        try:
            self.store.ui()["trainer_event"] = event
            self.store.save()
        except Exception:  # noqa: BLE001
            pass
        window = self._win
        if window is None:
            return
        try:
            payload = json.dumps(event, ensure_ascii=False)
            window.evaluate_js(
                "window.AL && AL.trainerFinished ? AL.trainerFinished(" + payload + ") : null")
        except Exception:  # noqa: BLE001
            log.debug("trainer toast failed", exc_info=True)

    def start_trainer_watch(self) -> None:
        if not self.store.settings.get("watch_trainers", True):
            return
        self._trainer_watch_obj().start()

    start_trainer_watch._serializable = False  # type: ignore[attr-defined]

    def set_trainer_watch(self, enabled: bool = True) -> dict[str, Any]:
        self.store.update_settings({"watch_trainers": bool(enabled)})
        watcher = self._trainer_watch_obj()
        watcher.enabled = bool(enabled)
        if enabled:
            watcher.start()
        else:
            watcher.stop()
        return {"ok": True, "watch": self._trainer_state(),
                "message": ("트레이너 종료 감지를 켰습니다" if enabled else "트레이너 종료 감지를 껐습니다")}

    def trainer_watch_state(self) -> dict[str, Any]:
        return {"ok": True, **self._trainer_state()}

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
            if self._trainer_watch is not None:
                self._trainer_watch.stop()
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
            payload = {
                "settings": {k: v for k, v in self.store.public_settings().items() if k != "api_key"},
                "records": self.store.records(),
                "leaderboard": nexon.leaderboard_payload(self.store.leaderboard()),
                "rotation": self.store.rotation(),
                "exported_at": now_iso(),
                "app_version": paths.APP_VERSION,
            }
            client = supabase_sync.SupabaseClient(url, key)
            client.upsert_profile(token, user_id, payload)
            try:
                # 18단계 티어 분포 집계 — 점수·티어 한 줄만 올린다 (기록 payload 는 위 테이블에만)
                profile = scoring.summary(self.store.records(), "kovak", "sniper")
                client.upsert_aim_score(token, user_id,
                                        (self.store.settings.get("nickname") or "").strip(),
                                        int(profile.get("score") or 0),
                                        str((profile.get("tier") or {}).get("n") or ""))
            except Exception:  # noqa: BLE001 - 집계 테이블이 없어도 동기화는 성공
                log.debug("cloud_push · aim_scores upsert skipped", exc_info=True)
            cloud["last_sync"] = now_iso()
            self.store.save()
            return {"ok": True, "message": "☁️ 클라우드에 기록을 올렸습니다", "state": self._build_state()}
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
                for section in ("records", "rotation"):
                    if isinstance(payload.get(section), list):
                        data[section] = payload[section]
                if isinstance(payload.get("leaderboard"), list):
                    data["leaderboard"] = payload["leaderboard"]
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

    # ============================================================ v1.2.0
    # 코박스 점수 인식 · 워밍업 · 플레이리스트 · 리포트 카드 · 업데이트 ·
    # 1:1 비교 · 전원/게임 모드 · 트레이
    # ------------------------------------------------------------ 코박스 stats
    def _today_target_names(self) -> list[str]:
        """오늘 추천 5종 + 더 연습하기에 올라온 시나리오 이름들."""
        today = _dt.date.today().isoformat()
        names: list[str] = []
        for entry in self.store.rotation():
            if entry.get("date") == today:
                names.extend(str(n) for n in (entry.get("names") or []))
        for entry in self.store.more():
            if entry.get("date") == today:
                names.extend(str(n) for n in (entry.get("names") or []))
        seen: set[str] = set()
        out: list[str] = []
        for name in names:
            key = name.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(name)
        return out

    def import_kovak_scores(self, names: list[str] | None = None, days: int = 1) -> dict[str, Any]:
        """코박스 `stats/*.csv` 에서 오늘(또는 최근) 점수를 읽어 추천 과제와 맞춘다."""
        try:
            custom = (self.store.settings.get("kovak_stats_path") or "").strip() or None
            dirs = kovak.stats_dirs(custom)
            targets = [str(n).strip() for n in (names or []) if str(n).strip()]
            if not targets:
                targets = self._today_target_names()
            if not dirs:
                return {"ok": False, "found": False, "matched": [], "dirs": [],
                        "message": "코박스 stats 폴더를 찾지 못했습니다 · 설정 탭에서 '코박스 통계 폴더'를 직접 지정해 주세요"}
            runs = kovak.latest_runs(dirs, days=max(1, min(30, int(days or 1))))
            matched = kovak.match_targets(targets, runs)
            scanned = next((int(r.get("scanned") or 0) for r in runs.values()), 0)
            if not runs:
                return {"ok": True, "found": False, "matched": [], "dirs": [str(d) for d in dirs],
                        "scanned": scanned, "checked_dirs": len(dirs),
                        "message": f"오늘(최근 {days}일) 코박스 기록이 없습니다 · 한 판 플레이하면 자동으로 잡힙니다"}
            if not matched:
                return {"ok": True, "found": True, "matched": [], "dirs": [str(d) for d in dirs],
                        "scanned": scanned, "runs": len(runs), "checked_dirs": len(dirs),
                        "message": f"코박스 기록 {len(runs)}종을 찾았지만 오늘 추천 과제와 겹치는 항목이 없습니다"}
            return {"ok": True, "found": True, "matched": matched, "dirs": [str(d) for d in dirs],
                    "scanned": scanned, "runs": len(runs), "checked_dirs": len(dirs),
                    "message": f"📥 코박스 점수 {len(matched)}종 인식 · {', '.join(m['name'] for m in matched[:3])}"
                               + (" 외" if len(matched) > 3 else "")}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, matched=[])

    def kovak_stats_state(self) -> dict[str, Any]:
        """설정 탭 표시용 — 지금 읽을 수 있는 stats 폴더와 최근 기록 수."""
        custom = (self.store.settings.get("kovak_stats_path") or "").strip() or None
        try:
            dirs = kovak.stats_dirs(custom)
        except Exception:  # noqa: BLE001
            dirs = []
        try:
            runs = kovak.latest_runs(dirs, days=7) if dirs else {}
        except Exception:  # noqa: BLE001
            runs = {}
        return {"ok": True, "dirs": [str(d) for d in dirs], "found": bool(dirs),
                "recent": len(runs), "custom": custom,
                "playlists": str(kovak.find_playlists_dir() or paths.playlists_dir())}

    # ------------------------------------------------------------ 워밍업 · 플레이리스트
    def warmup_plan(self, game: str = "kovak", weapon: str = "sniper") -> dict[str, Any]:
        """랭크전 직전 5분 워밍업 3종."""
        try:
            game = game if game in GAMES else "kovak"
            weapon = weapon if weapon in WEAPONS else "sniper"
            records = self.store.records()
            today = _dt.date.today()
            recs = scoring.warmup_recs(game=game, weapon=weapon,
                                       values=scoring.profile_values(records), records=records, today=today)
            if not recs:
                raise ValueError("워밍업 시나리오를 고르지 못했습니다")
            return {"ok": True, "recs": recs, "game": game, "weapon": weapon,
                    "label": "⚡ 5분 워밍업 · " + ("KovaaK's" if game == "kovak" else "Aim Lab"),
                    "message": f"⚡ 워밍업 3종 · {scoring.WEAPON_PHILOSOPHY.get(weapon, '')}"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, recs=[])

    def export_playlist(self, kind: str = "recs", game: str = "kovak", weapon: str = "sniper",
                        name: str | None = None, target_dir: str | None = None) -> dict[str, Any]:
        """오늘 추천 5종 / 5분 워밍업 3종을 코박스 플레이리스트로 저장."""
        try:
            game = game if game in GAMES else "kovak"
            weapon = weapon if weapon in WEAPONS else "sniper"
            records = self.store.records()
            today = _dt.date.today()
            stamp = today.strftime("%m.%d")
            if kind == "warmup":
                recs = scoring.warmup_recs(game=game, weapon=weapon,
                                           values=scoring.profile_values(records), records=records, today=today)
                default_name = f"SA AIM 5분 워밍업 {stamp}"
            else:
                summary = scoring.summary(records, game, weapon, today)
                recs = self._today_recs(summary["values"], records, game, weapon, today)
                default_name = f"SA AIM 추천 5종 {stamp}"
            names = [str(r.get("name")) for r in recs if r.get("name")]
            if not names:
                raise ValueError("플레이리스트에 담을 시나리오가 없습니다 · 먼저 추천을 불러와 주세요")
            folder = kovak.find_playlists_dir(target_dir or None)
            target = folder or paths.playlists_dir()
            trainer_label = "KovaaK's" if game == "kovak" else "Aim Lab"
            result = kovak.write_playlist(
                (name or default_name).strip(), names, target=target,
                description="SA AIM LABS · {} · {} · {}".format(trainer_label, weapon, today.isoformat()),
            )
            note = ""
            if game != "kovak":
                note = " · 플레이리스트는 KovaaK's 전용입니다 (Aim Lab 은 지원하지 않습니다)"
            if not folder:
                note += " · 코박스 Playlists 폴더를 찾지 못해 앱 데이터 폴더에 저장했습니다 · 파일을 Playlists 폴더로 복사해 주세요"
            return {"ok": True, **result, "in_game": bool(folder), "names": names, "kind": kind,
                    "message": f"📜 플레이리스트 {len(names)}종 저장 · {target}{note}"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # ------------------------------------------------------------ 리포트 카드 PNG
    def save_report_card(self, data_url: str, label: str | None = None) -> dict[str, Any]:
        """UI(캔버스)가 만든 리포트 카드 PNG(data URL)를 파일로 저장한다."""
        import base64

        try:
            raw = str(data_url or "").strip()
            if not raw:
                raise ValueError("리포트 카드 이미지가 비어 있습니다")
            if raw.startswith("data:") and "," in raw[:120]:
                raw = raw.split(",", 1)[1]
            blob = base64.b64decode(raw, validate=False)
            if len(blob) < 200:
                raise ValueError("리포트 카드 이미지를 읽지 못했습니다")
            if len(blob) > 12_000_000:
                raise ValueError("리포트 카드 이미지가 너무 큽니다")
            stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = paths.reports_dir() / f"sa_aim_report_{stamp}.png"
            path.write_bytes(blob)
            return {"ok": True, "path": str(path), "folder": str(path.parent), "size": len(blob),
                    "label": (label or "").strip()[:60],
                    "message": f"📸 리포트 카드 저장 완료 · {path.name} ({len(blob) / 1024:.0f}KB)"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # ------------------------------------------------------------ 업데이트 알림
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
            found = len([r for r in rows if not r.get("error")])
            return {"ok": found > 0, "rows": rows, "team": team, "calls": int(getattr(client, "calls", 0)),
                    "message": f"스카우팅 {found}/{len(rows)}명 완료 · {team.get('label')} (위협도 {team.get('threat')})"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, rows=[], team={})

    # 2) 듀오·깐부 시너지
    def duo_synergy(self, nickname: str = "") -> dict[str, Any]:
        try:
            cache = self.store.barracks_cache()
            barracks = cache.get(self.store.settings.get("nickname") or "", None)
            if barracks is None and cache:
                barracks = list(cache.values())[-1]
            matches = (barracks or {}).get("matches") or []
            result = v140.duo_synergy(matches, nickname)
            return {"ok": True, **result, "message": result.get("label") or result.get("reason") or ""}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def scout_last_match(self) -> dict[str, Any]:
        """방금 끝난 판 상대 팀을 그대로 불러와 분석한다 (타이핑 없음)."""
        try:
            nick = (self.store.settings.get("nickname") or "").strip()
            if not nick:
                raise ValueError("설정에서 서든 닉네임과 API 키를 먼저 저장해 주세요")
            client = self._client()
            ouid = client.resolve_ouid(nick)
            rows = client.recent_matches(ouid, 1)
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
    def personal_playlist(self, game: str = "kovak", weapon: str = "sniper",
                          save: bool = False, target_dir: str | None = None) -> dict[str, Any]:
        try:
            game = game if game in GAMES else "kovak"
            weapon = weapon if weapon in WEAPONS else "sniper"
            records = self.store.records()
            today = _dt.date.today()
            values = scoring.profile_values(records)
            plan = v140.personal_plan(values, records, game=game, weapon=weapon, today=today)
            if not plan["names"]:
                raise ValueError("플레이리스트에 담을 시나리오를 고르지 못했습니다")
            payload: dict[str, Any] = {"ok": True, **plan,
                                       "message": f"🎯 맞춤 플레이리스트 {plan['count']}종 · {plan['focus'][0]} 우선"}
            if save:
                folder = kovak.find_playlists_dir(target_dir or None)
                target = folder or paths.playlists_dir()
                stamp = today.strftime("%m.%d")
                written = kovak.write_playlist(
                    f"SA AIM 맞춤 {stamp}", plan["names"], target=target,
                    description=f"SA AIM LABS v{paths.APP_VERSION} · {weapon} · {today.isoformat()}",
                )
                note = "" if folder else " · 코박스 Playlists 폴더를 찾지 못해 앱 데이터 폴더에 저장했습니다"
                payload.update(written)
                payload["saved"] = True
                payload["message"] = f"📜 맞춤 플레이리스트 {plan['count']}종 저장 · {target}{note}"
            return payload
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

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
            prep = updates.prepare_install(url)
            if not prep.get("ok"):
                raise ValueError(prep.get("message") or "업데이트를 내려받지 못했습니다")
            run = updates.run_install(str(prep["script"]))
            if not run.get("ok"):
                raise ValueError(run.get("message") or "업데이트를 실행하지 못했습니다")
            return self._ok(path=prep.get("path"), size=prep.get("size"), message=run.get("message"))
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
            if data.get("weapon") in WEAPONS:
                patch["weapon"] = data["weapon"]
            if data.get("game") in GAMES:
                patch["game"] = data["game"]
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
            compare = scoring.friend_compare(me, target)
            return {"ok": True, "compare": compare,
                    "message": f"{compare['me']['name']} vs {compare['friend']['name']} · {compare['verdict']}"}
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


def _area_for(game: str, weapon: str, name: str) -> str:
    for item in scoring.scenarios_for(game, weapon):
        if item["name"] == name:
            return item["area"]
    return "precision"
