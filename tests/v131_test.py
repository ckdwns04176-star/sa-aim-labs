"""v1.3.1 tests — 실기 피드백 24건 (창 이동·최대화 제거·트레이 메뉴·엔비디아 내역·
추천 행 정리·팝업 상세·PB·전원 토글·리포트 폴더·시나리오 라이브러리·병영수첩 링크·
친구 메모·KPI 그래프/툴팁·맵 매치 전적·설정 기어·티어 순서·플레이 코칭).

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \
    python3 tests/v131_test.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
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
        win = i % 2 == 0
        rows.append({
            "date": (dt.date.today() - dt.timedelta(days=i % 7)).isoformat(),
            "time": f"20:{10 + i:02d}",
            "map": name, "type": "솔로" if i % 3 else "파티",
            "result": "승" if win else "패",
            "k": 12 + i % 7, "d": 9 + i % 5, "kd": 50.0 + (i % 8),
            "damage": 900 + i * 20,
            "players": [
                [nick, "팀", 12 + i % 7, 9 + i % 5, 900 + i * 20],
                ["아군1", "팀", 8, 7, 700], ["아군2", "팀", 6, 9, 520],
                ["상대1", "적", 10, 8, 810], ["상대2", "적", 7, 12, 640],
            ] if i < 6 else [],
        })
    return {
        "nickname": nick, "ouid": ouid,
        "basic": {"clan": "리드", "created": "2011.05.02", "manner": "3"},
        "tier": {"solo": {"name": "그랜드마스터 II", "rp": 3261},
                 "party": {"name": "그랜드마스터 III", "rp": 3435}},
        "stats": {"games": 100, "kills": 900, "deaths": 800, "wins": 47,
                  "win": 47.0, "kd": 51.5, "dmg": 1009},
        "recent": {"sniper": 60, "assault": 40},
        "maps": [{"rk": i + 1, "map": m[0], "games": m[1], "win": m[2], "kd": m[3],
                  "dmg": m[4], "weak": round(100 - m[2] * .5 - m[3] * .3 - m[4] / 200, 1)}
                 for i, m in enumerate(maps)],
        "matches": rows,
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def build_state() -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="sa_v131_"))
    os.environ["SA_AL_DATA_DIR"] = str(tmp)
    from app.store import Store
    from app.api import Api

    store = Store(tmp / "data.json")
    store.update_settings({"nickname": "휴가중.", "api_key": "test_9542ea5ac663d2c0", "onboarded": True})
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
    yesterday = json.loads(json.dumps(barracks))
    yesterday["stats"] = {**barracks["stats"], "win": 45.5, "kd": 50.1, "dmg": 975}
    yesterday["tier"] = {"solo": {"name": "그랜드마스터 II", "rp": 3230},
                         "party": {"name": "그랜드마스터 III", "rp": 3400}}
    yesterday["updated_at"] = (dt.datetime.now().astimezone() - dt.timedelta(days=1)).isoformat(timespec="seconds")
    store.add_barracks_snapshot(yesterday)
    store.add_barracks_snapshot(barracks)
    # 스냅샷 날짜는 저장 시 오늘로 찍히므로, 비교용으로 이전 것을 어제로 옮긴다
    hist = store.barracks_history()
    if len(hist) >= 2:
        hist[0]["date"] = (today - dt.timedelta(days=1)).isoformat()
        hist[0]["at"] = (dt.datetime.now().astimezone() - dt.timedelta(days=1)).isoformat(timespec="seconds")
    store.save()
    store.set_leaderboard([
        {"name": "휴가중.", "me": True, "ouid": barracks["ouid"], "clan": "리드", "weapon": "sniper",
         "solo": 3261, "party": 3435, "kd": 51.5, "dmg": 1009, "win": 47.0},
        {"name": "0.1초의각", "ouid": "ouid-friend-1", "clan": "리드", "weapon": "sniper",
         "solo": 3208, "party": 3520, "kd": 49.3, "dmg": 1041, "win": 50.4},
        {"name": "탄피수집가", "ouid": "ouid-friend-2", "clan": "리드", "weapon": "rifle",
         "solo": 3124, "party": 3311, "kd": 54.8, "dmg": 1182, "win": 53.1},
    ])
    store.set_memo("0.1초의각", "A롱 담당 · 스나 1픽")
    api = Api(store=store)
    return api._build_state()


def mock_js(state: dict) -> str:
    return r"""
    window.__CALLS = [];
    window.__PY_STATE = __STATE_JSON__;
    window.__flip = {perf: 0};
    const S = () => JSON.parse(JSON.stringify(window.__PY_STATE));
    const clone = (v) => JSON.parse(JSON.stringify(v));
    window.pywebview = {api: {
      async bootstrap() { return {ok: true, state: S()}; },
      async get_state() { return {ok: true, state: S()}; },
      async get_nvidia_state() {
        return {ok: true, utils: {supported: true, mode: null, alias: false, backups: [], points: [],
          modes: {rifle: true, sniper: true, stairfix: true}, missing: [], accel: false,
          stairfix: {ready: true, applied: false, value: '0x00020041', label: '기본값', target: '0x00401241'},
          profile_items: [{id: '0x00D55F7D', name: '계단현상 끄기', en: 'Antialiasing compatibility (DX9)',
                           value: '0x00020041', label: '기본값'}],
          checklist: []}, state: S()};
      },
      async save_settings(patch) { window.__CALLS.push(['save_settings', patch]); return {ok: true, state: S()}; },
      async drag_window() { window.__CALLS.push(['drag_window']); return {ok: true, supported: true}; },
      async start_window_drag() { window.__CALLS.push(['start_window_drag']); return {ok: true, supported: true, mode: 'win32-cursor'}; },
      async end_window_drag() { window.__CALLS.push(['end_window_drag']); return {ok: true}; },
      async move_window(dx, dy) { window.__CALLS.push(['move_window', dx, dy]); return {ok: true, supported: true}; },
      async toggle_maximize() { window.__CALLS.push(['toggle_maximize']); return {ok: true, maximized: false, fixed: true}; },
      async minimize_window() { return {ok: true}; },
      async close_window() { return {ok: true}; },
      async apply_nvidia_profile(mode, elevate) {
        window.__CALLS.push(['apply_nvidia_profile', mode, elevate]);
        const st = S();
        return {ok: true, mode, state: st, merged: true,
          changes: [{id: '0x00D55F7D', name: '계단현상 끄기', en: 'Antialiasing compatibility (DX9)',
                     before: '0x00020041', after: '0x00401241', label_before: '기본값',
                     label_after: '계단현상·타격효과 제거', key: true}],
          base_changes: [{id: '0x10F9DC81', name: '드라이버 설정 0x10F9DC81', en: 'setting 0x10F9DC81',
                          scope: '전역 프로필', before: '0x00000011', after: '없음'}],
          files: [{name: 'nvdrsdb0.bin', before_size: 2355580, after_size: 1944984, changed: true}],
          profile_items: [{id: '0x00D55F7D', name: '계단현상 끄기', en: 'Antialiasing compatibility (DX9)',
                           value: '0x00401241', label: '계단현상·타격효과 제거'}],
          checklist: [{id: '0x00D55F7D', name: '계단현상 끄기', want: '0x00401241 로 패치',
                       label_value: '계단현상·타격효과 제거', status: 'ok'},
                      {id: '0x1057EB71', name: '전원 관리 모드', want: '최대 성능 선호',
                       label_value: '전역 설정 상속', status: 'global'}],
          message: (mode === 'rifle' ? '라플 프로필' : '스나 프로필') + ' + 계단현상 끄기 적용 완료 · 2개 항목 변경'};
      },
      async restore_nvidia_profile() { window.__CALLS.push(['restore_nvidia_profile']);
        return {ok: true, state: S(), message: '복구 완료'}; },
      async wait_elevated_result(t) { window.__CALLS.push(['wait_elevated_result', t]);
        return {ok: true, mode: 'sniper', state: S(), changes: [], base_changes: [],
          profile_items: [{id: '0x00D55F7D', name: '계단현상 끄기', value: '0x00401241', label: '적용됨'}],
          message: '관리자 권한 적용 완료'}; },
      async set_mouse_accel(v) { return {ok: true, enabled: !!v, state: S(), message: 'ok'}; },
      async perf_state(force) { return {ok: true, perf: {supported: true, power: {guid: 'x', name: '균형 조정'},
        game_mode: {enabled: null}, optimal: false, active: false, applied: false, label: '균형 조정',
        backup: null, applied_at: null}}; },
      async apply_perf_mode() { window.__CALLS.push(['apply_perf_mode']);
        return {ok: true, message: '⚡ 적용 완료', perf: {supported: true, power: {name: '최고 성능'},
          game_mode: {enabled: true}, optimal: false, active: true, applied: true, label: '최고 성능',
          backup: {power: {guid: 'x'}}, applied_at: '2026-11-24T20:30:00'}}; },
      async restore_perf_mode() { window.__CALLS.push(['restore_perf_mode']);
        return {ok: true, message: '↩ 복구', perf: {supported: true, power: {name: '균형 조정'},
          game_mode: {enabled: null}, optimal: false, active: false, applied: false, label: '균형 조정',
          backup: null, applied_at: null}}; },
      async save_report_card(a, b) { window.__CALLS.push(['save_report_card']);
        return {ok: true, message: '📸 리포트 카드 저장 완료', path: '/tmp/x.png', folder: '/tmp/reports'}; },
      async open_reports_folder() { window.__CALLS.push(['open_reports_folder']);
        return {ok: true, folder: '/tmp/reports'}; },
      async save_member_memo(name, text) {
        window.__CALLS.push(['save_member_memo', name, text]);
        const st = S(); st.memos = st.memos || {}; st.memos[name] = text;
        return {ok: true, state: st, memos: st.memos, message: '메모를 저장했습니다 · ' + name};
      },
      async open_barracks_page(ouid) { window.__CALLS.push(['open_barracks_page', ouid]);
        return {ok: true, url: 'https://barracks.sa.nexon.com/' + (ouid || 'me') + '/detail'}; },
      async open_external(url) { window.__CALLS.push(['open_external', url]); return {ok: true}; },
      async launch_trainer(url) { return {ok: true, url}; },
      async save_training_session(p) { window.__CALLS.push(['save_training_session', p]);
        return {ok: true, state: S()}; },
      async import_kovak_scores() { window.__CALLS.push(['import_kovak_scores']);
        return {ok: true, matched: [], message: '없음'}; },
      async warmup_plan() { return {ok: true, recs: []}; },
      async export_playlist() { return {ok: true, message: '저장'}; },
      async fetch_barracks() { return {ok: true, state: S(), barracks: S().barracks, message: '최신화'}; },
      async fetch_leaderboard() { return {ok: true, state: S(), message: '갱신'}; },
      async compare_friend() { return {ok: false, message: '비교 없음'}; },
      async ack_digest() { const st = S(); st.digest = {...(st.digest||{}), show: false, ack: true};
        return {ok: true, state: st}; },
      async export_backup() { return {ok: true, message: '백업'}; },
      async import_backup() { return {ok: true, state: S()}; },
      async open_data_folder() { return {ok: true}; },
      async check_update() { return S().update; },
      async tray_hide() { return {ok: true, message: '숨김'}; },
      async cloud_status() { return {ok: true, cloud: {}, state: S()}; },
      async reset_scores(c) { return {ok: true, state: S(), message: '초기화'}; }
    }};
    """.replace("__STATE_JSON__", json.dumps(state, ensure_ascii=False))


# --------------------------------------------------------------------- backend
def backend_checks() -> None:
    from app import paths, winutil, nvidia, drs, scoring, nexon
    from app.store import Store

    check("APP_VERSION 1.5.0", paths.APP_VERSION == "1.5.0", paths.APP_VERSION)

    # 1·2. 창 이동 · 최대화 제거
    style = 0x00CF0000  # WS_OVERLAPPEDWINDOW
    fixed = winutil.fixed_style(style)
    check("창 스타일에서 최대화·크기조절 제거",
          not (fixed & winutil.WS_MAXIMIZEBOX) and not (fixed & winutil.WS_THICKFRAME)
          and not (fixed & winutil.WS_MAXIMIZE), hex(fixed))
    plan = winutil.window_fix_plan((300, 220, 1600, 1000), (1200, 750), 96, (0, 0, 1920, 1040))
    check("WindowGuard 는 위치를 절대 바꾸지 않음", plan["x"] == 300 and plan["y"] == 220
          and plan["moved"] is False and plan["width"] == 1200 and plan["height"] == 750, str(plan))
    check("창 이동은 SetWindowPos SWP_NOSIZE|NOZORDER|NOACTIVATE 상수",
          winutil.SWP_NOSIZE == 0x0001 and winutil.SWP_NOZORDER == 0x0004 and winutil.SWP_NOACTIVATE == 0x0010)
    src = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
    check("api.move_window · start_window_drag 노출",
          "def move_window(" in src and "def start_window_drag(" in src and "def end_window_drag(" in src)
    check("최대화 API 는 고정 안내만", "fixed=True" in src and "최대화 후" not in src)

    # 3. 트레이 메뉴 3종 + 설정 콜백
    tray_src = (ROOT / "app" / "tray.py").read_text(encoding="utf-8")
    check("트레이 메뉴 열기/설정/종료", all(x in tray_src for x in ('"열기"', '"설정"', '"종료"')))
    check("트레이 설정 콜백 · SetForegroundWindow", "on_settings" in tray_src
          and "SetForegroundWindow" in tray_src and "PostMessageW" in tray_src)
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    check("main 이 트레이 설정 → 설정 팝업", "on_settings=_show_window_and_settings" in main_src
          and "openSettings" in main_src)
    check("main 이 창 스타일 고정", "fixed_window_style" in main_src)

    # 4. 엔비디아 변경 내역
    rifle = (ROOT / "assets" / "profiles" / "rifle" / "nvdrsdb0.bin").read_bytes()
    sniper = (ROOT / "assets" / "profiles" / "sniper" / "nvdrsdb0.bin").read_bytes()
    items = drs.profile_items(rifle)
    check("프로필 전체 항목 읽기", len(items) >= 5 and any(i["name"] == "계단현상 끄기" for i in items),
          str(len(items)))
    check("설정 이름 정정(SLI 호환 비트 DX9)", any(i["id"] == "0x1095DEF8" and "SLI" in i["name"] for i in items))
    base_rows = drs.base_change_list(rifle, sniper)
    check("전역(Base Profile) 변경 내역 계산", len(base_rows) >= 1, str([r["id"] for r in base_rows]))
    cl = drs.checklist_state(rifle)
    check("고성능 체크리스트 6항목", len(cl) == 6 and any(c["status"] == "global" for c in cl), str(len(cl)))
    res = nvidia._readiness(rifle, [], [], "rifle")
    check("변경 없음 이유 문구", "바뀌지 않았습니다" in res and "프로필" in res, res[:50])
    apply_src = (ROOT / "app" / "nvidia.py").read_text(encoding="utf-8")
    check("적용 결과에 changes/base_changes/files/profile_items/checklist",
          all(k in apply_src for k in ('"base_changes"', '"files"', '"profile_items"', '"checklist"', '"unchanged_reason"')))
    check("관리자 권한 결과 대기 API", "def wait_elevated_result" in src)
    check("관리자 권한 복구(--drs-restore)", "--drs-restore" in main_src and "run_elevated_restore" in apply_src)

    # 5. 추천 행 목표·평균 제거 (UI 소스)
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    rec_block = html[html.index("function recRowHtml"):html.index("function drawRecs")]
    check("추천 행에서 평균·목표 제거", "평균" not in rec_block and "목표" not in rec_block and "PB" in rec_block)

    # 6. 호버는 팝업 요소만
    check("팝업 카드에만 볼록 효과(.card.pop)", ".card.pop:hover" in html and "transform:translateY(-2px)" in html)

    # 8. 라이브러리 PB
    state = build_state()
    pbs = state.get("pbs") or {}
    check("state 에 시나리오 PB 사전", "VT 1w6ts Rasp Novice" in pbs
          and pbs["VT 1w6ts Rasp Novice"]["best"] == 152, str(list(pbs)[:2]))
    check("라이브러리 최고 기록 숫자만 표기", "lib-pb" in html and "PB ${pbv" not in html and "pbv.toLocaleString('ko-KR')" in html)

    # 12. 시나리오 라이브러리 · 상단 코박스 버튼
    check("메뉴 이름 시나리오 라이브러리", "시나리오 라이브러리</span>" in html and "오늘의 훈련</button>" not in html)
    order = html.index('id="kovak-import-head"') < html.index('id="run-all-btn"')
    check("코박스 점수 버튼이 5종 연속 실행 왼쪽", order)

    # 13·14. 병영수첩 링크 · 친구 메모
    check("내 병영수첩 버튼", 'id="br-site"' in html and "내 병영수첩" in html)
    check("친구 행 병영 버튼", 'data-site=' in html and "병영" in html)
    check("친구 메모 아이콘", "lb-memo" in html and "openMemo" in html)

    # 15·16. KPI 그래프 · 맵 전적
    check("KPI 카드 data-chart", html.count('data-chart="') >= 4)
    check("맵 행 클릭 → 매치 전적", "openMapMatches" in html and "data-map=\"" in html)
    check("차트 툴팁", "chart-tip" in html and "bindChartHover" in html)

    # 18. 티어표 순서 반전
    check("티어표 역순 렌더", "TIERS.map((t,i)=>({t,i})).reverse()" in html)

    # 19. 코칭 문구 — 서든 콜아웃, 번역투 금지
    coach = scoring.play_coaching({
        "stats": {"games": 100, "kills": 900, "deaths": 800, "wins": 47, "win": 45.0, "kd": 46.0, "dmg": 980},
        "tier": {"solo": {"rp": 2100}, "party": {"rp": 2600}},
        "maps": [{"map": "이탈리아-폭파", "games": 6, "win": 33.3, "kd": 43.5, "dmg": 836.8, "weak": 61.9},
                 {"map": "제5보급-심플", "games": 11, "win": 63.6, "kd": 49.5, "dmg": 765.9, "weak": 45.7}],
        "matches": [{"result": "패"}] * 5,
        "recent": {"sniper": 30, "assault": 70},
    })
    flat = " ".join(t["title"] + " " + t["text"] for t in coach)
    banned = [w for w in ("복도", "초동 교전", "초동", "각이 애매", "브리핑") if w in flat]
    check("코칭에 번역투·타게임 용어 없음", not banned, str(banned))
    # v1.5.0 — 확인되지 않은 지형 이름(2층·골목·중통)은 쓰지 않고 실제 수치로만 쓴다
    check("코칭에 확인 안 된 지형 이름 없음", not any(w in flat for w in ("2층", "골목", "중통")), flat[:80])
    check("코칭이 숫자를 그대로 인용", "2,100" in flat and "2,600" in flat and "46.0" in flat, flat[:100])

    guide = scoring.map_guide({"map": "제5보급-심플", "games": 11, "win": 63.6, "kd": 49.5, "dmg": 765.9})
    gflat = " ".join(guide["tips"])
    check("맵 가이드에 지어낸 지형 이름 없음", not any(w in gflat for w in ("2층", "골목", "중통")), gflat[:80])
    check("맵 가이드에 번역투 없음", not any(w in gflat for w in ("복도", "초동", "각이 애매")), gflat[:80])
    italy = scoring.map_guide({"map": "이탈리아-폭파", "games": 6, "win": 33.3, "kd": 43.5, "dmg": 836.8})
    check("이탈리아 가이드 — 수치 기반 조언", any(w in " ".join(italy["tips"]) for w in ("승률", "K/D", "딜량")),
          str(italy.get("callouts")))

    # 17. 프로필 카드 제거 → 기어 버튼
    check("미니 프로필 카드 제거 · 기어 설정 버튼", 'id="profile-card"' not in html and 'id="settings-btn"' in html)

    # 9·10·11 (백엔드)
    store = Store(Path(os.environ["SA_AL_DATA_DIR"]) / "data.json")
    store.set_memo("테스트친구", "A롱 담당")
    check("친구 메모 저장/불러오기", store.memos().get("테스트친구") == "A롱 담당", str(store.memos()))
    store.set_memo("테스트친구", "")
    check("메모 비우면 삭제", "테스트친구" not in store.memos())

    players = nexon._match_players({"match_detail": [
        {"user_name": "휴가중.", "team_id": "0", "kill": 5, "death": 2, "damage": 700},
        {"user_name": "적1", "team_id": "1", "kill": 3, "death": 4, "damage": 500},
    ]}, "휴가중.")
    check("매치 상세에서 팀/상대 구분", players[0][1] == "팀" and players[1][1] == "적", str(players))
    rows = nexon._match_rows([{"kill": 5, "death": 2, "match_result": "1", "match_type": "랭크전 솔로",
                               "date_match": "2026-11-24T11:00:00Z", "match_id": "m1"}],
                             [{"match_map": "이탈리아-폭파", "match_detail": [
                                 {"user_name": "휴가중.", "team_id": "0", "damage": 700}]}], "휴가중.")
    check("매치 행에 players 배열", rows and isinstance(rows[0].get("players"), list) and rows[0]["players"], str(rows[0])[:80])


# --------------------------------------------------------------------- UI
def ui_checks() -> None:
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
        page.wait_for_selector("#app-splash", state="hidden", timeout=20000)
        page.wait_for_timeout(600)
        # 어제 대비 변화 팝업이 자동으로 뜨면 닫고 시작한다
        if page.evaluate("() => { const m=document.getElementById('digest-mask'); return !!m && m.classList.contains('show'); }"):
            page.click("#digest-ack")
            page.wait_for_timeout(300)
        # v1.4.0 — 첫 화면이 홈이므로 대시보드로 이동
        page.click('.nav-item[data-screen="dashboard"]')
        page.wait_for_timeout(450)

        # 1·2. 타이틀바 / 최대화 제거
        bar = page.evaluate("""() => {
            const tb = document.getElementById('titlebar');
            return {max: !!document.getElementById('win-max'),
                    drag: tb.classList.contains('pywebview-drag-region'),
                    closures: window.__CALLS.length};
        }""")
        check("최대화 버튼 제거", bar["max"] is False, str(bar))
        check("타이틀바 pywebview-drag-region", bar["drag"] is True, str(bar))
        page.evaluate("() => { window.__CALLS = []; }")
        page.mouse.move(320, 18)
        page.mouse.down()
        page.mouse.move(380, 66, steps=5)
        page.mouse.up()
        page.wait_for_timeout(250)
        calls = page.evaluate("() => window.__CALLS.map(c => c[0])")
        check("타이틀바 드래그 → start_window_drag", "start_window_drag" in calls, str(calls[:5]))
        check("최대화 호출 없음", "toggle_maximize" not in calls, str(calls[:6]))

        # 17. 설정 기어
        gear = page.evaluate("""() => ({btn: !!document.getElementById('settings-btn'),
            card: !!document.getElementById('profile-card'),
            title: (document.getElementById('settings-btn')||{}).getAttribute('title')||'',
            bottom: (() => { const b = document.getElementById('settings-btn'); const n = document.querySelector('.sidebar .nav-item');
                             return b && n ? (b.getBoundingClientRect().top > n.getBoundingClientRect().top) : false; })()})""")
        check("사이드바 맨 아래 기어 설정 버튼", gear["btn"] and not gear["card"] and "설정" in gear["title"] and gear["bottom"],
              str(gear))
        page.click("#settings-btn")
        page.wait_for_timeout(250)
        check("기어 버튼 → 설정 팝업", page.evaluate("() => document.getElementById('settings-mask').classList.contains('show')"))
        page.evaluate("() => document.querySelector('#settings-mask').classList.remove('show')")

        # 5. 추천 행 정리
        page.wait_for_timeout(150)
        recs = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#rec-list .rec')];
            return {n: rows.length, meta: rows.map(r => (r.querySelector('.rec-stat2')||{}).innerText || ''),
                    heights: rows.map(r => Math.round(r.getBoundingClientRect().height))};
        }""")
        check("추천 5종 행 유지", recs["n"] >= 5, str(recs["n"]))
        check("추천 행에서 평균·목표 삭제", not any(("평균" in m or "목표" in m) for m in recs["meta"]), str(recs["meta"][:1]))
        check("추천 행 최고 기록 유지", all("최고" in m for m in recs["meta"]), str(recs["meta"][:1]))
        page.screenshot(path=str(SHOTS / "v131-dashboard.png"))

        # 6. 호버 — 팝업 카드만
        hover = page.evaluate("""() => {
            const plain = document.querySelector('#s-dashboard .card:not(.pop)');
            const pop = document.querySelector('#s-dashboard .card.pop');
            const tr = (el) => el ? getComputedStyle(el).transitionProperty : '';
            return {plain: tr(plain), pop: tr(pop), pops: document.querySelectorAll('.card.pop').length};
        }""")
        check("팝업 여는 카드만 볼록 효과", "transform" in hover["pop"] and "transform" not in hover["plain"]
              and hover["pops"] >= 3, str(hover))

        # 7. 5영역 에임 밸런스 팝업
        page.click("#radar-card")
        page.wait_for_timeout(300)
        aim = page.evaluate("""() => ({open: document.getElementById('aim-mask').classList.contains('show'),
            rows: [...document.querySelectorAll('#aim-rows .aim-row')].map(r => r.innerText.split('\\n').join(' | ')),
            advice: (document.getElementById('aim-advice')||{}).innerText})""")
        check("레이더 클릭 → 5영역 상세 팝업", aim["open"] and len(aim["rows"]) == 5, str(len(aim["rows"])))
        check("영역별 세부 점수·시나리오·약점", all(any(ch.isdigit() for ch in r) for r in aim["rows"])
              and any("약점" in r for r in aim["rows"]), str(aim["rows"][:1])[:120])
        page.screenshot(path=str(SHOTS / "v131-aim-balance.png"))
        page.evaluate("() => document.querySelector('#aim-mask').classList.remove('show')")

        # 8. 시나리오 라이브러리 PB
        page.click('.nav-item[data-screen="recs"]')
        page.wait_for_timeout(350)
        lib = page.evaluate("""() => {
            const items = [...document.querySelectorAll('#rec-grid [data-scen]')];
            return {n: items.length,
                    withPb: items.filter(el => { const row = el.closest('.lib-row'); const pb = row && row.querySelector('.lib-pb'); return !!pb && /[0-9-]/.test((pb.textContent||'').trim()); }).length,
                    noPbWord: ![...document.querySelectorAll('#rec-grid')].some(g => /PB/.test(g.innerText||'')),
                    sample: (items[0] ? items[0].parentElement.innerText : '')};
        }""")
        check("라이브러리 모든 행에 최고 기록 숫자 표시", lib["n"] > 20 and lib["withPb"] == lib["n"] and lib.get("noPbWord"),
              f"{lib['withPb']}/{lib['n']} · {lib['sample']}")
        head = page.evaluate("""() => {
            const h = document.getElementById('h1-title');
            const importBtn = document.getElementById('kovak-import-head');
            const runAll = document.getElementById('run-all-btn');
            const nav = [...document.querySelectorAll('.nav-item')].find(b => b.dataset.screen === 'recs');
            return {nav: nav ? (nav.getAttribute('title') || nav.innerText.trim()) : '',
                    h1: (document.querySelector('#s-recs h1')||{}).innerText,
                    order: importBtn && runAll ? (importBtn.getBoundingClientRect().right <= runAll.getBoundingClientRect().left) : false,
                    importBtn: !!importBtn, old: !!document.getElementById('kovak-import-2')};
        }""")
        check("메뉴·화면 이름 시나리오 라이브러리", head["nav"] == "시나리오 라이브러리" and head["h1"] == "시나리오 라이브러리",
              str(head))
        check("코박스 점수 버튼이 5종 연속 실행 바로 왼쪽", head["importBtn"] and head["order"] and not head["old"],
              str(head))
        page.screenshot(path=str(SHOTS / "v131-library.png"))

        # 12. 코박스 최신화 버튼 동작
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#kovak-import-head")
        page.wait_for_timeout(400)
        check("코박스 점수(최신화) 호출", "import_kovak_scores" in page.evaluate("() => window.__CALLS.map(c => c[0])"),
              str(page.evaluate("() => window.__CALLS.map(c => c[0])")))

        # 15. KPI 클릭 → 그래프
        page.click('.nav-item[data-screen="dashboard"]')
        page.wait_for_timeout(300)
        page.click("#kpi-tier-card")
        page.wait_for_timeout(350)
        chart = page.evaluate("""() => ({open: document.getElementById('chart-mask').classList.contains('show'),
            title: (document.getElementById('chart-title')||{}).innerText,
            dots: document.querySelectorAll('#chart-big circle').length,
            facts: document.querySelectorAll('#chart-facts .chart-fact').length})""")
        check("현재 종합 티어 클릭 → 티어 변화 그래프", chart["open"] and "티어" in chart["title"] and chart["dots"] > 0,
              str(chart))
        page.screenshot(path=str(SHOTS / "v131-chart-tier.png"))
        # 툴팁
        box = page.evaluate("() => { const r = document.querySelector('#chart-big').getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; }")
        page.mouse.move(box["x"] + box["w"] * 0.62, box["y"] + box["h"] * 0.5)
        page.wait_for_timeout(220)
        tip = page.evaluate("""() => { const t = document.getElementById('chart-tip');
            return {exists: !!t, show: t ? t.classList.contains('show') : false,
                    text: t ? t.innerText : ''}; }""")
        check("큰 그래프 마우스 오버 툴팁(날짜·숫자)", tip["show"] and any(ch.isdigit() for ch in tip["text"]),
              str(tip)[:120])
        page.evaluate("() => document.querySelector('#chart-mask').classList.remove('show')")
        page.click("#kpi-trend-card")
        page.wait_for_timeout(250)
        check("최근 7일 추세 → 점수 변화 그래프",
              "점수" in page.evaluate("() => document.getElementById('chart-title').innerText"))
        page.evaluate("() => document.querySelector('#chart-mask').classList.remove('show')")
        page.click("#kpi-kd-card")
        page.wait_for_timeout(250)
        check("인게임 랭크전 → K/D 변화 그래프",
              "K/D" in page.evaluate("() => document.getElementById('chart-title').innerText"))
        page.evaluate("() => document.querySelector('#chart-mask').classList.remove('show')")
        # 미니 차트 툴팁
        mini = page.evaluate("() => { const r = document.getElementById('chart-trend').getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; }")
        page.mouse.move(mini["x"] + mini["w"] * 0.55, mini["y"] + mini["h"] * 0.5)
        page.wait_for_timeout(240)
        mtip = page.evaluate("""() => { const t = document.getElementById('chart-tip');
            return t ? {show: t.classList.contains('show'), text: t.innerText} : {show: false, text: ''}; }""")
        check("일일 종합 평균 추이 툴팁", mtip["show"] and any(ch.isdigit() for ch in mtip["text"]), str(mtip)[:100])
        game = page.evaluate("""() => { const svg = document.getElementById('chart-game');
            return {hasData: !!document.getElementById('chart-game') && svg.innerHTML.length > 80}; }""")
        check("인게임 K/D 추이 그래프 데이터", game["hasData"], str(game))

        # 9. 날짜 상세 — 잔디 / 기록 행
        page.click('.nav-item[data-screen="records"]')
        page.wait_for_timeout(400)
        cell = page.evaluate("""() => {
            const cells = [...document.querySelectorAll('#heatmap .heat-cell[data-day]')].filter(c => !c.classList.contains('future') && (c.className.match(/l[1-3]/)));
            return cells.length ? cells[cells.length - 1].dataset.day : null;
        }""")
        if cell:
            page.click(f'#heatmap .heat-cell[data-day="{cell}"]')
            page.wait_for_timeout(300)
            day = page.evaluate("""() => ({open: document.getElementById('day-mask').classList.contains('show'),
                title: (document.getElementById('day-title')||{}).innerText,
                rows: document.querySelectorAll('#day-list .day-row').length,
                sub: (document.getElementById('day-sub')||{}).innerText})""")
            check("잔디 셀 클릭 → 그날 시나리오·점수 팝업", day["open"] and day["rows"] >= 1 and cell[-2:] in day["title"],
                  str(day))
            page.screenshot(path=str(SHOTS / "v131-day.png"))
            page.evaluate("() => document.querySelector('#day-mask').classList.remove('show')")
        else:
            check("잔디 셀 클릭 → 그날 시나리오·점수 팝업", False, "heat cell 없음")
        page.evaluate("() => { [...document.querySelectorAll('#records-table tr[data-day]')][0]?.click(); }")
        page.wait_for_timeout(300)
        check("최근 훈련 기록 행 클릭 → 상세 팝업",
              page.evaluate("() => document.getElementById('day-mask').classList.contains('show')"))
        page.evaluate("() => document.querySelector('#day-mask').classList.remove('show')")

        # 16. 맵 클릭 → 매치 전적
        page.click('.nav-item[data-screen="maps"]')
        page.wait_for_timeout(400)
        page.click('#map-table tbody tr[data-map]')
        page.wait_for_timeout(350)
        mm = page.evaluate("""() => ({open: document.getElementById('mapmatch-mask').classList.contains('show'),
            title: (document.getElementById('mm-title')||{}).innerText,
            matches: document.querySelectorAll('#mm-list .mm-match').length,
            players: document.querySelectorAll('#mm-list .mm-p').length,
            sub: (document.getElementById('mm-sub')||{}).innerText})""")
        check("맵별 분석 행 클릭 → 매치 전적 팝업", mm["open"] and mm["matches"] >= 1, str(mm)[:160])
        check("매치에 킬/데스·딜량·팀원 목록", mm["players"] >= 2 and "K/D" in page.inner_text("#mm-list"), str(mm)[:160])
        page.screenshot(path=str(SHOTS / "v131-map-matches.png"))
        page.evaluate("() => document.querySelector('#mapmatch-mask').classList.remove('show')")

        # 18. 티어표 순서
        page.click('.nav-item[data-screen="tiers"]')
        page.wait_for_timeout(350)
        tiers = page.evaluate("""() => [...document.querySelectorAll('#tier-table .tier-row:not(.hd) .nm')].map(el => el.innerText.trim().split(' ')[0])""")
        check("18단계 티어표 위=별레전드 아래=브론즈 I",
              tiers and "별레전드" in tiers[0] and "브론즈" in tiers[-1] and len(tiers) == 18, str(tiers[:2] + tiers[-2:]))
        page.screenshot(path=str(SHOTS / "v131-tiers.png"))

        # 13·14. 순위표 — 병영 버튼 · 우클릭 메모
        page.click('.nav-item[data-screen="leaderboard"]')
        page.wait_for_timeout(350)
        lb = page.evaluate("""() => ({sites: document.querySelectorAll('#lb-rows [data-site]').length,
            memos: document.querySelectorAll('#lb-rows .lb-memo').length})""")
        check("친구 행마다 병영수첩 버튼", lb["sites"] >= 3, str(lb))
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#lb-rows [data-site]")
        page.wait_for_timeout(250)
        check("병영 버튼 → 홈페이지 열기", "open_barracks_page" in page.evaluate("() => window.__CALLS.map(c => c[0])"),
              str(page.evaluate("() => window.__CALLS")))
        # 기존 메모 아이콘 (0.1초의각)
        check("저장된 메모는 닉네임 옆 아이콘", lb["memos"] >= 1, str(lb))
        page.click("#lb-rows .lb-memo")
        page.wait_for_timeout(250)
        memo = page.evaluate("""() => ({open: document.getElementById('memo-mask').classList.contains('show'),
            text: (document.getElementById('memo-text')||{}).value,
            title: (document.getElementById('memo-title')||{}).innerText})""")
        check("메모 아이콘 클릭 → 메모 내용", memo["open"] and "A롱" in (memo["text"] or ""), str(memo))
        page.evaluate("() => document.querySelector('#memo-mask').classList.remove('show')")
        # 우클릭 → 메모 작성
        page.evaluate("""() => {
            const row = [...document.querySelectorAll('#lb-rows [data-lbname]')].find(r => r.dataset.lbname === '탄피수집가');
            row.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, clientX: 400, clientY: 400}));
        }""")
        page.wait_for_timeout(250)
        check("친구 행 우클릭 → 메모 팝업",
              page.evaluate("() => document.getElementById('memo-mask').classList.contains('show')"))
        page.fill("#memo-text", "B롱 담당 · 라플")
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#memo-save")
        page.wait_for_timeout(500)
        saved = page.evaluate("() => window.__CALLS.filter(c => c[0] === 'save_member_memo').pop()")
        check("메모 저장 호출", bool(saved) and saved[1] == "탄피수집가" and saved[2] == "B롱 담당 · 라플", str(saved))
        after = page.evaluate("""() => [...document.querySelectorAll('#lb-rows .lb-memo')].length""")
        check("저장 후 닉네임 옆 메모 아이콘 표시", after >= 2, str(after))
        page.screenshot(path=str(SHOTS / "v131-leaderboard.png"))

        # 13. 내 병영수첩 버튼
        page.click('.nav-item[data-screen="barracks"]')
        page.wait_for_timeout(350)
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#br-site")
        page.wait_for_timeout(250)
        site = page.evaluate("() => window.__CALLS.filter(c => c[0] === 'open_barracks_page').pop()")
        check("내 병영수첩 버튼 → ouid 로 홈페이지", bool(site) and site[1] == "711c04a69769cc4b6b782ea2aa3eb614",
              str(site))
        page.screenshot(path=str(SHOTS / "v131-barracks.png"))

        # 10. 전원 스위치
        page.click('.nav-item[data-screen="utils"]')
        page.wait_for_timeout(450)
        page.click('[data-util-tab="system"]')
        page.wait_for_timeout(250)
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#perf-switch")
        page.wait_for_timeout(500)
        on = page.evaluate("""() => {
            const sw = document.getElementById('perf-switch');
            const knob = sw.querySelector('i');
            return {on: sw.classList.contains('on'), aria: sw.getAttribute('aria-checked'),
                    transform: getComputedStyle(knob).transform,
                    calls: window.__CALLS.map(c => c[0])};
        }""")
        check("고성능 전원 토글 → ON(오른쪽)", on["on"] and on["aria"] == "true"
              and "matrix" in (on["transform"] or ""), str(on))
        check("전원 모드 적용 호출", "apply_perf_mode" in on["calls"], str(on["calls"]))
        page.click("#perf-switch")
        page.wait_for_timeout(500)
        off = page.evaluate("""() => {
            const sw = document.getElementById('perf-switch');
            const knob = sw.querySelector('i');
            return {on: sw.classList.contains('on'), transform: getComputedStyle(knob).transform,
                    calls: window.__CALLS.map(c => c[0])};
        }""")
        check("한 번 더 → OFF(왼쪽) + 원래 전원 복구", (not off["on"]) and "restore_perf_mode" in off["calls"],
              str(off))

        # 11. 리포트 카드 → 폴더 열기 (v1.4.0 — 에임 기록·분석으로 이동)
        page.click('.nav-item[data-screen="records"]')
        page.wait_for_timeout(350)
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#report-btn")
        page.wait_for_timeout(900)
        toast = page.evaluate("""() => { const t = document.getElementById('app-toast');
            const b = t.querySelector('.tt-act');
            return {text: t.innerText, hasBtn: !!b, btn: b ? b.innerText : ''}; }""")
        check("리포트 카드 저장 토스트에 폴더 열기", toast["hasBtn"] and "폴더 열기" in toast["btn"], str(toast))
        page.click("#app-toast .tt-act")
        page.wait_for_timeout(300)
        check("폴더 열기 → open_reports_folder",
              "open_reports_folder" in page.evaluate("() => window.__CALLS.map(c => c[0])"),
              str(page.evaluate("() => window.__CALLS.map(c => c[0])")))

        # 4. 엔비디아 적용 → 변경 내역 상세
        page.click('.nav-item[data-screen="utils"]')
        page.wait_for_timeout(400)
        page.evaluate("() => { window.__CALLS = []; }")
        page.click('[data-apply="rifle"]')
        page.wait_for_timeout(900)
        chg = page.evaluate("""() => ({open: document.getElementById('changes-mask').classList.contains('show'),
            sections: [...document.querySelectorAll('#change-list > div:not(.change-row)')].map(d => d.innerText.slice(0, 24)),
            rows: [...document.querySelectorAll('#change-list .change-row')].map(r => r.innerText.split('\\n').join(' ')),
            sub: (document.getElementById('changes-sub')||{}).innerText})""")
        check("적용 후 변경 내역 팝업", chg["open"] and len(chg["rows"]) >= 3, str(len(chg["rows"])))
        check("변경 내역에 계단현상·전역·파일 증거",
              any("계단현상" in r for r in chg["rows"]) and any("전역" in s for s in chg["sections"])
              and any("nvdrsdb0.bin" in r for r in chg["rows"]), str(chg["sections"])[:120])
        check("변경 내역에 고성능 체크리스트", any("체크리스트" in s for s in chg["sections"]), str(chg["sections"])[:120])
        page.screenshot(path=str(SHOTS / "v131-changes.png"))
        page.evaluate("() => document.querySelector('#changes-mask').classList.remove('show')")

        # 스크린샷 모음
        page.click('.nav-item[data-screen="records"]')
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOTS / "v131-records.png"))
        page.click('.nav-item[data-screen="maps"]')
        page.wait_for_timeout(350)
        page.screenshot(path=str(SHOTS / "v131-maps.png"))

        # ---------- 3차 피드백: 병영수첩 배너 · 순위표 · 유틸 화면 정리
        page.click('.nav-item[data-screen="barracks"]')
        page.wait_for_timeout(450)
        bn = page.evaluate("""() => {
            const b = document.getElementById('br-banner');
            const sub = b.querySelector('.bn-sub');
            return {text: b.innerText, sub: sub ? sub.innerText : '',
                    user: (b.querySelector('#bn-user')||{}).innerText,
                    clanLink: !!b.querySelector('.bn-clan'),
                    deltas: b.querySelectorAll('.bn-delta').length,
                    up: b.querySelectorAll('.bn-stat.up').length,
                    down: b.querySelectorAll('.bn-stat.down').length,
                    digestBtn: !!document.getElementById('br-digest'),
                    exportBtn: !!document.getElementById('br-export')};
        }""")
        check("주무기 표기 '스나'만 (앞 '주무기 ' 제거)", (bn["user"] or "").strip() in ("스나", "라플"), str(bn["user"]))
        check("'예의' 매너 등급 삭제", "예의" not in bn["text"], bn["sub"])
        check("OUID 표시 삭제", "OUID" not in bn["text"], bn["sub"])
        check("닉네임 옆 작은 티어 칩 삭제", "그랜드마스터" not in bn["text"].split("가입")[0], bn["text"][:60])
        check("클랜명만 + 링크", bn["clanLink"] and "리드" in bn["sub"] and "리드 클랜" not in bn["sub"], bn["sub"])
        check("어제 대비 변화 ▲/▼ 표시", bn["deltas"] >= 1, f"{bn['deltas']}개 · up={bn['up']}")
        check("변화 다시 보기 · 기록 내보내기 삭제", (not bn["digestBtn"]) and (not bn["exportBtn"]), str(bn))
        page.screenshot(path=str(SHOTS / "v131-final-barracks.png"))

        page.click('.nav-item[data-screen="leaderboard"]')
        page.wait_for_timeout(400)
        lbx = page.evaluate("""() => ({
            pips: !!document.getElementById('lb-pips'), foot: !!document.querySelector('.lb-foot'),
            sorts: !!document.getElementById('lb-sorts'), partyGap: !!document.getElementById('sum-party-gap'),
            avg: !!document.getElementById('sum-avg'),
            sortable: document.querySelectorAll('#lb-head [data-lbsort]').length})""")
        check("10칸 슬롯 막대 삭제", lbx["pips"] is False, str(lbx))
        check("'1위와 파티 RP 차' 카드로 교체", lbx["partyGap"] and not lbx["avg"], str(lbx))
        check("자잘한 푸터 설명 삭제", lbx["foot"] is False, str(lbx))
        check("정렬 알약 삭제 → 표 헤더 정렬", (not lbx["sorts"]) and lbx["sortable"] >= 5, str(lbx))
        page.click('#lb-head [data-lbsort="kd"]')
        page.wait_for_timeout(300)
        kd_order = page.evaluate("""() => [...document.querySelectorAll('#lb-rows .lb-row')]
            .map(r => Number((r.querySelectorAll('.lb-num')[2]||{}).innerText ? r.querySelector('.lb-kd').dataset.kd : 0))""")
        check("헤더 클릭 → K/D 내림차순 + ▼", kd_order == sorted(kd_order, reverse=True)
              and "▼" in page.evaluate("() => document.querySelector('#lb-head [data-lbsort=\"kd\"]').innerText"),
              str(kd_order))
        page.click('#lb-head [data-lbsort="kd"]')
        page.wait_for_timeout(250)
        kd_order2 = page.evaluate("""() => [...document.querySelectorAll('#lb-rows .lb-row')]
            .map(r => Number((r.querySelectorAll('.lb-num')[2]||{}).innerText ? r.querySelector('.lb-kd').dataset.kd : 0))""")
        check("한 번 더 → 오름차순 ▲", kd_order2 == sorted(kd_order2)
              and "▲" in page.evaluate("() => document.querySelector('#lb-head [data-lbsort=\"kd\"]').innerText"),
              str(kd_order2))
        page.screenshot(path=str(SHOTS / "v131-final-leaderboard.png"))

        page.click('.nav-item[data-screen="utils"]')
        page.wait_for_timeout(450)
        page.click('[data-util-tab="system"]')
        page.wait_for_timeout(250)
        ut = page.evaluate("""() => ({text: document.getElementById('s-utils').innerText,
            desc: document.querySelectorAll('.util-desc').length,
            body: document.getElementById('s-utils').innerHTML.length})""")
        check("유틸 화면 PC방 문구 전부 삭제", "PC방" not in ut["text"], ut["text"][:80])
        check("'1초 복원' 배지 삭제", "1초 복원" not in ut["text"], "")
        check("적용 카드 회색 서브 글씨 삭제", ut["desc"] == 0, str(ut["desc"]))
        check("게임 켜져 있어도 적용 안내", "켜져 있어도 적용" in ut["text"], ut["text"][:60])
        page.screenshot(path=str(SHOTS / "v131-final-utils.png"))

        # ---------- 8개 메뉴 + 설정 팝업 최종 캡처
        for screen, name in (("dashboard", "dashboard"), ("recs", "library"), ("records", "records"),
                             ("barracks", "barracks"), ("maps", "maps"), ("leaderboard", "leaderboard"),
                             ("tiers", "tiers"), ("utils", "utils")):
            page.click(f'.nav-item[data-screen="{screen}"]')
            page.wait_for_timeout(450)
            page.screenshot(path=str(SHOTS / f"v131-final-{name}.png"))
        page.click('.nav-item[data-screen="dashboard"]')
        page.wait_for_timeout(300)
        page.click("#settings-btn")
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOTS / "v131-final-settings.png"))
        page.evaluate("() => document.querySelector('#settings-mask').classList.remove('show')")

        check("UI 런타임 오류 없음", not errors, "; ".join(errors[:3]))
        browser.close()


def main() -> int:
    backend_checks()
    ui_checks()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
