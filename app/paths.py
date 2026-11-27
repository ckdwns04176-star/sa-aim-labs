"""Filesystem locations for SA AIM LABS.

The app keeps its data in ``%APPDATA%\\SA_Aim_Labs`` by default.  Dropping a
``portable.txt`` file next to the executable (or setting ``SA_AL_PORTABLE=1``)
makes it keep ``data.json`` beside the executable instead, which is what the
PC-bang use case wants.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "SA_Aim_Labs"
APP_TITLE = "SA AIM LABS"
APP_VERSION = "2.0.6"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Directory that holds ui/, assets/ (the PyInstaller temp dir when frozen)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def exe_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return bundle_dir()


def portable_mode() -> bool:
    if os.environ.get("SA_AL_PORTABLE") == "1":
        return True
    try:
        return (exe_dir() / "portable.txt").exists()
    except OSError:
        return False


def data_dir() -> Path:
    env = os.environ.get("SA_AL_DATA_DIR")
    if env:
        path = Path(env).expanduser()
    elif portable_mode():
        path = exe_dir() / "data"
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        path = Path(base) / APP_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_file() -> Path:
    return data_dir() / "data.json"


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def nvidia_backup_dir() -> Path:
    path = data_dir() / "nvidia_backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def reports_dir() -> Path:
    """`📸 리포트 카드 저장` 이 만드는 PNG 보관함."""
    path = data_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def playlists_dir() -> Path:
    """코박스 Playlists 폴더를 찾지 못했을 때 쓰는 대체 보관함."""
    path = data_dir() / "playlists"
    path.mkdir(parents=True, exist_ok=True)
    return path


def perf_backup_file() -> Path:
    """전원 옵션·게임 모드 적용 전 상태 (복구용)."""
    return data_dir() / "perf_backup.json"


def ui_dir() -> Path:
    return bundle_dir() / "ui"


def index_file() -> Path:
    return ui_dir() / "index.html"


def assets_dir() -> Path:
    return bundle_dir() / "assets"


def profile_dir(mode: str) -> Path:
    return assets_dir() / "profiles" / mode


def icon_file() -> Path:
    return assets_dir() / "app.ico"


def index_url() -> str:
    return index_file().as_uri()
