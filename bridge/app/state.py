"""Collects data from OctoBot and Bitget and builds the cockpit state.

Every loop is independent and failure tolerant: if OctoBot or Bitget is not
reachable, the corresponding link is marked down and the rest keeps working.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import time
from typing import Any

from . import octobot_client as octo
from .bitget_client import BitgetClient
from .config import VERSION, settings

log = logging.getLogger("astra")


def now_ms() -> int:
    return int(time.time() * 1000)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _to_ms(ts: Any) -> int | None:
    v = _f(ts, 0)
    if not v:
        return None
    return int(v * 1000) if v < 1e11 else int(v)


def _side_from_type(kind: str) -> str:
    k = (kind or "").upper()
    if "BUY" in k:
        return "buy"
    if "SELL" in k or "STOP" in k or "TAKE" in k:
        return "sell"
    return ""


def fifo_closed(trades: list[dict]) -> list[dict]:
    """Rebuild closed round trips from a fill list (spot, long only).

    Used when OctoBot's PnL history is empty or when reading raw Bitget fills.
    """
    out: list[dict] = []
    books: dict[str, collections.deque] = collections.defaultdict(collections.deque)
    for t in sorted(trades, key=lambda x: x.get("time") or 0):
        sym = t.get("symbol", "")
        if ":" in sym:
            continue
        qty, price = _f(t.get("amount")), _f(t.get("price"))
        if qty <= 0 or price <= 0:
            continue
        fee = _f(t.get("fee"))
        if t.get("side") == "buy":
            books[sym].append([qty, price, fee / qty if qty else 0.0])
            continue
        if t.get("side") != "sell":
            continue
        remaining, cost, fees = qty, 0.0, fee
        while remaining > 1e-12 and books[sym]:
            lot = books[sym][0]
            take = min(lot[0], remaining)
            cost += take * lot[1]
            fees += take * lot[2]
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-12:
                books[sym].popleft()
        matched = qty - remaining
        if matched <= 0:
            continue
        proceeds = matched * price
        pnl = proceeds - cost - fees
        out.append({
            "id": f"fifo-{t.get('id')}",
            "symbol": sym,
            "entry": cost / matched,
            "exit": price,
            "amount": matched,
            "pnl": pnl,
            "pnlPct": pnl / cost * 100 if cost else 0.0,
            "time": t.get("time"),
        })
    return out


class Cockpit:
    def __init__(self) -> None:
        self.bitget = BitgetClient()
        self.focus = settings.default_focus
        self.clients: set = set()
        self.tasks: list[asyncio.Task] = []

        # market
        self.ticker: dict | None = None
        self.book: dict = {"bids": [], "asks": []}
        self.candles: list = []
        self.ticks: collections.deque = collections.deque(maxlen=900)
        self.prices: dict[str, float] = {}

        # links
        self.link_octobot: dict = {"ok": False, "version": None, "since": None, "error": "not checked yet",
                                   "url": settings.octobot_public_url}
        self.link_bitget: dict = {"ok": False, "latencyMs": None, "private": settings.bitget_private,
                                  "error": "not checked yet"}

        # OctoBot
        self.profile: dict = {}
        self.oct_orders: list[dict] = []
        self.oct_trades: list[dict] = []
        self.oct_positions: list[dict] = []
        self.oct_closed: list[dict] = []
        self.oct_equity: float | None = None  # portfolio value reported by OctoBot

        # Bitget account
        self.account: dict = {}

        # history and log
        self.equity_hist: collections.deque = collections.deque(maxlen=5760)
        self.equity_start: float | None = None
        self.equity_profile: str | None = None  # profile the equity history belongs to
        self.log: collections.deque = collections.deque(maxlen=160)
        self._log_seq = 0
        self._seen_trades: set[str] = set()
        self._seen_orders: set[str] = set()
        self._last_fill_ms = 0  # newest fill already reported; OctoBot re-sends old fills after a restart
        self._first_octo_sync = True
        self._last_equity_sample = 0.0
        self._last_persist = 0.0
        self._load_history()

    # ------------------------------------------------------------ helpers ---
    def add_log(self, level: str, msg: str) -> None:
        self._log_seq += 1
        self.log.append({"id": self._log_seq, "t": now_ms(), "level": level, "msg": msg})

    def _history_path(self) -> str:
        return os.path.join(settings.data_dir, "equity_history.json")

    def _load_history(self) -> None:
        try:
            with open(self._history_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.equity_hist.extend(data.get("history", []))
            self.equity_start = data.get("start")
            self.equity_profile = data.get("profile")
        except (OSError, json.JSONDecodeError):
            pass

    def _reset_history_on_profile_change(self) -> None:
        """Equity of different profiles (different starting portfolios) must not be mixed."""
        pid = self.profile.get("id")
        if not pid or pid == self.equity_profile:
            return
        if self.equity_hist:
            self.add_log("info", f"OctoBot 配置切换为 {self.profile.get('name') or pid}，权益曲线重新开始")
        self.equity_profile = pid
        self.equity_hist.clear()
        self.equity_start = None
        self._persist_history()

    def _persist_history(self) -> None:
        try:
            os.makedirs(settings.data_dir, exist_ok=True)
            tmp = self._history_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"start": self.equity_start, "profile": self.equity_profile,
                           "history": list(self.equity_hist)}, fh)
            os.replace(tmp, self._history_path())
        except OSError as err:
            log.warning("cannot save equity history: %s", err)

    def watch_symbols(self) -> list[str]:
        seen: list[str] = []
        for sym in [self.focus, *settings.watch_symbols, *self.profile.get("pairs", []),
                    *(o["symbol"] for o in self.oct_orders), *(p["symbol"] for p in self.oct_positions)]:
            if sym and sym not in seen:
                seen.append(sym)
        return seen[:12]

    def set_focus(self, symbol: str) -> None:
        symbol = (symbol or "").strip()
        if not symbol or symbol == self.focus:
            return
        if self.bitget.markets_loaded and not self.bitget.has_symbol(symbol):
            self.add_log("warn", f"Bitget 没有交易对 {symbol}")
            return
        self.focus = symbol
        self.ticks.clear()
        self.candles = []
        self.book = {"bids": [], "asks": []}
        self.ticker = None
        self.add_log("info", f"关注交易对切换为 {symbol}")

    # -------------------------------------------------------------- loops ---
    async def start(self) -> None:
        await self.bitget.start()
        self.add_log("info", f"桥接服务 v{VERSION} 启动，OctoBot 地址 {settings.octobot_url}")
        if not settings.bitget_private:
            self.add_log("info", "未配置 Bitget API 密钥：只读取公开行情，账户数据来自 OctoBot")
        loops = [self._loop_market, self._loop_candles, self._loop_octobot]
        if settings.bitget_private:
            loops.append(self._loop_private)
        self.tasks = [asyncio.create_task(fn()) for fn in loops]
        self.tasks.append(asyncio.create_task(self._loop_broadcast()))

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        self._persist_history()
        await self.bitget.close()

    async def _loop_market(self) -> None:
        backoff = settings.ticker_period
        while True:
            try:
                if await self.bitget.ensure_markets():
                    sym = self.focus
                    t, ob = await asyncio.gather(self.bitget.ticker(sym), self.bitget.order_book(sym))
                    if sym == self.focus:
                        self.ticker, self.book = t, ob
                        self.ticks.append([now_ms(), t["last"]])
                        self.prices[sym] = t["last"]
                    if not self.link_bitget["ok"]:
                        self.add_log("ok", "Bitget 行情已连接")
                    self.link_bitget.update(ok=True, latencyMs=self.bitget.latency_ms, error=None)
                    backoff = settings.ticker_period
                else:
                    self.link_bitget.update(ok=False, error=self.bitget.last_error)
                    backoff = 30
            except asyncio.CancelledError:
                raise
            except Exception as err:
                msg = str(err)[:180]
                if self.link_bitget["ok"] or self.link_bitget.get("error") != msg:
                    self.add_log("warn", f"Bitget 行情连接失败：{msg}")
                self.link_bitget.update(ok=False, error=msg)
                backoff = min(max(backoff * 2, 2), 30)
            await asyncio.sleep(backoff)

    async def _loop_candles(self) -> None:
        while True:
            try:
                if self.link_bitget["ok"]:
                    self.candles = await self.bitget.candles(self.focus, "1m", 200)
                    others = [s for s in self.watch_symbols() if s != self.focus]
                    self.prices.update(await self.bitget.last_prices(others + self._asset_symbols()))
            except asyncio.CancelledError:
                raise
            except Exception as err:
                log.debug("candles: %s", err)
            await asyncio.sleep(15 if self.candles else 3)

    def _asset_symbols(self) -> list[str]:
        ref = self.profile.get("referenceMarket", "USDT")
        assets = set(self.profile.get("startingPortfolio", {}).keys()) | set(self.account.get("spot", {}).keys())
        return [f"{a}/{ref}" for a in assets if a not in (ref, "USDT", "USDC")]

    async def _loop_octobot(self) -> None:
        tick = 0
        while True:
            try:
                if tick % 5 == 0 or not self.link_octobot["ok"]:
                    since = await octo.ping()
                    if not self.link_octobot["version"]:
                        try:
                            self.link_octobot["version"] = (await octo.version()).strip('"')
                        except octo.OctoBotError:
                            pass
                    self.link_octobot["since"] = since.strip('"')
                    self.profile = await asyncio.to_thread(octo.read_active_profile)
                    self._reset_history_on_profile_change()
                orders, trades, positions = await asyncio.gather(octo.orders(), octo.trades(), octo.positions())
                if tick % 3 == 0:
                    try:
                        self.oct_closed = self._norm_closed(await octo.pnl_history())
                    except octo.OctoBotError:
                        pass
                try:
                    self.oct_equity = await octo.portfolio_value(self.profile.get("referenceMarket", "USDT"))
                except octo.OctoBotError:
                    self.oct_equity = None
                self._ingest_octobot(orders, trades, positions)
                if not self.link_octobot["ok"]:
                    self.add_log("ok", f"OctoBot 已连接 {self.link_octobot.get('version') or ''}".strip())
                self.link_octobot.update(ok=True, error=None)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                msg = str(err)[:180]
                if self.link_octobot["ok"]:
                    self.add_log("warn", f"OctoBot 连接中断：{msg}")
                self.link_octobot.update(ok=False, error=msg)
            tick += 1
            await asyncio.sleep(settings.octobot_period if self.link_octobot["ok"] else 6)

    async def _loop_private(self) -> None:
        while True:
            try:
                if await self.bitget.ensure_markets():
                    self.account = await self.bitget.account(self.watch_symbols())
                    for err in self.account.get("errors", [])[:2]:
                        log.info("bitget private: %s", err)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self.account = {"errors": [str(err)]}
                self.add_log("warn", f"Bitget 账户读取失败：{str(err)[:160]}")
            await asyncio.sleep(settings.private_period)

    async def _loop_broadcast(self) -> None:
        while True:
            try:
                state = self.snapshot()
                self._sample_equity(state["equity"]["value"])
                if self.clients:
                    payload = json.dumps(state, separators=(",", ":"), default=str)
                    dead = []
                    for ws in list(self.clients):
                        try:
                            await ws.send_text(payload)
                        except Exception:
                            dead.append(ws)
                    for ws in dead:
                        self.clients.discard(ws)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("broadcast failed")
            await asyncio.sleep(settings.broadcast_period)

    # ---------------------------------------------------------- OctoBot ----
    def _ingest_octobot(self, orders: list, trades: list, positions: list) -> None:
        norm_orders = []
        for o in orders:
            kind = str(o.get("type", ""))
            norm_orders.append({
                "id": str(o.get("id")),
                "symbol": o.get("symbol", ""),
                "side": _side_from_type(kind),
                "type": kind,
                "price": _f(o.get("price")),
                "amount": _f(o.get("amount")),
                "time": _to_ms(o.get("time")),
                "source": o.get("SoR", "Real"),
            })
        norm_trades = []
        for t in trades:
            kind = str(t.get("type", ""))
            norm_trades.append({
                "id": str(t.get("id")),
                "symbol": t.get("symbol", ""),
                "side": _side_from_type(kind),
                "type": kind,
                "price": _f(t.get("price")),
                "amount": _f(t.get("amount")),
                "cost": _f(t.get("cost")),
                "fee": _f(t.get("fee_cost")),
                "feeCurrency": t.get("fee_currency", ""),
                "time": _to_ms(t.get("time")),
                "source": t.get("SoR", "Real"),
            })
        norm_trades.sort(key=lambda x: x["time"] or 0)
        norm_pos = []
        for p in positions:
            # OctoBot reports short sizes as negative and does not expose the leverage
            value, margin = abs(_f(p.get("value"))), _f(p.get("margin"))
            norm_pos.append({
                "symbol": p.get("symbol", ""),
                "side": str(p.get("side", "")).lower() or "long",
                "size": abs(_f(p.get("amount"))),
                "entry": _f(p.get("entry_price")),
                "mark": 0.0,
                "liq": _f(p.get("liquidation_price")),
                "margin": margin,
                "upnl": _f(p.get("unrealized_pnl")),
                "leverage": round(value / margin, 1) if margin > 0 else 0.0,
                "value": value,
                "source": p.get("SoR", "Real"),
            })

        # events
        order_ids = {o["id"] for o in norm_orders}
        trade_ids = {t["id"] for t in norm_trades}
        if self._first_octo_sync:
            self._first_octo_sync = False
            if norm_orders or norm_trades:
                self.add_log("info", f"同步 OctoBot：{len(norm_orders)} 个挂单，{len(norm_trades)} 笔历史成交")
        else:
            for t in norm_trades:
                if t["id"] not in self._seen_trades and (t["time"] or 0) > self._last_fill_ms:
                    verb = "买入" if t["side"] == "buy" else "卖出"
                    self.add_log("fill", f"{verb}成交 {t['symbol']} {t['amount']:g} @ {t['price']:g}（{t['source']}）")
            new_orders = [o for o in norm_orders if o["id"] not in self._seen_orders]
            gone_ids = self._seen_orders - order_ids
            if new_orders:
                o = new_orders[0]
                extra = f" 等 {len(new_orders)} 个" if len(new_orders) > 1 else ""
                self.add_log("info", f"新挂单 {o['type']} {o['symbol']} @ {o['price']:g}{extra}")
            if gone_ids:
                self.add_log("info", f"{len(gone_ids)} 个挂单已离开订单簿（成交或撤销）")
        self._seen_orders = order_ids
        self._seen_trades = trade_ids
        self._last_fill_ms = max([self._last_fill_ms, *(t["time"] or 0 for t in norm_trades)])
        self.oct_orders, self.oct_trades, self.oct_positions = norm_orders, norm_trades[-400:], norm_pos

    @staticmethod
    def _norm_closed(rows: list) -> list[dict]:
        out = []
        for r in rows:
            d = r.get("d") or {}
            entry, amount = _f(d.get("en_p")), _f(d.get("en_a"))
            pnl = _f(r.get("pnl"))
            basis = entry * amount
            out.append({
                "id": f"pnl-{r.get('ex_t')}",
                "symbol": d.get("s", ""),
                "entry": entry,
                "exit": _f(d.get("ex_p")),
                "amount": amount,
                "pnl": pnl,
                "pnlPct": pnl / basis * 100 if basis else 0.0,
                "time": _to_ms(r.get("ex_t")),
            })
        return out[-500:]

    # ------------------------------------------------------------- state ---
    def _mode(self) -> str:
        if self.link_octobot["ok"]:
            sources = {x.get("source") for x in self.oct_orders + self.oct_trades + self.oct_positions}
            # OctoBot only reports "Virtual" / "Inactive" orders for the real trader
            if sources & {"Real", "Virtual", "Inactive"} or self.profile.get("real"):
                return "live"
            if "Simulated" in sources or self.profile.get("simulated"):
                return "paper"
            return "idle"
        if settings.bitget_private and self.account and not self.account.get("errors"):
            return "watch"
        return "demo"

    def _equity(self, mode: str, positions: list[dict], closed: list[dict], trades: list[dict]) -> float | None:
        ref = self.profile.get("referenceMarket", "USDT")
        if mode in ("live", "watch") and self.account.get("spot") is not None and settings.bitget_private:
            total = 0.0
            for cur, bal in self.account.get("spot", {}).items():
                if cur in ("USDT", "USDC"):
                    total += bal["total"]
                else:
                    px = self.prices.get(f"{cur}/USDT")
                    if px:
                        total += bal["total"] * px
            total += self.account.get("swap", {}).get("USDT", {}).get("total", 0.0)
            return total if total > 0 else None
        if mode == "paper" and self.oct_equity is not None:
            return self.oct_equity
        if mode == "paper" and self.profile.get("startingPortfolio"):
            start = {k: _f(v) for k, v in self.profile["startingPortfolio"].items()}
            if self.profile.get("exchangeType") == "future":
                realized = sum(c["pnl"] for c in closed)
                upnl = sum(p["upnl"] for p in positions)
                return start.get(ref, 0.0) + realized + upnl
            hold = dict(start)
            for t in trades:
                if ":" in t["symbol"] or "/" not in t["symbol"]:
                    continue
                base, quote = t["symbol"].split("/")
                cost = t["cost"] or t["amount"] * t["price"]
                sign = 1 if t["side"] == "buy" else -1
                hold[base] = hold.get(base, 0.0) + sign * t["amount"]
                hold[quote] = hold.get(quote, 0.0) - sign * cost
                if t["fee"]:
                    fee_cur = t.get("feeCurrency") or quote
                    hold[fee_cur] = hold.get(fee_cur, 0.0) - t["fee"]
            total = 0.0
            for cur, qty in hold.items():
                if cur == ref:
                    total += qty
                    continue
                px = self.prices.get(f"{cur}/{ref}")
                if px is None:
                    return None  # prices not loaded yet
                total += qty * px
            return total
        return None

    def _sample_equity(self, value: float | None) -> None:
        if value is None:
            return
        now = time.time()
        if self.equity_start is None:
            self.equity_start = value
        if now - self._last_equity_sample >= 15:
            self._last_equity_sample = now
            self.equity_hist.append([now_ms(), round(value, 4)])
        if now - self._last_persist >= 120:
            self._last_persist = now
            self._persist_history()

    def snapshot(self) -> dict:
        mode = self._mode()
        use_octo = self.link_octobot["ok"]
        orders = self.oct_orders if use_octo else self.account.get("orders", [])
        trades = self.oct_trades if use_octo else self.account.get("trades", [])
        positions = list(self.oct_positions if use_octo and self.oct_positions else self.account.get("positions", []))
        for p in positions:
            if not p.get("mark"):
                p["mark"] = self.prices.get(p["symbol"], 0.0)
        closed = self.oct_closed if (use_octo and self.oct_closed) else fifo_closed(trades)

        equity = self._equity(mode, positions, closed, trades)
        hist = list(self.equity_hist)
        if len(hist) > 480:
            step = len(hist) / 480
            hist = [hist[int(i * step)] for i in range(480)]
        values = [v for _, v in self.equity_hist]
        peak, max_dd = 0.0, 0.0
        for v in values:
            peak = max(peak, v)
            if peak:
                max_dd = max(max_dd, (peak - v) / peak)
        wins = sum(1 for c in closed if c["pnl"] > 0)

        return {
            "v": 1,
            "ts": now_ms(),
            "source": "bridge",
            "mode": mode,
            "controls": settings.enable_controls and self.link_octobot["ok"],
            "links": {
                "bridge": {"ok": True, "version": VERSION},
                "octobot": self.link_octobot,
                "bitget": self.link_bitget,
            },
            "profile": self.profile,
            "focus": {"symbol": self.focus, "type": "swap" if ":" in self.focus else "spot"},
            "symbols": self.watch_symbols(),
            "ticker": self.ticker,
            "book": self.book,
            "candles": self.candles,
            "ticks": list(self.ticks),
            "orders": orders[:200],
            "trades": trades[-200:],
            "positions": positions,
            "closed": closed[-300:],
            "equity": {"value": equity, "currency": self.profile.get("referenceMarket", "USDT"),
                       "start": self.equity_start, "history": hist},
            "stats": {
                "realized": sum(c["pnl"] for c in closed),
                "unrealized": sum(_f(p.get("upnl")) for p in positions),
                "winRate": (wins / len(closed)) if closed else None,
                "closedCount": len(closed),
                "tradeCount": len(trades),
                "maxDD": max_dd,
            },
            "log": list(self.log)[-120:],
        }

    # ----------------------------------------------------------- controls ---
    async def cancel_all(self) -> str:
        ids = [o["id"] for o in self.oct_orders]
        if not ids:
            return "没有可撤销的挂单"
        res = await octo.cancel_orders(ids)
        self.add_log("risk", f"紧急操作：请求 OctoBot 撤销 {len(ids)} 个挂单（{res}）")
        return res

    async def stop_bot(self) -> str:
        res = await octo.stop_bot()
        self.add_log("risk", "紧急操作：已请求 OctoBot 停止运行")
        return res
