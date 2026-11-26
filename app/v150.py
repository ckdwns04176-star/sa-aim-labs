"""화면 보조 계산 — 파티 맵 밴·픽 추천 · RP 등락 뱃지 · 공식 맵 이미지 · 화면 배율.

v2.0.0 — 에임 트레이너 감시·사용자 시나리오·추천 제외를 걷어냈다.  모두 표준
라이브러리만 쓰고, 화면이 바로 쓸 수 있게 순수 함수로 둔다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence

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


# ---------------------------------------------------------------- 화면 배율
def auto_ui_scale(screen_w: int, screen_h: int, dpi_scale: float = 1.0) -> int:
    """해상도별 기본 배율 — QHD(2560×1440) 이상 115%, FHD 이하 100% (v1.0.0).

    OS 배율이 이미 큰 경우(125% 이상)에는 창이 화면을 넘치므로 100% 로 둔다.
    """
    try:
        w, h = int(screen_w), int(screen_h)
    except (TypeError, ValueError):
        return DEFAULT_UI_SCALE
    if w >= 2560 and h >= 1440 and float(dpi_scale or 1.0) < 1.25:
        return 115
    return DEFAULT_UI_SCALE


def map_asset_dir() -> Path:
    return paths.bundle_dir() / "ui" / "assets" / "maps"


def map_index_payload() -> dict[str, str]:
    """화면으로 넘기는 맵 표지 색인 {맵 이름: assets/maps/….jpg}."""
    return {name: f"assets/maps/{file_name}" for name, file_name in _map_index().items()}
