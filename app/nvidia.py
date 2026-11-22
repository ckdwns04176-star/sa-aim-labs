"""NVIDIA Drs profile apply / backup / restore (real Windows file operations).

Windows keeps per-application 3D settings in
``C:\\ProgramData\\NVIDIA Corporation\\Drs`` (``nvdrsdb0.bin``, ``nvdrsdb1.bin``,
``nvdrssel.bin``, ``nvAppTimestamps``, ``nvdrswr.lk``).  The two shipped packs
replace that folder with a Sudden Attack profile tuned for rifle or sniper aim.

Safety rules implemented here
-----------------------------
* refuse to touch the folder while ``suddenattack.exe`` is running (NVIDIA
  rewrites the DB on game exit and would eat the change);
* always copy the current folder into a timestamped backup first, keeping the
  three most recent restore points;
* verify the copied database really is the shipped profile (size + sha256);
* when the process lacks write permission, ask for elevation through UAC and
  report the result through a small JSON file.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import paths

DRS_DIR = Path(r"C:\ProgramData\NVIDIA Corporation\Drs")
DRS_FILES = ("nvdrsdb0.bin", "nvdrsdb1.bin", "nvdrssel.bin", "nvAppTimestamps", "nvdrswr.lk")
CORE_FILES = ("nvdrsdb0.bin", "nvdrsdb1.bin")
GAME_EXE = "suddenattack.exe"
KEEP_BACKUPS = 3
MODES = {"rifle": "라플 프로필", "sniper": "스나 프로필", "stairfix": "계단현상 끄기"}
RESULT_FILE = "elevated_result.json"


class DrsError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


class DrsManager:
    def __init__(
        self,
        drs_dir: str | os.PathLike[str] | None = None,
        backup_root: str | os.PathLike[str] | None = None,
        profiles_root: str | os.PathLike[str] | None = None,
        *,
        force_platform: bool | None = None,
    ):
        self.drs_dir = Path(drs_dir) if drs_dir else DRS_DIR
        self.backup_root = Path(backup_root) if backup_root else paths.nvidia_backup_dir()
        self.profiles_root = Path(profiles_root) if profiles_root else paths.assets_dir() / "profiles"
        self._force_platform = force_platform

    # ------------------------------------------------------------ platform
    @property
    def supported(self) -> bool:
        if self._force_platform is not None:
            return self._force_platform
        return os.name == "nt"

    def ensure_supported(self) -> None:
        if not self.supported:
            raise DrsError("NVIDIA Drs 적용은 윈도우에서만 동작합니다")

    # ------------------------------------------------------------ probe
    def game_running(self) -> bool | None:
        if os.name != "nt":
            return None
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=8, creationflags=_no_window_flags(),
            )
            text = (out.stdout or "").lower()
            return GAME_EXE in text
        except (OSError, subprocess.SubprocessError):
            return None

    def profile_files(self, mode: str) -> list[Path]:
        root = self.profiles_root / mode
        if not root.is_dir():
            return []
        return [root / name for name in DRS_FILES if (root / name).exists()]

    def available_modes(self) -> dict[str, bool]:
        return {m: bool(self.profile_files(m)) for m in MODES}

    def current_mode(self) -> str | None:
        """Which shipped profile does the live Drs folder match?"""
        for mode in ("rifle", "sniper", "stairfix"):
            src = self.profiles_root / mode / "nvdrsdb0.bin"
            live = self.drs_dir / "nvdrsdb0.bin"
            if src.exists() and live.exists():
                try:
                    if _sha256(src) == _sha256(live):
                        return mode
                except OSError:
                    continue
        return None

    def writable(self) -> bool:
        if not self.drs_dir.exists():
            return False
        try:
            for name in CORE_FILES:
                target = self.drs_dir / name
                if target.exists():
                    return os.access(target, os.W_OK)
            return os.access(self.drs_dir, os.W_OK)
        except OSError:
            return False

    def state(self) -> dict[str, Any]:
        modes = self.available_modes()
        backups = self.list_backups()
        return {
            "supported": self.supported,
            "drs_dir": str(self.drs_dir),
            "drs_exists": self.drs_dir.is_dir(),
            "writable": self.writable(),
            "game_running": self.game_running(),
            "mode": self.current_mode(),
            "alias": False,
            "modes": modes,
            "missing": [m for m, ok in modes.items() if not ok],
            "backups": backups,
            "last_backup": backups[0]["created"] if backups else None,
            "platform": os.name,
        }

    # ------------------------------------------------------------ backups
    def list_backups(self) -> list[dict[str, Any]]:
        if not self.backup_root.is_dir():
            return []
        rows = []
        for folder in sorted(self.backup_root.iterdir(), reverse=True):
            if not folder.is_dir():
                continue
            meta = folder / "meta.json"
            info: dict[str, Any] = {"id": folder.name, "label": folder.name}
            if meta.exists():
                try:
                    info.update(json.loads(meta.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
            info.setdefault("created", _dt.datetime.fromtimestamp(folder.stat().st_mtime).astimezone().isoformat(timespec="seconds"))
            info["files"] = [f.name for f in folder.iterdir() if f.is_file() and f.name != "meta.json"]
            info["id"] = folder.name
            rows.append(info)
        rows.sort(key=lambda r: r.get("created", ""), reverse=True)
        return rows[:20]

    def _make_backup(self, label: str, prev_mode: str | None) -> dict[str, Any]:
        folder = self.backup_root / f"{_stamp()}_{label}"
        folder.mkdir(parents=True, exist_ok=True)
        copied = []
        for name in DRS_FILES:
            src = self.drs_dir / name
            if src.exists():
                shutil.copy2(src, folder / name)
                copied.append(name)
        if not copied:
            shutil.rmtree(folder, ignore_errors=True)
            raise DrsError("현재 Drs 폴더에 백업할 파일이 없습니다")
        meta = {
            "id": folder.name,
            "label": label,
            "created": _now(),
            "previous_mode": prev_mode,
            "files": copied,
        }
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self._prune()
        return meta

    def _prune(self, keep: int = KEEP_BACKUPS) -> None:
        folders = sorted([d for d in self.backup_root.iterdir() if d.is_dir()], reverse=True)
        for old in folders[keep:]:
            shutil.rmtree(old, ignore_errors=True)

    # ------------------------------------------------------------ apply
    def apply(self, mode: str, *, force: bool = False) -> dict[str, Any]:
        self.ensure_supported()
        if mode not in MODES:
            raise DrsError(f"알 수 없는 프로필: {mode}")
        files = self.profile_files(mode)
        if len(files) < 2 or not (self.profiles_root / mode / "nvdrsdb0.bin").exists():
            raise DrsError(
                f"'{MODES[mode]}' Drs 파일이 앱에 내장되어 있지 않습니다 · "
                f"assets/profiles/{mode}/ 에 {', '.join(DRS_FILES)} 를 넣어 주세요"
            )
        if not self.drs_dir.is_dir():
            raise DrsError(f"NVIDIA Drs 폴더를 찾지 못했습니다 · {self.drs_dir}")
        if not force and self.game_running():
            raise DrsError("서든어택이 실행 중입니다 · 게임을 완전히 종료한 뒤 다시 시도해 주세요")

        prev = self.current_mode()
        if prev == mode:
            return {"ok": True, "mode": mode, "unchanged": True, "message": f"{MODES[mode]}이 이미 적용되어 있습니다"}

        backup = self._make_backup(f"{MODES[mode]} 적용 전", prev)
        try:
            for path in files:
                target = self.drs_dir / path.name
                shutil.copy2(path, target)
            for name in CORE_FILES:
                target = self.drs_dir / name
                if target.exists():
                    if _sha256(target) != _sha256(self.profiles_root / mode / name):
                        raise DrsError("복사한 Drs DB 검증에 실패했습니다")
        except PermissionError as exc:
            raise DrsError(f"관리자 권한이 필요합니다 · {exc}") from exc
        except OSError as exc:
            raise DrsError(f"Drs 파일을 쓰지 못했습니다 · {exc}") from exc

        return {
            "ok": True,
            "mode": mode,
            "backup": backup["id"],
            "message": f"{MODES[mode]} 적용 완료 · 이전 설정 자동 백업",
            "applied_at": _now(),
        }

    def restore(self, backup_id: str) -> dict[str, Any]:
        self.ensure_supported()
        folder = self.backup_root / str(backup_id)
        if not folder.is_dir():
            raise DrsError(f"복구 지점을 찾지 못했습니다 · {backup_id}")
        if self.game_running():
            raise DrsError("서든어택이 실행 중입니다 · 게임을 완전히 종료한 뒤 다시 시도해 주세요")
        meta = {}
        if (folder / "meta.json").exists():
            try:
                meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        copied = 0
        try:
            for name in DRS_FILES:
                src = folder / name
                if src.exists():
                    shutil.copy2(src, self.drs_dir / name)
                    copied += 1
        except PermissionError as exc:
            raise DrsError(f"관리자 권한이 필요합니다 · {exc}") from exc
        if not copied:
            raise DrsError("백업 폴더에 복구할 Drs 파일이 없습니다")
        return {
            "ok": True,
            "backup": str(backup_id),
            "restored": copied,
            "previous_mode": meta.get("previous_mode"),
            "message": f"복구 완료 · {meta.get('label', backup_id)} 상태로 되돌렸습니다",
        }

    # ------------------------------------------------------------ alias / stairfix
    def apply_alias(self, enable: bool) -> dict[str, Any]:
        """'계단현상 끄기' — needs the dedicated Drs pack (assets/profiles/stairfix)."""
        if enable:
            return self.apply("stairfix")
        result = self.restore_latest()
        return result

    def restore_latest(self) -> dict[str, Any]:
        backups = self.list_backups()
        if not backups:
            raise DrsError("복구 지점이 없습니다 · 먼저 프로필을 적용해 주세요")
        return self.restore(backups[0]["id"])

    # ------------------------------------------------------------ elevation
    def elevate_help(self, mode: str) -> dict[str, Any]:
        """Re-launch this executable elevated to finish the copy (UAC prompt)."""
        if os.name != "nt":
            raise DrsError("관리자 권한 상승은 윈도우에서만 지원합니다")
        import ctypes

        result_file = paths.data_dir() / RESULT_FILE
        if getattr(sys, "frozen", False):
            exe, args = sys.executable, f'--drs-apply "{mode}" --result "{result_file}"'
        else:
            exe = sys.executable
            script = Path(__file__).resolve().parent.parent / "main.py"
            args = f'"{script}" --drs-apply "{mode}" --result "{result_file}"'
        try:
            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 1)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - pywin32-less environment
            raise DrsError(f"관리자 권한 요청을 보내지 못했습니다 · {exc}") from exc
        if int(rc) <= 32:
            raise DrsError("관리자 권한 요청이 취소되었습니다 (UAC 거부)")
        return {"elevated": True, "result_file": str(result_file),
                "message": "관리자 권한으로 다시 적용 중입니다 · UAC 창을 승인해 주세요"}


def write_result(path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": _now(), **payload}, ensure_ascii=False, indent=2), encoding="utf-8")


def read_result() -> dict[str, Any] | None:
    p = paths.data_dir() / RESULT_FILE
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        p.unlink()
    except OSError:
        pass
    return data


def run_elevated_cli(mode: str, result_path: str | os.PathLike[str]) -> int:
    """Entry point of the elevated helper process (``--drs-apply``)."""
    try:
        mgr = DrsManager()
        result = mgr.apply(mode, force=True)
        result["elevated"] = True
        write_result(result_path, result)
        return 0
    except Exception as exc:  # noqa: BLE001 - report every failure to the UI
        write_result(result_path, {"ok": False, "elevated": True, "mode": mode, "message": str(exc)})
        return 1
