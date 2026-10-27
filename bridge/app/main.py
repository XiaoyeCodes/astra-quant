"""ASTRA bridge: serves the cockpit and streams state over WebSocket.

Run locally:  uvicorn app.main:app --port 8080   (from the bridge/ folder)
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import time

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import health as healthcheck
from .config import settings
from .octobot_client import OctoBotError
from .security import LocalOnlyGuard
from .state import Cockpit

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
cockpit = Cockpit()

CONFIRM_WORD = "确认"


class CockpitFiles(StaticFiles):
    """The cockpit files change with every update: make browsers revalidate them (cheap ETag
    check) instead of running a stale script against a newer bridge."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


async def state_endpoint(request: Request) -> JSONResponse:
    return JSONResponse(cockpit.snapshot())


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def health_report(request: Request) -> JSONResponse:
    return JSONResponse(healthcheck.report(cockpit))


async def diagnostics(request: Request) -> JSONResponse:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    body = healthcheck.redact(json.dumps(healthcheck.diagnostics(cockpit), ensure_ascii=False, default=str))
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="astra-diagnostics-{stamp}.json"',
        "Cache-Control": "no-store"})


async def focus_endpoint(request: Request) -> JSONResponse:
    body = await _json_body(request)
    cockpit.set_focus(str(body.get("symbol", "")))
    return JSONResponse({"focus": cockpit.focus})


async def _control(request: Request, action) -> JSONResponse:
    if not settings.enable_controls:
        return JSONResponse({"ok": False, "error": "紧急控制未开启（.env 中 ENABLE_CONTROLS=false）"}, status_code=403)
    body = await _json_body(request)
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


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:  # bad JSON or a body that is not UTF-8
        return {}
    return body if isinstance(body, dict) else {}


async def _desk_call(coro) -> JSONResponse:
    try:
        result = await coro
    except ValueError as err:
        return JSONResponse({"ok": False, "error": str(err)}, status_code=400)
    except OctoBotError as err:
        return JSONResponse({"ok": False, "error": f"OctoBot 没有接受操作（{err}）。新加入的策略需要先重启一次 OctoBot"},
                            status_code=502)
    return JSONResponse({"ok": True, "result": result, "strategy": cockpit.desk.snapshot()})


async def strategy_select(request: Request) -> JSONResponse:
    body = await _json_body(request)
    return await _desk_call(cockpit.desk.manual_select(str(body.get("profile", ""))))


async def strategy_scheme(request: Request) -> JSONResponse:
    body = await _json_body(request)
    def value(key):
        v = body.get(key)
        return v if v not in (None, "") else None
    return await _desk_call(cockpit.desk.start_scheme(
        str(body.get("scheme", "")),
        ai_profile=body.get("aiProfile"),
        stop_pct=value("stopLoss"), trail_pct=value("trail"), daily_pct=value("dailyLoss"),
    ))


async def strategy_risk(request: Request) -> JSONResponse:
    body = await _json_body(request)

    def value(key):
        v = body.get(key)
        return v if v not in (None, "") else None

    async def apply() -> str:
        cockpit.desk.set_risk(str(body.get("scheme", "")), stop=value("stopLoss"),
                              trail=value("trail"), daily=value("dailyLoss"))
        return "风控参数已保存"
    return await _desk_call(apply())


async def strategy_stoploss(request: Request) -> JSONResponse:
    body = await _json_body(request)

    async def apply() -> str:
        cockpit.desk.set_stop_loss(str(body.get("scheme", "")), body.get("pct"))
        return "已保存"
    return await _desk_call(apply())


async def strategy_handover(request: Request) -> JSONResponse:
    body = await _json_body(request)

    async def apply() -> str:
        cockpit.desk.set_handover(str(body.get("policy", "")))
        return "已保存"
    return await _desk_call(apply())


async def strategy_halt(request: Request) -> JSONResponse:
    body = await _json_body(request)
    if body.get("confirm") != CONFIRM_WORD:
        return JSONResponse({"ok": False, "error": f"请输入“{CONFIRM_WORD}”以停止交易"}, status_code=400)
    return await _desk_call(cockpit.desk.halt())


async def report_send(request: Request) -> JSONResponse:
    sent, text = cockpit.reporter.send_now()
    return JSONResponse({"ok": True, "sent": sent, "text": text,
                         "result": "日报已发送" if sent else "没有配置通知渠道，只能预览"})


async def strategy_ai_refresh(request: Request) -> JSONResponse:
    async def apply() -> str:
        cockpit.desk.refresh_ai()
        return "AI 正在分析行情"
    return await _desk_call(apply())


async def notify_test(request: Request) -> JSONResponse:
    channels = cockpit.notifier.channels
    if not channels:
        return JSONResponse({"ok": False, "error": "没有配置通知渠道：在 .env 填写 NOTIFY_TELEGRAM_* 或 NOTIFY_WEBHOOK_URL 后重启"},
                            status_code=400)
    cockpit.notifier.send("这是一条测试通知：收到说明止损、离线等提醒能送达。", "info", key="test", quiet_s=10)
    return JSONResponse({"ok": True, "result": f"已发送到 {', '.join(channels)}，请查看手机"})


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
    Route("/api/health/report", health_report),
    Route("/api/diagnostics", diagnostics),
    Route("/api/focus", focus_endpoint, methods=["POST"]),
    Route("/api/control/cancel-all", cancel_all, methods=["POST"]),
    Route("/api/control/stop-bot", stop_bot, methods=["POST"]),
    Route("/api/strategy/select", strategy_select, methods=["POST"]),
    Route("/api/strategy/scheme", strategy_scheme, methods=["POST"]),
    Route("/api/strategy/stoploss", strategy_stoploss, methods=["POST"]),
    Route("/api/strategy/risk", strategy_risk, methods=["POST"]),
    Route("/api/strategy/ai-refresh", strategy_ai_refresh, methods=["POST"]),
    Route("/api/strategy/handover", strategy_handover, methods=["POST"]),
    Route("/api/strategy/halt", strategy_halt, methods=["POST"]),
    Route("/api/notify/test", notify_test, methods=["POST"]),
    Route("/api/report/send", report_send, methods=["POST"]),
    WebSocketRoute("/ws", ws_endpoint),
    Mount("/", app=CockpitFiles(directory=os.path.abspath(settings.web_dir), html=True), name="web"),
]

app = Starlette(routes=routes, lifespan=lifespan,
                middleware=[Middleware(LocalOnlyGuard, allowed_hosts=settings.allowed_hosts)])
