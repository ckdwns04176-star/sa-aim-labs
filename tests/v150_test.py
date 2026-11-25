"""v1.5.0 검사 — 딥 차콜 디자인 · 3색 테마 · 화면 배율 · 피드백 27건 · 신규 3종.

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \
    python3 tests/v150_test.py
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


# ------------------------------------------------------------------ sample data
def sample_barracks(nick: str = "휴가중.", ouid: str = "711c04a69769cc4b6b782ea2aa3eb614") -> dict:
    maps = [
        ("이탈리아-폭파", 6, 33.3, 43.5, 836.8), ("프로방스(심플)", 12, 25.0, 51.0, 1177.8),
        ("크로스포트(심플)", 5, 40.0, 41.2, 873.8), ("시티캣-심플", 10, 40.0, 50.5, 995.1),
        ("올드타운(심플)", 8, 37.5, 56.1, 1071.9), ("팰리스-심플", 10, 50.0, 48.8, 857.2),
        ("지하철-심플", 9, 55.6, 55.2, 951.2), ("제5보급-심플", 11, 63.6, 49.5, 765.9),
        ("드래곤로드(심플)", 11, 54.5, 51.3, 1295.8), ("듀오 릭", 7, 57.1, 58.2, 984.4),
    ]
    rows = []
    for i in range(20):
        name = maps[i % len(maps)][0]
        win = i < 2 or i % 2 == 0
        rows.append({
            "date": (dt.date.today() - dt.timedelta(days=i % 7)).isoformat(),
            "time": f"20:{10 + i:02d}",
            "map": name, "type": "솔로" if i % 3 else "파티",
            "result": "승" if win else "패",
            "k": 12 + i % 7, "d": 9 + i % 5, "kd": 50.0 + (i % 8), "damage": 900 + i * 20,
            "players": [],
        })
    return {
        "nickname": nick, "ouid": ouid,
        "basic": {"clan": "리드", "created": "2011.05.02", "manner": "3"},
        "tier": {"solo": {"name": "그랜드마스터 II", "rp": 3261},
                 "party": {"name": "그랜드마스터 III", "rp": 3435}},
        "stats": {"games": 100, "kills": 900, "deaths": 800, "wins": 47,
                  "win": 47.0, "kd": 51.5, "dmg": 1009},
        "recent": {"sniper": 60, "assault": 52},
        "maps": [{"rk": i + 1, "map": m[0], "games": m[1], "win": m[2], "kd": m[3],
                  "dmg": m[4], "weak": round(100 - m[2] * .5 - m[3] * .3 - m[4] / 200, 1)}
                 for i, m in enumerate(maps)],
        "matches": rows,
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def build_state(**settings) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="sa_v150_"))
    os.environ["SA_AL_DATA_DIR"] = str(tmp)
    from app.store import Store
    from app.api import Api

    store = Store(tmp / "data.json")
    base = {"nickname": "휴가중.", "api_key": "test_9542ea5ac663d2c0", "onboarded": True}
    base.update(settings)
    store.update_settings(base)
    today = dt.date.today()
    store.add_record({
        "date": today.isoformat(), "game": "kovak", "weapon": "sniper",
        "scores": {"precision": 152, "wide": 128, "micro": 96, "track": 88, "switch": 141},
        "scenarios": [{"name": "VT 1w6ts Rasp Novice", "area": "precision", "score": 152}],
        "note": "",
    })
    barracks = sample_barracks()
    store.barracks_cache()[barracks["nickname"]] = barracks
    store.barracks_history().extend([
        {"date": "2026-11-20", "at": "2026-11-20T12:00:00", "nickname": "휴가중.",
         "stats": {"win": 44.0, "kd": 50.3, "dmg": 980},
         "tier": {"solo": {"rp": 3223}, "party": {"rp": 3400}}, "basic": {"clan": "리드"}},
        {"date": "2026-11-21", "at": "2026-11-21T12:00:00", "nickname": "휴가중.",
         "stats": {"win": 47.0, "kd": 51.5, "dmg": 1009},
         "tier": {"solo": {"rp": 3261}, "party": {"rp": 3435}}, "basic": {"clan": "리드"}},
    ])
    store.save()
    store.set_leaderboard([
        {"name": "휴가중.", "me": True, "ouid": barracks["ouid"], "clan": "리드", "weapon": "sniper",
         "solo": 3261, "party": 3435, "kd": 51.5, "dmg": 1009, "win": 47.0},
        {"name": "0.1초의각", "ouid": "ouid-friend-1", "clan": "리드", "weapon": "sniper",
         "solo": 3208, "party": 3520, "kd": 49.3, "dmg": 1041, "win": 50.4},
    ])
    return Api(store=store)._build_state()


def mock_js(state: dict) -> str:
    return r"""
    window.__CALLS = [];
    window.__PY_STATE = __STATE_JSON__;
    const S = () => JSON.parse(JSON.stringify(window.__PY_STATE));
    const clone = (v) => JSON.parse(JSON.stringify(v));
    const PLAN = {ok: true, available: true, members: ['휴가중.', '0.1초의각'], member_count: 2,
      sample_games: 64, headline: '픽 프로방스 61% · 밴 시티캣 38%',
      picks: [{map: '프로방스(심플)', games: 22, win: 63.6, expected: 61.2, kd: 54.0, image: 'assets/maps/m19.jpg'},
              {map: '팰리스-심플', games: 18, win: 61.1, expected: 58.4, kd: 52.2, image: 'assets/maps/m259.jpg'},
              {map: '드래곤로드(심플)', games: 14, win: 57.1, expected: 55.0, kd: 51.0, image: 'assets/maps/m6.jpg'}],
      bans: [{map: '시티캣-심플', games: 12, win: 33.3, expected: 38.0, kd: 43.1, image: 'assets/maps/m25.jpg'},
             {map: '이탈리아-폭파', games: 9, win: 33.3, expected: 39.4, kd: 42.0, image: 'assets/maps/m231.jpg'},
             {map: '크로스포트(심플)', games: 8, win: 37.5, expected: 41.0, kd: 44.0, image: 'assets/maps/m27.jpg'}],
      message: '픽 프로방스 61% · 밴 시티캣 38%'};
    window.pywebview = {api: {
      async bootstrap() { return {ok: true, state: S()}; },
      async get_state() { return {ok: true, state: S()}; },
      async get_nvidia_state() {
        return {ok: true, utils: {supported: true, mode: null, alias: false, backups: [], points: [],
          modes: {rifle: true, sniper: true, stairfix: true}, missing: [], accel: false,
          stairfix: {ready: true, applied: false, value: '0x00020041', label: '기본값', target: '0x00401241'}}, state: S()};
      },
      async save_settings(patch) { window.__CALLS.push(['save_settings', patch]); const st = S();
        Object.assign(st.settings || (st.settings = {}), patch || {});
        if (patch && patch.theme) st.theme = patch.theme;
        if (patch && patch.ui_scale) st.ui_scale = patch.ui_scale;
        return {ok: true, state: st}; },
      async drag_window() { return {ok: true, supported: true}; },
      async start_window_drag() { return {ok: true, supported: true}; },
      async end_window_drag() { return {ok: true}; },
      async move_window() { return {ok: true}; },
      async minimize_window() { return {ok: true}; },
      async close_window() { window.__CALLS.push(['close_window']); return {ok: true}; },
      async fetch_barracks() { return {ok: true, state: S(), barracks: S().barracks, message: '최신화'}; },
      async fetch_leaderboard() { window.__CALLS.push(['fetch_leaderboard']); return {ok: true, state: S(), message: '전체 갱신'}; },
      async save_training_session(p) { return {ok: true, state: S()}; },
      async launch_trainer(url) { window.__CALLS.push(['launch_trainer', url]); return {ok: true, url}; },
      async open_external(url) { window.__CALLS.push(['open_external', url]); return {ok: true}; },
      async ack_digest() { const st = S(); st.digest = {...(st.digest||{}), show: false, ack: true}; return {ok: true, state: st}; },
      async check_update() { return {ok: false, newer: false}; },
      async update_gate() { return {ok: true, gate: S().update_gate, update: S().update}; },
      async install_update() { return {ok: false, message: '테스트'}; },
      async tilt_state() { return {ok: true, tilt: {enabled: true, supported: false, alerts: [], game_running: null, last_check_at: null}}; },
      async set_tilt_watch() { return {ok: true, tilt: {alerts: []}, state: S()}; },
      async ack_tilt() { return {ok: true, alerts: []}; },
      async cloud_status() { return {ok: true, cloud: {}, state: S()}; },
      async cloud_signin() { return {ok: true, state: S()}; },
      async cloud_pull() { return {ok: true, state: S()}; },
      async complete_onboarding() { return {ok: true, state: S()}; },
      async skip_onboarding() { return {ok: true, state: S()}; },
      async scout_team() { return {ok: true, rows: [], team: {}, calls: 0, message: ''}; },
      async scout_last_match() {
        window.__CALLS.push(['scout_last_match']);
        return {ok: true, calls: 8, map: '프로방스(심플)', message: '이번 조회 API 8건',
          rows: [{name: '상대에이스', solo: 3500, solo_tier: '불레전드', kd: 54.0, dmg: 1200, win: 55.0, games: 30,
                  weak_maps: [], maps: [{map: '프로방스(심플)', games: 9, win: 22.2, kd: 40.0},
                                        {map: '시티캣-심플', games: 6, win: 33.3, kd: 44.0},
                                        {map: '팰리스-심플', games: 8, win: 62.5, kd: 58.0}],
                  form: {games: 8, kd: 52.0, streak: 1}, weapon: {key: 'sniper', label: '스나', estimate: '추정 스나'},
                  threat: 60, threat_label: '강함', style: null}],
          team: {tips: []}};
      },
      async duo_synergy() { return {ok: true, available: false, reason: '테스트'}; },
      async duo_rank() { return {ok: true, available: false, rows: []}; },
      async personal_playlist(g, w, save) {
        window.__CALLS.push(['personal_playlist', g, w, save]);
        return {ok: true, names: ['A'], reasons: [{name: 'A', area: '정밀 플릭', why: '약점'}],
                coaching: ['코칭'], focus: ['정밀 플릭'], count: 1, philosophy: '스나', message: 'ok'};
      },
      async balance_candidates() {
        return {ok: true, members: [{name: '휴가중.', solo: 3261, me: true, source: 'leaderboard'},
                                     {name: '0.1초의각', solo: 3208, source: 'leaderboard'}], coplayers: []};
      },
      async balance_teams() {
        window.__CALLS.push(['balance_teams']);
        return {ok: true, available: true, size: 2, pool: 2, diff: 1, verdict: '균형',
          a: {count: 1, power: 70, snipers: 1, rows: [{name: '휴가중.', power: 70, role: 'sniper', me: true}]},
          b: {count: 1, power: 69, snipers: 1, rows: [{name: '0.1초의각', power: 69, role: 'rifle'}]},
          note: ''};
      },
      async open_barracks_page(ouid, name) { window.__CALLS.push(['open_barracks_page', ouid, name]); return {ok: true, url: 'https://barracks.sa.nexon.com/96E8A3306F1061D2SA/detail'}; },
      async open_clan_page(ouid, name) { window.__CALLS.push(['open_clan_page', ouid, name]); return {ok: true, url: 'https://clan.sa.nexon.com/harlem22'}; },
      async open_playlists_folder() { window.__CALLS.push(['open_playlists_folder']); return {ok: true, folder: 'C:/Playlists'}; },
      async copy_text(t) { window.__CALLS.push(['copy_text', t]); return {ok: true}; },
      async party_map_plan(names, refresh) { window.__CALLS.push(['party_map_plan', names, refresh]); return clone(PLAN); },
      async add_custom_scenario(p) { window.__CALLS.push(['add_custom_scenario', p]); return {ok: true, state: S(), message: '추가했습니다'}; },
      async delete_custom_scenario(n) { window.__CALLS.push(['delete_custom_scenario', n]); return {ok: true, state: S(), message: '삭제했습니다'}; },
      async set_scenario_excluded(n, e) { window.__CALLS.push(['set_scenario_excluded', n, e]); return {ok: true, state: S(), message: 'ok'}; },
      async link_barracks_url(t) { window.__CALLS.push(['link_barracks_url', t]); return {ok: true, sn: '96E8A3306F1061D2SA', nickname: '휴가중.', state: S(), message: '연결'}; },
      async resolve_barracks(n, r) { window.__CALLS.push(['resolve_barracks', n, r]); return {ok: true, sn: '96E8A3306F1061D2SA', state: S()}; },
      async trainer_watch_state() { return {ok: true, enabled: true, supported: false, running: [], last: null}; },
      async set_trainer_watch(v) { window.__CALLS.push(['set_trainer_watch', v]); return {ok: true, watch: {enabled: v}}; },
      async save_member_memo() { return {ok: true, state: S()}; },
      async save_report_card() { return {ok: true, path: '/tmp/x.png', folder: '/tmp/reports', message: '저장'}; },
      async open_reports_folder() { return {ok: true, folder: '/tmp/reports'}; },
      async warmup_plan() { return {ok: true, recs: []}; },
      async export_playlist() { return {ok: true, message: '저장'}; },
      async import_kovak_scores() { return {ok: true, matched: [], message: '없음'}; },
      async tray_hide() { return {ok: true}; },
      async search_user() { return {ok: true, found: false, message: '둘 다 없음'}; },
      async lookup_barracks() { return {ok: true, barracks: S().barracks}; }
    }};
    """.replace("__STATE_JSON__", json.dumps(state, ensure_ascii=False))


# ------------------------------------------------------------------ backend
def backend() -> None:
    from app import paths, scoring, v150, barracks as barracks_mod, store as store_mod

    check("APP_VERSION 1.0.0", paths.APP_VERSION == "1.0.0", paths.APP_VERSION)

    # ── 테마 · 배율
    check("기본 테마 = coral", v150.clean_theme(None) == "coral", v150.clean_theme(None))
    check("테마 3종", set(v150.THEMES) == {"coral", "ice", "gold"}, str(list(v150.THEMES)))
    check("테마 hex", (v150.THEMES["coral"]["hex"], v150.THEMES["ice"]["hex"], v150.THEMES["gold"]["hex"])
          == ("#FF4655", "#38BDF8", "#DFC07C"), "")
    check("모르는 테마는 coral 로", v150.clean_theme("purple") == "coral", "")
    check("배율 4단계", tuple(v150.UI_SCALES) == (90, 100, 115, 125), str(v150.UI_SCALES))
    check("배율 값 보정", (v150.clean_ui_scale(93), v150.clean_ui_scale(130), v150.clean_ui_scale("x"))
          == (90, 125, 100), f"{v150.clean_ui_scale(93)}/{v150.clean_ui_scale(130)}/{v150.clean_ui_scale('x')}")
    check("QHD 자동 115%", v150.auto_ui_scale(2560, 1440, 1.0) == 115, "")
    check("QHD + OS 125% 는 그대로", v150.auto_ui_scale(2560, 1440, 1.25) == 100, "")
    check("FHD 는 100%", v150.auto_ui_scale(1920, 1080, 1.0) == 100, "")
    check("4K 도 115%", v150.auto_ui_scale(3840, 2160, 1.0) == 115, "")

    # ── 기본값 저장
    d = store_mod.DEFAULTS["settings"]
    check("설정 기본값 theme/ui_scale", d.get("theme") == "coral" and d.get("ui_scale") == 100, f"{d.get('theme')}/{d.get('ui_scale')}")
    check("트레이너 감시·자동 백업 기본 ON", d.get("watch_trainers") is True and d.get("auto_cloud") is True, "")

    # ── 공식 맵 이미지
    check("프로방스 공식 이미지", v150.map_image("프로방스(심플)").endswith("m19.jpg"), v150.map_image("프로방스(심플)"))
    check("크로스포트 공식 이미지", v150.map_image("크로스포트-심플").endswith("m27.jpg"), v150.map_image("크로스포트-심플"))
    check("제5보급 → 제5보급창고", v150.map_key("제5보급-심플") == "제5보급창고", v150.map_key("제5보급-심플"))
    check("모르는 맵은 기본 아이콘(빈 값)", v150.map_image("없는맵") == "", v150.map_image("없는맵"))
    idx = v150.map_index_payload()
    check("맵 색인 100개 이상", len(idx) >= 100, str(len(idx)))
    missing = [n for n in ("프로방스", "크로스포트", "올드타운", "마린베이스", "드래곤로드", "시티캣",
                           "제3보급창고", "제5보급창고", "이탈리아-폭파", "듀오 릭", "팰리스", "지하철", "웨어하우스")
               if n not in idx]
    check("주요 13맵 이미지 존재", not missing, str(missing))
    check("맵 이미지 파일이 실제로 있음",
          all((ROOT / "ui" / p).exists() for p in list(idx.values())[:20]), "")

    # ── 파티 맵 밴·픽 (TOP 3 + 예상 승률)
    entries = [
        {"name": "A", "maps": [{"map": "프로방스(심플)", "games": 10, "win": 70.0, "kd": 55.0},
                               {"map": "시티캣-심플", "games": 8, "win": 25.0, "kd": 40.0}]},
        {"name": "B", "maps": [{"map": "프로방스(심플)", "games": 10, "win": 50.0, "kd": 50.0},
                               {"map": "시티캣-심플", "games": 10, "win": 40.0, "kd": 45.0},
                               {"map": "팰리스-심플", "games": 6, "win": 66.7, "kd": 58.0}]},
        {"name": "C", "maps": [{"map": "드래곤로드(심플)", "games": 12, "win": 58.3, "kd": 52.0},
                               {"map": "이탈리아-폭파", "games": 9, "win": 22.2, "kd": 38.0},
                               {"map": "올드타운(심플)", "games": 7, "win": 28.6, "kd": 42.0},
                               {"map": "지하철-심플", "games": 6, "win": 33.3, "kd": 44.0}]},
    ]
    plan = v150.party_map_plan(entries)
    check("파티 맵 — 픽 TOP 3", len(plan["picks"]) == 3, str([r["map"] for r in plan["picks"]]))
    check("파티 맵 — 밴 TOP 3", len(plan["bans"]) == 3, str([r["map"] for r in plan["bans"]]))
    pick_maps = [r["map"] for r in plan["picks"]]
    check("파티 맵 — 픽에 프로방스 포함", "프로방스(심플)" in pick_maps, str(pick_maps))
    check("파티 맵 — 1밴은 이탈리아/시티캣", plan["bans"][0]["map"] in ("이탈리아-폭파", "시티캣-심플"), plan["bans"][0]["map"])
    check("파티 맵 — 예상 승률(%) 계산", all(isinstance(r["expected"], float) and 0 <= r["expected"] <= 100
                                              for r in plan["maps"]), str(plan["maps"][0]))
    prov = next(r for r in plan["maps"] if r["map"] == "프로방스(심플)")
    check("파티 맵 — 판 수 가중(프로방스 60%)", abs(prov["win"] - 60.0) < 0.01, str(prov["win"]))
    check("파티 맵 — 표본 적으면 50% 쪽으로 줄임",
          prov["expected"] < prov["win"] and prov["expected"] > 50, f'{prov["expected"]} vs {prov["win"]}')
    check("파티 맵 — 5명 초과 선택 방지(입력 3명)", plan["member_count"] == 3, str(plan["member_count"]))
    empty = v150.party_map_plan([])
    check("파티 맵 — 기록 없으면 안내", not empty["available"] and bool(empty["reason"]), str(empty["reason"]))

    # ── 등락 뱃지
    hist = [
        {"date": "2026-11-20", "stats": {"win": 44.0, "kd": 50.0, "dmg": 980}, "tier": {"solo": {"rp": 3223}, "party": {"rp": 3400}}},
        {"date": "2026-11-24", "stats": {"win": 47.0, "kd": 51.2, "dmg": 1009}, "tier": {"solo": {"rp": 3261}, "party": {"rp": 3435}}},
    ]
    badges = v150.delta_badges(hist)
    texts = " ".join(b["text"] for b in badges)
    check("등락 뱃지 — 솔로 +38 RP", any(b["key"] == "solo" and b["diff"] == 38 for b in badges), texts)
    check("등락 뱃지 — K/D +1.2%", any(b["key"] == "kd" and abs(b["diff"] - 1.2) < 0.01 for b in badges), texts)
    check("등락 뱃지 — 오름/내림 표시", all(b["tone"] in ("up", "down") for b in badges), texts)
    check("등락 뱃지 — 스냅샷 1개면 없음", v150.delta_badges(hist[:1]) == [], "")

    # ── 내전 팀 복사 문자열
    text = v150.team_copy_text({"teams": {"a": [{"name": "휴가중.", "role": "sniper"}, {"name": "탄피", "role": "rifle"}],
                                           "b": [{"name": "0.1초", "role": "sniper"}]}})
    check("팀 복사 — 한 줄 형식", text.startswith("[A팀] ") and " / [B팀] " in text, text)
    check("팀 복사 — 역할 포함", "휴가중.(스나)" in text and "탄피(라플)" in text, text)

    # ── 사용자 시나리오 · 제외
    row = v150.clean_custom_scenario({"name": "내 플릭", "area": "precision", "diff": "중",
                                      "play_url": "steam://run/824270"}, scoring.AREA_KEYS)
    check("내 시나리오 — 검증", row["name"] == "내 플릭" and row["diff"] == "중급" and row["custom"], str(row))
    try:
        v150.clean_custom_scenario({"name": "", "area": "precision"}, scoring.AREA_KEYS)
        bad = False
    except ValueError:
        bad = True
    check("내 시나리오 — 이름 필수", bad, "")
    try:
        v150.clean_custom_scenario({"name": "x", "area": "nope"}, scoring.AREA_KEYS)
        bad2 = False
    except ValueError:
        bad2 = True
    check("내 시나리오 — 5영역 검증", bad2, "")
    recs = [{"name": "A"}, {"name": "B"}]
    check("제외 시 추천에서 빠짐", [r["name"] for r in v150.apply_exclusions(recs, ["A"])] == ["B"], "")
    check("제외 목록은 라이브러리에 남음", v150.is_excluded("A", ["A"]) and not v150.is_excluded("C", ["A"]), "")

    # ── 플레이 코칭 · 맵 가이드 (지어낸 지형 금지)
    tips = scoring.play_coaching({
        "stats": {"games": 100, "win": 44.0, "kd": 51.5, "dmg": 1180, "kills": 900, "deaths": 800, "wins": 44},
        "tier": {"solo": {"rp": 2100}, "party": {"rp": 2600}},
        "maps": [{"map": "시티캣-심플", "games": 12, "win": 25.0, "kd": 41.0, "weak": 66}],
        "matches": [{"result": "패"}, {"result": "승"}, {"result": "패"}, {"result": "패"}, {"result": "승"}],
        "recent": {"sniper": 60, "assault": 40}})
    flat = " ".join(t["title"] + " " + t["text"] for t in tips)
    check("코칭 — 딜량 대비 승률 지적", "딜량" in flat and "승률" in flat, flat[:80])
    check("코칭 — 지어낸 지형 이름 없음",
          not any(w in flat for w in ("2층", "골목", "중통", "크로스포트 2층", "올드타운 골목")), flat[:80])
    check("코칭 — 3~4줄", 3 <= len(tips) <= 4, str(len(tips)))
    guide = scoring.map_guide({"map": "마린베이스", "games": 8, "win": 40.0, "kd": 45.0, "dmg": 800})
    gflat = " ".join(guide["tips"])
    check("마린베이스 가이드 — 중통 없음", "중통" not in gflat, gflat[:60])
    check("맵 가이드 — 유튜브 링크 1개", guide["youtube"]["label"] == "영상 보기"
          and guide["youtube"]["url"] == guide["youtube"]["search"], guide["youtube"]["label"])

    # ── 병영수첩 ID 해석
    check("병영수첩 URL 파싱", barracks_mod.parse_sn("https://barracks.sa.nexon.com/96E8A3306F1061D2SA/detail")
          == "96E8A3306F1061D2SA", "")
    check("ouid(32자)는 sn 아님", barracks_mod.parse_sn("711c04a69769cc4b6b782ea2aa3eb614") == "", "")
    check("프로필 주소 조립", barracks_mod.profile_url("96e8a3306f1061d2sa")
          == "https://barracks.sa.nexon.com/96E8A3306F1061D2SA/detail", "")
    check("클랜 주소 조립", barracks_mod.clan_url("harlem22") == "https://clan.sa.nexon.com/harlem22", "")
    cache: dict = {}
    res = barracks_mod.Resolver(cache)
    row = res.remember_url("https://barracks.sa.nexon.com/96E8A3306F1061D2SA/detail", "휴가중.")
    check("붙여넣은 URL 즉시 연결", row["sn"] == "96E8A3306F1061D2SA" and res.get("휴가중.")["sn"], str(row))
    check("연결된 주소는 실제 프로필", res.get("휴가중.")["profile"].endswith("/96E8A3306F1061D2SA/detail"), "")

    # ── 트레이너 감시 · 클라우드 자동
    watch = v150.TrainerWatch()
    check("트레이너 감시 — Windows 전용 표시", watch.supported == (os.name == "nt"), str(watch.supported))
    check("트레이너 감시 — 비Windows 에서 poll 안전", watch.poll() is None, "")
    check("트레이너 프로세스 이름", set(v150.TRAINER_PROCESSES) == {"fpsaimtrainer.exe", "aimlab_tb.exe", "aimlab.exe"},
          str(list(v150.TRAINER_PROCESSES)))


def backend_api() -> None:
    from app import v150

    state = build_state()
    check("state — 테마·배율 전달", state["theme"] == "coral" and state["ui_scale"] == 100, f'{state["theme"]}/{state["ui_scale"]}')
    check("state — 테마 3종 목록", len(state["themes"]) == 3, str([t["key"] for t in state["themes"]]))
    check("state — 맵 색인 전달", len(state["map_index"]) >= 100, str(len(state["map_index"])))
    check("state — 등락 뱃지", any(b["key"] == "solo" for b in state["delta"]), str(state["delta"])[:60])
    check("state — v150 플래그", state.get("v150") is True, "")
    check("state — 트레이너 감시 상태", "enabled" in state["trainer_watch"], str(state["trainer_watch"]))
    check("state — 병영수첩 링크", set(state["barracks_links"]) >= {"profile", "clan_url", "photo"}, "")

    tmp = Path(tempfile.mkdtemp(prefix="sa_v150_api_"))
    os.environ["SA_AL_DATA_DIR"] = str(tmp)
    from app.store import Store
    from app.api import Api

    api = Api(store=Store(tmp / "data.json"))
    api.store.update_settings({"nickname": "휴가중."})
    out = api.add_custom_scenario({"name": "커스텀 A", "area": "track", "diff": "고", "play_url": "steam://run/1"})
    check("API — 내 시나리오 추가", out.get("ok") and any(r["name"] == "커스텀 A" for r in api.store.custom_scenarios()),
          str(out.get("message")))
    st = api.get_state()["state"]
    check("API — 라이브러리에 사용자 시나리오 포함",
          any(s.get("custom") and s["name"] == "커스텀 A" for s in st["scenarios"]["kovak-sniper"]), "")
    ex = api.set_scenario_excluded("VT 1w6ts Rasp Novice")
    check("API — 추천 제외 저장", ex.get("ok") and "VT 1w6ts Rasp Novice" in api.get_state()["state"]["excluded_scenarios"], "")
    check("API — 제외 후 추천에서 빠짐",
          not any(r["name"] == "VT 1w6ts Rasp Novice" for r in api.get_state()["state"]["summaries"]["kovak-sniper"]["recs"]), "")
    back = api.set_scenario_excluded("VT 1w6ts Rasp Novice", False)
    check("API — 제외 해제", back.get("ok") and "VT 1w6ts Rasp Novice" not in api.get_state()["state"]["excluded_scenarios"], "")
    dele = api.delete_custom_scenario("커스텀 A")
    check("API — 내 시나리오 삭제", dele.get("ok") and not api.store.custom_scenarios(), str(dele.get("message")))
    check("API — 없는 시나리오 삭제는 실패", not api.delete_custom_scenario("없는 것").get("ok"), "")

    theme = api.save_settings({"theme": "ice"})
    check("API — 테마 저장", theme["state"]["theme"] == "ice", theme["state"]["theme"])
    scale = api.save_settings({"ui_scale": 115})
    check("API — 배율 저장", scale["state"]["ui_scale"] == 115 and scale["state"]["ui_scale_auto"] is False, "")
    bad = api.save_settings({"theme": "nope", "ui_scale": 999})
    check("API — 잘못된 값 보정", bad["state"]["theme"] == "coral" and bad["state"]["ui_scale"] == 125, "")

    watch = api.set_trainer_watch(False)
    check("API — 트레이너 감시 OFF", watch.get("ok") and api.store.settings["watch_trainers"] is False, "")
    off = api.cloud_auto_sync("test")
    check("API — 로그아웃 상태 자동 백업은 건너뜀", off.get("ok") is False and "로그인" in str(off.get("skipped")), str(off))

    plan = Api(store=api.store).party_map_plan(["휴가중."])
    check("API — 파티 맵(캐시 없음) 안내", plan.get("ok") in (True, False) and "message" in plan, str(plan.get("message"))[:60])

    # balance_teams — 순위표에 없는 닉네임은 API 조회로 채운다 (#21)
    from app import nexon
    called = {"n": 0}
    real_fetch = nexon.fetch_member

    def fake_fetch(client, nickname, **kw):
        called["n"] += 1
        return {"name": nickname, "ok": True, "solo": 2800, "party": 2900, "kd": 52.0, "dmg": 1100,
                "win": 51.0, "weapon": "sniper", "clan": "x"}

    nexon.fetch_member = fake_fetch
    try:
        camp = Api(store=api.store)
        camp.store.update_settings({"api_key": "test_key_for_unit"})
        bal = camp.balance_teams(["모르는닉", "휴가중."])
        rows = (bal.get("members") or [])
        unknown = next((m for m in rows if m.get("name") == "모르는닉"), None)
        check("API — 순위표에 없는 닉네임도 RP 채움 (#21)",
              bool(unknown) and int(unknown.get("solo") or 0) == 2800, str(unknown))
    finally:
        nexon.fetch_member = real_fetch


# ------------------------------------------------------------------ UI
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
        page.wait_for_selector("#app-splash", state="hidden", timeout=15000)
        page.wait_for_timeout(600)
        if page.evaluate("() => { const m=document.getElementById('digest-mask'); return !!m && m.classList.contains('show'); }"):
            page.click("#digest-ack")
            page.wait_for_timeout(250)

        # 1) 딥 차콜 · 3색 테마
        theme = page.evaluate("""() => {
            const cs = getComputedStyle(document.documentElement);
            return {bg: cs.getPropertyValue('--bg').trim(), panel: cs.getPropertyValue('--panel').trim(),
                    line: cs.getPropertyValue('--line').trim(), ac: cs.getPropertyValue('--ac').trim(),
                    theme: document.documentElement.getAttribute('data-theme'),
                    appBg: getComputedStyle(document.querySelector('.app')).backgroundImage.slice(0, 80),
                    splashGlow: getComputedStyle(document.getElementById('app-splash')).backgroundImage.includes('245, 158, 11')};
        }""")
        check("배경 = #0B0D12", theme["bg"] == "#0B0D12", theme["bg"])
        check("패널 = #13161F", theme["panel"] == "#13161F", theme["panel"])
        check("헤어라인 = rgba(255,255,255,.06)", theme["line"] == "rgba(255,255,255,.06)", theme["line"])
        check("기본 테마 coral", theme["theme"] == "coral" and theme["ac"] == "#FF4655", f'{theme["theme"]} {theme["ac"]}')
        check("옛 남색·노랑 글로우 제거", not theme["splashGlow"], str(theme["splashGlow"]))
        page.screenshot(path=str(SHOTS / "v150-home-coral.png"))

        # 2) 테마 즉시 전환
        page.click("#settings-btn")
        page.wait_for_timeout(300)
        page.click('[data-set-tab="more"]')
        page.wait_for_timeout(200)
        page.click('[data-theme-key="ice"]')
        page.wait_for_timeout(300)
        ice = page.evaluate("() => ({theme: document.documentElement.getAttribute('data-theme'), ac: getComputedStyle(document.documentElement).getPropertyValue('--ac').trim()})")
        check("테마 ice 즉시 적용", ice["theme"] == "ice" and ice["ac"] == "#38BDF8", str(ice))
        page.click('[data-theme-key="gold"]')
        page.wait_for_timeout(250)
        gold = page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--ac').trim()")
        check("테마 gold 즉시 적용", gold == "#DFC07C", gold)
        # 3) 설정 팝업 크기 고정 (#27)
        sizes = {}
        for tab in ("api", "run", "cloud", "more"):
            page.click(f'[data-set-tab="{tab}"]')
            page.wait_for_timeout(180)
            box = page.evaluate("""() => { const m = document.querySelector('#settings-mask .modal');
                const r = m.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; }""")
            sizes[tab] = box
        check("설정 팝업 크기 4탭 고정 (#27)", len({tuple(v) for v in sizes.values()}) == 1, str(sizes))
        # 4) 화면 배율
        page.click('[data-set-tab="more"]')
        page.wait_for_timeout(150)
        page.click('[data-scale="115"]')
        page.wait_for_timeout(250)
        zoom = page.evaluate("() => ({ui: getComputedStyle(document.body).zoom || document.documentElement.style.getPropertyValue('--ui'), theme: document.documentElement.getAttribute('data-theme')})")
        check("화면 배율 115% 적용", zoom["ui"] in ("1.15", 1.15), str(zoom))
        page.click('[data-scale="100"]')
        page.wait_for_timeout(200)
        page.click('#settings-mask [data-close="settings-mask"]')
        page.wait_for_timeout(200)
        page.click('[data-theme-key="coral"]') if page.evaluate("() => false") else None

        # 5) 홈 구조
        home = page.evaluate("""() => {
            const q = (s) => document.querySelector(s);
            const ava = q('#home-ava');
            const ab = ava.getBoundingClientRect();
            return {hero: !!q('.hm-hero'), mid: !!q('.hm-mid'), bar: !!q('.hm-bar'),
                    recs: document.querySelectorAll('#home-recs-list .hm-rec').length,
                    games: document.querySelectorAll('#home-games .hm-game').length,
                    thumbs: document.querySelectorAll('#home-games .hm-game .mthumb img').length,
                    avaRound: Math.abs(ab.width - ab.height) < 1,
                    delta: document.querySelectorAll('#home-deltas .hm-delta').length,
                    quick: document.querySelectorAll('.hm-qa').length};
        }""")
        check("홈 — 히어로+2열+상태바", home["hero"] and home["mid"] and home["bar"], str(home))
        check("홈 — 추천 5종", home["recs"] == 5, str(home["recs"]))
        check("홈 — 최근 랭크 5판 (실데이터)", home["games"] == 5, str(home["games"]))
        check("홈 — 최근 5판 맵 썸네일", home["thumbs"] >= 4, str(home["thumbs"]))
        check("홈 — 아바타 정원", home["avaRound"], str(home["avaRound"]))
        check("홈 — 직전 대비 뱃지", home["delta"] >= 1, str(home["delta"]))
        check("홈 — 바로가기 타일 2개", home["quick"] == 2, str(home["quick"]))

        # 6) 사이드바 정렬 · ? 제거 · 토글 위치
        rail = page.evaluate("""() => {
            const cx = (el) => { const r = el.getBoundingClientRect(); return Math.round((r.left + r.width / 2) * 10) / 10; };
            const nav = [...document.querySelectorAll('.sidebar .nav-item')];
            const centers = [cx(document.querySelector('.rail-logo')), ...nav.map(cx), cx(document.getElementById('settings-btn'))];
            return {centers, spread: Math.max(...centers) - Math.min(...centers),
                    width: Math.round(document.getElementById('sidebar').getBoundingClientRect().width),
                    tips: [...document.querySelectorAll('.tip')].filter(e => getComputedStyle(e).display !== 'none').length,
                    sideSegs: document.querySelectorAll('.sidebar .seg-btn').length};
        }""")
        check("레일 60px", 58 <= rail["width"] <= 62, str(rail["width"]))
        check("아이콘 x축 일자 정렬 (#15)", rail["spread"] <= 1.5, str(rail["centers"]))
        check("? 아이콘 0개 (#7)", rail["tips"] == 0, str(rail["tips"]))
        check("사이드바에 트레이너/무기 토글 없음 (#16)", rail["sideSegs"] == 0, str(rail["sideSegs"]))

        page.click('[data-screen="dashboard"]')
        page.wait_for_timeout(350)
        dash = page.evaluate("""() => ({game: !!document.querySelector('#s-dashboard .seg-game'),
            weapon: !!document.querySelector('#s-dashboard .seg-weapon')})""")
        check("에임 대시보드에 KovaaK's/Aim Lab·스나/라플 (#16)", dash["game"] and dash["weapon"], str(dash))

        # 7) 시나리오 라이브러리 — 제외 토글 · 내 시나리오
        page.click('[data-screen="recs"]')
        page.wait_for_timeout(350)
        page.evaluate("""() => {
            const row = [...document.querySelectorAll('#rec-grid .lib-row')].find(r => r.querySelector('[data-exclude]'));
            if (row) row.querySelector('[data-exclude]').click();
        }""")
        page.wait_for_timeout(250)
        excl = page.evaluate("() => (window.__CALLS || []).filter(c => c[0] === 'set_scenario_excluded')")
        check("추천 제외 버튼 → API 호출", bool(excl), str(excl[-1:]))
        page.click("#rec-add")
        page.wait_for_timeout(250)
        page.fill("#cscen-name", "테스트 시나리오")
        page.fill("#cscen-play", "steam://run/824270")
        page.select_option("#cscen-area", "track")
        page.select_option("#cscen-diff", "고급")
        page.click("#cscen-save")
        page.wait_for_timeout(300)
        add = page.evaluate("() => (window.__CALLS || []).filter(c => c[0] === 'add_custom_scenario')")
        check("내 시나리오 추가 → API 호출", bool(add) and add[-1][1]["name"] == "테스트 시나리오", str(add[-1:])[:80])
        check("내 시나리오 영역·난이도 전달", bool(add) and add[-1][1]["area"] == "track" and add[-1][1]["diff"] == "고급", "")

        # 8) 맵 팝업 — 배너 + 유튜브 1개
        page.click('[data-screen="maps"]')
        page.wait_for_timeout(400)
        mapinfo = page.evaluate("""() => {
            const weak = document.querySelector('#weak-list .weak');
            return {thumbs: document.querySelectorAll('#weak-list .weak .weak-bg').length,
                    thumbs2: document.querySelectorAll('#weak-list .weak .weak-thumb').length,
                    tableThumbs: document.querySelectorAll('#map-table .map-cell .mthumb img').length};
        }""")
        check("약점 카드 맵 배경 이미지 + 작은 썸네일 제거", mapinfo["thumbs"] == 3 and mapinfo.get("thumbs2") == 0, str(mapinfo))
        check("맵별 분석 행 맵 이미지", mapinfo["tableThumbs"] >= 8, str(mapinfo["tableThumbs"]))
        page.evaluate("() => { const w = document.querySelector('#weak-list .weak'); if (w) w.click(); }")
        page.wait_for_timeout(300)
        guide = page.evaluate("""() => ({open: document.getElementById('guide-mask').classList.contains('show'),
            banner: !!document.querySelector('#guide-banner img'),
            ytButtons: document.querySelectorAll('#guide-mask #guide-yt-open').length
                      + document.querySelectorAll('#guide-mask a[href*="youtube"]').length})""")
        check("맵 공략 팝업 — 상단 배너", guide["open"] and guide["banner"], str(guide))
        check("맵 공략 팝업 — 영상 링크 1개 (#9)", guide["ytButtons"] == 1, str(guide["ytButtons"]))
        page.evaluate("() => document.getElementById('guide-mask').classList.remove('show')")

        # 9) 전술 도구 — 파티 맵 밴·픽 + 복사
        page.click('[data-screen="utils"]')
        page.wait_for_timeout(400)
        page.fill("#party-names", "휴가중., 0.1초의각")
        page.click("#party-run")
        page.wait_for_timeout(400)
        party = page.evaluate("""() => ({picks: document.querySelectorAll('#party-picks .party-row').length,
            bans: document.querySelectorAll('#party-bans .party-row').length,
            pct: [...document.querySelectorAll('#party-picks .ex')].map(e => e.textContent),
            thumbs: document.querySelectorAll('#party-picks .mthumb img').length,
            called: (window.__CALLS || []).filter(c => c[0] === 'party_map_plan').length})""")
        check("파티 맵 — 픽 3 / 밴 3", party["picks"] == 3 and party["bans"] == 3, str(party))
        check("파티 맵 — 예상 승률 표시", all("%" in p for p in party["pct"]), str(party["pct"]))
        check("파티 맵 — 맵 이미지", party["thumbs"] >= 2, str(party["thumbs"]))
        check("파티 맵 — 분석 버튼 → API", party["called"] >= 1, str(party["called"]))
        page.screenshot(path=str(SHOTS / "v150-utils.png"))

        # 내전 팀 → 복사
        page.click("#util-balance")
        page.wait_for_timeout(350)
        page.evaluate("""() => { document.querySelectorAll('#balance-members input[data-bal-name]').forEach(el => { el.checked = true; el.dispatchEvent(new Event('change', {bubbles: true})); }); }""")
        page.click("#balance-run")
        page.wait_for_timeout(400)
        page.click("#balance-copy")
        page.wait_for_timeout(300)
        copied = page.evaluate("() => window.__LAST_COPY || ((window.__CALLS || []).filter(c => c[0] === 'copy_text').map(c => c[1])[0] || '')")
        check("내전 팀 복사 → [A팀]/[B팀] 한 줄", copied.startswith("[A팀]") and "/ [B팀]" in copied, copied[:90])
        check("복사 버튼 이름 = 복사", page.evaluate("() => document.getElementById('balance-copy').textContent.trim()") == "복사", "")
        check("전원 최신화 버튼 있음 (#14)", page.evaluate("() => !!document.getElementById('balance-refresh-all')"), "")
        check("개별 삭제(✕) 버튼 (#13)", page.evaluate("() => document.querySelectorAll('#balance-members [data-bal-remove]').length") >= 1, "")
        page.evaluate("() => document.getElementById('balance-mask').classList.remove('show')")

        # 10) 스카우팅 — 약한/강한 맵 요약
        page.click("#util-scout-auto")
        page.wait_for_timeout(500)
        scout = page.evaluate("""() => ({cols: document.querySelectorAll('#scout-maps .sm-col').length,
            rows: document.querySelectorAll('#scout-maps .sm-row').length,
            copy: !!document.getElementById('scout-copy')})""")
        check("상대분석 — 약한/강한 맵 요약 (#12)", scout["cols"] == 2 and scout["rows"] >= 2, str(scout))
        check("카운터 전략 복사 버튼 삭제 (#25)", not scout["copy"], "")
        page.evaluate("() => document.getElementById('scout-mask').classList.remove('show')")

        # 11) 맞춤 .plo — 코박스/에임랩
        page.click('[data-screen="recs"]')
        page.wait_for_timeout(300)
        page.click("#personal-plo-btn")
        page.wait_for_timeout(400)
        plo = page.evaluate("""() => ({folder: !!document.getElementById('plo-folder'),
            help: !!document.getElementById('plo-help'),
            aim: !document.getElementById('plo-save-aimlab').hidden,
            save: !document.getElementById('plo-save').hidden,
            guide: document.getElementById('plo-guide').textContent.slice(0, 30)})""")
        check("코박스 — 폴더 열기 + 사용법 (#6)", plo["folder"] and plo["help"] and plo["save"] and not plo["aim"], str(plo))
        page.evaluate("() => document.getElementById('plo-mask').classList.remove('show')")

        # 12) 닫기 확인 · 트레이너 종료 토스트
        closed = page.evaluate("() => AL.confirmClose()")
        page.wait_for_timeout(250)
        check("OS 닫기 → 같은 확인 팝업 (#23)", closed and page.evaluate("() => document.getElementById('close-mask').classList.contains('show')"), "")
        page.evaluate("() => document.getElementById('close-mask').classList.remove('show')")
        page.evaluate("() => AL.trainerFinished({label: String.fromCharCode(75,111,118,97,97,75) + 's', message: '종료 감지 · 점수를 입력하면 반영됩니다'})")
        page.wait_for_timeout(250)
        tr = page.evaluate("""() => ({show: document.getElementById('app-toast').classList.contains('show'),
            text: document.getElementById('app-toast').innerText})""")
        check("트레이너 종료 감지 토스트", tr["show"] and "종료 감지" in tr["text"], str(tr)[:80])

        # 13) 병영수첩 실제 주소
        page.evaluate("() => { window.__CALLS = []; }")
        page.click('[data-screen="barracks"]')
        page.wait_for_timeout(400)
        page.click("#br-site")
        page.wait_for_timeout(300)
        calls = page.evaluate("() => (window.__CALLS || []).filter(c => c[0] === 'open_barracks_page')")
        check("내 병영수첩 → 닉네임까지 전달 (#8)", bool(calls) and calls[-1][2] == "휴가중.", str(calls[-1:]))
        page.screenshot(path=str(SHOTS / "v150-barracks.png"))

        # 14) 버튼 이름 간결성
        labels = page.evaluate("""() => [...document.querySelectorAll('button')].map(b => b.textContent.replace(/\\s+/g, ' ').trim()).filter(Boolean)""")
        long = [x for x in labels if len(x) > 16 and "7" not in x]
        check("긴 버튼 라벨 없음", len(long) <= 3, str(long[:5]))
        check("'방금 판 불러오기' 라벨", any("방금 판 불러오기" == x for x in labels), "")

        check("런타임 오류 없음", not errors, "; ".join(errors[:3]))
        browser.close()


def main() -> int:
    backend()
    backend_api()
    ui()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
