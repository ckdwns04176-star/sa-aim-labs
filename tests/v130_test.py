"""v1.3.0 checks — 23가지 UI·기능 업그레이드.

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \\
    python3 tests/v130_test.py

  · backend — 창 드래그/크기 고정, Drs 계단현상 병합 + 변경 내역, 병영수첩 일일 변화,
               18단계 분포·상세, 점수 전체 초기화(잔디 유지), 추천 행 밀도, 맵 가이드
  · UI      — 8개 메뉴·설정 팝업·병영수첩 변화 팝업·맵 공략·티어 상세·정렬·토글·툴팁·
               글씨 크기·전환 애니메이션·닫기 확인·점수 초기화 2차 확인
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


# --------------------------------------------------------------------- fixtures
def barracks_fixture(today: dt.date, *, win: float, kd: float, dmg: int,
                     solo: int, party: int) -> dict:
    maps = [
        {"rk": 1, "map": "이탈리아-폭파", "games": 6, "win": 33.3, "kd": 43.5, "dmg": 836.8, "weak": 61.9},
        {"rk": 2, "map": "프로방스(심플)", "games": 12, "win": 25.0, "kd": 51.0, "dmg": 1177.8, "weak": 60.4},
        {"rk": 3, "map": "크로스포트(심플)", "games": 5, "win": 40.0, "kd": 41.2, "dmg": 873.8, "weak": 58.9},
        {"rk": 4, "map": "시티캣-심플", "games": 10, "win": 40.0, "kd": 50.5, "dmg": 995.1, "weak": 54.9},
        {"rk": 5, "map": "올드타운(심플)", "games": 8, "win": 37.5, "kd": 56.1, "dmg": 1071.9, "weak": 53.7},
        {"rk": 6, "map": "팰리스-심플", "games": 10, "win": 50.0, "kd": 48.8, "dmg": 857.2, "weak": 51.8},
        {"rk": 7, "map": "지하철-심플", "games": 9, "win": 55.6, "kd": 55.2, "dmg": 951.2, "weak": 46.1},
        {"rk": 8, "map": "제5보급-심플", "games": 11, "win": 63.6, "kd": 49.5, "dmg": 765.9, "weak": 45.7},
        {"rk": 9, "map": "드래곤로드(심플)", "games": 11, "win": 54.5, "kd": 51.3, "dmg": 1295.8, "weak": 44.4},
        {"rk": 10, "map": "듀오 릭", "games": 7, "win": 57.1, "kd": 58.2, "dmg": 984.4, "weak": 44.1},
    ]
    matches = [
        {"date": (today - dt.timedelta(days=i % 5)).isoformat(), "time": "20:1%d" % i,
         "map": maps[i % len(maps)]["map"], "type": "솔로", "result": "승" if i % 2 else "패",
         "k": 12 + i, "d": 9 + i, "kd": 54.0 + i % 5, "damage": 1000 + i * 20}
        for i in range(20)
    ]
    return {
        "nickname": "휴가중.", "ouid": "711c04a69769cc4b6b782ea2aa3eb614",
        "basic": {"clan": "리드", "created": "2011.05.02", "manner": "3"},
        "tier": {"solo": {"name": "그랜드마스터 II", "rp": solo},
                 "party": {"name": "그랜드마스터 III", "rp": party}},
        "stats": {"games": 100, "kills": 900, "deaths": 800, "wins": 47,
                  "win": win, "kd": kd, "dmg": dmg},
        "recent": {"sniper": 60, "assault": 40},
        "maps": maps, "matches": matches,
        "updated_at": f"{today.isoformat()}T20:10:00+09:00",
    }


def build_store(base: Path):
    os.environ["SA_AL_DATA_DIR"] = str(base)
    from app.store import Store

    store = Store(base / "data.json")
    store.update_settings({"nickname": "휴가중.", "api_key": "test_9542ea5ac663d2c0", "onboarded": True})
    today = dt.date.today()
    # 여러 날 기록 → 잔디·연속일·추이
    for i, score in enumerate((152, 146, 158, 151, 161)):
        day = (today - dt.timedelta(days=4 - i)).isoformat()
        store.add_record({
            "date": day, "game": "kovak", "weapon": "sniper",
            "scores": {"precision": score, "wide": score - 24, "micro": score - 56,
                       "track": score - 70, "switch": score - 12},
            "scenarios": [{"name": "VT 1w6ts Rasp Novice", "area": "precision", "score": score},
                          {"name": "Wide Wall 3 Targets TE", "area": "wide", "score": score - 24}],
            "note": "",
        })
    store.note_attendance(store.records())
    barracks = barracks_fixture(today, win=48.0, kd=52.9, dmg=1120, solo=3298, party=3435)
    store.barracks_cache()["휴가중."] = barracks
    yday = (today - dt.timedelta(days=1)).isoformat()
    store.data["barracks_history"] = [
        {"date": yday, "at": f"{yday}T19:40:00+09:00", "nickname": "휴가중.",
         "stats": {"win": 47.0, "kd": 51.5, "dmg": 1009}, "tier": {"solo": {"rp": 3261}, "party": {"rp": 3435}},
         "basic": {"clan": "리드"}},
        {"date": today.isoformat(), "at": f"{today.isoformat()}T20:10:00+09:00", "nickname": "휴가중.",
         "stats": {"win": 48.0, "kd": 52.9, "dmg": 1120}, "tier": {"solo": {"rp": 3298}, "party": {"rp": 3435}},
         "basic": {"clan": "리드"}},
    ]
    store.save()
    return store


# --------------------------------------------------------------------- backend
def backend() -> None:
    from app import drs, nvidia, paths, scoring, winutil
    import shutil

    check("APP_VERSION 1.4.0", paths.APP_VERSION == "1.4.0", paths.APP_VERSION)

    # ---- 1. 창: 크기만 고정, 위치는 자유 + 드래그 중 감시 pause
    plan = winutil.window_fix_plan((300, 900, 1600, 900), work=(0, 0, 1920, 1040))
    check("창 크기는 1200×750 으로 복원", plan["resize"] and plan["width"] == 1200 and plan["height"] == 750, str(plan))
    check("창 위치는 건드리지 않음(드래그 자유)", plan["x"] == 300 and plan["y"] == 900 and not plan["moved"], str(plan))
    guard = winutil.WindowGuard("SA AIM LABS")
    check("창 감시자에 드래그 일시정지(pause) 있음", hasattr(guard, "pause"), "")
    guard.pause(0.5)
    check("감시자 pause 가 이동을 기록", guard._pause_until > 0, str(guard._pause_until))

    # ---- 3·17. Drs 프로필 + 계단현상 병합 · 변경 내역
    tmp = Path(tempfile.mkdtemp(prefix="sa_v130_drs_"))
    drs_dir = tmp / "Drs"
    drs_dir.mkdir()
    for name in ("nvdrsdb0.bin", "nvdrsdb1.bin", "nvdrssel.bin", "nvAppTimestamps", "nvdrswr.lk"):
        shutil.copy2(ROOT / "assets" / "profiles" / "rifle" / name, drs_dir / name)
    mgr = nvidia.DrsManager(drs_dir=drs_dir, backup_root=tmp / "bk",
                            profiles_root=ROOT / "assets" / "profiles", force_platform=True)
    check("적용 전 = 라플 팩, 계단현상 기본값", mgr.current_mode() == "rifle"
          and drs.aa_compat(mgr.live_blob()) == 0x00020041, hex(drs.aa_compat(mgr.live_blob()) or 0))
    applied = mgr.apply("sniper")
    check("스나 최적화 적용 성공", applied.get("ok") is True, str(applied.get("message"))[:60])
    check("스나 최적화에 계단현상 끄기가 병합됨", drs.aa_compat(mgr.live_blob()) == 0x00401241,
          hex(drs.aa_compat(mgr.live_blob()) or 0))
    check("적용 후 모드 = 스나 팩(병합 상태 인식)", mgr.current_mode() == "sniper", str(mgr.current_mode()))
    rows = applied.get("changes") or []
    check("변경 내역에 계단현상 끄기 항목", any(c.get("key") for c in rows), str(rows)[:120])
    again = mgr.apply("sniper")
    check("같은 팩 재적용은 중복 복사 없이 '이미 적용'", again.get("unchanged") is True, str(again.get("message")))
    restored = mgr.restore_latest()
    check("복구 한 번에 프로필+계단현상 모두 되돌림", restored.get("ok")
          and drs.aa_compat(mgr.live_blob()) == 0x00020041, hex(drs.aa_compat(mgr.live_blob()) or 0))
    modes = mgr.available_modes()
    check("엔비디아 모드는 라플·스나 2가지", all(k in modes for k in ("rifle", "sniper")), str(list(modes)))

    # ---- 4. 병영수첩 일일 변화 + 한마디 충고
    today = dt.date.today()
    snaps = [
        {"date": (today - dt.timedelta(days=1)).isoformat(), "at": "2026-11-23T19:00:00",
         "stats": {"win": 47.0, "kd": 51.5, "dmg": 1009}, "tier": {"solo": {"rp": 3261}, "party": {"rp": 3400}}},
        {"date": today.isoformat(), "at": "2026-11-24T20:00:00",
         "stats": {"win": 48.5, "kd": 53.0, "dmg": 1130}, "tier": {"solo": {"rp": 3300}, "party": {"rp": 3420}}},
    ]
    digest = scoring.barracks_digest(snaps, today)
    check("병영수첩 변화 계산됨", digest["available"] and len(digest["metrics"]) == 5, str(digest.get("from_date")))
    win = next(m for m in digest["metrics"] if m["key"] == "win")
    check("승률 변화량 +1.5", win["diff"] == 1.5, str(win))
    check("한마디 충고 문장 생성", isinstance(digest["advice"], str) and len(digest["advice"]) > 10,
          digest["advice"][:40])
    check("첫 기록뿐이면 비교 불가 안내", scoring.barracks_digest(snaps[1:], today).get("available") is False, "")

    # ---- 8·16. 18단계 분포 · 상세
    dist = scoring.tier_distribution()
    total = sum(r["pct"] for r in dist["rows"])
    check("18단계 분포 18행", len(dist["rows"]) == 18, str(len(dist["rows"])))
    check("분포 합계 ≈ 100%", 99.0 <= total <= 101.0, f"{total:.1f}")
    check("집계 부족 시 기준 분포 사용", dist["source"] == "baseline", dist["source"])
    live = scoring.tier_distribution([20, 5] + [0] * 16, source="live")
    check("실제 집계가 있으면 live 분포", live["source"] == "live" and live["total"] == 25
          and live["rows"][0]["pct"] == 80.0, str({k: live[k] for k in ("source", "total")}))
    check("집계가 20명 미만이면 기준 분포로 폴백", scoring.tier_distribution([1, 2, 3])["source"] == "baseline",
          scoring.tier_distribution([1, 2, 3])["source"])
    detail = scoring.tier_detail(6, 1966, dist, {"precision": 76, "wide": 64, "micro": 41, "track": 58, "switch": 71})
    check("티어 상세 — 내 티어 표시", detail["is_current"] is True, str(detail["tier"]["n"]))
    check("티어 상세 — 골드 I 상위 비율 계산", detail["above_pct"] > 0 and detail["share_pct"] > 0,
          f"above={detail['above_pct']} share={detail['share_pct']}")
    check("티어 상세 — 다음 티어까지 점수", detail["points_to_next"] == 34, str(detail["points_to_next"]))
    check("티어 상세 — 상위 퍼센트/팁", detail["above_pct"] > 0 and len(detail["tips"]) >= 2,
          f"{detail['above_pct']}% · {len(detail['tips'])}")
    check("티어 상세 — 가장 약한 영역 지목", detail["weak_area"] == "micro", str(detail["weak_area"]))

    # ---- 6. 맵 가이드 (가짜 영상 ID 없이 유튜브 검색)
    guide = scoring.map_guide({"map": "이탈리아-폭파", "games": 6, "win": 33.3, "kd": 43.5,
                               "dmg": 836.8, "weak": 61.9})
    check("맵 가이드 팁 3개 이상", len(guide["tips"]) >= 3, str(len(guide["tips"])))
    check("유튜브 링크는 실제 검색 URL", guide["youtube"]["url"].startswith("https://www.youtube.com/results?search_query="),
          guide["youtube"]["url"][:70])
    check("맵 이름 정리(이탈리아)", guide["clean"] == "이탈리아", guide["clean"])

    # ---- 5·10·14·15. 상태: 기록·추천 행 밀도 · 초기화
    base = Path(tempfile.mkdtemp(prefix="sa_v130_api_"))
    store = build_store(base)
    from app.api import Api

    api = Api(store=store)
    state = api._build_state()
    check("state 에 v1.3.0 키", state.get("v130") is True, str(state.get("v130")))
    check("state 에 티어 분포·상세 18개", len(state["tier_distribution"]["rows"]) == 18
          and len(state["tier_details"]) == 18, str(len(state["tier_details"])))
    check("state 에 병영수첩 변화(오늘 미확인 → show)", state["digest"]["available"]
          and state["digest"]["show"] is True, str(state["digest"].get("show")))
    recs = state["summaries"]["kovak-sniper"]["recs"]
    row = recs[0]
    check("추천 행에 PB·평균·다음 티어 목표", all(k in row for k in ("best", "avg", "next_target")),
          str({k: row.get(k) for k in ("best", "avg", "next_target")}))
    check("추천 행에 역대 점수 history", isinstance(row.get("history"), list), str(len(row.get("history") or [])))
    check("state 에 맵 가이드 10개", len(state.get("map_guides") or []) == 10, str(len(state.get("map_guides") or [])))

    # ---- 24. 날짜가 바뀌어도 PB/평균은 유지 (오늘 기록만 리셋)
    yday = today - dt.timedelta(days=1)
    past = [{"date": yday.isoformat(), "game": "kovak", "weapon": "sniper", "scores": {"precision": 161},
             "scenarios": [{"name": "VT 1w6ts Rasp Novice", "area": "precision", "score": 161}]}]
    rows = scoring.recs_for_names(["VT 1w6ts Rasp Novice"], values=scoring.profile_values(past),
                                  records=past, game="kovak", weapon="sniper", today=today)
    check("다음 날에도 역대 최고(PB) 유지", rows and rows[0]["best"] == 161, str(rows[0]["best"] if rows else None))
    check("다음 날 평균·추이 유지", rows and rows[0]["avg"] == 161 and len(rows[0]["history"]) == 1,
          f"{rows[0]['avg']} · {len(rows[0]['history'])}")
    check("오늘 안 쳤으면 today_score 는 비어 있음", rows and rows[0]["today_score"] is None and not rows[0]["done"],
          str(rows[0]["today_score"]))

    ack = api.ack_digest()
    check("오늘 다시 보지 않기 → show=False", ack["state"]["digest"]["show"] is False,
          str(ack["state"]["digest"].get("ack")))

    heat_before = state["heatmap"]["total"]
    days_before = state["streak"]["current"]
    bad = api.reset_scores("아무거나")
    check("초기화는 '초기화' 확인 문구 필요", bad.get("ok") is False, str(bad.get("message"))[:40])
    cleared = api.reset_scores("초기화")
    check("점수 전체 초기화 실행", cleared.get("ok") and cleared["state"]["record_count"] == 0,
          str(cleared.get("message")))
    check("초기화 후 훈련 잔디 유지", cleared["state"]["heatmap"]["total"] == heat_before,
          f"{cleared['state']['heatmap']['total']} vs {heat_before}")
    check("초기화 후 연속 훈련일 유지", cleared["state"]["streak"]["current"] == days_before,
          str(cleared["state"]["streak"]["current"]))
    check("초기화 후 순위표·닉네임 유지", cleared["state"]["settings"]["nickname"] == "휴가중."
          and len(cleared["state"]["leaderboard"]) >= 1, str(len(cleared["state"]["leaderboard"])))
    check("초기화 후 점수 0", cleared["state"]["profile"]["score"] == 0, str(cleared["state"]["profile"]["score"]))


# --------------------------------------------------------------------- UI mock
def ui_state(store) -> dict:
    from app.api import Api

    api = Api(store=store)
    state = api._build_state()
    # 24번 — 날짜가 바뀌어도 PB 는 계속 보인다: 오늘 추천 행에 역대 기록을 붙여 확인
    recs = state["summaries"]["kovak-sniper"]["recs"]
    if recs:
        recs[0].update({
            "name": "VT 1w6ts Rasp Novice", "area": 0, "area_key": "precision",
            "best": 161, "avg": 153.6, "today_score": 161, "done": True, "score": 161,
            "next_target": 200,
            "history": [{"date": "2026-11-20", "score": 146}, {"date": "2026-11-21", "score": 152},
                        {"date": "2026-11-22", "score": 158}, {"date": "2026-11-23", "score": 161}],
        })
    if len(recs) > 1:
        recs[1].update({
            "name": "Wide Wall 3 Targets TE", "area": 1, "area_key": "wide",
            "best": 132, "avg": 120.5, "today_score": None, "done": False, "score": 128,
            "next_target": 200,
            "history": [{"date": "2026-11-21", "score": 112}, {"date": "2026-11-23", "score": 128}],
        })
    state["update"] = {"ok": True, "current": "1.3.0", "latest": "1.3.0", "tag": "v1.3.0", "newer": False,
                       "message": "최신입니다"}
    state["utils"] = {**(state.get("utils") or {}), "supported": True, "mode": None, "alias": False,
                      "backups": [], "points": [], "modes": {"rifle": True, "sniper": True, "stairfix": True},
                      "missing": [], "accel": True, "pending": False,
                      "stairfix": {"ready": True, "applied": False, "value": "0x00020041",
                                   "label": "기본값", "target": "0x00401241"},
                      "last_changes": {"at": "2026-11-24T20:12:00+09:00", "mode": "sniper", "label": "스나 프로필 적용 전",
                                       "count": 1,
                                       "rows": [{"id": "0x00D55F7D", "name": "계단현상 끄기",
                                                 "en": "Antialiasing compatibility (DX9)",
                                                 "before": "0x00020041", "after": "0x00401241", "key": True}]}}
    return state


def mock_js(state: dict) -> str:
    return r"""
    window.__CALLS = [];
    window.__PY_STATE = __STATE_JSON__;
    const S = () => JSON.parse(JSON.stringify(window.__PY_STATE));
    window.pywebview = {api: {
      async bootstrap() { return {ok: true, state: S()}; },
      async get_state() { return {ok: true, state: S()}; },
      async bridge_ping() { return {ok: true, pong: true}; },
      async get_nvidia_state() { return {ok: true, utils: S().utils || {}, state: S()}; },
      async drag_window() { window.__CALLS.push(['drag_window']); return {ok: true, supported: true}; },
      async start_window_drag() { window.__CALLS.push(['start_window_drag']); return {ok: true, supported: true}; },
      async end_window_drag() { return {ok: true}; },
      async move_window(dx, dy) { return {ok: true, supported: true}; },
      async toggle_maximize() { return {ok: true}; },
      async minimize_window() { return {ok: true}; },
      async close_window() { window.__CALLS.push(['close_window']); return {ok: true}; },
      async save_settings(patch) { window.__CALLS.push(['save_settings', patch]);
        const st = S(); st.settings = {...(st.settings||{}), ...patch}; return {ok: true, state: st}; },
      async ack_digest() { window.__CALLS.push(['ack_digest']);
        const st = S(); st.digest = {...(st.digest||{}), show: false, ack: true}; return {ok: true, state: st}; },
      async reset_scores(word) { window.__CALLS.push(['reset_scores', word]);
        const st = S(); st.record_count = 0; st.records = []; st.profile = {...(st.profile||{}), score: 0};
        return {ok: true, state: st, message: '점수 기록 5건을 초기화했습니다'}; },
      async tray_hide() { window.__CALLS.push(['tray_hide']); return {ok: true, hidden: true,
        message: '트레이로 숨겼습니다'}; },
      async open_external(url) { window.__CALLS.push(['open_external', url]); return {ok: true}; },
      async launch_trainer(url) { return {ok: true, url}; },
      async save_training_session(p) { window.__CALLS.push(['save_training_session', p]); return {ok: true, state: S()}; },
      async fetch_barracks(nick, key) { window.__CALLS.push(['fetch_barracks', nick, key]);
        return {ok: true, barracks: S().barracks, state: S(), calls: 3, message: '최신화 완료'}; },
      async fetch_leaderboard(names) { return {ok: true, members: [], renamed: [], message: '갱신', state: S()}; },
      async add_leaderboard_member(n, d, o) { return {ok: true, state: S(), message: '추가'}; },
      async remove_leaderboard_member(n) { return {ok: true, state: S()}; },
      async apply_nvidia_profile(mode, elevate) {
        window.__CALLS.push(['apply_nvidia_profile', mode, elevate]);
        const st = S();
        st.utils = {...(st.utils||{}), mode: mode, alias: false};
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
                       label_value: '계단현상·타격효과 제거', status: 'ok'}],
          message: (mode === 'rifle' ? '라플 프로필' : '스나 프로필') + ' + 계단현상 끄기 적용 완료 · 2개 항목 변경'};
      },
      async toggle_nvidia_alias(v) { return {ok: true, state: S(), message: '복구 완료'}; },
      async restore_nvidia_profile() { window.__CALLS.push(['restore_nvidia_profile']);
        return {ok: true, state: S(), message: '복구 완료'}; },
      async set_mouse_accel(v) { window.__CALLS.push(['set_mouse_accel', v]);
        return {ok: true, enabled: !!v, state: S(), message: v ? '마우스 가속 켬' : '마우스 가속 끔'}; },
      async perf_state(force) { return {ok: true, perf: {supported: true, power: {guid: 'x', name: '균형 조정'},
        game_mode: {enabled: null}, optimal: false, label: '균형 조정', backup: null, applied_at: null}}; },
      async apply_perf_mode() { window.__CALLS.push(['apply_perf_mode']);
        return {ok: true, message: '⚡ 적용 완료', perf: {supported: true, power: {name: '최고 성능'},
          game_mode: {enabled: true}, optimal: true, label: '최고 성능', backup: {}, applied_at: '2026-11-24T20:30:00'}}; },
      async restore_perf_mode() { window.__CALLS.push(['restore_perf_mode']);
        return {ok: true, message: '↩ 복구', perf: {supported: true, power: {name: '균형 조정'},
          game_mode: {enabled: null}, optimal: false, label: '균형 조정'}}; },
      async export_backup() { return {ok: true, path: 'x.json', message: '백업 완료'}; },
      async import_backup() { return {ok: true, state: S()}; },
      async open_data_folder() { return {ok: true}; },
      async check_update(force) { return S().update; },
      async compare_friend(name) { return {ok: false, message: '비교 없음'}; },
      async warmup_plan(g, w) { return {ok: true, recs: []}; },
      async export_playlist() { return {ok: true, message: '저장'}; },
      async import_kovak_scores() { return {ok: true, matched: [], message: '없음'}; },
      async save_report_card(a, b) { return {ok: true, message: '저장'}; },
      async cloud_status() { return {ok: true, cloud: {}, state: S()}; }
    }};
    """.replace("__STATE_JSON__", json.dumps(state, ensure_ascii=False))


# --------------------------------------------------------------------- UI checks
def ui_checks() -> None:
    from playwright.sync_api import sync_playwright

    base = Path(tempfile.mkdtemp(prefix="sa_v130_ui_"))
    store = build_store(base)
    state = ui_state(store)
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
        page.wait_for_timeout(700)

        # ---------- 4. 당일 첫 실행 병영수첩 변화 팝업 (자동)
        dig = page.evaluate("""() => {
            const mask = document.getElementById('digest-mask');
            const rows = [...document.querySelectorAll('#digest-metrics .digest-metric')];
            return {open: mask.classList.contains('show'), rows: rows.length,
                    advice: (document.getElementById('digest-advice')||{}).textContent,
                    ack: !!document.getElementById('digest-ack'),
                    text: rows.map(r => r.innerText.split('++').join(' ').trim())};
        }""")
        page.screenshot(path=str(SHOTS / "v130-digest.png"))
        check("첫 실행에 병영수첩 변화 팝업 자동 표시", dig["open"], str(dig["open"]))
        check("변화 지표 5개(승률·K/D·딜량·솔로/파티 RP)", dig["rows"] == 5, str(dig["rows"]))
        check("한마디 충고 표시", "좋아졌" in (dig["advice"] or "") or len(dig["advice"] or "") > 8, (dig["advice"] or "")[:40])
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#digest-ack")
        page.wait_for_timeout(400)
        names = page.evaluate("() => window.__CALLS.map(c => c[0])")
        dig_open = page.evaluate("() => document.getElementById('digest-mask').classList.contains('show')")
        check("'오늘 다시 보지 않기' → ack_digest 호출", "ack_digest" in names, str(names))
        check("확인 후 팝업 닫힘", dig_open is False, str(dig_open))

        # ---------- 11 · 23. 8개 메뉴 · 설정 팝업
        nav = page.evaluate("""() => [...document.querySelectorAll('.nav-item')].map(b => b.dataset.screen)""")
        expect = ["home", "dashboard", "recs", "records", "barracks", "maps", "leaderboard", "tiers", "utils"]
        check("사이드바 9개 메뉴로 세분화", nav == expect, str(nav))
        check("설정은 메뉴에서 빠짐", "settings" not in nav, str(nav))
        page.click("#settings-btn")
        page.wait_for_timeout(450)
        cfg = page.evaluate("""() => ({
            open: document.getElementById('settings-mask').classList.contains('show'),
            tabs: [...document.querySelectorAll('#set-tabs button')].map(b => b.textContent.trim()),
            key: !!document.getElementById('set-key'), nick: !!document.getElementById('set-nick'),
            cloud: !!document.getElementById('set-cloud'), stats: !!document.getElementById('set-kovak-stats'),
            reset: !!document.getElementById('set-reset-scores'),
            trayToggle: !!document.querySelector('[data-switch="tray_background"]')
        })""")
        page.screenshot(path=str(SHOTS / "v130-settings.png"))
        check("프로필/톱니바퀴 → 설정 팝업", cfg["open"] and cfg["key"] and cfg["nick"], str(cfg["open"]))
        check("설정 팝업에 API 키·닉네임·클라우드·코박스 폴더", cfg["cloud"] and cfg["stats"], str(cfg))
        check("설정 팝업에 점수 전체 초기화", cfg["reset"], str(cfg["reset"]))
        check("'닫으면 트레이로 숨기기' 옵션 없음", cfg["trayToggle"] is False, str(cfg["trayToggle"]))
        check("설정 팝업 탭 구성", len(cfg["tabs"]) == 4, str(cfg["tabs"]))

        # ---------- 10. 점수 전체 초기화 2중 확인
        page.click("#set-reset-scores")
        page.wait_for_timeout(350)
        rst = page.evaluate("""() => ({open: document.getElementById('reset-mask').classList.contains('show'),
            disabled: document.getElementById('reset-go').disabled,
            text: document.getElementById('reset-mask').innerText})""")
        check("초기화 1차 확인창(문구 입력)", rst["open"] and rst["disabled"], str(rst["disabled"]))
        check("초기화 범위 안내 — 잔디는 유지", "훈련 잔디" in rst["text"] and "유지" in rst["text"], rst["text"][:70])
        page.fill("#reset-word", "초기화")
        page.wait_for_timeout(200)
        enabled = page.evaluate("() => !document.getElementById('reset-go').disabled")
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#reset-go")
        page.wait_for_timeout(500)
        names = page.evaluate("() => window.__CALLS.map(c => c[0])")
        check("확인 문구 입력 후 초기화 버튼 활성화", enabled, str(enabled))
        check("초기화 실행 → reset_scores('초기화')", "reset_scores" in names, str(names))
        page.evaluate("() => document.querySelector('#settings-mask').classList.remove('show')")
        page.wait_for_timeout(200)

        # ---------- 3 · 18. 유틸: 버튼 2개 · 토글 스위치
        page.click('.nav-item[data-screen="utils"]')
        page.wait_for_timeout(450)
        page.click('[data-util-tab="system"]')
        page.wait_for_timeout(250)
        utils = page.evaluate("""() => ({
            applies: [...document.querySelectorAll('#s-utils [data-apply]')].map(b => b.dataset.apply),
            alias: !!document.querySelector('[data-apply="alias"]'),
            switches: [...document.querySelectorAll('#s-utils .switch')].map(s => s.id),
            oldButtons: !!document.getElementById('accel-btn') || !!document.getElementById('perf-apply'),
            descs: [...document.querySelectorAll('#s-utils .util-desc')].map(d => d.textContent)
        })""")
        page.screenshot(path=str(SHOTS / "v130-utils.png"))
        check("최적화 버튼 2개(라플·스나)만", utils["applies"] == ["rifle", "sniper"], str(utils["applies"]))
        check("계단현상 단독 버튼 제거", utils["alias"] is False, str(utils["alias"]))
        check("온/오프 토글 스위치(가속·전원)", "accel-switch" in utils["switches"] and "perf-switch" in utils["switches"],
              str(utils["switches"]))
        check("기존 버튼형 토글 제거", utils["oldButtons"] is False, str(utils["oldButtons"]))
        page.evaluate("() => { window.__CALLS = []; }")
        page.click('[data-apply="rifle"]')
        page.wait_for_timeout(700)
        calls = page.evaluate("() => window.__CALLS.map(c => c[0])")
        chg = page.evaluate("""() => ({open: document.getElementById('changes-mask').classList.contains('show'),
            rows: [...document.querySelectorAll('#change-list .change-row')].map(r => r.innerText.split('++').join(' ').trim())})""")
        page.screenshot(path=str(SHOTS / "v130-changes.png"))
        check("라플 최적화 적용 호출", "apply_nvidia_profile" in calls, str(calls))
        check("적용 후 변경 내역 모달 표시", chg["open"] and len(chg["rows"]) >= 2, str(chg["rows"])[:80])
        check("변경 내역에 전역 프로필·파일 증거", any("Base" in r or "0x10F9DC81" in r for r in chg["rows"])
              or any("nvdrsdb0.bin" in r for r in chg["rows"]), str(chg["rows"])[-200:])
        check("변경 내역에 0x00401241 표시", any("0x00401241" in r for r in chg["rows"]), str(chg["rows"])[:100])
        page.evaluate("() => document.querySelector('#changes-mask').classList.remove('show')")
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#accel-switch")
        page.wait_for_timeout(500)
        accel = page.evaluate("() => ({calls: window.__CALLS.map(c => c[0]), on: document.getElementById('accel-switch').classList.contains('on')})")
        check("마우스 가속 토글 → set_mouse_accel 호출", "set_mouse_accel" in accel["calls"], str(accel["calls"]))

        # ---------- 6 · 7. 맵·약점 분석: 정렬 · 공략 팝업
        page.click('.nav-item[data-screen="maps"]')
        page.wait_for_timeout(450)
        order_before = page.evaluate("""() => [...document.querySelectorAll('#map-table tbody tr td.map')].map(t => t.textContent.trim())""")
        page.click('#map-table th[data-sort="win"]')
        page.wait_for_timeout(350)
        order_after = page.evaluate("""() => [...document.querySelectorAll('#map-table tbody tr td.map')].map(t => t.textContent.trim())""")
        arrows = page.evaluate("""() => [...document.querySelectorAll('#map-table th.sortable.on')].map(t => t.textContent.trim())""")
        check("맵 표 헤더 클릭 → 정렬 순서 변경", order_before != order_after, f"{order_before[:2]} → {order_after[:2]}")
        check("정렬 화살표 표시", any(("▼" in a or "▲" in a) for a in arrows), str(arrows))
        page.click("#map-table th[data-sort='win']")
        page.wait_for_timeout(300)
        order_asc = page.evaluate("""() => [...document.querySelectorAll('#map-table tbody tr td.map')].map(t => t.textContent.trim())""")
        esc = page.evaluate("""() => [...document.querySelectorAll('#map-table tbody tr')].map(tr => ({
            map: tr.querySelector('td.map').textContent.trim(),
            win: parseFloat(tr.querySelectorAll('td')[3].textContent)}))""")
        wins = [r["win"] for r in esc]
        check("같은 헤더 재클릭 → 오름차순(최소 승률이 맨 위)", wins == sorted(wins), str(wins))
        page.screenshot(path=str(SHOTS / "v130-maps.png"))

        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#weak-list .weak")
        page.wait_for_timeout(450)
        guide = page.evaluate("""() => ({open: document.getElementById('guide-mask').classList.contains('show'),
            map: (document.getElementById('guide-map')||{}).textContent,
            tips: document.querySelectorAll('#guide-tips .guide-tip').length,
            url: (document.getElementById('guide-yt-open')||{}).dataset ? document.getElementById('guide-yt-open').dataset.url : '',
            yt: document.getElementById('guide-mask').innerText})""")
        page.screenshot(path=str(SHOTS / "v130-guide.png"))
        check("약점 TOP3 클릭 → 공략 팝업", guide["open"] and guide["map"] == "이탈리아-폭파", str(guide["map"]))
        check("공략 팁 3개 이상", guide["tips"] >= 3, str(guide["tips"]))
        check("유튜브 공략 버튼(실제 검색 URL)", guide["url"].startswith("https://www.youtube.com/results?search_query="),
              guide["url"][:60])
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#guide-yt-open")
        page.wait_for_timeout(400)
        names = page.evaluate("() => window.__CALLS.filter(c => c[0] === 'open_external').map(c => c[1])")
        check("영상 보기 → 외부 브라우저 열기", bool(names) and "youtube.com" in names[0], str(names)[:80])
        page.evaluate("() => document.querySelector('#guide-mask').classList.remove('show')")

        # ---------- 8 · 16. 티어표 분포 · 상세 팝업
        page.click('.nav-item[data-screen="tiers"]')
        page.wait_for_timeout(450)
        tiers = page.evaluate("""() => ({
            rows: document.querySelectorAll('#tier-table .tier-row:not(.hd)').length,
            dist: document.querySelectorAll('#tier-table .tier-dist').length,
            now: document.querySelectorAll('#tier-table .tier-row.me-row').length,
            nowText: (document.querySelector('#tier-table .tier-row.me-row')||{}).innerText || '',
            note: (document.getElementById('tier-dist-note')||{}).textContent
        })""")
        page.screenshot(path=str(SHOTS / "v130-tiers.png"))
        check("티어표 18행 + 분포 막대", tiers["rows"] == 18 and tiers["dist"] == 18, str(tiers))
        check("내 티어 강조(me-row)", tiers["now"] == 1 and "내 위치" in tiers["nowText"], tiers["nowText"][:40])
        check("분포 출처 안내", "분포" in (tiers["note"] or ""), (tiers["note"] or "")[:40])
        page.click('#tier-table .tier-row[data-tier="6"]')
        page.wait_for_timeout(400)
        td = page.evaluate("""() => ({open: document.getElementById('tier-mask').classList.contains('show'),
            name: (document.getElementById('tier-name')||{}).textContent,
            share: (document.getElementById('tier-share')||{}).textContent,
            top: (document.getElementById('tier-top')||{}).textContent,
            need: (document.getElementById('tier-need')||{}).textContent,
            tips: document.querySelectorAll('#tier-tips .guide-tip').length})""")
        page.screenshot(path=str(SHOTS / "v130-tier-detail.png"))
        check("티어 클릭 → 상세 팝업", td["open"] and td["name"] == "골드 I", str(td["name"]))
        check("상세에 분포·상위 퍼센트", td["share"].endswith("%") and "상위" in td["top"], f"{td['share']} / {td['top']}")
        check("상세에 다음 티어 조건·팁", len(td["need"]) > 6 and td["tips"] >= 2, f"{td['need']} · {td['tips']}")
        page.evaluate("() => document.querySelector('#tier-mask').classList.remove('show')")

        # ---------- 2 · 14 · 15. 대시보드 추천 행 밀도 · PB 배지 · 추이 그래프
        page.click('.nav-item[data-screen="dashboard"]')
        page.wait_for_timeout(500)
        recs = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#rec-list .rec')];
            const pb = document.querySelector('#rec-list .rec-row-pb');
            const row = pb ? pb.closest('.rec') : null;
            const rr = row ? row.getBoundingClientRect() : null;
            const br = pb ? pb.getBoundingClientRect() : null;
            return {rows: rows.length, heights: rows.map(r => Math.round(r.getBoundingClientRect().height)),
                    pbText: pb ? pb.textContent.trim() : '',
                    clipped: pb ? (br.top < rr.top - 0.5 || br.bottom > rr.bottom + 0.5) : false,
                    meta: rows.map(r => (r.querySelector('.rec-stat2')||{}).innerText || '')};
        }""")
        check("추천 5종 행", recs["rows"] >= 5, str(recs["rows"]))
        check("PB 배지가 행 안에서 잘리지 않음", recs["pbText"] and not recs["clipped"],
              f"{recs['pbText']} clipped={recs['clipped']}")
        check("각 행에 최고 기록 표시 (평균·목표 표기 삭제)", all("최고" in m for m in recs["meta"]),
              str(recs["meta"][:1]))
        check("추천 행에서 평균·목표 문구 제거", not any(("평균" in m or "목표" in m) for m in recs["meta"]),
              str(recs["meta"][:1]))
        todayless = page.evaluate("""() => {
            const row = [...document.querySelectorAll('#rec-list .rec')].find(r => r.querySelector('.rec-today.none'));
            if (!row) return null;
            return {name: (row.querySelector('.rec-name')||{}).textContent,
                    stats: (row.querySelector('.rec-stat2')||{}).innerText.split('++').join(' '),
                    today: (row.querySelector('.rec-today')||{}).textContent};
        }""")
        check("오늘 안 친 시나리오도 최고 기록은 그대로 표시",
              bool(todayless) and "최고" in (todayless["stats"] or "") and "132" in (todayless["stats"] or "")
              and "미기록" in (todayless["today"] or ""), str(todayless))
        page.screenshot(path=str(SHOTS / "v130-dashboard.png"))
        page.click("#rec-list .rec")
        page.wait_for_timeout(450)
        hist = page.evaluate("""() => ({open: document.getElementById('scen-mask').classList.contains('show'),
            paths: document.querySelectorAll('#scen-hist path').length,
            note: (document.getElementById('scen-hist-note')||{}).textContent})""")
        page.screenshot(path=str(SHOTS / "v130-scen-history.png"))
        check("시나리오 클릭 → 역대 점수 추이 그래프", hist["open"] and hist["paths"] >= 2, str(hist))
        page.evaluate("() => document.querySelector('#scen-mask').classList.remove('show')")

        # ---------- 5. 잔디 하단 잘림
        page.click('.nav-item[data-screen="records"]')
        page.wait_for_timeout(500)
        heat = page.evaluate("""() => {
            const card = document.getElementById('heat-card');
            const cells = [...document.querySelectorAll('#heatmap .heat-cell')];
            const last = cells[cells.length - 1].getBoundingClientRect();
            const cr = card.getBoundingClientRect();
            const cols = document.querySelectorAll('#heatmap .heat-col').length;
            return {cols, cellCount: cells.length, overflow: +(last.bottom - cr.bottom).toFixed(1),
                    rows: cells.length / (cols || 1)};
        }""")
        page.screenshot(path=str(SHOTS / "v130-records.png"))
        check("훈련 잔디 12주 × 7행", heat["cols"] == 12 and heat["rows"] == 7, str(heat))
        check("잔디 맨 아래 줄이 카드 안에 들어옴", heat["overflow"] <= 0.5, f"overflow={heat['overflow']}px")

        # ---------- 9 · 13. 내부 문구·기준 표기 제거
        body = page.inner_text("body")
        banned = ["추가 API 0건", "추가 API 호출", "저장된 순위표·병영수첩 값만 사용", "로컬 저장", "스나 기준", "라플 기준"]
        left = [b for b in banned if b in body]
        check("내부 구현 문구·'기준' 표기 제거", not left, str(left))
        check("타이틀의 '스나 유저 AUTO' 배지 제거", page.evaluate("() => !document.getElementById('tb-user')"), "")

        # ---------- 12 · 19 · 20 · 21. 애니메이션 · 툴팁 · 글씨 크기 · 호버
        page.click('.nav-item[data-screen="dashboard"]')
        page.wait_for_timeout(200)
        anim = page.evaluate("""() => {
            const s = document.getElementById('s-dashboard');
            const cs = getComputedStyle(s);
            return {name: cs.animationName, dur: cs.animationDuration,
                    cls: s.classList.contains('enter')};
        }""")
        check("화면 전환 애니메이션 0.3~0.4초", anim["cls"] and anim["name"] == "screenIn"
              and 0.25 <= float(str(anim["dur"]).replace("s", "") or 0) <= 0.45, str(anim))
        page.mouse.move(640, 420)
        page.wait_for_timeout(200)
        page.mouse.move(30, 600)
        page.wait_for_timeout(400)
        tip_box = page.evaluate("""() => { const t = document.querySelector('.micro .tip'); const r = t.getBoundingClientRect(); return {x: r.left + r.width/2, y: r.top + r.height/2}; }""")
        page.mouse.move(tip_box["x"], tip_box["y"])
        page.wait_for_timeout(400)
        tip = page.evaluate("""() => { const l = document.getElementById('tip-layer');
            return {exists: !!l, show: l ? l.classList.contains('show') : false,
                    text: l ? l.textContent.slice(0, 40) : ''}; }""")
        check("? 아이콘 호버 툴팁", tip["exists"] and tip["show"] and len(tip["text"]) > 2, str(tip))
        fonts = page.evaluate("""() => {
            const fs = (sel) => { const el = document.querySelector(sel); return el ? parseFloat(getComputedStyle(el).fontSize) : 0; };
            return {rec: fs('.rec-name'), sub: fs('.card-sub'), nav: fs('.nav-item'),
                    th: fs('table.maps-t th'), foot: fs('.kpi-foot')};
        }""")
        check("글씨 크기 상향(본문 12px+)", fonts["rec"] >= 12 and fonts["nav"] >= 12.5 and fonts["sub"] >= 10.5, str(fonts))
        hover = page.evaluate("""() => {
            const plain = document.querySelector('#s-dashboard .card:not(.pop)');
            const pop = document.querySelector('#s-dashboard .card.pop');
            return {plain: plain ? getComputedStyle(plain).transitionProperty : 'MISSING',
                    pop: pop ? getComputedStyle(pop).transitionProperty : 'MISSING',
                    pops: document.querySelectorAll('.card.pop').length};
        }""")
        check("팝업 여는 카드만 호버 볼록 효과 (v1.3.1 6번)",
              "transform" in (hover["pop"] or "") and "transform" not in (hover["plain"] or "") and hover["pops"] >= 3,
              str(hover))

        # ---------- 22. 닫기 X → 확인 팝업
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#win-close")
        page.wait_for_timeout(400)
        close_box = page.evaluate("""() => ({open: document.getElementById('close-mask').classList.contains('show'),
            text: document.getElementById('close-mask').innerText})""")
        page.screenshot(path=str(SHOTS / "v130-close.png"))
        check("X 클릭 → 확인 팝업", close_box["open"] and "트레이로 숨기기" in close_box["text"]
              and "프로그램 종료" in close_box["text"], close_box["text"][:50])
        page.click("#close-to-tray")
        page.wait_for_timeout(400)
        names = page.evaluate("() => window.__CALLS.map(c => c[0])")
        check("트레이로 숨기기 선택 → tray_hide", "tray_hide" in names, str(names))
        page.click("#win-close")
        page.wait_for_timeout(300)
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#close-quit")
        page.wait_for_timeout(350)
        names = page.evaluate("() => window.__CALLS.map(c => c[0])")
        check("프로그램 종료 선택 → close_window", "close_window" in names, str(names))

        real = [e for e in errors if "favicon" not in e.lower()]
        check("UI 런타임 오류 없음", not real, str(real[:2]))
        browser.close()


def main() -> int:
    backend()
    try:
        ui_checks()
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        check("UI 검사 실행", False, str(exc))
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
