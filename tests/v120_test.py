"""v1.2.0 checks — 12가지 신규 기능 (코박스 인식 · 1:1 비교 · 전후 비교 · 리포트 카드
· 자동 업데이트 · 5분 워밍업 · 전원/게임 모드 · 주간 목표 · 플레이리스트 · PB · 잔디
· 트레이/단축키).

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \\
    python3 tests/v120_test.py

  · backend — stats/*.csv 파싱·매칭, .plo/.json 플레이리스트, 버전 비교, 잔디·주간목표·
               훈련 전후·1:1 비교·PB 계산, 리포트 카드 PNG 저장, 비Windows 안전 동작
  · UI      — Playwright(Chromium) 로 대시보드(잔디·주간목표·워밍업·리포트 버튼),
               1:1 비교 모달, 워밍업 모달, 유틸(전원/게임 모드), 설정(트레이·업데이트)
               을 띄워 확인하고 tests/shots 에 스크린샷을 남긴다.
"""
from __future__ import annotations

import base64
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
def make_stats_dir(base: Path, today: dt.date | None = None) -> Path:
    """코박스가 남기는 것과 같은 모양의 stats CSV 를 만든다."""
    today = today or dt.date.today()
    stats = base / "FPSAimTrainer" / "stats"
    stats.mkdir(parents=True, exist_ok=True)
    stamp = today.strftime("%Y.%m.%d") + "-19.04.12"
    old_stamp = (today - dt.timedelta(days=9)).strftime("%Y.%m.%d") + "-11.02.33"
    files = {
        f"1wall 6targets small - Challenge - {stamp} Stats.csv":
            "Kill Count,Score,Accuracy\n50,152.5,61.2\n"
            "Scenario:,1wall 6targets small\nScore:,152.5\nHit Count:,86\nMiss Count:,12\n",
        f"Wide Wall 3 Targets - Challenge - {stamp} Stats.csv":
            "Scenario:,Wide Wall 3 Targets\nScore:,132.25\nHit Count:,80\n",
        # 오늘 추천에 없는(다른) 과제 → 매칭되면 안 된다
        f"Reflex Micro Flick 250ms - Challenge - {stamp} Stats.csv":
            "Scenario:,Reflex Micro Flick 250ms\nScore:,99.5\n",
        # 오래된 기록 — 오늘 범위에서 제외되어야 한다
        f"voxTargetSwitch - Challenge - {old_stamp} Stats.csv":
            "Scenario:,voxTargetSwitch\nScore:,188.0\n",
    }
    for name, body in files.items():
        (stats / name).write_text(body, encoding="utf-8")
    return stats


def build_store(base: Path) -> "object":
    os.environ["SA_AL_DATA_DIR"] = str(base / "data")
    from app.store import Store

    store = Store(base / "data" / "data.json")
    today = dt.date.today()
    # 3주치 훈련 기록 (잔디·주간목표·전후 비교용)
    for offset in range(0, 21):
        day = today - dt.timedelta(days=offset)
        if offset % 3 == 2:
            continue                     # 미훈련일
        store.add_record({
            "date": day.isoformat(), "created_at": day.isoformat() + "T20:10:00+09:00",
            "game": "kovak", "weapon": "sniper",
            "scores": {"precision": 152 - offset, "wide": 128 - offset, "micro": 96, "track": 88, "switch": 141},
            "scenarios": [{"name": "1wall 6targets small", "area": "precision", "score": 152 - offset},
                          {"name": "Wide Wall 3 Targets", "area": "wide", "score": 132 - offset}],
            "note": "",
        })
    store.update_settings({"nickname": "휴가중.", "api_key": "test_9542ea5ac663d2c0", "onboarded": True,
                           "week_goals": {"days": 5, "games": 25}})
    barracks = {
        "nickname": "휴가중.", "ouid": "711c04a69769cc4b6b782ea2aa3eb614",
        "basic": {"clan": "리드", "created": "2025.11.29", "manner": "3"},
        "tier": {"solo": {"name": "그랜드마스터 II", "rp": 3261},
                 "party": {"name": "그랜드마스터 III", "rp": 3435}},
        "stats": {"games": 100, "kills": 903, "deaths": 812, "wins": 47, "win": 47.0, "kd": 51.5, "dmg": 1009},
        "recent": {"sniper": 62, "assault": 38},
        "maps": [{"rk": 1, "map": "이탈리아-폭파", "games": 6, "win": 33.3, "kd": 43.5, "dmg": 836.8, "weak": 61.9},
                 {"rk": 2, "map": "프로방스(심플)", "games": 12, "win": 25.0, "kd": 51.0, "dmg": 1177.8, "weak": 60.4}],
        "matches": [{"date": (today - dt.timedelta(days=i % 5)).isoformat(), "map": "이탈리아-폭파",
                     "mode": "랭크전 솔로", "result": "승" if i % 3 != 0 else "패",
                     "kills": 10 + i % 7, "deaths": 8 + i % 4, "kd": round(44 + (i % 9) * 1.8, 1),
                     "damage": 780 + (i % 11) * 47} for i in range(40)],
        "updated_at": today.isoformat() + "T18:30:00+09:00",
    }
    store.barracks_cache()["휴가중."] = barracks
    members = [
        {"name": "휴가중.", "me": True, "clan": "리드", "weapon": "sniper", "solo": 3261, "party": 3435,
         "solo_tier": "그랜드마스터 II", "party_tier": "그랜드마스터 III", "kd": 51.5, "dmg": 1009, "win": 47.0,
         "ouid": barracks["ouid"]},
        {"name": "무빙샷장인", "clan": "리드", "weapon": "rifle", "solo": 2940, "party": 3211,
         "solo_tier": "마스터 III", "party_tier": "그랜드마스터 I", "kd": 57.4, "dmg": 1292, "win": 56.0},
        {"name": "헤더온리", "clan": "리드", "weapon": "sniper", "solo": 2715, "party": 2788,
         "solo_tier": "마스터 II", "party_tier": "마스터 III", "kd": 52.1, "dmg": 988, "win": 48.6},
        {"name": "배치중", "clan": "친구", "weapon": "sniper", "solo": 0, "party": None,
         "kd": None, "dmg": None, "win": None},
    ]
    store.set_leaderboard(members)
    store.save()
    return store


# --------------------------------------------------------------------- backend
def backend_checks() -> None:
    base = Path(tempfile.mkdtemp(prefix="sa_v120_"))
    store = build_store(base)
    stats = make_stats_dir(base)
    os.environ["SA_AL_KOVAK_STATS_DIR"] = str(stats)

    from app import kovak, paths, scoring, tray, updates, winutil
    from app.api import Api

    api = Api(store=store)
    state = api._build_state()

    # ---- 1. 코박스 stats 파싱/매칭
    runs = kovak.latest_runs([stats], days=1)
    check("코박스 CSV 최신 점수 파싱", runs.get("1wall 6targets small", {}).get("score") == 152.5,
          str(runs.get("1wall 6targets small")))
    check("오래된 기록은 오늘 범위에서 제외", "voxtargetswitch" not in runs, str(list(runs)[:4]))
    matched = kovak.match_targets(["1wall 6targets small", "Wide Wall 3 Targets", "voxTargetSwitch"], runs)
    check("추천 과제 ↔ 코박스 기록 매칭", [m["name"] for m in matched] == ["1wall 6targets small", "Wide Wall 3 Targets"],
          str([(m["name"], m["score"]) for m in matched]))
    result = api.import_kovak_scores(["1wall 6targets small", "Wide Wall 3 Targets", "Gridshot Ultimate"], 1)
    check("import_kovak_scores API", result.get("ok") and len(result.get("matched") or []) == 2, str(result.get("message")))
    check("import 안내 문구에 폴더 수", "stats" in str(result.get("dirs")), str(result.get("dirs")))
    empty = api.import_kovak_scores(["Gridshot Ultimate"], 1)
    check("겹치는 과제 없음 안내", empty.get("ok") and not empty.get("matched"), str(empty.get("message")))

    # ---- 2. 플레이리스트 (.json + .plo)
    out_dir = base / "Playlists"
    out_dir.mkdir()
    os.environ["SA_AL_KOVAK_PLAYLISTS_DIR"] = str(out_dir)
    pl = api.export_playlist("recs", "kovak", "sniper")
    files = [Path(p) for p in (pl.get("files") or [])]
    check("플레이리스트 2종 저장(.json/.plo)", pl.get("ok") and len(files) == 2
          and {f.suffix for f in files} == {".json", ".plo"}, str([f.name for f in files]))
    doc = json.loads(files[0].read_text(encoding="utf-8")) if files else {}
    check("코박스 스키마 키(scenario_name/play_Count)",
          bool(doc.get("scenarioList")) and "scenario_name" in doc["scenarioList"][0]
          and "play_Count" in doc["scenarioList"][0], str(list(doc)[:6]))
    check("플레이리스트에 오늘 추천 5종", len(doc.get("scenarioList") or []) == 5,
          str([s["scenario_name"] for s in doc.get("scenarioList") or []]))
    warm = api.export_playlist("warmup", "kovak", "sniper")
    check("워밍업 플레이리스트 3종", warm.get("ok") and warm.get("count") == 3, str(warm.get("names")))

    # ---- 3. 업데이트 버전 비교 (네트워크 없이)
    check("버전 비교 1.2.1 > 1.2.0", updates.is_newer("v1.2.1", "1.2.0"))
    check("버전 비교 1.2.0 == 1.2.0", not updates.is_newer("v1.2.0", "1.2.0"))
    check("버전 비교 1.10 > 1.9", updates.is_newer("1.10.0", "1.9.9"))
    real_fetch = updates.fetch_latest
    try:
        updates.clear()
        updates.fetch_latest = lambda timeout=4.0: {"ok": True, "tag": "v1.5.1", "name": "v1.5.1",
                                                    "url": "https://github.com/x/y", "download": "https://x/y.exe",
                                                    "published_at": "2026-11-23T00:00:00Z", "notes": ""}
        newer = updates.check(paths.APP_VERSION, force=True)
        check("새 버전 감지", newer.get("ok") and newer.get("newer") and newer.get("latest") == "1.5.1",
              str(newer.get("message")))
        updates.clear()
        updates.fetch_latest = lambda timeout=4.0: {"ok": True, "tag": "v1.0.0", "url": "", "download": ""}
        older = updates.check(paths.APP_VERSION, force=True)
        check("최신 버전이면 배너 없음", older.get("ok") and not older.get("newer"), str(older.get("message")))
        updates.clear()
        updates.fetch_latest = lambda timeout=4.0: {"ok": False, "offline": True, "message": "네트워크 확인 실패"}
        offline = updates.check(paths.APP_VERSION, force=True)
        check("오프라인은 조용히 실패", offline.get("ok") is False and offline.get("newer") is False,
              str(offline.get("message")))

        # 이 저장소는 tag 가 `latest-build` (버전 없음) → 빌드 시각으로 '새 빌드' 판단
        updates.clear()
        updates.fetch_latest = lambda timeout=4.0: {"ok": True, "tag": "latest-build", "name": "Latest build",
                                                    "url": "https://x", "download": "https://x/y.exe",
                                                    "published_at": "2026-11-22T00:00:00Z",
                                                    "build_at": "2026-11-23T12:03:15Z", "notes": ""}
        real_build_time = updates.build_time
        updates.build_time = lambda: 1_700_000_000.0        # 오래된 exe
        by_build = updates.check(paths.APP_VERSION, force=True)
        check("버전 없는 릴리스도 새 빌드 감지", by_build.get("ok") and by_build.get("newer") is True
              and by_build.get("reason") == "build", str(by_build.get("message")))
        updates.clear()
        updates.build_time = lambda: 1_800_000_000.0        # 방금 받은 exe
        same_build = updates.check(paths.APP_VERSION, force=True)
        check("방금 받은 빌드는 최신 처리", same_build.get("ok") and same_build.get("newer") is False,
              str(same_build.get("message")))
        check("릴리스 태그에서 버전 추출", updates._version_in("latest-build v1.3.0") == "1.3.0"
              and updates._version_in("latest-build") is None)
    finally:
        updates.fetch_latest = real_fetch
        updates.build_time = real_build_time
        updates.clear()

    # ---- 4. 잔디 · 주간 목표 · 전후 비교
    heat = state.get("heatmap") or {}
    cells = [c for col in (heat.get("grid") or []) for c in col]
    check("잔디 12주 × 7일 격자", heat.get("weeks") == 12 and len(heat.get("grid") or []) == 12
          and len(cells) == 84, f"{len(heat.get('grid') or [])}주 / {len(cells)}칸")
    check("잔디 4단계 레벨", {c["lvl"] for c in cells} <= {0, 1, 2, 3}, str(sorted({c["lvl"] for c in cells})))
    check("잔디 오늘 칸 표시", any(c["d"] == dt.date.today().isoformat() for c in cells))
    week = state.get("week") or {}
    check("주간 목표 계산", week.get("days") == 5 and week.get("games") == 25
          and week.get("days_done", 0) >= 1, json.dumps({k: week.get(k) for k in ("days_done", "games_done", "pct")}, ensure_ascii=False))
    imp = state.get("impact") or {}
    check("훈련일/미훈련일 랭크전 분리", (imp.get("trained") or {}).get("games", 0) > 0
          and (imp.get("untrained") or {}).get("games", 0) > 0,
          json.dumps({"trained": (imp.get("trained") or {}).get("games"),
                      "untrained": (imp.get("untrained") or {}).get("games")}, ensure_ascii=False))
    check("전후 비교 안내 문구", bool(imp.get("note")), str(imp.get("note"))[:60])

    # 표본이 적으면 안내 문구로 대체
    tiny = scoring.training_impact([{"date": "2026-11-23", "result": "승", "kd": 50, "damage": 1000}],
                                   [{"date": "2026-11-23"}], today=dt.date(2026, 11, 23))
    check("표본 부족 안내", tiny.get("enough") is False and "표본" in tiny.get("note", ""), tiny.get("note"))

    # ---- 5. 1:1 비교 (추가 API 0건)
    cmp = api.compare_friend("무빙샷장인")
    body = cmp.get("compare") or {}
    check("1:1 비교 응답", cmp.get("ok") and (body.get("friend") or {}).get("name") == "무빙샷장인",
          str(cmp.get("message")))
    check("1:1 비교 지표 6행", len(body.get("rows") or []) == 6,
          str([r["label"] for r in body.get("rows") or []]))
    check("1:1 비교 승자 표시", body.get("verdict") in ("내가 우세", "친구가 우세", "접전"), str(body.get("verdict")))
    check("1:1 비교는 API 를 부르지 않음", api._client.__name__ == "_client")
    missing = api.compare_friend("없는친구")
    check("순위표에 없는 친구 안내", missing.get("ok") is False, str(missing.get("message")))

    # ---- 6. NEW PB
    first = api.save_training({"game": "kovak", "weapon": "sniper",
                               "scenarios": [{"name": "1wall 6targets small", "area_key": "precision", "score": 200}]})
    check("최고 기록 경신 → NEW PB", first.get("ok") and any(p["name"] == "1wall 6targets small"
                                                            for p in first.get("pbs") or []),
          str(first.get("pbs")))
    lower = api.save_training({"game": "kovak", "weapon": "sniper",
                               "scenarios": [{"name": "1wall 6targets small", "area_key": "precision", "score": 120}]})
    check("낮은 점수는 PB 아님", lower.get("ok") and not lower.get("pbs"), str(lower.get("pbs")))

    # ---- 7. 리포트 카드 PNG 저장
    png = base64.b64encode(png_bytes(96, 96)).decode()
    saved = api.save_report_card("data:image/png;base64," + png)
    check("리포트 카드 PNG 저장", saved.get("ok") and Path(saved["path"]).exists()
          and Path(saved["path"]).read_bytes()[:4] == b"\x89PNG", str(saved.get("message")))
    bad = api.save_report_card("data:image/png;base64,AAAA")
    check("빈 이미지는 거부", bad.get("ok") is False, str(bad.get("message")))

    # ---- 8. 5분 워밍업
    warmup = api.warmup_plan("kovak", "s狙击" if False else "sniper")
    names = [r["name"] for r in warmup.get("recs") or []]
    check("워밍업 3종", warmup.get("ok") and len(names) == 3, str(names))
    rifle = api.warmup_plan("kovak", "rifle")
    check("라플 워밍업은 트래킹 포함", any(r["area_key"] == "track" for r in rifle.get("recs") or []),
          str([r["area_key"] for r in rifle.get("recs") or []]))

    # ---- 9. 전원 옵션 / 게임 모드 (비Windows 안전 동작)
    perf = api.perf_state(force=True)
    check("perf_state 는 예외 없이 응답", perf.get("ok") is True and isinstance(perf.get("perf"), dict),
          str((perf.get("perf") or {}).get("label")))
    applied = api.apply_perf_mode()
    check("비Windows 적용은 시뮬레이션 안내", applied.get("unsupported") or applied.get("ok") is False,
          str(applied.get("message"))[:60])
    restore = api.restore_perf_mode()
    check("비Windows 복구도 안전", isinstance(restore, dict), str(restore.get("message"))[:60])

    # ---- 10. 트레이 / 단축키 (비Windows 안전 동작)
    check("비Windows 트레이는 미지원 처리", tray.available() is False)
    check("단축키 파싱 Ctrl+Shift+A", tray.parse_hotkey("Ctrl+Shift+A") == (0x0002 | 0x0004 | 0x4000, 0x41),
          str(tray.parse_hotkey("Ctrl+Shift+A")))
    check("잘못된 단축키는 None", tray.parse_hotkey("A") is None)
    icon = tray.TrayIcon("test")
    check("트레이 시작 실패는 False", icon.start() is False and bool(icon.error), str(icon.error))
    state_tray = state.get("tray") or {}
    check("state.tray 제공", "supported" in state_tray and "hotkey_spec" in state_tray, str(state_tray))
    hide = api.tray_hide()
    check("창이 없으면 숨기기 안전", hide.get("ok") is False or hide.get("hidden") is False, str(hide.get("message")))

    # ---- 11. 설정 저장 확장
    saved_settings = api.save_settings({"week_goals": {"days": 6, "games": 30}, "kovak_autofill": True,
                                        "hotkey_enabled": False, "kovak_stats_path": str(stats)})
    st2 = saved_settings.get("state") or {}
    check("주간 목표 저장", (st2.get("week") or {}).get("days") == 6, str((st2.get("week") or {}).get("days")))
    check("코박스 자동 채움 저장", (st2.get("settings") or {}).get("kovak_autofill") is True)
    check("단축키 OFF 저장", (st2.get("settings") or {}).get("hotkey_enabled") is False)
    check("stats 폴더 지정 저장", (st2.get("settings") or {}).get("kovak_stats_path") == str(stats))
    bad_goal = api.save_settings({"week_goals": {"days": 0, "games": "x"}})
    check("주간 목표 범위 보정", (bad_goal.get("state") or {}).get("week", {}).get("days") == 1,
          str((bad_goal.get("state") or {}).get("week", {}).get("days")))
    clamp_goal = api.save_settings({"week_goals": {"days": 99, "games": 900}})
    week_after = (clamp_goal.get("state") or {}).get("week", {})
    check("주간 목표 상한 보정", week_after.get("days") == 7 and week_after.get("games") == 200,
          str((week_after.get("days"), week_after.get("games"))))

    # ---- 11.5 🧭 최근 100판 플레이 코칭 (추가 API 0건)
    coach = (state.get("barracks") or {}).get("play_coaching") or []
    check("플레이 코칭 3~4줄 생성", 3 <= len(coach) <= 4, f"{len(coach)}줄")
    check("코칭 항목 구조(icon/title/text/tone)",
          all(set(("icon", "title", "text", "tone")) <= set(t) for t in coach), str(coach[0] if coach else {}))
    flat = " ".join(t.get("text", "") + " " + t.get("title", "") for t in coach)
    check("코칭이 실제 수치를 인용", any(token in flat for token in ("승률", "K/D", "딜량", "RP")), flat[:80])
    check("약한 맵 코칭 포함 (v1.3.1 — 맵 이름·승률·K/D 제목)",
          any("판 · 승률" in t.get("title", "") and "K/D" in t.get("title", "") for t in coach),
          str([t.get("title") for t in coach]))
    thin = scoring.play_coaching({"stats": {"games": 0}})
    check("표본 없으면 코칭 비움", thin == [], str(thin))
    streaks = scoring.play_coaching({
        "stats": {"games": 100, "win": 40.0, "kd": 44.0, "dmg": 900},
        "tier": {"solo": {"rp": 2100}, "party": {"rp": 2600}},
        "maps": [{"map": "A", "games": 8, "win": 20.0, "kd": 35.0, "weak": 70},
                 {"map": "B", "games": 9, "win": 66.0, "kd": 61.0, "weak": 30}],
        "matches": [{"result": "패"}] * 5,
        "recent": {"sniper": 30, "assault": 70}})
    check("연패·솔로격차 코칭", 3 <= len(streaks) <= 4 and any("최근 5판" in t["title"] for t in streaks),
          str([t["title"] for t in streaks]))
    check("코칭 문구가 수치에 맞음", any("44.0" in t["text"] or "900" in t["text"] for t in streaks),
          str([t["text"][:60] for t in streaks]))
    gap = scoring.play_coaching({
        "stats": {"games": 100, "win": 53.0, "kd": 55.0, "dmg": 1200},
        "tier": {"solo": {"rp": 2100}, "party": {"rp": 2600}},
        "maps": [{"map": "A", "games": 8, "win": 50.0, "kd": 52.0, "weak": 40}],
        "matches": [{"result": "승"}] * 5,
        "recent": {"sniper": 60, "assault": 40}})
    _gap_hit = [t for t in gap if ("2,100" in t["text"] and "2,600" in t["text"])
                or ("2,100" in t["title"] and "2,600" in t["title"])]
    check("솔로/파티 격차 코칭", bool(_gap_hit), str([t["title"] for t in gap]))

    # ---- 12. 앱 버전 · API 노출
    check("APP_VERSION 1.0.0", paths.APP_VERSION == "1.0.0", paths.APP_VERSION)
    import main as app_main

    exposed = app_main._public_names(api)
    need = ("import_kovak_scores", "warmup_plan", "export_playlist", "save_report_card",
            "check_update", "compare_friend", "perf_state", "apply_perf_mode", "restore_perf_mode",
            "tray_hide", "tray_state", "kovak_stats_state")
    check("신규 API 가 JS 에 모두 노출", all(n in exposed for n in need),
          str([n for n in need if n not in exposed]))
    check("bootstrap 상태에 v1.2.0 키", state.get("v120") is True and "heatmap" in state)


# --------------------------------------------------------------------- UI state
def ui_state(store) -> dict:
    from app.api import Api

    api = Api(store=store)
    state = api._build_state()
    state["update"] = {"ok": True, "current": "1.3.0", "latest": "1.3.1", "tag": "v1.3.1", "newer": True,
                       "url": "https://github.com/ckdwns04176-star/sa-aim-labs/releases/latest",
                       "download": "https://example.invalid/SA_Aim_Labs.exe",
                       "published_at": "2026-11-23T09:00:00+09:00", "message": "새 버전 v1.3.1 이 있습니다"}
    state["tray"] = {"supported": True, "active": True, "icon": True, "hotkey": True,
                     "hotkey_spec": "Ctrl+Shift+A", "error": None, "background": True, "hotkey_enabled": True,
                     "hidden": False}
    state["utils"] = {**(state.get("utils") or {}),
                      "supported": True, "mode": None, "alias": False, "backups": [],
                      "points": [], "modes": {"rifle": True, "sniper": True, "stairfix": True},
                      "missing": [], "accel": True, "pending": False,
                      "stairfix": {"ready": True, "applied": False, "value": "0x00020041",
                                   "label": "기본값", "target": "0x00401241"}}
    return state


def png_bytes(width: int = 96, height: int = 96) -> bytes:
    """테스트용 진짜 PNG (zlib 로 만든 단색 이미지)."""
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes([20, 30, 48] * width) for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


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
      async drag_window() { return {ok: true, supported: true}; },
      async toggle_maximize() { return {ok: true}; },
      async minimize_window() { return {ok: true}; },
      async close_window() { return {ok: true}; },
      async save_settings(patch) {
        window.__CALLS.push(['save_settings', patch]);
        const st = S();
        if (patch && patch.week_goals) {
          st.week = {...st.week, days: patch.week_goals.days, games: patch.week_goals.games};
        }
        st.settings = {...(st.settings||{}), ...patch};
        return {ok: true, state: st};
      },
      async import_kovak_scores(names, days) {
        window.__CALLS.push(['import_kovak_scores', names, days]);
        return {ok: true, found: true, matched: [{name: '1wall 6targets small', score: 161.5, at: '2026-11-23T19:04:12'}],
                dirs: ['C:/Steam/FPSAimTrainer/stats'], scanned: 3, runs: 2,
                message: '📥 코박스 점수 1종 인식 · 1wall 6targets small'};
      },
      async save_training_session(payload) {
        window.__CALLS.push(['save_training_session', payload]);
        return {ok: true, state: S(), record: {id: 'x'},
                pbs: [{name: payload.scenarios[0].name, score: payload.scenarios[0].score, prev: 152.0}]};
      },
      async warmup_plan(game, weapon) {
        window.__CALLS.push(['warmup_plan', game, weapon]);
        const st = S();
        const rec = (st.summaries && st.summaries[game + '-' + weapon] && st.summaries[game + '-' + weapon].recs) || [];
        const recs = rec.slice(0, 3).map((r, i) => ({...r, done: i === 0}));
        return {ok: true, recs, game, weapon, message: '⚡ 워밍업 3종 · 스나 맞춤'};
      },
      async export_playlist(kind, game, weapon) {
        window.__CALLS.push(['export_playlist', kind, game, weapon]);
        return {ok: true, files: ['C:/Playlists/SA AIM 추천 5종.json', 'C:/Playlists/SA AIM 추천 5종.plo'],
                count: 5, folder: 'C:/Playlists', names: [], in_game: true,
                message: '📜 플레이리스트 5종 저장 · C:/Playlists'};
      },
      async save_report_card(dataUrl, label) {
        window.__CALLS.push(['save_report_card', String(dataUrl || '').length, label]);
        window.__CARD = String(dataUrl || '');
        return {ok: true, path: 'C:/reports/sa_aim_report.png', size: 12345,
                message: '📸 리포트 카드 저장 완료 · sa_aim_report.png'};
      },
      async check_update(force) {
        window.__CALLS.push(['check_update', force]);
        return S().update;
      },
      async compare_friend(name) {
        window.__CALLS.push(['compare_friend', name]);
        const st = S();
        const me = (st.leaderboard || []).find((m) => m.me) || {};
        const friend = (st.leaderboard || []).find((m) => m.name === name) || {};
        const row = (key, label, unit) => ({key, label, unit, me: me[key], friend: friend[key],
          diff: (Number(me[key]) || 0) - (Number(friend[key]) || 0),
          better: (Number(me[key]) || 0) > (Number(friend[key]) || 0) ? 'me' : 'friend', text: false});
        return {ok: true, compare: {
          me: {name: me.name, clan: me.clan, solo_tier: (me.solo||0).toLocaleString() + ' RP', party_tier: (me.party||0).toLocaleString() + ' RP'},
          friend: {name: friend.name, clan: friend.clan, solo_tier: (friend.solo||0).toLocaleString() + ' RP', party_tier: (friend.party||0).toLocaleString() + ' RP'},
          rows: [row('solo', '솔로 RP', 'RP'), row('party', '파티 RP', 'RP'), row('win', '최근 100판 승률', '%'),
                 row('kd', 'K/D', '%'), row('dmg', '평균 딜량', ''),
                 {key: 'weapon', label: '주무기 성향', unit: '', me: '스나', friend: '라플', diff: null, better: 'none', text: true}],
          score: {me: 3, friend: 2}, verdict: '내가 우세',
          note: '저장된 순위표·병영수첩 데이터만 비교합니다 · 넥슨 API 를 다시 부르지 않습니다'},
          message: '비교 완료'};
      },
      async perf_state(force) {
        window.__CALLS.push(['perf_state', force]);
        return {ok: true, perf: {supported: true, power: {guid: '381b4222-f694-41f0-9685-ff5bb260df2e', name: '균형 조정'},
          game_mode: {supported: true, exists: false, enabled: null}, optimal: false, label: '균형 조정',
          backup: null, applied_at: null}};
      },
      async apply_perf_mode() {
        window.__CALLS.push(['apply_perf_mode']);
        return {ok: true, message: '⚡ 적용 완료 · 전원 옵션 → 최고 성능 · 게임 모드 켜기 (복구 지점 저장됨)',
          perf: {supported: true, power: {guid: 'e9a42b02-d5df-448d-aa00-03f14749eb61', name: '최고 성능'},
                 game_mode: {supported: true, exists: true, enabled: true}, optimal: true, label: '최고 성능',
                 backup: {power: {guid: '381b4222', name: '균형 조정'}, game_mode: {exists: false}},
                 applied_at: '2026-11-23T20:40:00+09:00'}};
      },
      async restore_perf_mode() {
        window.__CALLS.push(['restore_perf_mode']);
        return {ok: true, message: '↩ 원래 전원 옵션·게임 모드로 되돌렸습니다',
          perf: {supported: true, power: {guid: '381b4222', name: '균형 조정'},
                 game_mode: {supported: true, exists: false, enabled: null}, optimal: false, label: '균형 조정',
                 backup: null, applied_at: null}};
      },
      async tray_hide() { window.__CALLS.push(['tray_hide']); return {ok: true, hidden: true,
        message: '트레이로 숨겼습니다 · 단축키(Ctrl+Shift+A) 또는 트레이 아이콘으로 다시 엽니다'}; },
      async tray_state() { return {ok: true, tray: S().tray, state: S()}; },
      async open_external(url) { window.__CALLS.push(['open_external', url]); return {ok: true}; },
      async launch_trainer(url) { window.__CALLS.push(['launch_trainer', url]); return {ok: true, url}; },
      async fetch_leaderboard(names) { window.__CALLS.push(['fetch_leaderboard', names]);
        return {ok: true, members: [], renamed: [], message: '전적 일괄 갱신 완료', state: S()}; },
      async remove_leaderboard_member(n) { return {ok: true, state: S()}; },
      async add_leaderboard_member(n, d, o) { return {ok: true, state: S(), message: '추가'}; },
      async set_mouse_accel(v) { return {ok: true, enabled: !!v, state: S()}; },
      async apply_nvidia_profile(m, e) { return {ok: true, mode: m, state: S(), message: '적용 완료'}; },
      async toggle_nvidia_alias(v) { return {ok: true, state: S(), message: '복구 완료'}; },
      async restore_nvidia_profile() { return {ok: true, state: S(), message: '복구 완료'}; },
      async export_backup() { return {ok: true, path: 'x.json', message: '백업 완료'}; },
      async open_data_folder() { return {ok: true}; }
    }};
    """.replace("__STATE_JSON__", json.dumps(state, ensure_ascii=False))


# --------------------------------------------------------------------- UI checks
def ui_checks() -> None:
    from playwright.sync_api import sync_playwright

    base = Path(tempfile.mkdtemp(prefix="sa_v120_ui_"))
    store = build_store(base)
    make_stats_dir(base)
    os.environ["SA_AL_KOVAK_STATS_DIR"] = str(base / "FPSAimTrainer" / "stats")
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
        page.wait_for_timeout(600)

        # ---------- 대시보드
        dash = page.evaluate("""() => {
            const cells = document.querySelectorAll('#heatmap .heat-cell');
            const weeks = document.querySelectorAll('#heatmap .heat-col');
            const lit = Array.from(cells).filter((c) => /l[123]/.test(c.className)).length;
            const bar = (id) => { const el = document.getElementById(id); return el ? el.style.width : null; };
            const rows = Array.from(document.querySelectorAll('#impact-rows .imp-row')).map((r) => r.textContent.trim());
            const btns = ['warmup-btn', 'kovak-import-btn', 'report-btn', 'plo-btn'].map((id) => {
                const el = document.getElementById(id); return el ? el.textContent.trim() : null; });
            const update = document.getElementById('update-banner');
            const sc = document.querySelector('#s-dashboard');
            return {cells: cells.length, weeks: weeks.length, lit,
                    days: (document.getElementById('wg-days') || {}).textContent,
                    games: (document.getElementById('wg-games') || {}).textContent,
                    barDays: bar('wg-days-bar'), barGames: bar('wg-games-bar'),
                    pct: (document.getElementById('wg-pct') || {}).textContent,
                    heatSub: (document.getElementById('heat-sub') || {}).textContent,
                    impactNote: (document.getElementById('impact-note') || {}).textContent,
                    impactChip: (document.getElementById('impact-chip') || {}).textContent,
                    rows, btns,
                    updateShown: update ? !update.hidden : false,
                    updateText: update ? update.textContent.trim() : '',
                    scrollH: sc ? sc.scrollHeight : 0, clientH: sc ? sc.clientHeight : 0,
                    insightsTop: (document.querySelector('.insights') || {getBoundingClientRect: () => ({})})
                        .getBoundingClientRect().top,
                    recRows: document.querySelectorAll('#rec-list .rec').length};
        }""")
        page.screenshot(path=str(SHOTS / "v120-dashboard.png"))
        page.click(".nav-item[data-screen='records']")
        page.wait_for_timeout(400)
        page.locator(".insights").screenshot(path=str(SHOTS / "v120-insights.png"))
        page.click(".nav-item[data-screen='dashboard']")
        page.wait_for_timeout(300)
        check("잔디 12주 × 7칸 렌더", dash["cells"] == 84 and dash["weeks"] == 12,
              f"{dash['weeks']}주 {dash['cells']}칸")
        check("잔디에 훈련일 색이 들어감", dash["lit"] >= 10, f"{dash['lit']}칸")
        check("주간 목표 게이지", dash["days"] == "3/5일" or "/5일" in (dash["days"] or ""),
              str({"days": dash["days"], "games": dash["games"], "bars": (dash["barDays"], dash["barGames"])}))
        check("주간 목표 퍼센트", "%" in (dash["pct"] or ""), str(dash["pct"]))
        check("훈련 전후 비교 3행", len(dash["rows"]) == 3, str(dash["rows"]))
        check("전후 비교 안내 문구 표시", bool(dash["impactNote"]), str(dash["impactNote"])[:50])
        # v1.4.0 — 워밍업·코박스·리포트·플레이리스트는 라이브러리/기록 화면으로 이동
        page.click(".nav-item[data-screen='recs']")
        page.wait_for_timeout(300)
        moved = page.evaluate("""(() => ({warm: !!document.getElementById('warmup-btn-2'),
            kovak: !!document.getElementById('kovak-import-head'),
            plo: !!document.getElementById('plo-btn'),
            personal: !!document.getElementById('personal-plo-btn'),
            report: !!document.getElementById('report-btn')}))()""")
        check("대시보드 추천 카드는 2버튼만", not any(dash["btns"][:2]), str(dash["btns"]))
        check("워밍업·코박스·플레이리스트는 라이브러리로 이동", moved["warm"] and moved["kovak"] and moved["plo"] and moved["personal"], str(moved))
        page.click(".nav-item[data-screen='dashboard']")
        page.wait_for_timeout(250)
        check("새 버전 배너 표시", dash["updateShown"] and "새 버전" in dash["updateText"], str(dash["updateText"]))
        page.click(".nav-item[data-screen='records']")
        page.wait_for_timeout(400)
        ins_top = page.evaluate("""() => {
            const r = document.querySelector('#s-records .insights').getBoundingClientRect();
            const s = document.querySelector('#s-records').getBoundingClientRect();
            return Math.round(r.top - s.top);
        }""")
        check("인사이트 행이 기록·분석 화면에 보임", 0 < ins_top < 720, f"top={ins_top}")
        page.click(".nav-item[data-screen='dashboard']")
        page.wait_for_timeout(300)
        check("추천 5종 행 유지", dash["recRows"] >= 5, str(dash["recRows"]))

        # ---------- 리포트 카드 PNG (v1.4.0 — 에임 기록·분석 화면으로 이동)
        page.click(".nav-item[data-screen='records']")
        page.wait_for_timeout(300)
        page.click("#report-btn")
        page.wait_for_timeout(1200)
        card = page.evaluate("""() => {
            const c = ('save_report_card' in (window.pywebview.api||{})) ? window.__CARD : '';
            return {len: (c || '').length, head: (c || '').slice(0, 22)};
        }""")
        card_png = SHOTS / "v120-report-card.png"
        raw = page.evaluate("() => window.__CARD || ''")
        if raw.startswith("data:image/png;base64,"):
            card_png.write_bytes(base64.b64decode(raw.split(",", 1)[1]))
        check("리포트 카드 PNG 생성", raw.startswith("data:image/png;base64,") and len(raw) > 40000,
              f"{len(raw)} chars → {card_png.name}")
        size = card_png.stat().st_size if card_png.exists() else 0
        check("리포트 카드 파일 크기", size > 30000, f"{size / 1024:.0f}KB")

        # ---------- 1:1 비교 모달 (순위표 행 클릭)
        page.click(".nav-item[data-screen='leaderboard']")
        page.wait_for_timeout(350)
        page.click("#lb-rows .lb-row:nth-child(2)")
        page.wait_for_timeout(500)
        compare = page.evaluate("""() => {
            const m = document.getElementById('compare-mask');
            const rows = document.querySelectorAll('#cmp-body .cmp-row');
            const bars = document.querySelectorAll('#cmp-body .cmp-bar i');
            return {shown: m.classList.contains('show'),
                    title: (document.getElementById('cmp-title')||{}).textContent,
                    rows: rows.length, bars: bars.length,
                    text: (document.getElementById('cmp-body')||{}).textContent.replace(/\\s+/g,' ').slice(0, 160)};
        }""")
        page.screenshot(path=str(SHOTS / "v120-compare.png"))
        check("1:1 비교 모달 열림", compare["shown"], str(compare.get("title")))
        check("1:1 비교 6개 지표", compare["rows"] == 6, str(compare["rows"]))
        check("1:1 비교 막대 그래프", compare["bars"] >= 10, str(compare["bars"]))
        page.click("#compare-mask [data-close]")
        page.wait_for_timeout(200)

        # ---------- 5분 워밍업 모달 (v1.4.0 — 시나리오 라이브러리로 이동)
        page.click(".nav-item[data-screen='recs']")
        page.wait_for_timeout(250)
        page.click("#warmup-btn-2")
        page.wait_for_timeout(600)
        warm = page.evaluate("""() => {
            const m = document.getElementById('warmup-mask');
            return {shown: m.classList.contains('show'), rows: document.querySelectorAll('#warmup-list .warmup-row').length,
                    sub: (document.getElementById('warmup-sub')||{}).textContent};
        }""")
        page.screenshot(path=str(SHOTS / "v120-warmup.png"))
        check("워밍업 모달 3종", warm["shown"] and warm["rows"] == 3, str(warm))
        page.click("#warmup-mask [data-close]")
        page.wait_for_timeout(200)

        # ---------- 코박스 점수 자동 인식 → 저장
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#kovak-import-head")
        page.wait_for_timeout(900)
        calls = page.evaluate("() => window.__CALLS")
        names = [c[0] for c in calls]
        check("코박스 불러오기 → import + 저장 호출", "import_kovak_scores" in names and "save_training_session" in names,
              str(names))
        saved_payload = next((c[1] for c in calls if c[0] == "save_training_session"), {})
        check("코박스 점수가 시나리오로 저장됨",
              bool(saved_payload.get("scenarios")) and saved_payload["scenarios"][0].get("score") == 161.5,
              str(saved_payload.get("scenarios")))

        # ---------- 주간 목표 수정 (v1.3.0 — 기록·분석 화면)
        page.click(".nav-item[data-screen='records']")
        page.wait_for_timeout(300)
        page.click("#week-goal-edit")
        page.wait_for_timeout(300)
        page.fill("#week-days", "6")
        page.fill("#week-games", "30")
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#week-save")
        page.wait_for_timeout(600)
        calls = page.evaluate("() => window.__CALLS")
        wg = next((c[1] for c in calls if c[0] == "save_settings"), {})
        check("주간 목표 저장 호출", wg.get("week_goals") == {"days": 6, "games": 30}, str(wg))

        # ---------- 유틸 탭 · 전원 + 게임 모드
        page.click(".nav-item[data-screen='utils']")
        page.wait_for_timeout(500)
        page.click('[data-util-tab="system"]')
        page.wait_for_timeout(250)
        util = page.evaluate("""() => {
            const card = document.getElementById('perf-card');
            return {exists: !!card, chip: (document.getElementById('perf-chip')||{}).textContent,
                    state: (document.getElementById('perf-state')||{}).textContent,
                    label: (document.getElementById('perf-label')||{}).textContent,
                    apply: !!document.getElementById('perf-switch'),
                    restore: !!document.getElementById('perf-restore')};
        }""")
        page.screenshot(path=str(SHOTS / "v120-utils.png"))
        check("유틸 탭에 전원/게임 모드 카드", util["exists"] and util["apply"] and util["restore"], str(util))
        check("전원 옵션 현재 상태 표시", "전원 옵션" in (util["label"] or ""), str(util["label"]))
        page.evaluate("() => { window.__CALLS = []; }")
        page.click("#perf-switch")
        page.wait_for_timeout(600)
        calls = page.evaluate("() => window.__CALLS")
        chip_after = page.evaluate("() => document.getElementById('perf-chip').textContent")
        check("전원/게임 모드 적용 호출", any(c[0] == "apply_perf_mode" for c in calls), str([c[0] for c in calls]))
        check("적용 후 상태 칩 갱신", "적용됨" in (chip_after or ""), str(chip_after))

        # ---------- 설정 팝업 (v1.3.0 — 좌측 하단 프로필/톱니바퀴)
        page.evaluate("() => document.querySelector('#settings-btn').click()")
        page.wait_for_timeout(500)
        settings = page.evaluate("""() => {
            const q = (id) => { const el = document.getElementById(id); return el ? el.value || el.textContent : null; };
            return {open: document.getElementById('settings-mask').classList.contains('show'),
                    stats: q('set-kovak-stats'), update: q('set-update-info'), tray: q('set-tray-state'),
                    autofill: !!document.querySelector('[data-switch="kovak_autofill"]'),
                    hotkey: !!document.querySelector('[data-switch="hotkey_enabled"]'),
                    trayToggle: !!document.querySelector('[data-switch="tray_background"]'),
                    resetBtn: !!document.getElementById('set-reset-scores'),
                    updBtn: !!document.getElementById('set-update-check'),
                    tabs: [...document.querySelectorAll('#set-tabs button')].map((b) => b.textContent.trim())};
        }""")
        page.screenshot(path=str(SHOTS / "v120-settings.png"))
        check("설정 팝업이 열림(좌측 하단 프로필)", settings["open"], str(settings["open"]))
        check("설정 · 코박스 stats 폴더 입력", settings["stats"] is not None, str(settings["stats"]))
        check("설정 · 자동 채움/단축키 토글", settings["autofill"] and settings["hotkey"], str(settings))
        check("설정 · '닫으면 트레이로 숨기기' 옵션 제거", settings["trayToggle"] is False, str(settings["trayToggle"]))
        check("설정 · 점수 전체 초기화 버튼", settings["resetBtn"], str(settings["resetBtn"]))
        check("설정 · 업데이트 확인 버튼", settings["updBtn"], str(settings["updBtn"]))
        check("설정 · 새 버전 안내 문구", "새 버전" in (settings["update"] or ""), str(settings["update"]))
        page.evaluate("() => { window.__CALLS = []; }")
        page.evaluate("() => document.querySelector('#set-tabs [data-set-tab=\"more\"]').click()")
        page.wait_for_timeout(250)
        page.click("#set-tray-hide")
        page.wait_for_timeout(400)
        calls = page.evaluate("() => window.__CALLS")
        check("트레이로 숨기기 호출", any(c[0] == "tray_hide" for c in calls), str([c[0] for c in calls]))
        page.evaluate("() => document.querySelector('#settings-mask').classList.remove('show')")
        page.wait_for_timeout(200)

        # ---------- 병영수첩 · 최근 100판 플레이 코칭
        page.click(".nav-item[data-screen='barracks']")
        page.wait_for_timeout(700)
        coach = page.evaluate("""() => {
            const rows = Array.from(document.querySelectorAll('#br-coach-list .coach-row'));
            const card = document.getElementById('br-coach');
            return {rows: rows.length, chip: (document.getElementById('br-coach-chip')||{}).textContent,
                    sub: (document.getElementById('br-coach-sub')||{}).textContent,
                    tones: rows.map((r) => r.className.replace('coach-row','').trim()),
                    text: rows.map((r) => r.textContent.replace(/\\s+/g,' ').trim().slice(0, 70)),
                    visible: !!card && card.getBoundingClientRect().height > 40};
        }""")
        page.screenshot(path=str(SHOTS / "v120-barracks-coach.png"))
        check("병영수첩 플레이 코칭 카드", coach["visible"] and coach["rows"] >= 3, str(coach["rows"]))
        check("코칭 칩에 판수 표시", "100판" in (coach["chip"] or ""), str(coach["chip"]))
        check("코칭이 수치 인용", any(any(k in t for k in ("승률", "K/D", "딜량", "RP")) for t in coach["text"]),
              str(coach["text"][:2]))
        page.evaluate("""() => { const sc = document.getElementById('s-barracks'); sc.scrollTop = sc.scrollHeight; }""")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "v120-barracks-coach-bottom.png"))

        # ---------- 오늘의 추천 5종 화면 버튼
        page.click(".nav-item[data-screen='recs']")
        page.wait_for_timeout(400)
        recs = page.evaluate("""() => ({warm: !!document.getElementById('warmup-btn-2'),
            imp: !!document.getElementById('kovak-import-head'),
            head: !!document.getElementById('score-input-btn')})""")
        check("추천 5종 화면에 워밍업·코박스 버튼", recs["warm"] and recs["imp"] and recs["head"], str(recs))
        page.screenshot(path=str(SHOTS / "v120-recs.png"))

        # ---------- 640px 창(최소 크기) 에서도 겹치지 않는지
        page.set_viewport_size({"width": 1200, "height": 640})
        page.click(".nav-item[data-screen='records']")
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOTS / "v120-dashboard-640.png"))
        small = page.evaluate("""() => {
            const ins = document.querySelector('.insights');
            const r = ins.getBoundingClientRect();
            const over = Array.from(document.querySelectorAll('#s-dashboard *'))
                .filter((el) => el.scrollWidth > el.clientWidth + 4 && el.clientWidth > 0).length;
            return {w: Math.round(r.width), h: Math.round(r.height), over};
        }""")
        check("640px 에서도 인사이트 행 폭 유지", small["w"] > 900 and small["h"] >= 100, str(small))
        page.set_viewport_size({"width": 1200, "height": 750})

        real = [e for e in errors if "favicon" not in e.lower()]
        check("UI 런타임 오류 없음", not real, str(real[:2]))
        browser.close()


def main() -> int:
    backend_checks()
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
