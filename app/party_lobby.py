"""파티랭 대기실 5인 OCR · 10인 팀 판별 · 닉네임 보정 · 고정 파티 프리셋.

읽는 위치
--------
파티 대기실: 각 카드의 **윗줄 남색 닉네임 칸**만 잘라 읽는다(클랜명 칸 제외).
게임 시작 화면: Red/Blue 두 줄의 닉네임 칸을 읽어 10명을 팀별로 나눈다.

보정 순서
--------
1) OCR 원문 정리(클랜 태그·기호 제거, 공백 제거)
2) 저장된 이름(순위표 · 최근 파티원 · 프리셋)과 유사도 비교 → 후보
3) 후보를 NEXON API 로 존재 검증 (동시 조회)
4) 검증된 이름만 '확정', 아니면 '확인 필요' 로 남긴다 — 지어내지 않는다.

이 파일의 문자열 처리·팀 분할은 순수 함수라 Windows 없이 테스트한다.
"""
from __future__ import annotations

import difflib
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Sequence

from . import capture, nexon

NICK_MAX = 12
PARTY_SLOTS = 5

_CLEAN_RE = re.compile(r"[^0-9A-Za-z가-힣_.\-]")
_CLAN_RE = re.compile(r"^\[[^\]]{1,12}\]")
_VISUAL = str.maketrans({
    "0": "o", "1": "l", "|": "l", "I": "l", "5": "s", "2": "z",
    "ㅣ": "l", "ㆍ": ".", "·": ".",
})


def clean_ocr_text(text: Any) -> str:
    """OCR 한 줄 → 닉네임 후보 문자열."""
    raw = str(text or "").strip()
    raw = _CLAN_RE.sub("", raw)
    raw = raw.replace(" ", "")
    raw = _CLEAN_RE.sub("", raw)
    raw = raw.strip("[]() 	")
    return raw[:NICK_MAX]


def nick_key(text: Any) -> str:
    """비교용 키 — OCR 이 자주 헷갈리는 글자를 하나로 접는다."""
    value = str(text or "").strip().lower().replace(" ", "")
    return value.translate(_VISUAL).replace("rn", "m")


def similarity(raw: str, known: str) -> float:
    left, right = nick_key(raw), nick_key(known)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    if left in right or right in left:
        ratio = max(ratio, 0.82)
    if len(left) >= 2 and len(right) >= 2 and left[:2] == right[:2]:
        ratio += 0.05
    return min(1.0, ratio)


def best_candidate(raw: str, known: Iterable[str], *, minimum: float = 0.78) -> tuple[str, float]:
    """저장된 이름 중 가장 비슷한 하나 (없으면 원문, 0).

    v1.0.1 — 임계값 0.62 는 너무 관대해서 OCR 이 깨진 이름을 **전혀 다른 사람**으로
    바꿔 버렸다(사용자 제보 #2).  0.78 로 올리고, 길이 차가 2글자 넘거나
    앞 두 글자가 다르면 후보로 쓰지 않는다.
    """
    if isinstance(known, str):
        known = [known]
    left = nick_key(raw)
    if len(left) < 2:
        return raw, 0.0
    best, score = "", 0.0
    for name in known:
        candidate = str(name or "").strip()
        if not candidate:
            continue
        right = nick_key(candidate)
        if not right:
            continue
        if left[:2] != right[:2] and abs(len(left) - len(right)) > 1:
            continue
        value = similarity(raw, candidate)
        if value > score:
            best, score = candidate, value
    if score < minimum:
        return raw, score
    return best, score


def pick_line(lines: Sequence[str]) -> tuple[str, list[str]]:
    """OCR 줄들 중 닉네임으로 가장 그럴듯한 하나 + 전체 후보."""
    candidates: list[str] = []
    for line in lines:
        cleaned = clean_ocr_text(line)
        if len(cleaned) >= 2:
            candidates.append(cleaned)
    if not candidates:
        return "", []
    def score(text: str) -> tuple[int, int]:
        has_korean = bool(re.search(r"[가-힣]", text))
        return (1 if has_korean else 0, len(text))
    candidates.sort(key=score, reverse=True)
    return candidates[0], candidates


def team_of(rows: Sequence[dict[str, Any]], me: str) -> dict[str, Any]:
    """Red/Blue 10명 중 내 닉네임이 있는 팀과 상대 5명을 가른다."""
    mine_key = nick_key(me)
    mine: list[dict[str, Any]] = []
    opponents: list[dict[str, Any]] = []
    my_team = ""
    for row in rows:
        if not row.get("name"):
            continue
        if nick_key(row["name"]) == mine_key or similarity(row["name"], me) >= 0.9:
            my_team = str(row.get("team") or "")
    for row in rows:
        if not row.get("name"):
            continue
        if my_team and row.get("team") == my_team:
            mine.append(row)
        elif my_team:
            opponents.append(row)
    if my_team and len(mine) > PARTY_SLOTS:
        mine = mine[:PARTY_SLOTS]
    return {"my_team": my_team, "mine": mine, "opponents": opponents[:PARTY_SLOTS],
            "found_me": bool(my_team)}


def validate_names(client: nexon.NexonClient, names: Sequence[str], known: Iterable[str],
                   *, workers: int = 5) -> list[dict[str, Any]]:
    """OCR 이름 5개를 API 로 검증한다 — **확실한 것만** 조회한다.

    v2.0.0 — 억지 유사 매칭 금지
     · OCR 원문이 저장된 이름과 유사도 ``CONFIDENT_MIN``(0.92) 미만이면
       보정 후보로 바꾸지 않는다.  애매한 이름은 API 조회를 **건너뛰고**
       ``status="needs_check"`` (화면에 '확인 필요')로 남긴다.
     · 원문 그대로 API 에 있는 이름만 ``ok``, 0.92 이상으로 아주 가까운
       보정만 ``corrected`` 로 인정한다.
     · API 가 돌려준 이름이 OCR 원문과 너무 다르면(오타 수준 아님) 그 행도
       ``unknown`` 으로 두고 이름을 지어내지 않는다.
    """
    known_list = [str(n).strip() for n in known if str(n or "").strip()]
    CONFIDENT_MIN = 0.92
    AMBIGUOUS_MIN = 0.62

    def _unknown(slot: int, raw: str, score: float, reason: str) -> dict[str, Any]:
        return {"slot": slot, "raw": raw, "name": raw, "ouid": None,
                "status": "needs_check", "score": round(score, 2), "skip_reason": reason}

    def _one(entry: tuple[int, str]) -> dict[str, Any]:
        slot, raw = entry
        if not raw:
            return {"slot": slot, "raw": "", "name": "", "ouid": None,
                    "status": "empty", "score": 0.0}
        corrected, score = best_candidate(raw, known_list, minimum=CONFIDENT_MIN)
        confident = score >= CONFIDENT_MIN and corrected and corrected != raw
        # 애매 구간(0.78~0.92)은 어느 쪽인지 확신할 수 없다 → API 조회도 하지 않고 확인 필요
        if known_list and AMBIGUOUS_MIN <= score < CONFIDENT_MIN:
            return _unknown(slot, raw, score, "닉네임이 애매해 조회를 건너뜀")

        # 1) OCR 원문 그대로 먼저 확인 — 이게 가장 안전한 경로
        try:
            ouid = client.resolve_ouid(raw)
            basic = client.user_basic(ouid)
            name = str(basic.get("user_name") or raw)
        except nexon.NexonError:
            ouid, basic, name = None, None, ""
        if ouid:
            if similarity(name, raw) < 0.55:
                # 원문과 전혀 다른 사람이 나왔다 → 조회 결과를 쓰지 않는다
                return _unknown(slot, raw, score, "OCR 원문과 API 이름이 너무 다름")
            return {"slot": slot, "raw": raw, "name": name, "ouid": ouid,
                    "status": "ok", "score": round(score, 2),
                    "clan": (basic or {}).get("clan_name") or ""}

        # 2) 원문이 API 에 없다 — 0.92 이상으로 확실할 때만 보정 조회
        if not confident:
            return _unknown(slot, raw, score, "유사도가 낮아 조회를 건너뜀")
        try:
            ouid = client.resolve_ouid(corrected)
            basic = client.user_basic(ouid)
            name = str(basic.get("user_name") or corrected)
        except nexon.NexonError:
            return _unknown(slot, raw, score, "보정 후보도 조회되지 않음")
        if similarity(name, raw) < 0.70:
            return _unknown(slot, raw, score, "보정 후보가 원문과 너무 다음")
        return {"slot": slot, "raw": raw, "name": name, "ouid": ouid,
                "status": "corrected", "score": round(score, 2),
                "clan": basic.get("clan_name") or ""}

    rows: list[dict[str, Any]] = []
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(names) or 1)))
    try:
        for row in pool.map(_one, list(enumerate(names))):
            rows.append(row)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    rows.sort(key=lambda r: r["slot"])
    return rows


def scan_party(client: nexon.NexonClient, game: tuple[int, int, int, int],
               known: Iterable[str], *, calib: dict[str, Any] | None = None) -> dict[str, Any]:
    """대기실 5칸을 읽고 → 보정 → API 검증까지 한 번에."""
    from . import overlay

    rects = overlay.party_card_rects(game, calib)
    shot = capture.ocr_regions([(r["x"], r["y"], r["w"], r["h"]) for r in rects])
    if not shot.get("ok"):
        return {"ok": False, "supported": bool(shot.get("supported")), "rows": [],
                "message": shot.get("message") or "대기실 화면을 읽지 못했습니다"}
    names: list[str] = []
    raws: list[str] = []
    for lines in shot.get("lines") or []:
        name, candidates = pick_line(lines)
        names.append(name)
        raws.append(" / ".join(candidates[:3]))
    rows = validate_names(client, names, known)
    for row, raw in zip(rows, raws):
        row["lines"] = raw
    found = len([r for r in rows if r.get("ouid")])
    needs = len([r for r in rows if r.get("status") == "needs_check"])
    message = f"대기실 5칸 · {found}명 확인" if found else "닉네임을 읽지 못했습니다"
    if needs:
        message += f" · 확인 필요 {needs}명"
    return {"ok": found > 0, "supported": True, "rows": rows,
            "names": [r.get("name") for r in rows if r.get("ouid")],
            "confirmed": found, "needs_check": needs, "message": message}


# ---------------------------------------------------------------- 10인 팀 스캔
TEAM_ROWS = {
    "red": (0.2632, 0.3470, 0.4700, 0.0230),
    "blue": (0.2632, 0.7720, 0.4700, 0.0240),
}


def team_name_rects(game: tuple[int, int, int, int]) -> list[dict[str, Any]]:
    """게임 시작 10인 화면 — Red/Blue 각 5칸 (비율 고정)."""
    results: list[dict[str, Any]] = []
    x, y, w, h = (int(v) for v in game[:4])
    for team, (left, top, row_w, row_h) in TEAM_ROWS.items():
        card_w = row_w / PARTY_SLOTS
        for slot in range(PARTY_SLOTS):
            # 이름표는 카드 폭의 76% 가운데
            cx = x + int(round((left + card_w * (slot + 0.5)) * w))
            half = int(round(card_w * w * 0.38))
            results.append({
                "team": team, "slot": slot,
                "x": cx - half, "y": y + int(round(top * h)),
                "w": max(40, half * 2), "h": max(12, int(round(row_h * h))),
            })
    return results


def scan_teams(game: tuple[int, int, int, int], known: Iterable[str],
               client: nexon.NexonClient) -> dict[str, Any]:
    rects = team_name_rects(game)
    shot = capture.ocr_regions([(r["x"], r["y"], r["w"], r["h"]) for r in rects])
    if not shot.get("ok"):
        return {"ok": False, "rows": [], "message": shot.get("message") or "10인 화면을 읽지 못했습니다"}
    rows: list[dict[str, Any]] = []
    for meta, lines in zip(rects, shot.get("lines") or []):
        name, candidates = pick_line(lines)
        if not name:
            continue
        corrected, score = best_candidate(name, known)
        rows.append({"team": meta["team"], "slot": meta["slot"], "raw": name,
                     "name": corrected, "score": round(score, 2),
                     "lines": " / ".join(candidates[:3])})
    return {"ok": bool(rows), "rows": rows,
            "message": f"10인 화면 · {len(rows)}명 인식"}


def last_match_teams(client: nexon.NexonClient, my_nick: str) -> dict[str, Any]:
    """직전 판 API 상세에서 내 팀 5명 + 상대 5명."""
    if not my_nick:
        return {"ok": False, "rows": [], "message": "닉네임이 없습니다"}
    ouid = client.resolve_ouid(my_nick)
    rows = client.recent_matches(ouid, 1)
    if not rows:
        return {"ok": False, "rows": [], "message": "최근 경기를 찾지 못했습니다"}
    latest = rows[0]
    detail = client.match_detail_cached(latest.get("match_id")) or client.match_detail(latest.get("match_id"))
    players = (detail or {}).get("match_detail") or []
    my_team = None
    for player in players:
        if nick_key(player.get("user_name")) == nick_key(my_nick):
            my_team = player.get("team_id")
    if my_team is None:
        return {"ok": False, "rows": [], "message": "직전 판에서 내 팀을 찾지 못했습니다"}
    out = []
    for player in players:
        name = str(player.get("user_name") or "").strip()
        if not name:
            continue
        team = "mine" if player.get("team_id") == my_team else "opp"
        out.append({"team": team, "name": name, "raw": name, "score": 1.0,
                    "slot": len(out)})
    mine = [r for r in out if r["team"] == "mine"][:PARTY_SLOTS]
    opp = [r for r in out if r["team"] == "opp"][:PARTY_SLOTS]
    return {"ok": bool(mine or opp), "rows": mine + opp,
            "map": (detail or {}).get("match_map") or "",
            "message": f"직전 판 · 우리 팀 {len(mine)}명 · 상대 {len(opp)}명"}


# ---------------------------------------------------------------- 프리셋
def clean_preset_name(value: Any, fallback: str = "고정 파티") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:20]


def clean_members(value: Any, limit: int = PARTY_SLOTS) -> list[str]:
    rows: list[str] = []
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\n/]+", value)]
    for item in (value or []):
        name = str(item or "").strip()
        if not name:
            continue
        if name.lower() in {r.lower() for r in rows}:
            continue
        rows.append(name[:NICK_MAX])
    return rows[:limit]


def preset_payload(presets: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in presets:
        members = clean_members(row.get("members"))
        if not members:
            continue
        out.append({"name": clean_preset_name(row.get("name")),
                    "members": members, "count": len(members),
                    "created": str(row.get("created") or "")})
    return out
