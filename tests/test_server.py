import subprocess
import sys
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

from app.config import Config
from app.server import make_server
from app.session import issue_session
from app.wake import WakeService

ROOT = Path(__file__).resolve().parents[1]
SECRET = "s" * 32
ORIGIN = "http://127.0.0.1"


def test_config():
    return Config(
        public_url=ORIGIN,
        google_client_id="test-client",
        google_client_secret="test-secret",
        allowed_emails=frozenset({"nils@gmail.com"}),
        session_secret=SECRET,
        mikrotik_host="127.0.0.1",
        mikrotik_port=8728,
        mikrotik_user="wol",
        mikrotik_password="router-secret",
        mikrotik_interface="bridge",
        pc_mac="AA:BB:CC:DD:EE:FF",
        pc_address="192.168.88.50",
        listener_url="http://192.168.88.50:8765/start",
        listener_secret="listener-secret-value",
        bind_host="127.0.0.1",
        bind_port=0,
        wake_timeout_seconds=30,
    )


class FakeWake:
    def __init__(self):
        self.phase = "idle"
        self.detail = ""
        self.started = 0

    def snapshot(self):
        return {"phase": self.phase, "detail": self.detail}

    def start(self):
        self.started += 1
        self.phase = "waking"
        self.detail = "Sending the wake packet."
        return True

    def join(self, timeout=None):
        return


def _headers(response):
    found = {}
    for key, value in response.getheaders():
        found.setdefault(key.lower(), []).append(value)
    return found


def _request(port, method, path, cookies="", origin=None, retries=20):
    import http.client

    last = None
    for _ in range(retries):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            headers = {}
            if cookies:
                headers["Cookie"] = cookies
            if origin is not None:
                headers["Origin"] = origin
            body = b"" if method == "POST" else None
            if method == "POST":
                headers["Content-Length"] = "0"
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read()
            headers = _headers(response)
            status = response.status
            conn.close()
            return status, headers, payload
        except (ConnectionRefusedError, ConnectionResetError) as exc:
            last = exc
            time.sleep(0.05)
    raise last


def _cookie_pairs(headers):
    return [value.split(";", 1)[0] for value in headers.get("set-cookie", [])]


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.wake = FakeWake()
        self.httpd = make_server(test_config(), self.wake)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2)

    def test_signed_out_visitor_sees_only_the_login(self):
        status, headers, body = _request(self.port, "GET", "/")
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertIn("Sign in with Google", text)
        self.assertNotIn("/wake", text)
        self.assertNotIn("Steam", text)
        self.assertNotIn("Waiting for Windows", text)
        self.assertNotIn("router-secret", text)
        self.assertIn("no-store", " ".join(headers.get("cache-control", [])))

    def test_other_pages_are_not_the_app(self):
        status, _headers, body = _request(self.port, "GET", "/api/status")
        self.assertEqual(status, 404)
        self.assertNotIn(b"Steam", body)

    def test_post_without_a_session_does_not_wake(self):
        status, headers, _body = _request(self.port, "POST", "/wake", origin=ORIGIN)
        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], ["/"])
        self.assertEqual(self.wake.started, 0)

    def test_null_origin_does_not_wake(self):
        cookie = f"pc_session={issue_session('nils@gmail.com', SECRET)}"
        status, _headers, _body = _request(self.port, "POST", "/wake", cookies=cookie, origin="null")
        self.assertEqual(status, 303)
        self.assertEqual(self.wake.started, 0)

    def test_wrong_origin_does_not_wake(self):
        cookie = f"pc_session={issue_session('nils@gmail.com', SECRET)}"
        status, _headers, _body = _request(
            self.port, "POST", "/wake", cookies=cookie, origin="https://evil.example"
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.wake.started, 0)

    def test_allowed_account_can_wake(self):
        cookie = f"pc_session={issue_session('Nils@gmail.com', SECRET)}"
        status, _headers, body = _request(self.port, "GET", "/", cookies=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"Wake the PC", body)
        self.assertIn(b'action="/wake"', body)
        self.assertNotIn(b"Sign in with Google", body)
        status, headers, _body = _request(self.port, "POST", "/wake", cookies=cookie, origin=ORIGIN)
        self.assertEqual(status, 303)
        self.assertEqual(self.wake.started, 1)
        status, _headers, body = _request(self.port, "GET", "/", cookies=cookie)
        self.assertIn(b"Waking", body)
        self.assertIn(b'http-equiv="refresh"', body)

    def test_other_google_account_sees_only_the_login(self):
        cookie = f"pc_session={issue_session('other@gmail.com', SECRET)}"
        status, _headers, body = _request(self.port, "GET", "/", cookies=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"Sign in with Google", body)
        self.assertNotIn(b'action="/wake"', body)

    def test_tampered_cookie_sees_only_the_login(self):
        token = issue_session("nils@gmail.com", SECRET)
        token = token[:-1] + ("a" if token[-1] != "a" else "b")
        _status, _headers, body = _request(self.port, "GET", "/", cookies=f"pc_session={token}")
        self.assertIn(b"Sign in with Google", body)
        self.assertNotIn(b'action="/wake"', body)

    def test_google_redirect_uses_the_public_callback(self):
        status, headers, _body = _request(self.port, "GET", "/auth/google")
        self.assertEqual(status, 302)
        location = headers["location"][0]
        self.assertTrue(location.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
        self.assertEqual(query["redirect_uri"], [f"{ORIGIN}/auth/callback"])
        self.assertEqual(query["client_id"], ["test-client"])
        self.assertNotIn("test-secret", location)
        cookie = headers["set-cookie"][0]
        self.assertIn("pc_oauth_state=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn("Secure", cookie)

    def test_callback_sets_a_session_for_the_allowed_account(self):
        def fetch(_config, code):
            self.assertEqual(code, "auth-code")
            return "Nils@gmail.com"

        self.httpd.app.fetch_email = fetch
        _status, headers, _body = _request(self.port, "GET", "/auth/google")
        location = headers["location"][0]
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)["state"][0]
        cookies = "; ".join(_cookie_pairs(headers))
        status, headers, _body = _request(
            self.port,
            "GET",
            "/auth/callback?code=auth-code&state=" + urllib.parse.quote(state),
            cookies=cookies,
        )
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], ["/"])
        pairs = _cookie_pairs(headers)
        session = next(pair for pair in pairs if pair.startswith("pc_session="))
        _status, _headers, body = _request(self.port, "GET", "/", cookies=session)
        self.assertIn(b"Wake the PC", body)
        self.assertIn(b"Nils@gmail.com", body)

    def test_callback_rejects_a_different_account(self):
        self.httpd.app.fetch_email = lambda _config, _code: "other@gmail.com"
        _status, headers, _body = _request(self.port, "GET", "/auth/google")
        location = headers["location"][0]
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)["state"][0]
        cookies = "; ".join(_cookie_pairs(headers))
        status, headers, _body = _request(
            self.port,
            "GET",
            f"/auth/callback?code=auth-code&state={urllib.parse.quote(state)}",
            cookies=cookies,
        )
        self.assertEqual(headers["location"], ["/?error=denied"])
        self.assertFalse(any(pair.startswith("pc_session=") for pair in _cookie_pairs(headers)))
        _status, _headers, body = _request(self.port, "GET", "/?error=denied")
        self.assertIn(b"This Google account cannot use this page.", body)
        self.assertNotIn(b'action="/wake"', body)
        self.assertNotIn(b"other@gmail.com", body)

    def test_callback_state_mismatch(self):
        self.httpd.app.fetch_email = lambda _config, _code: "nils@gmail.com"
        _status, headers, _body = _request(self.port, "GET", "/auth/google")
        cookies = "; ".join(_cookie_pairs(headers))
        _status, headers, _body = _request(
            self.port,
            "GET",
            "/auth/callback?code=auth-code&state=nope",
            cookies=cookies,
        )
        self.assertEqual(headers["location"], ["/?error=signin"])
        self.assertFalse(any(pair.startswith("pc_session=") for pair in _cookie_pairs(headers)))

    def test_logout_clears_the_session(self):
        cookie = f"pc_session={issue_session('nils@gmail.com', SECRET)}"
        status, headers, _body = _request(self.port, "POST", "/logout", cookies=cookie, origin=ORIGIN)
        self.assertEqual(status, 303)
        cleared = " ".join(headers.get("set-cookie", []))
        self.assertIn("pc_session=", cleared)
        self.assertIn("Max-Age=0", cleared)
        _status, _headers, body = _request(self.port, "GET", "/")
        self.assertIn(b"Sign in with Google", body)

    def test_page_states(self):
        cookie = f"pc_session={issue_session('nils@gmail.com', SECRET)}"
        for phase, heading in (
            ("waiting", b"Waiting for Windows"),
            ("started", b"Steam started"),
            ("failed", b"Failed"),
        ):
            self.wake.phase = phase
            self.wake.detail = "The PC did not answer in time." if phase == "failed" else ""
            _status, _headers, body = _request(self.port, "GET", "/", cookies=cookie)
            self.assertIn(heading, body)
            if phase == "failed":
                self.assertIn(b"The PC did not answer in time.", body)
                self.assertNotIn(b'http-equiv="refresh"', body)
            if phase == "waiting":
                self.assertIn(b'http-equiv="refresh"', body)


class WakeThroughServerTests(unittest.TestCase):
    def test_button_runs_the_wake_sequence(self):
        calls = []
        wake = WakeService(
            5,
            lambda: calls.append("wol"),
            lambda: True,
            lambda: calls.append("steam"),
            probe_interval=0.01,
            wol_repeat=100,
        )
        httpd = make_server(test_config(), wake)
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def stop():
            httpd.shutdown()
            httpd.server_close()
            thread.join(2)

        self.addCleanup(stop)
        port = httpd.server_address[1]
        cookie = f"pc_session={issue_session('nils@gmail.com', SECRET)}"
        status, _headers, _body = _request(port, "POST", "/wake", cookies=cookie, origin=ORIGIN)
        self.assertEqual(status, 303)
        wake.join(2)
        _status, _headers, body = _request(port, "GET", "/", cookies=cookie)
        self.assertIn(b"Steam started", body)
        self.assertEqual(calls, ["wol", "steam"])
        self.assertNotIn(b"listener-secret-value", body)
        self.assertNotIn(b"router-secret", body)


class StartupTests(unittest.TestCase):
    def test_process_refuses_to_start_without_configuration(self):
        proc = subprocess.run(
            [sys.executable, "-m", "app"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("PUBLIC_URL", proc.stderr)


if __name__ == "__main__":
    unittest.main()
