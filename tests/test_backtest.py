"""Backtest engine: conservative fills, costs, and the strategy rules behave as documented."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from app import backtest as bt

H = 3600_000


def candles(closes, wick=0.0):
    """Flat-ish candles: open = previous close, high/low = max/min of open/close ± wick."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append([i * H, o, max(o, c) + wick, min(o, c) - wick, c, 1.0])
        prev = c
    return out


class Probe(bt.Strategy):
    """Places one order at a given candle and records fills."""

    def __init__(self, at, side, kind, price, qty):
        self.at, self.args, self.fills = at, (side, kind, price, qty), []

    def start(self, ctx):
        if self.args[0] == "sell":
            ctx.account.market(ctx.i, ctx.symbol, "buy", 1.0, ctx.close)

    def on_candle(self, ctx):
        if ctx.i == self.at:
            ctx.place(*self.args)

    def on_fill(self, ctx, order, fill):
        self.fills.append(fill)


class FillRuleTests(unittest.TestCase):
    def test_touching_a_limit_is_not_enough(self):
        rows = candles([100, 100, 100])
        rows[2] = [2 * H, 100, 101, 99.0, 100, 1]          # low exactly at the limit
        p = Probe(0, "buy", "limit", 99.0, 1)
        bt.run(p, rows, cash=1000, costs=bt.Costs(0, 0, 0))
        self.assertEqual(p.fills, [])
        rows[2][3] = 98.9                                    # trades through
        p = Probe(0, "buy", "limit", 99.0, 1)
        bt.run(p, rows, cash=1000, costs=bt.Costs(0, 0, 0))
        self.assertEqual(len(p.fills), 1)
        self.assertEqual(p.fills[0].price, 99.0)

    def test_no_fill_in_the_candle_the_order_was_placed(self):
        rows = [[0, 100, 100, 100, 100, 1], [H, 100, 100, 94, 99, 1], [2 * H, 99, 99.5, 98, 99, 1]]
        p = Probe(1, "buy", "limit", 95.0, 1)                # placed at candle 1's close; candle 1 traded at 94
        bt.run(p, rows, cash=1000, costs=bt.Costs(0, 0, 0))
        self.assertEqual(p.fills, [])                         # candle 2 never trades through 95

    def test_marketable_limit_takes_the_market_price_and_taker_fee(self):
        rows = [[0, 100, 100, 100, 100, 1], [H, 100, 100, 100, 90, 1], [2 * H, 90, 91, 89, 90, 1]]
        p = Probe(1, "buy", "limit", 95.0, 1)                # limit above the market
        bt.run(p, rows, cash=1000, costs=bt.Costs(maker=0.0, taker=0.002, slippage=0))
        self.assertEqual(p.fills[0].price, 90)
        self.assertAlmostEqual(p.fills[0].fee, 90 * 0.002)

    def test_stop_gap_fills_at_the_open_with_slippage_and_taker_fee(self):
        rows = candles([100, 100, 80])
        rows[2] = [2 * H, 85, 86, 79, 80, 1]                 # gaps below the 95 stop
        p = Probe(1, "sell", "stop", 95.0, 1)
        bt.run(p, rows, cash=1000, costs=bt.Costs(maker=0, taker=0.001, slippage=0.001))
        self.assertAlmostEqual(p.fills[0].price, 85 * 0.999)
        self.assertAlmostEqual(p.fills[0].fee, 85 * 0.999 * 0.001)

    def test_cash_never_goes_negative(self):
        acc = bt.Account(100, bt.Costs())
        acc.market(0, "BTC/USDT", "buy", 10, 50)              # wants 500, has 100
        self.assertGreaterEqual(acc.cash, -1e-9)
        self.assertGreater(acc.qty["BTC/USDT"], 0)


class StrategyTests(unittest.TestCase):
    def test_hold_return_is_price_change_minus_costs(self):
        rows = candles([100, 110, 120])
        r = bt.run(bt.HoldBTC(), rows, cash=1000, costs=bt.Costs(maker=0, taker=0.001, slippage=0.0005))
        expected = 1000 / (100 * 1.0005 * 1.001) * 120
        self.assertAlmostEqual(r["end"], expected, places=6)
        self.assertAlmostEqual(r["marketReturn"], 0.2)

    def test_grid_profits_from_oscillation_and_mirrors_orders(self):
        osc = [100 + 3 * math.sin(i / 3) for i in range(400)]
        r = bt.run(bt.Grid(levels=5, spacing=0.01), candles(osc, wick=0.2), cash=1000)
        flat = bt.run(bt.Grid(levels=5, spacing=0.01), candles([100] * 400), cash=1000)
        self.assertGreater(r["trades"], 20)
        self.assertGreater(r["end"], flat["end"])
        self.assertEqual(flat["trades"], 1)                   # only the initial 50/50 set-up

    def test_dca_exit_rules(self):
        prices = [100] * 5 + [99.5, 98.5, 98, 99, 100, 101, 102, 102] + [102] * 5
        ctx_strategy = bt.DCA(every=4)
        account_fills = []

        class Spy(bt.DCA):
            def on_fill(self, ctx, order, fill):
                account_fills.append(order.tag)
                super().on_fill(ctx, order, fill)
        bt.run(Spy(every=4), candles(prices, wick=0.3), cash=10_000)
        self.assertIn("entry", account_fills)
        self.assertIn("dca1", account_fills)                  # added on the dip
        self.assertIn("tp", account_fills)                    # closed on the rebound
        self.assertNotIn("sl", account_fills)
        self.assertIsNotNone(ctx_strategy)

    def test_handover_rules(self):
        rows = candles([100] * 10)
        verdicts = [None, "range", "range", "range", "down", "down", "down", "down", "down", "down"]
        keep = bt.run(bt.RegimeSwitch(verdicts, "keep", confirm=2, hold_hours=0), rows, cash=1000,
                      costs=bt.Costs(0, 0, 0))
        flat = bt.run(bt.RegimeSwitch(verdicts, "flatten_on_idle", confirm=2, hold_hours=0), rows, cash=1000,
                      costs=bt.Costs(0, 0, 0))
        self.assertGreater(keep["exposure"], flat["exposure"])
        self.assertAlmostEqual(flat["curve"][-1][1], 1000)    # all cash after switching to 观望

    def test_regime_switch_respects_confirmation_and_hold(self):
        rows = candles([100] * 12)
        flip = [None, "range", "down", "range", "down", "range", "down", "range", "down", "range", "down", "range"]
        s = bt.RegimeSwitch(flip, "keep", confirm=2, hold_hours=0)
        bt.run(s, rows, cash=1000)
        self.assertEqual(s.switches, 0)                       # never two identical verdicts in a row
        steady = [None] + ["range"] * 11
        s = bt.RegimeSwitch(steady, "keep", confirm=3, hold_hours=0)
        bt.run(s, rows, cash=1000)
        self.assertEqual(s.switches, 1)

    def test_index_rebalances_to_weights(self):
        btc, eth = candles([100] * 30), candles([10] * 30)
        s = bt.Index({"BTC/USDT": 0.5, "ETH/USDT": 0.5}, band=0.05, every=24)
        r = bt.run(s, btc, cash=1000, symbol="BTC/USDT", others={"ETH/USDT": eth}, costs=bt.Costs(0, 0, 0))
        self.assertAlmostEqual(r["end"], 1000, places=6)
        self.assertGreater(r["exposure"], 0.99)

    def test_metrics(self):
        rows = candles([100, 50, 100])
        r = bt.run(bt.HoldBTC(), rows, cash=1000, costs=bt.Costs(0, 0, 0))
        self.assertAlmostEqual(r["maxDrawdown"], 0.5)
        self.assertAlmostEqual(r["totalReturn"], 0.0, places=9)


if __name__ == "__main__":
    unittest.main()
