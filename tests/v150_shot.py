"""v1.5.0 화면 캡처 — 새 디자인 검수용 (tests/shots/v150-*.png).

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \
    python3 tests/v150_shot.py [테마]
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

from v140_test import build_state, mock_js, ROOT as TROOT  # noqa: E402

SCREENS = [
    ("home", "s-home"),
    ("dashboard", "s-dashboard"),
    ("library", "s-recs"),
    ("records", "s-records"),
    ("barracks", "s-barracks"),
    ("maps", "s-maps"),
    ("leaderboard", "s-leaderboard"),
    ("tiers", "s-tiers"),
    ("utils", "s-utils"),
]


def main(theme: str = "coral") -> int:
    from playwright.sync_api import sync_playwright

    state = build_state()
    state["theme"] = theme
    if theme == "coral":
        # 등락 뱃지가 보이는지 확인하려면 스냅샷 2개가 필요하다
        h = state.get("barracks_history") or []
        if len(h) >= 2:
            state["delta"] = [{"key": "solo", "label": "솔로", "text": "솔로 +38 RP", "tone": "up",
                               "diff": 38, "unit": " RP"}]
    url = (ROOT / "ui" / "index.html").as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 750}, device_scale_factor=1)
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append("console:" + msg.text) if msg.type == "error" else None)
        page.add_init_script(mock_js(state))
        page.goto(url)
        page.wait_for_selector("#app-splash", state="hidden", timeout=15000)
        page.wait_for_timeout(600)
        if page.evaluate("() => { const m=document.getElementById('digest-mask'); return !!m && m.classList.contains('show'); }"):
            page.click("#digest-ack")
            page.wait_for_timeout(250)
        if page.evaluate("() => document.getElementById('onb-mask') && document.getElementById('onb-mask').classList.contains('show')"):
            page.click("#onb-skip")
            page.wait_for_timeout(250)
        for name, sid in SCREENS:
            page.evaluate("(id) => window.showScreen && window.showScreen(id.replace('s-','')) || null", sid)
            page.wait_for_timeout(60)
            page.evaluate("""(sid) => {
                document.querySelectorAll('.screen').forEach(s => { s.hidden = (s.id !== sid); });
                const nav = document.querySelector(`.nav-item[data-screen="${sid.replace('s-','')}"]`);
                document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('on', b === nav));
            }""", sid)
            page.wait_for_timeout(320)
            page.screenshot(path=str(SHOTS / f"v150-{theme}-{name}.png"))
        print("errors:", errors[:5])
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "coral"))
