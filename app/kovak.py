"""KovaaK's 로컬 폴더 연동 — stats CSV 점수 인식 + 플레이리스트(.plo/.json) 생성.

코박스는 과제(시나리오)를 한 판 끝낼 때마다 `<설치>/FPSAimTrainer/stats/` 에
CSV 를 하나씩 남긴다.  파일 이름은

    Wide Wall 3 Targets - Challenge - 2026.11.23-19.04.12 Stats.csv

모양이고, 파일 안에는 과제 요약 블록이 들어 있다::

    Scenario:,Wide Wall 3 Targets
    ...
    Score:,132.5
    Hit Count:,86
    Miss Count:,12

그래서 **파일 이름에서 시나리오 이름·시각**을, **본문에서 `Score:` 값**을 읽으면
오늘 플레이한 과제의 최신 점수를 그대로 가져올 수 있다.  (코박스 3.x 기준 —
Score/Hit Count/Miss Count 처럼 콜론이 붙은 요약 키가 계약이다.)

플레이리스트는 코박스가 지금 쓰는 JSON 스키마(`scenario_name` / `play_Count`
케이싱이 중요)로 `Saved/SaveGames/Playlists` 에 넣는다.  예전 버전·외부 도구가
`.plo` 확장자를 보는 경우가 있어 같은 내용을 `.plo` 로도 한 벌 더 남긴다.

표준 라이브러리만 쓴다(csv·json·re·pathlib).  폴더가 없거나 권한이 없으면
예외 대신 `{"ok": False, "message": ...}` 로 돌려준다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import paths

# 사용자가 설정에서 직접 지정할 수 있는 경로 (테스트도 이 값을 쓴다)
ENV_STATS = "SA_AL_KOVAK_STATS_DIR"
ENV_PLAYLISTS = "SA_AL_KOVAK_PLAYLISTS_DIR"

_STAMP_RE = re.compile(
    r"(?P<stamp>\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2})"
    r"(?:\s*Stats?)?\.csv$",
    re.IGNORECASE,
)
# `이름 - 챌린지 - 시각 Stats.csv`.  시나리오 이름에 " - " 가 들어갈 수 있어
# 시각 패턴을 기준으로 잘라 낸다.
_NAME_RE = re.compile(
    r"^(?P<name>.+?)\s+-\s+(?P<challenge>[^-]+?)\s+-\s+"
    r"\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}(?:\s*Stats?)?\.csv$",
    re.IGNORECASE,
)


def _norm(name: str) -> str:
    """시나리오 이름 비교용 — 대소문자·연속 공백·양끝 공백을 무시한다."""
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


# ------------------------------------------------------------------ install probes
def _steam_libraries() -> list[Path]:
    """Steam 라이브러리 폴더들 (레지스트리 → libraryfolders.vdf).  실패는 조용히."""
    roots: list[Path] = []
    steam_roots: list[Path] = []
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                value, _ = winreg.QueryValueEx(key, "SteamPath")
                if value:
                    steam_roots.append(Path(str(value)))
        except Exception:  # noqa: BLE001 - Steam 미설치/권한 없음
            pass
    for base in (
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramW6432"),
    ):
        if base:
            steam_roots.append(Path(base) / "Steam")
    for drive in ("C:", "D:", "E:", "F:"):
        steam_roots.append(Path(f"{drive}/SteamLibrary"))
        steam_roots.append(Path(f"{drive}/Steam"))
    seen: set[str] = set()
    for steam in steam_roots:
        key = str(steam).lower()
        if key in seen:
            continue
        seen.add(key)
        if not steam.is_dir():
            continue
        roots.append(steam)
        # libraryfolders.vdf: "path" "D:\\SteamLibrary"
        try:
            vdf = steam / "steamapps" / "libraryfolders.vdf"
            if vdf.is_file():
                text = vdf.read_text(encoding="utf-8", errors="ignore")
                for found in re.findall(r'"path"\s+"([^"]+)"', text):
                    extra = Path(found.replace("\\\\", "\\"))
                    if extra.is_dir() and str(extra).lower() not in seen:
                        seen.add(str(extra).lower())
                        roots.append(extra)
        except OSError:
            continue
    return roots


def kovaak_roots() -> list[Path]:
    """코박스 게임 폴더(FPSAimTrainer.exe 가 있는 곳) 후보들."""
    out: list[Path] = []
    for steam in _steam_libraries():
        for sub in (("steamapps", "common", "FPSAimTrainer", "FPSAimTrainer"),
                    ("steamapps", "common", "FPSAimTrainer")):
            candidate = steam.joinpath(*sub)
            if (candidate / "stats").is_dir() or (candidate / "FPSAimTrainer.exe").exists():
                out.append(candidate)
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            candidate = Path(base) / "Steam" / "steamapps" / "common" / "FPSAimTrainer" / "FPSAimTrainer"
            if candidate.is_dir():
                out.append(candidate)
    return _dedup(out)


def _dedup(items: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for item in items:
        key = str(item).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def root_from_path(value: str | os.PathLike[str] | None) -> Path | None:
    """사용자가 지정한 경로(실행파일·게임폴더·stats 폴더 어느 것이든) → 게임 루트."""
    if not value:
        return None
    raw = Path(str(value)).expanduser()
    try:
        if raw.is_file():
            raw = raw.parent
    except OSError:
        return None
    for candidate in (raw, raw.parent, raw.parent.parent):
        try:
            if (candidate / "stats").is_dir():
                return candidate
            nested = candidate / "FPSAimTrainer"
            if (nested / "stats").is_dir():
                return nested
        except OSError:
            continue
    return raw if raw.is_dir() else None


def stats_dirs(custom: str | os.PathLike[str] | None = None) -> list[Path]:
    """점수를 읽을 stats 폴더 목록 (지정 폴더 → 환경변수 → 표준 설치 경로)."""
    out: list[Path] = []
    for value in (custom, os.environ.get(ENV_STATS)):
        root = root_from_path(value)
        if root is None:
            continue
        out.append(root if root.name.lower() == "stats" else root / "stats")
    for root in kovaak_roots():
        out.append(root / "stats")
    return [p for p in _dedup(out) if p.is_dir()]


def playlists_dirs(custom: str | os.PathLike[str] | None = None) -> list[Path]:
    """코박스가 읽는 플레이리스트 폴더 (`Saved/SaveGames/Playlists`)."""
    out: list[Path] = []
    for value in (custom, os.environ.get(ENV_PLAYLISTS)):
        if value:
            p = Path(str(value)).expanduser()
            if p.is_dir():
                out.append(p)
            elif p.name.lower() == "playlists":
                out.append(p)  # 아직 없어도 만들어 쓴다
    for root in kovaak_roots():
        out.append(root / "Saved" / "SaveGames" / "Playlists")
    return _dedup(out)


def find_playlists_dir(custom: str | os.PathLike[str] | None = None) -> Path | None:
    for candidate in playlists_dirs(custom):
        if candidate.is_dir():
            return candidate
    return None


# ------------------------------------------------------------------ CSV parsing
def _stamp_from_name(name: str) -> _dt.datetime | None:
    match = _STAMP_RE.search(name)
    if not match:
        return None
    try:
        return _dt.datetime.strptime(match.group("stamp"), "%Y.%m.%d-%H.%M.%S")
    except ValueError:
        return None


def name_from_file(name: str) -> str:
    """파일 이름 → 시나리오 이름 (` - Challenge - 시각 Stats.csv` 제거)."""
    match = _NAME_RE.match(name)
    if match:
        return match.group("name").strip()
    stem = re.sub(r"(?:\s*Stats?)?\.csv$", "", name, flags=re.IGNORECASE)
    parts = stem.rsplit(" - ", 2)
    return parts[0].strip() if parts else stem.strip()


def _score_from_line(line: str) -> float | None:
    """`Score:,152.5` · `Score: 152.5` · `Score,152.5` 를 모두 받는다."""
    text = line.strip()
    low = text.lower()
    if not (low.startswith("score:") or low.startswith("score,")):
        return None
    body = text.split(",", 1)[1] if "," in text else text.split(":", 1)[-1]
    body = body.strip().strip('"').strip()
    try:
        return float(body)
    except ValueError:
        return None


def parse_stats_file(path: Path) -> dict[str, Any] | None:
    """stats CSV 1개 → {name, score, at, file}.  읽을 수 없으면 None."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    score: float | None = None
    scenario: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        value = _score_from_line(stripped)
        if value is not None:
            score = value          # 요약 블록이 아래쪽 → 마지막 값을 쓴다
            continue
        if stripped.lower().startswith("scenario:") and scenario is None:
            body = stripped.split(",", 1)[1] if "," in stripped else stripped.split(":", 1)[-1]
            scenario = body.strip().strip('"') or None
    if score is None:
        return None
    stamp = _stamp_from_name(path.name)
    if stamp is None:
        try:
            stamp = _dt.datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            stamp = None
    return {
        "name": scenario or name_from_file(path.name),
        "file_name": path.name,
        "score": round(float(score), 2),
        "at": stamp.isoformat(timespec="seconds") if stamp else None,
        "file": str(path),
    }


def latest_runs(dirs: Sequence[Path], days: int = 1, today: _dt.date | None = None,
                limit_files: int = 6000) -> dict[str, dict[str, Any]]:
    """최근 `days` 일 안에 플레이한 시나리오별 **최신 실행** 한 건.

    오래된 파일은 이름의 시각만 보고 건너뛰어(본문을 읽지 않아) 폴더에 수천 개가
    쌓여 있어도 빠르다.  시각을 못 읽는 파일은 mtime 으로 판단한다.
    """
    today = today or _dt.date.today()
    since = _dt.datetime.combine(today - _dt.timedelta(days=max(1, int(days)) - 1), _dt.time.min)
    best: dict[str, dict[str, Any]] = {}
    scanned = 0
    for directory in dirs:
        try:
            files = sorted(Path(directory).glob("*.csv"))
        except OSError:
            continue
        for file in files[-limit_files:]:
            stamp = _stamp_from_name(file.name)
            if stamp is None:
                try:
                    stamp = _dt.datetime.fromtimestamp(file.stat().st_mtime)
                except OSError:
                    continue
            if stamp < since:
                continue
            scanned += 1
            info = parse_stats_file(file)
            if not info:
                continue
            key = _norm(info["name"])
            at = str(info.get("at") or "")
            if key in best and str(best[key].get("at") or "") >= at:
                continue
            best[key] = info
    for row in best.values():
        row["scanned"] = scanned
    return best


def match_targets(targets: Sequence[str], runs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """오늘 추천/추가 연습 시나리오 이름 ↔ stats 실행 매칭 결과."""
    out: list[dict[str, Any]] = []
    for name in targets:
        hit = runs.get(_norm(name))
        if not hit:
            continue
        out.append({
            "name": name,
            "score": hit["score"],
            "at": hit.get("at"),
            "file_name": hit.get("file_name"),
            "file": hit.get("file"),
        })
    return out


# ------------------------------------------------------------------ playlists
PLAYLIST_VERSION = 31


def playlist_doc(name: str, scenario_names: Sequence[str], play_count: int = 1,
                 description: str = "") -> dict[str, Any]:
    """코박스가 읽는 플레이리스트 JSON (키 케이싱이 계약)."""
    return {
        "playlistName": name,
        "playlistId": 0,
        "authorSteamId": "",
        "authorName": "SA AIM LABS",
        "scenarioList": [{"scenario_name": str(s), "play_Count": max(1, int(play_count))}
                         for s in scenario_names if str(s).strip()],
        "description": description,
        "hasOfflineScenarios": True,
        "hasEdited": False,
        "shareCode": "",
        "version": PLAYLIST_VERSION,
        "updated": int(time.time()),
        "isPrivate": True,
    }


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "SA_AIM_LABS"


def write_playlist(name: str, scenario_names: Sequence[str], target: Path | None = None,
                   play_count: int = 1, description: str = "") -> dict[str, Any]:
    """플레이리스트 1개를 `.json`(코박스 표준) + `.plo`(같은 내용)로 저장한다."""
    names = [str(s).strip() for s in scenario_names if str(s).strip()]
    if not names:
        raise ValueError("플레이리스트에 담을 시나리오가 없습니다")
    folder = Path(target) if target else paths.playlists_dir()
    folder.mkdir(parents=True, exist_ok=True)
    doc = playlist_doc(name, names, play_count=play_count, description=description)
    payload = json.dumps(doc, ensure_ascii=False, indent="\t")
    written: list[str] = []
    safe = _safe_name(name)
    for suffix in (".json", ".plo"):
        dest = folder / f"{safe}{suffix}"
        dest.write_text(payload, encoding="utf-8", newline="\r\n")
        written.append(str(dest))
    return {"ok": True, "files": written, "count": len(names), "folder": str(folder),
            "names": names, "name": name}
