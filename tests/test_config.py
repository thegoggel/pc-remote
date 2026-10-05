import pathlib
import unittest

from app.config import load_config

ROOT = pathlib.Path(__file__).resolve().parents[1]


def sample_env(**overrides):
    env = {
        "PUBLIC_URL": "https://wake.example.com",
        "GOOGLE_CLIENT_ID": "google-client",
        "GOOGLE_CLIENT_SECRET": "google-secret",
        "ALLOWED_EMAIL": "Nils@gmail.com, other@gmail.com",
        "SESSION_SECRET": "s" * 32,
        "MIKROTIK_HOST": "192.168.88.1",
        "MIKROTIK_USER": "wol",
        "MIKROTIK_PASSWORD": "router-secret",
        "MIKROTIK_INTERFACE": "bridge",
        "PC_MAC": "aa:bb:cc:dd:ee:ff",
        "PC_ADDRESS": "192.168.88.50",
        "LISTENER_URL": "http://192.168.88.50:8765/start",
        "LISTENER_SECRET": "listener-secret-value",
    }
    env.update(overrides)
    return env


class ConfigTests(unittest.TestCase):
    def test_loads_and_normalizes(self):
        config = load_config(sample_env())
        self.assertEqual(config.public_url, "https://wake.example.com")
        self.assertEqual(config.redirect_uri, "https://wake.example.com/auth/callback")
        self.assertEqual(config.origin, "https://wake.example.com")
        self.assertTrue(config.secure_cookies)
        self.assertEqual(config.allowed_emails, frozenset({"nils@gmail.com", "other@gmail.com"}))
        self.assertEqual(config.pc_mac, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(config.listener_port, 8765)
        self.assertEqual(config.bind_host, "127.0.0.1")
        self.assertEqual(config.bind_port, 8080)
        self.assertEqual(config.mikrotik_port, 8728)
        self.assertEqual(config.mikrotik_password, "router-secret")

    def test_localhost_http_is_allowed(self):
        config = load_config(sample_env(PUBLIC_URL="http://127.0.0.1:8080"))
        self.assertFalse(config.secure_cookies)
        self.assertEqual(config.origin, "http://127.0.0.1:8080")

    def test_missing_names_are_reported_without_values(self):
        env = sample_env()
        del env["PUBLIC_URL"]
        del env["LISTENER_SECRET"]
        with self.assertRaises(SystemExit) as caught:
            load_config(env)
        message = str(caught.exception)
        self.assertIn("PUBLIC_URL", message)
        self.assertIn("LISTENER_SECRET", message)
        self.assertNotIn("router-secret", message)
        self.assertNotIn("google-secret", message)

    def test_rejects_insecure_public_url(self):
        with self.assertRaises(SystemExit):
            load_config(sample_env(PUBLIC_URL="http://wake.example.com"))

    def test_rejects_short_secrets_and_bad_mac(self):
        with self.assertRaises(SystemExit):
            load_config(sample_env(SESSION_SECRET="short"))
        with self.assertRaises(SystemExit):
            load_config(sample_env(LISTENER_SECRET="short"))
        with self.assertRaises(SystemExit):
            load_config(sample_env(PC_MAC="not-a-mac"))

    def test_rejects_secret_in_listener_url(self):
        with self.assertRaises(SystemExit):
            load_config(sample_env(LISTENER_URL="http://user:secret@192.168.88.50:8765/start"))

    def test_example_env_leaves_secrets_empty(self):
        text = (ROOT / ".env.example").read_text()
        values = {}
        for line in text.splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value
        for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "MIKROTIK_PASSWORD", "LISTENER_SECRET"):
            self.assertEqual(values[key], "")


if __name__ == "__main__":
    unittest.main()
