import json
import unittest
import urllib.parse

from app.config import load_config
from app.oauth import AUTH_URL, TOKEN_URL, USERINFO_URL, OAuthError, authorization_url, fetch_email
from tests.test_config import sample_env


class _Response:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()

    def read(self, size=-1):
        if size is None or size < 0:
            chunk, self._raw = self._raw, b""
            return chunk
        chunk, self._raw = self._raw[:size], self._raw[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(sample_env())

    def test_authorization_url_hides_the_client_secret(self):
        url = authorization_url(self.config, "state-value")
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parts.query)
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", AUTH_URL)
        self.assertEqual(query["client_id"], ["google-client"])
        self.assertEqual(query["redirect_uri"], ["https://wake.example.com/auth/callback"])
        self.assertEqual(query["state"], ["state-value"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertNotIn("google-secret", url)

    def test_fetch_email(self):
        seen = []

        def opener(request, timeout):
            seen.append(request.full_url)
            if request.full_url.startswith(TOKEN_URL):
                self.assertIn(b"client_secret=google-secret", request.data)
                self.assertIn(b"code=auth-code", request.data)
                return _Response({"access_token": "token-value"})
            self.assertTrue(request.full_url.startswith(USERINFO_URL))
            self.assertEqual(request.get_header("Authorization"), "Bearer token-value")
            return _Response({"email": "nils@gmail.com", "email_verified": True})

        email = fetch_email(self.config, "auth-code", opener)
        self.assertEqual(email, "nils@gmail.com")
        self.assertEqual(len(seen), 2)

    def test_unverified_email_is_denied(self):
        def opener(request, timeout):
            if request.full_url.startswith(TOKEN_URL):
                return _Response({"access_token": "token-value"})
            return _Response({"email": "nils@gmail.com", "email_verified": False})

        with self.assertRaises(OAuthError) as caught:
            fetch_email(self.config, "auth-code", opener)
        self.assertEqual(caught.exception.code, "denied")

    def test_token_failure(self):
        def opener(request, timeout):
            return _Response({"error": "invalid_grant"})

        with self.assertRaises(OAuthError) as caught:
            fetch_email(self.config, "auth-code", opener)
        self.assertEqual(caught.exception.code, "signin")
        self.assertNotIn("google-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
