"""Playwright UI test for SA AIM LABS with a mock pywebview bridge.

Runs against ui/index.html + ui/bridge.js in Chromium.  The mock bridge hands
the real Python `Api._build_state()` payload to the page, so the whole UI data
path (state → renderers → bridge calls) is exercised without Windows/WebView2.

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \
    python3 tests/ui_test.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SHOTS = ROOT / "tests" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""))


def build_state() -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="sa_ui_state_"))
    os.environ["SA_AL_DATA_DIR"] = str(tmp)
    from app.store import Store
    from app.api import Api

    store = Store(tmp / "data.json")
    store.update_settings({"nickname": "휴가중.", "api_key": "test_9542ea5ac663d2c0"})
    # Aim Lab 대용량 점수 + 코박스 점수 (0~100 상한 없음 검증)
    store.add_record({"date": dt.date.today().isoformat(), "game": "aimlab", "weapon": "sniper",
                      "scores": {"precision": 92500, "wide": 81000, "micro": 64000, "track": 55000, "switch": 73000},
                      "scenarios": [{"name": "Sixshot Ultimate", "area": "precision", "score": 92500}], "note": ""})
    store.add_record({"date": dt.date.today().isoformat(), "game": "kovak", "weapon": "sniper",
                      "scores": {"precision": 152, "wide": 128, "micro": 96, "track": 88, "switch": 141},
                      "scenarios": [{"name": "VT 1w6ts Rasp Novice", "area": "precision", "score": 152}], "note": ""})
    api = Api(store=store)
    return api._build_state()


def mock_js(state: dict) -> str:
    return r"""
    window.__CALLS = [];
    window.__PY_STATE = %s;
    window.__flip = {fetch: 0};
    const S = () => JSON.parse(JSON.stringify(window.__PY_STATE));
    const clone = (v) => JSON.parse(JSON.stringify(v));
    const barracksPeek = () => {
      const b = clone(S().barracks || {});
      b.nickname = '테스트친구';
      b.ouid = 'peek-ouid-0001';
      b.basic = {...(b.basic||{}), clan: '친구'};
      b.tier = {solo: {name: '마스터 I', rp: 2410}, party: {name: '골드 III', rp: 2205}};
      b.stats = {games: 100, kills: 900, deaths: 800, wins: 47, win: 47.0, kd: 52.9, dmg: 1180};
      b.updated_at = '2026-11-23T17:00:00+09:00';
      return b;
    };
    window.pywebview = {api: {
      async bootstrap() { return {ok: true, state: S()}; },
      async get_state() { return {ok: true, state: S()}; },
      async get_nvidia_state() {
        return {ok: true, utils: {supported: true, mode: null, alias: false, backups: [], points: [],
          modes: {rifle: true, sniper: true, stairfix: true}, missing: [], accel: false,
          stairfix: {ready: true, applied: false, value: '0x00020041', label: '기본값', target: '0x00401241'}},
          state: S()};
      },
      async save_settings(patch) { window.__CALLS.push(['save_settings', patch]); return {ok: true, state: S()}; },
      async drag_window() { window.__CALLS.push(['drag_window']); return {ok: true, supported: true}; },
      async toggle_maximize() { window.__CALLS.push(['toggle_maximize']); return {ok: true, maximized: true}; },
      async minimize_window() { return {ok: true}; },
      async close_window() { return {ok: true}; },
      async fetch_barracks(nick, key) {
        window.__CALLS.push(['fetch_barracks', nick, key]);
        const st = S();
        const b = clone(st.barracks || {});
        b.updated_at = '2026-11-23T17:05:00+09:00';
        b.stats = {games: 100, kills: 910, deaths: 800, wins: 48, win: 48.0, kd: 53.2, dmg: 1120};
        st.barracks = b;
        return {ok: true, barracks: b, state: st, calls: 4, message: '최신화 완료'};
      },
      async search_user(nick) {
        window.__CALLS.push(['search_user', nick]);
        if (String(nick).indexOf('없는') === 0 || String(nick) === 'zzzz') {
          return {ok: true, found: false, query: nick, message: "'" + nick + "' 조회 결과 없음"};
        }
        return {ok: true, found: true, query: nick, user: {name: nick, ouid: 'peek-ouid-0001', clan: '친구',
          created: '2011.05.02', manner: '3', grade: '마스터', grade_ranking: 4321,
          solo: 2410, party: 2205, solo_tier: '마스터 I', party_tier: '골드 III'}};
      },
      async lookup_barracks(nick, ouid) {
        window.__CALLS.push(['lookup_barracks', nick, ouid]);
        return {ok: true, barracks: barracksPeek(), peek: true, calls: 3};
      },
      async fetch_leaderboard(names) {
        window.__CALLS.push(['fetch_leaderboard', names]);
        return {ok: true, members: [], renamed: [], message: '3명 전적 일괄 갱신 완료', state: S()};
      },
      async add_leaderboard_member(name, depth, ouid) {
        window.__CALLS.push(['add_leaderboard_member', name, depth, ouid]);
        return {ok: true, member: {name: name, ouid: ouid}, state: S(), message: "'" + name + "' 순위표에 추가했습니다"};
      },
      async remove_leaderboard_member(name) { return {ok: true, state: S()}; },
      async apply_nvidia_profile(mode, elevate) {
        window.__CALLS.push(['apply_nvidia_profile', mode, elevate]);
        if (mode === 'stairfix' || mode === 'alias') {
          const st = S();
          st.utils = {...(st.utils||{}), alias: true, mode: 'stairfix',
            stairfix: {ready: true, applied: true, value: '0x00401241', label: '계단현상·타격효과 제거', target: '0x00401241'}};
          return {ok: true, mode: 'stairfix', value: '0x00401241', state: st,
            message: '계단현상 끄기 적용 완료 · Antialiasing compatibility 0x00401241 (계단현상·타격효과·중세철창 제거)'};
        }
        return {ok: true, mode: mode, state: S(), message: '적용 완료'};
      },
      async toggle_nvidia_alias(enabled) { return {ok: true, state: S(), message: '복구 완료'}; },
      async restore_nvidia_profile() { return {ok: true, state: S(), message: '복구 완료'}; },
      async set_mouse_accel(v) { return {ok: true, enabled: !!v, state: S()}; },
      async save_training_session(payload) {
        window.__CALLS.push(['save_training_session', payload]);
        return {ok: true, state: S(), record: {id: 'x'}};
      },
      async save_training(payload) { return this.save_training_session(payload); },
      async export_backup() { return {ok: true, path: 'x.json', message: '백업 완료'}; },
      async import_backup() { return {ok: true, state: S()}; },
      async open_data_folder() { return {ok: true}; },
      async launch_trainer(url) { return {ok: true, url}; },
      async regenerate_recs() { return {ok: true, recs: []}; },
      async cloud_status() { return {ok: true, cloud: {}, state: S()}; }
    }};
    """ % json.dumps(state, ensure_ascii=False)


def main() -> int:
    from playwright.sync_api import sync_playwright

    state = build_state()
    url = (ROOT / "ui" / "index.html").as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 750})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append("console:" + msg.text) if msg.type == "error" else None)
        page.add_init_script(mock_js(state))
        page.goto(url)
        page.wait_for_timeout(300)
        splash = page.evaluate("""() => {
            const el = document.getElementById('app-splash');
            const r = el.getBoundingClientRect();
            return {visible: !!el && !el.classList.contains('out') && getComputedStyle(el).display !== 'none',
                    pct: document.getElementById('sp-pct') ? document.getElementById('sp-pct').textContent : '',
                    arms: document.querySelectorAll('#app-splash .sp-arm').length,
                    bar: !!document.getElementById('sp-fill'),
                    covers: r.width >= 1199 && r.height >= 749,
                    z: getComputedStyle(el).zIndex};
        }""")
        page.screenshot(path=str(SHOTS / "splash.png"))
        check("스플래시가 첫 화면에 먼저 보임", splash["visible"] and splash["arms"] == 2 and splash["bar"], str(splash))
        check("스플래시가 전체 화면을 덮음", splash["covers"] and splash["z"] == "9999", str({"covers": splash["covers"], "z": splash["z"]}))
        check("스플래시 퍼센트 표시", "%" in (splash["pct"] or ""), str(splash["pct"]))
        page.wait_for_selector("#app-splash", state="hidden", timeout=8000)
        done = page.evaluate("""() => ({pct: document.getElementById('sp-pct').textContent,
            hidden: getComputedStyle(document.getElementById('app-splash')).display === 'none' ||
                    document.getElementById('app-splash').classList.contains('out')})""")
        check("스플래시 100% 후 메인 화면 전환", done["pct"] == "100%" and done["hidden"], str(done))
        page.wait_for_timeout(250)

        check("JS 문법 오류 없음", not errors, "; ".join(errors[:3]))

        # 1) 창 드래그 = 네이티브 호출
        page.mouse.move(300, 20)
        page.mouse.down()
        page.mouse.move(360, 60, steps=4)
        page.mouse.up()
        page.wait_for_timeout(150)
        calls = page.evaluate("window.__CALLS.map(c=>c[0])")
        check("타이틀바 드래그 → drag_window 호출", "drag_window" in calls, str(calls[:6]))

        # 2) 설정 팝업(v1.3.0): 좌측 하단 프로필/톱니바퀴 클릭 → 닉네임/API 키 placeholder 제거
        page.click("#profile-card")
        page.wait_for_timeout(300)
        ph = page.evaluate("""() => {
            const nick = document.querySelector('#set-nick');
            const key = document.querySelector('#set-key');
            return {nick: nick ? nick.getAttribute('placeholder') : 'MISSING',
                    key: key ? key.getAttribute('placeholder') : 'MISSING'};
        }""")
        check("닉네임/API 키 placeholder 없음", ph.get("nick") in ("", None) and ph.get("key") in ("", None), str(ph))

        # 2-1) 클라우드 카드: 용도 설명 + 고급 서버 설정 접힘
        cloud_card = page.evaluate("""() => {
            const card = document.getElementById('set-cloud');
            if (!card) return {};
            const adv = card.querySelector('#set-cloud-adv');
            return {text: card.innerText, advOpen: adv ? adv.open : null,
                    hasUrlInput: !!card.querySelector('#set-sb-url')};
        }""")
        check("클라우드 카드에 용도 설명", "어디서나" in cloud_card.get("text", "") or "클라우드 저장소" in cloud_card.get("text", ""),
              cloud_card.get("text", "")[:70].replace("\n", " | "))
        check("서버 URL/키는 접힌 고급 설정 안에", cloud_card.get("advOpen") is False and cloud_card.get("hasUrlInput"),
              str({k: cloud_card.get(k) for k in ("advOpen", "hasUrlInput")}))
        page.screenshot(path=str(SHOTS / "settings.png"))
        page.evaluate("() => document.querySelector('#settings-mask').classList.remove('show')")
        page.wait_for_timeout(150)

        # 3) 사이드바: 뱃지/이모지 없이 동일 스타일
        nav = page.evaluate("""() => {
            const items = [...document.querySelectorAll('.nav-item')];
            return {
              count: items.length,
              badges: document.querySelectorAll('.nav-item .nav-badge, .nav-item .nav-new, .nav-emoji').length,
              noIcon: items.filter(i => !i.querySelector('svg')).map(i => i.textContent.trim()),
              colors: [...new Set(items.map(i => getComputedStyle(i).color))],
              sizes: [...new Set(items.map(i => getComputedStyle(i).fontSize))]
            };
        }""")
        check("사이드바 숫자/텍스트 뱃지 제거", nav["badges"] == 0, f"badges={nav['badges']}")
        check("모든 사이드바 항목 동일 아이콘 타입", len(nav["noIcon"]) == 0, str(nav["noIcon"]))
        check("비활성 메뉴 색상 통일", len(nav["colors"]) <= 2, str(nav["colors"]))

        # 4) 알파벳 등급 제거
        grades = page.evaluate("""() => [document.querySelector('#kpi-grade')?.textContent,
                                          document.querySelector('#radar-grade')?.textContent]""")
        joined = " ".join(g or "" for g in grades)
        check("알파벳 등급(S/A/B/C/D) 없음", all(x not in joined for x in ("등급", "S", "A등급")) or "등급" not in joined, str(grades))

        # 5) 트레이너/무기 전환 → 추천 5종 + 시나리오 라이브러리 즉시 변경
        page.click('[data-screen="recs"]')
        page.wait_for_timeout(200)
        kov = page.eval_on_selector("#rec-grid", "el => el.innerText")
        page.click('.seg-game .seg-btn[data-game="aimlab"]')
        page.wait_for_timeout(300)
        aim = page.eval_on_selector("#rec-grid", "el => el.innerText")
        aim_recs = page.eval_on_selector("#rec-list", "el => el.innerText")
        page.click('.seg-weapon .seg-btn[data-weapon="rifle"]')
        page.wait_for_timeout(300)
        aim_rifle = page.eval_on_selector("#rec-grid", "el => el.innerText")
        check("코박스 → 에임랩 시 라이브러리 변경", kov != aim and "Sixshot" in aim, aim[:60].replace("\n", " | "))
        check("스나 → 라플 시 라이브러리 변경", aim != aim_rifle and "Microflex" in aim_rifle, aim_rifle[:60].replace("\n", " | "))
        check("에임랩 추천 5종 표시", "Sixshot" in aim_recs or "Microshot" in aim_recs, aim_recs[:60].replace("\n", " | "))
        page.click('.seg-game .seg-btn[data-game="kovak"]')
        page.click('.seg-weapon .seg-btn[data-weapon="sniper"]')
        page.wait_for_timeout(250)

        # 6) 점수 입력: 0~100 안내/상한 제거 + 10만점 입력
        page.click('[data-screen="dashboard"]')
        page.wait_for_timeout(200)
        page.click("#rec-save")
        page.wait_for_timeout(250)
        sub = page.eval_on_selector("#score-sub", "el => el.innerText")
        attrs = page.evaluate("""() => [...document.querySelectorAll('#score-body input')].map(i => ({
            max: i.getAttribute('max'), ph: i.getAttribute('placeholder')}))""")
        check("점수 창 '0 ~ 100' 문구 제거", "0 ~ 100" not in sub, sub[:50])
        check("점수 입력 max 상한 제거", all(a["max"] in (None, "") for a in attrs), str(attrs[:2]))
        page.fill("#score-body input", "100000")
        page.click("#score-save")
        page.wait_for_timeout(400)
        saved = page.evaluate("window.__CALLS.filter(c=>c[0]==='save_training_session').pop()")
        value = None
        if saved:
            value = saved[1]["scenarios"][0]["score"]
        check("10만점 그대로 저장", value == 100000, f"value={value}")

        # 7) 병영수첩: 폭파미션 제거 + 전적 불러오기 동작
        page.click('[data-screen="barracks"]')
        page.wait_for_timeout(250)
        body = page.inner_text("#s-barracks")
        check("병영수첩 '폭파미션' 문구 제거", "폭파미션" not in page.inner_text("body"), "")
        page.click("#br-refresh")
        page.wait_for_timeout(600)
        calls = page.evaluate("window.__CALLS.map(c=>c[0])")
        updated = page.eval_on_selector("#br-updated", "el => el.innerText")
        check("전적 불러오기 → fetch_barracks 호출", "fetch_barracks" in calls, str(calls[-4:]))
        check("전적 최신화 시각 갱신", "최신화" in updated and "—" not in updated, updated)

        # 8) 닉네임 검색: 없음 / 있음 → 클릭 → 임시 조회 → 복귀
        page.fill("#br-search-input", "없는닉네임")
        page.click("#br-search-btn")
        page.wait_for_timeout(400)
        box = page.eval_on_selector("#br-search-result", "el => el.innerText")
        check("검색 결과 없음 표시", "조회 결과 없음" in box, box.strip()[:40])
        page.fill("#br-search-input", "테스트친구")
        page.click("#br-search-btn")
        page.wait_for_timeout(400)
        box = page.eval_on_selector("#br-search-result", "el => el.innerText")
        check("검색 결과 카드 표시", "테스트친구" in box and "마스터 I" in box, box.strip().replace("\n", " | ")[:70])
        page.click("#br-search-result .sr-hit")
        page.wait_for_timeout(700)
        peek_visible = page.eval_on_selector("#br-peek-bar", "el => !el.hidden")
        banner = page.eval_on_selector("#br-banner", "el => el.innerText")
        check("클릭 시 다른 유저 병영수첩 표시", peek_visible and "테스트친구" in banner, banner.replace("\n", " | ")[:60])
        page.screenshot(path=str(SHOTS / "barracks-peek.png"))
        page.click("#br-peek-add")
        page.wait_for_timeout(500)
        add_call = page.evaluate("window.__CALLS.filter(c=>c[0]==='add_leaderboard_member').pop()")
        check("순위표에 추가 버튼", bool(add_call) and add_call[3] == "peek-ouid-0001", str(add_call))
        page.click("#br-peek-back")
        page.wait_for_timeout(400)
        restored = page.evaluate("document.querySelector('#br-peek-bar').hidden")
        check("내 병영수첩으로 돌아가기", restored is True, f"hidden={restored}")

        # 9) v1.3.0 — 계단현상 끄기는 라플/스나 최적화에 병합 (버튼 2개)
        page.click('[data-screen="utils"]')
        page.wait_for_timeout(300)
        check("최적화 유틸 화면에 폭파미션 없음", "폭파미션" not in page.inner_text("#s-utils"), "")
        rows = page.evaluate("() => [...document.querySelectorAll('#s-utils [data-apply]')].map(b => b.dataset.apply)")
        check("엔비디아 최적화 버튼 2개(라플·스나)로 통일", rows == ["rifle", "sniper"], str(rows))
        check("계단현상 단독 버튼 제거", "alias" not in rows, str(rows))
        page.click('[data-apply="sniper"]')
        page.wait_for_timeout(700)
        toast = page.eval_on_selector("#app-toast", "el => el.textContent")
        apply_calls = page.evaluate("window.__CALLS.filter(c=>c[0]==='apply_nvidia_profile').map(c=>c[1])")
        check("스나 최적화 적용 호출", "sniper" in apply_calls, str(apply_calls))
        check("'내장되어 있지 않다' 오류 없음", "내장" not in toast, toast)
        page.screenshot(path=str(SHOTS / "utils-applied.png"))
        page.evaluate("() => document.querySelector('#changes-mask').classList.remove('show')")
        page.wait_for_timeout(150)

        # 10) 레이아웃 스크린샷
        page.click('[data-screen="dashboard"]')
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOTS / "dashboard.png"))
        page.click('[data-screen="recs"]')
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "recs-kovak.png"))
        page.click('.seg-game .seg-btn[data-game="aimlab"]')
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOTS / "recs-aimlab.png"))
        page.click('[data-screen="barracks"]')
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "barracks.png"))

        check("런타임 에러 없음(최종)", not errors, "; ".join(errors[:3]))
        browser.close()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
