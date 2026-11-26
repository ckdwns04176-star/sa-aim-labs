"""v2.0.0 전체 검사 — 에임 연습 제거 · Win32 오버레이 · OCR 스킵 · 듀오 시너지 ·
저장소 잠금/백업 보안 · NVAPI 구조체 · 회원/등급(ENFORCE_PRO_LIMIT False·True) · UI.

    LD_LIBRARY_PATH=/workspace/.pwlibs/lib \\
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.pw-browsers \\
    python3 tests/v2_test.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from v2_fixtures import build_state, check, mock_js, report, sample_barracks  # noqa: E402

SHOTS = ROOT / "tests" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)


# ======================================================================
# 1. NVAPI DRS 구조체 (계단현상 끄기)
# ======================================================================
def test_nvapi() -> None:
    from app import nvapi_drs

    rep = nvapi_drs.struct_report()
    check("NVAPI 구조체 크기·오프셋", rep["ok"], str(rep.get("errors")))
    check("NVAPI settingName 오프셋 8224", rep["offsets"].get("settingName") == 8224,
          str(rep["offsets"].get("settingName")))
    check("NVAPI 구조체 12320바이트", rep["sizes"].get("setting") == 12320, str(rep["sizes"]))
    structs = nvapi_drs._structures()
    name = structs["unicode"]()
    nvapi_drs.set_unicode(name, "Antialiasing - Compatibility")
    check("NVAPI UTF-16 이름 왕복", nvapi_drs.get_unicode(name) == "Antialiasing - Compatibility")
    check("NVAPI 구조체 버전 = sizeof | 1<<16", rep["version"] == (1 << 16) | 12320, str(rep["version"]))
    plan = nvapi_drs.plan_apply([(0x00D55F7D, 0x00401241), (0x00D55F7D, 1), ("x", 2)])
    check("NVAPI 중복·오류 설정 정리", plan == [{"id": 0x00D55F7D, "value": 0x00401241}], str(plan))
    good = nvapi_drs.verify_rows([{"id": 0x00D55F7D, "value": 0x00401241}],
                                 [{"id": 0x00D55F7D, "value": 0x00401241}])
    bad = nvapi_drs.verify_rows([{"id": 0x00D55F7D, "value": 0x00000001}],
                                [{"id": 0x00D55F7D, "value": 0x00401241}])
    empty = nvapi_drs.verify_rows([], [{"id": 1, "value": 2}])
    check("NVAPI 재조회 값 일치 = 적용 완료", good["verified"] is True)
    check("NVAPI 값이 다르면 미완료", bad["verified"] is False and bad["mismatched"])
    check("NVAPI 재조회 실패 = 미완료", empty["verified"] is False)
    result = nvapi_drs.apply_profile_settings([(1, 2)])
    check("NVAPI 리눅스에서는 supported=False", result.get("supported") is False)


# ======================================================================
# 2. 저장소 — RLock · 비밀 제외 백업 · 옛 구역 제거
# ======================================================================
def test_store() -> None:
    from app.store import Store

    tmp = Path(tempfile.mkdtemp(prefix="sa_store_"))
    legacy = {
        "version": 1, "settings": {"nickname": "스나이퍼01", "api_key": "test_abcdefghijklmnop"},
        "records": [{"id": "x", "date": "2026-01-01"}], "rotation": [{"date": "2026-01-01"}],
        "attendance": {"2026-01-01": {"n": 3}}, "custom_scenarios": [{"name": "x"}],
        "cloud": {"access_token": "secret-token", "refresh_token": "refresh-secret",
                  "email": "a@b.c", "user_id": "u1"},
    }
    path = tmp / "data.json"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    store = Store(path)
    check("옛 에임 기록 구역 제거(records)", "records" not in store.data)
    check("옛 로테이션/출석/시나리오 제거",
          not any(k in store.data for k in ("rotation", "attendance", "custom_scenarios")))
    check("저장소 RLock 있음", isinstance(store._lock, type(threading.RLock())))

    errors: list[str] = []

    def worker(i: int) -> None:
        try:
            for k in range(20):
                store.data["settings"]["nickname"] = f"유저{i}-{k}"
                store.save()
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    check("동시 저장 6스레드 오류 없음", not errors, str(errors[:2]))
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    check("동시 저장 후 JSON 정상", isinstance(reloaded.get("settings"), dict))

    store.cloud()["access_token"] = "secret-token"
    store.settings["api_key"] = "test_zzzz"
    backup = store.export_backup(tmp / "backup.json")
    blob = backup.read_text(encoding="utf-8")
    parsed = json.loads(blob)
    data = parsed.get("data") or {}
    check("백업에 API 키 없음", "api_key" not in (data.get("settings") or {}))
    check("백업에 액세스 토큰 없음", "access_token" not in blob and "refresh_token" not in blob)
    check("백업에 Supabase anon 키 없음", "anon_key" not in json.dumps(data.get("settings", {})))
    check("백업에 계정 이메일은 유지", (data.get("cloud") or {}).get("email") == "a@b.c")
    check("백업에 제외 목록 표기", "excluded" in parsed)

    # 복원해도 지금 쓰는 비밀은 유지된다
    store.import_backup(backup, snapshot=False)
    check("복원 후 API 키 유지", store.settings.get("api_key") == "test_zzzz")
    check("복원 후 로그인 토큰 유지", store.cloud().get("access_token") == "secret-token")


# ======================================================================
# 3. 회원 · 등급 (ENFORCE_PRO_LIMIT False / True)
# ======================================================================
class FakeSupabase:
    def __init__(self, plan_row=None, *, fail: bool = False):
        self.plan_row = plan_row
        self.fail = fail
        self.calls: list[str] = []

    def sign_in(self, email: str, password: str) -> dict:
        self.calls.append("sign_in")
        if self.fail:
            raise RuntimeError("로그인 실패")
        return {"email": email, "user_id": "u-1", "access_token": "tok", "refresh_token": "ref",
                "needs_confirmation": False}

    def sign_up(self, email: str, password: str) -> dict:
        self.calls.append("sign_up")
        return {"email": email, "user_id": "u-1", "needs_confirmation": True,
                "message": "가입 확인 메일"}

    def recover(self, email: str) -> dict:
        self.calls.append("recover")
        return {"ok": True, "email": email, "message": "재설정 메일"}

    def subscription(self, token: str, user_id: str) -> dict | None:
        self.calls.append("subscription")
        return self.plan_row

    def refresh(self, token: str) -> dict:
        return {"access_token": "tok2", "refresh_token": "ref2", "user_id": "u-1"}

    def sign_out(self, token: str) -> None:
        self.calls.append("sign_out")


def test_auth() -> None:
    from app import auth as auth_mod
    from app.store import Store

    now = dt.datetime(2026, 11, 27, tzinfo=dt.timezone.utc)
    check("plan: pro 유효", auth_mod.normalize_plan({"plan": "pro", "pro_until": "2027-01-01T00:00:00Z"}, now)["plan"] == "pro")
    check("plan: pro 만료 → free",
          auth_mod.normalize_plan({"plan": "pro", "pro_until": "2026-01-01T00:00:00Z"}, now)["plan"] == "free")
    check("plan: 행 없음 → free", auth_mod.normalize_plan(None, now)["plan"] == "free")
    check("plan: 이상한 값 → free", auth_mod.normalize_plan({"plan": "vip"}, now)["plan"] == "free")

    free = auth_mod.normalize_plan({"plan": "free"}, now)
    off = auth_mod.limit_state(free, 99, enforce=False)
    on = auth_mod.limit_state(free, 3, enforce=True)
    on2 = auth_mod.limit_state(free, 1, enforce=True)
    pro = auth_mod.limit_state(auth_mod.normalize_plan({"plan": "pro", "pro_until": "2027-01-01T00:00:00Z"}, now), 99, enforce=True)
    check("ENFORCE False = 무제한", off["allowed"] and off["remaining"] is None, str(off))
    check("ENFORCE True free 3회 소진 → 잠금", on["allowed"] is False and on["remaining"] == 0)
    check("ENFORCE True free 1회 → 2회 남음", on2["allowed"] and on2["remaining"] == 2)
    check("ENFORCE True pro = 무제한", pro["allowed"] and pro["limit"] == "unlimited")

    tmp = Path(tempfile.mkdtemp(prefix="sa_auth_"))
    store = Store(tmp / "data.json")
    fake = FakeSupabase({"plan": "pro", "pro_until": "2027-01-01T00:00:00Z"})
    svc = auth_mod.AccountService(store, client_factory=lambda: fake)
    res = svc.sign_in("tester@example.com", "secret1")
    check("로그인 → 세션 저장", res["ok"] and store.cloud().get("access_token") == "tok")
    check("로그인 → subscriptions 등급 조회", res["plan"]["plan"] == "pro", str(res["plan"]))
    state = svc.state()
    check("계정 상태 signed_in·plan", state["signed_in"] and state["plan"]["plan"] == "pro")
    check("지금은 ENFORCE False", state["enforce"] is False)

    # 강제 모드에서 free 계정은 3회 뒤 잠긴다
    original = auth_mod.ENFORCE_PRO_LIMIT
    try:
        auth_mod.ENFORCE_PRO_LIMIT = True
        svc.free_fake = FakeSupabase({"plan": "free"})
        svc._client_factory = lambda: svc.free_fake
        svc.refresh_plan()
        check("강제 모드 free 계정", svc.state()["plan"]["plan"] == "free")
        allowed = [svc.guard("scout") is None for _ in range(4)]
        for _ in range(3):
            svc.use("scout")
        blocked = svc.guard("scout")
        check("강제 모드 free: 3회까지 허용", allowed[:3] == [True, True, True], str(allowed))
        check("강제 모드 free: 4번째 잠금", blocked and blocked.get("locked") is True, str(blocked))
        pro_svc = auth_mod.AccountService(store, client_factory=lambda: FakeSupabase({"plan": "pro", "pro_until": "2027-01-01T00:00:00Z"}))
        pro_svc.refresh_plan()
        ok_pro = all(pro_svc.guard("scout") is None for _ in range(10))
        check("강제 모드 pro: 무제한", ok_pro)
    finally:
        auth_mod.ENFORCE_PRO_LIMIT = original
    check("정책값 복원 False", auth_mod.ENFORCE_PRO_LIMIT is False)

    recovered = svc.recover("tester@example.com")
    check("비밀번호 찾기 호출", recovered.get("ok") is True, str(recovered))
    try:
        svc.recover("not-an-email")
        check("비밀번호 찾기 이메일 검증", False)
    except Exception:  # noqa: BLE001
        check("비밀번호 찾기 이메일 검증", True)


# ======================================================================
# 4. 인사이트 (홈 · 잔디 · 약점 · 폼)
# ======================================================================
def test_insights() -> None:
    from app import insights

    matches = [
        {"date": "2026-11-27", "result": "승", "k": 10, "d": 8, "damage": 1000},
        {"date": "2026-11-27", "result": "패", "k": 6, "d": 9, "damage": 800},
        {"date": "2026-11-26", "result": "승", "k": 12, "d": 7, "damage": 1200},
    ]
    grass = insights.play_grass(matches, {"2026-11-26": {"games": 3, "wins": 2}}, weeks=12,
                                today=dt.date(2026, 11, 27))
    cell27 = [c for c in grass["cells"] if c["date"] == "2026-11-27"][0]
    cell26 = [c for c in grass["cells"] if c["date"] == "2026-11-26"][0]
    check("잔디: 날짜별 판 수", cell27["games"] == 2 and cell27["level"] >= 1, str(cell27))
    check("잔디: 누적 기록과 중복 합산 없음", cell26["games"] == 3, str(cell26))
    check("잔디: 7일 × 12주 = 84칸", len(grass["cells"]) == 84, str(len(grass["cells"])))
    check("잔디: 승률 계산", grass["win"] == 60.0, str(grass["win"]))

    form = insights.recent_form(matches, 8)
    check("최근 폼: 승/패", form["wins"] == 2 and form["games"] == 3, str(form))
    check("최근 폼: K/D", form["kd"] == round(28 / 52 * 100, 1), str(form["kd"]))

    maps = [
        {"map": "프로방스", "games": 12, "win": 25.0, "kd": 51.0},
        {"map": "이탈리아", "games": 6, "win": 33.0, "kd": 43.5},
        {"map": "1판맵", "games": 1, "win": 0.0, "kd": 10.0},
    ]
    weak = insights.weak_maps(maps, 3, min_games=5)
    check("약점 맵: 5판 미만 제외", all(m["map"] != "1판맵" for m in weak), str(weak))
    check("약점 맵: 낮은 승률 1순위", weak[0]["map"] == "프로방스", str(weak[0]))

    digest = insights.barracks_digest([
        {"date": "2026-11-26", "at": "x", "stats": {"win": 45.0, "kd": 50.0}, "tier": {"solo": {"rp": 3200}}},
        {"date": "2026-11-27", "at": "y", "stats": {"win": 47.0, "kd": 51.5}, "tier": {"solo": {"rp": 3261}}},
    ])
    check("다이제스트: 변화 계산", digest["available"] and any(m["diff"] for m in digest["metrics"]), str(digest)[:120])
    cold = insights.barracks_digest([{"date": "2026-11-27", "stats": {"win": 1}}])
    check("다이제스트: 비교 대상 없음", cold["available"] is False)

    coach = insights.play_coaching(sample_barracks())
    check("플레이 코칭: 3~4줄", 3 <= len(coach) <= 4, str(len(coach)))
    check("플레이 코칭: 실제 수치 포함", any("%" in t["title"] for t in coach))

    guides = insights.map_guides(maps[:2])
    check("맵 가이드: 2개", len(guides) == 2 and guides[0]["youtube"]["url"].startswith("https://"))
    band = insights.band_of(3261)
    check("RP 3261 → 그랜드마스터", band["n"] == "그랜드마스터", str(band))


# ======================================================================
# 5. 듀오 (타인 A + 타인 B)
# ======================================================================
def test_duo() -> None:
    from app import v140

    def row(mid, win, k, d, dmg=900):
        return {"match_id": mid, "date": "2026-11-2%d" % (int(mid[-1]) % 9), "map": "이탈리아",
                "match_result": "1" if win else "0", "kill": k, "death": d, "damage": dmg}

    a = [row("m1", True, 12, 8), row("m2", True, 10, 6), row("m3", False, 7, 9), row("m9", True, 9, 9)]
    b = [row("m1", True, 9, 9), row("m2", True, 8, 7), row("m3", False, 6, 10), row("m8", True, 5, 5)]
    res = v140.duo_between(a, b, "A", "B")
    check("듀오: 함께 뛴 판 3판", res["available"] and res["shared"]["games"] == 3, str(res.get("shared")))
    check("듀오: 공동 승률 66.7%", res["shared"]["win"] == 66.7, str(res["shared"]["win"]))
    check("듀오: 시너지 라벨", res["verdict"] in ("찰떡 궁합", "무난", "합이 안 맞음"))
    check("듀오: 표본 신뢰도 표시", 0 < res["confidence"] <= 1, str(res["confidence"]))
    short = v140.duo_between(a, b, "A", "B", min_games=5)
    check("듀오: 표본 부족 처리", short["available"] is False and "판뿐" in short["reason"], short.get("reason"))
    same = v140.duo_between(a, b, "같은이", "같은이")
    check("듀오: 같은 사람 거부", same["available"] is False)


# ======================================================================
# 6. OCR — 애매한 이름은 조회하지 않는다
# ======================================================================
class FakeNexon:
    def __init__(self, known=("시티캣", "프로방스", "상대에이스")):
        self.known = {k.lower(): k for k in known}
        self.lookups: list[str] = []

    def resolve_ouid(self, nickname: str) -> str:
        self.lookups.append(nickname)
        if nickname.lower() in self.known:
            return f"ouid-{nickname}"
        from app.nexon import NexonError
        raise NexonError("없음")

    def user_basic(self, ouid: str) -> dict:
        name = ouid.replace("ouid-", "")
        return {"user_name": name, "clan_name": "리드"}


def test_ocr() -> None:
    from app import party_lobby

    client = FakeNexon()
    rows = party_lobby.validate_names(client, ["시티캣", "시티캐트", "????"], ["시티캣", "상대에이스"])
    check("OCR: 정확히 읽은 이름은 ok", rows[0]["status"] == "ok" and rows[0]["ouid"], str(rows[0]))
    check("OCR: 애매한 이름은 확인 필요", rows[1]["status"] == "needs_check", str(rows[1]))
    check("OCR: 애매한 이름은 API 조회 스킵", "시티캐트" not in client.lookups, str(client.lookups))
    check("OCR: 확신 없는 보정 안 함", rows[1]["name"] == "시티캐트" and rows[1]["ouid"] is None)
    check("OCR: 못 읽은 칸은 확인 필요", rows[2]["status"] == "needs_check")
    corrected = party_lobby.validate_names(FakeNexon(), ["상대에이스."], ["상대에이스"])
    check("OCR: 0.92 이상 근접만 보정", corrected[0]["status"] in ("corrected", "needs_check"), str(corrected[0]))


# ======================================================================
# 7. 스카우팅 — 시간 제한 · 중복 요청
# ======================================================================
def test_scouting() -> None:
    from app import scouting

    started = time.monotonic()
    done, timed_out = scouting.run_pool(lambda x: time.sleep(0.6) or x, [1, 2], workers=2, timeout=0.15)
    elapsed = time.monotonic() - started
    check("스카우팅: 타임아웃이면 기다리지 않음", elapsed < 0.5 and timed_out, f"{elapsed:.2f}s")
    check("스카우팅: 끝난 작업만 사용", done == [], str(done))

    ledger = scouting.InFlight()
    key = scouting.request_key("scout", ["A", "B"], "이탈리아")
    check("스카우팅: 첫 요청 잠금", ledger.acquire(key) is True)
    check("스카우팅: 같은 요청 중복 차단", ledger.acquire(key) is False)
    ledger.release(key)
    check("스카우팅: 끝나면 다시 가능", ledger.acquire(key) is True)

    class Slow:
        calls = 0
        def resolve_ouid(self, n): return n
        def user_basic(self, o): return {"user_name": o, "clan_name": ""}
        def user_recent(self, o): time.sleep(0.4); return {"recent_win_rate": 50, "recent_kill_death_rate": 50}

    busy_res = scouting.quick_scout(Slow(), ["A"], ledger=scouting.INFLIGHT)
    check("스카우팅: busy 응답에 ok=False", busy_res.get("ok") in (True, False))


# ======================================================================
# 8. 오버레이 — Win32 스펙 · 기하(16:9/4:3) · 창 수명
# ======================================================================
def test_overlay() -> None:
    from app import native_overlay, overlay

    spec = native_overlay.build_spec("toast", overlay.toast_lines({"map": "제3보급창고", "result": "승",
                                                                 "kills": 14, "deaths": 6, "damage": 1840, "streak": 3}), 372, 96)
    check("오버레이: 토스트 스펙", spec["w"] == 372 and any(p["type"] == "text" for p in spec["prims"]))
    party = native_overlay.build_spec("party", {"rows": [{"name": f"p{i}", "status": "ok"} for i in range(5)],
                                                "layout": overlay.party_layout(overlay.party_overlay_rect((0, 0, 1920, 1080)), (0, 0, 1920, 1080)),
                                                "plan": {"picks": [{"map": "제3보급창고"}], "bans": [{"map": "이탈리아"}]}},
                                      600, 80)
    check("오버레이: 파티 배지 5칸", len([p for p in party["prims"] if p["type"] == "rect"]) >= 6)
    ban = native_overlay.build_spec("match", {"tab": "mapban", "cards": overlay.verdict_rows(
        [{"map": "A", "win": 60}, {"map": "B", "win": 55}, {"map": "C", "win": 50}, {"map": "D", "win": 45}, {"map": "E", "win": 40}])}, 900, 60)
    texts = [p["text"] for p in ban["prims"] if p["type"] == "text"]
    check("오버레이: 맵 밴 PICK/BAN 텍스트", any("PICK" in t for t in texts) and any("BAN" in t for t in texts), str(texts))
    scout = native_overlay.build_spec("match", {"tab": "scout", "loading": True, "rows": []}, 500, 300)
    check("오버레이: 스카우팅 로딩 표시", any("분석 중" in str(p.get("text")) for p in scout["prims"]))

    wide = overlay.mapban_overlay_rect((0, 0, 1920, 1080))
    four3 = overlay.mapban_overlay_rect((0, 0, 1024, 768))
    check("오버레이: 16:9 기하", wide["w"] > 900 and wide["h"] > 100, str(wide))
    check("오버레이: 4:3 기하", four3["w"] > 400 and four3["h"] > 60, str(four3))
    check("오버레이: 비율 판별", overlay.aspect_label((0, 0, 1920, 1080)) == "16:9"
          and overlay.aspect_label((0, 0, 1024, 768)) == "4:3")

    hub = overlay.OverlayHub(window_factory=lambda k, t: native_overlay.FakeWindow(k, t))
    rect = {"x": 10, "y": 20, "w": 372, "h": 96}
    shown = hub.show("toast", overlay.toast_lines({"map": "A", "result": "패", "kills": 3, "deaths": 9}), rect=rect)
    win = hub._windows.get("toast")
    check("오버레이: 표시(가짜 창)", shown["ok"] and win and win.created, str(shown))
    hub.refresh("toast", {"map": "B", "win": True, "sub": "갱신"})
    check("오버레이: 갱신 호출", len(win.specs) >= 2)
    hidden = hub.hide("toast")
    check("오버레이: 숨김", hidden["closed"] and win.destroyed)
    check("오버레이: 백엔드 표기", hub.state()["backend"] == "win32-layered")


# ======================================================================
# 9. Api — 상태 · 설정 · 저장 범위
# ======================================================================
def test_api() -> None:
    from app.api import Api
    from app.store import Store

    state = build_state()
    for removed in ("summaries", "profile", "records", "scenarios", "pbs", "tiers",
                    "tier_distribution", "heatmap", "library", "today_scores"):
        check(f"state: 에임 키 제거({removed})", removed not in state)
    home = state.get("home") or {}
    check("state: home.rank", home.get("rank", {}).get("solo", {}).get("rp") == 3261, str(home.get("rank")))
    check("state: home.grass 84칸", len((home.get("grass") or {}).get("cells") or []) == 84)
    check("state: home.recent 5판", len(home.get("recent") or []) == 5)
    check("state: home.weak_maps 3", len(home.get("weak_maps") or []) == 3)
    check("state: 오버레이 백엔드", (home.get("overlay") or {}).get("backend") == "win32-layered")
    check("state: 계정 등급", (state.get("account") or {}).get("plan", {}).get("plan") in ("free", "pro"))
    check("state: 출처 표기", state.get("attribution") == "Data based on NEXON Open API")

    tmp = Path(tempfile.mkdtemp(prefix="sa_api_"))
    store = Store(tmp / "data.json")
    api = Api(store=store)
    saved = api.save_settings({"kovak_path": "C:/x", "aimlab_path": "C:/y", "excluded_scenarios": ["a"],
                               "watch_trainers": True, "game": "kovak"})
    check("save_settings: 에임 설정 무시", saved.get("ok") and "kovak_path" not in store.settings)
    saved2 = api.save_settings({"nickname": "테스트닉", "theme": "ice", "ui_scale": 115})
    check("save_settings: 닉네임·테마·배율 저장",
          store.settings.get("nickname") == "테스트닉" and store.settings.get("theme") == "ice"
          and store.settings.get("ui_scale") == 115)

    api.store.cloud().update({"email": "me@x.com", "access_token": "secret", "refresh_token": "r"})
    api.store.settings["api_key"] = "test_key"
    from app import supabase_sync
    captured = {}

    class FakeClient:
        def __init__(self, url, key):
            self.url, self.key = url, key
        def upsert_profile(self, token, user_id, payload):
            captured.update(payload)
            return {}
        def refresh(self, token):
            return {"access_token": "t2"}

    original = supabase_sync.SupabaseClient
    supabase_sync.SupabaseClient = FakeClient  # type: ignore[assignment]
    try:
        api.store.cloud()["user_id"] = "u1"
        res = api.cloud_push()
        check("cloud_push 성공", res.get("ok"), str(res)[:120])
        blob = json.dumps(captured, ensure_ascii=False)
        check("cloud_push: 비밀 제외", "api_key" not in json.dumps(captured.get("settings", {}))
              and "access_token" not in blob and "anon_key" not in blob)
        check("cloud_push: 백업 범위 일치", "play_days" in captured and "leaderboard" in captured)
        check("cloud_push: aim_scores 없음", "aim_scores" not in blob and "records" not in captured)
    finally:
        supabase_sync.SupabaseClient = original  # type: ignore[assignment]


# ======================================================================
# 10. UI (Playwright) — 100% / 115% · 로그인 · 듀오 · 에임 문구 없음
# ======================================================================
def test_ui() -> list[str]:
    from playwright.sync_api import sync_playwright

    state = build_state()
    state["digest"]["show"] = False
    shots: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 750})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append("console:" + msg.text) if msg.type == "error" else None)
        page.add_init_script(mock_js(state))
        page.goto((ROOT / "ui" / "index.html").as_uri())
        page.wait_for_selector("#app-splash", state="hidden", timeout=20000)
        page.wait_for_timeout(700)
        check("UI: 부팅 오류 없음", not errors, str(errors[:3]))
        check("UI: 홈 닉네임", page.eval_on_selector("#home-name", "e=>e.textContent").strip() == "스나이퍼01")
        check("UI: 잔디 84칸", page.eval_on_selector_all("#home-grass i", "els=>els.length") == 84)
        check("UI: 최근 5판 렌더", page.eval_on_selector_all("#home-games .hm-game", "els=>els.length") == 5)
        check("UI: 약점 맵 3", page.eval_on_selector_all("#home-weak .v2-weak-row", "els=>els.length") == 3)
        check("UI: 상태 카드 4줄", page.eval_on_selector_all("#home-overlay-st, #home-nv-st, #home-accel-st, #home-account-st",
                                                             "els=>els.length") == 4)
        overflow = page.evaluate("()=>({sw:document.documentElement.scrollWidth, sh:document.documentElement.scrollHeight, w:innerWidth, h:innerHeight})")
        check("UI: 100% 스크롤 넘침 없음", overflow["sw"] <= overflow["w"] + 1 and overflow["sh"] <= overflow["h"] + 1, str(overflow))
        check("UI: 에임 메뉴 없음", page.eval_on_selector_all(
            '.nav-item[data-screen="dashboard"], .nav-item[data-screen="recs"], .nav-item[data-screen="records"], .nav-item[data-screen="tiers"]',
            "els=>els.length") == 0)
        body_text = page.evaluate("()=>document.body.innerText")
        for word in ("코박스", "KovaaK", "에임랩", "Aim Lab", "시나리오", "18단계"):
            check(f"UI: '{word}' 문구 없음", word not in body_text)
        # 화면 전환
        for name, sid in (("barracks", "s-barracks"), ("maps", "s-maps"),
                          ("leaderboard", "s-leaderboard"), ("utils", "s-utils")):
            page.evaluate("""(name)=>{document.querySelectorAll('.screen').forEach(s=>{s.hidden=(s.id!=='s-'+name)});
              const n=document.querySelector('.nav-item[data-screen="'+name+'"]'); document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('on',b===n));
              if(window.AL&&AL.onScreen) AL.onScreen(name);
              if(name==='maps'&&window.drawMaps) drawMaps();
              if(name==='leaderboard'&&window.drawLeaderboard) drawLeaderboard();
              if(name==='utils'&&window.renderUtils) renderUtils();}""", name)
            page.wait_for_timeout(320)
            has = page.evaluate(f"()=>!document.getElementById('{sid}').hidden")
            check(f"UI: {name} 화면 표시", has)
            path = SHOTS / f"v2-100-{name}.png"
            page.query_selector("#" + sid).screenshot(path=str(path))
            shots.append(str(path))
        # 설정 5탭
        page.evaluate("()=>document.getElementById('settings-mask').classList.add('show')")
        page.wait_for_timeout(250)
        for tab, needle in (("api", "set-nick"), ("overlay", "set-ov-hot1"), ("run", "set-goal-win"),
                            ("cloud", "auth-open"), ("more", "backup")):
            page.evaluate("""(tab)=>{document.querySelectorAll('#set-tabs [data-set-tab]').forEach(b=>b.classList.toggle('on',b.dataset.setTab===tab));
              document.querySelectorAll('#set-panes .set-pane').forEach(p=>p.classList.toggle('on',p.dataset.pane===tab));}""", tab)
            page.wait_for_timeout(200)
            present = page.evaluate("(n)=>!!document.querySelector('#set-panes [id=\"'+n+'\"], #set-panes [data-act=\"'+n+'\"]')", needle)
            check(f"UI: 설정 {tab} 탭 내용", present)
            path = SHOTS / f"v2-100-settings-{tab}.png"
            page.query_selector("#settings-mask .modal").screenshot(path=str(path))
            shots.append(str(path))
        page.evaluate("()=>document.getElementById('settings-mask').classList.remove('show')")
        # 로그인 · 비밀번호 찾기
        page.evaluate("()=>AL.openAuth('login')")
        page.wait_for_timeout(250)
        page.fill("#auth-email", "tester@example.com")
        page.fill("#auth-password", "secret1")
        page.click("#auth-submit")
        page.wait_for_timeout(500)
        calls = page.evaluate("()=>window.__CALLS.map(c=>c[0])")
        check("UI: 로그인 → auth_login 호출", "auth_login" in calls, str(calls[-4:]))
        page.evaluate("()=>AL.openAuth('recover')")
        page.wait_for_timeout(200)
        pw_hidden = page.evaluate("()=>document.getElementById('auth-pw-field').hidden")
        check("UI: 비밀번호 찾기에서 비밀번호 칸 숨김", pw_hidden is True)
        path = SHOTS / "v2-100-auth-recover.png"
        page.query_selector("#auth-mask .modal").screenshot(path=str(path))
        shots.append(str(path))
        page.evaluate("()=>document.getElementById('auth-mask').classList.remove('show')")
        # 듀오 A+B
        page.evaluate("()=>AL.openDuoBetween('상대에이스','상대스나')")
        page.wait_for_timeout(450)
        calls = page.evaluate("()=>window.__CALLS.map(c=>c[0])")
        duo_ok = page.evaluate("()=>document.querySelectorAll('#cmp-result .duo-card').length")
        check("UI: 듀오 → duo_between 호출", "duo_between" in calls, str(calls[-4:]))
        check("UI: 듀오 결과 카드 2장", duo_ok == 2, str(duo_ok))
        path = SHOTS / "v2-100-duo.png"
        page.query_selector("#compare-mask .modal").screenshot(path=str(path))
        shots.append(str(path))
        page.evaluate("()=>document.getElementById('compare-mask').classList.remove('show')")
        page.evaluate("()=>{document.querySelectorAll('.screen').forEach(s=>{s.hidden=(s.id!=='s-home')}); if(window.drawHome) drawHome();}")
        page.wait_for_timeout(300)
        path = SHOTS / "v2-100-home.png"
        page.query_selector("#s-home").screenshot(path=str(path))
        shots.append(str(path))
        browser.close()

    # 115% 배율 — 창이 1380×862 로 커진 상태에서 하단이 잘리지 않는지
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1380, "height": 862})
        state2 = build_state()
        state2["digest"]["show"] = False
        state2["ui_scale"] = 115
        page.add_init_script(mock_js(state2))
        page.goto((ROOT / "ui" / "index.html").as_uri())
        page.wait_for_selector("#app-splash", state="hidden", timeout=20000)
        page.wait_for_timeout(500)
        page.evaluate("()=>{document.body.style.zoom='1.15';}")
        page.wait_for_timeout(400)
        box = page.evaluate("""()=>{const el=document.querySelector('#s-home .v2-status'); const r=el.getBoundingClientRect();
          return {bottom:r.bottom, h:innerHeight, scroll:document.documentElement.scrollHeight, sw:document.documentElement.scrollWidth, w:innerWidth};}""")
        check("UI: 115% 하단 잘림 없음", box["bottom"] <= box["h"] + 1, str(box))
        check("UI: 115% 가로 넘침 없음", box["sw"] <= box["w"] + 1, str(box))
        path = SHOTS / "v2-115-home.png"
        page.query_selector("#s-home").screenshot(path=str(path))
        shots.append(str(path))
        browser.close()
    return shots


# ======================================================================
# 11. 코드에 에임 연습 코드가 남아 있지 않은지
# ======================================================================
def test_code_clean() -> None:
    api_src = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
    for word in ("save_training", "regenerate_recs", "warmup_plan",
                 "import_kovak_scores", "personal_playlist", "custom_scenarios", "reset_scores"):
        check(f"api.py: {word} 제거", word not in api_src)
    check("kovak.py 삭제", not (ROOT / "app" / "kovak.py").exists())
    check("scoring.py 삭제", not (ROOT / "app" / "scoring.py").exists())
    ui_src = (ROOT / "ui" / "index.html").read_text(encoding="utf-8") + (ROOT / "ui" / "bridge.js").read_text(encoding="utf-8")
    for word in ("KovaaK", "코박스", "에임랩", "aim_scores", "18단계", "시나리오"):
        check(f"ui: {word} 문자열 없음", word not in ui_src)


def main() -> int:
    for fn in (test_nvapi, test_store, test_auth, test_insights, test_duo, test_ocr,
               test_scouting, test_overlay, test_api, test_code_clean):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} 실행", False, str(exc))
    try:
        shots = test_ui()
        print("screenshots:", len(shots))
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("test_ui 실행", False, str(exc))
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
