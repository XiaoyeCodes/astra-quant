"""Bitget market and account data through ccxt (async).

Public data (ticker, order book, candles) needs no key. Account data needs an
API key; a READ-ONLY key is enough and is what we recommend for the bridge.
The bridge never places orders on Bitget: trading is done by OctoBot.
"""
from __future__ import annotations

import time
from typing import Any

from .config import settings

try:  # ccxt is installed in the Docker image; optional for local UI work
    import ccxt.async_support as ccxt  # type: ignore
except Exception:  # pragma: no cover
    ccxt = None


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class BitgetClient:
    def __init__(self) -> None:
        self.ex = None
        self.available = ccxt is not None
        self.markets_loaded = False
        self.last_error: str | None = None
        self.latency_ms: float | None = None

    async def start(self) -> None:
        if not self.available:
            self.last_error = "ccxt not installed"
            return
        params: dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": 10000,
            "options": {"defaultType": "spot"},
        }
        if settings.bitget_private:
            params.update(
                apiKey=settings.bitget_key,
                secret=settings.bitget_secret,
                password=settings.bitget_passphrase,
            )
        self.ex = ccxt.bitget(params)
        if settings.bitget_proxy:
            self.ex.httpsProxy = settings.bitget_proxy

    async def close(self) -> None:
        if self.ex is not None:
            try:
                await self.ex.close()
            except Exception:
                pass

    async def ensure_markets(self) -> bool:
        if self.ex is None:
            return False
        if not self.markets_loaded:
            await self.ex.load_markets()
            self.markets_loaded = True
        return True

    def has_symbol(self, symbol: str) -> bool:
        return bool(self.ex is not None and self.markets_loaded and symbol in self.ex.markets)

    # ------------------------------------------------------------- public ---
    async def ticker(self, symbol: str) -> dict:
        t0 = time.perf_counter()
        t = await self.ex.fetch_ticker(symbol)
        self.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        last = _f(t.get("last"))
        open_ = _f(t.get("open"), last)
        return {
            "symbol": symbol,
            "last": last,
            "open24": open_,
            "change": last - open_,
            "changePct": _f(t.get("percentage"), ((last - open_) / open_ * 100) if open_ else 0.0),
            "high": _f(t.get("high"), last),
            "low": _f(t.get("low"), last),
            "quoteVolume": _f(t.get("quoteVolume")),
            "bid": _f(t.get("bid"), last),
            "ask": _f(t.get("ask"), last),
            "ts": t.get("timestamp") or int(time.time() * 1000),
        }

    async def order_book(self, symbol: str, depth: int = 20) -> dict:
        ob = await self.ex.fetch_order_book(symbol, depth)
        return {
            "bids": [[_f(p), _f(a)] for p, a, *_ in ob.get("bids", [])[:depth]],
            "asks": [[_f(p), _f(a)] for p, a, *_ in ob.get("asks", [])[:depth]],
        }

    async def candles(self, symbol: str, timeframe: str = "1m", limit: int = 200) -> list:
        rows = await self.ex.fetch_ohlcv(symbol, timeframe, limit=limit)
        return [[int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5])] for r in rows]

    async def last_prices(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        symbols = [s for s in symbols if self.has_symbol(s)]
        if not symbols:
            return out
        spot = [s for s in symbols if ":" not in s]
        swap = [s for s in symbols if ":" in s]
        for group in (spot, swap):
            if not group:
                continue
            tickers = await self.ex.fetch_tickers(group)
            for sym, t in tickers.items():
                out[sym] = _f(t.get("last"))
        return out

    # ------------------------------------------------------------ private ---
    async def account(self, watch: list[str]) -> dict:
        """Balances, futures positions, open orders and recent fills."""
        result: dict[str, Any] = {"spot": {}, "swap": {}, "positions": [], "orders": [], "trades": [], "errors": []}

        try:
            bal = await self.ex.fetch_balance({"type": "spot"})
            result["spot"] = {
                cur: {"free": _f(v.get("free")), "used": _f(v.get("used")), "total": _f(v.get("total"))}
                for cur, v in bal.items()
                if isinstance(v, dict) and _f(v.get("total")) > 0
            }
        except Exception as err:  # keep going with the rest
            result["errors"].append(f"spot balance: {err}")

        try:
            bal = await self.ex.fetch_balance({"type": "swap"})
            result["swap"] = {
                cur: {"free": _f(v.get("free")), "used": _f(v.get("used")), "total": _f(v.get("total"))}
                for cur, v in bal.items()
                if isinstance(v, dict) and _f(v.get("total")) > 0
            }
        except Exception as err:
            result["errors"].append(f"swap balance: {err}")

        try:
            for p in await self.ex.fetch_positions():
                size = _f(p.get("contracts")) * _f(p.get("contractSize"), 1.0)
                if size == 0:
                    continue
                result["positions"].append({
                    "symbol": p.get("symbol"),
                    "side": p.get("side") or "long",
                    "size": size,
                    "entry": _f(p.get("entryPrice")),
                    "mark": _f(p.get("markPrice")),
                    "liq": _f(p.get("liquidationPrice")),
                    "margin": _f(p.get("initialMargin") or p.get("collateral")),
                    "upnl": _f(p.get("unrealizedPnl")),
                    "leverage": _f(p.get("leverage")),
                    "source": "Bitget",
                })
        except Exception as err:
            result["errors"].append(f"positions: {err}")

        for symbol in watch:
            if not self.has_symbol(symbol):
                continue
            try:
                for o in await self.ex.fetch_open_orders(symbol):
                    result["orders"].append({
                        "id": str(o.get("id")),
                        "symbol": symbol,
                        "side": o.get("side") or "",
                        "type": (o.get("type") or "").upper(),
                        "price": _f(o.get("price") or o.get("triggerPrice")),
                        "amount": _f(o.get("amount")),
                        "time": o.get("timestamp"),
                        "source": "Bitget",
                    })
            except Exception as err:
                result["errors"].append(f"orders {symbol}: {err}")
            try:
                for t in await self.ex.fetch_my_trades(symbol, limit=50):
                    fee = t.get("fee") or {}
                    result["trades"].append({
                        "id": str(t.get("id")),
                        "symbol": symbol,
                        "side": t.get("side") or "",
                        "price": _f(t.get("price")),
                        "amount": _f(t.get("amount")),
                        "cost": _f(t.get("cost")),
                        "fee": _f(fee.get("cost")),
                        "time": t.get("timestamp"),
                        "source": "Bitget",
                    })
            except Exception as err:
                result["errors"].append(f"trades {symbol}: {err}")
        return result
