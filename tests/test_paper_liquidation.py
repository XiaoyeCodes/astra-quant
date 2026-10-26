from decimal import Decimal
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from octobot_paper_safety import liquidate_paper_spot


class LiquidationTests(unittest.IsolatedAsyncioTestCase):
    def make_exchange(self):
        holdings = {key: SimpleNamespace(available=amount) for key, amount in
                    {'USDT': Decimal('1100'), 'BTC': 0, 'ETH': Decimal('.2838'), 'SOL': Decimal('4.3327')}.items()}
        return SimpleNamespace(is_simulated=True, is_future=False,
            exchange_personal_data=SimpleNamespace(portfolio_manager=SimpleNamespace(
                reference_market='USDT', portfolio=SimpleNamespace(portfolio=holdings))),
            exchange=SimpleNamespace(get_market_status=Mock(return_value={'active': True}),
                                     get_price_ticker=AsyncMock(return_value={'close': 100})),
            exchange_symbols_data=SimpleNamespace(get_exchange_symbol_data=Mock(return_value=
                SimpleNamespace(prices_manager=SimpleNamespace(set_mark_price=Mock())))),
            trader=SimpleNamespace(_sell_everything=AsyncMock(return_value=['paper-order'])))

    async def test_sells_assets_absent_from_active_strategy(self):
        exchange = self.make_exchange()
        orders = await liquidate_paper_spot(exchange, 'exchange-mark-price')
        self.assertEqual([call.args[0] for call in exchange.trader._sell_everything.await_args_list],
                         ['ETH/USDT', 'SOL/USDT'])
        self.assertEqual(len(orders), 2)

    async def test_refuses_real_account(self):
        exchange = self.make_exchange(); exchange.is_simulated = False
        with self.assertRaises(ValueError):
            await liquidate_paper_spot(exchange, 'exchange-mark-price')
        exchange.trader._sell_everything.assert_not_awaited()

    async def test_invalid_market_price_is_not_reported_as_success(self):
        exchange = self.make_exchange()
        exchange.exchange.get_price_ticker.return_value = {'close': 0}
        with self.assertRaises(ValueError):
            await liquidate_paper_spot(exchange, 'exchange-mark-price')
        exchange.trader._sell_everything.assert_not_awaited()
