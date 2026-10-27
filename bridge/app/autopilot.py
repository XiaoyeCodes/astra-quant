"""Strategy desk: schemes, market radar, automatic switching and the stop-loss guard.

Schemes (方案):
  manual       手动：用户自己选策略
  ai_trade     AI 交易：运行 OctoBot 自带的 AI 策略（AI 信号 / AI 仓位）
  ai_regime    AI 行情选策略：大模型判断行情，自动切换到对应策略
  rule_regime  指标行情选策略：ADX / ATR / 均线斜率 / 布林带宽度判断行情，自动切换

Every scheme has its own maximum loss (stop-loss %), measured from the equity at the
moment the scheme was started. When it is hit, OctoBot cancels every order, sells
everything for USDT and pauses trading, then the profile is set to "观望" so a later
restart does not resume trading. Only paper trading is handled from here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from . import llm
from . import octobot_client as octo
from . import regime as radar
from . import strategies
from .config import settings

if TYPE_CHECKING:
    from .state import Cockpit

log = logging.getLogger("astra")

SCHEMES = {
    "manual": "手动选择策略",
    "ai_trade": "AI 交易",
    "ai_regime": "AI 行情判断 → 自动选策略",
    "rule_regime": "指标行情判断 → 自动选策略",
}
AI_TRADE_PROFILES = ("astra_ai_signal", "astra_ai_index")
AI_CONFIRM = 2  # consecutive identical AI verdicts before an automatic switch
# backup stop run by OctoBot itself when the bridge is down. StopTrading is patched to wait for the
# paper save and park on 观望 with a restart: pausing (StopStrategiesAndPauseTrader) made OctoBot
# rebuild the paper account from the starting funds on the next start
STOP_ACTIONS = ["CancelOpenOrders", "SellAllCurrencies", "StopTrading"]
CLEAR_ACTIONS = ["CancelOpenOrders", "SellAllCurrencies"]  # repeated each second until verified
DUST_VALUE = 1.0  # holdings worth less than this (in the reference market) count as sold
CLEAR_TIMEOUT = 60  # seconds to wait for orders cancelled and holdings sold
IDLE_PROFILE = "astra_idle"
RISK_TZ = timezone(timedelta(hours=8))  # trading day for the daily loss limit (Beijing time)
RULE_LABELS = {"base": "最大止损", "trail": "回撤止损", "daily": "单日亏损上限", "manual": "手动停止"}
# What happens to coins already held when the running strategy changes
HANDOVER = {
    "flatten_on_idle": "切到观望时卖出全部持币，切到其他策略时保留",
    "keep": "保留持币，交给下一个策略（观望时仍然持有）",
    "flatten": "每次切换都先卖出全部持币，新策略从纯 USDT 开始",
}
STOP_CONFIRM_POLLS = 3  # consecutive polls below the stop line before stopping (about 9 s)
PAUSED_CONFIRM_POLLS = 5  # polls without any portfolio value: OctoBot paused itself (backup stop)
# Nothing trades while OctoBot is offline. A larger equity jump across a gap means OctoBot reset
# the paper account to the profile's starting funds (seen after container re-creation), not a loss.
RESET_JUMP = 0.05


def now_ms() -> int:
    return int(time.time() * 1000)


def backup_pct(stop_pct: float) -> float:
    return round(abs(stop_pct) + max(1.0, abs(stop_pct) * 0.5), 2)


def trading_day(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), RISK_TZ).strftime("%Y-%m-%d")


def automation_config(stop_pct: float | None = None, immediate: bool = False,
                      actions: list[str] | None = None) -> dict:
    """OctoBot Automation tentacle config: optional safety net and/or an immediate stop."""
    items = []
    if stop_pct:
        items.append({
            "trigger_event": "ProfitabilityThreshold",
            # backup in case the bridge is down. OctoBot measures it from its own start and reacts
            # instantly, so it gets a margin: the bridge (exact baseline, confirmed breach) acts first
            "ProfitabilityThreshold": {"percent_change": -backup_pct(stop_pct), "time_period": 5256000,
                                       "trigger_only_once": True, "max_trigger_frequency": 0},
            "conditions": ["NoCondition"], "actions": STOP_ACTIONS,
        })
    if immediate:
        items.append({
            "trigger_event": "PeriodicCheck", "PeriodicCheck": {"update_period": 1},
            "conditions": ["NoCondition"], "actions": actions or STOP_ACTIONS,
        })
    return {"automations_count": len(items), "automations": {str(i + 1): a for i, a in enumerate(items)}}


class StrategyDesk:
    def __init__(self, cockpit: "Cockpit") -> None:
        self.c = cockpit
        self.catalog: list[dict] = []
        self.regime: dict | None = None
        self.regime_trail: list[str] = []
        self.ai_regime: dict | None = None
        self.ai_trail: list[dict] = []
        self.ai_error: str | None = None
        self.switching: dict | None = None
        self.cfg: dict = {
            "scheme": "manual",
            "aiTradeProfile": AI_TRADE_PROFILES[0],
            "stopLoss": {"manual": 0, "ai_trade": 10, "ai_regime": 10, "rule_regime": 10},
            # from the highest equity since the scheme started / from the day's first equity (0 = off)
            "trail": {"manual": 0, "ai_trade": 0, "ai_regime": 0, "rule_regime": 0},
            "dailyLoss": {"manual": 0, "ai_trade": 0, "ai_regime": 0, "rule_regime": 0},
            "peak": None,       # {"equity", "ts"}: highest settled equity since the scheme started
            "day": None,        # {"date", "equity"}: first settled equity of the trading day
            "baseline": None,   # {"equity", "ts", "scheme"}
            "stopped": None,    # {"ts", "equity", "baseline", "pct", "scheme"}
            "lastSwitch": None, "lastSwitchTo": None,
            "lastSwitchInfo": None,  # {"ts", "from", "fromLabel", "to", "label", "by", "why", "handover"}
            "handover": "flatten_on_idle",
        }
        self._stopping = False
        self._handing_over = False
        self._breaches = 0
        self._blind = 0
        self._connected_at = 0
        self._equity_before_gap: float | None = None  # last settled equity before OctoBot went away
        self._gap_check = False
        self._automation_key = None
        self._ai_wake = asyncio.Event()
        self._load()
        # survives bridge restarts, so a paper reset during a redeploy is still recognised
        self._equity_before_gap = (self.cfg.get("lastEquity") or {}).get("equity")

    # ------------------------------------------------------------ storage ---
    def _path(self) -> str:
        return os.path.join(settings.data_dir, "strategy.json")

    def _load(self) -> None:
        try:
            with open(self._path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return
        if data.pop("auto", False) and "scheme" not in data:
            data["scheme"] = "rule_regime"  # settings saved by the first radar version
        stop = dict(self.cfg["stopLoss"], **data.pop("stopLoss", {}))
        trail = dict(self.cfg["trail"], **data.pop("trail", {}))
        daily = dict(self.cfg["dailyLoss"], **data.pop("dailyLoss", {}))
        self.cfg.update(data)
        self.cfg["trail"], self.cfg["dailyLoss"] = trail, daily
        self.cfg["stopLoss"] = stop

    def save(self) -> None:
        try:
            os.makedirs(settings.data_dir, exist_ok=True)
            tmp = self._path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.cfg, fh, ensure_ascii=False)
            os.replace(tmp, self._path())
        except OSError as err:
            log.warning("cannot save strategy settings: %s", err)

    # ------------------------------------------------------------- radar ---
    async def loop_rule(self) -> None:
        while True:
            delay = settings.regime_period
            try:
                if self.c.link_bitget["ok"]:
                    candles = await self.c.bitget.candles(settings.regime_symbol, "1h", 200)
                    verdict = radar.analyse(candles)
                    if verdict:
                        verdict.update(symbol=settings.regime_symbol, ts=now_ms())
                        if not self.regime or self.regime["regime"] != verdict["regime"]:
                            self.c.add_log("info", f"指标行情雷达：{settings.regime_symbol}「{verdict['label']}」"
                                                   f"（置信度 {verdict['confidence']:.0%}）")
                        self.regime = verdict
                        self.regime_trail = (self.regime_trail + [verdict["regime"]])[-12:]
                        self._candles = candles
                        self._ai_wake.set()  # the AI read needs fresh metrics
                        if self.cfg["scheme"] == "rule_regime":
                            await self.maybe_auto_switch()
                else:
                    delay = 20
            except asyncio.CancelledError:
                raise
            except Exception as err:
                log.warning("regime: %s", err)
                delay = 60
            await asyncio.sleep(delay)

    def _ai_choices(self) -> list[dict]:
        return [s for s in self.catalog if not s["blocked"] and s["family"] in ("grid", "dca", "signal", "idle")]

    async def loop_ai(self) -> None:
        """One AI read per AI_REGIME_PERIOD, or right away when asked (scheme started, refresh)."""
        await asyncio.sleep(5)
        while True:
            try:
                await asyncio.wait_for(self._ai_wake.wait(), timeout=settings.ai_regime_period)
            except asyncio.TimeoutError:
                pass
            self._ai_wake.clear()
            if not llm.available() or not self.regime or not getattr(self, "_candles", None):
                continue
            fresh = self.ai_regime and now_ms() - self.ai_regime["ts"] < settings.ai_regime_period * 1000 * 0.9
            if fresh and not getattr(self, "_ai_force", False):
                continue
            self._ai_force = False
            try:
                verdict = await llm.judge(settings.regime_symbol, self._candles, self.regime["metrics"], self._ai_choices())
                verdict.update(ts=now_ms(), symbol=settings.regime_symbol)
                if not self.ai_regime or self.ai_regime["regime"] != verdict["regime"] \
                        or self.ai_regime["strategy"] != verdict["strategy"]:
                    label = strategies.META.get(verdict["strategy"], {}).get("label", verdict["strategy"])
                    self.c.add_log("info", f"AI 行情判断：「{verdict['label']}」，建议 {label}"
                                           f"（置信度 {verdict['confidence']:.0%}）")
                self.ai_regime, self.ai_error = verdict, None
                self.ai_trail = (self.ai_trail + [verdict])[-12:]
                if self.cfg["scheme"] == "ai_regime":
                    await self.maybe_auto_switch()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                msg = str(err)[:160]
                if msg != self.ai_error:
                    self.c.add_log("warn", f"AI 行情判断失败：{msg}")
                notifier = getattr(self.c, "notifier", None)
                if notifier and self.cfg["scheme"] in ("ai_regime", "ai_trade"):
                    notifier.send(f"AI 行情判断失败：{msg}", "warn", key="ai-fail", quiet_s=3600)
                self.ai_error = msg

    def refresh_ai(self) -> None:
        self._ai_force = True
        self._ai_wake.set()

    # --------------------------------------------------------- switching ---
    def block_reason(self) -> str | None:
        c = self.c
        if not c.link_octobot["ok"]:
            return "OctoBot 未连接"
        if c._mode() == "live" or c.profile.get("real"):
            return "当前为实盘：策略只能在 OctoBot 中手动切换"
        if self.switching:
            return "正在切换策略，请等待 OctoBot 重启完成"
        if self._stopping:
            return "正在执行止损"
        if self._handing_over:
            return "正在按交接规则卖出持币"
        if self._connected_at and now_ms() - self._connected_at < 60 * 1000:
            return "账户资产估值正在初始化，请在连接稳定 1 分钟后启动方案或切换策略"
        return None

    async def switch(self, pid: str, by: str = "user", why: str | None = None) -> str:
        c = self.c
        target = await asyncio.to_thread(strategies.find, pid)
        if target is None:
            raise ValueError("没有这个策略")
        if target["blocked"]:
            raise ValueError(target["blocked"])
        if not target["simulated"]:
            raise ValueError("只能切换到模拟盘策略")
        reason = self.block_reason()
        if reason:
            raise ValueError(reason)
        current = c.profile.get("id")
        if pid == current:
            raise ValueError("已经在运行这个策略")
        handover_note = await self._handover(pid)
        await octo.select_profile(pid)
        selected = (await asyncio.to_thread(octo.read_active_profile)).get("id")
        if selected != pid:
            raise ValueError("OctoBot 没有接受这个策略：新加入的策略需要先重启一次 OctoBot 才能被识别")
        await octo.restart_bot()
        self.switching = {"from": current, "to": pid, "label": target["label"], "by": by,
                          "since": now_ms(), "octoSince": c.link_octobot.get("since")}
        self.cfg.update(lastSwitch=now_ms(), lastSwitchTo=pid, lastSwitchInfo={
            "ts": now_ms(), "from": current, "fromLabel": c.profile.get("label") or current, "to": pid,
            "label": target["label"], "by": by, "why": why, "handover": handover_note})
        self.save()
        who = {"auto": "自动切换", "scheme": "启动方案，切换"}.get(by, "手动切换")
        c.add_log("risk", f"{who}策略：{c.profile.get('label') or current} → {target['label']}，OctoBot 正在重启"
                          + (f"；{handover_note}" if handover_note else ""))
        return f"已切换到 {target['label']}，OctoBot 重启需要 1–3 分钟" + (f"（{handover_note}）" if handover_note else "")

    def set_handover(self, policy: str) -> None:
        if policy not in HANDOVER:
            raise ValueError("没有这个交接规则")
        self.cfg["handover"] = policy
        self.save()
        self.c.add_log("info", f"交接规则改为：{HANDOVER[policy]}")

    def _flatten_for(self, pid: str) -> bool:
        policy = self.cfg.get("handover", "flatten_on_idle")
        return policy == "flatten" or (policy == "flatten_on_idle" and pid == IDLE_PROFILE)

    async def _handover(self, pid: str) -> str | None:
        """Sell the coins the old strategy leaves behind when the rule asks for it (verified)."""
        if not self._flatten_for(pid):
            return None
        quote = self.c.profile.get("referenceMarket", "USDT")
        before = await self._liquidation_status(quote)
        if before["known"] and not before["leftovers"] and not before["orders"]:
            return None  # nothing to hand over
        self._handing_over = True
        try:
            sold_at = time.time()
            status = await self._clear_positions(quote)
            saved = await self._wait_paper_saved(sold_at) if self.c._mode() == "paper" else True
        finally:
            self._handing_over = False
        if status["known"] and not status["orders"] and not status["leftovers"]:
            note = f"交接规则：已卖出全部持币，只剩 {quote}（{status['equity']:.2f}）"
            if not saved:
                self.c.add_log("risk", "OctoBot 30 秒内没有保存卖出后的模拟账户，重启后可能恢复成卖出前的持币，请切换后检查")
                note += "；OctoBot 未及时保存模拟账户，请检查持仓"
            return note
        problem = ("无法读取持仓，未能确认是否已卖出" if not status["known"] else
                   f"仍有 {status['orders']} 张挂单" if status["orders"] else
                   "仍有持币未卖出：" + "、".join(f"{k} {v:g}" for k, v in status["leftovers"].items()))
        self.c.add_log("risk", f"交接清仓未完成：{problem}。切换仍会进行，请到 OctoBot 检查持仓")
        return f"交接清仓未完成：{problem}"

    def check_switch_done(self) -> None:
        sw = self.switching
        if not sw:
            return
        if self.c.link_octobot.get("since") != sw["octoSince"] and self.c.profile.get("id") == sw["to"]:
            self.c.add_log("ok", f"策略已切换为 {sw['label']}")
            self.switching = None
        elif now_ms() - sw["since"] > 10 * 60 * 1000:
            self.c.add_log("warn", f"切换到 {sw['label']} 超过 10 分钟仍未完成，请在 OctoBot 中检查")
            self.switching = None

    async def manual_select(self, pid: str) -> str:
        """Picking a strategy by hand stops any automatic scheme (it would switch back)."""
        result = await self.switch(pid)
        if self.cfg["scheme"] != "manual":
            self.c.add_log("info", f"已从「{SCHEMES[self.cfg['scheme']]}」改为手动选择策略")
        self._arm("manual")
        return result

    # ------------------------------------------------------------ schemes ---
    def _arm(self, scheme: str) -> None:
        equity = self.c.oct_equity_live
        self.cfg.update(scheme=scheme, stopped=None,
                        baseline={"equity": equity, "ts": now_ms(), "scheme": scheme,
                                  # BTC price at the start: the daily report compares against holding BTC
                                  "btc": getattr(self.c, "prices", {}).get("BTC/USDT")} if equity else None,
                        peak={"equity": equity, "ts": now_ms()} if equity else None,
                        day={"date": trading_day(), "equity": equity} if equity else None)
        self.save()
        self._automation_key = None  # push the safety net again

    async def start_scheme(self, scheme: str, ai_profile: str | None = None, stop_pct=None,
                           trail_pct=None, daily_pct=None) -> str:
        if scheme not in SCHEMES:
            raise ValueError("没有这个方案")
        reason = self.block_reason()
        if reason:
            raise ValueError(reason)
        if self.c._mode() == "live" or self.c.profile.get("real"):
            raise ValueError("当前为实盘：方案只能在模拟盘下启动")
        if ai_profile is not None and ai_profile not in AI_TRADE_PROFILES:
            raise ValueError("没有这个 AI 策略")
        if scheme == "ai_trade":
            target = await asyncio.to_thread(strategies.find, ai_profile or self.cfg["aiTradeProfile"])
            if not target or target.get("blocked"):
                raise ValueError(target.get("blocked") if target else "没有这个 AI 策略")
        if scheme in ("ai_trade", "ai_regime") and not llm.available():
            raise ValueError("未配置 LLM_API_KEY：请在 .env 中填写 DeepSeek 的 Key 后重新运行 start.bat")
        if stop_pct is not None or trail_pct is not None or daily_pct is not None:
            self.set_risk(scheme, stop=stop_pct, trail=trail_pct, daily=daily_pct)
        if ai_profile is not None:
            self.cfg["aiTradeProfile"] = ai_profile
        self._arm(scheme)
        rules = [(RULE_LABELS["base"], self.cfg["stopLoss"].get(scheme, 0)),
                 (RULE_LABELS["trail"], self.cfg["trail"].get(scheme, 0)),
                 (RULE_LABELS["daily"], self.cfg["dailyLoss"].get(scheme, 0))]
        active = "，".join(f"{name} {pct:g}%" for name, pct in rules if pct) or "未设置任何止损"
        self.c.add_log("risk", f"启动方案「{SCHEMES[scheme]}」：{active}")
        if scheme == "ai_trade":
            target = self.cfg["aiTradeProfile"]
            if self.c.profile.get("id") != target:
                return await self.switch(target, by="scheme")
        elif scheme == "rule_regime":
            await self.maybe_auto_switch(starting=True)
        elif scheme == "ai_regime":
            self.refresh_ai()
            return "方案已启动，AI 正在分析行情（约 10–30 秒），之后自动选择策略"
        return "方案已启动"

    @staticmethod
    def _pct(value, label: str, top: float) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{label}需要是数字")
        try:
            pct = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label}需要是数字") from None
        if not 0 <= pct <= top:
            raise ValueError(f"{label}应在 0–{top:g}% 之间（0 表示关闭）")
        return round(pct, 2)

    def set_stop_loss(self, scheme: str, pct: float) -> None:
        self.set_risk(scheme, stop=pct)

    def set_risk(self, scheme: str, stop=None, trail=None, daily=None) -> None:
        """Validate everything first so a bad field never half-applies the others."""
        if scheme not in SCHEMES:
            raise ValueError("没有这个方案")
        changes = {}
        if stop is not None:
            changes["stopLoss"] = self._pct(stop, "止损比例", 90)
        if trail is not None:
            changes["trail"] = self._pct(trail, "回撤止损比例", 90)
        if daily is not None:
            changes["dailyLoss"] = self._pct(daily, "单日亏损上限", 50)
        if not changes:
            raise ValueError("没有要保存的风控参数")
        for key, value in changes.items():
            self.cfg[key][scheme] = value
        self.save()
        self._automation_key = None

    def _verdict(self) -> tuple[dict | None, list[str] | list[dict], int, list[str]]:
        """(verdict, trail, confirmations needed, acceptable strategies) for the running scheme."""
        if self.cfg["scheme"] == "ai_regime":
            v = self.ai_regime
            ok = ([v["strategy"]] + radar.PREFER[v["regime"]]) if v else []
            return v, self.ai_trail, AI_CONFIRM, ok
        v = self.regime
        return v, self.regime_trail, settings.auto_confirm, (v["prefer"] if v else [])

    def auto_state(self) -> dict:
        cfg = self.cfg
        scheme = cfg["scheme"]
        hold_ms = settings.auto_min_hold_hours * 3600 * 1000
        last = cfg.get("lastSwitch")
        eligible_at = last + hold_ms if last else None
        verdict, trail, need, ok = self._verdict()
        current = self.c.profile.get("id")
        usable = {s["id"] for s in self.catalog if not s["blocked"]}
        target = next((p for p in ok if p in usable), None) if verdict else None
        confirmations = 0
        expected = ((verdict["regime"], verdict["strategy"])
                    if scheme == "ai_regime" and verdict else
                    verdict["regime"] if verdict else None)
        for item in reversed(trail):
            key = (item["regime"], item["strategy"]) if isinstance(item, dict) else item
            if key != expected:
                break
            confirmations += 1
        confirmed = confirmations >= need
        if scheme not in ("ai_regime", "rule_regime"):
            status = "当前方案不自动切换策略"
        elif cfg.get("stopped"):
            status = "已触发止损，方案停止"
        elif self.c._mode() != "paper":
            status = "只在模拟盘下工作"
        elif not verdict:
            status = "等待 AI 的第一次判断" if scheme == "ai_regime" else "等待行情雷达的第一次判断"
        elif current in ok:
            status = "当前策略适合现在的行情，保持不变"
        elif not confirmed:
            status = f"行情判断需连续 {need} 次一致（已 {confirmations} 次）"
        elif verdict["confidence"] < 0.5:
            status = "行情判断置信度不足 50%，暂不切换"
        elif eligible_at and now_ms() < eligible_at:
            status = f"冷却中：上次切换后需运行满 {settings.auto_min_hold_hours:g} 小时"
        elif target:
            status = f"将切换到 {strategies.META.get(target, {}).get('label', target)}"
        else:
            status = "没有可用的推荐策略"
        return {"enabled": scheme in ("ai_regime", "rule_regime"), "status": status, "confirmNeeded": need,
                "minHoldHours": settings.auto_min_hold_hours, "lastSwitch": last,
                "lastSwitchTo": cfg.get("lastSwitchTo"), "eligibleAt": eligible_at, "target": target,
                "ready": status.startswith("将切换到")}

    async def maybe_auto_switch(self, starting: bool = False) -> None:
        if self.cfg["scheme"] not in ("ai_regime", "rule_regime") or self.cfg.get("stopped") or self.block_reason():
            return
        state = self.auto_state()
        if starting:
            # a freshly started scheme acts on the latest verdict without waiting for confirmations
            verdict, _, _, ok = self._verdict()
            if not verdict or self.c.profile.get("id") in ok or not state["target"]:
                return
        elif not state["ready"]:
            return
        verdict = self._verdict()[0]
        source = "AI" if self.cfg["scheme"] == "ai_regime" else "行情雷达"
        why = (f"{source}判断现在是「{verdict.get('label') or verdict.get('regime')}」行情"
               f"（置信度 {verdict.get('confidence', 0):.0%}），这个策略更适合") if verdict else None
        try:
            await self.switch(state["target"], by="auto", why=why)
        except (ValueError, octo.OctoBotError) as err:
            self.c.add_log("warn", f"自动切换失败：{err}")

    # ---------------------------------------------------------- stop loss ---
    def risk_lines(self) -> dict[str, float | None]:
        """Equity level of each active rule for the running scheme (None when off or unknown)."""
        scheme = self.cfg["scheme"]

        def line(anchor: dict | None, pct: float) -> float | None:
            if not anchor or not anchor.get("equity") or not pct:
                return None
            return anchor["equity"] * (1 - pct / 100)
        day = self.cfg.get("day")
        today = day if day and day.get("date") == trading_day() else None
        return {"base": line(self.cfg.get("baseline"), self.cfg["stopLoss"].get(scheme, 0)),
                "trail": line(self.cfg.get("peak"), self.cfg["trail"].get(scheme, 0)),
                "daily": line(today, self.cfg["dailyLoss"].get(scheme, 0))}

    def binding_rule(self) -> tuple[str | None, float | None]:
        """The rule whose line is highest, i.e. the one that fires first."""
        active = [(v, k) for k, v in self.risk_lines().items() if v is not None]
        if not active:
            return None, None
        value, rule = max(active)
        return rule, value

    def stop_line(self) -> float | None:
        return self.binding_rule()[1]

    def _track_anchors(self, live: float) -> None:
        """Keep the peak and the day's first equity up to date (settled values only)."""
        changed = False
        peak = self.cfg.get("peak")
        if self.cfg.get("baseline") and (not peak or live > peak.get("equity", 0)):
            self.cfg["peak"] = {"equity": live, "ts": now_ms()}
            changed = peak is None or live > peak["equity"] * 1.001  # do not rewrite the file on every tick
        today = trading_day()
        day = self.cfg.get("day")
        if not day or day.get("date") != today:
            self.cfg["day"] = {"date": today, "equity": live}
            changed = True
        if changed:
            self.save()

    async def on_octobot_sync(self, fresh_connection: bool) -> None:
        """Called after each successful OctoBot poll."""
        c = self.c
        live = c.oct_equity_live
        if fresh_connection:
            self._connected_at = now_ms()
            self._gap_check = self._equity_before_gap is not None
            marker = self._new_backup_stop()
            if marker and not self._stopping:
                # OctoBot's own stop sold and restarted while the bridge was away: the lower equity is
                # a stop, not a paper reset, so the gap check must not rebase the anchors
                self._gap_check = False
                self._stopping = True
                self._stop_task = asyncio.create_task(self.record_backup_stop(marker))
                return
        settling = now_ms() - self._connected_at < 60 * 1000
        if live and not settling:
            if self._gap_check:
                self._gap_check = False
                self._check_paper_reset(self._equity_before_gap, live)
            self._equity_before_gap = live
            if c._mode() == "paper":
                self._track_anchors(live)
            if now_ms() - (self.cfg.get("lastEquity") or {}).get("ts", 0) >= 60 * 1000:
                self.cfg["lastEquity"] = {"equity": live, "ts": now_ms()}
                self.save()
        if self.cfg["baseline"] is None and live and c._mode() == "paper" and not settling:
            self.cfg["baseline"] = {"equity": live, "ts": now_ms(), "scheme": self.cfg["scheme"]}
            self.save()
        rule, line = self.binding_rule()
        breach = bool(line and live and live <= line)
        # right after a (re)start OctoBot may value the portfolio before every price is known:
        # ignore the first minute and require the breach on several polls in a row
        self._breaches = self._breaches + 1 if breach and not settling else 0
        armed = bool(line) and not self.cfg.get("stopped") and not self._stopping and c._mode() == "paper"
        if self._breaches >= STOP_CONFIRM_POLLS and armed:
            self._stopping = True  # set before the task starts so the next poll does not fire twice
            self._stop_task = asyncio.create_task(self.execute_stop(rule=rule))
            return
        # a paused OctoBot reports no portfolio and no orders: its backup stop-loss fired first
        blind = armed and not settling and not live and not c.oct_orders and not self.switching
        self._blind = self._blind + 1 if blind else 0
        if self._blind >= PAUSED_CONFIRM_POLLS:
            self._blind = 0
            self._stopping = True
            self._stop_task = asyncio.create_task(self.record_backup_stop())
            return
        if fresh_connection or self._automation_key is None:
            await self._sync_safety_net()

    def _check_paper_reset(self, before: float, after: float) -> None:
        """Rebase the stop-loss when OctoBot reset the paper account instead of liquidating it."""
        base = self.cfg.get("baseline")
        if not base or self.c._mode() != "paper" or self.cfg.get("stopped"):
            return
        change = (after - before) / before
        if abs(change) <= RESET_JUMP:
            return
        old = base.get("equity")
        self.cfg["baseline"] = {"equity": after, "ts": now_ms(), "scheme": self.cfg["scheme"], "rebased": True}
        self.cfg["peak"] = {"equity": after, "ts": now_ms()}
        self.cfg["day"] = {"date": trading_day(), "equity": after}
        self.save()
        self._breaches = 0
        self.c.add_log("risk", f"OctoBot 重启前后模拟资金从 {before:.2f} 变为 {after:.2f}（{change:+.1%}），离线期间不会交易，"
                               f"判断为 OctoBot 重置了模拟资金而不是亏损；止损起点由 {old:.2f} 改为 {after:.2f}")

    async def _sync_safety_net(self) -> None:
        c = self.c
        pct = self.cfg["stopLoss"].get(self.cfg["scheme"], 0) if not self.cfg.get("stopped") else 0
        if c.profile.get("id") == IDLE_PROFILE:
            pct = 0  # 观望 holds only USDT and never trades: nothing for a backup stop to protect
        if c._mode() != "paper" or self._stopping:
            return
        key = (c.link_octobot.get("since"), c.profile.get("id"), pct)
        if key == self._automation_key:
            return
        try:
            await octo.save_automations(automation_config(pct or None))
            await octo.start_automations()
            self._automation_key = key
            if pct:
                log.info("OctoBot safety stop-loss set to -%s%% on %s", pct, c.profile.get("id"))
        except octo.OctoBotError as err:
            log.warning("cannot set OctoBot automation: %s", err)

    def _new_backup_stop(self) -> dict | None:
        """The marker OctoBot's patched StopTrading leaves, if it fired since we last looked."""
        marker = octo.backup_stop_marker()
        if not marker or not isinstance(marker.get("ts"), (int, float)):
            return None
        ts = int(marker["ts"] * 1000)
        if ts <= (self.cfg.get("backupSeen") or 0):
            return None
        base = self.cfg.get("baseline") or {}
        if self.cfg.get("stopped") or ts < base.get("ts", 0):
            self.cfg["backupSeen"] = ts  # belongs to an earlier scheme or an already recorded stop
            self.save()
            return None
        return dict(marker, ts=ts)

    async def record_backup_stop(self, marker: dict | None = None) -> None:
        if marker:
            await self._record_octobot_stop(marker)
        else:
            await self._record_paused_stop()

    async def _record_octobot_stop(self, marker: dict) -> None:
        """OctoBot cancelled, sold, waited for the paper save and restarted on 观望: record and verify."""
        c = self.c
        base = self.cfg.get("baseline") or {}
        scheme = self.cfg["scheme"]
        pct = self.cfg["stopLoss"].get(scheme, 0)
        quote = c.profile.get("referenceMarket", "USDT")
        self.cfg["backupSeen"] = marker["ts"]
        self.cfg["stopped"] = {"ts": marker["ts"], "equity": None, "baseline": base.get("equity"), "pct": pct,
                               "loss": None, "scheme": scheme, "by": "octobot", "verified": None,
                               "rule": "base", "ruleLabel": RULE_LABELS["base"]}
        self.save()
        c.add_log("risk", f"OctoBot 内置备用止损已在 {datetime.fromtimestamp(marker['ts'] / 1000, RISK_TZ):%m-%d %H:%M} "
                          f"触发（−{backup_pct(pct):g}%，桥接服务当时没有运行）：已撤单、卖出持币并切换到观望。正在核实")
        try:
            status = {"known": False, "orders": None, "leftovers": {}, "equity": None}
            for _ in range(10):  # OctoBot just restarted: wait until it can value the holdings
                await asyncio.sleep(6)
                try:
                    status = await self._liquidation_status(quote)
                except octo.OctoBotError:
                    continue
                if status["known"]:
                    break
            on_idle = c.profile.get("id") == IDLE_PROFILE
            verified = status["known"] and not status["orders"] and not status["leftovers"] and on_idle
            problem = None
            if not status["known"]:
                problem = "无法读取 OctoBot 持仓，未能确认是否全部卖出"
            elif status["orders"]:
                problem = f"仍有 {status['orders']} 张挂单未撤销"
            elif status["leftovers"]:
                problem = "仍有持币未卖出：" + "、".join(f"{k} {v:g}" for k, v in status["leftovers"].items())
            elif not on_idle:
                problem = f"OctoBot 没有切换到观望（当前 {c.profile.get('id')}）"
            equity = status.get("equity")
            loss = (1 - equity / base["equity"]) * 100 if equity and base.get("equity") else None
            self.cfg["stopped"].update(verified=verified, problem=problem, finalEquity=equity, equity=equity, loss=loss)
            self.save()
            if verified:
                c.add_log("risk", f"备用止损已核实：挂单已撤销，只剩 {quote}（{equity:.2f}），OctoBot 停在观望，不会再下单")
            else:
                c.add_log("risk", f"备用止损未完全执行：{problem}。请到 OctoBot 检查并手动处理")
        finally:
            self._automation_key = None
            self._stopping = False

    async def _record_paused_stop(self) -> None:
        """Older OctoBot path (or a trader paused by hand): cancelled, sold and paused; park on 观望."""
        c = self.c
        base = self.cfg.get("baseline") or {}
        pct = self.cfg["stopLoss"].get(self.cfg["scheme"], 0)
        c.add_log("risk", f"OctoBot 已暂停交易且不再报告资产：判断为 OctoBot 内置备用止损已触发（−{backup_pct(pct):g}%），"
                          "挂单已撤销、持币已卖出")
        self.cfg["stopped"] = {"ts": now_ms(), "equity": None, "baseline": base.get("equity"), "pct": pct,
                               "loss": None, "scheme": self.cfg["scheme"], "by": "octobot", "verified": None,
                               "problem": "OctoBot 已暂停，无法再读取持仓，请到 OctoBot 的 Portfolio 页面确认已全部卖出"}
        self.save()
        try:
            await octo.save_automations(automation_config())
            await octo.start_automations()
            await octo.select_profile(IDLE_PROFILE)
            self._automation_key = None
            c.add_log("risk", "已把 OctoBot 配置改为「观望」，之后重启也不会恢复交易")
        except octo.OctoBotError as err:
            c.add_log("risk", f"无法把 OctoBot 改为观望：{err}。请在 OctoBot 中手动选择“ASTRA · 观望”")
        finally:
            self._stopping = False

    async def _liquidation_status(self, quote: str) -> dict:
        """What is still open after the clear phase, read from OctoBot itself."""
        orders = await octo.orders()
        held = await octo.holdings()
        if held is None:
            return {"known": False, "orders": len(orders), "leftovers": {}, "equity": None}
        leftovers = {cur: round(h["total"], 8) for cur, h in held.items()
                     if cur != quote and h["value"] >= DUST_VALUE}
        return {"known": True, "orders": len(orders), "leftovers": leftovers,
                "equity": sum(h["value"] for h in held.values())}

    async def _wait_paper_saved(self, since: float, timeout: float = 30) -> bool:
        """Wait until OctoBot has written the paper portfolio after `since` (it reloads it on restart)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            stamp = await asyncio.to_thread(octo.paper_snapshot_time)
            if stamp is not None and stamp >= since:
                return True
            await asyncio.sleep(1)
        return False

    async def _clear_positions(self, quote: str) -> dict:
        """Cancel and sell every second until OctoBot shows no order and only the quote currency
        (twice in a row) or CLEAR_TIMEOUT passes; then remove the automation. Returns the last status."""
        status = {"known": False, "orders": None, "leftovers": {}, "equity": None}
        await octo.save_automations(automation_config(immediate=True, actions=CLEAR_ACTIONS))
        await octo.start_automations()
        try:
            clean_polls, deadline = 0, now_ms() + CLEAR_TIMEOUT * 1000
            while now_ms() < deadline:
                await asyncio.sleep(2)
                status = await self._liquidation_status(quote)
                clean = status["known"] and not status["orders"] and not status["leftovers"]
                clean_polls = clean_polls + 1 if clean else 0
                if clean_polls >= 2:
                    break
        finally:
            await octo.save_automations(automation_config())
            await octo.start_automations()
            self._automation_key = None  # the safety net is pushed again on the next sync
        return status

    def _stop_message(self, rule: str, equity: float) -> tuple[str, float, float | None]:
        """(log text, limit %, loss % from that rule's anchor)."""
        scheme = self.cfg["scheme"]
        if rule == "manual":
            ref = (self.cfg.get("baseline") or {}).get("equity")
            loss = (1 - equity / ref) * 100 if equity and ref else None
            return f"手动停止：权益 {equity or 0:.2f}。正在撤单、卖出全部持币并停止交易", 0, loss
        anchor, limit, what = {
            "base": (self.cfg.get("baseline"), self.cfg["stopLoss"].get(scheme, 0), "方案启动时"),
            "trail": (self.cfg.get("peak"), self.cfg["trail"].get(scheme, 0), "运行期间最高"),
            "daily": (self.cfg.get("day"), self.cfg["dailyLoss"].get(scheme, 0), "今日起点"),
        }[rule]
        ref = (anchor or {}).get("equity")
        loss = (1 - equity / ref) * 100 if equity and ref else None
        text = (f"触发{RULE_LABELS[rule]}：权益 {equity:.2f}，{what} {ref or 0:.2f}，"
                f"{'下跌' if rule != 'trail' else '回撤'} {loss or 0:.2f}%（上限 {limit:g}%）。"
                "正在撤单、卖出全部持币并停止交易")
        return text, limit, loss

    async def execute_stop(self, reason: str | None = None, rule: str = "base") -> None:
        """Cancel, sell everything, verify it really happened, then park on 观望 and restart (no pause)."""
        c = self.c
        self._stopping = True
        base = self.cfg.get("baseline") or {}
        quote = c.profile.get("referenceMarket", "USDT")
        equity = c.oct_equity_live or c.oct_equity
        text, pct, loss = self._stop_message(rule, equity)
        what = "停止" if rule == "manual" else "止损"
        c.add_log("risk", reason or text)
        self.cfg["stopped"] = {"ts": now_ms(), "equity": equity, "baseline": base.get("equity"),
                               "pct": pct, "loss": loss, "scheme": self.cfg["scheme"], "verified": None,
                               "rule": rule, "ruleLabel": RULE_LABELS[rule]}
        self.save()
        status = {"known": False, "orders": None, "leftovers": {}}
        try:
            # 1. cancel and sell every second (also undoes orders a strategy re-creates), verified
            sold_at = time.time()
            status = await self._clear_positions(quote)
            # 2. park on 观望 with a restart instead of pausing: a paused OctoBot saves its paper
            #    account without an ending portfolio and resets it to the starting funds on the next
            #    start. 观望 places no orders, so trading stops the same way.
            if c._mode() == "paper":
                await self._wait_paper_saved(sold_at)
            octo_since = c.link_octobot.get("since")
            await octo.select_profile(IDLE_PROFILE)
            await octo.restart_bot()
            self.switching = {"from": c.profile.get("id"), "to": IDLE_PROFILE, "label": "观望", "by": "stop",
                              "since": now_ms(), "octoSince": octo_since}
            self._automation_key = None
        except octo.OctoBotError as err:
            self.cfg["stopped"].update(verified=False, problem=f"{what}执行出错：{err}")
            self.save()
            c.add_log("risk", f"{what}执行出错：{err}。请立即在 OctoBot 中手动撤单、卖出并停止策略")
            self._stopping = False
            return
        verified = status["known"] and not status["orders"] and not status["leftovers"]
        problem = None
        if not status["known"]:
            problem = "无法读取 OctoBot 持仓，未能确认是否全部卖出"
        elif status["orders"]:
            problem = f"仍有 {status['orders']} 张挂单未撤销"
        elif status["leftovers"]:
            problem = "仍有持币未卖出：" + "、".join(f"{k} {v:g}" for k, v in status["leftovers"].items())
        self.cfg["stopped"].update(verified=verified, problem=problem, finalEquity=status.get("equity"))
        self.save()
        if verified:
            c.add_log("risk", f"{what}完成并已核实：挂单已撤销，只剩 {quote}（{status['equity']:.2f}）；"
                              "OctoBot 正在切换到「观望」并重启，不会再下单")
        else:
            c.add_log("risk", f"{what}未完全执行：{problem}。OctoBot 正在切换到「观望」，请立即到 OctoBot 检查并手动处理")
        self._stopping = False

    async def halt(self) -> str:
        """Stop by hand the same verified way a stop-loss does: cancel, sell, check, park on 观望."""
        mode = self.c._mode()
        if not self.c.link_octobot.get("ok"):
            raise ValueError("OctoBot 未连接，现在无法撤单和卖出")
        if mode == "live":
            raise ValueError("当前为实盘：请在 OctoBot 中操作")
        if mode != "paper":
            raise ValueError("OctoBot 没有在模拟盘交易，不需要停止")
        if self._stopping:
            raise ValueError("正在执行止损或停止")
        if self.switching:
            raise ValueError("正在切换策略，请等待 OctoBot 重启完成")
        self._stopping = True  # before the task starts, so a second click is refused
        self._stop_task = asyncio.create_task(self.execute_stop(rule="manual"))
        return "正在撤单并卖出全部持币，完成后切换到观望（约 1 分钟）"

    # ----------------------------------------------------------- snapshot ---
    def snapshot(self) -> dict:
        cfg = self.cfg
        equity = self.c.oct_equity
        base = (cfg.get("baseline") or {}).get("equity")
        return {
            "current": self.c.profile.get("id"),
            "list": self.catalog,
            "switching": self.switching,
            "blocked": self.block_reason(),
            "scheme": cfg["scheme"],
            "schemes": SCHEMES,
            "aiTradeProfile": cfg["aiTradeProfile"],
            "aiTradeProfiles": list(AI_TRADE_PROFILES),
            "stopLoss": cfg["stopLoss"],
            "trail": cfg["trail"],
            "dailyLoss": cfg["dailyLoss"],
            "peak": cfg.get("peak"),
            "day": cfg.get("day") if (cfg.get("day") or {}).get("date") == trading_day() else None,
            "lines": self.risk_lines(),
            "bindingRule": self.binding_rule()[0],
            "handover": cfg.get("handover", "flatten_on_idle"),
            "handoverOptions": HANDOVER,
            "backtest": strategies.BACKTEST,
            "baseline": cfg.get("baseline"),
            "stopLine": self.stop_line(),
            "drawdown": (1 - equity / base) * 100 if equity and base else None,
            "stopped": cfg.get("stopped"),
            "stopping": self._stopping,
            "lastSwitchInfo": cfg.get("lastSwitchInfo"),
            "auto": self.auto_state(),
            "ai": {"available": llm.available(), "model": settings.llm_model, "verdict": self.ai_regime,
                   "error": self.ai_error, "periodMin": round(settings.ai_regime_period / 60)},
        }
