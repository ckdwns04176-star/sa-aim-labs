"""v2.0.0 테스트 공용 fixture — 실제 Api 로 만든 state + 브리지 mock.

`build_state()` 는 임시 폴더에 저장소를 만들고 서든 전적(병영수첩) 캐시를
채운 뒤 ``Api._build_state()`` 를 그대로 돌려준다.  그래서 UI 테스트가 보는
state 는 앱이 실제로 내보내는 모양과 같다.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""))


def report() -> int:
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} 통과")
    for name, _ok, detail in bad:
        print("  FAIL", name, detail)
    return 0 if not bad else 1


def sample_matches(nick: str = "스나이퍼01", count: int = 20) -> list[dict]:
    maps = ["이탈리아-폭파", "프로방스(심플)", "크로스포트(심플)", "시티캣-심플", "올드타운(심플)",
            "제5보급-심플", "드래곤로드(심플)", "팰리스-심플", "지하철-심플", "듀오 릭"]
    rows = []
    today = dt.date.today()
    for i in range(count):
        win = i % 3 != 0
        rows.append({
            "match_id": f"m{i:04d}",
            "date": (today - dt.timedelta(days=i // 3)).isoformat(),
            "time": f"{20 + (i % 4):02d}:{10 + i % 50:02d}",
            "map": maps[i % len(maps)],
            "type": "솔로" if i % 4 else "파티",
            "result": "승" if win else "패",
            "k": 10 + i % 8, "d": 8 + i % 6, "kd": round((10 + i % 8) / (18 + i % 8) * 100, 1),
            "damage": 900 + i * 17,
            "players": [
                [nick, "팀", 10 + i % 8, 8 + i % 6, 900 + i * 17],
                ["0.1초의각", "팀", 9, 9, 980],
                ["탄피수집가", "팀", 11, 7, 1100],
                ["상대에이스", "적", 13, 8, 1210],
                ["상대스나", "적", 8, 10, 860],
                ["상대라플", "적", 9, 9, 940],
            ],
        })
    return rows


def sample_barracks(nick: str = "스나이퍼01") -> dict:
    maps = [
        ("이탈리아-폭파", 6, 33.3, 43.5, 836.8), ("프로방스(심플)", 12, 25.0, 51.0, 1177.8),
        ("크로스포트(심플)", 5, 40.0, 41.2, 873.8), ("시티캣-심플", 10, 40.0, 50.5, 995.1),
        ("올드타운(심플)", 8, 37.5, 56.1, 1071.9), ("팰리스-심플", 10, 50.0, 48.8, 857.2),
        ("지하철-심플", 9, 55.6, 55.2, 951.2), ("제5보급-심플", 11, 63.6, 49.5, 765.9),
        ("드래곤로드(심플)", 11, 54.5, 51.3, 1295.8), ("듀오 릭", 7, 57.1, 58.2, 984.4),
    ]
    return {
        "nickname": nick, "ouid": "711c04a69769cc4b6b782ea2aa3eb614",
        "basic": {"clan": "리드", "created": "2011.05.02", "manner": "3"},
        "tier": {"solo": {"name": "그랜드마스터 II", "rp": 3261},
                 "party": {"name": "그랜드마스터 III", "rp": 3435}},
        "stats": {"games": 100, "kills": 900, "deaths": 800, "wins": 47,
                  "win": 47.0, "kd": 51.5, "dmg": 1009},
        "recent": {"sniper": 60, "assault": 52},
        "maps": [{"rk": i + 1, "map": m[0], "games": m[1], "win": m[2], "kd": m[3],
                  "dmg": m[4], "weak": round(100 - m[2] * .5 - m[3] * .3 - m[4] / 200, 1)}
                 for i, m in enumerate(maps)],
        "matches": sample_matches(nick),
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def build_state(nick: str = "스나이퍼01", signed_in: bool = True, plan: str = "free") -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="sa_v2_"))
    os.environ["SA_AL_DATA_DIR"] = str(tmp)
    from app.store import Store
    from app.api import Api

    store = Store(tmp / "data.json")
    store.update_settings({"nickname": nick, "onboarded": True})
    barracks = sample_barracks(nick)
    store.barracks_cache()[nick] = barracks
    store.add_barracks_snapshot(barracks)
    older = dict(barracks)
    older = {**barracks,
             "stats": {**barracks["stats"], "win": 45.8, "kd": 50.2, "dmg": 980},
             "tier": {"solo": {"name": "그랜드마스터 II", "rp": 3218},
                      "party": {"name": "그랜드마스터 III", "rp": 3435}},
             "updated_at": (dt.datetime.now() - dt.timedelta(days=1)).astimezone().isoformat(timespec="seconds")}
    store.barracks_history().insert(0, {
        "date": (dt.date.today() - dt.timedelta(days=1)).isoformat(),
        "at": older["updated_at"], "nickname": nick, "stats": older["stats"],
        "tier": older["tier"], "basic": {"clan": "리드"},
    })
    store.merge_play_days(barracks["matches"])
    store.save()
    store.set_leaderboard([
        {"name": nick, "me": True, "ouid": barracks["ouid"], "clan": "리드", "weapon": "sniper",
         "solo": 3261, "party": 3435, "kd": 51.5, "dmg": 1009, "win": 47.0},
        {"name": "0.1초의각", "ouid": "ouid-friend-1", "clan": "리드", "weapon": "sniper",
         "solo": 3208, "party": 3520, "kd": 49.3, "dmg": 1041, "win": 50.4},
        {"name": "탄피수집가", "ouid": "ouid-friend-2", "clan": "리드", "weapon": "rifle",
         "solo": 3124, "party": 3311, "kd": 54.8, "dmg": 1182, "win": 53.1},
        {"name": "무빙샷장인", "ouid": "ouid-friend-3", "clan": "리드", "weapon": "rifle",
         "solo": 2940, "party": 3211, "kd": 57.4, "dmg": 1292, "win": 56.0},
    ])
    if signed_in:
        store.cloud().update({"email": "tester@example.com", "user_id": "u-1",
                              "access_token": "token", "refresh_token": "refresh"})
        store.set_account(plan, "2027-01-01T00:00:00+00:00" if plan == "pro" else None,
                          "tester@example.com")
        store.save()
    api = Api(store=store)
    state = api._build_state()
    state["_store_path"] = str(store.path)
    return state


def mock_js(state: dict) -> str:
    """pywebview.api 를 흉내내는 init script — 화면이 실제 state 로 그려지게 한다."""
    import json

    return r"""
    window.__CALLS = [];
    window.__PY_STATE = __STATE__;
    const S = () => JSON.parse(JSON.stringify(window.__PY_STATE));
    const clone = (v) => JSON.parse(JSON.stringify(v));
    window.pywebview = {api: {
      async bootstrap() { return {ok: true, state: S(), log_file: 'C:/log/app.log'}; },
      async get_state() { return {ok: true, state: S()}; },
      async bridge_ping() { return {ok: true, pong: true, version: '2.0.0'}; },
      async get_nvidia_state() { const st=S(); return {ok:true, utils: st.utils, state: st}; },
      async save_settings(p) { window.__CALLS.push(['save_settings', p]); return {ok:true, state:S()}; },
      async drag_window() { return {ok:true, supported:true}; },
      async start_window_drag() { return {ok:true, supported:true}; },
      async end_window_drag() { return {ok:true}; },
      async move_window() { return {ok:true}; },
      async minimize_window() { return {ok:true}; },
      async close_window() { window.__CALLS.push(['close_window']); return {ok:true}; },
      async fetch_barracks() { return {ok:true, state:S(), message:'최신화'}; },
      async fetch_leaderboard() { return {ok:true, state:S(), message:'갱신'}; },
      async refresh_leaderboard_names() { return {ok:true, renamed:[], state:S()}; },
      async recent_opponents() { window.__CALLS.push(['recent_opponents']);
        return {ok:true, opponents:['상대에이스','상대스나','상대라플'], stale:false,
                match:{map:'이탈리아-폭파', result:'승', at:'2026-11-27 20:10'},
                rows:[{name:'상대에이스'},{name:'상대스나'},{name:'상대라플'}],
                fetched_at:'2026-11-27T20:12:00', message:'상대 3명'}; },
      async scout_team() { window.__CALLS.push(['scout_team']); return {ok:true, rows:[
        {name:'상대에이스', clan:'리드', solo:3610, solo_tier:'별레전드', party:3400, party_tier:'그랜드마스터 III',
         solo_stats:{tier:'별레전드', rp:3610, win:57.1, kd:61.2, games:21},
         party_stats:{tier:'그랜드마스터 III', rp:3400, win:48.3, kd:55.0, games:12},
         weapon:{key:'sniper', label:'스나', estimate:'추정 스나', sniper_kd:63.1, assault_kd:44.2},
         kd:61.2, dmg:null, win:53.3, games:33, threat:88, threat_label:'위협',
         form:{games:33, kd:61.2, streak:3}, style:{label:'돌격형', emoji:'⚔️', desc:'판당 킬 10.2 · 데스 7.9'}, tips:[]},
        {name:'상대스나', clan:'-', solo:3240, solo_tier:'그랜드마스터 II', party:null, party_tier:'언랭크',
         solo_stats:{tier:'그랜드마스터 II', rp:3240, win:44.0, kd:52.9, games:25},
         party_stats:{tier:'언랭크', rp:0, win:null, kd:null, games:0},
         weapon:{key:'rifle', label:'라플', estimate:'추정 라플', sniper_kd:41.0, assault_kd:56.0},
         kd:52.9, dmg:null, win:44.0, games:25, threat:64, threat_label:'경계',
         form:{games:25, kd:52.9, streak:0}, style:{label:'안정형', emoji:'🛡️', desc:'판당 데스 4.1 · K/D 52.9%'}, tips:[]}],
        team:{label:'위협 높음', threat:84, top:'상대에이스', tips:['가장 위협적인 상대는 상대에이스 (위협 88) — 첫 교전을 피하세요',
              '핵심 스나수 — 상대에이스(스나 K/D 63.1%). 이 둘의 각을 동시에 주지 말고 연막으로 순서를 나눠 끊으세요.'],
              avg_kd:57.1, count:2}, calls:11, message:'2/2명'}; },
      async scout_last_match() { return {ok:true, rows:[], team:{}, opponents:['상대에이스'], message:'방금 판 1명'}; },
      async duo_between(a,b) { window.__CALLS.push(['duo_between',a,b]); return {ok:true,
        a:{name:a, win:58.3, kd:54.1, overall:{win:50.0}}, b:{name:b, win:58.3, kd:56.2, overall:{win:52.0}},
        shared:{games:12, wins:7, win:58.3, kd:55.2}, delta:{win:7.3, kd:3.5}, score:13.4,
        verdict:'찰떡 궁합', tone:'good', label:'둘이 함께 뛴 12판 승률 58.3% · 평소보다 +7.3%p', sample:12,
        note:'같은 match_id 12판', pairs:[{date:'2026-11-26',map:'이탈리아-폭파',result:'승',a:{k:12,d:8},b:{k:9,d:9}}],
        message:'찰떡 궁합'}; },
      async balance_candidates() { return {ok:true, members:[{name:'0.1초의각',solo:3208,weapon:'sniper'}], coplayers:[]}; },
      async balance_teams() { return {ok:true, available:true, teams:{a:[{name:'0.1초의각'}],b:[{name:'탄피수집가'}]}, roles:{}, label:'균형', message:'균형'}; },
      async party_map_plan() { return {ok:true, available:true, picks:[{map:'제3보급창고',expected:61,games:9,image:''}], bans:[{map:'이탈리아-폭파',expected:41,games:8,image:''}], headline:'픽 제3보급창고', message:'픽 제3보급창고'}; },
      async duo_rank() { return {ok:true, rows:[], message:'표본 없음'}; },
      async duo_synergy(n) { return {ok:true, available:true, friend:n, together:{games:5,win:60,kd:53,dmg:1100}, alone:{games:15,win:46,kd:50,dmg:980}, delta:{win:14,kd:3,dmg:120}, verdict:'찰떡 궁합', tone:'good', label:n+'와 함께 좋습니다', pairs:[], force:{power:62}, sample:5, message:''}; },
      async connect_nickname() { return {ok:true, state:S(), nickname:'스나이퍼01', message:'연결'}; },
      async search_user() { return {ok:true, found:true, user:{name:'스나이퍼01'}}; },
      async lookup_barracks() { return {ok:true, barracks:S().barracks, peek:true}; },
      async add_leaderboard_member() { return {ok:true, state:S(), message:'추가'}; },
      async remove_leaderboard_member() { return {ok:true, state:S(), message:'삭제'}; },
      async save_member_memo() { return {ok:true, state:S(), memos:{}, message:'메모'}; },
      async ack_digest() { const st=S(); st.digest={...(st.digest||{}), show:false, ack:true}; return {ok:true, state:st}; },
      async get_state_after(p) { return {ok:true}; },
      async open_data_folder() { return {ok:true}; },
      async open_barracks_page() { return {ok:true}; },
      async open_clan_page() { return {ok:true}; },
      async open_external() { return {ok:true}; },
      async export_backup() { return {ok:true, message:'백업', path:'C:/backup.json'}; },
      async import_backup() { return {ok:true, state:S()}; },
      async auth_login() { window.__CALLS.push(['auth_login']); const st=S(); st.account={...st.account, signed_in:true, email:'tester@example.com'}; return {ok:true, state:st, message:'로그인되었습니다'}; },
      async auth_signup() { return {ok:true, needs_confirmation:true, message:'가입 확인 메일을 보냈습니다'}; },
      async auth_recover() { return {ok:true, message:'비밀번호 재설정 메일을 보냈습니다'}; },
      async auth_signout() { return {ok:true, state:S(), message:'로그아웃되었습니다'}; },
      async account_state() { return {ok:true, account:S().account, quota:{}}; },
      async cloud_status() { return {ok:true, cloud:S().cloud, state:S()}; },
      async cloud_signin() { return {ok:true, state:S()}; },
      async cloud_push() { return {ok:true, state:S(), message:'올림'}; },
      async cloud_pull() { return {ok:true, state:S(), message:'내려받음'}; },
      async cloud_signout() { return {ok:true, state:S()}; },
      async complete_onboarding() { window.__CALLS.push(['complete_onboarding']); const st=S(); st.onboarding={needed:false}; return {ok:true, state:st}; },
      async skip_onboarding() { const st=S(); st.onboarding={needed:false}; return {ok:true, state:st}; },
      async tilt_state() { return {ok:true, tilt:{enabled:true, supported:false, alerts:[], game_running:null}}; },
      async set_tilt_watch() { return {ok:true, tilt:{alerts:[]}, state:S()}; },
      async ack_tilt() { return {ok:true, alerts:[]}; },
      async check_update() { return {ok:true, newer:false}; },
      async update_gate() { return {ok:true, gate:{blocked:false}, update:{}}; },
      async install_update() { return {ok:false, message:'테스트'}; },
      async apply_nvidia_profile(m) { window.__CALLS.push(['apply_nvidia', m]); return {ok:true, message:'적용', result:{ok:true, changes:[], verified:true}, update:{mode:m}}; },
      async restore_nvidia_profile() { return {ok:true, message:'복구'}; },
      async toggle_nvidia_alias() { return {ok:true}; },
      async set_mouse_accel() { return {ok:true}; },
      async get_mouse_accel() { return {ok:true, enabled:false}; },
      async perf_state() { return {ok:true, perf:{supported:true, applied:false, power:{name:'균형'}, game_mode:{on:false}}}; },
      async apply_perf_mode() { return {ok:true, perf:{supported:true, applied:true}}; },
      async restore_perf_mode() { return {ok:true, perf:{supported:true, applied:false}}; },
      async wait_elevated_result() { return {ok:true, result:{ok:true, message:'적용'}}; },
      async tray_hide() { window.__CALLS.push(['tray_hide']); return {ok:true, hidden:true}; },
      async tray_show() { return {ok:true, hidden:false}; },
      async overlay_state() { return {ok:true, overlay:{supported:true, open:[], backend:'win32-layered'}}; },
      async overlay_preview(kind) { window.__CALLS.push(['overlay_preview', kind]); return {ok:true, message:'미리보기'}; },
      async overlay_toast_preview() { return {ok:true}; },
      async overlay_hide() { return {ok:true}; },
      async save_party_preset() { return {ok:true, state:S(), message:'프리셋'}; },
      async delete_party_preset() { return {ok:true, state:S()}; },
      async load_party_preset() { return {ok:true, members:['0.1초의각'], message:'불러옴'}; },
      async party_scan() { return {ok:true, rows:[], message:'스캔'}; },
      async overlay_party_show() { return {ok:false, message:'Windows 전용'}; },
      async copy_text() { return {ok:true}; },
      async app_info() { return {ok:true}; }
    }};
    """.replace("__STATE__", json.dumps(state, ensure_ascii=False))
