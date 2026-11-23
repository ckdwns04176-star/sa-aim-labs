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
        "goals": {"win": 55.0, "kd": 60.0, "dmg": 1400, "rp": 4000},
        "supabase": {"url": DEFAULT_URL, "anon_key": DEFAULT_ANON_KEY},
        "cloud_email": "",
    },
    "records": [],
    "leaderboard": [],
    "rotation": [],
    "more": [],
    "barracks": {},
    "utils": {
        "nvidia": {"mode": None, "alias": False, "last_backup": None, "points": []},
        "accel": None,
        "last_backup_at": None,
        "last_restore_at": None,
        "last_launch": None,
    },
    "cloud": {"email": None, "user_id": None, "access_token": None, "refresh_token": None, "last_sync": None},
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

    def cloud(self) -> dict[str, Any]:
        return self.data.setdefault("cloud", copy.deepcopy(DEFAULTS["cloud"]))

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
