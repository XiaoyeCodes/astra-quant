"""Paper trades on Bitget pay the profile's fees, not ccxt's static 0.2% defaults."""
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from octobot_paper_fees import configured_paper_fee
from patch_octobot import FEE_MARKER, patch_paper_fees

BITGET = '''class Bitget(exchanges.RestExchange):
    @classmethod
    def get_name(cls):
        return 'bitget'

    def get_adapter_class(self):
        return BitgetCCXTAdapter
'''


def manager(simulated=True, future=False, fees=None):
    config = {"trader-simulator": {"fees": fees if fees is not None else {"maker": 0.1, "taker": 0.1}}}
    return SimpleNamespace(is_simulated=simulated, is_future=future, config=config)


class PaperFeeTests(unittest.TestCase):
    def test_uses_profile_rate_in_quote(self):
        fee = configured_paper_fee(manager(), "BTC/USDT", Decimal("0.01"), Decimal("80000"), "taker")
        self.assertEqual(fee["currency"], "USDT")
        self.assertAlmostEqual(fee["rate"], 0.001)
        self.assertEqual(fee["cost"], Decimal("0.80000"))
        self.assertFalse(fee["is_from_exchange"])

    def test_maker_and_default_side(self):
        m = manager(fees={"maker": 0.08, "taker": 0.1})
        self.assertAlmostEqual(configured_paper_fee(m, "ETH/USDT", 1, 2000, "maker")["rate"], 0.0008)
        self.assertAlmostEqual(configured_paper_fee(m, "ETH/USDT", 1, 2000, None)["rate"], 0.001)

    def test_real_trading_and_futures_are_untouched(self):
        self.assertIsNone(configured_paper_fee(manager(simulated=False), "BTC/USDT", 1, 1, "taker"))
        self.assertIsNone(configured_paper_fee(manager(future=True), "BTC/USDT:USDT", 1, 1, "taker"))
        self.assertIsNone(configured_paper_fee(manager(fees={}), "BTC/USDT", 1, 1, "taker"))

    def test_patch_is_idempotent_and_compiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "tentacles/Trading/Exchange/bitget/bitget_exchange.py"
            target.parent.mkdir(parents=True)
            target.write_text(BITGET, encoding="utf-8")
            patch_paper_fees(tmp)
            once = target.read_text(encoding="utf-8")
            patch_paper_fees(tmp)
            self.assertEqual(once, target.read_text(encoding="utf-8"))
            self.assertEqual(once.count(FEE_MARKER), 1)
            self.assertIn("def get_trade_fee", once)
            compile(once, "bitget_exchange.py", "exec")

    def test_unknown_layout_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "tentacles/Trading/Exchange/bitget/bitget_exchange.py"
            target.parent.mkdir(parents=True)
            target.write_text("class Bitget: pass\n", encoding="utf-8")
            patch_paper_fees(tmp)
            self.assertEqual(target.read_text(encoding="utf-8"), "class Bitget: pass\n")


if __name__ == "__main__":
    unittest.main()
