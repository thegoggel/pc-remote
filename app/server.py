"""HTTP app: Google sign-in, then one button that wakes the PC and starts Steam."""

from __future__ import annotations

import hmac
import logging
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.config import Config, load_config
from app.oauth import authorization_url, fetch_email
from app.pages import app_page, login_page
from app.session import (
    SESSION_TTL_SECONDS,
    STATE_TTL_SECONDS,
    issue_session,
    issue_state,
    read_session,
    read_state,
)
from app.wake import build_wake_service

log = logging.getLogger(__name__)


class App:
    def __init__(self, config: Config, wake, fetch=None):
        self.config = config
        self.wake = wake
        self.fetch_email = fetch_email if fetch is None else fetch


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pc-remote"

    def do_GET(self) -> None:
        path, query = _split(self.path)
        if path == "/":
            self._home(query)
        elif path == "/auth/google":
            self._start_google()
        elif path == "/auth/callback":
            self._callback(query)
        else:
            self._plain(404, "Not found")

    def do_POST(self) -> None:
        path, _query = _split(self.path)
        if not self._discard_body():
            self._plain(413, "Request too large")
            return
        if path == "/wake":
            self._wake()
        elif path == "/logout":
            self._logout()
        else:
            self._plain(404, "Not found")

    def log_message(self, fmt: str, *args) -> None:
        path = urllib.parse.urlsplit(self.path).path
        log.info("%s %s %s", self.client_address[0], self.command, path)

    def _home(self, query: dict[str, list[str]]) -> None:
        email = self._email()
        if not email:
            error = _first(query, "error")
            if error not in ("signin", "denied"):
                error = ""
            self._html(200, login_page(error))
            return
        snap = self.server.app.wake.snapshot()
        self._html(200, app_page(email, snap["phase"], snap["detail"]))

    def _start_google(self) -> None:
        state = secrets.token_urlsafe(32)
        url = authorization_url(self.server.app.config, state)
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "accounts.google.com":
            self._plain(500, "Something went wrong.")
            return
        cookie = _cookie(
            "pc_oauth_state",
            issue_state(state, self.server.app.config.session_secret),
            STATE_TTL_SECONDS,
            self.server.app.config.secure_cookies,
        )
        self._redirect(url, [cookie])

    def _callback(self, query: dict[str, list[str]]) -> None:
        clear = _cookie(
            "pc_oauth_state",
            "",
            0,
            self.server.app.config.secure_cookies,
            clear=True,
        )
        if _first(query, "error"):
            self._redirect("/?error=signin", [clear])
            return
        code = _first(query, "code")
        state = _first(query, "state")
        if not code or not state or len(code) > 512 or len(state) > 200:
            self._redirect("/?error=signin", [clear])
            return
        saved = read_state(
            self._cookies().get("pc_oauth_state", ""),
            self.server.app.config.session_secret,
        )
        if not saved or len(saved) != len(state) or not hmac.compare_digest(saved, state):
            self._redirect("/?error=signin", [clear])
            return
        try:
            email = self.server.app.fetch_email(self.server.app.config, code)
        except Exception as exc:
            code_name = getattr(exc, "code", "signin")
            if code_name not in ("signin", "denied"):
                code_name = "signin"
            log.info("google sign-in did not finish")
            self._redirect(f"/?error={code_name}", [clear])
            return
        if email.casefold() not in self.server.app.config.allowed_emails:
            log.info("google sign-in rejected")
            self._redirect("/?error=denied", [clear])
            return
        session = _cookie(
            "pc_session",
            issue_session(email, self.server.app.config.session_secret),
            SESSION_TTL_SECONDS,
            self.server.app.config.secure_cookies,
        )
        self._redirect("/", [clear, session])

    def _wake(self) -> None:
        origin_ok = self._same_origin()
        signed_in = self._email() is not None
        if not origin_ok or not signed_in:
            log.info(
                "wake refused origin_ok=%s signed_in=%s origin=%s",
                origin_ok,
                signed_in,
                self.headers.get("Origin"),
            )
            self._redirect("/", status=303)
            return
        self.server.app.wake.start()
        log.info("wake started")
        self._redirect("/", status=303)

    def _logout(self) -> None:
        if not self._same_origin():
            self._redirect("/", status=303)
            return
        clear = _cookie(
            "pc_session",
            "",
            0,
            self.server.app.config.secure_cookies,
            clear=True,
        )
        self._redirect("/", [clear], status=303)

    def _email(self) -> str | None:
        token = self._cookies().get("pc_session", "")
        email = read_session(token, self.server.app.config.session_secret)
        if not email or email.casefold() not in self.server.app.config.allowed_emails:
            return None
        return email

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return origin == self.server.app.config.origin

    def _cookies(self) -> dict[str, str]:
        header = self.headers.get("Cookie", "")
        found: dict[str, str] = {}
        for part in header.split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            found[name.strip()] = value.strip()
        return found

    def _discard_body(self) -> bool:
        raw = self.headers.get("Content-Length", "0") or "0"
        try:
            length = int(raw)
        except ValueError:
            return False
        if length < 0 or length > 4096:
            return False
        if length:
            self.rfile.read(length)
        return True

    def _html(self, status: int, body: str, cookies: list[str] | None = None) -> None:
        self._send(status, body.encode(), "text/html; charset=utf-8", cookies)

    def _plain(self, status: int, body: str) -> None:
        self._send(status, body.encode(), "text/plain; charset=utf-8")

    def _redirect(self, location: str, cookies: list[str] | None = None, status: int = 302) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "same-origin")
        self._security_headers()
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _send(self, status: int, body: bytes, content_type: str, cookies: list[str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Chrome sends Origin: null when this is no-referrer, and the form is ignored.
        self.send_header("Referrer-Policy", "same-origin")
        self._security_headers()
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self) -> None:
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "base-uri 'self'; frame-ancestors 'none'",
        )


def _split(path: str) -> tuple[str, dict[str, list[str]]]:
    parts = urllib.parse.urlsplit(path)
    return parts.path, urllib.parse.parse_qs(parts.query)


def _first(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return values[0] if values else ""


def _cookie(name: str, value: str, max_age: int, secure: bool, clear: bool = False) -> str:
    pieces = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
    pieces.append("Max-Age=0" if clear else f"Max-Age={max_age}")
    if secure:
        pieces.append("Secure")
    return "; ".join(pieces)


def make_server(config: Config, wake, fetch=None) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((config.bind_host, config.bind_port), Handler)
    httpd.app = App(config, wake, fetch)
    return httpd


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    httpd = make_server(config, build_wake_service(config))
    host, port = httpd.server_address[:2]
    log.info("listening on %s:%s", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        httpd.server_close()
