"""게임 시작 10초 — 상대 팀 5명 병렬 스카우팅 (v2.0.1).

v2.0.1 변경
-----------
* **match-detail 호출을 완전히 없앴다.**  예전에는 상대 1명마다 최근 10~20판의
  경기 상세를 따로 받아(5명이면 최대 100콜) 맵 승률·딜량을 계산했다.  그래서
  첫 스카우팅이 3초를 넘기고 넥슨 호출 한도도 금방 닳았다.
* 이제는 ``/user/tier`` + ``/match (랭크전 솔로)`` + ``/match (랭크전 파티)`` 만
  병렬로 받아 **솔로랭크(티어·승률·K/D)** 와 **파티랭크(티어·승률·K/D)** 를
  나눠서 보여 준다.  경기 목록(kill·death·match_result)만으로 승률·K/D 를
  계산하므로 데미지 항목은 표시하지 않는다.
* 무기는 ``/user/recent-info`` 한 콜(15분 캐시)로만 판단한다.

호출은 두 단계다.
  A) 닉네임 → ouid (1시간 캐시)
  B) ouid 별 basic · tier · match(솔로) · match(파티) · recent-info 를 한 풀에서 병렬
"""
from __future__ import annotations

import threading
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed
from typing import Any, Callable, Sequence

from . import nexon

# 모드별로 볼 최근 판 수 — 목록이 1000건까지 올 수 있어 잘라 쓴다
SCOUT_GAMES = 20
# 10인 카운트다운: 10명 × (id·tier·솔로·파티·최근정보) = 50콜을 20워커로 병렬
MAX_TARGETS = 10
MAX_WORKERS = 20
DEFAULT_BUDGET = 4.5
PROFILE_BUDGET = 1.8

WEAPON_KO = {"sniper": "스나", "rifle": "라플", "special": "특화"}

SHORT_TIER = {
    "언랭크": "언랭", "브론즈": "브론즈", "실버": "실버", "골드": "골드",
    "마스터": "마스터", "그랜드마스터": "그마", "레전드": "레전",
    "불레전드": "불레", "별레전드": "별레",
}


class InFlight:
    """같은 스카우팅 요청이 이미 돌고 있으면 두 번째는 즉시 돌려보낸다.

    단축키 연타·오버레이 재입력으로 같은 5명을 열 번 조회하면 넥슨 프록시가
    큐에 쌓여 10초 넘게 늦어진다.  (키 = 기능 + 정렬된 닉네임 + 맵)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: set[str] = set()

    def acquire(self, key: str) -> bool:
        with self._lock:
            if key in self._keys:
                return False
            self._keys.add(key)
            return True

    def release(self, key: str) -> None:
        with self._lock:
            self._keys.discard(key)

    def keys(self) -> list[str]:
        with self._lock:
            return sorted(self._keys)


INFLIGHT = InFlight()


def request_key(kind: str, names: Sequence[str], map_name: str = "") -> str:
    clean = ",".join(sorted(str(n or "").strip().lower() for n in names if str(n or "").strip()))
    return f"{kind}|{clean}|{str(map_name or '').strip().lower()}"


def run_pool(fn: Callable[[Any], Any], items: Sequence[Any], *,
             workers: int, timeout: float) -> tuple[list[tuple[Any, Any]], bool]:
    """제한 시간 안에 끝난 작업만 돌려준다 — 시간이 지나면 **기다리지 않는다**.

    ``with ThreadPoolExecutor`` 는 블록을 나갈 때 모든 스레드를 join 하므로,
    as_completed 가 타임아웃해도 느린 호출이 끝날 때까지 화면이 멈췄다.
    여기서는 실행기를 직접 닫아(``cancel_futures``) 남은 작업을 버린다.
    """
    if not items:
        return [], False
    pool = ThreadPoolExecutor(max_workers=max(1, min(int(workers or 1), len(items))))
    futures = {pool.submit(fn, item): item for item in items}
    done: list[tuple[Any, Any]] = []
    timed_out = False
    try:
        for future in as_completed(futures, timeout=max(0.05, float(timeout))):
            item = futures[future]
            try:
                done.append((item, future.result()))
            except Exception:  # noqa: BLE001 - 한 건 실패는 나머지를 막지 않는다
                done.append((item, None))
    except FutureTimeout:
        timed_out = True
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
    return done, timed_out


# ---------------------------------------------------------------- 값 다듬기
def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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


def short_tier(name: Any) -> str:
    """'그랜드마스터 II' → '그마 II' (오버레이 한 줄에 넣기)."""
    text = str(name or "").strip()
    if not text:
        return "언랭"
    parts = text.split(" ")
    head = SHORT_TIER.get(parts[0], parts[0])
    return " ".join([head] + parts[1:]) if len(parts) > 1 else head


def mode_stats(rows: Sequence[dict[str, Any]] | None, limit: int = SCOUT_GAMES) -> dict[str, Any]:
    """한 모드의 최근 N판 → 판수 · 승률 · K/D · 연승.

    경기 목록에는 ``kill`` · ``death`` · ``match_result`` 가 있으므로
    match-detail 없이도 승률과 K/D 를 낼 수 있다.
    """
    items = [m for m in (rows or []) if isinstance(m, dict)]
    items.sort(key=nexon.match_time, reverse=True)
    items = items[:max(1, int(limit))]
    games = len(items)
    if not games:
        return {"games": 0, "wins": 0, "win": None, "kd": None,
                "kills": 0, "deaths": 0, "streak": 0}
    wins = 0
    kills = deaths = 0
    streak = 0
    streak_open = True
    for m in items:
        won = str(m.get("match_result")) == "1"
        wins += 1 if won else 0
        kills += int(_num(m.get("kill")))
        deaths += int(_num(m.get("death")))
        if streak_open:
            if won:
                streak += 1
            else:
                streak_open = False
    return {
        "games": games, "wins": wins,
        "win": round(wins / games * 100, 1),
        "kd": round(kills / (kills + deaths) * 100, 1) if (kills + deaths) else None,
        "kills": kills, "deaths": deaths, "streak": streak,
    }


def _tier_side(tier: dict[str, Any], kind: str) -> dict[str, Any]:
    """user/tier 응답 → 한 모드의 {tier, rp} (이름 우선)."""
    prefix = "party" if kind == "party" else "solo"
    rp = int(_num(tier.get(f"{prefix}_rank_match_score")))
    name = nexon.kr_tier(tier.get(f"{prefix}_rank_match_tier"))
    if rp <= 0 and name in ("", "언랭크"):
        name = "언랭크"
    return {"tier": name, "rp": rp, "ranked": rp > 0}


def build_row(name: str, *, basic: dict[str, Any] | None = None,
              tier: dict[str, Any] | None = None,
              solo_rows: Sequence[dict[str, Any]] | None = None,
              party_rows: Sequence[dict[str, Any]] | None = None,
              recent: dict[str, Any] | None = None,
              ouid: str | None = None, error: str | None = None) -> dict[str, Any]:
    """한 명의 스카우팅 행 — 솔로/파티를 나눠 담는다 (데미지 없음)."""
    if error:
        return {"name": name, "ouid": ouid, "error": error, "weapon": "sniper",
                "solo": 0, "party": None, "solo_tier": "언랭크", "party_tier": "언랭크",
                "solo_stats": mode_stats([]), "party_stats": mode_stats([]),
                "games": 0, "win": None, "kd": None, "badge": "정보 없음"}
    basic = basic or {}
    tier = tier or {}
    real = str(basic.get("user_name") or name).strip() or name
    solo_stats = mode_stats(solo_rows)
    party_stats = mode_stats(party_rows)
    solo_info = _tier_side(tier, "solo")
    party_info = _tier_side(tier, "party")
    kills = solo_stats["kills"] + party_stats["kills"]
    deaths = solo_stats["deaths"] + party_stats["deaths"]
    games = solo_stats["games"] + party_stats["games"]
    wins = solo_stats["wins"] + party_stats["wins"]
    weapon = weapon_of(recent or {})
    row = {
        "name": real,
        "ouid": ouid,
        "clan": basic.get("clan_name") or "",
        "weapon": weapon,
        "solo": solo_info["rp"],
        "solo_tier": solo_info["tier"],
        "party": party_info["rp"] or None,
        "party_tier": party_info["tier"] if party_info["ranked"] else "언랭크",
        "solo_stats": {**solo_stats, **solo_info},
        "party_stats": {**party_stats, **party_info},
        "games": games,
        "win": round(wins / games * 100, 1) if games else None,
        "kd": round(kills / (kills + deaths) * 100, 1) if (kills + deaths) else None,
        "dmg": None,          # v2.0.1 — 데미지는 더 이상 조회·표시하지 않는다
        "recent": {
            "win": _pct((recent or {}).get("recent_win_rate")),
            "kd": _pct((recent or {}).get("recent_kill_death_rate")),
        },
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    row["badge"] = badge_text(row)
    return row


def _mode_badge(label: str, stats: dict[str, Any]) -> str:
    if not stats.get("games"):
        return f"{label} 표본 없음"
    win = stats.get("win")
    kd = stats.get("kd")
    bits = [f"{label} {round(win)}%" if win is not None else f"{label} —"]
    if kd is not None:
        bits.append(f"K/D {round(kd)}%")
    return " · ".join(bits)


def badge_text(row: dict[str, Any]) -> str:
    """`스나 · 솔로 52% · K/D 58% · 파티 47% · K/D 61%` — 한 줄 배지."""
    if row.get("error"):
        return "정보 없음"
    weapon = WEAPON_KO.get(str(row.get("weapon") or ""), "스나")
    parts = [weapon,
             _mode_badge("솔로", row.get("solo_stats") or {}),
             _mode_badge("파티", row.get("party_stats") or {})]
    return " · ".join(parts)


def row_payload(row: dict[str, Any]) -> dict[str, Any]:
    """오버레이·화면이 그대로 그릴 수 있는 슬림 행."""
    solo = row.get("solo_stats") or {}
    party = row.get("party_stats") or {}
    return {
        "name": row.get("name") or "",
        "clan": row.get("clan") or "",
        "weapon": WEAPON_KO.get(str(row.get("weapon") or ""), "스나"),
        "solo": row.get("solo") or 0,
        "solo_tier": row.get("solo_tier") or "언랭크",
        "party": row.get("party"),
        "party_tier": row.get("party_tier") or "언랭크",
        "solo_stats": {"tier": solo.get("tier") or "", "short": short_tier(solo.get("tier")),
                       "win": solo.get("win"), "kd": solo.get("kd"), "games": solo.get("games") or 0},
        "party_stats": {"tier": party.get("tier") or "", "short": short_tier(party.get("tier")),
                        "win": party.get("win"), "kd": party.get("kd"), "games": party.get("games") or 0},
        "games": row.get("games") or 0,
        "win": row.get("win"),
        "kd": row.get("kd"),
        "badge": row.get("badge") or badge_text(row),
        "error": row.get("error"),
    }


# ---------------------------------------------------------------- 조회
def _endpoint(client: nexon.NexonClient, kind: str, ouid: str) -> Any:
    if kind == "basic":
        return client.user_basic(ouid)
    if kind == "tier":
        return client.user_tier(ouid)
    if kind == "solo":
        return client.matches(ouid, nexon.MATCH_TYPES[0])
    if kind == "party":
        return client.matches(ouid, nexon.MATCH_TYPES[1])
    return client.user_recent(ouid)


def fetch_rows(client: nexon.NexonClient, targets: Sequence[str], *,
               budget: float = DEFAULT_BUDGET,
               on_update: Callable[[list[dict[str, Any]]], None] | None = None
               ) -> dict[str, Any]:
    """상대 N명 → 행 목록.  (닉네임 해석 → 5종 병렬 조회)"""
    started = time.monotonic()
    deadline = started + max(0.4, float(budget))

    def _resolve(name: str) -> tuple[str, str | None, str | None]:
        text = str(name or "").strip()
        # 32자리 hex = ouid (닉네임은 최대 12자) — 그대로 쓰면 API 1건을 아낀다
        if re.fullmatch(r"[0-9a-fA-F]{32}", text):
            return name, text.lower(), None
        try:
            return name, client.resolve_ouid(text), None
        except Exception as exc:  # noqa: BLE001 - 한 명 실패는 나머지를 막지 않는다
            return name, None, nexon.friendly_error(str(exc))

    resolved, timed_out = run_pool(_resolve, list(targets), workers=len(targets),
                                   timeout=min(PROFILE_BUDGET, max(0.4, budget * 0.5)))
    found: dict[str, tuple[str | None, str | None]] = {}
    for name, value in resolved:
        if isinstance(value, tuple) and len(value) == 3:
            _, ouid, error = value
        else:
            ouid, error = None, "응답 없음"
        found[name] = (ouid, error)
    for name in targets:
        found.setdefault(name, (None, "시간 초과"))

    jobs: list[tuple[str, str, str]] = []
    for name in targets:
        ouid, error = found[name]
        if not ouid:
            continue
        for kind in ("basic", "tier", "solo", "party", "recent"):
            jobs.append((name, kind, ouid))

    fetched: dict[tuple[str, str], Any] = {}
    if jobs:
        remaining = max(0.3, deadline - time.monotonic())
        done, more_timeout = run_pool(lambda job: _endpoint(client, job[1], job[2]), jobs,
                                      workers=min(MAX_WORKERS, len(jobs)), timeout=remaining)
        timed_out = timed_out or more_timeout
        for job, value in done:
            if value is not None:
                fetched[(job[0], job[1])] = value

    rows: list[dict[str, Any]] = []
    for name in targets:
        ouid, error = found[name]
        if not ouid:
            rows.append(build_row(name, error=error or "닉네임을 찾지 못했습니다"))
            continue
        if (name, "basic") not in fetched and (name, "tier") not in fetched:
            rows.append(build_row(name, ouid=ouid, error=error or "조회 시간 초과"))
            continue
        rows.append(build_row(
            name, ouid=ouid,
            basic=fetched.get((name, "basic")),
            tier=fetched.get((name, "tier")),
            solo_rows=fetched.get((name, "solo")),
            party_rows=fetched.get((name, "party")),
            recent=fetched.get((name, "recent")),
        ))
    if on_update:
        try:
            on_update([row_payload(r) for r in rows])
        except Exception:  # noqa: BLE001
            pass
    return {"rows": rows, "timed_out": timed_out, "elapsed": round(time.monotonic() - started, 2)}


def quick_scout(client: nexon.NexonClient, names: Sequence[str], map_name: str = "",
                *, depth: int = 0, budget: float = DEFAULT_BUDGET,
                on_update: Callable[[list[dict[str, Any]]], None] | None = None,
                ledger: InFlight | None = None) -> dict[str, Any]:
    """상대 5명 병렬 스카우팅 — 솔로/파티를 나눠서, match-detail 없이.

    같은 요청(같은 닉네임 조합·맵)이 이미 돌고 있으면 ``busy=True`` 로 즉시 돌아간다.
    ``depth`` · ``map_name`` 은 예전 호출부 호환용이며 더 이상 쓰지 않는다.
    """
    targets = [str(n).strip() for n in names if str(n or "").strip()][:MAX_TARGETS]
    if not targets:
        return {"ok": False, "rows": [], "message": "분석할 닉네임이 없습니다"}
    ledger = ledger or INFLIGHT
    key = request_key("scout", targets, map_name)
    if not ledger.acquire(key):
        return {"ok": False, "busy": True, "rows": [],
                "message": "같은 상대를 이미 분석 중입니다 · 잠시 뒤 결과가 뜨는대로 갱신됩니다"}
    try:
        got = fetch_rows(client, targets, budget=budget, on_update=on_update)
        rows = got["rows"]
        payload = [row_payload(r) for r in rows]
        found = len([r for r in rows if not r.get("error")])
        elapsed = got["elapsed"]
        return {"ok": found > 0, "rows": payload, "raw": rows,
                "elapsed": elapsed, "timed_out": bool(got.get("timed_out")),
                "stale": False,
                "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
                "message": f"상대 {found}/{len(rows)}명 분석 · {round(elapsed * 1000)}ms"}
    finally:
        ledger.release(key)


def team_power(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """5명 위협도 요약 — 오버레이 상단에 한 줄."""
    values = [float(r.get("kd") or 0) for r in rows if r.get("badge")]
    average = round(sum(values) / len(values), 1) if values else 0.0
    label = "위협 높음" if average >= 60 else "무난" if average >= 52 else "약함"
    return {"avg_kd": average, "label": label, "count": len(values)}
