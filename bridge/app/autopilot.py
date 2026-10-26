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
STOP_ACTIONS = ["CancelOpenOrders", "SellAllCurrencies", "StopStrategiesAndPauseTrader"]
CLEAR_ACTIONS = ["CancelOpenOrders", "SellAllCurrencies"]  # repeated each second until verified
PAUSE_ACTIONS = ["StopStrategiesAndPauseTrader"]
DUST_VALUE = 1.0  # holdings worth less than this (in the reference market) count as sold
CLEAR_TIMEOUT = 60  # seconds to wait for orders cancelled and holdings sold
IDLE_PROFILE = "astra_idle"
STOP_CONFIRM_POLLS = 3  # consecutive polls below the stop line before stopping (about 9 s)
PAUSED_CONFIRM_POLLS = 5  # polls without any portfolio value: OctoBot paused itself (backup stop)
# Nothing trades while OctoBot is offline. A larger equity jump across a gap means OctoBot reset
# the paper account to the profile's starting funds (seen after container re-creation), not a loss.
RESET_JUMP = 0.05


def now_ms() -> int:
    return int(time.time() * 1000)


def backup_pct(stop_pct: float) -> float:
    return round(abs(stop_pct) + max(1.0, abs(stop_pct) * 0.5), 2)


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
            "baseline": None,   # {"equity", "ts", "scheme"}
            "stopped": None,    # {"ts", "equity", "baseline", "pct", "scheme"}
            "lastSwitch": None, "lastSwitchTo": None,
        }
        self._stopping = False
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
        self.cfg.update(data)
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
        if self._connected_at and now_ms() - self._connected_at < 60 * 1000:
            return "账户资产估值正在初始化，请在连接稳定 1 分钟后启动方案或切换策略"
        return None

    async def switch(self, pid: str, by: str = "user") -> str:
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
        await octo.select_profile(pid)
        selected = (await asyncio.to_thread(octo.read_active_profile)).get("id")
        if selected != pid:
            raise ValueError("OctoBot 没有接受这个策略：新加入的策略需要先重启一次 OctoBot 才能被识别")
        await octo.restart_bot()
        self.switching = {"from": current, "to": pid, "label": target["label"], "by": by,
                          "since": now_ms(), "octoSince": c.link_octobot.get("since")}
        self.cfg.update(lastSwitch=now_ms(), lastSwitchTo=pid)
        self.save()
        who = {"auto": "自动切换", "scheme": "启动方案，切换"}.get(by, "手动切换")
        c.add_log("risk", f"{who}策略：{c.profile.get('label') or current} → {target['label']}，OctoBot 正在重启")
        return f"已切换到 {target['label']}，OctoBot 重启需要 1–3 分钟"

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
                        baseline={"equity": equity, "ts": now_ms(), "scheme": scheme} if equity else None)
        self.save()
        self._automation_key = None  # push the safety net again

    async def start_scheme(self, scheme: str, ai_profile: str | None = None, stop_pct=None) -> str:
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
        if stop_pct is not None:
            self.set_stop_loss(scheme, stop_pct)
        if ai_profile is not None:
            self.cfg["aiTradeProfile"] = ai_profile
        self._arm(scheme)
        pct = self.cfg["stopLoss"].get(scheme, 0)
        self.c.add_log("risk", f"启动方案「{SCHEMES[scheme]}」，最大止损 {pct:g}%" if pct else
                       f"启动方案「{SCHEMES[scheme]}」，未设置止损")
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

    def set_stop_loss(self, scheme: str, pct: float) -> None:
        if scheme not in SCHEMES:
            raise ValueError("没有这个方案")
        if isinstance(pct, bool):
            raise ValueError("止损比例需要是数字")
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            raise ValueError("止损比例需要是数字") from None
        if not 0 <= pct <= 90:
            raise ValueError("止损比例应在 0–90% 之间（0 表示不止损）")
        self.cfg["stopLoss"][scheme] = round(pct, 2)
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
        try:
            await self.switch(state["target"], by="auto")
        except (ValueError, octo.OctoBotError) as err:
            self.c.add_log("warn", f"自动切换失败：{err}")

    # ---------------------------------------------------------- stop loss ---
    def stop_line(self) -> float | None:
        base, pct = self.cfg.get("baseline"), self.cfg["stopLoss"].get(self.cfg["scheme"], 0)
        if not base or not base.get("equity") or not pct:
            return None
        return base["equity"] * (1 - pct / 100)

    async def on_octobot_sync(self, fresh_connection: bool) -> None:
        """Called after each successful OctoBot poll."""
        c = self.c
        live = c.oct_equity_live
        if fresh_connection:
            self._connected_at = now_ms()
            self._gap_check = self._equity_before_gap is not None
        settling = now_ms() - self._connected_at < 60 * 1000
        if live and not settling:
            if self._gap_check:
                self._gap_check = False
                self._check_paper_reset(self._equity_before_gap, live)
            self._equity_before_gap = live
            if now_ms() - (self.cfg.get("lastEquity") or {}).get("ts", 0) >= 60 * 1000:
                self.cfg["lastEquity"] = {"equity": live, "ts": now_ms()}
                self.save()
        if self.cfg["baseline"] is None and live and c._mode() == "paper" and not settling:
            self.cfg["baseline"] = {"equity": live, "ts": now_ms(), "scheme": self.cfg["scheme"]}
            self.save()
        line = self.stop_line()
        breach = bool(line and live and live <= line)
        # right after a (re)start OctoBot may value the portfolio before every price is known:
        # ignore the first minute and require the breach on several polls in a row
        self._breaches = self._breaches + 1 if breach and not settling else 0
        armed = bool(line) and not self.cfg.get("stopped") and not self._stopping and c._mode() == "paper"
        if self._breaches >= STOP_CONFIRM_POLLS and armed:
            self._stopping = True  # set before the task starts so the next poll does not fire twice
            self._stop_task = asyncio.create_task(self.execute_stop())
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
        self.save()
        self._breaches = 0
        self.c.add_log("risk", f"OctoBot 重启前后模拟资金从 {before:.2f} 变为 {after:.2f}（{change:+.1%}），离线期间不会交易，"
                               f"判断为 OctoBot 重置了模拟资金而不是亏损；止损起点由 {old:.2f} 改为 {after:.2f}")

    async def _sync_safety_net(self) -> None:
        c = self.c
        pct = self.cfg["stopLoss"].get(self.cfg["scheme"], 0) if not self.cfg.get("stopped") else 0
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

    async def record_backup_stop(self) -> None:
        """OctoBot's own automation already cancelled, sold and paused: record it and park on 观望."""
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

    async def execute_stop(self, reason: str | None = None) -> None:
        """Cancel, sell everything, verify it really happened, then pause and park on 观望."""
        c = self.c
        self._stopping = True
        base = self.cfg.get("baseline") or {}
        pct = self.cfg["stopLoss"].get(self.cfg["scheme"], 0)
        quote = c.profile.get("referenceMarket", "USDT")
        equity = c.oct_equity_live or c.oct_equity
        loss = (1 - equity / base["equity"]) * 100 if equity and base.get("equity") else None
        c.add_log("risk", reason or f"触发止损：权益 {equity:.2f} 低于止损线（方案启动时 {base.get('equity', 0):.2f}，"
                                    f"最大亏损 {pct:g}%，当前 −{loss:.2f}%）。正在撤单、卖出全部持币并停止交易")
        self.cfg["stopped"] = {"ts": now_ms(), "equity": equity, "baseline": base.get("equity"),
                               "pct": pct, "loss": loss, "scheme": self.cfg["scheme"], "verified": None}
        self.save()
        status = {"known": False, "orders": None, "leftovers": {}}
        try:
            # 1. cancel and sell every second (also undoes orders a strategy re-creates)
            await octo.save_automations(automation_config(immediate=True, actions=CLEAR_ACTIONS))
            await octo.start_automations()
            clean_polls, deadline = 0, now_ms() + CLEAR_TIMEOUT * 1000
            while now_ms() < deadline:
                await asyncio.sleep(2)
                status = await self._liquidation_status(quote)
                clean = status["known"] and not status["orders"] and not status["leftovers"]
                clean_polls = clean_polls + 1 if clean else 0
                if clean_polls >= 2:
                    break
            # 2. pause trading whatever the outcome, so a failed sale cannot keep trading
            await octo.save_automations(automation_config(immediate=True, actions=PAUSE_ACTIONS))
            await octo.start_automations()
            await asyncio.sleep(3)
            await octo.save_automations(automation_config())
            await octo.start_automations()
            await octo.select_profile(IDLE_PROFILE)  # a later restart must not resume trading
            self._automation_key = None
        except octo.OctoBotError as err:
            self.cfg["stopped"].update(verified=False, problem=f"止损执行出错：{err}")
            self.save()
            c.add_log("risk", f"止损执行出错：{err}。请立即在 OctoBot 中手动撤单、卖出并停止策略")
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
            c.add_log("risk", f"止损完成并已核实：挂单已撤销，只剩 {quote}（{status['equity']:.2f}），交易已暂停；"
                              "OctoBot 下次启动将使用「观望」")
        else:
            c.add_log("risk", f"止损未完全执行：{problem}。交易已暂停，请立即到 OctoBot 检查并手动处理")
        self._stopping = False

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
            "baseline": cfg.get("baseline"),
            "stopLine": self.stop_line(),
            "drawdown": (1 - equity / base) * 100 if equity and base else None,
            "stopped": cfg.get("stopped"),
            "stopping": self._stopping,
            "auto": self.auto_state(),
            "ai": {"available": llm.available(), "model": settings.llm_model, "verdict": self.ai_regime,
                   "error": self.ai_error, "periodMin": round(settings.ai_regime_period / 60)},
        }
