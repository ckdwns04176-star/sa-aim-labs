"""Local JSON storage (``data.json``) — thread-safe, atomic, secret-free backups.

v2.0.0
------
* 에임 연습(코박스·에임랩) 기록 저장을 없앴다.  남는 데이터는 **서든어택 전적
  캐시 · 순위표 · 맵 밴 프리셋 · 설정 · 계정 등급 캐시**뿐이다.
* 모든 읽기/쓰기는 ``RLock`` 으로 감싼다 — 65초 전적 최신화 스레드, 오버레이
  단축키 스레드, 30분 클라우드 백업이 같은 dict 를 동시에 만진다.
* ``export_backup()`` 은 **민감정보를 빼고** 쓴다 (API 키 · Supabase 토큰 ·
  비밀번호).  백업 파일은 사용자가 메일·메신저로 주고받을 수 있는 파일이다.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from . import paths
from .supabase_sync import DEFAULT_ANON_KEY, DEFAULT_URL

SCHEMA_VERSION = 2

# 백업 파일에 절대 들어가면 안 되는 키 (settings 안쪽 이름 기준)
SECRET_KEYS = ("api_key", "access_token", "refresh_token", "password", "anon_key", "service_key")

DEFAULTS: dict[str, Any] = {
    "version": SCHEMA_VERSION,
    "created_at": None,
    "updated_at": None,
    "settings": {
        "nickname": "",
        "auto_sync": True,
        "notify": True,
        "hotkey_enabled": True,
        "onboarded": False,
        "tilt_watch": True,
        "tilt_interval": 240,
        "goals": {"win": 55.0, "kd": 60.0, "dmg": 1400, "rp": 4000},
        "supabase": {"url": DEFAULT_URL, "anon_key": DEFAULT_ANON_KEY},
        "cloud_email": "",
        # ── 화면 ──
        "theme": "coral",
        "ui_scale": 100,
        "ui_scale_auto": True,
        # ── 인게임 오버레이 (v2.0.0 · Win32 순정 창) ──
        "overlay_enabled": True,
        "overlay_hotkey_lobby": "F8",
        "overlay_hotkey_scout": "F9",
        "overlay_mapban_seconds": 12,
        "overlay_party_seconds": 15,
        "overlay_scan_x": 0,
        "overlay_scan_y": 0,
        "overlay_scan_scale": 1.0,
        "toast_enabled": True,
        "toast_position": "br",
        "toast_auto": True,
        "auto_cloud": True,
    },
    "leaderboard": [],
    "memos": {},
    "barracks": {},            # 닉네임 → 병영수첩 캐시
    "barracks_ids": {},        # 닉네임 → {sn, clan_id, photo, profile}
    "barracks_history": [],    # 날짜별 최신화 스냅샷 (변화 비교)
    "condition_history": [],   # 날짜별 랭크 컨디션
    "play_days": {},           # 날짜별 플레이 잔디 {date: {games, wins}}
    "ui": {"digest_ack": None},
    "utils": {
        "nvidia": {"mode": None, "alias": False, "last_backup": None, "points": []},
        "accel": None,
        "perf": {"applied_at": None, "backup": None},
        "last_backup_at": None,
        "last_restore_at": None,
        "last_launch": None,
    },
    "cloud": {"email": None, "user_id": None, "access_token": None,
              "refresh_token": None, "last_sync": None},
    "cloud_auto": {"last_at": None, "last_reason": None},
    "account": {"plan": "free", "pro_until": None, "checked_at": None, "email": None},
    "usage": {"date": None, "count": 0},
    "party_presets": [],
    "overlay": {"last_scan_at": None, "last_scan": [], "last_team": []},
    "cache_cleanup_at": None,
}


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today_str() -> str:
    return _dt.date.today().isoformat()


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def scrub_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """민감정보를 지운 사본 — 백업 파일 · 클라우드 payload 공용."""
    clean = copy.deepcopy(data or {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            for key in list(node.keys()):
                if str(key) in SECRET_KEYS:
                    node.pop(key, None)
                else:
                    node[key] = walk(node[key])
        elif isinstance(node, list):
            node = [walk(v) for v in node]
        return node

    clean = walk(clean)
    settings = clean.setdefault("settings", {})
    settings.pop("api_key", None)
    settings.pop("api_key_masked", None)
    supabase = settings.get("supabase")
    if isinstance(supabase, dict):
        settings["supabase"] = {"url": supabase.get("url", "")}
    cloud = clean.get("cloud")
    if isinstance(cloud, dict):
        clean["cloud"] = {"email": cloud.get("email"), "user_id": cloud.get("user_id"),
                          "last_sync": cloud.get("last_sync")}
    return clean


class Store:
    """Thread-safe local store: every mutation rewrites the whole document."""

    # pywebview walks the js_api object's public attributes and recurses into
    # anything non-callable.  This marker stops that walk at the store.
    _serializable = False

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path else paths.data_file()
        self.data: dict[str, Any] = {}
        self.load_error: str | None = None
        self._lock = threading.RLock()
        self.load()

    # ------------------------------------------------------------ io
    def load(self) -> dict[str, Any]:
        with self._lock:
            data: dict[str, Any] = {}
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:  # corrupted file: keep a copy, start fresh
                    self.load_error = f"data.json을 읽지 못했습니다 ({exc})"
                    try:
                        shutil.copy2(self.path, self.path.with_suffix(".corrupt.json"))
                    except OSError:
                        pass
                    data = {}
            self.data = _deep_merge(DEFAULTS, data)
            # v1 백업/데이터에 남아 있던 에임 연습 구역은 더 이상 쓰지 않는다.
            for legacy in ("records", "rotation", "more", "attendance", "custom_scenarios"):
                self.data.pop(legacy, None)
            if not self.data.get("created_at"):
                self.data["created_at"] = now_iso()
            return self.data

    def save(self) -> None:
        with self._lock:
            self.data["version"] = SCHEMA_VERSION
            self.data["updated_at"] = now_iso()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self.data, ensure_ascii=False, indent=2)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".data-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                if self.path.exists():
                    try:
                        shutil.copy2(self.path, self.path.with_suffix(".bak"))
                    except OSError:
                        pass
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass

    # ------------------------------------------------------------ settings
    @property
    def settings(self) -> dict[str, Any]:
        with self._lock:
            return self.data["settings"]

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.data["settings"] = _deep_merge(self.data["settings"], patch or {})
            self.save()
            return self.data["settings"]

    def public_settings(self) -> dict[str, Any]:
        """Settings safe to hand to the UI (secrets masked, never raw)."""
        with self._lock:
            s = copy.deepcopy(self.data["settings"])
        key = s.get("api_key") or ""
        s["api_key_set"] = bool(key)
        s["api_key_masked"] = (key[:9] + "•" * 12) if key else ""
        s["api_key"] = ""
        sb = s.get("supabase") or {}
        anon = sb.get("anon_key") or ""
        s["supabase"] = {"url": sb.get("url", ""), "anon_key_set": bool(anon),
                         "anon_key_masked": (anon[:8] + "…") if anon else ""}
        return s

    # ------------------------------------------------------------ leaderboard
    def leaderboard(self) -> list[dict[str, Any]]:
        with self._lock:
            return self.data.setdefault("leaderboard", [])

    def set_leaderboard(self, members: list[dict[str, Any]]) -> None:
        with self._lock:
            self.data["leaderboard"] = members[:20]
            self.save()

    # ------------------------------------------------------------ friend memos
    def memos(self) -> dict[str, str]:
        with self._lock:
            return self.data.setdefault("memos", {})

    def set_memo(self, nickname: str, text: str) -> dict[str, str]:
        name = str(nickname or "").strip()
        if not name:
            raise ValueError("닉네임이 없습니다")
        body = str(text or "").strip()[:400]
        with self._lock:
            table = self.memos()
            if body:
                table[name] = body
            else:
                table.pop(name, None)
            self.save()
            return table

    # ------------------------------------------------------------ misc sections
    def utils(self) -> dict[str, Any]:
        with self._lock:
            return self.data.setdefault("utils", copy.deepcopy(DEFAULTS["utils"]))

    def barracks_cache(self) -> dict[str, Any]:
        with self._lock:
            return self.data.setdefault("barracks", {})

    def barracks_ids(self) -> dict[str, Any]:
        with self._lock:
            return self.data.setdefault("barracks_ids", {})

    def party_presets(self) -> list[dict[str, Any]]:
        with self._lock:
            return self.data.setdefault("party_presets", [])

    def save_party_preset(self, name: str, members: Any) -> dict[str, Any]:
        from . import party_lobby

        label = party_lobby.clean_preset_name(name)
        rows = party_lobby.clean_members(members)
        with self._lock:
            presets = [row for row in self.party_presets() if isinstance(row, dict)]
            presets = [row for row in presets if str(row.get("name") or "") != label]
            entry = {"name": label, "members": rows, "created": now_iso()}
            presets.append(entry)
            self.data["party_presets"] = presets[-20:]
            self.save()
            return entry

    def delete_party_preset(self, name: str) -> bool:
        with self._lock:
            rows = [row for row in self.party_presets()
                    if str((row or {}).get("name") or "") != str(name or "").strip()]
            changed = len(rows) != len(self.party_presets())
            self.data["party_presets"] = rows
            if changed:
                self.save()
            return changed

    def overlay_cache(self) -> dict[str, Any]:
        with self._lock:
            return self.data.setdefault("overlay", {"last_scan_at": None, "last_scan": [], "last_team": []})

    # ------------------------------------------------------------ barracks snapshots
    def barracks_history(self) -> list[dict[str, Any]]:
        with self._lock:
            return self.data.setdefault("barracks_history", [])

    def add_barracks_snapshot(self, barracks: dict[str, Any], *, keep: int = 60) -> dict[str, Any] | None:
        """같은 날 같은 최신화 시각의 스냅샷은 하나만 남긴다."""
        if not isinstance(barracks, dict) or not barracks.get("nickname"):
            return None
        snapshot = {
            "date": today_str(),
            "at": str(barracks.get("updated_at") or now_iso()),
            "nickname": barracks.get("nickname"),
            "stats": barracks.get("stats") or {},
            "tier": barracks.get("tier") or {},
            "basic": {"clan": (barracks.get("basic") or {}).get("clan")},
        }
        with self._lock:
            rows = self.barracks_history()
            if rows and rows[-1].get("date") == snapshot["date"] and rows[-1].get("at") == snapshot["at"]:
                return rows[-1]
            rows.append(snapshot)
            self.data["barracks_history"] = rows[-keep:]
            self.save()
            return snapshot

    # ------------------------------------------------------------ play days (잔디)
    def play_days(self) -> dict[str, Any]:
        with self._lock:
            return self.data.setdefault("play_days", {})

    def merge_play_days(self, matches: list[dict[str, Any]]) -> dict[str, Any]:
        from . import insights

        with self._lock:
            merged = insights.merge_play_days(self.play_days(), matches or [])
            if merged != self.data.get("play_days"):
                self.data["play_days"] = merged
                self.save()
            return self.data["play_days"]

    # ------------------------------------------------------------ ui flags
    def ui(self) -> dict[str, Any]:
        with self._lock:
            return self.data.setdefault("ui", {"digest_ack": None})

    # ------------------------------------------------------------ account · usage
    def account(self) -> dict[str, Any]:
        with self._lock:
            return self.data.setdefault("account", copy.deepcopy(DEFAULTS["account"]))

    def set_account(self, plan: str, pro_until: str | None, email: str | None = None) -> dict[str, Any]:
        with self._lock:
            row = self.account()
            row.update({"plan": plan, "pro_until": pro_until, "checked_at": now_iso()})
            if email:
                row["email"] = email
            self.save()
            return row

    def usage(self) -> dict[str, Any]:
        with self._lock:
            row = self.data.setdefault("usage", copy.deepcopy(DEFAULTS["usage"]))
            if row.get("date") != today_str():
                row.update({"date": today_str(), "count": 0})
            return row

    def bump_usage(self, amount: int = 1) -> int:
        with self._lock:
            row = self.usage()
            row["count"] = int(row.get("count") or 0) + int(amount or 0)
            self.save()
            return int(row["count"])

    def cloud(self) -> dict[str, Any]:
        with self._lock:
            return self.data.setdefault("cloud", copy.deepcopy(DEFAULTS["cloud"]))

    def cloud_auto(self) -> dict[str, Any]:
        with self._lock:
            return self.data.setdefault("cloud_auto", copy.deepcopy(DEFAULTS["cloud_auto"]))

    # ------------------------------------------------------------ backup / restore
    def export_backup(self, dest: str | os.PathLike[str] | None = None) -> Path:
        dest_path = Path(dest) if dest else paths.backup_dir() / f"sa_aim_backup_{_stamp()}.json"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = {
                "app": paths.APP_NAME,
                "version": SCHEMA_VERSION,
                "exported_at": now_iso(),
                "data": scrub_secrets(self.data),
                "excluded": ["api_key", "supabase 토큰", "비밀번호"],
            }
            dest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.utils()["last_backup_at"] = now_iso()
            self.save()
            return dest_path

    def import_backup(self, src: str | os.PathLike[str], *, snapshot: bool = True) -> dict[str, Any]:
        src_path = Path(src)
        raw = json.loads(src_path.read_text(encoding="utf-8"))
        data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
        if not isinstance(data, dict) or not ("leaderboard" in data or "settings" in data):
            raise ValueError("SA AIM LABS 백업 파일이 아닙니다 (settings/leaderboard 없음)")
        with self._lock:
            if snapshot and self.path.exists():
                shutil.copy2(self.path, paths.backup_dir() / f"before_import_{_stamp()}.json")
            merged = _deep_merge(DEFAULTS, data)
            # 백업에는 비밀이 없으니 지금 쓰는 비밀은 유지한다
            current = self.data.get("settings") or {}
            incoming = merged.get("settings") or {}
            for key in ("api_key", "supabase"):
                if key == "supabase":
                    incoming_sb = incoming.get("supabase") or {}
                    current_sb = current.get("supabase") or {}
                    incoming["supabase"] = {
                        "url": incoming_sb.get("url") or current_sb.get("url") or DEFAULT_URL,
                        "anon_key": current_sb.get("anon_key") or DEFAULT_ANON_KEY,
                    }
                elif current.get(key):
                    incoming[key] = current[key]
            merged["settings"] = incoming
            cloud = self.data.get("cloud") or {}
            if cloud.get("access_token"):
                merged["cloud"] = cloud
            for legacy in ("records", "rotation", "more", "attendance", "custom_scenarios"):
                merged.pop(legacy, None)
            merged["created_at"] = self.data.get("created_at") or merged.get("created_at")
            self.data = merged
            self.utils()["last_restore_at"] = now_iso()
            self.save()
            return self.data

    def reset(self, keep_settings: bool = True) -> None:
        with self._lock:
            settings = copy.deepcopy(self.settings) if keep_settings else copy.deepcopy(DEFAULTS["settings"])
            self.data = _deep_merge(DEFAULTS, {"settings": settings, "created_at": now_iso()})
            self.save()


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def load_settings_only(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Used by the headless helper (elevated Drs apply) to read settings fast."""
    p = Path(path) if path else paths.data_file()
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("settings", {})
    except (OSError, ValueError):
        return {}


def new_id() -> str:
    return uuid.uuid4().hex[:12]
