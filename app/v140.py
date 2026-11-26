"""서든 전술 계산 — 클랜전 스카우팅 · 랭크 컨디션 · 듀오 시너지 · 내전 팀 밸런스.

v2.0.0 — 에임 연습 플레이리스트(personal_plan)와 의존성을 걷어냈다.  모두
**이미 저장된 값과 넥슨 캐시를 먼저** 쓰고, 모자란 부분만 API 로 채운다.
표준 라이브러리만 사용한다 (추가 패키지 없음).
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Iterable, Sequence

from . import nexon

# 서든 병영수첩 URL · ouid 는 32자리 hex, 병영수첩 프로필 ID 는 16자리 hex + SA.
OUID_RE = re.compile(r"[0-9a-fA-F]{32}")
SN_RE = re.compile(r"[0-9A-Fa-f]{16}SA")
_SPLIT_RE = re.compile(r"[,，\n\t ]+")


def is_ouid(text: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{32}", str(text or "").strip()))


def is_sn(text: str) -> bool:
    """병영수첩 프로필 ID (`96E8A3306F1061D2SA`)."""
    return bool(re.fullmatch(r"[0-9A-Fa-f]{16}SA", str(text or "").strip()))


def normalize_target(text: Any) -> str:
    """닉네임 · ouid · 병영수첩 URL 을 하나로 정리한다.

    `https://barracks.sa.nexon.com/{ouid}/detail`, `/clan/{ouid}/home` 처럼
    URL 을 그대로 붙여넣어도 ouid 를 꺼내 쓴다.
    """
    raw = str(text or "").strip().strip("\"'")
    if not raw:
        return ""
    match = OUID_RE.search(raw)
    if match:
        return match.group(0)
    # v1.5.0 — 병영수첩 프로필 URL/{…SA}/detail 을 붙여넣어도 ID 를 꺼내 쓴다 (#13)
    sn = SN_RE.search(raw)
    if sn:
        return sn.group(0).upper()
    return raw[:12]


def split_targets(text: Any, limit: int = 12) -> list[str]:
    """여러 닉네임/URL 을 줄바꿈·쉼표·공백으로 나눠 최대 limit 개."""
    if isinstance(text, (list, tuple)):
        parts = [str(t) for t in text]
    else:
        parts = _SPLIT_RE.split(str(text or ""))
    out: list[str] = []
    for part in parts:
        value = normalize_target(part)
        if value and value not in out:
            out.append(value)
    return out[:limit]

# ---------------------------------------------------------------- helpers
def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value: float, digits: int = 1) -> float:
    return round(_num(value), digits)


def _result(m: dict[str, Any]) -> str:
    return "승" if str(m.get("result") or (m.get("match_result") if m.get("match_result") in ("1", 1) else "")) in ("승", "1") else "패"


def _win(m: dict[str, Any]) -> bool:
    raw = m.get("result")
    if raw is not None:
        return str(raw) == "승"
    return str(m.get("match_result")) == "1"


def _kd(m: dict[str, Any]) -> float:
    k = _num(m.get("k"))
    d = _num(m.get("d"))
    return round(k / (k + d) * 100, 1) if (k + d) else 0.0


def _metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"games": 0, "wins": 0, "win": None, "kd": None, "dmg": None}
    wins = sum(1 for m in rows if _win(m))
    kills = sum(_num(m.get("k")) for m in rows)
    deaths = sum(_num(m.get("d")) for m in rows)
    dmgs = [_num(m.get("damage")) for m in rows if m.get("damage") not in (None, "")]
    return {
        "games": n,
        "wins": wins,
        "win": round(wins / n * 100, 1),
        "kd": round(kills / (kills + deaths) * 100, 1) if (kills + deaths) else 0.0,
        "dmg": int(round(sum(dmgs) / len(dmgs))) if dmgs else None,
    }


# ================================================================ 랭크 컨디션
CONDITIONS = ("최상", "좋음", "보통", "주의")


def _verdict(score_value: float) -> str:
    if score_value >= 70:
        return "최상"
    if score_value >= 56:
        return "좋음"
    if score_value >= 42:
        return "보통"
    return "주의"


def rank_condition(matches: Sequence[dict[str, Any]], recent_n: int = 8) -> dict[str, Any]:
    """최근 N판 vs 최근 100판 평균으로 오늘의 랭크 컨디션을 판정한다.

    `matches` 는 병영수첩의 최근 매치 목록 (최신순).  추가 API 0건.
    """
    rows = [m for m in (matches or []) if isinstance(m, dict)]
    if not rows:
        return {"available": False, "verdict": None, "label": "전적 없음",
                "note": "병영수첩을 불러오면 오늘 컨디션을 판정합니다",
                "recent": _metrics([]), "baseline": _metrics([]), "streak": {"kind": None, "n": 0}}
    total = _metrics(rows)
    recent_rows = rows[: max(3, min(int(recent_n or 8), 10))]
    recent = _metrics(recent_rows)

    # 연승·연패 (최신순 첫 결과가 이어지는 길이)
    streak_kind = "승" if _win(rows[0]) else "패"
    streak_n = 0
    for m in rows:
        if (_win(m) and streak_kind == "승") or ((not _win(m)) and streak_kind == "패"):
            streak_n += 1
        else:
            break

    win_diff = (recent["win"] or 0) - (total["win"] or 0)
    kd_diff = (recent["kd"] or 0) - (total["kd"] or 0)
    dmg_ratio = ((recent["dmg"] or 0) / total["dmg"]) if (recent["dmg"] and total["dmg"]) else 1.0

    value = 52.0
    value += max(-18.0, min(18.0, win_diff * 1.1))
    value += max(-12.0, min(12.0, kd_diff * 0.8))
    value += max(-10.0, min(10.0, (dmg_ratio - 1.0) * 45))
    if streak_kind == "승":
        value += min(14.0, streak_n * 4.5)
    else:
        value -= min(18.0, streak_n * 6.0)
    value = max(0.0, min(100.0, value))

    verdict = _verdict(value)
    tone = {"최상": "hot", "좋음": "good", "보통": "mid", "주의": "warn"}[verdict]
    if verdict == "최상":
        line = f"지금 감이 최고입니다 · 최근 {recent['games']}판 승률 {recent['win']}% (연속 {streak_n}{streak_kind})"
    elif verdict == "좋음":
        line = f"컨디션 좋음 · 최근 {recent['games']}판 승률 {recent['win']}% · K/D {recent['kd']}%"
    elif verdict == "보통":
        line = f"평소와 비슷합니다 · 최근 {recent['games']}판 승률 {recent['win']}% · 랭크전 무난"
    else:
        line = f"오늘은 주의 · 최근 {recent['games']}판 승률 {recent['win']}% (연속 {streak_n}{streak_kind})"
    note = (f"최근 {recent['games']}판과 최근 {total['games']}판 평균을 비교했습니다"
            if total["games"] > recent["games"] else f"최근 {total['games']}판 기록으로 판정했습니다")
    return {
        "available": True,
        "verdict": verdict,
        "tone": tone,
        "value": round(value, 1),
        "label": line,
        "note": note,
        "recent": recent,
        "baseline": total,
        "streak": {"kind": streak_kind, "n": streak_n},
        "diff": {"win": round(win_diff, 1), "kd": round(kd_diff, 1),
                 "dmg": int(round((recent["dmg"] or 0) - (total["dmg"] or 0)))},
        "checked_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def condition_history(history: Sequence[dict[str, Any]] | None, condition: dict[str, Any],
                      *, today: str | None = None, keep: int = 60) -> list[dict[str, Any]]:
    """일자별 컨디션 히스토리 — 같은 날은 마지막 판정으로 덮어쓴다."""
    day = today or _dt.date.today().isoformat()
    rows = [r for r in (history or []) if isinstance(r, dict) and r.get("date")]
    if not condition.get("available"):
        return rows[-keep:]
    entry = {
        "date": day,
        "verdict": condition.get("verdict"),
        "value": condition.get("value"),
        "win": (condition.get("recent") or {}).get("win"),
        "kd": (condition.get("recent") or {}).get("kd"),
        "dmg": (condition.get("recent") or {}).get("dmg"),
        "games": (condition.get("recent") or {}).get("games"),
        "at": condition.get("checked_at"),
    }
    rows = [r for r in rows if r.get("date") != day] + [entry]
    rows.sort(key=lambda r: str(r.get("date")))
    return rows[-keep:]


# ================================================================ 클랜전 스카우팅
def _weapon_tendency(recent: dict[str, Any], weapon: str | None) -> dict[str, Any]:
    """`recent_sniper_rate` / `recent_assault_rate` 는 **무기별 K/D(%)** 이다.

    사용 비중이 아니라서 둘의 합이 100을 넘을 수 있다. 서든은 대부분 유저가
    스나 K/D가 더 높기 때문에 개인별로 `스나 >= 라플` 이면 스나로 잡으면
    6명 중 5~6명이 스나가 된다. 그래서 여기서는 두 수치만 담고,
    **팀 단위 역할 배정(assign_roles)** 에서 격차 상위 2명만 스나로 정한다.
    """
    sniper_kd = round(_num((recent or {}).get("sniper")), 1)
    assault_kd = round(_num((recent or {}).get("assault")), 1)
    manual = weapon in ("sniper", "rifle")
    key = weapon if manual else ("sniper" if sniper_kd >= assault_kd else "rifle")
    return {"key": key, "label": ("스나" if key == "sniper" else "라플"),
            "estimate": "추정 스나" if key == "sniper" else "추정 라플",
            "manual": manual, "assigned": False,
            "sniper_kd": sniper_kd, "assault_kd": assault_kd,
            "gap": round(sniper_kd - assault_kd, 1),
            "sniper": sniper_kd, "assault": assault_kd}


def assign_roles(rows: Sequence[dict[str, Any]], *, max_snipers: int = 2) -> list[dict[str, Any]]:
    """팀 단위 기본 역할 — 스나 K/D 우위 인원 중 **격차 큰 상위 2명만 추정 스나**.

    서든은 대부분 스나 K/D가 더 높으므로 개인별 비교만 하면 전원이 스나가 된다.
    나머지는 추정 라플로 두고, 사용자가 화면에서 언제든 바꿀 수 있다.
    """
    players = [r for r in rows if isinstance(r, dict) and not r.get("error") and isinstance(r.get("weapon"), dict)]
    ranked = sorted(players, key=lambda r: -_num((r.get("weapon") or {}).get("gap")))
    sniper_names: list[str] = []
    for r in ranked:
        if len(sniper_names) >= max(0, int(max_snipers)):
            break
        if _num((r.get("weapon") or {}).get("gap")) < 0:
            continue
        sniper_names.append(str(r.get("name") or ""))
    for r in players:
        w = r["weapon"]
        if w.get("manual"):
            continue
        is_sniper = str(r.get("name") or "") in sniper_names
        w["key"] = "sniper" if is_sniper else "rifle"
        w["label"] = "스나" if is_sniper else "라플"
        w["estimate"] = "추정 스나" if is_sniper else "추정 라플"
        w["assigned"] = True
        w["rank"] = (sniper_names.index(str(r.get("name") or "")) + 1) if is_sniper else None
    return list(rows)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def win_streak_raw(rows: Sequence[dict[str, Any]]) -> int:
    """최신순 넥슨 경기 목록에서 이어진 연승 수."""
    n = 0
    for m in rows:
        won = m.get("result") == "승" if m.get("result") is not None else str(m.get("match_result")) == "1"
        if not won:
            break
        n += 1
    return n


def threat_score(solo_rp: float, kd: float, dmg: float, *, party_rp: float = 0,
                 win: float | None = None, streak: int = 0) -> int:
    """0~100 팀 위협도.

    v1.4.0 재설계 — **티어(RP) 70% + 최근 폼(10~15판 K/D·딜량·승률·연승) 30%**.
    승률·K/D 만으로 줄 세우면 실버 60% 가 불레전드 50% 보다 높게 나오던 오류를 막는다.
    같은 티어대 안에서는 최근 폼이 좋은 '에이스'를 가려낸다.
    """
    solo = max(0.0, _num(solo_rp))
    party = max(0.0, _num(party_rp))
    # 파티 RP 가 더 높으면 조금만 보정 (팀 위협도는 솔로 기준이 기본)
    effective = solo + min(200.0, max(0.0, party - solo) * 0.25)
    tier_part = _clamp01(effective / 3600.0) * 70.0

    kd_norm = _clamp01((_num(kd) - 35.0) / 30.0)          # 35% → 0, 65% → 1
    dmg_norm = _clamp01((_num(dmg) - 600.0) / 1000.0)     # 600 → 0, 1600 → 1
    win_norm = _clamp01((_num(win if win is not None else 0.0) - 35.0) / 30.0)
    form = kd_norm * 0.42 + dmg_norm * 0.36 + win_norm * 0.22
    form = _clamp01(form + min(0.18, max(0, int(streak or 0)) * 0.045))
    form_part = form * 30.0
    return int(round(max(0.0, min(100.0, tier_part + form_part))))


def threat_parts(solo_rp: float, kd: float, dmg: float, *, party_rp: float = 0,
                 win: float | None = None, streak: int = 0) -> dict[str, Any]:
    """위협도 구성 — 화면에서 근거를 보여 주기 위한 분해값."""
    solo = max(0.0, _num(solo_rp))
    party = max(0.0, _num(party_rp))
    effective = solo + min(200.0, max(0.0, party - solo) * 0.25)
    tier_part = _clamp01(effective / 3600.0) * 70.0
    kd_norm = _clamp01((_num(kd) - 35.0) / 30.0)
    dmg_norm = _clamp01((_num(dmg) - 600.0) / 1000.0)
    win_norm = _clamp01((_num(win if win is not None else 0.0) - 35.0) / 30.0)
    form = _clamp01(kd_norm * 0.42 + dmg_norm * 0.36 + win_norm * 0.22
                    + min(0.18, max(0, int(streak or 0)) * 0.045))
    return {"tier": round(tier_part, 1), "form": round(form * 30.0, 1),
            "tier_pct": round(tier_part / 70.0 * 100) if tier_part else 0,
            "form_pct": round(form * 100)}


def threat_label(value: int) -> str:
    if value >= 75:
        return "위협"
    if value >= 55:
        return "경계"
    if value >= 35:
        return "평범"
    return "무난"


def counter_tips(row: dict[str, Any]) -> list[str]:
    """상대 한 명에 대한 카운터 팁 (저장된 지표만 사용)."""
    tips: list[str] = []
    weapon = (row.get("weapon") or {}).get("key")
    if weapon == "sniper":
        tips.append("스나 추정 — A롱·설대 같은 긴 각을 주지 말고 연막·플래시로 각을 끊으세요.")
    elif weapon == "rifle":
        tips.append("라플 추정 — 좁은 각에서 먼저 자리 잡고 짧게 끊어 쏘세요. 긴 거리 1:1은 피합니다.")
    weak_maps = row.get("weak_maps") or []
    if weak_maps:
        tips.append(f"약한 맵 {weak_maps[0]['map']} (승률 {weak_maps[0]['win']}%) — 그 맵을 유도하면 유리합니다.")
    if (row.get("threat") or 0) >= 75:
        tips.append("팀 위협 1순위 — 1:1 대신 2:1 교전으로 묶고, 킬보다 생존 우선으로 운영하세요.")
    elif (row.get("threat") or 0) >= 55:
        tips.append("주요 교전 상대 — 첫 교전은 각을 받으며 시작하고, 아군 스나와 각을 겹치지 마세요.")
    else:
        tips.append("위협도가 낮은 상대 — 먼저 각을 만들고 킬을 주도해도 됩니다.")
    style = (row.get("style") or {}).get("key")
    if style == "rush":
        tips.append("돌격형 — 먼저 붙어옵니다. 첫 교전 각을 미리 잡고 받아치면 오히려 유리합니다.")
    elif style == "stable":
        tips.append("안정형 — 각을 오래 잡고 버팁니다. 연막으로 시야를 끊고 수적으로 몰아붙이세요.")
    elif style == "support":
        tips.append("지원형 — 어시로 팀을 살립니다. 이 상대의 주변 2인을 먼저 끊으면 팀이 무너집니다.")
    elif style == "ace":
        tips.append("에이스형 — 이 한 명만 끊어도 승률이 크게 올라갑니다. 2:1 로 묶어 잡으세요.")
    return tips[:4]


def scout_player(client: nexon.NexonClient, nickname: str, *, me_rp: int = 0,
                 depth: int = 10) -> dict[str, Any]:
    """한 명 스카우팅 — 프로필/티어는 캐시 우선, 최근 경기 상세는 depth 건까지.

    `nickname` 에 ouid 나 병영수첩 URL 을 넣어도 된다.
    """
    target = normalize_target(nickname)
    if not target:
        raise nexon.NexonError("닉네임을 입력해 주세요")
    if is_ouid(target):
        ouid = target
        basic = client.user_basic(ouid)
        name = str(basic.get("user_name") or "")
        if not name:
            raise nexon.NexonError("해당 ouid 의 계정을 찾지 못했습니다")
    else:
        name = target
        ouid = client.resolve_ouid(name)
        basic = client.user_basic(ouid)
    real = str(basic.get("user_name") or name)
    tier = client.user_tier(ouid)
    recent = client.user_recent(ouid)
    rows = client.recent_matches(ouid, nexon.RECENT_N)
    details = nexon._detail_jobs(client, rows, None, real, depth=depth) if rows else []
    agg = nexon.aggregate(rows, details, real)
    # 같은 팀으로 뛴 동료(캐시 상세에서) — 스카우팅 팀 케미 계산용
    team_mates: dict[str, dict[str, int]] = {}
    for raw, detail in zip(rows, details):
        if not detail:
            continue
        players = detail.get("match_detail") or []
        my_team = None
        for p in players:
            if str(p.get("user_name") or "").strip().lower() == real.strip().lower():
                my_team = p.get("team_id")
        if my_team is None:
            continue
        won = str(raw.get("match_result")) == "1"
        for p in players:
            mate = str(p.get("user_name") or "").strip()
            if not mate or mate.lower() == real.strip().lower():
                continue
            if p.get("team_id") != my_team:
                continue
            slot = team_mates.setdefault(mate, {"games": 0, "wins": 0})
            slot["games"] += 1
            slot["wins"] += 1 if won else 0
    # v1.4.0 — 위협도는 '티어 70% + 최근 폼 30%'. 폼은 최근 12판(10~15판) 기준.
    form_rows = rows[:12]
    form_agg = nexon.aggregate(form_rows, details[:len(form_rows)], real)
    streak = win_streak_raw(form_rows)
    assists = sum(_num(m.get("assist")) for m in form_rows)
    style = play_style(games=form_agg.get("games") or 0, kills=form_agg.get("kills") or 0,
                       deaths=form_agg.get("deaths") or 0, assists=assists,
                       dmg=form_agg.get("dmg") or 0, kd=form_agg.get("kd") or 0,
                       win=form_agg.get("win"))
    solo_rp = int(_num(tier.get("solo_rank_match_score")))
    party_rp = int(_num(tier.get("party_rank_match_score")))
    weapon = _weapon_tendency(recent, None)
    weak = [m for m in (agg.get("weak") or []) if _num(m.get("games")) >= 3][:2]
    row = {
        "name": real,
        "ouid": ouid,
        "clan": basic.get("clan_name") or "-",
        "solo": solo_rp,
        "party": party_rp or None,
        "solo_tier": nexon.kr_tier(tier.get("solo_rank_match_tier")),
        "party_tier": nexon.kr_tier(tier.get("party_rank_match_tier")),
        "kd": agg.get("kd") if agg.get("games") else None,
        "dmg": agg.get("dmg") if agg.get("games") else None,
        "win": agg.get("win") if agg.get("games") else None,
        "games": agg.get("games") or 0,
        "weapon": weapon,
        "weak_maps": [{"map": m.get("map"), "win": m.get("win"), "kd": m.get("kd"),
                       "games": m.get("games"), "weak": m.get("weak")} for m in weak],
        "maps": agg.get("maps") or [],
        "rp_gap": (solo_rp - int(me_rp or 0)) if me_rp else None,
        "form": {"games": form_agg.get("games") or 0, "kd": form_agg.get("kd"),
                 "dmg": form_agg.get("dmg"), "win": form_agg.get("win"), "streak": streak,
                 "kills": form_agg.get("kills"), "deaths": form_agg.get("deaths"),
                 "assists": int(round(assists))},
        "style": style,
        "team_mates": team_mates,
        "updated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    row["threat"] = threat_score(solo_rp, form_agg.get("kd") or 0, form_agg.get("dmg") or 0,
                                  party_rp=party_rp, win=form_agg.get("win"), streak=streak)
    row["threat_parts"] = threat_parts(solo_rp, form_agg.get("kd") or 0, form_agg.get("dmg") or 0,
                                       party_rp=party_rp, win=form_agg.get("win"), streak=streak)
    row["threat_label"] = threat_label(row["threat"])
    row["tips"] = counter_tips(row)
    return row


def team_summary(rows: Sequence[dict[str, Any]], roles: dict[str, str] | None = None) -> dict[str, Any]:
    """스카우팅한 상대 팀 전체 — 위협도 · 핵심 스나수 · 팀 전략 코멘트.

    ``roles`` ({'닉네임': 'sniper'|'rifle'}) 로 사용자가 바꾼 역할을 반영한다.
    """
    roles = roles or {}
    players = [r for r in rows if isinstance(r, dict) and not r.get("error")]
    if not players:
        return {"count": 0, "threat": 0, "label": "정보 없음", "top": None, "tips": [],
                "snipers": [], "sniper_style": "", "sniper_count": 0}
    # 역할 보정 (화면에서 수동 전환한 값)
    for r in players:
        override = roles.get(str(r.get("name") or ""))
        if override in ("sniper", "rifle") and isinstance(r.get("weapon"), dict):
            r["weapon"]["key"] = override
            r["weapon"]["label"] = "스나" if override == "sniper" else "라플"
            r["weapon"]["manual"] = True
    ranked = sorted(players, key=lambda r: -(r.get("threat") or 0))
    avg = sum((r.get("threat") or 0) for r in players) / len(players)
    team = int(round(min(100.0, avg * 0.65 + (ranked[0].get("threat") or 0) * 0.35)))
    label = threat_label(team)
    top = ranked[0]

    # 스나 역할 중 스나 K/D가 높은 상위 2명 = 핵심 스나수 (6:6 기준 1·2스나)
    snipers = sorted([r for r in players if (r.get("weapon") or {}).get("key") == "sniper"],
                     key=lambda r: -((r.get("weapon") or {}).get("sniper_kd") or 0))
    core = snipers[:2]
    sniper_count = len(snipers)
    team_size = len(players)
    if sniper_count >= 4 or (sniper_count == 3 and team_size >= 6):
        sniper_style = f"{sniper_count}스나 과다"
        style_tip = (f"상대 스나 {sniper_count}명 — 스나가 많은 조합입니다. 연막·플래시로 시야를 끊고 "
                     f"숏·중거리 러시로 붙어서 싸우면 유리합니다.")
    elif sniper_count == 2:
        sniper_style = "정석 2스나" if team_size >= 6 else "정석 1~2스나"
        style_tip = ("상대가 2스나 조합입니다. A롱·설대 같은 긴 각만 피하고, 2스나 각이 겹치는 구간을 "
                     "먼저 끊으면 나머지는 수적 우위로 풀 수 있습니다.")
    elif sniper_count == 1:
        sniper_style = "1스나 변칙" if team_size <= 5 else "0~1스나 · 라플 위주"
        style_tip = (f"상대 스나가 {sniper_count}명뿐인 라플 위주 팀입니다. 긴 거리에서 견제하며 "
                     "먼 각에서 먼저 한 명을 따면 유리하게 풀립니다.")
    else:
        sniper_style = "스나 없음 · 라플 위주"
        style_tip = "상대에 스나가 없습니다. 긴 각에서 먼저 한 명을 따고 천천히 밀어붙이면 유리합니다."

    tips = [
        f"가장 위협적인 상대는 {top.get('name')} ({top.get('threat_label')} {top.get('threat')}) — "
        f"솔로 {top.get('solo_tier')} · 최근 폼 K/D {top.get('kd') if top.get('kd') is not None else '—'}%.",
    ]
    if core:
        def _kd_label(c: dict[str, Any]) -> str:
            kd = (c.get("weapon") or {}).get("sniper_kd")
            return f"{c.get('name')}(스나 K/D {kd if kd is not None else '—'}%)"
        tips.append(f"핵심 스나수 — {', '.join(_kd_label(c) for c in core)}. "
                    f"이 둘의 각을 동시에 주지 말고 연막으로 순서를 나눠 끊으세요.")
    tips.append(style_tip)
    weak_maps = [r.get("weak_maps") or [] for r in players]
    if any(weak_maps):
        first = next((w[0]["map"] for w in weak_maps if w), None)
        if first:
            tips.append(f"상대들이 약한 맵 {first} 를 유도하면 승률이 올라갑니다.")
    return {"count": len(players), "threat": team, "label": label, "avg": int(round(avg)),
            "top": top.get("name"), "tips": tips[:4],
            "snipers": [{"name": c.get("name"), "kd_sniper": (c.get("weapon") or {}).get("sniper_kd"),
                         "kd_assault": (c.get("weapon") or {}).get("assault_kd"),
                         "kd": c.get("kd")} for c in core],
            "sniper_count": sniper_count, "sniper_style": sniper_style,
            "synergy": team_synergy(players), "force": team_power(players)}


# ================================================================ 듀오 · 깐부 시너지
def _player_side(match: dict[str, Any], friend: str) -> str | None:
    key = friend.strip().lower()
    if not key:
        return None
    for p in match.get("players") or []:
        try:
            name = str(p[0] if isinstance(p, (list, tuple)) else (p or {}).get("name") or "").strip().lower()
        except (IndexError, AttributeError):
            continue
        if not name or name != key:
            continue
        side = str((p[1] if isinstance(p, (list, tuple)) else (p or {}).get("side")) or "")
        if side == "팀":
            return "together"
        if side == "적":
            return "versus"
        return "unknown"
    return None


def duo_synergy(matches: Sequence[dict[str, Any]], friend: str) -> dict[str, Any]:
    """내 매치 캐시에서 친구와 같은 팀으로 뛴 판 vs 혼자 뛴 판 비교 (추가 API 0건)."""
    name = str(friend or "").strip()
    rows = [m for m in (matches or []) if isinstance(m, dict)]
    if not rows:
        return {"available": False, "friend": name, "reason": "최근 매치 기록이 없습니다 · 병영수첩을 불러와 주세요"}
    if not name:
        return {"available": False, "friend": "", "reason": "함께 뛴 친구 닉네임을 입력해 주세요"}
    together, versus = [], []
    for m in rows:
        side = _player_side(m, name)
        if side == "together":
            together.append(m)
        elif side == "versus":
            versus.append(m)
    # 친구가 낀 판(같은 편+상대편)은 '혼자 뛴 판'에서 제외한다
    pair_ids = {id(m) for m in together} | {id(m) for m in versus}
    alone = [m for m in rows if id(m) not in pair_ids]
    if not together:
        return {
            "available": False,
            "friend": name,
            "reason": (f"최근 {len(rows)}판에서 {name} 님과 같은 팀으로 뛴 판을 찾지 못했습니다"
                       if versus else f"최근 {len(rows)}판에 {name} 님이 없습니다"),
            "versus": _metrics(versus),
            "alone": _metrics(alone),
        }
    t = _metrics(together)
    a = _metrics(alone)
    delta = {
        "win": round((t["win"] or 0) - (a["win"] or 0), 1) if (t["win"] is not None and a["win"] is not None) else None,
        "kd": round((t["kd"] or 0) - (a["kd"] or 0), 1) if (t["kd"] is not None and a["kd"] is not None) else None,
        "dmg": (int(round((t["dmg"] or 0) - (a["dmg"] or 0)))
                if (t["dmg"] is not None and a["dmg"] is not None) else None),
    }
    score = 0.0
    score += (delta["win"] or 0) * 1.4
    score += (delta["kd"] or 0) * 0.6
    if t["dmg"] and a["dmg"]:
        score += ((t["dmg"] - a["dmg"]) / max(1, a["dmg"])) * 40
    win_txt = f"{delta['win']:+.1f}%p" if delta.get("win") is not None else "비교 표본 부족"
    if not a.get("games"):
        verdict, tone = "함께 뛴 판만 있음", "mid"
        line = f"{name} 님과 뛴 {t['games']}판만 있어 혼자 뛴 판과 비교할 수 없습니다"
    elif score >= 8:
        verdict, tone = "찰떡 궁합", "good"
        line = f"{name} 님과 함께 뛴 판이 확실히 좋습니다 · 승률 {win_txt}"
    elif score >= -4:
        verdict, tone = "무난", "mid"
        line = f"{name} 님과의 시너지는 평소와 비슷합니다 · 승률 {win_txt}"
    else:
        verdict, tone = "합이 안 맞음", "warn"
        line = f"{name} 님과 함께 뛰면 지표가 떨어집니다 · 승률 {win_txt}"
    pairs = [{"date": m.get("date"), "time": m.get("time"), "map": m.get("map"),
              "result": m.get("result"), "k": m.get("k"), "d": m.get("d")}
             for m in together[:8]]
    # v1.4.0 — 실전 전력: 내 기본 전력 + 함께 뛴 차이(케미) 보정
    base_power = int(round(_clamp01((a.get("win") or 0) / 100.0) * 40
                           + _clamp01(((a.get("kd") or 0) - 35) / 30.0) * 35
                           + _clamp01(((a.get("dmg") or 0) - 600) / 1000.0) * 25))
    adjust = max(-20, min(20, int(round(score))))
    force = {"base": base_power, "adjust": adjust, "power": max(0, min(100, base_power + adjust)),
             "notes": ([f"함께 뛴 판 승률 {t['win']}% (혼자 {a['win']}%)"] if a.get("win") is not None else [])}
    return {
        "available": True,
        "friend": name,
        "together": t,
        "alone": a,
        "versus": _metrics(versus),
        "delta": delta,
        "verdict": verdict,
        "tone": tone,
        "label": line,
        "pairs": pairs,
        "force": force,
        "sample": len(together),
        "note": f"최근 {len(rows)}판 중 {len(together)}판을 함께 뛰었습니다 · 추가 API 호출 0건",
    }


def duo_ranking(matches: Sequence[dict[str, Any]], candidates: Iterable[str],
                *, min_games: int = 2, limit: int = 5) -> dict[str, Any]:
    """최고 궁합 깐부 순위 — 이미 캐시된 내 매치만 사용 (추가 API 0건)."""
    seen: set[str] = set()
    order: list[str] = []
    for name in candidates or []:
        key = str(name or "").strip()
        if not key or key.lower() in seen or key.strip().lower() == "":
            continue
        seen.add(key.lower())
        order.append(key)
    rows: list[dict[str, Any]] = []
    for name in order:
        res = duo_synergy(matches, name)
        if not res.get("available"):
            continue
        t = res.get("together") or {}
        dl = res.get("delta") or {}
        games = int(t.get("games") or 0)
        if games < max(1, int(min_games)):
            continue
        dmg_ratio = 0.0
        a = res.get("alone") or {}
        if t.get("dmg") and a.get("dmg"):
            dmg_ratio = (float(t["dmg"]) - float(a["dmg"])) / max(1.0, float(a["dmg"])) * 100
        score_value = (dl.get("win") or 0) * 1.5 + (dl.get("kd") or 0) * 0.6 + dmg_ratio * 0.35
        rows.append({
            "friend": name, "games": games,
            "win": t.get("win"), "kd": t.get("kd"), "dmg": t.get("dmg"),
            "win_delta": dl.get("win"), "kd_delta": dl.get("kd"), "dmg_delta": dl.get("dmg"),
            "score": round(score_value, 1), "verdict": res.get("verdict"),
            "tone": res.get("tone"), "alone_games": (a or {}).get("games") or 0,
            "force": res.get("force"),
        })
    rows.sort(key=lambda r: -r["score"])
    return {"available": bool(rows), "rows": rows[:limit], "checked": len(order),
            "note": (f"최근 매치 {len([m for m in (matches or []) if isinstance(m, dict)])}판에서 "
                     f"함께 뛴 판이 {min_games}판 이상인 후보만 비교했습니다")}


STYLE_LABELS = {
    "rush": ("고교전·돌격형", "⚔️"),
    "stable": ("생존·안정형", "🛡"),
    "support": ("지원·팀플형", "🤝"),
    "ace": ("에이스·캐리형", "⭐"),
    "balanced": ("균형형", "⚖️"),
}


def play_style(*, games: int, kills: float, deaths: float, assists: float, dmg: float,
               kd: float, win: float | None) -> dict[str, Any]:
    """킬·데스·딜량·어시스트·승률만으로 추정하는 플레이 스타일.

    API 가 이동 좌표·선킬을 주지 않으므로 '최근 경기 지표 조합'으로만 추정한다.
    """
    games = max(1, int(games or 0))
    dpg = _num(kills) / games
    death_pg = _num(deaths) / games
    apg = _num(assists) / games
    dmg = _num(dmg)
    kd = _num(kd)
    win = _num(win)
    assist_ratio = (apg / (dpg + apg)) if (dpg + apg) else 0.0

    def n(v: float, lo: float, hi: float) -> float:
        return _clamp01((v - lo) / (hi - lo))

    aggression = n(dmg, 700, 1600) * 0.55 + n(dpg, 4.0, 10.0) * 0.45
    survival = n(5.5 - death_pg, 0.0, 2.5) * 0.6 + n(kd, 40, 70) * 0.4
    support = (assist_ratio * 0.7 + n(win, 40, 65) * 0.3) if apg else 0.0
    ace = n(dmg, 800, 1550) * 0.4 + n(kd, 45, 68) * 0.35 + n(win, 45, 62) * 0.25
    candidates = {"rush": aggression, "stable": survival, "support": support, "ace": ace}
    key = max(candidates, key=lambda k: candidates[k])
    best = candidates[key]
    if best < 0.42:
        key = "balanced"
    label, emoji = STYLE_LABELS.get(key, STYLE_LABELS["balanced"])
    desc = {
        "rush": f"판당 킬 {dpg:.1f} · 데스 {death_pg:.1f} · 딜량 {int(dmg):,} — 먼저 붙는 돌격형",
        "stable": f"판당 데스 {death_pg:.1f} · K/D {kd:.1f}% — 각을 받으며 버티는 안정형",
        "support": f"어시 {apg:.1f}/판 ({assist_ratio * 100:.0f}%) · 승률 {win:.0f}% — 팀을 살리는 지원형",
        "ace": f"딜량 {int(dmg):,} · K/D {kd:.1f}% · 승률 {win:.0f}% — 균형 잡힌 캐리형",
        "balanced": f"딜량 {int(dmg):,} · K/D {kd:.1f}% — 치우침 없는 균형형",
    }[key]
    return {"key": key, "label": label, "emoji": emoji, "desc": desc,
            "dpg": round(dpg, 1), "death_pg": round(death_pg, 1), "apg": round(apg, 2),
            "assist_ratio": round(assist_ratio, 2), "score": round(best, 2)}


def team_power(players: Sequence[dict[str, Any]], *, limit: int = 6) -> dict[str, Any]:
    """실전 전력 = 기본 티어력 + 조합 시너지 보정.

    티어가 높아도 역할이 겹치면 실전 전력이 떨어지고, 한 단계 낮아도
    스나+라플·공격형+안정형 조합에 케미가 좋으면 더 높게 나온다.
    """
    rows = [r for r in players if isinstance(r, dict) and not r.get("error")]
    if not rows:
        return {"base": 0, "adjust": 0, "power": 0, "notes": []}
    base = int(round(sum((r.get("threat") or 0) for r in rows) / len(rows)))
    notes: list[str] = []
    adjust = 0
    snipers = [r for r in rows if (r.get("weapon") or {}).get("key") == "sniper"]
    rifles = [r for r in rows if (r.get("weapon") or {}).get("key") == "rifle"]
    team_n = len(rows)
    # 랭크전 5대5 기준 정석 1~2스나, 6대6 기준 2스나
    if len(snipers) == 2 and len(rifles) >= max(2, team_n - 3):
        adjust += 6
        notes.append("2스나 + 라플 보완 (+6)")
    elif team_n <= 5 and len(snipers) == 1 and len(rifles) >= 3:
        adjust += 5
        notes.append("5:5 1스나 변칙 (+5)")
    elif len(snipers) > 2:
        penalty = 8 * (len(snipers) - 2)
        adjust -= penalty
        notes.append(f"스나 {len(snipers)}명 중복 ({-penalty})")
    elif len(snipers) == 0:
        adjust -= 6
        notes.append("스나 없음 · 장거리 각 취약 (-6)")

    # 고딜량 공격형 + 고승률 안정형이 서로 다른 사람이면 가산
    with_dmg = [r for r in rows if r.get("dmg")]
    with_win = [r for r in rows if r.get("win") is not None]
    if with_dmg and with_win:
        attacker = max(with_dmg, key=lambda r: float(r.get("dmg") or 0))
        stable = max(with_win, key=lambda r: float(r.get("win") or 0))
        if attacker.get("name") != stable.get("name"):
            adjust += 4
            notes.append(f"공격형+안정형 상호보완 (+4)")
        else:
            adjust += 1
            notes.append("에이스 단일 의존 (+1)")

    # 플레이 스타일 상성 — 돌격형 + 안정형/지원형은 시너지, 극단적 수비형 중복은 감점
    styles = [str((r.get("style") or {}).get("key") or "") for r in rows]
    rush = styles.count("rush")
    stable = styles.count("stable")
    support = styles.count("support")
    ace = styles.count("ace")
    if rush >= 1 and (stable >= 1 or support >= 1):
        adjust += 4
        notes.append("돌격형 + 안정/지원형 상성 (+4)")
    if stable >= 3:
        adjust -= 6
        notes.append(f"수비형 {stable}명 중복 (-6)")
    if rush >= 3:
        adjust -= 4
        notes.append(f"돌격형 {rush}명 · 앵커 부재 (-4)")
    if ace >= 1:
        adjust += 2
        notes.append("에이스 캐리형 보유 (+2)")

    # 실제 합동 전적 — 같은 팀으로 뛴 판이 캐시에 있을 때만
    names = {str(r.get("name") or "") for r in rows}
    pair_bonus = 0
    pairs = 0
    for r in rows:
        mates = r.get("team_mates") or {}
        for mate, stat in mates.items():
            if mate == r.get("name") or mate not in names:
                continue
            games = int((stat or {}).get("games") or 0)
            wins = int((stat or {}).get("wins") or 0)
            if games < 2:
                continue
            pairs += 1
            joint_win = wins / games * 100
            own = float(r.get("win") or 0)
            if joint_win >= own + 2:
                pair_bonus += 5
            elif joint_win <= own - 2:
                pair_bonus -= 5
    if pairs:
        pair_bonus = max(-10, min(10, pair_bonus // max(1, pairs // 2)))
        adjust += pair_bonus
        notes.append(f"합동 전적 케미 {'+' if pair_bonus >= 0 else ''}{pair_bonus}")

    power = max(0, min(100, base + adjust))
    return {"base": base, "adjust": adjust, "power": power, "notes": notes[:4],
            "pairs": pairs}


def style_from_member(member: dict[str, Any]) -> dict[str, Any]:
    """순위표 멤버(킬·데스 없이 K/D·딜량·승률만)로 추정하는 스타일."""
    kd = _num(member.get("kd"))
    dmg = _num(member.get("dmg"))
    win = _num(member.get("win"))
    if dmg >= 1150 and kd >= 50:
        key = "rush"
    elif kd >= 55 and dmg < 1100:
        key = "stable"
    elif win >= 53 and kd >= 48:
        key = "ace"
    elif win >= 55:
        key = "support"
    else:
        key = "balanced"
    label, emoji = STYLE_LABELS.get(key, STYLE_LABELS["balanced"])
    return {"key": key, "label": label, "emoji": emoji,
            "desc": f"K/D {kd:.1f}% · 딜량 {int(dmg):,} · 승률 {win:.1f}%"}


def member_power(member: dict[str, Any]) -> int:
    """멤버 실전 전력 — RP 70% + 폼(K/D·딜량·승률) 30% (스카우팅 위협도와 같은 축)."""
    return threat_score(member.get("solo") or 0, member.get("kd") or 0, member.get("dmg") or 0,
                        party_rp=member.get("party") or 0, win=member.get("win"))


def balance_teams(members: Sequence[dict[str, Any]], *, size: int | None = None,
                  roles: dict[str, str] | None = None) -> dict[str, Any]:
    """내전 팀 자동 짜기 (3:3 ~ 8:8).

    * 인원수에 맞춰 스나 유저를 **양 팀에 균등 분배**한다 (고정 2명이 아니다).
    * 티어(RP) 기반 실전 전력이 양 팀 반반에 가장 가깝게 나뉜다.
    * ``roles`` 로 화면에서 바꾼 스나/라플을 반영한다.
    """
    roles = roles or {}
    pool = [m for m in (members or []) if isinstance(m, dict) and m.get("name")]
    if len(pool) < 6:
        return {"available": False, "reason": "내전은 3:3(6명) 이상 필요합니다 · 멤버를 더 선택해 주세요"}
    pool = pool[:16]
    total = len(pool)
    size = max(2, min(8, int(size or (total // 2))))
    size = min(size, total // 2)
    rows = []
    for m in pool:
        name = str(m.get("name"))
        weapon = roles.get(name) or m.get("weapon") or "sniper"
        rows.append({
            "name": name, "rp": int(m.get("solo") or 0), "party": int(m.get("party") or 0),
            "kd": m.get("kd"), "dmg": m.get("dmg"), "win": m.get("win"),
            "weapon": weapon, "role": "sniper" if weapon == "sniper" else "rifle",
            "style": style_from_member(m), "power": member_power(m), "me": bool(m.get("me")),
            "manual": name in roles,
        })
    rows.sort(key=lambda r: -r["power"])
    team_a: list[dict[str, Any]] = []
    team_b: list[dict[str, Any]] = []

    def stats(team: list[dict[str, Any]]) -> tuple[int, int]:
        return sum(r["power"] for r in team), len([r for r in team if r["role"] == "sniper"])

    # ① 스나 유저를 양 팀에 번갈아 균등 분배
    snipers = [r for r in rows if r["role"] == "sniper"]
    others = [r for r in rows if r["role"] != "sniper"]
    for idx, r in enumerate(snipers):
        if len(team_a) >= size and len(team_b) >= size:
            break
        if len(team_a) >= size:
            team_b.append(r)
        elif len(team_b) >= size:
            team_a.append(r)
        else:
            (team_a if idx % 2 == 0 else team_b).append(r)

    # ② 나머지는 전력이 낮은 팀부터 (티어 반반)
    for r in others:
        if len(team_a) >= size and len(team_b) >= size:
            break
        a_pow, _ = stats(team_a)
        b_pow, _ = stats(team_b)
        if len(team_a) >= size:
            team_b.append(r)
        elif len(team_b) >= size:
            team_a.append(r)
        else:
            (team_a if a_pow <= b_pow else team_b).append(r)

    # ③ 남은 인원(홀수/인원 초과)은 전력이 낮은 팀에 서브로
    for r in rows:
        if r in team_a or r in team_b:
            continue
        a_pow, _ = stats(team_a)
        b_pow, _ = stats(team_b)
        (team_a if a_pow <= b_pow else team_b).append(r)

    def summary(team: list[dict[str, Any]]) -> dict[str, Any]:
        power = sum(r["power"] for r in team)
        snipers = len([r for r in team if r["role"] == "sniper"])
        styles: dict[str, int] = {}
        for r in team:
            styles[r["style"]["key"]] = styles.get(r["style"]["key"], 0) + 1
        return {"count": len(team), "power": round(power / max(1, len(team))),
                "total": power, "snipers": snipers, "styles": styles,
                "rows": [{"name": r["name"], "rp": r["rp"], "power": r["power"],
                          "role": r["role"], "style": r["style"]["label"],
                          "style_emoji": r["style"]["emoji"], "me": r["me"],
                          "manual": r["manual"]} for r in team]}

    a, b = summary(team_a), summary(team_b)
    diff = abs(a["power"] - b["power"])
    verdict = ("완전 균형" if diff <= 2 else "거의 균형" if diff <= 5 else "한쪽 우세")
    return {"available": True, "a": a, "b": b, "diff": diff, "verdict": verdict,
            "size": size, "pool": len(rows),
            "note": (f"{a['count']} : {b['count']} · 평균 실전 전력 차 {diff}점 · "
                     f"스나 A {a['snipers']} / B {b['snipers']}")}


def team_synergy(players: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """상대 팀 조합 시너지 — 역할 균형(2스나 기준) + 상위 에이스 화력."""
    rows = [r for r in players if isinstance(r, dict) and not r.get("error")]
    if not rows:
        return {"score": 0, "label": "정보 없음", "balance": 0, "ace": 0, "comment": ""}
    sniper_count = len([r for r in rows if (r.get("weapon") or {}).get("key") == "sniper"])
    rifle_count = len(rows) - sniper_count
    team_n = len(rows)
    # 5:5 → 정석 1~2스나(3~4라플) / 6:6 → 정석 2스나(4라플)
    ideal = (1, 2) if team_n <= 5 else (2,)
    if sniper_count in ideal:
        balance, balance_txt = 100, ("정석 1~2스나" if team_n <= 5 else "정석 2스나")
    elif sniper_count == 3:
        balance, balance_txt = 74, "3스나 편중"
    elif sniper_count == 0:
        balance, balance_txt = 52, "스나 없음"
    elif sniper_count == 4:
        balance, balance_txt = 44, "4스나 편중"
    else:
        balance, balance_txt = 24, f"{sniper_count}스나 과다"
    ranked = sorted(rows, key=lambda r: -(r.get("threat") or 0))
    top2 = ranked[:2]
    ace = round(sum((r.get("threat") or 0) for r in top2) / max(1, len(top2)))
    power = round(sum((r.get("threat") or 0) for r in rows) / len(rows))
    score = int(round(balance * 0.45 + ace * 0.35 + power * 0.20))
    if score >= 80:
        label = "완성형 조합"
    elif score >= 65:
        label = "균형 잡힌 조합"
    elif score >= 50:
        label = "무난한 조합"
    else:
        label = "구멍 있는 조합"
    comment = (f"{balance_txt} · 스나 {sniper_count} / 라플 {rifle_count} — "
               f"에이스 화력 {ace}점")
    return {"score": score, "label": label, "balance": balance, "balance_txt": balance_txt,
            "ace": ace, "power": power, "sniper_count": sniper_count, "rifle_count": rifle_count,
            "comment": comment,
            "force": team_power(rows)}


# ================================================================ 타인 A + 타인 B 듀오 시너지 (v2.0.0)
def _row_stats(row: dict[str, Any]) -> dict[str, Any]:
    """경기 한 줄 → (승리, 킬, 데스, 딜량).  원본 API 행과 병영수첩 행을 모두 받는다."""
    raw_result = row.get("match_result", row.get("result"))
    win = str(raw_result) in ("1", "승", "WIN", "win", "True", "true")
    try:
        kills = int(row.get("kill", row.get("k", 0)) or 0)
    except (TypeError, ValueError):
        kills = 0
    try:
        deaths = int(row.get("death", row.get("d", 0)) or 0)
    except (TypeError, ValueError):
        deaths = 0
    try:
        damage = float(row.get("damage", row.get("dmg", 0)) or 0)
    except (TypeError, ValueError):
        damage = 0.0
    return {"win": win, "k": kills, "d": deaths, "dmg": damage}


def _metrics_of(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    wins = kills = deaths = 0
    dmg_sum = 0.0
    dmg_n = 0
    for row in rows:
        st = _row_stats(row)
        wins += 1 if st["win"] else 0
        kills += st["k"]
        deaths += st["d"]
        if st["dmg"]:
            dmg_sum += st["dmg"]
            dmg_n += 1
    n = len(rows)
    return {"games": n, "wins": wins,
            "win": round(wins / n * 100, 1) if n else None,
            "kd": round(kills / (kills + deaths) * 100, 1) if (kills + deaths) else None,
            "dmg": int(round(dmg_sum / dmg_n)) if dmg_n else None}


def duo_between(matches_a: Sequence[dict[str, Any]], matches_b: Sequence[dict[str, Any]],
                name_a: str, name_b: str, *, min_games: int = 2,
                map_lookup: dict[str, str] | None = None) -> dict[str, Any]:
    """타인 A · 타인 B 가 **함께 플레이한 판**만 모아 공동 승률·K/D·시너지를 낸다.

    두 사람의 최근 경기 목록을 ``match_id`` 로 맞춘다.  같은 판에서 각자 남긴
    행을 그대로 쓰므로 추가 호출이 필요 없다.

    표본이 ``min_games`` 미만이면 ``available=False`` + 이유를 돌려주고
    숫자를 지어내지 않는다.
    """
    a_name = str(name_a or "").strip() or "A"
    b_name = str(name_b or "").strip() or "B"
    if a_name.lower() == b_name.lower():
        return {"available": False, "reason": "서로 다른 두 사람을 골라 주세요",
                "a": {"name": a_name}, "b": {"name": b_name}}
    index_b: dict[str, dict[str, Any]] = {}
    for row in matches_b or []:
        key = str(row.get("match_id") or "")
        if key:
            index_b[key] = row
    shared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in matches_a or []:
        key = str(row.get("match_id") or "")
        if not key or key in seen or key not in index_b:
            continue
        seen.add(key)
        shared.append({"a": row, "b": index_b[key]})

    all_a = _metrics_of(list(matches_a or []))
    all_b = _metrics_of(list(matches_b or []))
    if len(shared) < max(1, int(min_games)):
        return {
            "available": False,
            "reason": (f"최근 경기에서 두 사람이 같은 판을 뛴 기록이 {len(shared)}판뿐입니다 "
                       f"(최소 {max(1, int(min_games))}판 필요)"),
            "a": {"name": a_name, **all_a}, "b": {"name": b_name, **all_b},
            "shared": {"games": len(shared)}, "sample": len(shared),
        }

    rows_a = [item["a"] for item in shared]
    rows_b = [item["b"] for item in shared]
    metrics_a = _metrics_of(rows_a)
    metrics_b = _metrics_of(rows_b)
    joint = _metrics_of(rows_a)
    wins = joint["wins"]
    # 두 사람의 '평소' 승률 평균과 함께 뛴 판 승률의 차이
    baseline_parts = [v for v in (all_a.get("win"), all_b.get("win")) if v is not None]
    baseline = round(sum(baseline_parts) / len(baseline_parts), 1) if baseline_parts else None
    delta_win = round(joint["win"] - baseline, 1) if (joint["win"] is not None and baseline is not None) else None
    kd_parts = [v for v in (all_a.get("kd"), all_b.get("kd")) if v is not None]
    kd_base = round(sum(kd_parts) / len(kd_parts), 1) if kd_parts else None
    delta_kd = round(joint["kd"] - kd_base, 1) if (joint["kd"] is not None and kd_base is not None) else None

    # 표본이 적을수록 점수를 완만하게 (5판 미만은 최대 60%까지만 반영)
    confidence = min(1.0, 0.55 + len(shared) / 12.0)
    score = ((delta_win or 0) * 1.6 + (delta_kd or 0) * 0.8) * confidence
    if delta_win is None and delta_kd is None:
        verdict, tone = "표본 부족", "mid"
        line = f"같은 판 {len(shared)}판 — 평소 승률을 알 수 없어 차이를 계산하지 못했습니다"
    elif score >= 8:
        verdict, tone = "찰떡 궁합", "good"
        line = f"둘이 함께 뛴 {len(shared)}판 승률 {joint['win']}% · 평소보다 {delta_win:+.1f}%p"
    elif score >= -4:
        verdict, tone = "무난", "mid"
        line = f"둘이 함께 뛴 {len(shared)}판 승률 {joint['win']}% · 평소와 비슷합니다"
    else:
        verdict, tone = "합이 안 맞음", "warn"
        line = f"둘이 함께 뛴 {len(shared)}판 승률 {joint['win']}% · 평소보다 {delta_win:+.1f}%p"

    pairs = []
    for item in shared[:8]:
        row_a, row_b = item["a"], item["b"]
        match_id = str(row_a.get("match_id") or "")
        st_a, st_b = _row_stats(row_a), _row_stats(row_b)
        pairs.append({
            "match_id": match_id,
            "date": str(row_a.get("date") or row_a.get("date_match") or "")[:10],
            "map": (map_lookup or {}).get(match_id) or row_a.get("map") or "-",
            "result": "승" if st_a["win"] else "패",
            "a": {"k": st_a["k"], "d": st_a["d"]}, "b": {"k": st_b["k"], "d": st_b["d"]},
        })
    note = (f"두 사람의 최근 경기에서 같은 match_id {len(shared)}판을 골랐습니다"
            + (" · 표본이 적어 시너지 점수를 완만하게 반영했습니다" if len(shared) < 5 else ""))
    return {
        "available": True, "a": {"name": a_name, **metrics_a, "overall": all_a},
        "b": {"name": b_name, **metrics_b, "overall": all_b},
        "shared": {"games": len(shared), "wins": wins, "win": joint["win"], "kd": joint["kd"],
                   "dmg": joint["dmg"], "baseline_win": baseline, "baseline_kd": kd_base},
        "delta": {"win": delta_win, "kd": delta_kd},
        "score": round(score, 1), "verdict": verdict, "tone": tone, "label": line,
        "sample": len(shared), "confidence": round(confidence, 2),
        "pairs": pairs, "note": note,
    }
