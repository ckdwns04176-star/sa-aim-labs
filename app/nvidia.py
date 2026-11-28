"""NVIDIA Drs profile apply / backup / restore (real Windows file operations).

Windows keeps per-application 3D settings in
``C:\\ProgramData\\NVIDIA Corporation\\Drs`` (``nvdrsdb0.bin``, ``nvdrsdb1.bin``,
``nvdrssel.bin``, ``nvAppTimestamps``, ``nvdrswr.lk``).  The shipped pack
replaces the Sudden Attack profile with a tuned one (v2.0.5 — 라플·스나 팩의
``suddenattack.exe`` 값이 같아 UI는 '서든어택 그래픽 최적화' 버튼 하나다).

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

from . import drs, nvapi_drs, paths

DRS_DIR = Path(r"C:\ProgramData\NVIDIA Corporation\Drs")
DRS_FILES = ("nvdrsdb0.bin", "nvdrsdb1.bin", "nvdrssel.bin", "nvAppTimestamps", "nvdrswr.lk")
CORE_FILES = ("nvdrsdb0.bin", "nvdrsdb1.bin")
GAME_EXE = drs.GAME_EXE
KEEP_BACKUPS = 3
# v2.0.5 — 버튼은 하나('서든어택 그래픽 최적화'). 두 팩의 서든 프로필 값이 같아서
# 이름만 나뉘어 있었고, 예전 화면/호출과의 호환을 위해 rifle / sniper 키는 그대로 둔다.
MODES = {"rifle": "서든어택 그래픽 최적화", "sniper": "서든어택 그래픽 최적화",
         "stairfix": "그래픽 호환 설정"}
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


def _file_rows(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Drs 파일별 before → after (크기·해시) — 실제로 폴더가 바뀐 증거."""
    rows: list[dict[str, Any]] = []
    for name in DRS_FILES:
        old = before.get(name) or {}
        new = after.get(name) or {}
        if not old.get("exists") and not new.get("exists"):
            continue
        rows.append({"name": name,
                     "before_size": int(old.get("size") or 0), "after_size": int(new.get("size") or 0),
                     "before_sha": str(old.get("sha") or "")[:16], "after_sha": str(new.get("sha") or "")[:16],
                     "changed": (old.get("sha") or "") != (new.get("sha") or "")})
    return rows


def _readiness(blob: bytes | None, changes: list[dict[str, Any]], base_changes: list[dict[str, Any]],
               mode: str | None) -> str:
    """'바뀐 항목이 없다'는 안내 대신 왜 그런지 분명히 설명한다."""
    if changes or base_changes:
        return ""
    label = MODES.get(mode or "", "프로필")
    same = "이미 같은 프로필이 적용되어 있어" if mode else "설정이 그대로여서"
    return (f"{same} 세부 값이 바뀌지 않았습니다 · {label}의 서든어택 프로필 값이 직전 Drs DB와 같습니다. "
            f"아래 '이 프로필이 설정하는 항목'에서 현재 값을 확인할 수 있습니다.")


def _same_except_stairfix(a: bytes, b: bytes) -> bool:
    """두 Drs DB 가 그래픽 호환 설정(AA compatibility) 값만 다른지.

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
        # v1.0.0 — 파일 해시 대신 '마지막으로 적용한 팩'을 선호값으로 쓴다
        # (다른 게임 프로필을 보존하면 파일은 팩과 같을 수 없기 때문)
        self.preferred_mode: str | None = None

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
        """True when 그래픽 호환 설정 can be applied with the built-in patch logic."""
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
        # 그래픽 호환 설정 is a built-in patch of the live Drs DB (no pack needed) —
        # a shipped assets/profiles/stairfix pack still wins when present.
        modes["stairfix"] = bool(self.profile_files("stairfix")) or self.stairfix_ready()
        return modes

    def _pack_game_values(self, mode: str) -> dict[int, int]:
        src = self.profiles_root / mode / "nvdrsdb0.bin"
        if not src.exists():
            return {}
        try:
            return drs.profile_dwords(src.read_bytes(), GAME_EXE)
        except (OSError, drs.DrsFormatError):
            return {}

    def _pack_values(self, mode: str) -> dict[int, int]:
        """번들 팩에서 **서든에만 적용할** 설정을 꺼낸다.

        팩의 서든 프로필 값 + 팩의 Base Profile 에 들어 있는 3D 성능 항목을
        합친다(전역에 쓰지 않고 게임 프로필 오버라이드로만 넣기 위해).
        """
        src = self.profiles_root / mode / "nvdrsdb0.bin"
        if not src.exists():
            return {}
        try:
            blob = src.read_bytes()
            values = dict(drs.profile_dwords(blob, GAME_EXE))
            base_off = drs.named_profile_offset(blob, "Base Profile")
            if base_off is not None:
                import struct as _struct

                for rec in drs.iter_settings(blob, base_off):
                    if rec["value_size"] >= 4 and rec["setting_id"] not in values:
                        values[rec["setting_id"]] = _struct.unpack_from("<I", blob, rec["value_offset"])[0]
            return values
        except (OSError, drs.DrsFormatError):
            return {}

    def current_mode(self) -> str | None:
        """지금 서든 프로필이 어떤 팩과 같은가 — **서든 프로필 값만** 비교한다.

        파일 전체 해시 비교는 다른 게임 프로필까지 같아야 해서, 이제는 쓰지
        않는다(다른 게임 설정을 보존하므로 파일은 당연히 다르다).
        """
        blob = self.live_blob()
        if blob is None:
            return None
        try:
            live_values = drs.profile_dwords(blob)
        except drs.DrsFormatError:
            live_values = {}
        if not live_values:
            if self.stairfix_applied():
                return "stairfix"
            return None
        live_path = self.drs_dir / "nvdrsdb0.bin"
        stair_ids = {sid for sid, _ in drs.STAIRFIX_SET}
        candidates: list[tuple[int, str]] = []
        for mode in PACK_MODES:
            src = self.profiles_root / mode / "nvdrsdb0.bin"
            if src.exists() and live_path.exists():
                try:
                    if _sha256(src) == _sha256(live_path):
                        return mode          # 팩을 그대로 쓰는 첫 상태
                except OSError:
                    pass
            pack_game = {sid: value for sid, value in self._pack_game_values(mode).items()
                         if sid not in stair_ids}
            if not pack_game:
                continue
            # **서든 프로필 값만** 비교한다 — 전역/다른 게임은 보존하므로 파일은 다를 수 있다.
            # 그래픽 호환 세트는 팩 기본값과 달라도 정상이므로 비교에서 뺀다.
            matches = sum(1 for sid, value in pack_game.items() if live_values.get(sid) == value)
            if matches == len(pack_game):
                candidates.append((matches, mode))
        if candidates:
            best_matches = max(m for m, _ in candidates)
            tied = [mode for m, mode in candidates if m == best_matches]
            if self.preferred_mode in tied:
                return self.preferred_mode
            return tied[0]
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
        blob = self.live_blob()          # 2MB 파일 — 한 번만 읽는다
        stair_value = None if blob is None else drs.aa_compat(blob)
        mode = self.current_mode()
        return {
            "supported": self.supported,
            "drs_dir": str(self.drs_dir),
            "scope": "suddenattack.exe 프로필만 적용 · 다른 게임·전역 설정 유지",
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
            "profile_items": drs.profile_items(blob),
            "checklist": drs.checklist_state(blob),
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
    def _nvapi_apply_values(self, values: dict[int, int]) -> dict[str, Any]:
        """임의 설정값을 NVAPI 로 서든 프로필에만 저장 (서브프로세스)."""
        if os.name != "nt" or not nvapi_drs.available() or not values:
            return {"ok": False, "supported": False, "message": "NVAPI 없음"}
        import tempfile
        import time

        result_path = Path(tempfile.gettempdir()) / f"sa_nvapi_set_{os.getpid()}_{int(time.time())}.json"
        settings_path = Path(tempfile.gettempdir()) / f"sa_nvapi_in_{os.getpid()}_{int(time.time())}.json"
        try:
            settings_path.write_text(json.dumps({"settings": [[sid, val] for sid, val in values.items()]}),
                                     encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "supported": True, "message": f"NVAPI 입력 준비 실패 · {exc}"}
        cmd = [str(sys.executable)]
        if not getattr(sys, "frozen", False):
            cmd.append(str(Path(__file__).resolve().parent.parent / "main.py"))
        cmd += ["--nvapi-settings", str(settings_path), "--result", str(result_path)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=30,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "supported": True, "message": f"NVAPI 호출 실패 · {exc}"}
        data: dict[str, Any] = {}
        try:
            if result_path.exists():
                data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        finally:
            for path in (result_path, settings_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        if not data:
            data = {"ok": False, "supported": True,
                    "message": f"NVAPI 응답 없음 · rc={proc.returncode}"}
        return data

    def apply(self, mode: str, *, force: bool = False) -> dict[str, Any]:
        """라플/스나 팩의 **서든어택 프로필 값만** 내 프로필에 적용한다.

        v1.0.0 — 예전에는 nvdrsdb0.bin 파일을 통째로 덮어써서 전역(Base Profile)과
        다른 게임의 커스텀 프로필까지 함께 바뀔 수 있었다.  이제는

          1) 팩에서 ``suddenattack.exe`` 프로필의 DWORD 값만 꺼내고,
          2) 내 Drs DB 의 **같은 프로필 레코드만** 값만 바꿔 쓰고,
          3) 레코드가 없는 항목은 NVAPI DRS 로 서든 프로필에만 새로 만든다.

        다른 게임 프로필·전역 설정은 한 바이트도 건드리지 않는다.
        """
        self.ensure_supported()
        if mode not in MODES:
            raise DrsError(f"알 수 없는 프로필: {mode}")
        if mode == "stairfix" and not self.profile_files("stairfix"):
            return self.apply_stairfix(force=force)
        pack_values = self._pack_values(mode)
        if not pack_values:
            raise DrsError(
                f"{MODES[mode]} 데이터를 읽지 못했습니다 · 앱을 다시 설치해 주세요 "
                f"(assets/profiles/{mode})"
            )
        if not self.drs_dir.is_dir():
            raise DrsError(f"NVIDIA Drs 폴더를 찾지 못했습니다 · {self.drs_dir}")
        live = self.live_blob()
        if live is None:
            raise DrsError(f"Drs DB를 읽지 못했습니다 · {self.live_db}")
        try:
            before_values = drs.profile_dwords(live)
        except drs.DrsFormatError as exc:
            raise DrsError(str(exc)) from exc
        if not before_values:
            raise DrsError("Drs DB에 서든어택(suddenattack.exe) 프로필이 없습니다 · NVIDIA 제어판에서 게임을 한 번 추가해 주세요")
        if not force and self.game_running():
            raise DrsError("서든어택이 실행 중입니다 · 게임을 완전히 종료한 뒤 다시 시도해 주세요")

        prev_mode = self.current_mode()
        self.preferred_mode = mode
        stair_ids = {sid for sid, _ in drs.STAIRFIX_SET}
        pack_game_ids = set(drs.profile_dwords(
            (self.profiles_root / mode / "nvdrsdb0.bin").read_bytes())) if (self.profiles_root / mode / "nvdrsdb0.bin").exists() else set()
        # 서든 프로필의 핵심 값이 모두 같고, 이미 있는 항목이 전부 목표값이면 '적용됨'
        value_match = (all(before_values.get(sid) == value for sid, value in pack_values.items()
                           if sid not in stair_ids and sid in before_values)
                       and all(sid in before_values for sid in pack_game_ids))
        # v2.0.5 — 라플·스나 팩은 서든 프로필 값이 같아 같은 설정이다.
        # 예전에 '스나'로 적용해 둔 PC에서 새 버튼(rifle)을 눌러도 이미 적용된 상태로 본다.
        matched = (prev_mode == mode) or (prev_mode in PACK_MODES and mode in PACK_MODES) \
            or (prev_mode is None and value_match)
        if matched:
            merged = self._merge_stairfix()
            after_blob = self.live_blob()
            if merged.get("changed"):
                return {
                    "ok": True, "mode": mode, "merged": True, "stairfix": merged,
                    "changes": merged.get("changes", []), "base_changes": [], "files": [],
                    "profile_items": drs.profile_items(after_blob),
                    "checklist": drs.checklist_state(after_blob),
                    "message": f"{MODES[mode]} 유지 · 그래픽 호환 설정을 함께 적용했습니다",
                    "applied_at": _now(),
                }
            return {"ok": True, "mode": mode, "unchanged": True, "merged": bool(merged.get("ok")),
                    "stairfix": merged, "changes": [], "base_changes": [], "files": [],
                    "profile_items": drs.profile_items(after_blob),
                    "checklist": drs.checklist_state(after_blob),
                    "unchanged_reason": _readiness(after_blob, [], [], mode),
                    "message": f"{MODES[mode]}가 이미 적용되어 있습니다"}

        backup = self._make_backup(f"{MODES[mode]} 적용 전", self.current_mode())
        patched, report = drs.apply_profile_values(live, pack_values)
        try:
            for name in ("nvdrsdb0.bin", "nvdrsdb1.bin"):
                target = self.drs_dir / name
                if not target.exists():
                    continue
                if name == "nvdrsdb0.bin":
                    target.write_bytes(patched)
                else:
                    # 미러 DB 도 자기 서든 프로필만 값만 바꾼다 (전체 복사 금지)
                    try:
                        mirror, _ = drs.apply_profile_values(target.read_bytes(), pack_values)
                        target.write_bytes(mirror)
                    except (OSError, drs.DrsFormatError):
                        continue
        except PermissionError as exc:
            raise DrsError(f"관리자 권한이 필요합니다 · {exc}") from exc
        except OSError as exc:
            raise DrsError(f"Drs 파일을 쓰지 못했습니다 · {exc}") from exc

        # 레코드가 없는 항목은 NVAPI 로 서든 프로필에만 새로 만든다
        nvapi = self._nvapi_apply_values(pack_values) if report.get("missing") else {"ok": False, "skipped": True}
        merged = self._merge_stairfix()
        after_blob = self.live_blob()
        try:
            after_values = drs.profile_dwords(after_blob)
        except drs.DrsFormatError:
            after_values = {}
        changes: list[dict[str, Any]] = []
        for setting_id, value in pack_values.items():
            if setting_id in stair_ids:
                continue
            was, now = before_values.get(setting_id), after_values.get(setting_id)
            if was == now:
                continue
            label, en = drs.setting_name(setting_id)
            changes.append({"id": f"0x{setting_id:08X}", "name": label, "en": en,
                            "before": (f"0x{was:08X}" if was is not None else "없음"),
                            "after": (f"0x{now:08X}" if now is not None else "없음"),
                            "label_before": drs.value_label(setting_id, was),
                            "label_after": drs.value_label(setting_id, now),
                            "created": was is None})
        remaining = [sid for sid, value in pack_values.items()
                     if sid not in stair_ids and after_values.get(sid) != value]
        if remaining and int(nvapi.get("rc") or 0) == nvapi_drs.NVAPI_ACCESS_DENIED:
            raise DrsError("관리자 권한이 필요합니다 · NVIDIA 프로필에 새 항목을 저장하지 못했습니다 (NVAPI ACCESS_DENIED)")
        message = f"{MODES[mode]} 적용 완료 · 서든어택 프로필만 변경 · {len(changes)}개 값"
        if nvapi.get("ok"):
            message += " · NVAPI 로 누락 레코드 생성"
        if remaining:
            message += f" · ⚠ {len(remaining)}개 항목은 적용하지 못했습니다"
        if merged.get("ok"):
            message += " · 그래픽 호환 설정 포함"
            changes.extend(merged.get("changes") or [])
        return {
            "ok": True,
            "mode": mode,
            "backup": backup["id"],
            "merged": bool(merged.get("ok")),
            "stairfix": merged,
            "changes": changes,
            "base_changes": [],
            "files": [],
            "profile_items": drs.profile_items(after_blob),
            "checklist": drs.checklist_state(after_blob),
            "nvapi": nvapi,
            "applied_count": len(changes),
            "remaining": len(remaining),
            "other_profiles_preserved": True,
            "unchanged_reason": _readiness(after_blob, changes, [], mode),
            "game_running": False,
            "message": message + " · 이전 설정 자동 백업",
            "applied_at": _now(),
        }

    def _merge_stairfix(self) -> dict[str, Any]:
        """지금 Drs DB 의 서든 프로필에 그래픽 호환 세트를 병합한다.

        프로필 팩 DB 의 AA compatibility 는 기본값(``0x00020041``)이라 팩만
        덮어쓰면 예전 값이 다시 덮인다.  또 예전에는 호환성 값만 바꿔서
        AA Mode/Setting 이 꺼져 있으면 드라이버가 무시했다.  이제는
        호환성 + AA 모드(Override) + AA 설정(2x)을 함께 패치하고 검증한다.
        """
        blob = self.live_blob()
        if blob is None:
            return {"ok": False, "changed": False, "message": "Drs DB를 읽지 못했습니다"}
        try:
            before_values = drs.stairfix_values(blob)
        except drs.DrsFormatError as exc:
            return {"ok": False, "changed": False, "message": str(exc)}
        before = before_values.get(drs.SETTING_AA_COMPAT)
        if before is None:
            return {"ok": False, "changed": False,
                    "message": "서든 프로필에 Antialiasing compatibility 항목이 없습니다"}
        if drs.stairfix_ready(blob):
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
        current = self.live_blob()
        after_values = drs.stairfix_values(current) if current is not None else {}
        if after_values.get(drs.SETTING_AA_COMPAT) != drs.STAIRFIX_VALUE:
            return {"ok": False, "changed": False, "message": "그래픽 호환 값 검증에 실패했습니다"}
        changes = [
            self._stairfix_row(drs.SETTING_AA_COMPAT, before_values, after_values,
                               "그래픽 호환 설정", "Antialiasing compatibility (DX9)"),
            self._stairfix_row(drs.SETTING_AA_MODE, before_values, after_values,
                               "안티앨리어싱 모드", "Antialiasing - Mode"),
            self._stairfix_row(drs.SETTING_AA_SETTING, before_values, after_values,
                               "안티앨리어싱 설정", "Antialiasing - Setting"),
        ]
        return {
            "ok": True, "changed": bool(report.get("changed", True)),
            "before": f"0x{before:08X}", "after": f"0x{drs.STAIRFIX_VALUE:08X}",
            "label": drs.describe(drs.STAIRFIX_VALUE), "changes": changes,
            "values": {f"0x{k:08X}": v for k, v in after_values.items()},
            "aa_mode": after_values.get(drs.SETTING_AA_MODE),
            "aa_setting": after_values.get(drs.SETTING_AA_SETTING),
        }

    def restore(self, backup_id: str, *, force: bool = False) -> dict[str, Any]:
        self.ensure_supported()
        folder = self.backup_root / str(backup_id)
        if not folder.is_dir():
            raise DrsError(f"복구 지점을 찾지 못했습니다 · {backup_id}")
        if not force and self.game_running():
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
    def _nvapi_stairfix(self) -> dict[str, Any]:
        """NVAPI DRS 를 **별도 프로세스**로 먼저 시도한다 (앱 본체 보호).

        nvapi64.dll 은 구조체 버전이 맞지 않으면 return code 로 알려 주지만,
        만약의 접근 위반이 앱을 죽이지 않도록 서브프로세스에서만 호출한다.
        """
        if os.name != "nt" or not nvapi_drs.available():
            return {"ok": False, "supported": False,
                    "message": "NVAPI 없음 · Drs 파일 패치로 진행"}
        import tempfile
        import time

        result_path = Path(tempfile.gettempdir()) / f"sa_nvapi_{os.getpid()}_{int(time.time())}.json"
        cmd = [str(sys.executable)]
        if not getattr(sys, "frozen", False):
            cmd.append(str(Path(__file__).resolve().parent.parent / "main.py"))
        cmd += ["--nvapi-stairfix", "--result", str(result_path)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=25,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "supported": True, "message": f"NVAPI 호출 실패 · {exc}"}
        data: dict[str, Any] = {}
        try:
            if result_path.exists():
                data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        finally:
            try:
                result_path.unlink(missing_ok=True)
            except OSError:
                pass
        if not data:
            raw = (proc.stdout or b"").decode("utf-8", "replace").strip()
            data = {"ok": False, "supported": True,
                    "message": f"NVAPI 응답 없음 · rc={proc.returncode} {raw[:120]}"}
        data["returncode"] = proc.returncode
        return data

    def _stairfix_row(self, setting_id: int, before: dict[int, int | None],
                      after: dict[int, int | None], label: str, en: str) -> dict[str, Any]:
        was, now = before.get(setting_id), after.get(setting_id)
        return {"id": f"0x{setting_id:08X}", "name": label, "en": en,
                "before": (f"0x{was:08X}" if was is not None else "없음"),
                "after": (f"0x{now:08X}" if now is not None else "없음"),
                "label_before": drs.value_label(setting_id, was),
                "label_after": drs.value_label(setting_id, now),
                "key": setting_id == drs.SETTING_AA_COMPAT,
                "created": was is None}

    def apply_stairfix(self, *, force: bool = False) -> dict[str, Any]:
        self.ensure_supported()
        if not self.drs_dir.is_dir():
            raise DrsError(f"NVIDIA Drs 폴더를 찾지 못했습니다 · {self.drs_dir}")
        blob = self.live_blob()
        if blob is None:
            raise DrsError(f"Drs DB를 읽지 못했습니다 · {self.live_db}")
        try:
            before_values = drs.stairfix_values(blob)
        except drs.DrsFormatError as exc:
            raise DrsError(str(exc)) from exc
        if drs.stairfix_ready(blob):
            return {"ok": True, "mode": "stairfix", "unchanged": True,
                    "changes": [], "base_changes": [], "files": [],
                    "profile_items": drs.profile_items(blob),
                    "checklist": drs.checklist_state(blob),
                    "unchanged_reason": "그래픽 호환 세트(호환성 0x00401241 · AA 모드 · AA 설정)가 이미 적용되어 있습니다",
                    "message": "그래픽 호환 설정이 이미 적용되어 있습니다",
                    "value": f"0x{drs.STAIRFIX_VALUE:08X}"}
        if not force and self.game_running():
            raise DrsError("서든어택이 실행 중입니다 · 게임을 완전히 종료한 뒤 다시 시도해 주세요")

        backup = self._make_backup("그래픽 호환 설정 적용 전", self.current_mode())

        # ① NVAPI DRS — 레코드가 없어도 새로 만들어 저장한다 (우선 경로)
        nvapi = self._nvapi_stairfix()
        blob = self.live_blob()
        binary_error: str | None = None

        # ② Drs 파일 패치 — 이미 있는 레코드를 확실히 0x00401241 / AA 모드·설정으로
        if blob is None or not drs.stairfix_ready(blob):
            try:
                source = blob if blob is not None else self.live_blob()
                if source is None:
                    raise drs.DrsFormatError("Drs DB를 다시 읽지 못했습니다")
                patched, _report = drs.set_stairfix(source)
                targets = [p for p in (self.drs_dir / "nvdrsdb0.bin", self.drs_dir / "nvdrsdb1.bin") if p.exists()]
                for target in targets:
                    if target.name == "nvdrsdb1.bin":
                        try:
                            mirror, _ = drs.set_stairfix(target.read_bytes())
                            target.write_bytes(mirror)
                        except (OSError, drs.DrsFormatError):
                            continue
                    else:
                        target.write_bytes(patched)
            except PermissionError as exc:
                binary_error = f"관리자 권한이 필요합니다 · {exc}"
            except (OSError, drs.DrsFormatError) as exc:
                binary_error = str(exc)

        current = self.live_blob()
        after_values = drs.stairfix_values(current) if current is not None else {}
        if after_values.get(drs.SETTING_AA_COMPAT) != drs.STAIRFIX_VALUE:
            reason = binary_error or nvapi.get("message") or "적용 후 값을 확인하지 못했습니다"
            raise DrsError(f"그래픽 호환 설정 검증에 실패했습니다 · {reason}")

        changes = [
            self._stairfix_row(drs.SETTING_AA_COMPAT, before_values, after_values,
                               "그래픽 호환 설정", "Antialiasing compatibility (DX9)"),
            self._stairfix_row(drs.SETTING_AA_MODE, before_values, after_values,
                               "안티앨리어싱 모드", "Antialiasing - Mode"),
            self._stairfix_row(drs.SETTING_AA_SETTING, before_values, after_values,
                               "안티앨리어싱 설정", "Antialiasing - Setting"),
        ]
        mode_ok = after_values.get(drs.SETTING_AA_MODE) == 1
        setting_ok = after_values.get(drs.SETTING_AA_SETTING) == 1
        notes = []
        if nvapi.get("ok"):
            notes.append("NVAPI 로 프로필에 직접 저장")
        elif nvapi.get("supported") is False:
            notes.append("NVAPI 없음 · Drs 파일 패치")
        else:
            notes.append(f"NVAPI 건너뜀({nvapi.get('message', '실패')}) · Drs 파일 패치")
        if not mode_ok or not setting_ok:
            notes.append("⚠ AA 모드/설정 레코드가 없어 일부만 적용됨 · NVIDIA 제어판에서 안티앨리어싱을 한 번 저장하면 레코드가 생깁니다")
        return {
            "ok": True,
            "mode": "stairfix",
            "backup": backup["id"],
            "changed": True,
            "value": f"0x{drs.STAIRFIX_VALUE:08X}",
            "values": {f"0x{k:08X}": v for k, v in after_values.items()},
            "changes": changes,
            "base_changes": [],
            "files": [],
            "nvapi": nvapi,
            "profile_items": drs.profile_items(current),
            "checklist": drs.checklist_state(current),
            "message": "그래픽 호환 설정 적용 완료 · " + " · ".join(notes),
            "applied_at": _now(),
        }

    def apply_alias(self, enable: bool) -> dict[str, Any]:
        """'그래픽 호환 설정' 토글."""
        if enable:
            return self.apply_stairfix()
        return self.restore_latest()

    def restore_latest(self, *, force: bool = True) -> dict[str, Any]:
        backups = self.list_backups()
        if not backups:
            raise DrsError("복구 지점이 없습니다 · 먼저 프로필을 적용해 주세요")
        return self.restore(backups[0]["id"], force=force)

    # ------------------------------------------------------------ elevation
    def elevate_help(self, mode: str, *, restore: bool = False) -> dict[str, Any]:
        """Re-launch this executable elevated to finish the copy (UAC prompt).

        ``restore=True`` 이면 백업 복구를 관리자 권한으로 실행한다.
        """
        if os.name != "nt":
            raise DrsError("관리자 권한 상승은 윈도우에서만 지원합니다")
        import ctypes

        result_file = paths.data_dir() / RESULT_FILE
        flag = "--drs-restore" if restore else f'--drs-apply "{mode}"'
        if getattr(sys, "frozen", False):
            exe, args = sys.executable, f'{flag} --result "{result_file}"'
        else:
            exe = sys.executable
            script = Path(__file__).resolve().parent.parent / "main.py"
            args = f'"{script}" {flag} --result "{result_file}"'
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


def run_elevated_restore(result_path: str | os.PathLike[str]) -> int:
    """Entry point of the elevated helper process (``--drs-restore``)."""
    try:
        mgr = DrsManager()
        result = mgr.restore_latest()
        result["elevated"] = True
        result["mode"] = None
        write_result(result_path, result)
        return 0
    except Exception as exc:  # noqa: BLE001
        write_result(result_path, {"ok": False, "elevated": True, "message": str(exc)})
        return 1


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
