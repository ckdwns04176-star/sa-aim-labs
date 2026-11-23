"""Boot behaviour of ui/index.html + ui/bridge.js in desktop (.exe) mode.

The user's report: "로딩이 다 된상태에서 메인창이 나오게 해야지.. 지금도 로딩 다 되고
메인창 가면 무응답인 창만떠 … 다 제기능을 못해".  The .exe ran on the HTML sample
data because the splash gave up after ~2.6 s and set AL.real = false.

These tests drive the real page in Chromium with a controllable fake
``window.pywebview`` and check:

  1. a bridge that arrives 3 s late still boots the app (splash never leaves early)
  2. the splash only finishes after bootstrap() data is painted
  3. no bridge (or a failing / hanging bootstrap) in desktop mode shows the
     error + retry screen instead of the sample data
  4. browser preview (?preview=1) keeps the old sample-data behaviour

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \\
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \\
    python3 tests/boot_test.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import ui_test  # noqa: E402  (build_state 만 재사용)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""))


def mock_js(state: dict, *, delay: int = 0, mode: str = "ok") -> str:
    """Fake pywebview.  mode: ok | fail | hang | none."""
    return r"""
    (function () {
      const MODE = %s, DELAY = %s;
      window.__PY_STATE = %s;
      if (MODE === 'none') return;
      const S = () => JSON.parse(JSON.stringify(window.__PY_STATE));
      const api = {
        async bootstrap() {
          if (MODE === 'hang') return new Promise(function () {});
          if (MODE === 'fail') return {ok: false, message: '테스트 실패 원인 (bootstrap error)'};
          return {ok: true, state: S()};
        },
        async get_state() { return {ok: true, state: S()}; },
        async bridge_ping() { return {ok: true, pong: true}; },
        async get_nvidia_state() {
          return {ok: true, utils: {supported: true, pending: false, mode: null, alias: false, backups: [],
            points: [], modes: {}, missing: [], last_backup: null, game_running: null, writable: false,
            drs_exists: false, drs_dir: '', error: null, needs_admin: false, accel: false,
            stairfix: {ready: false, applied: false, value: null, label: '확인 중', target: '0x00401241'}},
            state: S()};
        },
        async open_data_folder() { return {ok: true}; },
        async close_window() { return {ok: true}; }
      };
      const install = function () {
        window.pywebview = {api: api};
        window.dispatchEvent(new Event('pywebviewready'));
      };
      setTimeout(install, DELAY);
    })();
    """ % (json.dumps(mode), int(delay), json.dumps(state, ensure_ascii=False))


SPLASH_STATE = """() => {
  const el = document.getElementById('app-splash');
  const style = getComputedStyle(el);
  return {visible: !!el && !el.classList.contains('out') && style.display !== 'none' && style.visibility !== 'hidden',
          pct: (document.getElementById('sp-pct') || {}).textContent || '',
          step: (document.getElementById('sp-step') || {}).textContent || ''};
}"""

BOOT_STATE = """() => ({
  real: !!window.AL && window.AL.real === true,
  ready: !!window.AL && window.AL.ready === true,
  nickname: (window.AL && window.AL.state && window.AL.state.settings && window.AL.state.settings.nickname) || '',
  overlay: (() => { const el = document.getElementById('al-bridge-error');
    if (!el) return null;
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return {shown: !el.hidden && s.display !== 'none', z: s.zIndex, text: el.innerText,
            covers: r.width >= window.innerWidth - 2 && r.height >= window.innerHeight - 2};
  })()
})"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    state = ui_test.build_state()
    base = (ROOT / "ui" / "index.html").as_uri()

    # ── 0-a) main.py 가 webview.create_window 에 넘기는 url 은 paths.index_url() 그대로여야 한다.
    #       v1.0.1 은 여기에 ?desktop=1&log=...&wv2=0 을 붙여 WebView2 가 파일을 못 찾았다.
    import ast

    from app import paths as app_paths

    check("index_url() 에 쿼리스트링 없음",
          "?" not in app_paths.index_url() and app_paths.index_url().startswith("file:"),
          app_paths.index_url())
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(main_src)
    url_assigns = [
        node.value
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for t in node.targets if isinstance(t, ast.Name) and t.id == "url"
    ]
    url_aug = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == "url"
    ]
    url_values = [
        kw.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_window"
        for kw in node.keywords if kw.arg == "url"
    ]
    pure_url = (
        len(url_values) == 1
        and isinstance(url_values[0], ast.Name) and url_values[0].id == "url"
        and len(url_assigns) == 1
        and isinstance(url_assigns[0], ast.Call)
        and isinstance(url_assigns[0].func, ast.Attribute)
        and url_assigns[0].func.attr == "index_url"
        and not url_assigns[0].args
        and not url_aug
    )
    check("create_window(url=paths.index_url()) 순수 URI", pure_url,
          (ast.unparse(url_assigns[0]) if url_assigns else "url 대입을 찾지 못함")
          + (" / 누적 대입 있음" if url_aug else ""))
    string_consts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    check("main.py 코드에 쿼리스트링 문자열 없음",
          not any(('desktop=1&log' in s) or ('wv2=0' in s) or ('urllib.parse.quote' in s) for s in string_consts),
          "")
    check("APP_VERSION 1.4.0", app_paths.APP_VERSION == "1.4.0", app_paths.APP_VERSION)

    # ── 0) 쿼리스트링이 전혀 없는 순수 file:// URL 로도 데스크톱 부팅이 된다.
    #       v1.0.1 은 main.py 가 ?desktop=1&log=...&wv2=0 을 붙이면서 WebView2 가
    #       그 URL 을 파일 경로로 해석해 "파일을 찾을 수 없음" 을 띄웠다.  이제
    #       .exe 는 paths.index_url() 만 쓰므로 이 경우가 실제 배포 형태다.
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])

        def open_page(query: str, script: str):
            page = browser.new_page(viewport={"width": 1200, "height": 750})
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.add_init_script(script)
            page.goto(base + query)
            return page, errors

        page, errors = open_page("", mock_js(state))
        page.wait_for_selector("#app-splash", state="hidden", timeout=8000)
        pure = page.evaluate("""() => ({
          desktop: window.__AL_DESKTOP__ === true,
          file: location.protocol === 'file:',
          search: location.search,
          real: !!window.AL && window.AL.real === true,
          ready: !!window.AL && window.AL.ready === true,
          overlay: (() => { const el = document.getElementById('al-bridge-error');
            return !!el && !el.hidden && getComputedStyle(el).display !== 'none'; })()
        })""")
        check("순수 file:// (쿼리스트링 없음) → 데스크톱 모드",
              pure["file"] and pure["desktop"] and pure["search"] == "",
              json.dumps(pure, ensure_ascii=False)[:120])
        check("순수 file:// 로 정상 부팅 · 오류 화면 없음",
              pure["real"] and pure["ready"] and not pure["overlay"],
              json.dumps(pure, ensure_ascii=False)[:120])
        check("순수 file:// JS 오류 없음", not errors, "; ".join(errors[:2]))
        page.close()

        # ── 1) 브리지가 4초 늦게 도착해도 스플래시가 먼저 걷히지 않고, 데이터가
        #       그려진 뒤에만 메인창이 열린다 (사용자가 겪은 증상의 정확한 재현)
        #       쿼리에는 데스크톱 테스트용 boottimeout 만 둔다 (?desktop=1 없이도 데스크톱)
        page, errors = open_page("?boottimeout=15000", mock_js(state, delay=4000))
        # 페이지 기준 경과 시간을 직접 재서 본다 (goto 가 load 를 기다리는 시간 제외)
        def wait_until_page_ms(ms: float) -> None:
            elapsed = page.evaluate("performance.now()")
            page.wait_for_timeout(max(0, ms - elapsed))

        wait_until_page_ms(2400)
        early = page.evaluate(SPLASH_STATE)
        check("브리지 4초 지연 · 2.4초에 스플래시 유지", early["visible"] and early["pct"] != "100%",
              json.dumps(early, ensure_ascii=False))
        wait_until_page_ms(2700)   # 기존 코드가 포기하던 2.6초 지점
        at2600 = page.evaluate(SPLASH_STATE)
        check("2.7초(기존 포기 시점)에도 스플래시 유지",
              at2600["visible"] and at2600["pct"] != "100%", json.dumps(at2600, ensure_ascii=False))
        page.wait_for_selector("#app-splash", state="hidden", timeout=15000)
        done = page.evaluate(BOOT_STATE)
        check("늦은 브리지로도 정상 부팅", done["real"] and done["ready"], json.dumps(done, ensure_ascii=False)[:160])
        check("실제 데이터(state) 적용됨", done["nickname"] == "휴가중.", f"nickname={done['nickname']!r}")
        check("오류 화면 없음", not (done["overlay"] or {}).get("shown"), str((done["overlay"] or {}).get("text", ""))[:60])
        check("지연 부팅 중 JS 오류 없음", not errors, "; ".join(errors[:2]))
        page.close()

        # ── 2) 브리지가 아예 없으면 .exe 에서는 샘플 데이터 대신 오류·재시도 화면,
        #       로그 경로는 브리지가 없으니 기본 안내 문구(%APPDATA%\SA_Aim_Labs\logs\app.log)
        page, errors = open_page("?boottimeout=1600", mock_js(state, mode="none"))
        page.wait_for_selector("#al-bridge-error", state="visible", timeout=8000)
        page.wait_for_timeout(200)
        err = page.evaluate(BOOT_STATE)
        ov = err["overlay"] or {}
        check("브리지 없음 → 오류 화면 표시", ov.get("shown") is True, str(ov.get("text", ""))[:60])
        check("오류 화면이 전체 화면 덮음", bool(ov.get("covers")) and ov.get("z") == "10000",
              f"covers={ov.get('covers')} z={ov.get('z')}")
        check("오류 제목 표시", "앱 데이터에 연결하지 못했습니다" in (ov.get("text") or ""), (ov.get("text") or "")[:40])
        check("다시 시도 버튼", "다시 시도" in (ov.get("text") or ""), "")
        check("app.log 경로 안내", "app.log" in (ov.get("text") or ""), "")
        check("브리지 없음 → 기본 %APPDATA% 로그 경로 안내",
              "%APPDATA%" in (ov.get("text") or "") and "SA_Aim_Labs" in (ov.get("text") or ""),
              (ov.get("text") or "")[-120:])
        check("샘플 데이터를 실제 앱으로 쓰지 않음(AL.real=false)", err["real"] is False, str(err["real"]))
        splash2 = page.evaluate(SPLASH_STATE)
        check("오류 시 스플래시는 걷힘", splash2["visible"] is False, json.dumps(splash2, ensure_ascii=False))
        check("브리지 없음 시나리오 JS 오류 없음", not errors, "; ".join(errors[:2]))
        page.close()

        # ── 3) bootstrap 이 에러를 돌려주면 원인 문구가 그대로 보인다
        page, _ = open_page("?desktop=1", mock_js(state, mode="fail"))
        page.wait_for_selector("#al-bridge-error", state="visible", timeout=8000)
        text = page.evaluate("document.getElementById('al-bridge-error').innerText")
        check("bootstrap 실패 원인 표시", "테스트 실패 원인" in text, text.replace("\n", " | ")[:80])
        page.close()

        # ── 4) bootstrap 이 응답 없이 멈춰도(무응답) 워치독이 오류 화면을 띄운다
        page, _ = open_page("?desktop=1&boottimeout=1200", mock_js(state, mode="hang"))
        page.wait_for_selector("#al-bridge-error", state="visible", timeout=8000)
        text = page.evaluate("document.getElementById('al-bridge-error').innerText")
        check("bootstrap 무응답 → 워치독 오류 화면", "응답이 없습니다" in text, text.replace("\n", " | ")[:80])
        page.close()

        # ── 5) 브라우저 미리보기(?preview=1)는 예전처럼 샘플 데이터로 뜬다
        page, errors = open_page("?preview=1", mock_js(state, mode="none"))
        page.wait_for_timeout(2600)
        prev = page.evaluate(BOOT_STATE)
        check("미리보기 모드에서는 스플래시 자동 종료",
              page.evaluate(SPLASH_STATE)["visible"] is False, "")
        check("미리보기 모드에는 오류 화면 없음", not (prev["overlay"] or {}).get("shown"), "")
        check("미리보기 모드 JS 오류 없음", not errors, "; ".join(errors[:2]))
        page.close()

        # ── 5-b) 실제 브라우저 미리보기(--serve)는 http://127.0.0.1 → 데스크톱 모드 아님
        import functools
        import http.server
        import threading

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "ui"))
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 750})
            http_errors: list[str] = []
            page.on("pageerror", lambda exc: http_errors.append(str(exc)))
            page.add_init_script(mock_js(state, mode="none"))
            page.goto(f"http://127.0.0.1:{port}/index.html")
            page.wait_for_timeout(2600)
            served = page.evaluate("""() => ({
              protocol: location.protocol,
              desktop: window.__AL_DESKTOP__ === true,
              splash: (() => { const el = document.getElementById('app-splash');
                return !!el && !el.classList.contains('out') && getComputedStyle(el).display !== 'none'; })(),
              overlay: (() => { const el = document.getElementById('al-bridge-error');
                return !!el && !el.hidden && getComputedStyle(el).display !== 'none'; })()
            })""")
            check("--serve(http://) 는 데스크톱 모드가 아니다",
                  served["protocol"] == "http:" and served["desktop"] is False,
                  json.dumps(served, ensure_ascii=False))
            check("--serve(http://) 는 미리보기로 뜬다(오류 화면 없음)",
                  served["splash"] is False and served["overlay"] is False,
                  json.dumps(served, ensure_ascii=False))
            check("--serve(http://) JS 오류 없음", not http_errors, "; ".join(http_errors[:2]))
            page.close()
        finally:
            httpd.shutdown()

        # ── 6) 즉시 준비된 브리지: 스플래시 100% 뒤 메인창
        page, errors = open_page("?desktop=1", mock_js(state))
        page.wait_for_selector("#app-splash", state="hidden", timeout=8000)
        final = page.evaluate("""() => ({
          pct: (document.getElementById('sp-pct') || {}).textContent,
          real: !!window.AL && window.AL.real === true,
          ready: !!window.AL && window.AL.ready === true})""")
        check("즉시 브리지 · 100% 후 메인창", final["pct"] == "100%" and final["real"] and final["ready"],
              json.dumps(final, ensure_ascii=False))
        check("즉시 브리지 JS 오류 없음", not errors, "; ".join(errors[:2]))
        page.close()

        # ── 6-b) ?desktop=1 은 여전히 데스크톱 모드 (하위 호환 · 테스트용)
        page, _ = open_page("?desktop=1", mock_js(state))
        page.wait_for_selector("#app-splash", state="hidden", timeout=8000)
        flag = page.evaluate("""() => ({
          desktop: window.__AL_DESKTOP__ === true,
          url_has_query: location.search.includes('desktop=1'),
          real: !!window.AL && window.AL.real === true})""")
        check("?desktop=1 → 데스크톱 모드 유지", flag["desktop"] and flag["real"],
              json.dumps(flag, ensure_ascii=False))
        page.close()

        # ── 7) 로그 경로는 bootstrap 응답의 meta.log_file 을 쓴다 (URL 로 넘기지 않음)
        page, _ = open_page("", mock_js(state))
        page.wait_for_selector("#app-splash", state="hidden", timeout=8000)
        meta = page.evaluate("""() => ({
          hint: window.__AL_LOG__ || '',
          meta: (window.AL && window.AL.state && window.AL.state.meta && window.AL.state.meta.log_file) || '',
          search: location.search})""")
        check("URL 에 로그 경로 없음", "log=" not in (meta["search"] or ""), str(meta["search"]))
        check("bootstrap meta.log_file 을 로그 경로로 사용",
              bool(meta["meta"]) and meta["hint"] == meta["meta"] and meta["hint"].endswith("app.log"),
              json.dumps(meta, ensure_ascii=False)[:160])
        page.close()

        browser.close()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
