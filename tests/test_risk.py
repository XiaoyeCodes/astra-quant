"""Risk rules: maximum loss, trailing drawdown from the peak, and the daily loss limit."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from app import autopilot
from app.autopilot import StrategyDesk, trading_day
from app.config import settings


class FakeCockpit:
    def __init__(self):
        self.oct_equity = self.oct_equity_live = 1000.0
        self.oct_orders = [{"id": "o1"}]
        self.profile = {"id": "astra_spot_grid", "real": False, "simulated": True, "referenceMarket": "USDT"}
        self.link_octobot = {"ok": True, "since": "A"}
        self.logs = []

    def _mode(self):
        return "paper"

    def add_log(self, level, msg):
        self.logs.append((level, msg))


class RiskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = patch.object(settings, "data_dir", self.tmp.name)
        self.p.start()
        self.c = FakeCockpit()
        self.desk = StrategyDesk(self.c)
        self.desk._arm("manual")                       # baseline, peak and day = 1000
        self.desk._connected_at = 0
        self.stops = []

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    async def _poll(self, equity, times=1):
        self.c.oct_equity_live = equity
        with patch.object(self.desk, "execute_stop", self._record), \
                patch.object(self.desk, "_sync_safety_net", self._noop):
            for _ in range(times):
                await self.desk.on_octobot_sync(False)
            await asyncio.sleep(0)

    async def _record(self, reason=None, rule="base"):
        self.stops.append(rule)
        self.desk._stopping = False

    async def _noop(self, *a, **k):
        return None

    async def test_trailing_stop_follows_the_peak(self):
        self.desk.set_risk("manual", stop=20, trail=10)
        await self._poll(1300, 3)                      # new high
        self.assertEqual(self.desk.cfg["peak"]["equity"], 1300)
        self.assertAlmostEqual(self.desk.risk_lines()["trail"], 1170)
        self.assertEqual(self.desk.binding_rule()[0], "trail")   # 1170 is above the 800 base line
        await self._poll(1180, 3)
        self.assertFalse(self.stops)                   # 9.2% below the peak
        await self._poll(1160, 3)
        self.assertEqual(self.stops, ["trail"])        # still +16% overall, but 10.8% off the high

    async def test_daily_limit_uses_the_days_first_equity(self):
        self.desk.set_risk("manual", daily=3)
        self.assertAlmostEqual(self.desk.risk_lines()["daily"], 970)
        await self._poll(975, 3)
        self.assertFalse(self.stops)
        await self._poll(965, 3)
        self.assertEqual(self.stops, ["daily"])

    async def test_daily_anchor_rolls_over_at_beijing_midnight(self):
        self.desk.set_risk("manual", daily=3)
        self.desk.cfg["day"] = {"date": "2000-01-01", "equity": 2000}   # yesterday, much higher
        self.assertIsNone(self.desk.risk_lines()["daily"])               # stale anchor never fires
        await self._poll(1000)
        self.assertEqual(self.desk.cfg["day"], {"date": trading_day(), "equity": 1000})
        self.assertFalse(self.stops)

    async def test_highest_line_binds_and_message_names_the_rule(self):
        self.desk.set_risk("manual", stop=5, trail=0, daily=2)
        self.assertEqual(self.desk.binding_rule(), ("daily", 980.0))
        text, limit, loss = self.desk._stop_message("daily", 975)
        self.assertIn("单日亏损上限", text)
        self.assertEqual(limit, 2)
        self.assertAlmostEqual(loss, 2.5)

    def test_off_rules_have_no_line(self):
        self.desk.set_risk("manual", stop=0, trail=0, daily=0)
        self.assertEqual(self.desk.risk_lines(), {"base": None, "trail": None, "daily": None})
        self.assertEqual(self.desk.binding_rule(), (None, None))

    def test_invalid_values_never_half_apply(self):
        self.desk.set_risk("manual", stop=10, trail=8, daily=3)
        for kwargs in ({"stop": 5, "trail": "x"}, {"daily": 60}, {"trail": True}, {"stop": -1}, {}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.desk.set_risk("manual", **kwargs)
        self.assertEqual((self.desk.cfg["stopLoss"]["manual"], self.desk.cfg["trail"]["manual"],
                          self.desk.cfg["dailyLoss"]["manual"]), (10, 8, 3))

    async def test_paper_reset_rebases_every_anchor(self):
        self.desk.set_risk("manual", stop=10, trail=10, daily=3)
        await self._poll(1200)                         # peak 1200
        self.desk._check_paper_reset(1200, 600)        # OctoBot reset the paper account
        self.assertEqual(self.desk.cfg["baseline"]["equity"], 600)
        self.assertEqual(self.desk.cfg["peak"]["equity"], 600)
        self.assertEqual(self.desk.cfg["day"]["equity"], 600)
        await self._poll(600, 4)
        self.assertFalse(self.stops)

    async def test_stopped_record_names_the_rule(self):
        self.desk.set_risk("manual", trail=10)
        self.desk.cfg["peak"] = {"equity": 1300, "ts": 0}
        fake_status = {"known": True, "orders": 0, "leftovers": {}, "equity": 1150.0}

        async def clear(_quote):
            return fake_status

        async def ok(*_a, **_k):
            return None
        with patch.object(self.desk, "_clear_positions", clear), \
                patch.object(autopilot.octo, "save_automations", ok), \
                patch.object(autopilot.octo, "start_automations", ok), \
                patch.object(autopilot.octo, "select_profile", ok), \
                patch.object(autopilot.octo, "restart_bot", ok), \
                patch.object(autopilot.octo, "paper_snapshot_time", lambda: 9e12), \
                patch.object(autopilot.asyncio, "sleep", ok):
            self.c.oct_equity_live = 1150
            await self.desk.execute_stop(rule="trail")
        stopped = self.desk.cfg["stopped"]
        self.assertEqual((stopped["rule"], stopped["ruleLabel"], stopped["pct"]), ("trail", "回撤止损", 10))
        self.assertTrue(stopped["verified"])
        self.assertIn("回撤止损", self.c.logs[0][1])


if __name__ == "__main__":
    unittest.main()
