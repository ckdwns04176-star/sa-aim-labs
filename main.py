"""SA AIM LABS — desktop entry point (PyWebView, frameless 1440x880).

Run from source:
    python main.py                # normal app
    python main.py --serve 8710   # dev: serve ui/ over HTTP (browser preview)
    python main.py --drs-apply rifle --result <file>   # elevated Drs helper
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
from pathlib import Path

# Allow "python main.py" from any cwd and PyInstaller onefile builds alike.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import paths  # noqa: E402  (path setup must come first)
from app import tray, winutil  # noqa: E402


def _setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    try:
        handler = logging.handlers.RotatingFileHandler(
            paths.log_dir() / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s · %(message)s")
        handler.setFormatter(fmt)
        root = logging.getLogger()
        root.setLevel(level)
        root.addHandler(handler)
        # pywebview 은 고른 렌더러(WinForms / Chromium · MSHTML)를 debug 레벨로만
        # 남긴다 — 브리지가 안 붙는 현장 진단에 꼭 필요해서 파일에 남긴다.
        logging.getLogger("pywebview").setLevel(logging.DEBUG)
    except OSError:
        logging.basicConfig(level=level)
    if sys.stderr:
        console = logging.StreamHandler()
        console.setLevel(level)
        logging.getLogger().addHandler(console)


def _dpi_aware() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - older Windows
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def _serve(port: int) -> int:
    """Serve ui/ for browser preview (no bridge → UI runs on its sample data)."""
    import functools
    import http.server
    import socketserver

    directory = str(paths.ui_dir())
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"SA AIM LABS UI · http://127.0.0.1:{port}/index.html")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            return 0
    return 0


def _webview2_version() -> str | None:
    """Installed Microsoft Edge WebView2 Runtime version (per-user or machine).

    When it is present the app pins ``gui='edgechromium'`` so the build never
    silently falls back to the ancient mshtml backend; when it is missing the
    log says so, which is the answer to "the window is blank/unresponsive".
    """
    if os.name != "nt":
        return None
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return None
    client = r"\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root, path in (
        (winreg.HKEY_CURRENT_USER, r"Software" + client),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node" + client),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE" + client),
    ):
        try:
            with winreg.OpenKey(root, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
                if version:
                    return str(version)
        except OSError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return None


def _public_names(js_api) -> list[str]:
    """Names pywebview will hand to JS — logged so a bad attribute is visible.

    A name that is not a plain function is worth seeing in the log: pywebview
    recurses into those while building ``window.pywebview.api``.
    """
    names: list[str] = []
    for name in dir(js_api):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(js_api, name)
        except Exception:  # noqa: BLE001
            names.append(f"{name}:ERROR")
            continue
        if getattr(attr, "_serializable", True) is False:
            names.append(f"{name}:skip")
        elif callable(attr):
            names.append(name)
        else:
            names.append(f"{name}:<{type(attr).__name__}>")
    return names


def _work_area() -> tuple[int, int, int, int]:
    """Primary monitor work area (excludes the taskbar), DPI aware."""
    if os.name != "nt":
        return 0, 0, 0, 0
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        SPI_GETWORKAREA = 0x0030
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width > 200 and height > 200:
                return int(rect.left), int(rect.top), width, height
    except Exception:  # noqa: BLE001 - fall through to GetSystemMetrics
        pass
    try:
        import ctypes

        user32 = ctypes.windll.user32
        return 0, 0, int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:  # noqa: BLE001
        return 0, 0, 0, 0


def _os_scale() -> float:
    """Windows 표시 배율 (100% → 1.0, 125% → 1.25)."""
    if os.name != "nt":
        return 1.0
    try:
        import ctypes

        percent = ctypes.windll.shcore.GetScaleFactorForDevice(0)  # type: ignore[attr-defined]
        if percent:
            return float(percent) / 100.0
    except Exception:  # noqa: BLE001 - 구형 윈도우
        try:
            import ctypes

            dc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)   # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, dc)
            if dpi:
                return float(dpi) / 96.0
        except Exception:  # noqa: BLE001
            pass
    return 1.0


def _center_position(width: int, height: int) -> tuple[int | None, int | None]:
    """정중앙 좌표. 모니터를 못 읽으면 None 을 돌려 pywebview 기본값을 쓴다."""
    left, top, screen_w, screen_h = _work_area()
    if not screen_w or not screen_h:
        try:
            import webview

            screens = getattr(webview, "screens", None) or []
            if screens:
                area = screens[0]
                screen_w, screen_h = int(area.width), int(area.height)
                left, top = int(getattr(area, "x", 0) or 0), int(getattr(area, "y", 0) or 0)
        except Exception:  # noqa: BLE001
            return None, None
    if not screen_w or not screen_h:
        return None, None
    x = left + max(0, (screen_w - width) // 2)
    y = top + max(0, (screen_h - height) // 2)
    return x, y


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--drs-apply", metavar="MODE", help="internal: apply a Drs profile elevated")
    parser.add_argument("--drs-restore", action="store_true", help="internal: restore the latest Drs backup elevated")
    parser.add_argument("--nvapi-stairfix", action="store_true", help="internal: apply stair-fix through NVAPI DRS")
    parser.add_argument("--netopt-apply", action="store_true", help="internal: apply network tweaks elevated")
    parser.add_argument("--netopt-restore", action="store_true", help="internal: restore network tweaks elevated")
    parser.add_argument("--audioeq-apply", action="store_true", help="internal: apply the sound EQ preset elevated")
    parser.add_argument("--audioeq-restore", action="store_true", help="internal: restore the sound EQ preset elevated")
    parser.add_argument("--nvapi-settings", metavar="FILE", help="internal: apply a JSON list of DRS settings through NVAPI")
    parser.add_argument("--result", metavar="FILE", help="internal: write the elevated result JSON here")
    parser.add_argument("--serve", nargs="?", const=8710, type=int, help="dev: serve ui/ over HTTP")
    parser.add_argument("--debug", action="store_true", help="verbose log + devtools")
    args = parser.parse_args(argv)

    if args.netopt_apply or args.netopt_restore or args.audioeq_apply or args.audioeq_restore:
        from app import audioeq, netopt

        target = args.result or str(paths.data_dir() / "netopt_result.json")
        if args.netopt_apply or args.netopt_restore:
            return netopt.run_elevated("restore" if args.netopt_restore else "apply", target)
        return audioeq.run_elevated("restore" if args.audioeq_restore else "apply", target)

    if args.nvapi_stairfix or args.nvapi_settings:
        import json as _json

        from app import drs as _drs, nvapi_drs

        pairs = list(_drs.STAIRFIX_SET)
        if args.nvapi_settings:
            try:
                payload = _json.loads(Path(args.nvapi_settings).read_text(encoding="utf-8"))
                pairs = [(int(a), int(b)) for a, b in (payload.get("settings") or [])]
            except (OSError, ValueError, TypeError):
                pairs = list(_drs.STAIRFIX_SET)
        target = args.result or str(paths.data_dir() / "nvapi_result.json")
        return nvapi_drs.cli(settings=pairs, result_path=target)

    if args.drs_apply or args.drs_restore:
        from app import nvidia

        target = args.result or str(paths.data_dir() / "elevated_result.json")
        if args.drs_restore:
            return nvidia.run_elevated_restore(target)
        return nvidia.run_elevated_cli(args.drs_apply, target)

    if args.serve is not None:
        return _serve(int(args.serve))

    _setup_logging(args.debug)
    log = logging.getLogger("main")
    _dpi_aware()

    import webview  # imported late so --serve / --drs-apply work without a GUI

    from app.api import Api, MATCH_REFRESH_SECONDS

    api = Api()

    # 프레임 없는 창의 드래그는 JS 이동 대신 OS에 넘긴다 (api.drag_window → WM_NCLBUTTONDOWN).
    try:
        webview.settings.pop("DRAG_REGION_SELECTOR", None)
    except Exception:  # noqa: BLE001 - older pywebview
        pass
    try:
        webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False
        webview.settings["ALLOW_DOWNLOADS"] = False
    except Exception:  # noqa: BLE001 - older pywebview
        pass

    # ── 진단 정보: 어떤 pywebview / gui 백엔드로 뜨는지 app.log 에 남긴다 ──
    try:
        from webview import __version__ as webview_version  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - pywebview has no __version__ in some builds
        try:
            from importlib.metadata import version as _pkg_version

            webview_version = _pkg_version("pywebview")
        except Exception:  # noqa: BLE001
            webview_version = "?"
    gui: str | None = None
    webview2: str | None = None
    if os.name == "nt":
        webview2 = _webview2_version()
        if webview2:
            gui = "edgechromium"
            log.info("WebView2 runtime %s · gui=edgechromium", webview2)
        else:
            log.warning("WebView2 runtime not found · pywebview falls back to MSHTML, "
                        "where window.pywebview.api does not work")
    exposed = _public_names(api)
    log.info("pywebview %s · python %s · js_api: %s", webview_version, sys.version.split()[0],
             ", ".join(exposed))
    missing = [name for name in ("bootstrap", "get_state", "bridge_ping") if name not in exposed]
    if missing:
        log.error("js_api is missing %s · the UI would boot without a bridge", missing)

    # .exe 는 순수 file:// URI 만으로 띄운다.  v1.0.1 은 여기에
    # "?desktop=1&log=...&wv2=0" 쿼리스트링을 붙였는데, WebView2 가
    # "file:///.../_MEI.../ui/index.html?desktop=1..." 을 파일 경로로 해석해
    # "파일을 찾을 수 없음" 오류를 냈다 (사용자 제보).
    # ui/ 는 location.protocol === 'file:' 로 데스크톱 실행을 스스로 판별하고,
    # 로그 경로는 bootstrap 응답의 meta.log_file 을 쓴다 — URL 로 넘길 것이 없다.
    url = paths.index_url()
    log_file = paths.log_dir() / "app.log"
    # ── v1.5.0 (#1) · QHD(2560×1440) 이상 + 낮은 OS 배율이면 창/배율을 자동으로 키운다 ──
    area = _work_area()
    display = api.set_display(area[2], area[3], _os_scale())
    ui_scale = api.effective_ui_scale()
    zoom = max(0.75, min(1.5, ui_scale / 100.0))
    win_w = int(round(winutil.WINDOW_WIDTH * zoom))
    win_h = int(round(winutil.WINDOW_HEIGHT * zoom))
    x, y = _center_position(win_w, win_h)
    log.info("display %sx%s @%s · ui scale %s%% · window %sx%s at %s,%s",
             display.get("width"), display.get("height"), display.get("scale"),
             ui_scale, win_w, win_h, x, y)
    window = webview.create_window(
        paths.APP_TITLE,
        url=url,
        js_api=api,
        width=win_w,
        height=win_h,
        x=x,
        y=y,
        min_size=(int(1280 * zoom), int(780 * zoom)),
        frameless=True,
        easy_drag=False,
        shadow=True,
        background_color="#0B0D12",
        confirm_close=False,
        text_select=False,
        zoomable=False,
        # hidden=True + a timer that called show() from another thread made the
        # WinForms window sit blank/unresponsive for seconds on launch.  The page
        # paints #070B13 and #app-splash instantly, so showing it right away is
        # both faster and safer.
        hidden=False,
    )
    api.attach(window)

    # ── 창 크기 고정 ──
    # 서든어택을 4:3 저해상도로 실행했다가 바탕화면(고해상도)으로 돌아오면
    # DPI·해상도가 바뀌며 창이 커지던 문제.  짧은 간격으로 사각형을 읽어
    # 논리 1200×750(DPI 환산)에서 벗어나면 즉시 되돌리고, 모니터 작업 영역
    # 밖으로 나가지 않게 좌표를 보정한다(최소화·최대화는 건드리지 않음).
    guard = winutil.WindowGuard(paths.APP_TITLE, win_w, win_h)
    if guard.start():
        api.attach_guard(guard)
        log.info("window guard on · %sx%s 고정 (해상도·DPI 변경 감시 · 이동은 자유)", win_w, win_h)
    else:
        log.info("window guard off (Windows 전용)")

    # ── 최대화 완전 차단 (v1.3.1) ──
    # 1440×880 고정.  타이틀바의 최대화 버튼을 없애고, Win32 스타일에서도
    # WS_MAXIMIZEBOX·WS_THICKFRAME·WS_MAXIMIZE 를 지워 더블클릭·단축키로도
    # 최대화되지 않게 한다 (최대화 후 복귀가 어긋나던 문제 자체를 막는다).
    try:
        locked = winutil.fixed_window_style(api._hwnd() or 0)
        log.info("window style fixed · %s", locked)
    except Exception:  # noqa: BLE001 - 스타일 고정은 최선 노력
        log.debug("window style could not be fixed", exc_info=True)

    # ── v2.0.0 · 클라우드 30분 자동 백업 ──
    try:
        api.start_cloud_auto()
    except Exception:  # noqa: BLE001
        log.debug("cloud auto backup could not start", exc_info=True)
    # ── v1.0.1 · 내 전적 65초 자동 최신화 (#12) ──
    try:
        api.start_match_auto_refresh()
        log.info("match auto refresh armed · interval=%ss", MATCH_REFRESH_SECONDS)
    except Exception:  # noqa: BLE001 - 감시 실패가 앱 시작을 막지 않는다
        log.debug("match auto refresh could not start", exc_info=True)

    # ── 인게임 오버레이 단축키(기본 Insert/Home) · 경기 종료 토스트 감시 ──
    try:
        overlay_state = api.start_overlays()
        log.info("overlays · %s", overlay_state)
    except Exception:  # noqa: BLE001 - 오버레이 실패가 앱 시작을 막지 않는다
        log.debug("overlays could not start", exc_info=True)

    # ── 새 버전 확인 (백그라운드 · 실패해도 조용히) ──
    api.start_update_check()

    # ── v1.4.0 연패 브레이크 감시 (설정 ON + suddenattack.exe 실행 중일 때만) ──
    try:
        api.start_tilt_watch()
        log.info("tilt watcher armed · interval=%ss", api.store.settings.get("tilt_interval"))
    except Exception:  # noqa: BLE001 - 감시 실패는 앱 시작을 막지 않는다
        log.debug("tilt watcher could not start", exc_info=True)

    # ── 시스템 트레이 + 전역 단축키 (Ctrl+Shift+A) ──
    # 아이콘을 누르거나 단축키를 누르면 창을 숨기기/보이기 토글한다.
    # pywebview 의 window.show()/hide() 는 WinForms Invoke 로 메인 스레드에 넘어간다.
    def _set_hidden(state: bool) -> None:
        try:
            window._sa_hidden = bool(state)
        except Exception:  # noqa: BLE001 - 표시용 플래그일 뿐
            pass

    def _toggle_window() -> None:
        try:
            if getattr(window, "_sa_hidden", False):
                window.show()
                _set_hidden(False)
            else:
                window.hide()
                _set_hidden(True)
        except Exception:  # noqa: BLE001 - 트레이 클릭이 앱을 죽이면 안 된다
            log.exception("tray toggle failed")

    def _quit_app() -> None:
        try:
            api.close_window()
        except Exception:  # noqa: BLE001
            log.exception("tray quit failed")

    def _show_window_and_settings() -> None:
        """트레이 `설정` — 창을 띄우고 설정 팝업을 바로 연다."""
        try:
            window.show()
            _set_hidden(False)
        except Exception:  # noqa: BLE001
            log.exception("tray settings · show failed")
        try:
            window.evaluate_js(
                "window.AL && window.AL.openSettings ? window.AL.openSettings() : "
                "document.querySelector('#settings-mask')?.classList.add('show');"
            )
        except Exception:  # noqa: BLE001 - 페이지가 아직 준비되지 않았을 수 있다
            log.debug("tray settings · evaluate_js failed", exc_info=True)

    tray_icon: tray.TrayIcon | None = None
    if os.name == "nt":
        tray_icon = tray.TrayIcon(
            title=paths.APP_TITLE,
            icon_path=str(paths.icon_file()),
            on_toggle=_toggle_window,
            on_open=lambda: (window.show(), _set_hidden(False)),
            on_settings=_show_window_and_settings,
            on_quit=_quit_app,
            hotkey="Ctrl+Shift+A",
        )
        if tray_icon.start():
            # Older/newer Api builds may not expose attach_tray(). The tray
            # remains managed by this entry point, so its absence must not
            # prevent the desktop app from starting.
            attach_tray = getattr(api, "attach_tray", None)
            if callable(attach_tray):
                attach_tray(tray_icon)
            else:
                log.debug("Api.attach_tray unavailable; tray is managed by main.py")
            enabled = bool(api.store.settings.get("hotkey_enabled", True))
            tray_icon.set_hotkey(enabled)
            log.info("tray on · icon=%s · hotkey %s=%s", tray_icon.icon_added, tray_icon.hotkey, enabled)
        else:
            log.info("tray off · %s", tray_icon.error)
            tray_icon = None

    # ── 창 닫기 ──
    # v1.3.0: '닫으면 트레이로 숨기기' 설정을 없앴다.  X 버튼은 UI 가
    # "트레이로 숨기기 / 프로그램 종료" 확인 팝업을 띄우고 그 선택으로 닫는다.
    # (OS 레벨 닫기 · Alt+F4 는 그대로 종료 — 확인창 없이 창만 사라지면
    #  사용자가 "앱이 꺼졌다"고 오해하므로, UI 실행 중에는 X 로 닫도록 안내한다.)
    def _on_loaded() -> None:
        log.info("ui loaded · bridge should be ready")
        # 창은 webview.start() 안에서 만들어지므로, 스타일 고정은 로드 직후에 한 번 더 한다
        try:
            log.info("window style fixed(after load) · %s", winutil.fixed_window_style(api._hwnd() or 0))
        except Exception:  # noqa: BLE001
            log.debug("window style could not be fixed after load", exc_info=True)

    try:
        window.events.loaded += _on_loaded
    except Exception:  # noqa: BLE001
        pass

    # ── v1.5.0 (#23) · 작업표시줄 ✕ · Alt+F4 도 X 버튼과 같은 확인 팝업 ──
    # pywebview 는 closing 핸들러가 False 를 돌려주면 창을 닫지 않는다.
    # 그 자리에서 UI 의 '트레이로 이동 / 완전 종료' 팝업을 띄우고, 사용자가
    # 완전 종료를 고르면 api.close_window() 가 _CLOSE_ALLOWED 를 세워 다시 닫는다.
    def _on_closing() -> bool:
        try:
            if api.allow_close():
                return True
            api.request_close()
            log.info("close requested from OS — confirm popup shown")
        except Exception:  # noqa: BLE001 - 확인 팝업 실패가 창을 잠그면 안 된다
            log.exception("close confirm failed")
            return True
        return False

    try:
        window.events.closing += _on_closing
    except Exception:  # noqa: BLE001 - 구버전 pywebview
        log.debug("closing event not available", exc_info=True)

    log.info("starting %s %s · ui=%s · log=%s · data=%s", paths.APP_TITLE, paths.APP_VERSION,
             url, log_file, paths.data_file())

    try:
        webview.start(gui=gui, debug=bool(args.debug), private_mode=False,
                      storage_path=str(paths.data_dir() / "webview"))
    except Exception as exc:  # noqa: BLE001
        log.exception("window failed")
        _fatal(str(exc))
        return 1
    finally:
        # v2.0.3 — 창이 이미 닫혔으면 여기서 오래 붙잡지 않는다.  마지막 백업은
        # 1.5초까지만 기다리고, 감시·트레이는 플래그/짧은 join 으로 즉시 정리한다.
        try:
            api.shutdown("exit", cloud_timeout=1.5)
        except Exception:  # noqa: BLE001
            log.debug("final cloud backup failed", exc_info=True)
        try:
            guard.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if tray_icon is not None:
                tray_icon.stop()
        except Exception:  # noqa: BLE001
            pass
    return 0


def _fatal(message: str) -> None:
    """Show a native error box when the webview runtime is missing."""
    text = (
        "SA AIM LABS를 시작하지 못했습니다.\n\n"
        f"{message}\n\n"
        "Windows 10/11 에서는 'Microsoft Edge WebView2 Runtime' 이 필요합니다.\n"
        "https://developer.microsoft.com/microsoft-edge/webview2/ 에서 설치할 수 있습니다."
    )
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "SA AIM LABS", 0x10)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        print(text, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
