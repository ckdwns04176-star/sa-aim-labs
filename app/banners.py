"""자체 광고 배너 — 창 안에 붙는 우측 세로(160×600) · 하단 가로(728×90).

설계
----
* **자사 배너가 기본값**이다.  지금은 광고주가 없으므로 앱 소개 + 광고·클랜·
  제휴 문의 창구로 보낸다 (``https://sa-aim-labs.pages.dev/#partner`` 와
  ``ckdwns04176@gmail.com``).
* 광고주가 생기면 **원격 JSON** 만 바꾸면 된다::

      GET https://sa-aim-labs.pages.dev/banners.json
      {"version": 1,
       "rail":   [{"id": "...", "title": "...", "sub": "...", "body": "...",
                   "cta": "...", "url": "https://...", "image": "https://...",
                   "accent": "#FF4655", "weight": 1}],
       "bottom": [{...}]}

  원격이 없거나 형식이 틀리면 조용히 자사 배너로 돌아간다.  받은 값은 파일로
  캐시해 두어 앱을 다시 열어도 마지막 배너가 그대로 보인다 (6시간마다 갱신).
* 노출·클릭 카운터는 **로컬 파일**에 항상 쌓고, ``banner_stats="remote"`` 면
  설정한 주소로도 조용히 POST 한다 (광고주 정산용 · 실패해도 앱은 그대로).
* PRO 회원은 배너를 숨길 수 있다 (``banner_hide_pro``).

이 모듈은 윈도우·리눅스 어디서든 돌고, 네트워크·파일 실패가 앱을 막지 않는다.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import paths

RAIL_SIZE = (160, 600)
BOTTOM_SIZE = (728, 90)
PARTNER_URL = "https://sa-aim-labs.pages.dev/#partner"
CONTACT_EMAIL = "ckdwns04176@gmail.com"
DEFAULT_JSON_URL = "https://sa-aim-labs.pages.dev/banners.json"
REMOTE_TTL_SECONDS = 6 * 3600
FETCH_TIMEOUT = 4.0
MAX_BYTES = 256 * 1024

DEFAULT_SETTINGS: dict[str, Any] = {
    "banner_enabled": True,
    "banner_hide_pro": True,
    "banner_source": "auto",      # auto | remote | local
    "banner_json_url": DEFAULT_JSON_URL,
    "banner_stats": "local",      # local | remote
    "banner_stats_url": "",
}


# ---------------------------------------------------------------- 자사 배너
def house_banners() -> dict[str, list[dict[str, Any]]]:
    """광고주가 없을 때 보여 줄 자사 배너 (텍스트 디자인 · 가짜 이미지 없음)."""
    return {
        "rail": [
            {
                "id": "house-rail-1", "slot": "rail", "house": True,
                "kicker": "SUDDEN ATTACK TOOL",
                "title": "서든 대시보드",
                "sub": "전적 · 상대분석 · 오버레이",
                "points": ["병영수첩 · 최근 100판", "상대 5명 솔로/파티", "인게임 오버레이"],
                "stats": [{"v": "100", "l": "최근 판 기록"}, {"v": "5", "l": "상대 스카우트"}],
                "body": "매일 쓰는 서든 도구",
                "cta": "광고·제휴 문의",
                "url": PARTNER_URL, "email": CONTACT_EMAIL,
                "accent": "#FF4655", "weight": 1,
            },
            {
                "id": "house-rail-2", "slot": "rail", "house": True,
                "kicker": "AD SLOT",
                "title": "배너 자리 안내",
                "sub": "서든 유저가 매일 여는 화면",
                "points": ["우측 160 × 600", "하단 728 × 90", "클랜 모집 · 대회 · 장비"],
                "stats": [{"v": "2", "l": "배너 자리"}, {"v": "1:1", "l": "문의 응대"}],
                "body": "클랜 · 광고 · 제휴",
                "cta": "문의하기",
                "url": PARTNER_URL, "email": CONTACT_EMAIL,
                "accent": "#38BDF8", "weight": 1,
            },
        ],
        "bottom": [
            {
                "id": "house-bottom-1", "slot": "bottom", "house": True,
                "title": "이 자리에 클랜 · 광고 배너를 넣을 수 있습니다",
                "sub": "서든어택 유저가 매일 여는 앱 · 우측 160×600 / 하단 728×90",
                "points": [],
                "body": CONTACT_EMAIL,
                "cta": "문의하기",
                "url": PARTNER_URL, "email": CONTACT_EMAIL,
                "accent": "#DFC07C", "weight": 1,
            },
        ],
    }


# ---------------------------------------------------------------- 정규화 · 검증
def _clean_text(value: Any, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _safe_url(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("https://") or text.startswith("http://"):
        return text[:500]
    return ""


def _clean_accent(value: Any, default: str = "#FF4655") -> str:
    text = str(value or "").strip()
    if len(text) in (4, 7) and text.startswith("#"):
        try:
            int(text[1:], 16)
            return text
        except ValueError:
            return default
    return default


def normalize_item(raw: Any, slot: str, index: int) -> dict[str, Any] | None:
    """원격 배너 한 건 → 화면이 쓰는 모양.  쓸 수 없으면 ``None``."""
    if not isinstance(raw, dict):
        return None
    title = _clean_text(raw.get("title"), 60)
    url = _safe_url(raw.get("url"))
    if not title or not url:
        return None
    image = _safe_url(raw.get("image"))
    item = {
        "id": _clean_text(raw.get("id") or f"{slot}-{index + 1}", 64),
        "slot": slot,
        "house": False,
        "title": title,
        "kicker": _clean_text(raw.get("kicker"), 32),
        "points": [ _clean_text(p, 40) for p in (raw.get("points") or [])[:4]
                    if _clean_text(p, 40) ] if isinstance(raw.get("points"), list) else [],
        "stats": [{"v": _clean_text(row.get("v"), 12), "l": _clean_text(row.get("l"), 20)}
                  for row in (raw.get("stats") or [])[:3] if isinstance(row, dict)] if isinstance(raw.get("stats"), list) else [],
        "sub": _clean_text(raw.get("sub"), 80),
        "body": _clean_text(raw.get("body"), 160),
        "cta": _clean_text(raw.get("cta") or "자세히 보기", 24),
        "url": url,
        "email": _clean_text(raw.get("email"), 80),
        "image": image,
        "accent": _clean_accent(raw.get("accent")),
        "weight": 1,
    }
    try:
        item["weight"] = max(1, min(20, int(raw.get("weight") or 1)))
    except (TypeError, ValueError):
        item["weight"] = 1
    return item


def normalize_payload(payload: Any) -> dict[str, list[dict[str, Any]]] | None:
    """원격 JSON 전체 검증 — 쓸 만한 배너가 하나도 없으면 ``None``."""
    if not isinstance(payload, dict):
        return None
    out: dict[str, list[dict[str, Any]]] = {"rail": [], "bottom": []}
    for slot in ("rail", "bottom"):
        rows = payload.get(slot)
        if not isinstance(rows, list):
            continue
        for index, raw in enumerate(rows[:6]):
            item = normalize_item(raw, slot, index)
            if item:
                out[slot].append(item)
    if not out["rail"] and not out["bottom"]:
        return None
    return out


# ---------------------------------------------------------------- 서비스
class BannerService:
    """배너 상태·카운터·원격 갱신을 한곳에서 관리한다."""

    def __init__(self, store: Any = None, *, opener: Callable[[str, float], Any] | None = None,
                 data_dir: Path | None = None):
        self.store = store
        self._opener = opener or self._http_json
        self._dir = Path(data_dir) if data_dir else None
        self._lock = threading.Lock()
        self._remote: dict[str, list[dict[str, Any]]] | None = None
        self._remote_at: float = 0.0
        self._remote_error: str | None = None
        self._refreshing = False
        self._last_origin = "local"

    # ---------------------------------------------------------- 파일 경로
    @property
    def dir(self) -> Path:
        return self._dir or (paths.data_dir() / "banners")

    def _cache_file(self) -> Path:
        return self.dir / "remote.json"

    def _stats_file(self) -> Path:
        return self.dir / "stats.json"

    # ---------------------------------------------------------- 설정
    def settings(self) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        if self.store is not None:
            try:
                raw = dict(self.store.settings or {})
            except Exception:  # noqa: BLE001 - 설정을 못 읽어도 기본값으로 동작
                raw = {}
        out = dict(DEFAULT_SETTINGS)
        for key, default in DEFAULT_SETTINGS.items():
            if key in raw:
                out[key] = raw[key]
        out["banner_enabled"] = bool(out.get("banner_enabled", True))
        out["banner_hide_pro"] = bool(out.get("banner_hide_pro", True))
        source = str(out.get("banner_source") or "auto").lower()
        out["banner_source"] = source if source in ("auto", "remote", "local") else "auto"
        stats = str(out.get("banner_stats") or "local").lower()
        out["banner_stats"] = stats if stats in ("local", "remote") else "local"
        out["banner_json_url"] = str(out.get("banner_json_url") or DEFAULT_JSON_URL)[:400]
        out["banner_stats_url"] = str(out.get("banner_stats_url") or "")[:400]
        return out

    # ---------------------------------------------------------- 원격
    @staticmethod
    def _http_json(url: str, timeout: float = FETCH_TIMEOUT) -> Any:
        req = urllib.request.Request(url, headers={
            "User-Agent": f"SA-Aim-Labs/{paths.APP_VERSION}",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("배너 JSON 이 너무 큽니다")
        return json.loads(raw.decode("utf-8", "replace"))

    def _load_disk_cache(self) -> dict[str, list[dict[str, Any]]] | None:
        try:
            path = self._cache_file()
            if not path.exists():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            return normalize_payload(payload)
        except (OSError, ValueError):
            return None

    def _save_disk_cache(self, payload: dict[str, list[dict[str, Any]]]) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._cache_file().write_text(
                json.dumps({**payload, "at": _now()}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _ensure_loaded(self) -> None:
        if self._remote is not None:
            return
        cached = self._load_disk_cache()
        if cached:
            self._remote = cached
            try:
                self._remote_at = self._cache_file().stat().st_mtime
            except OSError:
                self._remote_at = 0.0

    def refresh(self, force: bool = False) -> dict[str, Any]:
        """원격 배너를 (필요하면) 받아 둔다 — 실패해도 예외를 올리지 않는다."""
        cfg = self.settings()
        if cfg["banner_source"] == "local":
            with self._lock:
                self._remote_error = None
            return {"ok": False, "skipped": "local"}
        with self._lock:
            self._ensure_loaded()
            fresh = self._remote is not None and (time.time() - self._remote_at) < REMOTE_TTL_SECONDS
        if fresh and not force:
            return {"ok": True, "cached": True}
        if self._refreshing and not force:
            return {"ok": False, "busy": True}
        self._refreshing = True
        try:
            payload = self._opener(cfg["banner_json_url"], FETCH_TIMEOUT)
            data = normalize_payload(payload)
            if not data:
                raise ValueError("배너 JSON 형식이 올바르지 않습니다")
            with self._lock:
                self._remote = data
                self._remote_at = time.time()
                self._remote_error = None
            self._save_disk_cache(data)
            return {"ok": True, "rail": len(data["rail"]), "bottom": len(data["bottom"])}
        except Exception as exc:  # noqa: BLE001 - 오프라인이면 자사 배너로
            with self._lock:
                self._remote_error = str(exc)
            return {"ok": False, "message": str(exc)}
        finally:
            self._refreshing = False

    def refresh_async(self, force: bool = False) -> None:
        def _run() -> None:
            try:
                self.refresh(force=force)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_run, name="banner-refresh", daemon=True).start()

    def items(self) -> dict[str, Any]:
        """화면에 보여 줄 배너 — 원격이 있으면 원격, 없으면 자사."""
        cfg = self.settings()
        with self._lock:
            self._ensure_loaded()
            remote = self._remote
            error = self._remote_error
            updated = self._remote_at
        house = house_banners()
        rail: list[dict[str, Any]] = []
        bottom: list[dict[str, Any]] = []
        origin = "local"
        if cfg["banner_source"] != "local" and remote and (remote.get("rail") or remote.get("bottom")):
            rail = list(remote.get("rail") or [])
            bottom = list(remote.get("bottom") or [])
            origin = "remote"
            if cfg["banner_source"] == "auto":
                # 원격이 한쪽만 채웠으면 빈 자리는 자사 배너로 채운다 (빈 칸 금지)
                if not rail:
                    rail = house["rail"]
                if not bottom:
                    bottom = house["bottom"]
        if not rail:
            rail = house["rail"]
        if not bottom:
            bottom = house["bottom"]
        self._last_origin = origin
        return {
            "rail": rail[:3], "bottom": bottom[:1], "origin": origin,
            "updated_at": datetime.fromtimestamp(updated).astimezone().isoformat(timespec="seconds")
            if updated else None,
            "error": error, "sizes": {"rail": list(RAIL_SIZE), "bottom": list(BOTTOM_SIZE)},
        }

    # ---------------------------------------------------------- 표시 여부
    def hidden(self, plan: str | None = None) -> bool:
        cfg = self.settings()
        if not cfg["banner_enabled"]:
            return True
        if cfg["banner_hide_pro"] and str(plan or "").lower() == "pro":
            return True
        return False

    def state(self, plan: str | None = None) -> dict[str, Any]:
        cfg = self.settings()
        visible = not self.hidden(plan)
        payload: dict[str, Any] = {
            "enabled": cfg["banner_enabled"],
            "hide_pro": cfg["banner_hide_pro"],
            "visible": visible,
            "hidden_reason": None if visible else ("pro" if str(plan or "").lower() == "pro"
                                                  else "off"),
            "source": cfg["banner_source"],
            "stats_mode": cfg["banner_stats"],
            "partner_url": PARTNER_URL,
            "contact_email": CONTACT_EMAIL,
        }
        if visible:
            payload.update(self.items())
        else:
            payload.update({"rail": [], "bottom": [], "origin": "hidden"})
        payload["stats"] = self.stats_summary()
        return payload

    # ---------------------------------------------------------- 카운터
    def _read_stats(self) -> dict[str, Any]:
        try:
            path = self._stats_file()
            if not path.exists():
                return {"impressions": {}, "clicks": {}, "days": {}}
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError
            data.setdefault("impressions", {})
            data.setdefault("clicks", {})
            data.setdefault("days", {})
            return data
        except (OSError, ValueError):
            return {"impressions": {}, "clicks": {}, "days": {}}

    def _write_stats(self, data: dict[str, Any]) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._stats_file().write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
        except OSError:
            pass

    def stats_summary(self) -> dict[str, Any]:
        data = self._read_stats()
        return {
            "impressions": sum(int(v or 0) for v in (data.get("impressions") or {}).values()),
            "clicks": sum(int(v or 0) for v in (data.get("clicks") or {}).values()),
            "by_id": {"impressions": dict(data.get("impressions") or {}),
                      "clicks": dict(data.get("clicks") or {})},
        }

    def record(self, kind: str, ids: list[str]) -> dict[str, Any]:
        """노출(impression)·클릭(click) 카운터 — 같은 날 같은 배너는 한 번만 노출로 센다."""
        if kind not in ("impressions", "clicks"):
            raise ValueError("알 수 없는 카운터")
        clean = [str(i)[:64] for i in ids if str(i or "").strip()][:8]
        if not clean:
            return {"ok": False, "message": "배너 id 가 없습니다"}
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            data = self._read_stats()
            bucket = data.setdefault(kind, {})
            days = data.setdefault("days", {}).setdefault(today, {"impressions": 0, "clicks": 0})
            for bid in clean:
                if kind == "impressions":
                    seen = data.setdefault("seen", {}).setdefault(today, [])
                    if bid in seen:
                        continue
                    seen.append(bid)
                    data["seen"][today] = seen[-40:]
                bucket[bid] = int(bucket.get(bid, 0)) + 1
                days[kind] = int(days.get(kind, 0)) + 1
            self._write_stats(data)
        if kind == "clicks":
            self._push_remote(clean, kind)
        return {"ok": True, "kind": kind, "ids": clean, "stats": self.stats_summary()}

    def _push_remote(self, ids: list[str], kind: str) -> None:
        """광고주 정산용 원격 카운터 — 설정이 remote 일 때만, 실패는 조용히."""
        cfg = self.settings()
        url = cfg.get("banner_stats_url") or ""
        if cfg.get("banner_stats") != "remote" or not url:
            return
        body = json.dumps({"event": kind, "ids": ids, "app": paths.APP_VERSION,
                           "at": _now()}).encode("utf-8")

        def _send() -> None:
            try:
                req = urllib.request.Request(url, data=body, method="POST", headers={
                    "Content-Type": "application/json",
                    "User-Agent": f"SA-Aim-Labs/{paths.APP_VERSION}",
                })
                with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT):
                    pass
            except Exception:  # noqa: BLE001 - 카운터 전송 실패는 앱과 무관
                pass

        threading.Thread(target=_send, name="banner-stats", daemon=True).start()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
