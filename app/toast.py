"""한 판 종료 미니 결과 토스트 — 우하단 기본 · 5초 뒤 페이드아웃.

경기 감시는 내 최근 전적 목록을 **18초 간격**으로 읽어 **새 match_id** 가 생겼을
때만 상세를 받아 문구를 만든다.  v1.0.1 — 예전 45초 폴링 + 10분 목록 캐시 때문에
토스트가 다음 판 도중에 뜨던 문제를 고쳐, 경기 종료 후 5~30초 안에 뜨게 했다
(목록은 fresh 로 조회해 캐시를 건너뛴다).  토스트 자체는 ``overlay`` 모듈의
투명 창이 그린다.  감시는 설정에서 끌 수 있고, 꺼져 있으면 아무 것도 하지
않는다(수동 미리보기는 항상 가능).

v2.0.6 — 감시는 **기본 꺼짐**이다(``DEFAULT_ENABLED``).  18초마다 전적 목록을
읽는 호출이 하루 API 사용량의 절반을 차지해, 설정 화면에서 직접 켠 사람만 돈다.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from . import nexon

log = logging.getLogger("toast")

POLL_SECONDS = 18.0
# v2.0.6 — 앱을 켜자마자 감시를 돌리지 않는다(설정에서 켰을 때만).
DEFAULT_ENABLED = False


def last_match(client: nexon.NexonClient, nick: str, *, fresh: bool = True) -> dict[str, Any] | None:
    """가장 최근 경기 1건 → 토스트 재료 (맵 · 승패 · K/D · 딜량 · 연승).

    ``fresh=True`` 면 경기 목록 캐시를 건너뛰고 서버에서 다시 받는다 — 방금 끝난
    판을 즉시 보기 위해서다(경기 상세는 match_id 단위 캐시라 다시 받지 않는다).
    """
    if not nick:
        return None
    ouid = client.resolve_ouid(nick)
    rows = client.recent_matches(ouid, 8, fresh=fresh)
    if not rows:
        return None
    latest = rows[0]
    detail = client.match_detail_cached(latest.get("match_id")) or client.match_detail(latest.get("match_id"))
    dmg = 0
    for player in (detail or {}).get("match_detail") or []:
        if str(player.get("user_name") or "").strip().lower() == nick.strip().lower():
            try:
                dmg = int(round(float(player.get("damage") or 0)))
            except (TypeError, ValueError):
                dmg = 0
            break
    won = str(latest.get("match_result")) == "1"
    streak = 0
    for row in rows:
        if (str(row.get("match_result")) == "1") == won:
            streak += 1
        else:
            break
    return {
        "match_id": latest.get("match_id"),
        "map": (detail or {}).get("match_map") or latest.get("match_map") or "",
        "result": "승" if won else "패",
        "kills": int(latest.get("kill") or 0),
        "deaths": int(latest.get("death") or 0),
        "assist": int(latest.get("assist") or 0),
        "damage": dmg,
        "streak": streak if won else 0,
        "date": latest.get("date_match") or "",
    }


class MatchWatcher:
    """새 경기가 생기면 콜백(토스트)을 부른다 — 스레드 1개, 실패는 조용히."""

    def __init__(self, client_factory: Callable[[], nexon.NexonClient],
                 nick_provider: Callable[[], str],
                 on_match: Callable[[dict[str, Any]], None],
                 interval: float = POLL_SECONDS):
        self._client_factory = client_factory
        self._nick = nick_provider
        self._on_match = on_match
        self._interval = max(8.0, float(interval or POLL_SECONDS))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_seen: str | None = None
        self.enabled = DEFAULT_ENABLED
        self.last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            self.enabled = True
            return
        self._stop = threading.Event()
        self.enabled = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sa-match-watch")
        self._thread.start()

    def stop(self) -> None:
        self.enabled = False
        self._stop.set()

    def prime(self, match_id: str | None) -> None:
        """지금 있는 경기를 '이미 본 것'으로 표시 — 앱 켤 때 과거 판을 띄우지 않는다."""
        self._last_seen = str(match_id or "") or None

    def state(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "last_seen": self._last_seen, "interval": self._interval,
                "running": bool(self._thread and self._thread.is_alive()),
                "error": self.last_error}

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            if not self.enabled:
                continue
            nick = (self._nick() or "").strip()
            if not nick:
                continue
            try:
                match = last_match(self._client_factory(), nick)
            except Exception as exc:  # noqa: BLE001 - 감시 실패는 앱을 막지 않는다
                self.last_error = str(exc)
                continue
            if not match:
                continue
            match_id = str(match.get("match_id") or "")
            if not match_id:
                continue
            if self._last_seen is None:
                self._last_seen = match_id
                continue
            if match_id != self._last_seen:
                self._last_seen = match_id
                try:
                    self._on_match(match)
                except Exception:  # noqa: BLE001
                    log.exception("toast callback failed")
