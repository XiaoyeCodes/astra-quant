"""The bridge can work with a password-protected OctoBot web interface."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from app import octobot_client as octo
from app.config import settings

LOGIN_PAGE = b"""<!doctype html><html><head><title>Login - OctoBot</title></head><body>
<form method="post"><input id="csrf_token" name="csrf_token" type="hidden" value="tok123">
<input name="password" type="password"></form></body></html>"""


class FakeOctoBot(BaseHTTPRequestHandler):
    password = "s3cret"
    logins = 0

    def log_message(self, *_):
        pass

    def _send(self, code, body=b"", headers=()):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/login":
            return self._send(200, LOGIN_PAGE, [("Content-Type", "text/html"), ("Set-Cookie", "csrf=1; Path=/")])
        if "session=ok" not in (self.headers.get("Cookie") or ""):
            return self._send(302, b"", [("Location", "/login?next=" + self.path)])
        if path == "/api/ping":
            return self._send(200, b'"Running since now."', [("Content-Type", "application/json")])
        return self._send(200, b"<html>home</html>", [("Content-Type", "text/html")])

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode())
        if urlsplit(self.path).path == "/login":
            FakeOctoBot.logins += 1
            ok = form.get("password") == [self.password] and form.get("csrf_token") == ["tok123"] \
                and "csrf=1" in (self.headers.get("Cookie") or "")
            if ok:
                return self._send(302, b"", [("Location", "/"), ("Set-Cookie", "session=ok; Path=/")])
            return self._send(200, LOGIN_PAGE, [("Content-Type", "text/html")])
        return self._send(404)


class LoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOctoBot)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        octo._cookies.clear()
        octo._last_login = 0.0
        FakeOctoBot.logins = 0
        self.url_patch = patch.object(settings, "octobot_url", self.url)
        self.url_patch.start()

    def tearDown(self):
        self.url_patch.stop()

    def test_logs_in_once_and_reuses_the_session(self):
        with patch.object(settings, "octobot_password", "s3cret"):
            self.assertEqual(octo._request("GET", "/api/ping"), "Running since now.")
            self.assertEqual(octo._request("GET", "/api/ping"), "Running since now.")
        self.assertEqual(FakeOctoBot.logins, 1)

    def test_wrong_password_is_reported(self):
        with patch.object(settings, "octobot_password", "nope"):
            with self.assertRaises(octo.OctoBotError) as ctx:
                octo._request("GET", "/api/ping")
        self.assertIn("OCTOBOT_PASSWORD", str(ctx.exception))

    def test_missing_password_explains_what_to_do(self):
        with patch.object(settings, "octobot_password", ""):
            with self.assertRaises(octo.OctoBotError) as ctx:
                octo._request("GET", "/api/ping")
        self.assertIn("OCTOBOT_PASSWORD", str(ctx.exception))
        self.assertEqual(FakeOctoBot.logins, 0)

    def test_failed_logins_are_rate_limited(self):
        with patch.object(settings, "octobot_password", "nope"):
            for _ in range(3):
                with self.assertRaises(octo.OctoBotError):
                    octo._request("GET", "/api/ping")
        self.assertEqual(FakeOctoBot.logins, 1)  # OctoBot bans addresses after repeated failures


if __name__ == "__main__":
    unittest.main()
