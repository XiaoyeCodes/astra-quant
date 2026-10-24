"""ASTRA bridge: serves the cockpit and streams state over WebSocket.

Run locally:  uvicorn app.main:app --port 8080   (from the bridge/ folder)
"""
from __future__ import annotations

import contextlib
import json
import logging
import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .config import settings
from .octobot_client import OctoBotError
from .state import Cockpit

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
cockpit = Cockpit()

CONFIRM_WORD = "确认"


async def state_endpoint(request: Request) -> JSONResponse:
    return JSONResponse(cockpit.snapshot())


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def focus_endpoint(request: Request) -> JSONResponse:
    body = await request.json()
    cockpit.set_focus(str(body.get("symbol", "")))
    return JSONResponse({"focus": cockpit.focus})


async def _control(request: Request, action) -> JSONResponse:
    if not settings.enable_controls:
        return JSONResponse({"ok": False, "error": "紧急控制未开启（.env 中 ENABLE_CONTROLS=false）"}, status_code=403)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    if body.get("confirm") != CONFIRM_WORD:
        return JSONResponse({"ok": False, "error": f"请输入“{CONFIRM_WORD}”以执行该操作"}, status_code=400)
    try:
        result = await action()
    except OctoBotError as err:
        return JSONResponse({"ok": False, "error": f"OctoBot 没有响应：{err}"}, status_code=502)
    return JSONResponse({"ok": True, "result": result})


async def cancel_all(request: Request) -> JSONResponse:
    return await _control(request, cockpit.cancel_all)


async def stop_bot(request: Request) -> JSONResponse:
    return await _control(request, cockpit.stop_bot)


async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    cockpit.clients.add(ws)
    try:
        await ws.send_text(json.dumps(cockpit.snapshot(), default=str))
        while True:
            msg = await ws.receive_text()
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "focus":
                cockpit.set_focus(str(data.get("symbol", "")))
    except WebSocketDisconnect:
        pass
    finally:
        cockpit.clients.discard(ws)


@contextlib.asynccontextmanager
async def lifespan(app):
    await cockpit.start()
    try:
        yield
    finally:
        await cockpit.stop()


routes = [
    Route("/api/health", health),
    Route("/api/state", state_endpoint),
    Route("/api/focus", focus_endpoint, methods=["POST"]),
    Route("/api/control/cancel-all", cancel_all, methods=["POST"]),
    Route("/api/control/stop-bot", stop_bot, methods=["POST"]),
    WebSocketRoute("/ws", ws_endpoint),
    Mount("/", app=StaticFiles(directory=os.path.abspath(settings.web_dir), html=True), name="web"),
]

app = Starlette(routes=routes, lifespan=lifespan)
