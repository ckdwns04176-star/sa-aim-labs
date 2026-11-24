"""Local JSON storage (``data.json``) with atomic writes and .json backups."""
from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from . import paths
from .supabase_sync import DEFAULT_ANON_KEY, DEFAULT_URL

SCHEMA_VERSION = 1
MAX_RECORDS = 5000

DEFAULTS: dict[str, Any] = {
    "version": SCHEMA_VERSION,
    "created_at": None,
    "updated_at": None,
    "settings": {
        "nickname": "",
        "api_key": "",
        "kovak_path": r"C:\Program Files (x86)\Steam\steamapps\common\FPSAimTrainer\FPSAimTrainer.exe",
        "aimlab_path": r"C:\Program Files (x86)\Steam\steamapps\common\Aim Lab\AimLab.exe",
        "auto_sync": True,
        "auto_launch": True,
        "tray_background": False,
        "notify": True,
        "autosave": True,
        "hotkey_enabled": True,
        "kovak_stats_path": "",
        "kovak_autofill": False,
        "onboarded": False,
        "weapon": "sniper",
        "game": "kovak",
        "tilt_watch": True,
        "tilt_interval": 240,
        "week_goals": {"days": 5, "games": 25},
        "goals": {"win": 55.0, "kd": 60.0, "dmg": 1400, "rp": 4000},
        "supabase": {"url": DEFAULT_URL, "anon_key": DEFAULT_ANON_KEY},
        "cloud_email": "",
        # ── v1.5.0 ──
        "theme": "coral",                 # coral / ice / gold (#4)
        "ui_scale": 100,                  # 90 / 100 / 115 / 125 (#1)
        "ui_scale_auto": True,            # QHD 이상이면 자동으로 키운다
        "excluded_scenarios": [],         # 추천에서 빼 둔 시나리오 이름
        "watch_trainers": True,           # 코박스·에임랩 종료 감지 알림
        "auto_cloud": True,               # 로그인 상태면 30분마다 자동 동기화
    },
    "records": [],
    "leaderboard": [],
    "memos": {},
    "rotation": [],
    "more": [],
    "barracks": {},
    "barracks_ids": {},          # 닉네임 → {sn, clan_id, photo, profile}
    "custom_scenarios": [],      # 사용자가 직접 추가한 시나리오 (#5)
    "barracks_history": [],
    "condition_history": [],
    "ui": {"digest_ack": None},
    "attendance": {},
    "utils": {
        "nvidia": {"mode": None, "alias": False, "last_backup": None, "points": []},
        "accel": None,
        "perf": {"applied_at": None, "backup": None},
        "last_backup_at": None,
        "last_restore_at": None,
        "last_launch": None,
    },
    "cloud": {"email": None, "user_id": None, "access_token": None, "refresh_token": None, "last_sync": None},
    "cloud_auto": {"last_at": None, "last_reason": None},
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


class Store:
    """Thread-safe enough for the app: every mutation saves the whole document."""

    # pywebview walks the js_api object's public attributes and recurses into
    # anything non-callable.  This marker stops that walk at the store, so
    # internal methods (``save``, ``records`` …) are never exposed to JS.
    _serializable = False

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path else paths.data_file()
        self.data: dict[str, Any] = {}
        self.load_error: str | None = None
        self.load()

    # ------------------------------------------------------------ io
    def load(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:  # corrupted file: keep a copy and start fresh
                self.load_error = f"data.json을 읽지 못했습니다 ({exc})"
                try:
                    shutil.copy2(self.path, self.path.with_suffix(".corrupt.json"))
                except OSError:
                    pass
                data = {}
        self.data = _deep_merge(DEFAULTS, data)
        if not self.data.get("created_at"):
            self.data["created_at"] = now_iso()
        return self.data

    def save(self) -> None:
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
        return self.data["settings"]

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.data["settings"] = _deep_merge(self.data["settings"], patch or {})
        self.save()
        return self.data["settings"]

    def public_settings(self) -> dict[str, Any]:
        """Settings safe to hand to the UI (API keys are masked, never sent raw)."""
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

    # ------------------------------------------------------------ records
    def records(self) -> list[dict[str, Any]]:
        return self.data.setdefault("records", [])

    def add_record(self, record: dict[str, Any]) -> dict[str, Any]:
        record = copy.deepcopy(record)
        record.setdefault("id", uuid.uuid4().hex[:12])
        record.setdefault("created_at", now_iso())
        record.setdefault("date", today_str())
        self.records().append(record)
        if len(self.records()) > MAX_RECORDS:
            self.data["records"] = self.records()[-MAX_RECORDS:]
        self.save()
        return record

    def delete_record(self, record_id: str) -> bool:
        before = len(self.records())
        self.data["records"] = [r for r in self.records() if r.get("id") != record_id]
        if len(self.data["records"]) != before:
            self.save()
            return True
        return False

    # ------------------------------------------------------------ leaderboard
    def leaderboard(self) -> list[dict[str, Any]]:
        return self.data.setdefault("leaderboard", [])

    def set_leaderboard(self, members: list[dict[str, Any]]) -> None:
        self.data["leaderboard"] = members[:10]
        self.save()

    # ------------------------------------------------------------ friend memos (v1.3.1)
    def memos(self) -> dict[str, str]:
        """친구별 메모 {'닉네임': '메모'} — 순위표 우클릭으로 적는 한 줄 기록."""
        return self.data.setdefault("memos", {})

    def set_memo(self, nickname: str, text: str) -> dict[str, str]:
        name = str(nickname or "").strip()
        if not name:
            raise ValueError("닉네임이 없습니다")
        body = str(text or "").strip()[:400]
        table = self.memos()
        if body:
            table[name] = body
        else:
            table.pop(name, None)
        self.save()
        return table

    # ------------------------------------------------------------ rotation
    def rotation(self) -> list[dict[str, Any]]:
        return self.data.setdefault("rotation", [])

    def set_rotation(self, rows: list[dict[str, Any]]) -> None:
        self.data["rotation"] = rows
        self.save()

    # ------------------------------------------------------------ more practice
    def more(self) -> list[dict[str, Any]]:
        """`+ 더 연습하기` 로 오늘 연 시나리오 목록 [{date, game, weapon, names}]"""
        return self.data.setdefault("more", [])

    def set_more(self, rows: list[dict[str, Any]], keep_days: int = 30) -> None:
        rows = rows[-keep_days * 4:]
        self.data["more"] = rows
        self.save()

    # ------------------------------------------------------------ misc sections
    def utils(self) -> dict[str, Any]:
        return self.data.setdefault("utils", copy.deepcopy(DEFAULTS["utils"]))

    def barracks_cache(self) -> dict[str, Any]:
        return self.data.setdefault("barracks", {})

    # ------------------------------------------------------------ barracks ids (v1.5.0)
    def barracks_ids(self) -> dict[str, Any]:
        """닉네임 → 병영수첩 user_nexon_sn·클랜 ID·프로필 사진 캐시."""
        return self.data.setdefault("barracks_ids", {})

    # ------------------------------------------------------------ custom scenarios (v1.5.0)
    def custom_scenarios(self) -> list[dict[str, Any]]:
        return self.data.setdefault("custom_scenarios", [])

    # ------------------------------------------------------------ barracks snapshots (v1.3.0)
    def barracks_history(self) -> list[dict[str, Any]]:
        """병영수첩 최신화 스냅샷 [{date, at, nickname, stats, tier, basic}] — 날짜별 비교용."""
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
        rows = self.barracks_history()
        if rows and rows[-1].get("date") == snapshot["date"] and rows[-1].get("at") == snapshot["at"]:
            return rows[-1]
        rows.append(snapshot)
        self.data["barracks_history"] = rows[-keep:]
        self.save()
        return snapshot

    # ------------------------------------------------------------ attendance (v1.3.0)
    def attendance(self) -> dict[str, Any]:
        """날짜별 훈련 흔적 {'YYYY-MM-DD': {'n': 시나리오 수, 'r': 기록 수}}.

        `점수 전체 초기화` 는 연습 점수·PB 만 지우고 **훈련 잔디(출석)** 는 남긴다.
        그래서 기록과 별도로 날짜별 흔적을 보관한다.
        """
        return self.data.setdefault("attendance", {})

    def note_attendance(self, records: list[dict[str, Any]]) -> None:
        """오늘 기록에서 시나리오 수·기록 수를 다시 세어 보관(줄어들지 않게 max)."""
        by_day: dict[str, dict[str, Any]] = {}
        for rec in records:
            day = str(rec.get("date") or str(rec.get("created_at") or "")[:10])
            if not day:
                continue
            names = {str(s.get("name")) for s in (rec.get("scenarios") or []) if s.get("score") is not None}
            slot = by_day.setdefault(day, {"n": 0, "r": 0})
            slot["n"] += len(names) or 1
            slot["r"] += 1
        table = self.attendance()
        changed = False
        for day, slot in by_day.items():
            old = table.get(day) or {}
            merged = {"n": max(int(old.get("n") or 0), int(slot["n"])),
                      "r": max(int(old.get("r") or 0), int(slot["r"]))}
            if merged != old:
                table[day] = merged
                changed = True
        if changed:
            self.save()

    # ------------------------------------------------------------ ui flags
    def ui(self) -> dict[str, Any]:
        return self.data.setdefault("ui", {"digest_ack": None})

    # ------------------------------------------------------------ reset (v1.3.0)
    def reset_scores(self) -> dict[str, int]:
        """에임 연습 점수·PB·로그만 지운다.

        남기는 것 — 설정(닉네임·API 키·친구 목록·유틸), 순위표, 병영수첩/변화 이력,
        훈련 잔디(attendance: 날짜별 출석·훈련 흔적).  지우는 것 — 훈련 기록(점수·PB),
        오늘 추천 로테이션, 더 연습하기 목록.
        """
        self.note_attendance(self.records())      # 지우기 직전에 오늘 흔적을 확정
        removed = {"records": len(self.records()), "rotation": len(self.rotation()), "more": len(self.more())}
        self.data["records"] = []
        self.data["rotation"] = []
        self.data["more"] = []
        self.data.setdefault("ui", {})["digest_ack"] = None
        self.save()
        return removed

    def cloud(self) -> dict[str, Any]:
        return self.data.setdefault("cloud", copy.deepcopy(DEFAULTS["cloud"]))

    def cloud_auto(self) -> dict[str, Any]:
        """자동 백업 상태 {last_at, last_reason}."""
        return self.data.setdefault("cloud_auto", copy.deepcopy(DEFAULTS["cloud_auto"]))

    # ------------------------------------------------------------ backup / restore
    def export_backup(self, dest: str | os.PathLike[str] | None = None) -> Path:
        dest_path = Path(dest) if dest else paths.backup_dir() / f"sa_aim_backup_{_stamp()}.json"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "app": paths.APP_NAME,
            "version": SCHEMA_VERSION,
            "exported_at": now_iso(),
            "data": self.data,
        }
        dest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.utils()["last_backup_at"] = now_iso()
        self.save()
        return dest_path

    def import_backup(self, src: str | os.PathLike[str], *, snapshot: bool = True) -> dict[str, Any]:
        src_path = Path(src)
        raw = json.loads(src_path.read_text(encoding="utf-8"))
        data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
        if not isinstance(data, dict) or ("records" not in data and "settings" not in data):
            raise ValueError("SA AIM LABS 백업 파일이 아닙니다 (records/settings 없음)")
        if snapshot and self.path.exists():
            shutil.copy2(self.path, paths.backup_dir() / f"before_import_{_stamp()}.json")
        merged = _deep_merge(DEFAULTS, data)
        merged["created_at"] = self.data.get("created_at") or merged.get("created_at")
        self.data = merged
        self.utils()["last_restore_at"] = now_iso()
        self.save()
        return self.data

    def reset(self, keep_settings: bool = True) -> None:
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
