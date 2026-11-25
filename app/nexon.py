"""NEXON Open API client (Sudden Attack) with pacing, retries and disk cache.

Endpoints used
--------------
GET /suddenattack/v1/id            ?user_name=
GET /suddenattack/v1/user/basic    ?ouid=
GET /suddenattack/v1/user/rank     ?ouid=
GET /suddenattack/v1/user/tier     ?ouid=&match_type=랭크전 솔로|랭크전 파티
GET /suddenattack/v1/user/recent-info ?ouid=
GET /suddenattack/v1/match         ?ouid=&match_mode=폭파미션&match_type=
GET /suddenattack/v1/match-detail  ?match_id=

The test key allows roughly ten requests per second; requests are therefore
paced (default 0.13 s apart) and 429 responses are retried with backoff.  Match
details never change, so they are cached on disk for a week.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable, Sequence

from . import paths, scoring

BASE_URL = "https://open.api.nexon.com"
# v1.0.0 — 앱에는 넥슨 키가 들어가지 않는다.  모든 조회는 Cloudflare Workers
# 프록시를 거치고, 사용자가 개인 키를 직접 넣은 경우에만 넥슨으로 바로 쏜다.
PROXY_URL = "https://sa-aim-proxy.ckdwns04176.workers.dev"
PROXY_MIN_INTERVAL = 0.04
MATCH_MODE = "폭파미션"
MATCH_TYPES = ("랭크전 솔로", "랭크전 파티")
RECENT_N = 100
MATCH_ROWS = 100   # 맵별 매치 히스토리 팝업까지 쓰려면 최근 100판이 필요하다
MAP_MIN = 5
DETAIL_DEPTH = 0   # 순위표는 API 상세 호출 0건 (디스크 캐시에 있는 상세만 사용)

Progress = Callable[[int, int, str], None]


class NexonError(RuntimeError):
    def __init__(self, message: str, *, quota: bool = False):
        super().__init__(message)
        self.quota = quota


_ERR_KO = {
    "OPENAPI00001": "서버 내부 오류입니다 · 잠시 후 다시 시도해 주세요",
    "OPENAPI00002": "권한이 없습니다 · API 키를 확인해 주세요",
    "OPENAPI00003": "유효하지 않은 식별자입니다 (ouid)",
    "OPENAPI00004": "요청 값이 올바르지 않습니다",
    "OPENAPI00005": "API 키가 올바르지 않습니다 · open.api.nexon.com 에서 다시 발급해 주세요",
    "OPENAPI00006": "등록되지 않은 API 경로입니다",
    "OPENAPI00007": "오늘 넥슨 API 일일 호출 한도(1,000건)를 초과했어요. 자정 이후 초기화됩니다",
    "OPENAPI00009": "데이터를 준비 중입니다 · 잠시 후 다시 시도해 주세요",
    "OPENAPI00010": "서버 점검 중입니다",
    "OPENAPI00011": "서비스 점검 중입니다",
}
QUOTA_CODES = ("OPENAPI00007",)
MAX_CALLS_HINT = 1000


def friendly_error(message: str) -> str:
    """NEXON error code → 사용자가 읽을 수 있는 한국어 문장."""
    text = str(message or "")
    for code, korean in _ERR_KO.items():
        if code in text:
            suffix = f" ({code})" if code not in QUOTA_CODES else f" ({code})"
            return f"{korean}{suffix}"
    return text


# --------------------------------------------------------------------- tier names
_TIER_KR = {
    "BRONZE": "브론즈", "SILVER": "실버", "GOLD": "골드",
    "MASTER": "마스터", "GRAND MASTER": "그랜드마스터", "GRANDMASTER": "그랜드마스터",
    "LEGEND": "레전드", "RANKER": "불레전드", "HIGH RANKER": "별레전드",
    "UNRANKED": "언랭크", "UNRANK": "언랭크",
}
_ROMAN = {"1": "I", "2": "II", "3": "III", "I": "I", "II": "II", "III": "III"}


def kr_tier(name: str | None) -> str:
    """'GRAND MASTER II' -> '그랜드마스터 II'."""
    if not name:
        return "언랭크"
    s = str(name).strip().upper().replace("_", " ")
    if s in ("", "-", "NONE", "NULL", "UNRANKED"):
        return "언랭크"
    family, _, suffix = s.rpartition(" ")
    if family in _TIER_KR and suffix in _ROMAN:
        return f"{_TIER_KR[family]} {_ROMAN[suffix]}"
    if s in _TIER_KR:
        return _TIER_KR[s]
    # e.g. "GRANDMASTERII"
    for key in sorted(_TIER_KR, key=len, reverse=True):
        if s.startswith(key.replace(" ", "")):
            rest = s[len(key.replace(" ", "")):]
            if rest in _ROMAN:
                return f"{_TIER_KR[key]} {_ROMAN[rest]}"
    return str(name)


# --------------------------------------------------------------------- disk cache
class _DiskCache:
    def __init__(self, path: os.PathLike[str], max_entries: int = 4000):
        self.path = path
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self.data: dict[str, dict[str, Any]] = {}
        try:
            if os.path.exists(path):
                self.data = json.loads(open(path, encoding="utf-8").read())
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str) -> Any | None:
        row = self.data.get(key)
        if not row:
            return None
        if row.get("ttl") and time.time() - row.get("at", 0) > row["ttl"]:
            return None
        return row.get("data")

    def put(self, key: str, data: Any, ttl: int | None) -> None:
        with self._lock:
            self.data[key] = {"at": time.time(), "ttl": ttl, "data": data}
            if len(self.data) > self.max_entries:
                # drop the oldest quarter
                rows = sorted(self.data.items(), key=lambda kv: kv[1].get("at", 0))
                for k, _ in rows[: len(rows) // 4]:
                    self.data.pop(k, None)
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = str(self.path) + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(self.data, fh, ensure_ascii=False)
                os.replace(tmp, self.path)
            except OSError:
                pass


# --------------------------------------------------------------------- client
class NexonClient:
    MIN_INTERVAL = 0.13

    def __init__(
        self,
        api_key: str = "",
        *,
        proxy: str | None = None,
        cache_dir: str | os.PathLike[str] | None = None,
        timeout: float = 20.0,
        min_interval: float | None = None,
        progress: Progress | None = None,
        force: bool = False,
    ):
        self.api_key = (api_key or "").strip()
        self.proxy = (PROXY_URL if proxy is None else str(proxy)).strip().rstrip("/")
        self.using_proxy = bool(self.proxy) and not self.api_key
        self.timeout = timeout
        if min_interval is None:
            min_interval = PROXY_MIN_INTERVAL if self.using_proxy else self.MIN_INTERVAL
        self.min_interval = min_interval
        self.progress = progress
        self.force = force          # 전적 새로고침: 목록/프로필 캐시 TTL 무시
        self._lock = threading.Lock()
        self._last = 0.0
        self.calls = 0
        cache_root = str(cache_dir or paths.cache_dir())
        os.makedirs(cache_root, exist_ok=True)
        self._cache_api = _DiskCache(os.path.join(cache_root, "nexon_api.json"), 600)
        self._cache_detail = _DiskCache(os.path.join(cache_root, "nexon_match_detail.json"), 5000)

    @property
    def endpoint(self) -> str:
        return "proxy" if self.using_proxy else "direct"

    # ------------------------------------------------------------ http
    def _pace(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if self.using_proxy:
            url = f"{self.proxy}{path}?{query}"
            headers = {"Accept": "application/json",
                       "User-Agent": f"SA-Aim-Labs/{paths.APP_VERSION}"}
        elif self.api_key:
            url = f"{BASE_URL}{path}?{query}"
            headers = {"x-nxopen-api-key": self.api_key,
                       "Accept": "application/json",
                       "User-Agent": f"SA-Aim-Labs/{paths.APP_VERSION}"}
        else:
            raise NexonError("NEXON API 연결이 설정되지 않았습니다 · 설정 탭에서 개인 키를 넣거나 프록시를 확인해 주세요")
        last_error: Exception | None = None
        for attempt in range(4):
            self._pace()
            req = urllib.request.Request(url, headers=headers)
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                body = json.loads(raw) if raw else {}
                if isinstance(body, dict) and body.get("error"):
                    err = body["error"]
                    if isinstance(err, dict):
                        detail = f"{err.get('name', 'ERROR')} · {err.get('message', '')}".strip(" ·")
                        code = str(err.get("name") or "")
                    else:
                        detail, code = str(err), ""
                    if "not_allowed" in detail:
                        raise NexonError("API 프록시가 이 요청을 막았습니다 · 잠시 후 다시 시도해 주세요")
                    raise NexonError(friendly_error(detail))
                return body
            except urllib.error.HTTPError as exc:
                detail = ""
                code = ""
                try:
                    payload = json.loads(exc.read().decode("utf-8", "replace"))
                    err = payload.get("error") or {}
                    if isinstance(err, dict):
                        code = str(err.get("name") or "")
                        detail = f"{err.get('name', '')} · {err.get('message', '')}".strip(" ·")
                    else:
                        detail = str(err)
                except Exception:  # noqa: BLE001 - body may be empty
                    pass
                last_error = NexonError(
                    friendly_error(detail or f"HTTP {exc.code}"),
                    quota=(exc.code == 429 or code in QUOTA_CODES),
                )
                if exc.code == 429 or code in QUOTA_CODES:
                    time.sleep(1.2 * (attempt + 1))
                    self.min_interval = min(0.5, self.min_interval * 1.5)
                    continue
                if exc.code in (500, 502, 503, 504):
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise last_error
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = NexonError(f"네트워크 오류 · {exc}")
                time.sleep(0.6 * (attempt + 1))
        raise last_error or NexonError("NEXON API 요청 실패")

    def _cached(self, cache: _DiskCache, key: str, path: str, params: dict[str, Any], ttl: int | None) -> dict[str, Any]:
        if self.force and cache is self._cache_api:
            # "전적 불러오기" 를 직접 눌렀을 때는 목록/프로필을 서버에서 다시 받아온다.
            # (경기 상세는 변하지 않으므로 항상 캐시를 쓴다 — 호출 한도 절약)
            data = self._request(path, params)
            cache.put(key, data, ttl)
            return data
        hit = cache.get(key)
        if hit is not None:
            return hit
        data = self._request(path, params)
        cache.put(key, data, ttl)
        return data

    # ------------------------------------------------------------ endpoints
    def resolve_ouid(self, nickname: str) -> str:
        nickname = (nickname or "").strip()
        if not nickname:
            raise NexonError("서든어택 닉네임을 입력해 주세요")
        body = self._cached(self._cache_api, f"id:{nickname.lower()}", "/suddenattack/v1/id",
                            {"user_name": nickname}, ttl=3600)
        ouid = body.get("ouid")
        if not ouid:
            raise NexonError(f"닉네임 '{nickname}' 의 전적을 찾지 못했습니다")
        return str(ouid)

    def user_name_of(self, ouid: str) -> str | None:
        """ouid → 현재 인게임 닉네임 (닉네임 변경 추적용)."""
        try:
            return str(self.user_basic(ouid).get("user_name") or "") or None
        except NexonError:
            return None

    def user_basic(self, ouid: str) -> dict[str, Any]:
        return self._cached(self._cache_api, f"basic:{ouid}", "/suddenattack/v1/user/basic", {"ouid": ouid}, ttl=1800)

    def user_rank(self, ouid: str) -> dict[str, Any]:
        return self._cached(self._cache_api, f"rank:{ouid}", "/suddenattack/v1/user/rank", {"ouid": ouid}, ttl=1800)

    def user_tier(self, ouid: str) -> dict[str, Any]:
        body = self._cached(self._cache_api, f"tier:{ouid}", "/suddenattack/v1/user/tier",
                            {"ouid": ouid, "match_type": MATCH_TYPES[0]}, ttl=900)
        if "party_rank_match_score" not in body:
            party = self._cached(self._cache_api, f"tier-party:{ouid}", "/suddenattack/v1/user/tier",
                                 {"ouid": ouid, "match_type": MATCH_TYPES[1]}, ttl=900)
            body = {**body, **{k: v for k, v in party.items() if k not in body}}
        return body

    def user_recent(self, ouid: str) -> dict[str, Any]:
        return self._cached(self._cache_api, f"recent:{ouid}", "/suddenattack/v1/user/recent-info",
                            {"ouid": ouid}, ttl=900)

    def matches(self, ouid: str, match_type: str, ttl: int = 600) -> list[dict[str, Any]]:
        body = self._cached(self._cache_api, f"match:{ouid}:{match_type}", "/suddenattack/v1/match",
                            {"ouid": ouid, "match_mode": MATCH_MODE, "match_type": match_type}, ttl=ttl)
        rows = body.get("match") or []
        return rows if isinstance(rows, list) else []

    def recent_matches(self, ouid: str, limit: int = RECENT_N) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for mtype in MATCH_TYPES:
            try:
                rows.extend(self.matches(ouid, mtype))
            except NexonError:
                continue
        rows.sort(key=match_time, reverse=True)
        return rows[:limit]

    def match_detail(self, match_id: str) -> dict[str, Any] | None:
        if not match_id:
            return None
        try:
            return self._cached(self._cache_detail, f"d:{match_id}", "/suddenattack/v1/match-detail",
                                {"match_id": match_id}, ttl=30 * 24 * 3600)
        except NexonError:
            return None

    def match_detail_cached(self, match_id: str) -> dict[str, Any] | None:
        """디스크 캐시에 있는 경기 상세만 돌려준다 (API 호출 0건)."""
        if not match_id:
            return None
        return self._cache_detail.get(f"d:{match_id}")

    def cache_stats(self) -> dict[str, int]:
        return {"api": len(self._cache_api.data), "detail": len(self._cache_detail.data), "calls": self.calls}


# --------------------------------------------------------------------- aggregation
def match_time(m: dict[str, Any]) -> float:
    raw = str(m.get("date_match") or "").strip()
    if not raw:
        return 0.0
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _player_detail(detail: dict[str, Any] | None, nickname: str) -> dict[str, Any]:
    out = {"map": None, "damage": None}
    if not detail:
        return out
    out["map"] = detail.get("match_map") or None
    target = (nickname or "").strip().lower()
    for p in detail.get("match_detail") or []:
        if str(p.get("user_name") or "").strip().lower() == target:
            try:
                out["damage"] = float(p.get("damage") or 0)
            except (TypeError, ValueError):
                out["damage"] = None
            break
    return out


def _weak_index(win: float, kd: float, dmg: float) -> float:
    score = win * 0.5 + min(100.0, kd) * 0.3 + min(100.0, dmg / 20.0) * 0.2
    return round((100.0 - score) * 10) / 10


def aggregate(rows: Sequence[dict[str, Any]], details: Sequence[dict[str, Any] | None], nickname: str) -> dict[str, Any]:
    kills = deaths = wins = 0
    dmg_sum = 0.0
    dmg_n = 0
    maps: dict[str, dict[str, Any]] = {}
    for idx, m in enumerate(rows):
        k = int(m.get("kill") or 0)
        d = int(m.get("death") or 0)
        win = str(m.get("match_result")) == "1"
        kills += k
        deaths += d
        wins += 1 if win else 0
        one = _player_detail(details[idx] if idx < len(details) else None, nickname)
        dmg = one["damage"]
        if dmg is not None:
            dmg_sum += dmg
            dmg_n += 1
        key = one["map"] or "기타"
        agg = maps.setdefault(key, {"n": 0, "w": 0, "k": 0, "d": 0, "ds": 0.0, "dc": 0})
        agg["n"] += 1
        agg["w"] += 1 if win else 0
        agg["k"] += k
        agg["d"] += d
        if dmg is not None:
            agg["ds"] += dmg
            agg["dc"] += 1

    n = len(rows)
    kd = round(kills / (kills + deaths) * 100, 1) if (kills + deaths) else 0.0
    win_rate = round(wins / n * 100, 1) if n else 0.0
    avg_dmg = int(round(dmg_sum / dmg_n)) if dmg_n else 0

    map_rows = []
    for name, a in maps.items():
        m_win = (a["w"] / a["n"] * 100) if a["n"] else 0.0
        m_kd = (a["k"] / (a["k"] + a["d"]) * 100) if (a["k"] + a["d"]) else 0.0
        m_dmg = (a["ds"] / a["dc"]) if a["dc"] else 0.0
        map_rows.append({
            "map": name, "games": a["n"],
            "win": round(m_win, 1), "kd": round(m_kd, 1), "dmg": round(m_dmg, 1),
            "weak": _weak_index(m_win, m_kd, m_dmg),
        })
    solid = sorted([m for m in map_rows if m["games"] >= MAP_MIN], key=lambda m: -m["weak"])
    thin = sorted([m for m in map_rows if m["games"] < MAP_MIN], key=lambda m: m["games"])
    for i, m in enumerate(solid + thin, start=1):
        m["rk"] = i
    return {
        "games": n, "kills": kills, "deaths": deaths, "wins": wins,
        "win": win_rate, "kd": kd, "dmg": avg_dmg,
        "maps": solid + thin, "weak": solid[:3], "map_count": len(solid), "thin_count": len(thin),
    }


def _match_players(detail: dict[str, Any] | None, nickname: str) -> list[list[Any]]:
    """경기 상세 → [[이름, 팀, 킬, 데스, 딜량], …] (나 포함 · 병영수첩 느낌의 전적 팝업용).

    10명까지만 남겨 data.json 이 커지지 않게 한다.  상세 캐시가 없으면 빈 목록.
    """
    out: list[list[Any]] = []
    my_team = None
    target = (nickname or "").strip().lower()
    for p in (detail or {}).get("match_detail") or []:
        name = str(p.get("user_name") or "").strip()
        if not name:
            continue
        team = str(p.get("team_id") if p.get("team_id") is not None else "-")
        if name.lower() == target:
            my_team = team
    for p in (detail or {}).get("match_detail") or []:
        name = str(p.get("user_name") or "").strip()
        if not name:
            continue
        team = str(p.get("team_id") if p.get("team_id") is not None else "-")
        side = "?" if my_team is None else ("팀" if team == my_team else "적")
        try:
            dmg = int(round(float(p.get("damage") or 0)))
        except (TypeError, ValueError):
            dmg = 0
        out.append([name, side, int(p.get("kill") or 0), int(p.get("death") or 0), dmg])
        if len(out) >= 10:
            break
    return out


def _match_rows(rows: Sequence[dict[str, Any]], details: Sequence[dict[str, Any] | None], nickname: str,
                limit: int = MATCH_ROWS) -> list[dict[str, Any]]:
    out = []
    for idx, m in enumerate(rows[:limit]):
        detail = details[idx] if idx < len(details) else None
        one = _player_detail(detail, nickname)
        k = int(m.get("kill") or 0)
        d = int(m.get("death") or 0)
        raw = str(m.get("date_match") or "")
        try:
            dt = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
            date_txt, time_txt = dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
        except ValueError:
            date_txt, time_txt = "-", "-"
        out.append({
            "date": date_txt, "time": time_txt,
            "type": "솔로" if "솔로" in str(m.get("match_type")) else "파티",
            "mode": str(m.get("match_mode") or ""),
            "map": one["map"] or "-",
            "result": "승" if str(m.get("match_result")) == "1" else "패",
            "k": k, "d": d,
            "kd": round(k / (k + d) * 100, 1) if (k + d) else 0.0,
            "damage": None if one["damage"] is None else int(round(one["damage"])),
            "players": _match_players(detail, nickname),
        })
    return out


def _detail_jobs(client: NexonClient, rows: Sequence[dict[str, Any]], progress: Progress | None, label: str,
                 depth: int | None = None, cache_only: bool = False) -> list[dict[str, Any] | None]:
    """경기 상세 수집 — 이미 본 match_id 는 디스크 캐시에서 꺼내 API 호출을 아낀다."""
    target = rows if depth is None else rows[:depth]
    total = len(target)
    out: list[dict[str, Any] | None] = []
    for i, m in enumerate(target):
        match_id = m.get("match_id")
        cached = client.match_detail_cached(match_id) if match_id else None
        if cached is not None:
            out.append(cached)
        elif cache_only:
            out.append(None)
        else:
            out.append(client.match_detail(match_id))
        if progress and (i + 1) % 5 == 0:
            progress(i + 1, total, label)
    if progress and total:
        progress(total, total, label)
    return out


# --------------------------------------------------------------------- public builders
def build_barracks(client: NexonClient, nickname: str, progress: Progress | None = None) -> dict[str, Any]:
    nickname = (nickname or "").strip()
    ouid = client.resolve_ouid(nickname)
    if progress:
        progress(1, 6, "계정 확인")
    basic = client.user_basic(ouid)
    rank = client.user_rank(ouid)
    recent = client.user_recent(ouid)
    tier = client.user_tier(ouid)
    if progress:
        progress(2, 6, "랭크 정보")
    rows = client.recent_matches(ouid, RECENT_N)
    details = _detail_jobs(client, rows, progress, "경기 상세", depth=None)
    agg = aggregate(rows, details, nickname)
    if progress:
        progress(6, 6, "집계 완료")

    solo_rp = tier.get("solo_rank_match_score")
    party_rp = tier.get("party_rank_match_score")
    created = str(basic.get("user_date_create") or "")
    payload = {
        "nickname": basic.get("user_name") or nickname,
        "ouid": ouid,
        "basic": {
            "name": basic.get("user_name") or nickname,
            "clan": basic.get("clan_name") or "-",
            "title": basic.get("title_name") or "-",
            "manner": basic.get("manner_grade") or "-",
            "created": created[:10].replace("-", ".") if created else "-",
        },
        "rank": {
            "grade": rank.get("grade") or "-",
            "grade_ranking": rank.get("grade_ranking"),
            "season_grade": rank.get("season_grade") or "-",
            "season_ranking": rank.get("season_grade_ranking"),
        },
        "recent": {
            "win": recent.get("recent_win_rate"),
            "kd": round(float(recent.get("recent_kill_death_rate") or 0), 1),
            "assault": recent.get("recent_assault_rate"),
            "sniper": recent.get("recent_sniper_rate"),
        },
        "tier": {
            "solo": {"name": kr_tier(tier.get("solo_rank_match_tier")), "rp": int(solo_rp or 0)},
            "party": {"name": kr_tier(tier.get("party_rank_match_tier")), "rp": int(party_rp or 0)},
        },
        "stats": {k: agg[k] for k in ("games", "kills", "deaths", "wins", "win", "kd", "dmg")},
        "maps": agg["maps"],
        "weak": agg["weak"],
        "matches": _match_rows(rows, details, nickname),
        "updated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    # 🧭 최근 100판 플레이 코칭 — 이미 받은 지표만으로 계산한다(추가 API 0건)
    try:
        payload["play_coaching"] = scoring.play_coaching(payload)
    except Exception:  # noqa: BLE001 - 코칭 실패가 병영수첩을 막지 않는다
        payload["play_coaching"] = []
    return payload


def fetch_member(client: NexonClient, nickname: str, *, ouid: str | None = None,
                 depth: int = DETAIL_DEPTH, progress: Progress | None = None) -> dict[str, Any]:
    """One leaderboard row — ouid 가 있으면 닉변해도 끊기지 않는다."""
    nickname = (nickname or "").strip()
    base = {"name": nickname, "updated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    try:
        ouid = str(ouid).strip() if ouid else client.resolve_ouid(nickname)
        basic = client.user_basic(ouid)
        real_name = str(basic.get("user_name") or "").strip() or nickname
        tier = client.user_tier(ouid)
        recent = client.user_recent(ouid)
        rows = client.recent_matches(ouid, RECENT_N)
        # 순위표: 호출 한도를 아끼려고 캐시된 경기 상세만 사용 (API 호출 0건)
        details = _detail_jobs(client, rows, progress, real_name, depth=(depth or None), cache_only=True)
        agg = aggregate(rows, details, real_name)
        solo = int(tier.get("solo_rank_match_score") or 0)
        party = int(tier.get("party_rank_match_score") or 0)
        sniper = float(recent.get("recent_sniper_rate") or 0)
        assault = float(recent.get("recent_assault_rate") or 0)
        return {
            **base,
            "ok": True,
            "name": real_name,
            "ouid": ouid,
            "renamed_from": nickname if nickname and nickname != real_name else None,
            "clan": basic.get("clan_name") or "친구",
            "weapon": "sniper" if sniper >= assault else "rifle",
            "solo": solo,
            "party": party or None,
            "kd": agg["kd"] if agg["games"] else None,
            "dmg": agg["dmg"] if agg["games"] and any(d is not None for d in details) else None,
            "win": agg["win"] if agg["games"] else None,
            "games": agg["games"],
            "solo_tier": kr_tier(tier.get("solo_rank_match_tier")),
            "party_tier": kr_tier(tier.get("party_rank_match_tier")),
            "unranked": solo <= 0,
        }
    except NexonError as exc:
        return {**base, "ok": False, "error": str(exc), "unranked": True, "solo": 0, "party": None,
                "kd": None, "dmg": None, "win": None, "weapon": "sniper", "clan": "친구",
                "ouid": ouid}


def build_leaderboard(client: NexonClient, members: Iterable[Any], progress: Progress | None = None,
                      depth: int = DETAIL_DEPTH) -> list[dict[str, Any]]:
    """members may be names or stored member dicts (ouid/me preserved)."""
    rows = list(members)[:20]
    out: list[dict[str, Any]] = []
    total = len(rows) * 4
    for i, entry in enumerate(rows):
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            ouid = entry.get("ouid")
        else:
            name, ouid = str(entry).strip(), None

        def _p(done: int, all_: int, label: str, _i=i) -> None:
            if progress:
                progress(_i * 4 + min(4, max(1, round(done / max(1, all_) * 4))), total, label)
        row = fetch_member(client, name, ouid=ouid, depth=depth, progress=_p)
        if isinstance(entry, dict) and entry.get("me"):
            row["me"] = True
        out.append(row)
        if progress:
            progress((i + 1) * 4, total, row.get("name") or name)
    return out


def leaderboard_payload(members: Sequence[dict[str, Any]], client: NexonClient | None = None) -> list[dict[str, Any]]:
    """Normalise stored members for the UI (tier marks, unranked flags)."""
    from . import scoring

    rows = []
    for m in members:
        solo = m.get("solo") or 0
        band = scoring.band_of(solo)
        party = m.get("party")
        rows.append({
            **m,
            "solo": solo,
            "party": party,
            "solo_tier": m.get("solo_tier") or band["n"],
            "solo_img": band["img"],
            "party_tier": m.get("party_tier") or (scoring.party_band_of(party)["n"] if party else "언랭크"),
            "unranked": solo <= 0,
            "kd": m.get("kd"),
            "dmg": m.get("dmg"),
            "win": m.get("win"),
        })
    return rows


def safe_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def fmt_ts(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return _dt.datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def hhmm(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return _dt.datetime.fromisoformat(iso).astimezone().strftime("%H:%M")
    except ValueError:
        return iso


def ceil_pct(a: float) -> float:
    return math.ceil(a * 10) / 10


def key_digest(api_key: str) -> str:
    return hashlib.sha1((api_key or "").encode()).hexdigest()[:8]


# --------------------------------------------------------------------- 캐시 정리 (v1.0.0)
CACHE_TTL_DAYS = 30
_CACHE_FILES = ("nexon_api.json", "nexon_match_detail.json")


def purge_expired_cache(days: int = CACHE_TTL_DAYS, cache_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """30일 지난 **넥슨 매치 캐시 항목만** 지운다.

    사용자의 훈련 기록·최고 기록·잔디·파티 프리셋은 ``data.json`` 에 있고
    여기서는 절대 건드리지 않는다.  지우는 대상은 cache/ 안의 넥슨 응답뿐이다.
    """
    root = str(cache_dir or paths.cache_dir())
    cutoff = time.time() - max(1, int(days)) * 86400
    removed = 0
    kept = 0
    touched: list[str] = []
    for name in _CACHE_FILES:
        path = os.path.join(root, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        fresh: dict[str, Any] = {}
        for key, row in data.items():
            try:
                at = float((row or {}).get("at") or 0)
            except (TypeError, ValueError):
                at = 0.0
            if at and at < cutoff:
                removed += 1
            else:
                fresh[key] = row
                kept += 1
        if len(fresh) == len(data):
            continue
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(fresh, handle, ensure_ascii=False)
            os.replace(tmp, path)
            touched.append(name)
        except OSError:
            continue
    # cache/ 안의 오래된 match 임시 파일도 30일이 지나면 정리 (data.json 은 제외)
    try:
        for entry in os.scandir(root):
            if not entry.is_file():
                continue
            if entry.name in _CACHE_FILES or not entry.name.lower().startswith(("match_cache", "nexon_")):
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
                    removed += 1
                    touched.append(entry.name)
            except OSError:
                continue
    except OSError:
        pass
    return {"ok": True, "days": int(days), "removed": removed, "kept": kept,
            "files": touched}
