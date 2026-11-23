"""연패 브레이크 스마트 감시 (v1.4.0).

`suddenattack.exe` 가 켜져 있을 때만 3~5분 주기로 최신 매치를 가볍게 확인해
3연패 이상 또는 평균 딜량 급락을 감지하면 틸트 방지 알림을 남긴다.
설정에서 끄고 켤 수 있고, Windows 가 아닌 환경에서는 조용히 대기한다.

표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
import threading
import time
from collections import deque
from typing import Any, Callable, Sequence

PROCESS_NAME = "suddenattack.exe"
DEFAULT_INTERVAL = 240        # 4분
MIN_INTERVAL = 180
MAX_INTERVAL = 300
IDLE_POLL = 20


def _win() -> bool:
    return os.name == "nt"


def game_running(name: str = PROCESS_NAME) -> bool:
    """tasklist 로 서든어택 프로세스 확인 (Windows 전용 · 실패하면 False)."""
    if not _win():
        return False
    try:
        flags = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True, text=True, timeout=6,
            creationflags=flags,
        )
        return name.lower() in (proc.stdout or "").lower()
    except (OSError, subprocess.SubprocessError):
        return False


def _won(m: dict[str, Any]) -> bool:
    raw = m.get("result")
    if raw is not None:
        return str(raw) == "승"
    return str(m.get("match_result")) == "1"


def loss_streak(matches: Sequence[dict[str, Any]]) -> int:
    """최신순 매치에서 이어진 연패 수."""
    n = 0
    for m in matches:
        if _won(m):
            break
        n += 1
    return n


def win_streak(matches: Sequence[dict[str, Any]]) -> int:
    n = 0
    for m in matches:
        if not _won(m):
            break
        n += 1
    return n


def average_damage(rows: Sequence[dict[str, Any]]) -> float | None:
    values = [float(m.get("damage")) for m in rows if m.get("damage") not in (None, "")]
    return sum(values) / len(values) if values else None


def evaluate(matches: Sequence[dict[str, Any]], *, window: int = 3, baseline: int = 20,
             drop_ratio: float = 0.72) -> dict[str, Any] | None:
    """연패/딜량 급락 판정 — 알림이 필요 없으면 None."""
    rows = [m for m in (matches or []) if isinstance(m, dict)]
    if len(rows) < 3:
        return None
    streak = loss_streak(rows)
    if streak >= 3:
        danger = streak >= 5
        return {
            "kind": "loss",
            "level": "위험" if danger else "주의",
            "tone": "danger" if danger else "warn",
            "title": (f"{streak}연패 · 오늘은 여기서 쉬세요" if danger else f"{streak}연패 · 잠깐 숨 고르기"),
            "message": (
                f"최근 {streak}판 연속 패배입니다. 지금은 랭크를 멈추고 5~10분 쉬는 게 점수를 지킵니다. "
                f"" if danger else
                f"최근 {streak}판 연속 패배 · 잠깐 손을 풀고 연습 2종만 하고 랭크전으로 돌아가세요. "
            ) + "오늘은 킬보다 생존 우선.",
            "streak": streak,
            "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    wins = win_streak(rows)
    if wins >= 3:
        hot = wins >= 5
        return {
            "kind": "win",
            "level": "상승" if not hot else "최상승",
            "tone": "hot",
            "title": f"🔥 {wins}연승 중!" if not hot else f"🔥🔥 {wins}연승 폭주!",
            "message": (f"현재 폼이 올라타고 있습니다 · {wins}연승. 이 흐름 그대로 한 판 더 가세요."
                        if not hot else
                        f"{wins}연승입니다. 지금은 감도·무기를 바꾸지 말고 그대로 유지하는 게 이득입니다."),
            "streak": wins,
            "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    recent = rows[:window]
    dmg_now = average_damage(recent)
    dmg_base = average_damage(rows[: max(window, baseline)])
    if dmg_now and dmg_base and dmg_base > 0 and dmg_now < dmg_base * drop_ratio:
        pct = round((1 - dmg_now / dmg_base) * 100)
        return {
            "kind": "dmg",
            "level": "주의",
            "tone": "warn",
            "title": f"딜량이 {pct}% 떨어졌습니다",
            "message": (f"최근 {len(recent)}판 평균 딜량 {int(dmg_now)} · 평소 {int(dmg_base)}. "
                        f"에임이 흔들릴 때는 감도 대신 자세·호흡을 먼저 확인하세요."),
            "streak": win_streak(rows),
            "drop": pct,
            "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    return None


class TiltWatcher:
    """백그라운드 감시 스레드 — 테스트에서는 모든 의존성을 주입할 수 있다."""

    def __init__(self, fetch: Callable[[], Sequence[dict[str, Any]]] | None = None, *,
                 interval: int = DEFAULT_INTERVAL,
                 process_check: Callable[[], bool] = game_running,
                 enabled: Callable[[], bool] = lambda: True,
                 on_alert: Callable[[dict[str, Any]], None] | None = None,
                 notify: Callable[[str, str, str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.fetch = fetch
        self.interval = max(MIN_INTERVAL, min(MAX_INTERVAL, int(interval or DEFAULT_INTERVAL)))
        self.process_check = process_check
        self.enabled = enabled
        self.on_alert = on_alert
        self.notify = notify
        self.clock = clock
        self.alerts: deque[dict[str, Any]] = deque(maxlen=6)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_check: float | None = None
        self._last_check_at: str | None = None
        self._game: bool | None = None
        self._last_error: str | None = None
        self._seq = 0

    # ------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def state(self) -> dict[str, Any]:
        return {
            "supported": _win(),
            "running": self.running,
            "game_running": self._game,
            "interval": self.interval,
            "last_check_at": self._last_check_at,
            "alerts": list(self.alerts),
            "error": self._last_error,
            "process": PROCESS_NAME,
        }

    def ack(self, alert_id: str | None = None) -> list[dict[str, Any]]:
        if alert_id:
            self.alerts = deque([a for a in self.alerts if a.get("id") != alert_id], maxlen=6)
        else:
            self.alerts.clear()
        return list(self.alerts)

    # ------------------------------------------------------------ loop
    def start(self) -> bool:
        if self.running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sa-tilt-watch", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def check_once(self, matches: Sequence[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """한 번 확인 — 테스트/수동 확인용. 알림이면 dict, 아니면 None."""
        try:
            rows = list(matches) if matches is not None else list(self.fetch() or [])
        except Exception as exc:  # noqa: BLE001 - 감시 실패는 앱을 방해하지 않는다
            self._last_error = str(exc)
            return None
        self._last_error = None
        self._last_check = self.clock()
        self._last_check_at = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        alert = evaluate(rows)
        if alert:
            self._seq += 1
            alert["id"] = f"tilt-{self._seq}"
            self.alerts.append(alert)
            if self.on_alert:
                try:
                    self.on_alert(alert)
                except Exception:  # noqa: BLE001
                    pass
            if self.notify:
                try:
                    level = {"danger": "error", "warn": "warning"}.get(str(alert.get("tone")), "info")
                    self.notify(str(alert.get("title") or "SA AIM LABS"), str(alert.get("message") or ""), level)
                except Exception:  # noqa: BLE001 - 알림 실패는 감시를 멈추지 않는다
                    pass
        return alert

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.enabled():
                    self._game = None
                    self._stop.wait(IDLE_POLL)
                    continue
                active = self.process_check() if _win() else False
                self._game = bool(active)
                if not active:
                    self._stop.wait(IDLE_POLL)
                    continue
                due = self._last_check is None or (self.clock() - self._last_check) >= self.interval
                if due and self.fetch:
                    self.check_once()
                self._stop.wait(10)
            except Exception:  # noqa: BLE001 - 루프는 절대 죽지 않는다
                self._stop.wait(30)
