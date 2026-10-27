"""AI integration regressions; no API key, network, or trading process required."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

from app import llm
from app.autopilot import StrategyDesk
from app.config import settings


def answer(**changes):
    value = {"regime": "range", "confidence": 0.8,
             "strategy": "astra_spot_grid", "reasons": ["区间震荡"]}
    value.update(changes)
    return value


class AnswerValidationTests(unittest.TestCase):
    def test_valid_fenced_answer(self):
        result = llm.parse_answer("```json\n" + json.dumps(answer()) + "\n```",
                                  {"astra_spot_grid"})
        self.assertEqual(result["strategy"], "astra_spot_grid")
        self.assertEqual(result["confidence"], 0.8)

    def test_unknown_strategy_is_rejected(self):
        with self.assertRaises(llm.LLMError):
            llm.parse_answer(json.dumps(answer(strategy="live_profile")), {"astra_spot_grid"})

    def test_invalid_confidence_is_rejected(self):
        for confidence in (2, -0.1, float("nan"), float("inf"), True, "bad"):
            with self.subTest(confidence=confidence), self.assertRaises(llm.LLMError):
                llm.parse_answer(json.dumps(answer(confidence=confidence)), {"astra_spot_grid"})

    def test_invalid_reasons_are_rejected(self):
        for reasons in (None, "a reason", {"reason": "text"}, [None], [123]):
            with self.subTest(reasons=reasons), self.assertRaises(llm.LLMError):
                llm.parse_answer(json.dumps(answer(reasons=reasons)), {"astra_spot_grid"})


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, base_url, response=None):
        response = response or {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(answer())}}], "usage": {"total_tokens": 100}}
        candles = [[0, 100, 102, 99, 101, 10]] * 200
        choices = [{"id": "astra_spot_grid", "label": "grid", "summary": "range"}]
        with patch.object(settings, "llm_api_key", "test-only"), \
                patch.object(settings, "llm_base_url", base_url), \
                patch.object(settings, "llm_model", "deepseek-flash"), \
                patch.object(llm, "_post", return_value=response) as post:
            result = await llm.judge("BTC/USDT", candles, {}, choices)
            return result, post.call_args.args[0]

    async def test_deepseek_short_json_request_disables_thinking(self):
        result, body = await self.request("https://api.deepseek.com/v1")
        self.assertEqual(result["strategy"], "astra_spot_grid")
        self.assertEqual(body.get("thinking"), {"type": "disabled"})

    async def test_other_provider_does_not_get_deepseek_parameters(self):
        _, body = await self.request("https://example.invalid/v1")
        self.assertNotIn("thinking", body)

    async def test_truncated_answer_has_actionable_error(self):
        response = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
        with self.assertRaisesRegex(llm.LLMError, "截断"):
            await self.request("https://api.deepseek.com", response)


class CredentialLoggingTests(unittest.TestCase):
    def check_error(self, prefix):
        fake_key = "unit-test-secret-123456789"
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions", 401, "unauthorized", {},
            io.BytesIO((prefix + fake_key).encode()),
        )
        opener = SimpleNamespace(open=lambda *args, **kwargs: (_ for _ in ()).throw(error))
        with patch.object(settings, "llm_api_key", fake_key), \
                patch.object(llm.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(llm.LLMError) as caught:
                llm._post({})
        self.assertNotIn(fake_key, str(caught.exception))
        self.assertNotIn(fake_key[:10], str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    def test_provider_error_does_not_expose_key(self):
        self.check_error("Invalid credential: ")

    def test_error_truncation_does_not_expose_partial_key(self):
        self.check_error("x" * 150)


class AutomaticSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.config_patch = patch.object(settings, "data_dir", self.tempdir.name)
        self.config_patch.start()
        self.cockpit = SimpleNamespace(
            profile={"id": "astra_idle", "real": False},
            link_octobot={"ok": True, "since": "test"},
            _mode=lambda: "paper", oct_equity_live=1000, oct_equity=1000,
            add_log=lambda *args: None,
        )
        self.desk = StrategyDesk(self.cockpit)
        self.desk.catalog = [{"id": "astra_spot_grid", "blocked": None},
                             {"id": "astra_signal", "blocked": None}]
        self.desk.cfg["scheme"] = "ai_regime"
        self.desk.switch = AsyncMock()

    def tearDown(self):
        self.config_patch.stop()
        self.tempdir.cleanup()

    def set_verdicts(self, *verdicts):
        self.desk.ai_trail = list(copy.deepcopy(verdicts))
        self.desk.ai_regime = copy.deepcopy(verdicts[-1])

    async def test_same_regime_different_recommendations_do_not_confirm(self):
        self.set_verdicts(answer(), answer(strategy="astra_signal"))
        await self.desk.maybe_auto_switch()
        self.desk.switch.assert_not_awaited()

    async def test_two_matching_recommendations_can_switch(self):
        self.set_verdicts(answer(), answer())
        await self.desk.maybe_auto_switch()
        self.desk.switch.assert_awaited_once()
        args, kwargs = self.desk.switch.await_args
        self.assertEqual((args, kwargs["by"]), (("astra_spot_grid",), "auto"))
        self.assertIn("置信度", kwargs["why"])                 # the reason shown to the user

    async def test_low_confidence_does_not_switch(self):
        self.set_verdicts(answer(confidence=0.49), answer(confidence=0.49))
        await self.desk.maybe_auto_switch()
        self.desk.switch.assert_not_awaited()

    async def test_live_mode_does_not_switch(self):
        self.cockpit._mode = lambda: "live"
        self.set_verdicts(answer(), answer())
        await self.desk.maybe_auto_switch()
        self.desk.switch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
