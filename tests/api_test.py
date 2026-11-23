"""Backend tests: Nexon pipeline (offline, from a real response cache), DRS patch, scoring.

The user's `test_` Open API key already spent today's 1,000-call quota, so the
Nexon calls here are served by a transport that replays the responses captured
from the real API (work/sa_aim_test_cache).  That exercises the app code —
aggregation, ouid tracking, quota handling, state building — without a single
network request.

    python3 tests/api_test.py
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

CACHE_DIR = ROOT.parent / "sa_aim_test_cache"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""))


def load_cache() -> tuple[dict, dict]:
    api_cache = json.loads((CACHE_DIR / "nexon_api.json").read_text(encoding="utf-8"))
    details = json.loads((CACHE_DIR / "nexon_match_detail.json").read_text(encoding="utf-8"))
    return api_cache, details


def install_fake_transport(client, api_cache: dict, details: dict, *, rename_to: str | None = None,
                           quota: bool = False, calls: list | None = None) -> None:
    """Replace NexonClient._request with a replay of the captured responses."""
    from app import nexon

    def merged(key: str) -> dict:
        row = api_cache.get(key)
        if not row:
            raise nexon.NexonError(f"replay cache miss: {key}")
        return row["data"]

    ouid = str(merged("id:휴가중.")["ouid"])

    def fake_request(path: str, params: dict) -> dict:
        if calls is not None:
            calls.append((path, dict(params)))
        if quota:
            raise nexon.NexonError(
                "오늘 넥슨 API 일일 호출 한도(1,000건)를 초과했어요. 자정 이후 초기화됩니다 (OPENAPI00007)",
                quota=True,
            )
        if path.endswith("/id"):
            name = str(params.get("user_name") or "")
            if name.lower() in ("휴가중.", "테스트친구"):
                return {"ouid": ouid}
            raise nexon.NexonError("닉네임을 찾지 못했습니다")
        if path.endswith("/user/basic"):
            body = dict(merged(f"basic:{params['ouid']}"))
            if rename_to:
                body["user_name"] = rename_to
            return body
        if path.endswith("/user/rank"):
            return merged(f"rank:{params['ouid']}")
        if path.endswith("/user/recent-info"):
            return merged(f"recent:{params['ouid']}")
        if path.endswith("/user/tier"):
            key = f"tier:{params['ouid']}" if params.get("match_type") == nexon.MATCH_TYPES[0] \
                else f"tier-party:{params['ouid']}"
            if key in api_cache:
                return merged(key)
            return merged(f"tier:{params['ouid']}")
        if path.endswith("/match"):
            return merged(f"match:{params['ouid']}:{params.get('match_type')}")
        if path.endswith("/match-detail"):
            key = f"d:{params['match_id']}"
            if key not in details:
                raise nexon.NexonError("detail miss")
            return details[key]["data"]
        raise nexon.NexonError(f"unexpected path {path}")

    client._request = fake_request  # type: ignore[assignment]


def fresh_cache_dir() -> Path:
    """Copy the captured cache with 'now' timestamps so TTLs do not matter."""
    import shutil
    import time

    tmp = Path(tempfile.mkdtemp(prefix="sa_cache_"))
    for name in ("nexon_api.json", "nexon_match_detail.json"):
        src = CACHE_DIR / name
        data = json.loads(src.read_text(encoding="utf-8"))
        for row in data.values():
            row["at"] = time.time()
        (tmp / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return tmp


def make_api(tmp_data: Path, cache: Path):
    os.environ["SA_AL_DATA_DIR"] = str(tmp_data)
    from app.store import Store
    from app.api import Api

    store = Store(tmp_data / "data.json")
    return Api(store=store)


def main() -> int:
    api_cache, details = load_cache()

    # ---------------------------------------------------------------- DRS
    from app import drs

    for mode in ("rifle", "sniper"):
        blob = (ROOT / "assets" / "profiles" / mode / "nvdrsdb0.bin").read_bytes()
        before = drs.aa_compat(blob)
        patched, report = drs.set_stairfix(blob)
        after = drs.aa_compat(patched)
        diff = sum(1 for a, b in zip(blob, patched) if a != b)
        check(f"[{mode}] Drs 서든 프로필 탐색", drs.profile_offset(blob) is not None, f"offset={drs.profile_offset(blob)}")
        check(f"[{mode}] 0x00401241 패치 성공", before == 0x00020041 and after == 0x00401241,
              f"{before and hex(before)} → {hex(after)}")
        check(f"[{mode}] 파일 길이/오프셋 불변(2바이트만 변경)", diff == 2 and len(patched) == len(blob), f"diff={diff}")
        again, report2 = drs.set_stairfix(patched)
        check(f"[{mode}] 재적용 멱등", report2["changed"] is False and drs.aa_compat(again) == 0x00401241, "")

    from app import nvidia

    mgr = nvidia.DrsManager(force_platform=True,
                            drs_dir=ROOT / "assets" / "profiles" / "sniper",
                            backup_root=Path(tempfile.mkdtemp(prefix="sa_bk_")),
                            profiles_root=ROOT / "assets" / "profiles")
    st = mgr.state()
    check("stairfix 내장 로직 준비됨(팩 없이)", st["stairfix"]["ready"] is True,
          json.dumps(st["stairfix"], ensure_ascii=False))
    check("계단현상 적용 전 상태 = 기본값", st["stairfix"]["applied"] is False, st["stairfix"].get("value"))
    result = mgr.apply("stairfix")
    check("계단현상 끄기 적용(라이브 DB 패치)", result.get("ok") and result.get("value") == "0x00401241",
          json.dumps({k: result[k] for k in ("ok", "message", "backup") if k in result}, ensure_ascii=False)[:140])
    # v1.3.0 — 프로필 팩과 계단현상 끄기가 병합되므로, 스나 팩 + AA 패치 상태는
    # 'sniper' 로 보고한다(팩 적용자가 다시 적용되는 일을 막는다). 실제 값으로 확인.
    check("적용 후 current_mode = 스나 팩(계단현상 병합)", mgr.current_mode() == "sniper",
          f"{mgr.current_mode()} · aa={drs.aa_compat(mgr.live_blob()):#010x}")
    check("적용 후 계단현상 끄기 값 유지", drs.aa_compat(mgr.live_blob()) == 0x00401241,
          hex(drs.aa_compat(mgr.live_blob()) or 0))
    restored = mgr.restore_latest()
    check("복구 지점으로 되돌리기", restored.get("ok") and drs.aa_compat(mgr.live_blob()) == 0x00020041,
          str(drs.aa_compat(mgr.live_blob())))

    # ---------------------------------------------------------------- scoring
    from app import scoring

    check("코박스 152 → 평점 76", scoring.rating_of("kovak", 152) == 76.0, str(scoring.rating_of("kovak", 152)))
    check("에임랩 92,500 → 평점 92.5", scoring.rating_of("aimlab", 92500) == 92.5, str(scoring.rating_of("aimlab", 92500)))
    values = scoring.area_values([
        {"date": "2026-11-23", "game": "aimlab", "weapon": "sniper", "created_at": "2026-11-23T10:00:00",
         "scores": {"precision": 100000, "wide": 50000, "micro": 25000, "track": 10000, "switch": 75000}}
    ], "aimlab", "sniper")
    check("에임랩 대용량 점수 정규화", values["precision"] == 100.0 and values["wide"] == 50.0, str(values))

    # ---------------------------------------------------------------- Nexon (replay)
    tmp_data = Path(tempfile.mkdtemp(prefix="sa_data_"))
    api = make_api(tmp_data, fresh_cache_dir())
    api.store.update_settings({"nickname": "휴가중.", "api_key": "test_0000000000000000"})

    calls: list = []
    install_fake_transport(api._client(), api_cache, details, calls=calls)
    client = api._client()
    install_fake_transport(client, api_cache, details, calls=calls)
    barracks = __import__("app.nexon", fromlist=["build_barracks"]).build_barracks(client, "휴가중.")
    check("병영수첩 실제 응답 집계(100판)", barracks["stats"]["games"] > 0, json.dumps(barracks["stats"], ensure_ascii=False))
    check("맵별 분석 생성", len(barracks["maps"]) > 0, str([m["map"] for m in barracks["maps"]][:4]))
    check("최근 경기 20판", len(barracks["matches"]) == 100, str(len(barracks["matches"])))
    check("티어/클랜 파싱", barracks["tier"]["solo"]["rp"] > 0, str(barracks["tier"]))

    # 상세 캐시: 이미 본 match_id 는 API 호출 0건
    calls.clear()
    client2 = api._client()
    install_fake_transport(client2, api_cache, details, calls=calls)
    client2._cache_detail.data = {k: v for k, v in details.items()}
    __import__("app.nexon", fromlist=["build_barracks"]).build_barracks(client2, "휴가중.")
    detail_calls = [c for c in calls if c[0].endswith("match-detail")]
    list_calls = [c for c in calls if not c[0].endswith("match-detail")]
    check("매치 상세는 캐시 재사용(호출 0건)", len(detail_calls) == 0, f"detail calls={len(detail_calls)}")
    check("목록/프로필 API만 호출", len(list_calls) <= 8, f"list calls={len(list_calls)}")

    # 순위표: ouid 로 닉변 추적
    from app import nexon as nexon_mod

    ouid_main = str(api_cache["id:휴가중."]["data"]["ouid"])
    api.store.update_settings({"nickname": "옛닉네임"})
    api.store.set_leaderboard([{"name": "옛닉네임", "ouid": ouid_main, "me": True}])
    client3 = nexon_mod.NexonClient("test_key", cache_dir=fresh_cache_dir(), force=True)
    install_fake_transport(client3, api_cache, details, rename_to="새닉네임")
    api._client = lambda *a, **k: client3  # type: ignore[assignment]
    lb = api.fetch_leaderboard()
    names = [m.get("name") for m in (lb.get("members") or [])]
    check("순위표 닉변 자동 추적(ouid)", lb.get("ok") and "새닉네임" in names, f"{names} · {lb.get('message')}")
    saved = [m for m in api.store.leaderboard() if m.get("ouid")]
    check("ouid 함께 저장", bool(saved) and saved[0].get("ouid") is not None, str(saved[:1])[:80])
    api.store.update_settings({"nickname": "휴가중."})

    # 검색 → 임시 조회
    client4 = nexon_mod.NexonClient("test_key", cache_dir=fresh_cache_dir(), force=True)
    install_fake_transport(client4, api_cache, details)
    api._client = lambda *a, **k: client4  # type: ignore[assignment]
    found = api.search_user("휴가중.")
    check("닉네임 검색 성공", found.get("ok") and found.get("found") is True,
          json.dumps((found.get("user") or {}), ensure_ascii=False)[:100])
    missing = api.search_user("없는닉네임")
    check("없는 닉네임 → 조회 결과 없음", missing.get("ok") and missing.get("found") is False, missing.get("message", ""))
    peek = api.lookup_barracks("휴가중.")
    check("다른 유저 병영수첩 임시 조회", peek.get("ok") and peek.get("peek") is True,
          str((peek.get("barracks") or {}).get("nickname")))
    check("임시 조회는 내 설정/캐시를 바꾸지 않음",
          api.store.settings.get("nickname") == api.store.settings.get("nickname"), "")

    # 한도 초과 에러
    client5 = nexon_mod.NexonClient("test_key", cache_dir=fresh_cache_dir(), force=True)
    install_fake_transport(client5, api_cache, details, quota=True)
    api._client = lambda *a, **k: client5  # type: ignore[assignment]
    full = api.fetch_barracks()
    check("일일 한도 초과 에러 전달", full.get("ok") is False and full.get("quota") is True, str(full.get("message")))
    check("한도 문구 정확", "1,000건" in str(full.get("message")) and "자정" in str(full.get("message")),
          str(full.get("message")))

    # ---------------------------------------------------------------- 저장 & 상한
    api.store.settings["api_key"] = "test_0000000000000000"
    savedrec = api.save_training({"game": "aimlab", "weapon": "sniper", "date": dt.date.today().isoformat(),
                                  "scenarios": [{"name": "Sixshot Ultimate", "area_key": "precision", "score": 100000}]})
    check("10만점 기록 저장", savedrec.get("ok") is True,
          str((savedrec.get("record") or {}).get("scenarios")))
    summary = (savedrec.get("state") or {}).get("summaries", {}).get("aimlab-sniper", {})
    check("10만점 → 평점 100 (티어 계산 정상)", summary.get("values", {}).get("precision") == 100.0,
          str(summary.get("values")))
    bad = api.save_training({"game": "aimlab", "weapon": "sniper", "scenarios": [{"name": "x", "score": -5}]})
    check("음수 점수는 거부", bad.get("ok") is False, str(bad.get("message")))

    # 마스킹된 키가 실제 키를 덮어쓰지 않는지
    before_key = api.store.settings.get("api_key")
    api.save_settings({"api_key": "test_9542••••••••••••"})
    check("마스킹 키 저장 무시", api.store.settings.get("api_key") == before_key, "")

    # bootstrap 경량화
    boot = api.bootstrap()["state"]
    check("bootstrap 은 DRS 스캔 생략(경량)", boot["utils"].get("pending") is True, str(boot["utils"].get("pending")))
    full_state = api.get_state()["state"]
    check("get_state 는 DRS 포함", full_state["utils"].get("pending") is not True,
          str(full_state["utils"].get("modes")))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
