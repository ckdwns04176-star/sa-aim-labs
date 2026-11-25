"""게임 시작 10초 — 상대 팀 5명 병렬 스카우팅.

목표: 1.5~2.5초 안에 각 적의 **주무기 + 이 맵 승률(없으면 전체) + K/D(%)** 를
한 줄 배지로 띄운다.  그래서 두 단계로 나눠 그린다.

  A) 프로필 단계 (~0.4초): id · basic · tier · recent-info 4콜을 5명 병렬로.
     → 주무기 · 전체 승률 · KD% 는 바로 표시.
  B) 정밀 단계 (~2초): 최근 15~20판 목록 + match-detail(캐시 우선)으로
     이 맵 승률을 채운다.  끝난 것부터 화면에 덧쓴다.

호출 수가 많은 단계는 프록시(공용 키)에서 간격을 좁혀 돌리고, 개인 키로
직접 호출할 때는 NEXON 이 정한 간격을 지킨다 (NexonClient 가 알아서).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Sequence

from . import nexon

DEFAULT_DEPTH = 16
PROFILE_BUDGET = 1.2
TOTAL_BUDGET = 3.4

WEAPON_KO = {"sniper": "스나", "rifle": "라플", "special": "특화"}


def _pct(value: Any) -> float | None:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def weapon_of(recent: dict[str, Any]) -> str:
    """recent-info 의 스나/돌격 비율로 주무기 추정."""
    sniper = _pct(recent.get("recent_sniper_rate")) or 0.0
    assault = _pct(recent.get("recent_assault_rate")) or 0.0
    special = _pct(recent.get("recent_special_rate")) or 0.0
    best = max((("sniper", sniper), ("rifle", assault), ("special", special)),
               key=lambda pair: pair[1])
    return best[0] if best[1] > 0 else "sniper"


def badge_text(row: dict[str, Any]) -> str:
    """`스나 · 이 맵 64% · KD 61%` — 오버레이 한 줄 배지."""
    weapon = WEAPON_KO.get(str(row.get("weapon") or ""), "스나")
    parts = [weapon]
    map_win = row.get("map_win")
    if map_win is not None:
        parts.append(f"이 맵 {round(float(map_win))}%")
    elif row.get("win") is not None:
        parts.append(f"전체 {round(float(row['win']))}%")
    if row.get("kd") is not None:
        parts.append(f"KD {round(float(row['kd']))}%")
    return " · ".join(parts)


def row_payload(row: dict[str, Any]) -> dict[str, Any]:
    """오버레이가 그대로 그릴 수 있는 슬림 배지."""
    return {
        "name": row.get("name") or "",
        "clan": row.get("clan") or "",
        "tier": row.get("tier") or "",
        "weapon": WEAPON_KO.get(str(row.get("weapon") or ""), "스나"),
        "map_win": row.get("map_win"),
        "win": row.get("win"),
        "kd": row.get("kd"),
        "games": row.get("games") or 0,
        "badge": badge_text(row),
        "error": row.get("error"),
    }


def _profile(client: nexon.NexonClient, name: str) -> dict[str, Any]:
    try:
        ouid = client.resolve_ouid(name)
        basic = client.user_basic(ouid)
        real = str(basic.get("user_name") or name)
        tier = client.user_tier(ouid)
        recent = client.user_recent(ouid)
        return {
            "name": real, "ouid": ouid,
            "clan": basic.get("clan_name") or "",
            "tier": nexon.kr_tier(tier.get("party_rank_match_tier") or tier.get("solo_rank_match_tier")),
            "weapon": weapon_of(recent),
            "win": _pct(recent.get("recent_win_rate")),
            "kd": _pct(recent.get("recent_kill_death_rate")),
            "games": 0, "map_win": None, "maps": [],
        }
    except Exception as exc:  # noqa: BLE001 - 한 명 실패는 나머지를 막지 않는다
        return {"name": name, "error": nexon.friendly_error(str(exc)), "weapon": "sniper"}


def _details(client: nexon.NexonClient, row: dict[str, Any], map_name: str,
             depth: int) -> dict[str, Any]:
    ouid = row.get("ouid")
    if not ouid:
        return row
    try:
        rows = client.recent_matches(str(ouid), 20)
        details = []
        for item in rows[:depth]:
            match_id = item.get("match_id")
            detail = client.match_detail_cached(match_id) or client.match_detail(match_id)
            details.append(detail)
        agg = nexon.aggregate(rows, details, str(row.get("name") or ""))
        row = dict(row)
        row["games"] = int(agg.get("games") or 0)
        row["win"] = _pct(agg.get("win")) if agg.get("games") else row.get("win")
        row["kd"] = _pct(agg.get("kd")) if agg.get("games") else row.get("kd")
        maps = [m for m in (agg.get("maps") or []) if m.get("games")]
        row["maps"] = maps
        if map_name:
            for entry in maps:
                if nexon_map_equal(entry.get("map"), map_name):
                    row["map_win"] = _pct(entry.get("win"))
                    break
        return row
    except Exception:  # noqa: BLE001 - 정밀 단계 실패는 프로필 값으로 버틴다
        return row


def nexon_map_equal(a: Any, b: Any) -> bool:
    def base(value: Any) -> str:
        text = str(value or "").strip()
        for suffix in ("(심플)", "(폭파)", "(클래식)", "-심플", "-폭파"):
            text = text.replace(suffix, "")
        return text.replace(" ", "").lower()

    left, right = base(a), base(b)
    return bool(left) and bool(right) and (left == right or left in right or right in left)


def quick_scout(client: nexon.NexonClient, names: Sequence[str], map_name: str = "",
                *, depth: int = DEFAULT_DEPTH, budget: float = TOTAL_BUDGET,
                on_update: Callable[[list[dict[str, Any]]], None] | None = None) -> dict[str, Any]:
    """상대 5명 병렬 스카우팅 — 프로필 먼저, 맵 승률은 이어서."""
    targets = [str(n).strip() for n in names if str(n or "").strip()][:5]
    if not targets:
        return {"ok": False, "rows": [], "message": "분석할 닉네임이 없습니다"}
    started = time.monotonic()
    rows: dict[str, dict[str, Any]] = {}

    # ── A) 프로필 (병렬) ─────────────────────────────────────────────
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = {pool.submit(_profile, client, name): name for name in targets}
        try:
            for future in as_completed(futures, timeout=PROFILE_BUDGET + 1.6):
                row = future.result()
                rows[row["name"]] = row
        except Exception:  # noqa: BLE001 - 시간이 지나면 있는 것만 쓴다
            pass
    for name in targets:
        rows.setdefault(name, {"name": name, "weapon": "sniper", "error": "시간 초과", "map_win": None})
    ordered = [rows.get(name) or {"name": name} for name in targets]
    if on_update:
        try:
            on_update([row_payload(r) for r in ordered])
        except Exception:  # noqa: BLE001
            pass

    # ── B) 최근 경기 상세 → 이 맵 승률 (병렬 · 남은 시간만큼) ─────────
    remaining = max(0.4, budget - (time.monotonic() - started))
    refined: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = {pool.submit(_details, client, row, map_name, depth): row for row in ordered
                   if row.get("ouid")}
        refined_map: dict[str, dict[str, Any]] = {}
        try:
            for future in as_completed(futures, timeout=remaining):
                row = future.result()
                refined_map[row["name"]] = row
                if on_update:
                    current = [refined_map.get(r["name"], r) for r in ordered]
                    try:
                        on_update([row_payload(r) for r in current])
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001 - 남은 선수는 프로필 값 유지
            pass
    for row in ordered:
        refined.append(refined_map.get(row["name"], row))
    payload = [row_payload(r) for r in refined]
    found = len([r for r in refined if not r.get("error")])
    return {"ok": found > 0, "rows": payload, "raw": refined,
            "elapsed": round(time.monotonic() - started, 2),
            "message": f"상대 {found}/{len(refined)}명 분석 · {round((time.monotonic() - started) * 1000)}ms"}


def team_power(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """5명 위협도 요약 — 오버레이 상단에 한 줄."""
    values = [float(r.get("kd") or 0) for r in rows if r.get("badge")]
    average = round(sum(values) / len(values), 1) if values else 0.0
    label = "위협 높음" if average >= 60 else "무난" if average >= 52 else "약함"
    return {"avg_kd": average, "label": label, "count": len(values)}
