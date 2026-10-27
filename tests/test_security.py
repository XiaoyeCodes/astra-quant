"""The cockpit must refuse cross-site and DNS-rebinding requests (no login on localhost)."""
from pathlib import Path
import sys
import unittest

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from app import main
from app.security import LocalOnlyGuard, _host_name

HOSTS = {"localhost", "127.0.0.1", "::1"}
JSON = {"Content-Type": "application/json"}


async def ok(request):
    return JSONResponse({"ok": True, "method": request.method})


async def ws(websocket):
    await websocket.accept()
    await websocket.send_text("hello")
    await websocket.close()


def client(host="localhost:8080"):
    app = Starlette(routes=[Route("/api/x", ok, methods=["GET", "POST"]), WebSocketRoute("/ws", ws)],
                    middleware=[Middleware(LocalOnlyGuard, allowed_hosts=HOSTS)])
    return TestClient(app, base_url=f"http://{host}")


class GuardTests(unittest.TestCase):
    def test_host_parsing(self):
        self.assertEqual(_host_name("localhost:8080"), "localhost")
        self.assertEqual(_host_name("[::1]:8080"), "::1")
        self.assertEqual(_host_name("127.0.0.1"), "127.0.0.1")
        self.assertEqual(_host_name("EVIL.example:80"), "evil.example")

    def test_same_origin_post_is_allowed(self):
        r = client().post("/api/x", json={}, headers={"Origin": "http://localhost:8080"})
        self.assertEqual(r.status_code, 200)
        r = client("127.0.0.1:8080").post("/api/x", json={}, headers={"Origin": "http://127.0.0.1:8080"})
        self.assertEqual(r.status_code, 200)

    def test_non_browser_post_without_origin_is_allowed(self):
        self.assertEqual(client().post("/api/x", json={}).status_code, 200)

    def test_cross_site_post_is_refused(self):
        for origin in ("https://evil.example", "http://localhost.evil.example", "null"):
            with self.subTest(origin=origin):
                r = client().post("/api/x", json={}, headers={"Origin": origin})
                self.assertEqual(r.status_code, 403)

    def test_simple_cross_site_form_post_is_refused(self):
        # a text/plain body needs no CORS preflight; it must never reach the endpoint
        r = client().post("/api/x", content=b'{"profile":"astra_idle"}', headers={"Content-Type": "text/plain"})
        self.assertEqual(r.status_code, 403)

    def test_dns_rebinding_host_is_refused_even_for_reads(self):
        r = client("evil.example:8080").get("/api/x")
        self.assertEqual(r.status_code, 403)

    def test_get_from_cockpit_host_is_allowed(self):
        self.assertEqual(client().get("/api/x").status_code, 200)

    def test_websocket_origin(self):
        with client().websocket_connect("ws://localhost:8080/ws", headers={"Origin": "http://localhost:8080"}) as s:
            self.assertEqual(s.receive_text(), "hello")
        with self.assertRaises(WebSocketDisconnect):
            with client().websocket_connect("ws://localhost:8080/ws", headers={"Origin": "https://evil.example"}) as s:
                s.receive_text()

    def test_real_app_is_guarded(self):
        guards = [m for m in main.app.user_middleware if m.cls is LocalOnlyGuard]
        self.assertEqual(len(guards), 1)
        c = TestClient(main.app, base_url="http://localhost:8080")  # no lifespan: loops not started
        r = c.post("/api/strategy/ai-refresh", content=b"{}", headers={"Content-Type": "text/plain",
                                                                         "Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(c.get("/api/health").status_code, 200)

    def test_cockpit_files_are_revalidated(self):
        c = TestClient(main.app, base_url="http://localhost:8080")
        r = c.get("/js/simple.js")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-cache")   # a stale script never runs after an update

    def test_body_that_is_not_utf8_is_a_400_not_a_crash(self):
        c = TestClient(main.app, base_url="http://localhost:8080")
        r = c.post("/api/strategy/halt", content='{"confirm":"确认"}'.encode("gbk"),
                   headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
