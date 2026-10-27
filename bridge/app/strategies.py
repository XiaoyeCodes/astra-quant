"""ASTRA strategy catalog: the astra_* OctoBot profiles the cockpit can switch between.

Only paper (trader simulator) profiles may be selected from the cockpit. Real trading
stays a manual decision made in OctoBot itself.
"""
from __future__ import annotations

import glob
import json
import os

from .config import settings

# Exchanges whose OctoBot 2.1.1 tentacle supports futures. Bitget is spot only.
FUTURES_EXCHANGES = {"binance", "bybit", "okx", "kucoin", "bingx", "bitmex", "coinbase"}

# OctoBot 2.1.1's unauthenticated Coindesk news/market-cap feed returned HTTP 401
# in the live-market paper test. Re-enable this preset after its full data and
# allocation chain has been repaired and verified; a working LLM alone is insufficient.
UNAVAILABLE_PRESETS = {
    "astra_ai_index": "AI 仓位尚未通过测试：新闻/市值数据源返回 401，无法完成仓位决策。请先使用 AI 信号。",
}

# family: drives how the cockpit draws the strategy (grid band, DCA ladder, signals, basket, ai)
# regimes: market regimes the strategy is suited to (see regime.py)
META: dict[str, dict] = {
    "astra_spot_grid": {"label": "现货网格", "family": "grid", "regimes": ["range"],
                        "summary": "区间内等距挂单，成交后挂反向单赚差价。震荡市稳定，单边行情会被套或卖飞。"},
    "astra_trailing_grid": {"label": "移动网格", "family": "grid", "regimes": ["range", "up"],
                            "summary": "网格随价格突破上沿整体上移，震荡上行时不踏空；急跌一样被套。"},
    "astra_staggered": {"label": "阶梯挂单", "family": "grid", "regimes": ["range"],
                        "summary": "超宽区间按比例间距挂单，不易跑出区间；资金分散，单格利润低。"},
    "astra_spot_dca": {"label": "分批补仓", "family": "dca", "regimes": ["range"],
                       "summary": "定时开仓，下跌分批补仓，均价上方止盈、下方止损。越跌买越多。"},
    "astra_smart_dca": {"label": "智能补仓", "family": "dca", "regimes": ["up"],
                        "summary": "EMA 显示超跌才买，小反弹分批止盈。适合上升趋势里的回调，无止损。"},
    "astra_trend": {"label": "趋势指标", "family": "signal", "regimes": ["up", "down"],
                    "summary": "双均线 + RSI 顺势买卖，带止损。吃得到大段趋势，震荡市反复止损。"},
    "astra_dip": {"label": "抄底分析", "family": "signal", "regimes": ["volatile"],
                  "summary": "识别急跌低点加权买入，涨 4%–10% 分批卖。没有止损，宜小仓位。"},
    "astra_signal": {"label": "反转信号", "family": "signal", "regimes": ["range"],
                     "summary": "布林带 + Klinger 找反转高抛低吸，带止损。趋势行情里是逆势。"},
    "astra_index": {"label": "指数持仓", "family": "index", "regimes": [],
                    "summary": "按 BTC/ETH/SOL 权重持有并定期再平衡。被动低频，熊市跟跌。"},
    "astra_ai_signal": {"label": "AI 信号", "family": "ai", "regimes": [],
                        "summary": "DeepSeek 每小时读 K 线给看涨/看跌信号，看涨时分批买入、2% 止盈。"},
    "astra_ai_index": {"label": "AI 仓位", "family": "ai", "regimes": [],
                       "summary": "多个 AI 智能体综合指标、新闻、情绪决定 BTC/ETH/SOL/USDT 比例并调仓。"},
    "astra_idle": {"label": "观望", "family": "idle", "regimes": ["down", "volatile"],
                   "summary": "不下新单，只看行情。按交接规则（默认）切换到观望时会先卖出全部持币，真正空仓。"},
    "astra_futures_dca": {"label": "合约分批补仓", "family": "dca", "regimes": [],
                          "summary": "USDT 永续分批补仓（类马丁）。"},
}


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def describe(pid: str, data: dict) -> dict:
    """Catalog entry for one profile.json content."""
    cfg, prof = data.get("config", {}), data.get("profile", {})
    meta = META.get(pid, {})
    exchanges = {k: v for k, v in cfg.get("exchanges", {}).items() if v.get("enabled")}
    futures = [k for k, v in exchanges.items() if v.get("exchange-type") == "future"]
    real = bool(cfg.get("trader", {}).get("enabled"))
    simulated = bool(cfg.get("trader-simulator", {}).get("enabled")) and not real
    unsupported = [k for k in futures if k not in FUTURES_EXCHANGES]
    if unsupported:
        blocked = f"OctoBot 的 {unsupported[0]} 插件不支持合约"
    elif not simulated:
        blocked = "这是实盘配置，只能在 OctoBot 中手动切换"
    elif pid in UNAVAILABLE_PRESETS:
        blocked = UNAVAILABLE_PRESETS[pid]
    else:
        blocked = None
    return {
        "id": pid,
        "name": prof.get("name", pid),
        "label": meta.get("label", prof.get("name", pid)),
        "family": meta.get("family", "other"),
        "regimes": meta.get("regimes", []),
        "summary": meta.get("summary", prof.get("description", "")[:80]),
        "description": prof.get("description", ""),
        "exchangeType": "future" if futures else "spot",
        "simulated": simulated,
        "blocked": blocked,
    }


def backtest_summary() -> dict | None:
    """Two-year backtest headline numbers (tools/run_backtest.py --summary), shipped with the bridge."""
    return _load(os.path.join(os.path.dirname(__file__), "backtest_summary.json")) or None


BACKTEST = backtest_summary()


def catalog() -> list[dict]:
    base = os.path.join(settings.octobot_user_dir, "profiles")
    out = []
    for path in sorted(glob.glob(os.path.join(base, "astra_*", "profile.json"))):
        pid = os.path.basename(os.path.dirname(path))
        data = _load(path)
        if data:
            out.append(describe(pid, data))
    order = list(META)
    out.sort(key=lambda s: order.index(s["id"]) if s["id"] in order else len(order))
    return out


def find(pid: str) -> dict | None:
    """Re-read one profile from disk right before switching to it."""
    if not pid.startswith("astra_") or "/" in pid or "\\" in pid or ".." in pid:
        return None
    data = _load(os.path.join(settings.octobot_user_dir, "profiles", pid, "profile.json"))
    return describe(pid, data) if data else None
