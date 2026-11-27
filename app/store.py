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

# v2.0.1 — 오버레이 기본 단축키를 F8/F9 → Insert/Home 으로 바꿨다.
#   설정에 남아 있는 '예전 기본값(F8·F9)' 만 한 번 새 기본값으로 옮긴다.
#   사용자가 직접 고른 다른 키는 건드리지 않는다.
HOTKEY_DEFAULTS = {"overlay_hotkey_lobby": "Insert", "overlay_hotkey_scout": "Home"}
LEGACY_HOTKEY_DEFAULTS = {"overlay_hotkey_lobby": "F8", "overlay_hotkey_scout": "F9"}

# 백업 가져오기 상한 — 손상·악성 파일이 앱 데이터를 망가뜨리지 않게
BACKUP_MAX_BYTES = 20 * 1024 * 1024
BACKUP_MAX_DEPTH = 12

# 백업 파일에 절대 들어가면 안 되는 키 (settings 안쪽 이름 기준)
SECRET_KEYS = ("api_key", "access_token", "refresh_token", "password", "anon_key", "service_key")

# 한 사람이 쓰는 서든 계정 — 최대 3개까지 저장하고 원클릭으로 전환한다 (v2.0.4).
MAX_ACCOUNTS = 3
NICK_MAX = 12

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
        "overlay_hotkey_lobby": "Insert",
        "overlay_hotkey_scout": "Home",
        "hotkey_defaults_version": 2,
        "overlay_mapban_seconds": 12,
        "overlay_party_seconds": 15,
        "overlay_scan_x": 0,
        "overlay_scan_y": 0,
        "overlay_scan_scale": 1.0,
        "toast_enabled": False,
        "toast_defaults_version": 1,
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


def _value_depth(node: Any, depth: int = 0) -> int:
    """중첩 깊이 — 비정상적으로 깊은 JSON 을 걸러낸다."""
    if depth > BACKUP_MAX_DEPTH:
        return depth
    if isinstance(node, dict):
        return max([depth] + [_value_depth(v, depth + 1) for v in node.values()])
    if isinstance(node, list):
        return max([depth] + [_value_depth(v, depth + 1) for v in node])
    return depth


def _validate_backup(data: dict[str, Any], raw: dict[str, Any] | None = None) -> None:
    """가져올 백업 한 건을 검사한다 — 문제가 있으면 ``ValueError``.

    data.json 을 건드리기 전에 호출해야 기존 데이터가 안전하다.
    """
    if not isinstance(data, dict):
        raise ValueError("백업 데이터 형식이 올바르지 않습니다")
    if not ("settings" in data or "leaderboard" in data):
        raise ValueError("SA AIM LABS 백업 파일이 아닙니다 (settings/leaderboard 없음)")
    stamp = (raw or {}).get("version", data.get("version"))
    if stamp not in (None, ""):
        try:
            version = int(stamp)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"백업 version 값이 숫자가 아닙니다 ({stamp!r})") from exc
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"이 백업은 더 새로운 앱(v{version})에서 만든 파일입니다 · 지금 앱(v{SCHEMA_VERSION})에서는 복원할 수 없습니다")
        if version < 1:
            raise ValueError(f"백업 version 값이 올바르지 않습니다 ({version})")
    if _value_depth(data) > BACKUP_MAX_DEPTH:
        raise ValueError("백업 구조가 너무 깊어 읽을 수 없습니다")
    if "settings" in data and not isinstance(data["settings"], dict):
        raise ValueError("백업의 settings 가 객체가 아닙니다")
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    for key in ("nickname", "theme", "toast_position", "overlay_hotkey_lobby", "overlay_hotkey_scout"):
        if key in settings and not isinstance(settings[key], str):
            raise ValueError(f"백업 settings.{key} 값이 문자형이 아닙니다")
    for key in ("ui_scale", "tilt_interval", "overlay_mapban_seconds", "overlay_party_seconds"):
        if key not in settings:
            continue
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"백업 settings.{key} 값이 숫자가 아닙니다")
    if "leaderboard" in data and not isinstance(data["leaderboard"], list):
        raise ValueError("백업의 leaderboard 가 목록이 아닙니다")
    for row in (data.get("leaderboard") or [])[:50]:
        if not isinstance(row, dict) or not isinstance(row.get("name", ""), str):
            raise ValueError("백업의 순위표 행 형식이 올바르지 않습니다")
    for key in ("memos", "barracks", "barracks_ids", "play_days", "utils", "overlay", "cloud"):
        if key in data and not isinstance(data[key], dict):
            raise ValueError(f"백업의 {key} 가 객체가 아닙니다")
    for key in ("barracks_history", "condition_history", "party_presets"):
        if key in data and not isinstance(data[key], list):
            raise ValueError(f"백업의 {key} 가 목록이 아닙니다")
    if "account" in data and not isinstance(data["account"], dict):
        raise ValueError("백업의 account 가 객체가 아닙니다")


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
            self._migrate_hotkeys(data)
            self._migrate_toast(data)
            self.data = _deep_merge(DEFAULTS, data)
            # v1 백업/데이터에 남아 있던 에임 연습 구역은 더 이상 쓰지 않는다.
            for legacy in ("records", "rotation", "more", "attendance", "custom_scenarios"):
                self.data.pop(legacy, None)
            if not self.data.get("created_at"):
                self.data["created_at"] = now_iso()
            return self.data

    @staticmethod
    def _migrate_hotkeys(raw: dict[str, Any]) -> None:
        """v2.0.1 — 예전 기본값(F8/F9)만 새 기본값(Insert/Home)으로 한 번 옮긴다.

        사용자가 직접 고른 단축키(F10, Ctrl+F8 등)는 그대로 둔다.
        """
        if not isinstance(raw, dict):
            return
        settings = raw.get("settings")
        if not isinstance(settings, dict):
            return
        if settings.get("hotkey_defaults_version") == 2:
            return
        for key, legacy in LEGACY_HOTKEY_DEFAULTS.items():
            current = str(settings.get(key) or "").strip()
            if not current or current.upper() == legacy.upper():
                settings[key] = HOTKEY_DEFAULTS[key]
        settings["hotkey_defaults_version"] = 2

    @staticmethod
    def _migrate_toast(raw: dict[str, Any]) -> None:
        """v2.0.6 — 경기 종료 토스트 감시를 기본 껴짐으로 내린다 (데이터당 한 번).

        예전에는 ``toast_enabled`` 기본값이 True 여서, 저장 파일에는 사용자가 켜지
        않아도 True 가 적혔다.  그래서 이 버전으로 처음 올라올 때 한 번만 껴 두고
        (``toast_defaults_version`` 표식을 남긴다) 원하는 사람은 설정 화면의
        토글로 바로 켤 수 있게 한다.  표식이 있는 뒤에는 사용자가 고른 값이 그대로
        유지된다.
        """
        if not isinstance(raw, dict):
            return
        settings = raw.get("settings")
        if not isinstance(settings, dict):
            return
        if settings.get("toast_defaults_version"):
            return
        settings["toast_enabled"] = False
        settings["toast_defaults_version"] = 1

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

    # ---------------------------------------------------------------- 서든 계정 (최대 3)
    def accounts(self) -> list[dict[str, Any]]:
        """저장된 서든 계정 목록.

        예전 단일 ``nickname`` 설정과 하위 호환 — 목록이 비어 있으면 현재
        닉네임 하나를 첫 계정으로 돌려준다.  중복은 버리고 최대 3개만 남긴다.
        """
        with self._lock:
            raw = self.data["settings"].get("accounts")
            current = str(self.data["settings"].get("nickname") or "").strip()[:NICK_MAX]
        rows: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for item in raw:
                label = ""
                if isinstance(item, dict):
                    nick = str(item.get("nickname") or "").strip()[:NICK_MAX]
                    label = str(item.get("label") or "").strip()[:16]
                elif isinstance(item, str):
                    nick = item.strip()[:NICK_MAX]
                else:
                    nick = ""
                if nick:
                    rows.append({"nickname": nick, "label": label})
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for row in rows:
            key = row["nickname"].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        if not out and current:
            out = [{"nickname": current, "label": ""}]
        return out[:MAX_ACCOUNTS]

    def active_account_index(self) -> int:
        """지금 조회 기준이 되는 계정 번호 — 현재 닉네임과 일치하는 칸을 우선."""
        current = str(self.settings.get("nickname") or "").strip().lower()
        rows = self.accounts()
        for index, row in enumerate(rows):
            if row["nickname"].lower() == current:
                return index
        try:
            index = int(self.settings.get("active_account") or 0)
        except (TypeError, ValueError):
            index = 0
        return index if 0 <= index < len(rows) else 0

    def accounts_state(self) -> dict[str, Any]:
        rows = self.accounts()
        return {"items": rows, "active": self.active_account_index(), "max": MAX_ACCOUNTS,
                "nickname": str(self.settings.get("nickname") or "")}

    def _write_accounts(self, rows: list[dict[str, Any]], active: int,
                        nickname: str | None = None) -> dict[str, Any]:
        rows = rows[:MAX_ACCOUNTS]
        active = max(0, min(int(active), max(0, len(rows) - 1)))
        with self._lock:
            settings = self.data["settings"]
            settings["accounts"] = rows
            settings["active_account"] = active
            if nickname is not None:
                settings["nickname"] = nickname
            elif rows and active < len(rows):
                settings["nickname"] = rows[active]["nickname"]
            self.save()
        return self.accounts_state()

    def save_account(self, index: Any = 0, nickname: Any = "", label: Any = "") -> dict[str, Any]:
        """한 칸의 닉네임을 등록·수정한다 (빈 닉네임 = 그 칸 비우기)."""
        nick = str(nickname or "").strip()[:NICK_MAX]
        try:
            slot = int(index)
        except (TypeError, ValueError):
            slot = 0
        slot = max(0, min(MAX_ACCOUNTS - 1, slot))
        rows = list(self.accounts())
        active = self.active_account_index()

        if not nick:
            if slot < len(rows):
                rows.pop(slot)
                if active > slot:
                    active -= 1
            active = max(0, min(active, len(rows) - 1))
            return self._write_accounts(rows, active, rows[active]["nickname"] if rows else "")

        while len(rows) <= slot:
            rows.append({"nickname": "", "label": ""})
        rows[slot] = {"nickname": nick,
                      "label": str(label or rows[slot].get("label") or "").strip()[:16]}

        # 같은 닉네임이 두 칸에 들어가지 않게 앞 칸만 남긴다.
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for row in rows:
            key = str(row.get("nickname") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(row)
        rows = unique[:MAX_ACCOUNTS]

        if slot == active or len(rows) == 1:
            active = next((i for i, row in enumerate(rows)
                           if row["nickname"].lower() == nick.lower()), 0)
            return self._write_accounts(rows, active, nick)
        active = min(active, len(rows) - 1)
        return self._write_accounts(rows, active)

    def switch_account(self, index: Any = 0) -> dict[str, Any]:
        """계정 전환 — 홈·병영수첩 조회 기준 닉네임이 바로 바뀐다."""
        rows = self.accounts()
        if not rows:
            return self.accounts_state()
        try:
            slot = int(index)
        except (TypeError, ValueError):
            slot = 0
        slot = max(0, min(slot, len(rows) - 1))
        return self._write_accounts(rows, slot, rows[slot]["nickname"])

    def remember_account(self, nickname: Any) -> dict[str, Any]:
        """설정에서 저장한 닉네임을 계정 목록에 반영한다.

        이미 있으면 그 칸을 활성화하고, 없으면 빈 칸에 추가한다.  세 칸이 모두
        차 있으면 지금 활성 칸을 새 닉네임으로 교체한다.
        """
        nick = str(nickname or "").strip()[:NICK_MAX]
        rows = self.accounts()
        active = self.active_account_index()
        if not nick:
            return self._write_accounts(rows, active, "")
        keys = [row["nickname"].lower() for row in rows]
        if nick.lower() in keys:
            return self._write_accounts(rows, keys.index(nick.lower()), nick)
        if len(rows) < MAX_ACCOUNTS:
            rows = rows + [{"nickname": nick, "label": ""}]
            return self._write_accounts(rows, len(rows) - 1, nick)
        rows[active] = {"nickname": nick, "label": rows[active].get("label") or ""}
        return self._write_accounts(rows, active, nick)

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
        """백업 JSON 을 가져온다 — 검증이 끝나기 전에는 현재 데이터를 건드리지 않는다.

        v2.0.1 — 크기 상한 · JSON 구조 · version 호환성 · 주요 필드 자료형을 먼저
        확인한다.  하나라도 걸리면 ``ValueError`` 로 멈추고 data.json 은 그대로 둔다.
        """
        src_path = Path(src)
        try:
            size = src_path.stat().st_size
        except OSError as exc:
            raise ValueError(f"백업 파일을 읽을 수 없습니다 · {exc}") from exc
        if size <= 0:
            raise ValueError("백업 파일이 비어 있습니다")
        if size > BACKUP_MAX_BYTES:
            raise ValueError(f"백업 파일이 너무 큽니다 ({size / 1024 / 1024:.1f}MB · 최대 20MB)")
        try:
            raw = json.loads(src_path.read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError("백업 파일이 UTF-8 JSON 이 아닙니다") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"백업 파일의 JSON 이 손상되었습니다 (줄 {exc.lineno}) ") from exc
        if not isinstance(raw, dict):
            raise ValueError("SA AIM LABS 백업 파일이 아닙니다 (JSON 최상위가 객체가 아님)")
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        if not isinstance(data, dict) or not data:
            raise ValueError("SA AIM LABS 백업 파일이 아닙니다 (data 없음)")
        _validate_backup(data, raw)
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
