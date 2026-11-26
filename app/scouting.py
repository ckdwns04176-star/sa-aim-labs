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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed
from typing import Any, Callable, Sequence

from . import nexon

DEFAULT_DEPTH = 12
PROFILE_BUDGET = 1.2
TOTAL_BUDGET = 3.4

WEAPON_KO = {"sniper": "스나", "rifle": "라플", "special": "특화"}


class InFlight:
    """같은 스카우팅 요청이 이미 돌고 있으면 두 번째는 즉시 돌려보낸다.

    F9 연타·오버레이 재입력으로 같은 5명을 열 번 조회하면 넥슨 프록시가
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
    if row.get("error"):
        return "정보 없음"
    weapon = WEAPON_KO.get(str(row.get("weapon") or ""), "스나")
    parts = [weapon]
    map_win = row.get("map_win")
    if map_win is not None:
        parts.append(f"이 맵 {round(float(map_win))}%")
    elif row.get("win") is not None:
        parts.append(f"전체 {round(float(row['win']))}%")
    if row.get("kd") is not None:
        parts.append(f"KD {round(float(row['kd']))}%")
    if len(parts) == 1:
        parts.append("표본 없음")
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
        recent = client.user_recent(ouid)
        return {
            "name": real, "ouid": ouid,
            "clan": basic.get("clan_name") or "",
            "tier": "",
            "weapon": weapon_of(recent),
            "win": _pct(recent.get("recent_win_rate")),
            "kd": _pct(recent.get("recent_kill_death_rate")),
            "games": 0, "map_win": None, "maps": [],
        }
    except Exception as exc:  # noqa: BLE001 - 한 명 실패는 나머지를 막지 않는다
        return {"name": name, "error": nexon.friendly_error(str(exc)), "weapon": "sniper"}


def _finish_row(row: dict[str, Any], rows: list[dict[str, Any]],
                details: list[dict[str, Any] | None], map_name: str) -> dict[str, Any]:
    try:
        agg = nexon.aggregate(rows, details, str(row.get("name") or ""))
        row = dict(row)
        row["games"] = int(agg.get("games") or 0)
        if agg.get("games"):
            row["win"] = _pct(agg.get("win"))
            row["kd"] = _pct(agg.get("kd"))
        maps = [m for m in (agg.get("maps") or []) if m.get("games")]
        row["maps"] = maps
        if map_name:
            for entry in maps:
                if nexon_map_equal(entry.get("map"), map_name):
                    row["map_win"] = _pct(entry.get("win"))
                    break
    except Exception:  # noqa: BLE001 - 정밀 단계 실패는 프로필 값으로 버틴다
        pass
    return row


def nexon_map_equal(a: Any, b: Any) -> bool:
    def base(value: Any) -> str:
        text = str(value or "").strip()
        for suffix in ("(심플)", "(폭파)", "(클래식)", "-심플", "-폭파"):
            text = text.replace(suffix, "")
        return text.replace(" ", "").lower()

    left, right = base(a), base(b)
    return bool(left) and bool(right) and (left == right or left in right or right in left)


def _recent_party(client: nexon.NexonClient, ouid: str, limit: int = 20) -> list[dict[str, Any]]:
    """파티 랭크 목록 1콜 (없으면 솔로 1콜) — 스카우팅은 파티전이 기준.

    NEXON API 는 목록을 최대 1000건까지 돌려주기도 해서, 최근 20판만 남긴다
    (전체 1000판을 집계하면 '이 맵 승률'이 엉뚱한 표본이 된다).
    """
    try:
        rows = client.matches(ouid, nexon.MATCH_TYPES[1])
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        try:
            rows = client.matches(ouid, nexon.MATCH_TYPES[0])
        except Exception:  # noqa: BLE001
            rows = []
    rows = list(rows)
    rows.sort(key=nexon.match_time, reverse=True)
    return rows[:limit]


def quick_scout(client: nexon.NexonClient, names: Sequence[str], map_name: str = "",
                *, depth: int = DEFAULT_DEPTH, budget: float = TOTAL_BUDGET,
                on_update: Callable[[list[dict[str, Any]]], None] | None = None,
                ledger: InFlight | None = None) -> dict[str, Any]:
    """상대 5명 병렬 스카우팅 — 프로필 먼저, 맵 승률은 이어서.

    같은 요청(같은 닉네임 조합·맵)이 이미 돌고 있으면 ``busy=True`` 로
    즉시 돌아간다.  모든 단계는 남은 시간까지만 기다린다.
    """
    targets = [str(n).strip() for n in names if str(n or "").strip()][:5]
    if not targets:
        return {"ok": False, "rows": [], "message": "분석할 닉네임이 없습니다"}
    ledger = ledger or INFLIGHT
    key = request_key("scout", targets, map_name)
    if not ledger.acquire(key):
        return {"ok": False, "busy": True, "rows": [],
                "message": "같은 상대를 이미 분석 중입니다 · 잠시 뒤 결과가 뜨는대로 갱신됩니다"}
    try:
        return _quick_scout(client, targets, map_name, depth=depth, budget=budget,
                            on_update=on_update)
    finally:
        ledger.release(key)


def _quick_scout(client: nexon.NexonClient, targets: list[str], map_name: str, *,
                 depth: int, budget: float,
                 on_update: Callable[[list[dict[str, Any]]], None] | None) -> dict[str, Any]:
    started = time.monotonic()
    rows: dict[str, dict[str, Any]] = {}

    # ── A) 프로필 (병렬) ─────────────────────────────────────────────
    done, timed_out = run_pool(lambda name: _profile(client, name), targets,
                               workers=len(targets), timeout=PROFILE_BUDGET + 1.6)
    for name, row in done:
        if isinstance(row, dict):
            rows[row.get("name") or name] = row
    for name in targets:
        rows.setdefault(name, {"name": name, "weapon": "sniper", "error": "시간 초과",
                               "map_win": None, "stale": True})
    ordered = [rows.get(name) or {"name": name} for name in targets]
    if on_update:
        try:
            on_update([row_payload(r) for r in ordered])
        except Exception:  # noqa: BLE001
            pass

    # ── B) 최근 경기 상세 → 이 맵 승률 (전체 병렬 · 남은 시간만큼) ─────
    remaining = max(0.4, budget - (time.monotonic() - started))
    with_ouid = [r for r in ordered if r.get("ouid")]
    match_rows: dict[str, list[dict[str, Any]]] = {}
    done, _ = run_pool(lambda row: _recent_party(client, str(row["ouid"])), with_ouid,
                       workers=6, timeout=max(0.4, remaining * 0.5))
    for row, matches in done:
        match_rows[row["name"]] = matches or []

    tasks: list[tuple[str, str]] = []      # (선수 이름, match_id)
    for row in ordered:
        for match in (match_rows.get(row["name"]) or [])[:depth]:
            if match.get("match_id"):
                tasks.append((str(row.get("name") or ""), str(match["match_id"])))
    details_by_player: dict[str, dict[str, dict[str, Any] | None]] = {}
    if tasks:
        pending: list[tuple[str, str]] = []
        for name, match_id in tasks:
            cached = client.match_detail_cached(match_id)
            if cached is not None:
                details_by_player.setdefault(name, {})[match_id] = cached
                continue
            pending.append((name, match_id))
        if pending:
            def _fetch(task: tuple[str, str]) -> Any:
                return client.match_detail(task[1])

            done, _ = run_pool(_fetch, pending, workers=10,
                               timeout=max(0.4, (started + budget) - time.monotonic()))
            for (name, match_id), detail in done:
                details_by_player.setdefault(name, {})[match_id] = detail

    refined: list[dict[str, Any]] = []
    for row in ordered:
        name = row["name"]
        matches = (match_rows.get(name) or [])[:depth]
        detail_map = details_by_player.get(name) or {}
        details = [detail_map.get(str(m.get("match_id"))) for m in matches]
        refined.append(_finish_row(row, matches, details, map_name) if matches else row)
    payload = [row_payload(r) for r in refined]
    for row, shallow in zip(payload, refined):
        if shallow.get("stale") or shallow.get("error"):
            row["stale"] = True
    found = len([r for r in refined if not r.get("error")])
    elapsed = round(time.monotonic() - started, 2)
    return {"ok": found > 0, "rows": payload, "raw": refined,
            "elapsed": elapsed, "timed_out": bool(timed_out),
            "stale": any(r.get("stale") for r in payload),
            "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
            "message": f"상대 {found}/{len(refined)}명 분석 · {round(elapsed * 1000)}ms"}


def team_power(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """5명 위협도 요약 — 오버레이 상단에 한 줄."""
    values = [float(r.get("kd") or 0) for r in rows if r.get("badge")]
    average = round(sum(values) / len(values), 1) if values else 0.0
    label = "위협 높음" if average >= 60 else "무난" if average >= 52 else "약함"
    return {"avg_kd": average, "label": label, "count": len(values)}
