import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.wake import NotReady, WakeError, WakeService, http_answers, request_steam_start

SECRET = "listener-secret-value"


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def service(send_wol, answers, steam, timeout=30, interval=2, repeat=30):
    clock = Clock()
    wake = WakeService(
        timeout,
        send_wol,
        answers,
        steam,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        probe_interval=interval,
        wol_repeat=repeat,
    )
    return wake


class WakeTests(unittest.TestCase):
    def test_starts_steam_after_the_pc_answers(self):
        calls = []

        def send_wol():
            calls.append("wol")

        def answers():
            calls.append("probe")
            return True

        def steam():
            calls.append("steam")

        wake = service(send_wol, answers, steam)
        self.assertTrue(wake.start())
        wake.join(2)
        self.assertEqual(wake.snapshot(), {"phase": "started", "detail": "Steam started."})
        self.assertEqual(calls, ["wol", "probe", "steam"])

    def test_waits_until_windows_answers(self):
        probes = {"n": 0}

        def answers():
            probes["n"] += 1
            return probes["n"] >= 3

        steam = []
        wake = service(lambda: None, answers, lambda: steam.append("go"), timeout=20, interval=2, repeat=100)
        wake.start()
        wake.join(2)
        self.assertEqual(wake.snapshot()["phase"], "started")
        self.assertEqual(probes["n"], 3)
        self.assertEqual(steam, ["go"])

    def test_timeout(self):
        wol = []
        wake = service(lambda: wol.append(1), lambda: False, lambda: None, timeout=5, interval=2, repeat=30)
        wake.start()
        wake.join(2)
        snap = wake.snapshot()
        self.assertEqual(snap["phase"], "failed")
        self.assertEqual(snap["detail"], "The PC did not answer in time.")
        self.assertEqual(wol, [1])

    def test_resends_the_magic_packet_while_waiting(self):
        wol = []
        wake = service(lambda: wol.append(1), lambda: False, lambda: None, timeout=25, interval=10, repeat=15)
        wake.start()
        wake.join(2)
        self.assertEqual(len(wol), 2)
        self.assertEqual(wake.snapshot()["phase"], "failed")

    def test_router_failure_does_not_call_steam(self):
        steam = []

        def send_wol():
            raise WakeError("Could not reach the router.")

        wake = service(send_wol, lambda: True, lambda: steam.append(1))
        wake.start()
        wake.join(2)
        self.assertEqual(wake.snapshot(), {"phase": "failed", "detail": "Could not reach the router."})
        self.assertEqual(steam, [])

    def test_rejected_steam_request_fails_once(self):
        calls = []

        def steam():
            calls.append(1)
            raise WakeError("The PC rejected the Steam start request.")

        wake = service(lambda: None, lambda: True, steam)
        wake.start()
        wake.join(2)
        self.assertEqual(calls, [1])
        self.assertIn("rejected", wake.snapshot()["detail"])

    def test_not_ready_is_retried(self):
        calls = {"n": 0}

        def steam():
            calls["n"] += 1
            if calls["n"] == 1:
                raise NotReady()

        wake = service(lambda: None, lambda: True, steam, timeout=10, interval=1)
        wake.start()
        wake.join(2)
        self.assertEqual(wake.snapshot()["phase"], "started")
        self.assertEqual(calls["n"], 2)

    def test_unexpected_error_hides_the_message(self):
        def send_wol():
            raise RuntimeError("super-secret-value")

        wake = service(send_wol, lambda: True, lambda: None)
        wake.start()
        wake.join(2)
        snap = wake.snapshot()
        self.assertEqual(snap["detail"], "Wake failed.")
        self.assertNotIn("super-secret-value", snap["detail"])

    def test_second_start_while_running_is_ignored(self):
        hold = threading.Event()
        entered = threading.Event()

        def send_wol():
            entered.set()
            hold.wait(2)

        wake = WakeService(5, send_wol, lambda: True, lambda: None)
        self.assertTrue(wake.start())
        self.assertTrue(entered.wait(1))
        self.assertFalse(wake.start())
        self.assertEqual(wake.snapshot()["phase"], "waking")
        hold.set()
        wake.join(2)
        self.assertEqual(wake.snapshot()["phase"], "started")
        self.assertTrue(wake.start())
        wake.join(2)


class ListenerDouble:
    def __init__(self, status=200, path="/start", body=b"", redirect=None):
        self.status = status
        self.path = path
        self.body = body
        self.redirect = redirect
        self.requests = []
        handler = self

        class Http(BaseHTTPRequestHandler):
            def do_GET(self):
                handler.requests.append(("GET", self.path, self.headers.get("Authorization")))
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                handler.requests.append(("POST", self.path, self.headers.get("Authorization")))
                if handler.redirect:
                    self.send_response(302)
                    self.send_header("Location", handler.redirect)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload = handler.body or b'{"ok":true}'
                self.send_response(handler.status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt, *args):
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Http)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2)


class SteamRequestTests(unittest.TestCase):
    def test_probe_then_one_authenticated_post(self):
        listener = ListenerDouble()
        self.addCleanup(listener.close)
        url = f"http://127.0.0.1:{listener.port}/start"
        self.assertTrue(http_answers(f"http://127.0.0.1:{listener.port}/"))
        request_steam_start(url, SECRET, timeout=2)
        self.assertEqual(listener.requests[0][0], "GET")
        self.assertEqual(listener.requests[1][0], "POST")
        self.assertEqual(listener.requests[1][1], "/start")
        self.assertEqual(listener.requests[1][2], f"Bearer {SECRET}")

    def test_closed_port_is_not_an_answer(self):
        sock = __import__("socket").socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.assertFalse(http_answers(f"http://127.0.0.1:{port}/", timeout=0.5))

    def test_unauthorized(self):
        listener = ListenerDouble(status=401)
        self.addCleanup(listener.close)
        with self.assertRaises(WakeError) as caught:
            request_steam_start(f"http://127.0.0.1:{listener.port}/start", SECRET, timeout=2)
        self.assertIn("rejected", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_redirect_is_not_followed(self):
        listener = ListenerDouble(redirect="http://127.0.0.1:1/stolen")
        self.addCleanup(listener.close)
        with self.assertRaises(WakeError) as caught:
            request_steam_start(f"http://127.0.0.1:{listener.port}/start", SECRET, timeout=1)
        self.assertIn("did not start", str(caught.exception))
        self.assertEqual(len(listener.requests), 1)

    def test_connection_failure_is_not_ready(self):
        from app.wake import NotReady

        sock = __import__("socket").socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        with self.assertRaises(NotReady):
            request_steam_start(f"http://127.0.0.1:{port}/start", SECRET, timeout=0.5)


if __name__ == "__main__":
    unittest.main()
