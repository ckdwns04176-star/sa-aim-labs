"""앱 자동 업데이트 알림 — GitHub Releases `latest` 를 조용히 확인한다.

앱이 켜지면 백그라운드 스레드에서 한 번, 그리고 `업데이트 확인` 버튼을 누르면
다시 확인한다.  네트워크 실패·오프라인·API 한도는 전부 조용히 삼키고
(`ok: False`) 앱 화면에는 아무 일도 일어나지 않는다 — 알림은 **있을 때만**.

표준 라이브러리(urllib)만 쓴다.  추가 패키지 없음.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import threading
import urllib.error
import urllib.request
from typing import Any

REPO = "ckdwns04176-star/sa-aim-labs"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
EXE_NAME = "SA_Aim_Labs.exe"

log = logging.getLogger("updates")

_cache: dict[str, Any] | None = None
_cache_at: float = 0.0
_lock = threading.Lock()
_running = False
CACHE_SECONDS = 6 * 60 * 60


def version_tuple(value: str) -> tuple[int, ...]:
    """`v1.2.0` · `1.2` · `1.2.0-beta` → 비교 가능한 숫자 튜플."""
    numbers = re.findall(r"\d+", str(value or ""))
    if not numbers:
        return (0,)
    return tuple(int(n) for n in numbers[:4])


def is_newer(latest: str, current: str) -> bool:
    a, b = version_tuple(latest), version_tuple(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _download_url(release: dict[str, Any]) -> str:
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower() == EXE_NAME.lower() or name.lower().endswith(".exe"):
            return str(asset.get("browser_download_url") or "")
    return str(release.get("html_url") or RELEASES_URL)


def fetch_latest(timeout: float = 4.0) -> dict[str, Any]:
    """GitHub Releases `latest` 1건.  실패하면 `{ok: False, message: ...}`."""
    request = urllib.request.Request(
        API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "SA-AIM-LABS-update-check",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https 고정
            raw = response.read(200_000).decode("utf-8", "ignore")
        release = json.loads(raw)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": int(getattr(exc, "code", 0) or 0),
                "message": f"릴리스 정보를 읽지 못했습니다 (HTTP {getattr(exc, 'code', '?')})"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "offline": True, "message": f"네트워크 확인 실패 · {exc}"}
    except ValueError:
        return {"ok": False, "message": "릴리스 응답을 해석하지 못했습니다"}
    if not isinstance(release, dict):
        return {"ok": False, "message": "릴리스 응답 형식이 다릅니다"}
    return {
        "ok": True,
        "tag": str(release.get("tag_name") or ""),
        "name": str(release.get("name") or release.get("tag_name") or ""),
        "url": str(release.get("html_url") or RELEASES_URL),
        "download": _download_url(release),
        "published_at": str(release.get("published_at") or ""),
        "notes": str(release.get("body") or "")[:2000],
    }


def check(current: str, *, force: bool = False, timeout: float = 4.0) -> dict[str, Any]:
    """현재 버전과 릴리스 최신 버전을 비교한다.  성공/실패 모두 사전 형태로."""
    global _cache, _cache_at, _running
    import time

    with _lock:
        if not force and _cache and time.monotonic() - _cache_at < CACHE_SECONDS \
                and _cache.get("current") == str(current):
            return dict(_cache)
        running = _running
    if not force and running:
        # 시작할 때 띄운 백그라운드 확인이 아직 도는 중 — 잠깐만 기다리고 결과를 돌려준다
        deadline = time.monotonic() + min(2.0, max(0.2, timeout))
        while time.monotonic() < deadline:
            with _lock:
                if _cache and _cache.get("current") == str(current):
                    return dict(_cache)
            time.sleep(0.05)
        return {"ok": False, "pending": True, "current": str(current), "newer": False,
                "message": "새 버전을 확인하는 중입니다"}
    with _lock:
        _running = True
    try:
        latest = fetch_latest(timeout=timeout)
    finally:
        with _lock:
            _running = False
    if not latest.get("ok"):
        result = {"ok": False, "current": str(current), "newer": False,
                  "message": latest.get("message") or "업데이트를 확인하지 못했습니다",
                  "offline": bool(latest.get("offline"))}
    else:
        tag = latest.get("tag") or latest.get("name") or ""
        newer = is_newer(tag, current)
        result = {
            "ok": True,
            "current": str(current),
            "latest": tag.lstrip("v") or tag,
            "tag": tag,
            "newer": bool(newer),
            "url": latest.get("url"),
            "download": latest.get("download"),
            "published_at": latest.get("published_at"),
            "notes": latest.get("notes"),
            "checked_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "message": (f"새 버전 {tag} 이 있습니다" if newer else f"최신 버전입니다 (v{current})"),
        }
    with _lock:
        _cache, _cache_at = dict(result), time.monotonic()
    return result


def cached() -> dict[str, Any] | None:
    """마지막 확인 결과(있으면) — bootstrap 응답에 그대로 실어 보낸다."""
    with _lock:
        return dict(_cache) if _cache else None


def clear() -> None:
    global _cache, _cache_at, _running
    with _lock:
        _cache, _cache_at = None, 0.0
        _running = False


def start_background(current: str, on_done=None) -> threading.Thread:
    """앱 시작 시 조용히 확인하는 스레드 (실패해도 앱은 그대로)."""
    def run() -> None:
        try:
            result = check(current, force=True)
            log.info("update check · %s", result.get("message"))
            if on_done:
                on_done(result)
        except Exception:  # noqa: BLE001 - 업데이트 확인은 절대 앱을 방해하지 않는다
            log.debug("update check failed", exc_info=True)

    thread = threading.Thread(target=run, name="sa-update-check", daemon=True)
    thread.start()
    return thread
