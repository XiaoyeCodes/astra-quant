"""Health checks and the diagnostics export (which must never carry a secret)."""
import collections
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from app import health
from app.audit import AuditLog
from app.autopilot import StrategyDesk
from app.config import settings
from app.notify import Notifier


def now_ms():
    return int(time.time() * 1000)


class FakeCockpit:
    def __init__(self, folder):
        self.oct_equity = self.oct_equity_live = 1000.0
        self.oct_orders, self.oct_trades = [], []
        self.profile = {"id": "astra_spot_grid", "real": False, "simulated": True, "referenceMarket": "USDT"}
        self.link_octobot = {"ok": True, "version": "2.1.1", "since": "A", "error": None}
        self.link_bitget = {"ok": True, "latencyMs": 80, "error": None}
        self.ticks = collections.deque([[now_ms(), 60000.0]])
        self.last_octo_sync = now_ms()
        self._octo_lost_at = None
        self.mode = "paper"
        self.notifier = Notifier()
        self.audit = AuditLog(folder)
        self.equity_hist = collections.deque([[now_ms(), 1000.0]])
        self.logs = []

    def _mode(self):
        return self.mode

    def add_log(self, level, msg):
        self.logs.append((level, msg))

    def snapshot(self):
        return {"mode": self.mode, "links": {"octobot": self.link_octobot, "bitget": self.link_bitget},
                "profile": self.profile, "strategy": self.desk.snapshot(),
                "equity": {"value": self.oct_equity, "start": 1000.0}, "stats": {},
                "orders": [], "trades": self.oct_trades, "positions": []}


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(settings, "data_dir", self.tmp.name),
                        patch.object(settings, "notify_webhook_url", "https://hook.example/secret-hook-token"),
                        patch.object(settings, "notify_webhook_type", "wecom"),
                        patch.object(settings, "llm_api_key", "sk-very-secret-llm-key")]
        for p in self.patches:
            p.start()
        self.c = FakeCockpit(self.tmp.name)
        self.c.desk = StrategyDesk(self.c)
        self.c.desk._arm("manual")
        self.c.desk.set_risk("manual", stop=10)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def by_id(self):
        return {x["id"]: x for x in health.checks(self.c)}

    def test_healthy_paper_setup_is_ok(self):
        r = health.report(self.c)
        self.assertEqual(r["status"], "ok", r["checks"])
        self.assertIn("最大止损", self.by_id()["risk"]["detail"])

    def test_octobot_down_is_bad_with_a_fix(self):
        self.c.link_octobot.update(ok=False, error="connection refused")
        self.c._octo_lost_at = now_ms() - 5 * 60000
        item = self.by_id()["octobot"]
        self.assertEqual(item["status"], "bad")
        self.assertIn("5 分钟", item["detail"])
        self.assertTrue(item["fix"])
        self.assertEqual(health.report(self.c)["status"], "bad")

    def test_stale_octobot_data_warns(self):
        self.c.last_octo_sync = now_ms() - 120000
        self.assertEqual(self.by_id()["octobot"]["status"], "warn")

    def test_live_mode_is_flagged(self):
        self.c.mode = "live"
        self.assertEqual(self.by_id()["mode"]["status"], "bad")

    def test_no_stop_line_warns_and_blind_equity_is_bad(self):
        self.c.desk.set_risk("manual", stop=0, trail=0, daily=0)
        self.assertEqual(self.by_id()["risk"]["status"], "warn")
        self.c.desk.set_risk("manual", stop=10)
        self.c.oct_equity_live = None
        self.assertEqual(self.by_id()["risk"]["status"], "bad")

    def test_fee_rate_in_either_currency(self):
        buy = {"symbol": "BTC/USDT", "price": 50000, "amount": 0.01, "cost": 500, "fee": 0.00001, "feeCurrency": "BTC"}
        sell = {"symbol": "BTC/USDT", "price": 50000, "amount": 0.01, "cost": 500, "fee": 0.5, "feeCurrency": "USDT"}
        self.assertAlmostEqual(health.trade_fee_rate(buy), 0.1)
        self.assertAlmostEqual(health.trade_fee_rate(sell), 0.1)
        self.assertIsNone(health.trade_fee_rate({"symbol": "BTC/USDT", "cost": 0, "fee": 1}))

    def test_old_ccxt_fee_warns(self):
        self.c.oct_trades = [{"symbol": "BTC/USDT", "price": 50000, "amount": 0.01, "cost": 500,
                              "fee": 1.0, "feeCurrency": "USDT"}] * 5
        self.assertEqual(self.by_id()["fees"]["status"], "warn")
        fixed = dict(self.c.oct_trades[0], fee=0.5)
        self.c.oct_trades = self.c.oct_trades * 4 + [fixed] * 6     # history before the patch, then fixed
        self.assertEqual(self.by_id()["fees"]["status"], "ok")

    def test_diagnostics_never_contain_secrets(self):
        self.c.add_log("warn", "通知失败：https://hook.example/secret-hook-token 404")
        self.c.audit.write({"id": 1, "t": now_ms(), "level": "warn",
                            "msg": "LLM said: bad key sk-very-secret-llm-key"})
        text = health.redact(json.dumps(health.diagnostics(self.c), ensure_ascii=False, default=str))
        self.assertNotIn("secret-hook-token", text)
        self.assertNotIn("sk-very-secret-llm-key", text)
        data = json.loads(text)
        self.assertTrue(data["settings"]["configured"]["llmApiKey"])
        self.assertTrue(data["log"])


if __name__ == "__main__":
    unittest.main()
