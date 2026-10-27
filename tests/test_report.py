"""Daily report: content, once per Beijing day, survives a restart, never without a channel."""
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from app import report
from app.autopilot import RISK_TZ, StrategyDesk
from app.config import settings


def bj(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=RISK_TZ).timestamp()


class FakeNotifier:
    def __init__(self, channels=("wecom",)):
        self.channels = list(channels)
        self.sent = []

    def send(self, text, level="info", key=None, quiet_s=300):
        if not self.channels:
            return False
        self.sent.append(text)
        return True


class FakeCockpit:
    def __init__(self):
        self.oct_equity = self.oct_equity_live = 1000.0
        self.oct_orders, self.oct_trades = [], []
        self.prices = {"BTC/USDT": 60000.0}
        self.profile = {"id": "astra_spot_grid", "label": "现货网格", "simulated": True, "referenceMarket": "USDT"}
        self.link_octobot = {"ok": True, "since": "A", "error": None}
        self.notifier = FakeNotifier()
        self.logs = []

    def _mode(self):
        return "paper"

    def add_log(self, level, msg):
        self.logs.append((level, msg))


OK_CHECKS = [{"id": "octobot", "label": "OctoBot", "status": "ok", "detail": "", "fix": ""}]


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(settings, "data_dir", self.tmp.name),
                        patch.object(settings, "daily_report_hour", 21)]
        for p in self.patches:
            p.start()
        self.c = FakeCockpit()
        self.c.desk = StrategyDesk(self.c)
        self.c.desk._arm("manual")                 # baseline 1000, BTC 60000
        self.c.desk.set_risk("manual", stop=10)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_text_compares_with_btc_and_counts_todays_trades(self):
        now = bj(2026, 10, 27, 21, 5)
        self.c.oct_equity = self.c.oct_equity_live = 1050.0
        self.c.prices["BTC/USDT"] = 63000.0
        self.c.desk.cfg["day"] = {"date": "2026-10-27", "equity": 1040.0}
        start = report.day_start_ms(now)
        self.c.oct_trades = [
            {"time": start - 1000, "side": "buy", "fee": 9.0, "feeCurrency": "USDT"},   # yesterday
            {"time": start + 1000, "side": "buy", "fee": 0.5, "feeCurrency": "USDT"},
            {"time": start + 2000, "side": "sell", "fee": 0.5, "feeCurrency": "USDT"},
        ]
        with patch.object(report, "trading_day", lambda now=None: "2026-10-27"), \
                patch("app.summary.trading_day", lambda now=None: "2026-10-27"):
            text = report.build(self.c, OK_CHECKS, now=now)
        self.assertIn("资产 1050.00 USDT", text)
        self.assertIn("今天 +10.00", text)
        self.assertIn("+50.00（+5.00%）；同期持有 BTC +5.00%", text)
        self.assertIn("今天成交 2 笔（买 1 / 卖 1），手续费约 1.00 USDT", text)
        self.assertIn("最大止损线 900.00", text)
        self.assertIn("系统体检全部正常", text)

    def test_issues_are_listed(self):
        checks = OK_CHECKS + [{"id": "notify", "label": "通知", "status": "warn", "detail": "最近一次发送失败", "fix": ""}]
        self.assertIn("需要注意：通知：最近一次发送失败", report.build(self.c, checks))

    def test_sends_once_per_day_after_the_hour(self):
        r = report.DailyReporter(self.c)
        with patch.object(report.health, "checks", lambda c: OK_CHECKS):
            self.assertFalse(r.tick(bj(2026, 10, 27, 20, 59)))
            self.assertTrue(r.tick(bj(2026, 10, 27, 21, 0)))
            self.assertFalse(r.tick(bj(2026, 10, 27, 23, 0)))
            self.assertTrue(r.tick(bj(2026, 10, 28, 21, 30)))
        self.assertEqual(len(self.c.notifier.sent), 2)

    def test_restart_does_not_resend(self):
        with patch.object(report.health, "checks", lambda c: OK_CHECKS):
            report.DailyReporter(self.c).tick(bj(2026, 10, 27, 21, 0))
            again = report.DailyReporter(self.c)           # bridge restarted the same evening
            self.assertFalse(again.tick(bj(2026, 10, 27, 22, 0)))
        self.assertEqual(len(self.c.notifier.sent), 1)

    def test_no_channel_or_off_means_no_report(self):
        self.c.notifier.channels = []
        self.assertFalse(report.DailyReporter(self.c).tick(bj(2026, 10, 27, 22, 0)))
        self.c.notifier.channels = ["wecom"]
        with patch.object(settings, "daily_report_hour", None):
            self.assertFalse(report.DailyReporter(self.c).tick(bj(2026, 10, 27, 22, 0)))
            self.assertIsNone(report.next_send())

    def test_manual_send_previews_without_a_channel(self):
        self.c.notifier.channels = []
        with patch.object(report.health, "checks", lambda c: OK_CHECKS):
            sent, text = report.DailyReporter(self.c).send_now()
        self.assertFalse(sent)
        self.assertIn("日报", text)

    def test_next_send(self):
        self.assertEqual(report.next_send(bj(2026, 10, 27, 20, 0)), int(bj(2026, 10, 27, 21) * 1000))
        self.assertEqual(report.next_send(bj(2026, 10, 27, 21, 30)), int(bj(2026, 10, 28, 21) * 1000))


if __name__ == "__main__":
    unittest.main()
