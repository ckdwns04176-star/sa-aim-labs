"""Supabase cloud sync (Auth + PostgREST) using only the standard library.

Configure either through ``config.json``::

    {"supabase_url": "https://xxxx.supabase.co", "supabase_anon_key": "eyJ..."}

or through the app's 설정 탭, or the environment variables ``SUPABASE_URL`` and
``SUPABASE_ANON_KEY``.  The anon key is a public client key; row access is
protected by the RLS policies in ``supabase/schema.sql`` (``auth.uid() = user_id``).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import paths

TABLE = "user_profiles"
SUBSCRIPTION_TABLE = "subscriptions"   # plan(free/pro) · pro_until

# 사용자가 발급받은 프로젝트 (publishable 키는 공개용 · RLS가 데이터를 보호합니다)
DEFAULT_URL = "https://ujnffbskudzkatskbmwc.supabase.co"
DEFAULT_ANON_KEY = "sb_publishable_B_6vCFDiRcNpBfdc0mG3Vw_41b7xL5x"


class SupabaseError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def load_config() -> dict[str, str]:
    """Resolution order: env → data_dir/config.json → <app>/config.json → built-in defaults."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if url and key:
        return {"url": url, "anon_key": key}
    for candidate in (paths.data_dir() / "config.json", paths.bundle_dir() / "config.json"):
        try:
            if not candidate.exists():
                continue
            data = json.loads(candidate.read_text(encoding="utf-8"))
            url = url or str(data.get("SUPABASE_URL") or data.get("supabase_url") or "").strip()
            key = key or str(data.get("SUPABASE_ANON_KEY") or data.get("supabase_anon_key") or "").strip()
            if url and key:
                return {"url": url, "anon_key": key}
        except (OSError, ValueError):
            continue
    return {"url": url or DEFAULT_URL, "anon_key": key or DEFAULT_ANON_KEY}


def save_config(url: str, anon_key: str) -> Path:
    path = paths.data_dir() / "config.json"
    payload = {"SUPABASE_URL": url.strip(), "SUPABASE_ANON_KEY": anon_key.strip()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class SupabaseClient:
    def __init__(self, url: str, anon_key: str, *, timeout: float = 20.0):
        self.url = (url or "").rstrip("/")
        self.anon_key = anon_key or ""
        self.timeout = timeout
        self.calls = 0

    # ------------------------------------------------------------ plumbing
    @property
    def configured(self) -> bool:
        return bool(self.url and self.anon_key)

    def _request(self, method: str, path: str, *, body: Any = None, token: str | None = None,
                 query: dict[str, str] | None = None, prefer: str | None = None) -> Any:
        if not self.configured:
            raise SupabaseError("Supabase URL 과 anon key 를 먼저 설정해 주세요")
        url = f"{self.url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {token or self.anon_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"SA-Aim-Labs/{paths.APP_VERSION}",
        }
        if prefer:
            headers["Prefer"] = prefer
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = raw
            message = payload
            if isinstance(payload, dict):
                message = payload.get("msg") or payload.get("message") or payload.get("error_description") or payload.get("error") or raw
            raise SupabaseError(str(message or f"HTTP {exc.code}"), status=exc.code, payload=payload) from exc
        except urllib.error.URLError as exc:
            raise SupabaseError(f"네트워크 오류 · {exc.reason}") from exc

    # ------------------------------------------------------------ auth
    @staticmethod
    def _session(payload: dict[str, Any], email: str) -> dict[str, Any]:
        user = payload.get("user") or {}
        session = {
            "email": user.get("email") or email,
            "user_id": user.get("id") or payload.get("id"),
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token"),
            "expires_in": payload.get("expires_in"),
            "needs_confirmation": not bool(payload.get("access_token")),
        }
        if session["needs_confirmation"]:
            session["message"] = "가입 확인 메일을 보냈습니다 · 메일 인증 후 로그인해 주세요"
        return session

    def sign_up(self, email: str, password: str) -> dict[str, Any]:
        body = self._request("POST", "/auth/v1/signup", body={"email": email, "password": password})
        return self._session(body or {}, email)

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        body = self._request("POST", "/auth/v1/token", body={"email": email, "password": password},
                             query={"grant_type": "password"})
        session = self._session(body or {}, email)
        if not session["access_token"]:
            raise SupabaseError("로그인에 실패했습니다 · 이메일과 비밀번호를 확인해 주세요")
        return session

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        body = self._request("POST", "/auth/v1/token", body={"refresh_token": refresh_token},
                             query={"grant_type": "refresh_token"})
        return self._session(body or {}, "")

    def recover(self, email: str, redirect_to: str = "") -> dict[str, Any]:
        """비밀번호 찾기 — ``POST /auth/v1/recover``.

        Supabase 가 재설정 링크를 담은 메일을 보낸다.  앱에는 메일만 보내고
        새 비밀번호는 받지 않는다 (비밀번호는 Supabase Auth 밖으로 나오지 않는다).
        """
        address = str(email or "").strip()
        if "@" not in address:
            raise SupabaseError("이메일 주소를 확인해 주세요")
        body: dict[str, Any] = {"email": address}
        if redirect_to:
            body["redirect_to"] = redirect_to
        self._request("POST", "/auth/v1/recover", body=body, prefer="return=minimal")
        return {"ok": True, "email": address,
                "message": "비밀번호 재설정 메일을 보냈습니다 · 메일의 링크에서 새 비밀번호를 정해 주세요"}

    def sign_out(self, access_token: str) -> None:
        try:
            self._request("POST", "/auth/v1/logout", token=access_token)
        except SupabaseError:
            pass

    def user(self, access_token: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", "/auth/v1/user", token=access_token)
        except SupabaseError:
            return None

    # ------------------------------------------------------------ data
    def upsert_profile(self, access_token: str, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = {"user_id": user_id, "payload": payload, "updated_at": _utc_now()}
        body = self._request(
            "POST", f"/rest/v1/{TABLE}",
            body=[row], token=access_token,
            query={"on_conflict": "user_id"},
            prefer="resolution=merge-duplicates,return=representation",
        )
        return (body or [{}])[0] if isinstance(body, list) else (body or {})

    def get_profile(self, access_token: str, user_id: str) -> dict[str, Any] | None:
        body = self._request(
            "GET", f"/rest/v1/{TABLE}", token=access_token,
            query={"user_id": f"eq.{user_id}", "select": "*"},
        )
        if isinstance(body, list) and body:
            return body[0]
        return None

    # ------------------------------------------------------------ subscription (v2.0.0)
    def subscription(self, access_token: str, user_id: str) -> dict[str, Any] | None:
        """public.subscriptions 에서 내 등급 한 줄 (plan · pro_until).

        테이블이 없거나 RLS 로 막혀 있으면 ``None`` — 호출한 쪽이 free 로 본다.
        """
        body = self._request(
            "GET", f"/rest/v1/{SUBSCRIPTION_TABLE}", token=access_token,
            query={"user_id": f"eq.{user_id}", "select": "plan,pro_until", "limit": "1"},
        )
        if isinstance(body, list) and body:
            return body[0]
        return None


def _utc_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def probe(url: str, anon_key: str) -> dict[str, Any]:
    """Cheap connectivity/auth check used by the settings screen."""
    client = SupabaseClient(url, anon_key)
    if not client.configured:
        return {"ok": False, "configured": False, "message": "Supabase URL 과 anon key 가 비어 있습니다"}
    try:
        client._request("GET", f"/rest/v1/{TABLE}", query={"select": "user_id", "limit": "1"})
        return {"ok": True, "configured": True, "message": "Supabase 연결 확인됨 · user_profiles 테이블 응답 정상"}
    except SupabaseError as exc:
        hint = ""
        if exc.status == 404:
            hint = " · supabase/schema.sql 을 먼저 실행해 테이블을 만들어 주세요"
        return {"ok": False, "configured": True, "message": f"{exc}{hint}", "status": exc.status}
