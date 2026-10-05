"""HMAC-signed cookies for the login session and the OAuth state."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
STATE_TTL_SECONDS = 10 * 60


def sign(payload: dict, secret: str) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(body).decode().rstrip("=")
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify(token: str, secret: str, now: float | None = None) -> dict | None:
    if not token or len(token) > 4096 or token.count(".") != 1:
        return None
    encoded, signature = token.split(".", 1)
    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if len(signature) != len(expected) or not hmac.compare_digest(expected, signature):
        return None
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    if (time.time() if now is None else now) >= float(exp):
        return None
    return payload


def issue_session(email: str, secret: str, now: float | None = None) -> str:
    issued_at = time.time() if now is None else now
    return sign(
        {"email": email, "exp": int(issued_at) + SESSION_TTL_SECONDS, "purpose": "session"},
        secret,
    )


def read_session(token: str, secret: str, now: float | None = None) -> str | None:
    payload = verify(token, secret, now)
    if not payload or payload.get("purpose") != "session":
        return None
    email = payload.get("email")
    if not isinstance(email, str) or "@" not in email:
        return None
    return email


def issue_state(state: str, secret: str, now: float | None = None) -> str:
    issued_at = time.time() if now is None else now
    return sign(
        {"exp": int(issued_at) + STATE_TTL_SECONDS, "purpose": "state", "state": state},
        secret,
    )


def read_state(token: str, secret: str, now: float | None = None) -> str | None:
    payload = verify(token, secret, now)
    if not payload or payload.get("purpose") != "state":
        return None
    state = payload.get("state")
    if not isinstance(state, str) or not state:
        return None
    return state
