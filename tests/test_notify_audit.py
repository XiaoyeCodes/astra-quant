"""Notifications reach the phone once per event and never break trading; the audit trail persists."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from app import notify
from app.audit import AuditLog
from app.config import settings
from app.state import Cockpit

_REAL_SLEEP = asyncio.sleep


class PayloadTests(unittest.TestCase):
    def test_platform_formats(self):
        self.assertEqual(notify.webhook_payload("wecom", "T", "x", "risk"), {"msgtype": "text", "text": {"content": "【T】x"}})
        self.assertEqual(notify.webhook_payload("dingtalk", "T", "x", "risk")["msgtype"], "text")
        self.assertEqual(notify.webhook_payload("feishu", "T", "x", "risk"), {"msg_type": "text", "content": {"text": "【T】x"}})
        self.assertEqual(notify.webhook_payload("serverchan", "T", "x", "risk")["desp"], "x")
        self.assertEqual(notify.webhook_payload("generic", "T", "x", "warn")["level"], "warn")


class NotifierTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = [patch.object(settings, "notify_webhook_url", "https://hooks.example/x"),
                  patch.object(settings, "notify_webhook_type", "wecom"),
                  patch.object(settings, "notify_telegram_token", ""),
                  patch.object(settings, "notify_telegram_chat", "")]
        for p in self.p:
            p.start()
        self.n = notify.Notifier()
        self.delivered = []
        self.n._deliver = lambda ch, text, level: self.delivered.append((ch, text, level))
        self.n.start()

    async def asyncTearDown(self):
        await self.n.stop()
        for p in self.p:
            p.stop()

    async def _drain(self):
        for _ in range(20):
            await _REAL_SLEEP(0.01)

    async def test_sends_and_suppresses_repeats(self):
        self.assertTrue(self.n.send("止损触发", "risk"))
        self.assertFalse(self.n.send("止损触发", "risk"))          # same text within the quiet window
        self.assertTrue(self.n.send("other", "info", key="k2"))
        await self._drain()
        self.assertEqual([d[1] for d in self.delivered], ["止损触发", "other"])
        self.assertEqual(self.n.status()["channels"], ["wecom"])

    async def test_failures_are_retried_and_never_raise(self):
        calls = []

        def flaky(ch, text, level):
            calls.append(1)
            raise OSError("network down")
        self.n._deliver = flaky
        with patch.object(notify.asyncio, "sleep", self._fast_sleep):
            self.assertTrue(self.n.send("x", "risk"))
            await self._drain()
        self.assertEqual(len(calls), 3)
        self.assertIn("network down", self.n.status()["lastError"])

    async def _fast_sleep(self, _s):
        await _REAL_SLEEP(0)

    async def test_no_channel_means_no_queueing(self):
        with patch.object(settings, "notify_webhook_url", ""):
            self.assertFalse(self.n.send("x"))

    def test_telegram_errors_do_not_leak_the_token(self):
        n = notify.Notifier()
        with patch.object(settings, "notify_telegram_token", "123:SECRET"), \
                patch.object(settings, "notify_telegram_chat", "42"), \
                patch.object(notify.urllib.request, "build_opener") as opener:
            import urllib.error
            opener.return_value.open.side_effect = urllib.error.HTTPError(
                "https://api.telegram.org/bot123:SECRET/sendMessage", 401, "Unauthorized", {}, None)
            with self.assertRaises(RuntimeError) as ctx:
                n._deliver("telegram", "x", "risk")
        self.assertNotIn("SECRET", str(ctx.exception))


class OfflineAlertTests(unittest.TestCase):
    def make(self, switching=None):
        sent = []
        fake = SimpleNamespace(link_octobot={"ok": False, "error": "timeout"}, _octo_lost_at=None,
                               desk=SimpleNamespace(switching=switching), _offline_notified=False,
                               notifier=SimpleNamespace(send=lambda text, level, key=None, quiet_s=0: sent.append((key, text))))
        return fake, sent

    def test_alert_once_after_threshold_then_recovery(self):
        fake, sent = self.make()
        now = time.time() * 1000
        with patch.object(settings, "notify_offline_after", 120):
            fake._octo_lost_at = now - 60 * 1000
            Cockpit._check_offline(fake)
            self.assertEqual(sent, [])                       # only 1 minute
            fake._octo_lost_at = now - 180 * 1000
            Cockpit._check_offline(fake)
            Cockpit._check_offline(fake)
            self.assertEqual([k for k, _ in sent], ["octobot-offline"])
            fake.link_octobot["ok"] = True
            Cockpit._check_offline(fake)
            self.assertEqual([k for k, _ in sent], ["octobot-offline", "octobot-online"])

    def test_no_alert_during_planned_switch(self):
        fake, sent = self.make(switching={"to": "astra_trend"})
        fake._octo_lost_at = time.time() * 1000 - 600 * 1000
        Cockpit._check_offline(fake)
        self.assertEqual(sent, [])


class AuditTests(unittest.TestCase):
    def test_write_recent_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = AuditLog(tmp, keep_days=30)
            base = time.time() * 1000
            for i in range(5):
                a.write({"id": i + 1, "t": base + i, "level": "info", "msg": f"m{i}"})
            self.assertEqual([e["msg"] for e in a.recent(3)], ["m2", "m3", "m4"])
            old = os.path.join(tmp, "astra-2000-01-01.jsonl")
            Path(old).write_text(json.dumps({"id": 0}) + "\n", encoding="utf-8")
            os.utime(old, (0, 0))
            a.prune()
            self.assertFalse(os.path.exists(old))
            self.assertEqual(len(os.listdir(tmp)), 1)


if __name__ == "__main__":
    unittest.main()
