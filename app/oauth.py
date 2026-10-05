"""Google OAuth authorization-code flow. The page never sees the client secret."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from app.config import Config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class OAuthError(Exception):
    def __init__(self, code: str):
        if code not in ("signin", "denied"):
            code = "signin"
        self.code = code
        super().__init__(code)


def authorization_url(config: Config, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": config.google_client_id,
            "prompt": "select_account",
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def fetch_email(config: Config, code: str, opener=None) -> str:
    """Exchange the code and return the verified Google email."""
    if opener is None:
        opener = urllib.request.urlopen
    token = _access_token(config, code, opener)
    info = _userinfo(token, opener)
    email = info.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise OAuthError("signin")
    if info.get("email_verified") is not True:
        raise OAuthError("denied")
    return email


def _access_token(config: Config, code: str, opener) -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": config.google_client_id,
            "client_secret": config.google_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": config.redirect_uri,
        }
    ).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    payload = _read_json(request, opener)
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise OAuthError("signin")
    return token


def _userinfo(token: str, opener) -> dict:
    request = urllib.request.Request(USERINFO_URL)
    request.add_header("Authorization", f"Bearer {token}")
    payload = _read_json(request, opener)
    if not isinstance(payload, dict):
        raise OAuthError("signin")
    return payload


def _read_json(request, opener) -> dict:
    try:
        with opener(request, timeout=10) as response:
            raw = response.read(65536)
            if response.read(1):
                raise OAuthError("signin")
        return json.loads(raw.decode())
    except OAuthError:
        raise
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeError, ValueError):
        raise OAuthError("signin") from None
