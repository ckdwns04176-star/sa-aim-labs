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

from . import drs, paths

DRS_DIR = Path(r"C:\ProgramData\NVIDIA Corporation\Drs")
DRS_FILES = ("nvdrsdb0.bin", "nvdrsdb1.bin", "nvdrssel.bin", "nvAppTimestamps", "nvdrswr.lk")
CORE_FILES = ("nvdrsdb0.bin", "nvdrsdb1.bin")
GAME_EXE = drs.GAME_EXE
KEEP_BACKUPS = 3
MODES = {"rifle": "라플 프로필", "sniper": "스나 프로필", "stairfix": "계단현상 끄기"}
PACK_MODES = ("rifle", "sniper")
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


def _same_except_stairfix(a: bytes, b: bytes) -> bool:
    """두 Drs DB 가 계단현상 끄기(AA compatibility) 값만 다른지.

    팩을 덮어쓴 뒤 AA 값을 0x00401241 로 패치하므로, 라이브 DB 는 팩과
    4바이트만 달라진다.  '이미 그 팩이 적용되어 있다'를 그렇게 판별한다.
    """
    if len(a) != len(b):
        return False
    try:
        pa, pb = drs.profile_offset(a), drs.profile_offset(b)
        if pa is None or pb is None:
            return a == b
        va, vb = dict(drs.values_of(a)), dict(drs.values_of(b))
        if set(va) != set(vb):
            return False
        for key, value in va.items():
            other = vb[key]
            if value == other:
                continue
            if key == drs.SETTING_AA_COMPAT:
                continue
            return False
        return True
    except Exception:  # noqa: BLE001 - 판별 실패는 '다르다'로 처리
        return False


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

    # ------------------------------------------------------------ live DB
    @property
    def live_db(self) -> Path:
        return self.drs_dir / "nvdrsdb0.bin"

    def live_blob(self) -> bytes | None:
        try:
            return self.live_db.read_bytes()
        except OSError:
            return None

    def stairfix_value(self) -> int | None:
        """Current Antialiasing compatibility of suddenattack.exe in the live DB."""
        blob = self.live_blob()
        if blob is None:
            return None
        try:
            return drs.aa_compat(blob)
        except drs.DrsFormatError:
            return None

    def stairfix_ready(self) -> bool:
        """True when 계단현상 끄기 can be applied with the built-in patch logic."""
        if not self.supported or not self.drs_dir.is_dir():
            return False
        blob = self.live_blob()
        if blob is None:
            return False
        try:
            return drs.has_profile(blob)
        except drs.DrsFormatError:
            return False

    def stairfix_applied(self) -> bool:
        return self.stairfix_value() == drs.STAIRFIX_VALUE

    def available_modes(self) -> dict[str, bool]:
        modes = {m: bool(self.profile_files(m)) for m in PACK_MODES}
        # 계단현상 끄기 is a built-in patch of the live Drs DB (no pack needed) —
        # a shipped assets/profiles/stairfix pack still wins when present.
        modes["stairfix"] = bool(self.profile_files("stairfix")) or self.stairfix_ready()
        return modes

    def current_mode(self) -> str | None:
        """Which profile is applied right now?

        v1.3.0 — 팩 적용과 함께 계단현상 끄기가 병합되므로, 팩 일치를 먼저 보고
        그다음에 계단현상만 적용된 상태를 본다(팩을 적용해 둔 사람이
        'stairfix'로 보여 다시 적용되는 일을 막는다).
        """
        live = self.drs_dir / "nvdrsdb0.bin"
        for mode in PACK_MODES:
            src = self.profiles_root / mode / "nvdrsdb0.bin"
            if not (src.exists() and live.exists()):
                continue
            try:
                if _sha256(src) == _sha256(live):
                    return mode
            except OSError:
                continue
            # 팩 + 계단현상 끄기 병합본도 그 팩으로 본다 — AA 값만 다른 경우
            try:
                if self.stairfix_applied() and _same_except_stairfix(src.read_bytes(), live.read_bytes()):
                    return mode
            except OSError:
                continue
        if self.stairfix_applied():
            return "stairfix"
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
        stair_value = self.stairfix_value()
        mode = self.current_mode()
        return {
            "supported": self.supported,
            "drs_dir": str(self.drs_dir),
            "drs_exists": self.drs_dir.is_dir(),
            "writable": self.writable(),
            "game_running": self.game_running(),
            "mode": mode,
            "alias": mode == "stairfix",
            "stairfix": {
                "ready": modes.get("stairfix", False),
                "applied": stair_value == drs.STAIRFIX_VALUE,
                "value": None if stair_value is None else f"0x{stair_value:08X}",
                "label": drs.describe(stair_value),
                "target": f"0x{drs.STAIRFIX_VALUE:08X}",
            },
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
        """프로필 팩 적용 + 계단현상 끄기 병합 (v1.3.0).

        예전에는 `라플 최적화` · `스나 최적화` · `계단현상끄기` 세 버튼이 따로
        있었지만, 이제 프로필을 적용할 때 Antialiasing compatibility
        (``0x00D55F7D``) 값을 ``0x00401241`` 로 같이 패치해 **두 버튼**으로
        통일했다.  복구는 기존 백업(적용 전 폴더) 하나로 둘 다 되돌아간다.
        """
        self.ensure_supported()
        if mode not in MODES:
            raise DrsError(f"알 수 없는 프로필: {mode}")
        if mode == "stairfix" and not self.profile_files("stairfix"):
            # 계단현상 끄기 = 내장된 Drs 패치 로직 (프로필 팩 없이 동작)
            return self.apply_stairfix(force=force)
        files = self.profile_files(mode)
        if len(files) < 2 or not (self.profiles_root / mode / "nvdrsdb0.bin").exists():
            raise DrsError(
                f"{MODES[mode]} 데이터를 읽지 못했습니다 · 앱을 다시 설치해 주세요 "
                f"(assets/profiles/{mode})"
            )
        if not self.drs_dir.is_dir():
            raise DrsError(f"NVIDIA Drs 폴더를 찾지 못했습니다 · {self.drs_dir}")
        if not force and self.game_running():
            raise DrsError("서든어택이 실행 중입니다 · 게임을 완전히 종료한 뒤 다시 시도해 주세요")

        prev = self.current_mode()
        if prev == mode:
            merged = self._merge_stairfix()
            if merged.get("changed"):
                return {
                    "ok": True, "mode": mode, "merged": True, "stairfix": merged,
                    "changes": merged.get("changes", []),
                    "message": f"{MODES[mode]} 유지 · 계단현상 끄기를 함께 적용했습니다",
                    "applied_at": _now(),
                }
            return {"ok": True, "mode": mode, "unchanged": True, "merged": bool(merged.get("ok")),
                    "stairfix": merged, "changes": [],
                    "message": f"{MODES[mode]}이 이미 적용되어 있습니다"}

        before_blob = self.live_blob()
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

        merged = self._merge_stairfix()
        after_blob = self.live_blob()
        try:
            changes = drs.change_list(before_blob, after_blob)
        except Exception:  # noqa: BLE001 - 내역 표시는 최선 노력
            changes = []
        return {
            "ok": True,
            "mode": mode,
            "backup": backup["id"],
            "merged": bool(merged.get("ok")),
            "stairfix": merged,
            "changes": changes,
            "message": (f"{MODES[mode]} + 계단현상 끄기 적용 완료 · 이전 설정 자동 백업"
                        if merged.get("ok") else f"{MODES[mode]} 적용 완료 · 이전 설정 자동 백업"),
            "applied_at": _now(),
        }

    def _merge_stairfix(self) -> dict[str, Any]:
        """지금 Drs DB 의 서든 프로필에 계단현상 끄기(0x00401241)를 병합한다.

        프로필 팩 DB 의 AA compatibility 는 기본값(``0x00020041``)이라 팩만
        덮어쓰면 계단현상이 다시 켜진다.  그래서 복사 직후 같은 레코드 값을
        패치하고(㉠오프셋은 변하지 않음) 실제 적용값을 다시 읽어 검증한다.
        """
        blob = self.live_blob()
        if blob is None:
            return {"ok": False, "changed": False, "message": "Drs DB를 읽지 못했습니다"}
        try:
            before = drs.aa_compat(blob)
        except drs.DrsFormatError as exc:
            return {"ok": False, "changed": False, "message": str(exc)}
        if before is None:
            return {"ok": False, "changed": False,
                    "message": "서든 프로필에 Antialiasing compatibility 항목이 없습니다"}
        if before == drs.STAIRFIX_VALUE:
            return {"ok": True, "changed": False, "before": f"0x{before:08X}",
                    "after": f"0x{drs.STAIRFIX_VALUE:08X}", "changes": []}
        try:
            patched, report = drs.set_stairfix(blob)
            for name in ("nvdrsdb0.bin", "nvdrsdb1.bin"):
                target = self.drs_dir / name
                if not target.exists():
                    continue
                if name == "nvdrsdb1.bin":
                    # 미러 DB — 서든 프로필이 없는 팩도 있어서 실패는 무시한다
                    try:
                        mirror, _ = drs.set_stairfix(target.read_bytes())
                        target.write_bytes(mirror)
                    except (OSError, drs.DrsFormatError):
                        continue
                else:
                    target.write_bytes(patched)
        except PermissionError as exc:
            return {"ok": False, "changed": False, "message": f"관리자 권한이 필요합니다 · {exc}"}
        except (OSError, drs.DrsFormatError) as exc:
            return {"ok": False, "changed": False, "message": str(exc)}
        after = self.stairfix_value()
        if after != drs.STAIRFIX_VALUE:
            return {"ok": False, "changed": False, "message": "계단현상 끄기 값 검증에 실패했습니다"}
        return {
            "ok": True, "changed": bool(report.get("changed", True)),
            "before": f"0x{before:08X}", "after": f"0x{drs.STAIRFIX_VALUE:08X}",
            "label": drs.describe(drs.STAIRFIX_VALUE),
            "changes": [{"id": f"0x{drs.SETTING_AA_COMPAT:08X}", "name": "계단현상 끄기",
                         "en": "Antialiasing compatibility (DX9)",
                         "before": f"0x{before:08X}", "after": f"0x{drs.STAIRFIX_VALUE:08X}", "key": True}],
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
    def apply_stairfix(self, *, force: bool = False) -> dict[str, Any]:
        self.ensure_supported()
        if not self.drs_dir.is_dir():
            raise DrsError(f"NVIDIA Drs 폴더를 찾지 못했습니다 · {self.drs_dir}")
        blob = self.live_blob()
        if blob is None:
            raise DrsError(f"Drs DB를 읽지 못했습니다 · {self.live_db}")
        try:
            before = drs.aa_compat(blob)
        except drs.DrsFormatError as exc:
            raise DrsError(str(exc)) from exc
        if before == drs.STAIRFIX_VALUE:
            return {"ok": True, "mode": "stairfix", "unchanged": True,
                    "message": "계단현상 끄기가 이미 적용되어 있습니다",
                    "value": f"0x{drs.STAIRFIX_VALUE:08X}"}
        if not force and self.game_running():
            raise DrsError("서든어택이 실행 중입니다 · 게임을 완전히 종료한 뒤 다시 시도해 주세요")

        backup = self._make_backup("계단현상 끄기 적용 전", self.current_mode())
        try:
            patched, report = drs.set_stairfix(blob)
            targets = [p for p in (self.drs_dir / "nvdrsdb0.bin", self.drs_dir / "nvdrsdb1.bin") if p.exists()]
            for target in targets:
                if target.name == "nvdrsdb1.bin":
                    mirror, _ = drs.set_stairfix(target.read_bytes())
                    target.write_bytes(mirror)
                else:
                    target.write_bytes(patched)
        except PermissionError as exc:
            raise DrsError(f"관리자 권한이 필요합니다 · {exc}") from exc
        except (OSError, drs.DrsFormatError) as exc:
            raise DrsError(f"Drs DB를 쓰지 못했습니다 · {exc}") from exc

        after = self.stairfix_value()
        if after != drs.STAIRFIX_VALUE:
            raise DrsError("Drs DB 검증에 실패했습니다 · 적용 후 값을 다시 확인해 주세요")
        return {
            "ok": True,
            "mode": "stairfix",
            "backup": backup["id"],
            "changed": report.get("changed", True),
            "value": f"0x{drs.STAIRFIX_VALUE:08X}",
            "changes": [{"id": f"0x{drs.SETTING_AA_COMPAT:08X}", "name": "계단현상 끄기",
                         "en": "Antialiasing compatibility (DX9)",
                         "before": (f"0x{before:08X}" if before is not None else "없음"),
                         "after": f"0x{drs.STAIRFIX_VALUE:08X}", "key": True}],
            "message": "계단현상 끄기 적용 완료 · Antialiasing compatibility 0x00401241 (계단현상·타격효과·중세철창 제거)",
            "applied_at": _now(),
        }

    def apply_alias(self, enable: bool) -> dict[str, Any]:
        """'계단현상 끄기' 토글."""
        if enable:
            return self.apply_stairfix()
        return self.restore_latest()

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
