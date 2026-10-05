import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ListenerScriptTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "windows" / "steam-listener.ps1").read_text(encoding="utf-8")

    def test_shutdown_is_one_fixed_normal_shutdown(self):
        match = re.search(r"function Start-WindowsShutdown\s*\{(?P<body>.*?)\n\}", self.text, re.S)
        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertIn('FileName = $shutdown', body)
        self.assertIn('Arguments = "/s /t 0"', body)
        self.assertIn("UseShellExecute = $false", body)
        self.assertIn("System32", body)
        self.assertIn("shutdown.exe", body)
        self.assertNotIn("$request", body)
        self.assertNotIn("$path", body)
        self.assertNotIn("$ListenerSecret", body)
        self.assertEqual(self.text.count('"/s /t 0"'), 1)
        self.assertNotIn('"/r', self.text)
        self.assertNotIn('"/f', self.text)
        self.assertNotIn('"/h', self.text)

    def test_auth_runs_before_either_action(self):
        handle = self.text.index("function Handle-Request")
        auth = self.text.index("Test-Secret", handle)
        steam = self.text.index("Start-SteamClient", handle)
        off = self.text.index("Start-WindowsShutdown", handle)
        self.assertLess(auth, steam)
        self.assertLess(auth, off)
        self.assertIn('"/start"', self.text)
        self.assertIn('"/shutdown"', self.text)

    def test_script_does_not_offer_a_shell(self):
        lowered = self.text.casefold()
        for banned in (
            "invoke-expression",
            "iex ",
            "cmd.exe",
            "encodedcommand",
            "downloadstring",
            "frombase64string",
        ):
            self.assertNotIn(banned, lowered)


if __name__ == "__main__":
    unittest.main()
