"""Download Bitget spot candles for backtests, cached in data/candles/<SYMBOL>_<tf>.json.

Usage:  python tools/fetch_history.py --days 730 BTCUSDT ETHUSDT SOLUSDT
Re-running only fetches what is missing. Public endpoint, no API key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

URL = "https://api.bitget.com/api/v2/spot/market/history-candles"
STEP_MS = {"1h": 3600_000, "4h": 4 * 3600_000, "1day": 86400_000}
GRANULARITY = {"1h": "1h", "4h": "4h", "1day": "1day"}


def fetch_page(symbol: str, tf: str, end_ms: int, proxy: str = "") -> list[list[float]]:
    query = f"?symbol={symbol}&granularity={GRANULARITY[tf]}&endTime={end_ms}&limit=200"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy} if proxy else {}))
    for attempt in range(7):
        try:
            with opener.open(URL + query, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") != "00000":
                raise RuntimeError(data.get("msg"))
            return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in data["data"]]
        except Exception as err:  # transient network errors: retry with backoff
            if attempt == 6:
                raise
            print(f"  retry {symbol} {end_ms}: {err}", file=sys.stderr)
            time.sleep(min(30, 2 ** attempt))
    return []


def load(path: str) -> list[list[float]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []


def save(path: str, candles: list[list[float]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(candles, fh)
    os.replace(tmp, path)


def update(symbol: str, tf: str, days: int, folder: str, proxy: str = "") -> list[list[float]]:
    path = os.path.join(folder, f"{symbol}_{tf}.json")
    have = {c[0]: c for c in load(path)}
    step = STEP_MS[tf]
    now = int(time.time() * 1000) // step * step
    start = now - days * 86400_000
    # last fully closed candle is now - step; walk backwards in pages of 200
    end, pages = now, 0
    while end > start:
        wanted = [t for t in range(max(start, end - 200 * step), end, step)]
        if all(t in have for t in wanted):
            end -= 200 * step
            continue
        rows = fetch_page(symbol, tf, end, proxy)
        pages += 1
        if not rows:
            break
        for r in rows:
            if r[0] < now:  # skip the still-forming candle
                have[r[0]] = r
        end = min(r[0] for r in rows)
        if pages % 20 == 0:
            save(path, sorted(have.values()))  # keep progress if the connection drops
        time.sleep(0.15)
    candles = sorted(c for t, c in have.items() if t >= start)
    save(path, candles)
    gaps = sum(1 for a, b in zip(candles, candles[1:]) if b[0] - a[0] != step)
    print(f"{symbol} {tf}: {len(candles)} candles, {pages} requests, {gaps} gaps -> {path}")
    return candles


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--tf", default="1h", choices=sorted(STEP_MS))
    ap.add_argument("--out", default="data/candles")
    ap.add_argument("--proxy", default=os.getenv("BITGET_PROXY", ""))
    args = ap.parse_args()
    for s in args.symbols:
        update(s.upper(), args.tf, args.days, args.out, args.proxy)


if __name__ == "__main__":
    main()
