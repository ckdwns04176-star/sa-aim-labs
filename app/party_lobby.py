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


def pick_line(lines: Sequence[str], known: Iterable[str] = ()) -> tuple[str, list[str]]:
    """여러 OCR 전처리 결과에서 닉네임 후보를 투표로 선택한다.

    원본/2배 픽셀 확대/회색조 OCR 결과가 여러 개 들어온다.
    같은 문자열이 반복되면 우선하고, 표가 같으면 저장된 이름과의 유사도로
    타이브레이크한다. 후보 원문은 API 검증 단계에서 다시 확인한다.
    """
    candidates: list[str] = []
    for line in lines:
        cleaned = clean_ocr_text(line)
        if len(cleaned) >= 2:
            candidates.append(cleaned)
    if not candidates:
        return "", []

    known_list = [str(n).strip() for n in known if str(n or "").strip()]
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    display: dict[str, str] = {}
    for index, text in enumerate(candidates):
        key = nick_key(text)
        counts[key] = counts.get(key, 0) + 1
        first_seen.setdefault(key, index)
        display.setdefault(key, text)

    def score(key: str) -> tuple[int, float, int, int, int]:
        text = display[key]
        known_score = max((similarity(text, item) for item in known_list), default=0.0)
        has_korean = int(bool(re.search(r"[가-힣]", text)))
        korean_count = len(re.findall(r"[가-힣]", text))
        # 같은 OCR 결과의 반복을 가장 중요하게 보고, 저장 이름 유사도는 동률일 때만 사용한다.
        return (counts[key], known_score, has_korean, korean_count, -first_seen[key])

    winner = max(counts, key=score)
    ordered = sorted(counts, key=score, reverse=True)
    return display[winner], [display[key] for key in ordered]


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


def _rank_known_candidates(raw: str, known: Iterable[str]) -> list[tuple[str, float]]:
    """OCR 원문과 저장된 닉네임의 유사도 순위. 점수 동률은 입력 순서를 유지한다."""
    left = nick_key(raw)
    if len(left) < 2:
        return []
    ranked: list[tuple[str, float]] = []
    seen: set[str] = set()
    for item in known:
        candidate = str(item or "").strip()
        right = nick_key(candidate)
        if not candidate or not right or right in seen:
            continue
        seen.add(right)
        if left[:2] != right[:2] and abs(len(left) - len(right)) > 1:
            continue
        ranked.append((candidate, similarity(raw, candidate)))
    ranked.sort(key=lambda row: row[1], reverse=True)
    return ranked


def validate_names(client: nexon.NexonClient, names: Sequence[str], known: Iterable[str],
                   *, workers: int = 5) -> list[dict[str, Any]]:
    """OCR 이름을 먼저 원문 그대로 조회하고, 실패하면 유일한 유사 후보만 API로 검증한다.

    원문 조회 전에 '애매하다'며 API 호출을 건너뛰던 기존 순서를 수정했다.
    오인식을 보정할 때는 0.72 이상 + 차순위와 0.08 이상 차이가 있어야 후보를
    조회하고, NEXON API가 반환한 user_name이 후보와 정확히 일치해야 확정한다.
    """
    known_list = [str(n).strip() for n in known if str(n or "").strip()]
    CORRECTION_MIN = 0.72
    CORRECTION_MARGIN = 0.08

    def _unknown(slot: int, raw: str, score: float, reason: str) -> dict[str, Any]:
        return {"slot": slot, "raw": raw, "name": raw, "ouid": None,
                "status": "needs_check", "score": round(score, 2), "skip_reason": reason}

    def _resolve_exact(name: str):
        try:
            ouid = client.resolve_ouid(name)
            basic = client.user_basic(ouid)
            actual = str(basic.get("user_name") or name)
            return ouid, basic, actual
        except nexon.NexonError:
            return None, None, ""

    def _one(entry: tuple[int, str]) -> dict[str, Any]:
        slot, raw = entry
        if not raw:
            return {"slot": slot, "raw": "", "name": "", "ouid": None,
                    "status": "empty", "score": 0.0}

        ranked = _rank_known_candidates(raw, known_list)
        corrected, score = ranked[0] if ranked else (raw, 0.0)
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = score - runner_up

        # 1) OCR 원문을 항상 먼저 조회한다. 유사 후보가 있어도 정확한 원문 조회를 막지 않는다.
        ouid, basic, name = _resolve_exact(raw)
        if ouid:
            if similarity(name, raw) < 0.55:
                return _unknown(slot, raw, score, "OCR 원문과 API 이름이 너무 다름")
            return {"slot": slot, "raw": raw, "name": name, "ouid": ouid,
                    "status": "ok", "score": round(score, 2),
                    "clan": (basic or {}).get("clan_name") or ""}

        # 2) 원문 조회가 실패하면, 한 후보가 충분히 앞설 때만 후보를 API로 확인한다.
        confident = (corrected and corrected != raw and score >= CORRECTION_MIN
                     and margin >= CORRECTION_MARGIN)
        if not confident:
            reason = "저장된 닉네임 후보가 없거나 후보 간 유사도가 비슷함"
            return _unknown(slot, raw, score, reason)

        ouid, basic, name = _resolve_exact(corrected)
        if not ouid:
            return _unknown(slot, raw, score, "유사 후보를 NEXON API에서 찾지 못함")
        # 후보로 조회한 경우 API의 실제 user_name이 후보 문자열과 같아야 자동 보정한다.
        if nick_key(name) != nick_key(corrected):
            return _unknown(slot, raw, score, "API 닉네임이 보정 후보와 일치하지 않음")
        return {"slot": slot, "raw": raw, "name": name, "ouid": ouid,
                "status": "corrected", "score": round(score, 2),
                "clan": (basic or {}).get("clan_name") or ""}

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

    known_list = [str(n).strip() for n in known if str(n or "").strip()]
    rects = overlay.party_card_rects(game, calib)
    shot = capture.ocr_regions([(r["x"], r["y"], r["w"], r["h"]) for r in rects])
    if not shot.get("ok"):
        return {"ok": False, "supported": bool(shot.get("supported")), "rows": [],
                "message": shot.get("message") or "대기실 화면을 읽지 못했습니다"}
    names: list[str] = []
    raws: list[str] = []
    for lines in shot.get("lines") or []:
        name, candidates = pick_line(lines, known_list)
        names.append(name)
        raws.append(" / ".join(candidates[:3]))
    rows = validate_names(client, names, known_list)
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
    known_list = [str(n).strip() for n in known if str(n or "").strip()]
    rects = team_name_rects(game)
    shot = capture.ocr_regions([(r["x"], r["y"], r["w"], r["h"]) for r in rects])
    if not shot.get("ok"):
        return {"ok": False, "rows": [], "message": shot.get("message") or "10인 화면을 읽지 못했습니다"}
    rows: list[dict[str, Any]] = []
    for meta, lines in zip(rects, shot.get("lines") or []):
        name, candidates = pick_line(lines, known_list)
        if not name:
            continue
        corrected, score = best_candidate(name, known_list)
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
