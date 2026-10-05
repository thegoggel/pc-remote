"""Send Wake-on-LAN, wait until the PC answers, then ask it to start Steam."""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

PHASES = ("idle", "waking", "waiting", "started", "failed")


class WakeError(Exception):
    """A failure whose message is safe to show on the page."""


class NotReady(Exception):
    """The PC is not accepting the Steam request yet."""


class WakeService:
    def __init__(
        self,
        timeout: float,
        send_wol,
        pc_answers,
        start_steam,
        *,
        monotonic=time.monotonic,
        sleep=time.sleep,
        probe_interval: float = 2,
        wol_repeat: float = 30,
    ):
        self._timeout = timeout
        self._send_wol = send_wol
        self._pc_answers = pc_answers
        self._start_steam = start_steam
        self._monotonic = monotonic
        self._sleep = sleep
        self._probe_interval = probe_interval
        self._wol_repeat = wol_repeat
        self._lock = threading.Lock()
        self._phase = "idle"
        self._detail = ""
        self._running = False
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return {"phase": self._phase, "detail": self._detail}

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._phase = "waking"
            self._detail = "Sending the wake packet."
            thread = threading.Thread(target=self._run, name="wake", daemon=True)
            self._thread = thread
        thread.start()
        return True

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _set(self, phase: str, detail: str) -> None:
        with self._lock:
            self._phase = phase
            self._detail = detail
        log.info("phase %s", phase)

    def _run(self) -> None:
        try:
            self._send_wol()
            self._set("waiting", "The wake packet was sent.")
            deadline = self._monotonic() + self._timeout
            next_wol = self._monotonic() + self._wol_repeat
            while self._monotonic() < deadline:
                if self._monotonic() >= next_wol:
                    try:
                        self._send_wol()
                    except WakeError as exc:
                        log.warning("wake resend failed: %s", exc)
                    next_wol = self._monotonic() + self._wol_repeat
                try:
                    if self._pc_answers():
                        self._start_steam()
                        self._set("started", "Steam started.")
                        return
                except NotReady:
                    pass
                self._sleep(self._probe_interval)
            self._set("failed", "The PC did not answer in time.")
        except WakeError as exc:
            self._set("failed", str(exc))
        except Exception:
            log.exception("wake failed")
            self._set("failed", "Wake failed.")
        finally:
            with self._lock:
                self._running = False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _opener():
    return urllib.request.build_opener(_NoRedirect())


def probe_url(pc_address: str, port: int, scheme: str) -> str:
    host = pc_address
    if ":" in pc_address and not pc_address.startswith("["):
        host = f"[{pc_address}]"
    return f"{scheme}://{host}:{port}/"


def http_answers(url: str, timeout: float = 2, opener=None) -> bool:
    """True when the PC accepts an HTTP connection. Any status counts as an answer."""
    request = urllib.request.Request(url, method="GET")
    request.add_header("Connection", "close")
    open_url = opener or _opener().open
    try:
        with open_url(request, timeout=timeout) as response:
            response.read(128)
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def request_steam_start(url: str, secret: str, timeout: float = 10, opener=None) -> None:
    request = urllib.request.Request(url, data=b"", method="POST")
    request.add_header("Authorization", f"Bearer {secret}")
    request.add_header("Connection", "close")
    open_url = opener or _opener().open
    try:
        with open_url(request, timeout=timeout) as response:
            if response.status != 200:
                raise WakeError("The PC answered, but Steam did not start.")
            response.read(128)
    except WakeError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise WakeError("The PC rejected the Steam start request.") from None
        raise WakeError("The PC answered, but Steam did not start.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise NotReady() from None


def build_wake_service(config) -> WakeService:
    from app.mikrotik import send_wol

    parts = urllib.parse.urlsplit(config.listener_url)
    ready = probe_url(config.pc_address, config.listener_port, parts.scheme)

    def wake_router() -> None:
        send_wol(
            config.mikrotik_host,
            config.mikrotik_port,
            config.mikrotik_user,
            config.mikrotik_password,
            config.pc_mac,
            config.mikrotik_interface,
        )

    def answers() -> bool:
        return http_answers(ready)

    def steam() -> None:
        request_steam_start(config.listener_url, config.listener_secret)

    return WakeService(config.wake_timeout_seconds, wake_router, answers, steam)
