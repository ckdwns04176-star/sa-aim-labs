"""v1.1.0 checks — 통합 에임 프로필 · 에임랩 확충 · 추천 UX · 스크롤 · 순위표 · 창 고정.

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \
    python3 tests/v110_test.py

Two halves:

  · backend  — 통합 점수(코박스+에임랩+스나+라플), 무기별 추천 철학, 에임랩 라이브러리
               규모, 오늘 점수/수정 저장, `+ 더 연습하기` 저장, 창 크기 고정 계산
  · UI       — Playwright(Chromium) 로 실제 화면을 띄워 레이더 상단 잘림 · 추천 점수
               표시 · 시나리오 모달 · 더 연습하기 · 병영수첩 하단 스크롤 · 순위표
               솔로/파티 티어 통일을 눈으로 확인하고 tests/shots 에 남긴다.
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
sys.path.insert(0, str(ROOT / "tests"))

SHOTS = ROOT / "tests" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""))


# --------------------------------------------------------------------- state
def build_state() -> dict:
    """코박스+에임랩 · 스나+라플 기록이 섞인 실제 payload + 병영수첩/순위표."""
    tmp = Path(tempfile.mkdtemp(prefix="sa_v110_"))
    os.environ["SA_AL_DATA_DIR"] = str(tmp)
    from app.store import Store
    from app.api import Api

    store = Store(tmp / "data.json")
    store.update_settings({"nickname": "휴가중.", "api_key": "test_9542ea5ac663d2c0"})
    today = dt.date.today()
    yday = today - dt.timedelta(days=1)

    # 어제: 코박스(스나) 5영역
    store.add_record({"date": yday.isoformat(), "created_at": yday.isoformat() + "T21:10:00+09:00",
                      "game": "kovak", "weapon": "sniper",
                      "scores": {"precision": 128, "wide": 110, "micro": 88, "track": 92, "switch": 120},
                      "scenarios": [{"name": "VT 1w6ts Rasp Novice", "area": "precision", "score": 128}], "note": ""})
    # 오늘: 에임랩(라플) + 코박스(스나) — 통합 점수 검증용
    store.add_record({"date": today.isoformat(), "created_at": today.isoformat() + "T19:05:00+09:00",
                      "game": "aimlab", "weapon": "rifle",
                      "scores": {"precision": 92500, "wide": 81000, "micro": 64000, "track": 55000, "switch": 73000},
                      "scenarios": [{"name": "Gridshot Ultimate", "area": "wide", "score": 92500}], "note": ""})
    store.add_record({"date": today.isoformat(), "created_at": today.isoformat() + "T19:20:00+09:00",
                      "game": "kovak", "weapon": "sniper",
                      "scores": {"precision": 152, "wide": 128, "micro": 96, "track": 88, "switch": 141},
                      "scenarios": [{"name": "VT 1w6ts Rasp Novice", "area": "precision", "score": 152}], "note": ""})

    barracks = {
        "nickname": "휴가중.", "ouid": "711c04a69769cc4b6b782ea2aa3eb614",
        "basic": {"clan": "리드", "created": "2025.11.29", "manner": "3"},
        "tier": {"solo": {"name": "그랜드마스터 II", "rp": 3261},
                 "party": {"name": "그랜드마스터 III", "rp": 3435}},
        "stats": {"games": 100, "kills": 903, "deaths": 812, "wins": 47, "win": 47.0, "kd": 51.5, "dmg": 1009},
        "recent": {"sniper": 62, "assault": 38},
        "maps": [{"rk": 1, "map": "이탈리아-폭파", "games": 6, "win": 33.3, "kd": 43.5, "dmg": 836.8, "weak": 61.9},
                 {"rk": 2, "map": "프로방스(심플)", "games": 12, "win": 25.0, "kd": 51.0, "dmg": 1177.8, "weak": 60.4},
                 {"rk": 3, "map": "크로스포트(심플)", "games": 5, "win": 40.0, "kd": 41.2, "dmg": 873.8, "weak": 58.9},
                 {"rk": 4, "map": "시티캣-심플", "games": 10, "win": 40.0, "kd": 50.5, "dmg": 995.1, "weak": 54.9},
                 {"rk": 5, "map": "올드타운(심플)", "games": 8, "win": 37.5, "kd": 56.1, "dmg": 1071.9, "weak": 53.7},
                 {"rk": 6, "map": "팰리스-심플", "games": 10, "win": 50.0, "kd": 48.8, "dmg": 857.2, "weak": 51.8},
                 {"rk": 7, "map": "지하철-심플", "games": 9, "win": 55.6, "kd": 55.2, "dmg": 951.2, "weak": 46.1},
                 {"rk": 8, "map": "제5보급-심플", "games": 11, "win": 63.6, "kd": 49.5, "dmg": 765.9, "weak": 45.7},
                 {"rk": 9, "map": "드래곤로드(심플)", "games": 11, "win": 54.5, "kd": 51.3, "dmg": 1295.8, "weak": 44.4},
                 {"rk": 10, "map": "듀오 릭", "games": 7, "win": 57.1, "kd": 58.2, "dmg": 984.4, "weak": 44.1}],
        "matches": [{"date": (today - dt.timedelta(days=i % 5)).isoformat(), "map": "이탈리아-폭파",
                     "mode": "랭크전 솔로", "win": i % 3 != 0,
                     "kills": 10 + i % 7, "deaths": 8 + i % 4, "kd": round(44 + (i % 9) * 1.8, 1),
                     "damage": 780 + (i % 11) * 47} for i in range(20)],
        "updated_at": today.isoformat() + "T18:30:00+09:00",
    }
    members = [
        {"name": "휴가중.", "me": True, "clan": "리드", "weapon": "sniper", "solo": 3261, "party": 3435,
         "solo_tier": "그랜드마스터 II", "party_tier": "그랜드마스터 III", "kd": 51.5, "dmg": 1009, "win": 47.0, "ouid": barracks["ouid"]},
        {"name": "무빙샷장인", "clan": "리드", "weapon": "rifle", "solo": 2940, "party": 3211, "kd": 57.4, "dmg": 1292, "win": 56.0},
        {"name": "헤더온리", "clan": "리드", "weapon": "sniper", "solo": 2715, "party": 2788, "kd": 52.1, "dmg": 988, "win": 48.6},
        {"name": "연사금지", "clan": "친구", "weapon": "rifle", "solo": 2486, "party": 2605, "kd": 58.2, "dmg": 1258, "win": 57.3},
        {"name": "어깨피기", "clan": "친구", "weapon": "sniper", "solo": 2310, "party": 2445, "kd": 46.9, "dmg": 902, "win": 44.8},
        {"name": "폭파반장", "clan": "친구", "weapon": "rifle", "solo": 2164, "party": 2180, "kd": 50.5, "dmg": 1108, "win": 49.2},
        {"name": "샤프슈터", "clan": "친구", "weapon": "sniper", "solo": 1882, "party": 1970, "kd": 53.6, "dmg": 1024, "win": 51.5},
        {"name": "배치중", "clan": "친구", "weapon": "sniper", "solo": 0, "party": None, "kd": None, "dmg": None, "win": None},
    ]
    store.set_leaderboard(members)
    api = Api(store=store)
    state = api._build_state()
    state["barracks"] = barracks  # 실 API 없이 화면 검수용
    state["leaderboard"] = members
    return state


# --------------------------------------------------------------------- mock bridge
def mock_js(state: dict) -> str:
    return r"""
    window.__CALLS = [];
    window.__PY_STATE = %s;
    const S = () => JSON.parse(JSON.stringify(window.__PY_STATE));
    const clone = (v) => JSON.parse(JSON.stringify(v));
    const key = (g, w) => `${g}-${w}`;
    window.pywebview = {api: {
      async bootstrap() { return {ok: true, state: S()}; },
      async get_state() { return {ok: true, state: S()}; },
      async bridge_ping() { return {ok: true, pong: true}; },
      async get_nvidia_state() { return {ok: true, utils: {supported: true, mode: null, alias: false, backups: [],
        points: [], modes: {}, missing: [], accel: false, pending: false,
        stairfix: {ready: true, applied: false, value: null, label: '기본값', target: '0x00401241'}}, state: S()}; },
      async save_settings(patch) { window.__CALLS.push(['save_settings', patch]); return {ok: true, state: S()}; },
      async drag_window() { return {ok: true, supported: true}; },
      async minimize_window() { return {ok: true}; },
      async toggle_maximize() { return {ok: true, maximized: false}; },
      async close_window() { return {ok: true}; },
      async launch_trainer(url) { window.__CALLS.push(['launch_trainer', url]); return {ok: true, url}; },
      async save_training_session(payload) {
        window.__CALLS.push(['save_training_session', payload]);
        const st = window.__PY_STATE;                 /* 실제 백엔드처럼 상태를 갱신한다 */
        const k = key(payload.game, payload.weapon);
        const one = (payload.scenarios || [])[0] || {};
        const rowsOf = (st.summaries[k] || {});
        [].concat(rowsOf.recs || [], rowsOf.more || []).forEach((r) => {
          if (r.name === one.name) { r.today_score = one.score; r.done = true; r.score = one.score; }
        });
        if (st.profile) { st.profile.today_scenarios = (st.profile.today_scenarios || 0) + 1; }
        st.today_scores = st.today_scores || {};
        st.today_scores[one.name] = one.score;
        return {ok: true, state: S(), record: {id: 'x'}};
      },
      async save_training(payload) { return this.save_training_session(payload); },
      async recommend_more(game, weapon, count) {
        window.__CALLS.push(['recommend_more', game, weapon, count]);
        const st = window.__PY_STATE;
        const k = key(game, weapon);
        const s = st.summaries[k] || (st.summaries[k] = {});
        const have = new Set([].concat(s.recs || [], s.more || []).map((r) => r.name));
        const pool = [
          {name: 'Circletrack Ultimate', area: 3, area_key: 'track', area_name: '트래킹·무빙'},
          {name: 'Microshot Speed', area: 2, area_key: 'micro', area_name: '마이크로 보정'},
          {name: 'Spidershot 180 Ultimate', area: 4, area_key: 'switch', area_name: '타겟 스위칭'},
          {name: 'Strafetrack Speed', area: 3, area_key: 'track', area_name: '트래킹·무빙'}
        ].filter((r) => !have.has(r.name)).slice(0, count)
          .map((r) => ({...r, diff: '중급', score: null, best: null, today_score: null, done: false, delta: null, extra: true}));
        s.more = [].concat(s.more || [], pool);
        return {ok: true, recs: pool, state: S(), message: `약점 보완 ${pool.length}종을 추가했습니다`};
      },
      async fetch_barracks() { window.__CALLS.push(['fetch_barracks']); return {ok: true, state: S(), message: '최신화 완료'}; },
      async fetch_leaderboard() { window.__CALLS.push(['fetch_leaderboard']); return {ok: true, members: [], message: '갱신 완료', state: S()}; },
      async add_leaderboard_member(n) { return {ok: true, state: S(), message: '추가'}; },
      async remove_leaderboard_member() { return {ok: true, state: S(), message: '삭제'}; },
      async regenerate_recs(g, w) { return {ok: true, recs: [], state: S()}; },
      async save_settings() { return {ok: true, state: S()}; },
      async cloud_status() { return {ok: true, cloud: {}, state: S()}; }
    }};
    """ % json.dumps(state, ensure_ascii=False)


# --------------------------------------------------------------------- backend
def backend() -> None:
    from app import scoring, winutil, paths

    check("버전 1.3.1", paths.APP_VERSION == "1.3.1", paths.APP_VERSION)

    records = [
        {"date": "2026-11-22", "created_at": "2026-11-22T21:00:00", "game": "kovak", "weapon": "sniper",
         "scores": {"precision": 200, "wide": 200, "micro": 200, "track": 200, "switch": 200}},
        {"date": "2026-11-23", "created_at": "2026-11-23T19:00:00", "game": "aimlab", "weapon": "rifle",
         "scores": {"precision": 100000, "wide": 100000, "micro": 100000, "track": 100000, "switch": 100000}},
    ]
    values = scoring.profile_values(records)
    check("통합 프로필 · 코박스+에임랩 같은 축으로 합산", all(v == 100.0 for v in values.values()), str(values))
    check("통합 점수 = 5영역 평균 × 36", scoring.overall_score(values) == 3600, str(scoring.overall_score(values)))
    only_sniper = scoring.summary(records, "kovak", "sniper")
    only_aim = scoring.summary(records, "aimlab", "rifle")
    check("트레이너/무기 선택과 무관하게 같은 종합 점수",
          only_sniper["score"] == only_aim["score"] == 3600, f'{only_sniper["score"]} / {only_aim["score"]}')
    check("누적·연속 훈련일도 통합", only_aim["days"] == 2 and only_aim["streak"]["current"] == 2,
          f'days={only_aim["days"]} streak={only_aim["streak"]}')

    lib = scoring.scenario_library()
    check("에임랩 스나 라이브러리 24종 이상", lib["aimlab-sniper"] >= 24, str(lib["aimlab-sniper"]))
    check("에임랩 라플 라이브러리 24종 이상", lib["aimlab-rifle"] >= 24, str(lib["aimlab-rifle"]))
    names = [s["name"] for s in scoring.scenarios_for("aimlab", "rifle")]
    for must in ("Gridshot Ultimate", "Sixshot Ultimate", "Microshot Precision", "Spidershot Speed",
                 "Spidershot Precision", "Headshot Precision", "Motionshot Speed", "Strafetrack Ultimate",
                 "Circletrack Ultimate", "Switchtrack Ultimate", "Linetrace Ultimate", "Reflexshot Precision",
                 "Multishot Ultimate", "Blinkshot Ultimate"):
        check(f"에임랩 시나리오 포함 · {must}", must in names, "")

    weak = {"precision": 40, "wide": 50, "micro": 20, "track": 10, "switch": 30}
    sniper = scoring.build_recs(values=weak, records=[], game="kovak", weapon="sniper")
    rifle = scoring.build_recs(values=weak, records=[], game="kovak", weapon="rifle")
    check("스나 추천 5종 · 롱 트래킹 제외", len(sniper) == 5 and all(r["area_key"] != "track" for r in sniper),
          str([r["area_key"] for r in sniper]))
    check("스나 추천은 가장 약한 영역부터", sniper[0]["area_key"] in ("wide", "precision", "switch", "micro"),
          str([(r["area_key"], weak[r["area_key"]]) for r in sniper]))
    check("라플 추천 5종 · 트래킹 2칸", len(rifle) == 5 and sum(1 for r in rifle if r["area_key"] == "track") == 2,
          str([r["area_key"] for r in rifle]))
    check("라플 추천 첫 칸 = 가장 약한 트래킹", rifle[0]["area_key"] == "track", str(rifle[0]["area_key"]))

    # + 더 연습하기: 오늘 5종과 겹치지 않는다
    more = scoring.build_more(values=weak, records=[], game="aimlab", weapon="rifle",
                              exclude=[r["name"] for r in rifle], count=3)
    check("더 연습하기 3종 · 오늘 목록과 중복 없음",
          len(more) == 3 and not ({r["name"] for r in more} & {r["name"] for r in rifle}),
          str([r["name"] for r in more]))

    # 오늘 점수 표시 + 수정 저장
    tmp = Path(tempfile.mkdtemp(prefix="sa_v110_api_"))
    os.environ["SA_AL_DATA_DIR"] = str(tmp)
    from app.store import Store
    from app.api import Api

    store = Store(tmp / "data.json")
    store.update_settings({"nickname": "휴가중.", "api_key": "test_x"})
    api = Api(store=store)
    state = api._build_state()
    today = dt.date.today().isoformat()
    first = state["summaries"]["kovak-sniper"]["recs"][0]
    saved = api.save_training({"game": "kovak", "weapon": "sniper", "date": today,
                               "scenarios": [{"name": first["name"], "area_key": first["area_key"], "score": 187}]})
    rec = next(r for r in saved["state"]["summaries"]["kovak-sniper"]["recs"] if r["name"] == first["name"])
    check("저장 후 추천 행에 오늘 점수 표시", rec["today_score"] == 187 and rec["done"] is True,
          json.dumps(rec, ensure_ascii=False)[:120])
    check("오늘 목록은 저장해도 그대로", [r["name"] for r in saved["state"]["summaries"]["kovak-sniper"]["recs"]]
          == [r["name"] for r in state["summaries"]["kovak-sniper"]["recs"]], "")
    again = api.save_training({"game": "kovak", "weapon": "sniper", "date": today,
                               "scenarios": [{"name": first["name"], "area_key": first["area_key"], "score": 199}]})
    rec2 = next(r for r in again["state"]["summaries"]["kovak-sniper"]["recs"] if r["name"] == first["name"])
    check("같은 시나리오 재입력 = 수정(중복 기록 없음)",
          rec2["today_score"] == 199 and len(api.store.records()) == 1,
          f'records={len(api.store.records())} score={rec2["today_score"]}')

    extra = api.recommend_more("kovak", "sniper", 3)
    check("더 연습하기 API · 3종 추가", extra["ok"] and len(extra["recs"]) == 3,
          str([r["name"] for r in extra.get("recs", [])]))
    check("더 연습하기 목록이 state 에 남음", len(extra["state"]["summaries"]["kovak-sniper"]["more"]) == 3,
          "")
    reloaded = Api(store=Store(tmp / "data.json"))._build_state()
    check("앱을 다시 켜도 더 연습하기 유지", len(reloaded["summaries"]["kovak-sniper"]["more"]) == 3, "")
    check("더 연습하기 저장 즉시 통합 점수 반영", extra["state"]["profile"]["today_scenarios"] >= 1,
          str(extra["state"]["profile"]["today_scenarios"]))

    # 창 크기 고정
    plan = winutil.window_fix_plan((0, 0, 1600, 900), work=(0, 0, 1920, 1040))
    check("창 크기 고정 · 1200×750 복원", plan["resize"] and plan["width"] == 1200 and plan["height"] == 750,
          str(plan))
    keep = winutil.window_fix_plan((40, 40, 1200, 750), work=(0, 0, 1920, 1040))
    check("정상 크기면 건드리지 않음", not keep["resize"] and not keep["moved"], str(keep))
    # v1.3.0 — 감시자는 크기만 고치고 위치는 절대 옮기지 않는다(드래그·멀티모니터 이동 자유).
    off = winutil.window_fix_plan((300, 900, 1200, 750), work=(0, 0, 1920, 1040))
    check("창 위치는 그대로 — 드래그·화면 밖 이동을 막지 않음",
          off["y"] == 900 and off["x"] == 300 and not off["moved"] and not off["resize"], str(off))
    small = winutil.window_fix_plan((0, 0, 1200, 750), work=(0, 0, 1366, 728))
    check("작업 영역보다 큰 화면에서는 크기를 줄임", small["height"] == 728, str(small))


# --------------------------------------------------------------------- UI
def ui() -> None:
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
        page.wait_for_selector("#app-splash", state="hidden", timeout=8000)
        page.wait_for_timeout(350)

        # 1) 레이더 상단 라벨이 잘리지 않는가 (SVG viewBox 안에 들어오는지)
        radar = page.evaluate("""() => {
            const svg = document.querySelector('#radar');
            const vb = svg.viewBox.baseVal;
            const texts = [...svg.querySelectorAll('text')].map((t) => {
                const b = t.getBBox();
                return {t: t.textContent, x: +b.x.toFixed(1), y: +b.y.toFixed(1),
                        r: +(b.x + b.width).toFixed(1), b: +(b.y + b.height).toFixed(1)};
            });
            const out = texts.filter((t) => t.y < 0 || t.x < 0 || t.r > vb.width + 0.5 || t.b > vb.height + 0.5);
            const top = texts.find((t) => t.t === '정밀 플릭') || null;
            return {vw: vb.width, vh: vb.height, out, top, count: texts.length};
        }""")
        check("레이더 라벨이 viewBox 안에 있음(잘림 없음)", not radar["out"], str(radar["out"])[:160])
        check("레이더 상단 라벨 y ≥ 0", radar["top"] is not None and radar["top"]["y"] >= 0,
              json.dumps(radar["top"], ensure_ascii=False))
        page.screenshot(path=str(SHOTS / "v110-dashboard.png"))

        # 2) 추천 5종 · 오늘 점수 표시 (미기록 / 숫자)
        rows = page.evaluate("""() => [...document.querySelectorAll('#rec-list .rec')].map((r) => ({
            name: r.dataset.name, idx: r.querySelector('.rec-idx').textContent.trim(),
            today: r.querySelector('.rec-today') ? r.querySelector('.rec-today').textContent.trim() : '',
            none: !!r.querySelector('.rec-today.none')}))""")
        check("추천 5종 표시", len(rows) == 5, str(len(rows)))
        check("오늘 점수 열이 모든 행에 있음", all(r["today"] for r in rows), str([r["today"] for r in rows]))
        check("미기록은 '미기록' 으로 표시", sum(1 for r in rows if r["none"]) >= 3,
              str([r["today"] for r in rows]))

        # 3) 시나리오 클릭 → 모달 (점수 입력 + 게임에서 재생)
        page.click("#rec-list .rec:nth-child(1) .rec-name")
        page.wait_for_timeout(300)
        modal = page.evaluate("""() => ({
            shown: document.querySelector('#scen-mask').classList.contains('show'),
            title: document.querySelector('#scen-title').textContent,
            today: document.querySelector('#scen-today').textContent,
            hasInput: !!document.querySelector('#scen-score'),
            hasPlay: !!document.querySelector('#scen-play'),
            hasSave: !!document.querySelector('#scen-save'),
            area: document.querySelector('#scen-area').textContent})""")
        check("시나리오 클릭 → 모달 열림", modal["shown"], "")
        check("모달에 점수 입력 + 게임에서 재생 + 저장", modal["hasInput"] and modal["hasPlay"] and modal["hasSave"],
              str({k: modal[k] for k in ("hasInput", "hasPlay", "hasSave")}))
        check("모달에 내 오늘 점수 표시", modal["today"] == "미기록", modal["today"])
        page.fill("#scen-score", "182")
        page.screenshot(path=str(SHOTS / "v110-scenario-modal.png"))
        page.click("#scen-save")
        page.wait_for_timeout(600)
        saved = page.evaluate("window.__CALLS.filter(c=>c[0]==='save_training_session').pop()")
        check("모달 저장 → 단일 시나리오 저장 호출", bool(saved) and saved[1]["scenarios"][0]["score"] == 182,
              str(saved)[:150])
        check("모달 저장 후 닫힘", page.evaluate("!document.querySelector('#scen-mask').classList.contains('show')"), "")
        first_today = page.evaluate("""() => {
            const r = document.querySelector('#rec-list .rec');
            return r.querySelector('.rec-today').textContent.trim();
        }""")
        check("저장 후 그 행에 오늘 점수 표시", "182" in first_today, first_today)
        check("저장 후 통합 점수 토스트", "통합" in page.eval_on_selector("#app-toast", "el => el.textContent"), "")

        # 3-1) 실행 버튼은 모달이 아니라 게임 실행
        page.click("#rec-list .rec:nth-child(2) [data-run]")
        page.wait_for_timeout(300)
        run_calls = page.evaluate("window.__CALLS.filter(c=>c[0]==='launch_trainer').map(c=>c[1])")
        check("실행 버튼 → 게임 실행(모달 아님)",
              bool(run_calls) and "steam://run/824270" in run_calls[-1],
              str(run_calls[-1:]))
        check("실행 버튼 클릭 시 모달이 열리지 않음",
              page.evaluate("!document.querySelector('#scen-mask').classList.contains('show')"), "")

        # 4) + 더 연습하기
        page.click("#rec-more")
        page.wait_for_timeout(700)
        more = page.evaluate("""() => ({
            rows: document.querySelectorAll('#rec-list .rec').length,
            sep: !!document.querySelector('#rec-list .rec-sep'),
            names: [...document.querySelectorAll('#rec-list .rec')].map((r) => r.dataset.name),
            call: (window.__CALLS.filter((c) => c[0] === 'recommend_more').pop() || [])})""")
        check("더 연습하기 → recommend_more 호출", bool(more["call"]), str(more["call"]))
        check("더 연습하기 → 목록에 추가", more["rows"] == 8 and more["sep"], f'rows={more["rows"]}')
        check("더 연습하기는 가장 약한 영역 위주", any(n in ("Circletrack Ultimate", "Microshot Speed") for n in more["names"]),
              str(more["names"]))
        after = page.evaluate("""() => ({
            first: document.querySelector('#rec-list .rec .rec-today').textContent.trim(),
            done: (document.getElementById('chip-today') || {}).textContent})""")
        check("더 연습하기 후에도 오늘 점수 유지", "182" in after["first"], str(after))
        check("오늘 완료 카운트 반영", str(after["done"]).strip() == "1", str(after["done"]))
        page.screenshot(path=str(SHOTS / "v110-more-practice.png"))

        # 5-1) 오늘의 추천 5종 화면 — 에임랩 라이브러리 확장 + 무기 철학
        page.click('[data-screen="recs"]')
        page.wait_for_timeout(300)
        lib_row = page.evaluate("""() => {
            const item = [...document.querySelectorAll('#rec-grid [data-scen]')]
                .find((e) => e.dataset.scen === 'Reflex Micro++ flick reload small');
            return item ? item.parentElement.textContent.replace(/\\s+/g, ' ').trim() : '';
        }""")
        check("라이브러리 행에 PB 표시 (v1.3.1 — 오늘 글자 대신 PB)", "PB" in lib_row, lib_row[:70])
        page.click('.seg-game .seg-btn[data-game="aimlab"]')
        page.wait_for_timeout(450)
        aim_sub = page.evaluate("""() => ({sub: (document.querySelector('#hd2-count')||{}).textContent || '',
            head: document.querySelector('#hd2-count').textContent,
            chip: (document.querySelector('#rec-head-chip')||{}).textContent || '',
            items: document.querySelectorAll('#rec-grid [data-scen]').length,
            noSub: !document.getElementById('rec-grid-sub')})""")
        check("추천 화면 · 에임랩 스나 라이브러리 24종 이상", aim_sub["items"] >= 24, str(aim_sub))
        check("추천 화면 · 스나 철학 표시", "롱 트래킹 제외" in aim_sub["chip"], aim_sub["chip"])
        check("추천 화면 · 회색 부연 설명 제거 (v1.3.1)", aim_sub.get("noSub") is True, str(aim_sub.get("noSub")))
        page.screenshot(path=str(SHOTS / "v110-recs-aimlab-sniper.png"))
        page.click('.seg-weapon .seg-btn[data-weapon="rifle"]')
        page.wait_for_timeout(450)
        rifle = page.evaluate("""() => ({sub: (document.querySelector('#hd2-count')||{}).textContent || '',
            head: document.querySelector('#hd2-count').textContent,
            chip: (document.querySelector('#rec-head-chip')||{}).textContent || '',
            items: document.querySelectorAll('#rec-grid [data-scen]').length,
            noSub: !document.getElementById('rec-grid-sub')})""")
        check("추천 화면 · 라플 24종 이상 + 트래킹 위주", rifle["items"] >= 24 and "트래킹" in rifle["chip"],
              str(rifle))
        check("헤더 종목 수가 실제 라이브러리와 일치", "27" in rifle["head"] and "25" in aim_sub["head"],
              f'{aim_sub["head"]} / {rifle["head"]}')
        page.screenshot(path=str(SHOTS / "v110-recs-aimlab-rifle.png"))
        page.click('.seg-game .seg-btn[data-game="kovak"]')
        page.click('.seg-weapon .seg-btn[data-weapon="sniper"]')
        page.wait_for_timeout(300)

        # 5) 병영수첩 · 맵·약점 분석 화면 분리 (v1.3.0)
        page.click('[data-screen="barracks"]')
        page.wait_for_timeout(500)
        for height in (750, 640):
            page.set_viewport_size({"width": 1200, "height": height})
            page.wait_for_timeout(350)
            scroll = page.evaluate("""() => {
                const s = document.querySelector('#s-barracks');
                return {sh: s.scrollHeight, ch: s.clientHeight, can: getComputedStyle(s).overflowY,
                        sbWidth: s.offsetWidth - s.clientWidth};
            }""")
            check(f"{height}px 창 · 병영수첩 화면 스크롤 준비(overflow-y auto)",
                  scroll["can"] == "auto", json.dumps(scroll))
            page.click('[data-screen="maps"]')
            page.wait_for_timeout(450)
            bottom = page.evaluate("""() => {
                const s = document.querySelector('#s-maps');
                const t = document.querySelector('#map-table');
                const r = t.getBoundingClientRect(), sr = s.getBoundingClientRect();
                return {tableVisible: r.top < sr.bottom && r.bottom <= sr.bottom + 2,
                        rows: t.querySelectorAll('tbody tr').length,
                        title: (document.querySelector('#br-table-title') || {}).textContent};
            }""")
            check(f"{height}px 창 · 맵별 분석 표가 화면에 보임",
                  bottom["tableVisible"] and bottom["rows"] >= 10, str(bottom))
            page.screenshot(path=str(SHOTS / f"v110-maps-{height}.png"))
            page.click('[data-screen="barracks"]')
            page.wait_for_timeout(250)
        page.set_viewport_size({"width": 1200, "height": 750})
        page.wait_for_timeout(250)

        # 6) 친구 순위표 · 솔로/파티 티어 통일
        page.click('[data-screen="leaderboard"]')
        page.wait_for_timeout(400)
        lb = page.evaluate("""() => [...document.querySelectorAll('#lb-rows .lb-row')].map((r) => {
            const tiers = [...r.querySelectorAll('.lb-tier')];
            return {name: r.querySelector('.lb-name b').textContent,
                    cells: tiers.map((t) => ({
                        img: (t.querySelector('img') || {}).getAttribute ? t.querySelector('img').getAttribute('src') : '',
                        tier: (t.querySelector('.tt b') || {}).textContent,
                        rp: (t.querySelector('.tt span') || {}).textContent}))};
        })""")
        check("순위표 8명 표시", len(lb) == 8, str(len(lb)))
        check("솔로·파티 모두 [엠블럼 + 티어명 + RP]", all(
            len(r["cells"]) == 2 and all(c["img"] and "assets/" in c["img"] for c in r["cells"])
            and all((c["tier"] or "").strip() for c in r["cells"]) for r in lb),
            json.dumps(lb[0], ensure_ascii=False)[:200])
        me = next(r for r in lb if r["name"] == "휴가중.")
        check("내 파티 티어 = 그랜드마스터 III · 3,435 RP",
              me["cells"][1]["tier"] == "그랜드마스터 III" and "3,435" in me["cells"][1]["rp"],
              json.dumps(me["cells"][1], ensure_ascii=False))
        page.screenshot(path=str(SHOTS / "v110-leaderboard.png"))
        page.click('#lb-head [data-lbsort="party"]')
        page.wait_for_timeout(300)
        order = page.evaluate("""() => [...document.querySelectorAll('#lb-rows .lb-row')]
            .map((r) => Number((r.querySelectorAll('.lb-tier .tt span')[1] || {}).textContent.replace(/[^0-9]/g, '') || 0))""")
        check("파티 RP 정렬도 동일하게 동작", order == sorted(order, reverse=True), str(order))
        page.screenshot(path=str(SHOTS / "v110-leaderboard-party.png"))

        check("UI 런타임 오류 없음", not errors, "; ".join(errors[:3]))
        browser.close()


def main() -> int:
    backend()
    ui()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
