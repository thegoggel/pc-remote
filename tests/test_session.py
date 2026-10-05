import unittest

from app.session import (
    SESSION_TTL_SECONDS,
    issue_session,
    issue_state,
    read_session,
    read_state,
)

SECRET = "s" * 32
NOW = 1_700_000_000


class SessionTests(unittest.TestCase):
    def test_round_trip(self):
        token = issue_session("nils@gmail.com", SECRET, now=NOW)
        self.assertEqual(read_session(token, SECRET, now=NOW), "nils@gmail.com")

    def test_expired_and_wrong_secret(self):
        token = issue_session("nils@gmail.com", SECRET, now=NOW)
        self.assertIsNone(read_session(token, SECRET, now=NOW + SESSION_TTL_SECONDS))
        self.assertIsNone(read_session(token, "t" * 32, now=NOW))

    def test_tamper(self):
        token = issue_session("nils@gmail.com", SECRET, now=NOW)
        encoded, signature = token.split(".")
        flipped = ("0" if signature[0] != "0" else "1") + signature[1:]
        self.assertIsNone(read_session(f"{encoded}.{flipped}", SECRET, now=NOW))

    def test_state_is_not_a_session(self):
        token = issue_state("abc", SECRET, now=NOW)
        self.assertEqual(read_state(token, SECRET, now=NOW), "abc")
        self.assertIsNone(read_session(token, SECRET, now=NOW))
        session = issue_session("nils@gmail.com", SECRET, now=NOW)
        self.assertIsNone(read_state(session, SECRET, now=NOW))

    def test_rejects_garbage(self):
        self.assertIsNone(read_session("", SECRET, now=NOW))
        self.assertIsNone(read_session("a.b", SECRET, now=NOW))
        self.assertIsNone(read_session("nope", SECRET, now=NOW))


if __name__ == "__main__":
    unittest.main()
