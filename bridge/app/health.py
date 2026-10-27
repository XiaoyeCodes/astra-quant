"""Health checks a non-expert can act on, plus a diagnostics bundle that never contains secrets."""
from __future__ import annotations

import os
import statistics
import time

from . import llm
from .autopilot import RULE_LABELS
from .config import VERSION, settings

OK, WARN, BAD = "ok", "warn", "bad"
STALE_SYNC_S = 60          # OctoBot data older than this means the stop-loss is flying blind
STALE_TICK_S = 30
FEE_EXPECTED = 0.1         # % per side for Bitget spot (profile simulator fee)
FEE_TOLERANCE = 0.03
FEE_SAMPLE = 6           # latest trades only: older ones may predate the fee patch


def _now_ms() -> int:
    return int(time.time() * 1000)


def _check(cid: str, label: str, status: str, detail: str, fix: str = "") -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail, "fix": fix}


def trade_fee_rate(trade: dict) -> float | None:
    """Fee as % of the trade value, whichever currency OctoBot charged it in."""
    cost = trade.get("cost") or (trade.get("amount") or 0) * (trade.get("price") or 0)
    fee, sym = trade.get("fee") or 0, trade.get("symbol") or ""
    if not cost or not fee or "/" not in sym:
        return None
    base, quote = sym.split(":")[0].split("/")
    cur = trade.get("feeCurrency") or quote
    if cur == quote:
        return fee / cost * 100
    if cur == base and trade.get("price"):
        return fee * trade["price"] / cost * 100
    return None


def checks(c) -> list[dict]:
    now = _now_ms()
    out: list[dict] = []
    mode = c._mode()
    desk = c.desk
    octo_link = c.link_octobot

    # OctoBot reachable and its data fresh
    if octo_link["ok"]:
        age = (now - c.last_octo_sync) / 1000 if c.last_octo_sync else None
        if age is not None and age > STALE_SYNC_S:
            out.append(_check("octobot", "OctoBot", WARN, f"已连接，但 {age:.0f} 秒没有拿到新数据",
                              "OctoBot 可能卡住；看门狗会在 10 分钟无响应时自动重启它"))
        else:
            out.append(_check("octobot", "OctoBot", OK, f"已连接 {octo_link.get('version') or ''}".strip()))
    elif desk.switching:
        out.append(_check("octobot", "OctoBot", WARN, f"正在切换到「{desk.switching.get('label')}」，重启中"))
    else:
        lost = (now - c._octo_lost_at) / 60000 if c._octo_lost_at else None
        out.append(_check("octobot", "OctoBot", BAD,
                          f"连不上{f'（已 {lost:.0f} 分钟）' if lost else ''}：{octo_link.get('error') or '无响应'}",
                          "运行 docker compose ps 看 octobot 容器是否在运行；期间不会交易，止损也无法执行"))

    # Bitget market data
    bg = c.link_bitget
    tick_age = (now - c.ticks[-1][0]) / 1000 if c.ticks else None
    if not bg["ok"]:
        out.append(_check("bitget", "Bitget 行情", BAD, f"连不上：{bg.get('error') or '无响应'}",
                          "检查网络或 .env 里的 BITGET_PROXY（国内网络通常需要代理）"))
    elif tick_age is not None and tick_age > STALE_TICK_S:
        out.append(_check("bitget", "Bitget 行情", WARN, f"行情 {tick_age:.0f} 秒没有更新"))
    else:
        out.append(_check("bitget", "Bitget 行情", OK, f"延迟 {bg.get('latencyMs') or '—'} ms"))

    # Trading mode: this project is meant for paper trading
    if mode == "live":
        out.append(_check("mode", "交易模式", BAD, "OctoBot 正在用实盘交易",
                          "本项目的止损和策略只在模拟盘验证过；如非刻意，请在 OctoBot 里切回模拟盘"))
    elif mode == "paper":
        out.append(_check("mode", "交易模式", OK, "模拟盘"))
    else:
        out.append(_check("mode", "交易模式", WARN, {"idle": "OctoBot 没有在交易", "watch": "只读账户",
                                                     "demo": "演示数据"}.get(mode, mode)))

    # Risk: at least one stop line, and an equity reading to measure it against
    lines = desk.risk_lines()
    stopped = desk.cfg.get("stopped")
    on_idle = c.profile.get("id") == "astra_idle"
    if stopped and on_idle:
        out.append(_check("risk", "止损", WARN, f"已因「{stopped.get('ruleLabel') or '最大止损'}」停止，当前观望",
                          "确认原因后，在设置里重新启动一个方案"))
    elif on_idle:
        out.append(_check("risk", "止损", OK, "观望中，没有持仓风险"))
    elif not any(v is not None for v in lines.values()):
        out.append(_check("risk", "止损", WARN, "当前方案没有设置任何止损线",
                          "在设置 → 交易方案里填最大止损或回撤止损"))
    elif mode == "paper" and c.oct_equity_live is None:
        out.append(_check("risk", "止损", BAD, "读不到 OctoBot 的实时资产，止损无法判断",
                          "通常是 OctoBot 刚重启；持续超过几分钟请看 OctoBot 日志"))
    else:
        rule, line = desk.binding_rule()
        eq = c.oct_equity_live or c.oct_equity
        room = f"，距离 {(eq - line) / eq * 100:.2f}%" if eq and line else ""
        out.append(_check("risk", "止损", OK, f"最先触发：{RULE_LABELS[rule]} {line:.2f}{room}"))

    # Paper fees should match the profile (0.1%), not ccxt's 0.2% default
    rates = [r for r in (trade_fee_rate(t) for t in c.oct_trades[-FEE_SAMPLE:]) if r is not None]
    if mode == "paper" and rates:
        med = statistics.median(rates)
        if abs(med - FEE_EXPECTED) > FEE_TOLERANCE:
            out.append(_check("fees", "模拟手续费", WARN, f"最近成交手续费约 {med:.3f}%（预期 {FEE_EXPECTED}%）",
                              "手续费补丁可能没生效：重启 octobot 容器，看日志里有没有 “applied paper fee patch”"))
        else:
            out.append(_check("fees", "模拟手续费", OK, f"最近 {len(rates)} 笔成交约 {med:.3f}%"))

    # AI is only needed by the AI schemes
    if desk.cfg["scheme"] in ("ai_trade", "ai_regime"):
        if not llm.available():
            out.append(_check("ai", "AI 分析", BAD, "当前方案需要 AI，但没有配置 LLM_API_KEY", "在 .env 里填 LLM_API_KEY 后重启桥接服务"))
        elif desk.ai_error:
            out.append(_check("ai", "AI 分析", WARN, f"最近一次失败：{desk.ai_error}"))
        else:
            out.append(_check("ai", "AI 分析", OK, f"{settings.llm_model} 正常"))

    # Notifications: a stop that nobody hears about is a weak stop
    n = c.notifier.status()
    if not n["channels"]:
        out.append(_check("notify", "通知", WARN, "没有配置通知渠道，止损和离线时不会提醒你",
                          "在 .env 里配置 Telegram、企业微信、飞书、钉钉或 Server 酱中的一个"))
    elif n.get("lastError"):
        out.append(_check("notify", "通知", WARN, f"最近一次发送失败：{n['lastError']}"))
    else:
        out.append(_check("notify", "通知", OK, "、".join(n["channels"])))

    # The bridge must be able to write its state (stop-loss anchors, audit log)
    if os.access(settings.data_dir, os.W_OK):
        out.append(_check("storage", "数据目录", OK, "可写"))
    else:
        out.append(_check("storage", "数据目录", BAD, f"{settings.data_dir} 不可写，止损起点和日志无法保存",
                          "检查 docker-compose.yml 里 data 目录的挂载"))
    return out


def overall(items: list[dict]) -> str:
    statuses = {i["status"] for i in items}
    return BAD if BAD in statuses else WARN if WARN in statuses else OK


def report(c) -> dict:
    items = checks(c)
    return {"ts": _now_ms(), "status": overall(items), "checks": items}


def _secrets() -> list[str]:
    values = [settings.bitget_key, settings.bitget_secret, settings.bitget_passphrase, settings.llm_api_key,
              settings.octobot_password, settings.notify_telegram_token, settings.notify_webhook_url,
              settings.bitget_proxy, settings.llm_proxy, settings.notify_proxy]
    return sorted({v for v in values if v and len(v) >= 6}, key=len, reverse=True)


def redact(text: str) -> str:
    """Belt and braces: no configured secret survives in an exported text, whatever field carried it."""
    for value in _secrets():
        text = text.replace(value, "***")
    return text


def diagnostics(c) -> dict:
    """Everything needed to debug remotely. Secrets appear only as configured yes/no."""
    snap = c.snapshot()
    configured = {
        "bitgetApiKey": settings.bitget_private,
        "bitgetProxy": bool(settings.bitget_proxy),
        "llmApiKey": bool(settings.llm_api_key),
        "octobotPassword": bool(settings.octobot_password),
        "notifyTelegram": bool(settings.notify_telegram_token and settings.notify_telegram_chat),
        "notifyWebhook": bool(settings.notify_webhook_url),
    }
    return {
        "generated": _now_ms(),
        "version": VERSION,
        "health": report(c),
        "settings": {
            "configured": configured,
            "llmModel": settings.llm_model,
            "llmBaseUrl": settings.llm_base_url,
            "notifyWebhookType": settings.notify_webhook_type,
            "watchSymbols": settings.watch_symbols,
            "regimeSymbol": settings.regime_symbol,
            "enableControls": settings.enable_controls,
            "autoConfirm": settings.auto_confirm,
            "autoMinHoldHours": settings.auto_min_hold_hours,
        },
        "mode": snap["mode"],
        "links": snap["links"],
        "profile": snap["profile"],
        "strategy": {k: v for k, v in snap["strategy"].items() if k not in ("list", "backtest")},
        "equity": {"value": snap["equity"]["value"], "start": snap["equity"]["start"],
                   "history": list(c.equity_hist)[-240:]},
        "stats": snap["stats"],
        "orders": snap["orders"],
        "trades": snap["trades"][-50:],
        "positions": snap["positions"],
        "log": c.audit.recent(500),
    }
