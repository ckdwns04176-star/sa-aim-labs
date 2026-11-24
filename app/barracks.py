"""서든 병영수첩(barracks.sa.nexon.com) 주소 해석 — v1.5.0.

NEXON Open API 의 `ouid`(32자 hex)와 병영수첩 URL 의 `user_nexon_sn`
(`96E8A3306F1061D2SA` 처럼 16자 hex + `SA`)은 **다른 값**이다.  그래서 지금까지
`내 병영수첩` 버튼이 엉뚱한 주소(`/{ouid}/detail`)를 열었다.

여기서는 병영수첩이 스스로 쓰는 검색 API 로 닉네임 → `user_nexon_sn` 을 찾고,
프로필 응답에서 클랜 ID·프로필 사진 주소까지 함께 저장한다.  한 번 찾은 값은
`data.json` 의 `barracks_ids` 에 캐시해서 두 번 부르지 않는다.

병영수첩은 봇 차단(WAF)이 있어 요청이 거절될 수 있다.  그때는 예외를 올리지 않고
`{"ok": False, "reason": ...}` 을 돌려주며, 화면은 저장된 값이나 검색 페이지로
물러난다.  표준 라이브러리 + 이미 의존성에 있는 requests 만 쓴다.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from typing import Any

log = logging.getLogger("barracks")

BASE = "https://barracks.sa.nexon.com"
SN_RE = re.compile(r"([0-9A-Fa-f]{16}SA)")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def parse_sn(text: str) -> str:
    """병영수첩 URL·ID 어디서든 `user_nexon_sn` 만 뽑는다."""
    match = SN_RE.search(str(text or ""))
    return match.group(1).upper() if match else ""


def profile_url(sn: str, tab: str = "detail") -> str:
    sn = parse_sn(sn) or str(sn or "").strip().upper()
    if not sn:
        return BASE
    tab = tab if tab in ("detail", "match", "mastery") else "detail"
    return f"{BASE}/{sn}/{tab}"


def clan_url(clan_id: str) -> str:
    clan_id = str(clan_id or "").strip()
    return f"https://clan.sa.nexon.com/{urllib.parse.quote(clan_id)}" if clan_id else ""


def search_page() -> str:
    return BASE


def _headers() -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Referer": BASE + "/",
        "Origin": BASE,
        "X-Requested-With": "XMLHttpRequest",
    }


def _walk(node: Any) -> list[dict[str, Any]]:
    """JSON 어디에 있든 dict 를 전부 모은다 (응답 모양이 바뀌어도 버티게)."""
    out: list[dict[str, Any]] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            out.append(cur)
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return out


def _find_sn(payload: Any, nickname: str = "") -> str:
    target = (nickname or "").strip().lower()
    best = ""
    for row in _walk(payload):
        for key, value in row.items():
            if not isinstance(value, str):
                continue
            sn = parse_sn(value)
            if not sn and re.fullmatch(r"[0-9A-Fa-f]{16}SA", value.strip()):
                sn = value.strip().upper()
            if not sn:
                continue
            name = ""
            for name_key in ("user_nick", "user_name", "nickname", "nick", "name"):
                if isinstance(row.get(name_key), str):
                    name = str(row[name_key]).strip()
                    break
            if not target or name.lower() == target:
                return sn
            best = best or sn
    return best


def _find_value(payload: Any, names: tuple[str, ...], *, kind: str = "text") -> str:
    lowered = {n.lower() for n in names}
    for row in _walk(payload):
        for key, value in row.items():
            if str(key).lower() not in lowered:
                continue
            if isinstance(value, (str, int, float)) and str(value).strip():
                text = str(value).strip()
                if kind == "url" and not text.lower().startswith("http"):
                    continue
                return text
    return ""


class Resolver:
    """닉네임 ↔ user_nexon_sn 해석 + 프로필(클랜 ID·사진) 조회."""

    def __init__(self, cache: dict[str, Any] | None = None, *, timeout: float = 6.0,
                 save: Any = None) -> None:
        self.cache: dict[str, Any] = cache if isinstance(cache, dict) else {}
        self.timeout = timeout
        self.save = save
        self.last_error: str | None = None

    # ---------------------------------------------------------- 저장소
    def _key(self, nickname: str) -> str:
        return str(nickname or "").strip().lower()

    def get(self, nickname: str) -> dict[str, Any]:
        row = self.cache.get(self._key(nickname))
        return dict(row) if isinstance(row, dict) else {}

    def put(self, nickname: str, row: dict[str, Any]) -> dict[str, Any]:
        key = self._key(nickname)
        if not key or not row.get("sn"):
            return row
        merged = {**self.get(nickname), **row}
        self.cache[key] = merged
        if callable(self.save):
            try:
                self.save()
            except Exception:  # noqa: BLE001 - 캐시 저장 실패가 화면을 막지 않는다
                log.debug("barracks cache save failed", exc_info=True)
        return merged

    def remember_url(self, text: str, nickname: str = "") -> dict[str, Any]:
        """사용자가 붙여넣은 병영수첩 URL 만으로도 연결해 둔다."""
        sn = parse_sn(text)
        if not sn:
            return {}
        row = {"sn": sn, "profile": profile_url(sn), "source": "paste"}
        return self.put(nickname or sn, row)

    # ---------------------------------------------------------- HTTP
    def _session(self):
        import requests  # requirements.txt 에 이미 있는 의존성

        session = requests.Session()
        session.headers.update(_headers())
        try:
            session.get(BASE + "/", timeout=self.timeout)      # 쿠키 워밍업
        except Exception:  # noqa: BLE001 - 워밍업 실패는 그대로 진행
            pass
        return session

    def _search(self, nickname: str) -> str:
        """병영수첩 검색 API(post) → user_nexon_sn."""
        import requests

        nick = str(nickname or "").strip()
        if not nick:
            return ""
        encoded = urllib.parse.quote(nick, safe="")
        session = self._session()
        bodies = (
            {"searchKeyword": nick, "page": 1, "searchType": "0"},
            {"searchWord": nick, "page": 1},
            {"keyword": nick, "page": 1},
            {"searchKeyword": nick},
        )
        for body in bodies:
            try:
                resp = session.post(f"{BASE}/api/Search/GetSearchAll/{encoded}/1", json=body,
                                    timeout=self.timeout)
                if resp.status_code == 200:
                    sn = _find_sn(resp.json(), nick)
                    if sn:
                        return sn
            except (requests.RequestException, ValueError, json.JSONDecodeError):
                continue
        return ""

    def _profile(self, sn: str) -> dict[str, Any]:
        import requests

        session = self._session()
        try:
            resp = session.get(f"{BASE}/api/Profile/GetProfileMain/{sn}", timeout=self.timeout)
            if resp.status_code != 200:
                return {}
            payload = resp.json()
        except (requests.RequestException, ValueError):
            return {}
        nickname = _find_value(payload, ("user_nick", "user_name", "nickname", "nick"))
        clan_id = ""
        for row in _walk(payload):
            for key, value in row.items():
                if not isinstance(value, str):
                    continue
                if "clan.sa.nexon.com/" in value:
                    clan_id = value.rstrip("/").split("/")[-1]
                    break
            if clan_id:
                break
        if not clan_id:
            clan_id = _find_value(payload, ("clan_id", "clanid", "guild_id"))
        photo = _find_value(payload, ("profile_img", "profile_image", "user_img", "picture",
                                      "profile_image_url", "img_url"), kind="url")
        return {"sn": sn, "nickname": nickname, "clan_id": clan_id, "photo": photo,
                "profile": profile_url(sn)}

    # ---------------------------------------------------------- 공개
    def lookup(self, nickname: str, *, refresh: bool = False) -> dict[str, Any]:
        nick = str(nickname or "").strip()
        if not nick:
            return {"ok": False, "reason": "닉네임이 없습니다"}
        sn = parse_sn(nick)
        if sn:
            row = self.get(sn) or {"sn": sn}
            row.setdefault("profile", profile_url(sn))
            row["ok"] = True
            return self.put(sn, row)
        cached = self.get(nick)
        if cached.get("sn") and not refresh:
            cached.setdefault("profile", profile_url(cached["sn"]))
            return {**cached, "ok": True, "source": cached.get("source") or "cache"}
        # 병영수첩이 막히면 매번 6초씩 기다리지 않게 30분 동안은 재시도하지 않는다
        miss = self.cache.get("__miss__") or {}
        if not refresh and (time.time() - float(miss.get("at") or 0)) < 1800:
            return {"ok": False, "reason": self.last_error or "병영수첩 주소를 아직 찾지 못했습니다",
                    "search": search_page()}
        found = self._search(nick)
        if not found:
            self.cache["__miss__"] = {"at": time.time()}
            self.last_error = ("병영수첩에서 닉네임을 찾지 못했습니다 "
                               "(사이트가 요청을 막았을 수 있습니다 · 병영수첩 주소를 붙여넣으면 바로 연결됩니다)")
            return {"ok": False, "reason": self.last_error, "search": search_page()}
        extra = self._profile(found) if refresh or not self.get(nick) else {}
        row = {"sn": found, **extra, "profile": profile_url(found), "source": "search"}
        return {**self.put(nick, row), "ok": True}

    def lookup_sn(self, sn: str) -> dict[str, Any]:
        """`/{sn}/detail` URL → 닉네임 역조회 (#13)."""
        clean = parse_sn(sn)
        if not clean:
            return {"ok": False, "reason": "병영수첩 ID(…SA)를 찾지 못했습니다"}
        cached = self.get(clean)
        if cached.get("nickname") and cached.get("sn") == clean:
            return {**cached, "ok": True}
        row = self._profile(clean)
        if not row.get("nickname"):
            return {"ok": False, "reason": "병영수첩 프로필에서 닉네임을 읽지 못했습니다",
                    "sn": clean, "profile": profile_url(clean)}
        return {**self.put(row["nickname"], row), "ok": True}
