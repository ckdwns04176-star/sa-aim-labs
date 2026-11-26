"""v2.0.0 화면 캡처 — 홈 · 병영수첩 · 맵 · 순위표 · 전술 도구 · 설정 · 로그인 · 듀오 (100%/115%).

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \\
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \\
    python3 tests/v2_shot.py

결과는 tests/shots/v2-*.png 에 저장한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from v2_fixtures import build_state, mock_js  # noqa: E402

SHOTS = ROOT / "tests" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)

SCREENS = [("barracks", "s-barracks"), ("maps", "s-maps"),
           ("leaderboard", "s-leaderboard"), ("utils", "s-utils")]


def _settle(page) -> None:
    page.wait_for_selector("#app-splash", state="hidden", timeout=20000)
    page.wait_for_timeout(650)
    for mask in ("digest-mask", "onb-mask", "update-mask", "nick-mask"):
        if page.evaluate(f"()=>document.getElementById('{mask}')?.classList.contains('show')"):
            btn = page.query_selector(f"#{mask} [data-close], #{mask} .x")
            if btn:
                btn.click()
                page.wait_for_timeout(200)


def capture(scale: int = 100, theme: str = "coral") -> list[str]:
    from playwright.sync_api import sync_playwright

    state = build_state()
    state["digest"]["show"] = False
    state["ui_scale"] = scale
    state["theme"] = theme
    zoom = scale / 100
    viewport = {"width": int(1200 * zoom), "height": int(750 * zoom)}
    produced: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport=viewport, device_scale_factor=1)
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.add_init_script(mock_js(state))
        page.goto((ROOT / "ui" / "index.html").as_uri())
        _settle(page)
        tag = f"{scale}"
        if scale == 115:
            # 115% 는 창이 1380×862 로 커진다 — UI 배율도 같이 맞춘다
            page.evaluate("()=>{document.body.style.zoom='1.15';}")
            page.wait_for_timeout(300)
        path = SHOTS / f"v2-{tag}-home.png"
        page.query_selector("#s-home").screenshot(path=str(path))
        produced.append(str(path))
        for name, sid in SCREENS:
            page.evaluate("""(sid)=>{document.querySelectorAll('.screen').forEach(s=>{s.hidden=(s.id!==sid)});
              const key=sid.replace('s-','');
              document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('on',b.dataset.screen===key));
              if(window.AL&&AL.onScreen) AL.onScreen(key);
              if(key==='maps'&&window.drawMaps) drawMaps();
              if(key==='leaderboard'&&window.drawLeaderboard) drawLeaderboard();
              if(key==='utils'&&window.renderUtils) renderUtils();}""", sid)
            page.wait_for_timeout(420)
            path = SHOTS / f"v2-{tag}-{name}.png"
            page.query_selector("#" + sid).screenshot(path=str(path))
            produced.append(str(path))
        if scale == 100:
            # 전술 도구 · 시스템 최적화 탭
            page.evaluate("()=>window.showUtilTab && window.showUtilTab('system')")
            page.wait_for_timeout(350)
            path = SHOTS / "v2-100-utils-system.png"
            page.query_selector("#s-utils").screenshot(path=str(path))
            produced.append(str(path))
            # 설정 5개 탭
            page.evaluate("()=>document.getElementById('settings-mask')?.classList.add('show')")
            page.wait_for_timeout(250)
            for tab in ("api", "overlay", "run", "cloud", "more"):
                page.evaluate("""(tab)=>{document.querySelectorAll('#set-tabs [data-set-tab]').forEach(b=>b.classList.toggle('on',b.dataset.setTab===tab));
                  document.querySelectorAll('#set-panes .set-pane').forEach(p=>p.classList.toggle('on',p.dataset.pane===tab));}""", tab)
                page.wait_for_timeout(220)
                path = SHOTS / f"v2-100-settings-{tab}.png"
                page.query_selector("#settings-mask .modal").screenshot(path=str(path))
                produced.append(str(path))
            page.evaluate("()=>document.getElementById('settings-mask')?.classList.remove('show')")
            # 로그인 · 비밀번호 찾기
            page.evaluate("()=>window.AL && AL.openAuth && AL.openAuth('login')")
            page.wait_for_timeout(260)
            path = SHOTS / "v2-100-auth-login.png"
            page.query_selector("#auth-mask .modal").screenshot(path=str(path))
            produced.append(str(path))
            page.evaluate("()=>{document.querySelector('[data-auth-tab=recover]')?.click();}")
            page.wait_for_timeout(220)
            path = SHOTS / "v2-100-auth-recover.png"
            page.query_selector("#auth-mask .modal").screenshot(path=str(path))
            produced.append(str(path))
            page.evaluate("()=>document.getElementById('auth-mask')?.classList.remove('show')")
            # 듀오 A+B 결과
            page.evaluate("()=>window.AL && AL.openDuoBetween('상대에이스','상대스나')")
            page.wait_for_timeout(500)
            path = SHOTS / "v2-100-duo.png"
            page.query_selector("#compare-mask .modal").screenshot(path=str(path))
            produced.append(str(path))
        print("errors:", errors[:5])
        browser.close()
    return produced


if __name__ == "__main__":
    made = capture(100)
    made += capture(115)
    print("\n".join(made))
