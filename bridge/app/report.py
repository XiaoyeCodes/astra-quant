"""Daily report: one short message a day with the money, the comparison with holding BTC,
what the bot did and anything that needs attention. Sent once per Beijing day."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta

from . import health, summary
from .autopilot import RISK_TZ, RULE_LABELS, SCHEMES, trading_day
from .config import settings

log = logging.getLogger("astra")
CHECK_EVERY_S = 60


def day_start_ms(now: float | None = None) -> int:
    d = datetime.fromtimestamp(now if now is not None else time.time(), RISK_TZ)
    return int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def _money(v: float | None, pct: float | None = None) -> str:
    if v is None:
        return "—"
    text = f"{v:+.2f}"
    return text + (f"（{pct:+.2f}%）" if pct is not None else "")


def build(c, checks: list[dict] | None = None, now: float | None = None) -> str:
    """The report text. `checks` defaults to a fresh health check."""
    n = summary.numbers(c)
    st = summary.story(c)
    cfg = c.desk.cfg
    ccy = n["currency"]
    lines = [f"📅 {trading_day(now)} 日报（模拟盘）" if c._mode() == "paper" else f"📅 {trading_day(now)} 日报"]

    eq = n["equity"]
    lines.append(f"💰 资产 {eq:.2f} {ccy}" if eq else "💰 资产：暂时读不到")
    lines.append(f"今天 {_money(n['today'], n['todayPct'])}")
    base = cfg.get("baseline") or {}
    if n["sinceStart"] is not None:
        since = f"自方案启动（{summary.when(n['startedAt'])}）{_money(n['sinceStart'], n['sinceStartPct'])}"
        btc_then, btc_now = base.get("btc"), getattr(c, "prices", {}).get("BTC/USDT")
        if btc_then and btc_now:
            since += f"；同期持有 BTC {(btc_now / btc_then - 1) * 100:+.2f}%"
        lines.append(since)

    start = day_start_ms(now)
    trades = [t for t in c.oct_trades if (t.get("time") or 0) >= start]
    if trades:
        buys = sum(1 for t in trades if t.get("side") == "buy")
        fees = sum(t.get("fee") or 0 for t in trades if (t.get("feeCurrency") or ccy) == ccy)
        lines.append(f"🔁 今天成交 {len(trades)} 笔（买 {buys} / 卖 {len(trades) - buys}），手续费约 {fees:.2f} {ccy}")
    else:
        lines.append("🔁 今天没有成交")

    lines.append(f"📌 {st['title']}（方案：{SCHEMES.get(cfg['scheme'], cfg['scheme'])}）")
    rule, line = c.desk.binding_rule()
    if line and eq and not cfg.get("stopped"):
        lines.append(f"🛡 {RULE_LABELS[rule]}线 {line:.2f}，还差 {(eq - line) / eq * 100:.2f}%")
    if st["kind"] in ("stopped", "offline", "live") and st.get("next"):
        lines.append(f"👉 {st['next']}")

    items = checks if checks is not None else health.checks(c)
    issues = [i for i in items if i["status"] != "ok"]
    if issues:
        lines.append("⚠️ 需要注意：" + "；".join(f"{i['label']}：{i['detail']}" for i in issues))
    else:
        lines.append("✅ 系统体检全部正常")
    return "\n".join(lines)


class DailyReporter:
    """Sends the report once a day at `settings.daily_report_hour` (Beijing time)."""

    def __init__(self, cockpit) -> None:
        self.c = cockpit
        self.path = os.path.join(settings.data_dir, "daily_report.json")
        self.last_sent = self._load()

    def _load(self) -> str | None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh).get("date")
        except (OSError, ValueError):
            return None

    def _save(self, date: str) -> None:
        try:
            os.makedirs(settings.data_dir, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"date": date, "ts": int(time.time() * 1000)}, fh)
        except OSError as err:
            log.warning("cannot save daily report state: %s", err)

    def due(self, now: float | None = None) -> bool:
        hour = settings.daily_report_hour
        if hour is None or not self.c.notifier.channels:
            return False
        local = datetime.fromtimestamp(now if now is not None else time.time(), RISK_TZ)
        return local.hour >= hour and self.last_sent != trading_day(now)

    def send_now(self, now: float | None = None) -> tuple[bool, str]:
        """(sent, text): the text is returned either way so the cockpit can preview it."""
        text = build(self.c, now=now)
        return self.c.notifier.send(text, "info", key=f"daily-manual-{time.time()}", quiet_s=0), text

    def status(self) -> dict:
        return {"hour": settings.daily_report_hour, "lastSent": self.last_sent, "next": next_send()}

    def tick(self, now: float | None = None) -> bool:
        if not self.due(now):
            return False
        today = trading_day(now)
        self.c.notifier.send(build(self.c, now=now), "info", key=f"daily-{today}", quiet_s=86400)
        self.last_sent = today
        self._save(today)
        return True

    async def loop(self) -> None:
        await asyncio.sleep(CHECK_EVERY_S)  # let the first OctoBot poll fill the numbers
        while True:
            try:
                self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # a report must never take the bridge down
                log.warning("daily report failed: %s", err)
            await asyncio.sleep(CHECK_EVERY_S)


def next_send(now: float | None = None) -> int | None:
    """When the next report goes out (ms), for the settings drawer."""
    hour = settings.daily_report_hour
    if hour is None:
        return None
    local = datetime.fromtimestamp(now if now is not None else time.time(), RISK_TZ)
    at = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if at <= local:
        at += timedelta(days=1)
    return int(at.timestamp() * 1000)
