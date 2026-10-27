"""Candle-based backtester for the ASTRA strategy families, with fees and slippage.

Deliberately conservative so results lean pessimistic:
* a limit order fills only when a candle trades *through* its price (touching is not enough),
  at the limit price with the maker fee;
* market and stop orders pay the taker fee plus slippage; a stop that gaps fills at the open;
* an order placed while processing a candle can only fill from the next candle on, so no
  strategy gets a same-candle round trip for free.

The strategies re-implement the rules of the ASTRA presets transparently; they are not
OctoBot's own code, so treat results as evidence about the rules, not about OctoBot.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import regime as radar

HOURS_PER_YEAR = 24 * 365


@dataclass
class Costs:
    maker: float = 0.001      # Bitget spot default 0.1%
    taker: float = 0.001
    slippage: float = 0.0005  # 5 bps on market / stop fills


@dataclass
class Order:
    side: str        # "buy" | "sell"
    kind: str        # "limit" | "stop"
    price: float
    qty: float       # in base currency
    tag: str = ""
    born: int = 0    # candle index it was placed on; it may fill from born + 1


@dataclass
class Fill:
    i: int
    side: str
    price: float
    qty: float
    fee: float
    tag: str


class Account:
    """Spot account: quote cash plus base holdings per symbol. Fees are charged in quote."""

    def __init__(self, cash: float, costs: Costs) -> None:
        self.cash = cash
        self.qty: dict[str, float] = {}
        self.costs = costs
        self.fees = 0.0
        self.fills: list[Fill] = []

    def _trade(self, i, sym, side, price, qty, fee_rate, tag) -> Fill | None:
        if qty <= 1e-12:
            return None
        notional = price * qty
        fee = notional * fee_rate
        if side == "buy":
            if notional + fee > self.cash + 1e-9:  # never go negative: shrink to what cash allows
                qty = self.cash / (price * (1 + fee_rate))
                notional, fee = price * qty, price * qty * fee_rate
                if qty <= 1e-12:
                    return None
            self.cash -= notional + fee
            self.qty[sym] = self.qty.get(sym, 0.0) + qty
        else:
            qty = min(qty, self.qty.get(sym, 0.0))
            if qty <= 1e-12:
                return None
            notional, fee = price * qty, price * qty * fee_rate
            self.cash += notional - fee
            self.qty[sym] = self.qty.get(sym, 0.0) - qty
        self.fees += fee
        fill = Fill(i, side, price, qty, fee, tag)
        self.fills.append(fill)
        return fill

    def market(self, i, sym, side, qty, price, tag="market") -> Fill | None:
        slip = self.costs.slippage
        px = price * (1 + slip) if side == "buy" else price * (1 - slip)
        return self._trade(i, sym, side, px, qty, self.costs.taker, tag)

    def market_quote(self, i, sym, quote, price, tag="market") -> Fill | None:
        px = price * (1 + self.costs.slippage)
        return self._trade(i, sym, "buy", px, quote / (px * (1 + self.costs.taker)), self.costs.taker, tag)

    def limit(self, i, sym, order: Order, at: float | None = None) -> Fill | None:
        """Resting limit: limit price, maker fee. If the market was already past it (`at`, a
        marketable order) it takes liquidity: fills at the market with the taker fee."""
        if at is not None and ((order.side == "buy" and at <= order.price) or (order.side == "sell" and at >= order.price)):
            return self._trade(i, sym, order.side, at, order.qty, self.costs.taker, order.tag)
        return self._trade(i, sym, order.side, order.price, order.qty, self.costs.maker, order.tag)

    def stop(self, i, sym, order: Order, open_price: float) -> Fill | None:
        trigger = min(order.price, open_price) if order.side == "sell" else max(order.price, open_price)
        return self.market(i, sym, order.side, order.qty, trigger, order.tag)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(q * prices.get(s, 0.0) for s, q in self.qty.items())


class Context:
    """What a strategy sees: the account, its resting orders on the main symbol, candles so far."""

    def __init__(self, account: Account, symbol: str, candles: list, others: dict[str, list] | None = None):
        self.account, self.symbol, self.candles = account, symbol, candles
        self.others = others or {}
        self.orders: list[Order] = []
        self.i = 0

    @property
    def close(self) -> float:
        return self.candles[self.i][4]

    def prices(self) -> dict[str, float]:
        out = {self.symbol: self.close}
        for sym, rows in self.others.items():
            out[sym] = rows[self.i][4]
        return out

    def place(self, side, kind, price, qty, tag="") -> Order:
        order = Order(side, kind, price, qty, tag, self.i)
        self.orders.append(order)
        return order

    def cancel(self, pred=lambda o: True) -> None:
        self.orders = [o for o in self.orders if not pred(o)]

    def held(self) -> float:
        return self.account.qty.get(self.symbol, 0.0)

    def equity(self) -> float:
        return self.account.equity(self.prices())

    def liquidate(self, tag="flatten") -> None:
        """Sell every coin held at market (the handover rule 'flatten')."""
        for sym, q in list(self.account.qty.items()):
            if q > 1e-12:
                price = self.close if sym == self.symbol else self.others[sym][self.i][4]
                self.account.market(self.i, sym, "sell", q, price, tag)


class Strategy:
    name = "strategy"

    def start(self, ctx: Context) -> None:
        pass

    def on_fill(self, ctx: Context, order: Order, fill: Fill) -> None:
        pass

    def on_candle(self, ctx: Context) -> None:
        pass

    def stop(self, ctx: Context) -> None:
        ctx.cancel()


def _path(candle) -> list[float]:
    """Price path inside a candle: open, then the nearer extreme first, then the other, then close."""
    _, o, h, l, c = candle[:5]
    return [o, l, h, c] if c >= o else [o, h, l, c]


def run(strategy: Strategy, candles: list, cash: float = 10_000.0, costs: Costs | None = None,
        symbol: str = "BTC/USDT", others: dict[str, list] | None = None, warmup: int = 0,
        end: int | None = None) -> dict:
    """Run one strategy over candles[warmup:end] ([[ts, o, h, l, c, v], ...]). Earlier candles are
    visible to indicators (warm-up) but never traded; returns metrics and the equity curve."""
    end = len(candles) if end is None else end
    account = Account(cash, costs or Costs())
    ctx = Context(account, symbol, candles, others)
    ctx.i = warmup
    strategy.start(ctx)
    curve = []
    exposure = 0.0
    for i in range(warmup, end):
        ctx.i = i
        candle = candles[i]
        path = _path(candle)
        # resting orders (placed before this candle) fill as the price path reaches them
        for a, b in zip(path, path[1:]):
            lo, hi = min(a, b), max(a, b)
            for order in [o for o in ctx.orders if o.born < i]:
                if order not in ctx.orders:
                    continue  # cancelled by an earlier fill in this candle
                hit = False
                if order.kind == "limit":
                    hit = (order.side == "buy" and lo < order.price) or (order.side == "sell" and hi > order.price)
                elif order.kind == "stop":
                    hit = (order.side == "sell" and lo <= order.price) or (order.side == "buy" and hi >= order.price)
                if not hit:
                    continue
                ctx.orders.remove(order)
                fill = account.limit(i, symbol, order, a) if order.kind == "limit" else account.stop(i, symbol, order, a)
                if fill:
                    strategy.on_fill(ctx, order, fill)
        strategy.on_candle(ctx)
        prices = ctx.prices()
        eq = account.equity(prices)
        curve.append((candle[0], eq))
        exposure += (eq - account.cash) / eq if eq > 0 else 0
    result = metrics(curve, account, candles[warmup][4], candles[end - 1][4])
    result.update(name=strategy.name, exposure=exposure / max(1, len(curve)), curve=curve)
    return result


def metrics(curve: list, account: Account, first_price: float, last_price: float) -> dict:
    equities = [e for _, e in curve]
    start, end = equities[0], equities[-1]
    peak, max_dd = equities[0], 0.0
    for e in equities:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak if peak > 0 else 0)
    rets = [b / a - 1 for a, b in zip(equities, equities[1:]) if a > 0]
    mean = sum(rets) / len(rets) if rets else 0.0
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) if rets else 0.0
    years = max(1e-9, (curve[-1][0] - curve[0][0]) / (HOURS_PER_YEAR * 3600_000))
    total = end / start - 1 if start > 0 else 0.0
    if years < 1 / 12:
        cagr = None  # annualising less than a month says nothing
    else:
        cagr = (end / start) ** (1 / years) - 1 if start > 0 and end > 0 else -1.0
    return {
        "start": start, "end": end, "totalReturn": total, "cagr": cagr, "maxDrawdown": max_dd,
        "sharpe": mean / sd * math.sqrt(HOURS_PER_YEAR) if sd > 0 else 0.0,
        "calmar": cagr / max_dd if cagr is not None and max_dd > 0 else None,
        "fees": account.fees, "trades": len(account.fills),
        "feeShare": account.fees / start if start > 0 else 0.0,
        "years": years, "marketReturn": last_price / first_price - 1,
    }


# ---------------------------------------------------------------- strategies ---

class HoldBTC(Strategy):
    name = "持有 BTC（基准）"

    def start(self, ctx):
        ctx.account.market_quote(ctx.i, ctx.symbol, ctx.account.cash, ctx.close, "buy-hold")


class Idle(Strategy):
    name = "观望（全现金）"


class Grid(Strategy):
    """Symmetric grid: N buy levels below and N sell levels above, mirrored on fills.

    Starts with half of the equity in coins (like the ASTRA grid preset holding BTC + USDT).
    trailing=True rebuilds the grid around the price when it leaves the range upwards
    (the 'trailing up' option); the rebuild re-balances to 50/50 at market, paying fees.
    """

    def __init__(self, levels=8, spacing=0.005, trailing=False, name=None):
        self.levels, self.spacing, self.trailing = levels, spacing, trailing
        self.name = name or (f"移动网格 {levels}×{spacing:.2%}" if trailing else f"现货网格 {levels}×{spacing:.2%}")

    def start(self, ctx):
        self._build(ctx)

    def _build(self, ctx):
        ctx.cancel()
        price, eq = ctx.close, ctx.equity()
        coin_value = ctx.held() * price
        if coin_value < eq / 2:
            ctx.account.market_quote(ctx.i, ctx.symbol, eq / 2 - coin_value, price, "grid-setup")
        elif coin_value > eq / 2:
            ctx.account.market(ctx.i, ctx.symbol, "sell", (coin_value - eq / 2) / price, price, "grid-setup")
        self.center, self.step = price, price * self.spacing
        quote_per = ctx.account.cash / self.levels
        qty_per = ctx.held() / self.levels
        for k in range(1, self.levels + 1):
            buy = price - k * self.step
            ctx.place("buy", "limit", buy, quote_per / buy, f"grid:{-k}")
            ctx.place("sell", "limit", price + k * self.step, qty_per, f"grid:{k}")
        self.top = price + self.levels * self.step

    def on_fill(self, ctx, order, fill):
        level = int(order.tag.split(":")[1])
        if order.side == "buy":
            ctx.place("sell", "limit", order.price + self.step, fill.qty, f"grid:{level + 1}")
        else:
            ctx.place("buy", "limit", order.price - self.step, fill.qty, f"grid:{level - 1}")

    def on_candle(self, ctx):
        if self.trailing and ctx.close > self.top + self.step and not any(o.side == "sell" for o in ctx.orders):
            self._build(ctx)


class DCA(Strategy):
    """ASTRA 分批补仓: every `every` candles, a limit entry slightly below the price plus
    `secondaries` deeper entries; one take profit and one stop loss on the averaged position.
    Unfilled entries are cancelled at each new round (cancel_open_orders_at_each_entry)."""

    def __init__(self, every=4, entry=0.04, entry_off=0.002, secondaries=3, step=0.015, sec_size=0.06,
                 tp=0.012, sl=0.06, max_hold=1.0, name="分批补仓（ASTRA 参数）"):
        self.every, self.entry, self.entry_off = every, entry, entry_off
        self.secondaries, self.step, self.sec_size = secondaries, step, sec_size
        self.tp, self.sl, self.max_hold, self.name = tp, sl, max_hold, name
        self.cost = 0.0

    def start(self, ctx):
        self.last = ctx.i - self.every

    def _exits(self, ctx):
        ctx.cancel(lambda o: o.side == "sell")
        q = ctx.held()
        if q > 1e-12:
            avg = self.cost / q
            ctx.place("sell", "limit", avg * (1 + self.tp), q, "tp")
            ctx.place("sell", "stop", avg * (1 - self.sl), q, "sl")

    def on_fill(self, ctx, order, fill):
        if order.side == "buy":
            self.cost += fill.price * fill.qty + fill.fee
            self._exits(ctx)
        else:  # take profit or stop loss closed the whole position
            self.cost = 0.0
            ctx.cancel()

    def on_candle(self, ctx):
        if ctx.i - self.last < self.every:
            return
        self.last = ctx.i
        ctx.cancel(lambda o: o.side == "buy")
        eq = ctx.equity()
        if ctx.held() * ctx.close >= eq * self.max_hold:
            return
        entry = ctx.close * (1 - self.entry_off)
        ctx.place("buy", "limit", entry, eq * self.entry / entry, "entry")
        for k in range(1, self.secondaries + 1):
            px = entry * (1 - self.step * k)
            ctx.place("buy", "limit", px, eq * self.sec_size / px, f"dca{k}")


def ema_series(values, period):
    return radar.ema(values, period)


class Trend(Strategy):
    """Trend following, close to DailyTrading's intent: long when EMA fast > EMA slow and RSI is
    not overbought; exit on the opposite cross or an ATR trailing stop. All-in / all-out."""

    def __init__(self, fast=50, slow=200, rsi_max=70, atr_mult=3.0, name="趋势跟随 EMA50/200"):
        self.fast, self.slow, self.rsi_max, self.atr_mult, self.name = fast, slow, rsi_max, atr_mult, name

    def start(self, ctx):
        closes = [c[4] for c in ctx.candles]
        self.ef, self.es = ema_series(closes, self.fast), ema_series(closes, self.slow)
        self.rsi = rsi_series(closes, 14)
        highs, lows = [c[2] for c in ctx.candles], [c[3] for c in ctx.candles]
        self.atr = atr_series(highs, lows, closes, 14)
        self.trail = None

    def on_fill(self, ctx, order, fill):
        if order.tag == "trail":
            self.trail = None

    def on_candle(self, ctx):
        i = ctx.i
        if i < self.slow:
            return
        up = self.ef[i] > self.es[i]
        crossed_up = up and self.ef[i - 1] <= self.es[i - 1]
        held = ctx.held() > 1e-12
        # enter only on a fresh cross: after a stop-out, wait for the next trend instead of churning
        if not held and crossed_up and self.rsi[i] < self.rsi_max:
            ctx.account.market_quote(i, ctx.symbol, ctx.account.cash, ctx.close, "trend-in")
            self.trail = ctx.close - self.atr[i] * self.atr_mult
        elif held and not up:
            ctx.cancel()
            ctx.account.market(i, ctx.symbol, "sell", ctx.held(), ctx.close, "trend-out")
            self.trail = None
        elif held:
            self.trail = max(self.trail or 0, ctx.close - self.atr[i] * self.atr_mult)
            ctx.cancel()
            ctx.place("sell", "stop", self.trail, ctx.held(), "trail")


def rsi_series(closes, period=14):
    out = [50.0] * len(closes)
    gain = loss = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0), max(-d, 0)
        if i <= period:
            gain += g / period
            loss += l / period
        else:
            gain = (gain * (period - 1) + g) / period
            loss = (loss * (period - 1) + l) / period
        out[i] = 100 - 100 / (1 + gain / loss) if loss > 0 else 100.0
    return out


def atr_series(highs, lows, closes, period=14):
    out = [0.0] * len(closes)
    atr = None
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        atr = tr if atr is None else (atr * (period - 1) + tr) / period
        out[i] = atr
    return out


class Index(Strategy):
    """Hold a weighted basket and rebalance when any weight drifts more than `band` (checked daily)."""

    def __init__(self, weights: dict[str, float], band=0.05, every=24, name="指数持仓 BTC/ETH/SOL 50/30/20"):
        self.weights, self.band, self.every, self.name = weights, band, every, name

    def _rebalance(self, ctx, force=False):
        prices, eq = ctx.prices(), ctx.equity()
        drift = {s: ctx.account.qty.get(s, 0.0) * prices[s] / eq - w for s, w in self.weights.items()}
        if not force and max(abs(d) for d in drift.values()) <= self.band:
            return
        for s, d in drift.items():  # sell overweight first so cash is there for the buys
            if d > 0:
                ctx.account.market(ctx.i, s, "sell", d * eq / prices[s], prices[s], "rebalance")
        for s, d in drift.items():
            if d < 0:
                ctx.account.market_quote(ctx.i, s, min(-d * eq, ctx.account.cash), prices[s], "rebalance")

    def start(self, ctx):
        self._rebalance(ctx, force=True)
        self.last = ctx.i

    def on_candle(self, ctx):
        if ctx.i - self.last >= self.every:
            self.last = ctx.i
            self._rebalance(ctx)


# Strategy each regime maps to in the live system (regime.PREFER first choice), as factories
REGIME_STRATEGIES = {
    "astra_spot_grid": lambda: Grid(8, 0.005),
    "astra_trailing_grid": lambda: Grid(8, 0.005, trailing=True),
    "astra_idle": lambda: Idle(),
    "astra_trend": lambda: Trend(),
    "astra_spot_dca": lambda: DCA(),
}


def regime_verdicts(candles: list, every: int = 1) -> list[str | None]:
    """The live radar's verdict for every candle (computed on the trailing 200 candles)."""
    out: list[str | None] = [None] * len(candles)
    last = None
    for i in range(len(candles)):
        if i >= 199 and i % every == 0:
            v = radar.analyse(candles[i - 199:i + 1])
            last = v["regime"] if v else last
        out[i] = last
    return out


class RegimeSwitch(Strategy):
    """The live 'indicator picks the strategy' scheme: same radar, confirmations and minimum hold.

    handover decides what happens to coins held when switching:
      keep            - the next strategy inherits them ('观望' keeps the exposure: the old behaviour)
      flatten_on_idle - sell everything when switching to 观望, keep otherwise
      flatten         - sell everything on every switch
    """

    def __init__(self, verdicts: list, handover="keep", confirm=3, hold_hours=12, mapping=None, name=None):
        self.verdicts, self.handover, self.confirm, self.hold = verdicts, handover, confirm, hold_hours
        self.mapping = mapping or {r: prefs[0] for r, prefs in radar.PREFER.items()}
        self.name = name or f"指标选策略（交接：{handover}）"
        self.switches = 0

    def start(self, ctx):
        self.current_id, self.current = "astra_idle", Idle()
        self.since, self.streak, self.last = ctx.i - self.hold, 0, None

    def on_fill(self, ctx, order, fill):
        self.current.on_fill(ctx, order, fill)

    def on_candle(self, ctx):
        v = self.verdicts[ctx.i]
        self.streak = self.streak + 1 if v is not None and v == self.last else (1 if v else 0)
        self.last = v
        target = self.mapping.get(v) if v else None
        if target and target != self.current_id and self.streak >= self.confirm and ctx.i - self.since >= self.hold:
            self.current.stop(ctx)
            if self.handover == "flatten" or (self.handover == "flatten_on_idle" and target == "astra_idle"):
                ctx.liquidate("handover")
            self.current_id, self.current = target, REGIME_STRATEGIES[target]()
            self.current.start(ctx)
            self.since = ctx.i
            self.switches += 1
        self.current.on_candle(ctx)


def segments(n: int, parts: int) -> list[tuple[int, int]]:
    size = n // parts
    return [(k * size, (k + 1) * size if k < parts - 1 else n) for k in range(parts)]
