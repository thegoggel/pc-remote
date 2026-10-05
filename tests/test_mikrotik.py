import socket
import threading
import unittest

from app.mikrotik import _Reader, encode_length, encode_sentence, legacy_response, send_wol
from app.wake import WakeError

PASSWORD = "s3cret-password"
MAC = "AA:BB:CC:DD:EE:FF"
CHALLENGE = "00112233445566778899aabbccddeeff"


class OneByteSocket:
    def __init__(self, data):
        self._data = data

    def recv(self, size):
        if not self._data:
            return b""
        chunk = self._data[:1]
        self._data = self._data[1:]
        return chunk


class LengthTests(unittest.TestCase):
    def test_known_lengths(self):
        self.assertEqual(encode_length(0), b"\x00")
        self.assertEqual(encode_length(5), b"\x05")
        self.assertEqual(encode_length(127), b"\x7f")
        self.assertEqual(encode_length(128), b"\x80\x80")
        self.assertEqual(encode_length(16383), b"\xbf\xff")
        self.assertEqual(encode_length(16384), b"\xc0\x40\x00")

    def test_round_trip_including_split_reads(self):
        for length in (0, 1, 127, 128, 129, 16383, 16384, 0x1FFFFF, 0x200000, 0xFFFFFFF, 0x10000000):
            reader = _Reader(OneByteSocket(encode_length(length)))
            self.assertEqual(reader.read_length(), length)

    def test_sentence_survives_one_byte_reads(self):
        encoded = encode_sentence(["/tool/wol", f"=mac={MAC}"])
        self.assertEqual(encoded[:1], b"\x09")
        reader = _Reader(OneByteSocket(encoded))
        self.assertEqual(reader.read_sentence(), ["/tool/wol", f"=mac={MAC}"])

    def test_legacy_response_vector(self):
        self.assertEqual(
            legacy_response("secret", CHALLENGE),
            "00c17511d224a5e93170632807d36388aa",
        )


def _serve(handler):
    gate = threading.Event()
    received = bytearray()

    class Record:
        def __init__(self, sock):
            self._sock = sock

        def recv(self, size):
            data = self._sock.recv(size)
            received.extend(data)
            return data

        def sendall(self, data):
            self._sock.sendall(data)

        def close(self):
            self._sock.close()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def run():
        conn, _addr = listener.accept()
        conn.settimeout(3)
        wrapped = Record(conn)
        try:
            handler(wrapped)
        finally:
            wrapped.close()
            listener.close()
            gate.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port, received, thread, gate


def _sentences(conn):
    return _Reader(conn)


class RouterTests(unittest.TestCase):
    def test_modern_login_and_wol(self):
        def handler(conn):
            reader = _sentences(conn)
            self.assertEqual(reader.read_sentence(), ["/login"])
            conn.sendall(encode_sentence(["!trap", "=message=cannot log in"]))
            login = reader.read_sentence()
            self.assertIn("=name=wol", login)
            self.assertIn(f"=password={PASSWORD}", login)
            conn.sendall(encode_sentence(["!done"]))
            wol = reader.read_sentence()
            self.assertEqual(wol[0], "/tool/wol")
            self.assertIn(f"=mac={MAC}", wol)
            self.assertIn("=interface=bridge", wol)
            conn.sendall(encode_sentence(["!done"]))

        port, _received, thread, gate = _serve(handler)
        send_wol("127.0.0.1", port, "wol", PASSWORD, MAC, "bridge")
        self.assertTrue(gate.wait(3))
        thread.join(3)

    def test_legacy_login_does_not_send_the_password(self):
        expected = legacy_response(PASSWORD, CHALLENGE)

        def handler(conn):
            reader = _sentences(conn)
            self.assertEqual(reader.read_sentence(), ["/login"])
            conn.sendall(encode_sentence(["!done", f"=ret={CHALLENGE}"]))
            login = reader.read_sentence()
            self.assertIn(f"=response={expected}", login)
            self.assertNotIn(PASSWORD, "".join(login))
            conn.sendall(encode_sentence(["!done"]))
            self.assertEqual(reader.read_sentence()[0], "/tool/wol")
            conn.sendall(encode_sentence(["!done"]))

        port, received, thread, gate = _serve(handler)
        send_wol("127.0.0.1", port, "wol", PASSWORD, MAC, "bridge")
        self.assertTrue(gate.wait(3))
        thread.join(3)
        self.assertNotIn(PASSWORD.encode(), bytes(received))

    def test_bad_login_message_hides_the_password(self):
        def handler(conn):
            reader = _sentences(conn)
            reader.read_sentence()
            conn.sendall(encode_sentence(["!trap", "=message=cannot log in"]))
            reader.read_sentence()
            conn.sendall(encode_sentence(["!trap", f"=message=bad {PASSWORD}"]))

        port, _received, thread, gate = _serve(handler)
        with self.assertRaises(WakeError) as caught:
            send_wol("127.0.0.1", port, "wol", PASSWORD, MAC, "bridge")
        self.assertEqual(str(caught.exception), "The router refused the login.")
        self.assertNotIn(PASSWORD, str(caught.exception))
        gate.wait(3)
        thread.join(3)

    def test_router_unreachable(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        with self.assertRaises(WakeError) as caught:
            send_wol("127.0.0.1", port, "wol", PASSWORD, MAC, "bridge", timeout=1)
        self.assertEqual(str(caught.exception), "Could not reach the router.")


if __name__ == "__main__":
    unittest.main()
