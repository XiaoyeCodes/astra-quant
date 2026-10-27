"""Plain-language status (simple home page, why-card) and the manual "stop and sell all"."""
import asyncio
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from app import autopilot, summary
from app.autopilot import StrategyDesk, trading_day
from app.config import settings


def now_ms():
    return int(time.time() * 1000)


class FakeCockpit:
    def __init__(self):
        self.oct_equity = self.oct_equity_live = 1000.0
        self.oct_orders = []
        self.profile = {"id": "astra_spot_grid", "label": "现货网格", "real": False, "simulated": True,
                        "referenceMarket": "USDT"}
        self.link_octobot = {"ok": True, "since": "A", "error": None}
        self.mode = "paper"
        self.logs = []

    def _mode(self):
        return self.mode

    def add_log(self, level, msg):
        self.logs.append((level, msg))


class SummaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = patch.object(settings, "data_dir", self.tmp.name)
        self.p.start()
        self.c = FakeCockpit()
        self.c.desk = StrategyDesk(self.c)
        self.c.desk._arm("manual")

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_running_without_stop_warns(self):
        s = summary.story(self.c)
        self.assertEqual((s["kind"], s["tone"]), ("running", "warn"))
        self.assertIn("现货网格", s["title"])
        self.assertIn("没有设置", " ".join(s["lines"]))

    def test_running_with_stop_names_the_line(self):
        self.c.desk.set_risk("manual", stop=10)
        s = summary.story(self.c)
        self.assertEqual(s["tone"], "ok")
        self.assertIn("最大止损线 900.00，还差 10.00%", " ".join(s["lines"]))

    def test_numbers_today_and_since_start(self):
        self.c.desk.cfg["day"] = {"date": trading_day(), "equity": 980.0}
        self.c.desk.cfg["baseline"]["equity"] = 1250.0
        n = summary.numbers(self.c)
        self.assertAlmostEqual(n["today"], 20.0)
        self.assertAlmostEqual(n["sinceStartPct"], -20.0)

    def test_trailing_stop_explains_why_and_what_now(self):
        self.c.profile = {"id": "astra_idle", "referenceMarket": "USDT"}
        self.c.desk.cfg["stopped"] = {"ts": now_ms(), "equity": 1150.0, "pct": 10, "loss": 11.5,
                                      "rule": "trail", "ruleLabel": "回撤止损", "verified": True,
                                      "finalEquity": 1149.2}
        s = summary.story(self.c)
        self.assertEqual(s["kind"], "stopped")
        self.assertIn("回撤止损", s["title"])
        text = " ".join(s["lines"])
        self.assertIn("运行期间最高", text)
        self.assertIn("只剩 USDT（1149.20）", text)
        self.assertTrue(s["next"])

    def test_unverified_stop_is_bad(self):
        self.c.desk.cfg["stopped"] = {"ts": now_ms(), "rule": "base", "pct": 10, "loss": 12,
                                      "verified": False, "problem": "仍有持币未卖出：BTC 0.01"}
        s = summary.story(self.c)
        self.assertEqual(s["tone"], "bad")
        self.assertIn("BTC 0.01", " ".join(s["lines"]))

    def test_recent_auto_switch_carries_its_reason(self):
        self.c.desk.cfg["lastSwitchInfo"] = {"ts": now_ms(), "from": "astra_trend", "fromLabel": "趋势跟随",
                                             "to": "astra_spot_grid", "label": "现货网格", "by": "auto",
                                             "why": "行情雷达判断现在是「震荡」行情（置信度 72%），这个策略更适合",
                                             "handover": None}
        text = " ".join(summary.story(self.c)["lines"])
        self.assertIn("自动切换：趋势跟随 → 现货网格", text)
        self.assertIn("震荡", text)
        self.c.desk.cfg["lastSwitchInfo"]["ts"] = now_ms() - 2 * summary.RECENT_SWITCH_MS
        self.assertNotIn("自动切换", " ".join(summary.story(self.c)["lines"]))

    def test_offline_and_live(self):
        self.c.link_octobot.update(ok=False, error="connection refused")
        self.assertEqual(summary.story(self.c)["kind"], "offline")
        self.c.link_octobot.update(ok=True)
        self.c.mode = "live"
        self.assertEqual(summary.story(self.c)["tone"], "bad")

    async def test_halt_runs_the_verified_stop_once(self):
        calls = []

        async def fake_stop(reason=None, rule="base"):
            calls.append(rule)
            self.c.desk._stopping = False
        with patch.object(self.c.desk, "execute_stop", fake_stop):
            self.assertIn("正在撤单", await self.c.desk.halt())
            with self.assertRaises(ValueError):
                await self.c.desk.halt()             # second click while stopping
            await asyncio.sleep(0)
        self.assertEqual(calls, ["manual"])

    async def test_halt_refuses_outside_paper(self):
        self.c.mode = "live"
        with self.assertRaisesRegex(ValueError, "实盘"):
            await self.c.desk.halt()
        self.c.mode = "paper"
        self.c.link_octobot["ok"] = False
        with self.assertRaisesRegex(ValueError, "未连接"):
            await self.c.desk.halt()

    async def test_manual_stop_record(self):
        async def ok(*_a, **_k):
            return None

        async def clear(_quote):
            return {"known": True, "orders": 0, "leftovers": {}, "equity": 1000.0}
        with patch.object(self.c.desk, "_clear_positions", clear), \
                patch.object(autopilot.octo, "select_profile", ok), \
                patch.object(autopilot.octo, "restart_bot", ok), \
                patch.object(autopilot.octo, "paper_snapshot_time", lambda: 9e12), \
                patch.object(autopilot.asyncio, "sleep", ok):
            await self.c.desk.execute_stop(rule="manual")
        st = self.c.desk.cfg["stopped"]
        self.assertEqual((st["rule"], st["ruleLabel"], st["verified"]), ("manual", "手动停止", True))
        self.assertEqual(summary.story(self.c)["kind"], "switching")  # OctoBot restarting on 观望
        self.c.desk.switching = None
        self.c.profile = {"id": "astra_idle", "referenceMarket": "USDT"}
        self.assertEqual(summary.story(self.c)["title"], "已手动停止交易")


if __name__ == "__main__":
    unittest.main()
