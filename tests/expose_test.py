"""Regression: what pywebview hands to JavaScript as ``window.pywebview.api``.

``webview.util.inject_pywebview`` walks *every public attribute* of the js_api
object and recurses into anything that is not callable.  When ``Api`` kept the
native window in ``self.window``, that walk read ``Window.x`` / ``.width`` /
``.height`` — each of those blocks up to 15 s on ``events.shown`` and marshals
into the WinForms thread.  The result in the .exe was a window that sat
unresponsive and then ran on the HTML sample data ("미리보기").

This test mimics that walk and fails if it can reach a non-function attribute
(a Window, a Store, …).  It needs no display.

    python3 tests/expose_test.py
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""))


class TrapWindow:
    """Stands in for pywebview.Window: every property read is recorded."""

    __module__ = "test.trap"

    def __init__(self) -> None:
        object.__setattr__(self, "reads", [])

    def _read(self, name: str):
        object.__getattribute__(self, "reads").append(name)
        raise RuntimeError(f"Window.{name} was read during the js_api walk")

    @property
    def x(self):
        return self._read("x")

    @property
    def y(self):
        return self._read("y")

    @property
    def width(self):
        return self._read("width")

    @property
    def height(self):
        return self._read("height")

    @property
    def maximized(self):
        return self._read("maximized")

    def show(self):
        self._read("show")

    def destroy(self):
        self._read("destroy")

    def evaluate_js(self, *_a, **_k):
        self._read("evaluate_js")


def pywebview_walk(obj, base: str = "", functions: dict | None = None, leaks: list | None = None,
                   exposed: list | None = None):
    """The relevant half of ``webview.util.inject_pywebview.get_functions``."""
    functions = {} if functions is None else functions
    leaks = [] if leaks is None else leaks
    exposed = [] if exposed is None else exposed
    if id(obj) in exposed:
        return functions, leaks
    exposed.append(id(obj))
    for name in dir(obj):
        try:
            full = f"{base}.{name}" if base else name
            if name.startswith("_"):
                continue
            attr = getattr(obj, name)
            if getattr(attr, "_serializable", True) is False:
                continue
            if inspect.ismethod(attr) or inspect.isfunction(attr):
                functions[full] = attr
            elif inspect.isclass(attr) or (isinstance(attr, object) and not callable(attr)
                                           and hasattr(attr, "__module__")):
                pywebview_walk(attr, full, functions, leaks, exposed)
        except Exception:  # noqa: BLE001 - pywebview logs and continues
            continue
    return functions, leaks


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="sa_expose_"))
    os.environ["SA_AL_DATA_DIR"] = str(tmp)
    from app.api import Api
    from app.store import Store

    store = Store(tmp / "data.json")
    api = Api(store=store)
    trap = TrapWindow()
    api.attach(trap)

    # 1) 창 참조가 인스턴스 dict 에 없다
    check("Api 인스턴스에 window 속성이 없음", "window" not in api.__dict__ and not hasattr(Api, "window"),
          str(sorted(api.__dict__.keys())))
    check("attach() 한 창은 _win 으로만 접근", api._win is trap, type(api._win).__name__)

    # 2) pywebview 노출 walk 가 실제로 함수만 모은다
    functions, leaks = pywebview_walk(api)
    names = sorted(n.split(".")[-1] for n in functions)
    check("bootstrap/get_state/bridge_ping 이 노출됨",
          all(n in names for n in ("bootstrap", "get_state", "bridge_ping", "save_settings")), str(names[:8]))
    check("attach 는 JS 에 노출되지 않음", "attach" not in names, str([n for n in names if n == "attach"]))
    check("walk 중 Window 속성 접근 0회", trap.reads == [], str(trap.reads))
    check("walk 이 Store 내부로 들어가지 않음",
          not any(n.startswith("store.") for n in functions), str([n for n in functions if "." in n][:5]))

    # 3) 노출되는 공개 속성은 전부 함수(또는 _serializable=False)여야 한다
    public_noncallable = []
    for name in dir(api):
        if name.startswith("_"):
            continue
        attr = getattr(api, name)
        if getattr(attr, "_serializable", True) is False:
            continue
        if not (inspect.ismethod(attr) or inspect.isfunction(attr)):
            public_noncallable.append((name, type(attr).__name__))
    check("공개 비함수 속성 없음", public_noncallable == [], str(public_noncallable))
    check("Store 가 _serializable=False 마커를 가짐", getattr(Store, "_serializable", True) is False, "")

    # 4) bootstrap / bridge_ping 이 JSON 직렬화 가능한 dict 를 돌려준다
    boot = api.bootstrap()
    ping = api.bridge_ping()
    check("bootstrap ok", boot.get("ok") is True, str(boot.get("message"))[:120])
    check("bootstrap state 에 log_file 포함",
          bool((boot.get("state") or {}).get("meta", {}).get("log_file")),
          str((boot.get("state") or {}).get("meta", {}).get("log_file")))
    try:
        json.dumps(boot, ensure_ascii=False)
        serial = True
    except Exception as exc:  # noqa: BLE001
        serial = False
        print(exc)
    check("bootstrap 결과 JSON 직렬화 가능", serial, "")
    check("bridge_ping 응답", ping.get("ok") is True and ping.get("pong") is True, str(ping)[:100])

    # 5) main.py 의 진단 로그가 같은 결론을 보여 준다
    import main as app_main

    names = app_main._public_names(api)
    check("main 로그 · js_api 목록에 함수만",
          "bootstrap" in names and not any(":<" in n or ":ERROR" in n for n in names),
          ", ".join(names[:6]))
    check("main 로그 · store 는 skip 으로 표시", "store:skip" in names, ", ".join(n for n in names if "store" in n))
    check("비윈도우에서 WebView2 감지 안전", app_main._webview2_version() is None, "")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
