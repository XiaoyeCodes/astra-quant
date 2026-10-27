"""Stop-loss: trigger rules, verified liquidation, and honest reporting when it fails."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from app import autopilot, octobot_client as octo
from app.autopilot import StrategyDesk, CLEAR_ACTIONS, PAUSE_ACTIONS
from app.config import settings

PAGE = """<table class="table" id="holdings-table"><tbody>
<tr class="symbol-holding text-center"><td class="row mx-0"><img alt="BTC"><span class="symbol">BTC</span></td>
<td class="align-middle"> 0.0165 </td><td class="total-value">1320.5</td><td>0.0165</td><td>0</td></tr>
<tr class="symbol-holding text-center"><td class="row mx-0"><span class="symbol">USDT</span></td>
<td> 880.1 </td><td class="total-value">880.1</td><td>880.1</td><td>0E-11</td></tr>
</tbody></table>"""


class FakeCockpit:
    def __init__(self):
        self.oct_equity = self.oct_equity_live = 2000.0
        self.oct_orders = [{"id": "o1"}]
        self.profile = {"id": "astra_spot_grid", "real": False, "simulated": True, "referenceMarket": "USDT"}
        self.link_octobot = {"ok": True, "since": "A"}
        self.logs = []

    def _mode(self):
        return "paper"

    def add_log(self, level, msg):
        self.logs.append((level, msg))


class FakeOctoBot:
    """Records automation saves; liquidates once the clear automation has started."""

    def __init__(self, sells=True, holdings_known=True):
        self.calls, self.sells, self.known = [], sells, holdings_known
        self.cleared = False

    async def save_automations(self, cfg):
        actions = [a["actions"] for a in cfg["automations"].values()]
        self.calls.append(("save", actions))
        self.pending = actions

    async def start_automations(self):
        self.calls.append(("start",))
        if CLEAR_ACTIONS in getattr(self, "pending", []):
            self.cleared = True

    async def orders(self):
        return [] if self.cleared else [{"id": "o1"}]

    async def holdings(self):
        if not self.known:
            return None
        if self.cleared and self.sells:
            return {"USDT": {"total": 1990.0, "value": 1990.0}}
        return {"BTC": {"total": 0.0165, "value": 1320.0}, "USDT": {"total": 670.0, "value": 670.0}}

    async def select_profile(self, pid):
        self.calls.append(("select", pid))


class StopLossTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        StopLossTests.stops = []
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(settings, "data_dir", self.tmp.name),
                        patch.object(autopilot.asyncio, "sleep", self._no_sleep)]
        for p in self.patches:
            p.start()
        self.c = FakeCockpit()
        self.desk = StrategyDesk(self.c)
        self.desk.cfg.update(scheme="manual", baseline={"equity": 2000.0, "ts": 0, "scheme": "manual"})
        self.desk.cfg["stopLoss"]["manual"] = 5

    async def _no_sleep(self, _s=0):
        await _REAL_SLEEP(0)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def use(self, fake):
        names = ("save_automations", "start_automations", "orders", "holdings", "select_profile")
        for n in names:
            p = patch.object(octo, n, getattr(fake, n))
            p.start()
            self.patches.append(p)

    def test_parse_holdings(self):
        held = octo.parse_holdings(PAGE)
        self.assertEqual(held["BTC"], {"total": 0.0165, "value": 1320.5})
        self.assertEqual(held["USDT"]["total"], 880.1)
        self.assertIsNone(octo.parse_holdings("<html>paused</html>"))
        self.assertIsNone(octo.parse_holdings('<table id="holdings-table"><tbody></tbody></table>'))

    async def test_verified_stop_sequence(self):
        fake = FakeOctoBot()
        self.use(fake)
        self.c.oct_equity = self.c.oct_equity_live = 1890.0
        await self.desk.execute_stop()
        saves = [c[1] for c in fake.calls if c[0] == "save"]
        self.assertEqual(saves, [[CLEAR_ACTIONS], [], [PAUSE_ACTIONS], []])  # clear, remove, pause, remove
        self.assertEqual(fake.calls[-1], ("select", "astra_idle"))
        stopped = self.desk.cfg["stopped"]
        self.assertTrue(stopped["verified"])
        self.assertIsNone(stopped["problem"])
        self.assertEqual(stopped["finalEquity"], 1990.0)
        self.assertIn("已核实", self.c.logs[-1][1])
        self.assertFalse(self.desk._stopping)

    async def test_unsold_coins_are_reported_not_hidden(self):
        fake = FakeOctoBot(sells=False)
        self.use(fake)
        with patch.object(autopilot, "CLEAR_TIMEOUT", 0.05):
            await self.desk.execute_stop()
        stopped = self.desk.cfg["stopped"]
        self.assertFalse(stopped["verified"])
        self.assertIn("BTC", stopped["problem"])
        self.assertIn("未完全执行", self.c.logs[-1][1])
        # trading is still paused and parked on idle
        self.assertIn(("save", [PAUSE_ACTIONS]), fake.calls)
        self.assertEqual(fake.calls[-1], ("select", "astra_idle"))

    async def test_unreadable_holdings_are_not_called_verified(self):
        fake = FakeOctoBot(holdings_known=False)
        self.use(fake)
        with patch.object(autopilot, "CLEAR_TIMEOUT", 0.05):
            await self.desk.execute_stop()
        self.assertFalse(self.desk.cfg["stopped"]["verified"])
        self.assertIn("无法读取", self.desk.cfg["stopped"]["problem"])

    async def test_octobot_error_is_recorded(self):
        fake = FakeOctoBot()

        async def broken(_cfg):
            raise octo.OctoBotError("unreachable")
        fake.save_automations = broken
        self.use(fake)
        await self.desk.execute_stop()
        self.assertFalse(self.desk.cfg["stopped"]["verified"])
        self.assertIn("unreachable", self.desk.cfg["stopped"]["problem"])
        self.assertFalse(self.desk._stopping)

    async def test_breach_needs_confirmation_and_settled_connection(self):
        self.use(FakeOctoBot())
        with patch.object(self.desk, "execute_stop", self._record_stop), \
                patch.object(self.desk, "_sync_safety_net", self._noop):
            await self.desk.on_octobot_sync(True)            # fresh connection: settling minute
            self.c.oct_equity_live = 1000.0                   # bogus half valuation during start-up
            for _ in range(5):
                await self.desk.on_octobot_sync(False)
            self.assertFalse(self.stops)
            self.desk._connected_at = 0                       # settling over
            self.c.oct_equity_live = 1899.0
            await self.desk.on_octobot_sync(False)
            await self.desk.on_octobot_sync(False)
            self.assertFalse(self.stops)                      # only 2 polls below the line
            await self.desk.on_octobot_sync(False)
            await asyncio.sleep(0)
            self.assertEqual(len(self.stops), 1)

    async def test_backup_stop_detected_when_octobot_paused_itself(self):
        fake = FakeOctoBot()
        self.use(fake)
        self.desk._connected_at = 0
        self.c.oct_equity_live, self.c.oct_orders = None, []
        with patch.object(self.desk, "_sync_safety_net", self._noop):
            for _ in range(5):
                await self.desk.on_octobot_sync(False)
            await self.desk._stop_task
        stopped = self.desk.cfg["stopped"]
        self.assertEqual(stopped["by"], "octobot")
        self.assertIsNone(stopped["verified"])
        self.assertEqual(fake.calls[-1], ("select", "astra_idle"))

    async def _gap(self, before, after, polls=4):
        """OctoBot drops out and comes back; returns after the settling minute and a few polls."""
        self.c.oct_equity_live = before
        self.desk._connected_at = 0
        await self.desk.on_octobot_sync(False)              # settled value before the gap
        self.c.oct_equity_live = after
        await self.desk.on_octobot_sync(True)               # reconnect: settling minute starts
        self.desk._connected_at = 0                         # settling minute over
        for _ in range(polls):
            await self.desk.on_octobot_sync(False)
        await asyncio.sleep(0)

    async def test_paper_reset_across_a_gap_rebases_instead_of_stopping(self):
        self.use(FakeOctoBot())
        self.desk.cfg["baseline"]["equity"] = 2200.0
        with patch.object(self.desk, "execute_stop", self._record_stop),                 patch.object(self.desk, "_sync_safety_net", self._noop):
            await self._gap(2200.0, 1000.0)
        self.assertFalse(self.stops)
        self.assertEqual(self.desk.cfg["baseline"]["equity"], 1000.0)
        self.assertTrue(self.desk.cfg["baseline"]["rebased"])
        self.assertIn("重置了模拟资金", self.c.logs[-1][1])

    async def test_small_move_across_a_gap_still_counts(self):
        self.use(FakeOctoBot())
        self.desk.cfg["baseline"]["equity"] = 2000.0      # 5% stop -> line 1900
        with patch.object(self.desk, "execute_stop", self._record_stop),                 patch.object(self.desk, "_sync_safety_net", self._noop):
            await self._gap(1950.0, 1890.0)                 # -3% while offline: real price move
        self.assertEqual(self.desk.cfg["baseline"]["equity"], 2000.0)
        self.assertEqual(len(self.stops), 1)

    async def test_pre_gap_equity_survives_a_bridge_restart(self):
        self.use(FakeOctoBot())
        self.desk._connected_at = 0
        with patch.object(self.desk, "_sync_safety_net", self._noop):
            await self.desk.on_octobot_sync(False)
        reborn = StrategyDesk(self.c)
        self.assertEqual(reborn._equity_before_gap, 2000.0)

    # ------------------------------------------------------------ handover ---
    def _switchable(self, fake):
        """Patch the parts of a strategy switch that read OctoBot's files or restart it."""
        selected = {}

        async def select(pid):
            fake.calls.append(("select", pid))
            selected["id"] = pid

        async def restart():
            fake.calls.append(("restart",))
        fake.select_profile = select
        self.use(fake)
        for name, value in (("restart_bot", restart),
                            ("read_active_profile", lambda: {"id": selected.get("id")}),
                            ("paper_snapshot_time", lambda: self.saved_at())):
            p = patch.object(octo, name, value)
            p.start()
            self.patches.append(p)
        p = patch.object(autopilot.strategies, "find",
                         lambda pid: {"id": pid, "label": pid, "blocked": None, "simulated": True})
        p.start()
        self.patches.append(p)

    def saved_at(self):
        """OctoBot's last save of the paper account: 'just now' unless a test says otherwise."""
        return getattr(self, "fixed_save_time", None) or __import__("time").time() + 1

    async def test_unsaved_paper_portfolio_is_flagged(self):
        fake = FakeOctoBot()
        self._switchable(fake)
        self.fixed_save_time = 1.0                          # OctoBot never wrote after the sale
        with patch.object(autopilot.StrategyDesk, "_wait_paper_saved", self._never_saved):
            result = await self.desk.switch("astra_idle")
        self.assertIn("未及时保存", result)
        self.assertTrue(any("恢复成卖出前的持币" in m for _, m in self.c.logs))

    async def _never_saved(self, *_a, **_k):
        return False

    async def test_switch_to_idle_sells_first_then_switches(self):
        fake = FakeOctoBot()
        self._switchable(fake)
        result = await self.desk.switch("astra_idle")
        saves = [c[1] for c in fake.calls if c[0] == "save"]
        self.assertEqual(saves[:2], [[CLEAR_ACTIONS], []])
        order = [c[0] for c in fake.calls]
        self.assertLess(order.index("save"), order.index("select"))   # sold before switching
        self.assertEqual(fake.calls[-1], ("restart",))
        self.assertIn("已卖出全部持币", result)

    async def test_keep_rule_does_not_sell(self):
        fake = FakeOctoBot()
        self._switchable(fake)
        self.desk.set_handover("keep")
        await self.desk.switch("astra_idle")
        self.assertFalse([c for c in fake.calls if c[0] == "save"])

    async def test_flatten_rule_sells_on_any_switch(self):
        fake = FakeOctoBot()
        self._switchable(fake)
        self.desk.set_handover("flatten")
        await self.desk.switch("astra_trend")
        self.assertIn(("save", [CLEAR_ACTIONS]), fake.calls)
        fake2 = FakeOctoBot()
        self.desk.set_handover("flatten_on_idle")
        self.assertFalse(self.desk._flatten_for("astra_trend"))
        self.assertIsNotNone(fake2)

    async def test_nothing_to_sell_means_no_clear_phase(self):
        fake = FakeOctoBot()
        fake.cleared = True                                 # already only USDT, no orders
        self._switchable(fake)
        await self.desk.switch("astra_idle")
        self.assertFalse([c for c in fake.calls if c[0] == "save"])

    async def test_failed_handover_is_reported_and_switch_continues(self):
        fake = FakeOctoBot(sells=False)
        self._switchable(fake)
        with patch.object(autopilot, "CLEAR_TIMEOUT", 0.05):
            result = await self.desk.switch("astra_idle")
        self.assertIn("交接清仓未完成", result)
        self.assertIn(("select", "astra_idle"), fake.calls)
        self.assertTrue(any("交接清仓未完成" in m for _, m in self.c.logs))

    def test_bad_handover_rule_is_refused(self):
        with self.assertRaises(ValueError):
            self.desk.set_handover("yolo")
        self.assertEqual(self.desk.cfg["handover"], "flatten_on_idle")

    async def _record_stop(self, *a, **k):
        self.stops.append(1)
        self.desk._stopping = False

    async def _noop(self, *a, **k):
        return None

    stops: list


_REAL_SLEEP = asyncio.sleep


def setUpModule():
    StopLossTests.stops = []


if __name__ == "__main__":
    unittest.main()
