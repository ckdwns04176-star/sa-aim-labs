"""Scoring engine: 5 aim areas, 18-tier ladder, 40-scenario rotation.

The numbers mirror the "코박스 에임 성장일기" spreadsheet the user built
(18 tiers of 0 ~ 3,600+ points, 5 areas x 8 scenarios = 40 KovaaK's scenarios,
Aim Lab 5 + 5).  Everything here is pure Python with no I/O so it can be unit
tested and reused from the JS side through the bridge.
"""
from __future__ import annotations

import datetime as _dt
import statistics
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------- areas
AREAS: list[dict[str, str]] = [
    {"key": "precision", "name": "정밀 플릭", "desc": "작은 타겟 정지 사격 · 첫 발 정확도", "cls": "", "color": "#F59E0B"},
    {"key": "wide", "name": "와이드 플릭", "desc": "넓은 화면 드래그 · 오버플릭 제어", "cls": "w", "color": "#38BDF8"},
    {"key": "micro", "name": "마이크로 보정", "desc": "미세 조정 · 트래킹 중 보정", "cls": "m", "color": "#EF4444"},
    {"key": "track", "name": "트래킹·무빙", "desc": "움직이는 타겟 유지 · 무빙샷", "cls": "t", "color": "#34D399"},
    {"key": "switch", "name": "타겟 스위칭", "desc": "연속 타겟 전환 속도", "cls": "s", "color": "#A98BFF"},
]
AREA_KEYS = [a["key"] for a in AREAS]
AREA_INDEX = {a["key"]: i for i, a in enumerate(AREAS)}

# ---------------------------------------------------------------- tiers
# cut = tier start score.  img = official KR circular mark shipped in ui/assets.
TIERS: list[dict[str, Any]] = [
    {"n": "브론즈 I", "cut": 0, "c": "#C08552", "fam": 0, "img": "solo_101"},
    {"n": "브론즈 II", "cut": 800, "c": "#C08552", "fam": 0, "img": "solo_102"},
    {"n": "브론즈 III", "cut": 1000, "c": "#C08552", "fam": 0, "img": "solo_103"},
    {"n": "실버 I", "cut": 1200, "c": "#C7D2DC", "fam": 1, "img": "solo_104"},
    {"n": "실버 II", "cut": 1400, "c": "#C7D2DC", "fam": 1, "img": "solo_105"},
    {"n": "실버 III", "cut": 1600, "c": "#C7D2DC", "fam": 1, "img": "solo_106"},
    {"n": "골드 I", "cut": 1800, "c": "#E8B84B", "fam": 2, "img": "solo_107"},
    {"n": "골드 II", "cut": 2000, "c": "#E8B84B", "fam": 2, "img": "solo_108"},
    {"n": "골드 III", "cut": 2200, "c": "#E8B84B", "fam": 2, "img": "solo_109"},
    {"n": "마스터 I", "cut": 2400, "c": "#4C9AFF", "fam": 3, "img": "solo_110"},
    {"n": "마스터 II", "cut": 2600, "c": "#4C9AFF", "fam": 3, "img": "solo_111"},
    {"n": "마스터 III", "cut": 2800, "c": "#4C9AFF", "fam": 3, "img": "solo_112"},
    {"n": "그랜드마스터 I", "cut": 3000, "c": "#A98BFF", "fam": 4, "img": "solo_113"},
    # 유효 풀(랭크전을 실제로 도는 유저) 상위 5.26% 지점
    {"n": "그랜드마스터 II", "cut": 3200, "c": "#A98BFF", "fam": 4, "img": "solo_114"},
    {"n": "그랜드마스터 III", "cut": 3400, "c": "#A98BFF", "fam": 4, "img": "solo_115", "top_pct": "5.26%"},
    {"n": "레전드", "cut": 3600, "c": "#FF9349", "fam": 5, "img": "solo_116", "top_pct": "2.1%"},
    {"n": "불레전드", "cut": 3600, "c": "#FF6B7B", "fam": 5, "img": "solo_117"},
    {"n": "별레전드", "cut": 3600, "c": "#FF6B7B", "fam": 5, "img": "solo_118"},
]
FAM_COLORS = ["#B87333", "#C7D2DC", "#E8B84B", "#4C9AFF", "#A98BFF", "#FF9349"]
UNRANKED_IMG = "solo_100"
MAX_SCORE = 3600

# 서든 랭크 밴드 (200 RP 단위, 800 / 1000 은 브론즈 구간)
SOLO_BANDS: list[dict[str, Any]] = [
    {"min": 0, "n": "브론즈 I", "img": "solo_101"},
    {"min": 800, "n": "브론즈 II", "img": "solo_102"},
    {"min": 1000, "n": "브론즈 III", "img": "solo_103"},
    {"min": 1200, "n": "실버 I", "img": "solo_104"},
    {"min": 1400, "n": "실버 II", "img": "solo_105"},
    {"min": 1600, "n": "실버 III", "img": "solo_106"},
    {"min": 1800, "n": "골드 I", "img": "solo_107"},
    {"min": 2000, "n": "골드 II", "img": "solo_108"},
    {"min": 2200, "n": "골드 III", "img": "solo_109"},
    {"min": 2400, "n": "마스터 I", "img": "solo_110"},
    {"min": 2600, "n": "마스터 II", "img": "solo_111"},
    {"min": 2800, "n": "마스터 III", "img": "solo_112"},
    {"min": 3000, "n": "그랜드마스터 I", "img": "solo_113"},
    {"min": 3200, "n": "그랜드마스터 II", "img": "solo_114"},
    {"min": 3400, "n": "그랜드마스터 III", "img": "solo_115"},
    {"min": 3600, "n": "레전드", "img": "solo_116"},
]
PARTY_BANDS: list[dict[str, Any]] = [
    {"min": 0, "n": "언랭크", "img": UNRANKED_IMG},
    {"min": 1600, "n": "실버 III", "img": "party_10"},
    {"min": 1800, "n": "골드 I", "img": "party_10"},
    {"min": 2000, "n": "골드 II", "img": "party_10"},
    {"min": 2200, "n": "골드 III", "img": "party_10"},
    {"min": 2400, "n": "마스터 I", "img": "party_10"},
    {"min": 2600, "n": "마스터 II", "img": "party_11"},
    {"min": 2800, "n": "마스터 III", "img": "party_12"},
    {"min": 3000, "n": "그랜드마스터 I", "img": "party_13"},
    {"min": 3200, "n": "그랜드마스터 II", "img": "party_14"},
    {"min": 3400, "n": "그랜드마스터 III", "img": "party_15"},
    {"min": 3600, "n": "레전드", "img": "party_16"},
]

# ---------------------------------------------------------------- scenarios
# (name, area key) — order inside each area is the recommendation priority.
KOVAAK_SCENARIOS: list[tuple[str, str]] = [
    ("1wall 6targets small", "precision"),
    ("1wall6targets TE", "precision"),
    ("VT 1w6ts Rasp Novice", "precision"),
    ("1wall 6targets small Horizontalish", "precision"),
    ("1wall 2targets small - valorant", "precision"),
    ("VT 1w4ts Novice S5", "precision"),
    ("1wall 6targets extra small", "precision"),
    ("1 wall 6 targets Micro++", "precision"),
    ("Wide Wall 3 Targets", "wide"),
    ("1w4ts Voltaic", "wide"),
    ("Wide Wall 3 Targets TE", "wide"),
    ("Wide Wall 4 Targets", "wide"),
    ("ww3t Voltaic", "wide"),
    ("Wide Wall 6Targets", "wide"),
    ("Wide Wall 3 Targets Small", "wide"),
    ("Wide Wall 6Targets Small", "wide"),
    ("Reflex Micro++ flick reload small", "micro"),
    ("1wall5targets_pasu Reload", "micro"),
    ("Micro adjust 5 targets small", "micro"),
    ("1wall5targets_pasu slow", "micro"),
    ("1w2t smallflicks small 60s", "micro"),
    ("Reflex Micro Flick 250ms", "micro"),
    ("Reflex Micro++ Flick", "micro"),
    ("Reflex Micro++ Flick Horizontalish", "micro"),
    ("Close Fast Strafes Easy Invincible", "track"),
    ("Smoothbot Voltaic Easy", "track"),
    ("Close Long Strafes Easy", "track"),
    ("Air Angelic 4 Voltaic Easy", "track"),
    ("Smoothbot Voltaic", "track"),
    ("Bounce 180 Tracking", "track"),
    ("Air Angelic 4 Voltaic", "track"),
    ("Close Fast Strafes Invincible", "track"),
    ("voxTargetSwitch", "switch"),
    ("voxTS Voltaic Easy", "switch"),
    ("voxTS Static Click rAim", "switch"),
    ("Pasu Voltaic Easy", "switch"),
    ("voxTargetClick Easy Strafe", "switch"),
    ("voxTS Voltaic", "switch"),
    ("kinTargetSwitch", "switch"),
    ("voxTargetSwitch 2 Large", "switch"),
]

AIMLAB_SCENARIOS: dict[str, list[tuple[str, str]]] = {
    "sniper": [
        ("Sixshot Ultimate", "precision"),
        ("Gridshot Precision", "wide"),
        ("Microshot Precision", "micro"),
        ("Motionshot Precision", "track"),
        ("Strafetrack Ultimate", "switch"),
    ],
    "rifle": [
        ("Gridshot Precision", "precision"),
        ("Microflex", "wide"),
        ("Headshot Precision", "micro"),
        ("Strafetrack", "track"),
        ("Switchtrack", "switch"),
    ],
}

GAMES = {"kovak": "KovaaK's", "aimlab": "Aim Lab"}
WEAPONS = {"sniper": "스나", "rifle": "라플"}

# difficulty: per-area score thresholds (0-100)
DIFF_LEVELS = ("초급", "중급", "상급")
DIFF_CUTS = (70, 85)


def scenarios_for(game: str, weapon: str) -> list[dict[str, Any]]:
    """All scenarios available for a game/weapon pair, with area + priority."""
    if game == "aimlab":
        pairs = AIMLAB_SCENARIOS.get(weapon) or AIMLAB_SCENARIOS["sniper"]
        return [{"name": n, "area": a, "area_idx": AREA_INDEX[a], "priority": i} for i, (n, a) in enumerate(pairs)]
    out: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    for name, area in KOVAAK_SCENARIOS:
        p = counters.get(area, 0)
        counters[area] = p + 1
        out.append({"name": name, "area": area, "area_idx": AREA_INDEX[area], "priority": p})
    return out


def difficulty_for(value: float) -> str:
    if value < DIFF_CUTS[0]:
        return DIFF_LEVELS[0]
    if value < DIFF_CUTS[1]:
        return DIFF_LEVELS[1]
    return DIFF_LEVELS[2]


# ---------------------------------------------------------------- basics
def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def grade_of(score: int) -> str:
    if score >= 3060:
        return "S"
    if score >= 2520:
        return "A"
    if score >= 1980:
        return "B"
    if score >= 1440:
        return "C"
    return "D"


def tier_index(score: int) -> int:
    idx = 0
    for i, t in enumerate(TIERS):
        if score >= t["cut"]:
            idx = i
    return idx


def tier_of(score: int) -> dict[str, Any]:
    return TIERS[tier_index(score)]


def next_tier(score: int) -> dict[str, Any] | None:
    i = tier_index(score)
    return TIERS[i + 1] if i + 1 < len(TIERS) else None


def band_of(rp: int | None) -> dict[str, Any]:
    if not rp or rp <= 0:
        return {"min": 0, "n": "언랭크", "img": UNRANKED_IMG}
    band = SOLO_BANDS[0]
    for b in SOLO_BANDS:
        if rp >= b["min"]:
            band = b
    return band


def party_band_of(rp: int | None) -> dict[str, Any]:
    if not rp or rp <= 0:
        return {"min": 0, "n": "언랭크", "img": UNRANKED_IMG}
    band = PARTY_BANDS[1] if len(PARTY_BANDS) > 1 else PARTY_BANDS[0]
    for b in PARTY_BANDS:
        if rp >= b["min"]:
            band = b
    return band


def band_progress(rp: int | None) -> dict[str, Any]:
    """Progress inside the current 200 RP band, used by the barracks rank cards."""
    band = band_of(rp)
    start = band["min"]
    nxt = next((b for b in SOLO_BANDS if b["min"] > start), None)
    end = nxt["min"] if nxt else start + 200
    rp = rp or 0
    pct = 0.0 if end <= start else clamp((rp - start) / (end - start) * 100, 0, 100)
    return {
        "band": band["n"],
        "start": start,
        "end": end,
        "next": nxt["n"] if nxt else None,
        "remain": max(0, end - rp) if nxt else 0,
        "pct": pct,
    }


# ---------------------------------------------------------------- training data
def _sorted_records(records: Iterable[dict[str, Any]], game: str | None = None, weapon: str | None = None) -> list[dict[str, Any]]:
    rows = [r for r in records if (game is None or r.get("game") == game) and (weapon is None or r.get("weapon") == weapon)]
    rows.sort(key=lambda r: (r.get("created_at") or r.get("date") or "", r.get("id", "")))
    return rows


def area_values(records: Sequence[dict[str, Any]], game: str = "kovak", weapon: str = "sniper", window: int = 5) -> dict[str, float]:
    """Average of the last ``window`` records per area (0-100, 1 decimal)."""
    rows = _sorted_records(records, game, weapon)[-window:]
    values: dict[str, float] = {}
    for key in AREA_KEYS:
        vals = [float(r["scores"][key]) for r in rows if r.get("scores", {}).get(key) is not None]
        values[key] = round(statistics.fmean(vals), 1) if vals else 0.0
    return values


def overall_score(values: dict[str, float]) -> int:
    if not values:
        return 0
    mean = statistics.fmean(values.get(k, 0.0) for k in AREA_KEYS)
    return int(round(clamp(mean, 0, 100) * 36))


def score_of_record(record: dict[str, Any]) -> int:
    return overall_score({k: float(record.get("scores", {}).get(k) or 0) for k in AREA_KEYS})


def latest_record(records: Sequence[dict[str, Any]], game: str, weapon: str) -> dict[str, Any] | None:
    rows = _sorted_records(records, game, weapon)
    return rows[-1] if rows else None


def scenario_history(records: Sequence[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """Every recorded attempt of one scenario, oldest first."""
    out = []
    for rec in records:
        for item in rec.get("scenarios", []):
            if item.get("name") == name and item.get("score") is not None:
                out.append({"date": rec.get("date"), "score": float(item["score"]), "record_id": rec.get("id")})
    out.sort(key=lambda x: (x["date"] or "", x["record_id"] or ""))
    return out


# ---------------------------------------------------------------- rotation
ROTATION_MEMORY_DAYS = 3  # 오늘 + 이전 3일 동안 같은 시나리오를 다시 내지 않는다


def _used_recently(history: Sequence[dict[str, Any]], game: str, weapon: str, today: _dt.date, days: int = ROTATION_MEMORY_DAYS) -> set[str]:
    floor = (today - _dt.timedelta(days=days)).isoformat()
    used: set[str] = set()
    for entry in history:
        if entry.get("game") != game or entry.get("weapon") != weapon:
            continue
        if str(entry.get("date", "")) < floor:
            continue
        used.update(entry.get("names") or [])
    return used


def build_recs(
    *,
    values: dict[str, float],
    records: Sequence[dict[str, Any]],
    game: str = "kovak",
    weapon: str = "sniper",
    history: Sequence[dict[str, Any]] = (),
    today: _dt.date | None = None,
    offset: int = 0,
    count: int = 5,
) -> list[dict[str, Any]]:
    """Pick today's non-repeating recommendations, weakest area first."""
    today = today or _dt.date.today()
    pool = scenarios_for(game, weapon)
    used = _used_recently(history, game, weapon, today)
    today_record = next((r for r in _sorted_records(records, game, weapon) if r.get("date") == today.isoformat()), None)
    done_names = {i.get("name") for i in (today_record or {}).get("scenarios", [])}

    # weakest area first, then stable area order
    order = sorted(range(len(AREAS)), key=lambda i: (values.get(AREA_KEYS[i], 0.0), i))
    picked: list[dict[str, Any]] = []
    for area_idx in order:
        key = AREA_KEYS[area_idx]
        candidates = [s for s in pool if s["area_idx"] == area_idx]
        fresh = [s for s in candidates if s["name"] not in used]
        if not fresh:
            fresh = candidates
        if not fresh:
            continue
        # offset rotates through the fresh list when the user asks for other recs
        choice = fresh[offset % len(fresh)]
        hist = scenario_history(records, choice["name"])
        last = hist[-1]["score"] if hist else None
        prev = hist[-2]["score"] if len(hist) > 1 else None
        delta = round(last - prev, 1) if (last is not None and prev is not None) else None
        picked.append({
            "name": choice["name"],
            "area": area_idx,
            "area_key": key,
            "area_name": AREAS[area_idx]["name"],
            "diff": difficulty_for(values.get(key, 0.0)),
            "score": None if last is None else round(last, 1),
            "delta": delta,
            "done": choice["name"] in done_names,
            "attempts": len(hist),
        })
        if len(picked) >= count:
            break
    return picked


def rotation_entry(recs: Sequence[dict[str, Any]], game: str, weapon: str, today: _dt.date) -> dict[str, Any]:
    return {
        "date": today.isoformat(),
        "game": game,
        "weapon": weapon,
        "names": [r["name"] for r in recs],
    }


def push_rotation(history: Sequence[dict[str, Any]], entry: dict[str, Any], keep_days: int = 30) -> list[dict[str, Any]]:
    rows = [h for h in history if not (h.get("date") == entry["date"] and h.get("game") == entry["game"] and h.get("weapon") == entry["weapon"])]
    rows.append(entry)
    rows.sort(key=lambda h: str(h.get("date", "")))
    return rows[-keep_days * 4:]


# ---------------------------------------------------------------- streaks & trend
def record_dates(records: Sequence[dict[str, Any]]) -> set[str]:
    return {str(r.get("date")) for r in records if r.get("date")}


def streak(records: Sequence[dict[str, Any]], today: _dt.date | None = None) -> dict[str, Any]:
    today = today or _dt.date.today()
    dates = sorted(record_dates(records))
    if not dates:
        return {"current": 0, "best": 0, "week": 0, "last": None, "days7": [False] * 7}
    dset = set(dates)

    def run_back(start: _dt.date) -> int:
        n = 0
        cur = start
        while cur.isoformat() in dset:
            n += 1
            cur -= _dt.timedelta(days=1)
        return n

    current = run_back(today)
    if current == 0:
        current = run_back(today - _dt.timedelta(days=1))

    seq = sorted(_dt.date.fromisoformat(d) for d in dset)
    run = 1
    best = 1
    for a, b in zip(seq, seq[1:]):
        if (b - a).days == 1:
            run += 1
            best = max(best, run)
        else:
            run = 1
    week_start = today - _dt.timedelta(days=6)
    week = sum(1 for d in dset if week_start.isoformat() <= d <= today.isoformat())
    days7 = [(today - _dt.timedelta(days=6 - i)).isoformat() in dset for i in range(7)]
    return {"current": current, "best": max(best, current), "week": week, "last": max(dates), "days7": days7}


def daily_scores(records: Sequence[dict[str, Any]], days: int = 7, today: _dt.date | None = None) -> list[dict[str, Any]]:
    today = today or _dt.date.today()
    out = []
    for i in range(days - 1, -1, -1):
        day = (today - _dt.timedelta(days=i)).isoformat()
        rows = [r for r in records if r.get("date") == day]
        out.append({
            "date": day,
            "score": int(round(statistics.fmean(score_of_record(r) for r in rows))) if rows else None,
            "records": len(rows),
        })
    return out


def user_style(records: Sequence[dict[str, Any]], days: int = 30, today: _dt.date | None = None) -> dict[str, Any]:
    """스나 vs 라플 훈련 횟수 비교 (최근 30일) → 자동 무기 판별."""
    today = today or _dt.date.today()
    floor = (today - _dt.timedelta(days=days - 1)).isoformat()
    counts = {"sniper": 0, "rifle": 0}
    for r in records:
        if str(r.get("date", "")) < floor:
            continue
        w = r.get("weapon")
        if w in counts:
            counts[w] += 1
    is_sniper = counts["sniper"] >= counts["rifle"]
    return {
        "is_sniper": is_sniper,
        "label": "스나 유저" if is_sniper else "라플 유저",
        "sniper": counts["sniper"],
        "rifle": counts["rifle"],
    }


def summary(records: Sequence[dict[str, Any]], game: str, weapon: str, today: _dt.date | None = None) -> dict[str, Any]:
    """Everything the dashboard needs for one game/weapon selection."""
    today = today or _dt.date.today()
    values = area_values(records, game, weapon)
    score = overall_score(values)
    tier = tier_of(score)
    nxt = next_tier(score)
    daily = daily_scores(records, 7, today)
    filled = [d for d in daily if d["score"] is not None]
    week_delta = None
    if len(filled) >= 2:
        week_delta = filled[-1]["score"] - filled[0]["score"]
    return {
        "values": values,
        "score": score,
        "grade": grade_of(score),
        "tier": tier,
        "tier_index": tier_index(score),
        "next_tier": nxt,
        "points_to_next": (nxt["cut"] - score) if nxt else 0,
        "daily": daily,
        "week_delta": week_delta,
        "streak": streak(records, today),
        "style": user_style(records, today=today),
        "records": len([r for r in records if r.get("game") == game and r.get("weapon") == weapon]),
        "last": latest_record(records, game, weapon),
    }
