"""Minimal MikroTik RouterOS API client, used only to send one Wake-on-LAN packet.

The login follows the current API (RouterOS 6.43 and newer) and the older
challenge login when the router still answers /login with a challenge.
"""

from __future__ import annotations

import hashlib
import logging
import socket
from dataclasses import dataclass

from app.wake import WakeError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Sentence:
    kind: str
    attrs: dict[str, str]


def encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    if length < 0x4000:
        return (length | 0x8000).to_bytes(2, "big")
    if length < 0x200000:
        return (length | 0xC00000).to_bytes(3, "big")
    if length < 0x10000000:
        return (length | 0xE0000000).to_bytes(4, "big")
    if length <= 0xFFFFFFFF:
        return bytes([0xF0]) + length.to_bytes(4, "big")
    raise ValueError("word too long")


def encode_sentence(words: list[str]) -> bytes:
    out = bytearray()
    for word in words:
        raw = word.encode("utf-8")
        out += encode_length(len(raw))
        out += raw
    out += b"\x00"
    return bytes(out)


def legacy_response(password: str, challenge_hex: str) -> str:
    """Pre-6.43 login response: 00 + hex MD5 of NUL || password || challenge."""
    try:
        challenge = bytes.fromhex(challenge_hex)
    except ValueError:
        raise WakeError("The router refused the login.") from None
    digest = hashlib.md5(b"\x00" + password.encode("utf-8") + challenge).hexdigest()
    return "00" + digest


class _Reader:
    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._buf = bytearray()

    def read(self, size: int) -> bytes:
        while len(self._buf) < size:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("MikroTik API connection closed")
            self._buf.extend(chunk)
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    def read_length(self) -> int:
        first = self.read(1)[0]
        if first < 0x80:
            return first
        if first < 0xC0:
            second = self.read(1)[0]
            return ((first & 0x3F) << 8) | second
        if first < 0xE0:
            rest = self.read(2)
            return ((first & 0x1F) << 16) | (rest[0] << 8) | rest[1]
        if first < 0xF0:
            rest = self.read(3)
            return ((first & 0x0F) << 24) | (rest[0] << 16) | (rest[1] << 8) | rest[2]
        if first == 0xF0:
            return int.from_bytes(self.read(4), "big")
        raise WakeError("Unexpected router reply.")

    def read_sentence(self) -> list[str]:
        words: list[str] = []
        while True:
            length = self.read_length()
            if length == 0:
                return words
            words.append(self.read(length).decode("utf-8", "replace"))


class RouterOSClient:
    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._reader = _Reader(sock)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def command(self, words: list[str]) -> Sentence:
        self._sock.sendall(encode_sentence(words))
        while True:
            sentence = self._reader.read_sentence()
            if not sentence:
                continue
            kind = sentence[0]
            attrs: dict[str, str] = {}
            for word in sentence[1:]:
                if word.startswith("="):
                    key, _, value = word[1:].partition("=")
                    attrs[key] = value
            if kind == "!re":
                continue
            if kind in ("!done", "!trap", "!fatal"):
                return Sentence(kind, attrs)
            raise WakeError("Unexpected router reply.")


def send_wol(
    host: str,
    port: int,
    username: str,
    password: str,
    mac: str,
    interface: str,
    timeout: float = 8,
) -> None:
    try:
        client = _connect(host, port, timeout)
    except OSError:
        raise WakeError("Could not reach the router.") from None
    try:
        client = _login(client, host, port, timeout, username, password)
        reply = client.command(
            ["/tool/wol", f"=interface={interface}", f"=mac={mac}"]
        )
        if reply.kind != "!done":
            _log_router(reply, password)
            raise WakeError("The router refused the wake request.")
    finally:
        client.close()


def _connect(host: str, port: int, timeout: float) -> RouterOSClient:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    return RouterOSClient(sock)


def _login(
    client: RouterOSClient,
    host: str,
    port: int,
    timeout: float,
    username: str,
    password: str,
) -> RouterOSClient:
    probe: Sentence | None
    try:
        probe = client.command(["/login"])
    except (ConnectionError, OSError):
        client.close()
        try:
            client = _connect(host, port, timeout)
        except OSError:
            raise WakeError("Could not reach the router.") from None
        probe = None

    if probe and probe.kind == "!done" and "ret" in probe.attrs:
        response = legacy_response(password, probe.attrs["ret"])
        result = client.command(
            ["/login", f"=name={username}", f"=response={response}"]
        )
        if result.kind != "!done":
            _log_router(result, password)
            raise WakeError("The router refused the login.")
        return client

    if probe and probe.kind == "!done":
        raise WakeError("The router refused the login.")

    try:
        result = client.command(
            ["/login", f"=name={username}", f"=password={password}"]
        )
    except (ConnectionError, OSError):
        client.close()
        try:
            client = _connect(host, port, timeout)
        except OSError:
            raise WakeError("Could not reach the router.") from None
        try:
            result = client.command(
                ["/login", f"=name={username}", f"=password={password}"]
            )
        except (ConnectionError, OSError):
            raise WakeError("Could not reach the router.") from None

    if result.kind != "!done":
        _log_router(result, password)
        raise WakeError("The router refused the login.")
    return client


def _log_router(reply: Sentence, password: str) -> None:
    message = reply.attrs.get("message", "")
    if password and password.casefold() in message.casefold():
        message = "router error"
    log.warning("router %s: %s", reply.kind, (message or "no message")[:200])
