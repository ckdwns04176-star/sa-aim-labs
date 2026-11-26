"""v1.0.1 스크린샷 — 수정된 화면을 눈으로 검수하기 위한 캡처.

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \
    python3 tests/v101_shot.py

결과: tests/shots/v101-*.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

SHOTS = ROOT / "tests" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)

from v101_test import build_state, mock_js  # noqa: E402

OVERLAY_PAYLOAD = {
    "rows": [
        {"slot": 0, "name": "도봉이", "status": "ok", "score": 1.0, "clan": "히히"},
        {"slot": 1, "name": "ak-호야", "status": "ok", "score": 1.0, "clan": "히히"},
        {"slot": 2, "name": "hunny", "status": "corrected", "score": 0.86, "clan": "블러드"},
        {"slot": 3, "name": "llkkkllll", "status": "ok", "score": 1.0, "clan": "블러드"},
        {"slot": 4, "name": "영코", "status": "unknown", "score": 0.4, "clan": "행크건"},
    ],
    "plan": {
        "picks": [{"map": "프로방스(심플)", "expected": 61.2, "image": "assets/maps/m19.jpg"},
                  {"map": "제3보급창고", "expected": 57.4, "image": "assets/maps/m27.jpg"},
                  {"map": "드래곤로드(심플)", "expected": 55.0, "image": "assets/maps/m6.jpg"}],
        "bans": [{"map": "시티캣-심플", "expected": 38.0, "image": "assets/maps/m25.jpg"},
                 {"map": "이탈리아-폭파", "expected": 39.4, "image": "assets/maps/m231.jpg"},
                 {"map": "크로스포트(심플)", "expected": 41.0, "image": "assets/maps/m27.jpg"}],
    },
    "layout": {"chips": [{"left": 0, "width": 17.949}, {"left": 20.513, "width": 17.949},
                         {"left": 41.026, "width": 17.949}, {"left": 61.539, "width": 17.949},
                         {"left": 82.051, "width": 17.949}],
               "chip_top": 0, "chip_height": 41.429, "maps_top": 48.572, "maps_height": 51.428},
}


def main() -> int:
    from playwright.sync_api import sync_playwright

    state = build_state()
    index = (ROOT / "ui" / "index.html").as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 750})
        page.add_init_script(mock_js(state))
        page.goto(index)
        page.wait_for_selector("#app-splash", state="hidden", timeout=15000)
        page.wait_for_timeout(800)
        page.evaluate("() => { ['nick-mask','digest-mask','onboard-mask','update-mask','tilt-mask'].forEach(id => { const m = document.getElementById(id); if (m) m.classList.remove('show'); }); AL.nickLater = true; }")
        page.wait_for_timeout(300)

        page.screenshot(path=str(SHOTS / "v101-home.png"))

        page.click('.nav-item[data-screen="dashboard"]')
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "v101-dashboard.png"))
        page.screenshot(path=str(SHOTS / "v101-dashboard-head.png"),
                        clip={"x": 640, "y": 30, "width": 560, "height": 80})
        page.screenshot(path=str(SHOTS / "v101-dashboard-head-zoom.png"),
                        clip={"x": 900, "y": 44, "width": 300, "height": 56})

        page.click('.nav-item[data-screen="recs"]')
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "v101-recs.png"))
        page.screenshot(path=str(SHOTS / "v101-recs-head.png"), clip={"x": 300, "y": 30, "width": 900, "height": 70})

        page.click('.nav-item[data-screen="utils"]')
        page.wait_for_timeout(600)
        page.screenshot(path=str(SHOTS / "v101-utils.png"))

        page.click("#settings-btn")
        page.wait_for_timeout(500)
        page.evaluate("() => { const t = document.querySelector('#set-tabs [data-set-tab=\"overlay\"]'); if (t) t.click(); }")
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "v101-settings-overlay.png"))
        page.evaluate("() => document.getElementById('settings-mask').classList.remove('show')")

        # 닉네임 연결 팝업
        page.evaluate("() => { const st = JSON.parse(JSON.stringify(AL.state)); st.nexon.nickname = ''; st.settings.nickname = ''; st.barracks = null; AL.nickAutoShown = false; AL.nickLater = false; AL.applyState(st); }")
        page.wait_for_timeout(1000)
        page.screenshot(path=str(SHOTS / "v101-nick-popup.png"))
        browser.close()

    # ── 파티 오버레이를 실제 대기실 스크린샷 위에 합성해 위치를 눈으로 확인
    ref = ROOT.parent.parent / "attachments" / "2026-11-26" / "1000058472-c77fdffb431f485e8420a5029b8c48a5.jpg"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1560, "height": 70}, device_scale_factor=1)
        page.add_init_script("window.pywebview = {api: {overlay_payload: async () => (%s)}};"
                             % json.dumps({"ok": True, "payload": OVERLAY_PAYLOAD}, ensure_ascii=False))
        page.goto((ROOT / "ui" / "overlay_party.html").as_uri())
        page.wait_for_timeout(600)
        page.screenshot(path=str(SHOTS / "v101-overlay-party-row.png"), omit_background=True)
        browser.close()

    try:
        from PIL import Image
        if ref.exists():
            game = Image.open(ref).convert("RGBA")
            overlay = Image.open(SHOTS / "v101-overlay-party-row.png").convert("RGBA")
            # 클라이언트 (183,0) + 창 (67,473) → 스크린샷 좌표 (250, 473)
            base = game.copy()
            base.alpha_composite(overlay, (183 + 67, 473))
            base.convert("RGB").save(SHOTS / "v101-overlay-on-reference.png")
            base.crop((150, 380, 1900, 620)).convert("RGB").save(SHOTS / "v101-overlay-on-reference-crop.png")
    except Exception as exc:  # noqa: BLE001
        print("합성 실패:", exc)

    for f in sorted(SHOTS.glob("v101-*.png")):
        print(f.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
