"""Scoring engine: one unified aim profile, 5 areas, 18-tier ladder, 60+ scenarios.

The numbers mirror the "코박스 에임 성장일기" spreadsheet the user built
(18 tiers of 0 ~ 3,600+ points, 5 areas x 8 scenarios = 40 KovaaK's scenarios,
Aim Lab 25(스나) + 27(라플) — 중복 제외 34종).  Everything here is pure Python with no
I/O so it can be unit tested and reused from the JS side through the bridge.

v1.1.0 — 통합 에임 프로필
  · 종합 점수 / 18단계 티어 / 5영역 밸런스 / 연속·누적 훈련일 / 일별 추이를
    트레이너(코박스·에임랩)와 무기(스나·라플) 구분 없이 **하나로 합산**한다.
    (`profile_values` · `summary`)
  · 각 훈련 로그에는 `game`(trainer) · `weapon` · `scenarios[].name` · raw 점수가
    그대로 남아, 스나/라플 유저 자동 판별과 시나리오별 기록은 계속 동작한다.
  · 코박스/에임랩 · 스나/라플 토글은 **추천 5종 · 더 연습하기의 필터 + 실행
    프로그램**을 고르는 용도다 (`build_recs` · `WEAPON_PLAN`).
"""
from __future__ import annotations

import datetime as _dt
import statistics
from collections import Counter
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
AREA_NAME = {a["key"]: a["name"] for a in AREAS}

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

# Aim Lab — 실제 게임에 있는 인기 과제 이름 그대로 (Ultimate / Precision / Speed).
# 스나: 정밀·와이드 플릭 + 마이크로 보정 + 반응·동체 + 스위칭 (20종)
AIMLAB_SCENARIOS: dict[str, list[tuple[str, str]]] = {
    "sniper": [
        ("Sixshot Ultimate", "precision"),
        ("Sixshot Precision", "precision"),
        ("Snipershot Precision", "precision"),
        ("Reflexshot Precision", "precision"),
        ("Headshot Precision", "precision"),
        ("Multishot Precision", "precision"),
        ("Multishot Ultimate", "precision"),
        ("Gridshot Ultimate", "wide"),
        ("Gridshot Precision", "wide"),
        ("Spidershot Speed", "wide"),
        ("Spidershot Ultimate", "wide"),
        ("Reflexshot Ultimate", "wide"),
        ("Microshot Precision", "micro"),
        ("Microshot Ultimate", "micro"),
        ("Microshot Speed", "micro"),
        ("Microflex Precision", "micro"),
        ("Blinkshot Ultimate", "micro"),
        ("Motionshot Precision", "track"),
        ("Strafetrack Precision", "track"),
        ("Spidershot 180 Ultimate", "track"),
        ("Circletrack Ultimate", "track"),
        ("Spheretrack Precision", "track"),
        ("Switchtrack Ultimate", "switch"),
        ("Circletrack Speed", "switch"),
        ("Circletrack Precision", "switch"),
    ],
    # 라플: 트래킹 · 스위칭 · 헤드라인/마이크로 · 플릭 (27종)
    "rifle": [
        ("Gridshot Precision", "precision"),
        ("Sixshot Ultimate", "precision"),
        ("Sixshot Precision", "precision"),
        ("Spidershot Precision", "precision"),
        ("Headshot Precision", "precision"),
        ("Reflexshot Precision", "precision"),
        ("Reflexshot Ultimate", "precision"),
        ("Gridshot Ultimate", "wide"),
        ("Spidershot Ultimate", "wide"),
        ("Spidershot Speed", "wide"),
        ("Multishot Ultimate", "wide"),
        ("Microflex Standard", "wide"),
        ("Microflex Precision", "micro"),
        ("Microshot Speed", "micro"),
        ("Microshot Precision", "micro"),
        ("Blinkshot Ultimate", "micro"),
        ("Strafetrack Ultimate", "track"),
        ("Strafetrack Speed", "track"),
        ("Circletrack Ultimate", "track"),
        ("Spheretrack Ultimate", "track"),
        ("Motiontrack Ultimate", "track"),
        ("Reactivetrack Ultimate", "track"),
        ("Linetrace Ultimate", "track"),
        ("Motionshot Speed", "track"),
        ("Switchtrack Ultimate", "switch"),
        ("Spidershot 180 Ultimate", "switch"),
        ("Circletrack Precision", "switch"),
    ],
}

GAMES = {"kovak": "KovaaK's", "aimlab": "Aim Lab"}
WEAPONS = {"sniper": "스나", "rifle": "라플"}

# 무기별 맞춤 시나리오 철학 — 추천 5종이 뽑히는 영역과 장수.
#   스나 : 롱 트래킹 제외. 플릭(정밀·와이드) · 마이크로 보정 · 반응/동체 · 스위칭
#   라플 : 트래킹 · 스위칭 · 헤드라인/마이크로 · 플릭 위주
WEAPON_PLAN: dict[str, list[str]] = {
    "sniper": ["precision", "wide", "micro", "switch", "precision"],
    "rifle": ["track", "track", "switch", "micro", "precision"],
}
WEAPON_PHILOSOPHY = {
    "sniper": "스나 맞춤 · 롱 트래킹 제외 · 플릭/마이크로/반응·동체/스위칭",
    "rifle": "라플 맞춤 · 트래킹/스위칭/헤드라인·마이크로/플릭",
}

# difficulty: per-area score thresholds (0-100)
DIFF_LEVELS = ("초급", "중급", "상급")
DIFF_CUTS = (70, 85)


def scenarios_for(game: str, weapon: str) -> list[dict[str, Any]]:
    """All scenarios available for a game/weapon pair, with area + priority."""
    if game == "aimlab":
        pairs = AIMLAB_SCENARIOS.get(weapon) or AIMLAB_SCENARIOS["sniper"]
        return [{"name": n, "area": a, "area_key": a, "area_idx": AREA_INDEX[a], "priority": i}
                for i, (n, a) in enumerate(pairs)]
    out: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    for name, area in KOVAAK_SCENARIOS:
        p = counters.get(area, 0)
        counters[area] = p + 1
        out.append({"name": name, "area": area, "area_key": area, "area_idx": AREA_INDEX[area], "priority": p})
    return out


def scenario_library() -> dict[str, int]:
    """{'game-weapon': 종} — 대시보드/테스트가 라이브러리 규모를 확인할 때."""
    return {f"{g}-{w}": len(scenarios_for(g, w)) for g in GAMES for w in WEAPONS}


def difficulty_for(value: float) -> str:
    """0~100 평점 기준 난이도."""
    if value < DIFF_CUTS[0]:
        return DIFF_LEVELS[0]
    if value < DIFF_CUTS[1]:
        return DIFF_LEVELS[1]
    return DIFF_LEVELS[2]


# ---------------------------------------------------------------- basics
def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- scale
# 훈련 프로그램마다 점수 단위가 완전히 다르다.
#   · KovaaK's  : 시나리오당 수십~수백 점 (예: 1wall 6targets small 152)
#   · Aim Lab   : 수만 점 (예: Gridshot Ultimate 92,500)
# 그래서 입력한 점수는 그대로 저장하고, 레이더·티어용 0~100 평점으로만 환산한다.
GAME_REF = {"kovak": 200.0, "aimlab": 100000.0}


def rating_of(game: str, value: float | None) -> float:
    """시나리오 점수 → 0~100 평점 (게임별 기준값으로 나눈 뒤 상한)."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number <= 0:
        return 0.0
    ref = GAME_REF.get(game, 200.0)
    return round(max(0.0, min(100.0, number / ref * 100.0)), 1)


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
def _sorted_records(records: Iterable[dict[str, Any]], game: str | None = None,
                    weapon: str | None = None) -> list[dict[str, Any]]:
    rows = [r for r in records if (game is None or r.get("game") == game) and (weapon is None or r.get("weapon") == weapon)]
    rows.sort(key=lambda r: (r.get("created_at") or r.get("date") or "", r.get("id", "")))
    return rows


def _record_day(record: dict[str, Any]) -> str:
    return str(record.get("date") or str(record.get("created_at") or "")[:10])


def area_values(records: Sequence[dict[str, Any]], game: str = "kovak", weapon: str = "sniper",
                window: int = 5) -> dict[str, float]:
    """Average of the last ``window`` records per area (0-100 평점, 1 decimal).

    게임/무기로 거른 뒤의 값 — 개별 트레이너를 들여다볼 때만 쓴다.
    대시보드·티어는 통합 프로필(:func:`profile_values`)을 쓴다.
    """
    rows = _sorted_records(records, game, weapon)[-window:]
    values: dict[str, float] = {}
    for key in AREA_KEYS:
        vals = [rating_of(game, r["scores"][key]) for r in rows if r.get("scores", {}).get(key) is not None]
        values[key] = round(statistics.fmean(vals), 1) if vals else 0.0
    return values


def profile_values(records: Sequence[dict[str, Any]], window: int = 5) -> dict[str, float]:
    """통합 에임 프로필 — 코박스·에임랩·스나·라플의 모든 기록을 한 값으로.

    영역마다 '가장 최근 window 개의 그 영역 점수'를 게임별 기준값으로 0~100
    평점으로 바꿔 평균한다.  그래서 코박스 152 와 에임랩 92,500 이 같은 축에 놓인다.
    """
    rows = _sorted_records(records)
    values: dict[str, float] = {}
    for key in AREA_KEYS:
        vals: list[float] = []
        for rec in reversed(rows):
            raw = (rec.get("scores") or {}).get(key)
            if raw is None:
                continue
            vals.append(rating_of(str(rec.get("game") or "kovak"), raw))
            if len(vals) >= window:
                break
        values[key] = round(statistics.fmean(vals), 1) if vals else 0.0
    return values


def overall_score(values: dict[str, float]) -> int:
    if not values:
        return 0
    mean = statistics.fmean(values.get(k, 0.0) for k in AREA_KEYS)
    return int(round(clamp(mean, 0, 100) * 36))


def score_of_record(record: dict[str, Any]) -> int:
    game = str(record.get("game") or "kovak")
    return overall_score({k: rating_of(game, record.get("scores", {}).get(k)) for k in AREA_KEYS})


def latest_record(records: Sequence[dict[str, Any]], game: str | None = None,
                  weapon: str | None = None) -> dict[str, Any] | None:
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


def today_scores(records: Sequence[dict[str, Any]], today: _dt.date | None = None) -> dict[str, float]:
    """오늘 내가 적은 시나리오 점수 {'시나리오 이름': 점수}."""
    day = (today or _dt.date.today()).isoformat()
    out: dict[str, float] = {}
    for rec in _sorted_records(records):
        if _record_day(rec) != day:
            continue
        for item in rec.get("scenarios", []):
            if item.get("name") and item.get("score") is not None:
                out[str(item["name"])] = float(item["score"])
    return out


def today_scenario_count(records: Sequence[dict[str, Any]], game: str | None = None,
                         weapon: str | None = None, today: _dt.date | None = None) -> int:
    day = (today or _dt.date.today()).isoformat()
    names = set()
    for rec in _sorted_records(records, game, weapon):
        if _record_day(rec) != day:
            continue
        for item in rec.get("scenarios", []):
            if item.get("name") and item.get("score") is not None:
                names.add(str(item["name"]))
    return len(names)


# ---------------------------------------------------------------- rotation
ROTATION_MEMORY_DAYS = 3  # 오늘 + 이전 3일 동안 같은 시나리오를 다시 내지 않는다


def _used_recently(history: Sequence[dict[str, Any]], game: str, weapon: str, today: _dt.date,
                   days: int = ROTATION_MEMORY_DAYS) -> set[str]:
    floor = (today - _dt.timedelta(days=days)).isoformat()
    used: set[str] = set()
    for entry in history:
        if entry.get("game") != game or entry.get("weapon") != weapon:
            continue
        if str(entry.get("date", "")) < floor:
            continue
        used.update(entry.get("names") or [])
    return used


def weapon_slots(weapon: str, values: dict[str, float]) -> list[str]:
    """무기 철학(WEAPON_PLAN) 대로 오늘 뽑을 영역 순서 — 약한 영역이 먼저.

    각 영역을 한 번씩(약점 순) 뽑고, 같은 영역이 두 번 들어가는 무기는
    그 두 번째 칸을 맨 뒤에 붙인다.  스나 모드에는 트래킹 칸이 없다.
    """
    plan = list(WEAPON_PLAN.get(weapon) or AREA_KEYS)
    counts = Counter(plan)
    uniq = [k for k in dict.fromkeys(plan)]
    uniq.sort(key=lambda k: (values.get(k, 0.0), AREA_INDEX[k]))
    order = list(uniq)
    for key in uniq:
        order.extend([key] * max(0, counts[key] - 1))
    return order


def _rec_row(choice: dict[str, Any], *, values: dict[str, float], records: Sequence[dict[str, Any]],
             today_scores_map: dict[str, float], extra: bool = False) -> dict[str, Any]:
    key = choice["area_key"]
    hist = scenario_history(records, choice["name"])
    last = hist[-1]["score"] if hist else None
    prev = hist[-2]["score"] if len(hist) > 1 else None
    delta = round(last - prev, 1) if (last is not None and prev is not None) else None
    mine = today_scores_map.get(choice["name"])
    return {
        "name": choice["name"],
        "area": choice["area_idx"],
        "area_key": key,
        "area_name": AREA_NAME.get(key, ""),
        "diff": difficulty_for(values.get(key, 0.0)),
        "score": None if last is None else round(last, 1),
        "best": None if not hist else round(max(h["score"] for h in hist), 1),
        "today_score": None if mine is None else round(mine, 1),
        "delta": delta,
        "done": mine is not None,
        "attempts": len(hist),
        "extra": extra,
    }


def _pick(pool: Sequence[dict[str, Any]], key: str, *, seen: set[str], used: set[str],
          offset: int) -> dict[str, Any] | None:
    cands = [s for s in pool if s["area_key"] == key and s["name"] not in seen]
    if not cands:
        return None
    fresh = [s for s in cands if s["name"] not in used] or cands
    return fresh[offset % len(fresh)]


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
    """오늘의 추천 count 종 — 무기 철학(약점 우선 · 4일 비반복)대로 뽑는다."""
    today = today or _dt.date.today()
    pool = scenarios_for(game, weapon)
    used = _used_recently(history, game, weapon, today)
    mine = today_scores(records, today)
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in weapon_slots(weapon, values):
        if len(picked) >= count:
            break
        choice = _pick(pool, key, seen=seen, used=used, offset=offset)
        if not choice:
            continue
        seen.add(choice["name"])
        picked.append(_rec_row(choice, values=values, records=records, today_scores_map=mine))
    return picked


def build_more(
    *,
    values: dict[str, float],
    records: Sequence[dict[str, Any]],
    game: str = "kovak",
    weapon: str = "sniper",
    exclude: Sequence[str] = (),
    today: _dt.date | None = None,
    count: int = 3,
) -> list[dict[str, Any]]:
    """`+ 더 연습하기` — 오늘 리스트에 없는 시나리오 중 가장 약한 영역 위주."""
    today = today or _dt.date.today()
    pool = scenarios_for(game, weapon)
    mine = today_scores(records, today)
    seen = {str(n) for n in exclude if n}
    picked: list[dict[str, Any]] = []
    # 약한 영역 순으로 두 바퀴 — 한 영역에서만 뽑히지 않게
    order = weapon_slots(weapon, values) + list(weapon_slots(weapon, values))
    for key in order:
        if len(picked) >= count:
            break
        cands = [s for s in pool if s["area_key"] == key and s["name"] not in seen]
        if not cands:
            continue
        choice = cands[0]
        seen.add(choice["name"])
        picked.append(_rec_row(choice, values=values, records=records, today_scores_map=mine, extra=True))
    return picked


def recs_for_names(
    names: Sequence[str],
    *,
    values: dict[str, float],
    records: Sequence[dict[str, Any]],
    game: str = "kovak",
    weapon: str = "sniper",
    today: _dt.date | None = None,
) -> list[dict[str, Any]]:
    """저장된 '더 연습하기' 이름들을 다시 화면용 행으로."""
    today = today or _dt.date.today()
    by_name = {s["name"]: s for s in scenarios_for(game, weapon)}
    mine = today_scores(records, today)
    out = []
    for name in names:
        choice = by_name.get(str(name))
        if not choice:
            continue
        out.append(_rec_row(choice, values=values, records=records, today_scores_map=mine, extra=True))
    return out


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
    return {_record_day(r) for r in records if r.get("date") or r.get("created_at")}


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
    """그 날 기록만으로 계산한 종합 점수(참고용 · 한 영역만 적은 날은 낮게 나온다)."""
    today = today or _dt.date.today()
    out = []
    for i in range(days - 1, -1, -1):
        day = (today - _dt.timedelta(days=i)).isoformat()
        rows = [r for r in records if _record_day(r) == day]
        out.append({
            "date": day,
            "score": int(round(statistics.fmean(score_of_record(r) for r in rows))) if rows else None,
            "records": len(rows),
        })
    return out


def daily_profile(records: Sequence[dict[str, Any]], days: int = 7, today: _dt.date | None = None) -> list[dict[str, Any]]:
    """통합 프로필의 일별 추이 — 그날까지의 모든 기록으로 계산한 종합 점수.

    하루에 시나리오 1~2개만 적어도 점수가 반토막 나지 않고, 프로필이 쌓이며
    오르는 모습이 그대로 보인다(통합 점수 정책).
    """
    today = today or _dt.date.today()
    out = []
    for i in range(days - 1, -1, -1):
        day = today - _dt.timedelta(days=i)
        stamp = day.isoformat()
        upto = [r for r in records if _record_day(r) <= stamp]
        on_day = [r for r in upto if _record_day(r) == stamp]
        values = profile_values(upto)
        filled = any(v > 0 for v in values.values())
        out.append({
            "date": stamp,
            "score": overall_score(values) if (on_day and filled) else None,
            "records": len(on_day),
            "total_records": len(upto),
        })
    return out


def user_style(records: Sequence[dict[str, Any]], days: int = 30, today: _dt.date | None = None) -> dict[str, Any]:
    """스나 vs 라플 훈련 횟수 비교 (최근 30일) → 자동 무기 판별."""
    today = today or _dt.date.today()
    floor = (today - _dt.timedelta(days=days - 1)).isoformat()
    counts = {"sniper": 0, "rifle": 0}
    for r in records:
        if _record_day(r) < floor:
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


def trainer_breakdown(records: Sequence[dict[str, Any]], today: _dt.date | None = None) -> dict[str, Any]:
    """통합 프로필 구성 — 어느 프로그램·무기로 얼마나 쌓았는지(표시용)."""
    today = today or _dt.date.today()
    by_game = {g: {"records": 0, "today": 0} for g in GAMES}
    by_weapon = {w: {"records": 0, "today": 0} for w in WEAPONS}
    stamp = today.isoformat()
    for rec in records:
        g = str(rec.get("game") or "")
        w = str(rec.get("weapon") or "")
        on_today = _record_day(rec) == stamp
        if g in by_game:
            by_game[g]["records"] += 1
            by_game[g]["today"] += 1 if on_today else 0
        if w in by_weapon:
            by_weapon[w]["records"] += 1
            by_weapon[w]["today"] += 1 if on_today else 0
    return {"by_game": by_game, "by_weapon": by_weapon}


# ================================================================ v1.2.0
# 시나리오 최고 기록 · 잔디 히트맵 · 주간 목표 · 훈련 전후 비교 · 1:1 비교 · 워밍업
# ------------------------------------------------------------------
def scenario_bests(records: Sequence[dict[str, Any]],
                   exclude_ids: Iterable[str] = ()) -> dict[str, float]:
    """시나리오별 내 최고 기록 {'시나리오': 점수}."""
    skip = {str(i) for i in exclude_ids}
    best: dict[str, float] = {}
    for rec in records:
        if str(rec.get("id")) in skip:
            continue
        for item in rec.get("scenarios", []) or []:
            name = str(item.get("name") or "")
            score = item.get("score")
            if not name or score is None:
                continue
            try:
                value = float(score)
            except (TypeError, ValueError):
                continue
            if name not in best or value > best[name]:
                best[name] = round(value, 1)
    return best


def new_pbs(records: Sequence[dict[str, Any]], scenarios: Sequence[dict[str, Any]],
            exclude_ids: Iterable[str] = ()) -> list[dict[str, Any]]:
    """이번에 저장하는 점수가 기존 최고 기록을 넘었는지 (🏆 NEW PB)."""
    best = scenario_bests(records, exclude_ids=exclude_ids)
    out: list[dict[str, Any]] = []
    for row in scenarios:
        name = str(row.get("name") or "")
        score = row.get("score")
        if not name or score is None:
            continue
        prev = best.get(name)
        if prev is None or float(score) > float(prev):
            out.append({"name": name, "score": round(float(score), 1),
                        "prev": None if prev is None else round(float(prev), 1), "first": prev is None})
    return out


def heatmap(records: Sequence[dict[str, Any]], weeks: int = 14,
            today: _dt.date | None = None) -> dict[str, Any]:
    """최근 N주 훈련 잔디 — 요일(행) × 주(열) 격자, 4단계 색.

    그날 기록한 **시나리오 개수**로 단계를 나눈다(0 / 1~2 / 3~5 / 6+).
    """
    today = today or _dt.date.today()
    weeks = max(6, min(26, int(weeks or 14)))
    start = today - _dt.timedelta(days=today.weekday()) - _dt.timedelta(weeks=weeks - 1)
    per_day: dict[str, int] = {}
    for rec in records:
        day = _record_day(rec)
        names = {str(s.get("name")) for s in (rec.get("scenarios") or []) if s.get("score") is not None}
        per_day[day] = per_day.get(day, 0) + (len(names) or 1)

    def level(count: int) -> int:
        if count <= 0:
            return 0
        if count <= 2:
            return 1
        if count <= 5:
            return 2
        return 3

    grid: list[list[dict[str, Any]]] = []
    months: list[dict[str, Any]] = []
    trained_days = 0
    total_runs = 0
    last_month = None
    for w in range(weeks):
        column: list[dict[str, Any]] = []
        monday = start + _dt.timedelta(weeks=w)
        label = f"{monday.month}월"
        if label != last_month:
            months.append({"label": label, "week": w})
            last_month = label
        for dow in range(7):
            day = monday + _dt.timedelta(days=dow)
            stamp = day.isoformat()
            count = per_day.get(stamp, 0)
            future = day > today
            if count:
                trained_days += 1
                total_runs += count
            column.append({"d": stamp, "n": count, "lvl": 0 if future else level(count),
                           "future": future, "dow": dow})
        grid.append(column)
    return {
        "weeks": weeks,
        "start": start.isoformat(),
        "end": today.isoformat(),
        "grid": grid,
        "months": months,
        "days_trained": trained_days,
        "total": total_runs,
        "level_names": ["없음", "가벼움", "보통", "불태움"],
        "weekdays": ["월", "화", "수", "목", "금", "토", "일"],
    }


WEEK_GOAL_DEFAULTS = {"days": 5, "games": 25}


def weekly_progress(records: Sequence[dict[str, Any]], goals: dict[str, Any] | None = None,
                    today: _dt.date | None = None) -> dict[str, Any]:
    """이번 주(월~일) 목표 달성률 — 출석 일수 + 훈련 판수."""
    today = today or _dt.date.today()
    goals = {**WEEK_GOAL_DEFAULTS, **(goals or {})}
    try:
        goal_days = max(1, int(goals.get("days") or WEEK_GOAL_DEFAULTS["days"]))
    except (TypeError, ValueError):
        goal_days = WEEK_GOAL_DEFAULTS["days"]
    try:
        goal_games = max(1, int(goals.get("games") or WEEK_GOAL_DEFAULTS["games"]))
    except (TypeError, ValueError):
        goal_games = WEEK_GOAL_DEFAULTS["games"]

    week_start = today - _dt.timedelta(days=today.weekday())
    week_end = week_start + _dt.timedelta(days=6)
    days: set[str] = set()
    games = 0
    for rec in records:
        day = _record_day(rec)
        if week_start.isoformat() <= day <= week_end.isoformat():
            days.add(day)
            games += 1
    days_done = len(days)
    pct_days = min(100.0, days_done / goal_days * 100)
    pct_games = min(100.0, games / goal_games * 100)
    done = days_done >= goal_days and games >= goal_games
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "days": goal_days, "days_done": days_done, "pct_days": round(pct_days, 1),
        "games": goal_games, "games_done": games, "pct_games": round(pct_games, 1),
        "pct": round((pct_days + pct_games) / 2, 1),
        "done": done,
        "left_days": max(0, goal_days - days_done),
        "left_games": max(0, goal_games - games),
        "label": (f"이번 주 목표 달성 · {days_done}/{goal_days}일 · {games}/{goal_games}판" if done
                  else f"{days_done}/{goal_days}일 · {games}/{goal_games}판"),
    }


def training_impact(matches: Sequence[dict[str, Any]], records: Sequence[dict[str, Any]],
                    today: _dt.date | None = None, min_sample: int = 5) -> dict[str, Any]:
    """에임 훈련한 날 vs 안 한 날의 랭크전 성적 (로컬 데이터만 · API 0건)."""
    today = today or _dt.date.today()
    trained_days = record_dates(records)
    groups = {"trained": [], "untrained": []}
    for match in matches or []:
        day = str(match.get("date") or "")[:10]
        if not day or len(day) != 10:
            continue
        groups["trained" if day in trained_days else "untrained"].append(match)

    def agg(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
        wins = [r for r in rows if str(r.get("result")) == "승"]
        kd = [float(r["kd"]) for r in rows if isinstance(r.get("kd"), (int, float))]
        dmg = [float(r["damage"]) for r in rows if isinstance(r.get("damage"), (int, float))]
        return {
            "games": len(rows),
            "win": round(len(wins) / len(rows) * 100, 1) if rows else None,
            "kd": round(statistics.fmean(kd), 1) if kd else None,
            "dmg": round(statistics.fmean(dmg)) if dmg else None,
        }

    trained, untrained = agg(groups["trained"]), agg(groups["untrained"])
    enough = trained["games"] >= min_sample and untrained["games"] >= min_sample

    def diff(key: str) -> float | None:
        a, b = trained.get(key), untrained.get(key)
        if a is None or b is None:
            return None
        return round(float(a) - float(b), 1)

    delta = {"win": diff("win"), "kd": diff("kd"), "dmg": diff("dmg")}
    if not matches:
        note = "병영수첩 전적을 불러오면 훈련일·미훈련일 랭크전 성적을 비교해 드립니다"
    elif not enough:
        note = (f"표본 부족 · 훈련일 {trained['games']}판 / 미훈련일 {untrained['games']}판 "
                f"(각 {min_sample}판 이상 모이면 비교합니다)")
    else:
        best = max((k for k in delta if delta[k] is not None), key=lambda k: abs(delta[k] or 0), default=None)
        word = {"win": "승률", "kd": "K/D", "dmg": "평균 딜량"}.get(best or "", "")
        if best and (delta[best] or 0) > 0:
            note = f"훈련한 날이 {word} {abs(delta[best]):.1f} 더 좋았습니다 · 훈련 효과가 보입니다"
        elif best:
            note = f"아직 훈련한 날이 {word} {abs(delta[best]):.1f} 낮습니다 · 회복·컨디션도 같이 보세요"
        else:
            note = "비교할 지표가 부족합니다"
    return {
        "has_data": bool(matches),
        "enough": enough,
        "min_sample": min_sample,
        "trained": trained,
        "untrained": untrained,
        "delta": delta,
        "note": note,
        "rows": [
            {"key": "win", "label": "승률", "unit": "%", "trained": trained["win"],
             "untrained": untrained["win"], "diff": delta["win"], "better": "up"},
            {"key": "kd", "label": "K/D", "unit": "%", "trained": trained["kd"],
             "untrained": untrained["kd"], "diff": delta["kd"], "better": "up"},
            {"key": "dmg", "label": "평균 딜량", "unit": "", "trained": trained["dmg"],
             "untrained": untrained["dmg"], "diff": delta["dmg"], "better": "up"},
        ],
    }


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def friend_compare(me: dict[str, Any] | None, friend: dict[str, Any] | None) -> dict[str, Any]:
    """나 vs 친구 — 이미 저장된 순위표/병영수첩 값만 쓴다(추가 API 호출 0건)."""
    me = me or {}
    friend = friend or {}

    def row(key: str, label: str, unit: str, *, text: bool = False,
            higher_better: bool = True) -> dict[str, Any]:
        a_raw, b_raw = me.get(key), friend.get(key)
        a, b = _num(a_raw), _num(b_raw)
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
                "diff": diff, "better": better, "text": text}

    def tier_text(row_: dict[str, Any], key: str) -> Any:
        name, rp = row_.get(f"{key}_tier"), _num(row_.get(key))
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
        "note": "저장된 순위표·병영수첩 데이터만 비교합니다 · 넥슨 API 를 다시 부르지 않습니다",
    }


WARMUP_PICKS: dict[str, list[str]] = {
    "kovak-sniper": ["1wall 6targets small", "Wide Wall 3 Targets", "voxTargetSwitch"],
    "kovak-rifle": ["Smoothbot Voltaic Easy", "voxTS Voltaic Easy", "1w4ts Voltaic"],
    "aimlab-sniper": ["Sixshot Ultimate", "Gridshot Ultimate", "Spidershot Speed"],
    "aimlab-rifle": ["Strafetrack Ultimate", "Switchtrack Ultimate", "Gridshot Ultimate"],
}


def warmup_recs(*, game: str = "kovak", weapon: str = "sniper",
                values: dict[str, float] | None = None,
                records: Sequence[dict[str, Any]] = (),
                today: _dt.date | None = None) -> list[dict[str, Any]]:
    """랭크전 직전 5분 워밍업 3종 — 짧고 핵심인 과제만."""
    values = values or {k: 0.0 for k in AREA_KEYS}
    today = today or _dt.date.today()
    pool = {s["name"]: s for s in scenarios_for(game, weapon)}
    mine = today_scores(records, today)
    picked: list[dict[str, Any]] = []
    for name in WARMUP_PICKS.get(f"{game}-{weapon}", []):
        choice = pool.get(name)
        if choice and name not in {p["name"] for p in picked}:
            picked.append(_rec_row(choice, values=values, records=records, today_scores_map=mine))
    if len(picked) < 3:
        # 이름이 라이브러리에 없으면 가장 약한 영역 순으로 채운다
        for row in build_recs(values=values, records=records, game=game, weapon=weapon,
                              today=today, count=3):
            if row["name"] not in {p["name"] for p in picked}:
                picked.append(row)
            if len(picked) >= 3:
                break
    return picked[:3]


# ================================================================ v1.2.0 · 플레이 코칭
def play_coaching(barracks: dict[str, Any] | None, limit: int = 4) -> list[dict[str, Any]]:
    """최근 100판(병영수첩 캐시)만 보고 '앞으로 어떻게 플레이할지' 3~4줄 코칭.

    추가 API 호출 없음 — 이미 받아 둔 승률·K/D·딜량·맵별 지표·최근 경기 결과만
    규칙으로 조합한다.  각 항목은 {icon, title, text, tone} 이고 tone 은
    ``warn``(고칠 점) · ``good``(강점/유지) · ``info``(참고).
    """
    data = barracks or {}
    stats = data.get("stats") or {}
    tier = data.get("tier") or {}
    maps = data.get("maps") or []
    matches = data.get("matches") or []
    recent = data.get("recent") or {}

    games = int(_num(stats.get("games")) or 0)
    win = _num(stats.get("win"))
    kd = _num(stats.get("kd"))
    dmg = _num(stats.get("dmg"))
    if games <= 0 or (win is None and kd is None):
        return []

    solo = _num((tier.get("solo") or {}).get("rp")) or 0.0
    party = _num((tier.get("party") or {}).get("rp")) or 0.0
    tips: list[dict[str, Any]] = []

    def num(value: float | None, unit: str = "", digits: int = 0) -> str:
        if value is None:
            return "—"
        if unit == "%":
            return f"{value:.1f}%"
        if digits:
            return f"{value:,.{digits}f}"
        return f"{value:,.0f}"

    # 1) 한 줄 핵심 진단 — 딜량/K/D/승률 조합
    win_txt = num(win, "%")
    kd_txt = num(kd, "%")
    dmg_txt = num(dmg)
    if dmg is not None and win is not None and dmg >= 1050 and win < 50:
        tips.append({"icon": "🎯", "tone": "warn", "title": "딜량은 되는데 승률이 따라오지 않습니다",
                     "text": f"딜량 {dmg_txt} · 승률 {win_txt} · 교전은 열리는데 마무리가 약합니다. "
                             f"교전 후 복귀·아군 합류 타이밍과 1:1 확신 각만 잡는 습관을 먼저 보세요."})
    elif kd is not None and kd < 48:
        tips.append({"icon": "🛡️", "tone": "warn", "title": "먼저 죽는 각을 줄이는 게 1순위",
                     "text": f"K/D {kd_txt} · 딜량 {dmg_txt} · 승률 {win_txt}. 상대에게 먼저 각을 주는 "
                             f"선진입을 줄이고, 정보 확인 후 한 박자 늦게 들어가는 것만으로도 지표가 올라갑니다."})
    elif win is not None and win < 45:
        tips.append({"icon": "🧭", "tone": "warn", "title": "초반 운영을 보수적으로",
                     "text": f"승률 {win_txt} · K/D {kd_txt} · 라운드 초반 무리한 선진입을 줄이고 "
                             f"각·정보를 먼저 확보하세요. 1킬보다 라운드 생존이 승률에 더 큽니다."})
    elif win is not None and win >= 52:
        tips.append({"icon": "🔥", "tone": "good", "title": "지금 페이스가 좋습니다",
                     "text": f"승률 {win_txt} · K/D {kd_txt} · 딜량 {dmg_txt}. 지금 쓰는 초동 루틴과 "
                             f"포지션을 그대로 유지하세요. 바꿀 것은 무기 감도보다 교전 선택입니다."})
    else:
        tips.append({"icon": "📊", "tone": "info", "title": "지표는 평균권 · 한 가지만 바꿔 보세요",
                     "text": f"승률 {win_txt} · K/D {kd_txt} · 딜량 {dmg_txt}. 한 판에 한 가지 목표"
                             f"(첫 교전 각·생존·합류)만 정해도 승률이 흔들리지 않습니다."})

    last5 = [m for m in matches[:5]]
    if len(last5) >= 5:
        wins5 = sum(1 for m in last5 if str(m.get("result")) == "승")
        if wins5 <= 1:
            tips.append({"icon": "⚡", "tone": "warn", "title": f"최근 5판 {wins5}승 · 흐름이 끊겼습니다",
                         "text": "지금은 5분 워밍업으로 감각을 올린 뒤 1~2판만 가볍게 돌리는 편이 좋습니다. "
                                 "연패 중에는 새 무기·새 감도보다 익숙한 세팅으로 돌아오세요."})

    # 2) 약한 맵 — 5판 이상 표본 중 K/D 최저
    pool = [m for m in maps if int(_num(m.get("games")) or 0) >= 5]
    worst = None
    if pool:
        worst = min(pool, key=lambda m: (_num(m.get("kd")) if _num(m.get("kd")) is not None else 999,
                                         -(_num(m.get("weak")) or 0)))
    if worst:
        w_kd = num(_num(worst.get("kd")), "%")
        w_win = num(_num(worst.get("win")), "%")
        w_games = int(_num(worst.get("games")) or 0)
        tips.append({"icon": "🗺️", "tone": "warn", "title": f"약한 맵 · {worst.get('map')}",
                     "text": f"K/D {w_kd} · 승률 {w_win} ({w_games}판). 여기서는 초동 교전 라인을 "
                             f"보수적으로 잡고, 각이 애매하면 다른 맵을 먼저 픽하는 편이 이득입니다."})

    # 3) 강한 맵 — 승률 최고 (표본 5판 이상)
    if pool:
        best = max(pool, key=lambda m: (_num(m.get("win")) or 0, _num(m.get("kd")) or 0))
        if best is not worst:
            tips.append({"icon": "✅", "tone": "good", "title": f"강한 맵 · {best.get('map')}",
                         "text": f"승률 {num(_num(best.get('win')), '%')} · K/D {num(_num(best.get('kd')), '%')}. "
                                 f"이 맵에서는 먼저 각을 만들고 킬을 주도하세요 — 자신 있는 맵에서 리듬을 타는 게 좋습니다."})

    # 4) 솔로/파티 격차 · 연패 · 주무기 성향 중 하나
    if abs(solo - party) >= 150:
        low, gap = ("솔로", solo - party) if solo < party else ("파티", party - solo)
        if gap < 0:
            tips.append({"icon": "👥", "tone": "info", "title": f"{low} RP가 {abs(gap):,.0f} 낮습니다",
                         "text": (f"솔로 {solo:,.0f} RP · 파티 {party:,.0f} RP. "
                                  + ("솔로 큐에서는 초반 선진입을 줄이고 아군 뒤에서 각을 받는 운영이 안전합니다."
                                     if low == "솔로" else
                                     "파티에서는 역할 분담(진입·엄호)을 정하고 콜을 맞추면 RP 손실이 줄어듭니다."))})
    sniper = _num(recent.get("sniper")) or 0.0
    assault = _num(recent.get("assault")) or 0.0
    if len(tips) < 3 and (sniper or assault):
        sniper_share = sniper / (sniper + assault) * 100 if (sniper + assault) else 0.0
        if sniper_share >= 55:
            tips.append({"icon": "🎯", "tone": "info", "title": f"스나 비중 {sniper_share:.0f}%",
                         "text": "오프앵글에서 첫 발을 먼저 꽂는 루틴을 유지하고, 빗나간 뒤 재조준보다 "
                                 "엄폐 후 각을 다시 잡는 쪽이 생존에 유리합니다."})
        elif sniper_share <= 45:
            tips.append({"icon": "🔫", "tone": "info", "title": f"라플 비중 {100 - sniper_share:.0f}%",
                         "text": "연사 교전에서는 무빙샷보다 짧게 끊어 쏘고, 헤드라인 높이를 유지하면 "
                                 "K/D가 안정됩니다."})

    # 3~4줄로 정리 (핵심 진단은 항상 맨 앞)
    if len(tips) < 3 and games:
        tips.append({"icon": "🎮", "tone": "info", "title": f"최근 {games}판 표본",
                     "text": "표본이 더 쌓이면 맵별·솔로/파티 조언이 정교해집니다. "
                             "전적을 한 번 더 최신화해 보세요."})
    return tips[:max(3, min(4, int(limit or 4)))]


def summary(records: Sequence[dict[str, Any]], game: str, weapon: str,
            today: _dt.date | None = None) -> dict[str, Any]:
    """Dashboard numbers for the *unified* aim profile.

    ``game``/``weapon`` no longer split the score — they only describe which
    recommendation filter is in front of the user right now.
    """
    today = today or _dt.date.today()
    values = profile_values(records)
    score = overall_score(values)
    tier = tier_of(score)
    nxt = next_tier(score)
    daily = daily_profile(records, 7, today)
    filled = [d for d in daily if d["score"] is not None]
    week_delta = filled[-1]["score"] - filled[0]["score"] if len(filled) >= 2 else None
    trainer_name = GAMES.get(game) or "KovaaK's"
    weapon_name = WEAPONS.get(weapon) or "스나"
    scope = f"{trainer_name} · {weapon_name}"
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
        "record_count": len(records),
        "days": len(record_dates(records)),
        "today_scenarios": today_scenario_count(records, game, weapon, today),
        "trainers": trainer_breakdown(records, today),
        "integrated": True,
        "scope": scope,
        "philosophy": WEAPON_PHILOSOPHY.get(weapon, ""),
        "last": latest_record(records),
    }
