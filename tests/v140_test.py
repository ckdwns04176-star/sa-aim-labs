"""v1.4.0 검사 — 홈·접이식 사이드바·디자인 개편 + 신규 기능 7종.

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \
    python3 tests/v140_test.py
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


# --------------------------------------------------------------------- state
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
            "k": 12 + i % 7, "d": 9 + i % 5, "kd": 50.0 + (i % 8),
            "damage": 900 + i * 20,
            "players": [
                [nick, "팀", 12 + i % 7, 9 + i % 5, 900 + i * 20],
                ["0.1초의각", "팀", 10, 8, 1010], ["아군2", "팀", 6, 9, 520],
                ["상대1", "적", 10, 8, 810], ["상대2", "적", 7, 12, 640],
            ] if i < 8 else [],
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


def build_state(onboarded: bool = True) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="sa_v140_"))
    os.environ["SA_AL_DATA_DIR"] = str(tmp)
    from app.store import Store
    from app.api import Api

    store = Store(tmp / "data.json")
    store.update_settings({"nickname": "휴가중.", "api_key": "test_9542ea5ac663d2c0", "onboarded": onboarded})
    today = dt.date.today()
    store.add_record({
        "date": today.isoformat(), "game": "kovak", "weapon": "sniper",
        "scores": {"precision": 152, "wide": 128, "micro": 96, "track": 88, "switch": 141},
        "scenarios": [{"name": "VT 1w6ts Rasp Novice", "area": "precision", "score": 152},
                      {"name": "Wide Wall 3 Targets TE", "area": "wide", "score": 128}],
        "note": "",
    })
    for back in (1, 2, 3):
        store.add_record({
            "date": (today - dt.timedelta(days=back)).isoformat(), "game": "kovak", "weapon": "sniper",
            "scores": {"precision": 140 + back, "wide": 120, "micro": 90, "track": 80, "switch": 135},
            "scenarios": [{"name": "VT 1w6ts Rasp Novice", "area": "precision", "score": 140 + back}],
            "note": "",
        })
    barracks = sample_barracks()
    store.barracks_cache()[barracks["nickname"]] = barracks
    store.save()
    store.set_leaderboard([
        {"name": "휴가중.", "me": True, "ouid": barracks["ouid"], "clan": "리드", "weapon": "sniper",
         "solo": 3261, "party": 3435, "kd": 51.5, "dmg": 1009, "win": 47.0},
        {"name": "0.1초의각", "ouid": "ouid-friend-1", "clan": "리드", "weapon": "sniper",
         "solo": 3208, "party": 3520, "kd": 49.3, "dmg": 1041, "win": 50.4},
        {"name": "탄피수집가", "ouid": "ouid-friend-2", "clan": "리드", "weapon": "rifle",
         "solo": 3124, "party": 3311, "kd": 54.8, "dmg": 1182, "win": 53.1},
        {"name": "무빙샷장인", "ouid": "ouid-friend-3", "clan": "리드", "weapon": "rifle",
         "solo": 2940, "party": 3211, "kd": 57.4, "dmg": 1292, "win": 56.0},
        {"name": "헤더온리", "ouid": "ouid-friend-4", "clan": "리드", "weapon": "sniper",
         "solo": 2715, "party": 2788, "kd": 52.1, "dmg": 988, "win": 48.6},
        {"name": "연사금지", "ouid": "ouid-friend-5", "clan": "친구", "weapon": "rifle",
         "solo": 2486, "party": 2605, "kd": 58.2, "dmg": 1258, "win": 57.3},
        {"name": "어깨피기", "ouid": "ouid-friend-6", "clan": "친구", "weapon": "sniper",
         "solo": 2310, "party": 2445, "kd": 46.9, "dmg": 902, "win": 44.8},
    ])
    api = Api(store=store)
    return api._build_state()


SCOUT_ROWS = [
    {"name": "상대에이스", "clan": "적팀", "ouid": "o1", "solo": 3600, "party": 3500,
     "solo_tier": "불레전드", "party_tier": "불레전드", "kd": 54.2, "dmg": 1210, "win": 55.0,
     "games": 100, "threat": 84, "threat_label": "위협",
     "form": {"games": 12, "kd": 55.0, "dmg": 1240, "win": 58.0, "streak": 3},
     "weapon": {"key": "sniper", "label": "스나", "estimate": "추정 스나", "sniper_kd": 71, "assault_kd": 55, "gap": 16},
     "style": {"key": "ace", "label": "에이스·캐리형", "emoji": "⭐", "desc": "딜량 1,240 · K/D 55.0%"},
     "weak_maps": [{"map": "프로방스(심플)", "win": 30.0, "kd": 45.0, "games": 6, "weak": 60}],
     "team_mates": {"상대스나": {"games": 4, "wins": 3}}},
    {"name": "상대스나", "clan": "적팀", "ouid": "o2", "solo": 3400, "party": 3300,
     "solo_tier": "그랜드마스터 III", "party_tier": "그랜드마스터 III", "kd": 52.0, "dmg": 1100, "win": 51.0,
     "games": 100, "threat": 76, "threat_label": "위협",
     "form": {"games": 12, "kd": 53.0, "dmg": 1120, "win": 52.0, "streak": 1},
     "weapon": {"key": "sniper", "label": "스나", "estimate": "추정 스나", "sniper_kd": 67, "assault_kd": 58, "gap": 9},
     "style": {"key": "stable", "label": "생존·안정형", "emoji": "🛡", "desc": "판당 데스 3.1 · K/D 53.0%"},
     "weak_maps": [], "team_mates": {}},
    {"name": "상대라플", "ouid": "o3", "solo": 2600, "party": 2500,
     "solo_tier": "마스터 II", "party_tier": "마스터 I", "kd": 58.0, "dmg": 1320, "win": 57.0,
     "games": 100, "threat": 62, "threat_label": "경계",
     "form": {"games": 12, "kd": 60.0, "dmg": 1350, "win": 60.0, "streak": 2},
     "weapon": {"key": "rifle", "label": "라플", "estimate": "추정 라플", "sniper_kd": 60, "assault_kd": 63, "gap": -3},
     "style": {"key": "rush", "label": "고교전·돌격형", "emoji": "⚔️", "desc": "판당 킬 8.1 · 딜량 1,350"},
     "weak_maps": [], "team_mates": {}},
    {"name": "상대서폿", "ouid": "o4", "solo": 2200, "party": 2100,
     "solo_tier": "골드 III", "party_tier": "골드 III", "kd": 48.0, "dmg": 900, "win": 53.0,
     "games": 100, "threat": 44, "threat_label": "평범",
     "form": {"games": 12, "kd": 49.0, "dmg": 920, "win": 55.0, "streak": 0},
     "weapon": {"key": "rifle", "label": "라플", "estimate": "추정 라플", "sniper_kd": 55, "assault_kd": 57, "gap": -2},
     "style": {"key": "support", "label": "지원·팀플형", "emoji": "🤝", "desc": "어시 2.3/판"},
     "weak_maps": [], "team_mates": {}},
    {"name": "상대뉴비", "ouid": "o5", "solo": 1300, "party": 0,
     "solo_tier": "실버 I", "party_tier": "언랭크", "kd": 61.0, "dmg": 1150, "win": 60.0,
     "games": 100, "threat": 33, "threat_label": "무난",
     "form": {"games": 12, "kd": 62.0, "dmg": 1180, "win": 61.0, "streak": 4},
     "weapon": {"key": "rifle", "label": "라플", "estimate": "추정 라플", "sniper_kd": 64, "assault_kd": 66, "gap": -2},
     "style": {"key": "rush", "label": "고교전·돌격형", "emoji": "⚔️", "desc": "판당 킬 7.2"},
     "weak_maps": [], "team_mates": {}},
]


def mock_js(state: dict) -> str:
    return r"""
    window.__CALLS = [];
    window.__PY_STATE = __STATE_JSON__;
    const S = () => JSON.parse(JSON.stringify(window.__PY_STATE));
    const clone = (v) => JSON.parse(JSON.stringify(v));
    window.pywebview = {api: {
      async bootstrap() { return {ok: true, state: S()}; },
      async get_state() { return {ok: true, state: S()}; },
      async get_nvidia_state() {
        return {ok: true, utils: {supported: true, mode: null, alias: false, backups: [], points: [],
          modes: {rifle: true, sniper: true, stairfix: true}, missing: [], accel: false,
          stairfix: {ready: true, applied: false, value: '0x00020041', label: '기본값', target: '0x00401241'}}, state: S()};
      },
      async save_settings(patch) { window.__CALLS.push(['save_settings', patch]); return {ok: true, state: S()}; },
      async drag_window() { return {ok: true, supported: true}; },
      async start_window_drag() { return {ok: true, supported: true}; },
      async end_window_drag() { return {ok: true}; },
      async move_window() { return {ok: true}; },
      async minimize_window() { return {ok: true}; },
      async close_window() { window.__CALLS.push(['close_window']); return {ok: true}; },
      async fetch_barracks() { return {ok: true, state: S(), barracks: S().barracks, message: '최신화'}; },
      async fetch_leaderboard() { return {ok: true, state: S(), message: '갱신'}; },
      async save_training_session(p) { window.__CALLS.push(['save_training_session', p]); return {ok: true, state: S()}; },
      async launch_trainer(url) { window.__CALLS.push(['launch_trainer', url]); return {ok: true, url}; },
      async open_external(url) { window.__CALLS.push(['open_external', url]); return {ok: true}; },
      async ack_digest() { const st = S(); st.digest = {...(st.digest||{}), show: false, ack: true}; return {ok: true, state: st}; },
      async check_update() { return S().update || {ok: false, newer: false}; },
      async update_gate() { return {ok: true, gate: S().update_gate, update: S().update}; },
      async install_update() { window.__CALLS.push(['install_update']); return {ok: false, message: '테스트'}; },
      async tilt_state() { return {ok: true, tilt: {enabled: true, supported: false, alerts: [], game_running: null, last_check_at: null}}; },
      async set_tilt_watch() { return {ok: true, tilt: {alerts: []}, state: S()}; },
      async ack_tilt() { return {ok: true, alerts: []}; },
      async cloud_status() { return {ok: true, cloud: {}, state: S()}; },
      async cloud_signin() { return {ok: true, state: S(), cloud: {signed_in: true, email: 'me@x.com'}}; },
      async cloud_signup() { return {ok: true, state: S(), cloud: {signed_in: false, email: 'me@x.com'}}; },
      async cloud_pull() { window.__CALLS.push(['cloud_pull']); return {ok: true, state: S()}; },
      async complete_onboarding(p) { window.__CALLS.push(['complete_onboarding', p]); const st = S(); st.onboarding = {needed: false}; return {ok: true, state: st, message: '환영합니다'}; },
      async skip_onboarding() { window.__CALLS.push(['skip_onboarding']); const st = S(); st.onboarding = {needed: false}; return {ok: true, state: st}; },
      async scout_team(names) {
        window.__CALLS.push(['scout_team', names]);
        return {ok: true, rows: clone(window.__SCOUT), team: {}, calls: 12, message: '스카우팅 5/5명 완료 · 위협'};
      },
      async scout_last_match() {
        window.__CALLS.push(['scout_last_match']);
        return {ok: true, rows: clone(window.__SCOUT), team: {}, opponents: window.__SCOUT.map(r => r.name),
                map: '프로방스(심플)', calls: 8, message: '방금 끝난 판 상대 5명 분석'};
      },
      async duo_synergy(nick) {
        window.__CALLS.push(['duo_synergy', nick]);
        return {ok: true, available: true, friend: nick,
          together: {games: 6, wins: 4, win: 66.7, kd: 58.0, dmg: 1180},
          alone: {games: 14, wins: 6, win: 42.9, kd: 49.5, dmg: 980},
          versus: {games: 2, wins: 1, win: 50.0, kd: 51.0, dmg: 1000},
          delta: {win: 23.8, kd: 8.5, dmg: 200}, verdict: '찰떡 궁합', tone: 'good',
          label: nick + ' 님과 함께 뛴 판이 확실히 좋습니다 · 승률 +23.8%p',
          force: {base: 42, adjust: 16, power: 58, notes: ['함께 뛴 판 승률 66.7% (혼자 42.9%)']},
          pairs: [{date: '2026-11-23', time: '20:10', map: '프로방스(심플)', result: '승', k: 12, d: 8}],
          sample: 6, note: '최근 20판 중 6판을 함께 뛰었습니다 · 추가 API 호출 0건'};
      },
      async duo_rank() {
        window.__CALLS.push(['duo_rank']);
        return {ok: true, available: true, checked: 6, note: '최근 20판 기준', rows: [
          {friend: '0.1초의각', games: 6, alone_games: 14, win: 66.7, kd: 58.0, dmg: 1180,
           win_delta: 23.8, kd_delta: 8.5, dmg_delta: 200, score: 41.2, verdict: '찰떡 궁합', tone: 'good',
           force: {base: 42, adjust: 16, power: 58}},
          {friend: '탄피수집가', games: 4, alone_games: 16, win: 50.0, kd: 52.0, dmg: 1010,
           win_delta: 7.1, kd_delta: 2.5, dmg_delta: 30, score: 12.0, verdict: '무난', tone: 'mid',
           force: {base: 42, adjust: 8, power: 50}},
        ]};
      },
      async personal_playlist(g, w, save) {
        window.__CALLS.push(['personal_playlist', g, w, save]);
        const plan = {ok: true, names: ['Reflex Micro++ flick reload small', 'Wide Wall 3 Targets TE', '1wall 6targets small'],
          reasons: [
            {name: 'Reflex Micro++ flick reload small', area: '마이크로 보정', area_key: 'micro', value: 48, why: '마이크로 보정 48 · 가장 약한 영역'},
            {name: 'Wide Wall 3 Targets TE', area: '와이드 플릭', area_key: 'wide', value: 64, why: '와이드 플릭 64 · 약점 보강'},
            {name: '1wall 6targets small', area: '정밀 플릭', area_key: 'precision', value: 76, why: '정밀 플릭 76 · 유지 영역'}],
          coaching: ['가장 약한 영역은 마이크로 보정 48 — 1종을 배치했습니다.', '두 번째 약점 와이드 플릭 64 도 함께 채웁니다.',
                     '가장 강한 영역 정밀 플릭 76 은 유지용으로만 1종.', '무기 철학 · 스나 맞춤 · 롱 트래킹 제외'],
          focus: ['마이크로 보정', '와이드 플릭'], count: 3, philosophy: '스나 맞춤 · 롱 트래킹 제외', message: '맞춤 플레이리스트'};
        if (save) { plan.saved = true; plan.message = '📜 맞춤 플레이리스트 3종 저장'; }
        return plan;
      },
      async balance_candidates() {
        return {ok: true, members: S().leaderboard.map(m => ({...m, source: 'leaderboard'})),
                coplayers: [{name: '아군1', games: 6, kd: 52.0, dmg: 900, win: 50.0, source: 'match'}]};
      },
      async balance_teams(names) {
        window.__CALLS.push(['balance_teams', names]);
        return {ok: true, available: true, size: 3, pool: 6, diff: 2, verdict: '완전 균형',
          a: {count: 3, power: 63, total: 189, snipers: 1, rows: [
            {name: '휴가중.', rp: 3261, power: 70, role: 'sniper', style: '에이스·캐리형', style_emoji: '⭐', me: true},
            {name: '탄피수집가', rp: 3124, power: 63, role: 'sniper', style: '생존·안정형', style_emoji: '🛡'},
            {name: '어깨피기', rp: 2310, power: 55, role: 'rifle', style: '지원·팀플형', style_emoji: '🤝'}]},
          b: {count: 3, power: 62, total: 186, snipers: 1, rows: [
            {name: '0.1초의각', rp: 3208, power: 68, role: 'sniper', style: '에이스·캐리형', style_emoji: '⭐'},
            {name: '무빙샷장인', rp: 2940, power: 60, role: 'rifle', style: '고교전·돌격형', style_emoji: '⚔️'},
            {name: '연사금지', rp: 2486, power: 57, role: 'rifle', style: '고교전·돌격형', style_emoji: '⚔️'}]},
          note: '3 : 3 · 평균 실전 전력 차 2점 · 스나 A 1 / B 1'};
      },
      async open_barracks_page(ouid) { window.__CALLS.push(['open_barracks_page', ouid]); return {ok: true, url: 'https://barracks.sa.nexon.com/' + (ouid || 'me') + '/detail'}; },
      async open_clan_page(ouid) { window.__CALLS.push(['open_clan_page', ouid]); return {ok: true, url: 'https://barracks.sa.nexon.com/clan/' + (ouid || 'me') + '/home'}; },
      async save_member_memo() { return {ok: true, state: S()}; },
      async save_report_card() { return {ok: true, path: '/tmp/x.png', folder: '/tmp/reports', message: '저장'}; },
      async open_reports_folder() { return {ok: true, folder: '/tmp/reports'}; },
      async warmup_plan() { return {ok: true, recs: []}; },
      async export_playlist() { return {ok: true, message: '저장'}; },
      async import_kovak_scores() { return {ok: true, matched: [], message: '없음'}; },
      async exchange_nvidia() { return {ok: true}; },
      async tray_hide() { return {ok: true}; }
    }};
    window.__SCOUT = __SCOUT_JSON__;
    """.replace("__STATE_JSON__", json.dumps(state, ensure_ascii=False)).replace(
        "__SCOUT_JSON__", json.dumps(SCOUT_ROWS, ensure_ascii=False))


# --------------------------------------------------------------------- backend
def backend_checks() -> None:
    from app import paths, scoring, tilt, updates, v140

    check("APP_VERSION 1.4.0", paths.APP_VERSION == "1.4.0", paths.APP_VERSION)

    # 위협도: 티어가 압도적이어야 한다 (실버 60% < 불레전드 50%)
    silver = v140.threat_score(1300, 60, 1200, win=55, streak=3)
    elite = v140.threat_score(3600, 50, 1000, win=48, streak=0)
    check("위협도 — 불레전드 50% > 실버 60%", elite > silver + 20, f"elite={elite} silver={silver}")
    check("위협도 — 티어 가중 70%", 0 <= v140.threat_score(0, 0, 0) <= 2, str(v140.threat_score(0, 0, 0)))
    check("위협도 — 같은 티어에서 폼 좋은 에이스가 더 높음",
          v140.threat_score(2400, 60, 1300, win=58, streak=2) > v140.threat_score(2400, 45, 850, win=44, streak=0))

    # 역할 배정: 스나 K/D 우위 전원이 아니라 격차 상위 2명만
    rows = [{"name": n, "weapon": v140._weapon_tendency(s, None), "threat": 50,
             "threat_label": "평범", "solo": 2000, "solo_tier": "골드", "kd": 50, "win": 50, "dmg": 1000}
            for n, s in (("A", {"sniper": 70, "assault": 50}), ("B", {"sniper": 64, "assault": 58}),
                         ("C", {"sniper": 62, "assault": 61}), ("D", {"sniper": 55, "assault": 60}),
                         ("E", {"sniper": 58, "assault": 57}), ("F", {"sniper": 60, "assault": 59}))]
    v140.assign_roles(rows)
    snipers = [r["name"] for r in rows if r["weapon"]["key"] == "sniper"]
    check("팀 역할 — 격차 상위 2명만 추정 스나", snipers == ["A", "B"], str(snipers))
    check("팀 역할 — 나머지는 추정 라플", [r["weapon"]["key"] for r in rows[2:]] == ["rifle"] * 4, str(snipers))

    # 팀 시너지 · 실전 전력 (5:5 / 6:6)
    base_rows = [dict(r) for r in rows]
    team = v140.team_summary(base_rows)
    check("팀 시너지 — 5:5 정석 1~2스나", "1~2스나" in team["sniper_style"] or "2스나" in team["sniper_style"],
          team["sniper_style"])
    check("팀 시너지 — 실전 전력 = 기본 + 보정", "force" in team and team["force"]["power"] == max(
        0, min(100, team["force"]["base"] + team["force"]["adjust"])), str(team.get("force")))

    heavy = [dict(r) for r in rows]
    for r in heavy[:4]:
        r["weapon"] = {**r["weapon"], "key": "sniper"}
    v140.assign_roles(heavy)  # manual 없음 → 재배정되지만, 과다 스나 상황을 직접 만든다
    for r in heavy[:4]:
        r["weapon"]["key"] = "sniper"
    power_many = v140.team_power(heavy)
    v140.assign_roles(rows)
    power_normal = v140.team_power(rows)
    check("스나 과다 팀은 역할 보정 감점", power_many["adjust"] < power_normal["adjust"],
          f"{power_many['adjust']} vs {power_normal['adjust']}")

    styles = [{"name": "R1", "weapon": {"key": "sniper"}, "threat": 60, "win": 50, "dmg": 1200,
               "style": {"key": "rush"}, "team_mates": {}},
              {"name": "S1", "weapon": {"key": "rifle"}, "threat": 60, "win": 55, "dmg": 950,
               "style": {"key": "stable"}, "team_mates": {}}]
    combo = v140.team_power(styles)
    check("스타일 상성 — 돌격형+안정형 가산", any("돌격형" in n for n in combo["notes"]), str(combo["notes"]))

    # 랭크 컨디션
    wins = [{"result": "승", "k": 12, "d": 8, "damage": 1200} for _ in range(6)]
    losses = [{"result": "패", "k": 6, "d": 11, "damage": 700} for _ in range(6)]
    cond_hot = v140.rank_condition(wins)
    cond_bad = v140.rank_condition(losses)
    check("컨디션 — 연승이면 최상/좋음", cond_hot["verdict"] in ("최상", "좋음"), cond_hot["verdict"])
    check("컨디션 — 연패면 주의", cond_bad["verdict"] == "주의", cond_bad["verdict"])
    hist = v140.condition_history([], cond_hot, today="2026-11-24")
    check("컨디션 히스토리 저장", len(hist) == 1 and hist[0]["date"] == "2026-11-24", str(hist))

    # 듀오 시너지 (함께 vs 혼자)
    matches = []
    for i in range(10):
        together = i < 5
        players = [["휴가중.", "팀", 12, 8, 1200]]
        if together:
            players.append(["친구", "팀", 10, 9, 900])
        matches.append({"result": "승" if together else "패", "k": 12, "d": 8, "damage": 1200,
                        "date": "2026-11-23", "time": f"20:{i:02d}", "map": "프로방스(심플)",
                        "players": players})
    duo = v140.duo_synergy(matches, "친구")
    check("듀오 시너지 — 함께 뛴 판 vs 혼자", duo["available"] and duo["together"]["games"] == 5
          and duo["alone"]["games"] == 5, str(duo.get("together")))
    check("듀오 시너지 — 실전 전력(기본+보정)", duo["force"]["power"] >= duo["force"]["base"], str(duo["force"]))
    rank = v140.duo_ranking(matches, ["친구", "없는사람"])
    check("최고 궁합 순위 — 1위 계산", rank["available"] and rank["rows"][0]["friend"] == "친구", str(rank.get("rows")))

    # 내전 밸런서 (6~16명, 스나 균등)
    members = [{"name": f"P{i}", "solo": 1200 + i * 220, "party": None, "weapon": "sniper" if i % 3 == 0 else "rifle",
                "kd": 48 + i, "dmg": 900 + i * 30, "win": 45 + i % 8} for i in range(16)]
    bal6 = v140.balance_teams(members[:6])
    bal16 = v140.balance_teams(members)
    check("내전 밸런서 — 3:3", bal6["available"] and bal6["size"] == 3 and bal6["a"]["count"] == 3, str(bal6.get("note")))
    check("내전 밸런서 — 8:8 (16명)", bal16["available"] and bal16["size"] == 8 and bal16["pool"] == 16, str(bal16.get("note")))
    check("내전 밸런서 — 스나 균등 분배", abs(bal16["a"]["snipers"] - bal16["b"]["snipers"]) <= 1,
          f"A{bal16['a']['snipers']} B{bal16['b']['snipers']}")
    roles = {f"P{i}": "sniper" for i in range(6)}
    bal_roles = v140.balance_teams(members[:6], roles=roles)
    check("내전 밸런서 — 수동 역할 반영", bal_roles["a"]["snipers"] + bal_roles["b"]["snipers"] == 6,
          f"A{bal_roles['a']['snipers']} B{bal_roles['b']['snipers']}")

    # 병영수첩 URL · ouid 파싱
    url = "https://barracks.sa.nexon.com/711c04a69769cc4b6b782ea2aa3eb614/detail"
    check("병영수첩 URL → ouid 추출", v140.normalize_target(url) == "711c04a69769cc4b6b782ea2aa3eb614", v140.normalize_target(url))
    clan_url = "https://barracks.sa.nexon.com/clan/711c04a69769cc4b6b782ea2aa3eb614/home"
    check("클랜 URL → ouid 추출", v140.normalize_target(clan_url) == "711c04a69769cc4b6b782ea2aa3eb614", "")
    mixed = "상대1, 상대2\n" + url + "\t상대3"
    parsed = v140.split_targets(mixed, limit=16)
    check("닉네임·URL 섞어 붙여넣기 파싱", len(parsed) == 4 and "711c04a69769cc4b6b782ea2aa3eb614" in parsed, str(parsed))

    # 맞춤 .plo
    values = {"precision": 76, "wide": 64, "micro": 48, "track": 55, "switch": 70}
    plan = v140.personal_plan(values, [], game="kovak", weapon="sniper")
    check("맞춤 플레이리스트 — 약점 우선 이유", plan["count"] > 0 and plan["focus"][0] == "마이크로 보정",
          str(plan["focus"]))
    check("맞춤 플레이리스트 — 왜 뽑혔는지 설명", any("약점" in r["why"] for r in plan["reasons"]), str(plan["reasons"][0]["why"]))

    # 연패 브레이크 2단계 + 연승
    m3 = [{"result": "패", "k": 6, "d": 10, "damage": 800} for _ in range(3)]
    m5 = [{"result": "패", "k": 6, "d": 10, "damage": 800} for _ in range(5)]
    w3 = [{"result": "승", "k": 12, "d": 7, "damage": 1200} for _ in range(3)]
    a3 = tilt.evaluate(m3)
    a5 = tilt.evaluate(m5)
    aw = tilt.evaluate(w3)
    check("연패 3~4 → 주의(앰버)", a3 and a3["level"] == "주의" and a3["tone"] == "warn", str(a3 and a3["level"]))
    check("연패 5+ → 위험(레드)", a5 and a5["level"] == "위험" and a5["tone"] == "danger", str(a5 and a5["level"]))
    check("3연승 → 상승 알림", aw and aw["kind"] == "win" and aw["tone"] == "hot", str(aw and aw["title"]))
    calls = []
    watcher = tilt.TiltWatcher(lambda: m5, notify=lambda t, m, lv: calls.append((t, m, lv)),
                               process_check=lambda: True)
    watcher.check_once()
    check("트레이 알림 호출 — 위험은 error", calls and calls[0][2] == "error", str(calls[:1]))
    calls2 = []
    tilt.TiltWatcher(lambda: m3, notify=lambda t, m, lv: calls2.append(lv)).check_once()
    check("트레이 알림 호출 — 주의는 warning", calls2 == ["warning"], str(calls2))
    check("비Windows 에서 서든 프로세스 확인 안전", tilt.game_running() is False, "")

    # 업데이트 게이트
    newer = {"ok": True, "newer": True, "latest": "1.4.1", "download": "https://x/y.exe", "message": "새 버전"}
    g_block = updates.gate(newer, "1.4.0", frozen=True, env={})
    g_source = updates.gate(newer, "1.4.0", frozen=False, env={})
    g_off = updates.gate({"ok": False, "offline": True}, "1.4.0", frozen=True, env={})
    g_env = updates.gate(newer, "1.4.0", frozen=True, env={"SA_AL_NO_UPDATE_GATE": "1"})
    check("구버전 차단 — .exe + 새 버전이면 차단", g_block["blocked"], str(g_block))
    check("구버전 차단 — 소스 실행은 통과", not g_source["blocked"], str(g_source))
    check("구버전 차단 — 오프라인은 통과", not g_off["blocked"], str(g_off))
    check("구버전 차단 — 테스트 환경변수 통과", not g_env["blocked"], str(g_env))
    script = updates.swap_script(r"C:\Apps\SA_Aim_Labs.exe", r"C:\Temp\SA_Aim_Labs.exe")
    check("원클릭 교체 배치 — 교체·재시작", "move /y" in script and "start" in script and "tasklist" in script, script[:60])

    # 온보딩 상태
    st = build_state(onboarded=False)
    ob = st["onboarding"]
    check("온보딩 — 기존 사용자(닉네임/키 있음)는 건너뜀", ob["needed"] is False, str(ob))
    fresh_tmp = Path(tempfile.mkdtemp(prefix="sa_v140_fresh_"))
    os.environ["SA_AL_DATA_DIR"] = str(fresh_tmp)
    from app.store import Store
    from app.api import Api

    fresh = Api(store=Store(fresh_tmp / "data.json"))._build_state()
    check("온보딩 — 완전 첫 실행이면 표시", fresh["onboarding"]["needed"] is True, str(fresh["onboarding"]))
    os.environ["SA_AL_DATA_DIR"] = str(Path(tempfile.mkdtemp(prefix="sa_v140_back_")))


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
        page.wait_for_selector("#app-splash", state="hidden", timeout=12000)
        page.wait_for_timeout(700)
        if page.evaluate("() => { const m=document.getElementById('digest-mask'); return !!m && m.classList.contains('show'); }"):
            page.click("#digest-ack")
            page.wait_for_timeout(300)

        # 1. 홈이 첫 화면
        home = page.evaluate("""() => ({
            shown: !document.getElementById('s-home').hidden,
            dashHidden: document.getElementById('s-dashboard').hidden,
            name: (document.getElementById('home-name')||{}).textContent,
            score: (document.getElementById('home-score')||{}).textContent,
            kpi: (document.getElementById('kpi-score')||{}).textContent,
            buttons: ['home-start','home-barracks','home-utils'].filter(id => !!document.getElementById(id)).length,
            bar: (document.querySelector('.home-bar')||{}).innerText || ''})""")
        check("앱 시작 = 홈 화면", home["shown"] and home["dashHidden"], str(home["shown"]))
        num = lambda v: str(v or "").replace(",", "").replace(" ", "")
        check("홈 히어로 — 닉네임·통합 점수 일치(반올림 통일)", home["name"] == "휴가중." and num(home["score"]) == num(home["kpi"]),
              f"{home['name']} / {home['score']} vs {home['kpi']}")
        check("홈 원클릭 버튼 3개", home["buttons"] == 3, str(home["buttons"]))
        check("홈 하단 1줄 상태", all(k in home["bar"] for k in ("오늘 추천", "연속", "이번 주 목표")), home["bar"][:60].replace("\n", " "))
        page.screenshot(path=str(SHOTS / "v140-home.png"))

        # 2. 접이식 사이드바
        rail = page.evaluate("""() => {
            const sb = document.getElementById('sidebar');
            const main = document.querySelector('.main');
            return {w: Math.round(sb.getBoundingClientRect().width), mainLeft: Math.round(main.getBoundingClientRect().left),
                    labelOpacity: getComputedStyle(document.querySelector('.nav-item .ni-t')).opacity};
        }""")
        page.mouse.move(30, 320)
        page.wait_for_timeout(460)
        opened = page.evaluate("""() => {
            const sb = document.getElementById('sidebar');
            const main = document.querySelector('.main');
            const veil = document.getElementById('side-veil');
            return {w: Math.round(sb.getBoundingClientRect().width), mainLeft: Math.round(main.getBoundingClientRect().left),
                    labelOpacity: getComputedStyle(document.querySelector('.nav-item .ni-t')).opacity,
                    veil: getComputedStyle(veil).opacity,
                    blur: getComputedStyle(veil).backdropFilter || getComputedStyle(veil).webkitBackdropFilter,
                    shadow: getComputedStyle(sb).boxShadow.slice(0, 30)};
        }""")
        check("사이드바 레일 ≈ 60px", 58 <= rail["w"] <= 62, str(rail))
        check("메인 화면이 레일 옆까지 넓게", rail["mainLeft"] <= 61, str(rail["mainLeft"]))
        check("호버 시 메뉴 글씨 펼쳐짐", float(opened["labelOpacity"]) > 0.9 and opened["w"] > 190, str(opened))
        check("호버 = 오버레이 (메인을 밀지 않음)", opened["mainLeft"] == rail["mainLeft"], f"{rail['mainLeft']} → {opened['mainLeft']}")
        check("호버 시 뒤 배경 딤 + 블러", float(opened["veil"]) > 0.5 and "blur" in (opened["blur"] or ""), str(opened["blur"]))
        page.screenshot(path=str(SHOTS / "v140-sidebar.png"))
        page.mouse.move(700, 400)
        page.wait_for_timeout(400)

        # 3. 스플래시 전환 (zoom 클래스/홈 스케일 인)
        trans = page.evaluate("""() => ({zoom: !!document.getElementById('app-splash').classList.contains('zoom'),
            homeIn: getComputedStyle(document.getElementById('s-home')).animationName})""")
        check("스플래시 줌 전환 + 홈 스케일 인", trans["zoom"] or "homeIn" in (trans["homeIn"] or ""), str(trans))

        # 4. 에임 대시보드
        page.click('.nav-item[data-screen="dashboard"]')
        page.wait_for_timeout(500)
        dash = page.evaluate("""() => ({
            title: document.querySelector('#s-dashboard h1').textContent,
            recBtns: [...document.querySelectorAll('#s-dashboard .rec-foot button')].map(b => b.id),
            ladder: !!document.getElementById('ladder'),
            charts: [...document.querySelectorAll('#s-dashboard .mini .card-title')].map(t => t.textContent),
            dupToday: document.querySelectorAll('#chip-today').length,
            streakLabel: document.querySelector('#kpi-streak-card .kpi-label').textContent,
            kpiSub: [...document.querySelectorAll('#s-dashboard .kpi-foot')].filter(e => getComputedStyle(e).display !== 'none').length,
            cards: [...document.querySelectorAll('#s-dashboard .kpi')].map(c => Math.round(c.getBoundingClientRect().height)),
            greySubs: [...document.querySelectorAll('#s-dashboard .card-sub')].filter(e => getComputedStyle(e).display !== 'none').length,
            titlebar: document.getElementById('tb-screen-name').textContent})""")
        check("대시보드 이름 = 에임 대시보드", dash["title"] == "에임 대시보드", dash["title"])
        check("추천 카드 버튼 2개만", dash["recBtns"] == ["rec-more", "rec-save"], str(dash["recBtns"]))
        check("18단계 사다리 제거", dash["ladder"] is False, str(dash["ladder"]))
        check("차트 2개 넓게", len(dash["charts"]) == 2, str(dash["charts"]))
        check("'오늘 /5 완료' 중복 제거", dash["dupToday"] == 0, str(dash["dupToday"]))
        check("STREAK 표기 정상", "STREAK" in dash["streakLabel"] and "S TREAK" not in dash["streakLabel"], dash["streakLabel"])
        check("카드 회색 부연설명 제거(? 툴팁)", dash["greySubs"] == 0, str(dash["greySubs"]))
        check("KPI 카드 높이 균일", len(set(dash["cards"])) == 1, str(dash["cards"]))
        check("타이틀바 현재 화면명", dash["titlebar"] == "에임 대시보드", dash["titlebar"])
        page.screenshot(path=str(SHOTS / "v140-dashboard.png"))

        # 5. 시나리오 라이브러리 — PB 글자 제거
        page.click('.nav-item[data-screen="recs"]')
        page.wait_for_timeout(450)
        lib = page.evaluate("""() => ({pb: [...document.querySelectorAll('#rec-grid')].some(e => /PB/.test(e.innerText)),
            note: !!document.getElementById('rec-grid-note'),
            truncated: [...document.querySelectorAll('.lib-name')].filter(e => e.scrollWidth > e.clientWidth + 1 && e.getBoundingClientRect().height < 20).length,
            cards: document.querySelectorAll('#rec-grid .lib-row').length,
            btnCount: ['warmup-btn-2','kovak-import-head','rec-more-2','plo-btn','personal-plo-btn','score-input-btn','run-all-btn'].filter(id => !!document.getElementById(id)).length})""")
        check("라이브러리 PB 글자 제거", not lib["pb"], str(lib["pb"]))
        check("라이브러리 안내 문구 삭제", not lib["note"], "")
        check("라이브러리 상단 버튼 7개", lib["btnCount"] == 7, str(lib["btnCount"]))
        check("라이브러리 행 렌더", lib["cards"] > 20, str(lib["cards"]))
        page.screenshot(path=str(SHOTS / "v140-library.png"))

        # 6. 에임 기록·분석 — 잔디 12주 · 코멘트 줄바꿈
        page.click('.nav-item[data-screen="records"]')
        page.wait_for_timeout(450)
        rec = page.evaluate("""() => {
            const cols = document.querySelectorAll('#heatmap .heat-col').length;
            const cells = document.querySelectorAll('#heatmap .heat-cell').length;
            const note = document.querySelector('#impact-note');
            const r = note ? note.getBoundingClientRect() : null;
            return {cols, cells, noteH: r ? Math.round(r.height) : 0,
                    clipped: note ? note.scrollHeight > note.clientHeight + 2 : false,
                    cell: (() => { const c = document.querySelector('#heatmap .heat-cell'); return c ? Math.round(c.getBoundingClientRect().height) : 0; })(),
                    grouped: [...document.querySelectorAll('#records-table tbody tr')].length,
                    times: document.querySelectorAll('#records-table thead th').length};
        }""")
        check("잔디 12주 · 84칸", rec["cols"] == 12 and rec["cells"] == 84, str(rec))
        check("잔디 셀 크기 확대(≥11px)", rec["cell"] >= 11, str(rec["cell"]))
        check("훈련일/미훈련일 코멘트 안 잘림", not rec["clipped"] and rec["noteH"] >= 10, str(rec))
        page.evaluate("() => document.getElementById('app-shell').classList.remove('side-open')")
        page.mouse.move(600, 400)
        page.wait_for_timeout(250)
        cell_box = page.evaluate("""() => { const c = document.querySelectorAll('#heatmap .heat-cell')[20]; const r = c.getBoundingClientRect(); return {x: r.left + r.width/2, y: r.top + r.height/2}; }""")
        page.mouse.move(cell_box["x"], cell_box["y"])
        page.wait_for_timeout(300)
        tip = page.evaluate("() => { const t = document.getElementById('chart-tip'); return t && t.classList.contains('show') ? t.innerText : ''; }")
        check("잔디 호버 툴팁(날짜·점수·횟수)", "훈련" in (tip or ""), (tip or "").replace("\n", " ")[:50])
        page.screenshot(path=str(SHOTS / "v140-records.png"))

        # 7. 병영수첩 — 연승 배지 · 클랜 링크 · 계정 생성일 삭제
        page.click('.nav-item[data-screen="barracks"]')
        page.wait_for_timeout(500)
        br = page.evaluate("""() => ({banner: document.getElementById('br-banner').innerText.replace(/\\n/g,' | '),
            flow: !!document.querySelector('#br-banner .streak-badge'),
            selfLink: !!document.querySelector('#br-banner .bn-self'),
            goals: [...document.querySelectorAll('#goals .goal-hd span')].map(s => s.textContent),
            goalBadges: [...document.querySelectorAll('#goals .goal-hd em')].filter(e => getComputedStyle(e).display !== 'none').length,
            scout: !!document.getElementById('br-scout'), duo: !!document.getElementById('br-duo'), report: !!document.getElementById('br-report')})""")
        check("병영수첩 연승/연패 흐름 배지", br["flow"], br["banner"][:50])
        check("계정 생성일 표시 삭제", "가입" not in br["banner"] and "2011.05.02" not in br["banner"], br["banner"][:60])
        check("닉네임·클랜 링크 분리", br["selfLink"], "")
        check("목표 카드에 현재/목표 명확 표기", all(("현재" in g and "목표" in g) for g in br["goals"]), str(br["goals"]))
        check("목표 카드 빨간 부족 배지 제거", br["goalBadges"] == 0, str(br["goalBadges"]))
        check("병영수첩에 스카우팅·듀오·리포트 버튼", br["scout"] and br["duo"] and br["report"], "")
        page.screenshot(path=str(SHOTS / "v140-barracks.png"))

        # 클랜 클릭 → 클랜 홈페이지 URL
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#br-banner .bn-clan")
        page.wait_for_timeout(300)
        clan_calls = page.evaluate("() => window.__CALLS.filter(c => c[0] === 'open_clan_page')")
        check("클랜명 클릭 → 클랜 홈페이지", bool(clan_calls) and clan_calls[0][1] == state["barracks"]["ouid"], str(clan_calls))

        # 8. 맵·약점 분석
        page.click('.nav-item[data-screen="maps"]')
        page.wait_for_timeout(450)
        maps = page.evaluate("""() => ({rk: [...document.querySelectorAll('#map-table tbody tr .rk')].map(td => td.textContent.trim()),
            hint: document.querySelectorAll('.weak-hint').length})""")
        check("맵 표 # 번호가 1,2,3 순서", maps["rk"][:5] == ["1", "2", "3", "4", "5"], str(maps["rk"][:6]))
        check("약점 안내 박스 제거", maps["hint"] == 0, str(maps["hint"]))
        page.screenshot(path=str(SHOTS / "v140-maps.png"))

        # 9. 친구·클랜 순위표
        page.click('.nav-item[data-screen="leaderboard"]')
        page.wait_for_timeout(450)
        lb = page.evaluate("""() => ({backup: !!document.getElementById('lb-backup'),
            sort: document.querySelector('#lb-head [data-lbsort].on') ? document.querySelector('#lb-head [data-lbsort].on').dataset.lbsort : '',
            arrow: (document.querySelector('#lb-head [data-lbsort].on .lbar')||{}).textContent,
            order: [...document.querySelectorAll('#lb-rows .lb-row')].slice(0,3).map(r => r.dataset.lbname),
            rp: [...document.querySelectorAll('#lb-rows .lb-row')].slice(0,3).map(r => Number((r.querySelector('.lb-tier .tt span')||{}).textContent.replace(/[^0-9]/g,''))),
            first: [...document.querySelectorAll('#lb-rows .lb-row')].slice(0,4).map(r => r.dataset.lbname)})""")
        check("순위표 기록 백업 버튼 제거", lb["backup"] is False, "")
        check("기본 정렬 = 솔로 RP ▼", lb["sort"] == "solo" and lb["arrow"] == "▼", f"{lb['sort']} {lb['arrow']}")
        check("솔로 RP 내림차순", lb["rp"] == sorted(lb["rp"], reverse=True), str(lb["rp"]))
        page.evaluate("() => document.querySelector('#lb-head [data-lbsort=\"kd\"]').click()")
        page.wait_for_timeout(300)
        kd1 = page.evaluate("() => document.querySelector('#lb-head [data-lbsort=\"kd\"] .lbar').textContent")
        page.evaluate("() => document.querySelector('#lb-head [data-lbsort=\"kd\"]').click()")
        page.wait_for_timeout(300)
        kd2 = page.evaluate("() => document.querySelector('#lb-head [data-lbsort=\"kd\"] .lbar').textContent")
        check("헤더 첫 클릭 = 내림차순, 두 번째 = 오름차순", kd1 == "▼" and kd2 == "▲", f"{kd1} {kd2}")
        page.screenshot(path=str(SHOTS / "v140-leaderboard.png"))

        # 10. 18단계 티어표 — 스크롤 없이 18단계, 라벨 구분
        page.click('.nav-item[data-screen="tiers"]')
        page.wait_for_timeout(450)
        tiers = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#tier-table .tier-row:not(.hd)')];
            const wrap = document.querySelector('#s-tiers .tier-wrap');
            const last = rows[rows.length-1].getBoundingClientRect();
            const wb = wrap.getBoundingClientRect();
            const now = document.querySelector('#tier-table .now-tag');
            return {n: rows.length, overflow: Math.round(last.bottom - wb.bottom), scroll: wrap.scrollHeight > wrap.clientHeight + 1,
                    range: rows.slice(0,3).map(r => r.children[4].textContent),
                    tag: now ? now.textContent : '', tagLeft: now ? Math.round(now.getBoundingClientRect().left - rows.find(r=>r.classList.contains('now')).children[2].getBoundingClientRect().left) : 0};
        }""")
        check("18단계 모두 표시", tiers["n"] == 18, str(tiers["n"]))
        check("티어표 스크롤 없이 한눈에", (not tiers["scroll"]) and tiers["overflow"] <= 1, str(tiers))
        check("레전드 구간이 상위 %로 구분", len(set(tiers["range"])) == 3 and all("상위" in r for r in tiers["range"]), str(tiers["range"]))
        check("내 티어 태그 띄어쓰기 정상", tiers["tag"].startswith("◀ 내 티어") and tiers["tagLeft"] > 20, str(tiers["tag"]))
        page.screenshot(path=str(SHOTS / "v140-tiers.png"))

        # 11. 전술 도구 — 서브탭 2개
        page.click('.nav-item[data-screen="utils"]')
        page.wait_for_timeout(450)
        util = page.evaluate("""() => ({tabs: [...document.querySelectorAll('#util-tabs button')].map(b => b.textContent.trim()),
            tactics: !document.getElementById('util-pane-tactics').hidden,
            system: !document.getElementById('util-pane-system').hidden,
            scout: !!document.getElementById('util-scout-auto'),
            balance: !!document.getElementById('util-balance'),
            h1: document.querySelector('#s-utils h1').textContent})""")
        check("전술 도구 = 2개 서브탭", len(util["tabs"]) == 2 and "전술 분석" in util["tabs"][0] and "시스템" in util["tabs"][1], str(util["tabs"]))
        check("전술 분석 탭 — 스카우팅·밸런서", util["tactics"] and not util["system"] and util["scout"] and util["balance"], str(util))
        page.screenshot(path=str(SHOTS / "v140-utils-tactics.png"))
        page.click('[data-util-tab="system"]')
        page.wait_for_timeout(350)
        syspane = page.evaluate("""() => ({system: !document.getElementById('util-pane-system').hidden,
            apply: [...document.querySelectorAll('#s-utils [data-apply]')].map(b => b.dataset.apply),
            guide: document.getElementById('nv-points').getBoundingClientRect().height,
            foot: document.querySelectorAll('#s-utils .util-foot').length,
            descs: document.querySelectorAll('#s-utils .util-desc').length,
            accel: (document.getElementById('accel-detail')||{}).textContent,
            perfDup: (document.getElementById('perf-label')||{}).textContent + ' / ' + (document.getElementById('perf-state')||{}).textContent})""")
        check("시스템 최적화 탭 전환", syspane["system"], str(syspane["system"]))
        check("최적화 버튼 2개 유지", syspane["apply"] == ["rifle", "sniper"], str(syspane["apply"]))
        check("빈 복구 지점 박스 컴팩트(≤40px)", syspane["guide"] <= 40, str(syspane["guide"]))
        check("유틸 중복 하단 바 제거", syspane["foot"] == 0, str(syspane["foot"]))
        check("마우스 가속 문구 직관적", "미적용" in (syspane["accel"] or "") and "가속 켜짐" in (syspane["accel"] or ""), str(syspane["accel"]))
        page.screenshot(path=str(SHOTS / "v140-utils-system.png"))

        # 12. 설정 — 키 마스킹 · '소스 실행' 삭제 · 트레이 토글
        page.click("#settings-btn")
        page.wait_for_timeout(450)
        setg = page.evaluate("""() => ({key: (document.getElementById('set-key')||{}).value || '',
            meta: (document.getElementById('set-meta-line')||{}).textContent || '',
            body: document.getElementById('settings-mask').innerText,
            tilt: !!document.querySelector('[data-switch="tilt_watch"]'),
            tiltCls: (document.querySelector('[data-switch="tilt_watch"]')||{}).className || ''})""")
        check("설정 — API 키 마스킹 표시", "•" in setg["key"], str(setg["key"][:16]))
        check("설정 — '소스 실행' 문구 삭제", "소스 실행" not in setg["body"] and "소스 실행" not in setg["meta"], setg["meta"])
        check("설정 — 연승·연패 토글 스위치", setg["tilt"] and "switch" in setg["tiltCls"], str(setg["tiltCls"]))
        page.screenshot(path=str(SHOTS / "v140-settings.png"))
        page.evaluate("() => document.querySelector('#settings-mask').classList.remove('show')")
        page.wait_for_timeout(200)

        # 13. 오늘 훈련 시작 → 바로 실행하지 않고 목록 팝업
        page.evaluate("() => { window.__CALLS = []; }")
        page.click('.nav-item[data-screen="home"]')
        page.wait_for_timeout(300)
        page.click("#home-start")
        page.wait_for_timeout(400)
        today = page.evaluate("""() => ({open: document.getElementById('today-mask').classList.contains('show'),
            rows: document.querySelectorAll('#today-list [data-today-run]').length,
            calls: window.__CALLS.map(c => c[0])})""")
        check("홈 훈련 시작 → 목록 팝업", today["open"] and today["rows"] >= 5 and "launch_trainer" not in today["calls"], str(today))
        page.screenshot(path=str(SHOTS / "v140-today.png"))
        page.click("#today-list [data-today-run]")
        page.wait_for_timeout(300)
        launched = page.evaluate("() => window.__CALLS.filter(c => c[0] === 'launch_trainer').length")
        check("팝업에서 개별 실행", launched == 1, str(launched))

        # 14. 스카우팅 — 자동 불러오기 · 역할 전환 · 위협/시너지
        page.click('.nav-item[data-screen="utils"]')
        page.wait_for_timeout(300)
        page.evaluate("() => document.querySelector('[data-util-tab=\"tactics\"]').click()")
        page.wait_for_timeout(250)
        page.click("#util-scout-auto")
        page.wait_for_timeout(700)
        scout = page.evaluate("""() => ({open: document.getElementById('scout-mask').classList.contains('show'),
            rows: document.querySelectorAll('#scout-table .scout-row:not(.hd)').length,
            sum: (document.getElementById('scout-sum')||{}).innerText || '',
            style: document.querySelectorAll('#scout-table [title]').length,
            roles: [...document.querySelectorAll('#scout-table [data-role]')].map(b => b.textContent),
            threats: [...document.querySelectorAll('#scout-table .th')].map(b => b.textContent),
            kd: (document.querySelector('#scout-table .scout-row:not(.hd)').innerText || '').replace(/\\n/g, ' | ')})""")
        check("스카우팅 — 방금 끝난 판 상대 자동 불러오기", scout["open"] and scout["rows"] == 5, str(scout["rows"]))
        check("스카우팅 — 기본 티어력/보정/실전 전력 표시", all(k in scout["sum"] for k in ("기본 티어력", "조합 시너지 보정", "실전 전력")), scout["sum"].replace("\n", " ")[:80])
        check("스카우팅 — 무기별 K/D 표시", "스나 K/D" in scout["kd"] and "라플 K/D" in scout["kd"], scout["kd"][:80])
        check("스카우팅 — 위협도 순위", "위협" in "".join(scout["threats"]), str(scout["threats"][:2]))
        page.screenshot(path=str(SHOTS / "v140-scout.png"))
        before = page.evaluate("() => (document.getElementById('scout-sum')||{}).innerText")
        page.click("#scout-table [data-role]")
        page.wait_for_timeout(350)
        after = page.evaluate("() => (document.getElementById('scout-sum')||{}).innerText")
        check("스카우팅 — 역할 버튼으로 스나수 재계산", before != after, f"{before[:30]} → {after[:30]}")
        page.click("#scout-mask [data-close]")
        page.wait_for_timeout(200)

        # 15. 듀오 시너지 — 궁합 순위
        page.click("#util-duo")
        page.wait_for_timeout(700)
        duo = page.evaluate("""() => ({open: document.getElementById('duo-mask').classList.contains('show'),
            rank: document.querySelectorAll('#duo-rank > div').length,
            rankText: (document.getElementById('duo-rank')||{}).innerText || '',
            body: (document.getElementById('duo-body')||{}).innerText || ''})""")
        check("듀오 — 최고 궁합 순위 표시", duo["open"] and duo["rank"] >= 2, str(duo["rank"]))
        page.click("#duo-run")
        page.wait_for_timeout(600)
        duo2 = page.evaluate("() => (document.getElementById('duo-body')||{}).innerText || ''")
        check("듀오 — 함께 vs 혼자 비교", "함께 뛴 판" in duo2 and "혼자 뛴 판" in duo2, duo2.replace("\n", " ")[:70])
        check("듀오 — 실전 전력(기본+케미)", "기본 전력" in duo2 and "실전 전력" in duo2, duo2.replace("\n", " ")[:90])
        page.screenshot(path=str(SHOTS / "v140-duo.png"))
        page.click("#duo-mask [data-close]")
        page.wait_for_timeout(200)

        # 16. 내전 팀 자동 짜기
        page.click("#util-balance")
        page.wait_for_timeout(700)
        bal = page.evaluate("""() => ({open: document.getElementById('balance-mask').classList.contains('show'),
            cands: document.querySelectorAll('#balance-members input[data-bal-name]').length,
            roles: document.querySelectorAll('#balance-members [data-bal-role]').length,
            paste: !!document.getElementById('balance-paste')})""")
        check("밸런서 — 순위표+동반 후보 체크박스", bal["open"] and bal["cands"] >= 7, str(bal))
        check("밸런서 — 역할 토글·붙여넣기", bal["roles"] >= 7 and bal["paste"], str(bal["roles"]))
        page.evaluate("""() => { document.querySelectorAll('#balance-members input[data-bal-name]').forEach((el, i) => { el.checked = i < 8; }); }""")
        page.click("#balance-run")
        page.wait_for_timeout(600)
        result = page.evaluate("() => (document.getElementById('balance-result')||{}).innerText || ''")
        check("밸런서 — A/B 자동 분배", "A팀" in result and "B팀" in result, result.replace("\n", " ")[:70])
        page.screenshot(path=str(SHOTS / "v140-balance.png"))
        page.click("#balance-mask [data-close]")
        page.wait_for_timeout(200)

        # 17. 맞춤 .plo
        page.click('.nav-item[data-screen="recs"]')
        page.wait_for_timeout(350)
        page.click("#personal-plo-btn")
        page.wait_for_timeout(600)
        plo = page.evaluate("""() => ({open: document.getElementById('plo-mask').classList.contains('show'),
            coaching: document.querySelectorAll('#plo-coaching .coach-row').length,
            rows: document.querySelectorAll('#plo-list > div').length,
            why: (document.getElementById('plo-list')||{}).innerText || ''})""")
        check("맞춤 .plo — 코칭 + 왜 뽑혔는지", plo["open"] and plo["coaching"] >= 3 and plo["rows"] >= 3 and "약점" in plo["why"], str(plo["rows"]))
        page.screenshot(path=str(SHOTS / "v140-personal-plo.png"))
        page.click("#plo-mask [data-close]")
        page.wait_for_timeout(200)

        # 18. 온보딩 (첫 실행)
        fresh = build_state(onboarded=False)
        fresh["settings"]["nickname"] = ""
        fresh["settings"]["api_key_set"] = False
        fresh["settings"]["api_key_masked"] = ""
        fresh["nexon"] = {"connected": False, "nickname": ""}
        fresh["onboarding"] = {"needed": True, "weapon": "sniper", "game": "kovak"}
        page2 = browser.new_page(viewport={"width": 1200, "height": 750})
        page2.add_init_script(mock_js(fresh))
        page2.goto(url)
        page2.wait_for_selector("#app-splash", state="hidden", timeout=12000)
        page2.wait_for_timeout(900)
        onb = page2.evaluate("""() => ({open: document.getElementById('onb-mask').classList.contains('show'),
            step0: !document.getElementById('onb-step-0').hidden,
            local: !!document.getElementById('onb-local'), login: !!document.getElementById('onb-login')})""")
        check("첫 실행 — 클라우드 로그인/로컬 선택", onb["open"] and onb["step0"] and onb["local"] and onb["login"], str(onb))
        page2.screenshot(path=str(SHOTS / "v140-onboarding.png"))
        page2.click("#onb-local")
        page2.wait_for_timeout(300)
        step1 = page2.evaluate("() => ({s1: !document.getElementById('onb-step-1').hidden, nick: !!document.getElementById('onb-nick')})")
        check("로그인 없이 → 닉네임/키 단계", step1["s1"] and step1["nick"], str(step1))
        page2.fill("#onb-nick", "테스트유저")
        page2.click("#onb-next")
        page2.wait_for_timeout(250)
        step2 = page2.evaluate("() => (!document.getElementById('onb-step-2').hidden)")
        page2.click("#onb-next")
        page2.wait_for_timeout(500)
        done = page2.evaluate("() => window.__CALLS.filter(c => c[0] === 'complete_onboarding').length")
        check("2단계 완료 → 온보딩 저장", step2 and done == 1, f"step2={step2} calls={done}")

        check("UI 런타임 오류 없음", not errors, "; ".join(errors[:3]))
        browser.close()


def main() -> int:
    backend_checks()
    ui()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
