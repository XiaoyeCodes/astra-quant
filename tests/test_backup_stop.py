"""OctoBot's backup stop without pausing: the StopTrading patch, its helper and the bridge's record."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
sys.path.insert(0, str(ROOT / "tools"))

import octobot_backup_stop as helper  # noqa: E402
import patch_octobot  # noqa: E402
from app import autopilot, summary  # noqa: E402
from app.autopilot import StrategyDesk  # noqa: E402
from app.config import settings  # noqa: E402

ORIGINAL_STOP_TRADING = '''import octobot_commons.constants as commons_constants
import octobot_services.interfaces.util as interfaces_util
import tentacles.Automation.actions.cancel_open_order_action as cancel_open_orders
import octobot.automation.bases.execution_details as execution_details


class StopTrading(cancel_open_orders.CancelOpenOrders):
    PROFILE_ID = commons_constants.DEFAULT_PROFILE  # non trading profile

    async def process(
        self, execution_details: execution_details.ExecutionDetails
    ) -> bool:
        # cancel all open orders
        await super().process(execution_details)
        # select non trading profile
        config = interfaces_util.get_edited_config(dict_only=False)
        config.select_profile(self.PROFILE_ID)
        config.save()
        # reboot
        interfaces_util.get_bot_api().restart_bot()
        return True
'''


class HelperTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_parks_on_idle_when_the_profile_exists(self):
        self.assertEqual(helper.target_profile("default", self.user), "default")
        os.makedirs(os.path.join(self.user, "profiles", "astra_idle"))
        self.assertEqual(helper.target_profile("default", self.user), "astra_idle")

    async def test_waits_for_the_paper_save_then_leaves_a_marker(self):
        os.makedirs(os.path.join(self.user, "profiles", "astra_idle"))
        slept = []

        async def fake_sleep(s):
            slept.append(s)
        with patch.object(helper, "USER_DIR", self.user), patch.object(helper.asyncio, "sleep", fake_sleep):
            profile = await helper.before_restart("default")
        self.assertEqual(profile, "astra_idle")
        self.assertGreaterEqual(slept[0], 10)            # past OctoBot's 5 s save debounce
        with open(os.path.join(self.user, helper.MARKER), encoding="utf-8") as fh:
            marker = json.load(fh)
        self.assertEqual(marker["profile"], "astra_idle")
        self.assertAlmostEqual(marker["ts"], time.time(), delta=5)


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "tentacles/Automation/actions/stop_trading_action/stop_trading.py"
        self.target.parent.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_patch_is_applied_once_and_compiles(self):
        self.target.write_text(ORIGINAL_STOP_TRADING, encoding="utf-8")
        patch_octobot.patch_backup_stop(self.tmp.name)
        once = self.target.read_text(encoding="utf-8")
        self.assertIn(patch_octobot.BACKUP_MARKER, once)
        self.assertIn("before_restart(self.PROFILE_ID)", once)
        self.assertIn("config.select_profile(profile_id)", once)
        self.assertNotIn("config.select_profile(self.PROFILE_ID)", once)
        compile(once, "stop_trading.py", "exec")
        patch_octobot.patch_backup_stop(self.tmp.name)
        self.assertEqual(self.target.read_text(encoding="utf-8"), once)

    def test_changed_layout_is_left_alone(self):
        changed = ORIGINAL_STOP_TRADING.replace("# select non trading profile", "# pick a profile")
        self.target.write_text(changed, encoding="utf-8")
        patch_octobot.patch_backup_stop(self.tmp.name)
        self.assertEqual(self.target.read_text(encoding="utf-8"), changed)

    def test_stop_keeps_the_paper_account(self):
        original = (
            "class HistoricalPortfolioValueManager:\n"
            "    async def stop(self):\n"
            "        await self.save_historical_portfolio_value(update_data=False)\n"
        )
        target = Path(self.tmp.name) / "historical_portfolio_value_manager.py"
        target.write_text(original, encoding="utf-8")
        patch_octobot.patch_keep_paper_account(target)
        patched = target.read_text(encoding="utf-8")
        self.assertIn("if self.ending_portfolio is None:", patched)
        compile(patched, str(target), "exec")
        namespace = {}
        exec(patched, namespace)                 # behaviour: fills the portfolio before saving

        class Fake(namespace["HistoricalPortfolioValueManager"]):
            ending_portfolio = None
            saved = None

            def _update_portfolios(self):
                self.ending_portfolio = {"USDT": 997.82}

            async def save_historical_portfolio_value(self, update_data=True):
                self.saved = dict(self.ending_portfolio)
        fake = Fake()
        asyncio.run(fake.stop())
        self.assertEqual(fake.saved, {"USDT": 997.82})
        patch_octobot.patch_keep_paper_account(target)
        self.assertEqual(target.read_text(encoding="utf-8"), patched)

    def test_backup_actions_never_pause(self):
        self.assertNotIn("StopStrategiesAndPauseTrader", autopilot.STOP_ACTIONS)
        self.assertEqual(autopilot.STOP_ACTIONS[-1], "StopTrading")
        backup = autopilot.automation_config(10)["automations"]["1"]
        self.assertEqual(backup["actions"], autopilot.STOP_ACTIONS)


class FakeCockpit:
    def __init__(self):
        self.oct_equity = self.oct_equity_live = 880.0
        self.oct_orders = []
        self.profile = {"id": "astra_idle", "referenceMarket": "USDT"}
        self.link_octobot = {"ok": True, "since": "B", "error": None}
        self.logs = []

    def _mode(self):
        return "paper"

    def add_log(self, level, msg):
        self.logs.append((level, msg))


class BridgeRecordTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = patch.object(settings, "data_dir", self.tmp.name)
        self.p.start()
        self.c = FakeCockpit()
        self.desk = self.c.desk = StrategyDesk(self.c)
        self.c.oct_equity_live = 1000.0
        self.desk._arm("manual")
        self.desk.set_risk("manual", stop=10)
        self.desk.cfg["baseline"]["ts"] = int(time.time() * 1000) - 3600 * 1000
        self.desk._equity_before_gap = 1000.0           # last equity before the bridge went away
        self.c.oct_equity_live = 880.0

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    async def _reconnect(self, marker, status):
        async def ok(*_a, **_k):
            return None

        async def liquidation(_quote):
            return status
        with patch.object(autopilot.octo, "backup_stop_marker", lambda: marker), \
                patch.object(self.desk, "_liquidation_status", liquidation), \
                patch.object(self.desk, "_sync_safety_net", ok), \
                patch.object(autopilot.asyncio, "sleep", ok):
            await self.desk.on_octobot_sync(True)
            task = getattr(self.desk, "_stop_task", None)
            if task:
                await task
                self.desk._stop_task = None

    async def test_marker_is_recorded_and_verified_not_mistaken_for_a_reset(self):
        await self._reconnect({"ts": time.time() - 60, "profile": "astra_idle"},
                              {"known": True, "orders": 0, "leftovers": {}, "equity": 879.5})
        st = self.desk.cfg["stopped"]
        self.assertEqual((st["by"], st["verified"], st["finalEquity"]), ("octobot", True, 879.5))
        self.assertAlmostEqual(st["loss"], 12.05)
        self.assertEqual(self.desk.cfg["baseline"]["equity"], 1000.0)   # not rebased to 880
        self.assertFalse(self.desk._stopping)
        story = summary.story(self.c)
        self.assertEqual(story["title"], "OctoBot 的备用止损停止了交易")
        self.assertIn("只剩 USDT（879.50）", " ".join(story["lines"]))

    async def test_leftovers_are_reported(self):
        await self._reconnect({"ts": time.time() - 60}, {"known": True, "orders": 0,
                                                          "leftovers": {"BTC": 0.004}, "equity": 880.0})
        st = self.desk.cfg["stopped"]
        self.assertFalse(st["verified"])
        self.assertIn("BTC", st["problem"])
        self.assertEqual(summary.story(self.c)["tone"], "bad")

    async def test_marker_seen_once_and_old_markers_ignored(self):
        old = {"ts": time.time() - 7200}                 # before this scheme started
        await self._reconnect(old, {"known": True, "orders": 0, "leftovers": {}, "equity": 880.0})
        self.assertIsNone(self.desk.cfg["stopped"])
        marker = {"ts": time.time() - 60}
        await self._reconnect(marker, {"known": True, "orders": 0, "leftovers": {}, "equity": 880.0})
        first = self.desk.cfg["stopped"]["ts"]
        self.desk.cfg["stopped"] = None                  # user restarted a scheme ...
        self.desk.cfg["baseline"]["ts"] = 0
        await self._reconnect(marker, {"known": True, "orders": 0, "leftovers": {}, "equity": 880.0})
        self.assertIsNone(self.desk.cfg["stopped"])      # ... and the same marker does not fire again
        self.assertEqual(self.desk.cfg["backupSeen"], first)


if __name__ == "__main__":
    unittest.main()
