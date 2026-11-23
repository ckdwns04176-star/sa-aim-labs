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
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from typing import Any

REPO = "ckdwns04176-star/sa-aim-labs"
# `releases/latest` 는 prerelease 를 돌려주지 않는다. 이 저장소의 빌드 릴리스는
# tag `latest-build` · prerelease 로 올라가므로 목록에서 직접 고른다.
API_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
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


_SEMVER_RE = re.compile(r"v?(\d+\.\d+\.\d+)")


def _version_in(text: str) -> str | None:
    """릴리스 태그·이름·본문에서 첫 `x.y.z` 를 찾는다."""
    match = _SEMVER_RE.search(str(text or ""))
    return match.group(1) if match else None


def build_time() -> float | None:
    """이 실행 파일(.exe)이 만들어진 시각 — 소스 실행이면 None."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        return os.path.getmtime(sys.executable)
    except OSError:
        return None


def build_is_newer(build_at: str, margin_seconds: float = 3600) -> bool:
    """태그에 버전이 없을 때: 릴리스 빌드가 내 .exe 보다 '나중'이면 새 빌드."""
    if not build_at:
        return False
    try:
        stamp = _dt.datetime.fromisoformat(build_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False
    local = build_time()
    if local is None:
        return False
    return stamp > local + margin_seconds


def _download_url(release: dict[str, Any]) -> str:
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower() == EXE_NAME.lower() or name.lower().endswith(".exe"):
            return str(asset.get("browser_download_url") or "")
    return str(release.get("html_url") or RELEASES_URL)


def _pick_release(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """초안이 아닌 릴리스 중 .exe 가 붙은 최신 것 (없으면 첫 항목)."""
    live = [r for r in items if isinstance(r, dict) and not r.get("draft")]
    if not live:
        return None
    with_exe = [r for r in live if (r.get("assets") or [])]
    return (with_exe or live)[0]


def fetch_latest(timeout: float = 4.0) -> dict[str, Any]:
    """GitHub Releases 목록에서 최신 빌드 1건.  실패하면 `{ok: False, message: ...}`."""
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
            raw = response.read(400_000).decode("utf-8", "ignore")
        items = json.loads(raw)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": int(getattr(exc, "code", 0) or 0),
                "message": f"릴리스 정보를 읽지 못했습니다 (HTTP {getattr(exc, 'code', '?')})"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "offline": True, "message": f"네트워크 확인 실패 · {exc}"}
    except ValueError:
        return {"ok": False, "message": "릴리스 응답을 해석하지 못했습니다"}
    if isinstance(items, dict):          # 예전 /releases/latest 형태도 받아 준다
        items = [items]
    release = _pick_release(items if isinstance(items, list) else [])
    if not release:
        return {"ok": False, "message": "아직 빌드 릴리스가 없습니다"}
    assets = [a for a in (release.get("assets") or []) if isinstance(a, dict)]
    build_at = ""
    for asset in assets:
        stamp = str(asset.get("updated_at") or "") or str(asset.get("created_at") or "")
        if stamp > build_at:
            build_at = stamp
    return {
        "ok": True,
        "tag": str(release.get("tag_name") or ""),
        "name": str(release.get("name") or release.get("tag_name") or ""),
        "url": str(release.get("html_url") or RELEASES_URL),
        "download": _download_url(release),
        "published_at": str(release.get("published_at") or ""),
        "build_at": build_at or str(release.get("published_at") or ""),
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
        tag = str(latest.get("tag") or "")
        name = str(latest.get("name") or tag)
        version = _version_in(f"{tag} {name} {latest.get('notes') or ''}")
        build_at = str(latest.get("build_at") or "")
        if version:
            newer = is_newer(version, current)
            reason = "version"
            message = (f"새 버전 v{version} 이 있습니다" if newer else f"최신 버전입니다 (v{current})")
        else:
            # 태그가 `latest-build` 처럼 버전이 없는 저장소 — 빌드 시각으로 '새 빌드' 판단
            newer = build_is_newer(build_at)
            reason = "build"
            label = build_at[:16].replace("T", " ") if build_at else name
            message = (f"새 빌드가 있습니다 · {name} ({label})" if newer
                       else f"최신 빌드입니다 (v{current})")
        result = {
            "ok": True,
            "current": str(current),
            "latest": version or None,
            "tag": tag,
            "name": name,
            "newer": bool(newer),
            "reason": reason,
            "build_at": build_at,
            "url": latest.get("url"),
            "download": latest.get("download"),
            "published_at": latest.get("published_at"),
            "notes": latest.get("notes"),
            "checked_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "message": message,
        }
    with _lock:
        _cache, _cache_at = dict(result), time.monotonic()
    return result


def gate(result: dict[str, Any] | None, current: str, *, frozen: bool | None = None,
         env: dict[str, str] | None = None) -> dict[str, Any]:
    """구버전 차단 판단 — 오프라인·테스트(소스 실행)에서는 절대 막지 않는다.

    차단은 .exe 로 실행 중이고, GitHub 확인에 성공했고, 더 높은 버전이 있을 때만.
    """
    env = os.environ if env is None else env
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    data = result or {}
    if str(env.get("SA_AL_NO_UPDATE_GATE") or "") in ("1", "true", "TRUE"):
        return {"blocked": False, "reason": "test-env", "current": str(current), "latest": data.get("latest")}
    if not data.get("ok"):
        return {"blocked": False,
                "reason": "offline" if (data.get("offline") or data.get("pending")) else "unknown",
                "current": str(current), "latest": None,
                "message": data.get("message") or "업데이트를 확인하지 못했습니다 · 차단하지 않습니다"}
    if not data.get("newer"):
        return {"blocked": False, "reason": "latest", "current": str(current), "latest": data.get("latest")}
    if not frozen:
        return {"blocked": False, "reason": "source-run", "current": str(current),
                "latest": data.get("latest"), "download": data.get("download"),
                "message": "소스 실행 중이라 구버전 차단을 건너뜁니다"}
    return {
        "blocked": True,
        "reason": "outdated",
        "current": str(current),
        "latest": data.get("latest") or data.get("name"),
        "download": data.get("download"),
        "url": data.get("url"),
        "notes": data.get("notes"),
        "message": data.get("message") or "새 버전이 있습니다 · 업데이트해야 계속 사용할 수 있습니다",
    }


def download(url: str, dest: str | os.PathLike[str], *, timeout: float = 60.0) -> dict[str, Any]:
    """새 .exe 내려받기 (urllib · 표준 라이브러리)."""
    target = str(dest)
    if not str(url or "").startswith("https://"):
        return {"ok": False, "message": "다운로드 주소가 올바르지 않습니다"}
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        request = urllib.request.Request(str(url), headers={
            "User-Agent": f"SA-AIM-LABS-updater/{paths_version()}", "Accept": "application/octet-stream"})
        tmp = target + ".part"
        with urllib.request.urlopen(request, timeout=timeout) as response, open(tmp, "wb") as fh:  # noqa: S310 - https 고정
            total = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                total += len(chunk)
                if total > 400 * 1024 * 1024:
                    raise ValueError("파일이 너무 큽니다")
        if total < 1024 * 1024:
            raise ValueError(f"받은 파일이 너무 작습니다 ({total} bytes)")
        os.replace(tmp, target)
        return {"ok": True, "path": target, "size": total}
    except Exception as exc:  # noqa: BLE001
        try:
            if os.path.exists(target + ".part"):
                os.unlink(target + ".part")
        except OSError:
            pass
        return {"ok": False, "message": f"업데이트 다운로드 실패 · {exc}"}


def paths_version() -> str:
    from . import paths
    return paths.APP_VERSION


def swap_script(exe_path: str, new_exe: str, *, pid: int | None = None) -> str:
    """앱을 닫은 뒤 새 .exe 로 교체하고 다시 실행하는 배치 내용."""
    exe = os.path.basename(exe_path) or EXE_NAME
    pid = int(pid or os.getpid())
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        f"rem SA AIM LABS 자동 업데이트 · pid {pid}",
        "timeout /t 2 /nobreak >nul",
        ":wait",
        f"tasklist /FI \"PID eq {pid}\" /NH | find \"{pid}\" >nul",
        "if not errorlevel 1 (",
        "  timeout /t 2 /nobreak >nul",
        "  goto wait",
        ")",
        f'move /y \"{new_exe}\" \"{exe_path}\" >nul',
        f'start \"\" \"{exe_path}\"',
        'del \"%~f0\" >nul 2>&1',
    ]
    return "\r\n".join(lines) + "\r\n"


def prepare_install(download_url: str, *, exe_path: str | None = None,
                    work_dir: str | None = None) -> dict[str, Any]:
    """내려받기 + 교체 배치 준비 (실행은 run_install)."""
    import tempfile

    base = work_dir or tempfile.mkdtemp(prefix="sa_al_update_")
    new_exe = os.path.join(base, EXE_NAME)
    got = download(download_url, new_exe)
    if not got.get("ok"):
        return got
    exe = exe_path or sys.executable
    script = os.path.join(base, "sa_aim_update.bat")
    try:
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(swap_script(exe, new_exe))
    except OSError as exc:
        return {"ok": False, "message": f"업데이트 스크립트를 쓰지 못했습니다 · {exc}"}
    return {"ok": True, "path": new_exe, "script": script, "size": got.get("size"),
            "message": "새 버전을 내려받았습니다 · 앱을 닫으면 자동으로 교체됩니다"}


def run_install(script: str) -> dict[str, Any]:
    """교체 배치 실행 (앱은 스스로 종료)."""
    if os.name != "nt":
        return {"ok": False, "message": "자동 교체는 Windows 에서만 동작합니다"}
    try:
        subprocess.Popen(["cmd", "/c", script], close_fds=True,
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        return {"ok": True, "message": "앱을 닫으면 새 버전으로 다시 시작됩니다"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"업데이트 실행 실패 · {exc}"}


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
