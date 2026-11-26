"""v1.0.0 검사 — 프록시 · 오버레이 · 파티 OCR · 상대 스카우팅 · 토스트 · 계단현상/가속/트레이 수정.

    python3 tests/v100_test.py          # 단위 검사 (Windows 불필요)
    LD_LIBRARY_PATH=... python3 tests/v100_test.py --ui   # + Playwright 화면 검사
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""))


def main(with_ui: bool = False) -> int:
    os.environ["SA_AL_DATA_DIR"] = tempfile.mkdtemp(prefix="sa_v100_")
    from app import drs, nexon, nvapi_drs, overlay, party_lobby, paths, scouting, toast, tray, winutil
    from app.api import Api
    from app.store import Store

    # ── 0. 버전 ───────────────────────────────────────────────
    check("APP_VERSION = 1.0.1", paths.APP_VERSION == "1.0.1", paths.APP_VERSION)
    check("QHD 자동 배율 115%", overlay is not None and __import__("app.v150", fromlist=["v150"]).auto_ui_scale(2560, 1440) == 115)
    check("FHD 자동 배율 100%", __import__("app.v150", fromlist=["v150"]).auto_ui_scale(1920, 1080) == 100)

    # ── 1. 프록시 기본값 ─────────────────────────────────────
    proxied = nexon.NexonClient("")
    direct = nexon.NexonClient("live_test_key")
    check("키 없이 프록시 연결", proxied.using_proxy and proxied.endpoint == "proxy", proxied.endpoint)
    check("개인 키는 직접 호출", direct.using_proxy is False and direct.endpoint == "direct")
    check("프록시 URL 고정", proxied.proxy == nexon.PROXY_URL, proxied.proxy)
    check("프록시는 더 빠른 간격", proxied.min_interval < direct.min_interval,
          f"{proxied.min_interval} < {direct.min_interval}")

    # ── 2. 매치 캐시 30일 정리 (기록은 보존) ────────────────
    cache = Path(tempfile.mkdtemp(prefix="sa_cache_"))
    now = time.time()
    old = now - 31 * 86400
    (cache / "nexon_api.json").write_text(json.dumps({
        "fresh": {"at": now, "ttl": 600, "data": {"ok": 1}},
        "old": {"at": old, "ttl": None, "data": {"ok": 2}},
    }), encoding="utf-8")
    (cache / "nexon_match_detail.json").write_text(json.dumps({
        "d:new": {"at": now, "ttl": 30 * 86400, "data": {}},
        "d:old": {"at": old, "ttl": 30 * 86400, "data": {}},
    }), encoding="utf-8")
    data_file = cache / "data.json"
    data_file.write_text("{\"records\":[{\"score\":123}]}", encoding="utf-8")
    purge = nexon.purge_expired_cache(30, cache)
    api_cache = json.loads((cache / "nexon_api.json").read_text(encoding="utf-8"))
    detail_cache = json.loads((cache / "nexon_match_detail.json").read_text(encoding="utf-8"))
    check("30일 지난 캐시 삭제", purge["removed"] == 2 and set(api_cache) == {"fresh"} and set(detail_cache) == {"d:new"}, str(purge))
    check("훈련 기록 data.json 은 손대지 않음", data_file.exists() and "records" in data_file.read_text(encoding="utf-8"))

    # ── 3. 계단현상 끄기 세트 (drs) ─────────────────────────
    check("계단현상 세트 3개", len(drs.STAIRFIX_SET) == 3 and
          dict(drs.STAIRFIX_SET)[drs.SETTING_AA_COMPAT] == drs.STAIRFIX_VALUE)
    check("AA 모드/설정 이름", drs.setting_name(drs.SETTING_AA_MODE)[1] == "Antialiasing - Mode" and
          drs.setting_name(drs.SETTING_AA_SETTING)[1] == "Antialiasing - Setting")
    check("값 설명 (Override/2x)", drs.value_label(drs.SETTING_AA_MODE, 1) == "앱 설정 무시(Override)" and
          drs.value_label(drs.SETTING_AA_SETTING, 1) == "2x")
    check("NVAPI 구조체 정의", bool(nvapi_drs._structures().get("setting")))
    try:
        from ctypes import sizeof
        setting = nvapi_drs._structures()["setting"]
        size = sizeof(setting)
        check("NVAPI SETTING 구조체 크기", size == 8224, str(size))
        check("NVAPI 버전 규칙", ((1 << 16) | size) & ~0xFFFF == 1 << 16)
    except Exception as exc:  # noqa: BLE001
        check("NVAPI 구조체", False, str(exc))

    # ── 4. 마우스 가속 SPI 파라미터 ─────────────────────────
    check("해제 파라미터 (0,0,0)", winutil.mouse_params(False) == (0, 0, 0), str(winutil.mouse_params(False)))
    check("켜기 파라미터 (6,10,1)", winutil.mouse_params(True) == (6, 10, 1), str(winutil.mouse_params(True)))

    # ── 4.5 프로필 적용이 서든 프로필만 바꾸는가 ───────────────
    try:
        from app import nvidia
        import struct as _struct

        live = Path(tempfile.mkdtemp(prefix="sa_live_"))
        for item in (ROOT / "assets" / "profiles" / "sniper").iterdir():
            if item.is_file():
                (live / item.name).write_bytes(item.read_bytes())
        before_blob = (live / "nvdrsdb0.bin").read_bytes()

        def _profile_map(blob: bytes, exe: str) -> dict[int, int]:
            off = drs.profile_offset(blob, exe)
            if off is None:
                return {}
            return {r["setting_id"]: _struct.unpack_from("<I", blob, r["value_offset"])[0]
                    for r in drs.iter_settings(blob, off) if r["value_size"] >= 4}

        base_before = {}
        base_off = drs.named_profile_offset(before_blob, "Base Profile")
        if base_off is not None:
            base_before = {r["setting_id"]: _struct.unpack_from("<I", before_blob, r["value_offset"])[0]
                           for r in drs.iter_settings(before_blob, base_off) if r["value_size"] >= 4}
        game_before = _profile_map(before_blob, drs.GAME_EXE)
        mgr = nvidia.DrsManager(force_platform=True, drs_dir=live,
                                backup_root=Path(tempfile.mkdtemp(prefix="sa_bk_")),
                                profiles_root=ROOT / "assets" / "profiles")
        result = mgr.apply("rifle", force=True)
        after_blob = (live / "nvdrsdb0.bin").read_bytes()
        game_after = _profile_map(after_blob, drs.GAME_EXE)
        base_after = {}
        base_off = drs.named_profile_offset(after_blob, "Base Profile")
        if base_off is not None:
            base_after = {r["setting_id"]: _struct.unpack_from("<I", after_blob, r["value_offset"])[0]
                          for r in drs.iter_settings(after_blob, base_off) if r["value_size"] >= 4}
        check("프로필 적용 · 서든 전용 표시", result.get("ok") and result.get("other_profiles_preserved") is True, str(result.get("message"))[:80])
        check("프로필 적용 · 전역(Base Profile) 불변", base_before == base_after, f"{len(base_before)} → {len(base_after)}")
        check("프로필 적용 · 서든 AA 호환성 유지/적용", game_after.get(drs.SETTING_AA_COMPAT) == drs.STAIRFIX_VALUE,
              hex(game_after.get(drs.SETTING_AA_COMPAT) or 0))
        other_changed = [sid for sid in game_before if game_after.get(sid) != game_before[sid]]
        check("프로필 적용 · 서든 프로필만 변경", set(game_before) == set(game_after), str(other_changed))
    except Exception as exc:  # noqa: BLE001
        check("프로필 적용 · 서든 전용", False, str(exc))

    # ── 5. 트레이 64비트 프로토타입 ─────────────────────────
    class FakeFn:
        restype = None
        argtypes = None

    class FakeDll:
        def __getattr__(self, name):
            return FakeFn()

    try:
        tray._configure(FakeDll(), FakeDll(), FakeDll())
        source = Path(tray.__file__).read_text(encoding="utf-8")
        required = ["DefWindowProcW", "RegisterClassW", "GetMessageW", "TranslateMessage",
                    "DispatchMessageW", "LoadIconW", "ShowWindow", "GetForegroundWindow",
                    "AttachThreadInput", "AllowSetForegroundWindow", "SetFocus"]
        missing = [name for name in required if f"user32.{name}.restype" not in source]
        check("트레이 Win32 64비트 프로토타입", not missing, str(missing))
        check("우클릭 메뉴 포그라운드 보정", "_attach_foreground" in source and
              "AttachThreadInput" in source and "WM_CONTEXTMENU" in source)
    except Exception as exc:  # noqa: BLE001
        check("트레이 프로토타입", False, str(exc))

    # ── 6. 오버레이 기하/문구 ──────────────────────────────
    game = (0, 0, 1920, 1080)
    cards = overlay.mapban_card_rects(game)
    check("맵 밴 카드 5개", len(cards) == 5, str(len(cards)))
    check("첫 카드 위치", cards[0]["x"] == 194 and cards[0]["w"] == 297, str(cards[0]))
    names = overlay.mapban_name_rects(game)
    check("맵 이름 줄이 카드 안쪽", names[0]["y"] > cards[0]["y"] and names[0]["y"] + names[0]["h"] <= cards[0]["y"] + cards[0]["h"] + 1)
    party = overlay.party_card_rects(game)
    # v1.0.1 — 2048×945 스크린샷의 게임 클라이언트(폭 1682) 기준 재보정: 1920 에서 x 76 · 폭 320
    check("대기실 5칸", len(party) == 5 and party[0]["w"] == 320 and party[0]["x"] == 76, str(party[0]))
    toast_rect = overlay.toast_rect((0, 0, 1920, 1080), "br")
    check("토스트 우하단", toast_rect["x"] + toast_rect["w"] == 1920 - 18 and
          toast_rect["y"] + toast_rect["h"] == 1080 - 18, str(toast_rect))
    tr = overlay.toast_rect((0, 0, 1920, 1080), "tr")
    check("토스트 우상단", tr["y"] == 18, str(tr))
    rows = overlay.verdict_rows([
        {"map": "펠리스", "win": 62, "games": 5}, {"map": "시티캣(심플)", "win": 49, "games": 5},
        {"map": "제5보급창고", "win": 38, "games": 5}, {"map": "프로방스(심플)", "win": 55, "games": 5},
        {"map": "크로스포트(심플)", "win": 41, "games": 5}])
    check("픽/밴 배지", [r["tone"] for r in rows] == ["pick", "neutral", "ban", "pick", "ban"], str([r["tone"] for r in rows]))
    lines = overlay.toast_lines({"map": "제3보급창고", "result": "승", "kills": 14, "deaths": 6,
                                 "damage": 1840, "streak": 3})
    check("토스트 문구", lines["title"] == "제3보급창고 승리 · 14K 6D" and "3연승" in lines["sub"], lines["sub"])
    check("오버레이 스타일 비트", overlay_style_ok(winutil))
    check("단축키 정리", overlay.clean_hotkey("f8", "F9") == "F8" and overlay.clean_hotkey("none", "F9") == "None")

    # ── 7. 파티 OCR 문자열 · 보정 · 팀 ─────────────────────
    check("클랜 태그 제거", party_lobby.clean_ocr_text("[리드] 휴가중.") == "휴가중.", party_lobby.clean_ocr_text("[리드] 휴가중."))
    check("닉네임 줄 선택", party_lobby.pick_line(["[리드]", "휴가중."])[0] == "휴가중.")
    best, score = party_lobby.best_candidate("휴가증.", ["휴가중.", "다른사람"])
    check("OCR 유사 닉네임 보정", best == "휴가중." and score >= 0.62, f"{best} {score}")
    check("닉네임 비교 키", party_lobby.nick_key("O.1초") == party_lobby.nick_key("o.l초"))
    rows = [{"team": "red", "name": "휴가중."}, {"team": "red", "name": "a"},
            {"team": "blue", "name": "b"}, {"team": "blue", "name": "c"}]
    split = party_lobby.team_of(rows, "휴가중.")
    check("내 팀/상대 분리", split["my_team"] == "red" and len(split["opponents"]) == 2, str(split))

    # ── 8. 스카우팅 배지 · 맵 매칭 ─────────────────────────
    check("한 줄 배지", scouting.badge_text({"weapon": "sniper", "map_win": 64, "kd": 61}) == "스나 · 이 맵 64% · KD 61%")
    check("전체 승률 대체", "전체 57%" in scouting.badge_text({"weapon": "rifle", "win": 57, "kd": 50}))
    check("(심플) 맵 매칭", scouting.nexon_map_equal("시티캣(심플)", "시티캣") and
          scouting.nexon_map_equal("제5보급창고", "제5보급창고(심플)"))
    check("무기 판별", scouting.weapon_of({"recent_sniper_rate": 60, "recent_assault_rate": 30}) == "sniper")

    # ── 9. Store/API — 프리셋 · 게스트 · 오버레이 설정 · 캐시 정리 ──
    store = Store(Path(tempfile.mkdtemp(prefix="sa_store_")) / "data.json")
    api = Api(store=store)
    state = api._build_state(light=True)
    check("게스트 상태", state["guest"] is True)
    check("프록시 상태 노출", state["nexon"]["endpoint"] == "proxy" and state["nexon"]["key_required"] is False)
    check("출처 표기", state["attribution"] == "Data based on NEXON Open API")
    check("오버레이 설정 기본값", state["overlay"]["settings"]["hotkey_lobby"] == "F8" and
          state["overlay"]["settings"]["hotkey_scout"] == "F9")
    saved = store.save_party_preset("새벽 고정팟", ["휴가중.", "a", "b", "c", "d", "e"])
    check("프리셋 5명 저장", len(saved["members"]) == 5, str(saved))
    presets = api.party_presets()["presets"]
    check("프리셋 목록", presets and presets[0]["name"] == "새벽 고정팟", str(presets))
    loaded = api.load_party_preset("새벽 고정팟")
    check("프리셋 불러오기", loaded.get("ok") and loaded["members"][0] == "휴가중.", str(loaded.get("members")))
    check("프리셋 삭제", store.delete_party_preset("새벽 고정팟") and api.party_presets()["presets"] == [])
    result = api.save_settings({"overlay_hotkey_lobby": "F10", "overlay_hotkey_scout": "F11",
                                "toast_position": "tr", "overlay_mapban_seconds": 9})
    settings = result["state"]["overlay"]["settings"]
    check("오버레이 설정 저장", settings["hotkey_lobby"] == "F10" and settings["hotkey_scout"] == "F11"
          and settings["toast_position"] == "tr" and settings["mapban_seconds"] == 9.0, str(settings))
    check("설정 저장 후 게스트 유지", result["state"]["guest"] is True)
    bad = api.save_settings({"overlay_hotkey_lobby": "??"})
    check("잘못된 단축키는 기본값", bad["state"]["overlay"]["settings"]["hotkey_lobby"] == "F8",
          bad["state"]["overlay"]["settings"]["hotkey_lobby"])
    check("일일 캐시 정리 기록", store.data.get("cache_cleanup_at"), str(store.data.get("cache_cleanup_at")))
    check("오버레이 상태 API", api.overlay_state()["ok"] and "supported" in api.overlay_state()["overlay"])
    check("오버레이 페이로드 API", api.overlay_payload("toast")["kind"] == "toast")

    # 토스트 감시자 초기 상태
    watcher = toast.MatchWatcher(lambda: None, lambda: "", lambda match: None)
    check("토스트 감시 기본 꺼짐", watcher.state()["enabled"] is False and watcher.state()["running"] is False)

    # ── 10. UI (선택) ──────────────────────────────────────
    if with_ui:
        ui_checks()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


def overlay_style_ok(winutil) -> bool:
    style = winutil.overlay_style(0)
    return all(style & bit for bit in (winutil.WS_EX_TOPMOST, winutil.WS_EX_NOACTIVATE,
                                        winutil.WS_EX_TRANSPARENT, winutil.WS_EX_TOOLWINDOW))


def ui_checks() -> None:
    from playwright.sync_api import sync_playwright
    sys.path.insert(0, str(ROOT / "tests"))
    from v140_test import build_state, mock_js

    state = build_state()
    state["guest"] = False
    state["overlay"] = {"supported": True, "settings": {"enabled": True, "hotkey_lobby": "F8",
                        "hotkey_scout": "F9", "mapban_seconds": 12, "toast_position": "br"},
                        "hotkeys": {"registered": ["lobby", "scout"], "failed": {}}}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 750})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.add_init_script(mock_js(state))
        page.goto((ROOT / "ui" / "index.html").as_uri())
        page.wait_for_selector("#app-splash", state="hidden", timeout=15000)
        page.wait_for_timeout(600)

        title = page.evaluate("() => ({menu: !!document.getElementById('tb-screen-name'), attr: (document.querySelector('.tb-attr')||{}).textContent})")
        check("UI · 상단 메뉴명 제거 + NEXON 출처", title["menu"] is False and "NEXON" in (title["attr"] or ""), str(title))

        page.evaluate("() => showScreen('leaderboard')")
        page.wait_for_timeout(300)
        lb = page.evaluate("""() => ({rows: document.querySelectorAll('#lb-rows .lb-row').length,
            h: Math.round((document.querySelector('#lb-rows .lb-row')||{getBoundingClientRect:()=>({height:0})}).getBoundingClientRect().height),
            slots: (document.getElementById('lb-count')||{}).nextSibling && document.querySelector('.slots').textContent})""")
        check("UI · 순위표 20명 · 한 줄 92px", "20" in (lb["slots"] or "") and 88 <= lb["h"] <= 96, str(lb))

        page.evaluate("() => showScreen('home')")
        page.wait_for_timeout(250)
        home = page.evaluate("() => document.querySelector('.hm-strip .k').textContent")
        check("UI · 최근 랭크 컨디션", home == "최근 랭크 컨디션", home)

        page.evaluate("() => showScreen('dashboard')")
        page.wait_for_timeout(300)
        kd = page.evaluate("() => (document.querySelector('#home-games .kd')||{}).textContent")
        check("UI · 최근 5판 K/D %", str(kd).endswith("%"), str(kd))
        rec = page.evaluate("() => { const r=document.querySelector('#rec-list .rec'); return r? {left:!!r.querySelector('.rec-left .area'), before: r.querySelector('.rec-left').getBoundingClientRect().left < r.querySelector('.rec-name').getBoundingClientRect().left} : null; }")
        check("UI · 추천 배지가 이름 왼쪽", bool(rec and rec["left"] and rec["before"]), str(rec))

        page.evaluate("() => showScreen('recs')")
        page.wait_for_timeout(250)
        gone = page.evaluate("() => !document.getElementById('rec-more-2')")
        check("UI · 라이브러리 더 연습하기 제거", gone)

        page.evaluate("() => showScreen('utils')")
        page.wait_for_timeout(250)
        utils = page.evaluate("() => ({load: !!document.getElementById('party-load'), cmp: !!document.getElementById('cmp-left'), cmpVal: (document.getElementById('cmp-left')||{}).value})")
        check("UI · 불러오기 제거 + 1:1 비교(내 닉네임 기본)", utils["load"] is False and utils["cmp"] and utils["cmpVal"] == "휴가중.", str(utils))

        page.evaluate("() => document.getElementById('settings-mask').classList.add('show')")
        page.evaluate("() => document.querySelector('[data-set-tab=\"overlay\"]').click()")
        page.wait_for_timeout(300)
        ov = page.evaluate("""() => ({pane: !!document.querySelector('#set-overlay.on'),
            hot1: (document.getElementById('set-ov-hot1')||{}).value,
            saved: !!document.getElementById('ov-save'),
            scroll: getComputedStyle(document.querySelector('#settings-mask .modal-bd')).overflowY})""")
        check("UI · 오버레이 설정 탭", ov["pane"] and ov["hot1"] == "F8" and ov["saved"] and ov["scroll"] == "auto", str(ov))
        check("UI · JS 오류 없음", not errors, "; ".join(errors[:2]))
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main("--ui" in sys.argv))
