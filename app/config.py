"""Runtime configuration. Values come from the environment, never from the repo."""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass

_MAC = re.compile(r"([0-9A-F]{2}:){5}[0-9A-F]{2}")
_INTERFACE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

_REQUIRED = (
    "PUBLIC_URL",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "ALLOWED_EMAIL",
    "SESSION_SECRET",
    "MIKROTIK_HOST",
    "MIKROTIK_USER",
    "MIKROTIK_PASSWORD",
    "MIKROTIK_INTERFACE",
    "PC_MAC",
    "PC_ADDRESS",
    "LISTENER_URL",
    "LISTENER_SECRET",
)


@dataclass(frozen=True)
class Config:
    public_url: str
    google_client_id: str
    google_client_secret: str
    allowed_emails: frozenset[str]
    session_secret: str
    mikrotik_host: str
    mikrotik_port: int
    mikrotik_user: str
    mikrotik_password: str
    mikrotik_interface: str
    pc_mac: str
    pc_address: str
    listener_url: str
    listener_secret: str
    bind_host: str
    bind_port: int
    wake_timeout_seconds: int

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url}/auth/callback"

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def origin(self) -> str:
        parts = urllib.parse.urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def listener_port(self) -> int:
        parts = urllib.parse.urlsplit(self.listener_url)
        if parts.port:
            return parts.port
        return 443 if parts.scheme == "https" else 80


def load_config(environ: dict[str, str] | None = None) -> Config:
    env = os.environ if environ is None else environ
    missing = [name for name in _REQUIRED if not env.get(name, "").strip()]
    if missing:
        raise SystemExit("Missing environment variables: " + ", ".join(missing))

    public_url = env["PUBLIC_URL"].strip().rstrip("/")
    parts = urllib.parse.urlsplit(public_url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise SystemExit("PUBLIC_URL must be an http(s) URL with a host")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise SystemExit("PUBLIC_URL must not include a path, query, or fragment")
    if parts.scheme != "https" and parts.hostname not in _LOCAL_HOSTS:
        raise SystemExit("PUBLIC_URL must be https (http is only for localhost)")

    allowed = frozenset(
        item.strip().casefold()
        for item in env["ALLOWED_EMAIL"].split(",")
        if item.strip()
    )
    if not allowed or any("@" not in email or email.startswith("@") for email in allowed):
        raise SystemExit("ALLOWED_EMAIL must be one or more email addresses")

    session_secret = env["SESSION_SECRET"].strip()
    if len(session_secret) < 32:
        raise SystemExit("SESSION_SECRET must be at least 32 characters")

    listener_secret = env["LISTENER_SECRET"].strip()
    if len(listener_secret) < 16:
        raise SystemExit("LISTENER_SECRET must be at least 16 characters")

    mac = env["PC_MAC"].strip().upper()
    if not _MAC.fullmatch(mac):
        raise SystemExit("PC_MAC must look like AA:BB:CC:DD:EE:FF")

    interface = env["MIKROTIK_INTERFACE"].strip()
    if not _INTERFACE.fullmatch(interface):
        raise SystemExit("MIKROTIK_INTERFACE must be an interface name, such as bridge")

    pc_address = env["PC_ADDRESS"].strip()
    if not pc_address or any(char in pc_address for char in " /\\:"):
        raise SystemExit("PC_ADDRESS must be the PC's LAN hostname or IPv4 address, without a port")

    listener_url = env["LISTENER_URL"].strip()
    listener = urllib.parse.urlsplit(listener_url)
    if listener.scheme not in ("http", "https") or not listener.hostname:
        raise SystemExit("LISTENER_URL must be an http(s) URL")
    if listener.username or listener.password:
        raise SystemExit("LISTENER_URL must not include a username or password")

    return Config(
        public_url=public_url,
        google_client_id=env["GOOGLE_CLIENT_ID"].strip(),
        google_client_secret=env["GOOGLE_CLIENT_SECRET"].strip(),
        allowed_emails=allowed,
        session_secret=session_secret,
        mikrotik_host=env["MIKROTIK_HOST"].strip(),
        mikrotik_port=_int_env(env, "MIKROTIK_PORT", 8728, 1, 65535),
        mikrotik_user=env["MIKROTIK_USER"].strip(),
        mikrotik_password=env["MIKROTIK_PASSWORD"],
        mikrotik_interface=interface,
        pc_mac=mac,
        pc_address=pc_address,
        listener_url=listener_url,
        listener_secret=listener_secret,
        bind_host=env.get("BIND_HOST", "127.0.0.1").strip() or "127.0.0.1",
        bind_port=_int_env(env, "BIND_PORT", 8080, 1, 65535),
        wake_timeout_seconds=_int_env(env, "WAKE_TIMEOUT_SECONDS", 180, 15, 900),
    )


def _int_env(env: dict[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be an integer") from None
    if value < low or value > high:
        raise SystemExit(f"{name} must be between {low} and {high}")
    return value
