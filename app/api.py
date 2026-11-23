"""The JavaScript ↔ Python bridge exposed to the UI as ``window.pywebview.api``.

Every public method returns plain JSON-serialisable data.  Errors are returned as
``{"ok": False, "message": ...}`` instead of raising, so the UI can show a toast
without losing the page.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import traceback
from pathlib import Path
from typing import Any, Callable

from . import nexon, nvidia, paths, scoring, supabase_sync, winutil
from .store import Store, now_iso, today_str

GAMES = ("kovak", "aimlab")
WEAPONS = ("sniper", "rifle")


class Api:
    def __init__(self, store: Store | None = None):
        self.store = Store() if store is None else store
        self.window = None
        self._drs_state_at = 0.0
        self._drs_state: dict[str, Any] | None = None
        self._busy: str | None = None
        self._last_progress = 0.0

    # ------------------------------------------------------------ helpers
    def attach(self, window) -> None:
        self.window = window

    def _progress(self, done: int, total: int, label: str = "") -> None:
        import time

        now = time.monotonic()
        if done < total and now - self._last_progress < 0.25:
            return
        self._last_progress = now
        if not self.window:
            return
        try:
            self.window.evaluate_js(
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
        if self.window:
            self.window.minimize()
        return self._ok()

    def toggle_maximize(self) -> dict[str, Any]:
        if not self.window:
            return self._ok(maximized=False)
        state = getattr(self.window, "maximized", False)
        try:
            if state:
                self.window.restore()
                return self._ok(maximized=False)
            self.window.maximize()
            return self._ok(maximized=True)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc, maximized=bool(state))

    def close_window(self) -> dict[str, Any]:
        if self.window:
            self.window.destroy()
        return self._ok()

    def drag_window(self) -> dict[str, Any]:
        """Native, zero-lag window move for the frameless title bar.

        JS mousemove → MoveWindow round trips (pywebview's drag region) lag behind
        the cursor on Windows; handing the drag to the window manager makes the OS
        move the window itself, so it tracks the pointer exactly.
        """
        if os.name != "nt" or not self.window:
            return self._ok(supported=False)
        try:
            import ctypes

            handle = self._hwnd()
            if not handle:
                return self._ok(supported=False)
            ctypes.windll.user32.ReleaseCapture()  # type: ignore[attr-defined]
            ctypes.windll.user32.SendMessageW(  # type: ignore[attr-defined]
                ctypes.c_void_p(handle), 0x00A1, 2, 0   # WM_NCLBUTTONDOWN, HTCAPTION
            )
            return self._ok(supported=True)
        except Exception as exc:  # noqa: BLE001 - drag is best effort
            return self._err(exc, supported=False)

    def _hwnd(self) -> int | None:
        native = getattr(self.window, "native", None)
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
        if not self.window:
            return self._ok()
        try:
            return self._ok(x=self.window.x, y=self.window.y, width=self.window.width, height=self.window.height)
        except Exception:  # noqa: BLE001
            return self._ok()

    # ------------------------------------------------------------ state
    def bootstrap(self) -> dict[str, Any]:
        """First paint payload: everything the UI needs, minus the slow probes.

        The DRS state (tasklist + sha256 over two ~2 MB databases) and the mouse
        registry read are skipped here and fetched right after the window is
        painted through :meth:`get_nvidia_state`, which is what used to make the
        app stutter for a second on launch.
        """
        try:
            return self._ok(state=self._build_state(light=True))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._err(exc)

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
        streak = scoring.streak(records, today)

        summaries: dict[str, Any] = {}
        for game in GAMES:
            for weapon in WEAPONS:
                key = f"{game}-{weapon}"
                summary = scoring.summary(records, game, weapon, today)
                recs = scoring.build_recs(
                    values=summary["values"], records=records, game=game, weapon=weapon,
                    history=store.rotation(), today=today,
                )
                summary["recs"] = recs
                summary["style"] = style
                summary["streak"] = streak
                summaries[key] = summary

        barracks = store.barracks_cache().get(settings.get("nickname") or "", None)
        if barracks is None and store.barracks_cache():
            barracks = list(store.barracks_cache().values())[-1]
        members = self._ensure_me(barracks)

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
            utils_state["accel"] = winutil.get_mouse_accel() if os.name == "nt" else self.store.utils().get("accel")
            utils_state["last_backup_at"] = store.utils().get("last_backup_at")
            utils_state["last_restore_at"] = store.utils().get("last_restore_at")

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
            "style": style,
            "streak": streak,
            "records": records[-200:],
            "record_count": len(records),
            "scenarios": {f"{g}-{w}": scoring.scenarios_for(g, w) for g in GAMES for w in WEAPONS},
            "areas": scoring.AREAS,
            "tiers": scoring.TIERS,
            "utils": utils_state,
            "cloud": self._cloud_state(),
            "goals": self._goals(barracks),
        }

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
                   "tray_background", "notify", "autosave", "goals", "cloud_email"}
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
            elif isinstance(value, bool):
                value = bool(value)
            clean[key] = value
        self.store.update_settings(clean)
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
                scen_rows.append({"name": name, "area": area, "score": round(value, 1)})
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

            record = {
                "date": payload.get("date") or today_str(),
                "game": game,
                "weapon": weapon,
                "scores": scores,
                "scenarios": scen_rows,
                "note": str(payload.get("note") or "")[:200],
            }
            saved = self.store.add_record(record)
            names = [s["name"] for s in scen_rows]
            if names:
                entry = scoring.rotation_entry([{"name": n} for n in names], game, weapon, _dt.date.fromisoformat(record["date"]))
                self.store.set_rotation(scoring.push_rotation(self.store.rotation(), entry))
            return {"ok": True, "record": saved, "state": self._build_state()}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

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
        summary = scoring.summary(self.store.records(), game, weapon)
        history = self.store.rotation()
        used_today = next((h for h in history if h.get("date") == today_str()
                           and h.get("game") == game and h.get("weapon") == weapon), None)
        step = int(offset) if offset is not None else (len((used_today or {}).get("names") or []) and 1 or 1)
        recs = scoring.build_recs(values=summary["values"], records=self.store.records(), game=game,
                                  weapon=weapon, history=history, offset=step)
        try:
            self.store.set_rotation(scoring.push_rotation(
                history, scoring.rotation_entry(recs, game, weapon, _dt.date.today())))
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "recs": recs, "state": self._build_state()}

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
        if not self.window:
            return None
        try:
            import webview

            result = self.window.create_file_dialog(
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
            supabase_sync.SupabaseClient(url, key).upsert_profile(token, user_id, payload)
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
