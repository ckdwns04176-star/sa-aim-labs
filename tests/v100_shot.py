"""v1.0.0 화면 캡처 — 전 화면 + 오버레이 3종 (tests/shots/v100-*.png).

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \\
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \\
    python3 tests/v100_shot.py [테마]
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

from v140_test import build_state, mock_js  # noqa: E402

SCREENS = [
    ("home", "s-home"), ("dashboard", "s-dashboard"), ("recs", "s-recs"),
    ("records", "s-records"), ("barracks", "s-barracks"), ("maps", "s-maps"),
    ("leaderboard", "s-leaderboard"), ("tiers", "s-tiers"), ("utils", "s-utils"),
]


def main(theme: str = "coral") -> int:
    from playwright.sync_api import sync_playwright

    state = build_state()
    state["theme"] = theme
    state["guest"] = False
    state["overlay"] = {
        "supported": True,
        "settings": {"enabled": True, "hotkey_lobby": "F8", "hotkey_scout": "F9",
                     "mapban_seconds": 12, "toast_position": "br"},
        "hotkeys": {"registered": ["lobby", "scout"], "failed": {}},
        "open": [],
    }
    state["party_presets"] = [{"name": "새벽 고정팟", "members": ["휴가중.", "0.1초의각", "탐피수집가", "무방상핑", "헤더온리"], "count": 5,
                               "created": "2026-11-26T06:00:00+09:00"}]
    url = (ROOT / "ui" / "index.html").as_uri()
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 750}, device_scale_factor=1)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append("console:" + msg.text) if msg.type == "error" else None)
        page.add_init_script(mock_js(state))
        page.goto(url)
        page.wait_for_selector("#app-splash", state="hidden", timeout=15000)
        page.wait_for_timeout(600)
        if page.evaluate("() => { const m=document.getElementById('digest-mask'); return !!m && m.classList.contains('show'); }"):
            page.click("#digest-ack")
            page.wait_for_timeout(250)
        if page.evaluate("() => { const m=document.getElementById('onb-mask'); return !!m && m.classList.contains('show'); }"):
            page.evaluate("() => document.getElementById('onb-mask').classList.remove('show')")

        for name, section in SCREENS:
            page.evaluate(f"() => showScreen('{name}')")
            page.wait_for_timeout(380)
            if name in ("barracks", "maps"):
                page.evaluate("() => window.AL && AL.onScreen && AL.onScreen('" + name + "')")
                page.wait_for_timeout(250)
            page.screenshot(path=str(SHOTS / f"v100-{name}.png"))

        # 게스트 홈
        page.evaluate("() => { const st=window.AL.state; st.guest=true; st.barracks=null; st.nexon.nickname=''; st.settings.nickname=''; drawHome(); showScreen('home'); }")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "v100-home-guest.png"))
        page.evaluate("() => { const st=window.AL.state; st.guest=false; st.barracks=%s; drawHome(); }" % json.dumps(state["barracks"], ensure_ascii=False))

        # 설정 탭
        page.evaluate("() => document.getElementById('settings-mask').classList.add('show')")
        page.wait_for_timeout(300)
        for tab in ("api", "overlay", "run", "cloud", "more"):
            page.evaluate(f"() => document.querySelector('[data-set-tab=\"{tab}\"]').click()")
            page.wait_for_timeout(250)
            page.screenshot(path=str(SHOTS / f"v100-settings-{tab}.png"))
        page.evaluate("() => document.querySelector('#settings-mask .modal-bd').scrollTop = 9999")
        page.wait_for_timeout(200)
        page.screenshot(path=str(SHOTS / "v100-settings-overlay-bottom.png"))
        page.evaluate("() => document.getElementById('settings-mask').classList.remove('show')")

        # 1:1 비교 모달
        page.evaluate("""() => {
          document.getElementById('cmp-title').textContent = '휴가중. vs 0.1초의각';
          document.getElementById('cmp-body').innerHTML = `
            <div class="cmp-hero"><div class="cmp-card me"><div class="nm">휴가중.</div><div class="cl">리드</div><div class="rp">그랜드마스터 II</div><div class="rp">그랜드마스터 III</div></div>
            <div class="cmp-vs">VS<br><span style="font-size:9px;color:#7E8FA6">2,075 : 1,980</span></div>
            <div class="cmp-card"><div class="nm">0.1초의각</div><div class="cl">리드</div><div class="rp">그랜드마스터 II</div><div class="rp">그랜드마스터 III</div></div></div>
            <div class="cmp-rows"><div class="cmp-row"><span class="lb">솔로 RP</span><span class="cmp-val win">3,261</span><div class="cmp-bar"><i class="a" style="width:52%"></i><i class="b" style="width:48%"></i></div><span class="cmp-val left">3,208</span></div>
            <div class="cmp-row"><span class="lb">최근 K/D</span><span class="cmp-val">51.5%</span><div class="cmp-bar"><i class="a" style="width:51%"></i><i class="b" style="width:49%"></i></div><span class="cmp-val left win">49.3%</span></div></div>
            <div class="mnote">근소하게 앞섭니다 · 솔로 RP +53</div>`;
          document.getElementById('compare-mask').classList.add('show');
        }""")
        page.wait_for_timeout(250)
        page.screenshot(path=str(SHOTS / "v100-compare.png"))
        page.evaluate("() => document.getElementById('compare-mask').classList.remove('show')")
        browser.close()

    # 오버레이 페이지 (투명 창 내용 확인용 — 배경만 어둡게)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1520, "height": 330})
        page.goto((ROOT / "ui" / "overlay_match.html").as_uri())
        page.wait_for_timeout(300)
        page.evaluate("""() => { document.body.style.background = '#151b25';
          OV.render({tab:'mapban', tab_pad:44, seconds:12, cards:[
            {map:'펠리스', pct:62, tone:'pick', label:'PICK', gauge:62},
            {map:'시티캣(심플)', pct:49, tone:'neutral', label:'', gauge:49},
            {map:'제5보급창고', pct:38, tone:'ban', label:'BAN', gauge:38},
            {map:'프로방스(심플)', pct:55, tone:'pick', label:'PICK', gauge:55},
            {map:'크로스포트(심플)', pct:41, tone:'ban', label:'BAN', gauge:41}]}); }""")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "v100-overlay-mapban.png"))

        page.set_viewport_size({"width": 470, "height": 332})
        page.evaluate("""() => OV.render({tab:'scout', map:'제3보급창고', elapsed:2.1, source:'ocr', rows:[
            {name:'0.1초의각', weapon:'스나', map_win:64, kd:61},
            {name:'탐피수집가', weapon:'라플', map_win:48, kd:54},
            {name:'무방상핑', weapon:'스나', map_win:71, kd:66},
            {name:'헤더온리', weapon:'스나', win:55, kd:58},
            {name:'안구2', weapon:'라플', map_win:44, kd:49}]})""")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "v100-overlay-scout.png"))

        page.set_viewport_size({"width": 372, "height": 96})
        page.goto((ROOT / "ui" / "overlay_toast.html").as_uri())
        page.wait_for_timeout(250)
        page.evaluate("""() => { document.body.style.background = '#151b25';
          OV.render({win:true, badge:'WIN', title:'제3보급창고 승리 · 14K 6D',
                     sub:'딜량 1,840 · K/D 2.33 · 3연승 중', seconds:5}); }""")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "v100-overlay-toast.png"))

        page.set_viewport_size({"width": 760, "height": 150})
        page.goto((ROOT / "ui" / "overlay_party.html").as_uri())
        page.wait_for_timeout(250)
        page.evaluate("""() => { document.body.style.background = '#151b25';
          OV.render({me:'휴가중.', presets:[{name:'새벽 고정팟', members:['a']}], rows:[
            {name:'휴가중.', status:'ok', score:1},
            {name:'0.1초의각', status:'corrected', score:0.82},
            {name:'탐피수집가', status:'ok', score:1},
            {name:'무방상핑', status:'unknown', score:0.4},
            {name:'헤더온리', status:'ok', score:1}]}); }""")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "v100-overlay-party.png"))
        browser.close()

    print("errors:", errors[:5] if errors else "[]")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "coral"))
