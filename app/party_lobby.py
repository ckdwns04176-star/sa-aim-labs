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
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Sequence

from . import capture, nexon, paths

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
        raw_line = str(line or "").strip()
        # PowerShell 예외 표식은 닉네임이 아니라 내부 오류다.
        if raw_line.startswith("__ERR__") or raw_line.startswith("ERR:"):
            continue
        cleaned = clean_ocr_text(raw_line)
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


def pick_line_variants(variants: dict[str, Sequence[str]],
                       known: Iterable[str] = ()) -> tuple[str, list[str], dict[str, list[str]]]:
    """4개 OCR 경로의 후보를 신뢰도 가중 투표로 합친다.

    청록 마스크는 닉네임 글자색을 직접 이용하므로 가장 높은 가중치를 준다.
    단, 마스크 한 경로가 오인식할 수 있어 원본·확대·고대비 경로의 일치도도
    함께 반영한다. 저장된 닉네임 유사도는 동점일 때만 사용한다.
    """
    weights = {"original": 1, "enlarged": 2, "contrast": 2, "cyan_mask": 5}
    known_list = [str(n).strip() for n in known if str(n or "").strip()]
    totals: dict[str, int] = {}
    frequencies: dict[str, int] = {}
    display: dict[str, str] = {}
    method_hits: dict[str, set[str]] = {}
    cleaned_by_method: dict[str, list[str]] = {}

    for method in ("cyan_mask", "contrast", "enlarged", "original"):
        method_lines = variants.get(method) or []
        if isinstance(method_lines, str):
            method_lines = [method_lines]
        cleaned_rows: list[str] = []
        for line in method_lines:
            raw_line = str(line or "").strip()
            if not raw_line or raw_line.startswith(("__ERR__", "ERR:")):
                continue
            cleaned = clean_ocr_text(raw_line)
            if len(cleaned) < 2:
                continue
            cleaned_rows.append(cleaned)
            key = nick_key(cleaned)
            if not key:
                continue
            totals[key] = totals.get(key, 0) + weights.get(method, 1)
            frequencies[key] = frequencies.get(key, 0) + 1
            display.setdefault(key, cleaned)
            method_hits.setdefault(key, set()).add(method)
        cleaned_by_method[method] = cleaned_rows

    if not totals:
        return "", [], cleaned_by_method

    def rank(key: str) -> tuple[int, float, int, int, int, int]:
        text = display[key]
        known_score = max((similarity(text, item) for item in known_list), default=0.0)
        has_korean = int(bool(re.search(r"[가-힣]", text)))
        # 한 방법의 반복보다 서로 다른 전처리의 합의를 우선한다.
        return (totals[key], len(method_hits.get(key, set())), known_score,
                frequencies[key], has_korean, -len(text))

    ordered = sorted(totals, key=rank, reverse=True)
    return display[ordered[0]], [display[key] for key in ordered], cleaned_by_method


def _write_party_debug(debug_dir, debug_path, report: dict[str, Any]) -> str:
    """마지막 파티 OCR의 슬롯 캡처와 진단 JSON을 하나의 ZIP으로 묶는다."""
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        json_path = debug_dir / "diagnostic.json"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        with zipfile.ZipFile(debug_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(debug_dir.iterdir()):
                if file_path.is_file():
                    archive.write(file_path, arcname=file_path.name)
        return str(debug_path)
    except Exception as exc:  # noqa: BLE001 - 진단 저장 실패가 OCR을 막지 않는다
        return f"debug save failed: {exc}"


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
    r"""대기실 5칸 캡처 → 4경로 OCR → 후보 투표 → NEXON API 확인.

    실제 캡처 PNG와 방법별 OCR 문자열을 %APPDATA%\SA_Aim_Labs\logs에
    party_ocr_debug.zip으로 보관한다. 캡처 실패와 OCR 실패, API 검증 실패를 구분한다.
    """
    from . import overlay

    known_list = [str(n).strip() for n in known if str(n or "").strip()]
    rects = overlay.party_card_rects(game, calib)
    debug_dir = paths.log_dir() / "party_ocr_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    # 직전 진단 이미지가 현재 결과와 섞이지 않게 슬롯 파일만 초기화한다.
    for old_file in debug_dir.glob("slot_*.png"):
        try:
            old_file.unlink()
        except OSError:
            pass

    shot = capture.ocr_regions(
        [(r["x"], r["y"], r["w"], r["h"]) for r in rects],
        timeout=35.0,
        debug_dir=str(debug_dir),
    )

    if not shot.get("ok"):
        rows = [{"slot": i, "raw": "", "name": "", "ouid": None,
                 "status": "empty", "score": 0.0, "lines": ""}
                for i in range(PARTY_SLOTS)]
        report = {"game": list(game[:4]), "rects": rects, "capture": shot,
                  "rows": rows, "stage": "capture_or_ocr_engine_failed"}
        debug_file = _write_party_debug(debug_dir, paths.log_dir() / "party_ocr_debug.zip", report)
        return {"ok": False, "supported": bool(shot.get("supported")), "rows": rows,
                "recognized": 0, "confirmed": 0, "debug_file": debug_file,
                "message": shot.get("message") or "화면 캡처/OCR 실행에 실패했습니다"}

    variant_items = shot.get("variant_lines") or []
    flat_items = shot.get("lines") or []
    names: list[str] = []
    raws: list[str] = []
    slot_debug: list[dict[str, Any]] = []
    for slot in range(PARTY_SLOTS):
        variants = variant_items[slot] if slot < len(variant_items) else {}
        if not variants:
            fallback_lines = flat_items[slot] if slot < len(flat_items) else []
            variants = {"original": fallback_lines}
        name, candidates, by_method = pick_line_variants(variants, known_list)
        names.append(name)
        raws.append(" / ".join(candidates[:5]))
        slot_debug.append({
            "slot": slot + 1,
            "rect": rects[slot] if slot < len(rects) else {},
            "capture_ok": (shot.get("capture_ok") or [True] * PARTY_SLOTS)[slot]
                          if slot < len(shot.get("capture_ok") or []) else False,
            "cyan_pixels": (shot.get("cyan_pixels") or [0] * PARTY_SLOTS)[slot]
                           if slot < len(shot.get("cyan_pixels") or []) else 0,
            "name_rect": (shot.get("name_rects") or [{}] * PARTY_SLOTS)[slot]
                         if slot < len(shot.get("name_rects") or []) else {},
            "ocr_candidates": candidates[:5],
            "ocr_by_method": by_method,
            "selected": name,
        })

    api_validation_error = ""
    try:
        rows = validate_names(client, names, known_list)
    except Exception as exc:  # noqa: BLE001 - API 실패가 OCR 원문/진단을 지우지 않게 한다
        api_validation_error = f"{type(exc).__name__}: {exc}"
        rows = [{"slot": i, "raw": name, "name": name, "ouid": None,
                 "status": "needs_check" if name else "empty", "score": 0.0,
                 "skip_reason": "NEXON API 검증 중 오류"}
                for i, name in enumerate(names)]
    for index, (row, raw) in enumerate(zip(rows, raws)):
        row["lines"] = raw
        row["ocr_by_method"] = slot_debug[index]["ocr_by_method"]
        row["capture_ok"] = slot_debug[index]["capture_ok"]
        row["cyan_pixels"] = slot_debug[index]["cyan_pixels"]

    recognized = sum(1 for name in names if name)
    found = sum(1 for row in rows if row.get("ouid"))
    needs = sum(1 for row in rows if row.get("status") == "needs_check")
    captures = shot.get("capture_ok") or []
    capture_count = sum(1 for ok in captures if ok)
    if capture_count == 0:
        message = "게임 화면 캡처 실패 · 진단 ZIP에서 CopyFromScreen 오류를 확인하세요"
        stage = "screen_capture_failed"
    elif recognized == 0:
        message = "화면 캡처 성공 · 닉네임 OCR 결과 0/5 · 진단 이미지 확인 필요"
        stage = "ocr_returned_no_nicknames"
    elif found == 0:
        message = f"닉네임 OCR {recognized}/5 · NEXON API 확인 0명 · 철자/API 검증 단계 확인 필요"
        stage = "ocr_succeeded_api_validation_failed"
    else:
        message = f"닉네임 OCR {recognized}/5 · NEXON API {found}/5명 확인"
        stage = "success" if found == PARTY_SLOTS else "partial_success"
    if needs:
        message += f" · 확인 필요 {needs}명"

    if api_validation_error:
        message += " · API 오류: " + api_validation_error[:120]
        stage = "api_validation_error"

    report = {
        "game": list(game[:4]),
        "capture": {
            "ok": True,
            "message": shot.get("message"),
            "capture_ok": captures,
            "rects": shot.get("rects") or rects,
            "name_rects": shot.get("name_rects") or [],
            "cyan_pixels": shot.get("cyan_pixels") or [],
            "errors": shot.get("errors") or [],
            "debug_dir": str(debug_dir),
        },
        "stage": stage,
        "api_validation_error": api_validation_error,
        "slots": slot_debug,
        "rows": [{k: row.get(k) for k in ("slot", "raw", "name", "status", "score", "skip_reason")}
                 for row in rows],
    }
    debug_file = _write_party_debug(debug_dir, paths.log_dir() / "party_ocr_debug.zip", report)
    return {
        "ok": found > 0,
        "supported": True,
        "rows": rows,
        "names": [row.get("name") for row in rows if row.get("ouid")],
        "recognized_names": names,
        "recognized": recognized,
        "confirmed": found,
        "needs_check": needs,
        "stage": stage,
        "debug_file": debug_file,
        "message": message,
    }


# ---------------------------------------------------------------- 10인 팀 스캔
# 10인 카운트다운 화면(Red Team 윗줄 / Blue Team 아랫줄)은 해상도가 높을수록
# 화면 대비 창이 작아진다.  고정 비율 슬롯 10개를 찍으면 QHD 이상에서 빗나가므로,
# 게임 창 **중앙을 한 번** 찍고 OCR 이 돌려준 줄 좌표로 위/아래를 나눈다.
UI_LABEL_RE = re.compile(r"^(?:red|blue)?team$|^준비$|^ready$|^win$|^lose$", re.IGNORECASE)
MIN_ROW_MEMBERS = 3


def _line_row(line: dict[str, Any]) -> dict[str, Any] | None:
    """OCR 한 줄 → 닉네임 후보 행 (UI 라벨·숫자·너무 짧은 줄은 버린다)."""
    text = str((line or {}).get("text") or "").strip()
    if not text:
        return None
    cleaned = clean_ocr_text(text)
    if len(cleaned) < 2 or cleaned.isdigit():
        return None
    if UI_LABEL_RE.match(cleaned) or UI_LABEL_RE.match(text.replace(" ", "")):
        return None
    try:
        x = int((line or {}).get("x") or 0)
        y = int((line or {}).get("y") or 0)
        w = int((line or {}).get("w") or 0)
        h = int((line or {}).get("h") or 0)
    except (TypeError, ValueError):
        return None
    return {"text": text, "name": cleaned, "x": x, "y": y, "w": max(0, w), "h": max(0, h)}


def cluster_rows(lines: Sequence[dict[str, Any]], *, tolerance: int = 0) -> list[dict[str, Any]]:
    """OCR 줄들을 Y 좌표로 묶어 '팀 한 줄' 단위로 만든다 (해상도 무관).

    각 팀 줄은 카드 5개가 같은 Y 에 있으므로 3개 이상 모인 묶음을 팀 줄로 본다.
    """
    items = [row for row in (_line_row(line) for line in (lines or [])) if row]
    items.sort(key=lambda row: (row["y"] + row["h"] * 0.5, row["x"]))
    clusters: list[dict[str, Any]] = []
    for item in items:
        center = item["y"] + item["h"] * 0.5
        tol = max(6, int(tolerance or item["h"] * 0.75))
        if clusters and abs(center - clusters[-1]["center"]) <= tol:
            group = clusters[-1]
            group["items"].append(item)
            group["center"] = sum(i["y"] + i["h"] * 0.5 for i in group["items"]) / len(group["items"])
        else:
            clusters.append({"center": center, "items": [item], "tol": tol})
    for group in clusters:
        group["items"].sort(key=lambda row: row["x"])
    return clusters


def _dedupe_x(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 자리에서 두 번 읽힌 줄(전처리 2경로)을 하나로 — X 가 가까우면 같은 사람."""
    out: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda row: row["x"]):
        duplicate = False
        for kept in out:
            if abs(kept["x"] - item["x"]) <= max(12, int(min(kept["w"], item["w"]) * 0.35)):
                duplicate = True
                break
        if not duplicate:
            out.append(item)
    return out


def _team_row(cluster: dict[str, Any], limit: int = PARTY_SLOTS) -> list[dict[str, Any]]:
    items = _dedupe_x(cluster.get("items") or [])[:limit]
    rows: list[dict[str, Any]] = []
    for slot, item in enumerate(items):
        rows.append({"slot": slot, "name": item["name"], "raw": item["text"],
                     "x": item["x"], "y": item["y"], "w": item["w"], "h": item["h"]})
    return rows


def split_top_bottom(lines: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """10인 화면 OCR 줄 → 윗줄 5명 / 아랫줄 5명 (순수 함수).

    * 같은 Y 에 3명 이상 모인 줄만 팀 줄로 인정한다(팀 라벨·점수·카운트 숫자 제외).
    * 위/아래는 화면 Y 좌표로만 결정한다 — 해상도·창 크기가 달라도 그대로다.
    """
    clusters = cluster_rows(lines)
    groups = [group for group in clusters if len(group["items"]) >= MIN_ROW_MEMBERS]
    top: dict[str, Any] | None = None
    bottom: dict[str, Any] | None = None
    if len(groups) >= 2:
        top, bottom = groups[0], groups[1]
    elif len(groups) == 1:
        top = groups[0]
        rest = [group for group in clusters if group is not groups[0]]
        wide = [group for group in rest if len(group["items"]) >= 2]
        if wide:
            bottom = max(wide, key=lambda group: len(group["items"]))
    elif clusters:
        # 한 줄로 뭉쳤으면(줄이 매우 가까움) X 순서로 앞 5명 / 뒤 5명.
        merged = max(clusters, key=lambda group: len(group["items"]))
        if len(merged["items"]) >= 6:
            items = _dedupe_x(merged["items"])
            top = {"items": items[:PARTY_SLOTS]}
            bottom = {"items": items[PARTY_SLOTS:PARTY_SLOTS * 2]}
    top_rows = _team_row(top) if top else []
    bottom_rows = _team_row(bottom) if bottom else []
    return {"top": top_rows, "bottom": bottom_rows,
            "count": len(top_rows) + len(bottom_rows),
            "clusters": len(clusters)}


def scan_teams(game: tuple[int, int, int, int], known: Iterable[str],
               client: nexon.NexonClient, *, debug: bool = False) -> dict[str, Any]:
    """게임 시작 10인 카운트다운 — 중앙 1회 캡처 → 줄 좌표로 상/하단 분리 → API 확인.

    고정 비율 슬롯 대신 넓은 중앙 캡처를 쓰므로 1920×1080·2560×1440 등
    해상도가 달라져 창이 작아져도 상단 5명 / 하단 5명을 위치로 나눌 수 있다.
    """
    from . import overlay

    known_list = [str(n).strip() for n in known if str(n or "").strip()]
    rect = overlay.countdown_capture_rect(game)
    debug_dir = None
    if debug:
        debug_dir = paths.log_dir() / "team_ocr_debug"
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            debug_dir = None
    shot = capture.ocr_region((rect["x"], rect["y"], rect["w"], rect["h"]),
                             scale=2, timeout=8.0, debug_dir=debug_dir)
    split = split_top_bottom(shot.get("lines") or [])

    names = [row["name"] for row in (split["top"] + split["bottom"])]
    validated: list[dict[str, Any]] = []
    api_error = ""
    if names:
        try:
            validated = validate_names(client, names, known_list, workers=5)
        except Exception as exc:  # noqa: BLE001 - API 실패가 OCR 원문을 지우지 않게
            api_error = f"{type(exc).__name__}: {exc}"
            validated = [{"slot": i, "raw": name, "name": name, "ouid": None,
                          "status": "needs_check"} for i, name in enumerate(names)]
    by_index = {i: row for i, row in enumerate(validated)}

    def _team(rows: Sequence[dict[str, Any]], offset: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for local, row in enumerate(rows):
            found = by_index.get(offset + local) or {}
            out.append({**row, "slot": local,
                        "name": found.get("name") or row.get("name") or "",
                        "ouid": found.get("ouid"),
                        "status": found.get("status") or "needs_check"})
        return out

    top_rows = _team(split["top"], 0)
    bottom_rows = _team(split["bottom"], len(split["top"]))
    confirmed = [r for r in top_rows + bottom_rows if r.get("ouid")]
    count = len(top_rows) + len(bottom_rows)
    if count == 0:
        message = "10인 화면에서 닉네임 줄을 찾지 못했습니다"
    else:
        message = f"상단 {len(top_rows)}명 · 하단 {len(bottom_rows)}명 인식"
        if confirmed:
            message += f" · API {len(confirmed)}명 확인"
        if api_error:
            message += " · API 오류: " + api_error[:100]
    return {
        "ok": count > 0,
        "top": top_rows,
        "bottom": bottom_rows,
        "rows": top_rows + bottom_rows,
        "count": count,
        "confirmed": len(confirmed),
        "capture_rect": rect,
        "ocr_count": int(shot.get("count") or 0),
        "debug_dir": str(debug_dir or ""),
        "message": message,
    }


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
