"""AI market read: asks a large language model (DeepSeek by default) for the market regime.

The model gets the same indicator numbers as the rule based radar plus recent price
action, and must answer in JSON with one of the four regimes and a strategy from the
allowed list. Anything else is rejected, so a bad answer can never pick an unknown
or real-money profile.
"""
from __future__ import annotations

import asyncio
import json
import math
import urllib.error
import urllib.parse
import urllib.request

from .config import settings
from .regime import LABELS

SYSTEM = (
    "你是加密货币量化交易的行情分析员。根据给出的 BTC 行情数据判断当前市场处于哪种状态，"
    "并从候选策略中选出最合适的一个。只输出 JSON，不要输出其他文字。"
)


class LLMError(Exception):
    pass


def available() -> bool:
    return bool(settings.llm_api_key)


def _summary(candles: list[list[float]]) -> list[list[float]]:
    """Last 48 hourly candles as [hours ago, open, high, low, close] to keep the prompt small."""
    rows = candles[-48:]
    return [[len(rows) - i - 1, round(c[1]), round(c[2]), round(c[3]), round(c[4])] for i, c in enumerate(rows)]


def build_prompt(symbol: str, candles: list[list[float]], metrics: dict, choices: list[dict]) -> str:
    closes = [c[4] for c in candles]
    change_7d = (closes[-1] - closes[-169]) / closes[-169] * 100 if len(closes) > 169 else None
    options = "\n".join(f"- {c['id']}：{c['label']}。{c['summary']}" for c in choices)
    return (
        f"交易对：{symbol}（Bitget 现货）\n"
        f"现价：{closes[-1]:.2f}\n"
        f"指标（1 小时 K 线）：{json.dumps(metrics, ensure_ascii=False)}\n"
        f"7 天涨跌：{'%.2f%%' % change_7d if change_7d is not None else '未知'}\n"
        f"最近 48 根 1 小时 K 线 [几小时前, 开, 高, 低, 收]：{json.dumps(_summary(candles))}\n\n"
        f"行情状态只能是：range（震荡）、up（上涨趋势）、down（下跌趋势）、volatile（剧烈波动）。\n"
        f"候选策略（只能选其中一个 id）：\n{options}\n\n"
        "输出 JSON：{\"regime\": \"range|up|down|volatile\", \"confidence\": 0到1的小数, "
        "\"strategy\": \"候选策略id\", \"reasons\": [\"不超过3条中文理由，每条不超过40字\"]}"
    )


def _post(body: dict) -> dict:
    req = urllib.request.Request(
        settings.llm_base_url + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings.llm_api_key}"},
        method="POST",
    )
    proxies = {"http": settings.llm_proxy, "https": settings.llm_proxy} if settings.llm_proxy else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    try:
        with opener.open(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        if settings.llm_api_key:
            detail = detail.replace(settings.llm_api_key, "[REDACTED]")
        detail = detail[:160]
        raise LLMError(f"HTTP {err.code}：{detail}") from err
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
        raise LLMError(f"无法访问大模型接口（{getattr(err, 'reason', err)}）") from err


def parse_answer(text: str, allowed: set[str]) -> dict:
    if not isinstance(text, str):
        raise LLMError("大模型返回的正文不是文本")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMError("大模型没有返回 JSON")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as err:
        raise LLMError("大模型返回的 JSON 无法解析") from err
    if not isinstance(data, dict):
        raise LLMError("大模型返回的 JSON 必须是对象")
    regime = str(data.get("regime", "")).strip()
    strategy = str(data.get("strategy", "")).strip()
    if regime not in LABELS:
        raise LLMError(f"大模型给出了未知的行情状态：{regime or '空'}")
    if strategy not in allowed:
        raise LLMError(f"大模型选了不在候选列表里的策略：{strategy or '空'}")
    raw_confidence = data.get("confidence")
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        raise LLMError("大模型返回的置信度必须是 0–1 的有限数值") from None
    if isinstance(raw_confidence, bool) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise LLMError("大模型返回的置信度必须是 0–1 的有限数值")
    raw_reasons = data.get("reasons", [])
    if not isinstance(raw_reasons, list) or any(not isinstance(reason, str) for reason in raw_reasons):
        raise LLMError("大模型返回的理由必须是文本列表")
    reasons = [r.strip()[:80] for r in raw_reasons if r.strip()][:3]
    return {"regime": regime, "label": LABELS[regime], "confidence": round(confidence, 2),
            "strategy": strategy, "reasons": reasons}


async def judge(symbol: str, candles: list[list[float]], metrics: dict, choices: list[dict]) -> dict:
    if not available():
        raise LLMError("未配置 LLM_API_KEY")
    body = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": build_prompt(symbol, candles, metrics, choices)}],
        "temperature": 0.2,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }
    if urllib.parse.urlparse(settings.llm_base_url).hostname == "api.deepseek.com":
        # This bounded JSON classification does not need a reasoning trace. With
        # DeepSeek's default thinking enabled, reasoning can exhaust max_tokens
        # before any final JSON is returned.
        body["thinking"] = {"type": "disabled"}
    res = await asyncio.to_thread(_post, body)
    try:
        choice = res["choices"][0]
        text = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as err:
        raise LLMError("大模型返回格式异常") from err
    if choice.get("finish_reason") == "length":
        raise LLMError("大模型回答被 token 上限截断，本次判断已忽略")
    verdict = parse_answer(text, {c["id"] for c in choices})
    verdict["tokens"] = (res.get("usage") or {}).get("total_tokens")
    verdict["model"] = res.get("model", settings.llm_model)
    return verdict
