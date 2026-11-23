"""SA AIM LABS — desktop entry point (PyWebView, frameless 1200x750).

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
    parser.add_argument("--result", metavar="FILE", help="internal: write the elevated result JSON here")
    parser.add_argument("--serve", nargs="?", const=8710, type=int, help="dev: serve ui/ over HTTP")
    parser.add_argument("--debug", action="store_true", help="verbose log + devtools")
    args = parser.parse_args(argv)

    if args.drs_apply:
        from app import nvidia

        target = args.result or str(paths.data_dir() / "elevated_result.json")
        return nvidia.run_elevated_cli(args.drs_apply, target)

    if args.serve is not None:
        return _serve(int(args.serve))

    _setup_logging(args.debug)
    log = logging.getLogger("main")
    _dpi_aware()

    import webview  # imported late so --serve / --drs-apply work without a GUI

    from app.api import Api

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

    url = paths.index_url()
    win_w, win_h = 1200, 750
    x, y = _center_position(win_w, win_h)
    log.info("window %sx%s at %s,%s", win_w, win_h, x, y)
    window = webview.create_window(
        paths.APP_TITLE,
        url=url,
        js_api=api,
        width=win_w,
        height=win_h,
        x=x,
        y=y,
        min_size=(1000, 640),
        frameless=True,
        easy_drag=False,
        shadow=True,
        background_color="#070B13",
        confirm_close=False,
        text_select=False,
        zoomable=False,
        hidden=True,          # 첫 프레임이 준비된 뒤에 보여 준다 (시작 버벅임 제거)
    )
    api.attach(window)

    def _show() -> None:
        try:
            window.show()
        except Exception:  # noqa: BLE001 - older pywebview
            pass

    for event_name in ("loaded", "shown"):
        try:
            event = getattr(window.events, event_name, None)
            if event is not None:
                event += _show
        except Exception:  # noqa: BLE001
            pass

    # loaded 이벤트가 오지 않는 환경에서도 창이 숨어 있지 않도록 안전장치
    import threading

    guard = threading.Timer(4.0, _show)
    guard.daemon = True
    guard.start()
    log.info("starting %s %s · ui=%s · data=%s", paths.APP_TITLE, paths.APP_VERSION, url, paths.data_file())

    try:
        webview.start(debug=bool(args.debug), private_mode=False, storage_path=str(paths.data_dir() / "webview"))
    except Exception as exc:  # noqa: BLE001
        log.exception("window failed")
        _fatal(str(exc))
        return 1
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
