"""v1.5.0 신규 — 파티 맵 밴·픽 추천 · RP 등락 뱃지 · 트레이너 종료 감지 · 공식 맵 이미지.

모두 표준 라이브러리만 쓴다 (추가 패키지 없음).  화면이 바로 쓸 수 있게
순수 함수 위주로 두고, Windows 프로세스 감시만 스레드 하나를 쓴다.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import paths

# ---------------------------------------------------------------- 테마
THEMES: dict[str, dict[str, str]] = {
    "coral": {"key": "coral", "name": "코랄 레드", "hex": "#FF4655", "label": "발로란트 트래커 톤"},
    "ice": {"key": "ice", "name": "아이스 블루", "hex": "#38BDF8", "label": "Blitz / OP.GG 프로 톤"},
    "gold": {"key": "gold", "name": "샴페인 골드", "hex": "#DFC07C", "label": "프리미엄 무광 톤"},
}
DEFAULT_THEME = "coral"
UI_SCALES = (90, 100, 115, 125)
DEFAULT_UI_SCALE = 100


def clean_theme(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in THEMES else DEFAULT_THEME


def clean_ui_scale(value: Any, default: int = DEFAULT_UI_SCALE) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return min(UI_SCALES) if number < 90 else max(UI_SCALES) if number > 125 else \
        min(UI_SCALES, key=lambda s: abs(s - number))


# ---------------------------------------------------------------- 공식 맵 이미지
# ui/assets/maps/index.json — 넥슨 서든어택 공식 게임 가이드(guide.sa.nexon.com/guide/35)
# 에서 내려받은 맵 표지 104장.  임의 생성 이미지는 쓰지 않는다.
_MAP_INDEX_CACHE: dict[str, str] | None = None
# 인게임 표기와 가이드 표기가 다른 것들 — 확인된 것만 적는다.
MAP_ALIASES = {
    "제5보급": "제5보급창고",
    "제3보급": "제3보급창고",
    "3보급": "제3보급창고",
    "빅보급": "빅보급창고",
    "a보급": "A보급창고",
    "b2": "B2",
    "듀오릭": "듀오 릭",
    "듀오": "듀오",
    "이터널": "",
    "화이트스콜": "화이트스콜",
}


def _map_index() -> dict[str, str]:
    global _MAP_INDEX_CACHE
    if _MAP_INDEX_CACHE is not None:
        return _MAP_INDEX_CACHE
    table: dict[str, str] = {}
    for base in (paths.bundle_dir() / "ui" / "assets" / "maps",):
        try:
            raw = json.loads((base / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for name, row in (raw.get("maps") or {}).items():
            file_name = str((row or {}).get("file") or "")
            if file_name:
                table[str(name).strip()] = file_name
    _MAP_INDEX_CACHE = table
    return table


def map_key(name: Any) -> str:
    """인게임 맵 이름 → 공식 가이드 맵 이름 (없으면 빈 문자열)."""
    text = str(name or "").strip()
    if not text:
        return ""
    table = _map_index()
    cleaned = re.sub(r"\((?:심플|폭파|클래식|개인전|인질)\)", "", text)
    cleaned = re.sub(r"-(?:심플|폭파|폭파미션|개인전|인질|할로윈|클리어)$", "", cleaned).strip()
    for candidate in (cleaned, text.strip()):
        if candidate in table:
            return candidate
        low = candidate.lower()
        alias = MAP_ALIASES.get(low)
        if alias and alias in table:
            return alias
        for key in table:
            if key.lower() == low:
                return key
    # 접두 일치 (예: '제5보급-심플' → '제5보급창고')
    for key in sorted(table, key=len, reverse=True):
        if cleaned.startswith(key) or key.startswith(cleaned[:3] + key[3:] if len(cleaned) >= 3 else key):
            if cleaned[:3] == key[:3] and len(cleaned) >= 2:
                return key
    return ""


def map_image(name: Any) -> str:
    """맵 이름 → `assets/maps/m19.jpg` (공식 이미지 없으면 빈 문자열 → UI 는 기본 아이콘)."""
    key = map_key(name)
    if not key:
        return ""
    file_name = _map_index().get(key) or ""
    return f"assets/maps/{file_name}" if file_name else ""


def _weighted(rows: Sequence[dict[str, Any]], field: str) -> float | None:
    total = 0.0
    games = 0.0
    for row in rows:
        try:
            g = float(row.get("games") or 0)
            v = float(row.get(field))
        except (TypeError, ValueError):
            continue
        if g <= 0:
            continue
        total += v * g
        games += g
    if games <= 0:
        return None
    return total / games


def party_map_plan(entries: Sequence[dict[str, Any]], *, shrink: float = 8.0,
                   top_n: int = 3) -> dict[str, Any]:
    """파티원 맵별 전적 → 추천 픽 TOP N / 피해야 할 밴 TOP N + 맵별 예상 승률.

    entries: [{"name": 닉, "maps": [{"map","games","win","kd","dmg"}], "source": "cache"|"live"}]
    예상 승률은 판 수로 가중 평균한 뒤 표본이 적은 맵을 50% 쪽으로 줄여(shrink) 계산한다.
    그래서 1~2판만 뛴 맵이 100%로 튀지 않는다.
    """
    table: dict[str, dict[str, Any]] = {}
    members = [str(e.get("name") or "").strip() for e in entries if str(e.get("name") or "").strip()]
    for entry in entries:
        name = str(entry.get("name") or "").strip()
        for row in entry.get("maps") or []:
            map_name = str((row or {}).get("map") or "").strip()
            if not map_name:
                continue
            try:
                games = float((row or {}).get("games") or 0)
            except (TypeError, ValueError):
                continue
            if games <= 0:
                continue
            slot = table.setdefault(map_name, {"map": map_name, "games": 0.0, "players": {},
                                               "_win": 0.0, "_kd": 0.0, "_dmg": 0.0,
                                               "_kd_games": 0.0, "_dmg_games": 0.0})
            slot["games"] += games
            slot["players"][name] = int(slot["players"].get(name, 0) + games)
            try:
                slot["_win"] += float((row or {}).get("win") or 0) * games
            except (TypeError, ValueError):
                pass
            if (row or {}).get("kd") is not None:
                try:
                    slot["_kd"] += float(row.get("kd")) * games
                    slot["_kd_games"] += games
                except (TypeError, ValueError):
                    pass
            if (row or {}).get("dmg") is not None:
                try:
                    slot["_dmg"] += float(row.get("dmg")) * games
                    slot["_dmg_games"] += games
                except (TypeError, ValueError):
                    pass

    rows: list[dict[str, Any]] = []
    for slot in table.values():
        games = slot["games"]
        if games <= 0:
            continue
        win = slot["_win"] / games
        expected = (slot["_win"] + 50.0 * shrink) / (games + shrink)
        kd = (slot["_kd"] / slot["_kd_games"]) if slot["_kd_games"] else None
        dmg = (slot["_dmg"] / slot["_dmg_games"]) if slot["_dmg_games"] else None
        rows.append({
            "map": slot["map"], "games": int(round(games)),
            "win": round(win, 1), "expected": round(expected, 1),
            "kd": round(kd, 1) if kd is not None else None,
            "dmg": int(round(dmg)) if dmg is not None else None,
            "players": dict(sorted(slot["players"].items(), key=lambda kv: -kv[1])),
            "member_games": len(slot["players"]),
            "image": map_image(slot["map"]),
            "key": map_key(slot["map"]),
        })
    rows.sort(key=lambda r: (-r["expected"], -r["games"]))
    enough = [r for r in rows if r["games"] >= 2] or rows
    picks = enough[:max(1, int(top_n))]
    bans = sorted(enough, key=lambda r: (r["expected"], -r["games"]))[:max(1, int(top_n))]
    pick_names = {r["map"] for r in picks}
    bans = [r for r in bans if r["map"] not in pick_names or len(enough) <= len(picks)]
    top_expected = picks[0]["expected"] if picks else None
    return {
        "available": bool(rows),
        "members": members,
        "member_count": len(members),
        "maps": rows,
        "picks": picks,
        "bans": bans,
        "sample_games": int(round(sum(r["games"] for r in rows))),
        "headline": (f"픽 {picks[0]['map']} {picks[0]['expected']:.0f}% · "
                     f"밴 {bans[0]['map']} {bans[0]['expected']:.0f}%" if picks and bans else ""),
        "reason": ("" if rows else "파티원 전적에 맵 기록이 없습니다 · 파티원을 불러와 주세요"),
        "top_expected": round(top_expected, 1) if top_expected is not None else None,
    }


# ---------------------------------------------------------------- 직전 대비 등락 뱃지
def delta_badges(history: Sequence[dict[str, Any]], *, limit: int = 4) -> list[dict[str, Any]]:
    """저장된 병영수첩 스냅샷(barracks_snapshots) 직전 값 대비 등락.

    history 는 오래된 → 최신 순.  비교할 스냅샷이 2개 미만이면 빈 목록.
    """
    rows = [r for r in (history or []) if isinstance(r, dict)]
    if len(rows) < 2:
        return []
    cur, prev = rows[-1], rows[-2]

    def num(source: dict[str, Any], *path: str) -> float | None:
        node: Any = source
        for step in path:
            if not isinstance(node, dict):
                return None
            node = node.get(step)
        try:
            return float(node) if node is not None and str(node).strip() != "" else None
        except (TypeError, ValueError):
            return None

    out: list[dict[str, Any]] = []

    def add(key: str, label: str, before: float | None, after: float | None, unit: str, digits: int = 0) -> None:
        if before is None or after is None:
            return
        diff = after - before
        if abs(diff) < (0.05 if digits else 0.5):
            return
        sign = "+" if diff > 0 else "−"
        fmt = f"{abs(diff):,.{digits}f}"
        out.append({
            "key": key, "label": label,
            "text": f"{label} {sign}{fmt}{unit}",
            "diff": round(diff, digits), "unit": unit,
            "tone": "up" if diff > 0 else "down",
            "before": round(before, digits), "after": round(after, digits),
        })

    add("solo", "솔로", num(prev, "tier", "solo", "rp"), num(cur, "tier", "solo", "rp"), " RP")
    add("party", "파티", num(prev, "tier", "party", "rp"), num(cur, "tier", "party", "rp"), " RP")
    add("kd", "K/D", num(prev, "stats", "kd"), num(cur, "stats", "kd"), "%", 1)
    add("win", "승률", num(prev, "stats", "win"), num(cur, "stats", "win"), "%", 1)
    add("dmg", "딜량", num(prev, "stats", "dmg"), num(cur, "stats", "dmg"), "", 0)
    out.sort(key=lambda r: (0 if r["key"] in ("solo", "party") else 1, r["key"]))
    result = out[:max(1, int(limit))]
    for item in result:
        item["from_date"] = str(prev.get("date") or "")
        item["to_date"] = str(cur.get("date") or "")
    return result


# ---------------------------------------------------------------- 트레이너 종료 감시
TRAINER_PROCESSES = {
    "fpsaimtrainer.exe": "kovak",
    "aimlab_tb.exe": "aimlab",
    "aimlab.exe": "aimlab",
}
TRAINER_LABEL = {"kovak": "KovaaK's", "aimlab": "Aim Lab"}


def _running_trainers_windows() -> set[str]:
    """실행 중인 트레이너 exe 이름 집합 (Windows · ctypes 만)."""
    if os.name != "nt":
        return set()
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001
        return set()
    found: set[str] = set()
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        PROCESS_VM_READ = 0x0010
        PROCESS_QUERY_INFORMATION = 0x0400
        MAX_PATH = 260
        ids = (wintypes.DWORD * 2048)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcesses(ctypes.byref(ids), ctypes.sizeof(ids), ctypes.byref(needed)):
            return set()
        count = int(needed.value) // ctypes.sizeof(wintypes.DWORD)
        buf = ctypes.create_unicode_buffer(MAX_PATH)
        for index in range(min(count, 2048)):
            pid = int(ids[index])
            if pid <= 0:
                continue
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
            if not handle:
                continue
            try:
                size = wintypes.DWORD(MAX_PATH)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    name = os.path.basename(str(buf.value)).lower()
                    if name in TRAINER_PROCESSES:
                        found.add(name)
            finally:
                kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - 감시 실패는 앱을 막지 않는다
        return set()
    return found


class TrainerWatch:
    """`FPSAimTrainer.exe` / `AimLab_tb.exe` 가 켜졌다가 꺼지면 한 번 알린다."""

    def __init__(self, on_finished: Callable[[dict[str, Any]], None] | None = None,
                 interval: float = 5.0) -> None:
        self.on_finished = on_finished
        self.interval = max(2.0, float(interval))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active: set[str] = set()
        self.started_at: dict[str, float] = {}
        self.last_event: dict[str, Any] | None = None
        self.enabled = True

    @property
    def supported(self) -> bool:
        return os.name == "nt"

    @property
    def running(self) -> list[str]:
        return sorted(TRAINER_LABEL.get(TRAINER_PROCESSES.get(n, ""), n) for n in self._active)

    def start(self) -> bool:
        if not self.supported or (self._thread and self._thread.is_alive()):
            return bool(self._thread and self._thread.is_alive())
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sa-trainer-watch", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def poll(self) -> dict[str, Any] | None:
        """한 번 확인 — 새로 꺼진 트레이너가 있으면 이벤트를 돌려준다."""
        if not self.enabled:
            return None
        current = _running_trainers_windows()
        closed = self._active - current
        opened = current - self._active
        now = time.monotonic()
        for name in opened:
            self.started_at[name] = now
        self._active = current
        if not closed:
            return None
        event = None
        for name in closed:
            game = TRAINER_PROCESSES.get(name, "kovak")
            played = now - self.started_at.pop(name, now)
            if played < 20:      # 20초 미만 실행은 오탐으로 본다
                continue
            event = {
                "game": game, "exe": name,
                "label": TRAINER_LABEL.get(game, game),
                "minutes": int(round(played / 60)),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "message": f"{TRAINER_LABEL.get(game, game)} 종료 감지 · 점수를 입력하면 오늘 기록에 바로 반영됩니다",
            }
        if event:
            self.last_event = event
            if self.on_finished:
                try:
                    self.on_finished(event)
                except Exception:  # noqa: BLE001
                    pass
        return event

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.poll()
            except Exception:  # noqa: BLE001
                continue


# ---------------------------------------------------------------- 내전 팀 복사
ROLE_LABEL = {"sniper": "스나", "rifle": "라플", "special": "특수", "assist": "지원"}


def team_copy_text(result: dict[str, Any]) -> str:
    """내전 팀 자동 짜기 결과 → `[A팀] 닉(스나) · … / [B팀] 닉(라플) · …` 한 줄."""
    teams = (result or {}).get("teams") or {}
    roles = (result or {}).get("roles") or {}

    def line(key: str, label: str) -> str:
        members = teams.get(key) or []
        parts: list[str] = []
        for member in members:
            if isinstance(member, dict):
                name = str(member.get("name") or "").strip()
                role = str(member.get("role") or roles.get(name) or "")
            else:
                name = str(member or "").strip()
                role = str(roles.get(name) or "")
            if not name:
                continue
            label_role = ROLE_LABEL.get(role, "")
            parts.append(f"{name}({label_role})" if label_role else name)
        return f"[{label}] " + " · ".join(parts) if parts else f"[{label}] —"

    left = line("a", "A팀")
    right = line("b", "B팀")
    return f"{left} / {right}"


# ---------------------------------------------------------------- 사용자 시나리오
SCENARIO_DIFFS = ("초급", "중급", "고급")


def clean_custom_scenario(payload: dict[str, Any], area_keys: Sequence[str]) -> dict[str, Any]:
    """`+ 내 시나리오 추가` 입력값 검증."""
    data = payload or {}
    name = str(data.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("시나리오 이름을 입력해 주세요")
    area = str(data.get("area") or "").strip()
    if area not in area_keys:
        raise ValueError("5영역 중 하나를 골라 주세요")
    diff = str(data.get("diff") or "").strip() or SCENARIO_DIFFS[0]
    if diff in ("초", "초급"):
        diff = "초급"
    elif diff in ("중", "중급"):
        diff = "중급"
    elif diff in ("고", "고급", "상급"):
        diff = "고급"
    else:
        diff = "초급"

    def url(value: Any) -> str:
        text = str(value or "").strip()[:400]
        if text and not re.match(r"^(https?://|steam://)", text, re.I):
            raise ValueError("플레이 URL 은 http(s):// 또는 steam:// 로 시작해야 합니다")
        return text

    out = {
        "name": name,
        "play_url": url(data.get("play_url")),
        "stats_url": url(data.get("stats_url")),
        "area": area,
        "diff": diff,
        "memo": str(data.get("memo") or "").strip()[:300],
        "game": str(data.get("game") or "kovak") if str(data.get("game") or "") in ("kovak", "aimlab") else "kovak",
        "custom": True,
    }
    return out


def apply_exclusions(recs: Sequence[dict[str, Any]], excluded: Iterable[str]) -> list[dict[str, Any]]:
    """추천 5종에서 제외된 시나리오를 뺀다 (라이브러리 목록은 그대로)."""
    blocked = {str(n).strip().lower() for n in (excluded or []) if str(n).strip()}
    if not blocked:
        return list(recs)
    return [r for r in recs if str((r or {}).get("name") or "").strip().lower() not in blocked]


def is_excluded(name: Any, excluded: Iterable[str]) -> bool:
    target = str(name or "").strip().lower()
    return bool(target) and target in {str(n).strip().lower() for n in (excluded or []) if str(n).strip()}


# ---------------------------------------------------------------- 화면 배율
def auto_ui_scale(screen_w: int, screen_h: int, dpi_scale: float = 1.0) -> int:
    """QHD(2560×1440) 이상 + 낮은 OS 배율이면 115% 로 키운다 (v1.5.0 · #1)."""
    try:
        w, h = int(screen_w), int(screen_h)
    except (TypeError, ValueError):
        return DEFAULT_UI_SCALE
    if w >= 3840 and h >= 2160 and float(dpi_scale or 1.0) < 1.75:
        return 125
    if w >= 2560 and h >= 1440 and float(dpi_scale or 1.0) < 1.25:
        return 115
    return DEFAULT_UI_SCALE


def map_asset_dir() -> Path:
    return paths.bundle_dir() / "ui" / "assets" / "maps"


def map_index_payload() -> dict[str, str]:
    """화면으로 넘기는 맵 표지 색인 {맵 이름: assets/maps/….jpg}."""
    return {name: f"assets/maps/{file_name}" for name, file_name in _map_index().items()}
