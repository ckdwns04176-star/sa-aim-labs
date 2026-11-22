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

    # Frameless window: only the custom title bar drags (buttons stopPropagation).
    try:
        webview.settings["DRAG_REGION_SELECTOR"] = ".titlebar"
        webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False
        webview.settings["ALLOW_DOWNLOADS"] = False
    except Exception:  # noqa: BLE001 - older pywebview
        pass

    url = paths.index_url()
    window = webview.create_window(
        paths.APP_TITLE,
        url=url,
        js_api=api,
        width=1200,
        height=750,
        min_size=(1000, 640),
        frameless=True,
        easy_drag=False,
        shadow=True,
        background_color="#070B13",
        confirm_close=False,
        text_select=False,
        zoomable=False,
    )
    api.attach(window)
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
