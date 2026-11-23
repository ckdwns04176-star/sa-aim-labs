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

from . import kovak, nexon, nvidia, paths, scoring, supabase_sync, tray, updates, winutil
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
        if not self._win:
            return self._ok(maximized=False)
        state = getattr(self._win, "maximized", False)
        try:
            if state:
                self._win.restore()
                return self._ok(maximized=False)
            self._win.maximize()
            return self._ok(maximized=True)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, maximized=bool(state))

    def close_window(self) -> dict[str, Any]:
        if self._win:
            self._win.destroy()
        return self._ok()

    def drag_window(self) -> dict[str, Any]:
        """Native, zero-lag window move for the frameless title bar.

        JS mousemove → MoveWindow round trips (pywebview's drag region) lag behind
        the cursor on Windows; handing the drag to the window manager makes the OS
        move the window itself, so it tracks the pointer exactly.
        """
        if os.name != "nt" or not self._win:
            return self._ok(supported=False)
        try:
            import ctypes

            handle = self._hwnd()
            if not handle:
                return self._ok(supported=False)
            # 드래그 시작 → 감시자가 크기를 건드리지 않게 잠깐 멈춘다 (창이 커서를 놓치지 않게)
            guard = _GUARD_REF
            if guard is not None and hasattr(guard, "pause"):
                guard.pause(1.2)
            ctypes.windll.user32.ReleaseCapture()  # type: ignore[attr-defined]
            ctypes.windll.user32.SendMessageW(  # type: ignore[attr-defined]
                ctypes.c_void_p(handle), 0x00A1, 2, 0   # WM_NCLBUTTONDOWN, HTCAPTION
            )
            return self._ok(supported=True)
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
                    summary["recs"] = recs
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
            heatmap_state = scoring.heatmap(records, 14, today, attendance)
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
            "library": scoring.scenario_library(),
            "today_scores": scoring.today_scores(records, today),
            "philosophy": scoring.WEAPON_PHILOSOPHY,
            "style": style,
            "streak": streak,
            "records": records[-200:],
            "record_count": len(records),
            "scenarios": {f"{g}-{w}": scoring.scenarios_for(g, w) for g in GAMES for w in WEAPONS},
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
            "v120": True,
            "v130": True,
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
            ("목표 승률", float(stats.get("win") or 0), float(g.get("win") or 55), "%"),
            ("목표 K/D", float(stats.get("kd") or 0), float(g.get("kd") or 60), "%"),
            ("목표 평균 딜량", float(stats.get("dmg") or 0), float(g.get("dmg") or 1400), ""),
            ("목표 솔로 RP", float(solo_rp), float(g.get("rp") or 4000), ""),
        ]
        out = []
        for label, cur, tgt, unit in rows:
            gap = cur - tgt
            out.append({"label": label, "cur": round(cur, 1), "tgt": tgt, "unit": unit,
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
                   "hotkey_enabled", "kovak_stats_path", "kovak_autofill", "week_goals"}
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
            elif isinstance(value, bool):
                value = bool(value)
            clean[key] = value
        self.store.update_settings(clean)
        if "hotkey_enabled" in clean:
            self._apply_hotkey(bool(clean["hotkey_enabled"]))
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
            if len(recs) == len(entry["names"]):
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
            return {"ok": True, "barracks": barracks, "state": self._build_state(),
                    "calls": client.calls, "cache": client.cache_stats(),
                    "message": f"{barracks['nickname']} 최신화 완료"}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def search_user(self, nickname: str, api_key: str | None = None) -> dict[str, Any]:
        """닉네임 검색 1단계 — 가볍게 존재 여부와 기본 정보만 확인한다."""
        try:
            nick = (nickname or "").strip()[:12]
            if not nick:
                raise ValueError("검색할 서든어택 닉네임을 입력해 주세요")
            key = (api_key or "").strip()
            if key and self._looks_masked(key):
                key = ""
            if not (key or (self.store.settings.get("api_key") or "").strip()):
                raise ValueError("NEXON Open API 키를 먼저 저장해 주세요 · 설정 탭")
            client = self._client(key or None, force=True)
            try:
                ouid = client.resolve_ouid(nick)
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
            name = str(nickname or "").strip()[:12]
            ouid = str(ouid or "").strip() or None
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
                result = mgr.apply_stairfix()
                self._remember_drs("stairfix", result, label="계단현상 끄기 적용 전")
                self._drs_state = None
                return {"ok": True, **result, "state": self._build_state()}
            if not mgr.available_modes().get(mode):
                raise nvidia.DrsError(
                    f"{nvidia.MODES[mode]} 데이터를 읽지 못했습니다 · 앱을 다시 설치해 주세요"
                )
            if elevate:
                return {"ok": True, **(mgr.elevate_help(mode)), "state": self._build_state()}
            result = mgr.apply(mode)
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

    def restore_nvidia_profile(self, backup_id: str | None = None) -> dict[str, Any]:
        try:
            mgr = self._drs()
            if backup_id:
                result = mgr.restore(str(backup_id))
            else:
                result = mgr.restore_latest()
            self._remember_drs(None, result, label="원래 설정 복구")
            self._drs_state = None
            return {"ok": True, **result, "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, needs_admin="관리자 권한" in str(exc))

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
            if changes:
                nv["last_changes"] = {"at": now_iso(), "mode": mode, "label": label,
                                       "rows": changes[:24], "count": len(changes)}
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
        backup = (self.store.utils().get("perf") or {}).get("backup")
        state["backup"] = backup
        state["applied_at"] = (self.store.utils().get("perf") or {}).get("applied_at")
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
