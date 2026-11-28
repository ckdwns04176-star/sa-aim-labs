"""회원 로그인 · 비밀번호 찾기 · 등급(plan) 관리 — Supabase Auth 연동.

정책 (2026-11-27 사용자 확정)
---------------------------
넥슨 상업적 이용 답변이 오기 전까지는 **무료로 풀어서 유저를 먼저 모은다**.
그래서 :data:`ENFORCE_PRO_LIMIT` 는 ``False`` 이고, 지금은 free/pro 구분 없이
모든 기능을 무제한으로 쓴다.  나중에 이 값을 ``True`` 로 바꾸면 **즉시**
``free = 하루 3회 / pro = 무제한`` 이 동작한다 — 코드는 그 두 모드를 모두
갖고 있고 단위 테스트가 둘 다 검증한다.

이 모듈의 판단 함수(:func:`normalize_plan` · :func:`limit_state`)는 I/O 가 없는
순수 함수라 네트워크 없이 테스트한다.  실제 호출은 :class:`AccountService` 가
``supabase_sync.SupabaseClient`` 로 한다.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Callable

from . import supabase_sync

# ── 사용자 확정 정책값 ─────────────────────────────────────────────
ENFORCE_PRO_LIMIT = False
import re

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FREE_DAILY_LIMIT = 3
PRO_FEATURES = ("scout", "scout_last", "duo", "duo_between", "mapban", "overlay")

PLAN_LABELS = {"free": "무료", "pro": "PRO"}


def _parse_time(value: Any) -> _dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    return stamp


def normalize_plan(row: dict[str, Any] | None, now: _dt.datetime | None = None) -> dict[str, Any]:
    """``subscriptions`` 한 줄 → 등급 상태.

    * ``plan`` 은 free/pro 만 인정한다.
    * ``pro_until`` 이 지났으면 free 로 내린다 (서버가 늦게 바뀌어도 앱이 먼저 안다).
    * 아직 못 받았으면 free — 잠금이 걸리는 쪽으로는 절대 넘어가지 않는다.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    row = row or {}
    plan = str(row.get("plan") or "free").strip().lower()
    if plan not in PLAN_LABELS:
        plan = "free"
    until_raw = row.get("pro_until")
    until = _parse_time(until_raw)
    expired = bool(until and until <= now.astimezone(_dt.timezone.utc))
    if plan == "pro" and expired:
        plan = "free"
    return {
        "plan": plan,
        "label": PLAN_LABELS[plan],
        "pro_until": str(until_raw) if until_raw else None,
        "pro_until_local": until.astimezone().strftime("%Y-%m-%d %H:%M") if until else None,
        "expired": expired,
        "unlimited": plan == "pro",
    }


def limit_state(plan: dict[str, Any] | None, used: int, *, enforce: bool = ENFORCE_PRO_LIMIT,
                daily_limit: int = FREE_DAILY_LIMIT) -> dict[str, Any]:
    """오늘 남은 횟수 — ``enforce=False`` 면 항상 무제한."""
    info = plan or {"plan": "free", "label": PLAN_LABELS["free"], "unlimited": False}
    used = max(0, int(used or 0))
    pro = bool(info.get("unlimited"))
    if not enforce or pro:
        return {"enforce": bool(enforce), "plan": info.get("plan", "free"),
                "label": info.get("label", PLAN_LABELS["free"]),
                "limit": None if not enforce else "unlimited",
                "used": used, "remaining": None, "allowed": True,
                "text": "무제한 (무료 운영 기간)" if not enforce else "PRO · 무제한"}
    remaining = max(0, int(daily_limit) - used)
    return {"enforce": True, "plan": "free", "label": PLAN_LABELS["free"],
            "limit": int(daily_limit), "used": used, "remaining": remaining,
            "allowed": remaining > 0,
            "text": f"오늘 {used}/{int(daily_limit)}회 사용" if remaining else "오늘 무료 횟수를 다 썼습니다"}


class AccountService:
    """Supabase Auth 세션 + 등급을 로컬 저장소와 함께 관리한다."""

    def __init__(self, store: Any, *, client_factory: Callable[[], supabase_sync.SupabaseClient] | None = None):
        self.store = store
        self._client_factory = client_factory

    # ------------------------------------------------------------ client
    def client(self) -> supabase_sync.SupabaseClient:
        if self._client_factory is not None:
            return self._client_factory()
        cfg = supabase_sync.load_config()
        settings = (self.store.settings or {}).get("supabase") or {}
        url = (settings.get("url") or cfg["url"] or "").strip()
        key = (settings.get("anon_key") or cfg["anon_key"] or "").strip()
        return supabase_sync.SupabaseClient(url, key)

    # ------------------------------------------------------------ session
    def _remember(self, session: dict[str, Any], email: str) -> None:
        cloud = self.store.cloud()
        cloud.update({
            "email": session.get("email") or email or cloud.get("email"),
            "user_id": session.get("user_id") or cloud.get("user_id"),
            "access_token": session.get("access_token"),
            "refresh_token": session.get("refresh_token"),
        })
        self.store.update_settings({"cloud_email": email or session.get("email") or ""})
        self.store.save()

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        address = str(email or "").strip()
        if "@" not in address:
            raise ValueError("이메일 주소를 확인해 주세요")
        if not password:
            raise ValueError("비밀번호를 입력해 주세요")
        session = self.client().sign_in(address, password)
        self._remember(session, address)
        plan = self.refresh_plan()
        return {"ok": True, "email": session.get("email") or address,
                "user_id": session.get("user_id"), "plan": plan,
                "message": "로그인되었습니다"}

    def sign_up(self, email: str, password: str) -> dict[str, Any]:
        address = str(email or "").strip()
        if "@" not in address:
            raise ValueError("이메일 주소를 확인해 주세요")
        if len(str(password or "")) < 6:
            raise ValueError("비밀번호는 6자 이상으로 정해 주세요")
        session = self.client().sign_up(address, password)
        if session.get("needs_confirmation"):
            self.store.update_settings({"cloud_email": address})
            return {"ok": True, "needs_confirmation": True,
                    "message": session.get("message") or "가입 확인 메일을 보냈습니다"}
        self._remember(session, address)
        plan = self.refresh_plan()
        return {"ok": True, "needs_confirmation": False, "email": address, "plan": plan,
                "message": "회원가입 완료 · 로그인되었습니다"}

    def recover(self, email: str) -> dict[str, Any]:
        """비밀번호 찾기 — 재설정 메일.  링크는 홈페이지(#partner 페이지)로 돌아온다."""
        addr = str(email or "").strip()
        if not _EMAIL.match(addr):
            raise ValueError("이메일 주소를 확인해 주세요")
        from .supabase_sync import RECOVER_REDIRECT

        return self.client().recover(addr, redirect_to=RECOVER_REDIRECT)

    def sign_out(self) -> dict[str, Any]:
        cloud = self.store.cloud()
        try:
            if cloud.get("access_token"):
                self.client().sign_out(cloud["access_token"])
        except Exception:  # noqa: BLE001 - 서버 로그아웃 실패가 로컬 세션을 막지 않는다
            pass
        cloud.update({"access_token": None, "refresh_token": None, "user_id": None})
        self.store.save()
        self.store.set_account("free", None)
        return {"ok": True, "message": "로그아웃되었습니다"}

    # ------------------------------------------------------------ token · plan
    def token(self, *, refresh: bool = True) -> tuple[str | None, str | None]:
        """(access_token, user_id) — 액세스 토큰이 만료됐으면 한 번 새로 받는다."""
        cloud = self.store.cloud()
        token = cloud.get("access_token")
        user_id = cloud.get("user_id")
        if (not token) and refresh and cloud.get("refresh_token"):
            return self._refresh()
        return token, user_id

    def _refresh(self) -> tuple[str | None, str | None]:
        cloud = self.store.cloud()
        session = self.client().refresh(cloud.get("refresh_token") or "")
        cloud.update({"access_token": session.get("access_token"),
                      "refresh_token": session.get("refresh_token"),
                      "user_id": session.get("user_id") or cloud.get("user_id")})
        self.store.save()
        return cloud.get("access_token"), cloud.get("user_id")

    def refresh_plan(self) -> dict[str, Any]:
        """로그인 상태면 ``public.subscriptions`` 를 읽어 등급을 갱신한다."""
        token, user_id = self.token()
        if not token or not user_id:
            info = normalize_plan(None)
            self.store.set_account(info["plan"], None)
            return {**info, "synced": False}
        row = None
        error = None
        try:
            row = self.client().subscription(token, user_id)
        except Exception as exc:  # noqa: BLE001 - 등급 조회 실패는 free 로 두되 이유를 남긴다
            error = str(exc)
        info = normalize_plan(row)
        self.store.set_account(info["plan"], info.get("pro_until"),
                               self.store.cloud().get("email"))
        return {**info, "synced": row is not None, "error": error}

    # ------------------------------------------------------------ state · quota
    def plan(self) -> dict[str, Any]:
        cached = self.store.account()
        info = normalize_plan({"plan": cached.get("plan"), "pro_until": cached.get("pro_until")})
        info["checked_at"] = cached.get("checked_at")
        return info

    def quota(self, *, enforce: bool | None = None) -> dict[str, Any]:
        state = limit_state(self.plan(), int(self.store.usage().get("count") or 0),
                            enforce=ENFORCE_PRO_LIMIT if enforce is None else enforce)
        return state

    def guard(self, feature: str) -> dict[str, Any] | None:
        """PRO 기능 호출 전 확인 — 쓸 수 있으면 ``None``, 아니면 안내 dict."""
        if not ENFORCE_PRO_LIMIT:
            return None
        if feature not in PRO_FEATURES:
            return None
        state = self.quota()
        if not state.get("allowed"):
            return {"ok": False, "locked": True, "plan": state.get("plan"),
                    "message": ("오늘 무료 사용 횟수를 다 썼습니다 · PRO 는 무제한입니다"
                                if state.get("plan") == "free" else "권한을 확인하지 못했습니다"),
                    "quota": state}
        return None

    def use(self, feature: str) -> dict[str, Any]:
        """실제로 한 번 썼을 때 호출 — 잠금이 꺼져 있으면 아무 것도 세지 않는다."""
        if not ENFORCE_PRO_LIMIT or feature not in PRO_FEATURES:
            return self.quota()
        if self.plan().get("unlimited"):
            return self.quota()
        self.store.bump_usage(1)
        return self.quota()

    # ------------------------------------------------------------ payload
    def state(self) -> dict[str, Any]:
        cloud = self.store.cloud()
        plan = self.plan()
        quota = self.quota()
        return {
            "signed_in": bool(cloud.get("access_token") or cloud.get("refresh_token")),
            "email": cloud.get("email") or (self.store.settings or {}).get("cloud_email") or "",
            "user_id": cloud.get("user_id"),
            "plan": plan,
            "quota": quota,
            "enforce": bool(ENFORCE_PRO_LIMIT),
            "free_daily_limit": FREE_DAILY_LIMIT,
            "last_sync": cloud.get("last_sync"),
        }


def auth_state(service: AccountService) -> dict[str, Any]:
    """작은 도우미 — 테스트와 API 가 같은 모양을 본다."""
    return service.state()
