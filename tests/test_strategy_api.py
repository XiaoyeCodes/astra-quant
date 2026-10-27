"""Local strategy/control input validation must not weaken the stop guard."""
import json
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from app import main
from app.autopilot import StrategyDesk
from app.config import settings
from app.strategies import describe


class StrategyAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.data_patch = patch.object(settings, "data_dir", self.tempdir.name)
        self.data_patch.start()
        cockpit = SimpleNamespace(oct_equity=1000, profile={"id": "astra_idle", "real": False},
                                  link_octobot={"ok": True}, _mode=lambda: "paper")
        self.desk = StrategyDesk(cockpit)
        self.desk.cfg["stopLoss"]["manual"] = 10
        self.fake_cockpit = SimpleNamespace(desk=self.desk, cancel_all=AsyncMock())
        self.cockpit_patch = patch.object(main, "cockpit", self.fake_cockpit)
        self.cockpit_patch.start()

    def tearDown(self):
        self.cockpit_patch.stop()
        self.data_patch.stop()
        self.tempdir.cleanup()

    async def test_bad_stop_values_return_400_without_changing_guard(self):
        for value in (None, {}, [], True, False, "bad", -1, 91):
            with self.subTest(value=value):
                request = SimpleNamespace(json=AsyncMock(return_value={"scheme": "manual", "pct": value}))
                response = await main.strategy_stoploss(request)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.desk.cfg["stopLoss"]["manual"], 10)

    async def test_missing_stop_value_does_not_disable_guard(self):
        request = SimpleNamespace(json=AsyncMock(return_value={"scheme": "manual"}))
        response = await main.strategy_stoploss(request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.desk.cfg["stopLoss"]["manual"], 10)

    async def test_explicit_zero_can_disable_guard(self):
        request = SimpleNamespace(json=AsyncMock(return_value={"scheme": "manual", "pct": 0}))
        response = await main.strategy_stoploss(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.desk.cfg["stopLoss"]["manual"], 0)

    async def test_non_object_control_body_is_rejected(self):
        with patch.object(settings, "enable_controls", True):
            for value in (None, [], "confirm"):
                request = SimpleNamespace(json=AsyncMock(return_value=value))
                response = await main.cancel_all(request)
                self.assertEqual(response.status_code, 400)
        self.fake_cockpit.cancel_all.assert_not_awaited()

    def test_failed_ai_allocation_preset_cannot_be_selected_as_available(self):
        config = {'config': {'trader': {'enabled': False}, 'trader-simulator': {'enabled': True}}}
        self.assertIn('401', describe('astra_ai_index', config)['blocked'])
        self.assertIsNone(describe('astra_ai_signal', config)['blocked'])

    def test_beginner_recommendation_and_caution_flags(self):
        config = {'config': {'trader': {'enabled': False}, 'trader-simulator': {'enabled': True}}}
        grid, dca = describe('astra_spot_grid', config), describe('astra_spot_dca', config)
        self.assertTrue(grid['recommended'])
        self.assertIsNone(grid['caution'])
        self.assertFalse(dca['recommended'])
        self.assertIn('过拟合', dca['caution'])          # in-sample parameters are labelled honestly

    def test_dca_backtest_summary_uses_the_new_parameters(self):
        summary = json.loads((Path(__file__).resolve().parents[1] / 'bridge/app/backtest_summary.json')
                             .read_text(encoding='utf-8'))
        self.assertIn('保守', summary['results']['astra_spot_dca']['name'])
        self.assertIn('dca_old', summary['results'])

    async def test_stop_guard_waits_for_grace_and_three_consecutive_breaches(self):
        self.desk.c.oct_equity_live = 80
        self.desk.c.oct_orders = []
        self.desk.cfg['baseline'] = {'equity': 100, 'scheme': 'manual'}
        self.desk._automation_key = ('already-synced',)
        self.desk.execute_stop = AsyncMock()
        self.desk._sync_safety_net = AsyncMock()
        with patch('app.autopilot.now_ms', return_value=1000000):
            await self.desk.on_octobot_sync(True)
            for _ in range(4):
                await self.desk.on_octobot_sync(False)
            self.desk.execute_stop.assert_not_awaited()
            self.desk._connected_at -= 61000
            for _ in range(2):
                await self.desk.on_octobot_sync(False)
            self.desk.execute_stop.assert_not_awaited()
            await self.desk.on_octobot_sync(False)
            await asyncio.sleep(0)
            self.desk.execute_stop.assert_awaited_once()

    async def test_stop_guard_resets_confirmation_after_equity_recovers(self):
        self.desk.c.oct_equity_live = 80
        self.desk.c.oct_orders = []
        self.desk.cfg['baseline'] = {'equity': 100, 'scheme': 'manual'}
        self.desk._automation_key = ('already-synced',)
        self.desk.execute_stop = AsyncMock()
        self.desk._sync_safety_net = AsyncMock()
        for value in (80, 80, 100, 80, 80):
            self.desk.c.oct_equity_live = value
            await self.desk.on_octobot_sync(False)
        self.desk.execute_stop.assert_not_awaited()

    async def test_starting_scheme_during_initialization_keeps_original_baseline(self):
        self.desk._connected_at = 1000000
        before = {'equity': 1234, 'scheme': 'manual'}
        self.desk.cfg['baseline'] = dict(before)
        with patch('app.autopilot.now_ms', return_value=1001000):
            with self.assertRaises(ValueError):
                await self.desk.start_scheme('manual', stop_pct=20)
        self.assertEqual(self.desk.cfg['baseline'], before)
        self.assertEqual(self.desk.cfg['stopLoss']['manual'], 10)

    async def test_initial_partial_valuation_does_not_become_baseline(self):
        self.desk.cfg['baseline'] = None
        self.desk.c.oct_equity_live = 80
        self.desk.c.oct_orders = []
        self.desk._sync_safety_net = AsyncMock()
        with patch('app.autopilot.now_ms', return_value=1000000):
            await self.desk.on_octobot_sync(True)
        self.assertIsNone(self.desk.cfg['baseline'])
        with patch('app.autopilot.now_ms', return_value=1061000):
            await self.desk.on_octobot_sync(False)
        self.assertEqual(self.desk.cfg['baseline']['equity'], 80)

    async def test_blocked_ai_scheme_does_not_change_scheme_or_stop_setting(self):
        before = json.loads(json.dumps(self.desk.cfg))
        with patch('app.autopilot.strategies.find', return_value={'blocked': 'data source 401'}):
            with self.assertRaises(ValueError):
                await self.desk.start_scheme('ai_trade', ai_profile='astra_ai_index', stop_pct=20)
        self.assertEqual(self.desk.cfg, before)


if __name__ == "__main__":
    unittest.main()
