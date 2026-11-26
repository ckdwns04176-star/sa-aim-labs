"""v1.0.1 검사 — 실기 피드백 21건 (포커스 비탈취 · 파티 OCR 재보정 · 65초 최신화 · 18초 토스트 등).

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \
    python3 tests/v101_test.py
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


# ------------------------------------------------------------------ state
def sample_barracks(nick: str = "휴가중.", ouid: str = "711c04a69769cc4b6b782ea2aa3eb614") -> dict:
    today = dt.date.today()
    matches = []
    for i in range(6):
        matches.append({
            "match_id": f"m{i}",
            "date": (today - dt.timedelta(days=0 if i == 0 else 1)).isoformat(),
            "time": f"2{i}:10",
            "map": "프로방스(심플)", "type": "솔로",
            "result": "승" if i % 2 == 0 else "패",
            "k": 12 + i, "d": 9, "kd": 55.0, "damage": 1100,
            "players": [["휴가중.", "팀", 12, 9, 1100], ["아군1", "팀", 8, 7, 900],
                        ["상대에이스", "적", 15, 8, 1300], ["상대2", "적", 10, 9, 950]],
        })
    return {
        "nickname": nick, "ouid": ouid,
        "basic": {"clan": "리드", "created": "2011.05.02", "manner": "3"},
        "tier": {"solo": {"name": "그랜드마스터 II", "rp": 3261},
                 "party": {"name": "그랜드마스터 III", "rp": 3435}},
        "stats": {"games": 100, "kills": 900, "deaths": 800, "wins": 47,
                  "win": 47.0, "kd": 51.5, "dmg": 1009},
        "recent": {"sniper": 60, "assault": 52},
        "maps": [{"rk": 1, "map": "프로방스(심플)", "games": 22, "win": 63.6, "kd": 54.0, "dmg": 1200, "weak": 30.0}],
        "weak": [], "matches": matches, "newest_match": "m0",
        "updated_at": "2026-11-26T17:00:00+09:00",
    }


def build_state(nickname: str = "휴가중.", barracks: bool = True) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="sa_v101_"))
    os.environ["SA_AL_DATA_DIR"] = str(tmp)
    from app.store import Store
    from app.api import Api

    store = Store(tmp / "data.json")
    patch = {"onboarded": True, "nickname": nickname}
    store.update_settings(patch)
    if barracks:
        store.barracks_cache()[nickname] = sample_barracks(nickname)
        store.add_barracks_snapshot(store.barracks_cache()[nickname])
    store.add_record({"date": dt.date.today().isoformat(), "game": "kovak", "weapon": "sniper",
                      "scores": {"precision": 152, "wide": 128, "micro": 96, "track": 88, "switch": 141},
                      "scenarios": [{"name": "VT 1w6ts Rasp Novice", "area": "precision", "score": 152}],
                      "note": ""})
    return Api(store=store)._build_state()


def mock_js(state: dict) -> str:
    return r"""
    window.__CALLS = [];
    window.__PY_STATE = __STATE_JSON__;
    const S = () => JSON.parse(JSON.stringify(window.__PY_STATE));
    const PLAN = {ok: true, available: true, members: ['휴가중.', '0.1초의각'], member_count: 2,
      sample_games: 64, headline: '픽 프로방스 61% · 밴 시티캣 38%',
      picks: [{map: '프로방스(심플)', games: 22, win: 63.6, expected: 61.2, image: 'assets/maps/m19.jpg'}],
      bans: [{map: '시티캣-심플', games: 12, win: 33.3, expected: 38.0, image: 'assets/maps/m25.jpg'}],
      message: '픽 프로방스 61%'};
    window.pywebview = {api: {
      async bootstrap() { return {ok: true, state: S()}; },
      async get_state() { window.__CALLS.push(['get_state']); return {ok: true, state: S()}; },
      async get_nvidia_state() { return {ok: true, utils: {supported: false, accel: false, backups: [], points: []}, state: S()}; },
      async save_settings(patch) { window.__CALLS.push(['save_settings', patch]); return {ok: true, state: S()}; },
      async drag_window() { return {ok: true}; },
      async start_window_drag() { return {ok: true}; },
      async end_window_drag() { return {ok: true}; },
      async move_window() { return {ok: true}; },
      async minimize_window() { return {ok: true}; },
      async close_window() { return {ok: true}; },
      async fetch_barracks() { window.__CALLS.push(['fetch_barracks']); return {ok: true, state: S(), barracks: S().barracks, message: '최신화'}; },
      async fetch_leaderboard() { return {ok: true, state: S(), message: '갱신'}; },
      async save_training_session() { return {ok: true, state: S()}; },
      async launch_trainer(url) { window.__CALLS.push(['launch_trainer', url]); return {ok: true}; },
      async ack_digest() { return {ok: true, state: S()}; },
      async check_update() { return {ok: false, newer: false}; },
      async update_gate() { return {ok: true, gate: S().update_gate, update: S().update}; },
      async tilt_state() { return {ok: true, tilt: {enabled: false, supported: false, alerts: []}}; },
      async set_tilt_watch() { return {ok: true, tilt: {alerts: []}, state: S()}; },
      async ack_tilt() { return {ok: true, alerts: []}; },
      async cloud_status() { return {ok: true, cloud: {}, state: S()}; },
      async cloud_signin() { return {ok: true, state: S()}; },
      async cloud_pull() { return {ok: true, state: S()}; },
      async complete_onboarding() { return {ok: true, state: S()}; },
      async skip_onboarding() { return {ok: true, state: S()}; },
      async scout_team() { return {ok: true, rows: [], team: {}}; },
      async scout_last_match() { return {ok: true, rows: [], team: {}, message: ''}; },
      async duo_synergy() { return {ok: true, available: false, reason: '테스트'}; },
      async duo_rank() { return {ok: true, rows: []}; },
      async personal_playlist() { return {ok: true, names: [], reasons: [], coaching: [], focus: [], count: 0}; },
      async balance_candidates() { return {ok: true, members: [], coplayers: []}; },
      async balance_teams() { return {ok: true, available: false}; },
      async party_map_plan(names, refresh) { window.__CALLS.push(['party_map_plan', names, refresh]); return JSON.parse(JSON.stringify(PLAN)); },
      async connect_nickname(text) {
        window.__CALLS.push(['connect_nickname', text]);
        const st = S(); st.settings = st.settings || {}; st.settings.nickname = '휴가중.';
        st.nexon = st.nexon || {}; st.nexon.nickname = '휴가중.';
        return {ok: true, nickname: '휴가중.', state: st, message: '휴가중. 연결 완료'};
      },
      async refresh_leaderboard_names(force) { window.__CALLS.push(['refresh_leaderboard_names', force]); return {ok: true, renamed: [], state: S()}; },
      async search_user() { return {ok: true, found: false, message: '없음'}; },
      async lookup_barracks() { return {ok: true, barracks: S().barracks}; },
      async link_barracks_url() { return {ok: true, nickname: '휴가중.', state: S()}; },
      async open_barracks_page() { return {ok: true, url: ''}; },
      async open_clan_page() { return {ok: true, url: ''}; },
      async copy_text() { return {ok: true}; },
      async save_member_memo() { return {ok: true, state: S()}; },
      async save_report_card() { return {ok: true, path: '/tmp/x.png'}; },
      async open_reports_folder() { return {ok: true, folder: '/tmp'}; },
      async export_playlist() { return {ok: true, message: '저장'}; },
      async import_kovak_scores() { return {ok: true, matched: [], message: '없음'}; },
      async tray_hide() { return {ok: true}; },
      async trainer_watch_state() { return {ok: true, enabled: true, supported: false}; },
      async set_trainer_watch() { return {ok: true, watch: {enabled: true}}; },
      async export_backup() { return {ok: true}; },
      async import_backup() { return {ok: true, state: S()}; },
      async match_refresh_state() { return {ok: true, interval: 65}; },
      async add_scenario(p) { return {ok: true, state: S()}; },
      async delete_scenario(n) { return {ok: true, state: S()}; },
      async set_scenario_excluded() { return {ok: true, state: S()}; },
      async resolve_barracks() { return {ok: true, sn: '96E8A3306F1061D2SA', state: S()}; }
    }};
    """.replace("__STATE_JSON__", json.dumps(state, ensure_ascii=False))


# ------------------------------------------------------------------ backend
def backend() -> None:
    from app import overlay, party_lobby, hotkeys, toast, nexon, winutil, paths
    from app import api as api_mod
    import inspect

    check("APP_VERSION 1.0.1", paths.APP_VERSION == "1.0.1", paths.APP_VERSION)

    # ── #2 · 파티 대기실 5칸 좌표 재보정 (2048×945 캡처의 클라이언트 1682 기준)
    g = (0, 0, 1682, 945)
    cards = overlay.party_card_rects(g)
    check("#2 닉네임 크롭 5칸", len(cards) == 5, str(len(cards)))
    check("#2 크롭 y = 446 (아이콘 줄이 아닌 남색 닉네임 줄)", 443 <= cards[0]["y"] <= 449 and cards[0]["h"] == 27,
          str(cards[0]))
    check("#2 카드 폭 280 · 피치 320", cards[0]["w"] == 280 and cards[1]["x"] - cards[0]["x"] == 320,
          f'{cards[0]["w"]}px · {cards[1]["x"] - cards[0]["x"]}px')
    check("#2 크롭이 클라이언트 안", all(c["x"] >= 0 and c["x"] + c["w"] <= 1682 for c in cards),
          str(cards[-1]))
    check("#2 클라이언트 폭이 달라도 비율 유지",
          overlay.party_card_rects((0, 0, 1920, 1080))[0]["w"] == 320,
          str(overlay.party_card_rects((0, 0, 1920, 1080))[0]))

    # ── #18 · 배지 창 = 닉네임 줄 바로 아래
    rect = overlay.party_overlay_rect(g)
    check("#18 배지 창이 닉네임 줄 바로 아래에서 시작", rect["y"] == cards[0]["y"] + cards[0]["h"], str(rect))
    check("#18 배지 창이 카드 줄 폭과 같다", rect["x"] == cards[0]["x"] and rect["w"] == 5 * 320 - 40, str(rect))
    check("#18 창 높이 = 배지 줄 + 맵 띠", rect["h"] == 68, str(rect["h"]))
    layout = overlay.party_layout(rect, g)
    check("#18 배지 5개가 카드 위치 %와 일치",
          [round(c["left"], 2) for c in layout["chips"]] == [0.0, 20.51, 41.03, 61.54, 82.05],
          str([c["left"] for c in layout["chips"]]))
    check("#19 맵 띠가 카드 아래(배지 창 안)", layout["maps_top"] > 40 and layout["maps_top"] + layout["maps_height"] <= 100.5,
          f'{layout["maps_top"]}%')
    badges = overlay.party_badge_rects(g)
    check("#18 배지 사각형이 카드 폭 그대로", all(b["w"] == c["w"] for b, c in zip(badges, cards)), str(badges[0]))

    # ── #6 · 스카우팅 창은 오른쪽 아래
    sc = overlay.scout_overlay_rect((0, 0, 1920, 1080))
    check("#6 상대 스카우팅 창 우하단", sc["x"] + sc["w"] == 1920 - 23 and sc["y"] + sc["h"] == 1080 - 22, str(sc))
    check("#6 좌측 상단이 아니다", sc["x"] > 1920 / 2 and sc["y"] > 1080 / 2, str(sc))

    # ── #7 · Insert / Delete 단축키
    check("#7 Insert/Delete 허용", overlay.clean_hotkey("insert") == "Insert"
          and overlay.clean_hotkey("Delete") == "Delete", overlay.clean_hotkey("insert"))
    check("#7 조합도 허용", overlay.clean_hotkey("ctrl+delete") == "Ctrl+Delete"
          and overlay.clean_hotkey("Alt+Insert") == "Alt+Insert", overlay.clean_hotkey("ctrl+delete"))
    check("#7 선택지에 포함", "Insert" in overlay.HOTKEY_CHOICES and "Delete" in overlay.HOTKEY_CHOICES
          and "Ctrl+Delete" in overlay.HOTKEY_CHOICES, str(len(overlay.HOTKEY_CHOICES)))
    check("#7 RegisterHotKey 가상키 등록", hotkeys.parse("Insert") == (hotkeys.MOD_NOREPEAT, 0x2D)
          and hotkeys.parse("Delete") == (hotkeys.MOD_NOREPEAT, 0x2E),
          f'{hotkeys.parse("Insert")} / {hotkeys.parse("Delete")}')
    check("#7 Alt+Delete 조합", hotkeys.parse("Alt+Delete") == (hotkeys.MOD_ALT | hotkeys.MOD_NOREPEAT, 0x2E),
          str(hotkeys.parse("Alt+Delete")))
    check("#7 모르는 키는 기본값", overlay.clean_hotkey("Bogus") == "F8", overlay.clean_hotkey("Bogus"))

    # ── #1 · 포커스 비탈취
    style = winutil.overlay_style(0)
    check("#1 NOACTIVATE 스타일", winutil.overlay_style_keeps_focus(style)
          and bool(style & winutil.WS_EX_TRANSPARENT) and bool(style & winutil.WS_EX_TOPMOST), hex(style))
    src = inspect.getsource(overlay.OverlayHub._ensure)
    check("#1 오버레이 창은 hidden 으로 만든다", "hidden=True" in src, "")
    src2 = inspect.getsource(overlay.OverlayHub._style_and_show)
    check("#1 스타일 → ShowWindow(SW_SHOWNOACTIVATE) → 게임 포커스 복구 순서",
          "apply_overlay_style" in src2 and "show_no_activate" in src2 and "restore_foreground" in src2, "")
    check("#1 SW_SHOWNOACTIVATE 상수", winutil.SW_SHOWNOACTIVATE == 4, str(winutil.SW_SHOWNOACTIVATE))
    check("#1 비Windows 는 조용히 미지원", winutil.show_no_activate(0).get("supported") is False, "")

    # ── #2 · OCR 오매칭 억제
    class FakeClient:
        NAMES = {"도봉이", "ak-호야", "hunny", "llkkkllll", "영코"}

        def resolve_ouid(self, name: str) -> str:
            if name in self.NAMES:
                return f"ouid-{name}"
            raise nexon.NexonError("없음")

        def user_basic(self, ouid: str) -> dict:
            return {"user_name": ouid.replace("ouid-", "")}

    rows = party_lobby.validate_names(FakeClient(), ["도봉이", "ak-호야", "hunny", "llkkkllll", "영코"],
                                      ["도봉이", "ak-호야", "hunny", "llkkkllll", "영코"])
    check("#2 5명 정확 인식 = 일치", all(r["status"] == "ok" for r in rows) and len(rows) == 5,
          str([r["status"] for r in rows]))
    typo = party_lobby.validate_names(FakeClient(), ["hunnyy"], ["hunny", "영코"])[0]
    check("#2 오타 1글자는 기존 이름으로 보정", typo["name"] == "hunny" and typo["status"] == "corrected", str(typo))
    other = party_lobby.validate_names(FakeClient(), ["완전다른사람"], ["hunny", "영코"])[0]
    check("#2 전혀 다른 이름은 다른 사람으로 바꾸지 않는다",
          other["name"] == "완전다른사람" and other["status"] == "unknown" and other["ouid"] is None, str(other))
    check("#2 유사도 임계값 상향", party_lobby.best_candidate("zzzzzz", ["hunny"])[1] == 0.0,
          str(party_lobby.best_candidate("zzzzzz", ["hunny"])))
    check("#2 같은 이름은 1.0", party_lobby.best_candidate("hunny", ["hunny"]) == ("hunny", 1.0), "")

    # ── #13 · 토스트 감지 주기
    check("#13 토스트 폴링 15~20초", 15 <= toast.POLL_SECONDS <= 20, str(toast.POLL_SECONDS))
    watcher = toast.MatchWatcher(lambda: None, lambda: "", lambda m: None, interval=18)
    check("#13 감시 간격이 상태에 노출", watcher.state().get("interval") == 18, str(watcher.state()))
    check("#13 방금 판은 캐시를 건너뛴다", "fresh=True" in inspect.getsource(toast.last_match), "")

    # ── #12 · 65초 자동 최신화
    check("#12 최신화 주기 = 65초", api_mod.MATCH_REFRESH_SECONDS == 65, str(api_mod.MATCH_REFRESH_SECONDS))
    check("#17 경기 목록 캐시 2분 이하", nexon.MATCH_LIST_TTL <= 120, str(nexon.MATCH_LIST_TTL))

    # fresh 조회가 캐시를 건너뛰는지 (네트워크 없이)
    client = nexon.NexonClient(api_key="test_v101")
    client._cache_api.data = {}
    payloads = [{"match": [{"match_id": "old"}]}, {"match": [{"match_id": "new"}]}]
    calls = {"n": 0}

    def fake_request(path, params, *a, **k):
        row = payloads[min(calls["n"], len(payloads) - 1)]
        calls["n"] += 1
        return row

    client._request = fake_request  # type: ignore[assignment]
    first = client.matches("ouid", "랭크전 솔로")
    cached = client.matches("ouid", "랭크전 솔로")
    fresh = client.matches("ouid", "랭크전 솔로", fresh=True)
    check("#17 목록은 캐시를 쓴다", first[0]["match_id"] == cached[0]["match_id"] == "old", str(cached))
    check("#12 fresh 는 캐시를 건너뛴다", fresh[0]["match_id"] == "new", str(fresh))

    # ── #4 · 클라우드 백업 트리거
    st = build_state()
    check("#4 상태에 자동 백업 설정", st["cloud_auto"]["enabled"] is True, str(st["cloud_auto"]))
    check("#4 상태에 전적 최신화 주기", st["match_refresh"]["interval"] == 65, str(st["match_refresh"]))
    from app.api import Api
    from app.store import Store

    tmp = Path(tempfile.mkdtemp(prefix="sa_v101_cloud_"))
    api = Api(store=Store(tmp / "data.json"))
    api.store.settings["auto_cloud"] = True
    check("#4 로그아웃 상태에선 저장 직후 백업을 건너뛴다", api._queue_cloud_backup("training").get("ok") is False,
          str(api._queue_cloud_backup("training")))
    api.store.cloud()["refresh_token"] = "rt"
    check("#4 리프레시 토큰만 있어도 로그인 상태로 본다", api._cloud_state().get("signed_in") is True,
          str(api._cloud_state()))
    src3 = inspect.getsource(Api.cloud_push)
    check("#4 401 이면 토큰 갱신 후 재시도", "refresh" in src3 and "401" in src3, "")
    check("#4 aim_scores 실패를 숨기지 않는다", "aim_scores_error" in src3, "")

    # ── #9 · ouid 기준 닉네임 최신화
    tmp2 = Path(tempfile.mkdtemp(prefix="sa_v101_lb_"))
    api2 = Api(store=Store(tmp2 / "data.json"))
    api2.store.set_leaderboard([{"name": "옛닉네임", "ouid": "ouid-x", "solo": 3000}])
    api2.store.set_memo("옛닉네임", "형님")

    class Basic:
        def user_basic(self, ouid):
            return {"user_name": "새닉네임", "clan_name": "새클랜"}

        def resolve_ouid(self, name):
            return "ouid-x"

    api2._client = lambda *a, **k: Basic()  # type: ignore[assignment]
    res = api2.refresh_leaderboard_names(force=True)
    names = [m["name"] for m in api2.store.leaderboard()]
    check("#9 저장된 ouid 로 현재 닉네임 갱신", "새닉네임" in names and "옛닉네임" not in names, str(names))
    check("#9 메모도 새 이름으로 이동", api2.store.memos().get("새닉네임") == "형님", str(api2.store.memos()))
    check("#9 변경 내역 보고", res.get("renamed") and res["renamed"][0]["to"] == "새닉네임", str(res.get("message")))

    # ── #10 · 닉네임 연결 API
    check("#10 connect_nickname 존재", hasattr(Api, "connect_nickname"), "")
    check("#10 빈 입력은 오류", Api(store=Store(Path(tempfile.mkdtemp()) / "d.json")).connect_nickname("").get("ok") is False, "")


# ------------------------------------------------------------------ UI
def ui() -> None:
    from playwright.sync_api import sync_playwright

    index = (ROOT / "ui" / "index.html").as_uri()
    state = build_state()
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 750})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(mock_js(state))
        page.goto(index)
        page.wait_for_selector("#app-splash", state="hidden", timeout=15000)
        page.wait_for_timeout(700)

        # ── #5 · 사이드바 홈 제거 + 로고가 홈
        nav = page.evaluate("() => [...document.querySelectorAll('.nav-item')].map(b => b.dataset.screen)")
        check("#5 사이드바에서 홈 메뉴 제거", "home" not in nav and len(nav) == 8, str(nav))
        page.click('.nav-item[data-screen="dashboard"]')
        page.wait_for_timeout(250)
        page.click("#rail-home")
        page.wait_for_timeout(300)
        check("#5 로고 클릭 → 홈", page.evaluate("() => !document.getElementById('s-home').hidden")
          and page.evaluate("() => document.getElementById('s-dashboard').hidden"), "")

        # ── #8 · 상단 배지 (프록시 기본 연결)
        page.wait_for_timeout(200)
        pill = page.evaluate("() => document.getElementById('tb-pill-text').textContent.trim()")
        check("#8 'API 키 입력 필요' 제거", "API 키" not in pill and "NEXON" in pill, pill)

        # ── #20 · 시나리오 라이브러리 버튼 정리
        page.click('.nav-item[data-screen="recs"]')
        page.wait_for_timeout(300)
        lib = page.evaluate("""() => ({
            ids: ['warmup-btn-2','plo-btn','score-input-btn'].filter(id => !!document.getElementById(id)),
            keep: ['kovak-import-head','personal-plo-btn','rec-add','run-all-btn'].filter(id => !!document.getElementById(id))})""")
        check("#20 워밍업·플레이리스트·5칸 입력 제거", lib["ids"] == [], str(lib["ids"]))
        check("#20 나머지 버튼 4개 유지", len(lib["keep"]) == 4, str(lib["keep"]))

        # ── #15·#16 · 전술 도구 버튼
        page.click('.nav-item[data-screen="utils"]')
        page.wait_for_timeout(350)
        tac = page.evaluate("""() => ({
            quick: !!document.getElementById('util-balance-quick'),
            duoPick: !!document.getElementById('util-duo-pick'),
            duo: (document.getElementById('util-duo') || {}).textContent,
            bal: (document.getElementById('util-balance') || {}).textContent})""")
        check("#15 내전 3:3 버튼 제거", tac["quick"] is False, "")
        check("#16 듀오 버튼 → '시너지 확인' 하나로", tac["duoPick"] is False
              and (tac["duo"] or "").strip() == "시너지 확인", str(tac))

        # ── #21 · 스나/라플 세그 정렬 (픽셀)
        page.click('.nav-item[data-screen="dashboard"]')
        page.wait_for_timeout(350)
        boxes = page.evaluate("""() => {
            const box = (el) => { const r = el.getBoundingClientRect(); return {t: r.top, h: r.height, b: r.bottom}; };
            const seg = document.querySelector('.seg-weapon.dash-seg');
            const game = document.querySelector('.seg-game.dash-seg');
            const chip = document.querySelector('#s-dashboard .head-right .chip');
            const btn = document.getElementById('start-train-btn');
            return {weapon: box(seg), game: box(game), chip: box(chip), btn: box(btn)};
        }""")
        same_top = abs(boxes["weapon"]["t"] - boxes["btn"]["t"]) <= 0.6 and abs(boxes["weapon"]["b"] - boxes["btn"]["b"]) <= 0.6
        check("#21 스나/라플 세그가 옆 버튼과 위아래 일치", same_top, str(boxes))
        check("#21 세그 높이도 같다", abs(boxes["weapon"]["h"] - boxes["btn"]["h"]) <= 0.6
              and abs(boxes["weapon"]["h"] - boxes["game"]["h"]) <= 0.6, f'{boxes["weapon"]["h"]}/{boxes["btn"]["h"]}')

        # ── #10 · 닉네임 연결 팝업 (닉네임 없는 상태)
        page.evaluate("() => { ['settings-mask','digest-mask','onboard-mask','update-mask'].forEach(id => { const m = document.getElementById(id); if (m) m.classList.remove('show'); }); AL.nickAutoShown = false; AL.nickLater = false; }")
        page.evaluate("() => { const st = JSON.parse(JSON.stringify(AL.state)); st.nexon.nickname = ''; st.settings.nickname = ''; st.barracks = null; AL.applyState(st); }")
        page.wait_for_timeout(900)
        nick = page.evaluate("() => ({open: document.getElementById('nick-mask').classList.contains('show'), ids: !!document.getElementById('nick-input')})")
        check("#10 닉네임 없으면 홈에서 연결 팝업 자동 표시", nick["open"] and nick["ids"], str(nick))
        page.fill("#nick-input", "https://barracks.sa.nexon.com/96E8A3306F1061D2SA/detail")
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#nick-connect")
        page.wait_for_timeout(500)
        calls = page.evaluate("() => window.__CALLS.filter(c => c[0] === 'connect_nickname')")
        check("#10 병영수첩 주소 그대로 connect_nickname 전달", bool(calls)
              and "96E8A3306F1061D2SA" in str(calls[-1][1]), str(calls[-1:])[:90])
        page.screenshot(path=str(SHOTS / "v101-nick-link.png"))
        page.evaluate("() => document.getElementById('nick-mask').classList.remove('show')")

        # 닉네임이 있으면 팝업이 뜨지 않는다
        page.evaluate("() => { AL.nickAutoShown = false; AL.nickLater = false; AL.applyState(JSON.parse(JSON.stringify(window.__PY_STATE))); }")
        page.wait_for_timeout(800)
        nick2 = page.evaluate("() => document.getElementById('nick-mask').classList.contains('show')")
        check("#10 닉네임이 있으면 팝업 없음", nick2 is False, str(nick2))

        # ── #11 · F8 5인 자동 입력 + 분석
        page.evaluate("() => { window.__CALLS = []; const i = document.getElementById('party-names'); if (i) i.value = ''; }")
        page.evaluate("() => AL.partyAutoFill(['도봉이','ak-호야','hunny','llkkkllll','영코'])")
        page.wait_for_timeout(500)
        fill = page.evaluate("""() => ({value: document.getElementById('party-names').value,
            calls: window.__CALLS.filter(c => c[0] === 'party_map_plan').length,
            rows: document.querySelectorAll('#party-picks .party-row').length})""")
        check("#11 5인 자동 입력", "도봉이" in fill["value"] and "영코" in fill["value"], fill["value"])
        check("#11 분석까지 자동 실행", fill["calls"] >= 1 and fill["rows"] >= 1, str(fill))

        # ── #14 · 최근 상대 팀에 우리 팀이 섞이지 않는다
        page.click('.nav-item[data-screen="utils"]')
        page.wait_for_timeout(400)
        recent = page.evaluate("""() => ({opp: [...document.querySelectorAll('#util-opp [data-opp]')].map(b => b.dataset.opp),
            meta: (document.getElementById('util-recent-meta') || {}).textContent})""")
        check("#14 우리 팀원 제외 · 상대만", "아군1" not in recent["opp"] and "상대에이스" in recent["opp"], str(recent))
        check("#14 최근 매치 날짜·시간 표시", "." in (recent["meta"] or "") and ":" in (recent["meta"] or ""), recent["meta"])

        # ── #7 · 설정 단축키 선택지
        page.click("#settings-btn")
        page.wait_for_timeout(450)
        page.evaluate("() => { const t = document.querySelector('#set-tabs [data-set-tab=\"overlay\"]'); if (t) t.click(); }")
        page.wait_for_timeout(400)
        opts = page.evaluate("""() => { const s = document.getElementById('set-ov-hot1');
            return s ? [...s.options].map(o => o.value) : []; }""")
        if not opts:
            page.evaluate("() => document.getElementById('settings-mask').classList.add('show')")
            page.wait_for_timeout(200)
            page.evaluate("() => { const t = document.querySelector('#set-tabs [data-set-tab=\"overlay\"]'); if (t) t.click(); }")
            page.wait_for_timeout(300)
            opts = page.evaluate("""() => { const s = document.getElementById('set-ov-hot1');
                return s ? [...s.options].map(o => o.value) : []; }""")
        check("#7 설정 단축키에 Insert/Delete", "Insert" in opts and "Delete" in opts
              and "Ctrl+Delete" in opts, str(opts[:8]) + "…")
        page.screenshot(path=str(SHOTS / "v101-settings-hotkeys.png"))
        page.evaluate("() => document.getElementById('settings-mask').classList.remove('show')")

        check("UI 런타임 오류 없음", not errors, str(errors[:2]))
        browser.close()

    # ── 파티 오버레이 페이지 (칩 5개 + 상태 문구 + 맵 띠)
    payload = {
        "rows": [
            {"slot": 0, "name": "도봉이", "status": "ok", "score": 1.0},
            {"slot": 1, "name": "ak-호야", "status": "ok", "score": 1.0},
            {"slot": 2, "name": "hunny", "status": "corrected", "score": 0.86},
            {"slot": 3, "name": "llkkkllll", "status": "ok", "score": 1.0},
            {"slot": 4, "name": "영코", "status": "unknown", "score": 0.4},
        ],
        "plan": {"picks": [{"map": "프로방스(심플)", "expected": 61.2, "image": "assets/maps/m19.jpg"},
                           {"map": "팰리스-심플", "expected": 58.4, "image": "assets/maps/m259.jpg"},
                           {"map": "드래곤로드(심플)", "expected": 55.0, "image": "assets/maps/m6.jpg"}],
                 "bans": [{"map": "시티캣-심플", "expected": 38.0, "image": "assets/maps/m25.jpg"},
                          {"map": "이탈리아-폭파", "expected": 39.4, "image": "assets/maps/m231.jpg"},
                          {"map": "크로스포트(심플)", "expected": 41.0, "image": "assets/maps/m27.jpg"}]},
        "layout": {"chips": [{"left": 0, "width": 17.949}, {"left": 20.513, "width": 17.949},
                             {"left": 41.026, "width": 17.949}, {"left": 61.539, "width": 17.949},
                             {"left": 82.051, "width": 17.949}],
                   "chip_top": 0, "chip_height": 41.429, "maps_top": 48.572, "maps_height": 51.428},
    }
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1180, "height": 96})
        page.add_init_script(
            "window.pywebview = {api: {overlay_payload: async () => (%s)}};"
            % json.dumps({"ok": True, "payload": payload}, ensure_ascii=False))
        page.goto((ROOT / "ui" / "overlay_party.html").as_uri())
        page.wait_for_timeout(600)
        ov = page.evaluate("""() => ({
            chips: document.querySelectorAll('.chip').length,
            labels: [...document.querySelectorAll('.chip .bd')].map(e => e.textContent),
            nums: [...document.querySelectorAll('.chip')].some(e => /\\d{2,3}/.test(e.textContent)),
            names: [...document.querySelectorAll('.chip .nm')].map(e => e.textContent),
            maps: document.querySelectorAll('.mc').length,
            imgs: [...document.querySelectorAll('.mc img')].map(e => e.getAttribute('src')),
            mapsVisible: getComputedStyle(document.getElementById('maps')).display !== 'none',
            box: (() => { const r = document.querySelector('.chip').getBoundingClientRect();
                          return {x: Math.round(r.x), w: Math.round(r.width), h: Math.round(r.height)}; })()
        })""")
        check("#3 상태 문구 = 일치/유사/미확인 (숫자 없음)", ov["labels"] == ["일치", "일치", "유사", "일치", "미확인"]
              and ov["nums"] is False, str(ov["labels"]))
        check("#18·#19 오버레이에 칩 5개 + 맵 6개", ov["chips"] == 5 and ov["maps"] == 6 and ov["mapsVisible"], str(ov["maps"]))
        check("#19 맵 사진 6장", all(imgs.startswith("assets/maps/") for imgs in ov["imgs"]) and len(ov["imgs"]) == 6,
              str(ov["imgs"][:2]))
        check("#18 칩 폭이 카드 폭 비율(17.9%)", abs(ov["box"]["w"] - round(1180 * 0.17949)) <= 2, str(ov["box"]))
        page.screenshot(path=str(SHOTS / "v101-overlay-party.png"))
        browser.close()


def main() -> int:
    backend()
    try:
        ui()
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("UI 검사 실행", False, str(exc)[:160])
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
