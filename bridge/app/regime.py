"""Market regime detection ("行情雷达") from hourly candles.

Rule based and fully explained: every verdict lists the numbers it was made from.
Regimes: range (震荡), up (上涨), down (下跌), volatile (剧烈波动).
"""
from __future__ import annotations

import statistics

LABELS = {"range": "震荡", "up": "上涨趋势", "down": "下跌趋势", "volatile": "剧烈波动"}

# strategies to prefer for each regime, best first (ids from strategies.META)
PREFER = {
    "range": ["astra_spot_grid", "astra_staggered", "astra_signal", "astra_spot_dca"],
    "up": ["astra_trailing_grid", "astra_smart_dca", "astra_trend"],
    "down": ["astra_idle", "astra_trend"],
    "volatile": ["astra_idle", "astra_dip"],
}


def ema(values: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def wilder(values: list[float], period: int) -> list[float]:
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append((out[-1] * (period - 1) + v) / period)
    return out


def adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> tuple[float, float, float]:
    """Return (ADX, +DI, -DI) of the last candle."""
    tr, plus_dm, minus_dm = [], [], []
    for i in range(1, len(closes)):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    atr_s, plus_s, minus_s = wilder(tr, period), wilder(plus_dm, period), wilder(minus_dm, period)
    dx, plus_di, minus_di = [], 0.0, 0.0
    for a, p, m in zip(atr_s, plus_s, minus_s):
        plus_di, minus_di = (100 * p / a, 100 * m / a) if a else (0.0, 0.0)
        total = plus_di + minus_di
        dx.append(100 * abs(plus_di - minus_di) / total if total else 0.0)
    return wilder(dx, period)[-1], plus_di, minus_di


def atr_pct_series(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    tr = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
          for i in range(1, len(closes))]
    atr_s = wilder(tr, period)
    return [a / c * 100 for a, c in zip(atr_s, closes[period:])]


def analyse(candles: list[list[float]]) -> dict | None:
    """candles: [[ts, open, high, low, close, volume], ...] hourly, oldest first (>= 120)."""
    if len(candles) < 120:
        return None
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    last = closes[-1]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    slope = (e50[-1] - e50[-25]) / e50[-25] * 100  # EMA50 change over 24h, %
    adx_v, plus_di, minus_di = adx(highs, lows, closes)
    atr_series = atr_pct_series(highs, lows, closes)
    atr_now, atr_med = atr_series[-1], statistics.median(atr_series)
    window = closes[-20:]
    bb_width = 4 * statistics.pstdev(window) / statistics.fmean(window) * 100
    change_24h = (last - closes[-25]) / closes[-25] * 100

    if atr_now > 1.8 * atr_med and abs(change_24h) > 3:
        regime, confidence = "volatile", min(1.0, atr_now / atr_med / 2.5)
    elif adx_v >= 25 and e20[-1] > e50[-1] and slope > 0.3:
        regime, confidence = "up", min(1.0, adx_v / 40)
    elif adx_v >= 25 and e20[-1] < e50[-1] and slope < -0.3:
        regime, confidence = "down", min(1.0, adx_v / 40)
    elif adx_v < 20:
        regime, confidence = "range", min(1.0, (25 - adx_v) / 15)
    else:
        # 20 <= ADX < 25 or mixed signals: lean on the moving averages, low confidence
        regime = "up" if slope > 0.3 and e20[-1] > e50[-1] else "down" if slope < -0.3 and e20[-1] < e50[-1] else "range"
        confidence = 0.4

    reasons = [
        f"ADX {adx_v:.1f}（{'有趋势' if adx_v >= 25 else '趋势弱' if adx_v < 20 else '趋势不明'}），+DI {plus_di:.1f} / −DI {minus_di:.1f}",
        f"EMA20 {'高于' if e20[-1] > e50[-1] else '低于'} EMA50，EMA50 24 小时变化 {slope:+.2f}%",
        f"ATR {atr_now:.2f}%（近期中位数 {atr_med:.2f}%），布林带宽度 {bb_width:.2f}%",
        f"24 小时涨跌 {change_24h:+.2f}%",
    ]
    return {
        "regime": regime,
        "label": LABELS[regime],
        "confidence": round(confidence, 2),
        "prefer": PREFER[regime],
        "reasons": reasons,
        "metrics": {"adx": round(adx_v, 1), "plusDI": round(plus_di, 1), "minusDI": round(minus_di, 1),
                    "emaSlope24h": round(slope, 2), "atrPct": round(atr_now, 2), "atrMedianPct": round(atr_med, 2),
                    "bbWidthPct": round(bb_width, 2), "change24h": round(change_24h, 2), "last": last},
    }
