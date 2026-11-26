"""서든어택 전적 해석 — 홈 화면 · 병영수첩 · 맵 분석에 쓰는 순수 계산.

v2.0.0 에서 에임 연습(코박스·에임랩) 기록이 빠지면서, 화면에 필요한 숫자는
전부 **넥슨 랭크전 전적**(병영수첩)에서만 나온다.  이 모듈은 I/O 가 없는
순수 함수만 모아 두어 리눅스에서 그대로 단위 테스트한다.

주요 함수
---------
``band_of`` / ``party_band_of``   RP → 공식 티어 구간
``friend_compare``                나 vs 친구 (저장된 값만, API 0건)
``play_grass``                    서든어택 플레이 잔디 (12주 × 7일)
``weak_maps``                     약점 맵 TOP N
``recent_form``                   최근 N판 승률·K/D·연승
``barracks_digest``               직전 최적화일 대비 병영수첩 변화
``play_coaching``                 최근 100판 실제 수치 기반 피드백
``map_guides``                    약점 맵 연습 포인트
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable, Sequence

UNRANKED_IMG = "solo_100"

# 공식 티어 구간 (RP) — 병영수첩 CDN 의 원형 마크 번호와 1:1 로 맞춘다.
SOLO_BANDS: list[dict[str, Any]] = [
    {"min": 0, "n": "언랭크", "img": UNRANKED_IMG},
    {"min": 1, "n": "브론즈", "img": "solo_101"},
    {"min": 1000, "n": "실버", "img": "solo_103"},
    {"min": 1400, "n": "골드", "img": "solo_105"},
    {"min": 1800, "n": "플래티넘", "img": "solo_107"},
    {"min": 2200, "n": "다이아몬드", "img": "solo_109"},
    {"min": 2600, "n": "마스터", "img": "solo_111"},
    {"min": 3000, "n": "그랜드마스터", "img": "solo_113"},
    {"min": 3400, "n": "불레전드", "img": "solo_115"},
    {"min": 3700, "n": "별레전드", "img": "solo_117"},
]
PARTY_BANDS: list[dict[str, Any]] = [
    {"min": 0, "n": "언랭크", "img": UNRANKED_IMG},
    {"min": 1, "n": "브론즈", "img": "party_10"},
    {"min": 1300, "n": "실버", "img": "party_11"},
    {"min": 1700, "n": "골드", "img": "party_12"},
    {"min": 2100, "n": "플래티넘", "img": "party_13"},
    {"min": 2500, "n": "다이아몬드", "img": "party_14"},
    {"min": 2900, "n": "마스터", "img": "party_15"},
    {"min": 3300, "n": "그랜드마스터", "img": "party_16"},
    {"min": 3600, "n": "불레전드", "img": "party_17"},
    {"min": 3900, "n": "별레전드", "img": "party_18"},
]


def num(value: Any, default: float | None = None) -> float | None:
    """문자열·None 섞인 넥슨 응답을 안전하게 float 로."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------- 티어
def band_of(rp: int | None) -> dict[str, Any]:
    if not rp or rp <= 0:
        return dict(SOLO_BANDS[0])
    band = SOLO_BANDS[1]
    for row in SOLO_BANDS:
        if rp >= row["min"]:
            band = row
    return dict(band)


def party_band_of(rp: int | None) -> dict[str, Any]:
    if not rp or rp <= 0:
        return dict(PARTY_BANDS[0])
    band = PARTY_BANDS[1]
    for row in PARTY_BANDS:
        if rp >= row["min"]:
            band = row
    return dict(band)


def band_progress(rp: int | None, party: bool = False) -> dict[str, Any]:
    """현재 구간 안 진행률 — 병영수첩 랭크 카드의 게이지."""
    bands = PARTY_BANDS if party else SOLO_BANDS
    rp = int(rp or 0)
    band = band_of(rp) if not party else party_band_of(rp)
    start = max((b["min"] for b in bands if rp >= b["min"]), default=0)
    nxt = next((b for b in bands if b["min"] > start), None)
    end = nxt["min"] if nxt else start + 300
    pct = 0.0 if end <= start else clamp((rp - start) / (end - start) * 100)
    return {"band": band["n"], "start": start, "end": end,
            "next": nxt["n"] if nxt else None,
            "remain": max(0, end - rp) if nxt else 0, "pct": pct}


# ---------------------------------------------------------------- 비교
def friend_compare(me: dict[str, Any] | None, friend: dict[str, Any] | None) -> dict[str, Any]:
    """나 vs 친구 — 이미 저장된 순위표/병영수첩 값만 쓴다(추가 API 0건)."""
    me = me or {}
    friend = friend or {}

    def row(key: str, label: str, unit: str, *, higher_better: bool = True) -> dict[str, Any]:
        a_raw, b_raw = me.get(key), friend.get(key)
        a, b = num(a_raw), num(b_raw)
        diff = None if (a is None or b is None) else round(a - b, 1)
        better = "none"
        if a is not None and b is not None:
            if a == b:
                better = "tie"
            elif (a > b) == higher_better:
                better = "me"
            else:
                better = "friend"
        return {"key": key, "label": label, "unit": unit, "me": a_raw, "friend": b_raw,
                "diff": diff, "better": better}

    def tier_text(row_: dict[str, Any], key: str) -> Any:
        name, rp = row_.get(f"{key}_tier"), num(row_.get(key))
        if name and rp:
            return f"{name} · {int(rp):,} RP"
        if rp:
            return f"{int(rp):,} RP"
        return name or "-"

    weapon_label = {"sniper": "스나", "rifle": "라플"}
    rows = [
        row("solo", "솔로 RP", "RP"),
        row("party", "파티 RP", "RP"),
        row("win", "최근 100판 승률", "%"),
        row("kd", "K/D", "%"),
        row("dmg", "평균 딜량", ""),
        {"key": "weapon", "label": "주무기 성향", "unit": "", "text": True,
         "me": weapon_label.get(str(me.get("weapon") or ""), "-"),
         "friend": weapon_label.get(str(friend.get("weapon") or ""), "-"),
         "diff": None, "better": "none"},
    ]
    me_wins = sum(1 for r in rows[:5] if r["better"] == "me")
    friend_wins = sum(1 for r in rows[:5] if r["better"] == "friend")
    return {
        "me": {"name": me.get("name") or "나", "clan": me.get("clan") or "-",
               "solo_tier": tier_text(me, "solo"), "party_tier": tier_text(me, "party"),
               "solo": me.get("solo"), "party": me.get("party"),
               "solo_tier_name": me.get("solo_tier"), "party_tier_name": me.get("party_tier"),
               "updated_at": me.get("updated_at")},
        "friend": {"name": friend.get("name") or "친구", "clan": friend.get("clan") or "-",
                   "solo_tier": tier_text(friend, "solo"), "party_tier": tier_text(friend, "party"),
                   "solo": friend.get("solo"), "party": friend.get("party"),
                   "solo_tier_name": friend.get("solo_tier"), "party_tier_name": friend.get("party_tier"),
                   "updated_at": friend.get("updated_at")},
        "rows": rows,
        "score": {"me": me_wins, "friend": friend_wins},
        "verdict": ("내가 우세" if me_wins > friend_wins else
                    "친구가 우세" if friend_wins > me_wins else "접전"),
        "note": "저장된 전적 값으로만 비교합니다",
    }


# ---------------------------------------------------------------- 잔디 · 최근 폼
def _match_day(match: dict[str, Any]) -> str:
    text = str(match.get("date") or match.get("date_match") or "")
    if len(text) >= 10:
        return text[:10]
    stamp = str(match.get("time") or "")
    return stamp[:10]


def _is_win(match: dict[str, Any]) -> bool:
    return str(match.get("result") or "") in ("승", "WIN", "win", "1", "True", "true")


def play_grass(matches: Sequence[dict[str, Any]], days: dict[str, Any] | None = None,
               *, weeks: int = 12, today: _dt.date | None = None) -> dict[str, Any]:
    """서든어택 플레이 잔디 — 날짜별 판 수·승 수.

    ``matches`` 는 병영수첩 최근 경기(최대 100판), ``days`` 는 저장해 둔 누적
    기록({date: {games, wins}})이다.  두 쪽을 합쳐 최근 ``weeks`` 주를 그린다.
    """
    today = today or _dt.date.today()
    table: dict[str, dict[str, int]] = {}
    for day, slot in (days or {}).items():
        key = str(day)[:10]
        if not key:
            continue
        table[key] = {"games": int((slot or {}).get("games") or 0),
                      "wins": int((slot or {}).get("wins") or 0)}
    # 같은 판을 누적 기록과 최근 경기 양쪽에서 세지 않는다 — 날짜별 max
    fresh: dict[str, dict[str, int]] = {}
    for match in matches or []:
        day = _match_day(match)
        if not day:
            continue
        slot = fresh.setdefault(day, {"games": 0, "wins": 0})
        slot["games"] += 1
        slot["wins"] += 1 if _is_win(match) else 0
    for day, slot in fresh.items():
        old = table.get(day) or {"games": 0, "wins": 0}
        table[day] = {"games": max(int(old.get("games") or 0), slot["games"]),
                      "wins": max(int(old.get("wins") or 0), slot["wins"])}

    # 12주 = 정확히 weeks*7 칸 (오늘까지). 월요일 정렬로 칸이 늘어나지 않게 한다.
    start = today - _dt.timedelta(days=weeks * 7 - 1)
    cells: list[dict[str, Any]] = []
    total = wins = 0
    active_days = 0
    cursor = start
    while cursor <= today:
        key = cursor.isoformat()
        slot = table.get(key) or {"games": 0, "wins": 0}
        games = int(slot.get("games") or 0)
        total += games
        wins += int(slot.get("wins") or 0)
        active_days += 1 if games else 0
        level = 0
        if games >= 1:
            level = 1
        if games >= 3:
            level = 2
        if games >= 6:
            level = 3
        if games >= 10:
            level = 4
        cells.append({"date": key, "games": games, "wins": int(slot.get("wins") or 0),
                      "level": level, "weekday": cursor.weekday()})
        cursor += _dt.timedelta(days=1)
    return {"weeks": weeks, "cells": cells, "start": start.isoformat(), "end": today.isoformat(),
            "total_games": total, "wins": wins, "active_days": active_days,
            "win": round(wins / total * 100, 1) if total else None}


def recent_form(matches: Sequence[dict[str, Any]], count: int = 8) -> dict[str, Any]:
    """최근 N판 — 승/패, K/D, 딜량, 연승·연패."""
    rows = list(matches or [])[:max(1, int(count or 8))]
    if not rows:
        return {"available": False, "games": 0, "wins": 0, "win": None, "kd": None,
                "dmg": None, "streak": 0, "streak_kind": None, "label": "표본 없음"}
    wins = sum(1 for m in rows if _is_win(m))
    kills = sum(int(num(m.get("k"), 0) or 0) for m in rows)
    deaths = sum(int(num(m.get("d"), 0) or 0) for m in rows)
    dmg = [num(m.get("damage"), 0) or 0 for m in rows]
    streak, kind = 0, None
    for m in rows:
        won = _is_win(m)
        if kind is None:
            kind = "win" if won else "lose"
        if (won and kind == "win") or (not won and kind == "lose"):
            streak += 1
        else:
            break
    return {
        "available": True, "games": len(rows), "wins": wins,
        "win": round(wins / len(rows) * 100, 1),
        "kd": round(kills / (kills + deaths) * 100, 1) if (kills + deaths) else None,
        "dmg": round(sum(dmg) / len(dmg)) if dmg else None,
        "streak": streak, "streak_kind": kind,
        "label": f"{streak}{'연승' if kind == 'win' else '연패'}" if streak >= 2 else
                 f"최근 {len(rows)}판 {wins}승 {len(rows) - wins}패",
    }


def weak_maps(maps: Sequence[dict[str, Any]], limit: int = 3, min_games: int = 1) -> list[dict[str, Any]]:
    """약점 맵 TOP N — 병영수첩 맵 전적에서 승률·K/D 가 낮은 순.

    표본이 ``min_games`` 미만인 맵은 제외한다(1판 진 맵을 '약점'이라 부르지 않는다).
    """
    rows = [dict(m) for m in (maps or []) if int(num(m.get("games"), 0) or 0) >= max(1, min_games)]
    if not rows:
        rows = [dict(m) for m in (maps or []) if num(m.get("win")) is not None][:0]
    for row in rows:
        win = num(row.get("win")) or 0.0
        kd = num(row.get("kd")) or 0.0
        games = int(num(row.get("games"), 0) or 0)
        # 승률 55% · K/D 50% 를 기준선으로 두고, 표본이 적으면 점수를 완만하게.
        score = (55 - win) * 0.6 + (50 - kd) * 0.4
        score = score * min(1.0, 0.6 + games / 20)
        row["weak_score"] = round(score, 1)
        row["win"] = round(win, 1)
        row["kd"] = round(kd, 1) if row.get("kd") is not None else None
    rows.sort(key=lambda r: (-(r["weak_score"]), int(num(r.get("games"), 0) or 0) * -1))
    return rows[:max(1, int(limit or 3))]


# ---------------------------------------------------------------- 다이제스트
def barracks_digest(snapshots: Sequence[dict[str, Any]], today: _dt.date | None = None) -> dict[str, Any]:
    """오늘 첫 실행용 — 직전 최신화일 대비 병영수첩 변화 + 한마디 충고."""
    today = today or _dt.date.today()
    rows = [s for s in snapshots if s.get("stats")]
    if not rows:
        return {"available": False, "reason": "병영수첩을 아직 불러오지 않았습니다"}
    rows = sorted(rows, key=lambda s: (str(s.get("date") or ""), str(s.get("at") or "")))
    current = rows[-1]
    cur_date = str(current.get("date") or "")
    prev = None
    for snap in reversed(rows[:-1]):
        if str(snap.get("date") or "") < cur_date:
            prev = snap
            break
    if prev is None:
        return {"available": False, "reason": "비교할 이전 최신화 기록이 없습니다",
                "current_date": cur_date, "nickname": current.get("nickname")}

    def stat(snap: dict[str, Any], key: str) -> float | None:
        return num((snap.get("stats") or {}).get(key))

    def rp(snap: dict[str, Any], mode: str) -> float | None:
        return num(((snap.get("tier") or {}).get(mode) or {}).get("rp"))

    metrics = []
    for key, label, unit in (("win", "승률", "%"), ("kd", "K/D", "%"), ("dmg", "평균 딜량", "")):
        before, after = stat(prev, key), stat(current, key)
        metrics.append({"key": key, "label": label, "unit": unit, "before": before, "after": after,
                        "diff": None if (before is None or after is None) else round(after - before, 1)})
    for mode, label in (("solo", "솔로 RP"), ("party", "파티 RP")):
        before, after = rp(prev, mode), rp(current, mode)
        metrics.append({"key": mode, "label": label, "unit": "RP", "before": before, "after": after,
                        "diff": None if (before is None or after is None) else round(after - before, 0)})

    ups = [m for m in metrics if (m["diff"] or 0) > 0]
    downs = [m for m in metrics if (m["diff"] or 0) < 0]

    def fmt(m: dict[str, Any]) -> str:
        v = abs(m["diff"] or 0)
        return f"{m['label']} {'+' if (m['diff'] or 0) > 0 else '-'}{v:,.1f}{m['unit']}".replace(".0%", "%")

    if ups and not downs:
        advice = f"{fmt(ups[0])} · 전반적으로 좋아졌습니다. 이 흐름을 오늘도 그대로 이어가세요."
        tone = "good"
    elif downs and not ups:
        advice = (f"{fmt(downs[0])} 흐름이 내려갔습니다. 오늘은 첫 교전을 줄이고, "
                  f"아군 뒤에서 마무리하는 쪽으로 한 판만 바꿔 보세요.")
        tone = "warn"
    elif ups and downs:
        good = max(ups, key=lambda m: abs(m["diff"] or 0))
        bad = max(downs, key=lambda m: abs(m["diff"] or 0))
        advice = f"{good['label']}은 올랐고 {bad['label']}은 내려갔습니다. 오늘은 {bad['label']}을(를) 되돌리는 데 집중하세요."
        tone = "info"
    else:
        advice = "지표가 거의 그대로입니다. 점수보다 한 가지 목표(첫 교전 각·생존)만 정해 보세요."
        tone = "info"
    if cur_date and cur_date != today.isoformat():
        advice += " (오늘 병영수첩을 한 번 최신화하면 오늘 기준으로 다시 비교합니다)"
    return {"available": True, "tone": tone, "advice": advice, "metrics": metrics,
            "from_date": prev.get("date"), "to_date": cur_date, "nickname": current.get("nickname")}


# ---------------------------------------------------------------- 플레이 코칭
def play_coaching(barracks: dict[str, Any] | None, limit: int = 4) -> list[dict[str, Any]]:
    """최근 100판(병영수첩)의 실제 수치만으로 쓰는 실전 피드백."""
    data = barracks or {}
    stats = data.get("stats") or {}
    tier = data.get("tier") or {}
    maps = data.get("maps") or []
    matches = data.get("matches") or []
    recent = data.get("recent") or {}

    games = int(num(stats.get("games"), 0) or 0)
    win = num(stats.get("win"))
    kd = num(stats.get("kd"))
    dmg = num(stats.get("dmg"))
    kills = int(num(stats.get("kills"), 0) or 0)
    deaths = int(num(stats.get("deaths"), 0) or 0)
    if games <= 0 or (win is None and kd is None):
        return []

    solo = num((tier.get("solo") or {}).get("rp"), 0) or 0.0
    party = num((tier.get("party") or {}).get("rp"), 0) or 0.0
    tips: list[dict[str, Any]] = []

    def fmt(value: float | None, unit: str = "") -> str:
        if value is None:
            return "—"
        return f"{value:.1f}%" if unit == "%" else f"{value:,.0f}"

    strong = ((win is not None and win >= 52) or (kd is not None and kd >= 55)
              or (dmg is not None and dmg >= 1100) or (win is not None and win >= 48))
    damage_without_wins = (dmg is not None and dmg >= 1000 and win is not None and win < 47)

    if damage_without_wins:
        tips.append({
            "icon": "🎯", "tone": "warn",
            "title": f"딜량 {dmg:,.0f} · 승률 {win:.1f}%",
            "text": (f"평균 딜량 {dmg:,.0f}인데 승률이 {win:.1f}%입니다. 딜은 넣고 있으니 에임 문제가 아니라 "
                     f"라운드 마무리 문제입니다 — 킬을 잡은 직후 자리를 바꿔 다음 각을 미리 잡고, "
                     f"설치·해체 라운드는 킬보다 목표에 시간을 쓰세요."),
        })
    elif kd is not None and kd >= 55 and win is not None:
        tips.append({
            "icon": "🏅", "tone": "good",
            "title": f"K/D {kd:.1f}% · {kills:,}킬 / {deaths:,}데스",
            "text": (f"K/D {kd:.1f}%면 교전 자체는 이기고 있습니다. 지금처럼 확실한 각에서만 먼저 쏘고, "
                     f"빗나가면 바로 물러나는 리듬을 유지하세요. 감도는 건드리지 마세요."),
        })
    elif win is not None and win >= 52:
        tips.append({
            "icon": "🔥", "tone": "good",
            "title": f"승률 {win:.1f}% · 최근 {games}판",
            "text": (f"{games}판 승률 {win:.1f}%는 유지할 가치가 있는 수치입니다. "
                     f"첫 교전 타이밍과 시작 위치를 그대로 두고, 같은 루틴을 반복하세요."),
        })
    elif kd is not None and kd < 48:
        tips.append({
            "icon": "🛡️", "tone": "warn",
            "title": f"K/D {kd:.1f}% · 데스 {deaths:,}",
            "text": (f"K/D {kd:.1f}%는 데스가 먼저 나오는 흐름입니다. 킬 욕심보다 생존을 먼저 잡으세요 — "
                     f"첫 교전은 아군이 만든 뒤에 붙고, 한 번 쏘고 맞으면 바로 물러나는 것만 지켜도 수치가 올라갑니다."),
        })
    elif dmg is not None:
        tips.append({
            "icon": "💥", "tone": "info",
            "title": f"평균 딜량 {dmg:,.0f} · K/D {fmt(kd, '%')} · 승률 {fmt(win, '%')}",
            "text": (f"딜량 {dmg:,.0f}과 승률 {fmt(win, '%')}를 같이 보면, 지금은 딜을 넣는 구간과 "
                     f"라운드를 끝내는 구간이 따로 놀고 있습니다. 킬 직후 3초만 자리를 바꿔 보세요."),
        })

    pool = [m for m in maps if int(num(m.get("games"), 0) or 0) >= 5]
    worst = None
    if pool:
        worst = min(pool, key=lambda m: (num(m.get("kd"), 999) if num(m.get("kd")) is not None else 999,
                                         -(num(m.get("weak"), 0) or 0)))
    w_kd = num(worst.get("kd")) if worst else None
    w_win = num(worst.get("win")) if worst else None
    if worst and strong and (w_win or 0) >= 50 and (w_kd or 0) >= 48:
        tips.append({
            "icon": "🧱", "tone": "good",
            "title": f"5판 이상 {len(pool)}개 맵 모두 승률 {(w_win or 0):.0f}% 이상",
            "text": "맵별 편차가 작습니다. 특정 맵만 무너지는 유형이 아니니 지금 루틴을 그대로 반복하는 쪽이 이득입니다.",
        })
        worst = None
    if worst:
        tips.append({
            "icon": "🗺️", "tone": "warn" if not strong else "info",
            "title": f"{worst.get('map')} {int(num(worst.get('games'), 0) or 0)}판 · 승률 {fmt(w_win, '%')} · K/D {fmt(w_kd, '%')}",
            "text": (f"{len(pool)}개 맵 중 이 맵만 승률 {fmt(w_win, '%')} · K/D {fmt(w_kd, '%')}입니다. "
                     f"이 맵에서는 첫 교전을 아군 뒤로 미루고, 라운드마다 같은 자리에서 시작해 "
                     f"상대 진입 각을 먼저 받는 쪽으로 바꿔 보세요."),
        })

    if pool and strong:
        best = max(pool, key=lambda m: (num(m.get("win"), 0) or 0, num(m.get("kd"), 0) or 0))
        if worst is None or str(best.get("map")) != str(worst.get("map")):
            b_win = num(best.get("win"), 0) or 0
            b_kd = num(best.get("kd"), 0) or 0
            if b_win >= 50 or b_kd >= 52:
                tips.append({
                    "icon": "✅", "tone": "good",
                    "title": f"{best.get('map')} 승률 {fmt(b_win, '%')} · K/D {fmt(b_kd, '%')}",
                    "text": ("이 맵은 이깁니다. 여기서는 먼저 각을 만들고 킬을 주도하세요 — "
                             "자신 있는 맵에서 리듬을 타면 다음 맵 첫 라운드까지 살아납니다."),
                })

    if len(tips) < 4 and solo > 0 and party > 0 and abs(solo - party) >= 100:
        gap = abs(solo - party)
        if solo < party:
            tips.append({
                "icon": "👥", "tone": "info",
                "title": f"솔로 {solo:,.0f} vs 파티 {party:,.0f} · {gap:,.0f}RP 차",
                "text": (f"솔로에서만 {gap:,.0f}RP를 잃고 있습니다. 솔로는 아군과 합을 맞출 수 없으니 "
                         f"첫 교전을 직접 열지 말고, 아군이 먼저 붙은 뒤 2선에서 마무리하는 쪽이 안전합니다."),
            })
        else:
            tips.append({
                "icon": "👥", "tone": "info",
                "title": f"파티 {party:,.0f} vs 솔로 {solo:,.0f} · {gap:,.0f}RP 차",
                "text": (f"파티에서 {gap:,.0f}RP가 더 낮습니다. 파티는 인원이 많아 역할이 겹치기 쉽습니다 — "
                         f"진입 1명·엄호 1명으로 미리 정하고, 같은 곳에 두 명이 같이 들어가지 않게 하세요."),
            })

    last5 = list(matches[:5])
    wins5 = sum(1 for m in last5 if _is_win(m))
    if len(tips) < 4 and len(last5) >= 5 and wins5 <= 2:
        tips.append({
            "icon": "⚡", "tone": "warn",
            "title": f"최근 5판 {wins5}승 {5 - wins5}패",
            "text": ("연패 구간입니다. 지금은 감도나 무기를 바꿀 때가 아니라 판 수를 줄일 때입니다 — "
                     "2연패면 한 판 쉬는 규칙을 걸어 두세요."),
        })
    elif len(tips) < 4 and len(last5) >= 5 and wins5 >= 4:
        tips.append({
            "icon": "📈", "tone": "good",
            "title": f"최근 5판 {wins5}승 {5 - wins5}패",
            "text": "최근 5판 흐름이 좋습니다. 이 리듬에서는 세팅을 바꾸지 말고 같은 판 수를 유지하세요.",
        })

    if len(tips) < 3:
        sniper = num(recent.get("sniper"), 0) or 0.0
        assault = num(recent.get("assault"), 0) or 0.0
        total_w = sniper + assault
        if total_w:
            share = sniper / total_w * 100
            tips.append({
                "icon": "🎯", "tone": "info",
                "title": f"스나 {share:.0f}% · 라플 {100 - share:.0f}%",
                "text": ("스나 비중이 높습니다. 한 발 먼저 꽂고, 빗나가면 재조준 대신 물러나 각을 다시 잡으세요."
                         if share >= 55 else
                         "라플 비중이 높습니다. 연사는 짧게 끊어 쏘고 헤드라인 높이를 유지하세요. "
                         "긴 거리 교전은 아군 스나에게 넘기세요."),
            })
    if len(tips) < 3 and games:
        tips.append({
            "icon": "🎮", "tone": "info",
            "title": f"최근 {games}판 표본",
            "text": ("표본이 더 쌓이면 맵별 조언이 정확해집니다. 전적을 한 번 더 최신화하고, "
                     "같은 맵을 3판 연속으로 돌면 지표가 눈에 보이게 바뀝니다."),
        })
    return tips[:max(1, min(4, int(limit or 4)))]


# ---------------------------------------------------------------- 맵 가이드
def map_family(name: str) -> str:
    text = str(name or "")
    if "폭파" in text:
        return "폭파"
    if "심플" in text:
        return "심플"
    return "일반"


def callouts_for(name: str) -> list[str]:
    """맵 이름 → 확인된 콜아웃. 폭파 계열만 A/B 폭파지점을 쓴다."""
    text = str(name or "")
    if any(word in text for word in ("폭파", "심플")) or map_family(text) == "폭파":
        return ["A", "B"]
    return []


def youtube_search(query: str) -> str:
    from urllib.parse import quote_plus

    return "https://www.youtube.com/results?search_query=" + quote_plus(query)


def map_spot_tips(name: str) -> list[str]:
    """맵과 무관하게 통하는 실제 전술만 (지형 이름을 지어내지 않는다)."""
    return [
        "라운드 시작 10초는 정보만 모으고, 아군 1픽이 자리를 잡은 뒤에 첫 교전을 받으세요.",
        "혼자 각을 열지 말고 2:1이 되는 쪽으로만 들어가세요.",
        "스나 1픽 자리를 비워 두면 라플이 먼저 끊깁니다 — 라플은 2선 백업부터 하세요.",
        "설치·해체 라운드는 서는 자리를 미리 정해 두고 같은 자리를 반복하세요.",
    ]


def map_guide(row: dict[str, Any]) -> dict[str, Any]:
    """약점 맵 클릭 → 맵 연습 포인트 + 유튜브 공략 검색 링크."""
    name = str((row or {}).get("map") or "")
    clean = name.split("-")[0].split("(")[0].strip() or name
    fam = map_family(name)
    kd = num((row or {}).get("kd"))
    win = num((row or {}).get("win"))
    dmg = num((row or {}).get("dmg"))
    games = int(num((row or {}).get("games"), 0) or 0)
    tips: list[str] = []
    if kd is not None and kd < 48:
        tips.append(f"K/D {kd:.1f}% · {games}판 — 교전에서 먼저 죽는 비율이 높습니다. "
                    f"첫 교전을 직접 열지 말고 아군 뒤에서 마무리부터 하세요.")
    elif win is not None and win < 45:
        tips.append(f"승률 {win:.1f}% · {games}판 — 라운드 시작에 바로 나가기보다 "
                    f"10초는 정보를 모은 뒤 움직이세요.")
    elif win is not None and kd is not None:
        tips.append(f"{games}판 · 승률 {win:.1f}% · K/D {kd:.1f}% — 지금 페이스는 유지하되, "
                    f"각을 먼저 만들고 킬을 주도하세요.")
    else:
        tips.append(f"{games}판 표본입니다. 판 수가 쌓이면 승률·K/D 기준으로 조언이 정확해집니다.")
    if dmg is not None and dmg < 900:
        tips.append(f"평균 딜량 {dmg:,.0f} — 붙어서 쏘는데 마무리가 약합니다. "
                    f"한 발 쏘고 바로 물러나는 끊어 쏘기로 바꾸세요.")
    elif dmg is not None and win is not None and dmg >= 1000 and win < 47:
        tips.append(f"딜량 {dmg:,.0f} · 승률 {win:.1f}% — 딜은 나오는데 라운드 마무리가 약합니다. "
                    f"킬 직후 자리를 바꿔 다음 각을 미리 잡으세요.")
    tips.extend(map_spot_tips(name))
    if fam == "폭파":
        tips.append("폭파전은 설치·해체 각이 승패를 가릅니다. 설치 지점 2곳, 해체를 끊는 자리 2곳만 정해 두고 반복하세요.")
    query = f"서든어택 {clean} 공략"
    return {
        "map": name, "clean": clean, "family": fam, "games": games,
        "callouts": callouts_for(name),
        "stats": {"win": win, "kd": kd, "dmg": dmg, "weak": (row or {}).get("weak")},
        "tips": tips[:5], "image": "",
        "youtube": {"label": "영상 보기", "search": youtube_search(query), "url": youtube_search(query)},
    }


def map_guides(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    from . import v150

    out = []
    for row in rows or []:
        guide = map_guide(row)
        guide["image"] = v150.map_image(guide["map"])
        out.append(guide)
    return out


def coach_lines(barracks: dict[str, Any] | None, limit: int = 4) -> list[dict[str, Any]]:
    """호환용 이름 — play_coaching 과 같다."""
    return play_coaching(barracks, limit)


def merge_play_days(existing: dict[str, Any] | None,
                    matches: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """병영수첩 경기를 누적 플레이 기록({date: {games, wins}})에 합친다.

    같은 판을 여러 번 최신화해도 날짜별 값이 줄지 않게 max 로 합친다.
    """
    table: dict[str, dict[str, int]] = {}
    for day, slot in (existing or {}).items():
        key = str(day)[:10]
        if key:
            table[key] = {"games": int((slot or {}).get("games") or 0),
                          "wins": int((slot or {}).get("wins") or 0)}
    fresh: dict[str, dict[str, int]] = {}
    for match in matches or []:
        day = _match_day(match)
        if not day:
            continue
        slot = fresh.setdefault(day, {"games": 0, "wins": 0})
        slot["games"] += 1
        slot["wins"] += 1 if _is_win(match) else 0
    for day, slot in fresh.items():
        old = table.get(day) or {"games": 0, "wins": 0}
        table[day] = {"games": max(int(old.get("games") or 0), slot["games"]),
                      "wins": max(int(old.get("wins") or 0), slot["wins"])}
    # 최근 1년만 남긴다
    cutoff = (_dt.date.today() - _dt.timedelta(days=365)).isoformat()
    return {k: v for k, v in sorted(table.items()) if k >= cutoff}
